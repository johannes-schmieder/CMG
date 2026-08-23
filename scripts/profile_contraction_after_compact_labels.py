import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
RECORD = Path('.ci/performance/contraction-subphase-post-labels.json')
SCRIPT = Path('scripts/profile_contraction_after_compact_labels.py')
WORKFLOW = Path('.github/workflows/profile-contraction-after-compact-labels.yml')
STATUS = Path('PERFORMANCE_STATUS.md')
PLAN = Path('PERFORMANCE_PLAN.md')


def run(command, *, timeout=12000):
    command = [str(value) for value in command]
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


source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
result = {
    'schema_version': 1,
    'experiment': 'contraction-subphase-profile-after-compact-labels',
    'source_sha': source_sha,
    'status': 'not_run',
    'cases': {},
    'phase_totals_ns': {},
    'phase_shares': {},
    'mapping_note': (
        'manual mapping uses the public lazy native-label compatibility slice; '
        'production_total_ns uses the retained compact-label contraction path'
    ),
}

try:
    run(['cargo', 'fmt', '--all', '--', '--check'])
    run([
        'cargo', 'fmt', '--manifest-path', 'benchmarks/Cargo.toml',
        '--all', '--', '--check',
    ])
    run([
        'cargo', 'clippy', '--manifest-path', 'benchmarks/Cargo.toml',
        '--all-targets', '--', '-D', 'warnings',
    ])
    run([
        'cargo', 'build', '--release',
        '--manifest-path', 'benchmarks/Cargo.toml',
        '--bin', 'contraction-subphase-profile',
    ])

    binary = Path('benchmarks/target/release/contraction-subphase-profile')
    specs = (
        ('path-1m', 'path', 1_000_000, 7),
        ('worker-firm-1.5m', 'worker-firm', 500_000, 7),
        ('worker-firm-3m', 'worker-firm', 1_000_000, 7),
        ('dense-worker-firm-1.6m', 'dense-worker-firm', 100_000, 7),
    )
    phases = ('mapping', 'sorting', 'merging', 'diagonal', 'finalize')
    totals = {phase: 0 for phase in phases}
    production_total = 0

    for name, case_name, scale, repetitions in specs:
        completed = run([
            binary, case_name, scale, repetitions, 'comparison',
        ])
        payloads = [
            json.loads(line)
            for line in completed.stdout.splitlines()
            if line.strip().startswith('{')
        ]
        case_rows = [row for row in payloads if row.get('record') == 'case']
        level_rows = [row for row in payloads if row.get('record') == 'level']
        if len(case_rows) != 1 or not level_rows:
            raise RuntimeError(
                f'{name}: unexpected output ({len(case_rows)} case rows, '
                f'{len(level_rows)} level rows)'
            )
        summary = case_rows[0]
        if summary['profiled_levels'] != len(level_rows):
            raise RuntimeError(f'{name}: profiled-level count mismatch')
        result['cases'][name] = {'summary': summary, 'levels': level_rows}
        for phase in phases:
            totals[phase] += int(summary[f'{phase}_ns'])
        production_total += int(summary['production_total_ns'])

    manual_total = sum(totals.values())
    if manual_total <= 0 or production_total <= 0:
        raise RuntimeError('profile totals must be positive')
    shares = {phase: totals[phase] / manual_total for phase in phases}
    dominant = max(phases, key=totals.get)
    result.update({
        'status': 'success',
        'phase_totals_ns': totals,
        'phase_shares': shares,
        'manual_total_ns': manual_total,
        'production_total_ns': production_total,
        'manual_over_production': manual_total / production_total,
        'dominant_phase': dominant,
        'dominant_share': shares[dominant],
        'exact_equivalence': 'passed inside the benchmark for every profiled level and repetition',
    })
except Exception as error:
    result['status'] = 'failure'
    result['error'] = repr(error)
    print(f'post-compaction contraction profile failed: {error}', flush=True)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

dominant = result.get('dominant_phase', 'unresolved')
share = result.get('dominant_share')
share_text = 'n/a' if share is None else f'{share:.1%}'
status = STATUS.read_text().rstrip()
heading = '## Contraction profile after compact labels\n'
block = f'''{heading}
- Status: `{result['status']}`.
- Dominant attributed phase: `{dominant}` (`{share_text}`).
- Profiled cases: `{len(result.get('cases', {}))}`.
- Exact reconstructed coarse-graph equivalence: `{result.get('exact_equivalence', 'not established')}`.
- Manual mapping uses the public compatibility label view; production totals use compact labels.
- Evidence: `.ci/performance/contraction-subphase-post-labels.json`.
'''
if heading in status:
    start = status.index(heading)
    end = status.find('\n## ', start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + block + status[end:]
else:
    status += '\n\n' + block
STATUS.write_text(status.rstrip() + '\n')

plan = PLAN.read_text()
marker = '## Current next action\n'
checkpoint_heading = '### Contraction profile after compact labels — 2026-08-23\n'
checkpoint = f'''{checkpoint_heading}
- Status: `{result['status']}`.
- Dominant phase: `{dominant}` (`{share_text}`).
- Evidence: `.ci/performance/contraction-subphase-post-labels.json`.

'''
if checkpoint_heading not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
    prefix, _ = plan.split(marker, 1)
    plan = prefix + marker + f'''
1. Benchmark-gate one narrowly scoped `{dominant}` or hierarchy-metadata optimization selected from the new profile.
2. Preserve exact coarse-graph ordering, compensated duplicate summation, routing metadata, and certified solves.
3. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
PLAN.write_text(plan)

SCRIPT.unlink(missing_ok=True)
WORKFLOW.unlink(missing_ok=True)

if result['status'] != 'success':
    raise SystemExit('post-compaction contraction profile failed')
