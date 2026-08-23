import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
GRAPH = Path('src/graph.rs')
SCRIPT = Path('scripts/radix_compact_sort_gate.py')
WORKFLOW = Path('.github/workflows/radix-compact-sort-gate.yml')
RECORD = Path('.ci/performance/radix-compact-sort-latest.json')

baseline_graph = GRAPH.read_text()
source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def run(command, *, env=None, timeout=9000, check=True):
    command = [str(item) for item in command]
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


def build(target):
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
    old_serial = '''    pub(crate) fn from_compact_edges(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        sort_compact_edges_two_stage(&mut raw);
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
'''
    new_serial = '''    pub(crate) fn from_compact_edges(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        sort_compact_edges_routed(&mut raw);
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
'''
    old_parallel = '''        if raw.len() >= PARALLEL_SETUP_MIN_ITEMS && executor.should_parallel(raw.len()) {
            executor.install(|| raw.par_sort_unstable_by(compare_raw_edges));
        } else {
            sort_compact_edges_two_stage(&mut raw);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
'''
    new_parallel = '''        if raw.len() >= PARALLEL_SETUP_MIN_ITEMS && executor.should_parallel(raw.len()) {
            executor.install(|| raw.par_sort_unstable_by(compare_raw_edges));
        } else {
            sort_compact_edges_routed(&mut raw);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
'''
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
    new_sort = '''const COMPACT_RADIX_MIN_EDGES: usize = 262_144;
const COMPACT_RADIX_BITS: usize = 16;
const COMPACT_RADIX_BUCKETS: usize = 1usize << COMPACT_RADIX_BITS;
const COMPACT_RADIX_MASK: u64 = (COMPACT_RADIX_BUCKETS as u64) - 1;

fn sort_compact_edges_routed(raw: &mut [Edge]) {
    if raw.len() >= COMPACT_RADIX_MIN_EDGES {
        sort_compact_edges_radix(raw);
    } else {
        sort_compact_edges_two_stage(raw);
    }
}

fn sort_compact_edges_two_stage(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
    sort_weights_within_endpoint_groups(raw);
}

fn sort_compact_edges_radix(raw: &mut [Edge]) {
    if raw.len() < 2 {
        return;
    }
    let mut scratch = vec![raw[0]; raw.len()];
    let mut counts = vec![0usize; COMPACT_RADIX_BUCKETS];
    radix_endpoint_pass(raw, &mut scratch, 0, &mut counts);
    radix_endpoint_pass(&scratch, raw, 16, &mut counts);
    radix_endpoint_pass(raw, &mut scratch, 32, &mut counts);
    radix_endpoint_pass(&scratch, raw, 48, &mut counts);
    sort_weights_within_endpoint_groups(raw);
}

fn radix_endpoint_pass(
    source: &[Edge],
    destination: &mut [Edge],
    shift: u32,
    counts: &mut [usize],
) {
    debug_assert_eq!(source.len(), destination.len());
    debug_assert_eq!(counts.len(), COMPACT_RADIX_BUCKETS);
    counts.fill(0);
    for edge in source {
        let bucket = ((endpoint_key(edge) >> shift) & COMPACT_RADIX_MASK) as usize;
        counts[bucket] += 1;
    }
    let mut next = 0usize;
    for count in counts.iter_mut() {
        let frequency = *count;
        *count = next;
        next += frequency;
    }
    for &edge in source {
        let bucket = ((endpoint_key(&edge) >> shift) & COMPACT_RADIX_MASK) as usize;
        let index = counts[bucket];
        destination[index] = edge;
        counts[bucket] = index + 1;
    }
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
    for old, new, label in (
        (old_serial, new_serial, 'serial compact constructor'),
        (old_parallel, new_parallel, 'parallel fallback'),
        (old_sort, new_sort, 'compact sort function'),
    ):
        if text.count(old) != 1:
            raise RuntimeError(f'{label} changed unexpectedly')
        text = text.replace(old, new, 1)

    test = '''

#[cfg(test)]
mod compact_radix_sort_tests {
    use super::{Edge, sort_compact_edges_radix, sort_compact_edges_two_stage};

