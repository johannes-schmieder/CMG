import json
import os
from pathlib import Path
import statistics
import subprocess

ROOT = Path.cwd()
FOREST = Path("src/forest.rs")
LIB = Path("src/lib.rs")
BENCH = Path("benchmarks/src/bin/split-forest-subphase-profile.rs")
WORKFLOW = Path(".github/workflows/profile-split-forest-subphases.yml")
SCRIPT = Path("scripts/profile_split_forest_subphases.py")
RECORD = Path(".ci/performance/split-forest-subphase-profile-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")

PROFILE_CODE = r'''

#[cfg(feature = "profiling")]
/// Median timing and structural diagnostics for the trusted forest splitter.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SplitForestPhaseProfile {
    vertex_count: usize,
    repetitions: usize,
    allocation_ns: u128,
    indegree_ns: u128,
    diameter_ns: u128,
    conductance_ns: u128,
    total_ns: u128,
    max_walk_len: usize,
    diameter_cuts: usize,
    conductance_cuts: usize,
    scratch_bytes: usize,
    result_checksum: u64,
}

#[cfg(feature = "profiling")]
impl SplitForestPhaseProfile {
    /// Return the parent-vector length.
    #[must_use]
    pub const fn vertex_count(self) -> usize {
        self.vertex_count
    }

    /// Return the number of timed repetitions.
    #[must_use]
    pub const fn repetitions(self) -> usize {
        self.repetitions
    }

    /// Return median nanoseconds spent allocating and initializing splitter arrays.
    #[must_use]
    pub const fn allocation_ns(self) -> u128 {
        self.allocation_ns
    }

    /// Return median nanoseconds spent constructing indegrees.
    #[must_use]
    pub const fn indegree_ns(self) -> u128 {
        self.indegree_ns
    }

    /// Return median nanoseconds spent in diameter cutting and ancestor propagation.
    #[must_use]
    pub const fn diameter_ns(self) -> u128 {
        self.diameter_ns
    }

    /// Return median nanoseconds spent in the conductance-cut pass.
    #[must_use]
    pub const fn conductance_ns(self) -> u128 {
        self.conductance_ns
    }

    /// Return median end-to-end nanoseconds for the trusted split.
    #[must_use]
    pub const fn total_ns(self) -> u128 {
        self.total_ns
    }

    /// Return the maximum temporary walk length observed.
    #[must_use]
    pub const fn max_walk_len(self) -> usize {
        self.max_walk_len
    }

    /// Return the deterministic number of diameter cuts.
    #[must_use]
    pub const fn diameter_cuts(self) -> usize {
        self.diameter_cuts
    }

    /// Return the deterministic number of conductance cuts.
    #[must_use]
    pub const fn conductance_cuts(self) -> usize {
        self.conductance_cuts
    }

    /// Return retained bytes in the splitter's principal temporary vectors.
    #[must_use]
    pub const fn scratch_bytes(self) -> usize {
        self.scratch_bytes
    }

    /// Return a deterministic checksum of the split parent vector.
    #[must_use]
    pub const fn result_checksum(self) -> u64 {
        self.result_checksum
    }
}

#[cfg(feature = "profiling")]
/// Profile the exact trusted `u32`-indegree splitter used for realistic graph sizes.
pub fn profile_split_forest_trusted(
    parent: &[usize],
    repetitions: usize,
) -> Result<SplitForestPhaseProfile, CmgError> {
    validate_parent(parent)?;
    if parent.len() > u32::MAX as usize {
        return Err(CmgError::InvalidHierarchy {
            context: "split profiling currently requires at most u32::MAX vertices",
        });
    }
    let reference = split_forest_trusted(parent)?;
    let result_checksum = reference.iter().enumerate().fold(0_u64, |checksum, (index, value)| {
        checksum
            .wrapping_mul(0x9e37_79b1_85eb_ca87)
            .wrapping_add((index as u64).rotate_left(19))
            .wrapping_add(*value as u64)
    });
    let repetitions = repetitions.max(1);
    let mut allocation_ns = Vec::with_capacity(repetitions);
    let mut indegree_ns = Vec::with_capacity(repetitions);
    let mut diameter_ns = Vec::with_capacity(repetitions);
    let mut conductance_ns = Vec::with_capacity(repetitions);
    let mut total_ns = Vec::with_capacity(repetitions);
    let mut reference_structure = None;

    for _ in 0..repetitions {
        let total_start = std::time::Instant::now();
        let phase_start = std::time::Instant::now();
        let n = parent.len();
        let mut forest = parent.to_vec();
        let mut ancestors = vec![0_i64; n];
        let mut indegree = vec![0_u32; n];
        let mut visited = vec![false; n];
        let mut walk = Vec::new();
        let mut new_ancestors = Vec::new();
        allocation_ns.push(phase_start.elapsed().as_nanos());

        let phase_start = std::time::Instant::now();
        for &target in &forest {
            indegree[target] = indegree[target]
                .checked_add(1)
                .expect("forest indegree overflow");
        }
        indegree_ns.push(phase_start.elapsed().as_nanos());

        let phase_start = std::time::Instant::now();
        let mut max_walk_len = 0_usize;
        let mut diameter_cuts = 0_usize;
        for start in 0..n {
            let mut current = start;
            let mut continue_walk = true;
            while continue_walk && indegree[current] == 0 && !visited[current] {
                continue_walk = false;
                let mut ancestors_in_path = 0_i64;
                walk.clear();
                walk.push(current);
                new_ancestors.clear();
                new_ancestors.push(0_i64);
                let mut k = 0_usize;

                while k <= 5 || visited[current] {
                    current = forest[current];
                    let terminated = current == walk[k] || (k > 0 && current == walk[k - 1]);
                    if terminated {
                        break;
                    }
                    k += 1;
                    walk.push(current);
                    if visited[current] {
                        new_ancestors.push(ancestors_in_path);
                    } else {
                        ancestors_in_path += 1;
                        new_ancestors.push(ancestors_in_path);
                    }
                }
                max_walk_len = max_walk_len.max(walk.len());

                if k > 5 {
                    diameter_cuts += 1;
                    let middle = k / 2;
                    forest[walk[middle]] = walk[middle];
                    let next = walk[middle + 1];
                    indegree[next] = indegree[next]
                        .checked_sub(1)
                        .expect("forest indegree invariant");
                    let removed = ancestors[walk[middle]];
                    for &vertex in &walk[(middle + 1)..=k] {
                        ancestors[vertex] -= removed;
                    }
                    for index in 0..=middle {
                        let vertex = walk[index];
                        visited.set(index, visited[index]);
                        visited[vertex] = true;
                        ancestors[vertex] += new_ancestors[index];
                    }
                    current = next;
                    continue_walk = true;
                }

                if !continue_walk {
                    for index in 0..=k {
                        let vertex = walk[index];
                        ancestors[vertex] += new_ancestors[index];
                        visited[vertex] = true;
                    }
                }
            }
        }
        diameter_ns.push(phase_start.elapsed().as_nanos());

        let phase_start = std::time::Instant::now();
        let mut conductance_cuts = 0_usize;
        for start in 0..n {
            let mut current = start;
            let mut continue_walk = true;
            while continue_walk && indegree[current] == 0 {
                continue_walk = false;
                let mut previous = current;
                let mut cut_mode = false;
                let mut removed_ancestors = 0_i64;
                let mut new_front = current;

                loop {
                    let next = forest[current];
                    if next == current || next == previous {
                        break;
                    }
                    if !cut_mode
                        && ancestors[current] > 2
                        && ancestors[next] - ancestors[current] > 2
                    {
                        conductance_cuts += 1;
                        forest[current] = current;
                        indegree[next] = indegree[next]
                            .checked_sub(1)
                            .expect("forest indegree invariant");
                        removed_ancestors = ancestors[current];
                        new_front = next;
                        cut_mode = true;
                    }
                    previous = current;
                    current = next;
                    if cut_mode {
                        ancestors[current] -= removed_ancestors;
                    }
                }
                if cut_mode {
                    continue_walk = true;
                    current = new_front;
                }
            }
        }
        conductance_ns.push(phase_start.elapsed().as_nanos());
        total_ns.push(total_start.elapsed().as_nanos());

        if forest != reference {
            return Err(CmgError::InvalidHierarchy {
                context: "split subphase profiler diverged from production output",
            });
        }
        let visited_bytes = visited.capacity().div_ceil(8);
        let scratch_bytes = forest.capacity() * std::mem::size_of::<usize>()
            + ancestors.capacity() * std::mem::size_of::<i64>()
            + indegree.capacity() * std::mem::size_of::<u32>()
            + visited_bytes
            + walk.capacity() * std::mem::size_of::<usize>()
            + new_ancestors.capacity() * std::mem::size_of::<i64>();
        let structure = (max_walk_len, diameter_cuts, conductance_cuts, scratch_bytes);
        if let Some(expected) = reference_structure {
            if structure != expected {
                return Err(CmgError::InvalidHierarchy {
                    context: "split subphase profiling produced unstable structure",
                });
            }
        } else {
            reference_structure = Some(structure);
        }
        std::hint::black_box(forest);
    }

    let (max_walk_len, diameter_cuts, conductance_cuts, scratch_bytes) =
        reference_structure.expect("positive repetition count");
    Ok(SplitForestPhaseProfile {
        vertex_count: parent.len(),
        repetitions,
        allocation_ns: median_split_profile_ns(&mut allocation_ns),
        indegree_ns: median_split_profile_ns(&mut indegree_ns),
        diameter_ns: median_split_profile_ns(&mut diameter_ns),
        conductance_ns: median_split_profile_ns(&mut conductance_ns),
        total_ns: median_split_profile_ns(&mut total_ns),
        max_walk_len,
        diameter_cuts,
        conductance_cuts,
        scratch_bytes,
        result_checksum,
    })
}

#[cfg(feature = "profiling")]
fn median_split_profile_ns(values: &mut [u128]) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}
'''

