import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
TEMP_BIN = Path('src/bin/endpoint-key-structure-profile.rs')
WORKFLOW = Path('.github/workflows/profile-endpoint-key-structure.yml')
SCRIPT = Path('scripts/profile_endpoint_key_structure.py')
RECORD = Path('.ci/performance/endpoint-key-structure-profile.json')
PLAN = Path('PERFORMANCE_PLAN.md')
STATUS = Path('PERFORMANCE_STATUS.md')

RUST_SOURCE = r'''use cmg::{CmgHierarchy, CmgOptions, Laplacian};

fn path_graph(vertices: usize) -> Laplacian {
    Laplacian::from_edges(
        vertices,
        (0..vertices.saturating_sub(1)).map(|vertex| (vertex, vertex + 1, 1.0)),
    )
    .unwrap()
}

fn worker_firm_graph(per_side: usize, degree: usize) -> Laplacian {
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
    Laplacian::from_edges(vertices, edges).unwrap()
}

fn percentile(sorted: &[usize], numerator: usize, denominator: usize) -> usize {
    if sorted.is_empty() {
        return 0;
    }
    let index = (sorted.len() - 1).saturating_mul(numerator) / denominator;
    sorted[index]
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().expect("case");
    let scale = arguments
        .next()
        .expect("scale")
        .parse::<usize>()
        .expect("numeric scale");
    let graph = match case.as_str() {
        "path" => path_graph(scale),
        "worker-firm" => worker_firm_graph(scale, 3),
        "dense-worker-firm" => worker_firm_graph(scale, 16),
        _ => panic!("unknown case"),
    };
    let hierarchy = CmgHierarchy::build(&graph, CmgOptions::default()).unwrap();

    for (level_index, level) in hierarchy.levels().iter().enumerate() {
        let Some(aggregation) = level.aggregation() else {
            continue;
        };
        let labels = aggregation.labels();
        let coarse_vertices = aggregation.coarse_dimension();
        assert!(coarse_vertices <= u32::MAX as usize);

        let mut keys = Vec::with_capacity(level.graph().edge_count());
        let mut internal_edges = 0_usize;
        let mut u_counts = vec![0_usize; coarse_vertices];
        for edge in level.graph().edges() {
            let left = labels[edge.u()];
            let right = labels[edge.v()];
            if left == right {
                internal_edges += 1;
                continue;
            }
            let (u, v) = if left < right {
                (left, right)
            } else {
                (right, left)
            };
            assert!(u <= u32::MAX as usize && v <= u32::MAX as usize);
            keys.push(((u as u64) << 32) | v as u64);
            u_counts[u] += 1;
        }

        let mapped_edges = keys.len();
        let mut descents = 0_usize;
        let mut equal_adjacent = 0_usize;
        let mut longest_run = usize::from(mapped_edges != 0);
        let mut current_run = longest_run;
        for pair in keys.windows(2) {
            if pair[1] < pair[0] {
                descents += 1;
                current_run = 1;
            } else {
                current_run += 1;
                longest_run = longest_run.max(current_run);
                if pair[1] == pair[0] {
                    equal_adjacent += 1;
                }
            }
        }
        let natural_runs = usize::from(mapped_edges != 0) + descents;

        let mut bucket_sizes: Vec<usize> = u_counts
            .into_iter()
            .filter(|count| *count != 0)
            .collect();
        bucket_sizes.sort_unstable();
        let distinct_u = bucket_sizes.len();
        let maximum_bucket = bucket_sizes.last().copied().unwrap_or(0);
        let axis_work: f64 = bucket_sizes
            .iter()
            .filter(|&&count| count > 1)
            .map(|&count| {
                let value = count as f64;
                value * value.log2()
            })
            .sum();
        let packed_work = if mapped_edges > 1 {
            let value = mapped_edges as f64;
            value * value.log2()
        } else {
            0.0
        };
        let estimated_axis_comparison_ratio = if packed_work == 0.0 {
            0.0
        } else {
            axis_work / packed_work
        };

        let mut sorted_keys = keys.clone();
        sorted_keys.sort_unstable();
        let unique_pairs = sorted_keys
            .iter()
            .enumerate()
            .filter(|(index, key)| *index == 0 || **key != sorted_keys[*index - 1])
            .count();
        let duplicate_edges = mapped_edges.saturating_sub(unique_pairs);

        println!(
            concat!(
                "{{\"case\":\"{}\",\"scale\":{},\"level\":{},",
                "\"fine_vertices\":{},\"fine_edges\":{},",
                "\"coarse_vertices\":{},\"internal_edges\":{},",
                "\"mapped_edges\":{},\"unique_pairs\":{},",
                "\"duplicate_edges\":{},\"duplicate_rate\":{:.12},",
                "\"natural_runs\":{},\"descent_rate\":{:.12},",
                "\"longest_nondecreasing_run\":{},",
                "\"equal_adjacent_unsorted\":{},",
                "\"distinct_first_endpoints\":{},",
                "\"mean_first_endpoint_bucket\":{:.6},",
                "\"p50_first_endpoint_bucket\":{},",
                "\"p90_first_endpoint_bucket\":{},",
                "\"p99_first_endpoint_bucket\":{},",
                "\"max_first_endpoint_bucket\":{},",
                "\"estimated_axis_comparison_ratio\":{:.12}}}"
            ),
            case,
            scale,
            level_index,
            level.graph().vertex_count(),
            level.graph().edge_count(),
            coarse_vertices,
            internal_edges,
            mapped_edges,
            unique_pairs,
            duplicate_edges,
            duplicate_edges as f64 / mapped_edges.max(1) as f64,
            natural_runs,
            descents as f64 / mapped_edges.saturating_sub(1).max(1) as f64,
            longest_run,
            equal_adjacent,
            distinct_u,
            mapped_edges as f64 / distinct_u.max(1) as f64,
            percentile(&bucket_sizes, 1, 2),
            percentile(&bucket_sizes, 9, 10),
            percentile(&bucket_sizes, 99, 100),
            maximum_bucket,
            estimated_axis_comparison_ratio,
        );
    }
}
'''


