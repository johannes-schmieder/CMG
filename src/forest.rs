//! Heavy-edge forests and the CMG forest-partitioning heuristics.

use crate::storage::{try_capacity, try_filled};
use crate::{CmgError, Laplacian};
#[cfg(feature = "parallel")]
use crate::{CsrLaplacian, ParallelExecutor, execution::PARALLEL_SETUP_MIN_ITEMS};

/// The complete diagnostic result of one CMG Steiner-group construction.
#[derive(Debug, Clone, PartialEq)]
pub struct ForestGrouping {
    heavy_parent: Vec<usize>,
    split_parent: Vec<usize>,
    final_parent: Vec<usize>,
    labels: Vec<usize>,
    sizes: Vec<usize>,
}

impl ForestGrouping {
    /// Return the maximum-weight incident-edge parent selected for every vertex.
    #[must_use]
    pub fn heavy_parent(&self) -> &[usize] {
        &self.heavy_parent
    }

    /// Return the parent vector after the diameter/conductance forest split.
    #[must_use]
    pub fn split_parent(&self) -> &[usize] {
        &self.split_parent
    }

    /// Return the parent vector after the low-effective-degree correction.
    #[must_use]
    pub fn final_parent(&self) -> &[usize] {
        &self.final_parent
    }

    /// Return the zero-based aggregate label of every fine vertex.
    #[must_use]
    pub fn labels(&self) -> &[usize] {
        &self.labels
    }

    /// Return aggregate sizes in label order.
    #[must_use]
    pub fn sizes(&self) -> &[usize] {
        &self.sizes
    }

    /// Return the number of aggregates.
    #[must_use]
    pub fn aggregate_count(&self) -> usize {
        self.sizes.len()
    }
}

