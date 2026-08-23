import subprocess

SOURCE_COMMIT = '46bfb616364d6b8e10e217615bee49a4498bffb4'
SOURCE_PATH = 'scripts/parallel_endpoint_sort_gate.py'

text = subprocess.check_output(
    ['git', 'show', f'{SOURCE_COMMIT}:{SOURCE_PATH}'],
    text=True,
)

for old, new in (
    ('parallel_endpoint_sort_gate.py', 'dense_parallel_endpoint_sort_gate.py'),
    ('parallel-endpoint-sort-gate.yml', 'dense-parallel-endpoint-sort-gate.yml'),
    ('parallel-endpoint-sort-latest.json', 'dense-parallel-endpoint-sort-latest.json'),
    ('parallel-compact-endpoint-first-sort', 'density-routed-parallel-compact-endpoint-first-sort'),
    ('Parallel endpoint-first compact sort gate', 'Density-routed parallel endpoint-first sort gate'),
    ('Parallel endpoint-first compact sort checkpoint', 'Density-routed parallel endpoint-first sort checkpoint'),
    ('/tmp/cmg-parallel-endpoint-sort-', '/tmp/cmg-dense-parallel-endpoint-sort-'),
):
    text = text.replace(old, new)

old_parser = '''    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'{tag}: unexpected benchmark output: {payloads}')
    match = re.search(
'''
new_parser = '''    json_start = completed.stdout.find('{')
    if json_start < 0:
        raise RuntimeError(f'{tag}: benchmark JSON object missing')
    payload = json.loads(completed.stdout[json_start:])
    match = re.search(
'''
if text.count(old_parser) != 1:
    raise SystemExit('historical benchmark parser changed unexpectedly')
text = text.replace(old_parser, new_parser, 1)
text = text.replace(
    '''    payload = payloads[0]
    payload['peak_rss_kib'] = int(match.group(1))
''',
    '''    payload['peak_rss_kib'] = int(match.group(1))
''',
    1,
)

old_candidate = '''        if raw.len() >= PARALLEL_SETUP_MIN_ITEMS && executor.should_parallel(raw.len()) {
            executor.install(|| raw.par_sort_unstable_by_key(endpoint_key));
            sort_weights_within_endpoint_groups(&mut raw);
        } else {
            sort_compact_edges_two_stage(&mut raw);
        }
'''
new_candidate = '''        if raw.len() >= PARALLEL_SETUP_MIN_ITEMS && executor.should_parallel(raw.len()) {
            if raw.len() >= vertex_count.saturating_mul(4) {
                executor.install(|| raw.par_sort_unstable_by_key(endpoint_key));
                sort_weights_within_endpoint_groups(&mut raw);
            } else {
                executor.install(|| raw.par_sort_unstable_by(compare_raw_edges));
            }
        } else {
            sort_compact_edges_two_stage(&mut raw);
        }
'''
if text.count(old_candidate) != 1:
    raise SystemExit('historical candidate block changed unexpectedly')
text = text.replace(old_candidate, new_candidate, 1)

old_specs = '''    specs = (
        ('path-500k', 'path', 500_000),
        ('grid-600k', 'grid', 600_000),
        ('worker-firm-1m', 'worker-firm', 1_000_000),
        ('dense-worker-firm-200k', 'dense-worker-firm', 200_000),
    )
'''
new_specs = '''    specs = (
        ('path-500k', 'path', 500_000),
        ('grid-600k', 'grid', 600_000),
        ('worker-firm-1m', 'worker-firm', 1_000_000),
        ('dense-worker-firm-50k', 'dense-worker-firm', 50_000),
        ('dense-worker-firm-100k', 'dense-worker-firm', 100_000),
        ('dense-worker-firm-200k', 'dense-worker-firm', 200_000),
    )
'''
if text.count(old_specs) != 1:
    raise SystemExit('historical benchmark matrix changed unexpectedly')
text = text.replace(old_specs, new_specs, 1)

old_active = '''    active = [
        result['cases']['grid-600k']['candidate_over_baseline_parallel_setup'],
        result['cases']['worker-firm-1m']['candidate_over_baseline_parallel_setup'],
        result['cases']['dense-worker-firm-200k']['candidate_over_baseline_parallel_setup'],
    ]
'''
new_active = '''    active = [
        result['cases']['dense-worker-firm-50k']['candidate_over_baseline_parallel_setup'],
        result['cases']['dense-worker-firm-100k']['candidate_over_baseline_parallel_setup'],
        result['cases']['dense-worker-firm-200k']['candidate_over_baseline_parallel_setup'],
    ]
'''
if text.count(old_active) != 1:
    raise SystemExit('historical active-case list changed unexpectedly')
text = text.replace(old_active, new_active, 1)

text = text.replace(
    "'active_parallel_setup_geometric_ratio_max': 0.985",
    "'active_parallel_setup_geometric_ratio_max': 0.99",
    1,
)
text = text.replace(
    'full qualification passed; parallel endpoint-first sorting reduced compact hierarchy comparison work without extra retained storage',
    'full qualification passed; dense-level parallel endpoint-first sorting reduced comparison work while sparse and grid-like levels retained the full comparator',
)
text = text.replace(
    'qualification passed but the parallel setup signal was too small or a setup, solve, or RSS regression gate was exceeded',
    'qualification passed but dense-level routing did not preserve a stable setup gain or a control/solve/RSS gate was exceeded',
)
text = text.replace(
    'Serial endpoint-first ordering and public graph construction remain unchanged.',
    'Serial endpoint-first ordering and public graph construction remain unchanged; the candidate routes only parallel compact levels with at least four edges per coarse vertex.',
)

old_plan = '''if checkpoint_heading not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
'''
new_plan = '''if checkpoint_heading in plan:
    start = plan.index(checkpoint_heading)
    end = plan.index(marker, start)
    plan = plan[:start] + checkpoint + plan[end:]
else:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
'''
if text.count(old_plan) != 1:
    raise SystemExit('historical plan checkpoint logic changed unexpectedly')
text = text.replace(old_plan, new_plan, 1)

required = (
    'raw.len() >= vertex_count.saturating_mul(4)',
    "('dense-worker-firm-50k', 'dense-worker-firm', 50_000)",
    'json.loads(completed.stdout[json_start:])',
    'dense-parallel-endpoint-sort-latest.json',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f'density-routed gate missing marker: {marker}')

exec(
    compile(text, 'scripts/dense_parallel_endpoint_sort_gate.py', 'exec'),
    {'__name__': '__main__'},
)
