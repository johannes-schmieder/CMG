from pathlib import Path
import subprocess

SOURCE_COMMIT = '496fdb15453702692f8d3bbbfb2e6226496db70c'
SOURCE_PATH = 'scripts/radix_compact_sort_gate.py'

text = subprocess.check_output(
    ['git', 'show', f'{SOURCE_COMMIT}:{SOURCE_PATH}'],
    text=True,
)

for old, new in (
    ('radix_compact_sort_gate.py', 'routed_radix_compact_sort_gate.py'),
    ('radix-compact-sort-gate.yml', 'routed-radix-compact-sort-gate.yml'),
    ('radix-compact-sort-latest.json', 'routed-radix-compact-sort-latest.json'),
    ('routed-four-pass-radix-compact-edge-sort', 'density-routed-four-pass-radix-compact-edge-sort'),
    ('/tmp/cmg-radix-sort-', '/tmp/cmg-routed-radix-sort-'),
    ('Routed compact-edge radix sort gate', 'Density-routed compact-edge radix sort gate'),
    ('Routed compact-edge radix sort checkpoint', 'Density-routed compact-edge radix sort checkpoint'),
):
    text = text.replace(old, new)

call = 'sort_compact_edges_routed(&mut raw);'
if text.count(call) != 2:
    raise SystemExit(f'expected two routed sort calls, found {text.count(call)}')
text = text.replace(call, 'sort_compact_edges_routed(&mut raw, vertex_count);')

old_router = '''fn sort_compact_edges_routed(raw: &mut [Edge]) {
    if raw.len() >= COMPACT_RADIX_MIN_EDGES {
        sort_compact_edges_radix(raw);
    } else {
        sort_compact_edges_two_stage(raw);
    }
}
'''
new_router = '''const COMPACT_RADIX_MIN_EDGES_PER_VERTEX: usize = 2;
const COMPACT_RADIX_MODERATE_MAX_EDGES_PER_VERTEX: usize = 8;
const COMPACT_RADIX_SMALL_DENSE_MAX_EDGES: usize = 350_000;

fn sort_compact_edges_routed(raw: &mut [Edge], vertex_count: usize) {
    if should_use_compact_radix(raw.len(), vertex_count) {
        sort_compact_edges_radix(raw);
    } else {
        sort_compact_edges_two_stage(raw);
    }
}

fn should_use_compact_radix(edge_count: usize, vertex_count: usize) -> bool {
    if edge_count < COMPACT_RADIX_MIN_EDGES {
        return false;
    }
    let minimum_useful = vertex_count.saturating_mul(COMPACT_RADIX_MIN_EDGES_PER_VERTEX);
    if edge_count < minimum_useful {
        return false;
    }
    let moderate_limit = vertex_count
        .saturating_mul(COMPACT_RADIX_MODERATE_MAX_EDGES_PER_VERTEX);
    edge_count <= moderate_limit || edge_count <= COMPACT_RADIX_SMALL_DENSE_MAX_EDGES
}
'''
if text.count(old_router) != 1:
    raise SystemExit('original radix router block changed unexpectedly')
text = text.replace(old_router, new_router, 1)

old_specs = '''    specs = (
        ('path-1m', 'path', 1_000_000),
        ('worker-firm-1.5m', 'worker-firm', 500_000),
        ('dense-worker-firm-1.6m', 'dense-worker-firm', 100_000),
    )
'''
new_specs = '''    specs = (
        ('path-1m', 'path', 1_000_000),
        ('worker-firm-750k', 'worker-firm', 250_000),
        ('worker-firm-1.5m', 'worker-firm', 500_000),
        ('worker-firm-3m', 'worker-firm', 1_000_000),
        ('dense-worker-firm-400k', 'dense-worker-firm', 25_000),
        ('dense-worker-firm-1.6m', 'dense-worker-firm', 100_000),
    )
'''
if text.count(old_specs) != 1:
    raise SystemExit('original radix benchmark matrix changed unexpectedly')
text = text.replace(old_specs, new_specs, 1)

old_active = '''    active_time = [
        result['cases']['worker-firm-1.5m']['candidate_over_baseline_time'],
        result['cases']['dense-worker-firm-1.6m']['candidate_over_baseline_time'],
    ]
'''
new_active = '''    active_time = [
        result['cases']['worker-firm-750k']['candidate_over_baseline_time'],
        result['cases']['worker-firm-1.5m']['candidate_over_baseline_time'],
        result['cases']['worker-firm-3m']['candidate_over_baseline_time'],
    ]
'''
if text.count(old_active) != 1:
    raise SystemExit('original active-case list changed unexpectedly')
text = text.replace(old_active, new_active, 1)

text = text.replace("'worst_additional_peak_ratio_max': 1.18", "'worst_additional_peak_ratio_max': 1.08")
text = text.replace("'worst_peak_rss_ratio_max': 1.15", "'worst_peak_rss_ratio_max': 1.08")
text = text.replace(
    'routed radix ordering materially improved worker-firm hierarchy time and justified its temporary buffer',
    'density-routed radix ordering improved ordinary worker-firm hierarchy time while avoiding dense-case memory inflation',
)
text = text.replace(
    'the timing benefit did not justify the temporary radix buffer or a regression limit was exceeded',
    'the density router did not preserve enough timing benefit or a memory/regression limit was exceeded',
)
text = text.replace(
    'Parallel sorting is unchanged; the candidate affects only large serial/fallback compact coarse-edge ordering.',
    'Parallel sorting is unchanged; scratch radix is restricted to large moderate-density or bounded small-dense serial/fallback levels.',
)
text = text.replace(
    'If radix is retained, evaluate caller-owned scratch reuse before widening its routing threshold.',
    'If routed radix is retained, re-profile contraction sorting and verify the full certified PCG routing matrix.',
)

required = (
    'should_use_compact_radix',
    "('worker-firm-3m', 'worker-firm', 1_000_000)",
    "'worst_additional_peak_ratio_max': 1.08",
    'routed-radix-compact-sort-latest.json',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f'routed radix transformation missing marker: {marker}')

exec(
    compile(text, 'scripts/routed_radix_compact_sort_gate.py', 'exec'),
    {'__name__': '__main__'},
)
