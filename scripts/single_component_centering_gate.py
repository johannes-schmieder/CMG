import json
import math
import os
from pathlib import Path
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path('src/components.rs')
ORIGINAL = SOURCE.read_text()
BASELINE_SHA = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def run(command, *, env=None, timeout=7000, check=True):
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
    text = SOURCE.read_text()

    old_single = '''            CenteringLabels::Single => {
                if !self.sizes.is_empty() {
                    for (vertex, value) in values.iter().enumerate() {
                        if !value.is_finite() {
                            return Err(CmgError::NonFiniteMatrixValue {
                                row: vertex,
                                column: 0,
                                value: *value,
                            });
                        }
                        neumaier_add(
                            &mut workspace.sums[0],
                            &mut workspace.corrections[0],
                            *value,
                        );
                    }
                }
            }
'''
    new_single = '''            CenteringLabels::Single => {
                if !self.sizes.is_empty() {
                    let mut sum = 0.0;
                    let mut correction = 0.0;
                    for (vertex, value) in values.iter().enumerate() {
                        if !value.is_finite() {
                            return Err(CmgError::NonFiniteMatrixValue {
                                row: vertex,
                                column: 0,
                                value: *value,
                            });
                        }
                        neumaier_add(&mut sum, &mut correction, *value);
                    }
                    workspace.sums[0] = sum;
                    workspace.corrections[0] = correction;
                }
            }
'''
    if text.count(old_single) != 1:
        raise RuntimeError('CenteringPlan single-component block not found exactly once')
    text = text.replace(old_single, new_single, 1)

    old_sums = '''        sums.fill(0.0);
        corrections.fill(0.0);
        for (vertex, (value, label)) in values.iter().zip(&self.labels).enumerate() {
'''
    new_sums = '''        sums.fill(0.0);
        corrections.fill(0.0);
        if self.count() == 1 {
            let mut sum = 0.0;
            let mut correction = 0.0;
            for (vertex, value) in values.iter().enumerate() {
                if !value.is_finite() {
                    return Err(CmgError::NonFiniteMatrixValue {
                        row: vertex,
                        column: 0,
                        value: *value,
                    });
                }
                neumaier_add(&mut sum, &mut correction, *value);
            }
            sums[0] = sum + correction;
            corrections[0] = correction;
            return Ok(());
        }
        for (vertex, (value, label)) in values.iter().zip(&self.labels).enumerate() {
'''
    if text.count(old_sums) != 1:
        raise RuntimeError('compensated_sums_into loop not found exactly once')
    text = text.replace(old_sums, new_sums, 1)

    marker = '    pub(crate) fn center_in_place_with_workspace(\n'
    start = text.index(marker, text.index('impl Components'))
    tail = text[start:]
    old_subtract = '''        for (value, label) in values.iter_mut().zip(&self.labels) {
            *value -= workspace.means[*label];
        }
'''
    new_subtract = '''        if self.count() == 1 {
            let mean = workspace.means[0];
            for value in values {
                *value -= mean;
            }
        } else {
            for (value, label) in values.iter_mut().zip(&self.labels) {
                *value -= workspace.means[*label];
            }
        }
'''
    if tail.count(old_subtract) != 1:
        raise RuntimeError('Components centering subtraction loop not found exactly once')
    tail = tail.replace(old_subtract, new_subtract, 1)
    text = text[:start] + tail
    SOURCE.write_text(text)


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

    stable = (
        'case', 'scale', 'vertices', 'edges', 'levels', 'threads',
        'operators', 'serial_iterations', 'planned_iterations',
        'serial_backward_error', 'planned_backward_error',
        'serial_residual_norm', 'planned_residual_norm',
        'max_scaled_difference',
    )
    reference = observations['baseline'][0]
    for group in observations.values():
        for item in group:
            for key in stable:
                if item[key] != reference[key]:
                    raise RuntimeError(
                        f"numerical or structural mismatch for {key}: "
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
        'metadata': {key: reference[key] for key in stable},
        'baseline_serial_ns': baseline_serial,
        'candidate_serial_ns': candidate_serial,
        'serial_ratio': candidate_serial / baseline_serial,
        'baseline_planned_ns': baseline_planned,
        'candidate_planned_ns': candidate_planned,
        'planned_ratio': candidate_planned / baseline_planned,
    }