LIB_MARKER = '''#[cfg(feature = "profiling")]
pub use pcg_profile::{PcgPhaseProfile, PcgPhaseSample, ProfiledPcgResult, profile_pcg_with_plan};
'''
LIB_EXPORT = '''#[cfg(feature = "profiling")]
pub use forest::{SplitForestPhaseProfile, profile_split_forest_trusted};
'''

BENCH_SOURCE = r'''use cmg::{
    CmgHierarchy, CmgOptions, Laplacian, maximum_weight_forest,
    profile_split_forest_trusted,
};

struct BenchGraph {
    graph: Laplacian,
    input_edges: usize,
}

fn path_graph(vertices: usize) -> BenchGraph {
    let edges: Vec<_> = (0..vertices.saturating_sub(1))
        .map(|vertex| (vertex, vertex + 1, 1.0))
        .collect();
    let input_edges = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).unwrap(),
        input_edges,
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
    let input_edges = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).unwrap(),
        input_edges,
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
    let case = arguments.next().unwrap();
    let scale = arguments.next().unwrap().parse::<usize>().unwrap();
    let repetitions = arguments.next().unwrap().parse::<usize>().unwrap().max(1);
    let bench_graph = build_case(&case, scale);
    let hierarchy = CmgHierarchy::build(&bench_graph.graph, CmgOptions::default()).unwrap();

    let mut allocation_ns = 0_u128;
    let mut indegree_ns = 0_u128;
    let mut diameter_ns = 0_u128;
    let mut conductance_ns = 0_u128;
    let mut total_ns = 0_u128;
    let mut profiled_levels = 0_usize;
    let mut profiled_vertices = 0_usize;
    let mut profiled_edges = 0_usize;
    let mut max_walk_len = 0_usize;
    let mut diameter_cuts = 0_usize;
    let mut conductance_cuts = 0_usize;
    let mut maximum_scratch_bytes = 0_usize;
    let mut checksum = 0_u64;

    for level in hierarchy.levels() {
        if level.aggregation().is_none() {
            continue;
        }
        let graph = level.graph();
        let (parent, _) = maximum_weight_forest(graph);
        let profile = profile_split_forest_trusted(&parent, repetitions).unwrap();
        allocation_ns += profile.allocation_ns();
        indegree_ns += profile.indegree_ns();
        diameter_ns += profile.diameter_ns();
        conductance_ns += profile.conductance_ns();
        total_ns += profile.total_ns();
        profiled_levels += 1;
        profiled_vertices += graph.vertex_count();
        profiled_edges += graph.edge_count();
        max_walk_len = max_walk_len.max(profile.max_walk_len());
        diameter_cuts += profile.diameter_cuts();
        conductance_cuts += profile.conductance_cuts();
        maximum_scratch_bytes = maximum_scratch_bytes.max(profile.scratch_bytes());
        checksum = checksum
            .wrapping_mul(0x9e37_79b1_85eb_ca87)
            .wrapping_add(profile.result_checksum());
    }

    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"input_edges\":{},\"vertices\":{},\"edges\":{},\"levels\":{},\"profiled_levels\":{profiled_levels},\"profiled_vertices\":{profiled_vertices},\"profiled_edges\":{profiled_edges},\"repetitions\":{repetitions},\"allocation_ns\":{allocation_ns},\"indegree_ns\":{indegree_ns},\"diameter_ns\":{diameter_ns},\"conductance_ns\":{conductance_ns},\"total_ns\":{total_ns},\"max_walk_len\":{max_walk_len},\"diameter_cuts\":{diameter_cuts},\"conductance_cuts\":{conductance_cuts},\"maximum_scratch_bytes\":{maximum_scratch_bytes},\"checksum\":{checksum}}}",
        bench_graph.input_edges,
        bench_graph.graph.vertex_count(),
        bench_graph.graph.edge_count(),
        hierarchy.levels().len(),
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
            f"command failed ({completed.returncode}): {' '.join(str(item) for item in command)}"
        )
    return completed


def sample(binary, arguments, tag):
    completed = run([binary, *arguments])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected split profile output for {tag}: {payloads}")
    return payloads[0]


def median_observations(observations):
    stable = (
        "case",
        "scale",
        "input_edges",
        "vertices",
        "edges",
        "levels",
        "profiled_levels",
        "profiled_vertices",
        "profiled_edges",
        "repetitions",
        "max_walk_len",
        "diameter_cuts",
        "conductance_cuts",
        "maximum_scratch_bytes",
        "checksum",
    )
    reference = observations[0]
    for observation in observations[1:]:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"unstable split profile metadata: {key}")
    result = {key: reference[key] for key in stable}
    phases = ("allocation", "indegree", "diameter", "conductance")
    for phase in phases:
        result[f"{phase}_ns"] = statistics.median(
            item[f"{phase}_ns"] for item in observations
        )
    result["total_ns"] = statistics.median(item["total_ns"] for item in observations)
    phase_sum = sum(result[f"{phase}_ns"] for phase in phases)
    result["phase_sum_ns"] = phase_sum
    for phase in phases:
        result[f"{phase}_share"] = result[f"{phase}_ns"] / phase_sum if phase_sum else 0.0
    return result


def update_documents(result):
    summary = result["summary"]
    dominant = summary["dominant_phase"]
    checkpoint = f'''### Forest-split subphase profile — 2026-08-24

