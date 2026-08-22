from pathlib import Path
import re

path = Path('src/forest.rs')
text = path.read_text()

patterns = {
    'walk': re.compile(
        r'(?m)^(?P<indent>\s*)let mut walk(?:\s*:\s*[^=]+)?\s*=\s*'
        r'Vec(?:::[^\n=]+)?::new\(\);\s*$'
    ),
    'new_ancestors': re.compile(
        r'(?m)^(?P<indent>\s*)let mut new_ancestors(?:\s*:\s*[^=]+)?\s*=\s*'
        r'Vec(?:::[^\n=]+)?::new\(\);\s*$'
    ),
}
matches = {name: list(pattern.finditer(text)) for name, pattern in patterns.items()}
for name, found in matches.items():
    if len(found) != 1:
        raise SystemExit(f'expected one {name} scratch declaration, found {len(found)}')

first_position = min(found[0].start() for found in matches.values())
function_candidates = list(re.finditer(r'(?m)^\s*(?:pub\([^)]*\)\s+)?fn\s+\w+[^\n]*\{\s*$', text[:first_position]))
if not function_candidates:
    raise SystemExit('enclosing forest function not found')
function = function_candidates[-1]
function_header = function.group(0)
if 'split' not in function_header.lower() or 'forest' not in function_header.lower():
    raise SystemExit(f'unexpected enclosing function: {function_header!r}')

opening_brace = text.find('{', function.start(), function.end())
if opening_brace < 0:
    raise SystemExit('forest function opening brace not found')
line_start = text.rfind('\n', 0, function.start()) + 1
function_indent = text[line_start:function.start()]
body_indent = function_indent + '    '
insert = (
    '\n'
    f'{body_indent}// Reuse traversal storage across forest walks.\n'
    f'{body_indent}let mut walk: Vec<usize> = Vec::new();\n'
    f'{body_indent}let mut new_ancestors: Vec<usize> = Vec::new();'
)
text = text[:opening_brace + 1] + insert + text[opening_brace + 1:]

for name, pattern in patterns.items():
    found = list(pattern.finditer(text))
    if len(found) != 1:
        raise SystemExit(f'{name} declaration changed unexpectedly after insertion')
    match = found[0]
    indentation = match.group('indent')
    text = text[:match.start()] + f'{indentation}{name}.clear();' + text[match.end():]

if text.count('let mut walk: Vec<usize> = Vec::new();') != 1:
    raise SystemExit('hoisted walk declaration missing')
if text.count('let mut new_ancestors: Vec<usize> = Vec::new();') != 1:
    raise SystemExit('hoisted ancestor declaration missing')
if text.count('walk.clear();') != 1 or text.count('new_ancestors.clear();') != 1:
    raise SystemExit('scratch clear calls missing')

path.write_text(text)
