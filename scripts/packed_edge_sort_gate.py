import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
ANALYSIS = Path('.ci/performance/packed-edge-sort-analysis.json')
GRAPH = Path('src/graph.rs')


def run(command, *, env=None, timeout=7200, check=True):
    print('+', ' '.join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end='')
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
        )
    return completed


def build(target, binary):
    env = os.environ.copy()
    env['CARGO_TARGET_DIR'] = str(target)
    run([
        'cargo', 'build', '--release',
        '--manifest-path', 'benchmarks/Cargo.toml',
        '--bin', binary,
    ], env=env)
    return target / 'release' / binary


def sample(binary, arguments, tag):
    time_path = Path(f'/tmp/cmg-packed-sort-{tag}.time')
    completed = run([
        '/usr/bin/time', '-v', '-o', str(time_path),
        str(binary), *[str(value) for value in arguments],
    ])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'unexpected benchmark output: {payloads}')
    rss_match = re.search(
        r'Maximum resident set size \(kbytes\):\s*(\d+)', time_path.read_text()
    )
    if rss_match is None:
        raise RuntimeError('peak RSS missing')
    return {
        'median_ns': payloads[0]['median_ns'],
        'peak_rss_kib': int(rss_match.group(1)),
        'metadata': payloads[0],
    }


def compare_case(baseline, candidate, arguments, name):
    observations = {'baseline': [], 'candidate': []}
    for index, (label, binary) in enumerate((
        ('baseline', baseline),
        ('candidate', candidate),
        ('candidate', candidate),
        ('baseline', baseline),
    )):
        observations[label].append(
            sample(binary, arguments, f'{name}-{label}-{index}')
        )
    reference = observations['baseline'][0]['metadata']
    for group in observations.values():
        for item in group:
            if item['metadata'] != reference:
                raise RuntimeError(f'{name}: benchmark metadata changed')
    baseline_time = statistics.median(
        item['median_ns'] for item in observations['baseline']
    )
    candidate_time = statistics.median(
        item['median_ns'] for item in observations['candidate']
    )
    baseline_rss = max(item['peak_rss_kib'] for item in observations['baseline'])
    candidate_rss = max(item['peak_rss_kib'] for item in observations['candidate'])
    return {
        'arguments': arguments,
        'baseline_median_ns': baseline_time,
        'candidate_median_ns': candidate_time,
        'candidate_over_baseline_time': candidate_time / baseline_time,
        'baseline_peak_rss_kib': baseline_rss,
        'candidate_peak_rss_kib': candidate_rss,
        'candidate_over_baseline_peak_rss': candidate_rss / baseline_rss,
        'metadata': reference,
    }


def edge_endpoint_fields(text):
    struct_match = re.search(
        r'(?ms)(?:pub(?:\([^)]*\))?\s+)?struct\s+Edge\s*\{(?P<body>.*?)\n\}',
        text,
    )
    if struct_match is None:
        raise RuntimeError('Edge struct was not found')
    fields = re.findall(
        r'(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?P<name>\w+)\s*:\s*u32\s*,?\s*$',
        struct_match.group('body'),
    )
    if len(fields) < 2:
        raise RuntimeError('fewer than two u32 Edge fields were found')
    return fields[0], fields[1]


def add_pair_key(text, first_field, second_field):
    if 'packed_endpoint_key' in text:
        return text
    impl_match = re.search(r'(?m)^impl\s+Edge\s*\{\s*$', text)
    if impl_match is None:
        raise RuntimeError('impl Edge block was not found')
    insertion = f'''
    #[inline]
    pub(crate) fn packed_endpoint_key(&self) -> u64 {{
        (u64::from(self.{first_field}) << 32) | u64::from(self.{second_field})
    }}
'''
    return text[:impl_match.end()] + insertion + text[impl_match.end():]


def patch_comparators(path, text):
    direct = re.compile(
        r'''(?P<left>\w+)\.(?P<first>\w+)(?P<first_call>\(\))?\.cmp\(\s*&(?P<right>\w+)\.(?P=first)(?P=first_call)\s*\)'''
        r'''\s*\.then_with\(\s*\|\|\s*'''
        r'''(?P=left)\.(?P<second>\w+)(?P<second_call>\(\))?\.cmp\(\s*&(?P=right)\.(?P=second)(?P=second_call)\s*\)\s*\)''',
        flags=re.MULTILINE,
    )
    tuple_pattern = re.compile(
        r'''\(\s*(?P<left>\w+)\.(?P<first>\w+)(?P<first_call>\(\))?\s*,\s*'''
        r'''(?P=left)\.(?P<second>\w+)(?P<second_call>\(\))?\s*\)'''
        r'''\.cmp\(\s*&\(\s*(?P<right>\w+)\.(?P=first)(?P=first_call)\s*,\s*'''
        r'''(?P=right)\.(?P=second)(?P=second_call)\s*\)\s*\)''',
        flags=re.MULTILINE,
    )

    count = 0
    def replace_direct(match):
        nonlocal count
        count += 1
        return (
            f"{match.group('left')}.packed_endpoint_key().cmp("
            f"&{match.group('right')}.packed_endpoint_key())"
        )
    text = direct.sub(replace_direct, text)

    def replace_tuple(match):
        nonlocal count
        count += 1
        return (
            f"{match.group('left')}.packed_endpoint_key().cmp("
            f"&{match.group('right')}.packed_endpoint_key())"
        )
    text = tuple_pattern.sub(replace_tuple, text)
    return text, count


