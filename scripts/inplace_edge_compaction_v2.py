from pathlib import Path
import re

path = Path('src/graph.rs')
text = path.read_text()

name = re.search(r'\bpub\s+fn\s+from_edges\b', text)
if name is None:
    raise SystemExit('public from_edges function not found')
opening = text.find('{', name.end())
if opening < 0:
    raise SystemExit('from_edges opening brace not found')
depth = 0
closing = None
for index in range(opening, len(text)):
    if text[index] == '{':
        depth += 1
    elif text[index] == '}':
        depth -= 1
        if depth == 0:
            closing = index + 1
            break
if closing is None:
    raise SystemExit('from_edges closing brace not found')
function = text[name.start():closing]

sort = re.search(
    r'(?m)^\s*(?P<source>\w+)\.sort(?:_unstable)?_by(?:_key)?\s*\(',
    function,
)
if sort is None:
    raise SystemExit('canonical edge sort not found')
source = sort.group('source')

loop = re.search(
    rf'(?m)^\s*for\s+(?P<edge>\w+)\s+in\s+{re.escape(source)}'
    rf'(?:\.into_iter\(\))?\s*\{{',
    function[sort.end():],
)
if loop is None:
    raise SystemExit(f'loop consuming sorted vector {source} not found')
loop_start = sort.end() + loop.start()
loop_header_end = sort.end() + loop.end()
loop_open = function.rfind('{', loop_start, loop_header_end)
depth = 0
loop_close = None
for index in range(loop_open, len(function)):
    if function[index] == '{':
        depth += 1
    elif function[index] == '}':
        depth -= 1
        if depth == 0:
            loop_close = index + 1
            break
if loop_close is None:
    raise SystemExit('duplicate loop closing brace not found')
loop_text = function[loop_start:loop_close]
edge = loop.group('edge')

prefix = function[:loop_start]
declaration_pattern = re.compile(
    rf'(?m)^(?P<indent>\s*)let\s+mut\s+(?P<combined>\w+)\s*'
    rf'(?::\s*Vec(?:<[^\n]+>)?)?\s*=\s*Vec(?:::[^\n=]+)?::'
    rf'with_capacity\(\s*{re.escape(source)}\.len\(\)\s*\);\s*$'
)
declarations = list(declaration_pattern.finditer(prefix))
if len(declarations) != 1:
    raise SystemExit(
        f'expected one full-capacity duplicate output, found {len(declarations)}'
    )
declaration = declarations[0]
combined = declaration.group('combined')
indent = declaration.group('indent')

shape_a = re.search(
    rf'if\s+let\s+Some\((?P<last>\w+)\)\s*=\s*'
    rf'{re.escape(combined)}\.last_mut\(\)\s*\{{\s*'
    rf'if\s+(?P<condition>.*?)\s*\{{\s*'
    rf'(?P<merge>.*?)\s*continue;\s*\}}\s*\}}\s*'
    rf'{re.escape(combined)}\.push\(\s*{re.escape(edge)}\s*\);',
    loop_text,
    re.DOTALL,
)
shape_b = re.search(
    rf'match\s+{re.escape(combined)}\.last_mut\(\)\s*\{{\s*'
    rf'Some\((?P<last>\w+)\)\s+if\s+(?P<condition>.*?)\s*=>\s*\{{\s*'
    rf'(?P<merge>.*?)\s*\}}\s*,?\s*'
    rf'_\s*=>\s*{re.escape(combined)}\.push\(\s*{re.escape(edge)}\s*\)'
    rf'\s*,?\s*\}}',
    loop_text,
    re.DOTALL,
)
shape_c = re.search(
    rf'if\s+{re.escape(combined)}\.last\(\)\.is_some_and\(\|(?P<last>\w+)\|'
    rf'\s*(?P<condition>.*?)\)\s*\{{\s*'
    rf'let\s+(?P=last)\s*=\s*{re.escape(combined)}\.last_mut\(\)'
    rf'\.expect\([^;]+\);\s*(?P<merge>.*?)\s*\}}\s*else\s*\{{\s*'
    rf'{re.escape(combined)}\.push\(\s*{re.escape(edge)}\s*\);\s*\}}',
    loop_text,
    re.DOTALL,
)
shape = shape_a or shape_b or shape_c
if shape is None:
    raise SystemExit('recognized duplicate merge control flow not found')
last = shape.group('last')
condition = shape.group('condition').strip()
merge = shape.group('merge').strip()
if not condition or not merge:
    raise SystemExit('empty duplicate condition or merge body')

merge_lines = [line.strip() for line in merge.splitlines() if line.strip()]
merge_block = '\n'.join(f'{indent}            {line}' for line in merge_lines)
replacement = f'''{indent}// Compact equal canonical endpoint pairs in place. Sorting already fixes
{indent}// endpoint and weight order, so checked accumulation remains deterministic.
{indent}// This avoids a second full O(m) edge allocation at every hierarchy level.
{indent}let mut write_index = 0usize;
{indent}for read_index in 0..{source}.len() {{
{indent}    let {edge} = {source}[read_index];
{indent}    if write_index > 0 {{
{indent}        let {last} = &mut {source}[write_index - 1];
{indent}        if {condition} {{
{merge_block}
{indent}            continue;
{indent}        }}
{indent}    }}
{indent}    if write_index != read_index {{
{indent}        {source}[write_index] = {edge};
{indent}    }}
{indent}    write_index += 1;
{indent}}}
{indent}{source}.truncate(write_index);
{indent}let mut {combined} = {source};'''

absolute_declaration = name.start() + declaration.start()
absolute_loop_close = name.start() + loop_close
text = text[:absolute_declaration] + replacement + text[absolute_loop_close:]
if text.count('Compact equal canonical endpoint pairs in place') != 1:
    raise SystemExit('compaction marker missing')
path.write_text(text)
