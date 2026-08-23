//! Canonical weighted graph-Laplacian representation.

use crate::CmgError;
#[cfg(feature = "parallel")]
use crate::{ParallelExecutor, execution::PARALLEL_SETUP_MIN_ITEMS};
#[cfg(feature = "parallel")]
use rayon::prelude::*;
use std::sync::Arc;

/// A canonical undirected weighted edge with `u < v` and positive weight.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Edge {
    u: u32,
    v: u32,
    weight: f64,
}

impl Edge {
    /// Return the lower-numbered endpoint.
    #[must_use]
    pub const fn u(self) -> usize {
        self.u as usize
    }

    /// Return the higher-numbered endpoint.
    #[must_use]
    pub const fn v(self) -> usize {
        self.v as usize
    }

    /// Return the strictly positive edge weight.
    #[must_use]
    pub const fn weight(self) -> f64 {
        self.weight
    }

    /// Construct an edge from already validated internal graph data.
    pub(crate) fn from_internal_parts(
        left: usize,
        right: usize,
        weight: f64,
    ) -> Result<Self, CmgError> {
        debug_assert!(left != right);
        debug_assert!(weight.is_finite() && weight > 0.0);
        let (u, v) = if left < right {
            (left, right)
        } else {
            (right, left)
        };
        let u = u32::try_from(u).map_err(|_| CmgError::VertexIndexTooWide {
            vertex: u,
            maximum: u32::MAX as usize,
        })?;
        let v = u32::try_from(v).map_err(|_| CmgError::VertexIndexTooWide {
            vertex: v,
            maximum: u32::MAX as usize,
        })?;
        Ok(Self { u, v, weight })
    }
}

/// A deterministic edge-list representation of a weighted graph Laplacian.
#[derive(Debug, Clone)]
pub struct Laplacian {
    vertex_count: usize,
    edges: Vec<Edge>,
    diagonal: Vec<f64>,
    matrix_nnz: usize,
    operator_norm_bound: f64,
    lineage: Arc<()>,
}

impl PartialEq for Laplacian {
    fn eq(&self, other: &Self) -> bool {
        self.vertex_count == other.vertex_count
            && self.edges == other.edges
            && self.diagonal == other.diagonal
            && self.matrix_nnz == other.matrix_nnz
            && self.operator_norm_bound == other.operator_norm_bound
    }
}

impl Laplacian {
    /// Build a Laplacian from undirected positive-weight edges.
    ///
    /// Endpoint order is canonicalized, duplicate edges are sorted by weight
    /// and summed with compensated summation, and the final edge list is sorted
    /// lexicographically by endpoint pair.
    pub fn from_edges<I>(vertex_count: usize, edges: I) -> Result<Self, CmgError>
    where
        I: IntoIterator<Item = (usize, usize, f64)>,
    {
        let mut raw = collect_validated_edges(vertex_count, edges)?;
        raw.sort_unstable_by(compare_raw_edges);
        Self::from_sorted_raw_edges(vertex_count, raw)
    }

