import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/graph.rs")
WORKFLOW = Path(".github/workflows/local-duplicate-merge.yml")
SCRIPT = Path("scripts/local_duplicate_merge_gate.py")
RECORD = Path(".ci/performance/local-duplicate-merge-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")


def run(command, *, env=None, timeout=7200, check=True):
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
    time_path = Path(f"/tmp/cmg-local-merge-{kind}-{tag}.time")
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
        raise RuntimeError(f"unexpected {kind} output: {payloads}")
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

    if kind == "hierarchy":
        stable = ("case", "scale", "vertices", "edges", "repetitions")
        metric = "median_ns"
    else:
        stable = ("case", "scale", "vertices", "edges", "levels", "profiled_levels")
        metric = "production_total_ns"
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: {kind} changed {key}")

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


OLD_COMPACT = '''    pub(crate) fn from_compact_edges(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        sort_compact_edges_two_stage(&mut raw);
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
'''
NEW_COMPACT = '''    pub(crate) fn from_compact_edges(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        sort_compact_edge_endpoints(&mut raw);
        Self::from_endpoint_sorted_raw_edges(vertex_count, raw)
    }
'''
OLD_PARALLEL_ELSE = '''        } else {
            sort_compact_edges_two_stage(&mut raw);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
'''
NEW_PARALLEL_ELSE = '''        } else {
            sort_compact_edge_endpoints(&mut raw);
            return Self::from_endpoint_sorted_raw_edges(vertex_count, raw);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
'''
OLD_SIGNATURE = '''    fn from_sorted_raw_edges(vertex_count: usize, mut raw: Vec<Edge>) -> Result<Self, CmgError> {
'''
NEW_SIGNATURE = '''    fn from_endpoint_sorted_raw_edges(
        vertex_count: usize,
        raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        Self::from_sorted_raw_edges_with_mode(vertex_count, raw, false)
    }

    fn from_sorted_raw_edges(vertex_count: usize, raw: Vec<Edge>) -> Result<Self, CmgError> {
        Self::from_sorted_raw_edges_with_mode(vertex_count, raw, true)
    }

    fn from_sorted_raw_edges_with_mode(
        vertex_count: usize,
        mut raw: Vec<Edge>,
        weights_are_sorted: bool,
    ) -> Result<Self, CmgError> {
'''
OLD_LOOP = '''        let mut read_index = 0;
        let mut write_index = 0;
        while read_index < raw.len() {
            let u = raw[read_index].u;
            let v = raw[read_index].v;
            let mut sum = 0.0;
            let mut correction = 0.0;
            while read_index < raw.len() && raw[read_index].u == u && raw[read_index].v == v {
                compensated_add(&mut sum, &mut correction, raw[read_index].weight);
                read_index += 1;
            }
            let weight = sum + correction;
            if !weight.is_finite() || weight <= 0.0 {
                return Err(CmgError::InvalidEdgeWeight {
                    u: u as usize,
                    v: v as usize,
                    weight,
                });
            }
            raw[write_index] = Edge { u, v, weight };
            diagonal[u as usize] += weight;
            diagonal[v as usize] += weight;
            write_index += 1;
        }
'''
NEW_LOOP = '''        let mut write_index = 0;
        if weights_are_sorted {
            let mut read_index = 0;
            while read_index < raw.len() {
                let u = raw[read_index].u;
                let v = raw[read_index].v;
                let mut sum = 0.0;
                let mut correction = 0.0;
                while read_index < raw.len()
                    && raw[read_index].u == u
                    && raw[read_index].v == v
                {
                    compensated_add(&mut sum, &mut correction, raw[read_index].weight);
                    read_index += 1;
                }
                write_merged_edge(
                    &mut raw,
                    &mut diagonal,
                    write_index,
                    u,
                    v,
                    sum + correction,
                )?;
                write_index += 1;
            }
        } else {
            let mut group_start = 0;
            while group_start < raw.len() {
                let u = raw[group_start].u;
                let v = raw[group_start].v;
                let mut group_end = group_start + 1;
                while group_end < raw.len()
                    && raw[group_end].u == u
                    && raw[group_end].v == v
                {
                    group_end += 1;
                }
                if group_end - group_start > 1 {
                    raw[group_start..group_end]
                        .sort_unstable_by(|left, right| left.weight.total_cmp(&right.weight));
                }
                let mut sum = 0.0;
                let mut correction = 0.0;
                for edge_index in group_start..group_end {
                    compensated_add(&mut sum, &mut correction, raw[edge_index].weight);
                }
                write_merged_edge(
                    &mut raw,
                    &mut diagonal,
                    write_index,
                    u,
                    v,
                    sum + correction,
                )?;
                write_index += 1;
                group_start = group_end;
            }
        }
'''
OLD_SORT_HELPER = '''fn sort_compact_edges_two_stage(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
    let mut start = 0;
    while start < raw.len() {
        let key = endpoint_key(&raw[start]);
        let mut end = start + 1;
        while end < raw.len() && endpoint_key(&raw[end]) == key {
            end += 1;
        }
        if end - start > 1 {
            raw[start..end].sort_unstable_by(|left, right| left.weight.total_cmp(&right.weight));
        }
        start = end;
    }
}
'''
NEW_SORT_HELPER = '''fn sort_compact_edge_endpoints(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
}

fn write_merged_edge(
    raw: &mut [Edge],
    diagonal: &mut [f64],
    write_index: usize,
    u: u32,
    v: u32,
    weight: f64,
) -> Result<(), CmgError> {
    if !weight.is_finite() || weight <= 0.0 {
        return Err(CmgError::InvalidEdgeWeight {
            u: u as usize,
            v: v as usize,
            weight,
        });
    }
    raw[write_index] = Edge { u, v, weight };
    diagonal[u as usize] += weight;
    diagonal[v as usize] += weight;
    Ok(())
}
'''
TEST_MODULE = '''

#[cfg(test)]
mod local_duplicate_merge_tests {
    use super::{Laplacian, sort_compact_edge_endpoints};

    #[test]
    fn endpoint_only_compact_path_matches_public_total_order_path() {
        let edges = vec![
            (4, 1, 4.0),
            (1, 4, 0.25),
            (3, 0, 8.0),
            (4, 1, 2.0),
            (0, 3, 0.5),
            (2, 5, 1.0),
        ];
        let public = Laplacian::from_edges(6, edges.clone()).unwrap();
        let mut compact = edges
            .into_iter()
            .map(|(u, v, weight)| super::Edge::from_internal_parts(u, v, weight).unwrap())
            .collect::<Vec<_>>();
        sort_compact_edge_endpoints(&mut compact);
        let local = Laplacian::from_endpoint_sorted_raw_edges(6, compact).unwrap();
        assert_eq!(local, public);
    }
}
'''


