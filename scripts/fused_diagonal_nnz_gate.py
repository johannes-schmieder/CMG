import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
GRAPH = Path('src/graph.rs')
SCRIPT = Path('scripts/fused_diagonal_nnz_gate.py')
WORKFLOW = Path('.github/workflows/fused-diagonal-nnz-gate.yml')
RECORD = Path('.ci/performance/fused-diagonal-nnz-latest.json')
STATUS = Path('PERFORMANCE_STATUS.md')
PLAN = Path('PERFORMANCE_PLAN.md')

baseline_source = GRAPH.read_text()
source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def run(command, *, env=None, timeout=9000, check=True):
    command = [str(value) for value in command]
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
        '--bin', 'graph-build',
        '--bin', 'hierarchy-alloc',
        '--bin', 'full-pcg-routing',
    ], env=env)
    return {
        'graph': target / 'release' / 'graph-build',
        'hierarchy': target / 'release' / 'hierarchy-alloc',
        'pcg': target / 'release' / 'full-pcg-routing',
    }


def apply_candidate():
    text = GRAPH.read_text()
    old = '''        let mut diagonal = vec![0.0; vertex_count];
        for edge in &raw {
            diagonal[edge.u()] += edge.weight;
            diagonal[edge.v()] += edge.weight;
        }

        let diagonal_nnz = diagonal.iter().filter(|degree| **degree != 0.0).count();
        let matrix_nnz = diagonal_nnz + 2 * raw.len();
'''
    new = '''        let mut diagonal = vec![0.0; vertex_count];
        let mut diagonal_nnz = 0usize;
        for edge in &raw {
            let u = edge.u();
            let v = edge.v();
            if diagonal[u] == 0.0 {
                diagonal_nnz += 1;
            }
            if diagonal[v] == 0.0 {
                diagonal_nnz += 1;
            }
            diagonal[u] += edge.weight;
            diagonal[v] += edge.weight;
        }

        let matrix_nnz = diagonal_nnz + 2 * raw.len();
'''
    if text.count(old) != 1:
        raise RuntimeError('diagonal metadata block changed unexpectedly')
    text = text.replace(old, new, 1)
    test = '''

#[cfg(test)]
mod fused_diagonal_nnz_tests {
    use super::Laplacian;

    #[test]
    fn fused_nonzero_count_includes_only_incident_vertices() {
        let graph = Laplacian::from_edges(
            6,
            [(0, 1, 1.0), (1, 2, 2.0), (0, 1, 3.0), (4, 5, 4.0)],
        )
        .unwrap();
        assert_eq!(graph.edge_count(), 3);
        assert_eq!(graph.matrix_nnz(), 5 + 2 * 3);
        assert_eq!(graph.diagonal()[3], 0.0);
    }
}
'''
    if 'mod fused_diagonal_nnz_tests' not in text:
        text += test
    GRAPH.write_text(text)


def sample(binary, arguments, tag):
    time_path = Path(f'/tmp/cmg-fused-diagonal-nnz-{tag}.time')
    completed = run([
        '/usr/bin/time', '-v', '-o', time_path,
        binary, *[str(value) for value in arguments],
    ])
    start = completed.stdout.find('{')
    if start < 0:
        raise RuntimeError(f'{tag}: benchmark JSON missing')
    payload = json.loads(completed.stdout[start:])
    match = re.search(
        r'Maximum resident set size \(kbytes\):\s*(\d+)',
        time_path.read_text(),
    )
    if match is None:
        raise RuntimeError(f'{tag}: peak RSS missing')
    payload['peak_rss_kib'] = int(match.group(1))
    return payload


def alternating_samples(baseline, candidate, arguments, tag):
    observations = {'baseline': [], 'candidate': []}
    order = (
        ('baseline', baseline),
        ('candidate', candidate),
        ('candidate', candidate),
        ('baseline', baseline),
    )
    for index, (label, binary) in enumerate(order):
        observations[label].append(sample(
            binary, arguments, f'{tag}-{label}-{index}'
        ))
    return observations


def median(observations, label, key):
    return statistics.median(item[key] for item in observations[label])