    /// Build a Laplacian while parallelizing deterministic edge sorting.
    ///
    /// Validation and compensated duplicate aggregation are identical to
    /// [`Self::from_edges`]. The package-owned executor is used only when the
    /// collected edge count exceeds its configured parallel threshold.
    #[cfg(feature = "parallel")]
    pub fn from_edges_with_executor<I>(
        vertex_count: usize,
        edges: I,
        executor: &ParallelExecutor,
    ) -> Result<Self, CmgError>
    where
        I: IntoIterator<Item = (usize, usize, f64)>,
    {
        let mut raw = collect_validated_edges(vertex_count, edges)?;
        if raw.len() >= PARALLEL_SETUP_MIN_ITEMS && executor.should_parallel(raw.len()) {
            executor.install(|| raw.par_sort_unstable_by(compare_raw_edges));
        } else {
            raw.sort_unstable_by(compare_raw_edges);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
    }

    pub(crate) fn from_compact_edges(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        raw.sort_unstable_by(compare_raw_edges);
        Self::from_sorted_raw_edges(vertex_count, raw)
    }

    #[cfg(feature = "parallel")]
    pub(crate) fn from_compact_edges_with_executor(
        vertex_count: usize,
        mut raw: Vec<Edge>,
        executor: &ParallelExecutor,
    ) -> Result<Self, CmgError> {
        if raw.len() >= PARALLEL_SETUP_MIN_ITEMS && executor.should_parallel(raw.len()) {
            executor.install(|| raw.par_sort_unstable_by(compare_raw_edges));
        } else {
            raw.sort_unstable_by(compare_raw_edges);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
    }

    fn from_sorted_raw_edges(vertex_count: usize, mut raw: Vec<Edge>) -> Result<Self, CmgError> {
        // Equal endpoint pairs are contiguous after sorting. Merge them into
        // the front of the compact input buffer so graph construction does not
        // allocate a separate full-capacity canonical vector.
        let mut read_index = 0;
        let mut write_index = 0;
        while read_index < raw.len() {
            let u = raw[read_index].u;
            let v = raw[read_index].v;
            let group_start = read_index;
            while read_index < raw.len() && raw[read_index].u == u && raw[read_index].v == v {
                read_index += 1;
            }
            let weight =
                compensated_sum(raw[group_start..read_index].iter().map(|edge| edge.weight));
            if !weight.is_finite() || weight <= 0.0 {
                return Err(CmgError::InvalidEdgeWeight {
                    u: u as usize,
                    v: v as usize,
                    weight,
                });
            }
            raw[write_index] = Edge { u, v, weight };
            write_index += 1;
        }
        raw.truncate(write_index);
        // Filtered coarse-edge iterators have a zero lower size hint, so their
        // vectors may grow beyond the final length. Do not retain that spare
        // capacity in every hierarchy level.
        if raw.capacity() != raw.len() {
            raw.shrink_to_fit();
        }

        let mut diagonal = vec![0.0; vertex_count];
        for edge in &raw {
            diagonal[edge.u()] += edge.weight;
            diagonal[edge.v()] += edge.weight;
        }

        let diagonal_nnz = diagonal.iter().filter(|degree| **degree != 0.0).count();
        let matrix_nnz = diagonal_nnz + 2 * raw.len();
        let operator_norm_bound = 2.0 * diagonal.iter().copied().fold(0.0, f64::max);

        Ok(Self {
            vertex_count,
            edges: raw,
            diagonal,
            matrix_nnz,
            operator_norm_bound,
            lineage: Arc::new(()),
        })
    }

    /// Return the number of vertices, including isolated vertices.
    #[must_use]
    pub const fn vertex_count(&self) -> usize {
        self.vertex_count
    }

    /// Return the number of canonical undirected edges.
    #[must_use]
    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    /// Return the canonical edge slice.
    #[must_use]
    pub fn edges(&self) -> &[Edge] {
        &self.edges
    }

    /// Return the weighted degree (Laplacian diagonal) of every vertex.
    #[must_use]
    pub fn diagonal(&self) -> &[f64] {
        &self.diagonal
    }

    /// Return the number of nonzero entries in the corresponding symmetric
    /// sparse matrix.
    #[must_use]
    pub const fn matrix_nnz(&self) -> usize {
        self.matrix_nnz
    }

    /// Return an inexpensive upper bound on the Euclidean operator norm.
    #[must_use]
    pub const fn operator_norm_bound(&self) -> f64 {
        self.operator_norm_bound
    }

    pub(crate) fn shares_lineage(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.lineage, &other.lineage)
    }

    pub(crate) const fn lineage(&self) -> &Arc<()> {
        &self.lineage
    }

    /// Compute `output = L * input` without allocating.
    pub fn matvec_into(&self, input: &[f64], output: &mut [f64]) -> Result<(), CmgError> {
        if input.len() != self.vertex_count {
            return Err(CmgError::dimension(
                "Laplacian::matvec input",
                self.vertex_count,
                input.len(),
            ));
        }
        if output.len() != self.vertex_count {
            return Err(CmgError::dimension(
                "Laplacian::matvec output",
                self.vertex_count,
                output.len(),
            ));
        }
        output.fill(0.0);
        for edge in &self.edges {
            let difference = edge.weight * (input[edge.u()] - input[edge.v()]);
            output[edge.u()] += difference;
            output[edge.v()] -= difference;
        }
        Ok(())
    }

    /// Compute and return `L * input`.
    pub fn matvec(&self, input: &[f64]) -> Result<Vec<f64>, CmgError> {
        let mut output = vec![0.0; self.vertex_count];
        self.matvec_into(input, &mut output)?;
        Ok(output)
    }

    /// Compute the Laplacian energy `input^T L input` from the edge identity.
    pub fn energy(&self, input: &[f64]) -> Result<f64, CmgError> {
        if input.len() != self.vertex_count {
            return Err(CmgError::dimension(
                "Laplacian::energy",
                self.vertex_count,
                input.len(),
            ));
        }
        Ok(compensated_sum(self.edges.iter().map(|edge| {
            let difference = input[edge.u()] - input[edge.v()];
            edge.weight * difference * difference
        })))
    }

    /// Materialize the Laplacian as a row-major dense matrix.
    ///
    /// This is intended for tests, diagnostics, and genuinely small systems.
    #[must_use]
    pub fn to_dense(&self) -> Vec<Vec<f64>> {
        let mut dense = vec![vec![0.0; self.vertex_count]; self.vertex_count];
        for edge in &self.edges {
            dense[edge.u()][edge.u()] += edge.weight;
            dense[edge.v()][edge.v()] += edge.weight;
            dense[edge.u()][edge.v()] -= edge.weight;
            dense[edge.v()][edge.u()] -= edge.weight;
        }
        dense
    }
}

fn collect_validated_edges<I>(vertex_count: usize, edges: I) -> Result<Vec<Edge>, CmgError>
where
    I: IntoIterator<Item = (usize, usize, f64)>,
{
    let iterator = edges.into_iter();
    let mut raw = Vec::with_capacity(iterator.size_hint().0);
    for (left, right, weight) in iterator {
        if left >= vertex_count {
            return Err(CmgError::VertexOutOfBounds {
                vertex: left,
                vertex_count,
            });
        }
        if right >= vertex_count {
            return Err(CmgError::VertexOutOfBounds {
                vertex: right,
                vertex_count,
            });
        }
        if left > u32::MAX as usize {
            return Err(CmgError::VertexIndexTooWide {
                vertex: left,
                maximum: u32::MAX as usize,
            });
        }
        if right > u32::MAX as usize {
            return Err(CmgError::VertexIndexTooWide {
                vertex: right,
                maximum: u32::MAX as usize,
            });
        }
        if left == right {
            return Err(CmgError::SelfLoop { vertex: left });
        }
        if !weight.is_finite() || weight <= 0.0 {
            return Err(CmgError::InvalidEdgeWeight {
                u: left,
                v: right,
                weight,
            });
        }
        let (u, v) = if left < right {
            (left, right)
        } else {
            (right, left)
        };
        raw.push(Edge {
            u: u as u32,
            v: v as u32,
            weight,
        });
    }
    Ok(raw)
}

fn compare_raw_edges(left: &Edge, right: &Edge) -> core::cmp::Ordering {
    let left_endpoints = (u64::from(left.u) << 32) | u64::from(left.v);
    let right_endpoints = (u64::from(right.u) << 32) | u64::from(right.v);
    left_endpoints
        .cmp(&right_endpoints)
        .then_with(|| left.weight.total_cmp(&right.weight))
}

pub(crate) fn compensated_sum<I>(values: I) -> f64
where
    I: IntoIterator<Item = f64>,
{
    let mut sum = 0.0;
    let mut correction = 0.0;
    for value in values {
        let next = sum + value;
        correction += if sum.abs() >= value.abs() {
            (sum - next) + value
        } else {
            (value - next) + sum
        };
        sum = next;
    }
    sum + correction
}

pub(crate) fn close(left: f64, right: f64, tolerance: f64) -> bool {
    let scale = 1.0_f64.max(left.abs()).max(right.abs());
    (left - right).abs() <= tolerance * scale
}

#[cfg(test)]
mod tests {
    use super::Laplacian;