/// Construct the CMG heavy-edge forest and aggregate labels.
///
/// Equal-weight ties select the lowest-numbered neighboring vertex. The
/// low-effective-degree threshold must be finite and lie in `[0, 1]`; the
/// upstream value is `1/8`.
pub fn build_forest_grouping(
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

/// Fallible, fixed-capacity variant for serial construction admission.
pub(crate) fn build_forest_aggregation_labels_admitted(
    graph: &Laplacian,
    threshold: f64,
) -> Result<(Vec<usize>, usize), CmgError> {
    validate_low_effective_degree_threshold(threshold)?;
    let n = graph.vertex_count();
    let mut parent = try_capacity(n, "forest heavy parents")?;
    parent.extend(0..n);
    let mut weights = try_filled(n, 0.0, "forest selected weights")?;
    for edge in graph.edges() {
        consider_parent(edge.u(), edge.v(), edge.weight(), &mut parent, &mut weights);
        consider_parent(edge.v(), edge.u(), edge.weight(), &mut parent, &mut weights);
    }
    let mut split = if n <= u32::MAX as usize {
        split_forest_impl_with_indegree::<u32, true>(&parent, false)?
    } else {
        split_forest_impl_with_indegree::<usize, true>(&parent, false)?
    };
    drop(parent);
    apply_low_effective_degree_correction_impl::<true>(graph, threshold, &weights, &mut split)?;
    drop(weights);
    forest_component_labels_impl::<true>(&split)
}

/// Controlled equivalent of serial admitted forest construction.
///
/// All walks, including union-find root discovery and compression, charge work
/// before bounded progress. Fixed-capacity storage keeps walk pushes fallible at
/// admission and prevents hidden growth during traversal.
#[cfg(feature = "experimental-components")]
pub(crate) fn build_forest_aggregation_labels_controlled(
    graph: &Laplacian,
    threshold: f64,
    meter: &mut crate::build_control::BuildMeter<'_>,
) -> Result<(Vec<usize>, usize), CmgError> {
    meter.work("forest threshold", 16)?;
    validate_low_effective_degree_threshold(threshold)?;
    let n = graph.vertex_count();
    let mut parent = meter.try_capacity(n, "forest heavy parents")?;
    for vertex in 0..n {
        meter.work("forest heavy parents", 16)?;
        parent.push(vertex);
    }
    let mut weights = meter.try_filled(n, 0.0, "forest selected weights")?;
    for edge in graph.edges() {
        meter.work("forest heavy edges", 64)?;
        consider_parent(edge.u(), edge.v(), edge.weight(), &mut parent, &mut weights);
        consider_parent(edge.v(), edge.u(), edge.weight(), &mut parent, &mut weights);
    }
    let mut split = if n <= u32::MAX as usize {
        split_forest_controlled::<u32>(&parent, meter)?
    } else {
        split_forest_controlled::<usize>(&parent, meter)?
    };
    drop(parent);
    apply_low_effective_degree_correction_controlled(
        graph, threshold, &weights, &mut split, meter,
    )?;
    drop(weights);
    forest_component_labels_controlled(&split, meter)
}

#[cfg(feature = "experimental-components")]
fn apply_low_effective_degree_correction_controlled(
    graph: &Laplacian,
    threshold: f64,
    selected_weight: &[f64],
    parent: &mut [usize],
    meter: &mut crate::build_control::BuildMeter<'_>,
) -> Result<(), CmgError> {
    let mut has_low_effective_degree = false;
    for (&degree, &weight) in graph.diagonal().iter().zip(selected_weight) {
        meter.work("forest low degree scan", 32)?;
        if degree > 0.0 && weight / degree < threshold {
            has_low_effective_degree = true;
            break;
        }
    }
    if !has_low_effective_degree {
        return Ok(());
    }
    let mut selected_incident_weight =
        meter.try_filled(graph.vertex_count(), 0.0, "forest incident weights")?;
    for (vertex, &target) in parent.iter().enumerate() {
        meter.work("forest incident weights", 32)?;
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
        meter.work("forest low degree correction", 32)?;
        if degree > 0.0 && tree_weight / degree < threshold {
            parent[vertex] = vertex;
        }
    }
    Ok(())
}

#[cfg(feature = "experimental-components")]
fn split_forest_controlled<I: ForestIndegree>(
    parent: &[usize],
    meter: &mut crate::build_control::BuildMeter<'_>,
) -> Result<Vec<usize>, CmgError> {
    let n = parent.len();
    let mut forest = meter.copy_slice(parent, "forest split parents")?;
    let mut ancestors = meter.try_filled(n, 0_i64, "forest ancestors")?;
    let mut indegree = meter.try_filled(n, I::ZERO, "forest indegrees")?;
    let mut visited = meter.try_filled(n, false, "forest visited")?;

    for &target in &forest {
        meter.work("forest indegrees", 16)?;
        indegree[target].increment();
    }

    let mut walk = meter.try_capacity(n, "forest walk")?;
    let mut new_ancestors = meter.try_capacity(n, "forest walk ancestors")?;

    for start in 0..n {
        meter.work("forest split starts", 16)?;
        let mut current = start;
        let mut continue_walk = true;
        loop {
            meter.work("forest diameter fronts", 64)?;
            if !(continue_walk && indegree[current].is_zero() && !visited[current]) {
                break;
            }
            continue_walk = false;
            let mut ancestors_in_path = 0_i64;
            walk.clear();
            walk.push(current);
            new_ancestors.clear();
            new_ancestors.push(0_i64);
            let mut k = 0_usize;

            loop {
                meter.work("forest diameter walk", 64)?;
                if !(k <= 5 || visited[current]) {
                    break;
                }
                current = forest[current];
                let terminated = current == walk[k] || (k > 0 && current == walk[k - 1]);
                if terminated {
                    break;
                }
                k += 1;
                walk.push(current);
                ancestors_in_path += i64::from(u8::from(!visited[current]));
                new_ancestors.push(ancestors_in_path);
            }

            meter.work("forest diameter cut", 64)?;
            if k > 5 {
                let middle = k / 2;
                forest[walk[middle]] = walk[middle];
                let next = walk[middle + 1];
                indegree[next].decrement();
                let removed = ancestors[walk[middle]];
                for &vertex in &walk[(middle + 1)..=k] {
                    meter.work("forest diameter ancestors", 16)?;
                    ancestors[vertex] -= removed;
                }
                for index in 0..=middle {
                    meter.work("forest diameter visited", 32)?;
                    let vertex = walk[index];
                    visited[vertex] = true;
                    ancestors[vertex] += new_ancestors[index];
                }
                current = next;
                continue_walk = true;
            }

            meter.work("forest diameter finish", 16)?;
            if !continue_walk {
                for index in 0..=k {
                    meter.work("forest diameter visited", 32)?;
                    let vertex = walk[index];
                    ancestors[vertex] += new_ancestors[index];
                    visited[vertex] = true;
                }
            }
        }
    }

    for start in 0..n {
        meter.work("forest split starts", 16)?;
        let mut current = start;
        'new_front: loop {
            meter.work("forest conductance fronts", 32)?;
            if !indegree[current].is_zero() {
                break;
            }
            let mut previous = current;
            loop {
                meter.work("forest conductance walk", 64)?;
                let next = forest[current];
                if next == current || next == previous {
                    break 'new_front;
                }
                if ancestors[current] > 2 && ancestors[next] - ancestors[current] > 2 {
                    forest[current] = current;
                    indegree[next].decrement();
                    let removed_ancestors = ancestors[current];

                    let mut adjustment_previous = current;
                    let mut adjustment_current = next;
                    loop {
                        meter.work("forest conductance ancestors", 32)?;
                        ancestors[adjustment_current] -= removed_ancestors;
                        let adjustment_next = forest[adjustment_current];
                        if adjustment_next == adjustment_current
                            || adjustment_next == adjustment_previous
                        {
                            break;
                        }
                        adjustment_previous = adjustment_current;
                        adjustment_current = adjustment_next;
                    }

                    current = next;
                    continue 'new_front;
                }
                previous = current;
                current = next;
            }
        }
    }

    Ok(forest)
}