def compare_graph(baseline, candidate, name, case_name, scale):
    observations = alternating_samples(
        baseline, candidate, [case_name, scale, 5], f'graph-{name}'
    )
    stable = ('case', 'scale', 'vertices', 'raw_edges', 'retained_edges')
    reference = observations['baseline'][0]
    for observation in observations['baseline'][1:] + observations['candidate']:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f'{name}: graph field changed: {key}')
    baseline_time = median(observations, 'baseline', 'median_ns')
    candidate_time = median(observations, 'candidate', 'median_ns')
    baseline_rss = max(item['peak_rss_kib'] for item in observations['baseline'])
    candidate_rss = max(item['peak_rss_kib'] for item in observations['candidate'])
    return {
        'metadata': {key: reference[key] for key in stable},
        'baseline_median_ns': baseline_time,
        'candidate_median_ns': candidate_time,
        'candidate_over_baseline_time': candidate_time / baseline_time,
        'baseline_peak_rss_kib': baseline_rss,
        'candidate_peak_rss_kib': candidate_rss,
        'candidate_over_baseline_peak_rss': candidate_rss / baseline_rss,
    }


def compare_hierarchy(baseline, candidate, name, case_name, scale):
    observations = alternating_samples(
        baseline, candidate, [case_name, scale, 3], f'hierarchy-{name}'
    )
    stable = (
        'case', 'scale', 'vertices', 'edges', 'levels',
        'hierarchy_matrix_nonzeros', 'max_post_drop_delta_bytes',
    )
    reference = observations['baseline'][0]
    for observation in observations['baseline'][1:] + observations['candidate']:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f'{name}: hierarchy field changed: {key}')
    keys = (
        'median_ns', 'median_additional_peak_bytes', 'median_retained_bytes'
    )
    values = {
        key: (
            median(observations, 'baseline', key),
            median(observations, 'candidate', key),
        )
        for key in keys
    }
    baseline_rss = max(item['peak_rss_kib'] for item in observations['baseline'])
    candidate_rss = max(item['peak_rss_kib'] for item in observations['candidate'])
    return {
        'metadata': {key: reference[key] for key in stable},
        'baseline_median_ns': values['median_ns'][0],
        'candidate_median_ns': values['median_ns'][1],
        'candidate_over_baseline_time': values['median_ns'][1] / values['median_ns'][0],
        'candidate_over_baseline_additional_peak': (
            values['median_additional_peak_bytes'][1]
            / values['median_additional_peak_bytes'][0]
        ),
        'candidate_over_baseline_retained': (
            values['median_retained_bytes'][1] / values['median_retained_bytes'][0]
        ),
        'candidate_over_baseline_peak_rss': candidate_rss / baseline_rss,
    }


def compare_pcg(baseline, candidate, name, case_name, scale):
    left = sample(baseline, [case_name, scale, 3, 4], f'pcg-{name}-baseline')
    right = sample(candidate, [case_name, scale, 3, 4], f'pcg-{name}-candidate')
    stable = (
        'case', 'scale', 'vertices', 'input_edges', 'edges', 'levels',
        'operators', 'plan_bytes', 'workspace_bytes', 'auto_execution',
        'serial_iterations', 'planned_iterations',
    )
    for key in stable:
        if left[key] != right[key]:
            raise RuntimeError(f'{name}: PCG field changed: {key}')
    difference = max(left['max_scaled_difference'], right['max_scaled_difference'])
    if difference > 1.0e-8:
        raise RuntimeError(f'{name}: scaled solution difference too large')
    return {
        'metadata': {key: left[key] for key in stable},
        'candidate_over_baseline_serial_time': (
            right['serial_median_ns'] / left['serial_median_ns']
        ),
        'candidate_over_baseline_planned_time': (
            right['planned_median_ns'] / left['planned_median_ns']
        ),
        'maximum_scaled_difference': difference,
    }


result = {
    'schema_version': 1,
    'experiment': 'fused-diagonal-nonzero-count',
    'source_sha': source_sha,
    'validation': 'not_run',
    'accepted': False,
    'decision_reason': '',
    'graph_cases': {},
    'hierarchy_cases': {},
    'pcg_cases': {},
}

