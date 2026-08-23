import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
GRAPH = Path("src/graph.rs")
COARSEN = Path("src/coarsen.rs")
WORKFLOW = Path(".github/workflows/trusted-compact-contraction.yml")
SCRIPT = Path("scripts/trusted_compact_contraction_gate.py")
RECORD = Path(".ci/performance/trusted-compact-contraction-latest.json")
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
            f"command failed ({completed.returncode}): {' '.join(str(item) for item in command)}"
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
    time_path = Path(f"/tmp/cmg-trusted-edge-{kind}-{tag}.time")
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
        observation = sample(kind, binary, arguments, f"{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(observation)

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


EDGE_MARKER = '''        Ok(Self { u, v, weight })
    }
}
'''
EDGE_REPLACEMENT = '''        Ok(Self { u, v, weight })
    }

    /// Construct an edge from compact aggregate labels whose range and weight
    /// invariants were already established by hierarchy construction.
    #[inline]
    pub(crate) fn from_validated_compact_parts(left: u32, right: u32, weight: f64) -> Self {
        debug_assert!(left != right);
        debug_assert!(weight.is_finite() && weight > 0.0);
        let (u, v) = if left < right {
            (left, right)
        } else {
            (right, left)
        };
        Self { u, v, weight }
    }
}
'''
SERIAL_COMPACT_OLD = '''            LabelStorage::Compact(labels) => {
                for edge in graph.edges() {
                    let left = labels[edge.u()] as usize;
                    let right = labels[edge.v()] as usize;
                    if left != right {
                        coarse_edges.push(Edge::from_internal_parts(left, right, edge.weight())?);
                    }
                }
            }
'''
SERIAL_COMPACT_NEW = '''            LabelStorage::Compact(labels) => {
                for edge in graph.edges() {
                    let left = labels[edge.u()];
                    let right = labels[edge.v()];
                    if left != right {
                        coarse_edges.push(Edge::from_validated_compact_parts(
                            left,
                            right,
                            edge.weight(),
                        ));
                    }
                }
            }
'''
PARALLEL_OLD = '''        let coarse_edges: Result<Vec<Edge>, CmgError> = executor.install(|| match &self.labels {
            LabelStorage::Compact(labels) => graph
                .edges()
                .par_iter()
                .filter_map(|edge| {
                    let left = labels[edge.u()] as usize;
                    let right = labels[edge.v()] as usize;
                    (left != right).then(|| Edge::from_internal_parts(left, right, edge.weight()))
                })
                .collect(),
            LabelStorage::Native(labels) => graph
                .edges()
                .par_iter()
                .filter_map(|edge| {
                    let left = labels[edge.u()];
                    let right = labels[edge.v()];
                    (left != right).then(|| Edge::from_internal_parts(left, right, edge.weight()))
                })
                .collect(),
        });
        Laplacian::from_compact_edges_with_executor(
            self.coarse_dimension(),
            coarse_edges?,
            executor,
        )
'''
PARALLEL_NEW = '''        let coarse_edges = match &self.labels {
            LabelStorage::Compact(labels) => executor.install(|| {
                graph
                    .edges()
                    .par_iter()
                    .filter_map(|edge| {
                        let left = labels[edge.u()];
                        let right = labels[edge.v()];
                        (left != right).then(|| {
                            Edge::from_validated_compact_parts(left, right, edge.weight())
                        })
                    })
                    .collect::<Vec<_>>()
            }),
            LabelStorage::Native(labels) => executor.install(|| {
                graph
                    .edges()
                    .par_iter()
                    .filter_map(|edge| {
                        let left = labels[edge.u()];
                        let right = labels[edge.v()];
                        (left != right)
                            .then(|| Edge::from_internal_parts(left, right, edge.weight()))
                    })
                    .collect::<Result<Vec<_>, CmgError>>()
            })?,
        };
        Laplacian::from_compact_edges_with_executor(
            self.coarse_dimension(),
            coarse_edges,
            executor,
        )
'''
TEST_MODULE = '''

#[cfg(test)]
mod validated_compact_edge_tests {
    use super::Edge;

    #[test]
    fn validated_compact_constructor_canonicalizes_endpoints() {
        let edge = Edge::from_validated_compact_parts(9, 3, 2.5);
        assert_eq!(edge.u(), 3);
        assert_eq!(edge.v(), 9);
        assert_eq!(edge.weight().to_bits(), 2.5_f64.to_bits());
    }
}
'''


def apply_candidate(graph_source, coarsen_source):
    if graph_source.count(EDGE_MARKER) != 1:
        raise RuntimeError("Edge implementation marker changed unexpectedly")
    if coarsen_source.count(SERIAL_COMPACT_OLD) != 1:
        raise RuntimeError("serial compact contraction marker changed unexpectedly")
    if coarsen_source.count(PARALLEL_OLD) != 1:
        raise RuntimeError("parallel compact contraction marker changed unexpectedly")
    graph_candidate = graph_source.replace(EDGE_MARKER, EDGE_REPLACEMENT, 1)
    if "mod validated_compact_edge_tests" not in graph_candidate:
        graph_candidate += TEST_MODULE
    coarsen_candidate = coarsen_source.replace(SERIAL_COMPACT_OLD, SERIAL_COMPACT_NEW, 1)
    coarsen_candidate = coarsen_candidate.replace(PARALLEL_OLD, PARALLEL_NEW, 1)
    return graph_candidate, coarsen_candidate


def update_documents(result):
    accepted = result.get("accepted", False)
    decision = "retained" if accepted else "not retained"
    contraction_ratio = result.get("contraction_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    rss_ratio = result.get("worst_peak_rss_ratio", 1.0)
    checkpoint = f'''### Trusted compact contraction checkpoint — 2026-08-23

- Redundant checked conversions for private compact aggregation labels were **{decision}**.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric production-contraction / hierarchy-build ratios: `{contraction_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{rss_ratio:.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/trusted-compact-contraction-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Trusted compact contraction checkpoint — 2026-08-23\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Trusted compact contraction gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Production-contraction / hierarchy-build ratios: `{contraction_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{rss_ratio:.3f}x`.
- Evidence: `.ci/performance/trusted-compact-contraction-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Trusted compact contraction gate\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + block + status[end:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")


baseline_graph = GRAPH.read_text()
baseline_coarsen = COARSEN.read_text()
result = {
    "schema_version": 1,
    "experiment": "trusted-private-compact-contraction",
    "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "accepted": False,
    "validation": "not_run",
    "contraction_cases": {},
    "hierarchy_cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-trusted-edge-baseline"))
    graph_candidate, coarsen_candidate = apply_candidate(baseline_graph, baseline_coarsen)
    GRAPH.write_text(graph_candidate)
    COARSEN.write_text(coarsen_candidate)

    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all", "--", "--check"])
    run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
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

    candidate = build(Path("/tmp/cmg-trusted-edge-candidate"))
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
        case["candidate_over_baseline_time"] for case in result["contraction_cases"].values()
    ]
    hierarchy_ratios = [
        case["candidate_over_baseline_time"] for case in result["hierarchy_cases"].values()
    ]
    rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (result["contraction_cases"], result["hierarchy_cases"])
        for case in collection.values()
    ]
    result["contraction_geometric_time_ratio"] = math.exp(
        sum(math.log(value) for value in contraction_ratios) / len(contraction_ratios)
    )
    result["hierarchy_geometric_time_ratio"] = math.exp(
        sum(math.log(value) for value in hierarchy_ratios) / len(hierarchy_ratios)
    )
    result["worst_contraction_time_ratio"] = max(contraction_ratios)
    result["worst_hierarchy_time_ratio"] = max(hierarchy_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["improved_contraction_case_count"] = sum(value < 1.0 for value in contraction_ratios)
    result["acceptance_limits"] = {
        "contraction_geometric_time_ratio_max": 0.985,
        "hierarchy_geometric_time_ratio_max": 0.997,
        "worst_contraction_time_ratio_max": 1.04,
        "worst_hierarchy_time_ratio_max": 1.03,
        "worst_peak_rss_ratio_max": 1.02,
        "improved_contraction_case_count_min": 4,
    }
    result["accepted"] = (
        result["contraction_geometric_time_ratio"] <= 0.985
        and result["hierarchy_geometric_time_ratio"] <= 0.997
        and result["worst_contraction_time_ratio"] <= 1.04
        and result["worst_hierarchy_time_ratio"] <= 1.03
        and result["worst_peak_rss_ratio"] <= 1.02
        and result["improved_contraction_case_count"] >= 4
    )
    result["decision_reason"] = (
        "full qualification passed; private compact-label mapping removed redundant conversion checks with stable setup gains"
        if result["accepted"]
        else "qualification passed, but contraction or hierarchy timing did not improve consistently enough"
    )
except Exception as error:
    result["validation"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"experiment failed safely: {error}"
    print(result["decision_reason"], flush=True)

if not result.get("accepted", False):
    GRAPH.write_text(baseline_graph)
    COARSEN.write_text(baseline_coarsen)
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
    "perf: retain trusted compact contraction mapping"
    if result.get("accepted", False)
    else "perf: record trusted compact contraction experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push trusted compact contraction decision")
