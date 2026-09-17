"""Structural validator tests with explicitly synthetic (not timing) records."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
VALIDATOR = REPO / "benchmarks/scc/validate_ownership_screen.py"


class OwnershipScreenTests(unittest.TestCase):
    def run_fixture(self, fault=None, lane="screen"):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            output = run / "output" / lane
            output.mkdir(parents=True)
            reference = run / "manifests/screen-reference"
            reference.mkdir(parents=True)
            (run / "manifests/source-commit.txt").write_text("0" * 40)
            (reference / "source.txt").write_text("1" * 40)
            for variant in ("baseline", "candidate"):
                work = run / "work" / lane / variant
                work.mkdir(parents=True)
                (work / "Cargo.lock").write_bytes((REPO / "Cargo.lock").read_bytes())
                for attempt in ("alloc", "1", "2", "3", "4", "5", "6", "7"):
                    rows = []
                    for case in ("path", "mixed", "dense", "weak", "fallback"):
                        for component in (False, True):
                            for workers in (1, 4):
                                row = dict(
                                    case=case, component=component, workers=workers,
                                    rhs=1 if case == "fallback" else 4,
                                    setup_ns=10, workspace_ns=10, solve_ns=10,
                                    shutdown_ns=10, total_ns=1000,
                                    peak_requested_increment=1024,
                                    retained_requested_increment=512,
                                    iterations=2, solution_fnv64="synthetic",
                                    residual=0.0, coefficient_relative_error=0.0,
                                    certified=True, discarded_or_fallback="not_exercised",
                                    allocation_instrumentation=attempt == "alloc",
                                )
                                if case == "fallback":
                                    row.update(
                                        discarded_or_fallback="strict-residual-rejection-then-direct",
                                        discarded_iterations=1, discarded_residual=1.8,
                                        discarded_coefficient_relative_error=2.5e-6,
                                        discarded_setup_ns=10, discarded_solve_ns=10,
                                        rejection_check_ns=10, discarded_cleanup_ns=10,
                                        acceptance_check_ns=10, accepted_cleanup_ns=10,
                                        discarded_peak_requested_increment=900,
                                        after_discard_requested_increment=0,
                                        after_retirement_requested_increment=0,
                                    )
                                rows.append(row)
                    if variant == "candidate" and attempt == "1" and fault:
                        fault(rows)
                    (output / f"{variant}-{attempt}.jsonl").write_text(
                        "".join(json.dumps(row) + "\n" for row in rows)
                    )
            result = subprocess.run(
                [sys.executable, str(VALIDATOR), str(run), lane],
                # SCC's system Python is 3.6; capture_output/text arrived in 3.7.
                cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, check=False,
            )
            summary = output / "summary.json"
            if result.returncode == 0:
                record = json.loads(summary.read_text())
                self.assertEqual(record["pairs"], 140)
                self.assertEqual(record["lane"], lane)
                self.assertEqual(len(record["cases"]), 20)
                self.assertEqual(record["baseline"], "0" * 40 if lane == "screen-parallel-admitted" else "1" * 40)
                self.assertEqual(record["both_variants_join_workers"], lane == "screen-parallel-admitted")
            else:
                self.assertFalse(summary.exists(), "invalid evidence produced a summary")
            return result

    def test_complete_synthetic_fixture(self):
        result = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_matched_serial_admission_fixture(self):
        result = self.run_fixture(lane="screen-admitted")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_fallback_is_rejected(self):
        self.assertNotEqual(self.run_fixture(lambda rows: rows.pop()).returncode, 0)

    def test_matched_parallel_admission_fixture(self):
        result = self.run_fixture(lane="screen-parallel-admitted")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unrejected_attempt_is_rejected(self):
        self.assertNotEqual(
            self.run_fixture(lambda rows: rows[-1].update(discarded_residual=0.0)).returncode, 0
        )

    def test_bad_forward_accuracy_is_rejected(self):
        self.assertNotEqual(
            self.run_fixture(lambda rows: rows[-1].update(coefficient_relative_error=2e-6)).returncode, 0
        )

    def test_paired_numerical_mismatch_is_rejected(self):
        self.assertNotEqual(
            self.run_fixture(lambda rows: rows[-1].update(solution_fnv64="mismatch")).returncode, 0
        )

    def test_nonfinite_residual_is_rejected(self):
        self.assertNotEqual(
            self.run_fixture(lambda rows: rows[-1].update(residual=float("nan"))).returncode, 0
        )

    def test_inconsistent_phase_accounting_is_rejected(self):
        self.assertNotEqual(
            self.run_fixture(lambda rows: rows[-1].update(discarded_setup_ns=2000)).returncode, 0
        )


if __name__ == "__main__":
    unittest.main()
