import json
import math
import os
from pathlib import Path
import statistics
import subprocess

ROOT = Path.cwd()
PCG = Path('src/pcg.rs')
ORIGINAL_PCG = PCG.read_text()
BASELINE_SHA = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def run(command, *, env=None, timeout=7200, check=True):
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
        '--bin', 'full-pcg-routing',
    ], env=env)
    return target / 'release' / 'full-pcg-routing'


def apply_candidate():
    text = PCG.read_text()
    planned_marker = '''#[cfg(feature = "parallel")]
pub fn solve_pcg_with_plan_and_workspace(
'''
    if text.count(planned_marker) != 1:
        raise RuntimeError('planned PCG marker was not unique')
    start = text.index(planned_marker)
    head = text[:start]
    tail = text[start:]

    replacements = {
        'let initial_residual_norm = euclidean_norm(rhs);':
            'let initial_residual_norm = euclidean_norm_with_executor(rhs, executor);',
        'let projected_initial_norm = euclidean_norm(&workspace.projected_rhs);':
            'let projected_initial_norm = euclidean_norm_with_executor(\n        &workspace.projected_rhs,\n        executor,\n    );',
        'let solution_norm = euclidean_norm(&workspace.solution);':
            'let solution_norm = euclidean_norm_with_executor(&workspace.solution, executor);',
        'let recursive_residual_norm = euclidean_norm(&workspace.residual);':
            'let recursive_residual_norm = euclidean_norm_with_executor(&workspace.residual, executor);',
    }
    for old, new in replacements.items():
        if tail.count(old) != 1:
            raise RuntimeError(f'expected one planned norm call: {old!r}')
        tail = tail.replace(old, new, 1)

    helper_start = tail.index(
        '#[cfg(feature = "parallel")]\nfn recompute_residual_with_plan('
    )
    helper_end = tail.index('\nfn recompute_residual(', helper_start)
    helper_section = tail[helper_start:helper_end]
    old_recompute = '    Ok(euclidean_norm(residual))\n'
    new_recompute = '    Ok(euclidean_norm_with_executor(residual, executor))\n'
    if helper_section.count(old_recompute) != 1:
        raise RuntimeError('planned residual norm call was not unique')
    helper_section = helper_section.replace(old_recompute, new_recompute, 1)
    tail = tail[:helper_start] + helper_section + tail[helper_end:]
    text = head + tail

    norm_marker = '\nfn euclidean_norm(values: &[f64]) -> f64 {\n'
    if text.count(norm_marker) != 1:
        raise RuntimeError('euclidean norm helper marker was not unique')
    helper = '''
#[cfg(feature = "parallel")]
fn euclidean_norm_with_executor(values: &[f64], executor: &ParallelExecutor) -> f64 {
    let options = executor.options();
    let parallel_floor = options
        .reduction_chunk_size
        .saturating_mul(executor.thread_count())
        .saturating_mul(2);
    let scale = if executor.should_parallel(values.len()) && values.len() >= parallel_floor {
        executor.install(|| {
            values
                .par_chunks(options.reduction_chunk_size)
                .map(|chunk| {
                    chunk
                        .iter()
                        .map(|value| value.abs())
                        .fold(0.0_f64, f64::max)
                })
                .reduce(|| 0.0_f64, f64::max)
        })
    } else {
        values
            .iter()
            .map(|value| value.abs())
            .fold(0.0_f64, f64::max)
    };
    if scale == 0.0 {
        0.0
    } else {
        scale
            * compensated_sum(values.iter().map(|value| {
                let scaled = *value / scale;
                scaled * scaled
            }))
            .sqrt()
    }
}
'''
    text = text.replace(norm_marker, '\n' + helper + norm_marker, 1)
    PCG.write_text(text)


def sample(binary, arguments):
    completed = run([str(binary), *map(str, arguments)])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'unexpected benchmark output: {payloads}')
    return payloads[0]


def compare_case(baseline, candidate, arguments):
    observations = {'baseline': [], 'candidate': []}
    for label, binary in (
        ('baseline', baseline),
        ('candidate', candidate),
        ('candidate', candidate),
        ('baseline', baseline),
    ):
        observations[label].append(sample(binary, arguments))

    timing_keys = {'serial_median_ns', 'planned_median_ns', 'speedup'}
    reference = observations['baseline'][0]
    stable_keys = sorted(set(reference) - timing_keys)
    for group in observations.values():
        for item in group:
            if set(item) != set(reference):
                raise RuntimeError('benchmark payload keys changed')
            for key in stable_keys:
                if item[key] != reference[key]:
                    raise RuntimeError(
                        f"exact result mismatch for {key}: "
                        f"{item[key]!r} != {reference[key]!r}"
                    )

    baseline_serial = statistics.median(
        item['serial_median_ns'] for item in observations['baseline']
    )
    candidate_serial = statistics.median(
        item['serial_median_ns'] for item in observations['candidate']
    )
    baseline_planned = statistics.median(
        item['planned_median_ns'] for item in observations['baseline']
    )
    candidate_planned = statistics.median(
        item['planned_median_ns'] for item in observations['candidate']
    )
    return {
        'arguments': arguments,
        'metadata': {key: reference[key] for key in stable_keys},
        'baseline_serial_ns': baseline_serial,
        'candidate_serial_ns': candidate_serial,
        'serial_ratio': candidate_serial / baseline_serial,
        'baseline_planned_ns': baseline_planned,
        'candidate_planned_ns': candidate_planned,
        'planned_ratio': candidate_planned / baseline_planned,
    }


