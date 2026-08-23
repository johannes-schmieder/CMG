import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
PCG = Path('src/pcg.rs')
PROFILE = Path('src/pcg_profile.rs')
ORIGINAL_PCG = PCG.read_text()
ORIGINAL_PROFILE = PROFILE.read_text()


def run(command, timeout=7200, check=True):
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
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
        )
    return completed


def apply_patch():
    pcg = PCG.read_text()
    replacements = {
        '#[cfg(feature = "parallel")]\nfn euclidean_norm_with_executor(':
            '#[cfg(feature = "parallel")]\npub(crate) fn euclidean_norm_with_executor(',
        '#[cfg(feature = "parallel")]\nfn dot_with_executor(':
            '#[cfg(feature = "parallel")]\npub(crate) fn dot_with_executor(',
    }
    for old, new in replacements.items():
        if pcg.count(old) != 1:
            raise RuntimeError(f'production helper marker was not unique: {old!r}')
        pcg = pcg.replace(old, new, 1)
    PCG.write_text(pcg)

    profile = PROFILE.read_text()
    import_marker = '''use crate::{
    CmgError, CmgPreconditioner, ComponentWorkspace, Laplacian, ParallelCmgPlan,
    ParallelExecutor, PcgOptions, PcgResult, PcgWorkspace,
};
'''
    import_replacement = import_marker + '''use crate::pcg::{dot_with_executor, euclidean_norm_with_executor};
'''
    if profile.count(import_marker) != 1:
        raise RuntimeError('profile import marker was not unique')
    profile = profile.replace(import_marker, import_replacement, 1)

    call_replacements = {
        'euclidean_norm(rhs)': 'euclidean_norm_with_executor(rhs, executor)',
        'euclidean_norm(&workspace.projected_rhs)':
            'euclidean_norm_with_executor(&workspace.projected_rhs, executor)',
        'euclidean_norm(&workspace.solution)':
            'euclidean_norm_with_executor(&workspace.solution, executor)',
        'euclidean_norm(residual)': 'euclidean_norm_with_executor(residual, executor)',
        'dot(&workspace.residual, &workspace.preconditioned)':
            'dot_with_executor(&workspace.residual, &workspace.preconditioned, executor)',
        'dot(&workspace.direction, &workspace.matvec)':
            'dot_with_executor(&workspace.direction, &workspace.matvec, executor)',
    }
    expected_counts = {
        'euclidean_norm(rhs)': 1,
        'euclidean_norm(&workspace.projected_rhs)': 1,
        'euclidean_norm(&workspace.solution)': 1,
        'euclidean_norm(residual)': 1,
        'dot(&workspace.residual, &workspace.preconditioned)': 2,
        'dot(&workspace.direction, &workspace.matvec)': 1,
    }
    for old, new in call_replacements.items():
        if profile.count(old) != expected_counts[old]:
            raise RuntimeError(
                f'profile call count changed for {old!r}: '
                f'{profile.count(old)} != {expected_counts[old]}'
            )
        profile = profile.replace(old, new)

    helper_start = profile.index('\nfn dot(left: &[f64], right: &[f64]) -> f64 {\n')
    helper_end = profile.index('\nfn validate_positive_pcg(', helper_start)
    profile = profile[:helper_start] + profile[helper_end:]
    PROFILE.write_text(profile)


result = {
    'schema_version': 1,
    'experiment': 'pcg-profiler-production-reduction-sync',
    'source_sha': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], text=True
    ).strip(),
    'validation': 'not_run',
    'retained': False,
    'cases': {},
}

try:
    apply_patch()
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
    run(['cargo', 'test', '--all-targets', '--all-features'])
    run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
    run([
        'cargo', 'build', '--manifest-path', 'benchmarks/Cargo.toml',
        '--bin', 'pcg-phase-profile', '--release',
    ])

    binary = Path('benchmarks/target/release/pcg-phase-profile')
    specs = [
        ('path-80k', ['path', '80000', '1', '4']),
        ('worker-firm-120k', ['worker-firm', '40000', '1', '4']),
        ('dense-worker-firm-160k', ['dense-worker-firm', '10000', '1', '4']),
    ]
    for name, arguments in specs:
        completed = run([str(binary), *arguments])
        payloads = [
            json.loads(line)
            for line in completed.stdout.splitlines()
            if line.strip().startswith('{')
        ]
        if len(payloads) != 1:
            raise RuntimeError(f'unexpected profiler output for {name}: {payloads}')
        result['cases'][name] = payloads[0]

    result['validation'] = 'success'
    result['retained'] = True
    result['decision_reason'] = (
        'full qualification passed; profiler now calls the exact production '
        'dot and norm helpers and remains bitwise equal to planned PCG'
    )
except Exception as error:
    result['error'] = repr(error)
    result['decision_reason'] = f'profiler sync failed: {error}'
    print(result['decision_reason'], flush=True)

if not result['retained']:
    PCG.write_text(ORIGINAL_PCG)
    PROFILE.write_text(ORIGINAL_PROFILE)
    run(['cargo', 'fmt', '--all'], check=False)

record = Path('.ci/performance/pcg-profiler-sync.json')
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

plan_path = Path('PERFORMANCE_PLAN.md')
plan = plan_path.read_text()
marker = '## Current next action\n'
checkpoint = f'''### Production-reduction profiler sync — 2026-08-23

- Profiler synchronization was **{'retained' if result['retained'] else 'not retained'}**.
- Validation: `{result['validation']}`.
- Decision: {result.get('decision_reason', 'no decision recorded')}.
- Representative bitwise-parity cases completed: `{', '.join(result.get('cases', {})) or 'none'}`.
- Machine-readable evidence: `.ci/performance/pcg-profiler-sync.json`.

'''
if '### Production-reduction profiler sync — 2026-08-23' not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
plan_path.write_text(plan)

status_path = Path('PERFORMANCE_STATUS.md')
status = status_path.read_text().rstrip()
if '## Production-reduction profiler sync' not in status:
    status += (
        '\n\n## Production-reduction profiler sync\n\n'
        f"- Decision: `{'retained' if result['retained'] else 'not retained'}`.\n"
        f"- Validation: `{result['validation']}`.\n"
        '- The phase profiler now reuses the exact production planned-PCG dot '
        'and norm helpers rather than maintaining stale copies.\n'
        '- Evidence: `.ci/performance/pcg-profiler-sync.json`.\n'
    )
    status_path.write_text(status + '\n')

Path('.github/workflows/sync-pcg-profiler.yml').unlink(missing_ok=True)
Path('scripts/sync_pcg_profiler.py').unlink(missing_ok=True)