def replace_section(text, heading, replacement, next_heading):
    if heading not in text:
        return text.replace(next_heading, replacement + next_heading, 1)
    start = text.index(heading)
    end = text.index(next_heading, start)
    return text[:start] + replacement + text[end:]


analysis = json.loads(ANALYSIS.read_text()) if ANALYSIS.exists() else {}
if analysis.get('analysis') != 'packed-edge-sort-sites':
    print('packed endpoint-key sort analysis is unresolved; leaving gate armed')
    raise SystemExit(0)

original_sources = {}
result = {
    'schema_version': 1,
    'experiment': 'packed-endpoint-key-sort',
    'baseline_sha': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], text=True
    ).strip(),
    'analysis_source_sha': analysis.get('source_sha'),
    'validation': 'not_run',
    'accepted': False,
    'patched_site_count': 0,
    'cases': {},
}

try:
    if analysis.get('candidate_count', 0) == 0:
        result['validation'] = 'skipped'
        result['decision_reason'] = 'all recognized endpoint sort sites already use packed keys or have no eligible comparator'
    else:
        baseline_graph = build(Path('/tmp/cmg-packed-sort-graph-baseline'), 'graph-build')
        baseline_hierarchy = build(
            Path('/tmp/cmg-packed-sort-hierarchy-baseline'), 'hierarchy-build'
        )

        graph_text = GRAPH.read_text()
        original_sources[GRAPH] = graph_text
        first_field, second_field = edge_endpoint_fields(graph_text)
        GRAPH.write_text(add_pair_key(graph_text, first_field, second_field))

        patched_sites = 0
        candidate_paths = sorted({
            Path(site['path'])
            for site in analysis.get('sites', [])
            if site.get('candidate_for_packed_pair_key')
        })
        for path in candidate_paths:
            text = path.read_text()
            original_sources.setdefault(path, text)
            patched, count = patch_comparators(path, text)
            if count:
                path.write_text(patched)
                patched_sites += count
        result['patched_site_count'] = patched_sites
        if patched_sites == 0:
            raise RuntimeError('analysis found candidate sites but no recognized comparator was patched')

        run(['cargo', 'fmt', '--all'])
        run(['cargo', 'fmt', '--manifest-path', 'benchmarks/Cargo.toml', '--all'])
        run(['cargo', 'fmt', '--all', '--', '--check'])
        run([
            'cargo', 'fmt', '--manifest-path', 'benchmarks/Cargo.toml',
            '--all', '--', '--check',
        ])
        run([
            'cargo', 'clippy', '--all-targets', '--all-features',
            '--', '-D', 'warnings',
        ])
        run([
            'cargo', 'clippy', '--manifest-path', 'benchmarks/Cargo.toml',
            '--all-targets', '--', '-D', 'warnings',
        ])
        run(['cargo', 'test', '--all-targets'])
        run(['cargo', 'test', '--all-targets', '--release'])
        run(['cargo', 'test', '--all-targets', '--all-features'])
        run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
        run(['cargo', 'build', '--release', '--all-features'])
        candidate_graph = build(
            Path('/tmp/cmg-packed-sort-graph-candidate'), 'graph-build'
        )
        candidate_hierarchy = build(
            Path('/tmp/cmg-packed-sort-hierarchy-candidate'), 'hierarchy-build'
        )
        result['validation'] = 'success'

        specs = [
            ('path-1m', ['path', 1_000_000, 1]),
            ('worker-firm-3m', ['worker-firm', 1_000_000, 1]),
            ('dense-worker-firm-3.2m', ['dense-worker-firm', 200_000, 1]),
        ]
        time_ratios = []
        rss_ratios = []
        for name, arguments in specs:
            graph_comparison = compare_case(
                baseline_graph, candidate_graph, arguments, f'graph-{name}'
            )
            hierarchy_comparison = compare_case(
                baseline_hierarchy,
                candidate_hierarchy,
                arguments,
                f'hierarchy-{name}',
            )
            result['cases'][name] = {
                'graph': graph_comparison,
                'hierarchy': hierarchy_comparison,
            }
            time_ratios.extend([
                graph_comparison['candidate_over_baseline_time'],
                hierarchy_comparison['candidate_over_baseline_time'],
            ])
            rss_ratios.extend([
                graph_comparison['candidate_over_baseline_peak_rss'],
                hierarchy_comparison['candidate_over_baseline_peak_rss'],
            ])

        result['geometric_candidate_over_baseline_time'] = math.exp(
            sum(math.log(value) for value in time_ratios) / len(time_ratios)
        )
        result['worst_candidate_over_baseline_time'] = max(time_ratios)
        result['geometric_candidate_over_baseline_peak_rss'] = math.exp(
            sum(math.log(value) for value in rss_ratios) / len(rss_ratios)
        )
        result['worst_candidate_over_baseline_peak_rss'] = max(rss_ratios)
        result['acceptance_limits'] = {
            'geometric_time_ratio_max': 0.985,
            'per_case_time_ratio_max': 1.04,
            'geometric_peak_rss_ratio_max': 1.01,
            'worst_peak_rss_ratio_max': 1.03,
        }
        result['accepted'] = (
            result['geometric_candidate_over_baseline_time'] <= 0.985
            and result['worst_candidate_over_baseline_time'] <= 1.04
            and result['geometric_candidate_over_baseline_peak_rss'] <= 1.01
            and result['worst_candidate_over_baseline_peak_rss'] <= 1.03
        )
        result['decision_reason'] = (
            'full qualification passed; packed endpoint keys materially improved graph/hierarchy setup'
            if result['accepted']
            else 'qualification passed but the large-graph setup-time gate was not met'
        )
