"""Bounded correctness diagnostics; hosted-runner timings do not qualify performance."""
import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite(value):
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(finite(v) for v in value.values())
    if isinstance(value, list):
        return all(finite(v) for v in value)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    models = []
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        models = sorted({line.split(":", 1)[1].strip()
                         for line in cpuinfo.read_text().splitlines()
                         if line.startswith("model name")})
    plan = {
        "source": args.source, "binary_sha256": sha(args.binary),
        "platform": platform.platform(), "machine": platform.machine(),
        "cpu_models": models, "logical_cpus": os.cpu_count(),
        "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "performance_qualified": False,
        "cases": [
            ["large", "large-weighted-path-plus-pairs", "1e-8", "25", "1000"],
            ["large", "large-weighted-path-plus-pairs", "1e-12", "25", "1000"],
            ["stress", "weighted-connected-path", "1e-12", "25", "1000"],
            ["large", "large-connected-path", "1e-12", "25", "1000"],
        ],
    }
    (root / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    results = []
    errors = []
    for index, case in enumerate(plan["cases"]):
        output = root / (str(index) + ".jsonl")
        with output.open("x") as stdout, output.with_suffix(".stderr").open("x") as stderr:
            completed = subprocess.run([str(args.binary.resolve()), *case],
                                       stdout=stdout, stderr=stderr, timeout=300)
        rows = [json.loads(line) for line in output.read_text().splitlines()]
        try:
            assert completed.returncode == 0 and not output.with_suffix(".stderr").read_bytes()
            assert len(rows) == 4 and finite(rows)
            assert rows[0]["source"] == args.source and rows[0]["case"] == case[1]
            assert rows[0]["tolerance"] == float(case[2])
            assert rows[0]["restart_interval"] == 25 and rows[0]["max_iterations"] == 1000
            assert rows[-1]["status"] == "ok"
            assert 0 <= rows[-1]["fresh_residual"] <= rows[-1]["allowed_residual"]
        except (AssertionError, KeyError):
            errors.append({"case": case, "kind": "accuracy diagnostic failed"})
        results.append({"case": case, "rows": rows, "output_sha256": sha(output)})
    if not errors:
        # Check the demonstrated benefit of tighter stopping on the unchanged
        # stress fixture, without asserting a general forward-error guarantee.
        loose, tight = [x["rows"][-1]["relative_solution_error"] for x in results[:2]]
        if tight >= loose / 10:
            errors.append({"kind": "tighter stopping did not reduce fixture error tenfold"})
    summary = {"plan": plan, "results": results, "errors": errors}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    with (root / "SHA256SUMS").open("x") as checksums:
        for path in sorted(root.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS":
                checksums.write(sha(path) + "  " + path.name + "\n")
    print(json.dumps(summary))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
