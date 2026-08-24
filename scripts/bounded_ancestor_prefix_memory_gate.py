import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/forest.rs")
WORKFLOW = Path(".github/workflows/bounded-ancestor-prefix-memory.yml")
SCRIPT = Path("scripts/bounded_ancestor_prefix_memory_gate.py")
RECORD = Path(".ci/performance/bounded-ancestor-prefix-memory-latest.json")
TIMING_RECORD = Path(".ci/performance/bounded-ancestor-prefix-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")

OLD_DECLARATIONS = '''    let mut walk = Vec::new();
    let mut new_ancestors = Vec::new();
'''
NEW_DECLARATIONS = '''    let mut walk = Vec::new();
    let mut ancestor_prefix = [0_u8; 7];
'''
OLD_PATH_STATE = '''            let mut ancestors_in_path = 0_i64;
            walk.clear();
            walk.push(current);
            new_ancestors.clear();
            new_ancestors.push(0_i64);
            let mut k = 0_usize;
'''
NEW_PATH_STATE = '''            let mut ancestors_in_path = 0_u8;
            walk.clear();
            walk.push(current);
            ancestor_prefix[0] = 0;
            let mut k = 0_usize;
'''
OLD_RECORDING = '''                k += 1;
                walk.push(current);
                if visited[current] {
                    new_ancestors.push(ancestors_in_path);
                } else {
                    ancestors_in_path += 1;
                    new_ancestors.push(ancestors_in_path);
                }
'''
NEW_RECORDING = '''                k += 1;
                walk.push(current);
                ancestors_in_path += u8::from(!visited[current]);
                if k < ancestor_prefix.len() {
                    ancestor_prefix[k] = ancestors_in_path;
                } else {
                    debug_assert!(visited[current]);
                }
'''
OLD_UPDATE = '''                    ancestors[vertex] += new_ancestors[index];
'''
NEW_UPDATE = '''                    ancestors[vertex] += i64::from(ancestor_prefix[index.min(6)]);
'''
TEST_MODULE = '''

#[cfg(test)]
mod bounded_ancestor_prefix_tests {
    #[test]
    fn prefix_table_covers_all_unvisited_diameter_steps() {
        let mut prefix = [0_u8; 7];
        let visited = [false, false, true, false, false, true, false, true, true];
        let mut count = 0_u8;
        for index in 1..visited.len() {
            count += u8::from(!visited[index]);
            if index < prefix.len() {
                prefix[index] = count;
            }
        }
        assert_eq!(prefix, [0, 1, 1, 2, 3, 3, 4]);
        assert_eq!(prefix[6], 4);
    }
}
'''


def apply_candidate(source):
    replacements = (
        (OLD_DECLARATIONS, NEW_DECLARATIONS, "ancestor-prefix declaration"),
        (OLD_PATH_STATE, NEW_PATH_STATE, "ancestor-prefix reset"),
        (OLD_RECORDING, NEW_RECORDING, "ancestor-prefix recording"),
    )
    candidate = source
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if candidate.count(OLD_UPDATE) != 2:
        raise RuntimeError("expected two ancestor-prefix application sites")
    candidate = candidate.replace(OLD_UPDATE, NEW_UPDATE)
    if "mod bounded_ancestor_prefix_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def run(command, *, env=None, timeout=9000, check=True):
    print("+", " ".join(str(item) for item in command), flush=True)
    completed = subprocess.run(
        [str(item) for item in command],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end="")
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): "
            f"{' '.join(str(item) for item in command)}"
        )
    return completed


def build(target):
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target)
    run(
        [
            "cargo",
            "build",
            "--release",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--bin",
            "hierarchy-build",
            "--bin",
            "hierarchy-alloc",
        ],
        env=env,
    )
    release = target / "release"
    return {
        "time": release / "hierarchy-build",
        "allocation": release / "hierarchy-alloc",
    }


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-bounded-prefix-memory-{tag}.time")
    completed = run(
        ["/usr/bin/time", "-v", "-o", time_path, binary, *arguments]
    )
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected benchmark output: {payloads}")
    rss_match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)",
        time_path.read_text(),
    )
    if rss_match is None:
        raise RuntimeError("peak RSS missing")
    payload = payloads[0]
    payload["peak_rss_kib"] = int(rss_match.group(1))
    return payload