try:
    baseline = build(Path('/tmp/cmg-fused-diagonal-baseline'))
    apply_candidate()
    run(['cargo', 'fmt', '--all'])
    run(['cargo', 'fmt', '--all', '--', '--check'])
    run([
        'cargo', 'fmt', '--manifest-path', 'benchmarks/Cargo.toml',
        '--all', '--', '--check',
    ])
    run(['cargo', 'clippy', '--all-targets', '--all-features', '--', '-D', 'warnings'])
    run([
        'cargo', 'clippy', '--manifest-path', 'benchmarks/Cargo.toml',
        '--all-targets', '--', '-D', 'warnings',
    ])
    doc_env = os.environ.copy()
    doc_env['RUSTDOCFLAGS'] = '-D warnings'
    run(['cargo', 'doc', '--no-deps', '--document-private-items', '--all-features'], env=doc_env)
    run(['cargo', 'test', '--all-targets', '--all-features'])
    run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
    run(['cargo', 'build', '--release', '--all-features'])
    candidate = build(Path('/tmp/cmg-fused-diagonal-candidate'))
    result['validation'] = 'success'

    graph_specs = (
        ('unique-1m', 'unique', 1_000_000),
        ('duplicates-4-250k', 'duplicates-4', 250_000),
        ('coarse-collisions-100k', 'coarse-collisions', 100_000),
    )
    for name, case_name, scale in graph_specs:
        result['graph_cases'][name] = compare_graph(
            baseline['graph'], candidate['graph'], name, case_name, scale
        )

    hierarchy_specs = (
        ('path-1m', 'path', 1_000_000),
        ('worker-firm-1.5m', 'worker-firm', 500_000),
        ('dense-worker-firm-1.6m', 'dense-worker-firm', 100_000),
    )
    for name, case_name, scale in hierarchy_specs:
        result['hierarchy_cases'][name] = compare_hierarchy(
            baseline['hierarchy'], candidate['hierarchy'], name, case_name, scale
        )

    pcg_specs = (
        ('worker-firm-600k', 'worker-firm', 200_000),
        ('dense-worker-firm-800k', 'dense-worker-firm', 50_000),
    )
    for name, case_name, scale in pcg_specs:
        result['pcg_cases'][name] = compare_pcg(
            baseline['pcg'], candidate['pcg'], name, case_name, scale
        )

    geometric = lambda values: math.exp(
        sum(math.log(value) for value in values) / len(values)
    )
    graph_time = [item['candidate_over_baseline_time'] for item in result['graph_cases'].values()]
    graph_rss = [item['candidate_over_baseline_peak_rss'] for item in result['graph_cases'].values()]
    hierarchy_time = [item['candidate_over_baseline_time'] for item in result['hierarchy_cases'].values()]
    hierarchy_peak = [item['candidate_over_baseline_additional_peak'] for item in result['hierarchy_cases'].values()]
    hierarchy_retained = [item['candidate_over_baseline_retained'] for item in result['hierarchy_cases'].values()]
    hierarchy_rss = [item['candidate_over_baseline_peak_rss'] for item in result['hierarchy_cases'].values()]
    serial = [item['candidate_over_baseline_serial_time'] for item in result['pcg_cases'].values()]
    planned = [item['candidate_over_baseline_planned_time'] for item in result['pcg_cases'].values()]

    result['graph_geometric_time_ratio'] = geometric(graph_time)
    result['graph_worst_time_ratio'] = max(graph_time)
    result['hierarchy_geometric_time_ratio'] = geometric(hierarchy_time)
    result['hierarchy_worst_time_ratio'] = max(hierarchy_time)
    result['pcg_serial_geometric_time_ratio'] = geometric(serial)
    result['pcg_planned_geometric_time_ratio'] = geometric(planned)
    result['worst_peak_rss_ratio'] = max(graph_rss + hierarchy_rss)
    result['worst_additional_peak_ratio'] = max(hierarchy_peak)
    result['worst_retained_ratio'] = max(hierarchy_retained)
    result['acceptance_limits'] = {
        'graph_or_hierarchy_geometric_time_ratio_max': 0.99,
        'graph_worst_time_ratio_max': 1.03,
        'hierarchy_worst_time_ratio_max': 1.03,
        'pcg_serial_geometric_time_ratio_max': 1.03,
        'pcg_planned_geometric_time_ratio_max': 1.03,
        'worst_peak_rss_ratio_max': 1.02,
        'worst_additional_peak_ratio_max': 1.001,
        'worst_retained_ratio_max': 1.001,
    }
    material_gain = (
        result['graph_geometric_time_ratio'] <= 0.99
        or result['hierarchy_geometric_time_ratio'] <= 0.99
    )
    result['accepted'] = (
        material_gain
        and result['graph_worst_time_ratio'] <= 1.03
        and result['hierarchy_worst_time_ratio'] <= 1.03
        and result['pcg_serial_geometric_time_ratio'] <= 1.03
        and result['pcg_planned_geometric_time_ratio'] <= 1.03
        and result['worst_peak_rss_ratio'] <= 1.02
        and result['worst_additional_peak_ratio'] <= 1.001
        and result['worst_retained_ratio'] <= 1.001
    )
    result['decision_reason'] = (
        'full qualification passed; counting incident vertices during degree accumulation removed a separate diagonal scan with a material graph or hierarchy gain'
        if result['accepted']
        else 'qualification passed but the removed diagonal scan did not produce a stable material gain or a regression gate was exceeded'
    )
