//! Certified quotient-space preconditioned conjugate gradients.

use crate::graph::compensated_sum;
use crate::{CmgError, CmgPreconditioner, CmgWorkspace, Components, Laplacian, PcgOptions};

/// Reusable vectors for repeated PCG solves with one preconditioner.
#[derive(Debug, Clone)]
pub struct PcgWorkspace {
    solution: Vec<f64>,
    residual: Vec<f64>,
    preconditioned: Vec<f64>,
    direction: Vec<f64>,
    matrix_direction: Vec<f64>,
    fresh_residual: Vec<f64>,
    cmg: CmgWorkspace,
}

impl PcgWorkspace {
    /// Allocate a solver workspace for a fixed preconditioner.
    #[must_use]
    pub fn new(preconditioner: &CmgPreconditioner) -> Self {
        let dimension = preconditioner.hierarchy().levels()[0]
            .graph()
            .vertex_count();
        Self {
            solution: vec![0.0; dimension],
            residual: vec![0.0; dimension],
            preconditioned: vec![0.0; dimension],
            direction: vec![0.0; dimension],
            matrix_direction: vec![0.0; dimension],
            fresh_residual: vec![0.0; dimension],
            cmg: preconditioner.workspace(),
        }
    }

    /// Return the system dimension.
    #[must_use]
    pub fn dimension(&self) -> usize {
        self.solution.len()
    }

    fn validate(&self, dimension: usize) -> Result<(), CmgError> {
        for (context, actual) in [
            ("PcgWorkspace solution", self.solution.len()),
            ("PcgWorkspace residual", self.residual.len()),
            ("PcgWorkspace preconditioned", self.preconditioned.len()),
            ("PcgWorkspace direction", self.direction.len()),
            ("PcgWorkspace matrix direction", self.matrix_direction.len()),
            ("PcgWorkspace fresh residual", self.fresh_residual.len()),
        ] {
            if actual != dimension {
                return Err(CmgError::dimension(context, dimension, actual));
            }
        }
        Ok(())
    }
}

/// A successfully certified PCG solution and its diagnostics.
#[derive(Debug, Clone, PartialEq)]
pub struct PcgResult {
    solution: Vec<f64>,
    iterations: usize,
    initial_residual_norm: f64,
    residual_norm: f64,
    relative_residual: f64,
    backward_error: f64,
    tolerance: f64,
    restarts: usize,
}

impl PcgResult {
    /// Return the certified solution.
    #[must_use]
    pub fn solution(&self) -> &[f64] {
        &self.solution
    }

    /// Consume the result and return its solution vector.
    #[must_use]
    pub fn into_solution(self) -> Vec<f64> {
        self.solution
    }

    /// Return completed PCG iterations.
    #[must_use]
    pub const fn iterations(&self) -> usize {
        self.iterations
    }

    /// Return the initial Euclidean residual norm.
    #[must_use]
    pub const fn initial_residual_norm(&self) -> f64 {
        self.initial_residual_norm
    }

    /// Return the freshly recomputed original-system residual norm.
    #[must_use]
    pub const fn residual_norm(&self) -> f64 {
        self.residual_norm
    }

    /// Return `||r|| / ||b||`, with zero denominator handled explicitly.
    #[must_use]
    pub const fn relative_residual(&self) -> f64 {
        self.relative_residual
    }

    /// Return `||r|| / (||b|| + ||A||_bound ||x||)`.
    #[must_use]
    pub const fn backward_error(&self) -> f64 {
        self.backward_error
    }

    /// Return the absolute residual threshold used for certification.
    #[must_use]
    pub const fn tolerance(&self) -> f64 {
        self.tolerance
    }

    /// Return the number of explicit residual-replacement restarts.
    #[must_use]
    pub const fn restarts(&self) -> usize {
        self.restarts
    }
}

/// Solve a compatible graph-Laplacian system with a newly allocated workspace.
pub fn solve_pcg(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    rhs: &[f64],
    options: PcgOptions,
) -> Result<PcgResult, CmgError> {
    let mut workspace = PcgWorkspace::new(preconditioner);
    solve_pcg_with_workspace(graph, preconditioner, rhs, options, &mut workspace)
}

