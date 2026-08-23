import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
ANALYSIS = Path('.ci/performance/compact-hierarchy-index-analysis.json')


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


def build(target):
    env = os.environ.copy()
    env['CARGO_TARGET_DIR'] = str(target)
    run([
        'cargo', 'build', '--release',
        '--manifest-path', 'benchmarks/Cargo.toml',
        '--bin', 'hierarchy-build',
    ], env=env)
    return target / 'release' / 'hierarchy-build'


def sample(binary, arguments, tag):
    time_path = Path(f'/tmp/cmg-compact-hierarchy-index-{tag}.time')
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
        raise RuntimeError(f'unexpected hierarchy output: {payloads}')
    rss_match = re.search(
        r'Maximum resident set size \(kbytes\):\s*(\d+)',
        time_path.read_text(),
    )
    if rss_match is None:
        raise RuntimeError('peak RSS missing from /usr/bin/time output')
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
                raise RuntimeError(f'{name}: hierarchy metadata changed')
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


def choose_candidate(analysis):
    allowed = {
        'declaration', 'indexed_write', 'indexed_read',
        'construction', 'initialization',
    }
    ordered = []
    if analysis.get('selected'):
        ordered.append(analysis['selected'])
    ordered.extend(analysis.get('candidates', []))
    seen = set()
    for candidate in ordered:
        key = (candidate.get('path'), candidate.get('struct'), candidate.get('field'))
        if key in seen:
            continue
        seen.add(key)
        if candidate.get('score', 0) <= 0:
            continue
        categories = {
            reference.get('category')
            for reference in candidate.get('references', [])
        }
        if categories <= allowed and candidate.get('path', '').startswith('src/'):
            return candidate
    return None


def cast_expression(value):
    value = value.strip()
    if value in {'usize::MAX', 'u32::MAX'}:
        return 'u32::MAX'
    if re.fullmatch(r'\d+(?:usize)?', value):
        return f'u32::try_from({value}).expect("validated hierarchy index fits u32")'
    return f'u32::try_from({value}).expect("validated hierarchy index fits u32")'