#[cfg(feature = "experimental-components")]
fn forest_component_labels_controlled(
    parent: &[usize],
    meter: &mut crate::build_control::BuildMeter<'_>,
) -> Result<(Vec<usize>, usize), CmgError> {
    let n = parent.len();
    let mut disjoint_set = meter.try_capacity(n, "forest union find")?;
    for vertex in 0..n {
        meter.work("forest union find initialize", 16)?;
        disjoint_set.push(vertex);
    }
    for (vertex, &target) in parent.iter().enumerate() {
        meter.work("forest union find edges", 16)?;
        let left_root = find_root_controlled(&mut disjoint_set, vertex, meter)?;
        let right_root = find_root_controlled(&mut disjoint_set, target, meter)?;
        meter.work("forest union find merge", 32)?;
        if left_root != right_root {
            let (root, child) = if left_root < right_root {
                (left_root, right_root)
            } else {
                (right_root, left_root)
            };
            disjoint_set[child] = root;
        }
    }
    for vertex in 0..n {
        meter.work("forest union find flatten", 16)?;
        disjoint_set[vertex] = find_root_controlled(&mut disjoint_set, vertex, meter)?;
    }
    let mut root_to_label = meter.try_filled(n, usize::MAX, "forest root labels")?;
    let mut labels = meter.try_filled(n, 0, "forest labels")?;
    let mut aggregate_count = 0usize;
    for (vertex, &root) in disjoint_set.iter().enumerate() {
        meter.work("forest component labels", 32)?;
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

#[cfg(feature = "experimental-components")]
fn find_root_controlled(
    parent: &mut [usize],
    vertex: usize,
    meter: &mut crate::build_control::BuildMeter<'_>,
) -> Result<usize, CmgError> {
    let mut root = vertex;
    loop {
        meter.work("forest union find root", 16)?;
        if parent[root] == root {
            break;
        }
        root = parent[root];
    }
    let mut current = vertex;
    loop {
        meter.work("forest union find compress", 32)?;
        if parent[current] == current {
            break;
        }
        let next = parent[current];
        parent[current] = root;
        current = next;
    }
    Ok(root)
}

/// Construct the same forest grouping while selecting heavy incident edges in parallel.
///
/// CSR row ownership makes every vertex selection independent and preserves the
/// serial maximum-weight and lowest-neighbor tie rule exactly. Forest splitting,
/// low-effective-degree correction, and component labeling remain deterministic.
#[cfg(feature = "parallel")]
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

fn finish_forest_grouping(
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
    let mut final_parent = split_forest_trusted(&heavy_parent)?;
    drop(heavy_parent);
    apply_low_effective_degree_correction(
        graph,
        low_effective_degree_threshold,
        &selected_weight,
        &mut final_parent,
    );
    drop(selected_weight);
    Ok(forest_component_labels_trusted(&final_parent))
}

fn apply_low_effective_degree_correction(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    selected_weight: &[f64],
    parent: &mut [usize],
) {
    apply_low_effective_degree_correction_impl::<false>(
        graph,
        low_effective_degree_threshold,
        selected_weight,
        parent,
    )
    .expect("unbudgeted forest allocation");
}

fn apply_low_effective_degree_correction_impl<const ADMITTED: bool>(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    selected_weight: &[f64],
    parent: &mut [usize],
) -> Result<(), CmgError> {
    let has_low_effective_degree =
        graph
            .diagonal()
            .iter()
            .zip(selected_weight)
            .any(|(degree, weight)| {
                *degree > 0.0 && *weight / *degree < low_effective_degree_threshold
            });
    if !has_low_effective_degree {
        return Ok(());
    }

    let mut selected_incident_weight = if ADMITTED {
        try_filled(graph.vertex_count(), 0.0, "forest incident weights")?
    } else {
        vec![0.0; graph.vertex_count()]
    };
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
    Ok(())
}

fn validate_low_effective_degree_threshold(threshold: f64) -> Result<(), CmgError> {
    if !threshold.is_finite() || !(0.0..=1.0).contains(&threshold) {
        return Err(CmgError::InvalidOption {
            name: "low_effective_degree_threshold",
            value: threshold,
        });
    }
    Ok(())
}

/// Select each vertex's maximum-weight incident edge.
///
/// Isolated vertices point to themselves. The selected weight is zero for an
/// isolated vertex.
#[must_use]
pub fn maximum_weight_forest(graph: &Laplacian) -> (Vec<usize>, Vec<f64>) {
    let n = graph.vertex_count();
    let mut parent: Vec<usize> = (0..n).collect();
    let mut selected_weight = vec![0.0; n];

    for edge in graph.edges() {
        consider_parent(
            edge.u(),
            edge.v(),
            edge.weight(),
            &mut parent,
            &mut selected_weight,
        );
        consider_parent(
            edge.v(),
            edge.u(),
            edge.weight(),
            &mut parent,
            &mut selected_weight,
        );
    }
    (parent, selected_weight)
}

/// Select every vertex's maximum-weight incident edge using the supplied executor.
///
/// Small graphs use the original compact edge-list scan. Larger graphs freeze a
/// temporary deterministic CSR representation and assign complete rows to workers.
#[cfg(feature = "parallel")]
pub fn maximum_weight_forest_with_executor(
    graph: &Laplacian,
    executor: &ParallelExecutor,
) -> Result<(Vec<usize>, Vec<f64>), CmgError> {
    let directed_entries = graph.edge_count().saturating_mul(2);
    let density_floor = graph.vertex_count().saturating_mul(3);
    if directed_entries < PARALLEL_SETUP_MIN_ITEMS
        || directed_entries <= density_floor
        || !executor.should_parallel(directed_entries)
    {
        return Ok(maximum_weight_forest(graph));
    }
    let csr = CsrLaplacian::from_laplacian(graph)?;
    Ok(csr.maximum_weight_neighbors_with_executor(executor))
}

fn consider_parent(
    vertex: usize,
    neighbor: usize,
    weight: f64,
    parent: &mut [usize],
    selected_weight: &mut [f64],
) {
    if weight > selected_weight[vertex]
        || (weight == selected_weight[vertex] && neighbor < parent[vertex])
    {
        selected_weight[vertex] = weight;
        parent[vertex] = neighbor;
    }
}

/// Port the upstream `split_forest_` diameter and conductance cuts.
pub fn split_forest(parent: &[usize]) -> Result<Vec<usize>, CmgError> {
    split_forest_impl(parent, true)
}

fn split_forest_trusted(parent: &[usize]) -> Result<Vec<usize>, CmgError> {
    split_forest_impl(parent, false)
}

trait ForestIndegree: Copy {
    const ZERO: Self;

    fn is_zero(self) -> bool;
    fn increment(&mut self);
    fn decrement(&mut self);
}

macro_rules! impl_forest_indegree {
    ($type:ty) => {
        impl ForestIndegree for $type {
            const ZERO: Self = 0;

            #[inline]
            fn is_zero(self) -> bool {
                self == 0
            }

            #[inline]
            fn increment(&mut self) {
                *self = self.checked_add(1).expect("forest indegree overflow");
            }

            #[inline]
            fn decrement(&mut self) {
                *self = self.checked_sub(1).expect("forest indegree invariant");
            }
        }
    };
}

impl_forest_indegree!(u32);
impl_forest_indegree!(usize);

fn split_forest_impl(parent: &[usize], validate: bool) -> Result<Vec<usize>, CmgError> {
    if parent.len() <= u32::MAX as usize {
        split_forest_impl_with_indegree::<u32, false>(parent, validate)
    } else {
        split_forest_impl_with_indegree::<usize, false>(parent, validate)
    }
}

fn split_forest_impl_with_indegree<I: ForestIndegree, const ADMITTED: bool>(
    parent: &[usize],
    validate: bool,
) -> Result<Vec<usize>, CmgError> {
    if validate {
        validate_parent(parent)?;
    }
    let n = parent.len();
    let (mut forest, mut ancestors, mut indegree, mut visited) = if ADMITTED {
        let mut forest = try_capacity(n, "forest split parents")?;
        forest.extend_from_slice(parent);
        (
            forest,
            try_filled(n, 0_i64, "forest ancestors")?,
            try_filled(n, I::ZERO, "forest indegrees")?,
            try_filled(n, false, "forest visited")?,
        )
    } else {
        (
            parent.to_vec(),
            vec![0_i64; n],
            vec![I::ZERO; n],
            vec![false; n],
        )
    };

    for &target in &forest {
        indegree[target].increment();
    }

    let mut walk = if ADMITTED {
        try_capacity(n, "forest walk")?
    } else {
        Vec::new()
    };
    let mut new_ancestors = if ADMITTED {
        try_capacity(n, "forest walk ancestors")?
    } else {
        Vec::new()
    };

    for start in 0..n {
        let mut current = start;
        let mut continue_walk = true;
        while continue_walk && indegree[current].is_zero() && !visited[current] {
            continue_walk = false;
            let mut ancestors_in_path = 0_i64;
            walk.clear();
            walk.push(current);
            new_ancestors.clear();
            new_ancestors.push(0_i64);
            let mut k = 0_usize;

            while k <= 5 || visited[current] {
                current = forest[current];
                let terminated = current == walk[k] || (k > 0 && current == walk[k - 1]);
                if terminated {
                    break;
                }
                k += 1;
                walk.push(current);
                ancestors_in_path += i64::from(u8::from(!visited[current]));
                new_ancestors.push(ancestors_in_path);
            }

            if k > 5 {
                let middle = k / 2;
                forest[walk[middle]] = walk[middle];
                let next = walk[middle + 1];
                indegree[next].decrement();
                let removed = ancestors[walk[middle]];
                for &vertex in &walk[(middle + 1)..=k] {
                    ancestors[vertex] -= removed;
                }
                for index in 0..=middle {
                    let vertex = walk[index];
                    visited[vertex] = true;
                    ancestors[vertex] += new_ancestors[index];
                }
                current = next;
                continue_walk = true;
            }

            if !continue_walk {
                for index in 0..=k {
                    let vertex = walk[index];
                    ancestors[vertex] += new_ancestors[index];
                    visited[vertex] = true;
                }
            }
        }
    }

    for start in 0..n {
        let mut current = start;
        'new_front: while indegree[current].is_zero() {
            let mut previous = current;
            loop {
                let next = forest[current];
                if next == current || next == previous {
                    break 'new_front;
                }
                if ancestors[current] > 2 && ancestors[next] - ancestors[current] > 2 {
                    forest[current] = current;
                    indegree[next].decrement();
                    let removed_ancestors = ancestors[current];

                    let mut adjustment_previous = current;
                    let mut adjustment_current = next;
                    loop {
                        ancestors[adjustment_current] -= removed_ancestors;
                        let adjustment_next = forest[adjustment_current];
                        if adjustment_next == adjustment_current
                            || adjustment_next == adjustment_previous
                        {
                            break;
                        }
                        adjustment_previous = adjustment_current;
                        adjustment_current = adjustment_next;
                    }

                    current = next;
                    continue 'new_front;
                }
                previous = current;
                current = next;
            }
        }
    }

    Ok(forest)
}

fn forest_component_labels_trusted(parent: &[usize]) -> (Vec<usize>, usize) {
    forest_component_labels_impl::<false>(parent).expect("unbudgeted forest allocation")
}

fn forest_component_labels_impl<const ADMITTED: bool>(
    parent: &[usize],
) -> Result<(Vec<usize>, usize), CmgError> {
    let n = parent.len();
    let mut disjoint_set: Vec<usize> = if ADMITTED {
        let mut values = try_capacity(n, "forest union find")?;
        values.extend(0..n);
        values
    } else {
        (0..n).collect()
    };
    for (vertex, &target) in parent.iter().enumerate() {
        union_min_root(&mut disjoint_set, vertex, target);
    }
    for vertex in 0..n {
        disjoint_set[vertex] = find_root(&mut disjoint_set, vertex);
    }

    let mut root_to_label = if ADMITTED {
        try_filled(n, usize::MAX, "forest root labels")?
    } else {
        vec![usize::MAX; n]
    };
    let mut labels = if ADMITTED {
        try_filled(n, 0, "forest labels")?
    } else {
        vec![0; n]
    };
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
    let mut sizes = Vec::new();
    for (vertex, &root) in disjoint_set.iter().enumerate() {
        let label = if root_to_label[root] == usize::MAX {
            let next = sizes.len();
            root_to_label[root] = next;
            sizes.push(0);
            next
        } else {
            root_to_label[root]
        };
        labels[vertex] = label;
        sizes[label] += 1;
    }
    Ok((labels, sizes))
}

fn validate_parent(parent: &[usize]) -> Result<(), CmgError> {
    let n = parent.len();
    for &target in parent {
        if target >= n {
            return Err(CmgError::VertexOutOfBounds {
                vertex: target,
                vertex_count: n,
            });
        }
    }
    Ok(())
}

fn find_root(parent: &mut [usize], vertex: usize) -> usize {
    let mut root = vertex;
    while parent[root] != root {
        root = parent[root];
    }
    let mut current = vertex;
    while parent[current] != current {
        let next = parent[current];
        parent[current] = root;
        current = next;
    }
    root
}

fn union_min_root(parent: &mut [usize], left: usize, right: usize) {
    let left_root = find_root(parent, left);
    let right_root = find_root(parent, right);
    if left_root == right_root {
        return;
    }
    let (root, child) = if left_root < right_root {
        (left_root, right_root)
    } else {
        (right_root, left_root)
    };
    parent[child] = root;
}

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
        let (labels, aggregate_count) = build_forest_aggregation_labels(&graph, 0.125).unwrap();
        assert_eq!(labels, complete.labels());
        assert_eq!(aggregate_count, complete.aggregate_count());
    }
}

