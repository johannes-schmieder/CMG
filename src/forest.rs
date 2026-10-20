//! Heavy-edge forests and the CMG forest-partitioning heuristics.

use crate::cancel::{Meter, Unmetered};
use crate::{CmgError, Laplacian};

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
    let meter = &mut Unmetered;
    let (heavy_parent, selected_weight) = heavy_forest(graph, meter)?;
    let split_parent = split_forest(&heavy_parent)?;
    let mut final_parent = split_parent.clone();
    low_effective_degree_correction(
        graph,
        low_effective_degree_threshold,
        &selected_weight,
        &mut final_parent,
        meter,
    )?;
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

/// Construct only the aggregation labels needed by hierarchy construction.
///
/// Every walk, including union-find root discovery and compression, is made
/// of bounded counted operations. Fixed-capacity storage keeps walk pushes
/// fallible at provisioning and prevents hidden growth during traversal.
pub(crate) fn build_forest_aggregation_labels<M: Meter>(
    graph: &Laplacian,
    threshold: f64,
    meter: &mut M,
) -> Result<(Vec<usize>, usize), CmgError> {
    meter.work("forest threshold", 16)?;
    validate_low_effective_degree_threshold(threshold)?;
    let (parent, weights) = heavy_forest(graph, meter)?;
    let mut split = if parent.len() <= u32::MAX as usize {
        split_forest_with::<u32, M>(&parent, meter)?
    } else {
        split_forest_with::<usize, M>(&parent, meter)?
    };
    drop(parent);
    low_effective_degree_correction(graph, threshold, &weights, &mut split, meter)?;
    drop(weights);
    component_labels(&split, meter)
}

/// Select each vertex's maximum-weight incident edge.
///
/// Isolated vertices point to themselves. The selected weight is zero for an
/// isolated vertex.
#[must_use]
pub fn maximum_weight_forest(graph: &Laplacian) -> (Vec<usize>, Vec<f64>) {
    heavy_forest(graph, &mut Unmetered).expect("heavy forest allocation")
}

fn heavy_forest<M: Meter>(
    graph: &Laplacian,
    meter: &mut M,
) -> Result<(Vec<usize>, Vec<f64>), CmgError> {
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
    Ok((parent, weights))
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

fn low_effective_degree_correction<M: Meter>(
    graph: &Laplacian,
    threshold: f64,
    selected_weight: &[f64],
    parent: &mut [usize],
    meter: &mut M,
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

fn validate_low_effective_degree_threshold(threshold: f64) -> Result<(), CmgError> {
    if !threshold.is_finite() || !(0.0..=1.0).contains(&threshold) {
        return Err(CmgError::InvalidOption {
            name: "low_effective_degree_threshold",
            value: threshold,
        });
    }
    Ok(())
}

/// Port the upstream `split_forest_` diameter and conductance cuts.
pub fn split_forest(parent: &[usize]) -> Result<Vec<usize>, CmgError> {
    validate_parent(parent)?;
    if parent.len() <= u32::MAX as usize {
        split_forest_with::<u32, _>(parent, &mut Unmetered)
    } else {
        split_forest_with::<usize, _>(parent, &mut Unmetered)
    }
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

fn split_forest_with<I: ForestIndegree, M: Meter>(
    parent: &[usize],
    meter: &mut M,
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

/// Deterministic union-find labels in first-occurrence root order.
fn component_labels<M: Meter>(
    parent: &[usize],
    meter: &mut M,
) -> Result<(Vec<usize>, usize), CmgError> {
    let n = parent.len();
    let mut disjoint_set = meter.try_capacity(n, "forest union find")?;
    for vertex in 0..n {
        meter.work("forest union find initialize", 16)?;
        disjoint_set.push(vertex);
    }
    for (vertex, &target) in parent.iter().enumerate() {
        meter.work("forest union find edges", 16)?;
        let left_root = find_root(&mut disjoint_set, vertex, meter)?;
        let right_root = find_root(&mut disjoint_set, target, meter)?;
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
        disjoint_set[vertex] = find_root(&mut disjoint_set, vertex, meter)?;
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

fn find_root<M: Meter>(
    parent: &mut [usize],
    vertex: usize,
    meter: &mut M,
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

/// Compute deterministic connected components of a functional forest.
pub fn forest_components(parent: &[usize]) -> Result<(Vec<usize>, Vec<usize>), CmgError> {
    validate_parent(parent)?;
    let (labels, count) = component_labels(parent, &mut Unmetered)?;
    let mut sizes = crate::storage::try_filled(count, 0, "forest component sizes")?;
    for &label in &labels {
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cancel::{CancelMeter, StopAt};

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

    #[test]
    fn hierarchy_labels_match_complete_diagnostics() {
        for graph in fixtures() {
            for threshold in [0.0, 0.125, 0.9, 1.0] {
                let complete = build_forest_grouping(&graph, threshold).unwrap();
                let (labels, count) =
                    build_forest_aggregation_labels(&graph, threshold, &mut Unmetered).unwrap();
                assert_eq!(labels, complete.labels());
                assert_eq!(count, complete.aggregate_count());
                let split = split_forest(complete.heavy_parent()).unwrap();
                assert_eq!(split, complete.split_parent());
                let (forest_labels, sizes) = forest_components(complete.final_parent()).unwrap();
                assert_eq!(forest_labels, labels);
                assert_eq!(sizes.len(), count);
            }
        }
    }

    #[test]
    fn public_split_rejects_out_of_range_parents_and_keeps_valid_targets() {
        assert!(split_forest(&[0, 2]).is_err());
        for parent in [
            vec![1, 2, 3, 4, 5, 6, 7, 7],
            vec![1, 2, 3, 4, 4, 6, 7, 7],
            vec![1, 1, 3, 3, 5, 6, 6, 7],
            vec![1, 2, 3, 4, 5, 6, 7, 7, 9, 10, 11, 11],
        ] {
            let split = split_forest(&parent).unwrap();
            assert!(split.iter().enumerate().all(|(vertex, &target)| {
                target < split.len() && (target == vertex || parent[vertex] == target)
            }));
        }
    }

    fn counting_run(graph: &Laplacian, threshold: f64) -> usize {
        let expected = build_forest_aggregation_labels(graph, threshold, &mut Unmetered).unwrap();
        let count = StopAt::never();
        let query = || count.query();
        let actual = build_forest_aggregation_labels(
            graph,
            threshold,
            &mut CancelMeter::every_operation(&query),
        )
        .unwrap();
        assert_eq!(actual, expected);
        count.polls()
    }

    #[test]
    fn metered_labels_match_unmetered_and_polls_repeat_exactly() {
        for graph in fixtures() {
            for threshold in [0.0, 0.125, 0.9, 1.0] {
                let first = counting_run(&graph, threshold);
                assert!(first > 0);
                assert_eq!(counting_run(&graph, threshold), first);
            }
        }
    }

    #[test]
    fn cancellation_returns_at_each_forest_poll() {
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
        for stop in 0..counting_run(&graph, 0.9) {
            let cancel = StopAt::at(Some(stop));
            let query = || cancel.query();
            let error = build_forest_aggregation_labels(
                &graph,
                0.9,
                &mut CancelMeter::every_operation(&query),
            )
            .unwrap_err();
            assert_eq!(cancel.polls(), stop + 1);
            assert!(matches!(error, CmgError::Cancelled { .. }));
        }
    }
}