except Exception as error:
    result['error'] = repr(error)
    result['decision_reason'] = f'packed endpoint-key sort experiment failed: {error}'
    print(result['decision_reason'], flush=True)

if not result['accepted']:
    for path, source in original_sources.items():
        path.write_text(source)
    run(['cargo', 'fmt', '--all'], check=False)

record = Path('.ci/performance/packed-edge-sort-latest.json')
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

rows = []
for name, case in result.get('cases', {}).items():
    rows.append(
        f"| {name} graph | {case['graph']['candidate_over_baseline_time']:.3f}x | "
        f"{case['graph']['candidate_over_baseline_peak_rss']:.3f}x |"
    )
    rows.append(
        f"| {name} hierarchy | {case['hierarchy']['candidate_over_baseline_time']:.3f}x | "
        f"{case['hierarchy']['candidate_over_baseline_peak_rss']:.3f}x |"
    )
checkpoint = f'''### Packed endpoint-key sort checkpoint — 2026-08-23

- Recognized comparator sites patched: `{result['patched_site_count']}`.
- Decision: **{'retained' if result['accepted'] else 'not retained'}**.
- Validation: `{result['validation']}`.
- Reason: {result.get('decision_reason', 'no decision recorded')}.

| Case | Time ratio | Peak-RSS ratio |
|---|---:|---:|
''' + ('\n'.join(rows) if rows else '| no completed benchmark cases | — | — |') + '''

- Graph and hierarchy metadata were required to remain exactly unchanged.
- Machine-readable evidence: `.ci/performance/packed-edge-sort-latest.json`.

'''
plan_path = Path('PERFORMANCE_PLAN.md')
plan_path.write_text(replace_section(
    plan_path.read_text(),
    '### Packed endpoint-key sort checkpoint — 2026-08-23\n',
    checkpoint,
    '## Current next action\n',
))

status_path = Path('PERFORMANCE_STATUS.md')
status = status_path.read_text().rstrip()
heading = '## Packed endpoint-key sort gate\n'
block = (
    '## Packed endpoint-key sort gate\n\n'
    f"- Decision: `{'retained' if result['accepted'] else 'not retained'}`.\n"
    f"- Validation: `{result['validation']}`.\n"
    f"- Patched comparator sites: `{result['patched_site_count']}`.\n"
    f"- Geometric setup ratio: `{result.get('geometric_candidate_over_baseline_time', float('nan')):.3f}x`.\n"
    '- Evidence: `.ci/performance/packed-edge-sort-latest.json`.\n'
)
if heading in status:
    start = status.index(heading)
    end = status.find('\n## ', start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + block + status[end:]
else:
    status += '\n\n' + block
status_path.write_text(status.rstrip() + '\n')

Path('.github/workflows/packed-edge-sort-gate.yml').unlink(missing_ok=True)
Path('scripts/packed_edge_sort_gate.py').unlink(missing_ok=True)