def apply_candidate(source):
    replacements = (
        (OLD_COMPACT, NEW_COMPACT, "compact constructor"),
        (OLD_PARALLEL_ELSE, NEW_PARALLEL_ELSE, "parallel serial fallback"),
        (OLD_SIGNATURE, NEW_SIGNATURE, "sorted constructor signature"),
        (OLD_LOOP, NEW_LOOP, "duplicate merge loop"),
        (OLD_SORT_HELPER, NEW_SORT_HELPER, "compact sorting helper"),
    )
    candidate = source
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} source marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if "mod local_duplicate_merge_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    accepted = result.get("accepted", False)
    decision = "retained" if accepted else "not retained"
    contraction_ratio = result.get("contraction_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    rss_ratio = result.get("worst_peak_rss_ratio", 1.0)
    checkpoint = f'''### Cache-local duplicate merge checkpoint — 2026-08-23

- Sorting and summing duplicate compact edges within one local group traversal was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric production-contraction / hierarchy-build ratios: `{contraction_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{rss_ratio:.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/local-duplicate-merge-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Cache-local duplicate merge checkpoint — 2026-08-23\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Cache-local duplicate merge gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Production-contraction / hierarchy-build ratios: `{contraction_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{rss_ratio:.3f}x`.
- Evidence: `.ci/performance/local-duplicate-merge-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Cache-local duplicate merge gate\n"
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
    "experiment": "cache-local-duplicate-merge",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "contraction_cases": {},
    "hierarchy_cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-local-merge-baseline"))
    SOURCE.write_text(apply_candidate(baseline_source))

    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(
        [
            "cargo",
            "fmt",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--all",
            "--",
            "--check",
        ]
    )
    run(
        [
            "cargo",
            "clippy",
            "--all-targets",
            "--all-features",
            "--",
            "-D",
            "warnings",
        ]
    )
    run(
        [
            "cargo",
            "clippy",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ]
    )
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    result["validation"] = "success"

    candidate = build(Path("/tmp/cmg-local-merge-candidate"))
    contraction_specs = (
        ("path-500k", ["path", "500000", "3", "comparison"]),
        ("worker-firm-750k", ["worker-firm", "250000", "3", "comparison"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3", "comparison"]),
        ("dense-worker-firm-800k", ["dense-worker-firm", "50000", "3", "comparison"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3", "comparison"]),
    )
    hierarchy_specs = (
        ("path-1m", ["path", "1000000", "3"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
    )
    for name, arguments in contraction_specs:
        result["contraction_cases"][name] = compare(
            "contraction", baseline, candidate, arguments, name
        )
    for name, arguments in hierarchy_specs:
        result["hierarchy_cases"][name] = compare(
            "hierarchy", baseline, candidate, arguments, name
        )

    contraction_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["contraction_cases"].values()
    ]
    hierarchy_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["hierarchy_cases"].values()
    ]
    rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (result["contraction_cases"], result["hierarchy_cases"])
        for case in collection.values()
    ]
    result["contraction_geometric_time_ratio"] = geometric(contraction_ratios)
    result["hierarchy_geometric_time_ratio"] = geometric(hierarchy_ratios)
    result["worst_contraction_time_ratio"] = max(contraction_ratios)
    result["worst_hierarchy_time_ratio"] = max(hierarchy_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["improved_contraction_case_count"] = sum(
        value < 1.0 for value in contraction_ratios
    )
    result["acceptance_limits"] = {
        "contraction_geometric_time_ratio_max": 0.98,
        "hierarchy_geometric_time_ratio_max": 0.995,
        "worst_contraction_time_ratio_max": 1.04,
        "worst_hierarchy_time_ratio_max": 1.03,
        "worst_peak_rss_ratio_max": 1.02,
        "improved_contraction_case_count_min": 4,
    }
    result["accepted"] = (
        result["contraction_geometric_time_ratio"] <= 0.98
        and result["hierarchy_geometric_time_ratio"] <= 0.995
        and result["worst_contraction_time_ratio"] <= 1.04
        and result["worst_hierarchy_time_ratio"] <= 1.03
        and result["worst_peak_rss_ratio"] <= 1.02
        and result["improved_contraction_case_count"] >= 4
    )
    result["decision_reason"] = (
        "full qualification passed; endpoint groups were sorted and summed cache-locally with stable hierarchy gains"
        if result["accepted"]
        else "qualification passed, but local duplicate merging did not improve contraction and hierarchy timing consistently enough"
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
    "hierarchy_geometric_time_ratio",
    "worst_contraction_time_ratio",
    "worst_hierarchy_time_ratio",
    "worst_peak_rss_ratio",
):
    result.setdefault(key, 1.0)
result.setdefault("improved_contraction_case_count", 0)
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
run(
    [
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ]
)
run(["git", "add", "-A"])
message = (
    "perf: retain cache-local duplicate merging"
    if result.get("accepted", False)
    else "perf: record cache-local duplicate merge experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push cache-local duplicate merge decision")
