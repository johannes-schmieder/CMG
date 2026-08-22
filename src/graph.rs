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
    u: usize,
    v: usize,
    weight: f64,
}

impl Edge {
    /// Return the lower-numbered endpoint.
    #[must_use]
    pub const fn u(self) -> usize {
        self.u
    }

    /// Return the higher-numbered endpoint.
    #[must_use]
    pub const fn v(self) -> usize {
        self.v
    }

    /// Return the strictly positive edge weight.
    #[must_use]
    pub const fn weight(self) -> f64 {
        self.weight
    }
}

/// A deterministic edge-list representation of a weighted graph Laplacian.
#[derive(Debug, Clone)]
pub struct Laplacian {
    vertex_count: usize,
    edges: Vec<Edge>,
    diagonal: Vec<f64>,
    lineage: Arc<()>,
}

impl PartialEq for Laplacian {
    fn eq(&self, other: &Self) -> bool {
        self.vertex_count == other.vertex_count
            && self.edges == other.edges
            && self.diagonal == other.diagonal
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
        raw.sort_by(compare_raw_edges);
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
            raw.sort_by(compare_raw_edges);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
    }

    fn from_sorted_raw_edges(
        vertex_count: usize,
        raw: Vec<(usize, usize, f64)>,
    ) -> Result<Self, CmgError> {
        let mut canonical = Vec::with_capacity(raw.len());
        let mut cursor = 0;
        while cursor < raw.len() {
            let u = raw[cursor].0;
            let v = raw[cursor].1;
            let start = cursor;
            while cursor < raw.len() && raw[cursor].0 == u && raw[cursor].1 == v {
                cursor += 1;
            }
            let weight = compensated_sum(raw[start..cursor].iter().map(|edge| edge.2));
            if !weight.is_finite() || weight <= 0.0 {
                return Err(CmgError::InvalidEdgeWeight { u, v, weight });
            }
            canonical.push(Edge { u, v, weight });
        }

        let mut diagonal = vec![0.0; vertex_count];
        for edge in &canonical {
            diagonal[edge.u] += edge.weight;
            diagonal[edge.v] += edge.weight;
        }

        Ok(Self {
            vertex_count,
            edges: canonical,
            diagonal,
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
    pub fn matrix_nnz(&self) -> usize {
        let diagonal_nnz = self
            .diagonal
            .iter()
            .filter(|degree| **degree != 0.0)
            .count();
        diagonal_nnz + 2 * self.edges.len()
    }

    /// Return an inexpensive upper bound on the Euclidean operator norm.
    #[must_use]
    pub fn operator_norm_bound(&self) -> f64 {
        2.0 * self.diagonal.iter().copied().fold(0.0, f64::max)
    }

    pub(crate) fn shares_lineage(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.lineage, &other.lineage)
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
            let difference = edge.weight * (input[edge.u] - input[edge.v]);
            output[edge.u] += difference;
            output[edge.v] -= difference;
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
            let difference = input[edge.u] - input[edge.v];
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
            dense[edge.u][edge.u] += edge.weight;
            dense[edge.v][edge.v] += edge.weight;
            dense[edge.u][edge.v] -= edge.weight;
            dense[edge.v][edge.u] -= edge.weight;
        }
        dense
    }
}

fn collect_validated_edges<I>(
    vertex_count: usize,
    edges: I,
) -> Result<Vec<(usize, usize, f64)>, CmgError>
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
        raw.push((u, v, weight));
    }
    Ok(raw)
}

fn compare_raw_edges(
    left: &(usize, usize, f64),
    right: &(usize, usize, f64),
) -> core::cmp::Ordering {
    left.0
        .cmp(&right.0)
        .then(left.1.cmp(&right.1))
        .then_with(|| left.2.total_cmp(&right.2))
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
    }
}
