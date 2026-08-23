import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
FOREST = Path('src/forest.rs')
HIERARCHY = Path('src/hierarchy.rs')
COARSEN = Path('src/coarsen.rs')
SCRIPT = Path('scripts/lean_forest_hierarchy_gate.py')
WORKFLOW = Path('.github/workflows/lean-forest-hierarchy-gate.yml')
RECORD = Path('.ci/performance/lean-forest-hierarchy-latest.json')
STATUS = Path('PERFORMANCE_STATUS.md')
PLAN = Path('PERFORMANCE_PLAN.md')

baseline_sources = {
    FOREST: FOREST.read_text(),
    HIERARCHY: HIERARCHY.read_text(),
    COARSEN: COARSEN.read_text(),
}
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
        '--bin', 'hierarchy-alloc',
        '--bin', 'full-pcg-routing',
    ], env=env)
    return {
        'hierarchy': target / 'release' / 'hierarchy-alloc',
        'pcg': target / 'release' / 'full-pcg-routing',
    }


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label} changed unexpectedly; matches={count}')
    return text.replace(old, new, 1)


def apply_candidate():
    forest = FOREST.read_text()
    forest = replace_once(
        forest,
        '''
    pub(crate) fn into_aggregation_parts(self) -> (Vec<usize>, Vec<usize>) {
        (self.labels, self.sizes)
    }
''',
        '\n',
        'obsolete ForestGrouping ownership method',
    )

    serial_block = '''pub fn build_forest_grouping(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
) -> Result<ForestGrouping, CmgError> {
    validate_low_effective_degree_threshold(low_effective_degree_threshold)?;
    let (heavy_parent, selected_weight) = maximum_weight_forest(graph);
    finish_forest_grouping(
        graph,
        low_effective_degree_threshold,
        heavy_parent,
        selected_weight,
    )
}
'''
    serial_new = serial_block + '''
/// Construct only the aggregation data needed by hierarchy construction.
pub(crate) fn build_forest_aggregation_labels(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
) -> Result<(Vec<usize>, usize), CmgError> {
    validate_low_effective_degree_threshold(low_effective_degree_threshold)?;
    let (heavy_parent, selected_weight) = maximum_weight_forest(graph);
    finish_forest_aggregation_labels(
        graph,
        low_effective_degree_threshold,
        heavy_parent,
        selected_weight,
    )
}
'''
    forest = replace_once(forest, serial_block, serial_new, 'serial forest builder')

    parallel_block = '''#[cfg(feature = "parallel")]
pub fn build_forest_grouping_with_executor(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    executor: &ParallelExecutor,
) -> Result<ForestGrouping, CmgError> {
    validate_low_effective_degree_threshold(low_effective_degree_threshold)?;
    let (heavy_parent, selected_weight) = maximum_weight_forest_with_executor(graph, executor)?;
    finish_forest_grouping(
        graph,
        low_effective_degree_threshold,
        heavy_parent,
        selected_weight,
    )
}
'''
    parallel_new = parallel_block + '''
#[cfg(feature = "parallel")]
pub(crate) fn build_forest_aggregation_labels_with_executor(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    executor: &ParallelExecutor,
) -> Result<(Vec<usize>, usize), CmgError> {
    validate_low_effective_degree_threshold(low_effective_degree_threshold)?;
    let (heavy_parent, selected_weight) = maximum_weight_forest_with_executor(graph, executor)?;
    finish_forest_aggregation_labels(
        graph,
        low_effective_degree_threshold,
        heavy_parent,
        selected_weight,
    )
}
'''
    forest = replace_once(forest, parallel_block, parallel_new, 'parallel forest builder')

    finish_block = '''fn finish_forest_grouping(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    heavy_parent: Vec<usize>,
    selected_weight: Vec<f64>,
) -> Result<ForestGrouping, CmgError> {
    let split_parent = split_forest(&heavy_parent)?;
    let mut final_parent = split_parent.clone();

    let has_low_effective_degree =
        graph
            .diagonal()
            .iter()
            .zip(&selected_weight)
            .any(|(degree, weight)| {
                *degree > 0.0 && *weight / *degree < low_effective_degree_threshold
            });

    if has_low_effective_degree {
        let mut selected_incident_weight = vec![0.0; graph.vertex_count()];
        for (vertex, &parent) in split_parent.iter().enumerate() {
            if parent != vertex {
                let weight = selected_weight[vertex];
                selected_incident_weight[vertex] += weight;
                selected_incident_weight[parent] += weight;
            }
        }
        for (vertex, (&degree, &tree_weight)) in graph
            .diagonal()
            .iter()
            .zip(&selected_incident_weight)
            .enumerate()
        {
            if degree > 0.0 && tree_weight / degree < low_effective_degree_threshold {
                final_parent[vertex] = vertex;
            }
        }
    }

    let (labels, sizes) = forest_components(&final_parent)?;
    Ok(ForestGrouping {
        heavy_parent,
        split_parent,
        final_parent,
        labels,
        sizes,
    })
}
'''
    finish_new = '''fn finish_forest_grouping(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    heavy_parent: Vec<usize>,
    selected_weight: Vec<f64>,
) -> Result<ForestGrouping, CmgError> {
    let split_parent = split_forest(&heavy_parent)?;
    let mut final_parent = split_parent.clone();
    apply_low_effective_degree_correction(
        graph,
        low_effective_degree_threshold,
        &selected_weight,
        &mut final_parent,
    );
    drop(selected_weight);

    let (labels, sizes) = forest_components(&final_parent)?;
    Ok(ForestGrouping {
        heavy_parent,
        split_parent,
        final_parent,
        labels,
        sizes,
    })
}

fn finish_forest_aggregation_labels(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    heavy_parent: Vec<usize>,
    selected_weight: Vec<f64>,
) -> Result<(Vec<usize>, usize), CmgError> {
    let mut final_parent = split_forest(&heavy_parent)?;
    drop(heavy_parent);
    apply_low_effective_degree_correction(
        graph,
        low_effective_degree_threshold,
        &selected_weight,
        &mut final_parent,
    );
    drop(selected_weight);
    forest_component_labels(&final_parent)
}

fn apply_low_effective_degree_correction(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    selected_weight: &[f64],
    parent: &mut [usize],
) {
    let has_low_effective_degree = graph
        .diagonal()
        .iter()
        .zip(selected_weight)
        .any(|(degree, weight)| {
            *degree > 0.0 && *weight / *degree < low_effective_degree_threshold
        });
    if !has_low_effective_degree {
        return;
    }

    let mut selected_incident_weight = vec![0.0; graph.vertex_count()];
    for (vertex, &target) in parent.iter().enumerate() {
        if target != vertex {
            let weight = selected_weight[vertex];
            selected_incident_weight[vertex] += weight;
            selected_incident_weight[target] += weight;
        }
    }
    for (vertex, (&degree, &tree_weight)) in graph
        .diagonal()
        .iter()
        .zip(&selected_incident_weight)
        .enumerate()
    {
        if degree > 0.0 && tree_weight / degree < low_effective_degree_threshold {
            parent[vertex] = vertex;
        }
    }
}
'''
    forest = replace_once(forest, finish_block, finish_new, 'forest finish path')

    components_anchor = '''/// Compute deterministic connected components of a functional forest.
pub fn forest_components(parent: &[usize]) -> Result<(Vec<usize>, Vec<usize>), CmgError> {
'''
    components_insert = '''fn forest_component_labels(parent: &[usize]) -> Result<(Vec<usize>, usize), CmgError> {
    validate_parent(parent)?;
    let n = parent.len();
    let mut disjoint_set: Vec<usize> = (0..n).collect();
    for (vertex, &target) in parent.iter().enumerate() {
        union_min_root(&mut disjoint_set, vertex, target);
    }
    for vertex in 0..n {
        disjoint_set[vertex] = find_root(&mut disjoint_set, vertex);
    }

    let mut root_to_label = vec![usize::MAX; n];
    let mut labels = vec![0; n];
    let mut aggregate_count = 0usize;
    for (vertex, &root) in disjoint_set.iter().enumerate() {
        let label = if root_to_label[root] == usize::MAX {
            let next = aggregate_count;
            aggregate_count += 1;
            root_to_label[root] = next;
            next
        } else {
            root_to_label[root]
        };
        labels[vertex] = label;
    }
    Ok((labels, aggregate_count))
}

/// Compute deterministic connected components of a functional forest.
pub fn forest_components(parent: &[usize]) -> Result<(Vec<usize>, Vec<usize>), CmgError> {
'''
    forest = replace_once(
        forest,
        components_anchor,
        components_insert,
        'forest component-label insertion',
    )

    tests = '''

#[cfg(test)]
mod lean_hierarchy_forest_tests {
    use super::{build_forest_aggregation_labels, build_forest_grouping};
    use crate::Laplacian;

    #[test]
    fn lean_labels_match_complete_diagnostics() {
        let graph = Laplacian::from_edges(
            8,
            [
                (0, 1, 3.0),
                (1, 2, 2.0),
                (2, 3, 1.0),
                (3, 4, 4.0),
                (4, 5, 1.5),
                (5, 6, 2.5),
                (6, 7, 0.75),
                (0, 7, 0.5),
            ],
        )
        .unwrap();
        let complete = build_forest_grouping(&graph, 0.125).unwrap();
        let (labels, aggregate_count) =
            build_forest_aggregation_labels(&graph, 0.125).unwrap();
        assert_eq!(labels, complete.labels());
        assert_eq!(aggregate_count, complete.aggregate_count());
    }
}
'''
    if 'mod lean_hierarchy_forest_tests' not in forest:
        forest += tests
    FOREST.write_text(forest)

    coarsen = COARSEN.read_text()
    coarsen = replace_once(
        coarsen,
        '''    pub(crate) fn from_forest_parts(labels: Vec<usize>, sizes: Vec<usize>) -> Self {
        debug_assert_eq!(sizes.iter().sum::<usize>(), labels.len());
        debug_assert!(labels.iter().all(|&label| label < sizes.len()));
        let aggregate_count = sizes.len();
        Self::from_validated_parts(labels, aggregate_count)
    }
''',
        '''    pub(crate) fn from_forest_labels(
        labels: Vec<usize>,
        aggregate_count: usize,
    ) -> Self {
        debug_assert!(labels.iter().all(|&label| label < aggregate_count));
        Self::from_validated_parts(labels, aggregate_count)
    }
''',
        'Aggregation forest constructor',
    )
    COARSEN.write_text(coarsen)

    hierarchy = HIERARCHY.read_text()
    hierarchy = replace_once(
        hierarchy,
        '''use crate::{Aggregation, CmgError, CmgOptions, ForestGrouping, Laplacian, build_forest_grouping};
#[cfg(feature = "parallel")]
use crate::{ParallelExecutor, build_forest_grouping_with_executor};
''',
        '''use crate::forest::build_forest_aggregation_labels;
#[cfg(feature = "parallel")]
use crate::forest::build_forest_aggregation_labels_with_executor;
use crate::{Aggregation, CmgError, CmgOptions, Laplacian};
#[cfg(feature = "parallel")]
use crate::ParallelExecutor;
''',
        'hierarchy forest imports',
    )
    hierarchy = replace_once(
        hierarchy,
        '''            build_forest_grouping,
''',
        '''            build_forest_aggregation_labels,
''',
        'serial hierarchy group function',
    )
    hierarchy = replace_once(
        hierarchy,
        '''            |current, threshold| build_forest_grouping_with_executor(current, threshold, executor),
''',
        '''            |current, threshold| {
                build_forest_aggregation_labels_with_executor(current, threshold, executor)
            },
''',
        'parallel hierarchy group function',
    )
    hierarchy = replace_once(
        hierarchy,
        '''        Group: FnMut(&Laplacian, f64) -> Result<ForestGrouping, CmgError>,
''',
        '''        Group: FnMut(&Laplacian, f64) -> Result<(Vec<usize>, usize), CmgError>,
''',
        'hierarchy group type',
    )
    hierarchy = replace_once(
        hierarchy,
        '''            let grouping = group(&current, options.low_effective_degree_threshold)?;
            let (labels, sizes) = grouping.into_aggregation_parts();
            let aggregation = Aggregation::from_forest_parts(labels, sizes);
''',
        '''            let (labels, aggregate_count) =
                group(&current, options.low_effective_degree_threshold)?;
            let aggregation = Aggregation::from_forest_labels(labels, aggregate_count);
''',
        'hierarchy grouping consumption',
    )
    HIERARCHY.write_text(hierarchy)


