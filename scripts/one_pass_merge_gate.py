import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
GRAPH = Path('src/graph.rs')
SCRIPT = Path('scripts/one_pass_merge_gate.py')
WORKFLOW = Path('.github/workflows/one-pass-merge-gate.yml')
RECORD = Path('.ci/performance/one-pass-merge-latest.json')

baseline_source = GRAPH.read_text()
source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def run(args, *, env=None, timeout=7200, check=True):
    args = [str(arg) for arg in args]
    print('+', ' '.join(args), flush=True)
    completed = subprocess.run(
        args, cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end='')
    if check and completed.returncode:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(args)}")
    return completed


def build(target):
    target = Path(target)
    env = os.environ.copy()
    env['CARGO_TARGET_DIR'] = str(target)
    run([
        'cargo', 'build', '--release',
        '--manifest-path', 'benchmarks/Cargo.toml',
        '--bin', 'hierarchy-alloc',
    ], env=env)
    return target / 'release' / 'hierarchy-alloc'


def apply_candidate():
    text = GRAPH.read_text()
    old_merge = '''        let mut read_index = 0;
        let mut write_index = 0;
        while read_index < raw.len() {
            let u = raw[read_index].u;
            let v = raw[read_index].v;
            let group_start = read_index;
            while read_index < raw.len() && raw[read_index].u == u && raw[read_index].v == v {
                read_index += 1;
            }
            let weight =
                compensated_sum(raw[group_start..read_index].iter().map(|edge| edge.weight));
            if !weight.is_finite() || weight <= 0.0 {
                return Err(CmgError::InvalidEdgeWeight {
                    u: u as usize,
                    v: v as usize,
                    weight,
                });
            }
            raw[write_index] = Edge { u, v, weight };
            write_index += 1;
        }
'''
    new_merge = '''        let mut read_index = 0;
        let mut write_index = 0;
        while read_index < raw.len() {
            let u = raw[read_index].u;
            let v = raw[read_index].v;
            let mut sum = 0.0;
            let mut correction = 0.0;
            while read_index < raw.len() && raw[read_index].u == u && raw[read_index].v == v {
                compensated_add(&mut sum, &mut correction, raw[read_index].weight);
                read_index += 1;
            }
            let weight = sum + correction;
            if !weight.is_finite() || weight <= 0.0 {
                return Err(CmgError::InvalidEdgeWeight {
                    u: u as usize,
                    v: v as usize,
                    weight,
                });
            }
            raw[write_index] = Edge { u, v, weight };
            write_index += 1;
        }
'''
    old_sum = '''pub(crate) fn compensated_sum<I>(values: I) -> f64
where
    I: IntoIterator<Item = f64>,
{
    let mut sum = 0.0;
    let mut correction = 0.0;
    for value in values {
        let next = sum + value;
        correction += if sum.abs() >= value.abs() {
            (sum - next) + value
        } else {
            (value - next) + sum
        };
        sum = next;
    }
    sum + correction
}
'''
    new_sum = '''#[inline]
fn compensated_add(sum: &mut f64, correction: &mut f64, value: f64) {
    let next = *sum + value;
    *correction += if sum.abs() >= value.abs() {
        (*sum - next) + value
    } else {
        (value - next) + *sum
    };
    *sum = next;
}

pub(crate) fn compensated_sum<I>(values: I) -> f64
where
    I: IntoIterator<Item = f64>,
{
    let mut sum = 0.0;
    let mut correction = 0.0;
    for value in values {
        compensated_add(&mut sum, &mut correction, value);
    }
    sum + correction
}
'''
    if text.count(old_merge) != 1:
        raise RuntimeError(f'merge block expected once, found {text.count(old_merge)}')
    if text.count(old_sum) != 1:
        raise RuntimeError(f'compensated sum expected once, found {text.count(old_sum)}')
    text = text.replace(old_merge, new_merge, 1).replace(old_sum, new_sum, 1)
    test = '''

#[cfg(test)]
mod one_pass_merge_arithmetic_tests {
    use super::{compensated_add, compensated_sum};

    #[test]
    fn incremental_compensation_matches_iterator_helper_bitwise() {
        let values = [1.0e100, 1.0, 2.0, 3.0, 1.0e-100, 7.0, 9.0];
        let expected = compensated_sum(values);
        let mut sum = 0.0;
        let mut correction = 0.0;
        for value in values {
            compensated_add(&mut sum, &mut correction, value);
        }
        assert_eq!((sum + correction).to_bits(), expected.to_bits());
    }
}
'''
    text += test
    GRAPH.write_text(text)


def sample(binary, case, scale, tag):
    time_file = Path(f'/tmp/cmg-one-pass-merge-{tag}.time')
    completed = run(['/usr/bin/time', '-v', '-o', time_file, binary, case, scale, 3])
    payloads = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith('{')]
    if len(payloads) != 1:
        raise RuntimeError(f'unexpected benchmark output: {payloads}')
    match = re.search(
        r'Maximum resident set size \(kbytes\):\s*(\d+)', time_file.read_text()
    )
    if not match:
        raise RuntimeError('peak RSS missing')
    payloads[0]['peak_rss_kib'] = int(match.group(1))
    return payloads[0]


