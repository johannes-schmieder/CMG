import json
import math
import os
from pathlib import Path
import statistics
import subprocess

ROOT = Path.cwd()
FOREST = Path("src/forest.rs")
LIB = Path("src/lib.rs")
BENCH = Path("benchmarks/src/bin/forest-phase-profile.rs")
WORKFLOW = Path(".github/workflows/profile-forest-phases.yml")
SCRIPT = Path("scripts/profile_forest_phases.py")
RECORD = Path(".ci/performance/forest-phase-profile-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")

PROFILE_CODE = r'''

#[cfg(feature = "profiling")]
/// Median phase timings for the hierarchy's lean forest-aggregation path.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ForestPhaseProfile {
    vertex_count: usize,
    edge_count: usize,
    repetitions: usize,
    heavy_edge_ns: u128,
    split_ns: u128,
    correction_ns: u128,
    component_labels_ns: u128,
    total_ns: u128,
    aggregate_count: usize,
    label_checksum: u64,
}

#[cfg(feature = "profiling")]
impl ForestPhaseProfile {
    /// Return the number of graph vertices.
    #[must_use]
    pub const fn vertex_count(self) -> usize {
        self.vertex_count
    }

    /// Return the number of canonical undirected graph edges.
    #[must_use]
    pub const fn edge_count(self) -> usize {
        self.edge_count
    }

    /// Return the number of timed repetitions.
    #[must_use]
    pub const fn repetitions(self) -> usize {
        self.repetitions
    }

    /// Return median nanoseconds spent selecting maximum-weight incident edges.
    #[must_use]
    pub const fn heavy_edge_ns(self) -> u128 {
        self.heavy_edge_ns
    }

    /// Return median nanoseconds spent in the upstream forest splitter.
    #[must_use]
    pub const fn split_ns(self) -> u128 {
        self.split_ns
    }

    /// Return median nanoseconds spent in low-effective-degree correction.
    #[must_use]
    pub const fn correction_ns(self) -> u128 {
        self.correction_ns
    }

    /// Return median nanoseconds spent constructing deterministic aggregate labels.
    #[must_use]
    pub const fn component_labels_ns(self) -> u128 {
        self.component_labels_ns
    }

    /// Return median end-to-end nanoseconds for the complete lean grouping path.
    #[must_use]
    pub const fn total_ns(self) -> u128 {
        self.total_ns
    }

    /// Return the deterministic aggregate count from the profiled path.
    #[must_use]
    pub const fn aggregate_count(self) -> usize {
        self.aggregate_count
    }

    /// Return a deterministic checksum of the aggregate-label vector.
    #[must_use]
    pub const fn label_checksum(self) -> u64 {
        self.label_checksum
    }
}

#[cfg(feature = "profiling")]
/// Profile the exact serial lean forest-aggregation path used by hierarchy setup.
pub fn profile_forest_aggregation_labels(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    repetitions: usize,
) -> Result<ForestPhaseProfile, CmgError> {
    profile_forest_aggregation_labels_with_selector(
        graph,
        low_effective_degree_threshold,
        repetitions,
        |current| Ok(maximum_weight_forest(current)),
    )
}

#[cfg(feature = "profiling")]
/// Profile the executor-aware lean forest-aggregation path used by parallel setup.
pub fn profile_forest_aggregation_labels_with_executor(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    repetitions: usize,
    executor: &ParallelExecutor,
) -> Result<ForestPhaseProfile, CmgError> {
    profile_forest_aggregation_labels_with_selector(
        graph,
        low_effective_degree_threshold,
        repetitions,
        |current| maximum_weight_forest_with_executor(current, executor),
    )
}

#[cfg(feature = "profiling")]
fn profile_forest_aggregation_labels_with_selector<Select>(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    repetitions: usize,
    mut select: Select,
) -> Result<ForestPhaseProfile, CmgError>
where
    Select: FnMut(&Laplacian) -> Result<(Vec<usize>, Vec<f64>), CmgError>,
{
    validate_low_effective_degree_threshold(low_effective_degree_threshold)?;
    let repetitions = repetitions.max(1);
    let mut heavy_edge_ns = Vec::with_capacity(repetitions);
    let mut split_ns = Vec::with_capacity(repetitions);
    let mut correction_ns = Vec::with_capacity(repetitions);
    let mut component_labels_ns = Vec::with_capacity(repetitions);
    let mut total_ns = Vec::with_capacity(repetitions);
    let mut reference = None;

    for _ in 0..repetitions {
        let total_start = std::time::Instant::now();

        let phase_start = std::time::Instant::now();
        let (heavy_parent, selected_weight) = select(graph)?;
        heavy_edge_ns.push(phase_start.elapsed().as_nanos());

        let phase_start = std::time::Instant::now();
        let mut final_parent = split_forest_trusted(&heavy_parent)?;
        split_ns.push(phase_start.elapsed().as_nanos());
        drop(heavy_parent);

        let phase_start = std::time::Instant::now();
        apply_low_effective_degree_correction(
            graph,
            low_effective_degree_threshold,
            &selected_weight,
            &mut final_parent,
        );
        correction_ns.push(phase_start.elapsed().as_nanos());
        drop(selected_weight);

        let phase_start = std::time::Instant::now();
        let (labels, aggregate_count) = forest_component_labels_trusted(&final_parent);
        component_labels_ns.push(phase_start.elapsed().as_nanos());
        total_ns.push(total_start.elapsed().as_nanos());

        let label_checksum = labels.iter().enumerate().fold(0_u64, |checksum, (index, label)| {
            checksum
                .wrapping_mul(0x9e37_79b1_85eb_ca87)
                .wrapping_add((index as u64).rotate_left(17))
                .wrapping_add(*label as u64)
        });
        let observation = (aggregate_count, label_checksum);
        if let Some(expected) = reference {
            if observation != expected {
                return Err(CmgError::InvalidHierarchy {
                    context: "forest phase profiling produced nondeterministic labels",
                });
            }
        } else {
            reference = Some(observation);
        }
        std::hint::black_box(labels);
    }

    let (aggregate_count, label_checksum) = reference.expect("positive repetition count");
    Ok(ForestPhaseProfile {
        vertex_count: graph.vertex_count(),
        edge_count: graph.edge_count(),
        repetitions,
        heavy_edge_ns: median_profile_ns(&mut heavy_edge_ns),
        split_ns: median_profile_ns(&mut split_ns),
        correction_ns: median_profile_ns(&mut correction_ns),
        component_labels_ns: median_profile_ns(&mut component_labels_ns),
        total_ns: median_profile_ns(&mut total_ns),
        aggregate_count,
        label_checksum,
    })
}

#[cfg(feature = "profiling")]
fn median_profile_ns(values: &mut [u128]) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}
'''