def sample(binary, arguments, tag):
    time_path = Path(f'/tmp/cmg-lean-forest-{tag}.time')
    completed = run([
        '/usr/bin/time', '-v', '-o', time_path,
        binary, *arguments,
    ])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'{tag}: unexpected output {payloads}')
    match = re.search(
        r'Maximum resident set size \(kbytes\):\s*(\d+)',
        time_path.read_text(),
    )
    if match is None:
        raise RuntimeError(f'{tag}: peak RSS missing')
    payload = payloads[0]
    payload['peak_rss_kib'] = int(match.group(1))
    return payload


def alternating(baseline, candidate, arguments, tag):
    groups = {'baseline': [], 'candidate': []}
    for index, (name, binary) in enumerate((
        ('baseline', baseline),
        ('candidate', candidate),
        ('candidate', candidate),
        ('baseline', baseline),
    )):
        groups[name].append(sample(binary, arguments, f'{tag}-{name}-{index}'))
    return groups


def med(groups, group, key):
    return statistics.median(item[key] for item in groups[group])


def compare_hierarchy(baseline, candidate, name, case_name, scale):
    groups = alternating(
        baseline,
        candidate,
        [case_name, str(scale), '3'],
        f'hierarchy-{name}',
    )
    stable = (
        'case', 'scale', 'vertices', 'edges', 'levels',
        'hierarchy_matrix_nonzeros', 'max_post_drop_delta_bytes',
    )
    reference = groups['baseline'][0]
    for observations in groups.values():
        for item in observations:
            for key in stable:
                if item[key] != reference[key]:
                    raise RuntimeError(f'{name}: changed hierarchy field {key}')
    baseline_time = med(groups, 'baseline', 'median_ns')
    candidate_time = med(groups, 'candidate', 'median_ns')
    baseline_peak = med(groups, 'baseline', 'median_additional_peak_bytes')
    candidate_peak = med(groups, 'candidate', 'median_additional_peak_bytes')
    baseline_retained = med(groups, 'baseline', 'median_retained_bytes')
    candidate_retained = med(groups, 'candidate', 'median_retained_bytes')
    baseline_rss = max(item['peak_rss_kib'] for item in groups['baseline'])
    candidate_rss = max(item['peak_rss_kib'] for item in groups['candidate'])
    return {
        'metadata': {key: reference[key] for key in stable},
        'candidate_over_baseline_time': candidate_time / baseline_time,
        'candidate_over_baseline_additional_peak': candidate_peak / baseline_peak,
        'candidate_over_baseline_retained': candidate_retained / baseline_retained,
        'candidate_over_baseline_peak_rss': candidate_rss / baseline_rss,
    }


