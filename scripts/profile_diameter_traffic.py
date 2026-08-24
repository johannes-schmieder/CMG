import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
BENCH = Path("src/bin/diameter-traffic-profile.rs")
WORKFLOW = Path(".github/workflows/profile-diameter-traffic.yml")
SCRIPT = Path("scripts/profile_diameter_traffic.py")
RECORD = Path(".ci/performance/diameter-traffic-profile-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")

BENCH_SOURCE = r'''use cmg::{
    CmgHierarchy, CmgOptions, Laplacian, maximum_weight_forest, split_forest,
};

#[derive(Clone, Default)]
struct Traffic {
    profiled_parent_vectors: u64,
    profiled_vertices: u64,
    start_scan_vertices: u64,
    front_entries: u64,
    parent_steps: u64,
    first_visit_steps: u64,
    revisit_steps: u64,
    root_terminations: u64,
    two_cycle_terminations: u64,
    diameter_cuts: u64,
    refronts: u64,
    cut_suffix_updates: u64,
    cut_prefix_updates: u64,
    terminal_prefix_updates: u64,
    walk_pushes: u64,
    max_walk_len: usize,
    long_walks_ge_16: u64,
    walk_length_histogram: [u64; 16],
    conductance_start_scans: u64,
    conductance_front_entries: u64,
    conductance_parent_steps: u64,
    conductance_cuts: u64,
    conductance_refronts: u64,
    conductance_adjust_updates: u64,
    checksum: u64,
}

impl Traffic {
    fn absorb(&mut self, other: &Self) {
        self.profiled_parent_vectors += other.profiled_parent_vectors;
        self.profiled_vertices += other.profiled_vertices;
        self.start_scan_vertices += other.start_scan_vertices;
        self.front_entries += other.front_entries;
        self.parent_steps += other.parent_steps;
        self.first_visit_steps += other.first_visit_steps;
        self.revisit_steps += other.revisit_steps;
        self.root_terminations += other.root_terminations;
        self.two_cycle_terminations += other.two_cycle_terminations;
        self.diameter_cuts += other.diameter_cuts;
        self.refronts += other.refronts;
        self.cut_suffix_updates += other.cut_suffix_updates;
        self.cut_prefix_updates += other.cut_prefix_updates;
        self.terminal_prefix_updates += other.terminal_prefix_updates;
        self.walk_pushes += other.walk_pushes;
        self.max_walk_len = self.max_walk_len.max(other.max_walk_len);
        self.long_walks_ge_16 += other.long_walks_ge_16;
        for (target, value) in self
            .walk_length_histogram
            .iter_mut()
            .zip(other.walk_length_histogram)
        {
            *target += value;
        }
        self.conductance_start_scans += other.conductance_start_scans;
        self.conductance_front_entries += other.conductance_front_entries;
        self.conductance_parent_steps += other.conductance_parent_steps;
        self.conductance_cuts += other.conductance_cuts;
        self.conductance_refronts += other.conductance_refronts;
        self.conductance_adjust_updates += other.conductance_adjust_updates;
        self.checksum = self
            .checksum
            .wrapping_mul(0x517c_c1b7_2722_0a95)
            .wrapping_add(other.checksum);
    }
}

fn checksum(values: &[usize]) -> u64 {
    values.iter().enumerate().fold(0_u64, |value, (index, item)| {
        value
            .wrapping_mul(0x9e37_79b1_85eb_ca87)
            .wrapping_add((index as u64).rotate_left(17))
            .wrapping_add(*item as u64)
    })
}

fn profile_parent(parent: &[usize]) -> Traffic {
    let reference = split_forest(parent).unwrap();
    let n = parent.len();
    let mut forest = parent.to_vec();
    let mut ancestors = vec![0_i64; n];
    let mut indegree = vec![0_u32; n];
    let mut visited = vec![false; n];
    let mut traffic = Traffic {
        profiled_parent_vectors: 1,
        profiled_vertices: n as u64,
        start_scan_vertices: n as u64,
        conductance_start_scans: n as u64,
        ..Traffic::default()
    };

    for &target in &forest {
        indegree[target] = indegree[target]
            .checked_add(1)
            .expect("forest indegree overflow");
    }

    let mut walk = Vec::new();
    let mut new_ancestors = Vec::new();
    for start in 0..n {
        let mut current = start;
        let mut continue_walk = true;
        while continue_walk && indegree[current] == 0 && !visited[current] {
            traffic.front_entries += 1;
            continue_walk = false;
            let mut ancestors_in_path = 0_i64;
            walk.clear();
            walk.push(current);
            new_ancestors.clear();
            new_ancestors.push(0_i64);
            let mut k = 0_usize;

            while k <= 5 || visited[current] {
                traffic.parent_steps += 1;
                current = forest[current];
                let root_termination = current == walk[k];
                let two_cycle_termination = k > 0 && current == walk[k - 1];
                if root_termination || two_cycle_termination {
                    traffic.root_terminations += u64::from(root_termination);
                    traffic.two_cycle_terminations += u64::from(two_cycle_termination);
                    break;
                }
                k += 1;
                walk.push(current);
                traffic.walk_pushes += 1;
                if visited[current] {
                    traffic.revisit_steps += 1;
                } else {
                    traffic.first_visit_steps += 1;
                }
                ancestors_in_path += i64::from(u8::from(!visited[current]));
                new_ancestors.push(ancestors_in_path);
            }

            let walk_len = walk.len();
            traffic.max_walk_len = traffic.max_walk_len.max(walk_len);
            let bucket = walk_len.min(16) - 1;
            traffic.walk_length_histogram[bucket] += 1;
            traffic.long_walks_ge_16 += u64::from(walk_len >= 16);

            if k > 5 {
                traffic.diameter_cuts += 1;
                let middle = k / 2;
                forest[walk[middle]] = walk[middle];
                let next = walk[middle + 1];
                indegree[next] = indegree[next]
                    .checked_sub(1)
                    .expect("forest indegree invariant");
                let removed = ancestors[walk[middle]];
                traffic.cut_suffix_updates += (k - middle) as u64;
                for &vertex in &walk[(middle + 1)..=k] {
                    ancestors[vertex] -= removed;
                }
                traffic.cut_prefix_updates += (middle + 1) as u64;
                for index in 0..=middle {
                    let vertex = walk[index];
                    visited[vertex] = true;
                    ancestors[vertex] += new_ancestors[index];
                }
                current = next;
                continue_walk = true;
                traffic.refronts += 1;
            }

            if !continue_walk {
                traffic.terminal_prefix_updates += (k + 1) as u64;
                for index in 0..=k {
                    let vertex = walk[index];
                    ancestors[vertex] += new_ancestors[index];
                    visited[vertex] = true;
                }
            }
        }
    }

    for start in 0..n {
        let mut current = start;
        'new_front: while indegree[current] == 0 {
            traffic.conductance_front_entries += 1;
            let mut previous = current;
            loop {
                traffic.conductance_parent_steps += 1;
                let next = forest[current];
                if next == current || next == previous {
                    break 'new_front;
                }
                if ancestors[current] > 2 && ancestors[next] - ancestors[current] > 2 {
                    traffic.conductance_cuts += 1;
                    forest[current] = current;
                    indegree[next] = indegree[next]
                        .checked_sub(1)
                        .expect("forest indegree invariant");
                    let removed_ancestors = ancestors[current];

                    let mut adjustment_previous = current;
                    let mut adjustment_current = next;
                    loop {
                        traffic.conductance_adjust_updates += 1;
                        ancestors[adjustment_current] -= removed_ancestors;
                        let adjustment_next = forest[adjustment_current];
                        if adjustment_next == adjustment_current
                            || adjustment_next == adjustment_previous
                        {
                            break;
                        }
                        adjustment_previous = adjustment_current;
                        adjustment_current = adjustment_next;
                    }

                    current = next;
                    traffic.conductance_refronts += 1;
                    continue 'new_front;
                }
                previous = current;
                current = next;
            }
        }
    }

    assert_eq!(forest, reference, "traffic profiler diverged from production split");
    traffic.checksum = checksum(&forest);
    traffic
}

fn path_graph(vertices: usize) -> (Laplacian, usize) {
    let edges: Vec<_> = (0..vertices.saturating_sub(1))
        .map(|vertex| (vertex, vertex + 1, 1.0))
        .collect();
    let input_edges = edges.len();
    (Laplacian::from_edges(vertices, edges).unwrap(), input_edges)
}

fn worker_firm_graph(per_side: usize, degree: usize) -> (Laplacian, usize) {
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
    (Laplacian::from_edges(vertices, edges).unwrap(), input_edges)
}

fn build_case(case: &str, scale: usize) -> (Laplacian, usize) {
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
    let (graph, input_edges) = build_case(&case, scale);
    let hierarchy = CmgHierarchy::build(&graph, CmgOptions::default()).unwrap();
    let mut aggregate = Traffic::default();
    let mut profiled_edges = 0_u64;

    for level in hierarchy.levels() {
        if level.aggregation().is_none() {
            continue;
        }
        let level_graph = level.graph();
        let parent = maximum_weight_forest(level_graph).0;
        let traffic = profile_parent(&parent);
        aggregate.absorb(&traffic);
        profiled_edges += level_graph.edge_count() as u64;
    }

    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"input_edges\":{input_edges},\"vertices\":{},\"edges\":{},\"levels\":{},\"profiled_parent_vectors\":{},\"profiled_vertices\":{},\"profiled_edges\":{profiled_edges},\"start_scan_vertices\":{},\"front_entries\":{},\"parent_steps\":{},\"first_visit_steps\":{},\"revisit_steps\":{},\"root_terminations\":{},\"two_cycle_terminations\":{},\"diameter_cuts\":{},\"refronts\":{},\"cut_suffix_updates\":{},\"cut_prefix_updates\":{},\"terminal_prefix_updates\":{},\"walk_pushes\":{},\"max_walk_len\":{},\"long_walks_ge_16\":{},\"walk_length_histogram\":{:?},\"conductance_start_scans\":{},\"conductance_front_entries\":{},\"conductance_parent_steps\":{},\"conductance_cuts\":{},\"conductance_refronts\":{},\"conductance_adjust_updates\":{},\"checksum\":{}}}",
        graph.vertex_count(),
        graph.edge_count(),
        hierarchy.levels().len(),
        aggregate.profiled_parent_vectors,
        aggregate.profiled_vertices,
        aggregate.start_scan_vertices,
        aggregate.front_entries,
        aggregate.parent_steps,
        aggregate.first_visit_steps,
        aggregate.revisit_steps,
        aggregate.root_terminations,
        aggregate.two_cycle_terminations,
        aggregate.diameter_cuts,
        aggregate.refronts,
        aggregate.cut_suffix_updates,
        aggregate.cut_prefix_updates,
        aggregate.terminal_prefix_updates,
        aggregate.walk_pushes,
        aggregate.max_walk_len,
        aggregate.long_walks_ge_16,
        aggregate.walk_length_histogram,
        aggregate.conductance_start_scans,
        aggregate.conductance_front_entries,
        aggregate.conductance_parent_steps,
        aggregate.conductance_cuts,
        aggregate.conductance_refronts,
        aggregate.conductance_adjust_updates,
        aggregate.checksum,
    );
}
'''