def transform(candidate):
    source_path = Path(candidate['path'])
    field = candidate['field']
    text = source_path.read_text()
    original = text

    declaration = re.compile(
        rf'(?m)^(?P<indent>\s*)(?P<vis>pub(?:\([^)]*\))?\s+)?'
        rf'{re.escape(field)}\s*:\s*Vec\s*<\s*usize\s*>\s*,?\s*$'
    )
    matches = list(declaration.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(
            f'expected one {candidate["struct"]}.{field} Vec<usize> declaration; '
            f'found {len(matches)}'
        )
    match = matches[0]
    if match.group('vis'):
        raise RuntimeError('selected compact hierarchy index field is public')
    text = text[:match.start()] + match.group(0).replace('usize', 'u32') + text[match.end():]

    assignment = re.search(
        rf'(?m)^\s*{re.escape(field)}\s*:\s*(?P<local>\w+)\s*,\s*$',
        text,
    )
    shorthand = re.search(rf'(?m)^\s*{re.escape(field)}\s*,\s*$', text)
    local = assignment.group('local') if assignment else (field if shorthand else None)
    if local is None:
        raise RuntimeError(f'struct initialization for {field} was not recognized')

    text = re.sub(
        rf'\blet\s+(?P<mut>mut\s+)?{re.escape(local)}\s*:\s*Vec\s*<\s*usize\s*>',
        lambda found: (
            'let ' + (found.group('mut') or '') + local + ': Vec<u32>'
        ),
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
        r'0usize(?P<suffix>\s*;)',
        rf'\g<prefix>0u32\g<suffix>',
        text,
    )

    push_pattern = re.compile(
        rf'(?m)^(?P<indent>\s*){re.escape(local)}\.push\((?P<value>.+)\);\s*$'
    )
    for found in reversed(list(push_pattern.finditer(text))):
        replacement = (
            f"{found.group('indent')}{local}.push("
            f"{cast_expression(found.group('value'))});"
        )
        text = text[:found.start()] + replacement + text[found.end():]

    write_pattern = re.compile(
        rf'(?m)^(?P<indent>\s*){re.escape(local)}\s*'
        rf'\[(?P<index>[^\]\n]+)\]\s*=\s*(?P<value>[^;]+);\s*$'
    )
    for found in reversed(list(write_pattern.finditer(text))):
        replacement = (
            f"{found.group('indent')}{local}[{found.group('index')}] = "
            f"{cast_expression(found.group('value'))};"
        )
        text = text[:found.start()] + replacement + text[found.end():]

    owners = [field, local, f'self.{field}']
    for owner in sorted(set(owners), key=len, reverse=True):
        pattern = re.compile(
            rf'(?P<expr>(?<![\w.]){re.escape(owner)}\s*\[[^\]\n]+\])'
        )
        rebuilt = []
        position = 0
        for found in pattern.finditer(text):
            rebuilt.append(text[position:found.start()])
            tail = text[found.end():found.end() + 12]
            prefix = text[max(0, found.start() - 24):found.start()]
            if re.match(r'\s*=', tail) or prefix.rstrip().endswith('as usize'):
                rebuilt.append(found.group('expr'))
            else:
                rebuilt.append(f'({found.group("expr")} as usize)')
            position = found.end()
        rebuilt.append(text[position:])
        text = ''.join(rebuilt)

    if re.search(
        rf'\b(?:self\.)?{re.escape(field)}\b[^\n]*\.'
        r'(?:iter|chunks|windows|par_iter)',
        text,
    ):
        raise RuntimeError('unsupported iterator use remains for compact field')
    if re.search(
        rf'for\s+[^\n]+\b(?:self\.)?{re.escape(field)}\b',
        text,
    ):
        raise RuntimeError('unsupported direct loop remains for compact field')
    if text == original:
        raise RuntimeError('compact hierarchy index transformation made no changes')
    source_path.write_text(text)
    return source_path, original


def replace_section(text, heading, replacement, next_heading):
    if heading not in text:
        return text.replace(next_heading, replacement + next_heading, 1)
    start = text.index(heading)
    end = text.index(next_heading, start)
    return text[:start] + replacement + text[end:]


analysis = json.loads(ANALYSIS.read_text()) if ANALYSIS.exists() else {}
if analysis.get('analysis') != 'compact-hierarchy-index-layout':
    print('compact hierarchy-index analysis is unresolved; leaving gate armed')
    raise SystemExit(0)

candidate = choose_candidate(analysis)
result = {
    'schema_version': 2,
    'experiment': 'compact-hierarchy-index-u32',
    'baseline_sha': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], text=True
    ).strip(),
    'analysis_source_sha': analysis.get('source_sha'),
    'selected': candidate,
    'validation': 'not_run',
    'accepted': False,
    'cases': {},
}
original_path = None
original_source = None

