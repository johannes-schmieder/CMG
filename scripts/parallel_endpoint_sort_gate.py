import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
GRAPH = Path('src/graph.rs')
BENCH = Path('src/bin/cmg-parallel-bench.rs')
SCRIPT = Path('scripts/parallel_endpoint_sort_gate.py')
WORKFLOW = Path('.github/workflows/parallel-endpoint-sort-gate.yml')
RECORD = Path('.ci/performance/parallel-endpoint-sort-latest.json')
STATUS = Path('PERFORMANCE_STATUS.md')
PLAN = Path('PERFORMANCE_PLAN.md')

baseline_graph = GRAPH.read_text()
baseline_bench = BENCH.read_text()
source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def run(command, *, env=None, timeout=9000, check=True):
    command = [str(value) for value in command]
    print('+', ' '.join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end='')
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
        )
    return completed


def replace_once(text, old, new, label):
    matches = text.count(old)
    if matches != 1:
        raise RuntimeError(f'{label} changed unexpectedly; matches={matches}')
    return text.replace(old, new, 1)


def add_dense_benchmark_case():
    text = BENCH.read_text()
    text = replace_once(
        text,
        '''        "worker-firm" => Ok(worker_firm_edges(vertices)),
        _ => Err(invalid_input(format!("unknown graph case: {case}"))),
''',
        '''        "worker-firm" => Ok(worker_firm_edges(vertices)),
        "dense-worker-firm" => Ok(dense_worker_firm_edges(vertices)),
        _ => Err(invalid_input(format!("unknown graph case: {case}"))),
''',
        'parallel benchmark case router',
    )
    insertion = '''
fn dense_worker_firm_edges(vertices: usize) -> Vec<(usize, usize, f64)> {
    let firm_count = (vertices / 5).clamp(1, vertices - 1);
    let worker_count = vertices - firm_count;
    let degree = 16usize;
    let mut edges = Vec::with_capacity(worker_count.saturating_mul(degree));
    for worker in 0..worker_count {
        for link in 0..degree {
            let firm = worker
                .wrapping_mul(48_271usize.wrapping_add(2 * link))
                .wrapping_add(17 * link + 3)
                % firm_count;
            edges.push((
                worker,
                worker_count + firm,
                deterministic_weight(worker.wrapping_mul(97).wrapping_add(31 * link)),
            ));
        }
    }
    edges
}

'''
    marker = 'fn deterministic_weight(seed: usize) -> f64 {\n'
    if marker not in text:
        raise RuntimeError('benchmark insertion marker missing')
    if 'fn dense_worker_firm_edges' not in text:
        text = text.replace(marker, insertion + marker, 1)
    BENCH.write_text(text)


def apply_candidate():
    text = GRAPH.read_text()
    old_parallel = '''    pub(crate) fn from_compact_edges_with_executor(
        vertex_count: usize,
        mut raw: Vec<Edge>,
        executor: &ParallelExecutor,
    ) -> Result<Self, CmgError> {
        if raw.len() >= PARALLEL_SETUP_MIN_ITEMS && executor.should_parallel(raw.len()) {
            executor.install(|| raw.par_sort_unstable_by(compare_raw_edges));
        } else {
            sort_compact_edges_two_stage(&mut raw);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
'''
    new_parallel = '''    pub(crate) fn from_compact_edges_with_executor(
        vertex_count: usize,
        mut raw: Vec<Edge>,
        executor: &ParallelExecutor,
    ) -> Result<Self, CmgError> {
        if raw.len() >= PARALLEL_SETUP_MIN_ITEMS && executor.should_parallel(raw.len()) {
            executor.install(|| raw.par_sort_unstable_by_key(endpoint_key));
            sort_weights_within_endpoint_groups(&mut raw);
        } else {
            sort_compact_edges_two_stage(&mut raw);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
'''
    text = replace_once(text, old_parallel, new_parallel, 'parallel compact constructor')

    old_sort = '''fn sort_compact_edges_two_stage(raw: &mut [Edge]) {
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
    new_sort = '''fn sort_compact_edges_two_stage(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
    sort_weights_within_endpoint_groups(raw);
}

