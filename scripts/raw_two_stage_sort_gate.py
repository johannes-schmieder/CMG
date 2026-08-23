import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
GRAPH = Path('src/graph.rs')
SCRIPT = Path('scripts/raw_two_stage_sort_gate.py')
WORKFLOW = Path('.github/workflows/raw-two-stage-sort-gate.yml')
RECORD = Path('.ci/performance/raw-two-stage-sort-latest.json')

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
        '--bin', 'graph-build',
    ], env=env)
    return target / 'release' / 'graph-build'


def replace_once(text, old, new, name):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{name} expected once, found {count}')
    return text.replace(old, new, 1)


def apply_candidate():
    text = GRAPH.read_text()
    serial_old = '''    pub fn from_edges<I>(vertex_count: usize, edges: I) -> Result<Self, CmgError>
    where
        I: IntoIterator<Item = (usize, usize, f64)>,
    {
        let mut raw = collect_validated_edges(vertex_count, edges)?;
        raw.sort_unstable_by(compare_raw_edges);
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
'''
    serial_new = '''    pub fn from_edges<I>(vertex_count: usize, edges: I) -> Result<Self, CmgError>
    where
        I: IntoIterator<Item = (usize, usize, f64)>,
    {
        let mut raw = collect_validated_edges(vertex_count, edges)?;
        sort_compact_edges_two_stage(&mut raw);
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
'''
    executor_old = '''    #[cfg(feature = "parallel")]
    pub fn from_edges_with_executor<I>(
        vertex_count: usize,
        edges: I,
        executor: &ParallelExecutor,
    ) -> Result<Self, CmgError>
    where
        I: IntoIterator<Item = (usize, usize, f64)>,
    {
        let mut raw = collect_validated_edges(vertex_count, edges)?;
        if raw.len() >= PARALLEL_SETUP_MIN_ITEMS && executor.should_parallel(raw.len()) {
            executor.install(|| raw.par_sort_unstable_by(compare_raw_edges));
        } else {
            raw.sort_unstable_by(compare_raw_edges);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
'''
    executor_new = '''    #[cfg(feature = "parallel")]
    pub fn from_edges_with_executor<I>(
        vertex_count: usize,
        edges: I,
        executor: &ParallelExecutor,
    ) -> Result<Self, CmgError>
    where
        I: IntoIterator<Item = (usize, usize, f64)>,
    {
        let mut raw = collect_validated_edges(vertex_count, edges)?;
        if raw.len() >= PARALLEL_SETUP_MIN_ITEMS && executor.should_parallel(raw.len()) {
            executor.install(|| raw.par_sort_unstable_by(compare_raw_edges));
        } else {
            sort_compact_edges_two_stage(&mut raw);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
'''
    text = replace_once(text, serial_old, serial_new, 'raw serial constructor')
    text = replace_once(text, executor_old, executor_new, 'raw executor constructor')
    GRAPH.write_text(text)


def sample(binary, case, scale, tag):
    time_file = Path(f'/tmp/cmg-raw-two-stage-{tag}.time')
    completed = run(['/usr/bin/time', '-v', '-o', time_file, binary, case, scale, 5])
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
    stable = ('case', 'scale', 'vertices', 'raw_edges', 'retained_edges')
    reference = baseline_rows[0]
    for row in baseline_rows[1:] + candidate_rows:
        for key in stable:
            if row[key] != reference[key]:
                raise RuntimeError(f'{name}: stable field changed: {key}')
    bt = statistics.median(row['median_ns'] for row in baseline_rows)
    ct = statistics.median(row['median_ns'] for row in candidate_rows)
    b_rss = max(row['peak_rss_kib'] for row in baseline_rows)
    c_rss = max(row['peak_rss_kib'] for row in candidate_rows)
    return {
        'case': case,
        'scale': scale,
        'metadata': {key: reference[key] for key in stable},
        'baseline_median_ns': bt,
        'candidate_median_ns': ct,
        'candidate_over_baseline_time': ct / bt,
        'baseline_peak_rss_kib': b_rss,
        'candidate_peak_rss_kib': c_rss,
        'candidate_over_baseline_peak_rss': c_rss / b_rss,
    }