    #[test]
    fn radix_order_matches_two_stage_order() {
        let mut seed = 0x9e37_79b9_7f4a_7c15_u64;
        let mut candidate = Vec::with_capacity(50_000);
        for index in 0..50_000_u64 {
            seed = seed
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            let left = (seed & 4_095) as u32;
            let step = 1 + ((seed >> 32) & 63) as u32;
            let right = (left + step) & 4_095;
            let (u, v) = if left < right {
                (left, right)
            } else {
                (right, left)
            };
            let weight = ((index % 29) + 1) as f64 / 7.0;
            candidate.push(Edge { u, v, weight });
        }
        let mut reference = candidate.clone();
        sort_compact_edges_two_stage(&mut reference);
        sort_compact_edges_radix(&mut candidate);
        assert_eq!(candidate, reference);
    }
}
'''
    if 'mod compact_radix_sort_tests' not in text:
        text += test
    GRAPH.write_text(text)


def sample(binary, case, scale, tag):
    time_path = Path(f'/tmp/cmg-radix-sort-{tag}.time')
    completed = run([
        '/usr/bin/time', '-v', '-o', time_path,
        binary, case, scale, 3,
    ])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'unexpected benchmark output: {payloads}')
    match = re.search(
        r'Maximum resident set size \(kbytes\):\s*(\d+)',
        time_path.read_text(),
    )
    if match is None:
        raise RuntimeError('peak RSS missing')
    payload = payloads[0]
    payload['peak_rss_kib'] = int(match.group(1))
    return payload


def compare_case(baseline, candidate, name, case, scale):
    baseline_samples = []
    candidate_samples = []
    for index, (label, binary) in enumerate((
        ('baseline', baseline),
        ('candidate', candidate),
        ('candidate', candidate),
        ('baseline', baseline),
    )):
        observation = sample(binary, case, scale, f'{name}-{label}-{index}')
        (baseline_samples if label == 'baseline' else candidate_samples).append(observation)

    stable = (
        'case', 'scale', 'vertices', 'edges', 'levels',
        'hierarchy_matrix_nonzeros', 'max_post_drop_delta_bytes',
    )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f'{name}: changed stable field {key}')

    def med(samples, key):
        return statistics.median(item[key] for item in samples)

    baseline_time = med(baseline_samples, 'median_ns')
    candidate_time = med(candidate_samples, 'median_ns')
    baseline_peak = med(baseline_samples, 'median_additional_peak_bytes')
    candidate_peak = med(candidate_samples, 'median_additional_peak_bytes')
    baseline_retained = med(baseline_samples, 'median_retained_bytes')
    candidate_retained = med(candidate_samples, 'median_retained_bytes')
    baseline_rss = max(item['peak_rss_kib'] for item in baseline_samples)
    candidate_rss = max(item['peak_rss_kib'] for item in candidate_samples)

    return {
        'case': case,
        'scale': scale,
        'metadata': {key: reference[key] for key in stable},
        'baseline_median_ns': baseline_time,
        'candidate_median_ns': candidate_time,
        'candidate_over_baseline_time': candidate_time / baseline_time,
        'baseline_additional_peak_bytes': baseline_peak,
        'candidate_additional_peak_bytes': candidate_peak,
        'candidate_over_baseline_additional_peak': candidate_peak / baseline_peak,
        'baseline_retained_bytes': baseline_retained,
        'candidate_retained_bytes': candidate_retained,
        'candidate_over_baseline_retained': candidate_retained / baseline_retained,
        'baseline_peak_rss_kib': baseline_rss,
        'candidate_peak_rss_kib': candidate_rss,
        'candidate_over_baseline_peak_rss': candidate_rss / baseline_rss,
    }


result = {
    'schema_version': 1,
    'experiment': 'routed-four-pass-radix-compact-edge-sort',
    'source_sha': source_sha,
    'validation': 'not_run',
    'accepted': False,
    'decision_reason': '',
    'cases': {},
}

try:
    baseline_binary = build(Path('/tmp/cmg-radix-sort-baseline'))
    apply_candidate()
    run(['cargo', 'fmt', '--all'])
    run(['cargo', 'fmt', '--all', '--', '--check'])
    run([
        'cargo', 'fmt', '--manifest-path', 'benchmarks/Cargo.toml',
        '--all', '--', '--check',
    ])
    run(['cargo', 'clippy', '--all-targets', '--all-features', '--', '-D', 'warnings'])
    run([
        'cargo', 'clippy', '--manifest-path', 'benchmarks/Cargo.toml',
        '--all-targets', '--', '-D', 'warnings',
    ])
    doc_env = os.environ.copy()
    doc_env['RUSTDOCFLAGS'] = '-D warnings'
    run(['cargo', 'doc', '--no-deps', '--document-private-items', '--all-features'], env=doc_env)
    run(['cargo', 'test', '--all-targets', '--all-features'])
    run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
    run(['cargo', 'build', '--release', '--all-features'])
    candidate_binary = build(Path('/tmp/cmg-radix-sort-candidate'))
    result['validation'] = 'success'

    specs = (
        ('path-1m', 'path', 1_000_000),
        ('worker-firm-1.5m', 'worker-firm', 500_000),
        ('dense-worker-firm-1.6m', 'dense-worker-firm', 100_000),
    )
    for name, case, scale in specs:
        result['cases'][name] = compare_case(
            baseline_binary, candidate_binary, name, case, scale
        )

    all_time = [item['candidate_over_baseline_time'] for item in result['cases'].values()]
    active_time = [
        result['cases']['worker-firm-1.5m']['candidate_over_baseline_time'],
        result['cases']['dense-worker-firm-1.6m']['candidate_over_baseline_time'],
    ]
    peak = [item['candidate_over_baseline_additional_peak'] for item in result['cases'].values()]
    retained = [item['candidate_over_baseline_retained'] for item in result['cases'].values()]
    rss = [item['candidate_over_baseline_peak_rss'] for item in result['cases'].values()]
    result['geometric_time_ratio'] = math.exp(
        sum(math.log(value) for value in all_time) / len(all_time)
    )
    result['active_worker_firm_geometric_time_ratio'] = math.exp(
        sum(math.log(value) for value in active_time) / len(active_time)
    )
    result['worst_time_ratio'] = max(all_time)
    result['worst_additional_peak_ratio'] = max(peak)
    result['worst_retained_ratio'] = max(retained)
    result['worst_peak_rss_ratio'] = max(rss)
    result['acceptance_limits'] = {
        'active_worker_firm_geometric_time_ratio_max': 0.94,
        'worst_time_ratio_max': 1.02,
        'worst_additional_peak_ratio_max': 1.18,
        'worst_retained_ratio_max': 1.002,
        'worst_peak_rss_ratio_max': 1.15,
    }
    result['accepted'] = (
        result['active_worker_firm_geometric_time_ratio'] <= 0.94
        and result['worst_time_ratio'] <= 1.02
        and result['worst_additional_peak_ratio'] <= 1.18
        and result['worst_retained_ratio'] <= 1.002
        and result['worst_peak_rss_ratio'] <= 1.15
    )
    result['decision_reason'] = (
        'full qualification passed; routed radix ordering materially improved worker-firm hierarchy time and justified its temporary buffer'
        if result['accepted']
        else 'qualification passed but the timing benefit did not justify the temporary radix buffer or a regression limit was exceeded'
    )
except Exception as error:
    result['validation'] = 'failure'
    result['decision_reason'] = f'experiment failed: {error}'
    result['error'] = repr(error)
    print(result['decision_reason'], flush=True)

if not result['accepted']:
    GRAPH.write_text(baseline_graph)
    run(['cargo', 'fmt', '--all'], check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

status_path = Path('PERFORMANCE_STATUS.md')
status = status_path.read_text().rstrip()
heading = '## Routed compact-edge radix sort gate\n'
status_name = 'retained' if result['accepted'] else 'not retained'
active_ratio = result.get('active_worker_firm_geometric_time_ratio')
worst_ratio = result.get('worst_time_ratio')
peak_ratio = result.get('worst_additional_peak_ratio')
block = f'''{heading}
- Decision: `{status_name}`.
- Validation: `{result['validation']}`.
- Worker-firm geometric hierarchy-time ratio: `{active_ratio if active_ratio is not None else 'n/a'}`.
- Worst hierarchy-time ratio: `{worst_ratio if worst_ratio is not None else 'n/a'}`.
- Worst exact additional-peak ratio: `{peak_ratio if peak_ratio is not None else 'n/a'}`.
- Parallel sorting is unchanged; the candidate affects only large serial/fallback compact coarse-edge ordering.
- Evidence: `.ci/performance/radix-compact-sort-latest.json`.
'''
if heading in status:
    start = status.index(heading)
    end = status.find('\n## ', start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + block + status[end:]
else:
    status += '\n\n' + block
status_path.write_text(status.rstrip() + '\n')

plan_path = Path('PERFORMANCE_PLAN.md')
plan = plan_path.read_text()
marker = '## Current next action\n'
checkpoint_heading = '### Routed compact-edge radix sort checkpoint — 2026-08-23\n'
checkpoint = f'''{checkpoint_heading}
- Candidate was **{status_name}**.
- Validation: `{result['validation']}`.
- Worker-firm geometric hierarchy-time ratio: `{active_ratio if active_ratio is not None else 'n/a'}`.
- Worst hierarchy-time / exact additional-peak ratios: `{worst_ratio if worst_ratio is not None else 'n/a'}` / `{peak_ratio if peak_ratio is not None else 'n/a'}`.
- Evidence: `.ci/performance/radix-compact-sort-latest.json`.

'''
if checkpoint_heading not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
    prefix, _ = plan.split(marker, 1)
    plan = prefix + marker + '''
1. Re-profile contraction subphases after the radix decision.
2. If radix is retained, evaluate caller-owned scratch reuse before widening its routing threshold.
3. Re-run full certified PCG routing after any retained hierarchy change.
4. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
plan_path.write_text(plan)

SCRIPT.unlink(missing_ok=True)
WORKFLOW.unlink(missing_ok=True)
