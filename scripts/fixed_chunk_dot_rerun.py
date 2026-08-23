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
        'let mut rho = dot(&workspace.residual, &workspace.preconditioned);':
            'let mut rho = dot_with_executor(\n        &workspace.residual,\n        &workspace.preconditioned,\n        executor,\n    );',
        'let direction_curvature = dot(&workspace.direction, &workspace.matrix_direction);':
            'let direction_curvature = dot_with_executor(\n            &workspace.direction,\n            &workspace.matrix_direction,\n            executor,\n        );',
        'let new_rho = dot(&workspace.residual, &workspace.preconditioned);':
            'let new_rho = dot_with_executor(\n            &workspace.residual,\n            &workspace.preconditioned,\n            executor,\n        );',
    }
    for old, new in replacements.items():
        if tail.count(old) != 1:
            raise RuntimeError(f'expected one planned dot call: {old!r}')
        tail = tail.replace(old, new, 1)
    text = head + tail

    dot_marker = '\nfn dot(left: &[f64], right: &[f64]) -> f64 {\n'
    if text.count(dot_marker) != 1:
        raise RuntimeError('dot helper marker was not unique')
    helper = '''
#[cfg(feature = "parallel")]
fn dot_with_executor(left: &[f64], right: &[f64], executor: &ParallelExecutor) -> f64 {
    debug_assert_eq!(left.len(), right.len());
    let options = executor.options();
    let parallel_floor = options
        .min_parallel_len
        .max(options.reduction_chunk_size.saturating_mul(8));
    if left.len() < parallel_floor || executor.thread_count() <= 1 {
        return dot(left, right);
    }
    executor.install(|| fixed_chunk_dot(left, right, options.reduction_chunk_size))
}

#[cfg(feature = "parallel")]
fn fixed_chunk_dot(left: &[f64], right: &[f64], chunk_size: usize) -> f64 {
    debug_assert_eq!(left.len(), right.len());
    let chunk_count = left.len().div_ceil(chunk_size);
    if chunk_count == 0 {
        return 0.0;
    }

    fn reduce_range(
        left: &[f64],
        right: &[f64],
        chunk_size: usize,
        first_chunk: usize,
        last_chunk: usize,
    ) -> f64 {
        if last_chunk - first_chunk == 1 {
            let start = first_chunk * chunk_size;
            let end = left.len().min(start + chunk_size);
            return compensated_sum(
                left[start..end]
                    .iter()
                    .zip(&right[start..end])
                    .map(|(left, right)| left * right),
            );
        }
        let middle = first_chunk + (last_chunk - first_chunk) / 2;
        let (left_sum, right_sum) = rayon::join(
            || reduce_range(left, right, chunk_size, first_chunk, middle),
            || reduce_range(left, right, chunk_size, middle, last_chunk),
        );
        compensated_sum([left_sum, right_sum])
    }

    reduce_range(left, right, chunk_size, 0, chunk_count)
}

#[cfg(all(test, feature = "parallel"))]
mod deterministic_parallel_dot_tests {
    use super::{dot, dot_with_executor};
    use crate::{ParallelExecutor, ParallelOptions};

    #[test]
    fn fixed_chunk_dot_is_thread_count_invariant() {
        let left: Vec<f64> = (0..257)
            .map(|index| ((index * 17) % 101) as f64 / 13.0 - 3.0)
            .collect();
        let right: Vec<f64> = (0..257)
            .map(|index| ((index * 31 + 7) % 113) as f64 / 19.0 - 2.0)
            .collect();
        let mut reference = None;
        for threads in [2, 3, 4] {
            let executor = ParallelExecutor::new(ParallelOptions {
                threads,
                min_parallel_len: 1,
                reduction_chunk_size: 16,
                ..ParallelOptions::default()
            })
            .unwrap();
            let value = dot_with_executor(&left, &right, &executor);
            match reference {
                Some(bits) => assert_eq!(bits, value.to_bits()),
                None => reference = Some(value.to_bits()),
            }
        }
        let fixed = f64::from_bits(reference.unwrap());
        let serial = dot(&left, &right);
        assert!((fixed - serial).abs() <= 2.0e-13 * (1.0 + serial.abs()));
    }
}
'''
    text = text.replace(dot_marker, '\n' + helper + dot_marker, 1)
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