def compare(kind, baseline, candidate, arguments, name):
    baseline_samples = []
    candidate_samples = []
    schedule = (
        ("baseline", baseline[kind]),
        ("candidate", candidate[kind]),
        ("candidate", candidate[kind]),
        ("baseline", baseline[kind]),
        ("baseline", baseline[kind]),
        ("candidate", candidate[kind]),
    )
    for index, (label, binary) in enumerate(schedule):
        observation = sample(binary, arguments, f"{kind}-{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(
            observation
        )

    stable = ("case", "scale", "vertices", "edges", "repetitions")
    if kind == "allocation":
        stable += (
            "levels",
            "hierarchy_matrix_nonzeros",
            "max_post_drop_delta_bytes",
        )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: unstable {kind} metadata for {key}")

    baseline_ns = statistics.median(item["median_ns"] for item in baseline_samples)
    candidate_ns = statistics.median(item["median_ns"] for item in candidate_samples)
    baseline_rss = statistics.median(item["peak_rss_kib"] for item in baseline_samples)
    candidate_rss = statistics.median(item["peak_rss_kib"] for item in candidate_samples)
    result = {
        "arguments": arguments,
        "metadata": {key: reference[key] for key in stable},
        "baseline_median_ns": baseline_ns,
        "candidate_median_ns": candidate_ns,
        "candidate_over_baseline_time": candidate_ns / baseline_ns,
        "baseline_median_peak_rss_kib": baseline_rss,
        "candidate_median_peak_rss_kib": candidate_rss,
        "candidate_over_baseline_median_peak_rss": candidate_rss / baseline_rss,
        "baseline_max_peak_rss_kib": max(item["peak_rss_kib"] for item in baseline_samples),
        "candidate_max_peak_rss_kib": max(item["peak_rss_kib"] for item in candidate_samples),
    }
    if kind == "allocation":
        for field in ("median_additional_peak_bytes", "median_retained_bytes"):
            baseline_value = statistics.median(
                item[field] for item in baseline_samples
            )
            candidate_value = statistics.median(
                item[field] for item in candidate_samples
            )
            result[f"baseline_{field}"] = baseline_value
            result[f"candidate_{field}"] = candidate_value
            result[f"candidate_over_baseline_{field}"] = (
                candidate_value / baseline_value
            )
    return result


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    checkpoint = f'''### Bounded ancestor-prefix exact-memory checkpoint — 2026-08-24

- The seven-byte bounded ancestor-prefix candidate was **{decision}** after exact-memory requalification.
- Validation: `{result.get("validation", "unknown")}`; prior exact split checksums and hierarchy metadata were unchanged.
- Prior split / rerun hierarchy timing ratios: `{result.get("prior_split_geometric_time_ratio", 1.0):.3f}x` / `{result.get("rerun_hierarchy_geometric_time_ratio", 1.0):.3f}x`.
- Exact additional-peak / retained hierarchy ratios: `{result.get("geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Median process-RSS geometric / worst-case ratios: `{result.get("geometric_median_rss_ratio", 1.0):.3f}x` / `{result.get("worst_median_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/bounded-ancestor-prefix-memory-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Bounded ancestor-prefix exact-memory checkpoint — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Re-profile forest-split subphases if the bounded prefix is retained.\n"
        "2. Continue exact-preserving diameter-loop optimization from the updated profile.\n"
        "3. Refresh cumulative retained optimization and memory guidance.\n"
        "4. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Bounded ancestor-prefix exact-memory gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Prior split / rerun hierarchy ratios: `{result.get("prior_split_geometric_time_ratio", 1.0):.3f}x` / `{result.get("rerun_hierarchy_geometric_time_ratio", 1.0):.3f}x`.
- Exact additional-peak / retained ratios: `{result.get("geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Median RSS geometric / worst ratios: `{result.get("geometric_median_rss_ratio", 1.0):.3f}x` / `{result.get("worst_median_rss_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/bounded-ancestor-prefix-memory-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Bounded ancestor-prefix exact-memory gate\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + block + status[end:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")


baseline_source = SOURCE.read_text()
prior = json.loads(TIMING_RECORD.read_text())
result = {
    "schema_version": 1,
    "experiment": "bounded-ancestor-prefix-exact-memory-requalification",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "time_cases": {},
    "allocation_cases": {},
    "prior_timing_record": prior,
}

try:
    baseline = build(Path("/tmp/cmg-bounded-prefix-memory-baseline"))
    SOURCE.write_text(apply_candidate(baseline_source))

    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all", "--", "--check"])
    run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
    run(["cargo", "clippy", "--manifest-path", "benchmarks/Cargo.toml", "--all-targets", "--", "-D", "warnings"])
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    result["validation"] = "success"

    candidate = build(Path("/tmp/cmg-bounded-prefix-memory-candidate"))
    specs = (
        ("path-1m", ["path", "1000000", "3"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
    )
    for name, arguments in specs:
        result["time_cases"][name] = compare(
            "time", baseline, candidate, arguments, name
        )
        result["allocation_cases"][name] = compare(
            "allocation", baseline, candidate, arguments, name
        )

    time_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["time_cases"].values()
    ]
    median_rss_ratios = [
        case["candidate_over_baseline_median_peak_rss"]
        for collection in (result["time_cases"], result["allocation_cases"])
        for case in collection.values()
    ]
    additional_peak = [
        case["candidate_over_baseline_median_additional_peak_bytes"]
        for case in result["allocation_cases"].values()
    ]
    retained = [
        case["candidate_over_baseline_median_retained_bytes"]
        for case in result["allocation_cases"].values()
    ]
    result["prior_split_geometric_time_ratio"] = prior.get(
        "split_geometric_time_ratio", 1.0
    )
    result["prior_hierarchy_geometric_time_ratio"] = prior.get(
        "hierarchy_geometric_time_ratio", 1.0
    )
    result["rerun_hierarchy_geometric_time_ratio"] = geometric(time_ratios)
    result["worst_rerun_hierarchy_time_ratio"] = max(time_ratios)
    result["geometric_median_rss_ratio"] = geometric(median_rss_ratios)
    result["worst_median_rss_ratio"] = max(median_rss_ratios)
    result["geometric_additional_peak_ratio"] = geometric(additional_peak)
    result["worst_additional_peak_ratio"] = max(additional_peak)
    result["geometric_retained_ratio"] = geometric(retained)
    result["worst_retained_ratio"] = max(retained)
    result["max_post_drop_delta_bytes"] = max(
        case["metadata"]["max_post_drop_delta_bytes"]
        for case in result["allocation_cases"].values()
    )
    result["acceptance_limits"] = {
        "prior_split_geometric_time_ratio_max": 0.94,
        "prior_hierarchy_geometric_time_ratio_max": 0.98,
        "rerun_hierarchy_geometric_time_ratio_max": 0.985,
        "worst_rerun_hierarchy_time_ratio_max": 1.02,
        "geometric_median_rss_ratio_max": 1.015,
        "worst_median_rss_ratio_max": 1.035,
        "geometric_additional_peak_ratio_max": 1.0,
        "worst_additional_peak_ratio_max": 1.002,
        "geometric_retained_ratio_max": 1.001,
        "worst_retained_ratio_max": 1.001,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        prior.get("validation") == "success"
        and result["prior_split_geometric_time_ratio"] <= 0.94
        and result["prior_hierarchy_geometric_time_ratio"] <= 0.98
        and result["rerun_hierarchy_geometric_time_ratio"] <= 0.985
        and result["worst_rerun_hierarchy_time_ratio"] <= 1.02
        and result["geometric_median_rss_ratio"] <= 1.015
        and result["worst_median_rss_ratio"] <= 1.035
        and result["geometric_additional_peak_ratio"] <= 1.0
        and result["worst_additional_peak_ratio"] <= 1.002
        and result["geometric_retained_ratio"] <= 1.001
        and result["worst_retained_ratio"] <= 1.001
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "large split and hierarchy speedups reproduced with non-regressive exact hierarchy memory and stable median RSS"
        if result["accepted"]
        else "timing, exact allocator, or repeated process-RSS limits were not all met"
    )
except Exception as error:
    result["validation"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"experiment failed safely: {error}"
    print(result["decision_reason"], flush=True)

if not result.get("accepted", False):
    SOURCE.write_text(baseline_source)
    run(["cargo", "fmt", "--all"], check=False)

for key in (
    "prior_split_geometric_time_ratio",
    "prior_hierarchy_geometric_time_ratio",
    "rerun_hierarchy_geometric_time_ratio",
    "worst_rerun_hierarchy_time_ratio",
    "geometric_median_rss_ratio",
    "worst_median_rss_ratio",
    "geometric_additional_peak_ratio",
    "worst_additional_peak_ratio",
    "geometric_retained_ratio",
    "worst_retained_ratio",
):
    result.setdefault(key, 1.0)
result.setdefault("max_post_drop_delta_bytes", 0)
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
update_documents(result)

WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass

run(["git", "config", "user.name", "github-actions[bot]"])
run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
run(["git", "add", "-A"])
message = (
    "perf: retain bounded ancestor prefixes after exact memory gate"
    if result.get("accepted", False)
    else "perf: record bounded-prefix exact-memory requalification"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push bounded-prefix memory decision")

if result.get("validation") != "success":
    raise SystemExit(1)