def run(command, *, timeout=7200):
    print("+", " ".join(str(item) for item in command), flush=True)
    completed = subprocess.run(
        [str(item) for item in command],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end="")
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): "
            f"{' '.join(str(item) for item in command)}"
        )
    return completed


def ratio(numerator, denominator):
    return 0.0 if denominator == 0 else numerator / denominator


BENCH.parent.mkdir(parents=True, exist_ok=True)
BENCH.write_text(BENCH_SOURCE)
result = {
    "schema_version": 1,
    "profile": "diameter-walk-and-ancestor-traffic",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(),
    "status": "not_run",
    "cases": {},
}

try:
    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--bin", "diameter-traffic-profile"])
    binary = Path("target/release/diameter-traffic-profile")
    specs = (
        ("path-1m", ("path", 1_000_000)),
        ("worker-firm-1.5m", ("worker-firm", 500_000)),
        ("worker-firm-3m", ("worker-firm", 1_000_000)),
        ("dense-worker-firm-1.6m", ("dense-worker-firm", 100_000)),
    )
    totals = {
        "profiled_vertices": 0,
        "front_entries": 0,
        "parent_steps": 0,
        "first_visit_steps": 0,
        "revisit_steps": 0,
        "diameter_cuts": 0,
        "refronts": 0,
        "cut_suffix_updates": 0,
        "cut_prefix_updates": 0,
        "terminal_prefix_updates": 0,
        "conductance_parent_steps": 0,
        "conductance_adjust_updates": 0,
        "walk_length_histogram": [0] * 16,
        "max_walk_len": 0,
    }
    for name, (case, scale) in specs:
        completed = run([binary, case, str(scale)])
        payloads = [
            json.loads(line)
            for line in completed.stdout.splitlines()
            if line.strip().startswith("{")
        ]
        if len(payloads) != 1:
            raise RuntimeError(f"unexpected profiler output for {name}: {payloads}")
        payload = payloads[0]
        payload["front_entry_rate"] = ratio(
            payload["front_entries"], payload["profiled_vertices"]
        )
        payload["parent_steps_per_profiled_vertex"] = ratio(
            payload["parent_steps"], payload["profiled_vertices"]
        )
        payload["revisit_share"] = ratio(
            payload["revisit_steps"],
            payload["first_visit_steps"] + payload["revisit_steps"],
        )
        ancestor_updates = (
            payload["cut_suffix_updates"]
            + payload["cut_prefix_updates"]
            + payload["terminal_prefix_updates"]
        )
        payload["diameter_ancestor_updates"] = ancestor_updates
        payload["ancestor_updates_per_parent_step"] = ratio(
            ancestor_updates, payload["parent_steps"]
        )
        result["cases"][name] = payload
        for key in (
            "profiled_vertices",
            "front_entries",
            "parent_steps",
            "first_visit_steps",
            "revisit_steps",
            "diameter_cuts",
            "refronts",
            "cut_suffix_updates",
            "cut_prefix_updates",
            "terminal_prefix_updates",
            "conductance_parent_steps",
            "conductance_adjust_updates",
        ):
            totals[key] += payload[key]
        totals["max_walk_len"] = max(totals["max_walk_len"], payload["max_walk_len"])
        for index, value in enumerate(payload["walk_length_histogram"]):
            totals["walk_length_histogram"][index] += value

    ancestor_updates = (
        totals["cut_suffix_updates"]
        + totals["cut_prefix_updates"]
        + totals["terminal_prefix_updates"]
    )
    counted_diameter_work = totals["parent_steps"] + ancestor_updates
    summary = dict(totals)
    summary.update(
        {
            "front_entry_rate": ratio(
                totals["front_entries"], totals["profiled_vertices"]
            ),
            "parent_steps_per_profiled_vertex": ratio(
                totals["parent_steps"], totals["profiled_vertices"]
            ),
            "revisit_share": ratio(
                totals["revisit_steps"],
                totals["first_visit_steps"] + totals["revisit_steps"],
            ),
            "diameter_ancestor_updates": ancestor_updates,
            "ancestor_updates_per_parent_step": ratio(
                ancestor_updates, totals["parent_steps"]
            ),
            "parent_step_share_of_counted_diameter_work": ratio(
                totals["parent_steps"], counted_diameter_work
            ),
            "ancestor_update_share_of_counted_diameter_work": ratio(
                ancestor_updates, counted_diameter_work
            ),
        }
    )
    result["summary"] = summary
    result["status"] = "success"
