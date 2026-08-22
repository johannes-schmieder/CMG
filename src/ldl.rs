//! Component-grounded, static-degree-ordered LDL^T terminal solver.

use crate::{CmgError, Components, Laplacian, ValidationOptions};

/// A deterministic direct solver for a graph Laplacian on its quotient space.
///
/// One highest-index vertex is grounded in each connected component. The
/// remaining grounded matrix is ordered by static row nonzero count and then by
/// original vertex index, matching the simple degree-ordering principle used by
/// the upstream terminal factorization.
#[derive(Debug, Clone, PartialEq)]
pub struct GroundedLdl {
    vertex_count: usize,
    components: Components,
    anchors: Vec<usize>,
    permutation: Vec<usize>,
    lower: Vec<Vec<f64>>,
    diagonal: Vec<f64>,
    factor_nonzeros: usize,
}

impl GroundedLdl {
    /// Factor the graph Laplacian after grounding one vertex per component.
    pub fn factor(graph: &Laplacian) -> Result<Self, CmgError> {
        let vertex_count = graph.vertex_count();
        let components = Components::from_laplacian(graph);

        let mut anchors = vec![0; components.count()];
        for (vertex, &component) in components.labels().iter().enumerate() {
            anchors[component] = vertex;
        }
        let mut is_anchor = vec![false; vertex_count];
        for &anchor in &anchors {
            is_anchor[anchor] = true;
        }

        let dense = graph.to_dense();
        let active_vertices: Vec<usize> = (0..vertex_count)
            .filter(|vertex| !is_anchor[*vertex])
            .collect();
        let mut pattern_nonzeros = vec![0_usize; vertex_count];
        for &row in &active_vertices {
            pattern_nonzeros[row] = active_vertices
                .iter()
                .filter(|&&column| dense[row][column] != 0.0)
                .count();
        }

        let mut permutation = active_vertices;
        permutation.sort_by_key(|&vertex| (pattern_nonzeros[vertex], vertex));
        let dimension = permutation.len();

        let mut matrix = vec![vec![0.0; dimension]; dimension];
        for (row, &original_row) in permutation.iter().enumerate() {
            for (column, &original_column) in permutation.iter().enumerate() {
                matrix[row][column] = dense[original_row][original_column];
            }
        }

        let mut lower = vec![vec![0.0; dimension]; dimension];
        let mut diagonal = vec![0.0; dimension];
        for row in 0..dimension {
            lower[row][row] = 1.0;
        }

        for column in 0..dimension {
            let mut pivot = matrix[column][column];
            for previous in 0..column {
                let value = lower[column][previous];
                pivot -= value * value * diagonal[previous];
            }
            if !pivot.is_finite() || pivot <= 0.0 {
                return Err(CmgError::NonPositivePivot {
                    vertex: permutation[column],
                    value: pivot,
                });
            }
            diagonal[column] = pivot;

            for row in (column + 1)..dimension {
                let mut value = matrix[row][column];
                for previous in 0..column {
                    value -= lower[row][previous] * lower[column][previous] * diagonal[previous];
                }
                lower[row][column] = value / pivot;
            }
        }

        let factor_nonzeros = lower
            .iter()
            .enumerate()
            .map(|(row, values)| values[..=row].iter().filter(|value| **value != 0.0).count())
            .sum();

        Ok(Self {
            vertex_count,
            components,
            anchors,
            permutation,
            lower,
            diagonal,
            factor_nonzeros,
        })
    }

    /// Return the original graph dimension.
    #[must_use]
    pub const fn vertex_count(&self) -> usize {
        self.vertex_count
    }

    /// Return the grounded vertex in each component.
    #[must_use]
    pub fn anchors(&self) -> &[usize] {
        &self.anchors
    }

    /// Return factor-order positions as original graph vertex indices.
    #[must_use]
    pub fn permutation(&self) -> &[usize] {
        &self.permutation
    }

    /// Return the dimension of the grounded positive-definite system.
    #[must_use]
    pub fn active_dimension(&self) -> usize {
        self.permutation.len()
    }

    /// Return the number of nonzeros in the unit lower factor.
    ///
    /// This is the denominator used by the upstream repeat heuristic before a
    /// direct terminal level.
    #[must_use]
    pub const fn factor_nonzeros(&self) -> usize {
        self.factor_nonzeros
    }

    /// Solve a compatible Laplacian system using default validation tolerances.
    pub fn solve(&self, rhs: &[f64]) -> Result<Vec<f64>, CmgError> {
        self.solve_with_validation(rhs, ValidationOptions::default())
    }

    /// Solve a compatible Laplacian system with explicit validation tolerances.
    ///
    /// The returned gauge sets every component anchor to zero.
    pub fn solve_with_validation(
        &self,
        rhs: &[f64],
        options: ValidationOptions,
    ) -> Result<Vec<f64>, CmgError> {
        if rhs.len() != self.vertex_count {
            return Err(CmgError::dimension(
                "GroundedLdl::solve",
                self.vertex_count,
                rhs.len(),
            ));
        }
        self.components.validate_rhs(rhs, options)?;

        let dimension = self.active_dimension();
        let mut forward = vec![0.0; dimension];
        for row in 0..dimension {
            let mut value = rhs[self.permutation[row]];
            for column in 0..row {
                value -= self.lower[row][column] * forward[column];
            }
            forward[row] = value;
        }

        for (value, pivot) in forward.iter_mut().zip(&self.diagonal) {
            *value /= *pivot;
        }

        let mut factor_solution = vec![0.0; dimension];
        for row in (0..dimension).rev() {
            let mut value = forward[row];
            for column in (row + 1)..dimension {
                value -= self.lower[column][row] * factor_solution[column];
            }
            factor_solution[row] = value;
        }

        let mut solution = vec![0.0; self.vertex_count];
        for (factor_index, &vertex) in self.permutation.iter().enumerate() {
            solution[vertex] = factor_solution[factor_index];
        }
        Ok(solution)
    }
}
