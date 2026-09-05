import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dispatch_campaign as dc
import dispatch_validator_reuse as reuse


class DispatchCampaignTests(unittest.TestCase):
    def test_exact_matrix_and_roundtrips(self):
        for profile in dc.PROFILES:
            for kind, count, cases in (("dispatch-smoke", 1, 2), ("dispatch-validate", 3, 8)):
                rows = dc.tasks(kind, profile)
                self.assertEqual(len(rows), count)
                self.assertEqual(len(rows[0]["cases"]), cases)
                self.assertEqual({r["slots"] for r in rows}, {1})
                self.assertEqual({r["target_cpu"] for r in rows}, {"portable"})
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory)/"manifest.jsonl"
                    path.write_text("\n".join(map(json.dumps, rows))+"\n")
                    self.assertEqual(dc.manifest(path), rows)
                    rows[0]["slots"] = 28
                    path.write_text("\n".join(map(json.dumps, rows))+"\n")
                    with self.assertRaises(ValueError): dc.manifest(path)
        with self.assertRaises(ValueError): dc.tasks("dispatch-smoke", "epyc-9124")
        with self.assertRaises(ValueError): dc.tasks("other", "gold-6242")

    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"result.json"
            dc.exclusive_json(path, {"first": True})
            with self.assertRaises(FileExistsError): dc.exclusive_json(path, {"first": False})
            self.assertEqual(json.loads(path.read_text()), {"first": True})

    def test_accounting_complete_failure_and_duplicates(self):
        record = """==============================================================
jobnumber 123
taskid 1
slots 1
failed 0
exit_status 0
hostname scc-test.scc.bu.edu
start_time Sat Sep 05 10:00:00 2026
end_time Sat Sep 05 10:01:00 2026
ru_wallclock 60.0
maxvmem 0.3G
"""
        self.assertEqual(len(dc.parse_qacct(record, "123", ["1"], 1)), 1)
        for bad in (record+record, record.replace("exit_status 0\n", ""), record.replace("failed 0", "failed 1"),
                    record.replace("slots 1", "slots 28"), record.replace("end_time Sat Sep 05 10:01:00 2026", "end_time 0")):
            with self.assertRaises(ValueError): dc.parse_qacct(bad, "123", ["1"], 1)
        with self.assertRaises(ValueError): dc.parse_qacct(record, "123", ["1", "2"], 1)

        # Real SCC qacct pads both keys and values, including non-array taskids.
        lines = []
        for line in record.splitlines():
            parts = line.split(None, 1)
            lines.append(f"{parts[0]:<13}{parts[1]:<30}" if len(parts) == 2 else line)
        padded = "\n".join(lines) + "\n"
        self.assertEqual(dc.parse_qacct(padded, "123", ["1"], 1),
                         dc.parse_qacct(record, "123", ["1"], 1))
        bootstrap = padded.replace("taskid       1", "taskid       undefined").replace("slots        1", "slots        4")
        self.assertEqual(dc.parse_qacct(bootstrap, "123", ["undefined"], 4)[0]["slots"], "4")
        for bad in (padded+padded, padded+"exit_status 0\n", padded.replace("failed       0", "failed       1"),
                    padded.replace("exit_status  0", "exit_status  1"),
                    padded.replace("jobnumber    123", "jobnumber    124")):
            with self.assertRaises(ValueError): dc.parse_qacct(bad, "123", ["1"], 1)

    def test_reuse_rejects_science_and_gate_changes(self):
        key = "benchmarks/scc/dispatch_campaign.py"
        original = {key: b"def parse_qacct(): return 0\ndef gate(): return 1\n", "src/lib.rs": b"numerics"}
        helper = dict(original, **{key: b"def parse_qacct(): return 2\ndef gate(): return 1\n"})
        reuse.verify_delta(original, helper)
        for bad in (dict(helper, **{"src/lib.rs": b"changed"}),
                    dict(helper, **{key: b"def parse_qacct(): return 2\ndef gate(): return 0\n"}),
                    dict(helper, **{"benchmarks/scc/run_task.sh": b"changed"})):
            with self.assertRaises(ValueError): reuse.verify_delta(original, bad)

    def test_scientific_gates(self):
        spec = dc.tasks("dispatch-smoke", "gold-6242")[0]["cases"][0]
        case = dict(spec, schema="cmg-dispatch-case-v1", source_commit="a"*40, source_archive_sha256="b"*64,
                    edges=14999,
                    bitwise_identical=True, cached_holdout=True, holdout_seeds=list(range(spec["rhs_seed"]+1, spec["rhs_seed"]+8)),
                    first_executed="Scalar", selected="Fused", reason="ClearFusedGain", repetitions=7,
                    scalar_ns=[100]*7, fused_ns=[50]*7, auto_ns=[50]*7,
                    fused_over_scalar=dict(ratio=.5, ci95=[.5,.5]), auto_over_scalar=dict(ratio=.5, ci95=[.5,.5]),
                    auto_over_selected=dict(ratio=1., ci95=[1.,1.]), first_call_ns=1000,
                    retained_workspace_bytes=1000, scalar_workspace_bytes=200, fused_workspace_bytes=800,
                    workspace_budget_bytes=1024**3, calibration_pairs=5, calibration_scalar_ns=[100]*5,
                    calibration_fused_ns=[50]*5, calibration_ratio=.5, calibration_ci95=[.5,.5],
                    break_even_batches=10, peak_bound_bytes=2000)
        dc.check_case(case, spec, "a"*40, "b"*64)
        for key, value in (("scalar_ns", [0]*7), ("bitwise_identical", False), ("calibration_pairs", 4),
                           ("calibration_ci95", [.5, .95]), ("retained_workspace_bytes", 1024**3+1),
                           ("auto_over_scalar", dict(ratio=.5, ci95=[float("nan"),1])),
                           ("auto_ns", [90]*7), ("source_commit", "c"*40)):
            bad = copy.deepcopy(case); bad[key] = value
            with self.assertRaises(ValueError): dc.check_case(bad, spec, "a"*40, "b"*64)

    def test_promotion_does_not_accept_incomplete_or_unexercised_routes(self):
        self.assertFalse(dc.promotion([])["promotion_pass"])
        results = []
        for profile in dc.PROFILES:
            for task in dc.tasks("dispatch-validate", profile):
                cases = [dict(spec, selected="Fused" if i % 2 else "Scalar", reason="test",
                              fused_over_scalar=dict(ratio=.8, ci95=[.7,.9]),
                              auto_over_scalar=dict(ratio=1., ci95=[.99,1.01]),
                              auto_over_selected=dict(ratio=1., ci95=[.99,1.01]),
                              first_call_ns=100, calibration_extra_ns=90, break_even_batches=10)
                         for i,spec in enumerate(task["cases"])]
                results.append(dict(task=task, hostname="scc-test", cases=cases))
        self.assertTrue(dc.promotion(results)["promotion_pass"])
        results[0]["cases"][0]["auto_over_selected"]["ci95"][1] = 1.03
        self.assertFalse(dc.promotion(results)["promotion_pass"])


if __name__ == "__main__": unittest.main()