def scaled_difference(left, right):
    return abs(left - right) / (1.0 + max(abs(left), abs(right)))


def compare_case(baseline, candidate, arguments):
    observations = {'baseline': [], 'candidate': []}
    for label, binary in (
        ('baseline', baseline),
        ('candidate', candidate),
        ('candidate', candidate),
        ('baseline', baseline),
    ):
        observations[label].append(sample(binary, arguments))

    baseline_reference = observations['baseline'][0]
    candidate_reference = observations['candidate'][0]
    exact_keys = (
        'case', 'scale', 'vertices', 'input_edges', 'edges', 'levels',
        'repetitions', 'threads', 'operators', 'plan_bytes', 'workspace_bytes',
        'auto_execution', 'serial_iterations', 'serial_backward_error',
        'serial_residual_norm',
    )
    for group in observations.values():
        for item in group:
            for key in exact_keys:
                if item[key] != baseline_reference[key]:
                    raise RuntimeError(
                        f"exact structural/serial mismatch for {key}: "
                        f"{item[key]!r} != {baseline_reference[key]!r}"
                    )
    for item in observations['candidate']:
        if item['planned_iterations'] != baseline_reference['planned_iterations']:
            raise RuntimeError('planned iteration count changed')
        if item['max_scaled_difference'] > 5.0e-10:
            raise RuntimeError('candidate solution differs too much from serial')
        if not math.isfinite(item['planned_backward_error']):
            raise RuntimeError('candidate backward error is non-finite')
        if not math.isfinite(item['planned_residual_norm']):
            raise RuntimeError('candidate residual norm is non-finite')
        if scaled_difference(
            item['planned_backward_error'],
            baseline_reference['planned_backward_error'],
        ) > 5.0e-10:
            raise RuntimeError('candidate backward error changed too much')
        if scaled_difference(
            item['planned_residual_norm'],
            baseline_reference['planned_residual_norm'],
        ) > 5.0e-10:
            raise RuntimeError('candidate residual norm changed too much')

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
        'baseline_planned': {
            'iterations': baseline_reference['planned_iterations'],
            'backward_error': baseline_reference['planned_backward_error'],
            'residual_norm': baseline_reference['planned_residual_norm'],
            'max_scaled_difference': baseline_reference['max_scaled_difference'],
        },
        'candidate_planned': {
            'iterations': candidate_reference['planned_iterations'],
            'backward_error': candidate_reference['planned_backward_error'],
            'residual_norm': candidate_reference['planned_residual_norm'],
            'max_scaled_difference': candidate_reference['max_scaled_difference'],
        },
        'baseline_serial_ns': baseline_serial,
        'candidate_serial_ns': candidate_serial,
        'serial_ratio': candidate_serial / baseline_serial,
        'baseline_planned_ns': baseline_planned,
        'candidate_planned_ns': candidate_planned,
        'planned_ratio': candidate_planned / baseline_planned,
    }


result = {
    'schema_version': 2,
    'experiment': 'deterministic-fixed-chunk-dot',
    'baseline_sha': BASELINE_SHA,
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'validation': 'not_run',
    'accepted': False,
    'cases': {},
}

try:
    baseline = build(Path('/tmp/cmg-fixed-dot-baseline'))
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
    candidate = build(Path('/tmp/cmg-fixed-dot-candidate'))
    result['validation'] = 'success'

    specs = [
        ('path-150k', ['path', 150000, 5, 4]),
        ('worker-firm-300k', ['worker-firm', 100000, 5, 4]),
        ('worker-firm-600k', ['worker-firm', 200000, 5, 4]),
        ('dense-worker-firm-400k', ['dense-worker-firm', 25000, 5, 4]),
    ]
    planned_ratios = []
    serial_ratios = []
    maximum_solution_difference = 0.0
    for name, arguments in specs:
        comparison = compare_case(baseline, candidate, arguments)
        result['cases'][name] = comparison
        planned_ratios.append(comparison['planned_ratio'])
        serial_ratios.append(comparison['serial_ratio'])
        maximum_solution_difference = max(
            maximum_solution_difference,
            comparison['candidate_planned']['max_scaled_difference'],
        )

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
    result['maximum_candidate_scaled_solution_difference'] = maximum_solution_difference
    result['acceptance_limits'] = {
        'planned_geometric_time_ratio_max': 0.99,
        'planned_best_time_ratio_max': 0.96,
        'planned_worst_time_ratio_max': 1.04,
        'serial_worst_time_ratio_max': 1.05,
        'maximum_scaled_solution_difference': 5.0e-10,
    }
    result['accepted'] = (
        planned_geometric <= 0.99
        and min(planned_ratios) <= 0.96
        and max(planned_ratios) <= 1.04
        and max(serial_ratios) <= 1.05
        and maximum_solution_difference <= 5.0e-10
    )
    result['decision_reason'] = (
        'full qualification passed; fixed deterministic chunk reductions improved planned solves'
        if result['accepted']
        else 'qualification passed but the deterministic full-solve timing gate was not met'
    )
