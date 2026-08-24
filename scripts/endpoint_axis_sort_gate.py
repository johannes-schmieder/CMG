import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/graph.rs")
WORKFLOW = Path(".github/workflows/endpoint-axis-sort.yml")
SCRIPT = Path("scripts/endpoint_axis_sort_gate.py")
RECORD = Path(".ci/performance/endpoint-axis-sort-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")


def run(command, *, env=None, timeout=7200, check=True):
    command = [str(item) for item in command]
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(
        command,
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
            f"command failed ({completed.returncode}): {' '.join(command)}"
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
            "contraction-subphase-profile",
        ],
        env=env,
    )
    release = target / "release"
    return {
        "hierarchy": release / "hierarchy-build",
        "contraction": release / "contraction-subphase-profile",
    }


def sample(kind, binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-endpoint-axis-{kind}-{tag}.time")
    completed = run(
        [
            "/usr/bin/time",
            "-v",
            "-o",
            time_path,
            binary,
            *arguments,
        ]
    )
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if kind == "contraction":
        payloads = [payload for payload in payloads if payload.get("record") == "case"]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected {kind} benchmark output: {payloads}")
    rss_match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)",
        time_path.read_text(),
    )
    if rss_match is None:
        raise RuntimeError("peak RSS missing from /usr/bin/time output")
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
    )
    for index, (label, binary) in enumerate(schedule):
        observation = sample(
            kind,
            binary,
            arguments,
            f"{name}-{label}-{index}",
        )
        (baseline_samples if label == "baseline" else candidate_samples).append(
            observation
        )

    stable = (
        ("case", "scale", "vertices", "edges", "repetitions")
        if kind == "hierarchy"
        else (
            "case",
            "scale",
            "vertices",
            "edges",
            "levels",
            "profiled_levels",
        )
    )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: {kind} changed {key}")

    metric = "median_ns" if kind == "hierarchy" else "production_total_ns"
    baseline_metric = statistics.median(item[metric] for item in baseline_samples)
    candidate_metric = statistics.median(item[metric] for item in candidate_samples)
    baseline_rss = max(item["peak_rss_kib"] for item in baseline_samples)
    candidate_rss = max(item["peak_rss_kib"] for item in candidate_samples)
    return {
        "arguments": arguments,
        "metric": metric,
        "baseline_metric": baseline_metric,
        "candidate_metric": candidate_metric,
        "candidate_over_baseline_time": candidate_metric / baseline_metric,
        "baseline_peak_rss_kib": baseline_rss,
        "candidate_peak_rss_kib": candidate_rss,
        "candidate_over_baseline_peak_rss": candidate_rss / baseline_rss,
        "metadata": {key: reference[key] for key in stable},
    }


OLD_SORT = '''fn sort_compact_edge_endpoints(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
}
'''
NEW_SORT = '''fn sort_compact_edge_endpoints(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(|edge| edge.u);
    let mut start = 0;
    while start < raw.len() {
        let first_endpoint = raw[start].u;
        let mut end = start + 1;
        while end < raw.len() && raw[end].u == first_endpoint {
            end += 1;
        }
        if end - start > 1 {
            raw[start..end].sort_unstable_by_key(|edge| edge.v);
        }
        start = end;
    }
}
'''
TEST_MODULE = '''

#[cfg(test)]
mod endpoint_axis_sort_tests {
    use super::{Edge, endpoint_key, sort_compact_edge_endpoints};

    fn generated_edges() -> Vec<Edge> {
        let mut edges = Vec::new();
        for index in 0..16_384_usize {
            let left = (37 * index + 11) % 4_003;
            let mut right = (97 * index + 29) % 4_003;
            if right == left {
                right = (right + 1) % 4_003;
            }
            for duplicate in 0..3_usize {
                let weight = 0.25 + ((index + 19 * duplicate) % 127) as f64 / 64.0;
                edges.push(Edge::from_internal_parts(left, right, weight).unwrap());
            }
        }
        edges.reverse();
        edges
    }

    #[test]
    fn endpoint_axis_sort_matches_packed_endpoint_order() {
        let mut candidate = generated_edges();
        let mut reference = candidate.clone();
        sort_compact_edge_endpoints(&mut candidate);
        reference.sort_unstable_by_key(endpoint_key);
        let candidate_keys: Vec<_> = candidate.iter().map(endpoint_key).collect();
        let reference_keys: Vec<_> = reference.iter().map(endpoint_key).collect();
        assert_eq!(candidate_keys, reference_keys);
    }
}
'''