LIB_EXPORT_MARKER = '''#[cfg(feature = "profiling")]
pub use pcg_profile::{PcgPhaseProfile, PcgPhaseSample, ProfiledPcgResult, profile_pcg_with_plan};
'''
LIB_EXPORT = '''#[cfg(feature = "profiling")]
pub use forest::{
    ForestPhaseProfile, profile_forest_aggregation_labels,
    profile_forest_aggregation_labels_with_executor,
};
'''

BENCH_SOURCE = r'''use cmg::{
    CmgOptions, Laplacian, ParallelExecutor, ParallelOptions,
    profile_forest_aggregation_labels, profile_forest_aggregation_labels_with_executor,
};

struct BenchGraph {
    graph: Laplacian,
    vertices: usize,
    edges: usize,
}

fn path_graph(vertices: usize) -> BenchGraph {
    let edges: Vec<_> = (0..vertices.saturating_sub(1))
        .map(|vertex| (vertex, vertex + 1, 1.0))
        .collect();
    let edge_count = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).expect("valid path graph"),
        vertices,
        edges: edge_count,
    }
}

fn worker_firm_graph(per_side: usize, degree: usize) -> BenchGraph {
    let vertices = 2 * per_side;
    let firm_offset = per_side;
    let mut edges = Vec::with_capacity(degree * per_side);
    for worker in 0..per_side {
        for link in 0..degree {
            let firm = if link == 0 {
                worker
            } else if link == 1 {
                (worker + 1) % per_side
            } else {
                ((2 * link + 1) * worker + 17 * link + 3) % per_side
            };
            let weight = 0.25 + ((worker + 7 * link) % 23) as f64 / 16.0;
            edges.push((worker, firm_offset + firm, weight));
        }
    }
    let edge_count = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).expect("valid worker-firm graph"),
        vertices,
        edges: edge_count,
    }
}

fn build_case(case: &str, scale: usize) -> BenchGraph {
    match case {
        "path" => path_graph(scale),
        "worker-firm" => worker_firm_graph(scale, 3),
        "dense-worker-firm" => worker_firm_graph(scale, 16),
        _ => panic!("unknown case"),
    }
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().expect("case");
    let scale = arguments
        .next()
        .expect("scale")
        .parse::<usize>()
        .expect("integer scale");
    let repetitions = arguments
        .next()
        .expect("repetitions")
        .parse::<usize>()
        .expect("integer repetitions")
        .max(1);
    let mode = arguments.next().expect("serial or parallel mode");
    let threads = arguments
        .next()
        .expect("threads")
        .parse::<usize>()
        .expect("integer threads")
        .max(1);
    let bench_graph = build_case(&case, scale);
    let threshold = CmgOptions::default().low_effective_degree_threshold;
    let profile = match mode.as_str() {
        "serial" => profile_forest_aggregation_labels(
            &bench_graph.graph,
            threshold,
            repetitions,
        )
        .expect("serial forest profile"),
        "parallel" => {
            let executor = ParallelExecutor::new(ParallelOptions {
                threads,
                min_parallel_len: 1,
                ..ParallelOptions::default()
            })
            .expect("parallel executor");
            profile_forest_aggregation_labels_with_executor(
                &bench_graph.graph,
                threshold,
                repetitions,
                &executor,
            )
            .expect("parallel forest profile")
        }
        _ => panic!("unknown mode"),
    };
    assert_eq!(profile.vertex_count(), bench_graph.vertices);
    assert_eq!(profile.edge_count(), bench_graph.edges);
    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"mode\":\"{mode}\",\"threads\":{threads},\"vertices\":{},\"edges\":{},\"repetitions\":{},\"heavy_edge_ns\":{},\"split_ns\":{},\"correction_ns\":{},\"component_labels_ns\":{},\"total_ns\":{},\"aggregate_count\":{},\"label_checksum\":{}}}",
        profile.vertex_count(),
        profile.edge_count(),
        profile.repetitions(),
        profile.heavy_edge_ns(),
        profile.split_ns(),
        profile.correction_ns(),
        profile.component_labels_ns(),
        profile.total_ns(),
        profile.aggregate_count(),
        profile.label_checksum(),
    );
}
'''


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