except Exception as error:
    result["status"] = "failure"
    result["error"] = repr(error)
    print(f"diameter traffic profiler failed: {error}", flush=True)

BENCH.unlink(missing_ok=True)
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

summary = result.get("summary", {})
checkpoint = f'''### Diameter traffic profile — 2026-08-24

- Status: `{result.get("status")}`; every profiled split parent vector matched production exactly.
- Profiled vertices / diameter fronts / parent steps: `{summary.get("profiled_vertices", 0)}` / `{summary.get("front_entries", 0)}` / `{summary.get("parent_steps", 0)}`.
- Parent steps per profiled vertex: `{summary.get("parent_steps_per_profiled_vertex", 0.0):.3f}`; revisit share: `{summary.get("revisit_share", 0.0):.3%}`.
- Diameter ancestor updates per parent step: `{summary.get("ancestor_updates_per_parent_step", 0.0):.3f}`.
- Counted diameter-work shares: parent traversal `{summary.get("parent_step_share_of_counted_diameter_work", 0.0):.1%}`, ancestor updates `{summary.get("ancestor_update_share_of_counted_diameter_work", 0.0):.1%}`.
- Maximum walk length: `{summary.get("max_walk_len", 0)}`; histogram: `{summary.get("walk_length_histogram", [])}`.
- Evidence: `.ci/performance/diameter-traffic-profile-latest.json`.

'''
plan = PLAN.read_text()
marker = "## Current next action\n"
if marker not in plan:
    raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
