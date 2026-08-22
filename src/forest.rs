//! Heavy-edge forests and the CMG forest-partitioning heuristics.

#[cfg(feature = "parallel")]
use crate::{CsrLaplacian, ParallelExecutor};
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
    let (heavy_parent, selected_weight) = maximum_weight_forest(graph);
    finish_forest_grouping(
        graph,
        low_effective_degree_threshold,
        heavy_parent,
        selected_weight,
    )
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
    if !executor.should_parallel(graph.edge_count().saturating_mul(2)) {
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
    validate_parent(parent)?;
    let n = parent.len();
    let mut forest = parent.to_vec();
    let mut ancestors = vec![0_i64; n];
    let mut indegree = vec![0_usize; n];
    let mut visited = vec![false; n];

    for &target in &forest {
        indegree[target] += 1;
    }

    for start in 0..n {
        let mut current = start;
        let mut continue_walk = true;
        while continue_walk && indegree[current] == 0 && !visited[current] {
            continue_walk = false;
            let mut ancestors_in_path = 0_i64;
            let mut walk = vec![current];
            let mut new_ancestors = vec![0_i64];
            let mut k = 0_usize;

            while k <= 5 || visited[current] {
                current = forest[current];
                let terminated = current == walk[k] || (k > 0 && current == walk[k - 1]);
                if terminated {
                    break;
                }
                k += 1;
                walk.push(current);
                if visited[current] {
                    new_ancestors.push(ancestors_in_path);
                } else {
                    ancestors_in_path += 1;
                    new_ancestors.push(ancestors_in_path);
                }
            }

            if k > 5 {
                let middle = k / 2;
                forest[walk[middle]] = walk[middle];
                let next = walk[middle + 1];
                indegree[next] = indegree[next]
                    .checked_sub(1)
                    .expect("forest indegree invariant");
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
        let mut continue_walk = true;
        while continue_walk && indegree[current] == 0 {
            continue_walk = false;
            let mut previous = current;
            let mut cut_mode = false;
            let mut removed_ancestors = 0_i64;
            let mut new_front = current;

            loop {
                let next = forest[current];
                if next == current || next == previous {
                    break;
                }
                if !cut_mode && ancestors[current] > 2 && ancestors[next] - ancestors[current] > 2 {
                    forest[current] = current;
                    indegree[next] = indegree[next]
                        .checked_sub(1)
                        .expect("forest indegree invariant");
                    removed_ancestors = ancestors[current];
                    new_front = next;
                    cut_mode = true;
                }
                previous = current;
                current = next;
                if cut_mode {
                    ancestors[current] -= removed_ancestors;
                }
            }
            if cut_mode {
                continue_walk = true;
                current = new_front;
            }
        }
    }

    Ok(forest)
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
