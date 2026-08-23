import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
BASELINE_PATH = Path('.ci/performance/full-pcg-routing-latest.json')
RECORD_PATH = Path('.ci/performance/post-compact-label-routing.json')
SCRIPT_PATH = Path('scripts/requalify_compact_label_routing.py')
WORKFLOW_PATH = Path('.github/workflows/requalify-compact-label-routing.yml')
STATUS_PATH = Path('PERFORMANCE_STATUS.md')
PLAN_PATH = Path('PERFORMANCE_PLAN.md')


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


def sample(binary, name, arguments):
    time_path = Path(f'/tmp/cmg-post-label-routing-{name}.time')
    completed = run([
        '/usr/bin/time', '-v', '-o', time_path,
        binary, *arguments,
    ])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'{name}: unexpected benchmark output {payloads}')
    match = re.search(
        r'Maximum resident set size \(kbytes\):\s*(\d+)',
        time_path.read_text(),
    )
    if match is None:
        raise RuntimeError(f'{name}: peak RSS missing')
    payload = payloads[0]
    payload['peak_rss_kib'] = int(match.group(1))
    return payload


def close(left, right, tolerance=5e-12):
    return abs(left - right) <= tolerance * (1.0 + max(abs(left), abs(right)))


source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
baseline = json.loads(BASELINE_PATH.read_text())
result = {
    'schema_version': 1,
    'experiment': 'post-compact-label-full-pcg-routing',
    'source_sha': source_sha,
    'baseline_record': str(BASELINE_PATH),
    'baseline_tested_sha': baseline.get('tested_sha'),
    'status': 'not_run',
    'cases': {},
    'routing_failures': [],
    'metadata_failures': [],
    'numerical_failures': [],
}

specs = (
    ('path-250k', 'path', 250_000, 'Serial'),
    ('worker-firm-300k', 'worker-firm', 100_000, 'Serial'),
    ('worker-firm-375k', 'worker-firm', 125_000, 'Planned'),
    ('worker-firm-450k', 'worker-firm', 150_000, 'Planned'),
    ('worker-firm-525k', 'worker-firm', 175_000, 'Planned'),
    ('worker-firm-600k', 'worker-firm', 200_000, 'Planned'),
    ('dense-worker-firm-400k', 'dense-worker-firm', 25_000, 'Planned'),
    ('dense-worker-firm-600k', 'dense-worker-firm', 37_500, 'Planned'),
    ('dense-worker-firm-800k', 'dense-worker-firm', 50_000, 'Planned'),
    ('dense-worker-firm-1.6m', 'dense-worker-firm', 100_000, 'Planned'),
)

