import subprocess

SOURCE_COMMIT = '371369a7844b626a24bbde48df2c58aa4d4dabc1'
SOURCE_PATH = 'scripts/owned_trusted_split_gate.py'

text = subprocess.check_output(
    ['git', 'show', f'{SOURCE_COMMIT}:{SOURCE_PATH}'],
    text=True,
)

for old, new in (
    ('owned_trusted_split_gate.py', 'owned_trusted_split_gate_v2.py'),
    ('owned-trusted-split-gate.yml', 'owned-trusted-split-gate-v2.yml'),
):
    text = text.replace(old, new)

old_import_patch = '''        ''' + "'''" + '''        forest_component_labels_trusted, forest_components, split_forest,
        split_forest_trusted,
''' + "'''" + ''',
        ''' + "'''" + '''        forest_component_labels_trusted, forest_components, split_forest,
        split_forest_trusted_owned,
''' + "'''" + ''',
        'trusted test import',
'''
new_import_patch = '''        ''' + "'''" + '''        forest_component_labels_trusted, forest_components, split_forest, split_forest_trusted,
''' + "'''" + ''',
        ''' + "'''" + '''        forest_component_labels_trusted, forest_components, split_forest,
        split_forest_trusted_owned,
''' + "'''" + ''',
        'trusted test import',
'''
if text.count(old_import_patch) != 1:
    raise SystemExit('historical trusted test import patch changed unexpectedly')
text = text.replace(old_import_patch, new_import_patch, 1)

required = (
    'owned_trusted_split_gate_v2.py',
    'owned-trusted-split-gate-v2.yml',
    'split_forest_trusted_owned(parent.clone())',
    'forest_component_labels_trusted, forest_components, split_forest, split_forest_trusted,',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f'repaired owned-split gate missing marker: {marker}')

exec(
    compile(text, 'scripts/owned_trusted_split_gate_v2.py', 'exec'),
    {'__name__': '__main__'},
)