- Validation: `{result["status"]}`; the instrumented splitter matched production exactly for every level and repetition.
- Aggregate shares: allocation `{summary["allocation_share"]:.1%}`, indegree `{summary["indegree_share"]:.1%}`, diameter `{summary["diameter_share"]:.1%}`, conductance `{summary["conductance_share"]:.1%}`.
- Dominant split subphase: **{dominant}** ({summary["dominant_share"]:.1%}).
- Maximum observed walk length: `{summary["max_walk_len"]}`; maximum principal scratch: `{summary["maximum_scratch_bytes"]}` bytes.
- Evidence: `.ci/performance/split-forest-subphase-profile-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Forest-split subphase profile — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    next_action = {
        "allocation": "Benchmark splitter workspace reuse and zero-initialization reduction without increasing retained hierarchy memory.",
        "indegree": "Benchmark fused forest cloning/indegree construction and compact counter traversal.",
        "diameter": "Profile diameter-walk lengths, revisit rates, and ancestor-update traffic before changing the upstream cut logic.",
        "conductance": "Profile conductance-pass leaf walks and repeated ancestor updates before changing the upstream cut logic.",
    }[dominant]
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        f"1. {next_action}\n"
        "2. Refresh cumulative retained optimization and memory guidance.\n"
        "3. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
        "4. Preserve exact split parents and complete hierarchy diagnostics in every gate.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Forest-split subphase profile

- Status: `{result["status"]}`.
- Dominant subphase: `{dominant}` ({summary["dominant_share"]:.1%}).
- Allocation / indegree / diameter / conductance shares: `{summary["allocation_share"]:.1%}` / `{summary["indegree_share"]:.1%}` / `{summary["diameter_share"]:.1%}` / `{summary["conductance_share"]:.1%}`.
- Evidence: `.ci/performance/split-forest-subphase-profile-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Forest-split subphase profile\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + block + status[end:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")


original_forest = FOREST.read_text()
original_lib = LIB.read_text()
result = {
    "schema_version": 1,
    "profile": "split-forest-subphases-after-ownership",
    "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "status": "not_run",
    "cases": {},
}

try:
    if "pub struct SplitForestPhaseProfile" in original_forest:
        raise RuntimeError("split subphase profiler already exists")
    FOREST.write_text(original_forest.rstrip() + PROFILE_CODE + "\n")
    if LIB_MARKER not in original_lib:
        raise RuntimeError("profiling export marker changed unexpectedly")
    LIB.write_text(original_lib.replace(LIB_MARKER, LIB_MARKER + LIB_EXPORT, 1))
    BENCH.parent.mkdir(parents=True, exist_ok=True)
    BENCH.write_text(BENCH_SOURCE)

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
    run([
        "cargo",
        "build",
        "--release",
        "--manifest-path",
        "benchmarks/Cargo.toml",
        "--bin",
        "split-forest-subphase-profile",
    ])

    binary = Path("benchmarks/target/release/split-forest-subphase-profile")
    specs = (
        ("path-1m", "path", "1000000"),
        ("worker-firm-1.5m", "worker-firm", "500000"),
        ("worker-firm-3m", "worker-firm", "1000000"),
        ("dense-worker-firm-1.6m", "dense-worker-firm", "100000"),
    )
    for name, case, scale in specs:
        observations = [
            sample(binary, [case, scale, "3"], f"{name}-{index}")
            for index in range(2)
        ]
        result["cases"][name] = median_observations(observations)

    phases = ("allocation", "indegree", "diameter", "conductance")
    phase_totals = {
        phase: sum(case[f"{phase}_ns"] for case in result["cases"].values())
        for phase in phases
    }
    phase_sum = sum(phase_totals.values())
    summary = {
        f"{phase}_ns": phase_totals[phase]
        for phase in phases
    }
    for phase in phases:
        summary[f"{phase}_share"] = phase_totals[phase] / phase_sum if phase_sum else 0.0
    dominant = max(phases, key=lambda phase: phase_totals[phase])
    summary["dominant_phase"] = dominant
    summary["dominant_share"] = summary[f"{dominant}_share"]
    summary["phase_sum_ns"] = phase_sum
    summary["max_walk_len"] = max(case["max_walk_len"] for case in result["cases"].values())
    summary["maximum_scratch_bytes"] = max(
        case["maximum_scratch_bytes"] for case in result["cases"].values()
    )
    summary["diameter_cuts"] = sum(case["diameter_cuts"] for case in result["cases"].values())
    summary["conductance_cuts"] = sum(
        case["conductance_cuts"] for case in result["cases"].values()
    )
    result["summary"] = summary
    result["status"] = "success"
except Exception as error:
    result["status"] = "failure"
    result["error"] = repr(error)
    print(f"split subphase profiling failed: {error}", flush=True)

subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=ROOT, check=False)
subprocess.run(["git", "clean", "-fd"], cwd=ROOT, check=False)
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
if result.get("status") == "success":
    update_documents(result)
else:
    status = STATUS.read_text().rstrip()
    status += (
        "\n\n## Forest-split subphase profile\n\n"
        f"- Status: `failure`.\n- Error: `{result.get('error', 'unknown')}`.\n"
        "- Production source was restored unchanged.\n"
        "- Evidence: `.ci/performance/split-forest-subphase-profile-latest.json`.\n"
    )
    STATUS.write_text(status + "\n")

WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass
run(["git", "config", "user.name", "github-actions[bot]"])
run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
run(["git", "add", "-A"])
run(["git", "commit", "-m", "perf: record split-forest subphase profile"])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push split subphase profile")
if result.get("status") != "success":
    raise SystemExit(1)
