"""Validate and summarize a fixed alternating upstream development screen."""
import json
import math
import pathlib
import statistics
import sys

run = pathlib.Path(sys.argv[1])
lane = sys.argv[2] if len(sys.argv) > 2 else "screen"
assert lane in ("screen", "screen-admitted", "screen-parallel-admitted")
root = run / "output" / lane

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

old_dependencies = dependencies(run / "work" / lane / "baseline/Cargo.lock")
assert old_dependencies == dependencies(run / "work" / lane / "candidate/Cargo.lock")
assert old_dependencies == dependencies(pathlib.Path.cwd() / "Cargo.lock")


def read(name):
    rows = [json.loads(line) for line in (root / name).read_text().splitlines()]
    assert len(rows) == 20
    values = {(r["case"], r["component"], r["workers"]): r for r in rows}
    expected = {(case, component, workers)
                for case in ("path", "mixed", "dense", "weak", "fallback")
                for component in (False, True) for workers in (1, 4)}
    assert set(values) == expected
    for row in rows:
        assert row["certified"] and row["rhs"] == (1 if row["case"] == "fallback" else 4)
        assert all(math.isfinite(v) for v in row.values() if isinstance(v, float))
        assert all(v >= 0 for k, v in row.items() if k.endswith("_ns"))
        if row["case"] == "fallback":
            assert row["discarded_or_fallback"] == "strict-residual-rejection-then-direct"
            assert row["discarded_residual"] > 1e-8 * math.sqrt(2)
            assert row["residual"] <= 1e-8 * math.sqrt(2)
            assert row["coefficient_relative_error"] <= 1e-6
            assert sum(v for k, v in row.items() if k.endswith("_ns") and k != "total_ns") <= row["total_ns"]
        else:
            assert row["discarded_or_fallback"] == "not_exercised"
    return values


baseline = [read("baseline-%d.jsonl" % i) for i in range(1, 8)]
candidate = [read("candidate-%d.jsonl" % i) for i in range(1, 8)]
allocation = {v: read(v + "-alloc.jsonl") for v in ("baseline", "candidate")}
summary = []
for key in sorted(baseline[0]):
    numerical_fields = ["solution_fnv64", "iterations", "residual", "coefficient_relative_error"]
    if key[0] == "fallback":
        numerical_fields += ["discarded_iterations", "discarded_residual", "discarded_coefficient_relative_error"]
    for old, new in zip(baseline, candidate):
        for field in numerical_fields:
            assert old[key][field] == new[key][field], (key, field)
        for variant, rows in (("baseline", old), ("candidate", new)):
            for field in numerical_fields:
                assert rows[key][field] == allocation[variant][key][field], (key, variant, field)
            assert not rows[key]["allocation_instrumentation"]
            assert allocation[variant][key]["allocation_instrumentation"]
    phases = {}
    for field in sorted(f for f in candidate[0][key] if f.endswith("_ns")):
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
        "memory": {v: {field: value for field, value in allocation[v][key].items() if field.endswith("_requested_increment")} for v in allocation},
        "coefficient_relative_error": candidate[0][key]["coefficient_relative_error"],
    })
source = (run / "manifests/source-commit.txt").read_text().strip()
same_source = lane == "screen-parallel-admitted"
policies = {
    "screen": "original ordinary executor setup; baseline components serial vs candidate executor-aware",
    "screen-admitted": "matched serial setup: baseline legacy vs candidate admitted",
    "screen-parallel-admitted": "same-source executor setup: legacy vs CMG-owned admitted; runtime uncovered in both",
}
record = {
    "protocol": "ownership-development-v2-with-discarded-attempt",
    "lane": lane,
    "setup_policy": policies[lane],
    "source": source,
    "baseline": source if same_source else (run / "manifests/screen-reference/source.txt").read_text().strip(),
    "both_variants_join_workers": same_source,
    "candidate_runtime_policy": "estimated: 32 MiB per pool + 2 MiB heap and explicit 8 MiB stack per worker; not a bound",
    "baseline_runtime_policy": "same estimated policy as candidate" if same_source else "legacy unbudgeted pool and default stack size",
    "pairs": 140,
    "numerical_fields_identical": True,
    "scope": "Seven alternating process pairs; unpinned shared-host development evidence, not component promotion or complete-command fereg evidence. Allocation runs are separate. Original four cases unchanged; added caller-rejected multilevel attempt followed by complete retirement and direct rebuild. Only the added fallback total includes all solver-owned cleanup. Runtime allocation observations are not admission proofs. " + ("Both variants join workers." if same_source else "Baseline pool drop does not wait for workers; candidate drop does."),
    "cases": summary,
}
with (root / "summary.json").open("x") as output:
    json.dump(record, output, indent=2)
    output.write("\n")
print("CMG_OWNERSHIP_SCREEN_VALIDATE_SUCCESS pairs=140")
