import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
COARSEN = Path('src/coarsen.rs')
FOREST = Path('src/forest.rs')
HIERARCHY = Path('src/hierarchy.rs')
SCRIPT = Path('scripts/compact_aggregation_labels_gate.py')
WORKFLOW = Path('.github/workflows/compact-aggregation-labels-gate.yml')
RECORD = Path('.ci/performance/compact-aggregation-labels-latest.json')

baseline_sources = {
    COARSEN: COARSEN.read_text(),
    FOREST: FOREST.read_text(),
    HIERARCHY: HIERARCHY.read_text(),
}
source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def run(command, *, env=None, timeout=9000, check=True):
    command = [str(item) for item in command]
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


def build_benchmarks(target):
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


def apply_candidate():
    coarsen = COARSEN.read_text()
    import_anchor = '#[cfg(feature = "parallel")]\nuse rayon::prelude::*;\n'
    if coarsen.count(import_anchor) != 1:
        raise RuntimeError('coarsen import anchor changed unexpectedly')
    coarsen = coarsen.replace(
        import_anchor,
        import_anchor + 'use std::sync::OnceLock;\n',
        1,
    )

    old_struct = '''#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Aggregation {
    labels: Vec<usize>,
    sizes: Vec<usize>,
}
'''
    new_struct = '''#[derive(Debug, Clone)]
enum LabelStorage {
    Compact(Vec<u32>),
    Native(Vec<usize>),
}

/// A zero-based partition of fine vertices into coarse aggregates.
#[derive(Debug)]
pub struct Aggregation {
    labels: LabelStorage,
    native_labels: OnceLock<Vec<usize>>,
    sizes: Vec<usize>,
}

impl Clone for Aggregation {
    fn clone(&self) -> Self {
        Self {
            labels: self.labels.clone(),
            native_labels: OnceLock::new(),
            sizes: self.sizes.clone(),
        }
    }
}

impl PartialEq for Aggregation {
    fn eq(&self, other: &Self) -> bool {
        self.sizes == other.sizes
            && self.fine_dimension() == other.fine_dimension()
            && (0..self.fine_dimension()).all(|index| self.label_at(index) == other.label_at(index))
    }
}

impl Eq for Aggregation {}
'''
    if coarsen.count(old_struct) != 1:
        raise RuntimeError('Aggregation struct block changed unexpectedly')
    coarsen = coarsen.replace(old_struct, new_struct, 1)

    impl_start = coarsen.index('impl Aggregation {')
    impl_end = coarsen.index('\nfn validate_prolong_dimensions(', impl_start)
    candidate_impl = r'''impl Aggregation {
    /// Construct an aggregation from explicit labels and a coarse dimension.
    pub fn new(labels: Vec<usize>, aggregate_count: usize) -> Result<Self, CmgError> {
        let mut sizes = vec![0; aggregate_count];
        for &label in &labels {
            if label >= aggregate_count {
                return Err(CmgError::VertexOutOfBounds {
                    vertex: label,
                    vertex_count: aggregate_count,
                });
            }
            sizes[label] += 1;
        }
        Ok(Self::from_validated_parts(labels, sizes))
    }

    pub(crate) fn from_forest_parts(labels: Vec<usize>, sizes: Vec<usize>) -> Self {
        debug_assert_eq!(sizes.iter().sum::<usize>(), labels.len());
        debug_assert!(labels.iter().all(|&label| label < sizes.len()));
        Self::from_validated_parts(labels, sizes)
    }

    fn from_validated_parts(labels: Vec<usize>, sizes: Vec<usize>) -> Self {
        let compact_limit = (u32::MAX as usize).saturating_add(1);
        let labels = if sizes.len() <= compact_limit {
            LabelStorage::Compact(labels.into_iter().map(|label| label as u32).collect())
        } else {
            LabelStorage::Native(labels)
        };
        Self {
            labels,
            native_labels: OnceLock::new(),
            sizes,
        }
    }

    /// Return the fine-to-coarse labels.
    ///
    /// Hierarchy-built aggregations retain compact labels internally. The
    /// native-width compatibility slice is materialized lazily only when this
    /// public accessor is called.
    #[must_use]
    pub fn labels(&self) -> &[usize] {
        match &self.labels {
            LabelStorage::Native(labels) => labels,
            LabelStorage::Compact(labels) => self.native_labels.get_or_init(|| {
                labels.iter().map(|&label| label as usize).collect()
            }),
        }
    }

    /// Return aggregate sizes.
    #[must_use]
    pub fn sizes(&self) -> &[usize] {
        &self.sizes
    }

    /// Return the fine dimension.
    #[must_use]
    pub fn fine_dimension(&self) -> usize {
        match &self.labels {
            LabelStorage::Compact(labels) => labels.len(),
            LabelStorage::Native(labels) => labels.len(),
        }
    }

    /// Return the coarse dimension.
    #[must_use]
    pub fn coarse_dimension(&self) -> usize {
        self.sizes.len()
    }

    #[inline]
    fn label_at(&self, index: usize) -> usize {
        match &self.labels {
            LabelStorage::Compact(labels) => labels[index] as usize,
            LabelStorage::Native(labels) => labels[index],
        }
    }

    /// Restrict by summing fine values within every aggregate.
    pub fn restrict(&self, fine: &[f64]) -> Result<Vec<f64>, CmgError> {
        let mut coarse = vec![0.0; self.coarse_dimension()];
        self.restrict_into(fine, &mut coarse)?;
        Ok(coarse)
    }

    /// Restrict into caller-owned storage.
    pub fn restrict_into(&self, fine: &[f64], coarse: &mut [f64]) -> Result<(), CmgError> {
        if fine.len() != self.fine_dimension() {
            return Err(CmgError::dimension(
                "Aggregation::restrict fine",
                self.fine_dimension(),
                fine.len(),
            ));
        }
        if coarse.len() != self.coarse_dimension() {
            return Err(CmgError::dimension(
                "Aggregation::restrict coarse",
                self.coarse_dimension(),
                coarse.len(),
            ));
        }
        coarse.fill(0.0);
        match &self.labels {
            LabelStorage::Compact(labels) => {
                for (&value, &label) in fine.iter().zip(labels) {
                    coarse[label as usize] += value;
                }
            }
            LabelStorage::Native(labels) => {
                for (&value, &label) in fine.iter().zip(labels) {
                    coarse[label] += value;
                }
            }
        }
        Ok(())
    }

    /// Prolong by copying each coarse value to its fine aggregate members.
    pub fn prolong(&self, coarse: &[f64]) -> Result<Vec<f64>, CmgError> {
        let mut fine = vec![0.0; self.fine_dimension()];
        self.prolong_into(coarse, &mut fine)?;
        Ok(fine)
    }

    /// Prolong into caller-owned storage.
    pub fn prolong_into(&self, coarse: &[f64], fine: &mut [f64]) -> Result<(), CmgError> {
        validate_prolong_dimensions(self, coarse, fine)?;
        match &self.labels {
            LabelStorage::Compact(labels) => {
                for (value, &label) in fine.iter_mut().zip(labels) {
                    *value = coarse[label as usize];
                }
            }
            LabelStorage::Native(labels) => {
                for (value, &label) in fine.iter_mut().zip(labels) {
                    *value = coarse[label];
                }
            }
        }
        Ok(())
    }

    /// Add a prolonged coarse vector to a fine vector in place.
    pub fn prolong_add_into(&self, coarse: &[f64], fine: &mut [f64]) -> Result<(), CmgError> {
        validate_prolong_dimensions(self, coarse, fine)?;
        match &self.labels {
            LabelStorage::Compact(labels) => {
                for (value, &label) in fine.iter_mut().zip(labels) {
                    *value += coarse[label as usize];
                }
            }
            LabelStorage::Native(labels) => {
                for (value, &label) in fine.iter_mut().zip(labels) {
                    *value += coarse[label];
                }
            }
        }
        Ok(())
    }

    #[cfg(feature = "parallel")]
    pub(crate) fn prolong_add_into_with_executor(
        &self,
        coarse: &[f64],
        fine: &mut [f64],
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        validate_prolong_dimensions(self, coarse, fine)?;
        if !executor.should_parallel(fine.len()) {
            return self.prolong_add_into(coarse, fine);
        }
        executor.install(|| match &self.labels {
            LabelStorage::Compact(labels) => fine
                .par_iter_mut()
                .zip(labels.par_iter())
                .for_each(|(value, &label)| *value += coarse[label as usize]),
            LabelStorage::Native(labels) => fine
                .par_iter_mut()
                .zip(labels.par_iter())
                .for_each(|(value, &label)| *value += coarse[label]),
        });
        Ok(())
    }

    /// Form the exact graph Laplacian `R L R^T`.
    pub fn contract(&self, graph: &Laplacian) -> Result<Laplacian, CmgError> {
        self.validate_contract_graph(graph)?;
        let mut coarse_edges = Vec::with_capacity(graph.edge_count());
        match &self.labels {
            LabelStorage::Compact(labels) => {
                for edge in graph.edges() {
                    let left = labels[edge.u()] as usize;
                    let right = labels[edge.v()] as usize;
                    if left != right {
                        coarse_edges.push(Edge::from_internal_parts(left, right, edge.weight())?);
                    }
                }
            }
            LabelStorage::Native(labels) => {
                for edge in graph.edges() {
                    let left = labels[edge.u()];
                    let right = labels[edge.v()];
                    if left != right {
                        coarse_edges.push(Edge::from_internal_parts(left, right, edge.weight())?);
                    }
                }
            }
        }
        Laplacian::from_compact_edges(self.coarse_dimension(), coarse_edges)
    }

    /// Form `R L R^T` using deterministic parallel edge mapping and sorting.
    ///
    /// The resulting graph is bit-for-bit identical to [`Self::contract`].
    /// Small edge sets follow the serial path selected by the executor.
    #[cfg(feature = "parallel")]
    pub fn contract_with_executor(
        &self,
        graph: &Laplacian,
        executor: &ParallelExecutor,
    ) -> Result<Laplacian, CmgError> {
        self.validate_contract_graph(graph)?;
        if graph.edge_count() < PARALLEL_SETUP_MIN_ITEMS
            || !executor.should_parallel(graph.edge_count())
        {
            return self.contract(graph);
        }
        let coarse_edges: Result<Vec<Edge>, CmgError> = executor.install(|| match &self.labels {
            LabelStorage::Compact(labels) => graph
                .edges()
                .par_iter()
                .filter_map(|edge| {
                    let left = labels[edge.u()] as usize;
                    let right = labels[edge.v()] as usize;
                    (left != right).then(|| Edge::from_internal_parts(left, right, edge.weight()))
                })
                .collect(),
            LabelStorage::Native(labels) => graph
                .edges()
                .par_iter()
                .filter_map(|edge| {
                    let left = labels[edge.u()];
                    let right = labels[edge.v()];
                    (left != right).then(|| Edge::from_internal_parts(left, right, edge.weight()))
                })
                .collect(),
        });
        Laplacian::from_compact_edges_with_executor(
            self.coarse_dimension(),
            coarse_edges?,
            executor,
        )
    }

    fn validate_contract_graph(&self, graph: &Laplacian) -> Result<(), CmgError> {
        if graph.vertex_count() != self.fine_dimension() {
            return Err(CmgError::dimension(
                "Aggregation::contract",
                self.fine_dimension(),
                graph.vertex_count(),
            ));
        }
        Ok(())
    }
}
'''
    coarsen = coarsen[:impl_start] + candidate_impl + coarsen[impl_end:]

    tests = r'''

#[cfg(test)]
mod compact_aggregation_label_tests {
    use super::{Aggregation, LabelStorage};

    #[test]
    fn compact_storage_preserves_public_labels_and_algebra() {
        let aggregation = Aggregation::new(vec![0, 0, 1, 2, 2], 3).unwrap();
        assert!(matches!(&aggregation.labels, LabelStorage::Compact(_)));
        assert!(aggregation.native_labels.get().is_none());

        let fine = [1.0, 2.0, 3.0, 4.0, 5.0];
        assert_eq!(aggregation.restrict(&fine).unwrap(), vec![3.0, 3.0, 9.0]);
        assert_eq!(
            aggregation.prolong(&[10.0, 20.0, 30.0]).unwrap(),
            vec![10.0, 10.0, 20.0, 30.0, 30.0]
        );
        assert_eq!(aggregation.labels(), &[0, 0, 1, 2, 2]);
        assert!(aggregation.native_labels.get().is_some());
    }

    #[test]
    fn clone_does_not_duplicate_materialized_native_cache() {
        let aggregation = Aggregation::new(vec![0, 1, 1, 2], 3).unwrap();
        let _ = aggregation.labels();
        let cloned = aggregation.clone();
        assert_eq!(aggregation, cloned);
        assert!(cloned.native_labels.get().is_none());
        assert_eq!(cloned.labels(), &[0, 1, 1, 2]);
    }
}
'''
    if 'mod compact_aggregation_label_tests' not in coarsen:
        coarsen += tests
    COARSEN.write_text(coarsen)

    forest = FOREST.read_text()
    anchor = '''    pub fn aggregate_count(&self) -> usize {
        self.sizes.len()
    }
'''
    replacement = anchor + '''
    pub(crate) fn into_aggregation_parts(self) -> (Vec<usize>, Vec<usize>) {
        (self.labels, self.sizes)
    }
'''
    if forest.count(anchor) != 1:
        raise RuntimeError('ForestGrouping ownership anchor changed unexpectedly')
    forest = forest.replace(anchor, replacement, 1)
    FOREST.write_text(forest)

    hierarchy = HIERARCHY.read_text()
    old = '''            let grouping = group(&current, options.low_effective_degree_threshold)?;
            let aggregation =
                Aggregation::new(grouping.labels().to_vec(), grouping.aggregate_count())?;
            let coarse_count = aggregation.coarse_dimension();
'''
    new = '''            let grouping = group(&current, options.low_effective_degree_threshold)?;
            let (labels, sizes) = grouping.into_aggregation_parts();
            let aggregation = Aggregation::from_forest_parts(labels, sizes);
            let coarse_count = aggregation.coarse_dimension();
'''
    if hierarchy.count(old) != 1:
        raise RuntimeError('hierarchy grouping-to-aggregation block changed unexpectedly')
    hierarchy = hierarchy.replace(old, new, 1)
    HIERARCHY.write_text(hierarchy)