fn sort_weights_within_endpoint_groups(raw: &mut [Edge]) {
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
    text = replace_once(text, old_sort, new_sort, 'endpoint group sorter')

    test = '''

#[cfg(all(test, feature = "parallel"))]
mod parallel_compact_endpoint_sort_tests {
    use super::{Edge, Laplacian};
    use crate::{ParallelExecutor, ParallelOptions};

    #[test]
    fn parallel_endpoint_first_sort_matches_serial_bits() {
        let mut raw = Vec::with_capacity(400_000);
        for index in 0..400_000usize {
            let left = index % 20_000;
            let right = 20_000 + ((index.wrapping_mul(48_271) + 17) % 20_000);
            let weight = 0.25 + ((index.wrapping_mul(31) + 7) % 29) as f64 / 16.0;
            raw.push(Edge::from_internal_parts(left, right, weight).unwrap());
        }
        let executor = ParallelExecutor::new(ParallelOptions {
            threads: 4,
            min_parallel_len: 1,
            ..ParallelOptions::default()
        })
        .unwrap();
        let serial = Laplacian::from_compact_edges(40_000, raw.clone()).unwrap();
        let parallel =
            Laplacian::from_compact_edges_with_executor(40_000, raw, &executor).unwrap();
        assert_eq!(parallel, serial);
    }
}
'''
    if 'mod parallel_compact_endpoint_sort_tests' not in text:
        text += test
    GRAPH.write_text(text)


def build(target):
    env = os.environ.copy()
    env['CARGO_TARGET_DIR'] = str(target)
    run([
        'cargo', 'build', '--release', '--features', 'parallel',
        '--bin', 'cmg-parallel-bench',
    ], env=env)
    return target / 'release' / 'cmg-parallel-bench'


def sample(binary, case_name, vertices, tag):
    time_path = Path(f'/tmp/cmg-parallel-endpoint-sort-{tag}.time')
    completed = run([
        '/usr/bin/time', '-v', '-o', time_path,
        binary,
        '--case', case_name,
        '--vertices', vertices,
        '--rhs', 1,
        '--repetitions', 3,
        '--threads', 4,
    ])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'{tag}: unexpected benchmark output: {payloads}')
    match = re.search(
        r'Maximum resident set size \(kbytes\):\s*(\d+)',
        time_path.read_text(),
    )
    if match is None:
        raise RuntimeError(f'{tag}: peak RSS missing')
    payload = payloads[0]
    payload['peak_rss_kib'] = int(match.group(1))
    return payload


def compare_case(baseline, candidate, name, case_name, vertices):
    observations = {'baseline': [], 'candidate': []}
    order = (
        ('baseline', baseline),
        ('candidate', candidate),
        ('candidate', candidate),
        ('baseline', baseline),
    )
    for index, (label, binary) in enumerate(order):
        observations[label].append(sample(
            binary, case_name, vertices, f'{name}-{label}-{index}'
        ))

    stable = (
        'case', 'logical_cpus', 'executor_threads', 'vertices',
        'canonical_edges', 'rhs_count', 'workspace_bytes', 'batch_concurrency',
        'iterations', 'backward_errors',
    )
    reference = observations['baseline'][0]
    for observation in observations['baseline'][1:] + observations['candidate']:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f'{name}: changed stable field {key}')

    def median(label, key):
        return statistics.median(item[key] for item in observations[label])

    baseline_parallel = median('baseline', 'parallel_setup_median_ns')
    candidate_parallel = median('candidate', 'parallel_setup_median_ns')
    baseline_serial = median('baseline', 'serial_setup_median_ns')
    candidate_serial = median('candidate', 'serial_setup_median_ns')
    baseline_batch = median('baseline', 'parallel_batch_median_ns')
    candidate_batch = median('candidate', 'parallel_batch_median_ns')
    baseline_rss = max(item['peak_rss_kib'] for item in observations['baseline'])
    candidate_rss = max(item['peak_rss_kib'] for item in observations['candidate'])

    return {
        'metadata': {key: reference[key] for key in stable},
        'baseline_parallel_setup_ns': baseline_parallel,
        'candidate_parallel_setup_ns': candidate_parallel,
        'candidate_over_baseline_parallel_setup': candidate_parallel / baseline_parallel,
        'baseline_serial_setup_ns': baseline_serial,
        'candidate_serial_setup_ns': candidate_serial,
        'candidate_over_baseline_serial_setup': candidate_serial / baseline_serial,
        'baseline_parallel_batch_ns': baseline_batch,
        'candidate_parallel_batch_ns': candidate_batch,
        'candidate_over_baseline_parallel_batch': candidate_batch / baseline_batch,
        'baseline_peak_rss_kib': baseline_rss,
        'candidate_peak_rss_kib': candidate_rss,
        'candidate_over_baseline_peak_rss': candidate_rss / baseline_rss,
    }


