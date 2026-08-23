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
    import_marker = 'use crate::{CmgError, Laplacian, ValidationOptions};\n'
    if components.count(import_marker) != 1:
        raise RuntimeError('components import marker was not unique')
    imports = '''use crate::{CmgError, Laplacian, ValidationOptions};
#[cfg(feature = "parallel")]
use crate::ParallelExecutor;
#[cfg(feature = "parallel")]
use rayon::prelude::*;
'''
    components = components.replace(import_marker, imports, 1)

    insertion_marker = '\n    fn stable_representatives_into(&self, values: &[f64], representatives: &mut [usize]) {'
    if components.count(insertion_marker) != 1:
        raise RuntimeError('component method insertion marker was not unique')
    method = '''
    #[cfg(feature = "parallel")]
    pub(crate) fn center_in_place_with_workspace_and_executor(
        &self,
        values: &mut [f64],
        workspace: &mut ComponentWorkspace,
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        if values.len() != self.labels.len() {
            return Err(CmgError::dimension(
                "Components::center_in_place_with_workspace_and_executor",
                self.labels.len(),
                values.len(),
            ));
        }
        workspace.validate(self.count())?;
        self.compensated_sums_into(
            values,
            "Components::center_in_place_with_workspace_and_executor",
            &mut workspace.sums,
            &mut workspace.corrections,
        )?;
        for component in 0..self.count() {
            workspace.means[component] = workspace.sums[component] / self.sizes[component] as f64;
        }

        if executor.should_parallel(values.len()) {
            if self.count() == 1 {
                let mean = workspace.means[0];
                executor.install(|| {
                    values.par_iter_mut().for_each(|value| *value -= mean);
                });
            } else {
                let labels = &self.labels;
                let means = &workspace.means;
                executor.install(|| {
                    values
                        .par_iter_mut()
                        .zip(labels.par_iter())
                        .for_each(|(value, label)| *value -= means[*label]);
                });
            }
        } else if self.count() == 1 {
            let mean = workspace.means[0];
            for value in values {
                *value -= mean;
            }
        } else {
            for (value, label) in values.iter_mut().zip(&self.labels) {
                *value -= workspace.means[*label];
            }
        }
        Ok(())
    }
'''
    components = components.replace(insertion_marker, method + insertion_marker, 1)
    COMPONENTS.write_text(components)

    pcg = PCG.read_text()
    planned_marker = '''#[cfg(feature = "parallel")]
pub fn solve_pcg_with_plan_and_workspace(
'''
    if pcg.count(planned_marker) != 1:
        raise RuntimeError('planned PCG marker was not unique')
    start = pcg.index(planned_marker)
    head = pcg[:start]
    tail = pcg[start:]
    if tail.count('center_in_place_with_workspace') != 4:
        raise RuntimeError(
            f"expected four planned centering calls, found {tail.count('center_in_place_with_workspace')}"
        )

    replacements = [
        (
            '''    components
        .center_in_place_with_workspace(&mut workspace.preconditioned, &mut workspace.component)?;
''',
            '''    components.center_in_place_with_workspace_and_executor(
        &mut workspace.preconditioned,
        &mut workspace.component,
        executor,
    )?;
''',
        ),
        (
            '''        components
            .center_in_place_with_workspace(&mut workspace.solution, &mut workspace.component)?;
''',
            '''        components.center_in_place_with_workspace_and_executor(
            &mut workspace.solution,
            &mut workspace.component,
            executor,
        )?;
''',
        ),
        (
            '''        components
            .center_in_place_with_workspace(&mut workspace.residual, &mut workspace.component)?;
''',
            '''        components.center_in_place_with_workspace_and_executor(
            &mut workspace.residual,
            &mut workspace.component,
            executor,
        )?;
''',
        ),
        (
            '''        components.center_in_place_with_workspace(
            &mut workspace.preconditioned,
            &mut workspace.component,
        )?;
''',
            '''        components.center_in_place_with_workspace_and_executor(
            &mut workspace.preconditioned,
            &mut workspace.component,
            executor,
        )?;
''',
        ),
    ]
    for old, new in replacements:
        if tail.count(old) != 1:
            raise RuntimeError('a planned centering call did not match exactly once')
        tail = tail.replace(old, new, 1)
    if 'center_in_place_with_workspace(' in tail:
        raise RuntimeError('an unconverted planned centering call remains')
    PCG.write_text(head + tail)


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
    'experiment': 'parallel-outer-pcg-centering',
    'baseline_sha': BASELINE_SHA,
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'validation': 'not_run',
    'accepted': False,
    'cases': {},
}

