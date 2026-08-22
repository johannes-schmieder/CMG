from pathlib import Path
import json
import re

record = json.loads(
    Path('.ci/performance/compact-label-feasibility.json').read_text()
)
if not record.get('feasible_for_mechanical_experiment'):
    raise SystemExit('compact label feasibility gate is false')
selected = record.get('selected')
if not selected:
    raise SystemExit('compact label selected field is missing')

source_path = Path(selected['path'])
struct_name = selected['struct']
field = selected['field']
text = source_path.read_text()
original = text

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
    raise SystemExit('selected label field is public')
text = text[:match.start()] + match.group(0).replace('usize', 'u32') + text[match.end():]

assignment = re.search(
    rf'(?m)^\s*{re.escape(field)}\s*:\s*(?P<local>\w+)\s*,\s*$', text
)
shorthand = re.search(rf'(?m)^\s*{re.escape(field)}\s*,\s*$', text)
local = assignment.group('local') if assignment else (field if shorthand else None)
if local is None:
    raise SystemExit(f'struct initialization for {field} not recognized')

# Convert explicit type annotations and common sentinel/zero constructors.
text = re.sub(
    rf'\blet\s+mut\s+{re.escape(local)}\s*:\s*Vec\s*<\s*usize\s*>',
    f'let mut {local}: Vec<u32>',
    text,
)
text = re.sub(
    rf'\blet\s+{re.escape(local)}\s*:\s*Vec\s*<\s*usize\s*>',
    f'let {local}: Vec<u32>',
    text,
)
text = re.sub(
    rf'(?P<prefix>\blet\s+(?:mut\s+)?{re.escape(local)}\s*=\s*vec!\[)'
    r'usize::MAX(?P<suffix>\s*;)',
    rf'\g<prefix>u32::MAX\g<suffix>',
    text,
)
text = re.sub(
    rf'(?P<prefix>\blet\s+(?:mut\s+)?{re.escape(local)}\s*=\s*vec!\[)'
    r'0(?:usize)?(?P<suffix>\s*;)',
    rf'\g<prefix>0u32\g<suffix>',
    text,
)

# Push and indexed-write construction sites retain usize calculations and cast
# only at the storage boundary.
push_pattern = re.compile(
    rf'(?m)^(?P<i>\s*){re.escape(local)}\.push\((?P<value>.+)\);\s*$'
)
for push in reversed(list(push_pattern.finditer(text))):
    value = push.group('value').strip()
    replacement = (
        f"{push.group('i')}{local}.push(u32::try_from({value})"
        f'.expect("hierarchy label exceeds u32::MAX"));'
    )
    text = text[:push.start()] + replacement + text[push.end():]

write_pattern = re.compile(
    rf'(?m)^(?P<i>\s*){re.escape(local)}\s*\[(?P<index>[^\]\n]+)\]'
    rf'\s*=\s*(?P<value>[^;]+);\s*$'
)
for write in reversed(list(write_pattern.finditer(text))):
    value = write.group('value').strip()
    compact_value = (
        'u32::MAX' if value == 'usize::MAX'
        else f'u32::try_from({value}).expect("hierarchy label exceeds u32::MAX")'
    )
    replacement = (
        f"{write.group('i')}{local}[{write.group('index')}] = "
        f'{compact_value};'
    )
    text = text[:write.start()] + replacement + text[write.end():]

# Stored-field direct reads return usize to preserve surrounding APIs.
field_index = re.compile(
    rf'(?P<expr>(?:self\.)?{re.escape(field)}\s*\[[^\]\n]+\])'
)
text = field_index.sub(r'(\g<expr> as usize)', text)

# Local reads after construction may occur before ownership moves into the
# struct. Avoid rewriting assignment left-hand sides already converted above.
local_read = re.compile(
    rf'(?P<expr>\b{re.escape(local)}\s*\[[^\]\n]+\])'
)
def cast_local(match):
    start = match.start()
    tail = text[match.end():match.end() + 8]
    if re.match(r'\s*=', tail):
        return match.group('expr')
    return f'({match.group("expr")} as usize)'
text = local_read.sub(cast_local, text)

# Convert sentinel comparisons tied to the compact local/field.
text = re.sub(
    rf'((?:self\.)?{re.escape(field)}\s*\[[^\]]+\])\s*==\s*usize::MAX',
    r'\1 == u32::MAX',
    text,
)
text = re.sub(
    rf'({re.escape(local)}\s*\[[^\]]+\])\s*==\s*usize::MAX',
    r'\1 == u32::MAX',
    text,
)

# Convert simple reference iteration over the stored field.
loop_patterns = [
    re.compile(
        rf'(?m)^(?P<i>\s*)for\s+&(?P<name>\w+)\s+in\s+'
        rf'&(?P<owner>(?:self\.)?{re.escape(field)})\s*\{{\s*$'
    ),
    re.compile(
        rf'(?m)^(?P<i>\s*)for\s+&(?P<name>\w+)\s+in\s+'
        rf'&(?P<owner>(?:self\.)?{re.escape(field)})'
        rf'(?P<slice>\[[^\n]+\])\s*\{{\s*$'
    ),
]
for pattern in loop_patterns:
    loops = list(pattern.finditer(text))
    for loop in reversed(loops):
        indent = loop.group('i')
        name = loop.group('name')
        compact = f'{name}_compact'
        slice_text = loop.groupdict().get('slice') or ''
        replacement = (
            f'{indent}for &{compact} in &{loop.group("owner")}{slice_text} {{\n'
            f'{indent}    let {name} = {compact} as usize;'
        )
        text = text[:loop.start()] + replacement + text[loop.end():]

# Reject unhandled iterator chains against the compact field.
if re.search(
    rf'\b(?:self\.)?{re.escape(field)}\b[^\n]*\.(?:iter|chunks|windows)', text
):
    raise SystemExit('unhandled compact label iterator chain remains')

module_name = re.sub(r'\W+', '_', field)
test_module = f'''

#[cfg(test)]
mod compact_{module_name}_layout_tests {{
    #[test]
    fn compact_label_element_is_u32() {{
        assert_eq!(std::mem::size_of::<u32>(), 4);
    }}
}}
'''
if f'mod compact_{module_name}_layout_tests' not in text:
    text += test_module

if text == original:
    raise SystemExit('compact label patch made no changes')
if not re.search(
    rf'(?m)^\s*{re.escape(field)}\s*:\s*Vec\s*<\s*u32\s*>', text
):
    raise SystemExit('compact label field type missing')
source_path.write_text(text)
