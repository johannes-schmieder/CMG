//! Deterministic restriction, prolongation, and Galerkin graph contraction.

#[cfg(feature = "parallel")]
use crate::ParallelExecutor;
use crate::{CmgError, Laplacian};
#[cfg(feature = "parallel")]
use rayon::prelude::*;

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
        for (&value, &label) in fine.iter().zip(&self.labels) {
            coarse[label] += value;
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
        for (value, &label) in fine.iter_mut().zip(&self.labels) {
            *value = coarse[label];
        }
        Ok(())
    }

    /// Add a prolonged coarse vector to a fine vector in place.
    pub fn prolong_add_into(&self, coarse: &[f64], fine: &mut [f64]) -> Result<(), CmgError> {
        validate_prolong_dimensions(self, coarse, fine)?;
        for (value, &label) in fine.iter_mut().zip(&self.labels) {
            *value += coarse[label];
        }
        Ok(())
    }

    /// Form the exact graph Laplacian `R L R^T`.
    pub fn contract(&self, graph: &Laplacian) -> Result<Laplacian, CmgError> {
        self.validate_contract_graph(graph)?;
        let coarse_edges = graph.edges().iter().filter_map(|edge| {
            let left = self.labels[edge.u()];
            let right = self.labels[edge.v()];
            (left != right).then_some((left, right, edge.weight()))
        });
        Laplacian::from_edges(self.coarse_dimension(), coarse_edges)
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
        if !executor.should_parallel(graph.edge_count()) {
            return self.contract(graph);
        }
        let coarse_edges: Vec<(usize, usize, f64)> = executor.install(|| {
            graph
                .edges()
                .par_iter()
                .filter_map(|edge| {
                    let left = self.labels[edge.u()];
                    let right = self.labels[edge.v()];
                    (left != right).then_some((left, right, edge.weight()))
                })
                .collect()
        });
        Laplacian::from_edges_with_executor(self.coarse_dimension(), coarse_edges, executor)
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

fn validate_prolong_dimensions(
    aggregation: &Aggregation,
    coarse: &[f64],
    fine: &[f64],
) -> Result<(), CmgError> {
    if coarse.len() != aggregation.coarse_dimension() {
        return Err(CmgError::dimension(
            "Aggregation::prolong coarse",
            aggregation.coarse_dimension(),
            coarse.len(),
        ));
    }
    if fine.len() != aggregation.fine_dimension() {
        return Err(CmgError::dimension(
            "Aggregation::prolong fine",
            aggregation.fine_dimension(),
            fine.len(),
        ));
    }
    Ok(())
}