def sample_json(binary, arguments, tag):
    time_path = Path(f'/tmp/cmg-compact-labels-{tag}.time')
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
        raise RuntimeError(f'unexpected benchmark output: {payloads}')
    match = re.search(
        r'Maximum resident set size \(kbytes\):\s*(\d+)',
        time_path.read_text(),
    )
    if match is None:
        raise RuntimeError('peak RSS missing')
    payload = payloads[0]
    payload['peak_rss_kib'] = int(match.group(1))
    return payload


def alternating_samples(baseline, candidate, arguments, name):
    observations = {'baseline': [], 'candidate': []}
    schedule = (
        ('baseline', baseline),
        ('candidate', candidate),
        ('candidate', candidate),
        ('baseline', baseline),
    )
    for index, (label, binary) in enumerate(schedule):
        observations[label].append(
            sample_json(binary, arguments, f'{name}-{label}-{index}')
        )
    return observations


def compare_hierarchy(baseline, candidate, name, case, scale):
    observations = alternating_samples(
        baseline,
        candidate,
        [case, str(scale), '3'],
        f'hierarchy-{name}',
    )
    stable = (
        'case', 'scale', 'vertices', 'edges', 'levels',
        'hierarchy_matrix_nonzeros', 'max_post_drop_delta_bytes',
    )
    reference = observations['baseline'][0]
    for group in observations.values():
        for item in group:
            for key in stable:
                if item[key] != reference[key]:
                    raise RuntimeError(f'{name}: changed hierarchy field {key}')

    def med(group, key):
        return statistics.median(item[key] for item in observations[group])

    baseline_time = med('baseline', 'median_ns')
    candidate_time = med('candidate', 'median_ns')
    baseline_peak = med('baseline', 'median_additional_peak_bytes')
    candidate_peak = med('candidate', 'median_additional_peak_bytes')
    baseline_retained = med('baseline', 'median_retained_bytes')
    candidate_retained = med('candidate', 'median_retained_bytes')
    baseline_rss = max(item['peak_rss_kib'] for item in observations['baseline'])
    candidate_rss = max(item['peak_rss_kib'] for item in observations['candidate'])
    return {
        'metadata': {key: reference[key] for key in stable},
        'baseline_median_ns': baseline_time,
        'candidate_median_ns': candidate_time,
        'candidate_over_baseline_time': candidate_time / baseline_time,
        'baseline_additional_peak_bytes': baseline_peak,
        'candidate_additional_peak_bytes': candidate_peak,
        'candidate_over_baseline_additional_peak': candidate_peak / baseline_peak,
        'baseline_retained_bytes': baseline_retained,
        'candidate_retained_bytes': candidate_retained,
        'candidate_over_baseline_retained': candidate_retained / baseline_retained,
        'baseline_peak_rss_kib': baseline_rss,
        'candidate_peak_rss_kib': candidate_rss,
        'candidate_over_baseline_peak_rss': candidate_rss / baseline_rss,
    }