except Exception as error:
    result['error'] = repr(error)
    result['decision_reason'] = f'experiment failed: {error}'
    print(result['decision_reason'], flush=True)

if not result['accepted']:
    PCG.write_text(ORIGINAL_PCG)
    run(['cargo', 'fmt', '--all'], check=False)

record = Path('.ci/performance/fixed-chunk-dot-latest.json')
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

rows = []
for name, case in result.get('cases', {}).items():
    rows.append(
        f"| {name} | {case['serial_ratio']:.3f}x | "
        f"{case['planned_ratio']:.3f}x | "
        f"{case['candidate_planned']['max_scaled_difference']:.2e} |"
    )
status = 'retained' if result['accepted'] else 'not retained'
checkpoint = f'''### Deterministic fixed-chunk dot checkpoint — 2026-08-23

- Fixed-chunk planned-PCG dot products were **{status}**.
- Qualification status: `{result['validation']}`.
- Decision: {result.get('decision_reason', 'no decision recorded')}.

| Case | Serial solve ratio | Planned solve ratio | Planned/serial scaled difference |
|---|---:|---:|---:|
''' + ('\n'.join(rows) if rows else '| no completed timing cases | — | — | — |') + '''

- Chunk boundaries and the binary combine tree are fixed by `reduction_chunk_size`, so results are invariant to thread scheduling and thread count. Iteration counts were required to remain unchanged and final solutions/residual certificates remain independently verified.
- Machine-readable evidence: `.ci/performance/fixed-chunk-dot-latest.json`.

'''
plan_path = Path('PERFORMANCE_PLAN.md')
plan = plan_path.read_text()
heading = '### Deterministic fixed-chunk dot checkpoint — 2026-08-23\n'
if heading in plan:
    start = plan.index(heading)
    end = plan.index('## Current next action\n', start)
    plan = plan[:start] + checkpoint + plan[end:]
else:
    plan = plan.replace('## Current next action\n', checkpoint + '## Current next action\n', 1)
plan_path.write_text(plan)

status_path = Path('PERFORMANCE_STATUS.md')
status_text = status_path.read_text()
status_heading = '## Deterministic fixed-chunk dot gate\n'
status_block = (
    '## Deterministic fixed-chunk dot gate\n\n'
    f"- Decision: `{status}`.\n"
    f"- Validation: `{result['validation']}`.\n"
    f"- Planned geometric full-solve ratio: `{result.get('planned_geometric_time_ratio', float('nan')):.3f}x`.\n"
    f"- Maximum scaled solution difference: `{result.get('maximum_candidate_scaled_solution_difference', float('nan')):.3e}`.\n"
    '- Evidence: `.ci/performance/fixed-chunk-dot-latest.json`.\n'
)
if status_heading in status_text:
    start = status_text.index(status_heading)
    end = status_text.find('\n## ', start + len(status_heading))
    if end == -1:
        end = len(status_text.rstrip())
    status_text = status_text[:start] + status_block + status_text[end:]
else:
    status_text = status_text.rstrip() + '\n\n' + status_block
status_path.write_text(status_text.rstrip() + '\n')

Path('.github/workflows/fixed-chunk-dot-rerun.yml').unlink(missing_ok=True)
Path('scripts/fixed_chunk_dot_rerun.py').unlink(missing_ok=True)