result = {
    'schema_version': 1,
    'experiment': 'parallel-compact-endpoint-first-sort',
    'source_sha': source_sha,
    'validation': 'not_run',
    'accepted': False,
    'decision_reason': '',
    'cases': {},
}

try:
    add_dense_benchmark_case()
    run(['cargo', 'fmt', '--all'])
    baseline = build(Path('/tmp/cmg-parallel-endpoint-sort-baseline'))
    apply_candidate()
    run(['cargo', 'fmt', '--all'])
    run(['cargo', 'fmt', '--all', '--', '--check'])
    run(['cargo', 'clippy', '--all-targets', '--all-features', '--', '-D', 'warnings'])
    doc_env = os.environ.copy()
    doc_env['RUSTDOCFLAGS'] = '-D warnings'
    run(['cargo', 'doc', '--no-deps', '--document-private-items', '--all-features'], env=doc_env)
    run(['cargo', 'test', '--all-targets', '--all-features'])
    run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
    run(['cargo', 'build', '--release', '--all-features'])
    run([
        'cargo', 'clippy', '--manifest-path', 'benchmarks/Cargo.toml',
        '--all-targets', '--', '-D', 'warnings',
    ])
    candidate = build(Path('/tmp/cmg-parallel-endpoint-sort-candidate'))
    result['validation'] = 'success'

    specs = (
        ('path-500k', 'path', 500_000),
        ('grid-600k', 'grid', 600_000),
        ('worker-firm-1m', 'worker-firm', 1_000_000),
        ('dense-worker-firm-200k', 'dense-worker-firm', 200_000),
    )
    for name, case_name, vertices in specs:
        result['cases'][name] = compare_case(
            baseline, candidate, name, case_name, vertices
        )

    parallel = [
        item['candidate_over_baseline_parallel_setup']
        for item in result['cases'].values()
    ]
    serial = [
        item['candidate_over_baseline_serial_setup']
        for item in result['cases'].values()
    ]
    batches = [
        item['candidate_over_baseline_parallel_batch']
        for item in result['cases'].values()
    ]
    rss = [
        item['candidate_over_baseline_peak_rss']
        for item in result['cases'].values()
    ]
    active = [
        result['cases']['grid-600k']['candidate_over_baseline_parallel_setup'],
        result['cases']['worker-firm-1m']['candidate_over_baseline_parallel_setup'],
        result['cases']['dense-worker-firm-200k']['candidate_over_baseline_parallel_setup'],
    ]
    geometric = lambda values: math.exp(
        sum(math.log(value) for value in values) / len(values)
    )
    result['parallel_setup_geometric_ratio'] = geometric(parallel)
    result['active_parallel_setup_geometric_ratio'] = geometric(active)
    result['parallel_setup_worst_ratio'] = max(parallel)
    result['serial_setup_geometric_ratio'] = geometric(serial)
    result['serial_setup_worst_ratio'] = max(serial)
    result['parallel_batch_geometric_ratio'] = geometric(batches)
    result['parallel_batch_worst_ratio'] = max(batches)
    result['worst_peak_rss_ratio'] = max(rss)
    result['acceptance_limits'] = {
        'active_parallel_setup_geometric_ratio_max': 0.985,
        'parallel_setup_worst_ratio_max': 1.03,
        'serial_setup_geometric_ratio_max': 1.025,
        'serial_setup_worst_ratio_max': 1.05,
        'parallel_batch_geometric_ratio_max': 1.03,
        'parallel_batch_worst_ratio_max': 1.08,
        'worst_peak_rss_ratio_max': 1.03,
    }
    result['accepted'] = (
        result['active_parallel_setup_geometric_ratio'] <= 0.985
        and result['parallel_setup_worst_ratio'] <= 1.03
        and result['serial_setup_geometric_ratio'] <= 1.025
        and result['serial_setup_worst_ratio'] <= 1.05
        and result['parallel_batch_geometric_ratio'] <= 1.03
        and result['parallel_batch_worst_ratio'] <= 1.08
        and result['worst_peak_rss_ratio'] <= 1.03
    )
    result['decision_reason'] = (
        'full qualification passed; parallel endpoint-first sorting reduced compact hierarchy comparison work without extra retained storage'
        if result['accepted']
        else 'qualification passed but the parallel setup signal was too small or a setup, solve, or RSS regression gate was exceeded'
    )