def close(left, right, tolerance=5e-12):
    return abs(left - right) <= tolerance * (1.0 + max(abs(left), abs(right)))


def compare_pcg(baseline, candidate, name, case, scale):
    observations = alternating_samples(
        baseline,
        candidate,
        [case, str(scale), '3', '4'],
        f'pcg-{name}',
    )
    stable_exact = (
        'case', 'scale', 'vertices', 'input_edges', 'edges', 'levels',
        'threads', 'operators', 'plan_bytes', 'workspace_bytes',
        'auto_execution', 'serial_iterations', 'planned_iterations',
    )
    stable_float = (
        'serial_backward_error', 'planned_backward_error',
        'serial_residual_norm', 'planned_residual_norm',
    )
    reference = observations['baseline'][0]
    for group in observations.values():
        for item in group:
            for key in stable_exact:
                if item[key] != reference[key]:
                    raise RuntimeError(f'{name}: changed PCG field {key}')
            for key in stable_float:
                if not close(item[key], reference[key]):
                    raise RuntimeError(f'{name}: changed PCG numerical field {key}')
            if item['max_scaled_difference'] > 5e-10:
                raise RuntimeError(f'{name}: serial/planned solution mismatch')

    def med(group, key):
        return statistics.median(item[key] for item in observations[group])

    baseline_serial = med('baseline', 'serial_median_ns')
    candidate_serial = med('candidate', 'serial_median_ns')
    baseline_planned = med('baseline', 'planned_median_ns')
    candidate_planned = med('candidate', 'planned_median_ns')
    return {
        'metadata': {key: reference[key] for key in stable_exact},
        'baseline_serial_median_ns': baseline_serial,
        'candidate_serial_median_ns': candidate_serial,
        'candidate_over_baseline_serial_time': candidate_serial / baseline_serial,
        'baseline_planned_median_ns': baseline_planned,
        'candidate_planned_median_ns': candidate_planned,
        'candidate_over_baseline_planned_time': candidate_planned / baseline_planned,
        'maximum_internal_scaled_difference': max(
            item['max_scaled_difference']
            for group in observations.values()
            for item in group
        ),
    }


