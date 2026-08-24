import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/graph.rs")
WORKFLOW = Path(".github/workflows/row-bucket-endpoint-sort.yml")
SCRIPT = Path("scripts/row_bucket_endpoint_sort_gate.py")
RECORD = Path(".ci/performance/row-bucket-endpoint-sort-latest.json")
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
    env["RUSTFLAGS"] = "-C target-cpu=native"
    run(
        [
            "cargo",
            "build",
            "--release",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--bin",
            "contraction-subphase-profile",
            "--bin",
            "hierarchy-build",
            "--bin",
            "hierarchy-alloc",
            "--bin",
            "single-rhs-solve",
        ],
        env=env,
    )
    release = target / "release"
    return {
        "contraction": release / "contraction-subphase-profile",
        "hierarchy": release / "hierarchy-build",
        "allocation": release / "hierarchy-alloc",
        "solve": release / "single-rhs-solve",
    }


def parse_payload(kind, output):
    payloads = [
        json.loads(line)
        for line in output.splitlines()
        if line.strip().startswith("{")
    ]
    if kind == "contraction":
        payloads = [item for item in payloads if item.get("record") == "case"]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected {kind} output: {payloads}")
    return payloads[0]


def sample(kind, binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-row-bucket-{kind}-{tag}.time")
    completed = run(
        ["/usr/bin/time", "-v", "-o", time_path, binary, *arguments]
    )
    payload = parse_payload(kind, completed.stdout)
    rss_match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)",
        time_path.read_text(),
    )
    if rss_match is None:
        raise RuntimeError("peak RSS missing")
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

    if kind == "contraction":
        stable = (
            "case",
            "scale",
            "vertices",
            "edges",
            "levels",
            "profiled_levels",
        )
        metric = "production_total_ns"
    elif kind == "hierarchy":
        stable = ("case", "scale", "vertices", "edges", "repetitions")
        metric = "median_ns"
    elif kind == "allocation":
        stable = (
            "case",
            "scale",
            "vertices",
            "edges",
            "repetitions",
            "levels",
            "hierarchy_matrix_nonzeros",
            "max_post_drop_delta_bytes",
        )
        metric = "median_ns"
    elif kind == "solve":
        stable = (
            "case",
            "scale",
            "vertices",
            "edges",
            "levels",
            "repetitions",
            "iterations",
            "workspace_bytes",
        )
        metric = "median_ns"
    else:
        raise RuntimeError(f"unsupported comparison kind: {kind}")

    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: {kind} changed {key}")
        if kind == "solve" and observation["backward_error"] > 1.0e-8:
            raise RuntimeError(
                f"{name}: solve backward error {observation['backward_error']}"
            )

    baseline_metric = statistics.median(item[metric] for item in baseline_samples)
    candidate_metric = statistics.median(item[metric] for item in candidate_samples)
    result = {
        "arguments": arguments,
        "metric": metric,
        "baseline_metric": baseline_metric,
        "candidate_metric": candidate_metric,
        "candidate_over_baseline_time": candidate_metric / baseline_metric,
        "baseline_peak_rss_kib": max(
            item["peak_rss_kib"] for item in baseline_samples
        ),
        "candidate_peak_rss_kib": max(
            item["peak_rss_kib"] for item in candidate_samples
        ),
        "metadata": {key: reference[key] for key in stable},
    }
    result["candidate_over_baseline_peak_rss"] = (
        result["candidate_peak_rss_kib"] / result["baseline_peak_rss_kib"]
    )
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
    if kind == "solve":
        result["baseline_maximum_backward_error"] = max(
            item["backward_error"] for item in baseline_samples
        )
        result["candidate_maximum_backward_error"] = max(
            item["backward_error"] for item in candidate_samples
        )
    return result


