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
        for (row, values) in lower.iter_mut().enumerate() {
            values[row] = 1.0;
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
        let mut solution = vec![0.0; self.vertex_count];
        let mut forward = vec![0.0; self.active_dimension()];
        let mut factor_solution = vec![0.0; self.active_dimension()];
        self.solve_into_compatible(
            rhs,
            &mut solution,
            &mut forward,
            &mut factor_solution,
        )?;
        Ok(solution)
    }

    pub(crate) fn solve_into_compatible(
        &self,
        rhs: &[f64],
        solution: &mut [f64],
        forward: &mut [f64],
        factor_solution: &mut [f64],
    ) -> Result<(), CmgError> {
        if rhs.len() != self.vertex_count {
            return Err(CmgError::dimension(
                "GroundedLdl::solve_into rhs",
                self.vertex_count,
                rhs.len(),
            ));
        }
        if solution.len() != self.vertex_count {
            return Err(CmgError::dimension(
                "GroundedLdl::solve_into solution",
                self.vertex_count,
                solution.len(),
            ));
        }
        let dimension = self.active_dimension();
        if forward.len() != dimension {
            return Err(CmgError::dimension(
                "GroundedLdl::solve_into forward",
                dimension,
                forward.len(),
            ));
        }
        if factor_solution.len() != dimension {
            return Err(CmgError::dimension(
                "GroundedLdl::solve_into factor solution",
                dimension,
                factor_solution.len(),
            ));
        }

        for row in 0..dimension {
            let correction: f64 = self.lower[row][..row]
                .iter()
                .zip(&forward[..row])
                .map(|(lower_value, previous)| lower_value * previous)
                .sum();
            forward[row] = rhs[self.permutation[row]] - correction;
        }
        for (value, pivot) in forward.iter_mut().zip(&self.diagonal) {
            *value /= *pivot;
        }
        for row in (0..dimension).rev() {
            let correction: f64 = self.lower[(row + 1)..]
                .iter()
                .zip(&factor_solution[(row + 1)..])
                .map(|(lower_row, later_solution)| lower_row[row] * later_solution)
                .sum();
            factor_solution[row] = forward[row] - correction;
        }

        solution.fill(0.0);
        for (factor_index, &vertex) in self.permutation.iter().enumerate() {
            solution[vertex] = factor_solution[factor_index];
        }
        Ok(())
    }
}