except Exception as error:
    result['validation'] = 'failure'
    result['decision_reason'] = f'experiment failed: {error}'
    result['error'] = repr(error)
    print(result['decision_reason'], flush=True)

if not result['accepted']:
    GRAPH.write_text(baseline_source)
run(['cargo', 'fmt', '--all'], check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

status = STATUS.read_text().rstrip()
heading = '## Fused diagonal nonzero-count gate\n'
decision = 'retained' if result['accepted'] else 'not retained'
block = f'''{heading}
- Decision: `{decision}`.
- Validation: `{result['validation']}`.
- Graph / hierarchy geometric time ratios: `{result.get('graph_geometric_time_ratio', 'n/a')}` / `{result.get('hierarchy_geometric_time_ratio', 'n/a')}`.
- Worst exact additional-peak / retained ratios: `{result.get('worst_additional_peak_ratio', 'n/a')}` / `{result.get('worst_retained_ratio', 'n/a')}`.
- Edge ordering, compensated duplicate aggregation, and degree arithmetic are unchanged.
- Evidence: `.ci/performance/fused-diagonal-nnz-latest.json`.
'''
if heading in status:
    start = status.index(heading)
    end = status.find('\n## ', start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + block + status[end:]
else:
    status += '\n\n' + block
STATUS.write_text(status.rstrip() + '\n')

plan = PLAN.read_text()
marker = '## Current next action\n'
checkpoint_heading = '### Fused diagonal nonzero-count checkpoint — 2026-08-23\n'
checkpoint = f'''{checkpoint_heading}
- Candidate was **{decision}**.
- Validation: `{result['validation']}`.
- Graph / hierarchy timing ratios: `{result.get('graph_geometric_time_ratio', 'n/a')}` / `{result.get('hierarchy_geometric_time_ratio', 'n/a')}`.
- Evidence: `.ci/performance/fused-diagonal-nnz-latest.json`.

'''
if checkpoint_heading not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
    prefix, _ = plan.split(marker, 1)
    plan = prefix + marker + '''
1. Re-profile graph finalization and hierarchy contraction after the diagonal-metadata decision.
2. Refresh cumulative performance and memory guidance from retained candidates only.
3. Run the manual 1–32 thread qualification on suitable hardware when available.
4. Defer additional sort variants unless new profiling exposes a larger stable opportunity.
'''
PLAN.write_text(plan)

SCRIPT.unlink(missing_ok=True)
WORKFLOW.unlink(missing_ok=True)