try:
    run(['cargo', 'fmt', '--all', '--', '--check'])
    run(['cargo', 'clippy', '--all-targets', '--all-features', '--', '-D', 'warnings'])
    run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
    target = Path('/tmp/cmg-post-label-routing-target')
    env = os.environ.copy()
    env['CARGO_TARGET_DIR'] = str(target)
    run([
        'cargo', 'build', '--release',
        '--manifest-path', 'benchmarks/Cargo.toml',
        '--bin', 'full-pcg-routing',
    ], env=env)
    binary = target / 'release' / 'full-pcg-routing'

    speedups = []
    selected_speedups = []
    maximum_difference = 0.0
    for name, case_name, scale, expected_execution in specs:
        current = sample(
            binary,
            name,
            [case_name, str(scale), '3', '4'],
        )
        previous = baseline['cases'].get(name)
        current['expected_execution'] = expected_execution
        current['route_matches_expected'] = current['auto_execution'] == expected_execution
        result['cases'][name] = current
        speedups.append(current['speedup'])
        if expected_execution == 'Planned':
            selected_speedups.append(current['speedup'])

        if not current['route_matches_expected']:
            result['routing_failures'].append(name)

        if previous is None:
            result['metadata_failures'].append(f'{name}:missing-baseline')
        else:
            for key in (
                'case', 'scale', 'vertices', 'input_edges', 'edges', 'levels',
                'operators', 'plan_bytes', 'workspace_bytes',
                'serial_iterations', 'planned_iterations',
            ):
                if current[key] != previous[key]:
                    result['metadata_failures'].append(
                        f'{name}:{key}:{previous[key]}->{current[key]}'
                    )

        difference = current['max_scaled_difference']
        maximum_difference = max(maximum_difference, difference)
        numerical_ok = (
            math.isfinite(current['serial_backward_error'])
            and math.isfinite(current['planned_backward_error'])
            and math.isfinite(current['serial_residual_norm'])
            and math.isfinite(current['planned_residual_norm'])
            and current['serial_backward_error'] <= 1e-7
            and current['planned_backward_error'] <= 1e-7
            and current['serial_iterations'] == current['planned_iterations']
            and difference <= 1e-8
            and close(
                current['serial_backward_error'],
                current['planned_backward_error'],
                tolerance=1e-10,
            )
        )
        if not numerical_ok:
            result['numerical_failures'].append(name)

    result['geometric_speedup_all'] = math.exp(
        statistics.fmean(math.log(value) for value in speedups)
    )
    result['geometric_speedup_planned_cases'] = math.exp(
        statistics.fmean(math.log(value) for value in selected_speedups)
    )
    result['minimum_speedup'] = min(speedups)
    result['maximum_speedup'] = max(speedups)
    result['maximum_scaled_difference'] = maximum_difference
    result['status'] = (
        'success'
        if not result['routing_failures']
        and not result['metadata_failures']
        and not result['numerical_failures']
        else 'failure'
    )
except Exception as error:
    result['status'] = 'failure'
    result['error'] = repr(error)
    print(f'post-compaction routing qualification failed: {error}', flush=True)

RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
RECORD_PATH.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

status = STATUS_PATH.read_text().rstrip()
heading = '## Post-compaction routing requalification\n'
block = f'''{heading}
- Status: `{result['status']}`.
- Source SHA: `{source_sha}`.
- Routing failures: `{', '.join(result['routing_failures']) or 'none'}`.
- Metadata failures: `{', '.join(result['metadata_failures']) or 'none'}`.
- Numerical failures: `{', '.join(result['numerical_failures']) or 'none'}`.
- Planned-case geometric speedup: `{result.get('geometric_speedup_planned_cases', 'n/a')}`.
- Maximum scaled serial/planned solution difference: `{result.get('maximum_scaled_difference', 'n/a')}`.
- Evidence: `.ci/performance/post-compact-label-routing.json`.
'''
if heading in status:
    start = status.index(heading)
    end = status.find('\n## ', start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + block + status[end:]
else:
    status += '\n\n' + block
STATUS_PATH.write_text(status.rstrip() + '\n')

plan = PLAN_PATH.read_text()
marker = '## Current next action\n'
checkpoint_heading = '### Post-compaction routing requalification — 2026-08-23\n'
checkpoint = f'''{checkpoint_heading}
- Status: `{result['status']}`.
- Routing / metadata / numerical failures: `{len(result['routing_failures'])}` / `{len(result['metadata_failures'])}` / `{len(result['numerical_failures'])}`.
- Planned-case geometric speedup: `{result.get('geometric_speedup_planned_cases', 'n/a')}`.
- Evidence: `.ci/performance/post-compact-label-routing.json`.

'''
if checkpoint_heading not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
    prefix, _ = plan.split(marker, 1)
    plan = prefix + marker + '''
1. Re-profile contraction mapping and sorting after compact aggregation labels.
2. Evaluate compact aggregate-size storage only if retained-memory accounting shows material headroom.
3. Revisit moderate-density scratch radix only if reusable scratch can preserve its speed signal without peak-memory inflation.
4. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
PLAN_PATH.write_text(plan)

SCRIPT_PATH.unlink(missing_ok=True)
WORKFLOW_PATH.unlink(missing_ok=True)

if result['status'] != 'success':
    raise SystemExit('post-compaction routing qualification failed')