#[cfg(test)]
mod trusted_internal_forest_tests {
    use super::{
        forest_component_labels_trusted, forest_components, split_forest, split_forest_trusted,
    };

    #[test]
    fn trusted_internal_paths_match_checked_public_paths() {
        let parent = vec![1, 2, 2, 4, 3, 6, 6, 8, 9, 9, 11, 10];
        let checked_split = split_forest(&parent).unwrap();
        let trusted_split = split_forest_trusted(&parent).unwrap();
        assert_eq!(trusted_split, checked_split);

        let (trusted_labels, aggregate_count) = forest_component_labels_trusted(&checked_split);
        let (checked_labels, sizes) = forest_components(&checked_split).unwrap();
        assert_eq!(trusted_labels, checked_labels);
        assert_eq!(aggregate_count, sizes.len());
    }

    #[test]
    fn public_split_still_rejects_out_of_range_parent() {
        assert!(split_forest(&[0, 2]).is_err());
    }
}

#[cfg(test)]
mod compact_forest_indegree_tests {
    use super::split_forest;

    #[test]
    fn compact_indegree_path_preserves_split_result() {
        let parent = vec![1, 2, 3, 4, 5, 6, 7, 7, 9, 10, 11, 11];
        let result = split_forest(&parent).unwrap();
        assert_eq!(result.len(), parent.len());
        assert!(result.iter().enumerate().all(|(vertex, target)| {
            *target < result.len() && (*target == vertex || parent[vertex] == *target)
        }));
    }
}

