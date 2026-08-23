from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path('src/graph.rs')
RECORD = Path('.ci/performance/unstable-edge-sort-latest.json')
PLAN = Path('PERFORMANCE_PLAN.md')
STATUS = Path('PERFORMANCE_STATUS.md')
WORKFLOW = Path('.github/workflows/unstable-edge-sort-gate.yml')
SCRIPT = Path('scripts/unstable_edge_sort_gate.py')
BASELINE_SOURCE = SOURCE.read_text()
BASE_SHA = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def run(command: list[str], *, env: dict[str, str] | None = None, timeout: int = 9000) -> subprocess.CompletedProcess[str]:
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
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}")
    return completed


def build(target: Path) -> Path:
    env = os.environ.copy()
    env['CARGO_TARGET_DIR'] = str(target)
    run([
        'cargo', 'build', '--release', '--manifest-path', 'benchmarks/Cargo.toml',
        '--bin', 'hierarchy-alloc',
    ], env=env)
    return target / 'release' / 'hierarchy-alloc'


def parse_payload(output: str) -> dict[str, object]:
    payloads = [
        json.loads(line)
        for line in output.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'unexpected benchmark output: {payloads}')
    return payloads[0]


def sample(binary: Path, arguments: list[str], tag: str) -> dict[str, object]:
    time_path = Path(f'/tmp/cmg-unstable-sort-{tag}.time')
    completed = run([
        '/usr/bin/time', '-v', '-o', str(time_path),
        str(binary), *arguments,
    ])
    payload = parse_payload(completed.stdout)
    match = re.search(
        r'Maximum resident set size \(kbytes\):\s*(\d+)',
        time_path.read_text(),
    )
    if match is None:
        raise RuntimeError('peak RSS missing')
    payload['peak_rss_kib'] = int(match.group(1))
    return payload


def compare_case(
    baseline: Path,
    candidate: Path,
    arguments: list[str],
    name: str,
) -> dict[str, object]:
    baseline_samples: list[dict[str, object]] = []
    candidate_samples: list[dict[str, object]] = []
    order = (
        ('baseline', baseline),
        ('candidate', candidate),
        ('candidate', candidate),
        ('baseline', baseline),
    )
    for index, (label, binary) in enumerate(order):
        observation = sample(binary, arguments, f'{name}-{label}-{index}')
        (baseline_samples if label == 'baseline' else candidate_samples).append(observation)

    stable_keys = (
        'case', 'scale', 'vertices', 'edges', 'repetitions', 'levels',
        'hierarchy_matrix_nonzeros', 'median_retained_bytes',
    )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable_keys:
            if observation[key] != reference[key]:
                raise RuntimeError(
                    f'{name} changed invariant {key}: '
                    f'{reference[key]!r} != {observation[key]!r}'
                )

    baseline_time = statistics.median(
        int(item['median_ns']) for item in baseline_samples
    )
    candidate_time = statistics.median(
        int(item['median_ns']) for item in candidate_samples
    )
    baseline_peak = statistics.median(
        int(item['median_additional_peak_bytes']) for item in baseline_samples
    )
    candidate_peak = statistics.median(
        int(item['median_additional_peak_bytes']) for item in candidate_samples
    )
    baseline_rss = max(int(item['peak_rss_kib']) for item in baseline_samples)
    candidate_rss = max(int(item['peak_rss_kib']) for item in candidate_samples)

    return {
        'arguments': arguments,
        'baseline_median_ns': baseline_time,
        'candidate_median_ns': candidate_time,
        'candidate_over_baseline_time': candidate_time / baseline_time,
        'baseline_additional_peak_bytes': baseline_peak,
        'candidate_additional_peak_bytes': candidate_peak,
        'candidate_over_baseline_additional_peak': candidate_peak / baseline_peak,
        'baseline_peak_rss_kib': baseline_rss,
        'candidate_peak_rss_kib': candidate_rss,
        'candidate_over_baseline_peak_rss': candidate_rss / baseline_rss,
        'metadata': {key: reference[key] for key in stable_keys},
        'baseline_max_post_drop_delta_bytes': max(
            int(item['max_post_drop_delta_bytes']) for item in baseline_samples
        ),
        'candidate_max_post_drop_delta_bytes': max(
            int(item['max_post_drop_delta_bytes']) for item in candidate_samples
        ),
    }