if "### Diameter traffic profile — 2026-08-24\n" not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
prefix, _, _ = plan.partition(marker)
plan = prefix + marker + (
    "1. Use the measured diameter traffic mix to select one exact-preserving traversal or scratch-layout gate.\n"
    "2. Refresh cumulative retained optimization and memory guidance.\n"
    "3. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
    "4. Preserve exact split parents and complete hierarchy diagnostics in every gate.\n"
)
PLAN.write_text(plan)

status_block = f'''## Diameter traffic profile

- Status: `{result.get("status")}`.
- Parent steps per profiled vertex: `{summary.get("parent_steps_per_profiled_vertex", 0.0):.3f}`.
- Revisit share: `{summary.get("revisit_share", 0.0):.3%}`.
- Counted parent-step / ancestor-update shares: `{summary.get("parent_step_share_of_counted_diameter_work", 0.0):.1%}` / `{summary.get("ancestor_update_share_of_counted_diameter_work", 0.0):.1%}`.
- Maximum walk length: `{summary.get("max_walk_len", 0)}`.
- Evidence: `.ci/performance/diameter-traffic-profile-latest.json`.
'''
status = STATUS.read_text().rstrip()
heading = "## Diameter traffic profile\n"
if heading in status:
    start = status.index(heading)
    end = status.find("\n## ", start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + status_block + status[end:]
else:
    status += "\n\n" + status_block
STATUS.write_text(status.rstrip() + "\n")

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
run(["git", "commit", "-m", "perf: record diameter traffic profile"])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = subprocess.run(
        ["git", "push", "origin", "HEAD:main"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(pushed.stdout, end="")
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push diameter traffic profile")

if result.get("status") != "success":
    raise SystemExit(1)