result = {
    'schema_version': 1,
    'experiment': 'single-component-centering-fast-path',
    'baseline_sha': BASELINE_SHA,
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'validation': 'not_run',
    'accepted': False,
    'cases': {},
}

try:
    baseline = build(Path('/tmp/cmg-centering-baseline'))
    apply_candidate()
    run(['cargo', 'fmt', '--all'])
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
    candidate = build(Path('/tmp/cmg-centering-candidate'))
    result['validation'] = 'success'

    specs = [
        ('path-150k', ['path', 150000, 3, 4]),
        ('worker-firm-300k', ['worker-firm', 100000, 3, 4]),
        ('dense-worker-firm-400k', ['dense-worker-firm', 25000, 3, 4]),
    ]
    ratios = []
    for name, arguments in specs:
        comparison = compare_case(baseline, candidate, arguments)
        result['cases'][name] = comparison
        ratios.extend([comparison['serial_ratio'], comparison['planned_ratio']])

    geometric = math.exp(sum(math.log(value) for value in ratios) / len(ratios))
    result['geometric_time_ratio'] = geometric
    result['best_time_ratio'] = min(ratios)
    result['worst_time_ratio'] = max(ratios)
    result['acceptance_limits'] = {
        'geometric_time_ratio_max': 0.985,
        'best_time_ratio_max': 0.95,
        'worst_time_ratio_max': 1.04,
    }
    result['accepted'] = (
        geometric <= 0.985
        and min(ratios) <= 0.95
        and max(ratios) <= 1.04
    )
    result['decision_reason'] = (
        'full qualification passed and connected-graph solve time improved'
        if result['accepted']
        else 'qualification passed but the full-solve timing gate was not met'
    )
except Exception as error:
    result['error'] = repr(error)
    result['decision_reason'] = f'experiment failed: {error}'
    print(result['decision_reason'], flush=True)

if not result['accepted']:
    SOURCE.write_text(ORIGINAL)
    run(['cargo', 'fmt', '--all'], check=False)

record = Path('.ci/performance/single-component-centering-latest.json')
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
checkpoint = f'''### Single-component centering checkpoint — 2026-08-23

- The scalar-label fast path was **{status}**.
- Qualification status: `{result['validation']}`.
- Decision: {result.get('decision_reason', 'no decision recorded')}.

| Case | Serial solve ratio | Planned solve ratio |
|---|---:|---:|
''' + ('\n'.join(rows) if rows else '| no completed timing cases | — | — |') + '''

- The candidate preserves the original vertex order and Neumaier operation order; all numerical and structural benchmark fields were required to match exactly.
- Machine-readable evidence: `.ci/performance/single-component-centering-latest.json`.

'''
if '### Single-component centering checkpoint — 2026-08-23' not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
plan_path.write_text(plan)

status_path = Path('PERFORMANCE_STATUS.md')
status_text = status_path.read_text().rstrip()
if '## Single-component centering gate' not in status_text:
    status_text += (
        '\n\n## Single-component centering gate\n\n'
        f"- Decision: `{status}`.\n"
        f"- Validation: `{result['validation']}`.\n"
        f"- Geometric full-solve ratio: `{result.get('geometric_time_ratio', float('nan')):.3f}x`.\n"
        '- Evidence: `.ci/performance/single-component-centering-latest.json`.\n'
    )
    status_path.write_text(status_text + '\n')

Path('.github/workflows/single-component-centering.yml').unlink(missing_ok=True)
Path('scripts/single_component_centering_gate.py').unlink(missing_ok=True)
