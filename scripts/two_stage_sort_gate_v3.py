import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
GRAPH = Path('src/graph.rs')
SCRIPT = Path('scripts/two_stage_sort_gate_v3.py')
WORKFLOW = Path('.github/workflows/two-stage-sort-gate-v3.yml')
RECORD = Path('.ci/performance/two-stage-sort-v3-latest.json')

baseline_source = GRAPH.read_text()
source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def run(args, *, env=None, timeout=7200, check=True):
    args = [str(arg) for arg in args]
    print('+', ' '.join(args), flush=True)
    completed = subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end='')
    if check and completed.returncode:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(args)}")
    return completed


def build(target):
    env = os.environ.copy()
    env['CARGO_TARGET_DIR'] = str(target)
    run([
        'cargo', 'build', '--release',
        '--manifest-path', 'benchmarks/Cargo.toml',
        '--bin', 'hierarchy-alloc',
    ], env=env)
    return target / 'release' / 'hierarchy-alloc'


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label} expected once, found {count}')
    return text.replace(old, new, 1)


def apply_candidate():
    text = GRAPH.read_text()
    serial_old = '''    pub(crate) fn from_compact_edges(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        raw.sort_unstable_by(compare_raw_edges);
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
'''
    serial_new = '''    pub(crate) fn from_compact_edges(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        sort_compact_edges_two_stage(&mut raw);
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
'''
    parallel_old = '''    #[cfg(feature = "parallel")]
    pub(crate) fn from_compact_edges_with_executor(
        vertex_count: usize,
        mut raw: Vec<Edge>,
        executor: &ParallelExecutor,
    ) -> Result<Self, CmgError> {
        if raw.len() >= PARALLEL_SETUP_MIN_ITEMS && executor.should_parallel(raw.len()) {
            executor.install(|| raw.par_sort_unstable_by(compare_raw_edges));
        } else {
            raw.sort_unstable_by(compare_raw_edges);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
'''
    parallel_new = '''    #[cfg(feature = "parallel")]
    pub(crate) fn from_compact_edges_with_executor(
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
    comparator_old = '''fn compare_raw_edges(left: &Edge, right: &Edge) -> core::cmp::Ordering {
    let left_endpoints = (u64::from(left.u) << 32) | u64::from(left.v);
    let right_endpoints = (u64::from(right.u) << 32) | u64::from(right.v);
    left_endpoints
        .cmp(&right_endpoints)
        .then_with(|| left.weight.total_cmp(&right.weight))
}
'''
    comparator_new = '''fn endpoint_key(edge: &Edge) -> u64 {
    (u64::from(edge.u) << 32) | u64::from(edge.v)
}

fn compare_raw_edges(left: &Edge, right: &Edge) -> core::cmp::Ordering {
    endpoint_key(left)
        .cmp(&endpoint_key(right))
        .then_with(|| left.weight.total_cmp(&right.weight))
}

fn sort_compact_edges_two_stage(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
    let mut start = 0;
    while start < raw.len() {
        let key = endpoint_key(&raw[start]);
        let mut end = start + 1;
        while end < raw.len() && endpoint_key(&raw[end]) == key {
            end += 1;
        }
        if end - start > 1 {
            raw[start..end]
                .sort_unstable_by(|left, right| left.weight.total_cmp(&right.weight));
        }
        start = end;
    }
}
'''
    text = replace_once(text, serial_old, serial_new, 'compact serial constructor')
    text = replace_once(text, parallel_old, parallel_new, 'compact executor constructor')
    text = replace_once(text, comparator_old, comparator_new, 'edge comparator')
    test = '''

#[cfg(test)]
mod two_stage_sort_equivalence_tests {
    use super::{Edge, compare_raw_edges, sort_compact_edges_two_stage};

