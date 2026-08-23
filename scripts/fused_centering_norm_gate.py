import json
import math
import os
from pathlib import Path
import statistics
import subprocess

ROOT = Path.cwd()
COMPONENTS = Path('src/components.rs')
PCG = Path('src/pcg.rs')
ORIGINAL_COMPONENTS = COMPONENTS.read_text()
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
    components = COMPONENTS.read_text()
    marker = '\n    fn stable_representatives_into(&self, values: &[f64], representatives: &mut [usize]) {'
    if components.count(marker) != 1:
        raise RuntimeError('stable representative insertion marker was not unique')
    method = '''
    pub(crate) fn center_in_place_with_workspace_and_max_abs(
        &self,
        values: &mut [f64],
        workspace: &mut ComponentWorkspace,
    ) -> Result<f64, CmgError> {
        if values.len() != self.labels.len() {
            return Err(CmgError::dimension(
                "Components::center_in_place_with_workspace_and_max_abs",
                self.labels.len(),
                values.len(),
            ));
        }
        workspace.validate(self.count())?;
        self.compensated_sums_into(
            values,
            "Components::center_in_place_with_workspace_and_max_abs",
            &mut workspace.sums,
            &mut workspace.corrections,
        )?;
        for component in 0..self.count() {
            workspace.means[component] = workspace.sums[component] / self.sizes[component] as f64;
        }

        let mut scale: f64 = 0.0;
        if self.count() == 1 {
            let mean = workspace.means[0];
            for value in values {
                *value -= mean;
                scale = scale.max(value.abs());
            }
        } else {
            for (value, label) in values.iter_mut().zip(&self.labels) {
                *value -= workspace.means[*label];
                scale = scale.max(value.abs());
            }
        }
        Ok(scale)
    }
'''
    components = components.replace(marker, method + marker, 1)
    COMPONENTS.write_text(components)

    pcg = PCG.read_text()
    old = '''        components
            .center_in_place_with_workspace(&mut workspace.solution, &mut workspace.component)?;

        let solution_norm = euclidean_norm(&workspace.solution);
'''
    new = '''        let solution_scale = components.center_in_place_with_workspace_and_max_abs(
            &mut workspace.solution,
            &mut workspace.component,
        )?;
        let solution_norm = euclidean_norm_with_scale(&workspace.solution, solution_scale);
'''
    if pcg.count(old) != 2:
        raise RuntimeError(
            f'expected two solution centering/norm sequences, found {pcg.count(old)}'
        )
    pcg = pcg.replace(old, new)

    helper_marker = '\nfn validate_positive_pcg(\n'
    if pcg.count(helper_marker) != 1:
        raise RuntimeError('norm helper insertion marker was not unique')
    helper = '''
fn euclidean_norm_with_scale(values: &[f64], scale: f64) -> f64 {
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
    pcg = pcg.replace(helper_marker, '\n' + helper + helper_marker, 1)
    PCG.write_text(pcg)


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
    'experiment': 'fused-centering-norm-scale',
    'baseline_sha': BASELINE_SHA,
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'validation': 'not_run',
    'accepted': False,
    'cases': {},
}

try:
    baseline = build(Path('/tmp/cmg-fused-norm-baseline'))
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
    candidate = build(Path('/tmp/cmg-fused-norm-candidate'))
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
        'geometric_time_ratio_max': 0.99,
        'best_time_ratio_max': 0.97,
        'worst_time_ratio_max': 1.04,
    }
    result['accepted'] = (
        geometric <= 0.99
        and min(ratios) <= 0.97
        and max(ratios) <= 1.04
    )
    result['decision_reason'] = (
        'full qualification passed and eliminating the extra norm-scale pass improved solves'
        if result['accepted']
        else 'qualification passed but the full-solve timing gate was not met'
    )
except Exception as error:
    result['error'] = repr(error)
    result['decision_reason'] = f'experiment failed: {error}'
    print(result['decision_reason'], flush=True)

if not result['accepted']:
    COMPONENTS.write_text(ORIGINAL_COMPONENTS)
    PCG.write_text(ORIGINAL_PCG)
    run(['cargo', 'fmt', '--all'], check=False)

record = Path('.ci/performance/fused-centering-norm-latest.json')
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
checkpoint = f'''### Fused centering and norm-scale checkpoint — 2026-08-23

- The fused solution-centering/norm-scale pass was **{status}**.
- Qualification status: `{result['validation']}`.
- Decision: {result.get('decision_reason', 'no decision recorded')}.

| Case | Serial solve ratio | Planned solve ratio |
|---|---:|---:|
''' + ('\n'.join(rows) if rows else '| no completed timing cases | — | — |') + '''

- The candidate preserves the centering subtraction order and the norm maximum/squared-sum orders. Every non-timing benchmark field was required to match exactly.
- Machine-readable evidence: `.ci/performance/fused-centering-norm-latest.json`.

'''
if '### Fused centering and norm-scale checkpoint — 2026-08-23' not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
plan_path.write_text(plan)

status_path = Path('PERFORMANCE_STATUS.md')
status_text = status_path.read_text().rstrip()
if '## Fused centering and norm-scale gate' not in status_text:
    status_text += (
        '\n\n## Fused centering and norm-scale gate\n\n'
        f"- Decision: `{status}`.\n"
        f"- Validation: `{result['validation']}`.\n"
        f"- Geometric full-solve ratio: `{result.get('geometric_time_ratio', float('nan')):.3f}x`.\n"
        '- Evidence: `.ci/performance/fused-centering-norm-latest.json`.\n'
    )
    status_path.write_text(status_text + '\n')

Path('.github/workflows/fused-centering-norm.yml').unlink(missing_ok=True)
Path('scripts/fused_centering_norm_gate.py').unlink(missing_ok=True)