def run(command, *, timeout=7200):
    command = [str(item) for item in command]
    print('+', ' '.join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end='')
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
        )
    return completed


TEMP_BIN.parent.mkdir(parents=True, exist_ok=True)
TEMP_BIN.write_text(RUST_SOURCE)
run(['cargo', 'fmt', '--all'])
run(['cargo', 'fmt', '--all', '--', '--check'])
run([
    'cargo', 'clippy', '--bin', 'endpoint-key-structure-profile',
    '--all-features', '--', '-D', 'warnings',
])
run([
    'cargo', 'build', '--release', '--bin', 'endpoint-key-structure-profile',
])

binary = Path('target/release/endpoint-key-structure-profile')
specs = (
    ('path-1m', ['path', '1000000']),
    ('worker-firm-1.5m', ['worker-firm', '500000']),
    ('worker-firm-2.25m', ['worker-firm', '750000']),
    ('worker-firm-3m', ['worker-firm', '1000000']),
    ('dense-worker-firm-800k', ['dense-worker-firm', '50000']),
    ('dense-worker-firm-1.6m', ['dense-worker-firm', '100000']),
)
cases = {}
for name, arguments in specs:
    completed = run([binary, *arguments])
    levels = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if not levels:
        raise RuntimeError(f'{name}: no profiled hierarchy levels')
    cases[name] = levels