#[cfg(test)]
mod branch_free_conductance_tests {
    use super::split_forest;

    #[test]
    fn conductance_refactor_preserves_representative_parent_vectors() {
        for parent in [
            vec![1, 2, 3, 4, 5, 6, 7, 7],
            vec![1, 2, 3, 4, 4, 6, 7, 7],
            vec![1, 1, 3, 3, 5, 6, 6, 7],
        ] {
            let split = split_forest(&parent).unwrap();
            assert_eq!(split.len(), parent.len());
            assert!(split.iter().all(|target| *target < split.len()));
        }
    }
}

#[cfg(test)]
mod branchless_ancestor_recording_tests {
    #[test]
    fn boolean_increment_matches_branching_definition() {
        for visited in [false, true] {
            let branchless = i64::from(u8::from(!visited));
            let branching = if visited { 0 } else { 1 };
            assert_eq!(branchless, branching);
        }
    }
}

#[cfg(all(test, feature = "experimental-components"))]
mod controlled_forest_tests {
    use super::{
        build_forest_aggregation_labels_admitted, build_forest_aggregation_labels_controlled,
    };
    use crate::build_control::{
        BuildBoundary, BuildDecision, BuildMeter, BuildOperation, BuildStopReason,
    };
    use crate::{CmgError, Laplacian};

