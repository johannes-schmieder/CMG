"""Validate and summarize a fixed alternating upstream development screen."""
import json
import pathlib
import statistics
import sys

run = pathlib.Path(sys.argv[1])
root = run / "output" / "screen"

# Generated driver manifests must resolve the same non-CMG dependency versions
# as each other and the committed library lockfile. Parse only the package
# records needed here, keeping this runner compatible with SCC's system Python.
def dependencies(path):
    packages = {}
    for entry in path.read_text().split("[[package]]")[1:]:
        fields = {}
        for line in entry.splitlines():
            if line.startswith(("name = ", "version = ", "checksum = ")):
                key, value = line.split(" = ", 1)
                fields[key] = value.strip('"')
        if fields.get("name") not in ("cmg", "ownership-screen"):
            packages[fields["name"]] = (fields["version"], fields.get("checksum"))
    return packages

old_dependencies = dependencies(run / "work/screen/baseline/Cargo.lock")
assert old_dependencies == dependencies(run / "work/screen/candidate/Cargo.lock")
assert old_dependencies == dependencies(pathlib.Path.cwd() / "Cargo.lock")


def read(name):
    rows = [json.loads(line) for line in (root / name).read_text().splitlines()]
    assert len(rows) == 16
    values = {(r["case"], r["component"], r["workers"]): r for r in rows}
    assert len(values) == 16
    assert {k[0] for k in values} == {"path", "mixed", "dense", "weak"}
    assert all(r["certified"] and r["rhs"] == 4 for r in rows)
    return values


baseline = [read("baseline-%d.jsonl" % i) for i in range(1, 8)]
candidate = [read("candidate-%d.jsonl" % i) for i in range(1, 8)]
allocation = {v: read(v + "-alloc.jsonl") for v in ("baseline", "candidate")}
summary = []
for key in sorted(baseline[0]):
    for old, new in zip(baseline, candidate):
        for field in ("solution_fnv64", "iterations", "residual", "coefficient_relative_error"):
            assert old[key][field] == new[key][field], (key, field)
        for variant, rows in (("baseline", old), ("candidate", new)):
            assert rows[key]["solution_fnv64"] == allocation[variant][key]["solution_fnv64"]
            assert not rows[key]["allocation_instrumentation"]
    phases = {}
    for field in ("setup_ns", "workspace_ns", "solve_ns", "shutdown_ns", "total_ns"):
        old = [r[key][field] for r in baseline]
        new = [r[key][field] for r in candidate]
        ratios = [b / a for a, b in zip(old, new)]
        phases[field] = {
            "baseline_median": statistics.median(old),
            "candidate_median": statistics.median(new),
            "median_paired_ratio": statistics.median(ratios),
            "development_slowdown_over_5pct_and_10ms": statistics.median(new) > 1.05 * statistics.median(old) and statistics.median(new) - statistics.median(old) > 10_000_000,
        }
    summary.append({
        "case": key[0], "component": key[1], "workers": key[2],
        "phases": phases,
        "memory": {v: {field: allocation[v][key][field] for field in ("peak_requested_increment", "retained_requested_increment")} for v in allocation},
        "coefficient_relative_error": candidate[0][key]["coefficient_relative_error"],
    })
record = {
    "source": (run / "manifests/source-commit.txt").read_text().strip(),
    "baseline": (run / "manifests/screen-reference/source.txt").read_text().strip(),
    "pairs": 112,
    "numerical_fields_identical": True,
    "scope": "Seven alternating process pairs; unpinned shared-host development evidence, not component promotion or complete-command fereg evidence. Allocation runs are separate. No discarded/fallback attempt exercised.",
    "cases": summary,
}
with (root / "summary.json").open("x") as output:
    json.dump(record, output, indent=2)
    output.write("\n")
print("CMG_OWNERSHIP_SCREEN_VALIDATE_SUCCESS pairs=112")
