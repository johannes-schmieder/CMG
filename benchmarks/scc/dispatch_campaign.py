#!/usr/bin/env python3
"""Frozen, one-slot dispatch qualification; never writes an existing artifact.

Performance acceptance is separate from execution validity. A valid but slow or
inconclusive holdout blocks promotion, not collection of the remaining evidence.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import time

from run_fused_task import cpu_model_name, sha256

PROFILES = {
    "e5-2680v4": (28, "E5-2680v4", "E5-2680 v4"),
    "gold-6242": (32, "Gold-6242", "Gold 6242"),
}
PROJECT = Path("/projectnb/welfgr/cmg-benchmarks")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def tasks(kind, profile):
    require(kind in ("dispatch-smoke", "dispatch-validate"), "invalid dispatch kind")
    require(profile in PROFILES, "invalid dispatch profile")
    cores, cpu_type, model = PROFILES[profile]
    rows = []
    for task_id in range(1, 2 if kind == "dispatch-smoke" else 4):
        specs = [(10_000, 3, 4, "heterogeneous"), (10_000, 16, 4, "distinct")]
        if kind == "dispatch-validate":
            specs = [(100_000, degree, 4, mode) for degree in (3, 8, 16)
                     for mode in ("distinct", "heterogeneous")]
            specs += [(300_000, 3, 5, "heterogeneous"), (300_000, 16, 16, "distinct")]
        cases = [dict(vertices=n, degree=d, rhs_count=r, mode=m,
                      rhs_seed=10_000 + task_id * 100 + i * 1000)
                 for i, (n, d, r, m) in enumerate(specs)]
        rows.append(dict(protocol="cmg-dispatch-v1", kind=kind, cpu_profile=profile,
                         experiment=f"{kind}-{profile}", task_id=task_id,
                         target_cpu="portable", slots=1, host_num_proc=cores,
                         host_cpu_type=cpu_type, cpu_model_contains=model,
                         repetitions=7, cases=cases))
    return rows


def manifest(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
    require(bool(rows), "empty manifest")
    require(rows == tasks(rows[0]["kind"], rows[0]["cpu_profile"]), "noncanonical dispatch manifest")
    return rows


def exclusive_json(path, value):
    with Path(path).open("x") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"), allow_nan=False)
        handle.write("\n")


def identity(run):
    source = (run / "manifests/source-commit.txt").read_text().strip()
    archive = (run / "manifests/source-archive-sha256.txt").read_text().strip()
    require(re.fullmatch(r"[0-9a-f]{40}", source), "invalid source SHA")
    require(re.fullmatch(r"[0-9a-f]{64}", archive), "invalid archive SHA")
    require(sha256(PROJECT / "source-archives" / f"{source}.tar") == archive, "archive mismatch")
    binary = PROJECT / "code-b2" / source / "benchmarks/target/release/fused-dispatch-experiment"
    digest = (run / "manifests/dispatch-portable-binary-sha256.txt").read_text().strip()
    require(sha256(binary) == digest, "portable binary mismatch")
    require(json.loads((run / "manifests/dispatch-identity.json").read_text()) ==
            dict(source_commit=source, source_archive_sha256=archive), "compiled dispatch identity mismatch")
    return source, archive, binary, digest


def positive(value):
    return type(value) in (float, int) and math.isfinite(value) and value > 0


def interval(value):
    ratio, ci = value["ratio"], value["ci95"]
    require(positive(ratio) and len(ci) == 2 and all(map(positive, ci)), "invalid paired interval")
    require(ci[0] <= ratio <= ci[1], "point estimate outside interval")


def check_case(value, spec, source, archive):
    require(value["schema"] == "cmg-dispatch-case-v1", "wrong case schema")
    require(all(value[k] == v for k, v in spec.items()), "case differs from manifest")
    require(value["source_commit"] == source and value["source_archive_sha256"] == archive, "compiled identity mismatch")
    require(value["bitwise_identical"] is True and value["cached_holdout"] is True, "numerical/cache failure")
    require(value["holdout_seeds"] == list(range(spec["rhs_seed"] + 1, spec["rhs_seed"] + 8)), "wrong holdout seeds")
    require(value["first_executed"] == "Scalar" and value["selected"] in ("Scalar", "Fused"), "wrong dispatch route")
    require(value["repetitions"] == 7, "wrong repetitions")
    require(spec["vertices"] - 1 <= value["edges"] <= spec["vertices"] * spec["degree"] // 2, "invalid graph edge count")
    require(value["reason"] in ("ZeroIterations", "TimeBudget", "MemoryBudget", "Allocation", "InvalidTiming", "NoClearGain", "ClearFusedGain"), "unknown calibration reason")
    for key in ("scalar_ns", "fused_ns", "auto_ns"):
        require(len(value[key]) == 7 and all(map(positive, value[key])), f"invalid {key}")
    for key in ("fused_over_scalar", "auto_over_scalar", "auto_over_selected"):
        interval(value[key])
    selected = "fused_ns" if value["selected"] == "Fused" else "scalar_ns"
    for key, denominator, numerator in (("fused_over_scalar", "scalar_ns", "fused_ns"),
                                       ("auto_over_scalar", "scalar_ns", "auto_ns"),
                                       ("auto_over_selected", selected, "auto_ns")):
        ratio = sorted(value[numerator])[3] / sorted(value[denominator])[3]
        require(math.isclose(value[key]["ratio"], ratio, rel_tol=1e-12), "ratio does not reconstruct samples")
    for key in ("first_call_ns", "retained_workspace_bytes", "scalar_workspace_bytes", "fused_workspace_bytes"):
        require(positive(value[key]), f"invalid {key}")
    require(value["workspace_budget_bytes"] == 1024**3, "wrong memory cap")
    require(value["retained_workspace_bytes"] <= 1024**3, "retained memory over cap")
    pairs = value["calibration_pairs"]
    require(type(pairs) is int and 0 <= pairs <= 5, "wrong calibration pair count")
    for key in ("calibration_scalar_ns", "calibration_fused_ns"):
        require(len(value[key]) == 5 and all(map(positive, value[key][:pairs]))
                and value[key][pairs:] == [0] * (5-pairs), "invalid calibration timings")
    if value["calibration_ratio"] is not None:
        require(pairs == 5, "partial calibration has estimate")
        interval(dict(ratio=value["calibration_ratio"], ci95=value["calibration_ci95"]))
        ratio = sorted(value["calibration_fused_ns"])[2] / sorted(value["calibration_scalar_ns"])[2]
        require(math.isclose(ratio, value["calibration_ratio"], rel_tol=1e-12), "calibration ratio does not reconstruct")
    if value["selected"] == "Fused":
        require(pairs == 5 and value["reason"] == "ClearFusedGain", "unsupported fused selection")
        require(value["calibration_ci95"][1] < 0.90, "gain margin not established")
        require(positive(value["break_even_batches"]), "missing break-even estimate")
    if pairs > 0:
        require(value["retained_workspace_bytes"] <= value["peak_bound_bytes"] <= 1024**3, "invalid calibration memory bound")


def check_result(value, task, run, source, archive, digest):
    require(value["task"] == task and value["run_id"] == run.name, "task identity mismatch")
    require(value["source_commit"] == source and value["source_archive_sha256"] == archive, "source identity mismatch")
    require(value["binary_sha256"] == digest, "binary identity mismatch")
    require(value["allocated_slots"] == 1 and len(set(value["allocated_cpus"])) == 1
            and len(value["allocated_cpus"]) == 1 and value["sge_slots"] == 1, "wrong single-slot affinity")
    require(value["host_num_proc"] == task["host_num_proc"], "wrong host core count")
    require(task["cpu_model_contains"] in value["cpu_model"] and bool(value["hostname"]), "wrong host provenance")
    require(len(value["cases"]) == len(task["cases"]), "wrong case count")
    require(positive(value["process_wall_ns"]), "missing walltime")
    for case, spec in zip(value["cases"], task["cases"]):
        check_case(case, spec, source, archive)


def roots(run, task):
    suffix = Path(task["experiment"]) / f"task-{task['task_id']}"
    return run / "output" / suffix, run / "receipts" / suffix


def validate(run, task):
    source, archive, _, digest = identity(run)
    output, receipt = roots(run, task)
    value = json.loads((output / "dispatch.json").read_text())
    check_result(value, task, run, source, archive, digest)
    require((receipt / "SUCCESS").read_text() == f"success=true\noutput_sha256={sha256(output / 'dispatch.json')}\n", "receipt mismatch")
    require(len(list(output.glob("case-*.stdout"))) == len(task["cases"]), "missing raw stdout")
    require(len(list(output.glob("case-*.stderr"))) == len(task["cases"]), "missing raw stderr")
    for i, case in enumerate(value["cases"], 1):
        require((output / f"case-{i}.stderr").stat().st_size == 0, "nonempty process stderr")
        require(json.loads((output / f"case-{i}.stdout").read_text()) == case, "raw case disagreement")
    return value


def run_task(run, task):
    source, archive, binary, digest = identity(run)
    output, receipt = roots(run, task)
    require(not output.exists() and not receipt.exists(), "refusing to overwrite task evidence")
    output.mkdir(parents=True, exist_ok=False)
    receipt.mkdir(parents=True, exist_ok=False)
    cpus = sorted(os.sched_getaffinity(0))
    require(len(cpus) == 1 and os.environ.get("NSLOTS") == "1", "single-slot contract failed")
    require(os.cpu_count() == task["host_num_proc"] and task["cpu_model_contains"] in cpu_model_name(), "wrong compute hardware")
    cases = []
    start = time.monotonic_ns()
    for i, spec in enumerate(task["cases"], 1):
        command = [str(binary)] + [str(spec[k]) for k in ("vertices", "degree", "rhs_count", "mode", "rhs_seed")]
        with (output / f"case-{i}.stdout").open("x") as stdout, (output / f"case-{i}.stderr").open("x") as stderr:
            subprocess.run(command, stdout=stdout, stderr=stderr, check=True)
        require((output / f"case-{i}.stderr").stat().st_size == 0, "nonempty benchmark stderr")
        cases.append(json.loads((output / f"case-{i}.stdout").read_text()))
    result = dict(task=task, run_id=run.name, source_commit=source, source_archive_sha256=archive,
                  binary_sha256=digest, allocated_slots=len(cpus), allocated_cpus=cpus,
                  sge_slots=int(os.environ["NSLOTS"]), host_num_proc=os.cpu_count(), hostname=socket.gethostname(),
                  cpu_model=cpu_model_name(), process_wall_ns=time.monotonic_ns()-start, cases=cases)
    exclusive_json(output / "dispatch.json", result)
    check_result(result, task, run, source, archive, digest)
    with (receipt / "SUCCESS").open("x") as handle:
        handle.write(f"success=true\noutput_sha256={sha256(output / 'dispatch.json')}\n")
    print(f"CMG_DISPATCH_TASK_SUCCESS experiment={task['experiment']} task={task['task_id']}")


def parse_qacct(raw, job_id, task_ids, slots):
    records = []
    for block in re.split(r"(?m)^=+\s*$", raw):
        record = dict(line.split(None, 1) for line in block.splitlines() if len(line.split(None, 1)) == 2)
        if not record:
            continue
        required = {"jobnumber", "taskid", "failed", "exit_status", "slots", "hostname", "start_time", "end_time", "ru_wallclock", "maxvmem"}
        require(required <= record.keys(), "incomplete qacct record")
        require(record["jobnumber"] == str(job_id), "wrong qacct job")
        require(record["taskid"] in task_ids, "wrong qacct task")
        require(record["start_time"] not in ("0", "-/-") and record["end_time"] not in ("0", "-/-"), "nonterminal qacct")
        require(record["slots"] == str(slots), "wrong accounting slots")
        require(record["failed"].split()[0] == "0" and record["exit_status"] == "0", "scheduler/application failure")
        require(positive(float(record["ru_wallclock"])) and bool(record["hostname"]), "invalid accounting provenance")
        records.append(record)
    require(len(records) == len(task_ids) and {r["taskid"] for r in records} == set(task_ids), "incomplete or duplicate accounting")
    return records


def submission(run, experiment):
    text = (run / "manifests" / f"submission-{experiment}.txt").read_text()
    match = re.search(r"(?m)^job_id=([0-9]+)(?:\.[0-9:-]+)?$", text)
    require(match is not None, "invalid submission receipt")
    return match.group(1)


def accounting(run, experiment, task_ids, slots):
    job = submission(run, experiment)
    queued = subprocess.run(["qstat", "-j", job], text=True, capture_output=True)
    require(queued.returncode != 0 and "do not exist" in queued.stderr, "job still queued/active or qstat unavailable")
    account = subprocess.run(["qacct", "-j", job], text=True, capture_output=True, check=True).stdout
    return job, parse_qacct(account, job, task_ids, slots)


def accept_stage(run, kind, profile):
    rows = tasks(kind, profile)
    require(manifest(run / "manifests/tasks" / f"{kind}-{profile}.jsonl") == rows, "manifest changed")
    experiment = rows[0]["experiment"]
    job, records = accounting(run, experiment, [str(r["task_id"]) for r in rows], 1)
    results = []
    for row in rows:
        task_id = row["task_id"]
        value = validate(run, row)
        record = next(r for r in records if r["taskid"] == str(task_id))
        require(value["hostname"].split(".")[0] == record["hostname"].split(".")[0], "qacct/result host mismatch")
        logs = run / "logs" / experiment
        stdout = list(logs.glob(f"*.o{job}.{task_id}"))
        stderr = list(logs.glob(f"*.e{job}.{task_id}"))
        require(len(stdout) == len(stderr) == 1 and stderr[0].stat().st_size == 0, "missing logs or nonempty scheduler stderr")
        text = stdout[0].read_text()
        for marker in ("CMG_DISPATCH_TASK_SUCCESS", "CMG_DISPATCH_VALIDATE_SUCCESS"):
            require(text.count(marker) == 1, "missing/duplicate success marker")
        results.append(value)
    require(len(list((run / "output" / experiment).glob("task-*/dispatch.json"))) == len(rows), "unexpected outputs")
    require(len(list((run / "receipts" / experiment).glob("task-*/SUCCESS"))) == len(rows), "unexpected receipts")
    return results, records


def bootstrap_gate(run):
    source, archive, _, _ = identity(run)
    job, _ = accounting(run, "bootstrap", ["undefined"], 4)
    logs = run / "logs"
    out = list(logs.glob(f"*.o{job}")); err = list(logs.glob(f"*.e{job}"))
    require(len(out) == len(err) == 1 and err[0].stat().st_size == 0, "invalid bootstrap scheduler logs")
    require(out[0].read_text().count("CMG_SCC2_BOOTSTRAP_SUCCESS") == 1, "missing bootstrap marker")
    receipt = (run / "receipts/BUILD_SUCCESS").read_text()
    require(all(line in receipt.splitlines() for line in ("success=true", f"source_commit={source}", f"source_archive_sha256={archive}")), "wrong build receipt")
    required_logs = ["source-files", "rustup", "cargo-fmt", "cargo-bench-fmt", "cargo-clippy", "cargo-bench-clippy", "cargo-test", "cargo-release-test", "cargo-bench-test", "cargo-build", "cargo-bench-build", "python-compileall", "python-tests", "fused-manifests", "dispatch-manifests", "task-generator"]
    for name in required_logs:
        path = logs / f"{name}.log"
        require(path.is_file(), f"missing build log {name}")
        text = path.read_text()
        require(not re.search(r"(?im)^(?:error(?:\[|:)|FAILED(?:\s|$)|test result: FAILED|FAIL:|ERROR:)", text), f"failure in {name}")
    require((logs / "rustup.log").stat().st_size > 0, "empty rustup log")
    for name in ("cargo-test", "cargo-release-test", "cargo-bench-test"):
        require("test result: ok" in (logs / f"{name}.log").read_text(), "tests did not finish")
    require(re.search(r"(?m)^OK$", (logs / "python-tests.log").read_text()), "Python tests did not finish")


def gate(run, kind):
    bootstrap_gate(run)
    if kind == "dispatch-validate":
        for profile in PROFILES:
            accept_stage(run, "dispatch-smoke", profile)


def promotion(results):
    """Evaluate frozen gates without changing policy or suppressing valid evidence."""
    rows = []
    routes = {p: set() for p in PROFILES}
    for result in results:
        profile = result["task"]["cpu_profile"]
        for case in result["cases"]:
            routes[profile].add(case["selected"])
            overhead = case["auto_over_selected"]
            scalar = case["auto_over_scalar"]
            rows.append(dict(cpu_profile=profile, task_id=result["task"]["task_id"],
                             hostname=result["hostname"], vertices=case["vertices"], degree=case["degree"],
                             rhs_count=case["rhs_count"], mode=case["mode"], selected=case["selected"],
                             reason=case["reason"], fused_over_scalar=case["fused_over_scalar"],
                             auto_over_scalar=scalar, auto_over_selected=overhead,
                             overhead_pass=overhead["ci95"][1] <= 1.02,
                             scalar_nonregression_pass=scalar["ci95"][1] <= 1.05,
                             first_call_ns=case["first_call_ns"], calibration_extra_ns=case["calibration_extra_ns"],
                             break_even_batches=case["break_even_batches"]))
    coverage = all(value == {"Scalar", "Fused"} for value in routes.values())
    complete = len(rows) == 48 and all(
        {r["task"]["task_id"] for r in results if r["task"]["cpu_profile"] == p} == {1, 2, 3}
        for p in PROFILES)
    return dict(complete=complete, both_routes_per_cpu=coverage,
                promotion_pass=complete and coverage and all(r["overhead_pass"] and r["scalar_nonregression_pass"] for r in rows),
                rows=rows, caveat="Within-allocation paired intervals; shared-host interference and one fixed graph construction. No AMD/general density claim.")


def collect(run, kind, profile):
    rows = tasks(kind, profile)
    job, _ = accounting(run, rows[0]["experiment"], [str(r["task_id"]) for r in rows], 1)
    # Check again before recording: only complete, successful records are accepted.
    raw = subprocess.run(["qacct", "-j", job], capture_output=True, text=True, check=True).stdout
    parse_qacct(raw, job, [str(r["task_id"]) for r in rows], 1)
    root = run / "receipts/accounting"
    root.mkdir(exist_ok=True)
    destination = root / f"dispatch-{job}.txt"
    if destination.exists():
        require(destination.read_text() == raw, "existing accounting differs; preserve both, do not overwrite")
    else:
        with destination.open("x") as handle: handle.write(raw)
    print(f"CMG_DISPATCH_ACCOUNTING_SUCCESS job={job}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("generate", "check-manifest", "run", "validate", "gate", "accept", "collect", "summary"))
    parser.add_argument("args", nargs="*")
    cli = parser.parse_args()
    args = cli.args
    if cli.action == "generate":
        for row in tasks(*args): print(json.dumps(row, sort_keys=True, separators=(",", ":")))
    elif cli.action == "check-manifest":
        manifest(args[0]); print("CMG_DISPATCH_MANIFEST_SUCCESS")
    elif cli.action in ("run", "validate"):
        run, task_file, task_id = args
        row = manifest(task_file)[int(task_id)-1]
        require(row["task_id"] == int(task_id), "invalid task id")
        if cli.action == "run": run_task(Path(run), row)
        else:
            validate(Path(run), row)
            print(f"CMG_DISPATCH_VALIDATE_SUCCESS experiment={row['experiment']} task={task_id}")
    elif cli.action == "gate":
        gate(Path(args[0]), args[1]); print("CMG_DISPATCH_GATE_SUCCESS")
    elif cli.action == "collect":
        collect(Path(args[0]), args[1], args[2])
    elif cli.action == "summary":
        results, accounting_records = [], []
        for profile in PROFILES:
            accepted, records = accept_stage(Path(args[0]), "dispatch-validate", profile)
            results.extend(accepted); accounting_records.extend(records)
        print(json.dumps(dict(promotion=promotion(results), accounting=accounting_records), sort_keys=True))
    else:
        results, records = accept_stage(Path(args[0]), args[1], args[2])
        print(json.dumps(dict(results=results, accounting=records), sort_keys=True))


if __name__ == "__main__":
    main()
