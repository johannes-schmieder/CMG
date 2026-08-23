import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/graph.rs")
WORKFLOW = Path(".github/workflows/incremental-diagonal-metadata.yml")
SCRIPT = Path("scripts/incremental_diagonal_metadata_gate.py")
RECORD = Path(".ci/performance/incremental-diagonal-metadata-latest.json")
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
    time_path = Path(f"/tmp/cmg-incremental-metadata-{tag}.time")
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
        observation = sample(
            binary,
            arguments,
            f"{kind}-{name}-{label}-{index}",
        )
        (baseline_samples if label == "baseline" else candidate_samples).append(
            observation
        )

    stable = ("case", "scale", "vertices", "repetitions")
    stable += (
        ("raw_edges", "retained_edges")
        if kind == "graph"
        else ("edges",)
    )
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


OLD_INIT = '''        let mut diagonal = vec![0.0; vertex_count];
        let mut read_index = 0;
        let mut write_index = 0;
'''
NEW_INIT = '''        let mut diagonal = vec![0.0; vertex_count];
        let mut diagonal_nnz = 0_usize;
        let mut maximum_degree = 0.0_f64;
        let mut read_index = 0;
        let mut write_index = 0;
'''
OLD_ADD = '''            raw[write_index] = Edge { u, v, weight };
            diagonal[u as usize] += weight;
            diagonal[v as usize] += weight;
            write_index += 1;
'''
NEW_ADD = '''            raw[write_index] = Edge { u, v, weight };

            let u_degree = &mut diagonal[u as usize];
            if *u_degree == 0.0 {
                diagonal_nnz += 1;
            }
            *u_degree += weight;
            maximum_degree = maximum_degree.max(*u_degree);

            let v_degree = &mut diagonal[v as usize];
            if *v_degree == 0.0 {
                diagonal_nnz += 1;
            }
            *v_degree += weight;
            maximum_degree = maximum_degree.max(*v_degree);

            write_index += 1;
'''
OLD_SCAN = '''        let mut diagonal_nnz = 0_usize;
        let mut maximum_degree = 0.0_f64;
        for &degree in &diagonal {
            diagonal_nnz += usize::from(degree != 0.0);
            maximum_degree = maximum_degree.max(degree);
        }
        let matrix_nnz = diagonal_nnz + 2 * raw.len();
'''
NEW_SCAN = '''        let matrix_nnz = diagonal_nnz + 2 * raw.len();
'''
TEST_MODULE = '''

#[cfg(test)]
mod incremental_diagonal_metadata_tests {
    use super::Laplacian;

    #[test]
    fn incremental_metadata_matches_final_diagonal_scan() {
        let graph = Laplacian::from_edges(
            8,
            [
                (0, 1, 1.0),
                (1, 2, 2.0),
                (2, 0, 4.0),
                (5, 6, 8.0),
                (6, 7, 16.0),
                (5, 7, 32.0),
                (0, 1, 0.5),
            ],
        )
        .unwrap();
        let diagonal_nonzeros = graph
            .diagonal()
            .iter()
            .filter(|degree| **degree != 0.0)
            .count();
        let maximum_degree = graph
            .diagonal()
            .iter()
            .copied()
            .fold(0.0_f64, f64::max);
        assert_eq!(graph.matrix_nnz(), diagonal_nonzeros + 2 * graph.edge_count());
        assert_eq!(
            graph.operator_norm_bound().to_bits(),
            (2.0 * maximum_degree).to_bits()
        );
    }
}
'''


def apply_candidate(source):
    markers = (
        (OLD_INIT, "merge-loop initialization"),
        (OLD_ADD, "diagonal accumulation"),
        (OLD_SCAN, "diagonal metadata scan"),
    )
    for marker, name in markers:
        if source.count(marker) != 1:
            raise RuntimeError(f"{name} source marker changed unexpectedly")
    candidate = source.replace(OLD_INIT, NEW_INIT, 1)
    candidate = candidate.replace(OLD_ADD, NEW_ADD, 1)
    candidate = candidate.replace(OLD_SCAN, NEW_SCAN, 1)
    if "mod incremental_diagonal_metadata_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def update_documents(result):
    accepted = result.get("accepted", False)
    decision = "retained" if accepted else "not retained"
    graph_ratio = result.get("graph_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    rss_ratio = result.get("worst_peak_rss_ratio", 1.0)
    checkpoint = f'''### Incremental diagonal-metadata checkpoint — 2026-08-23

- Updating diagonal nonzero count and maximum degree during edge application was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric graph-build / hierarchy-build ratios: `{graph_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{rss_ratio:.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/incremental-diagonal-metadata-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Incremental diagonal-metadata checkpoint — 2026-08-23\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Incremental diagonal-metadata gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Graph-build / hierarchy-build ratios: `{graph_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{rss_ratio:.3f}x`.
- Evidence: `.ci/performance/incremental-diagonal-metadata-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Incremental diagonal-metadata gate\n"
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
    "experiment": "incremental-diagonal-metadata",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "graph_cases": {},
    "hierarchy_cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-incremental-metadata-baseline"))
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

    candidate = build(Path("/tmp/cmg-incremental-metadata-candidate"))
    graph_specs = (
        ("unique-1m", ["unique", "1000000", "3"]),
        ("duplicates-4-1m", ["duplicates-4", "250000", "3"]),
        ("duplicates-16-1.6m", ["duplicates-16", "100000", "3"]),
        ("coarse-collisions-1.6m", ["coarse-collisions", "100000", "3"]),
    )
    hierarchy_specs = (
        ("path-1m", ["path", "1000000", "3"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
    )
    for name, arguments in graph_specs:
        result["graph_cases"][name] = compare(
            "graph", baseline, candidate, arguments, name
        )
    for name, arguments in hierarchy_specs:
        result["hierarchy_cases"][name] = compare(
            "hierarchy", baseline, candidate, arguments, name
        )

    graph_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["graph_cases"].values()
    ]
    hierarchy_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["hierarchy_cases"].values()
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
        sum(math.log(value) for value in hierarchy_ratios)
        / len(hierarchy_ratios)
    )
    result["combined_geometric_time_ratio"] = math.sqrt(
        result["graph_geometric_time_ratio"]
        * result["hierarchy_geometric_time_ratio"]
    )
    result["worst_time_ratio"] = max(graph_ratios + hierarchy_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["improved_metric_count"] = sum(
        value < 1.0 for value in graph_ratios + hierarchy_ratios
    )
    result["acceptance_limits"] = {
        "combined_geometric_time_ratio_max": 0.995,
        "graph_geometric_time_ratio_max": 0.995,
        "hierarchy_geometric_time_ratio_max": 1.005,
        "worst_time_ratio_max": 1.04,
        "worst_peak_rss_ratio_max": 1.02,
        "improved_metric_count_min": 5,
    }
    result["accepted"] = (
        result["combined_geometric_time_ratio"] <= 0.995
        and result["graph_geometric_time_ratio"] <= 0.995
        and result["hierarchy_geometric_time_ratio"] <= 1.005
        and result["worst_time_ratio"] <= 1.04
        and result["worst_peak_rss_ratio"] <= 1.02
        and result["improved_metric_count"] >= 5
    )
    result["decision_reason"] = (
        "full qualification passed; the final diagonal metadata pass was removed with stable setup gains"
        if result["accepted"]
        else "qualification passed, but eliminating the final diagonal pass did not improve setup consistently enough"
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
    "perf: retain incremental diagonal metadata"
    if result.get("accepted", False)
    else "perf: record incremental diagonal-metadata experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push incremental diagonal-metadata decision")