try:
    baseline = build(Path('/tmp/cmg-parallel-centering-baseline'))
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
    candidate = build(Path('/tmp/cmg-parallel-centering-candidate'))
    result['validation'] = 'success'

    specs = [
        ('path-150k', ['path', 150000, 3, 4]),
        ('worker-firm-300k', ['worker-firm', 100000, 3, 4]),
        ('worker-firm-600k', ['worker-firm', 200000, 3, 4]),
        ('dense-worker-firm-400k', ['dense-worker-firm', 25000, 3, 4]),
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
        'planned_geometric_time_ratio_max': 0.985,
        'planned_best_time_ratio_max': 0.96,
        'planned_worst_time_ratio_max': 1.04,
        'serial_worst_time_ratio_max': 1.04,
    }
    result['accepted'] = (
        planned_geometric <= 0.985
        and min(planned_ratios) <= 0.96
        and max(planned_ratios) <= 1.04
        and max(serial_ratios) <= 1.04
    )
    result['decision_reason'] = (
        'full qualification passed and parallel mean subtraction improved planned solves'
        if result['accepted']
        else 'qualification passed but the planned full-solve timing gate was not met'
    )
except Exception as error:
    result['error'] = repr(error)
    result['decision_reason'] = f'experiment failed: {error}'
    print(result['decision_reason'], flush=True)

if not result['accepted']:
    COMPONENTS.write_text(ORIGINAL_COMPONENTS)
    PCG.write_text(ORIGINAL_PCG)
    run(['cargo', 'fmt', '--all'], check=False)

record = Path('.ci/performance/parallel-outer-centering-latest.json')
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
checkpoint = f'''### Parallel outer-PCG centering checkpoint — 2026-08-23

- Parallel mean subtraction in the planned solver was **{status}**.
- Qualification status: `{result['validation']}`.
- Decision: {result.get('decision_reason', 'no decision recorded')}.

| Case | Serial solve ratio | Planned solve ratio |
|---|---:|---:|
''' + ('\n'.join(rows) if rows else '| no completed timing cases | — | — |') + '''

- Compensated component sums and means remain serial and unchanged. Only independent per-vertex subtraction is parallelized, and every non-timing benchmark field was required to match exactly.
- Machine-readable evidence: `.ci/performance/parallel-outer-centering-latest.json`.

'''
if '### Parallel outer-PCG centering checkpoint — 2026-08-23' not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
plan_path.write_text(plan)

status_path = Path('PERFORMANCE_STATUS.md')
status_text = status_path.read_text().rstrip()
if '## Parallel outer-PCG centering gate' not in status_text:
    status_text += (
        '\n\n## Parallel outer-PCG centering gate\n\n'
        f"- Decision: `{status}`.\n"
        f"- Validation: `{result['validation']}`.\n"
        f"- Planned geometric full-solve ratio: `{result.get('planned_geometric_time_ratio', float('nan')):.3f}x`.\n"
        '- Evidence: `.ci/performance/parallel-outer-centering-latest.json`.\n'
    )
    status_path.write_text(status_text + '\n')

Path('.github/workflows/parallel-outer-centering.yml').unlink(missing_ok=True)
Path('scripts/parallel_outer_centering_gate.py').unlink(missing_ok=True)
