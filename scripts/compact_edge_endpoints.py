from pathlib import Path
import re

path = Path('src/graph.rs')
text = path.read_text()
original = text

# Locate the Edge struct and require exactly the expected endpoint field types.
struct_match = re.search(
    r'(?s)(?P<prefix>(?:#\[[^\]]+\]\s*)*(?:pub(?:\([^)]*\))?\s+)?'
    r'struct\s+Edge\s*\{(?P<body>.*?)\n\}',
    text,
)
if struct_match is None:
    raise SystemExit('Edge struct not found')
body = struct_match.group('body')
if len(re.findall(r'(?m)^\s*u\s*:\s*usize\s*,?\s*$', body)) != 1:
    raise SystemExit('expected one usize Edge.u field')
if len(re.findall(r'(?m)^\s*v\s*:\s*usize\s*,?\s*$', body)) != 1:
    raise SystemExit('expected one usize Edge.v field')
body = re.sub(
    r'(?m)^(\s*u\s*:\s*)usize(\s*,?\s*)$', r'\1u32\2', body, count=1
)
body = re.sub(
    r'(?m)^(\s*v\s*:\s*)usize(\s*,?\s*)$', r'\1u32\2', body, count=1
)
text = text[:struct_match.start('body')] + body + text[struct_match.end('body'):]

# Extend the existing checked vertex-bound condition inside from_edges. This
# deliberately reuses the crate's current typed out-of-bounds error path.
from_edges = re.search(r'\bpub\s+fn\s+from_edges\b', text)
if from_edges is None:
    raise SystemExit('Laplacian::from_edges not found')
function_open = text.find('{', from_edges.end())
if function_open < 0:
    raise SystemExit('from_edges opening brace not found')
depth = 0
function_close = None
for index in range(function_open, len(text)):
    if text[index] == '{':
        depth += 1
    elif text[index] == '}':
        depth -= 1
        if depth == 0:
            function_close = index + 1
            break
if function_close is None:
    raise SystemExit('from_edges closing brace not found')
function = text[from_edges.start():function_close]

bounds_candidates = list(re.finditer(
    r'(?s)if\s+(?P<condition>[^\{]+)\{(?P<body>.*?)\}', function
))
bounds = None
for candidate in bounds_candidates:
    condition = candidate.group('condition')
    body_text = candidate.group('body')
    if (
        re.search(r'\bu\b', condition)
        and re.search(r'\bv\b', condition)
        and ('IndexOutOfBounds' in body_text or 'out_of_bounds' in body_text.lower())
    ):
        bounds = candidate
        break
if bounds is None:
    raise SystemExit('existing endpoint bounds check not recognized')
condition = bounds.group('condition').strip()
if 'u32::MAX' not in condition:
    extended = (
        f'({condition})\n'
        '                || u > u32::MAX as usize\n'
        '                || v > u32::MAX as usize'
    )
    absolute_start = from_edges.start() + bounds.start('condition')
    absolute_end = from_edges.start() + bounds.end('condition')
    text = text[:absolute_start] + extended + text[absolute_end:]

# Locate impl Edge, because Self literals there are endpoint constructors.
impl_match = re.search(r'\bimpl\s+Edge\s*\{', text)
if impl_match is None:
    raise SystemExit('impl Edge not found')
impl_open = text.find('{', impl_match.start(), impl_match.end())
depth = 0
impl_close = None
for index in range(impl_open, len(text)):
    if text[index] == '{':
        depth += 1
    elif text[index] == '}':
        depth -= 1
        if depth == 0:
            impl_close = index + 1
            break
if impl_close is None:
    raise SystemExit('impl Edge closing brace not found')