def compare_case(baseline, candidate, name, case, scale):
    baseline_rows = []
    candidate_rows = []
    for index, (label, binary) in enumerate((
        ('baseline', baseline), ('candidate', candidate),
        ('candidate', candidate), ('baseline', baseline),
    )):
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
    med = lambda rows, key: statistics.median(row[key] for row in rows)
    bt, ct = med(baseline_rows, 'median_ns'), med(candidate_rows, 'median_ns')
    bp = med(baseline_rows, 'median_additional_peak_bytes')
    cp = med(candidate_rows, 'median_additional_peak_bytes')
    br, cr = med(baseline_rows, 'median_retained_bytes'), med(candidate_rows, 'median_retained_bytes')
    b_rss = max(row['peak_rss_kib'] for row in baseline_rows)
    c_rss = max(row['peak_rss_kib'] for row in candidate_rows)
    return {
        'case': case, 'scale': scale,
        'metadata': {key: reference[key] for key in stable},
        'baseline_median_ns': bt, 'candidate_median_ns': ct,
        'candidate_over_baseline_time': ct / bt,
        'candidate_over_baseline_additional_peak': cp / bp,
        'candidate_over_baseline_retained': cr / br,
        'candidate_over_baseline_peak_rss': c_rss / b_rss,
        'baseline_additional_peak_bytes': bp, 'candidate_additional_peak_bytes': cp,
        'baseline_retained_bytes': br, 'candidate_retained_bytes': cr,
        'baseline_peak_rss_kib': b_rss, 'candidate_peak_rss_kib': c_rss,
    }


result = {
    'schema_version': 1,
    'experiment': 'one-pass-compensated-duplicate-merge',
    'source_sha': source_sha,
    'validation': 'not_run',
    'accepted': False,
    'cases': {},
    'decision_reason': '',
}

try:
    baseline = build('/tmp/cmg-one-pass-merge-baseline')
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
    candidate = build('/tmp/cmg-one-pass-merge-candidate')
    result['validation'] = 'success'
    for name, case, scale in (
        ('path-1m', 'path', 1_000_000),
        ('worker-firm-1.5m', 'worker-firm', 500_000),
        ('dense-worker-firm-1.6m', 'dense-worker-firm', 100_000),
    ):
        result['cases'][name] = compare_case(baseline, candidate, name, case, scale)
    times = [row['candidate_over_baseline_time'] for row in result['cases'].values()]
    active = [
        result['cases']['worker-firm-1.5m']['candidate_over_baseline_time'],
        result['cases']['dense-worker-firm-1.6m']['candidate_over_baseline_time'],
    ]
    peaks = [row['candidate_over_baseline_additional_peak'] for row in result['cases'].values()]
    retained = [row['candidate_over_baseline_retained'] for row in result['cases'].values()]
    rss = [row['candidate_over_baseline_peak_rss'] for row in result['cases'].values()]
    result['geometric_time_ratio'] = math.exp(sum(map(math.log, times)) / len(times))
    result['active_worker_firm_geometric_time_ratio'] = math.exp(sum(map(math.log, active)) / len(active))
    result['worst_time_ratio'] = max(times)
    result['worst_additional_peak_ratio'] = max(peaks)
    result['worst_retained_ratio'] = max(retained)
    result['worst_peak_rss_ratio'] = max(rss)
    result['acceptance_limits'] = {
        'active_worker_firm_geometric_time_ratio_max': 0.99,
        'worst_time_ratio_max': 1.035,
        'worst_additional_peak_ratio_max': 1.005,
        'worst_retained_ratio_max': 1.002,
        'worst_peak_rss_ratio_max': 1.03,
    }
    result['accepted'] = (
        result['active_worker_firm_geometric_time_ratio'] <= 0.99
        and result['worst_time_ratio'] <= 1.035
        and result['worst_additional_peak_ratio'] <= 1.005
        and result['worst_retained_ratio'] <= 1.002
        and result['worst_peak_rss_ratio'] <= 1.03
    )
    result['decision_reason'] = (
        'full qualification passed; one-pass compensated merging improved worker-firm hierarchy time'
        if result['accepted'] else
        'qualification passed but timing or regression gates were not met'
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

decision = 'retained' if result['accepted'] else 'not retained'
active = result.get('active_worker_firm_geometric_time_ratio', 'n/a')
worst = result.get('worst_time_ratio', 'n/a')
status = Path('PERFORMANCE_STATUS.md')
text = status.read_text().rstrip()
text += f'''\n\n## One-pass compensated duplicate merge gate

- Decision: `{decision}`.
- Validation: `{result['validation']}`.
- Worker-firm geometric hierarchy-time ratio: `{active}`.
- Worst hierarchy-time ratio: `{worst}`.
- Evidence: `.ci/performance/one-pass-merge-latest.json`.
'''
status.write_text(text.rstrip() + '\n')

plan = Path('PERFORMANCE_PLAN.md')
text = plan.read_text()
marker = '## Current next action\n'
checkpoint = f'''### One-pass compensated merge checkpoint — 2026-08-23

- Candidate was **{decision}**.
- Validation: `{result['validation']}`.
- Worker-firm geometric hierarchy-time ratio: `{active}`.
- Worst hierarchy-time ratio: `{worst}`.
- Evidence: `.ci/performance/one-pass-merge-latest.json`.

'''
if checkpoint.splitlines()[0] not in text:
    text = text.replace(marker, checkpoint + marker, 1)
if marker in text:
    prefix, _ = text.split(marker, 1)
    text = prefix + marker + '''
1. Re-profile contraction subphases after retained sorting and merge decisions.
2. Evaluate infallible prevalidated coarse-edge construction if mapping remains material.
3. Re-run full certified PCG routing after any retained hierarchy change.
4. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
plan.write_text(text)

WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
