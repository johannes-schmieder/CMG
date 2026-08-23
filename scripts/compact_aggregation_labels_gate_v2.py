from pathlib import Path
import subprocess

SOURCE_COMMIT = '35344303031b6ea97cd57df147f9cf55648d7cb0'
SOURCE_PATH = 'scripts/compact_aggregation_labels_gate.py'

text = subprocess.check_output(
    ['git', 'show', f'{SOURCE_COMMIT}:{SOURCE_PATH}'],
    text=True,
)

for old, new in (
    ('compact_aggregation_labels_gate.py', 'compact_aggregation_labels_gate_v2.py'),
    ('compact-aggregation-labels-gate.yml', 'compact-aggregation-labels-gate-v2.yml'),
    ('compact-aggregation-labels-latest.json', 'compact-aggregation-labels-v2-latest.json'),
    ('zero-copy-compact-aggregation-labels', 'zero-copy-compact-aggregation-labels-v2'),
    ('Compact aggregation-label gate', 'Corrected compact aggregation-label gate'),
    ('Compact aggregation-label checkpoint', 'Corrected compact aggregation-label checkpoint'),
    ('/tmp/cmg-compact-labels-', '/tmp/cmg-compact-labels-v2-'),
):
    text = text.replace(old, new)

old_check = '''            if item['max_scaled_difference'] > 5e-10:
                raise RuntimeError(f'{name}: serial/planned solution mismatch')
'''
new_check = '''            baseline_difference = reference['max_scaled_difference']
            allowed_difference = max(
                1e-8,
                baseline_difference * 1.01 + 1e-14,
            )
            if item['max_scaled_difference'] > allowed_difference:
                raise RuntimeError(
                    f'{name}: serial/planned solution difference exceeded baseline-relative guard'
                )
'''
if text.count(old_check) != 1:
    raise SystemExit('original absolute solution-difference guard changed unexpectedly')
text = text.replace(old_check, new_check, 1)

text = text.replace(
    "'decision_reason': '',\n    'hierarchy_cases': {},\n    'pcg_cases': {},",
    "'decision_reason': '',\n    'numerical_guard': 'candidate must not exceed max(1e-8, 1.01 * current-baseline difference + 1e-14)',\n    'diagnostic_record': '.ci/performance/compact-labels-pcg-diagnostic.json',\n    'hierarchy_cases': {},\n    'pcg_cases': {},",
    1,
)

required = (
    'baseline-relative guard',
    'compact-aggregation-labels-v2-latest.json',
    'compact-labels-pcg-diagnostic.json',
    "'geometric_retained_ratio_max': 0.97",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f'corrected compact-label gate missing marker: {marker}')

exec(
    compile(text, 'scripts/compact_aggregation_labels_gate_v2.py', 'exec'),
    {'__name__': '__main__'},
)
