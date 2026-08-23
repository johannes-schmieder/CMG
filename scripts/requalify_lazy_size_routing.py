from pathlib import Path
import subprocess

SOURCE_COMMIT = 'c3abca28b820a5c1e0730d5e4988680ee7900bf7'
SOURCE_PATH = 'scripts/requalify_compact_label_routing.py'

text = subprocess.check_output(
    ['git', 'show', f'{SOURCE_COMMIT}:{SOURCE_PATH}'],
    text=True,
)

replacements = (
    ('requalify_compact_label_routing.py', 'requalify_lazy_size_routing.py'),
    ('requalify-compact-label-routing.yml', 'requalify-lazy-size-routing.yml'),
    ('post-compact-label-routing.json', 'post-lazy-size-routing.json'),
    ('post-compact-label-full-pcg-routing', 'post-lazy-size-full-pcg-routing'),
    ('post-compaction-routing', 'post-lazy-size-routing'),
    ('Post-compaction routing requalification', 'Post-lazy-size routing requalification'),
    ('Post-compaction routing requalification — 2026-08-23', 'Post-lazy-size routing requalification — 2026-08-23'),
    ('/tmp/cmg-post-label-routing-', '/tmp/cmg-post-lazy-size-routing-'),
)
for old, new in replacements:
    text = text.replace(old, new)

old_actions = '''1. Re-profile contraction mapping and sorting after compact aggregation labels.
2. Evaluate compact aggregate-size storage only if retained-memory accounting shows material headroom.
3. Revisit moderate-density scratch radix only if reusable scratch can preserve its speed signal without peak-memory inflation.
4. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
new_actions = '''1. Refresh cumulative hierarchy-memory accounting after compact labels and lazy aggregate sizes.
2. Continue sort-dominant contraction work only with a design that clears both speed and peak-memory gates.
3. Audit public API documentation for the lazy compatibility caches.
4. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
if old_actions not in text:
    raise SystemExit('historical routing next-action block changed unexpectedly')
text = text.replace(old_actions, new_actions, 1)

required = (
    'post-lazy-size-routing.json',
    'post-lazy-size-full-pcg-routing',
    'requalify-lazy-size-routing.yml',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f'lazy-size routing transformation missing {marker}')

exec(
    compile(text, 'scripts/requalify_lazy_size_routing.py', 'exec'),
    {'__name__': '__main__'},
)
