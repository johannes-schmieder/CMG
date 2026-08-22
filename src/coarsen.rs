//! Deterministic restriction, prolongation, and Galerkin graph contraction.

use crate::{CmgError, Laplacian};

/// A zero-based partition of fine vertices into coarse aggregates.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Aggregation {
    labels: Vec<usize>,
    sizes: Vec<usize>,
}

impl Aggregation {
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
        Ok(Self { labels, sizes })
    }

    /// Return the fine-to-coarse labels.
    #[must_use]
    pub fn labels(&self) -> &[usize] {
        &self.labels
    }

    /// Return aggregate sizes.
    #[must_use]
    pub fn sizes(&self) -> &[usize] {
        &self.sizes
    }

    /// Return the fine dimension.
    #[must_use]
    pub fn fine_dimension(&self) -> usize {
        self.labels.len()
    }

    /// Return the coarse dimension.
    #[must_use]
    pub fn coarse_dimension(&self) -> usize {
        self.sizes.len()
    }

    /// Restrict by summing fine values within every aggregate.
    pub fn restrict(&self, fine: &[f64]) -> Result<Vec<f64>, CmgError> {
        if fine.len() != self.fine_dimension() {
            return Err(CmgError::dimension(
                "Aggregation::restrict",
                self.fine_dimension(),
                fine.len(),
            ));
        }
        let mut coarse = vec![0.0; self.coarse_dimension()];
        for (&value, &label) in fine.iter().zip(&self.labels) {
            coarse[label] += value;
        }
        Ok(coarse)
    }

    /// Prolong by copying each coarse value to its fine aggregate members.
    pub fn prolong(&self, coarse: &[f64]) -> Result<Vec<f64>, CmgError> {
        if coarse.len() != self.coarse_dimension() {
            return Err(CmgError::dimension(
                "Aggregation::prolong",
                self.coarse_dimension(),
                coarse.len(),
            ));
        }
        Ok(self.labels.iter().map(|&label| coarse[label]).collect())
    }

    /// Form the exact graph Laplacian `R L R^T`.
    pub fn contract(&self, graph: &Laplacian) -> Result<Laplacian, CmgError> {
        if graph.vertex_count() != self.fine_dimension() {
            return Err(CmgError::dimension(
                "Aggregation::contract",
                self.fine_dimension(),
                graph.vertex_count(),
            ));
        }
        let coarse_edges = graph.edges().iter().filter_map(|edge| {
            let left = self.labels[edge.u()];
            let right = self.labels[edge.v()];
            (left != right).then_some((left, right, edge.weight()))
        });
        Laplacian::from_edges(self.coarse_dimension(), coarse_edges)
    }
}
