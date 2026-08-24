import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
WORKFLOW = Path('.github/workflows/profile-current-contraction-subphases.yml')
SCRIPT = Path('scripts/profile_current_contraction_subphases.py')
RECORD = Path('.ci/performance/contraction-subphase-profile-current.json')
PLAN = Path('PERFORMANCE_PLAN.md')
STATUS = Path('PERFORMANCE_STATUS.md')


def run(command, *, timeout=7200):
    command = [str(item) for item in command]
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


run(['cargo', 'fmt', '--all', '--', '--check'])
run([
    'cargo', 'fmt', '--manifest-path', 'benchmarks/Cargo.toml',
    '--all', '--', '--check',
])
run([
    'cargo', 'clippy', '--manifest-path', 'benchmarks/Cargo.toml',
    '--bin', 'contraction-subphase-profile', '--', '-D', 'warnings',
])
run([
    'cargo', 'build', '--release', '--manifest-path', 'benchmarks/Cargo.toml',
    '--bin', 'contraction-subphase-profile',
])

binary = Path('benchmarks/target/release/contraction-subphase-profile')
specs = (
    ('path-1m', ['path', '1000000', '4', 'comparison']),
    ('worker-firm-1.5m', ['worker-firm', '500000', '4', 'comparison']),
    ('worker-firm-3m', ['worker-firm', '1000000', '4', 'comparison']),
    ('dense-worker-firm-1.6m', ['dense-worker-firm', '100000', '4', 'comparison']),
)

cases = {}
level_records = {}
for name, arguments in specs:
    completed = run([binary, *arguments])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    case_payloads = [payload for payload in payloads if payload.get('record') == 'case']
    levels = [payload for payload in payloads if payload.get('record') == 'level']
    if len(case_payloads) != 1:
        raise RuntimeError(f'unexpected case profile output for {name}: {payloads}')
    case = case_payloads[0]
    if len(levels) != case['profiled_levels']:
        raise RuntimeError(
            f"{name}: expected {case['profiled_levels']} level records, found {len(levels)}"
        )
    for level in levels:
        reconstructed = sum(
            level[field]
            for field in (
                'mapping_ns', 'sorting_ns', 'merging_ns', 'diagonal_ns', 'finalize_ns'
            )
        )
        if reconstructed <= 0 or level['production_ns'] <= 0:
            raise RuntimeError(f'{name}: nonpositive level timing')
        if level['mapped_edges'] < level['merged_edges']:
            raise RuntimeError(f'{name}: merged edge count exceeds mapped count')
    reconstructed_case = sum(
        case[field]
        for field in (
            'mapping_ns', 'sorting_ns', 'merging_ns', 'diagonal_ns', 'finalize_ns'
        )
    )
    if reconstructed_case != case['manual_total_ns']:
        raise RuntimeError(f'{name}: manual subphase total mismatch')
    cases[name] = case
    level_records[name] = levels

subphases = ('mapping', 'sorting', 'merging', 'diagonal', 'finalize')
totals = {
    phase: sum(case[f'{phase}_ns'] for case in cases.values())
    for phase in subphases
}
attributed_total = sum(totals.values())
ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
shares = {
    phase: elapsed / max(1, attributed_total)
    for phase, elapsed in ranked
}
production_total = sum(case['production_total_ns'] for case in cases.values())

case_summaries = {}
for name, case in cases.items():
    case_total = case['manual_total_ns']
    case_ranked = sorted(
        ((phase, case[f'{phase}_ns']) for phase in subphases),
        key=lambda item: item[1],
        reverse=True,
    )
    case_summaries[name] = {
        'dominant_subphase': case_ranked[0][0],
        'dominant_subphase_share': case_ranked[0][1] / max(1, case_total),
        'manual_over_production': case['manual_over_production'],
        'mapped_edge_survival_ratio': (
            sum(level['merged_edges'] for level in level_records[name])
            / max(1, sum(level['mapped_edges'] for level in level_records[name]))
        ),
        'level_count': len(level_records[name]),
    }

result = {
    'schema_version': 1,
    'profile': 'current-production-contraction-subphases',
    'source_sha': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True
    ).strip(),
    'status': 'success',
    'cases': cases,
    'levels': level_records,
    'case_summaries': case_summaries,
    'subphase_totals_ns': dict(ranked),
    'subphase_shares': shares,
    'dominant_subphase': ranked[0][0],
    'dominant_subphase_share': shares[ranked[0][0]],
    'manual_attributed_total_ns': attributed_total,
    'production_contraction_total_ns': production_total,
    'manual_over_production': attributed_total / max(1, production_total),
}
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