def close(left, right, tolerance=5e-12):
    return abs(left - right) <= tolerance * (1.0 + max(abs(left), abs(right)))


def compare_pcg(baseline, candidate, name, case_name, scale):
    groups = alternating(
        baseline,
        candidate,
        [case_name, str(scale), '3', '4'],
        f'pcg-{name}',
    )
    exact = (
        'case', 'scale', 'vertices', 'input_edges', 'edges', 'levels',
        'threads', 'operators', 'plan_bytes', 'workspace_bytes',
        'auto_execution', 'serial_iterations', 'planned_iterations',
    )
    floating = (
        'serial_backward_error', 'planned_backward_error',
        'serial_residual_norm', 'planned_residual_norm',
    )
    reference = groups['baseline'][0]
    for observations in groups.values():
        for item in observations:
            for key in exact:
                if item[key] != reference[key]:
                    raise RuntimeError(f'{name}: changed PCG field {key}')
            for key in floating:
                if not close(item[key], reference[key]):
                    raise RuntimeError(f'{name}: changed PCG numerical field {key}')
            if item['max_scaled_difference'] > 1e-8:
                raise RuntimeError(f'{name}: serial/planned solution difference too large')
    baseline_serial = med(groups, 'baseline', 'serial_median_ns')
    candidate_serial = med(groups, 'candidate', 'serial_median_ns')
    baseline_planned = med(groups, 'baseline', 'planned_median_ns')
    candidate_planned = med(groups, 'candidate', 'planned_median_ns')
    return {
        'metadata': {key: reference[key] for key in exact},
        'candidate_over_baseline_serial_time': candidate_serial / baseline_serial,
        'candidate_over_baseline_planned_time': candidate_planned / baseline_planned,
        'maximum_scaled_difference': max(
            item['max_scaled_difference']
            for observations in groups.values()
            for item in observations
        ),
    }