    fn fixtures() -> Vec<Laplacian> {
        let path = (0..39).map(|vertex| (vertex, vertex + 1, (vertex + 1) as f64));
        let dense = (0..10).flat_map(|left| {
            ((left + 1)..10).map(move |right| (left, right, 1.0 + (left + right) as f64 / 16.0))
        });
        vec![
            Laplacian::from_edges(40, path).unwrap(),
            Laplacian::from_edges(12, dense).unwrap(),
            Laplacian::from_edges(
                14,
                [
                    (0, 1, 1.0),
                    (1, 2, 2.0),
                    (2, 3, 3.0),
                    (3, 4, 4.0),
                    (4, 5, 5.0),
                    (5, 6, 6.0),
                    (6, 7, 7.0),
                    (8, 2, 1.0),
                    (9, 2, 1.0),
                    (10, 5, 1.0),
                    (11, 5, 1.0),
                ],
            )
            .unwrap(),
        ]
    }

    fn recording_run(graph: &Laplacian, threshold: f64) -> Vec<BuildBoundary> {
        let expected = build_forest_aggregation_labels_admitted(graph, threshold).unwrap();
        let mut boundaries = Vec::new();
        let mut control = |boundary| {
            boundaries.push(boundary);
            BuildDecision::Continue
        };
        let actual = build_forest_aggregation_labels_controlled(
            graph,
            threshold,
            &mut BuildMeter::new(u64::MAX, &mut control),
        )
        .unwrap();
        assert_eq!(actual, expected);
        boundaries
    }