summaries = {}
for name, levels in cases.items():
    total_mapped = sum(level['mapped_edges'] for level in levels)
    weighted_axis = sum(
        level['estimated_axis_comparison_ratio'] * level['mapped_edges']
        for level in levels
    ) / max(1, total_mapped)
    weighted_duplicate = sum(
        level['duplicate_rate'] * level['mapped_edges']
        for level in levels
    ) / max(1, total_mapped)
    weighted_descent = sum(
        level['descent_rate'] * level['mapped_edges']
        for level in levels
    ) / max(1, total_mapped)
    qualifying_axis_levels = [
        level
        for level in levels
        if level['mapped_edges'] >= 500_000
        and level['estimated_axis_comparison_ratio'] <= 0.75
    ]
    summaries[name] = {
        'level_count': len(levels),
        'total_mapped_edges': total_mapped,
        'weighted_axis_comparison_ratio': weighted_axis,
        'weighted_duplicate_rate': weighted_duplicate,
        'weighted_descent_rate': weighted_descent,
        'qualifying_axis_level_count': len(qualifying_axis_levels),
        'maximum_first_endpoint_bucket': max(
            level['max_first_endpoint_bucket'] for level in levels
        ),
        'maximum_duplicate_rate': max(level['duplicate_rate'] for level in levels),
    }

result = {
    'schema_version': 1,
    'profile': 'mapped-endpoint-key-structure',
    'source_sha': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True
    ).strip(),
    'status': 'success',
    'cases': cases,
    'summaries': summaries,
}
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

summary_rows = '\n'.join(
    f"| {name} | {summary['level_count']} | "
    f"{summary['weighted_axis_comparison_ratio']:.3f} | "
    f"{summary['weighted_duplicate_rate']:.1%} | "
    f"{summary['weighted_descent_rate']:.1%} | "
    f"{summary['qualifying_axis_level_count']} |"
    for name, summary in summaries.items()
)
checkpoint = f'''### Mapped endpoint-key structure profile — 2026-08-24

- Profiled SHA: `{result['source_sha']}`.
- Profiles preserve production code and inspect mapped coarse endpoints before sorting.

| Case | Levels | Estimated axis/packed comparison work | Duplicate rate | Descent rate | Large low-axis-work levels |
|---|---:|---:|---:|---:|---:|
{summary_rows}

- Evidence: `.ci/performance/endpoint-key-structure-profile.json`.

'''
plan = PLAN.read_text()
marker = '## Current next action\n'
heading = '### Mapped endpoint-key structure profile — 2026-08-24\n'
if heading in plan:
    start = plan.index(heading)
    end = plan.index(marker, start)
    plan = plan[:start] + checkpoint + plan[end:]
elif marker in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
else:
    plan += '\n\n' + checkpoint
if marker in plan:
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        '1. Resolve the routed endpoint-axis benchmark decision.\n'
        '2. Use measured bucket work and crossover evidence for any revised router.\n'
        '3. Re-profile contraction and hierarchy after any retained sorting change.\n'
        '4. Run manual 1–32 thread qualification when suitable hardware is available.\n'
    )
PLAN.write_text(plan)

block = f'''## Mapped endpoint-key structure profile

- Profiled SHA: `{result['source_sha']}`.
- Cases: `{len(cases)}`; production source mutation: `none`.
- Evidence: `.ci/performance/endpoint-key-structure-profile.json`.
'''
status = STATUS.read_text().rstrip()
heading = '## Mapped endpoint-key structure profile\n'
if heading in status:
    start = status.index(heading)
    end = status.find('\n## ', start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + block + status[end:]
else:
    status += '\n\n' + block
STATUS.write_text(status.rstrip() + '\n')

TEMP_BIN.unlink(missing_ok=True)
WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
try:
    Path('scripts').rmdir()
except OSError:
    pass
run(['cargo', 'fmt', '--all'])
run(['git', 'config', 'user.name', 'github-actions[bot]'])
run([
    'git', 'config', 'user.email',
    '41898282+github-actions[bot]@users.noreply.github.com',
])
run(['git', 'add', '-A'])
run(['git', 'commit', '-m', 'perf: record endpoint-key structure profile'])
for _ in range(10):
    run(['git', 'pull', '--rebase', 'origin', 'main'])
    pushed = subprocess.run(
        ['git', 'push', 'origin', 'HEAD:main'],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(pushed.stdout, end='')
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError('failed to push endpoint-key structure profile')