except Exception as error:
    result['validation'] = 'failure'
    result['decision_reason'] = f'experiment failed: {error}'
    result['error'] = repr(error)
    print(result['decision_reason'], flush=True)

BENCH.write_text(baseline_bench)
if not result['accepted']:
    GRAPH.write_text(baseline_graph)
run(['cargo', 'fmt', '--all'], check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

status = STATUS.read_text().rstrip()
heading = '## Parallel endpoint-first compact sort gate\n'
decision = 'retained' if result['accepted'] else 'not retained'
block = f'''{heading}
- Decision: `{decision}`.
- Validation: `{result['validation']}`.
- Active-case parallel setup geometric ratio: `{result.get('active_parallel_setup_geometric_ratio', 'n/a')}`.
- Worst parallel setup / peak-RSS ratios: `{result.get('parallel_setup_worst_ratio', 'n/a')}` / `{result.get('worst_peak_rss_ratio', 'n/a')}`.
- Serial endpoint-first ordering and public graph construction remain unchanged.
- Evidence: `.ci/performance/parallel-endpoint-sort-latest.json`.
'''
if heading in status:
    start = status.index(heading)
    end = status.find('\n## ', start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + block + status[end:]
else:
    status += '\n\n' + block
STATUS.write_text(status.rstrip() + '\n')

plan = PLAN.read_text()
marker = '## Current next action\n'
checkpoint_heading = '### Parallel endpoint-first compact sort checkpoint — 2026-08-23\n'
checkpoint = f'''{checkpoint_heading}
- Candidate was **{decision}**.
- Validation: `{result['validation']}`.
- Active parallel setup / worst RSS ratios: `{result.get('active_parallel_setup_geometric_ratio', 'n/a')}` / `{result.get('worst_peak_rss_ratio', 'n/a')}`.
- Evidence: `.ci/performance/parallel-endpoint-sort-latest.json`.

'''
if checkpoint_heading not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
    prefix, _ = plan.split(marker, 1)
    plan = prefix + marker + '''
1. Re-profile parallel hierarchy sorting after the endpoint-first decision.
2. Re-run the full certified PCG routing matrix after any retained setup change.
3. Continue contraction work only with candidates that preserve exact hierarchy bits and peak-memory limits.
4. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
PLAN.write_text(plan)

SCRIPT.unlink(missing_ok=True)
WORKFLOW.unlink(missing_ok=True)