def apply_candidate(source):
    if source.count(OLD_SORT) != 1:
        raise RuntimeError("compact endpoint sorter marker changed unexpectedly")
    candidate = source.replace(OLD_SORT, NEW_SORT, 1)
    if "mod endpoint_axis_sort_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    contraction_ratio = result.get("contraction_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    rss_ratio = result.get("worst_peak_rss_ratio", 1.0)
    checkpoint = f'''### Endpoint-axis compact sort checkpoint — 2026-08-24

- Global first-endpoint sorting followed by second-endpoint bucket sorting was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; canonical endpoint order and hierarchy metadata were unchanged.
- Geometric production-contraction / hierarchy-build ratios: `{contraction_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst contraction / hierarchy / peak-RSS ratios: `{result.get("worst_contraction_time_ratio", 1.0):.3f}x` / `{result.get("worst_hierarchy_time_ratio", 1.0):.3f}x` / `{rss_ratio:.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/endpoint-axis-sort-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Endpoint-axis compact sort checkpoint — 2026-08-24\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    if marker in plan:
        prefix, _, _ = plan.partition(marker)
        plan = prefix + marker + (
            "1. Re-profile current contraction subphases if endpoint-axis sorting is retained.\n"
            "2. Otherwise profile endpoint-key run structure before another sorting candidate.\n"
            "3. Refresh cumulative retained optimization and memory guidance.\n"
            "4. Run manual 1–32 thread qualification when suitable hardware is available.\n"
        )
    PLAN.write_text(plan)

    block = f'''## Endpoint-axis compact sort gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Production-contraction / hierarchy-build ratios: `{contraction_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{rss_ratio:.3f}x`.
- Evidence: `.ci/performance/endpoint-axis-sort-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Endpoint-axis compact sort gate\n"
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
result = {
    "schema_version": 1,
    "experiment": "endpoint-axis-compact-sort",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "contraction_cases": {},
    "hierarchy_cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-endpoint-axis-baseline"))
    SOURCE.write_text(apply_candidate(baseline_source))

    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run([
        "cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml",
        "--all", "--", "--check",
    ])
    run([
        "cargo", "clippy", "--all-targets", "--all-features",
        "--", "-D", "warnings",
    ])
    run([
        "cargo", "clippy", "--manifest-path", "benchmarks/Cargo.toml",
        "--all-targets", "--", "-D", "warnings",
    ])
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    result["validation"] = "success"

    candidate = build(Path("/tmp/cmg-endpoint-axis-candidate"))
    specs = (
        ("path-1m", "path", "1000000"),
        ("worker-firm-1.5m", "worker-firm", "500000"),
        ("worker-firm-3m", "worker-firm", "1000000"),
        ("dense-worker-firm-1.6m", "dense-worker-firm", "100000"),
    )
    for name, case, scale in specs:
        result["contraction_cases"][name] = compare(
            "contraction",
            baseline,
            candidate,
            [case, scale, "3", "comparison"],
            name,
        )
        result["hierarchy_cases"][name] = compare(
            "hierarchy",
            baseline,
            candidate,
            [case, scale, "3"],
            name,
        )

    contraction_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["contraction_cases"].values()
    ]
    hierarchy_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["hierarchy_cases"].values()
    ]
    active_names = (
        "worker-firm-1.5m",
        "worker-firm-3m",
        "dense-worker-firm-1.6m",
    )
    active_contraction = [
        result["contraction_cases"][name]["candidate_over_baseline_time"]
        for name in active_names
    ]
    rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (result["contraction_cases"], result["hierarchy_cases"])
        for case in collection.values()
    ]
    result["contraction_geometric_time_ratio"] = geometric(contraction_ratios)
    result["active_contraction_geometric_time_ratio"] = geometric(active_contraction)
    result["hierarchy_geometric_time_ratio"] = geometric(hierarchy_ratios)
    result["worst_contraction_time_ratio"] = max(contraction_ratios)
    result["worst_hierarchy_time_ratio"] = max(hierarchy_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["improved_active_contraction_count"] = sum(
        value < 1.0 for value in active_contraction
    )
    result["acceptance_limits"] = {
        "active_contraction_geometric_time_ratio_max": 0.98,
        "hierarchy_geometric_time_ratio_max": 0.995,
        "worst_contraction_time_ratio_max": 1.03,
        "worst_hierarchy_time_ratio_max": 1.03,
        "worst_peak_rss_ratio_max": 1.02,
        "improved_active_contraction_count_min": 3,
    }
    result["accepted"] = (
        result["active_contraction_geometric_time_ratio"] <= 0.98
        and result["hierarchy_geometric_time_ratio"] <= 0.995
        and result["worst_contraction_time_ratio"] <= 1.03
        and result["worst_hierarchy_time_ratio"] <= 1.03
        and result["worst_peak_rss_ratio"] <= 1.02
        and result["improved_active_contraction_count"] >= 3
    )
    result["decision_reason"] = (
        "full qualification passed; axis-local endpoint sorting materially improved worker-firm contraction and complete hierarchy construction without extra storage"
        if result["accepted"]
        else "correctness passed, but contraction, hierarchy, or memory gates were not all met"
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
    "contraction_geometric_time_ratio",
    "active_contraction_geometric_time_ratio",
    "hierarchy_geometric_time_ratio",
    "worst_contraction_time_ratio",
    "worst_hierarchy_time_ratio",
    "worst_peak_rss_ratio",
):
    result.setdefault(key, 1.0)
result.setdefault("improved_active_contraction_count", 0)
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
run([
    "git", "config", "user.email",
    "41898282+github-actions[bot]@users.noreply.github.com",
])
run(["git", "add", "-A"])
message = (
    "perf: retain endpoint-axis compact sorting"
    if result.get("accepted", False)
    else "perf: record endpoint-axis sort experiment"
)
run(["git", "commit", "-m", message])
for _ in range(8):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push endpoint-axis sort decision")

if result.get("validation") != "success":
    raise SystemExit(1)