result = {
    'schema_version': 1,
    'experiment': 'lean-internal-forest-hierarchy-path',
    'source_sha': source_sha,
    'validation': 'not_run',
    'accepted': False,
    'decision_reason': '',
    'hierarchy_cases': {},
    'pcg_cases': {},
}

try:
    baseline = build(Path('/tmp/cmg-lean-forest-baseline'))
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
    run(['cargo', 'test', '--all-targets'])
    run(['cargo', 'test', '--all-targets', '--release'])
    run(['cargo', 'test', '--all-targets', '--all-features'])
    run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
    run(['cargo', 'build', '--release', '--all-features'])
    candidate = build(Path('/tmp/cmg-lean-forest-candidate'))
    result['validation'] = 'success'

    hierarchy_specs = (
        ('path-1m', 'path', 1_000_000),
        ('worker-firm-1.5m', 'worker-firm', 500_000),
        ('worker-firm-3m', 'worker-firm', 1_000_000),
        ('dense-worker-firm-1.6m', 'dense-worker-firm', 100_000),
    )
    for name, case_name, scale in hierarchy_specs:
        result['hierarchy_cases'][name] = compare_hierarchy(
            baseline['hierarchy'], candidate['hierarchy'], name, case_name, scale
        )

    pcg_specs = (
        ('path-250k', 'path', 250_000),
        ('worker-firm-600k', 'worker-firm', 200_000),
        ('dense-worker-firm-800k', 'dense-worker-firm', 50_000),
    )
    for name, case_name, scale in pcg_specs:
        result['pcg_cases'][name] = compare_pcg(
            baseline['pcg'], candidate['pcg'], name, case_name, scale
        )

    hierarchy = list(result['hierarchy_cases'].values())
    geometric = lambda values: math.exp(
        statistics.fmean(math.log(value) for value in values)
    )
    time_ratios = [item['candidate_over_baseline_time'] for item in hierarchy]
    peak_ratios = [item['candidate_over_baseline_additional_peak'] for item in hierarchy]
    retained_ratios = [item['candidate_over_baseline_retained'] for item in hierarchy]
    rss_ratios = [item['candidate_over_baseline_peak_rss'] for item in hierarchy]
    serial_ratios = [
        item['candidate_over_baseline_serial_time']
        for item in result['pcg_cases'].values()
    ]
    planned_ratios = [
        item['candidate_over_baseline_planned_time']
        for item in result['pcg_cases'].values()
    ]
    result.update({
        'hierarchy_geometric_time_ratio': geometric(time_ratios),
        'hierarchy_worst_time_ratio': max(time_ratios),
        'geometric_additional_peak_ratio': geometric(peak_ratios),
        'best_additional_peak_ratio': min(peak_ratios),
        'worst_additional_peak_ratio': max(peak_ratios),
        'geometric_retained_ratio': geometric(retained_ratios),
        'worst_retained_ratio': max(retained_ratios),
        'worst_peak_rss_ratio': max(rss_ratios),
        'pcg_serial_geometric_time_ratio': geometric(serial_ratios),
        'pcg_serial_worst_time_ratio': max(serial_ratios),
        'pcg_planned_geometric_time_ratio': geometric(planned_ratios),
        'pcg_planned_worst_time_ratio': max(planned_ratios),
    })
    limits = {
        'geometric_additional_peak_ratio_max': 0.985,
        'at_least_one_additional_peak_ratio_max': 0.96,
        'worst_additional_peak_ratio_max': 1.0,
        'hierarchy_geometric_time_ratio_max': 1.03,
        'hierarchy_worst_time_ratio_max': 1.08,
        'worst_retained_ratio_max': 1.001,
        'worst_peak_rss_ratio_max': 1.03,
        'pcg_serial_geometric_time_ratio_max': 1.03,
        'pcg_serial_worst_time_ratio_max': 1.08,
        'pcg_planned_geometric_time_ratio_max': 1.03,
        'pcg_planned_worst_time_ratio_max': 1.08,
    }
    result['acceptance_limits'] = limits
    result['accepted'] = all((
        result['geometric_additional_peak_ratio'] <= limits['geometric_additional_peak_ratio_max'],
        result['best_additional_peak_ratio'] <= limits['at_least_one_additional_peak_ratio_max'],
        result['worst_additional_peak_ratio'] <= limits['worst_additional_peak_ratio_max'],
        result['hierarchy_geometric_time_ratio'] <= limits['hierarchy_geometric_time_ratio_max'],
        result['hierarchy_worst_time_ratio'] <= limits['hierarchy_worst_time_ratio_max'],
        result['worst_retained_ratio'] <= limits['worst_retained_ratio_max'],
        result['worst_peak_rss_ratio'] <= limits['worst_peak_rss_ratio_max'],
        result['pcg_serial_geometric_time_ratio'] <= limits['pcg_serial_geometric_time_ratio_max'],
        result['pcg_serial_worst_time_ratio'] <= limits['pcg_serial_worst_time_ratio_max'],
        result['pcg_planned_geometric_time_ratio'] <= limits['pcg_planned_geometric_time_ratio_max'],
        result['pcg_planned_worst_time_ratio'] <= limits['pcg_planned_worst_time_ratio_max'],
    ))
    result['decision_reason'] = (
        'full qualification passed; hierarchy construction uses a lean forest path without diagnostic parent clones or retained size construction'
        if result['accepted']
        else 'qualification passed but setup peak memory, hierarchy time, retained memory, or complete PCG timing missed a fixed gate'
    )