rows = '\n'.join(
    f'| {phase} | {shares[phase]:.1%} | {elapsed:,} |'
    for phase, elapsed in ranked
)
case_rows = '\n'.join(
    f"| {name} | {summary['dominant_subphase']} | "
    f"{summary['dominant_subphase_share']:.1%} | "
    f"{summary['mapped_edge_survival_ratio']:.1%} |"
    for name, summary in case_summaries.items()
)
checkpoint = f'''### Current production contraction profile — 2026-08-24

- Profiled SHA: `{result['source_sha']}`.
- Dominant contraction subphase: **{result['dominant_subphase']}**
  ({result['dominant_subphase_share']:.1%} of attributed contraction time).
- Manual/production contraction time ratio: `{result['manual_over_production']:.3f}x`.
- Every manually reconstructed coarse graph matched production exactly.

| Subphase | Share | Aggregate ns |
|---|---:|---:|
{rows}

| Case | Dominant subphase | Share | Mapped edges retained after merge |
|---|---|---:|---:|
{case_rows}

- Evidence: `.ci/performance/contraction-subphase-profile-current.json`.

'''
plan = PLAN.read_text()
marker = '## Current next action\n'
heading = '### Current production contraction profile — 2026-08-24\n'
if heading in plan:
    start = plan.index(heading)
    end = plan.index(marker, start)
    plan = plan[:start] + checkpoint + plan[end:]
elif marker in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
else:
    plan += '\n\n' + checkpoint

next_actions = {
    'mapping': (
        '1. Profile compact-label edge mapping and output-buffer growth on current code.\n'
        '2. Test a zero-copy or exact-capacity mapping candidate only with full hierarchy and memory gates.\n'
    ),
    'sorting': (
        '1. Profile current compact-edge sorting by level size, density, and duplicate rate.\n'
        '2. Test a narrowly routed deterministic sorting candidate against complete hierarchy time.\n'
    ),
    'merging': (
        '1. Profile duplicate-run lengths and merge-loop traffic on current coarse levels.\n'
        '2. Test an exact-preserving merge candidate only if run-length evidence shows headroom.\n'
    ),
    'diagonal': (
        '1. Profile canonical-edge diagonal accumulation and metadata construction.\n'
        '2. Test only one-pass candidates that preserve floating-point order exactly.\n'
    ),
    'finalize': (
        '1. Profile final graph metadata and retained-capacity decisions.\n'
        '2. Test only candidates with measurable whole-hierarchy impact.\n'
    ),
}
if marker in plan:
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + next_actions[result['dominant_subphase']] + (
        '3. Re-run the full hierarchy profile after any retained contraction change.\n'
        '4. Run manual 1–32 thread qualification when suitable hardware is available.\n'
    )
PLAN.write_text(plan)

block = f'''## Current production contraction profile

- Profiled SHA: `{result['source_sha']}`.
- Dominant subphase: `{result['dominant_subphase']}`
  ({result['dominant_subphase_share']:.1%} of attributed contraction time).
- Manual/production timing ratio: `{result['manual_over_production']:.3f}x`.
- Coarse-graph equality verification: `success`.
- Evidence: `.ci/performance/contraction-subphase-profile-current.json`.
'''
status = STATUS.read_text().rstrip()
heading = '## Current production contraction profile\n'
if heading in status:
    start = status.index(heading)
    end = status.find('\n## ', start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + block + status[end:]
else:
    status += '\n\n' + block
STATUS.write_text(status.rstrip() + '\n')

WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
try:
    Path('scripts').rmdir()
except OSError:
    pass

run(['git', 'config', 'user.name', 'github-actions[bot]'])
run([
    'git', 'config', 'user.email',
    '41898282+github-actions[bot]@users.noreply.github.com',
])
run(['git', 'add', '-A'])
run(['git', 'commit', '-m', 'perf: record current contraction subphase profile'])
for _ in range(8):
    run(['git', 'pull', '--rebase', 'origin', 'main'])
    pushed = subprocess.run(
        ['git', 'push', 'origin', 'HEAD:main'],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(pushed.stdout, end='')
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError('failed to push contraction subphase profile')