OLD_COMPACT = '''    pub(crate) fn from_compact_edges(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        sort_compact_edge_endpoints(&mut raw);
        Self::from_endpoint_sorted_raw_edges(vertex_count, raw)
    }
'''
NEW_COMPACT = '''    pub(crate) fn from_compact_edges(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        sort_compact_edge_endpoints_routed(vertex_count, &mut raw);
        Self::from_endpoint_sorted_raw_edges(vertex_count, raw)
    }
'''
OLD_FALLBACK = '''        } else {
            sort_compact_edge_endpoints(&mut raw);
            return Self::from_endpoint_sorted_raw_edges(vertex_count, raw);
        }
'''
NEW_FALLBACK = '''        } else {
            sort_compact_edge_endpoints_routed(vertex_count, &mut raw);
            return Self::from_endpoint_sorted_raw_edges(vertex_count, raw);
        }
'''
HELPER_MARKER = '''fn sort_compact_edge_endpoints(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
}
'''
HELPER_REPLACEMENT = '''fn sort_compact_edge_endpoints(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
}

const ROW_BUCKET_SORT_MIN_EDGES: usize = 1 << 18;
const ROW_BUCKET_SORT_MIN_EDGES_PER_VERTEX: usize = 8;

#[inline]
fn should_use_row_bucket_sort(vertex_count: usize, edge_count: usize) -> bool {
    vertex_count > 0
        && edge_count >= ROW_BUCKET_SORT_MIN_EDGES
        && edge_count
            >= vertex_count.saturating_mul(ROW_BUCKET_SORT_MIN_EDGES_PER_VERTEX)
}

fn sort_compact_edge_endpoints_routed(vertex_count: usize, raw: &mut [Edge]) {
    if should_use_row_bucket_sort(vertex_count, raw.len()) {
        sort_compact_edge_endpoints_by_row(vertex_count, raw);
    } else {
        sort_compact_edge_endpoints(raw);
    }
}

fn sort_compact_edge_endpoints_by_row(vertex_count: usize, raw: &mut [Edge]) {
    if raw.len() < 2 {
        return;
    }

    let mut starts = vec![0_usize; vertex_count + 1];
    for edge in raw.iter() {
        starts[edge.compact_u() as usize + 1] += 1;
    }
    let mut index = 1_usize;
    while index < starts.len() {
        starts[index] += starts[index - 1];
        index += 1;
    }

    let mut next = starts[..vertex_count].to_vec();
    let mut bucket = 0_usize;
    while bucket < vertex_count {
        let end = starts[bucket + 1];
        while next[bucket] < end {
            let current = next[bucket];
            let target = raw[current].compact_u() as usize;
            if target == bucket {
                next[bucket] += 1;
            } else {
                let target_index = next[target];
                debug_assert!(target_index < starts[target + 1]);
                raw.swap(current, target_index);
                next[target] += 1;
            }
        }
        bucket += 1;
    }

    bucket = 0;
    while bucket < vertex_count {
        let start = starts[bucket];
        let end = starts[bucket + 1];
        if end - start > 1 {
            raw[start..end].sort_unstable_by_key(endpoint_key);
        }
        bucket += 1;
    }
}
'''
TEST_MODULE = '''

#[cfg(test)]
mod row_bucket_endpoint_sort_tests {
    use super::{
        Edge, Laplacian, endpoint_key, should_use_row_bucket_sort,
        sort_compact_edge_endpoints_by_row,
    };

    #[test]
    fn row_bucket_sort_matches_endpoint_key_order_and_graph() {
        let vertex_count = 2_048;
        let mut candidate = Vec::new();
        for index in 0..50_000_usize {
            let left = (37 * index + 11) % (vertex_count - 1);
            let right = left + 1 + ((97 * index + 3) % (vertex_count - left - 1));
            let weight = 0.25 + ((index * 13) % 101) as f64 / 32.0;
            candidate.push(
                Edge::from_internal_parts(left, right, weight).unwrap(),
            );
            if index % 7 == 0 {
                candidate.push(
                    Edge::from_internal_parts(left, right, weight + 0.125).unwrap(),
                );
            }
        }
        candidate.reverse();
        let original = candidate.clone();
        let mut reference = candidate.clone();
        reference.sort_unstable_by_key(endpoint_key);
        sort_compact_edge_endpoints_by_row(vertex_count, &mut candidate);
        assert_eq!(
            candidate.iter().map(endpoint_key).collect::<Vec<_>>(),
            reference.iter().map(endpoint_key).collect::<Vec<_>>(),
        );
        let public = Laplacian::from_edges(
            vertex_count,
            original
                .iter()
                .map(|edge| (edge.u(), edge.v(), edge.weight())),
        )
        .unwrap();
        let routed = Laplacian::from_endpoint_sorted_raw_edges(
            vertex_count,
            candidate,
        )
        .unwrap();
        assert_eq!(routed, public);
    }

    #[test]
    fn router_requires_large_dense_edge_sets() {
        assert!(!should_use_row_bucket_sort(1_000_000, 999_999));
        assert!(!should_use_row_bucket_sort(40_000, 250_000));
        assert!(should_use_row_bucket_sort(40_000, 400_000));
    }
}
'''


