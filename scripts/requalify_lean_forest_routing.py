import json
import math
import os
from pathlib import Path
import re
import subprocess

ROOT = Path.cwd()
SCRIPT = Path('scripts/requalify_lean_forest_routing.py')
WORKFLOW = Path('.github/workflows/requalify-lean-forest-routing.yml')
RECORD = Path('.ci/performance/post-lean-forest-routing.json')
BASELINE_RECORD = Path('.ci/performance/post-lazy-size-routing.json')
STATUS = Path('PERFORMANCE_STATUS.md')
PLAN = Path('PERFORMANCE_PLAN.md')

source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def run(command, *, env=None, timeout=9000):
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
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
        )
    return completed


def sample(binary, case_name, scale, repetitions, threads, tag):
    time_path = Path(f'/tmp/cmg-post-lean-routing-{tag}.time')
    completed = run([
        '/usr/bin/time', '-v', '-o', time_path,
        binary, case_name, scale, repetitions, threads,
    ])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'{tag}: unexpected benchmark output: {payloads}')
    match = re.search(
        r'Maximum resident set size \(kbytes\):\s*(\d+)',
        time_path.read_text(),
    )
    if match is None:
        raise RuntimeError(f'{tag}: peak RSS missing')
    payload = payloads[0]
    payload['peak_rss_kib'] = int(match.group(1))
    return payload


result = {
    'schema_version': 1,
    'experiment': 'post-lean-forest-full-pcg-routing',
    'source_sha': source_sha,
    'baseline_record': str(BASELINE_RECORD),
    'status': 'not_run',
    'cases': {},
    'routing_failures': [],
    'metadata_failures': [],
    'numerical_failures': [],
}

try:
    baseline = json.loads(BASELINE_RECORD.read_text())
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
    run([
        'cargo', 'doc', '--no-deps', '--document-private-items', '--all-features',
    ], env=doc_env)
    run(['cargo', 'test', '--all-targets', '--all-features'])
    run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
    run(['cargo', 'build', '--release', '--all-features'])
    run([
        'cargo', 'build', '--release',
        '--manifest-path', 'benchmarks/Cargo.toml',
        '--bin', 'full-pcg-routing',
    ])

    binary = Path('benchmarks/target/release/full-pcg-routing')
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
    stable_keys = (
        'case', 'scale', 'vertices', 'input_edges', 'edges', 'levels',
        'operators', 'plan_bytes', 'workspace_bytes',
    )
    speedups = []
    planned_speedups = []
    maximum_difference = 0.0

    for name, case_name, scale, expected_execution in specs:
        observation = sample(binary, case_name, scale, 3, 4, name)
        observation['expected_execution'] = expected_execution
        observation['route_matches_expected'] = (
            observation['auto_execution'] == expected_execution
        )
        if not observation['route_matches_expected']:
            result['routing_failures'].append({
                'case': name,
                'expected': expected_execution,
                'observed': observation['auto_execution'],
            })

        baseline_case = baseline['cases'].get(name)
        if baseline_case is None:
            result['metadata_failures'].append({
                'case': name,
                'reason': 'case missing from baseline record',
            })
        else:
            for key in stable_keys:
                if observation.get(key) != baseline_case.get(key):
                    result['metadata_failures'].append({
                        'case': name,
                        'field': key,
                        'baseline': baseline_case.get(key),
                        'observed': observation.get(key),
                    })
            for key in ('serial_iterations', 'planned_iterations'):
                if observation.get(key) != baseline_case.get(key):
                    result['numerical_failures'].append({
                        'case': name,
                        'field': key,
                        'baseline': baseline_case.get(key),
                        'observed': observation.get(key),
                    })

        finite_fields = (
            'serial_backward_error', 'planned_backward_error',
            'serial_residual_norm', 'planned_residual_norm',
            'max_scaled_difference', 'speedup',
        )
        for key in finite_fields:
            value = observation[key]
            if not math.isfinite(value):
                result['numerical_failures'].append({
                    'case': name,
                    'field': key,
                    'reason': 'non-finite value',
                    'observed': value,
                })
        if observation['serial_iterations'] != observation['planned_iterations']:
            result['numerical_failures'].append({
                'case': name,
                'field': 'iterations',
                'serial': observation['serial_iterations'],
                'planned': observation['planned_iterations'],
            })
        if max(
            observation['serial_backward_error'],
            observation['planned_backward_error'],
        ) > 1.1e-8:
            result['numerical_failures'].append({
                'case': name,
                'field': 'backward_error',
                'serial': observation['serial_backward_error'],
                'planned': observation['planned_backward_error'],
            })
        if observation['max_scaled_difference'] > 1.0e-8:
            result['numerical_failures'].append({
                'case': name,
                'field': 'max_scaled_difference',
                'observed': observation['max_scaled_difference'],
            })

        result['cases'][name] = observation
        speedups.append(observation['speedup'])
        if expected_execution == 'Planned':
            planned_speedups.append(observation['speedup'])
        maximum_difference = max(
            maximum_difference,
            observation['max_scaled_difference'],
        )

    result['geometric_speedup_all'] = math.exp(
        sum(math.log(value) for value in speedups) / len(speedups)
    )
    result['geometric_speedup_planned_cases'] = math.exp(
        sum(math.log(value) for value in planned_speedups) / len(planned_speedups)
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
    print(f'post-lean routing qualification failed: {error}', flush=True)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

status_path = STATUS
status = status_path.read_text().rstrip()
heading = '## Post-lean-forest routing requalification\n'
block = f'''{heading}
- Status: `{result['status']}`.
- Source SHA: `{source_sha}`.
- Routing failures: `{len(result['routing_failures'])}`.
- Metadata failures: `{len(result['metadata_failures'])}`.
- Numerical failures: `{len(result['numerical_failures'])}`.
- Planned-case geometric speedup: `{result.get('geometric_speedup_planned_cases', 'n/a')}`.
- Maximum scaled serial/planned solution difference: `{result.get('maximum_scaled_difference', 'n/a')}`.
- Evidence: `.ci/performance/post-lean-forest-routing.json`.
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

plan_path = PLAN
plan = plan_path.read_text()
marker = '## Current next action\n'
checkpoint_heading = '### Post-lean-forest routing requalification — 2026-08-23\n'
checkpoint = f'''{checkpoint_heading}
- Status: `{result['status']}`.
- Routing / metadata / numerical failures: `{len(result['routing_failures'])}` / `{len(result['metadata_failures'])}` / `{len(result['numerical_failures'])}`.
- Planned-case geometric speedup: `{result.get('geometric_speedup_planned_cases', 'n/a')}`.
- Evidence: `.ci/performance/post-lean-forest-routing.json`.

'''
if checkpoint_heading not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
    prefix, _ = plan.split(marker, 1)
    plan = prefix + marker + '''
1. Profile hierarchy forest subphases after the retained lean construction path.
2. Refresh cumulative large-graph peak-memory guidance.
3. Continue sort-dominant contraction work only with a design that clears both speed and peak-memory gates.
4. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
plan_path.write_text(plan)

SCRIPT.unlink(missing_ok=True)
WORKFLOW.unlink(missing_ok=True)

if result['status'] != 'success':
    raise SystemExit('post-lean routing qualification failed; see machine-readable record')