    #[test]
    fn controlled_labels_match_admitted_and_boundaries_repeat_exactly() {
        for graph in fixtures() {
            for threshold in [0.0, 0.125, 0.9, 1.0] {
                let first = recording_run(&graph, threshold);
                assert_eq!(recording_run(&graph, threshold), first);
                for (ordinal, boundary) in first.iter().enumerate() {
                    assert_eq!(boundary.chunk_id, ordinal as u64);
                    if let BuildOperation::Work { units } = boundary.operation {
                        assert!((1..=1024).contains(&units));
                    }
                }
            }
        }
    }

    #[test]
    fn either_stop_decision_returns_at_each_forest_boundary() {
        let graph = Laplacian::from_edges(
            8,
            [
                (0, 1, 1.0),
                (1, 2, 2.0),
                (2, 3, 3.0),
                (3, 4, 4.0),
                (4, 5, 5.0),
                (5, 6, 6.0),
                (6, 7, 7.0),
                (0, 7, 0.5),
            ],
        )
        .unwrap();
        let boundaries = recording_run(&graph, 0.9);
        for expected in &boundaries {
            for (decision, reason) in [
                (BuildDecision::StopUnused, BuildStopReason::StopUnused),
                (BuildDecision::Interrupted, BuildStopReason::Interrupted),
            ] {
                let mut observed = Vec::new();
                let mut control = |boundary: BuildBoundary| {
                    observed.push(boundary);
                    if boundary.chunk_id == expected.chunk_id {
                        decision
                    } else {
                        BuildDecision::Continue
                    }
                };
                let error = build_forest_aggregation_labels_controlled(
                    &graph,
                    0.9,
                    &mut BuildMeter::new(u64::MAX, &mut control),
                )
                .unwrap_err();
                assert_eq!(observed, boundaries[..=expected.chunk_id as usize]);
                assert!(matches!(error, CmgError::BuildStopped {
                    reason: actual_reason, phase, chunk_id, charged_work,
                } if actual_reason == reason
                    && phase == expected.phase
                    && chunk_id == expected.chunk_id
                    && charged_work == expected.charged_work));
            }
        }
    }