result = {
    'schema_version': 1,
    'experiment': 'parallel-exact-norm-scale-pass',
    'baseline_sha': BASELINE_SHA,
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'validation': 'not_run',
    'accepted': False,
    'cases': {},
}

try:
    baseline = build(Path('/tmp/cmg-norm-scale-baseline'))
    apply_candidate()
    run(['cargo', 'fmt', '--all'])
    run(['cargo', 'fmt', '--manifest-path', 'benchmarks/Cargo.toml', '--all'])
    run(['cargo', 'fmt', '--all', '--', '--check'])
    run([
        'cargo', 'fmt', '--manifest-path', 'benchmarks/Cargo.toml',
        '--all', '--', '--check',
    ])
    run([
        'cargo', 'clippy', '--all-targets', '--all-features',
        '--', '-D', 'warnings',
    ])
    run([
        'cargo', 'clippy', '--manifest-path', 'benchmarks/Cargo.toml',
        '--all-targets', '--', '-D', 'warnings',
    ])
    doc_env = os.environ.copy()
    doc_env['RUSTDOCFLAGS'] = '-D warnings'
    run(['cargo', 'doc', '--no-deps', '--all-features'], env=doc_env)
    run(['cargo', 'test', '--all-targets', '--all-features'])
    run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
    run(['cargo', 'build', '--release', '--all-features'])
    candidate = build(Path('/tmp/cmg-norm-scale-candidate'))
    result['validation'] = 'success'

    specs = [
        ('path-150k', ['path', 150000, 5, 4]),
        ('worker-firm-300k', ['worker-firm', 100000, 5, 4]),
        ('worker-firm-600k', ['worker-firm', 200000, 5, 4]),
        ('dense-worker-firm-400k', ['dense-worker-firm', 25000, 5, 4]),
    ]
    planned_ratios = []
    serial_ratios = []
    for name, arguments in specs:
        comparison = compare_case(baseline, candidate, arguments)
        result['cases'][name] = comparison
        planned_ratios.append(comparison['planned_ratio'])
        serial_ratios.append(comparison['serial_ratio'])

    planned_geometric = math.exp(
        sum(math.log(value) for value in planned_ratios) / len(planned_ratios)
    )
    result['planned_geometric_time_ratio'] = planned_geometric
    result['planned_best_time_ratio'] = min(planned_ratios)
    result['planned_worst_time_ratio'] = max(planned_ratios)
    result['serial_geometric_time_ratio'] = math.exp(
        sum(math.log(value) for value in serial_ratios) / len(serial_ratios)
    )
    result['serial_worst_time_ratio'] = max(serial_ratios)
    result['acceptance_limits'] = {
        'planned_geometric_time_ratio_max': 0.99,
        'planned_best_time_ratio_max': 0.97,
        'planned_worst_time_ratio_max': 1.04,
        'serial_worst_time_ratio_max': 1.05,
    }
    result['accepted'] = (
        planned_geometric <= 0.99
        and min(planned_ratios) <= 0.97
        and max(planned_ratios) <= 1.04
        and max(serial_ratios) <= 1.05
    )
    result['decision_reason'] = (
        'full qualification passed; parallel max-absolute norm passes improved planned solves with exact results'
        if result['accepted']
        else 'qualification passed but the exact full-solve timing gate was not met'
    )
except Exception as error:
    result['error'] = repr(error)
    result['decision_reason'] = f'experiment failed: {error}'
    print(result['decision_reason'], flush=True)

if not result['accepted']:
    PCG.write_text(ORIGINAL_PCG)
    run(['cargo', 'fmt', '--all'], check=False)

record = Path('.ci/performance/parallel-exact-norm-scale-latest.json')
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

plan_path = Path('PERFORMANCE_PLAN.md')
plan = plan_path.read_text()
marker = '## Current next action\n'
status = 'retained' if result['accepted'] else 'not retained'
rows = []
for name, case in result.get('cases', {}).items():
    rows.append(
        f"| {name} | {case['serial_ratio']:.3f}x | "
        f"{case['planned_ratio']:.3f}x |"
    )
checkpoint = f'''### Exact parallel norm-scale checkpoint — 2026-08-23

- Parallel max-absolute passes for planned-PCG norms were **{status}**.
- Qualification status: `{result['validation']}`.
- Decision: {result.get('decision_reason', 'no decision recorded')}.

| Case | Serial solve ratio | Planned solve ratio |
|---|---:|---:|
''' + ('\n'.join(rows) if rows else '| no completed timing cases | — | — |') + '''

- Only the order-independent maximum pass is parallel. The compensated squared-sum pass remains serial in its original order, and every non-timing benchmark field was required to match exactly.
- Machine-readable evidence: `.ci/performance/parallel-exact-norm-scale-latest.json`.

'''
if '### Exact parallel norm-scale checkpoint — 2026-08-23' not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
plan_path.write_text(plan)

status_path = Path('PERFORMANCE_STATUS.md')
status_text = status_path.read_text().rstrip()
if '## Exact parallel norm-scale gate' not in status_text:
    status_text += (
        '\n\n## Exact parallel norm-scale gate\n\n'
        f"- Decision: `{status}`.\n"
        f"- Validation: `{result['validation']}`.\n"
        f"- Planned geometric full-solve ratio: `{result.get('planned_geometric_time_ratio', float('nan')):.3f}x`.\n"
        '- Evidence: `.ci/performance/parallel-exact-norm-scale-latest.json`.\n'
    )
    status_path.write_text(status_text + '\n')

Path('.github/workflows/parallel-exact-norm-scale.yml').unlink(missing_ok=True)
Path('scripts/parallel_exact_norm_scale_gate.py').unlink(missing_ok=True)