except Exception as error:
    result['validation'] = 'failure'
    result['decision_reason'] = f'experiment failed: {error}'
    result['error'] = repr(error)
    print(result['decision_reason'], flush=True)

if not result['accepted']:
    for path, content in baseline_sources.items():
        path.write_text(content)
    run(['cargo', 'fmt', '--all'], check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

status_name = 'retained' if result['accepted'] else 'not retained'
status = STATUS.read_text().rstrip()
heading = '## Lean forest hierarchy gate\n'
block = f'''{heading}
- Decision: `{status_name}`.
- Validation: `{result['validation']}`.
- Geometric / best exact additional-peak ratios: `{result.get('geometric_additional_peak_ratio', 'n/a')}` / `{result.get('best_additional_peak_ratio', 'n/a')}`.
- Geometric hierarchy-time ratio: `{result.get('hierarchy_geometric_time_ratio', 'n/a')}`.
- Serial/planned PCG ratios: `{result.get('pcg_serial_geometric_time_ratio', 'n/a')}` / `{result.get('pcg_planned_geometric_time_ratio', 'n/a')}`.
- The complete public `ForestGrouping` diagnostic path is unchanged.
- Evidence: `.ci/performance/lean-forest-hierarchy-latest.json`.
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
checkpoint_heading = '### Lean forest hierarchy checkpoint — 2026-08-23\n'
checkpoint = f'''{checkpoint_heading}
- Candidate was **{status_name}**.
- Validation: `{result['validation']}`.
- Geometric / best exact additional-peak ratios: `{result.get('geometric_additional_peak_ratio', 'n/a')}` / `{result.get('best_additional_peak_ratio', 'n/a')}`.
- Geometric hierarchy-time ratio: `{result.get('hierarchy_geometric_time_ratio', 'n/a')}`.
- Evidence: `.ci/performance/lean-forest-hierarchy-latest.json`.

'''
if checkpoint_heading not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
    prefix, _ = plan.split(marker, 1)
    plan = prefix + marker + '''
1. Re-run the full routing matrix if the lean forest hierarchy path is retained.
2. Refresh large hierarchy peak-memory guidance using the cumulative retained changes.
3. Continue sorting work only with a design that clears both speed and peak-memory gates.
4. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
PLAN.write_text(plan)

SCRIPT.unlink(missing_ok=True)
WORKFLOW.unlink(missing_ok=True)