# Convert Edge/Self struct literal endpoint values. Balanced-brace scanning is
# used so formatting differences do not matter.
def compact_literals(source: str, token_pattern: str, start: int, end: int) -> str:
    cursor = start
    replacements = []
    pattern = re.compile(token_pattern)
    while True:
        match = pattern.search(source, cursor, end)
        if match is None:
            break
        brace = source.find('{', match.start(), match.end())
        if brace < 0:
            cursor = match.end()
            continue
        depth = 0
        close = None
        for index in range(brace, end):
            if source[index] == '{':
                depth += 1
            elif source[index] == '}':
                depth -= 1
                if depth == 0:
                    close = index + 1
                    break
        if close is None:
            raise SystemExit('unterminated Edge literal')
        literal = source[brace + 1:close - 1]
        changed = literal
        changed = re.sub(
            r'(?m)^(?P<i>\s*)u\s*,',
            r'\g<i>u: u as u32,',
            changed,
        )
        changed = re.sub(
            r'(?m)^(?P<i>\s*)v\s*,',
            r'\g<i>v: v as u32,',
            changed,
        )
        changed = re.sub(
            r'(?m)^(?P<i>\s*)u\s*:\s*(?P<e>[^,\n]+),',
            lambda m: (
                m.group('i') + 'u: (' + m.group('e').strip() + ') as u32,'
                if 'as u32' not in m.group('e')
                else m.group(0)
            ),
            changed,
        )
        changed = re.sub(
            r'(?m)^(?P<i>\s*)v\s*:\s*(?P<e>[^,\n]+),',
            lambda m: (
                m.group('i') + 'v: (' + m.group('e').strip() + ') as u32,'
                if 'as u32' not in m.group('e')
                else m.group(0)
            ),
            changed,
        )
        if changed != literal:
            replacements.append((brace + 1, close - 1, changed))
        cursor = close
    for left, right, replacement in reversed(replacements):
        source = source[:left] + replacement + source[right:]
    return source

text = compact_literals(text, r'\bEdge\s*\{', 0, len(text))
# Recompute impl boundaries after prior replacements.
impl_match = re.search(r'\bimpl\s+Edge\s*\{', text)
impl_open = text.find('{', impl_match.start(), impl_match.end())
depth = 0
for index in range(impl_open, len(text)):
    if text[index] == '{':
        depth += 1
    elif text[index] == '}':
        depth -= 1
        if depth == 0:
            impl_close = index + 1
            break
text = compact_literals(text, r'\bSelf\s*\{', impl_open, impl_close)

# Direct endpoint field reads in graph.rs become public-style accessors. Field
# declarations and struct-literal labels do not match this dot syntax.
text = re.sub(r'\b([A-Za-z_][A-Za-z0-9_]*)\.u\b(?!\s*\()', r'\1.u()', text)
text = re.sub(r'\b([A-Za-z_][A-Za-z0-9_]*)\.v\b(?!\s*\()', r'\1.v()', text)

# Force the two endpoint accessors to cast exactly once rather than recurse.
def replace_accessor(source: str, name: str, field: str) -> str:
    pattern = re.compile(
        rf'(?s)(pub(?:\([^)]*\))?\s+(?:const\s+)?fn\s+{name}\s*\('
        rf'\s*&self\s*\)\s*->\s*usize\s*\{{).*?(\}})'
    )
    found = list(pattern.finditer(source))
    if len(found) != 1:
        raise SystemExit(f'expected one Edge::{name} accessor, found {len(found)}')
    match = found[0]
    replacement = match.group(1) + f'\n        self.{field} as usize\n    ' + match.group(2)
    return source[:match.start()] + replacement + source[match.end():]

text = replace_accessor(text, 'u', 'u')
text = replace_accessor(text, 'v', 'v')

# If shorthand constructors were single-line, normalize those as well.
text = re.sub(
    r'\b(Self|Edge)\s*\{\s*u\s*,\s*v\s*,\s*weight\s*\}',
    r'\1 { u: u as u32, v: v as u32, weight }',
    text,
)

# Add a permanent layout invariant. This protects the intended memory benefit.
layout_test = r'''

#[cfg(test)]
mod compact_edge_layout_tests {
    use super::Edge;

    #[test]
    fn edge_uses_compact_endpoints() {
        assert_eq!(std::mem::size_of::<Edge>(), 16);
        assert_eq!(std::mem::align_of::<Edge>(), 8);
    }
}
'''
if 'mod compact_edge_layout_tests' not in text:
    text += layout_test

if text == original:
    raise SystemExit('compact endpoint patch made no changes')
if not re.search(r'(?m)^\s*u\s*:\s*u32\s*,?\s*$', text):
    raise SystemExit('compact u field missing')
if not re.search(r'(?m)^\s*v\s*:\s*u32\s*,?\s*$', text):
    raise SystemExit('compact v field missing')
if 'u32::MAX' not in text:
    raise SystemExit('wide-index guard missing')

path.write_text(text)
