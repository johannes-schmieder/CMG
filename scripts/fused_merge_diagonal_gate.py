import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/graph.rs")
WORKFLOW = Path(".github/workflows/fused-merge-diagonal.yml")
SCRIPT = Path("scripts/fused_merge_diagonal_gate.py")
RECORD = Path(".ci/performance/fused-merge-diagonal-latest.json")
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
            "graph-build",
            "--bin",
            "hierarchy-build",
        ],
        env=env,
    )
    release = target / "release"
    return {
        "graph": release / "graph-build",
        "hierarchy": release / "hierarchy-build",
    }


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-fused-diagonal-{tag}.time")
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
    )
    for index, (label, binary) in enumerate(schedule):
        observation = sample(binary, arguments, f"{kind}-{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(observation)

    stable = ("case", "scale", "vertices", "edges", "repetitions")
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: unstable {kind} metadata for {key}")

    baseline_ns = statistics.median(item["median_ns"] for item in baseline_samples)
    candidate_ns = statistics.median(item["median_ns"] for item in candidate_samples)
    baseline_rss = max(item["peak_rss_kib"] for item in baseline_samples)
    candidate_rss = max(item["peak_rss_kib"] for item in candidate_samples)
    return {
        "arguments": arguments,
        "baseline_median_ns": baseline_ns,
        "candidate_median_ns": candidate_ns,
        "candidate_over_baseline_time": candidate_ns / baseline_ns,
        "baseline_peak_rss_kib": baseline_rss,
        "candidate_peak_rss_kib": candidate_rss,
        "candidate_over_baseline_peak_rss": candidate_rss / baseline_rss,
        "metadata": {key: reference[key] for key in stable},
    }


OLD_PREFIX = '''    fn from_sorted_raw_edges(vertex_count: usize, mut raw: Vec<Edge>) -> Result<Self, CmgError> {
        // Equal endpoint pairs are contiguous after sorting. Merge them into
        // the front of the compact input buffer so graph construction does not
        // allocate a separate full-capacity canonical vector.
        let mut read_index = 0;
        let mut write_index = 0;
'''
NEW_PREFIX = '''    fn from_sorted_raw_edges(vertex_count: usize, mut raw: Vec<Edge>) -> Result<Self, CmgError> {
        // Equal endpoint pairs are contiguous after sorting. Merge them into
        // the front of the compact input buffer so graph construction does not
        // allocate a separate full-capacity canonical vector. Accumulate the
        // diagonal in the same canonical edge order while each merged edge is
        // already hot, avoiding a second full edge pass.
        let mut diagonal = vec![0.0; vertex_count];
        let mut read_index = 0;
        let mut write_index = 0;
'''
OLD_WRITE = '''            raw[write_index] = Edge { u, v, weight };
            write_index += 1;
'''
NEW_WRITE = '''            raw[write_index] = Edge { u, v, weight };
            diagonal[u as usize] += weight;
            diagonal[v as usize] += weight;
            write_index += 1;
'''
OLD_DIAGONAL = '''
        let mut diagonal = vec![0.0; vertex_count];
        for edge in &raw {
            diagonal[edge.u()] += edge.weight;
            diagonal[edge.v()] += edge.weight;
        }

'''
TEST_MODULE = '''

#[cfg(test)]
mod fused_merge_diagonal_tests {
    use super::Laplacian;

    #[test]
    fn fused_diagonal_matches_canonical_edge_scan_bitwise() {
        let graph = Laplacian::from_edges(
            6,
            [
                (4, 1, 0.25),
                (0, 3, 7.0),
                (1, 4, 1.5),
                (2, 5, 3.0),
                (3, 0, 0.125),
                (4, 1, 2.25),
                (5, 2, 0.75),
            ],
        )
        .unwrap();
        let mut scanned = vec![0.0_f64; graph.vertex_count()];
        for edge in graph.edges() {
            scanned[edge.u()] += edge.weight();
            scanned[edge.v()] += edge.weight();
        }
        assert_eq!(
            graph.diagonal().iter().map(|value| value.to_bits()).collect::<Vec<_>>(),
            scanned.iter().map(|value| value.to_bits()).collect::<Vec<_>>()
        );
    }
}
'''


def apply_candidate(source):
    for marker, expected in ((OLD_PREFIX, 1), (OLD_WRITE, 1), (OLD_DIAGONAL, 1)):
        if source.count(marker) != expected:
            raise RuntimeError("fused diagonal source marker changed unexpectedly")
    candidate = source.replace(OLD_PREFIX, NEW_PREFIX, 1)
    candidate = candidate.replace(OLD_WRITE, NEW_WRITE, 1)
    candidate = candidate.replace(OLD_DIAGONAL, "\n", 1)
    if "mod fused_merge_diagonal_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def update_documents(result):
    accepted = result.get("accepted", False)
    decision = "retained" if accepted else "not retained"
    graph_ratio = result.get("graph_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    rss_ratio = result.get("worst_peak_rss_ratio", 1.0)
    checkpoint = f'''### Fused merge-diagonal checkpoint — 2026-08-23

- Diagonal accumulation during canonical duplicate merging was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric graph-build / hierarchy-build ratios: `{graph_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{rss_ratio:.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/fused-merge-diagonal-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Fused merge-diagonal checkpoint — 2026-08-23\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Fused merge-diagonal gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Graph-build / hierarchy-build ratios: `{graph_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{rss_ratio:.3f}x`.
- Evidence: `.ci/performance/fused-merge-diagonal-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Fused merge-diagonal gate\n"
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
    "experiment": "fused-canonical-merge-diagonal",
    "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "accepted": False,
    "validation": "not_run",
    "graph_cases": {},
    "hierarchy_cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-fused-diagonal-baseline"))
    SOURCE.write_text(apply_candidate(baseline_source))

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

    candidate = build(Path("/tmp/cmg-fused-diagonal-candidate"))
    specs = (
        ("path-1m", ["path", "1000000", "3"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
    )
    for name, arguments in specs:
        result["graph_cases"][name] = compare("graph", baseline, candidate, arguments, name)
        result["hierarchy_cases"][name] = compare(
            "hierarchy", baseline, candidate, arguments, name
        )

    graph_ratios = [case["candidate_over_baseline_time"] for case in result["graph_cases"].values()]
    hierarchy_ratios = [
        case["candidate_over_baseline_time"] for case in result["hierarchy_cases"].values()
    ]
    rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (result["graph_cases"], result["hierarchy_cases"])
        for case in collection.values()
    ]
    result["graph_geometric_time_ratio"] = math.exp(
        sum(math.log(value) for value in graph_ratios) / len(graph_ratios)
    )
    result["hierarchy_geometric_time_ratio"] = math.exp(
        sum(math.log(value) for value in hierarchy_ratios) / len(hierarchy_ratios)
    )
    result["combined_geometric_time_ratio"] = math.sqrt(
        result["graph_geometric_time_ratio"] * result["hierarchy_geometric_time_ratio"]
    )
    result["worst_time_ratio"] = max(graph_ratios + hierarchy_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["improved_metric_count"] = sum(
        value < 1.0 for value in graph_ratios + hierarchy_ratios
    )
    result["acceptance_limits"] = {
        "combined_geometric_time_ratio_max": 0.995,
        "graph_geometric_time_ratio_max": 1.005,
        "hierarchy_geometric_time_ratio_max": 1.005,
        "worst_time_ratio_max": 1.04,
        "worst_peak_rss_ratio_max": 1.02,
        "improved_metric_count_min": 5,
    }
    result["accepted"] = (
        result["combined_geometric_time_ratio"] <= 0.995
        and result["graph_geometric_time_ratio"] <= 1.005
        and result["hierarchy_geometric_time_ratio"] <= 1.005
        and result["worst_time_ratio"] <= 1.04
        and result["worst_peak_rss_ratio"] <= 1.02
        and result["improved_metric_count"] >= 5
    )
    result["decision_reason"] = (
        "full qualification passed; one canonical edge pass was removed with stable end-to-end setup gains"
        if result["accepted"]
        else "qualification passed, but the fused pass did not produce a sufficiently consistent measured gain"
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
    "graph_geometric_time_ratio",
    "hierarchy_geometric_time_ratio",
    "combined_geometric_time_ratio",
    "worst_time_ratio",
    "worst_peak_rss_ratio",
):
    result.setdefault(key, 1.0)
result.setdefault("improved_metric_count", 0)
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
    "perf: retain fused merge-diagonal construction"
    if result.get("accepted", False)
    else "perf: record fused merge-diagonal experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push fused merge-diagonal decision")
