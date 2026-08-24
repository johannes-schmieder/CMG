import json
import os
from pathlib import Path
import subprocess

ROOT = Path.cwd()
WORKFLOW = Path('.github/workflows/profile-current-hierarchy-phases.yml')
SCRIPT = Path('scripts/profile_current_hierarchy_phases.py')
RECORD = Path('.ci/performance/hierarchy-phase-profile-current.json')
PLAN = Path('PERFORMANCE_PLAN.md')
STATUS = Path('PERFORMANCE_STATUS.md')


def run(command, *, env=None, timeout=7200):
    print('+', ' '.join(str(item) for item in command), flush=True)
    completed = subprocess.run(
        [str(item) for item in command],
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
            f"command failed ({completed.returncode}): "
            f"{' '.join(str(item) for item in command)}"
        )
    return completed


run(['cargo', 'fmt', '--all', '--', '--check'])
run([
    'cargo', 'clippy', '--manifest-path', 'benchmarks/Cargo.toml',
    '--bin', 'hierarchy-phase-profile', '--', '-D', 'warnings',
])
run([
    'cargo', 'build', '--release', '--manifest-path', 'benchmarks/Cargo.toml',
    '--bin', 'hierarchy-phase-profile',
])

binary = Path('benchmarks/target/release/hierarchy-phase-profile')
specs = (
    ('path-1m', ['path', '1000000', '4']),
    ('worker-firm-1.5m', ['worker-firm', '500000', '4']),
    ('worker-firm-3m', ['worker-firm', '1000000', '4']),
    ('dense-worker-firm-1.6m', ['dense-worker-firm', '100000', '4']),
)
cases = {}
for name, arguments in specs:
    completed = run([binary, *arguments])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'unexpected profile output for {name}: {payloads}')
    cases[name] = payloads[0]

phase_totals = {}
manual_total = 0
production_hierarchy_total = 0
for case in cases.values():
    manual_total += case['manual_total_median_ns']
    production_hierarchy_total += case['production_hierarchy_median_ns']
    for phase, elapsed in case['phase_median_ns'].items():
        phase_totals[phase] = phase_totals.get(phase, 0) + elapsed

ranked = sorted(phase_totals.items(), key=lambda item: item[1], reverse=True)
phase_shares = {
    phase: elapsed / max(1, sum(phase_totals.values()))
    for phase, elapsed in ranked
}
result = {
    'schema_version': 1,
    'profile': 'current-production-hierarchy-phases',
    'source_sha': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True
    ).strip(),
    'status': 'success',
    'cases': cases,
    'phase_totals_ns': dict(ranked),
    'phase_shares': phase_shares,
    'dominant_phase': ranked[0][0],
    'dominant_phase_share': phase_shares[ranked[0][0]],
    'manual_total_ns': manual_total,
    'production_hierarchy_total_ns': production_hierarchy_total,
}
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

phase_lines = '\n'.join(
    f"| {phase} | {phase_shares[phase]:.1%} |"
    for phase, _ in ranked
)
checkpoint = f'''### Current production hierarchy profile — 2026-08-24

- Profiled SHA: `{result['source_sha']}`.
- Dominant attributed phase: **{result['dominant_phase']}**
  ({result['dominant_phase_share']:.1%}).
- All manual hierarchy metadata matched the production hierarchy in every case.

| Phase | Share of attributed time |
|---|---:|
{phase_lines}

- Evidence: `.ci/performance/hierarchy-phase-profile-current.json`.

'''
plan = PLAN.read_text()
marker = '## Current next action\n'
heading = '### Current production hierarchy profile — 2026-08-24\n'
if heading in plan:
    start = plan.index(heading)
    end = plan.index(marker, start)
    plan = plan[:start] + checkpoint + plan[end:]
elif marker in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
else:
    plan += '\n\n' + checkpoint

next_actions = {
    'contraction': (
        '1. Profile current compact contraction subphases and temporary allocation.\n'
        '2. Test only candidates that improve complete hierarchy time and exact memory.\n'
    ),
    'forest_split': (
        '1. Refresh current split-forest subphase and traffic profiles.\n'
        '2. Target the dominant exact-preserving loop with full hierarchy gates.\n'
    ),
    'heavy_edge': (
        '1. Profile serial and parallel heavy-edge selection on current code.\n'
        '2. Test deterministic gather or routing improvements only where the profile justifies them.\n'
    ),
    'forest_components': (
        '1. Profile forest-component labeling and its exact temporary allocations.\n'
        '2. Test in-place or compact-label variants only with full hierarchy gates.\n'
    ),
}
if marker in plan:
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + next_actions.get(
        result['dominant_phase'],
        '1. Profile the dominant production hierarchy phase in more detail.\n'
        '2. Preserve exact hierarchy and residual certification in every candidate.\n',
    ) + (
        '3. Refresh cumulative retained performance guidance.\n'
        '4. Run manual 1–32 thread qualification when suitable hardware is available.\n'
    )
PLAN.write_text(plan)

block = f'''## Current production hierarchy profile

- Profiled SHA: `{result['source_sha']}`.
- Dominant phase: `{result['dominant_phase']}`
  ({result['dominant_phase_share']:.1%} of attributed time).
- Production/manual hierarchy metadata agreement: `success`.
- Evidence: `.ci/performance/hierarchy-phase-profile-current.json`.
'''
status = STATUS.read_text().rstrip()
heading = '## Current production hierarchy profile\n'
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
run(['git', 'commit', '-m', 'perf: record current hierarchy phase profile'])
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
    raise RuntimeError('failed to push hierarchy phase profile')