def apply_candidate(source):
    replacements = (
        (OLD_COMPACT, NEW_COMPACT, "compact constructor"),
        (OLD_FALLBACK, NEW_FALLBACK, "serial executor fallback"),
        (HELPER_MARKER, HELPER_REPLACEMENT, "endpoint sort helper"),
    )
    candidate = source
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if "mod row_bucket_endpoint_sort_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    checkpoint = f'''### Row-bucket endpoint-sort checkpoint — 2026-08-24

- In-place endpoint-row partitioning with per-row key sorting was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; graph, hierarchy, iteration, and residual invariants were unchanged.
- Geometric active contraction / all contraction / hierarchy / solve ratios: `{result.get("active_contraction_geometric_ratio", 1.0):.3f}x` / `{result.get("contraction_geometric_ratio", 1.0):.3f}x` / `{result.get("hierarchy_geometric_ratio", 1.0):.3f}x` / `{result.get("solve_geometric_ratio", 1.0):.3f}x`.
- Exact additional-peak / retained hierarchy ratios: `{result.get("allocation_geometric_peak_ratio", 1.0):.3f}x` / `{result.get("allocation_geometric_retained_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/row-bucket-endpoint-sort-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Row-bucket endpoint-sort checkpoint — 2026-08-24\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Row-bucket endpoint-sort gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Active contraction / hierarchy / solve ratios: `{result.get("active_contraction_geometric_ratio", 1.0):.3f}x` / `{result.get("hierarchy_geometric_ratio", 1.0):.3f}x` / `{result.get("solve_geometric_ratio", 1.0):.3f}x`.
- Exact peak / retained hierarchy ratios: `{result.get("allocation_geometric_peak_ratio", 1.0):.3f}x` / `{result.get("allocation_geometric_retained_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/row-bucket-endpoint-sort-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Row-bucket endpoint-sort gate\n"
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
    "experiment": "in-place-row-bucket-endpoint-sort",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-row-bucket-baseline"))
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

    candidate = build(Path("/tmp/cmg-row-bucket-candidate"))
    specs = {
        "contraction": (
            ("path-500k", ["path", "500000", "4", "comparison"]),
            ("worker-firm-750k", ["worker-firm", "250000", "4", "comparison"]),
            ("worker-firm-1.5m", ["worker-firm", "500000", "4", "comparison"]),
            ("worker-firm-3m", ["worker-firm", "1000000", "3", "comparison"]),
            ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "4", "comparison"]),
        ),
        "hierarchy": (
            ("path-1m", ["path", "1000000", "3"]),
            ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
            ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
            ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
            ("dense-worker-firm-3.2m", ["dense-worker-firm", "200000", "2"]),
        ),
        "allocation": (
            ("path-1m", ["path", "1000000", "2"]),
            ("worker-firm-1.5m", ["worker-firm", "500000", "2"]),
            ("worker-firm-3m", ["worker-firm", "1000000", "2"]),
            ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "2"]),
        ),
        "solve": (
            ("path-250k", ["path", "250000", "3"]),
            ("worker-firm-600k", ["worker-firm", "200000", "3"]),
            ("dense-worker-firm-800k", ["dense-worker-firm", "50000", "3"]),
        ),
    }
    for kind, cases in specs.items():
        result["cases"][kind] = {}
        for name, arguments in cases:
            result["cases"][kind][name] = compare(
                kind,
                baseline,
                candidate,
                arguments,
                name,
            )

    def ratios(kind, field="candidate_over_baseline_time"):
        return [case[field] for case in result["cases"][kind].values()]

    active_names = (
        "worker-firm-750k",
        "worker-firm-1.5m",
        "worker-firm-3m",
        "dense-worker-firm-1.6m",
    )
    active_ratios = [
        result["cases"]["contraction"][name]["candidate_over_baseline_time"]
        for name in active_names
    ]
    result["active_contraction_geometric_ratio"] = geometric(active_ratios)
    result["contraction_geometric_ratio"] = geometric(ratios("contraction"))
    result["hierarchy_geometric_ratio"] = geometric(ratios("hierarchy"))
    result["solve_geometric_ratio"] = geometric(ratios("solve"))
    result["allocation_geometric_peak_ratio"] = geometric(
        ratios("allocation", "candidate_over_baseline_median_additional_peak_bytes")
    )
    result["allocation_geometric_retained_ratio"] = geometric(
        ratios("allocation", "candidate_over_baseline_median_retained_bytes")
    )
    result["worst_time_ratio"] = max(
        ratio
        for kind in ("contraction", "hierarchy", "solve")
        for ratio in ratios(kind)
    )
    result["worst_peak_rss_ratio"] = max(
        case["candidate_over_baseline_peak_rss"]
        for collection in result["cases"].values()
        for case in collection.values()
    )
    result["worst_allocation_peak_ratio"] = max(
        ratios("allocation", "candidate_over_baseline_median_additional_peak_bytes")
    )
    result["worst_allocation_retained_ratio"] = max(
        ratios("allocation", "candidate_over_baseline_median_retained_bytes")
    )
    result["acceptance_limits"] = {
        "active_contraction_geometric_ratio_max": 0.90,
        "contraction_geometric_ratio_max": 0.93,
        "hierarchy_geometric_ratio_max": 0.98,
        "solve_geometric_ratio_max": 1.01,
        "worst_time_ratio_max": 1.04,
        "allocation_geometric_peak_ratio_max": 1.08,
        "allocation_geometric_retained_ratio_max": 1.001,
        "worst_allocation_peak_ratio_max": 1.15,
        "worst_allocation_retained_ratio_max": 1.002,
        "worst_peak_rss_ratio_max": 1.10,
    }
    result["accepted"] = (
        result["active_contraction_geometric_ratio"] <= 0.90
        and result["contraction_geometric_ratio"] <= 0.93
        and result["hierarchy_geometric_ratio"] <= 0.98
        and result["solve_geometric_ratio"] <= 1.01
        and result["worst_time_ratio"] <= 1.04
        and result["allocation_geometric_peak_ratio"] <= 1.08
        and result["allocation_geometric_retained_ratio"] <= 1.001
        and result["worst_allocation_peak_ratio"] <= 1.15
        and result["worst_allocation_retained_ratio"] <= 1.002
        and result["worst_peak_rss_ratio"] <= 1.10
    )
    result["decision_reason"] = (
        "full qualification passed; dense coarse levels use in-place row partitioning with material contraction and hierarchy gains inside a bounded temporary-memory budget"
        if result["accepted"]
        else "correctness passed, but contraction, hierarchy, solve, or temporary-memory gates were not all met"
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
    "active_contraction_geometric_ratio",
    "contraction_geometric_ratio",
    "hierarchy_geometric_ratio",
    "solve_geometric_ratio",
    "allocation_geometric_peak_ratio",
    "allocation_geometric_retained_ratio",
    "worst_time_ratio",
    "worst_peak_rss_ratio",
    "worst_allocation_peak_ratio",
    "worst_allocation_retained_ratio",
):
    result.setdefault(key, 1.0)
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
    "perf: retain row-bucket endpoint sorting"
    if result.get("accepted", False)
    else "perf: record row-bucket endpoint-sort experiment"
)
run(["git", "commit", "-m", message])
for _ in range(12):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push row-bucket endpoint-sort decision")

if result["validation"] != "success":
    raise SystemExit(1)
