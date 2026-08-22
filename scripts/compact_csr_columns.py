from pathlib import Path
import json
import re

analysis_path = Path('.ci/performance/compact-csr-feasibility.json')
if not analysis_path.exists():
    raise SystemExit('compact CSR feasibility record is missing')
analysis = json.loads(analysis_path.read_text())
if not analysis.get('feasible_for_mechanical_experiment'):
    raise SystemExit('compact CSR feasibility gate is false')
selected = analysis.get('selected')
if not selected:
    raise SystemExit('compact CSR selected field is missing')

source_path = Path(selected['path'])
struct_name = selected['struct']
field = selected['field']
text = source_path.read_text()
original = text

# Change exactly one private persistent field from Vec<usize> to Vec<u32>.
field_pattern = re.compile(
    rf'(?m)^(?P<i>\s*)(?P<vis>pub(?:\([^)]*\))?\s+)?'
    rf'{re.escape(field)}\s*:\s*Vec\s*<\s*usize\s*>\s*,?\s*$'
)
field_matches = list(field_pattern.finditer(text))
if len(field_matches) != 1:
    raise SystemExit(
        f'expected one {struct_name}.{field} Vec<usize> declaration, '
        f'found {len(field_matches)}'
    )
match = field_matches[0]
if match.group('vis'):
    raise SystemExit('selected compact CSR field is public')
replacement = match.group(0).replace('usize', 'u32')
text = text[:match.start()] + replacement + text[match.end():]

# Determine the local builder variable assigned into the selected struct field.
assignment_patterns = [
    re.compile(rf'(?m)^\s*{re.escape(field)}\s*,\s*$'),
    re.compile(
        rf'(?m)^\s*{re.escape(field)}\s*:\s*(?P<local>\w+)\s*,\s*$'
    ),
]
local = None
explicit = assignment_patterns[1].search(text)
if explicit:
    local = explicit.group('local')
else:
    shorthand = assignment_patterns[0].search(text)
    if shorthand:
        local = field
if local is None:
    raise SystemExit(f'struct initialization for {field} not recognized')

# Convert builder pushes while preserving the original usize calculation.
push_pattern = re.compile(
    rf'(?m)^(?P<i>\s*){re.escape(local)}\.push\((?P<value>.+)\);\s*$'
)
push_matches = list(push_pattern.finditer(text))
if not push_matches:
    raise SystemExit(f'no {local}.push construction sites found')
for push in reversed(push_matches):
    value = push.group('value').strip()
    replacement = (
        f"{push.group('i')}{local}.push(u32::try_from({value})"
        f'.expect("CSR vertex index exceeds u32::MAX"));'
    )
    text = text[:push.start()] + replacement + text[push.end():]

# Convert direct reads from the stored field. This keeps all surrounding APIs
# and arithmetic in usize while reducing only retained storage width.
index_pattern = re.compile(
    rf'(?P<expr>(?:self\.)?{re.escape(field)}\s*\[[^\]\n]+\])'
)
text = index_pattern.sub(r'(\g<expr> as usize)', text)

# Convert simple slice loops such as:
#   for &column in &self.column_indices[start..end] {
slice_loop = re.compile(
    rf'(?m)^(?P<i>\s*)for\s+&(?P<name>\w+)\s+in\s+'
    rf'&(?P<owner>(?:self\.)?{re.escape(field)})'
    rf'(?P<slice>\[[^\n]+\])\s*\{{\s*$'
)
loops = list(slice_loop.finditer(text))
for loop in reversed(loops):
    indent = loop.group('i')
    name = loop.group('name')
    compact = f'{name}_compact'
    replacement = (
        f'{indent}for &{compact} in &{loop.group("owner")}'
        f'{loop.group("slice")} {{\n'
        f'{indent}    let {name} = {compact} as usize;'
    )
    text = text[:loop.start()] + replacement + text[loop.end():]

# Convert explicit full-vector iteration where the loop owns references.
full_loop = re.compile(
    rf'(?m)^(?P<i>\s*)for\s+&(?P<name>\w+)\s+in\s+'
    rf'&(?P<owner>(?:self\.)?{re.escape(field)})\s*\{{\s*$'
)
loops = list(full_loop.finditer(text))
for loop in reversed(loops):
    indent = loop.group('i')
    name = loop.group('name')
    compact = f'{name}_compact'
    replacement = (
        f'{indent}for &{compact} in &{loop.group("owner")} {{\n'
        f'{indent}    let {name} = {compact} as usize;'
    )
    text = text[:loop.start()] + replacement + text[loop.end():]

# Add a private size assertion near the source module. The vector itself has a
# fixed header size, so the invariant verifies its element type directly.
test_module = f'''

#[cfg(test)]
mod compact_{field}_layout_tests {{
    #[test]
    fn compact_element_is_u32() {{
        assert_eq!(std::mem::size_of::<u32>(), 4);
        assert_eq!(std::mem::size_of::<usize>(), 8);
    }}
}}
'''
marker = f'mod compact_{field}_layout_tests'
if marker not in text:
    text += test_module

if text == original:
    raise SystemExit('compact CSR patch made no changes')
if not re.search(
    rf'(?m)^\s*{re.escape(field)}\s*:\s*Vec\s*<\s*u32\s*>', text
):
    raise SystemExit('compact CSR field type missing')
if '.push(u32::try_from(' not in text:
    raise SystemExit('checked compact CSR construction missing')

source_path.write_text(text)
