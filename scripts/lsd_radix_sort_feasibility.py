import json
import math
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
TEMP_BIN = Path("benchmarks/src/bin/lsd-radix-feasibility.rs")
WORKFLOW = Path(".github/workflows/lsd-radix-sort-feasibility.yml")
SCRIPT = Path("scripts/lsd_radix_sort_feasibility.py")
RECORD = Path(".ci/performance/lsd-radix-sort-feasibility.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")

RUST_SOURCE = r'''use std::hint::black_box;
use std::time::Instant;

use cmg::{Aggregation, CmgHierarchy, CmgOptions, Laplacian};

const RADIX_BUCKETS: usize = 1 << 16;

#[derive(Clone, Copy, Debug, PartialEq)]
struct ProbeEdge {
    key: u64,
    weight: f64,
}

impl ProbeEdge {
    fn new(u: usize, v: usize, weight: f64) -> Self {
        let u = u32::try_from(u).unwrap();
        let v = u32::try_from(v).unwrap();
        Self {
            key: ((u as u64) << 32) | v as u64,
            weight,
        }
    }

    fn u(self) -> usize {
        (self.key >> 32) as usize
    }

    fn v(self) -> usize {
        (self.key as u32) as usize
    }
}

fn median(values: &mut [u128]) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn comparison_sort(raw: &mut [ProbeEdge]) {
    raw.sort_unstable_by_key(|edge| edge.key);
}

fn radix_pass(source: &[ProbeEdge], destination: &mut [ProbeEdge], shift: u32, positions: &mut [usize]) {
    positions.fill(0);
    for edge in source {
        positions[((edge.key >> shift) & 0xffff) as usize] += 1;
    }
    let mut running = 0_usize;
    for position in positions.iter_mut() {
        let count = *position;
        *position = running;
        running += count;
    }
    for &edge in source {
        let bucket = ((edge.key >> shift) & 0xffff) as usize;
        destination[positions[bucket]] = edge;
        positions[bucket] += 1;
    }
}

fn lsd_radix_sort(raw: &mut [ProbeEdge]) {
    if raw.len() < 2 {
        return;
    }
    let reference = raw[0].key;
    let varying = raw
        .iter()
        .skip(1)
        .fold(0_u64, |mask, edge| mask | (reference ^ edge.key));
    if varying == 0 {
        return;
    }

    let mut scratch = vec![raw[0]; raw.len()];
    let mut positions = vec![0_usize; RADIX_BUCKETS];
    let mut source_is_raw = true;
    for shift in [0_u32, 16, 32, 48] {
        if ((varying >> shift) & 0xffff) == 0 {
            continue;
        }
        if source_is_raw {
            radix_pass(raw, &mut scratch, shift, &mut positions);
        } else {
            radix_pass(&scratch, raw, shift, &mut positions);
        }
        source_is_raw = !source_is_raw;
    }
    if !source_is_raw {
        raw.copy_from_slice(&scratch);
    }
}

fn sort_weights(raw: &mut [ProbeEdge]) {
    let mut start = 0_usize;
    while start < raw.len() {
        let key = raw[start].key;
        let mut end = start + 1;
        while end < raw.len() && raw[end].key == key {
            end += 1;
        }
        if end - start > 1 {
            raw[start..end].sort_unstable_by(|left, right| left.weight.total_cmp(&right.weight));
        }
        start = end;
    }
}

fn compensated_add(sum: &mut f64, correction: &mut f64, value: f64) {
    let next = *sum + value;
    *correction += if sum.abs() >= value.abs() {
        (*sum - next) + value
    } else {
        (value - next) + *sum
    };
    *sum = next;
}

fn map_edges(graph: &Laplacian, aggregation: &Aggregation) -> Vec<ProbeEdge> {
    let labels = aggregation.labels();
    let mut mapped = Vec::with_capacity(graph.edge_count());
    for edge in graph.edges() {
        let left = labels[edge.u()];
        let right = labels[edge.v()];
        if left == right {
            continue;
        }
        let (u, v) = if left < right { (left, right) } else { (right, left) };
        mapped.push(ProbeEdge::new(u, v, edge.weight()));
    }
    mapped
}

fn merge_and_diagonal(raw: &mut Vec<ProbeEdge>, vertex_count: usize) -> Vec<f64> {
    let mut diagonal = vec![0.0_f64; vertex_count];
    let mut read = 0_usize;
    let mut write = 0_usize;
    while read < raw.len() {
        let key = raw[read].key;
        let mut sum = 0.0;
        let mut correction = 0.0;
        while read < raw.len() && raw[read].key == key {
            compensated_add(&mut sum, &mut correction, raw[read].weight);
            read += 1;
        }
        let edge = ProbeEdge {
            key,
            weight: sum + correction,
        };
        raw[write] = edge;
        diagonal[edge.u()] += edge.weight;
        diagonal[edge.v()] += edge.weight;
        write += 1;
    }
    raw.truncate(write);
    diagonal
}

fn verify(edges: &[ProbeEdge], diagonal: &[f64], expected: &Laplacian) {
    assert_eq!(edges.len(), expected.edge_count());
    for (candidate, reference) in edges.iter().zip(expected.edges()) {
        assert_eq!(candidate.u(), reference.u());
        assert_eq!(candidate.v(), reference.v());
        assert_eq!(candidate.weight.to_bits(), reference.weight().to_bits());
    }
    for (candidate, reference) in diagonal.iter().zip(expected.diagonal()) {
        assert_eq!(candidate.to_bits(), reference.to_bits());
    }
}

struct BenchGraph {
    graph: Laplacian,
    vertices: usize,
    edges: usize,
}

fn path_graph(vertices: usize) -> BenchGraph {
    let edges: Vec<_> = (0..vertices.saturating_sub(1))
        .map(|vertex| (vertex, vertex + 1, 1.0))
        .collect();
    BenchGraph {
        vertices,
        edges: edges.len(),
        graph: Laplacian::from_edges(vertices, edges).unwrap(),
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
    BenchGraph {
        vertices,
        edges: edges.len(),
        graph: Laplacian::from_edges(vertices, edges).unwrap(),
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
    let mut args = std::env::args().skip(1);
    let case = args.next().unwrap();
    let scale = args.next().unwrap().parse::<usize>().unwrap();
    let repetitions = args.next().unwrap().parse::<usize>().unwrap().max(1);
    let mode = args.next().unwrap();
    let bench = build_case(&case, scale);
    let hierarchy = CmgHierarchy::build(&bench.graph, CmgOptions::default()).unwrap();

    let mut mapping = 0_u128;
    let mut sorting = 0_u128;
    let mut merging = 0_u128;
    let mut total = 0_u128;
    let mut profiled_levels = 0_usize;
    for pair in hierarchy.levels().windows(2) {
        let fine = &pair[0];
        let coarse = &pair[1];
        let Some(aggregation) = fine.aggregation() else {
            continue;
        };
        let mut mapping_samples = Vec::with_capacity(repetitions);
        let mut sorting_samples = Vec::with_capacity(repetitions);
        let mut merging_samples = Vec::with_capacity(repetitions);
        let mut total_samples = Vec::with_capacity(repetitions);
        for _ in 0..repetitions {
            let total_start = Instant::now();
            let map_start = Instant::now();
            let mut mapped = map_edges(fine.graph(), aggregation);
            mapping_samples.push(map_start.elapsed().as_nanos());
            let sort_start = Instant::now();
            match mode.as_str() {
                "comparison" => comparison_sort(&mut mapped),
                "lsd" => lsd_radix_sort(&mut mapped),
                _ => panic!("mode must be comparison or lsd"),
            }
            sort_weights(&mut mapped);
            sorting_samples.push(sort_start.elapsed().as_nanos());
            let merge_start = Instant::now();
            let diagonal = merge_and_diagonal(&mut mapped, aggregation.coarse_dimension());
            merging_samples.push(merge_start.elapsed().as_nanos());
            total_samples.push(total_start.elapsed().as_nanos());
            verify(&mapped, &diagonal, coarse.graph());
            black_box((&mapped, &diagonal));
        }
        mapping += median(&mut mapping_samples);
        sorting += median(&mut sorting_samples);
        merging += median(&mut merging_samples);
        total += median(&mut total_samples);
        profiled_levels += 1;
    }

    println!(
        "{{\"case\":\"{case}\",\"mode\":\"{mode}\",\"scale\":{scale},\"vertices\":{},\"edges\":{},\"levels\":{},\"profiled_levels\":{profiled_levels},\"repetitions\":{repetitions},\"mapping_ns\":{mapping},\"sorting_ns\":{sorting},\"merging_ns\":{merging},\"total_ns\":{total}}}",
        bench.vertices,
        bench.edges,
        hierarchy.levels().len(),
    );
}
'''