/// Solve using caller-owned workspace suitable for repeated right-hand sides.
pub fn solve_pcg_with_workspace(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    rhs: &[f64],
    options: PcgOptions,
    workspace: &mut PcgWorkspace,
) -> Result<PcgResult, CmgError> {
    let options = options.validate()?;
    let dimension = graph.vertex_count();
    if preconditioner.hierarchy().levels()[0].graph() != graph {
        return Err(CmgError::InvalidHierarchy {
            context: "PCG graph differs from the preconditioner's finest graph",
        });
    }
    if rhs.len() != dimension {
        return Err(CmgError::dimension("solve_pcg rhs", dimension, rhs.len()));
    }
    workspace.validate(dimension)?;

    let components = Components::from_laplacian(graph);
    components.validate_rhs(rhs, options.validation)?;
    workspace.solution.fill(0.0);
    workspace.residual.copy_from_slice(rhs);
    workspace.preconditioned.fill(0.0);
    workspace.direction.fill(0.0);
    workspace.matrix_direction.fill(0.0);
    workspace.fresh_residual.fill(0.0);

    let initial_residual_norm = euclidean_norm(rhs);
    let operator_bound = graph.operator_norm_bound();
    let initial_tolerance = allowed_residual(options, initial_residual_norm, operator_bound, 0.0);
    if initial_residual_norm <= initial_tolerance {
        return Ok(make_result(
            workspace.solution.clone(),
            0,
            initial_residual_norm,
            initial_residual_norm,
            initial_tolerance,
            operator_bound,
            0,
        ));
    }

    preconditioner.apply_into_with_validation(
        &workspace.residual,
        &mut workspace.preconditioned,
        &mut workspace.cmg,
        options.validation,
    )?;
    components.center_in_place(&mut workspace.preconditioned)?;
    let mut rho = dot(&workspace.residual, &workspace.preconditioned);
    validate_positive_pcg(0, "r^T M r", rho)?;
    workspace
        .direction
        .copy_from_slice(&workspace.preconditioned);

    let mut restarts = 0_usize;
    let mut last_tolerance = initial_tolerance;

    for iteration in 1..=options.max_iterations {
        graph.matvec_into(&workspace.direction, &mut workspace.matrix_direction)?;
        let direction_curvature = dot(&workspace.direction, &workspace.matrix_direction);
        validate_positive_pcg(iteration, "p^T A p", direction_curvature)?;
        let alpha = rho / direction_curvature;
        validate_finite_pcg(iteration, "alpha", alpha)?;

        for (((solution, residual), direction), matrix_direction) in workspace
            .solution
            .iter_mut()
            .zip(&mut workspace.residual)
            .zip(&workspace.direction)
            .zip(&workspace.matrix_direction)
        {
            *solution += alpha * *direction;
            *residual -= alpha * *matrix_direction;
        }
        components.center_in_place(&mut workspace.solution)?;

        let solution_norm = euclidean_norm(&workspace.solution);
        last_tolerance = allowed_residual(
            options,
            initial_residual_norm,
            operator_bound,
            solution_norm,
        );
        let recursive_residual_norm = euclidean_norm(&workspace.residual);
        let candidate = recursive_residual_norm <= last_tolerance;
        let scheduled_recompute = iteration % options.residual_recompute_interval == 0;
        let mut restarted = false;

        if candidate || scheduled_recompute {
            let fresh_norm = recompute_residual(
                graph,
                rhs,
                &workspace.solution,
                &mut workspace.fresh_residual,
            )?;
            workspace
                .residual
                .copy_from_slice(&workspace.fresh_residual);
            restarted = true;
            restarts += 1;
            if fresh_norm <= last_tolerance {
                return Ok(make_result(
                    workspace.solution.clone(),
                    iteration,
                    initial_residual_norm,
                    fresh_norm,
                    last_tolerance,
                    operator_bound,
                    restarts,
                ));
            }
            if candidate && iteration == options.max_iterations {
                return Err(CmgError::ResidualVerificationFailed {
                    iteration,
                    residual_norm: fresh_norm,
                    tolerance: last_tolerance,
                });
            }
        }

        if iteration == options.max_iterations {
            break;
        }

        preconditioner.apply_into_with_validation(
            &workspace.residual,
            &mut workspace.preconditioned,
            &mut workspace.cmg,
            options.validation,
        )?;
        components.center_in_place(&mut workspace.preconditioned)?;
        let new_rho = dot(&workspace.residual, &workspace.preconditioned);
        validate_positive_pcg(iteration, "new r^T M r", new_rho)?;

        if restarted {
            workspace
                .direction
                .copy_from_slice(&workspace.preconditioned);
        } else {
            let beta = new_rho / rho;
            validate_finite_pcg(iteration, "beta", beta)?;
            for (direction, preconditioned) in workspace
                .direction
                .iter_mut()
                .zip(&workspace.preconditioned)
            {
                *direction = *preconditioned + beta * *direction;
            }
        }
        rho = new_rho;
    }

    let residual_norm = recompute_residual(
        graph,
        rhs,
        &workspace.solution,
        &mut workspace.fresh_residual,
    )?;
    Err(CmgError::MaximumIterations {
        iterations: options.max_iterations,
        residual_norm,
        tolerance: last_tolerance,
    })
}