result = {
    'schema_version': 1,
    'experiment': 'zero-copy-compact-aggregation-labels',
    'source_sha': source_sha,
    'validation': 'not_run',
    'accepted': False,
    'decision_reason': '',
    'hierarchy_cases': {},
    'pcg_cases': {},
}

try:
    baseline = build_benchmarks(Path('/tmp/cmg-compact-labels-baseline'))
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
    candidate = build_benchmarks(Path('/tmp/cmg-compact-labels-candidate'))
    result['validation'] = 'success'

    hierarchy_specs = (
        ('path-1m', 'path', 1_000_000),
        ('worker-firm-1.5m', 'worker-firm', 500_000),
        ('worker-firm-3m', 'worker-firm', 1_000_000),
        ('dense-worker-firm-1.6m', 'dense-worker-firm', 100_000),
    )
    for name, case, scale in hierarchy_specs:
        result['hierarchy_cases'][name] = compare_hierarchy(
            baseline['hierarchy'], candidate['hierarchy'], name, case, scale
        )

    pcg_specs = (
        ('path-250k', 'path', 250_000),
        ('worker-firm-600k', 'worker-firm', 200_000),
        ('dense-worker-firm-800k', 'dense-worker-firm', 50_000),
    )
    for name, case, scale in pcg_specs:
        result['pcg_cases'][name] = compare_pcg(
            baseline['pcg'], candidate['pcg'], name, case, scale
        )

    hierarchy_values = list(result['hierarchy_cases'].values())
    time_ratios = [item['candidate_over_baseline_time'] for item in hierarchy_values]
    peak_ratios = [item['candidate_over_baseline_additional_peak'] for item in hierarchy_values]
    retained_ratios = [item['candidate_over_baseline_retained'] for item in hierarchy_values]
    rss_ratios = [item['candidate_over_baseline_peak_rss'] for item in hierarchy_values]
    serial_ratios = [
        item['candidate_over_baseline_serial_time']
        for item in result['pcg_cases'].values()
    ]
    planned_ratios = [
        item['candidate_over_baseline_planned_time']
        for item in result['pcg_cases'].values()
    ]

    geometric = lambda values: math.exp(
        sum(math.log(value) for value in values) / len(values)
    )
    result.update({
        'hierarchy_geometric_time_ratio': geometric(time_ratios),
        'hierarchy_worst_time_ratio': max(time_ratios),
        'geometric_additional_peak_ratio': geometric(peak_ratios),
        'worst_additional_peak_ratio': max(peak_ratios),
        'geometric_retained_ratio': geometric(retained_ratios),
        'worst_retained_ratio': max(retained_ratios),
        'worst_peak_rss_ratio': max(rss_ratios),
        'pcg_serial_geometric_time_ratio': geometric(serial_ratios),
        'pcg_serial_worst_time_ratio': max(serial_ratios),
        'pcg_planned_geometric_time_ratio': geometric(planned_ratios),
        'pcg_planned_worst_time_ratio': max(planned_ratios),
    })
    result['acceptance_limits'] = {
        'geometric_retained_ratio_max': 0.97,
        'worst_retained_ratio_max': 0.995,
        'geometric_additional_peak_ratio_max': 1.0,
        'worst_additional_peak_ratio_max': 1.03,
        'hierarchy_geometric_time_ratio_max': 1.03,
        'hierarchy_worst_time_ratio_max': 1.08,
        'worst_peak_rss_ratio_max': 1.04,
        'pcg_serial_geometric_time_ratio_max': 1.03,
        'pcg_serial_worst_time_ratio_max': 1.08,
        'pcg_planned_geometric_time_ratio_max': 1.03,
        'pcg_planned_worst_time_ratio_max': 1.08,
    }
    limits = result['acceptance_limits']
    result['accepted'] = (
        result['geometric_retained_ratio'] <= limits['geometric_retained_ratio_max']
        and result['worst_retained_ratio'] <= limits['worst_retained_ratio_max']
        and result['geometric_additional_peak_ratio'] <= limits['geometric_additional_peak_ratio_max']
        and result['worst_additional_peak_ratio'] <= limits['worst_additional_peak_ratio_max']
        and result['hierarchy_geometric_time_ratio'] <= limits['hierarchy_geometric_time_ratio_max']
        and result['hierarchy_worst_time_ratio'] <= limits['hierarchy_worst_time_ratio_max']
        and result['worst_peak_rss_ratio'] <= limits['worst_peak_rss_ratio_max']
        and result['pcg_serial_geometric_time_ratio'] <= limits['pcg_serial_geometric_time_ratio_max']
        and result['pcg_serial_worst_time_ratio'] <= limits['pcg_serial_worst_time_ratio_max']
        and result['pcg_planned_geometric_time_ratio'] <= limits['pcg_planned_geometric_time_ratio_max']
        and result['pcg_planned_worst_time_ratio'] <= limits['pcg_planned_worst_time_ratio_max']
    )
    result['decision_reason'] = (
        'full qualification passed; aggregation labels are compact and the forest-to-hierarchy copy is removed with material retained-memory savings'
        if result['accepted']
        else 'qualification passed but retained memory, peak memory, hierarchy time, or complete PCG timing missed a fixed gate'
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

status_path = Path('PERFORMANCE_STATUS.md')
status = status_path.read_text().rstrip()
heading = '## Compact aggregation-label gate\n'
status_name = 'retained' if result['accepted'] else 'not retained'
block = f'''{heading}
- Decision: `{status_name}`.
- Validation: `{result['validation']}`.
- Geometric retained-memory ratio: `{result.get('geometric_retained_ratio', 'n/a')}`.
- Geometric hierarchy-time ratio: `{result.get('hierarchy_geometric_time_ratio', 'n/a')}`.
- Serial/planned PCG geometric ratios: `{result.get('pcg_serial_geometric_time_ratio', 'n/a')}` / `{result.get('pcg_planned_geometric_time_ratio', 'n/a')}`.
- Public native-width labels remain available through a lazy compatibility cache.
- Evidence: `.ci/performance/compact-aggregation-labels-latest.json`.
'''
if heading in status:
    start = status.index(heading)
    end = status.find('\n## ', start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + block + status[end:]
else:
    status += '\n\n' + block
status_path.write_text(status.rstrip() + '\n')

plan_path = Path('PERFORMANCE_PLAN.md')
plan = plan_path.read_text()
marker = '## Current next action\n'
checkpoint_heading = '### Compact aggregation-label checkpoint — 2026-08-23\n'
checkpoint = f'''{checkpoint_heading}
- Candidate was **{status_name}**.
- Validation: `{result['validation']}`.
- Geometric retained / hierarchy-time ratios: `{result.get('geometric_retained_ratio', 'n/a')}` / `{result.get('hierarchy_geometric_time_ratio', 'n/a')}`.
- Serial/planned PCG geometric ratios: `{result.get('pcg_serial_geometric_time_ratio', 'n/a')}` / `{result.get('pcg_planned_geometric_time_ratio', 'n/a')}`.
- Evidence: `.ci/performance/compact-aggregation-labels-latest.json`.

'''
if checkpoint_heading not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
    prefix, _ = plan.split(marker, 1)
    plan = prefix + marker + '''
1. Re-run the full certified routing matrix after any retained aggregation-label change.
2. Re-profile contraction mapping and sorting with the retained layout.
3. Evaluate compact aggregate-size storage only if it remains material after label compaction.
4. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
plan_path.write_text(plan)

SCRIPT.unlink(missing_ok=True)
WORKFLOW.unlink(missing_ok=True)