def update_documents(result: dict[str, object]) -> None:
    accepted = bool(result.get('accepted'))
    cases = result.get('cases', {})
    assert isinstance(cases, dict)
    status_word = 'retained' if accepted else 'not retained'
    checkpoint = f'''### Unstable compact-edge sort checkpoint — 2026-08-23

- Replacing stable comparison sorting with in-place unstable comparison sorting
  was **{status_word}**. The comparator remains a total order on packed endpoint
  key and weight, so equal-comparator edges are numerically identical.
- Geometric hierarchy-time ratio: {result.get('geometric_time_ratio', float('nan')):.3f}.
- Geometric exact additional-peak ratio:
  {result.get('geometric_additional_peak_ratio', float('nan')):.3f}.
- Worst per-case time ratio: {result.get('worst_time_ratio', float('nan')):.3f}.
- Full qualification status: `{result.get('validation')}`.
- Machine-readable evidence:
  `.ci/performance/unstable-edge-sort-latest.json`.

'''
    plan = PLAN.read_text()
    marker = '## Current next action\n'
    if marker not in plan:
        raise RuntimeError('PERFORMANCE_PLAN current-action marker missing')
    prefix = plan.split(marker, 1)[0]
    if '### Unstable compact-edge sort checkpoint — 2026-08-23' not in prefix:
        prefix += checkpoint
    if accepted:
        actions = '''## Current next action

1. Re-run the hierarchy phase profiler on the retained in-place unstable sort
   to quantify the remaining contraction share.
2. Benchmark a safe deterministic radix endpoint ordering only if contraction
   remains the dominant phase after the lower-memory in-place win.
3. Preserve exact weight ordering within duplicate endpoint groups,
   compensated summation, path performance, and requested-allocation limits.
4. Obtain controlled 8-, 16-, and 32-thread/high-memory evidence when suitable
   hardware is available.
'''
    else:
        actions = '''## Current next action

1. Benchmark a safe deterministic radix endpoint ordering with explicit scratch
   memory accounting; retain the current comparison sort unless the full
   hierarchy gate improves materially.
2. Preserve exact weight ordering within duplicate endpoint groups,
   compensated summation, path performance, and requested-allocation limits.
3. Obtain controlled 8-, 16-, and 32-thread/high-memory evidence when suitable
   hardware is available.
'''
    PLAN.write_text(prefix.rstrip() + '\n\n' + checkpoint + actions)

    status_text = STATUS.read_text()
    start = '## Next prepared optimization\n'
    end = '## Remaining major work\n'
    if start not in status_text or end not in status_text:
        raise RuntimeError('PERFORMANCE_STATUS markers missing')
    before, remainder = status_text.split(start, 1)
    _, after = remainder.split(end, 1)
    if accepted:
        section = f'''## Next prepared optimization

The in-place unstable compact-edge sort was retained after exact hierarchy and
allocation qualification. Its geometric hierarchy-time ratio was
{result['geometric_time_ratio']:.3f}, and its geometric exact additional-peak
ratio was {result['geometric_additional_peak_ratio']:.3f}.

The next checkpoint is a read-only phase-profile refresh. A custom radix path
will be considered only if contraction remains dominant after this simpler
in-place improvement.

'''
    else:
        section = '''## Next prepared optimization

The in-place unstable compact-edge sort did not meet the full time/memory gate
and was reverted. The next candidate is a safe deterministic radix ordering of
packed endpoint keys with explicit scratch-memory accounting and unchanged
within-pair weight ordering.

'''
    STATUS.write_text(before + section + end + after)


result: dict[str, object] = {
    'schema_version': 1,
    'experiment': 'unstable-compact-edge-sort',
    'baseline_sha': BASE_SHA,
    'validation': 'not_run',
    'accepted': False,
    'cases': {},
}