    #[test]
    fn clones_share_lineage_but_independent_equal_graphs_do_not() {
        let graph = Laplacian::from_edges(3, [(0, 1, 1.0), (1, 2, 2.0)]).unwrap();
        let clone = graph.clone();
        let rebuilt = Laplacian::from_edges(3, [(0, 1, 1.0), (1, 2, 2.0)]).unwrap();

        assert!(graph.shares_lineage(&clone));
        assert!(!graph.shares_lineage(&rebuilt));
        assert_eq!(graph, rebuilt);
        assert_eq!(graph.matrix_nnz(), 7);
        assert_eq!(graph.operator_norm_bound(), 6.0);
    }

    #[test]
    fn cached_invariants_include_isolated_vertices_correctly() {
        let graph = Laplacian::from_edges(4, [(0, 1, 2.5)]).unwrap();
        assert_eq!(graph.matrix_nnz(), 4);
        assert_eq!(graph.operator_norm_bound(), 5.0);
    }
}

#[cfg(test)]
mod compact_edge_layout_tests {
    use super::{Edge, Laplacian};
    use crate::CmgError;

    #[test]
    fn edge_uses_compact_endpoints() {
        assert_eq!(std::mem::size_of::<Edge>(), 16);
        assert_eq!(std::mem::align_of::<Edge>(), 8);
    }

    #[cfg(target_pointer_width = "64")]
    #[test]
    fn endpoint_above_u32_is_rejected_before_graph_allocation() {
        let vertex = u32::MAX as usize + 1;
        let error = Laplacian::from_edges(vertex + 1, [(0, vertex, 1.0)]).unwrap_err();
        assert_eq!(
            error,
            CmgError::VertexIndexTooWide {
                vertex,
                maximum: u32::MAX as usize,
            }
        );
    }
}
