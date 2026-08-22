from pathlib import Path
import re

path = Path('src/graph.rs')
text = path.read_text()

function_match = re.search(
    r'(?m)^\s*pub\s+fn\s+from_edges(?:<[^\n{]+>)?[^\n]*\{', text
)
if function_match is None:
    raise SystemExit('public Laplacian::from_edges function not found')
function_start = function_match.start()
opening = text.find('{', function_match.start(), function_match.end())
depth = 0
function_end = None
for index in range(opening, len(text)):
    if text[index] == '{':
        depth += 1
    elif text[index] == '}':
        depth -= 1
        if depth == 0:
            function_end = index + 1
            break
if function_end is None:
    raise SystemExit('from_edges function end not found')
body = text[function_start:function_end]

sort_match = re.search(
    r'(?m)^\s*(?P<source>\w+)\.sort(?:_unstable)?_by\s*\(', body
)
if sort_match is None:
    raise SystemExit('canonical edge sort not found')
source = sort_match.group('source')

loop_header = re.search(
    rf'(?m)^\s*for\s+(?P<edge>\w+)\s+in\s+{re.escape(source)}\s*\{{',
    body[sort_match.end():],
)
if loop_header is None:
    raise SystemExit(f'for-edge loop consuming {source} not found')
loop_header_start = sort_match.end() + loop_header.start()
loop_open = body.find('{', loop_header_start, sort_match.end() + loop_header.end())
depth = 0
loop_end = None
for index in range(loop_open, len(body)):
    if body[index] == '{':
        depth += 1
    elif body[index] == '}':
        depth -= 1
        if depth == 0:
            loop_end = index + 1
            break
if loop_end is None:
    raise SystemExit('duplicate-combination loop end not found')
edge_name = loop_header.group('edge')
loop_text = body[loop_header_start:loop_end]

prefix = body[:loop_header_start]
declarations = list(re.finditer(
    rf'(?m)^(?P<indent>\s*)let\s+mut\s+(?P<combined>\w+)\s*=\s*'
    rf'Vec(?:::[^\n=]+)?::with_capacity\(\s*{re.escape(source)}\.len\(\)\s*\);\s*$',
    prefix,
))
if len(declarations) != 1:
    raise SystemExit(
        f'expected one duplicate-output allocation for {source}, found {len(declarations)}'
    )
declaration = declarations[0]
combined = declaration.group('combined')
indent = declaration.group('indent')

shape_a = re.search(
    rf'if\s+let\s+Some\((?P<last>\w+)\)\s*=\s*'
    rf'{re.escape(combined)}\.last_mut\(\)\s*\{{\s*'
    rf'if\s+(?P<condition>.*?)\s*\{{\s*'
    rf'(?P<merge>.*?)\s*continue;\s*\}}\s*\}}\s*'
    rf'{re.escape(combined)}\.push\(\s*{re.escape(edge_name)}\s*\);',
    loop_text,
    re.DOTALL,
)
shape_b = re.search(
    rf'match\s+{re.escape(combined)}\.last_mut\(\)\s*\{{\s*'
    rf'Some\((?P<last>\w+)\)\s+if\s+(?P<condition>.*?)\s*=>\s*\{{\s*'
    rf'(?P<merge>.*?)\s*\}}\s*,?\s*'
    rf'_\s*=>\s*{re.escape(combined)}\.push\(\s*{re.escape(edge_name)}\s*\)\s*,?\s*\}}',
    loop_text,
    re.DOTALL,
)
shape = shape_a or shape_b
if shape is None:
    raise SystemExit('recognized duplicate merge control flow not found')
last_name = shape.group('last')
condition = shape.group('condition').strip()
merge = shape.group('merge').strip()
if not condition or not merge:
    raise SystemExit('duplicate condition or checked merge body was empty')
if combined not in loop_text or '.push' not in loop_text:
    raise SystemExit('unexpected duplicate loop')

# Preserve the exact checked-add expression and duplicate predicate from the
# existing implementation. Only ownership/storage changes.
merge_lines = merge.splitlines()
merge_block = '\n'.join(f'{indent}            {line.strip()}' for line in merge_lines)
replacement = f'''{indent}// Compact equal canonical endpoint pairs in place. The edge list is
{indent}// already sorted by endpoint and weight, so this preserves the exact
{indent}// deterministic accumulation order while avoiding a second O(m) edge
{indent}// allocation during every fine and coarse graph construction.
{indent}let mut write_index = 0usize;
{indent}for read_index in 0..{source}.len() {{
{indent}    let {edge_name} = {source}[read_index];
{indent}    if write_index > 0 {{
{indent}        let {last_name} = &mut {source}[write_index - 1];
{indent}        if {condition} {{
{merge_block}
{indent}            continue;
{indent}        }}
{indent}    }}
{indent}    if write_index != read_index {{
{indent}        {source}[write_index] = {edge_name};
{indent}    }}
{indent}    write_index += 1;
{indent}}}
{indent}{source}.truncate(write_index);
{indent}let mut {combined} = {source};'''

absolute_declaration_start = function_start + declaration.start()
absolute_loop_end = function_start + loop_end
text = text[:absolute_declaration_start] + replacement + text[absolute_loop_end:]

if text.count('Compact equal canonical endpoint pairs in place') != 1:
    raise SystemExit('in-place compaction marker missing')
if re.search(
    rf'Vec(?:::[^\n=]+)?::with_capacity\(\s*{re.escape(source)}\.len\(\)\s*\)',
    text[function_start:function_end + len(replacement)],
):
    raise SystemExit('duplicate-output full-capacity allocation remains')

path.write_text(text)