try:
    baseline_binary = build(Path('/tmp/cmg-unstable-sort-baseline'))

    old = 'raw.sort_by(compare_raw_edges);'
    count = BASELINE_SOURCE.count(old)
    if count != 4:
        raise RuntimeError(f'expected four serial raw sort sites, found {count}')
    SOURCE.write_text(BASELINE_SOURCE.replace(old, 'raw.sort_unstable_by(compare_raw_edges);'))

    run(['cargo', 'fmt', '--all'])
    run([
        'cargo', 'fmt', '--manifest-path', 'benchmarks/Cargo.toml', '--all',
    ])
    run(['cargo', 'fmt', '--all', '--', '--check'])
    run([
        'cargo', 'fmt', '--manifest-path', 'benchmarks/Cargo.toml',
        '--all', '--', '--check',
    ])
    run([
        'cargo', 'clippy', '--all-targets', '--all-features', '--', '-D', 'warnings',
    ])
    run([
        'cargo', 'clippy', '--manifest-path', 'benchmarks/Cargo.toml',
        '--all-targets', '--', '-D', 'warnings',
    ])
    doc_env = os.environ.copy()
    doc_env['RUSTDOCFLAGS'] = '-D warnings'
    run([
        'cargo', 'doc', '--no-deps', '--document-private-items', '--all-features',
    ], env=doc_env)
    run(['cargo', 'test', '--all-targets', '--all-features'])
    run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
    run(['cargo', 'build', '--release', '--all-features'])

    candidate_binary = build(Path('/tmp/cmg-unstable-sort-candidate'))
    result['validation'] = 'success'

    specs = [
        ('path-1m', ['path', '1000000', '3']),
        ('worker-firm-1.5m', ['worker-firm', '500000', '3']),
        ('dense-worker-firm-1.6m', ['dense-worker-firm', '100000', '3']),
    ]
    cases: dict[str, object] = {}
    for name, arguments in specs:
        cases[name] = compare_case(
            baseline_binary, candidate_binary, arguments, name
        )
    result['cases'] = cases

    time_ratios = [
        float(case['candidate_over_baseline_time'])
        for case in cases.values()
        if isinstance(case, dict)
    ]
    peak_ratios = [
        float(case['candidate_over_baseline_additional_peak'])
        for case in cases.values()
        if isinstance(case, dict)
    ]
    geometric_time = math.exp(
        sum(math.log(value) for value in time_ratios) / len(time_ratios)
    )
    geometric_peak = math.exp(
        sum(math.log(value) for value in peak_ratios) / len(peak_ratios)
    )
    result.update({
        'geometric_time_ratio': geometric_time,
        'geometric_additional_peak_ratio': geometric_peak,
        'worst_time_ratio': max(time_ratios),
        'worst_additional_peak_ratio': max(peak_ratios),
        'acceptance_limits': {
            'geometric_time_ratio_max_if_primary': 0.99,
            'geometric_additional_peak_ratio_max_if_primary': 0.90,
            'worst_time_ratio_max': 1.04,
            'worst_additional_peak_ratio_max': 1.02,
        },
    })
    result['accepted'] = (
        (geometric_time <= 0.99 or geometric_peak <= 0.90)
        and max(time_ratios) <= 1.04
        and max(peak_ratios) <= 1.02
    )
    result['decision_reason'] = (
        'full numerical qualification passed and the in-place sort met the time/memory gate'
        if result['accepted']
        else 'qualification passed but the time/memory gate was not met'
    )
except Exception as error:
    result['validation'] = 'failure'
    result['decision_reason'] = f'experiment failed: {error}'
    result['error'] = repr(error)

if not result.get('accepted'):
    SOURCE.write_text(BASELINE_SOURCE)
    run(['cargo', 'fmt', '--all'], timeout=600)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
update_documents(result)
WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
try:
    SCRIPT.parent.rmdir()
except OSError:
    pass

run(['git', 'config', 'user.name', 'github-actions[bot]'])
run([
    'git', 'config', 'user.email',
    '41898282+github-actions[bot]@users.noreply.github.com',
])
run(['git', 'add', '-A'])
message = (
    'perf: use in-place unstable compact-edge sorting'
    if result.get('accepted')
    else 'perf: record unstable compact-edge sort experiment'
)
run(['git', 'commit', '-m', message])
run(['git', 'pull', '--rebase', 'origin', 'main'])
run(['git', 'push', 'origin', 'HEAD:main'])