def sample(binary, arguments, tag):
    completed = run([binary, *arguments], timeout=7200)
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected profiler output for {tag}: {payloads}")
    return payloads[0]


def median_observations(observations):
    stable = (
        "case",
        "scale",
        "mode",
        "threads",
        "vertices",
        "edges",
        "repetitions",
        "aggregate_count",
        "label_checksum",
    )
    reference = observations[0]
    for observation in observations[1:]:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"unstable forest profile metadata: {key}")
    timing = (
        "heavy_edge_ns",
        "split_ns",
        "correction_ns",
        "component_labels_ns",
        "total_ns",
    )
    result = {key: reference[key] for key in stable}
    for key in timing:
        result[key] = statistics.median(item[key] for item in observations)
    phase_sum = sum(result[key] for key in timing[:-1])
    result["phase_sum_ns"] = phase_sum
    result["unattributed_ns"] = max(0, result["total_ns"] - phase_sum)
    for key in timing[:-1]:
        result[f"{key.removesuffix('_ns')}_share"] = (
            result[key] / result["total_ns"] if result["total_ns"] else 0.0
        )
    return result


def update_documents(result):
    serial = result["mode_summary"]["serial"]
    parallel = result["mode_summary"]["parallel"]
    dominant = result["dominant_phase"]
    checkpoint = f'''### Forest phase profile — 2026-08-24

- Validation: `{result["status"]}`; exact aggregate labels were stable across all repetitions.
- Serial aggregate shares: heavy `{serial["heavy_edge_share"]:.1%}`, split `{serial["split_share"]:.1%}`, correction `{serial["correction_share"]:.1%}`, labels `{serial["component_labels_share"]:.1%}`.
- Executor-aware aggregate shares: heavy `{parallel["heavy_edge_share"]:.1%}`, split `{parallel["split_share"]:.1%}`, correction `{parallel["correction_share"]:.1%}`, labels `{parallel["component_labels_share"]:.1%}`.
- Dominant profiled forest phase: **{dominant}**.
- Evidence: `.ci/performance/forest-phase-profile-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Forest phase profile — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    next_action = {
        "heavy_edge": "Profile heavy-edge selection and its serial/CSR router at large sparse and dense levels.",
        "split": "Profile forest splitting subphases and temporary arrays on path-like and worker-firm forests.",
        "correction": "Profile low-effective-degree detection, selected-weight accumulation, and correction writes.",
        "component_labels": "Profile disjoint-set construction, path compression, and label assignment separately.",
    }[dominant]
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        f"1. {next_action}\n"
        "2. Refresh cumulative retained optimization and memory guidance.\n"
        "3. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
        "4. Retain only end-to-end wins with unchanged hierarchy and residual certificates.\n"
    )
    PLAN.write_text(plan)

    status_block = f'''## Forest phase profile after ownership and compaction work

