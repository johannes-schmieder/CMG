import json
import os
from pathlib import Path
import subprocess

ROOT = Path.cwd()


def run(command, timeout=4200):
    print('+', ' '.join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
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


result = {
    'schema_version': 1,
    'experiment': 'pcg-phase-profile-post-centering',
    'source_sha': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], text=True
    ).strip(),
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'status': 'not_run',
    'cases': {},
}

try:
    run([
        'cargo', 'build', '--manifest-path', 'benchmarks/Cargo.toml',
        '--bin', 'pcg-phase-profile', '--release',
    ])
    binary = Path('benchmarks/target/release/pcg-phase-profile')
    specs = [
        ('path-150k', ['path', '150000', '2', '4']),
        ('worker-firm-300k', ['worker-firm', '100000', '2', '4']),
        ('dense-worker-firm-400k', ['dense-worker-firm', '25000', '2', '4']),
    ]
    for name, arguments in specs:
        completed = run([str(binary), *arguments])
        payloads = [
            json.loads(line)
            for line in completed.stdout.splitlines()
            if line.strip().startswith('{')
        ]
        if len(payloads) != 1:
            raise RuntimeError(
                f'unexpected profiler output for {name}: {payloads}'
            )
        result['cases'][name] = payloads[0]
    result['maximum_centering_share'] = max(
        case['phases']['centering']['share']
        for case in result['cases'].values()
    )
    result['maximum_norm_share'] = max(
        case['phases']['norms']['share']
        for case in result['cases'].values()
    )
    result['maximum_dot_product_share'] = max(
        case['phases']['dot_products']['share']
        for case in result['cases'].values()
    )
    result['maximum_vector_update_share'] = max(
        case['phases']['vector_updates']['share']
        for case in result['cases'].values()
    )
    result['status'] = 'success'
except Exception as error:
    result['status'] = 'failure'
    result['error'] = repr(error)
    print(f'post-centering profile failed: {error}', flush=True)

record = Path('.ci/performance/pcg-phase-profile-post-centering.json')
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

plan_path = Path('PERFORMANCE_PLAN.md')
plan = plan_path.read_text()
marker = '## Current next action\n'
if result['status'] == 'success':
    rows = []
    for name, case in result['cases'].items():
        phases = case['phases']
        rows.append(
            f"| {name} | {phases['centering']['share']:.1%} | "
            f"{phases['norms']['share']:.1%} | "
            f"{phases['dot_products']['share']:.1%} | "
            f"{phases['vector_updates']['share']:.1%} | "
            f"{phases['preconditioner']['share']:.1%} |"
        )
    checkpoint = '''### Post-centering PCG profile — 2026-08-23

| Case | Centering | Norms | Dot products | Vector updates | Preconditioner |
|---|---:|---:|---:|---:|---:|
''' + '\n'.join(rows) + '''

- Machine-readable evidence: `.ci/performance/pcg-phase-profile-post-centering.json`.

'''
else:
    checkpoint = f'''### Post-centering PCG profile — 2026-08-23

- Status: **failure**; `{result.get('error')}`.
- Machine-readable evidence: `.ci/performance/pcg-phase-profile-post-centering.json`.

'''
if '### Post-centering PCG profile — 2026-08-23' not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
plan_path.write_text(plan)

Path('.github/workflows/post-centering-profile.yml').unlink(missing_ok=True)
Path('scripts/run_post_centering_profile.py').unlink(missing_ok=True)