def run(command, *, timeout=7200, check=True):
    command = [str(item) for item in command]
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
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


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-lsd-feasibility-{tag}.time")
    completed = run(
        ["/usr/bin/time", "-v", "-o", time_path, binary, *arguments]
    )
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected radix feasibility output: {payloads}")
    rss_match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)",
        time_path.read_text(),
    )
    if rss_match is None:
        raise RuntimeError("peak RSS missing")
    payload = payloads[0]
    payload["peak_rss_kib"] = int(rss_match.group(1))
    return payload


def compare_case(binary, case, scale, repetitions):
    observations = {"comparison": [], "lsd": []}
    schedule = ("comparison", "lsd", "lsd", "comparison")
    for index, mode in enumerate(schedule):
        observation = sample(
            binary,
            [case, str(scale), str(repetitions), mode],
            f"{case}-{scale}-{mode}-{index}",
        )
        observations[mode].append(observation)
    reference = observations["comparison"][0]
    stable = ("case", "scale", "vertices", "edges", "levels", "profiled_levels")
    for observation in observations["comparison"][1:] + observations["lsd"]:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{case}: feasibility metadata changed for {key}")
    result = {"metadata": {key: reference[key] for key in stable}}
    for metric in ("mapping_ns", "sorting_ns", "merging_ns", "total_ns"):
        comparison = statistics.median(
            item[metric] for item in observations["comparison"]
        )
        lsd = statistics.median(item[metric] for item in observations["lsd"])
        result[f"comparison_{metric}"] = comparison
        result[f"lsd_{metric}"] = lsd
        result[f"lsd_over_comparison_{metric}"] = lsd / comparison
    comparison_rss = max(item["peak_rss_kib"] for item in observations["comparison"])
    lsd_rss = max(item["peak_rss_kib"] for item in observations["lsd"])
    result["comparison_peak_rss_kib"] = comparison_rss
    result["lsd_peak_rss_kib"] = lsd_rss
    result["lsd_over_comparison_peak_rss"] = lsd_rss / comparison_rss
    return result


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    rows = []
    for name, case in result["cases"].items():
        rows.append(
            f"| {name} | {case['lsd_over_comparison_sorting_ns']:.3f}x | "
            f"{case['lsd_over_comparison_total_ns']:.3f}x | "
            f"{case['lsd_over_comparison_peak_rss']:.3f}x |"
        )
    checkpoint = f'''### Streaming LSD radix feasibility — 2026-08-24

- Four-pass 16-bit stable LSD sorting with constant-pass elimination was classified as **{result.get("classification", "unknown")}**.
- Every candidate contraction matched the canonical hierarchy graph bitwise.

| Case | Sort ratio | Full manual-contraction ratio | Process RSS ratio |
|---|---:|---:|---:|
{chr(10).join(rows)}

- Active worker/dense geometric sort / total ratios: `{result.get("active_sort_geometric_ratio", 1.0):.3f}x` / `{result.get("active_total_geometric_ratio", 1.0):.3f}x`.
- Worst process-RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- No production source changed in this feasibility probe.
- Evidence: `.ci/performance/lsd-radix-sort-feasibility.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Streaming LSD radix feasibility — 2026-08-24\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Streaming LSD radix feasibility

- Classification: `{result.get("classification", "unknown")}`.
- Active sort / total ratios: `{result.get("active_sort_geometric_ratio", 1.0):.3f}x` / `{result.get("active_total_geometric_ratio", 1.0):.3f}x`.
- Worst RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Production was unchanged.
- Evidence: `.ci/performance/lsd-radix-sort-feasibility.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Streaming LSD radix feasibility\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + block + status[end:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")


result = {
    "schema_version": 1,
    "experiment": "streaming-lsd-radix-sort-feasibility",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "status": "not_run",
    "cases": {},
}
try:
    TEMP_BIN.parent.mkdir(parents=True, exist_ok=True)
    TEMP_BIN.write_text(RUST_SOURCE)
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    run(["cargo", "clippy", "--manifest-path", "benchmarks/Cargo.toml", "--all-targets", "--", "-D", "warnings"])
    run(["cargo", "build", "--release", "--manifest-path", "benchmarks/Cargo.toml", "--bin", "lsd-radix-feasibility"])
    binary = Path("benchmarks/target/release/lsd-radix-feasibility")
    specs = (
        ("path-500k", "path", 500_000, 5),
        ("worker-firm-750k", "worker-firm", 250_000, 5),
        ("worker-firm-1.5m", "worker-firm", 500_000, 5),
        ("dense-worker-firm-1.6m", "dense-worker-firm", 100_000, 5),
    )
    for name, case, scale, repetitions in specs:
        result["cases"][name] = compare_case(
            binary,
            case,
            scale,
            repetitions,
        )
    active = (
        "worker-firm-750k",
        "worker-firm-1.5m",
        "dense-worker-firm-1.6m",
    )
    result["active_sort_geometric_ratio"] = geometric(
        [
            result["cases"][name]["lsd_over_comparison_sorting_ns"]
            for name in active
        ]
    )
    result["active_total_geometric_ratio"] = geometric(
        [
            result["cases"][name]["lsd_over_comparison_total_ns"]
            for name in active
        ]
    )
    result["worst_peak_rss_ratio"] = max(
        case["lsd_over_comparison_peak_rss"]
        for case in result["cases"].values()
    )
    result["classification"] = (
        "promising-for-memory-routed-production-gate"
        if result["active_sort_geometric_ratio"] <= 0.75
        and result["active_total_geometric_ratio"] <= 0.85
        and result["worst_peak_rss_ratio"] <= 1.25
        else "not-promising"
    )
    result["status"] = "success"
    update_documents(result)
except Exception as error:
    result["status"] = "failure"
    result["error"] = repr(error)
    print(f"LSD radix feasibility failed safely: {error}", flush=True)
finally:
    TEMP_BIN.unlink(missing_ok=True)
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"], check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass

run(["git", "config", "user.name", "github-actions[bot]"])
run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
run(["git", "add", "-A"])
run(["git", "commit", "-m", "perf: record streaming radix-sort feasibility"])
for _ in range(12):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push LSD radix feasibility record")

if result["status"] != "success":
    raise SystemExit(1)