/// Solve multiple right-hand sides sequentially while reusing all work arrays.
pub fn solve_pcg_batch(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    right_hand_sides: &[Vec<f64>],
    options: PcgOptions,
) -> Result<Vec<PcgResult>, CmgError> {
    let mut workspace = PcgWorkspace::new(preconditioner);
    right_hand_sides
        .iter()
        .map(|rhs| solve_pcg_with_workspace(graph, preconditioner, rhs, options, &mut workspace))
        .collect()
}

fn make_result(
    solution: Vec<f64>,
    iterations: usize,
    initial_residual_norm: f64,
    residual_norm: f64,
    tolerance: f64,
    operator_bound: f64,
    restarts: usize,
) -> PcgResult {
    let solution_norm = euclidean_norm(&solution);
    let denominator = initial_residual_norm + operator_bound * solution_norm;
    let relative_residual = if initial_residual_norm > 0.0 {
        residual_norm / initial_residual_norm
    } else {
        residual_norm
    };
    let backward_error = if denominator > 0.0 {
        residual_norm / denominator
    } else {
        0.0
    };
    PcgResult {
        solution,
        iterations,
        initial_residual_norm,
        residual_norm,
        relative_residual,
        backward_error,
        tolerance,
        restarts,
    }
}

fn allowed_residual(
    options: PcgOptions,
    rhs_norm: f64,
    operator_bound: f64,
    solution_norm: f64,
) -> f64 {
    options.absolute_tolerance
        + options.relative_tolerance * (rhs_norm + operator_bound * solution_norm)
}

fn recompute_residual(
    graph: &Laplacian,
    rhs: &[f64],
    solution: &[f64],
    residual: &mut [f64],
) -> Result<f64, CmgError> {
    graph.matvec_into(solution, residual)?;
    for (value, rhs_value) in residual.iter_mut().zip(rhs) {
        *value = *rhs_value - *value;
    }
    Ok(euclidean_norm(residual))
}

fn dot(left: &[f64], right: &[f64]) -> f64 {
    compensated_sum(left.iter().zip(right).map(|(x, y)| x * y))
}

fn euclidean_norm(values: &[f64]) -> f64 {
    let scale = values.iter().map(|value| value.abs()).fold(0.0, f64::max);
    if scale == 0.0 {
        0.0
    } else {
        scale
            * compensated_sum(values.iter().map(|value| {
                let scaled = *value / scale;
                scaled * scaled
            }))
            .sqrt()
    }
}

fn validate_positive_pcg(
    iteration: usize,
    quantity: &'static str,
    value: f64,
) -> Result<(), CmgError> {
    if value.is_finite() && value > 0.0 {
        Ok(())
    } else {
        Err(CmgError::PcgBreakdown {
            iteration,
            quantity,
            value,
        })
    }
}

fn validate_finite_pcg(
    iteration: usize,
    quantity: &'static str,
    value: f64,
) -> Result<(), CmgError> {
    if value.is_finite() {
        Ok(())
    } else {
        Err(CmgError::PcgBreakdown {
            iteration,
            quantity,
            value,
        })
    }
}