result = {
    'schema_version': 1,
    'experiment': 'raw-graph-two-stage-endpoint-weight-sort',
    'source_sha': source_sha,
    'validation': 'not_run',
    'accepted': False,
    'cases': {},
    'decision_reason': '',
}

try:
    baseline = build('/tmp/cmg-raw-two-stage-baseline')
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
    candidate = build('/tmp/cmg-raw-two-stage-candidate')
    result['validation'] = 'success'
    specs = (
        ('unique-1m', 'unique', 1_000_000),
        ('duplicates-4-1m', 'duplicates-4', 250_000),
        ('duplicates-16-1m', 'duplicates-16', 62_500),
        ('coarse-collisions-1m', 'coarse-collisions', 62_500),
    )
    for name, case, scale in specs:
        result['cases'][name] = compare_case(baseline, candidate, name, case, scale)
    times = [row['candidate_over_baseline_time'] for row in result['cases'].values()]
    duplicate_times = [
        result['cases'][name]['candidate_over_baseline_time']
        for name in ('duplicates-4-1m', 'duplicates-16-1m', 'coarse-collisions-1m')
    ]
    rss = [row['candidate_over_baseline_peak_rss'] for row in result['cases'].values()]
    result['geometric_time_ratio'] = math.exp(sum(map(math.log, times)) / len(times))
    result['duplicate_geometric_time_ratio'] = math.exp(
        sum(map(math.log, duplicate_times)) / len(duplicate_times)
    )
    result['worst_time_ratio'] = max(times)
    result['worst_peak_rss_ratio'] = max(rss)
    result['acceptance_limits'] = {
        'geometric_time_ratio_max': 0.985,
        'duplicate_geometric_time_ratio_max': 0.975,
        'worst_time_ratio_max': 1.035,
        'worst_peak_rss_ratio_max': 1.03,
    }
    result['accepted'] = (
        result['geometric_time_ratio'] <= 0.985
        and result['duplicate_geometric_time_ratio'] <= 0.975
        and result['worst_time_ratio'] <= 1.035
        and result['worst_peak_rss_ratio'] <= 1.03
    )
    result['decision_reason'] = (
        'full qualification passed; endpoint-first ordering improved raw graph construction across unique and duplicate-heavy inputs'
        if result['accepted'] else
        'qualification passed but raw graph construction timing or RSS gates were not met'
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
geo = result.get('geometric_time_ratio', 'n/a')
duplicate = result.get('duplicate_geometric_time_ratio', 'n/a')
status = Path('PERFORMANCE_STATUS.md')
text = status.read_text().rstrip()
text += f'''\n\n## Raw graph two-stage ordering gate

- Decision: `{decision}`.
- Validation: `{result['validation']}`.
- Overall geometric graph-build ratio: `{geo}`.
- Duplicate-heavy geometric graph-build ratio: `{duplicate}`.
- The parallel sort path remains unchanged.
- Evidence: `.ci/performance/raw-two-stage-sort-latest.json`.
'''
status.write_text(text.rstrip() + '\n')

plan = Path('PERFORMANCE_PLAN.md')
text = plan.read_text()
marker = '## Current next action\n'
checkpoint = f'''### Raw graph two-stage sort checkpoint — 2026-08-23

- Candidate was **{decision}**.
- Validation: `{result['validation']}`.
- Overall/duplicate-heavy graph-build ratios: `{geo}` / `{duplicate}`.
- Evidence: `.ci/performance/raw-two-stage-sort-latest.json`.

'''
if checkpoint.splitlines()[0] not in text:
    text = text.replace(marker, checkpoint + marker, 1)
if marker in text:
    prefix, _ = text.split(marker, 1)
    text = prefix + marker + '''
1. Benchmark the remaining contraction mapping kernel under the current sort and merge implementation.
2. Re-run full certified PCG routing after any retained hierarchy change.
3. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
plan.write_text(text)

WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