    #[test]
    fn endpoint_then_weight_order_matches_total_comparator() {
        let mut candidate = vec![
            Edge { u: 4, v: 9, weight: 2.0 },
            Edge { u: 1, v: 7, weight: 3.0 },
            Edge { u: 4, v: 9, weight: 1.0 },
            Edge { u: 1, v: 7, weight: 1.0 },
            Edge { u: 2, v: 8, weight: 4.0 },
            Edge { u: 1, v: 7, weight: 2.0 },
            Edge { u: 2, v: 8, weight: 0.5 },
        ];
        let mut reference = candidate.clone();
        reference.sort_unstable_by(compare_raw_edges);
        sort_compact_edges_two_stage(&mut candidate);
        assert_eq!(candidate, reference);
    }
}
'''
    text += test
    GRAPH.write_text(text)


def sample(binary, case, scale, tag):
    time_file = Path(f'/tmp/cmg-two-stage-v3-{tag}.time')
    completed = run(['/usr/bin/time', '-v', '-o', time_file, binary, case, scale, 3])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'unexpected benchmark output: {payloads}')
    match = re.search(
        r'Maximum resident set size \(kbytes\):\s*(\d+)',
        time_file.read_text(),
    )
    if not match:
        raise RuntimeError('peak RSS missing')
    payload = payloads[0]
    payload['peak_rss_kib'] = int(match.group(1))
    return payload


def compare_case(baseline, candidate, name, case, scale):
    baseline_rows = []
    candidate_rows = []
    order = (
        ('baseline', baseline),
        ('candidate', candidate),
        ('candidate', candidate),
        ('baseline', baseline),
    )
    for index, (label, binary) in enumerate(order):
        row = sample(binary, case, scale, f'{name}-{label}-{index}')
        (baseline_rows if label == 'baseline' else candidate_rows).append(row)

    stable = (
        'case', 'scale', 'vertices', 'edges', 'levels',
        'hierarchy_matrix_nonzeros', 'max_post_drop_delta_bytes',
    )
    reference = baseline_rows[0]
    for row in baseline_rows[1:] + candidate_rows:
        for key in stable:
            if row[key] != reference[key]:
                raise RuntimeError(f'{name}: stable field changed: {key}')

    median = lambda rows, key: statistics.median(row[key] for row in rows)
    bt = median(baseline_rows, 'median_ns')
    ct = median(candidate_rows, 'median_ns')
    bp = median(baseline_rows, 'median_additional_peak_bytes')
    cp = median(candidate_rows, 'median_additional_peak_bytes')
    br = median(baseline_rows, 'median_retained_bytes')
    cr = median(candidate_rows, 'median_retained_bytes')
    b_rss = max(row['peak_rss_kib'] for row in baseline_rows)
    c_rss = max(row['peak_rss_kib'] for row in candidate_rows)
    return {
        'case': case,
        'scale': scale,
        'metadata': {key: reference[key] for key in stable},
        'baseline_median_ns': bt,
        'candidate_median_ns': ct,
        'candidate_over_baseline_time': ct / bt,
        'candidate_over_baseline_additional_peak': cp / bp,
        'candidate_over_baseline_retained': cr / br,
        'candidate_over_baseline_peak_rss': c_rss / b_rss,
        'baseline_additional_peak_bytes': bp,
        'candidate_additional_peak_bytes': cp,
        'baseline_retained_bytes': br,
        'candidate_retained_bytes': cr,
        'baseline_peak_rss_kib': b_rss,
        'candidate_peak_rss_kib': c_rss,
    }


result = {
    'schema_version': 3,
    'experiment': 'two-stage-endpoint-weight-sort',
    'source_sha': source_sha,
    'validation': 'not_run',
    'accepted': False,
    'cases': {},
    'decision_reason': '',
}

try:
    baseline = build('/tmp/cmg-two-stage-v3-baseline')
    apply_candidate()
    run(['cargo', 'fmt', '--all'])
    run(['cargo', 'fmt', '--all', '--', '--check'])
    run(['cargo', 'fmt', '--manifest-path', 'benchmarks/Cargo.toml', '--all', '--', '--check'])
    run(['cargo', 'clippy', '--all-targets', '--all-features', '--', '-D', 'warnings'])
    run(['cargo', 'clippy', '--manifest-path', 'benchmarks/Cargo.toml', '--all-targets', '--', '-D', 'warnings'])
    doc_env = os.environ.copy()
    doc_env['RUSTDOCFLAGS'] = '-D warnings'
    run(['cargo', 'doc', '--no-deps', '--document-private-items', '--all-features'], env=doc_env)
    run(['cargo', 'test', '--all-targets', '--all-features'])
    run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
    run(['cargo', 'build', '--release', '--all-features'])
    candidate = build('/tmp/cmg-two-stage-v3-candidate')
    result['validation'] = 'success'

    specs = (
        ('path-1m', 'path', 1_000_000),
        ('worker-firm-1.5m', 'worker-firm', 500_000),
        ('dense-worker-firm-1.6m', 'dense-worker-firm', 100_000),
    )
    for name, case, scale in specs:
        result['cases'][name] = compare_case(baseline, candidate, name, case, scale)

    all_time = [row['candidate_over_baseline_time'] for row in result['cases'].values()]
    active_time = [
        result['cases']['worker-firm-1.5m']['candidate_over_baseline_time'],
        result['cases']['dense-worker-firm-1.6m']['candidate_over_baseline_time'],
    ]
    peaks = [row['candidate_over_baseline_additional_peak'] for row in result['cases'].values()]
    retained = [row['candidate_over_baseline_retained'] for row in result['cases'].values()]
    rss = [row['candidate_over_baseline_peak_rss'] for row in result['cases'].values()]
    result['geometric_time_ratio'] = math.exp(sum(map(math.log, all_time)) / len(all_time))
    result['active_worker_firm_geometric_time_ratio'] = math.exp(
        sum(map(math.log, active_time)) / len(active_time)
    )
    result['worst_time_ratio'] = max(all_time)
    result['worst_additional_peak_ratio'] = max(peaks)
    result['worst_retained_ratio'] = max(retained)
    result['worst_peak_rss_ratio'] = max(rss)
    result['acceptance_limits'] = {
        'active_worker_firm_geometric_time_ratio_max': 0.985,
        'worst_time_ratio_max': 1.04,
        'worst_additional_peak_ratio_max': 1.005,
        'worst_retained_ratio_max': 1.002,
        'worst_peak_rss_ratio_max': 1.03,
    }
    result['accepted'] = (
        result['active_worker_firm_geometric_time_ratio'] <= 0.985
        and result['worst_time_ratio'] <= 1.04
        and result['worst_additional_peak_ratio'] <= 1.005
        and result['worst_retained_ratio'] <= 1.002
        and result['worst_peak_rss_ratio'] <= 1.03
    )
    result['decision_reason'] = (
        'full qualification passed; endpoint-first ordering materially improved worker-firm hierarchy time'
        if result['accepted']
        else 'qualification passed but the worker-firm timing or regression gates were not met'
    )
except Exception as error:
    result['validation'] = 'failure'
    result['decision_reason'] = f'experiment failed: {error}'
    result['error'] = repr(error)
    print(result['decision_reason'], flush=True)

if not result['accepted']:
    GRAPH.write_text(baseline_source)
    run(['cargo', 'fmt', '--all'], check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

status = Path('PERFORMANCE_STATUS.md')
text = status.read_text().rstrip()
heading = '## Function-scoped two-stage contraction sort gate\n'
decision = 'retained' if result['accepted'] else 'not retained'
active = result.get('active_worker_firm_geometric_time_ratio', 'n/a')
worst = result.get('worst_time_ratio', 'n/a')
block = f'''{heading}
- Decision: `{decision}`.
- Validation: `{result['validation']}`.
- Worker-firm geometric hierarchy-time ratio: `{active}`.
- Worst hierarchy-time ratio: `{worst}`.
- Parallel sort behavior is unchanged.
- Evidence: `.ci/performance/two-stage-sort-v3-latest.json`.
'''
text += '\n\n' + block
status.write_text(text.rstrip() + '\n')

plan = Path('PERFORMANCE_PLAN.md')
text = plan.read_text()
marker = '## Current next action\n'
checkpoint = f'''### Function-scoped two-stage sort checkpoint — 2026-08-23

- Candidate was **{decision}**.
- Validation: `{result['validation']}`.
- Worker-firm geometric hierarchy-time ratio: `{active}`.
- Worst hierarchy-time ratio: `{worst}`.
- Evidence: `.ci/performance/two-stage-sort-v3-latest.json`.

'''
if checkpoint.splitlines()[0] not in text:
    text = text.replace(marker, checkpoint + marker, 1)
if marker in text:
    prefix, _ = text.split(marker, 1)
    text = prefix + marker + '''
1. Re-profile contraction subphases after the sorting decision.
2. Evaluate infallible prevalidated coarse-edge construction and other mapping-kernel costs if mapping remains material.
3. Re-run full certified PCG routing after any retained hierarchy change.
4. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
plan.write_text(text)

WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