try:
    if candidate is None:
        result['validation'] = 'skipped'
        result['decision_reason'] = 'no mechanically bounded private hierarchy index vector was available'
    else:
        baseline = build(Path('/tmp/cmg-compact-index-baseline'))
        original_path, original_source = transform(candidate)
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
        candidate_binary = build(Path('/tmp/cmg-compact-index-candidate'))
        result['validation'] = 'success'

        specs = [
            ('path-1m', ['path', 1_000_000, 1]),
            ('worker-firm-3m', ['worker-firm', 1_000_000, 1]),
            ('dense-worker-firm-3.2m', ['dense-worker-firm', 200_000, 1]),
        ]
        baseline_binary = Path('/tmp/cmg-compact-index-baseline/release/hierarchy-build')
        time_ratios = []
        rss_ratios = []
        for name, arguments in specs:
            comparison = compare_case(
                baseline_binary, candidate_binary, arguments, name
            )
            result['cases'][name] = comparison
            time_ratios.append(comparison['candidate_over_baseline_time'])
            rss_ratios.append(comparison['candidate_over_baseline_peak_rss'])

        result['geometric_candidate_over_baseline_time'] = math.exp(
            sum(math.log(value) for value in time_ratios) / len(time_ratios)
        )
        result['worst_candidate_over_baseline_time'] = max(time_ratios)
        result['best_candidate_over_baseline_peak_rss'] = min(rss_ratios)
        result['worst_candidate_over_baseline_peak_rss'] = max(rss_ratios)
        result['geometric_candidate_over_baseline_peak_rss'] = math.exp(
            sum(math.log(value) for value in rss_ratios) / len(rss_ratios)
        )
        result['acceptance_limits'] = {
            'geometric_time_ratio_max': 1.04,
            'per_case_time_ratio_max': 1.10,
            'at_least_one_peak_rss_ratio_max': 0.985,
            'geometric_peak_rss_ratio_max': 0.995,
            'worst_peak_rss_ratio_max': 1.02,
        }
        result['accepted'] = (
            result['geometric_candidate_over_baseline_time'] <= 1.04
            and result['worst_candidate_over_baseline_time'] <= 1.10
            and result['best_candidate_over_baseline_peak_rss'] <= 0.985
            and result['geometric_candidate_over_baseline_peak_rss'] <= 0.995
            and result['worst_candidate_over_baseline_peak_rss'] <= 1.02
        )
        result['decision_reason'] = (
            'full qualification passed; compact hierarchy indices reduced large-graph memory without material setup regression'
            if result['accepted']
            else 'qualification passed but the setup-time or large-graph memory gate was not met'
        )
except Exception as error:
    result['error'] = repr(error)
    result['decision_reason'] = f'compact hierarchy-index experiment failed: {error}'
    print(result['decision_reason'], flush=True)

if not result['accepted'] and original_path is not None and original_source is not None:
    original_path.write_text(original_source)
    run(['cargo', 'fmt', '--all'], check=False)

record = Path('.ci/performance/compact-hierarchy-index-latest.json')
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

selected_text = (
    f'`{candidate["struct"]}.{candidate["field"]}` in `{candidate["path"]}`'
    if candidate else 'none'
)
rows = []
for name, case in result.get('cases', {}).items():
    rows.append(
        f"| {name} | {case['candidate_over_baseline_time']:.3f}x | "
        f"{case['candidate_over_baseline_peak_rss']:.3f}x |"
    )
checkpoint = f'''### Compact hierarchy-index checkpoint — 2026-08-23

- Candidate: {selected_text}.
- Decision: **{'retained' if result['accepted'] else 'not retained'}**.
- Validation: `{result['validation']}`.
- Reason: {result.get('decision_reason', 'no decision recorded')}.

| Case | Setup time ratio | Peak-RSS ratio |
|---|---:|---:|
''' + ('\n'.join(rows) if rows else '| no completed benchmark cases | — | — |') + '''

- The public `usize` API is preserved. Storage conversion is checked at construction boundaries, and hierarchy metadata must match exactly.
- Machine-readable evidence: `.ci/performance/compact-hierarchy-index-latest.json`.

'''
plan_path = Path('PERFORMANCE_PLAN.md')
plan_path.write_text(replace_section(
    plan_path.read_text(),
    '### Compact hierarchy-index checkpoint — 2026-08-23\n',
    checkpoint,
    '## Current next action\n',
))

status_path = Path('PERFORMANCE_STATUS.md')
status = status_path.read_text().rstrip()
heading = '## Compact hierarchy-index gate\n'
block = (
    '## Compact hierarchy-index gate\n\n'
    f'- Candidate: {selected_text}.\n'
    f"- Decision: `{'retained' if result['accepted'] else 'not retained'}`.\n"
    f"- Validation: `{result['validation']}`.\n"
    f"- Geometric setup ratio: `{result.get('geometric_candidate_over_baseline_time', float('nan')):.3f}x`.\n"
    f"- Geometric peak-RSS ratio: `{result.get('geometric_candidate_over_baseline_peak_rss', float('nan')):.3f}x`.\n"
    '- Evidence: `.ci/performance/compact-hierarchy-index-latest.json`.\n'
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

Path('.github/workflows/compact-hierarchy-index-v2.yml').unlink(missing_ok=True)
Path('scripts/compact_hierarchy_index_gate_v2.py').unlink(missing_ok=True)