- Status: `{result["status"]}`.
- Serial dominant phase: `{serial["dominant_phase"]}` ({serial["dominant_share"]:.1%}).
- Executor-aware dominant phase: `{parallel["dominant_phase"]}` ({parallel["dominant_share"]:.1%}).
- Cross-mode target selected for the next gate: `{dominant}`.
- Evidence: `.ci/performance/forest-phase-profile-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Forest phase profile after ownership and compaction work\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + status_block + status[end:]
    else:
        status += "\n\n" + status_block
    STATUS.write_text(status.rstrip() + "\n")


original_forest = FOREST.read_text()
original_lib = LIB.read_text()
result = {
    "schema_version": 1,
    "profile": "forest-aggregation-phases-after-ownership",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "status": "not_run",
    "cases": {},
}

try:
    if "pub struct ForestPhaseProfile" in original_forest:
        raise RuntimeError("forest phase profiler already exists")
    FOREST.write_text(original_forest.rstrip() + PROFILE_CODE + "\n")
    if LIB_EXPORT_MARKER not in original_lib:
        raise RuntimeError("profiling export marker changed unexpectedly")
    LIB.write_text(
        original_lib.replace(
            LIB_EXPORT_MARKER,
            LIB_EXPORT_MARKER + LIB_EXPORT,
            1,
        )
    )
    BENCH.parent.mkdir(parents=True, exist_ok=True)
    BENCH.write_text(BENCH_SOURCE)

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
    run(
        [
            "cargo",
            "build",
            "--release",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--bin",
            "forest-phase-profile",
        ]
    )

    binary = Path("benchmarks/target/release/forest-phase-profile")
    specs = (
        ("path-1m", "path", "1000000"),
        ("worker-firm-1.5m", "worker-firm", "500000"),
        ("worker-firm-3m", "worker-firm", "1000000"),
        ("dense-worker-firm-1.6m", "dense-worker-firm", "100000"),
    )
    for name, case, scale in specs:
        for mode in ("serial", "parallel"):
            observations = [
                sample(
                    binary,
                    [case, scale, "3", mode, "4"],
                    f"{name}-{mode}-{index}",
                )
                for index in range(2)
            ]
            result["cases"][f"{name}-{mode}"] = median_observations(observations)

    phase_names = (
        "heavy_edge",
        "split",
        "correction",
        "component_labels",
    )
    result["mode_summary"] = {}
    for mode in ("serial", "parallel"):
        selected = [
            case
            for name, case in result["cases"].items()
            if name.endswith(f"-{mode}")
        ]
        total = sum(case["total_ns"] for case in selected)
        summary = {}
        for phase in phase_names:
            phase_time = sum(case[f"{phase}_ns"] for case in selected)
            summary[f"{phase}_ns"] = phase_time
            summary[f"{phase}_share"] = phase_time / total if total else 0.0
        dominant_phase = max(
            phase_names,
            key=lambda phase: summary[f"{phase}_share"],
        )
        summary["total_ns"] = total
        summary["dominant_phase"] = dominant_phase
        summary["dominant_share"] = summary[f"{dominant_phase}_share"]
        result["mode_summary"][mode] = summary

    combined_shares = {
        phase: sum(
            result["mode_summary"][mode][f"{phase}_ns"]
            for mode in ("serial", "parallel")
        )
        for phase in phase_names
    }
    result["dominant_phase"] = max(combined_shares, key=combined_shares.get)
    result["maximum_relative_unattributed"] = max(
        case["unattributed_ns"] / case["total_ns"]
        if case["total_ns"]
        else 0.0
        for case in result["cases"].values()
    )
    result["status"] = "success"
except Exception as error:
    result["status"] = "failure"
    result["error"] = repr(error)
    print(f"forest phase profiling failed: {error}", flush=True)

subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=ROOT, check=False)
subprocess.run(["git", "clean", "-fd"], cwd=ROOT, check=False)
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
if result.get("status") == "success":
    update_documents(result)
else:
    status = STATUS.read_text().rstrip()
    status += (
        "\n\n## Forest phase profile after ownership and compaction work\n\n"
        f"- Status: `failure`.\n- Error: `{result.get('error', 'unknown')}`.\n"
        "- Production source was restored unchanged.\n"
        "- Evidence: `.ci/performance/forest-phase-profile-latest.json`.\n"
    )
    STATUS.write_text(status + "\n")

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
run(["git", "commit", "-m", "perf: record post-ownership forest phase profile"])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push forest phase profile")

if result.get("status") != "success":
    raise SystemExit(1)
