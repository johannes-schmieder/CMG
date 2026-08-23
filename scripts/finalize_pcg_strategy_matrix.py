"""Finalize the completed PCG strategy matrix and select a bounded heuristic."""

from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess

RESULT = Path('.ci/performance/pcg-strategy-matrix.json')
PLAN = Path('PERFORMANCE_PLAN.md')
WORKFLOW = Path('.github/workflows/finalize-pcg-strategy-matrix.yml')
SCRIPT = Path('scripts/finalize_pcg_strategy_matrix.py')

payload = json.loads(RESULT.read_text())
cases = payload.get('cases', {})
if len(cases) != 22:
    raise SystemExit(f'expected 22 completed matrix cases, found {len(cases)}')

for name, case in cases.items():
    difference = max(
        case.get('max_across_difference', float('inf')),
        case.get('max_planned_difference', float('inf')),
    )
    if difference > 5.0e-9:
        raise SystemExit(f'{name} numerical difference exceeded gate: {difference}')


def best_time(case):
    return min(case['serial_ns'], case['across_rhs_ns'], case['planned_ns'])


def strategy_time(case, strategy):
    return {
        'serial': case['serial_ns'],
        'across_rhs': case['across_rhs_ns'],
        'planned': case['planned_ns'],
    }[strategy]


def evaluate(threshold):
    ratios = []
    decisions = {}
    for name, case in cases.items():
        if case['rhs_count'] == 1:
            strategy = (
                'planned'
                if case['operators'] > 0 and case['edges'] >= threshold
                else 'serial'
            )
        elif case['concurrency'] >= 2:
            strategy = 'across_rhs'
        elif case['operators'] > 0 and case['edges'] >= threshold:
            strategy = 'planned'
        else:
            strategy = 'serial'
        ratio = strategy_time(case, strategy) / best_time(case)
        ratios.append(ratio)
        decisions[name] = {'strategy': strategy, 'over_best': ratio}
    return {
        'single_rhs_planned_edge_threshold': threshold,
        'geometric_over_best': math.exp(
            sum(math.log(value) for value in ratios) / len(ratios)
        ),
        'maximum_over_best': max(ratios),
        'decisions': decisions,
    }

threshold_candidates = sorted(
    {0, 1, *(case['edges'] for case in cases.values())}
    | {case['edges'] + 1 for case in cases.values()}
)
policies = [evaluate(threshold) for threshold in threshold_candidates]
policies.sort(
    key=lambda policy: (
        policy['maximum_over_best'],
        policy['geometric_over_best'],
        policy['single_rhs_planned_edge_threshold'],
    )
)
best_policy = policies[0]

existing_ratios = [case['auto_over_best'] for case in cases.values()]
payload.pop('error', None)
payload['status'] = 'success'
payload['completed_case_count'] = len(cases)
payload['maximum_scaled_difference'] = max(
    max(case['max_across_difference'], case['max_planned_difference'])
    for case in cases.values()
)
payload['provisional_policy'] = {
    'geometric_over_best': math.exp(
        sum(math.log(value) for value in existing_ratios) / len(existing_ratios)
    ),
    'maximum_over_best': max(existing_ratios),
}
payload['threshold_search'] = {
    'candidate_count': len(policies),
    'best': best_policy,
    'top_five': [
        {
            key: value
            for key, value in policy.items()
            if key != 'decisions'
        }
        for policy in policies[:5]
    ],
}
payload['strategy_wins'] = {
    strategy: sum(
        strategy_time(case, strategy) == best_time(case)
        for case in cases.values()
    )
    for strategy in ('serial', 'across_rhs', 'planned')
}
payload['finalized_from_sha'] = subprocess.check_output(
    ['git', 'rev-parse', 'HEAD'], text=True
).strip()
RESULT.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')

if PLAN.exists():
    text = PLAN.read_text()
    old = '''### PCG strategy-matrix checkpoint — 2026-08-23

- Benchmark-only strategy matrix status: `failure`.
- Cases compare serial sequential, across-RHS parallel, and planned within-solve PCG.
- The simple provisional auto policy had geometric/worst auto-to-best ratios of
  `nanx` and
  `1.079x`.
- No production routing was changed by this checkpoint.
- Machine-readable evidence:
  `.ci/performance/pcg-strategy-matrix.json`.

'''
    replacement = f'''### PCG strategy-matrix checkpoint — 2026-08-23

- Benchmark-only strategy matrix status: `success` across 22 cases.
- Cases compare serial sequential, across-RHS parallel, and planned within-solve PCG.
- The provisional policy had geometric/worst auto-to-best ratios of
  `{payload['provisional_policy']['geometric_over_best']:.3f}x` and
  `{payload['provisional_policy']['maximum_over_best']:.3f}x`.
- Exhaustive observed-threshold search selected
  `{best_policy['single_rhs_planned_edge_threshold']}` original edges for
  single-RHS planned execution, with geometric/worst ratios of
  `{best_policy['geometric_over_best']:.3f}x` and
  `{best_policy['maximum_over_best']:.3f}x`.
- No production routing was changed by this checkpoint.
- Machine-readable evidence:
  `.ci/performance/pcg-strategy-matrix.json`.

'''
    if old in text:
        text = text.replace(old, replacement, 1)
    elif '### PCG strategy-matrix checkpoint — 2026-08-23' not in text:
        marker = '## Current next action\n'
        if marker not in text:
            raise SystemExit('PERFORMANCE_PLAN current-next-action heading missing')
        text = text.replace(marker, replacement + marker, 1)
    PLAN.write_text(text)

WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
subprocess.run(['git', 'config', 'user.name', 'github-actions[bot]'], check=True)
subprocess.run(
    [
        'git',
        'config',
        'user.email',
        '41898282+github-actions[bot]@users.noreply.github.com',
    ],
    check=True,
)
subprocess.run(['git', 'add', '-A'], check=True)
subprocess.run(
    ['git', 'commit', '-m', 'perf: finalize PCG strategy matrix'],
    check=True,
)
subprocess.run(['git', 'pull', '--rebase', 'origin', 'main'], check=True)
subprocess.run(['git', 'push', 'origin', 'HEAD:main'], check=True)