    #[test]
    fn budget_denies_each_work_boundary_before_callback() {
        let graph = Laplacian::from_edges(3, [(0, 1, 1.0), (1, 2, 2.0)]).unwrap();
        let boundaries = recording_run(&graph, 0.125);
        let mut complete_budget = 0;
        for expected in &boundaries {
            let BuildOperation::Work { units } = expected.operation else {
                continue;
            };
            complete_budget = expected.charged_work + u64::from(units);
            let mut calls = 0;
            let mut control = |_boundary| {
                calls += 1;
                BuildDecision::Continue
            };
            let error = build_forest_aggregation_labels_controlled(
                &graph,
                0.125,
                &mut BuildMeter::new(complete_budget - 1, &mut control),
            )
            .unwrap_err();
            assert_eq!(calls, expected.chunk_id);
            assert!(matches!(error, CmgError::BuildStopped {
                reason: BuildStopReason::WorkBudgetExhausted, phase, chunk_id, charged_work,
            } if phase == expected.phase
                && chunk_id == expected.chunk_id
                && charged_work == expected.charged_work));
        }
        let mut control = |_| BuildDecision::Continue;
        let result = build_forest_aggregation_labels_controlled(
            &graph,
            0.125,
            &mut BuildMeter::new(complete_budget, &mut control),
        )
        .unwrap();
        assert_eq!(
            result,
            build_forest_aggregation_labels_admitted(&graph, 0.125).unwrap()
        );
    }
}
