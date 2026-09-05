//! Certified quotient-space preconditioned conjugate gradients.

use crate::components::ComponentWorkspace;
use crate::graph::compensated_sum;
use crate::{CmgError, CmgPreconditioner, CmgWorkspace, Laplacian, PcgOptions};
#[cfg(feature = "parallel")]
use crate::{ParallelCmgPlan, ParallelExecutor, ParallelOptions};
#[cfg(feature = "parallel")]
use rayon::prelude::*;
#[cfg(feature = "profiling")]
use std::time::Instant;

/// Experimental fixed-width independent-RHS solver.
#[cfg(feature = "experimental-fused-rhs")]
#[doc(hidden)]
pub mod experimental;

/// Reusable vectors for repeated PCG solves with one preconditioner.
#[derive(Debug, Clone)]
pub struct PcgWorkspace {
    projected_rhs: Vec<f64>,
    solution: Vec<f64>,
    residual: Vec<f64>,
    preconditioned: Vec<f64>,
    direction: Vec<f64>,
    matrix_direction: Vec<f64>,
    component: ComponentWorkspace,
    cmg: CmgWorkspace,
}

/// Solution-free diagnostics returned by caller-buffer PCG entry points.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct PcgDiagnostics {
    iterations: usize,
    initial_residual_norm: f64,
    residual_norm: f64,
    relative_residual: f64,
    backward_error: f64,
    tolerance: f64,
    restarts: usize,
    rhs_projection_norm: f64,
}

/// Untimed-use batch phase attribution for caller-buffer solves.
#[cfg(feature = "profiling")]
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct PcgBatchPhaseProfile {
    validation_nanoseconds: u128,
    gather_nanoseconds: u128,
    solve_nanoseconds: u128,
    scatter_nanoseconds: u128,
    result_construction_nanoseconds: u128,
    total_nanoseconds: u128,
}

#[cfg(feature = "profiling")]
impl PcgBatchPhaseProfile {
    /// Return batch view, dimension, compatibility, and workspace validation time.
    #[must_use]
    pub const fn validation_nanoseconds(self) -> u128 {
        self.validation_nanoseconds
    }

    /// Return time spent gathering noncontiguous RHS and guesses.
    #[must_use]
    pub const fn gather_nanoseconds(self) -> u128 {
        self.gather_nanoseconds
    }

    /// Return time spent in the shared certified numerical core.
    #[must_use]
    pub const fn solve_nanoseconds(self) -> u128 {
        self.solve_nanoseconds
    }

    /// Return time spent scattering solutions to caller layouts.
    #[must_use]
    pub const fn scatter_nanoseconds(self) -> u128 {
        self.scatter_nanoseconds
    }

    /// Return owned-result construction time, zero for this caller-buffer path.
    #[must_use]
    pub const fn result_construction_nanoseconds(self) -> u128 {
        self.result_construction_nanoseconds
    }

    /// Return complete profiled call wall time.
    #[must_use]
    pub const fn total_nanoseconds(self) -> u128 {
        self.total_nanoseconds
    }
}

/// Reusable single-RHS PCG workspace plus strided batch staging buffers.
#[derive(Debug, Clone)]
pub struct PcgBatchWorkspace {
    pcg: PcgWorkspace,
    rhs: Vec<f64>,
    guess: Vec<f64>,
    #[cfg(feature = "parallel")]
    outcome: Option<Result<PcgDiagnostics, CmgError>>,
}

impl PcgBatchWorkspace {
    /// Allocate a workspace for contiguous or strided batch solves.
    pub fn new(preconditioner: &CmgPreconditioner) -> Result<Self, CmgError> {
        let dimension = preconditioner.hierarchy().levels()[0]
            .graph()
            .vertex_count();
        let mut rhs = Vec::new();
        rhs.try_reserve_exact(dimension)
            .map_err(|_| CmgError::AllocationFailed {
                context: "PCG batch RHS staging",
            })?;
        rhs.resize(dimension, 0.0);
        let mut guess = Vec::new();
        guess
            .try_reserve_exact(dimension)
            .map_err(|_| CmgError::AllocationFailed {
                context: "PCG batch guess staging",
            })?;
        guess.resize(dimension, 0.0);
        Ok(Self {
            pcg: PcgWorkspace::try_new(preconditioner)?,
            rhs,
            guess,
            #[cfg(feature = "parallel")]
            outcome: None,
        })
    }

    /// Return the system dimension.
    #[must_use]
    pub fn dimension(&self) -> usize {
        self.pcg.dimension()
    }

    /// Return principal retained bytes for numerical and staging arrays.
    #[must_use]
    pub fn byte_len(&self) -> usize {
        self.pcg
            .byte_len()
            .saturating_add(self.rhs.capacity().saturating_mul(8))
            .saturating_add(self.guess.capacity().saturating_mul(8))
    }

    pub(crate) fn validate(&self, preconditioner: &CmgPreconditioner) -> Result<(), CmgError> {
        self.pcg.validate(preconditioner)?;
        for (context, actual) in [
            ("PcgBatchWorkspace RHS staging", self.rhs.len()),
            ("PcgBatchWorkspace guess staging", self.guess.len()),
        ] {
            if actual != self.pcg.dimension() {
                return Err(CmgError::dimension(context, self.pcg.dimension(), actual));
            }
        }
        Ok(())
    }
}

impl PcgDiagnostics {
    /// Return completed PCG iterations.
    #[must_use]
    pub const fn iterations(self) -> usize {
        self.iterations
    }

    /// Return the Euclidean residual norm at initialization.
    #[must_use]
    pub const fn initial_residual_norm(self) -> f64 {
        self.initial_residual_norm
    }

    /// Return the freshly recomputed residual norm against the submitted RHS.
    #[must_use]
    pub const fn residual_norm(self) -> f64 {
        self.residual_norm
    }

    /// Return `||r|| / ||b||`, with zero denominator handled explicitly.
    #[must_use]
    pub const fn relative_residual(self) -> f64 {
        self.relative_residual
    }

    /// Return `||r|| / (||b|| + ||A||_bound ||x||)`.
    #[must_use]
    pub const fn backward_error(self) -> f64 {
        self.backward_error
    }

    /// Return the absolute residual threshold used for certification.
    #[must_use]
    pub const fn tolerance(self) -> f64 {
        self.tolerance
    }

    /// Return the number of explicit residual-replacement restarts.
    #[must_use]
    pub const fn restarts(self) -> usize {
        self.restarts
    }

    /// Return the norm of component-nullspace roundoff removed from the RHS.
    #[must_use]
    pub const fn rhs_projection_norm(self) -> f64 {
        self.rhs_projection_norm
    }
}

/// Borrowed read-only batch storage with explicit RHS and value strides.
#[derive(Debug, Clone, Copy)]
pub struct PcgBatchRef<'a> {
    data: &'a [f64],
    rhs_count: usize,
    dimension: usize,
    rhs_stride: usize,
    value_stride: usize,
}

impl<'a> PcgBatchRef<'a> {
    /// Borrow a tightly packed RHS-major batch.
    pub fn contiguous(
        data: &'a [f64],
        rhs_count: usize,
        dimension: usize,
    ) -> Result<Self, CmgError> {
        let expected = rhs_count
            .checked_mul(dimension)
            .ok_or(CmgError::InvalidHierarchy {
                context: "PCG batch dimensions overflow",
            })?;
        if data.len() != expected {
            return Err(CmgError::dimension(
                "PcgBatchRef contiguous storage",
                expected,
                data.len(),
            ));
        }
        Self::strided(data, rhs_count, dimension, dimension.max(1), 1)
    }

    /// Borrow a batch whose logical value `(rhs, vertex)` is stored at
    /// `rhs * rhs_stride + vertex * value_stride`.
    pub fn strided(
        data: &'a [f64],
        rhs_count: usize,
        dimension: usize,
        rhs_stride: usize,
        value_stride: usize,
    ) -> Result<Self, CmgError> {
        let required = batch_required_len(rhs_count, dimension, rhs_stride, value_stride)?;
        if data.len() < required {
            return Err(CmgError::dimension(
                "PcgBatchRef strided storage",
                required,
                data.len(),
            ));
        }
        Ok(Self {
            data,
            rhs_count,
            dimension,
            rhs_stride,
            value_stride,
        })
    }

    /// Return the number of logical right-hand sides.
    #[must_use]
    pub const fn rhs_count(self) -> usize {
        self.rhs_count
    }

    /// Return the logical vector dimension.
    #[must_use]
    pub const fn dimension(self) -> usize {
        self.dimension
    }

    /// Return the storage stride between adjacent logical right-hand sides.
    #[must_use]
    pub const fn rhs_stride(self) -> usize {
        self.rhs_stride
    }

    /// Return the storage stride between adjacent vertices within one RHS.
    #[must_use]
    pub const fn value_stride(self) -> usize {
        self.value_stride
    }

    fn copy_rhs_into(self, rhs: usize, output: &mut [f64]) {
        debug_assert!(rhs < self.rhs_count);
        debug_assert_eq!(output.len(), self.dimension);
        for (vertex, value) in output.iter_mut().enumerate() {
            *value = self.data[rhs * self.rhs_stride + vertex * self.value_stride];
        }
    }

    fn contiguous_rhs(self, rhs: usize) -> Option<&'a [f64]> {
        if self.value_stride != 1 {
            return None;
        }
        if self.dimension == 0 {
            return Some(&self.data[0..0]);
        }
        let start = rhs * self.rhs_stride;
        Some(&self.data[start..start + self.dimension])
    }
}

/// Borrowed writable batch storage with checked, nonoverlapping strides.
#[derive(Debug)]
pub struct PcgBatchMut<'a> {
    data: &'a mut [f64],
    rhs_count: usize,
    dimension: usize,
    rhs_stride: usize,
    value_stride: usize,
}

impl<'a> PcgBatchMut<'a> {
    /// Borrow a tightly packed RHS-major output batch.
    pub fn contiguous(
        data: &'a mut [f64],
        rhs_count: usize,
        dimension: usize,
    ) -> Result<Self, CmgError> {
        let expected = rhs_count
            .checked_mul(dimension)
            .ok_or(CmgError::InvalidHierarchy {
                context: "PCG batch dimensions overflow",
            })?;
        if data.len() != expected {
            return Err(CmgError::dimension(
                "PcgBatchMut contiguous storage",
                expected,
                data.len(),
            ));
        }
        Self::strided(data, rhs_count, dimension, dimension.max(1), 1)
    }

    /// Borrow a batch whose logical value `(rhs, vertex)` is stored at
    /// `rhs * rhs_stride + vertex * value_stride`.
    ///
    /// The layout must be provably nonoverlapping in RHS-major or
    /// vertex-major order.
    pub fn strided(
        data: &'a mut [f64],
        rhs_count: usize,
        dimension: usize,
        rhs_stride: usize,
        value_stride: usize,
    ) -> Result<Self, CmgError> {
        let required = batch_required_len(rhs_count, dimension, rhs_stride, value_stride)?;
        if data.len() < required {
            return Err(CmgError::dimension(
                "PcgBatchMut strided storage",
                required,
                data.len(),
            ));
        }
        let rhs_span = dimension
            .saturating_sub(1)
            .checked_mul(value_stride)
            .and_then(|value| value.checked_add(1))
            .ok_or(CmgError::InvalidHierarchy {
                context: "PCG batch RHS span overflows",
            })?;
        let vertex_span = rhs_count
            .saturating_sub(1)
            .checked_mul(rhs_stride)
            .and_then(|value| value.checked_add(1))
            .ok_or(CmgError::InvalidHierarchy {
                context: "PCG batch vertex span overflows",
            })?;
        if rhs_count > 1 && dimension > 1 && rhs_stride < rhs_span && value_stride < vertex_span {
            return Err(CmgError::InvalidHierarchy {
                context: "mutable PCG batch layout may overlap",
            });
        }
        Ok(Self {
            data,
            rhs_count,
            dimension,
            rhs_stride,
            value_stride,
        })
    }

    /// Return the number of logical output vectors.
    #[must_use]
    pub const fn rhs_count(&self) -> usize {
        self.rhs_count
    }

    /// Return the logical vector dimension.
    #[must_use]
    pub const fn dimension(&self) -> usize {
        self.dimension
    }

    /// Return the storage stride between adjacent logical output vectors.
    #[must_use]
    pub const fn rhs_stride(&self) -> usize {
        self.rhs_stride
    }

    /// Return the storage stride between adjacent vertices within one output.
    #[must_use]
    pub const fn value_stride(&self) -> usize {
        self.value_stride
    }

    fn copy_rhs_from(&mut self, rhs: usize, input: &[f64]) {
        debug_assert!(rhs < self.rhs_count);
        debug_assert_eq!(input.len(), self.dimension);
        for (vertex, value) in input.iter().enumerate() {
            self.data[rhs * self.rhs_stride + vertex * self.value_stride] = *value;
        }
    }
}

fn batch_required_len(
    rhs_count: usize,
    dimension: usize,
    rhs_stride: usize,
    value_stride: usize,
) -> Result<usize, CmgError> {
    if rhs_count == 0 || dimension == 0 {
        return Ok(0);
    }
    if rhs_stride == 0 || value_stride == 0 {
        return Err(CmgError::InvalidHierarchy {
            context: "nonempty PCG batch strides must be positive",
        });
    }
    let rhs_offset = (rhs_count - 1)
        .checked_mul(rhs_stride)
        .ok_or(CmgError::InvalidHierarchy {
            context: "PCG batch RHS offset overflows",
        })?;
    let value_offset =
        (dimension - 1)
            .checked_mul(value_stride)
            .ok_or(CmgError::InvalidHierarchy {
                context: "PCG batch value offset overflows",
            })?;
    rhs_offset
        .checked_add(value_offset)
        .and_then(|value| value.checked_add(1))
        .ok_or(CmgError::InvalidHierarchy {
            context: "PCG batch storage span overflows",
        })
}

fn try_zeroed(len: usize, context: &'static str) -> Result<Vec<f64>, CmgError> {
    let mut values = Vec::new();
    values
        .try_reserve_exact(len)
        .map_err(|_| CmgError::AllocationFailed { context })?;
    values.resize(len, 0.0);
    Ok(values)
}

impl PcgWorkspace {
    #[cfg(any(feature = "parallel", feature = "experimental-fused-rhs"))]
    pub(crate) fn required_bytes(preconditioner: &CmgPreconditioner) -> usize {
        let dimension = preconditioner.hierarchy().levels()[0]
            .graph()
            .vertex_count();
        dimension
            .saturating_mul(core::mem::size_of::<f64>())
            .saturating_mul(6)
            .saturating_add(preconditioner.finest_components().workspace_bytes())
            .saturating_add(preconditioner.workspace_bytes())
    }

    /// Allocate a solver workspace for a fixed preconditioner.
    #[must_use]
    pub fn new(preconditioner: &CmgPreconditioner) -> Self {
        let dimension = preconditioner.hierarchy().levels()[0]
            .graph()
            .vertex_count();
        Self {
            projected_rhs: vec![0.0; dimension],
            solution: vec![0.0; dimension],
            residual: vec![0.0; dimension],
            preconditioned: vec![0.0; dimension],
            direction: vec![0.0; dimension],
            matrix_direction: vec![0.0; dimension],
            component: preconditioner.finest_components().workspace(),
            cmg: preconditioner.workspace(),
        }
    }

    /// Fallibly allocate a solver workspace for a fixed preconditioner.
    pub fn try_new(preconditioner: &CmgPreconditioner) -> Result<Self, CmgError> {
        let dimension = preconditioner.hierarchy().levels()[0]
            .graph()
            .vertex_count();
        Ok(Self {
            projected_rhs: try_zeroed(dimension, "PCG projected RHS")?,
            solution: try_zeroed(dimension, "PCG solution")?,
            residual: try_zeroed(dimension, "PCG residual")?,
            preconditioned: try_zeroed(dimension, "PCG preconditioned vector")?,
            direction: try_zeroed(dimension, "PCG direction")?,
            matrix_direction: try_zeroed(dimension, "PCG matrix direction")?,
            component: preconditioner.finest_components().try_workspace()?,
            cmg: preconditioner.try_workspace()?,
        })
    }

    /// Return the system dimension.
    #[must_use]
    pub fn dimension(&self) -> usize {
        self.solution.len()
    }

    /// Return the number of heap bytes reserved by the principal work arrays.
    #[must_use]
    pub fn byte_len(&self) -> usize {
        self.dimension()
            .saturating_mul(8)
            .saturating_mul(6)
            .saturating_add(self.component.byte_len())
            .saturating_add(self.cmg.byte_len())
    }

    fn validate(&self, preconditioner: &CmgPreconditioner) -> Result<(), CmgError> {
        let dimension = preconditioner.hierarchy().levels()[0]
            .graph()
            .vertex_count();
        for (context, actual) in [
            ("PcgWorkspace projected rhs", self.projected_rhs.len()),
            ("PcgWorkspace solution", self.solution.len()),
            ("PcgWorkspace residual", self.residual.len()),
            ("PcgWorkspace preconditioned", self.preconditioned.len()),
            ("PcgWorkspace direction", self.direction.len()),
            ("PcgWorkspace matrix direction", self.matrix_direction.len()),
        ] {
            if actual != dimension {
                return Err(CmgError::dimension(context, dimension, actual));
            }
        }
        preconditioner
            .finest_components()
            .validate_workspace(&self.component)?;
        preconditioner.validate_workspace(&self.cmg)
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
    rhs_projection_norm: f64,
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

    /// Return the initial Euclidean residual norm for the submitted RHS.
    #[must_use]
    pub const fn initial_residual_norm(&self) -> f64 {
        self.initial_residual_norm
    }

    /// Return the freshly recomputed residual norm against the submitted RHS.
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

    /// Return the norm of accepted component-nullspace roundoff removed from
    /// the submitted RHS before iteration.
    #[must_use]
    pub const fn rhs_projection_norm(&self) -> f64 {
        self.rhs_projection_norm
    }

    /// Return solution-free diagnostics for this owned result.
    #[must_use]
    pub const fn diagnostics(&self) -> PcgDiagnostics {
        PcgDiagnostics {
            iterations: self.iterations,
            initial_residual_norm: self.initial_residual_norm,
            residual_norm: self.residual_norm,
            relative_residual: self.relative_residual,
            backward_error: self.backward_error,
            tolerance: self.tolerance,
            restarts: self.restarts,
            rhs_projection_norm: self.rhs_projection_norm,
        }
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
///
/// Component-sum defects accepted by `compatibility_tolerance` are projected
/// onto the exact Laplacian range. Final certification is still performed
/// against the submitted, unprojected RHS and the projection norm is reported.
pub fn solve_pcg_with_workspace(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    rhs: &[f64],
    options: PcgOptions,
    workspace: &mut PcgWorkspace,
) -> Result<PcgResult, CmgError> {
    let diagnostics = solve_pcg_core(
        graph,
        preconditioner,
        rhs,
        None,
        options,
        workspace,
        GraphCompatibility::Exact,
    )?;
    Ok(result_from_diagnostics(
        workspace.solution.clone(),
        diagnostics,
    ))
}

/// Solve into caller-owned storage, optionally starting from a supplied guess.
///
/// This function allocates no solution result. The workspace owns all numerical
/// scratch and may be reused across calls.
pub fn solve_pcg_into_with_workspace(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    rhs: &[f64],
    initial_guess: Option<&[f64]>,
    solution: &mut [f64],
    options: PcgOptions,
    workspace: &mut PcgWorkspace,
) -> Result<PcgDiagnostics, CmgError> {
    if solution.len() != graph.vertex_count() {
        return Err(CmgError::dimension(
            "solve_pcg_into solution",
            graph.vertex_count(),
            solution.len(),
        ));
    }
    let diagnostics = solve_pcg_core(
        graph,
        preconditioner,
        rhs,
        initial_guess,
        options,
        workspace,
        GraphCompatibility::Exact,
    )?;
    solution.copy_from_slice(&workspace.solution);
    Ok(diagnostics)
}

/// Solve a prepared current-weight graph using an earlier compatible hierarchy.
///
/// The retained hierarchy is used only as a preconditioner. All matrix-vector
/// products and certificates use `current_graph`. Compatibility requires exact
/// prepared-topology identity and is deliberately stricter than structural
/// equality.
pub fn solve_pcg_with_retained_preconditioner_into_with_workspace(
    current_graph: &Laplacian,
    retained_preconditioner: &CmgPreconditioner,
    rhs: &[f64],
    initial_guess: Option<&[f64]>,
    solution: &mut [f64],
    options: PcgOptions,
    workspace: &mut PcgWorkspace,
) -> Result<PcgDiagnostics, CmgError> {
    if solution.len() != current_graph.vertex_count() {
        return Err(CmgError::dimension(
            "retained-preconditioner PCG solution",
            current_graph.vertex_count(),
            solution.len(),
        ));
    }
    let diagnostics = solve_pcg_core(
        current_graph,
        retained_preconditioner,
        rhs,
        initial_guess,
        options,
        workspace,
        GraphCompatibility::PreparedTopology,
    )?;
    solution.copy_from_slice(&workspace.solution);
    Ok(diagnostics)
}

#[derive(Clone, Copy)]
enum GraphCompatibility {
    Exact,
    PreparedTopology,
}

fn solve_pcg_core(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    rhs: &[f64],
    initial_guess: Option<&[f64]>,
    options: PcgOptions,
    workspace: &mut PcgWorkspace,
    compatibility: GraphCompatibility,
) -> Result<PcgDiagnostics, CmgError> {
    let options = options.validate()?;
    let dimension = graph.vertex_count();
    let compatible = match compatibility {
        GraphCompatibility::Exact => preconditioner.matches_graph(graph),
        GraphCompatibility::PreparedTopology => preconditioner.matches_prepared_topology(graph),
    };
    if !compatible {
        return Err(CmgError::InvalidHierarchy {
            context: match compatibility {
                GraphCompatibility::Exact => {
                    "PCG graph differs from the preconditioner's finest graph"
                }
                GraphCompatibility::PreparedTopology => {
                    "retained PCG preconditioner has a different prepared topology"
                }
            },
        });
    }
    if rhs.len() != dimension {
        return Err(CmgError::dimension("solve_pcg rhs", dimension, rhs.len()));
    }
    workspace.validate(preconditioner)?;
    if let Some(guess) = initial_guess {
        if guess.len() != dimension {
            return Err(CmgError::dimension(
                "solve_pcg initial guess",
                dimension,
                guess.len(),
            ));
        }
    }

    let components = preconditioner.finest_components();
    workspace.projected_rhs.copy_from_slice(rhs);
    let rhs_projection_norm = components.project_rhs_in_place_with_workspace(
        &mut workspace.projected_rhs,
        options.validation,
        &mut workspace.component,
    )?;
    if let Some(guess) = initial_guess {
        workspace.solution.copy_from_slice(guess);
        components
            .center_in_place_with_workspace(&mut workspace.solution, &mut workspace.component)?;
        recompute_residual(
            graph,
            &workspace.projected_rhs,
            &workspace.solution,
            &mut workspace.residual,
        )?;
    } else {
        workspace.solution.fill(0.0);
        workspace.residual.copy_from_slice(&workspace.projected_rhs);
    }

    let rhs_norm = euclidean_norm(rhs);
    let initial_residual_norm = if initial_guess.is_some() {
        original_residual_norm(rhs, &workspace.projected_rhs, &workspace.residual)
    } else {
        rhs_norm
    };
    let projected_initial_norm = if initial_guess.is_some() {
        euclidean_norm(&workspace.residual)
    } else {
        euclidean_norm(&workspace.projected_rhs)
    };
    let operator_bound = graph.operator_norm_bound();
    let initial_solution_norm = if initial_guess.is_some() {
        euclidean_norm(&workspace.solution)
    } else {
        0.0
    };
    let initial_tolerance =
        allowed_residual(options, rhs_norm, operator_bound, initial_solution_norm);
    if initial_residual_norm <= initial_tolerance {
        return Ok(make_diagnostics(
            &workspace.solution,
            0,
            initial_residual_norm,
            initial_residual_norm,
            rhs_norm,
            initial_tolerance,
            operator_bound,
            0,
            rhs_projection_norm,
        ));
    }
    if projected_initial_norm == 0.0 {
        return Err(CmgError::ResidualVerificationFailed {
            iteration: 0,
            residual_norm: initial_residual_norm,
            tolerance: initial_tolerance,
        });
    }

    preconditioner.apply_compatible_into_prevalidated(
        &workspace.residual,
        &mut workspace.preconditioned,
        &mut workspace.cmg,
    )?;
    components
        .center_in_place_with_workspace(&mut workspace.preconditioned, &mut workspace.component)?;
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
        components
            .center_in_place_with_workspace(&mut workspace.solution, &mut workspace.component)?;

        let solution_norm = euclidean_norm(&workspace.solution);
        last_tolerance = allowed_residual(options, rhs_norm, operator_bound, solution_norm);
        let recursive_residual_norm = euclidean_norm(&workspace.residual);
        let candidate = recursive_residual_norm <= last_tolerance;
        let scheduled_recompute = iteration % options.residual_recompute_interval == 0;
        let mut restarted = false;

        if candidate || scheduled_recompute {
            let projected_fresh_norm = recompute_residual(
                graph,
                &workspace.projected_rhs,
                &workspace.solution,
                &mut workspace.matrix_direction,
            )?;
            workspace
                .residual
                .copy_from_slice(&workspace.matrix_direction);
            restarted = true;
            restarts += 1;
            if projected_fresh_norm <= last_tolerance {
                let original_norm = original_residual_norm(
                    rhs,
                    &workspace.projected_rhs,
                    &workspace.matrix_direction,
                );
                if original_norm <= last_tolerance {
                    return Ok(make_diagnostics(
                        &workspace.solution,
                        iteration,
                        initial_residual_norm,
                        original_norm,
                        rhs_norm,
                        last_tolerance,
                        operator_bound,
                        restarts,
                        rhs_projection_norm,
                    ));
                }
                if iteration == options.max_iterations {
                    return Err(CmgError::ResidualVerificationFailed {
                        iteration,
                        residual_norm: original_norm,
                        tolerance: last_tolerance,
                    });
                }
            }
        }

        if iteration == options.max_iterations {
            break;
        }

        // The public solver projected the submitted RHS once. Remove only the
        // component-nullspace roundoff accumulated by Krylov updates before
        // reusing the compatible stationary core.
        components
            .center_in_place_with_workspace(&mut workspace.residual, &mut workspace.component)?;
        preconditioner.apply_compatible_into_prevalidated(
            &workspace.residual,
            &mut workspace.preconditioned,
            &mut workspace.cmg,
        )?;
        components.center_in_place_with_workspace(
            &mut workspace.preconditioned,
            &mut workspace.component,
        )?;
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

    recompute_residual(
        graph,
        &workspace.projected_rhs,
        &workspace.solution,
        &mut workspace.matrix_direction,
    )?;
    let residual_norm =
        original_residual_norm(rhs, &workspace.projected_rhs, &workspace.matrix_direction);
    Err(CmgError::MaximumIterations {
        iterations: options.max_iterations,
        residual_norm,
        tolerance: last_tolerance,
    })
}

/// Solve with a prebuilt optional parallel CMG plan and a newly allocated workspace.
#[cfg(feature = "parallel")]
pub fn solve_pcg_with_plan(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    plan: &ParallelCmgPlan,
    rhs: &[f64],
    options: PcgOptions,
    executor: &ParallelExecutor,
) -> Result<PcgResult, CmgError> {
    let mut workspace = PcgWorkspace::new(preconditioner);
    solve_pcg_with_plan_and_workspace(
        graph,
        preconditioner,
        plan,
        rhs,
        options,
        &mut workspace,
        executor,
    )
}

/// Solve with a prebuilt optional parallel CMG plan and caller-owned workspace.
///
/// The submitted right-hand side is projected and the final residual is
/// certified against the original system exactly as in [`solve_pcg_with_workspace`].
#[cfg(feature = "parallel")]
pub fn solve_pcg_with_plan_and_workspace(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    plan: &ParallelCmgPlan,
    rhs: &[f64],
    options: PcgOptions,
    workspace: &mut PcgWorkspace,
    executor: &ParallelExecutor,
) -> Result<PcgResult, CmgError> {
    let diagnostics = solve_pcg_with_plan_core(
        graph,
        preconditioner,
        plan,
        rhs,
        None,
        options,
        workspace,
        executor,
        GraphCompatibility::Exact,
    )?;
    Ok(result_from_diagnostics(
        workspace.solution.clone(),
        diagnostics,
    ))
}

/// Solve with a parallel plan into caller-owned solution storage.
#[cfg(feature = "parallel")]
#[allow(clippy::too_many_arguments)]
pub fn solve_pcg_with_plan_into_with_workspace(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    plan: &ParallelCmgPlan,
    rhs: &[f64],
    initial_guess: Option<&[f64]>,
    solution: &mut [f64],
    options: PcgOptions,
    workspace: &mut PcgWorkspace,
    executor: &ParallelExecutor,
) -> Result<PcgDiagnostics, CmgError> {
    if solution.len() != graph.vertex_count() {
        return Err(CmgError::dimension(
            "planned PCG solution",
            graph.vertex_count(),
            solution.len(),
        ));
    }
    let diagnostics = solve_pcg_with_plan_core(
        graph,
        preconditioner,
        plan,
        rhs,
        initial_guess,
        options,
        workspace,
        executor,
        GraphCompatibility::Exact,
    )?;
    solution.copy_from_slice(&workspace.solution);
    Ok(diagnostics)
}

/// Solve a current prepared frame using an earlier hierarchy and its plan.
///
/// The plan is used only for retained-hierarchy preconditioner application;
/// current-operator products and all certificates use `current_graph`.
#[cfg(feature = "parallel")]
#[allow(clippy::too_many_arguments)]
pub fn solve_pcg_with_plan_and_retained_preconditioner_into_with_workspace(
    current_graph: &Laplacian,
    retained_preconditioner: &CmgPreconditioner,
    retained_plan: &ParallelCmgPlan,
    rhs: &[f64],
    initial_guess: Option<&[f64]>,
    solution: &mut [f64],
    options: PcgOptions,
    workspace: &mut PcgWorkspace,
    executor: &ParallelExecutor,
) -> Result<PcgDiagnostics, CmgError> {
    if solution.len() != current_graph.vertex_count() {
        return Err(CmgError::dimension(
            "planned retained-preconditioner PCG solution",
            current_graph.vertex_count(),
            solution.len(),
        ));
    }
    let diagnostics = solve_pcg_with_plan_core(
        current_graph,
        retained_preconditioner,
        retained_plan,
        rhs,
        initial_guess,
        options,
        workspace,
        executor,
        GraphCompatibility::PreparedTopology,
    )?;
    solution.copy_from_slice(&workspace.solution);
    Ok(diagnostics)
}

#[cfg(feature = "parallel")]
#[allow(clippy::too_many_arguments)]
fn solve_pcg_with_plan_core(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    plan: &ParallelCmgPlan,
    rhs: &[f64],
    initial_guess: Option<&[f64]>,
    options: PcgOptions,
    workspace: &mut PcgWorkspace,
    executor: &ParallelExecutor,
    compatibility: GraphCompatibility,
) -> Result<PcgDiagnostics, CmgError> {
    let options = options.validate()?;
    let dimension = graph.vertex_count();
    let graph_matches = match compatibility {
        GraphCompatibility::Exact => preconditioner.matches_graph(graph),
        GraphCompatibility::PreparedTopology => preconditioner.matches_prepared_topology(graph),
    };
    if !graph_matches {
        return Err(CmgError::InvalidHierarchy {
            context: match compatibility {
                GraphCompatibility::Exact => {
                    "PCG graph differs from the preconditioner's finest graph"
                }
                GraphCompatibility::PreparedTopology => {
                    "planned retained PCG preconditioner has a different prepared topology"
                }
            },
        });
    }
    if rhs.len() != dimension {
        return Err(CmgError::dimension("solve_pcg rhs", dimension, rhs.len()));
    }
    workspace.validate(preconditioner)?;
    plan.validate(preconditioner)?;
    if let Some(guess) = initial_guess {
        if guess.len() != dimension {
            return Err(CmgError::dimension(
                "planned PCG initial guess",
                dimension,
                guess.len(),
            ));
        }
    }

    let components = preconditioner.finest_components();
    workspace.projected_rhs.copy_from_slice(rhs);
    let rhs_projection_norm = components.project_rhs_in_place_with_workspace(
        &mut workspace.projected_rhs,
        options.validation,
        &mut workspace.component,
    )?;
    if let Some(guess) = initial_guess {
        workspace.solution.copy_from_slice(guess);
        components.center_in_place_with_workspace_and_executor(
            &mut workspace.solution,
            &mut workspace.component,
            executor,
        )?;
        recompute_residual_with_mode(
            compatibility,
            plan,
            executor,
            graph,
            &workspace.projected_rhs,
            &workspace.solution,
            &mut workspace.residual,
        )?;
    } else {
        workspace.solution.fill(0.0);
        workspace.residual.copy_from_slice(&workspace.projected_rhs);
    }

    let rhs_norm = euclidean_norm_with_executor(rhs, executor);
    let initial_residual_norm = if initial_guess.is_some() {
        original_residual_norm(rhs, &workspace.projected_rhs, &workspace.residual)
    } else {
        rhs_norm
    };
    let projected_initial_norm = if initial_guess.is_some() {
        euclidean_norm_with_executor(&workspace.residual, executor)
    } else {
        euclidean_norm_with_executor(&workspace.projected_rhs, executor)
    };
    let operator_bound = graph.operator_norm_bound();
    let initial_solution_norm = if initial_guess.is_some() {
        euclidean_norm_with_executor(&workspace.solution, executor)
    } else {
        0.0
    };
    let initial_tolerance =
        allowed_residual(options, rhs_norm, operator_bound, initial_solution_norm);
    if initial_residual_norm <= initial_tolerance {
        return Ok(make_diagnostics(
            &workspace.solution,
            0,
            initial_residual_norm,
            initial_residual_norm,
            rhs_norm,
            initial_tolerance,
            operator_bound,
            0,
            rhs_projection_norm,
        ));
    }
    if projected_initial_norm == 0.0 {
        return Err(CmgError::ResidualVerificationFailed {
            iteration: 0,
            residual_norm: initial_residual_norm,
            tolerance: initial_tolerance,
        });
    }

    plan.apply_compatible_into_prevalidated(
        preconditioner,
        &workspace.residual,
        &mut workspace.preconditioned,
        &mut workspace.cmg,
        options.validation,
        executor,
    )?;
    components.center_in_place_with_workspace_and_executor(
        &mut workspace.preconditioned,
        &mut workspace.component,
        executor,
    )?;
    let mut rho = dot_with_executor(&workspace.residual, &workspace.preconditioned, executor);
    validate_positive_pcg(0, "r^T M r", rho)?;
    workspace
        .direction
        .copy_from_slice(&workspace.preconditioned);

    let mut restarts = 0_usize;
    let mut last_tolerance = initial_tolerance;

    for iteration in 1..=options.max_iterations {
        matvec_with_mode(
            compatibility,
            plan,
            executor,
            graph,
            &workspace.direction,
            &mut workspace.matrix_direction,
        )?;
        let direction_curvature =
            dot_with_executor(&workspace.direction, &workspace.matrix_direction, executor);
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
        components.center_in_place_with_workspace_and_executor(
            &mut workspace.solution,
            &mut workspace.component,
            executor,
        )?;

        let solution_norm = euclidean_norm_with_executor(&workspace.solution, executor);
        last_tolerance = allowed_residual(options, rhs_norm, operator_bound, solution_norm);
        let recursive_residual_norm = euclidean_norm_with_executor(&workspace.residual, executor);
        let candidate = recursive_residual_norm <= last_tolerance;
        let scheduled_recompute = iteration % options.residual_recompute_interval == 0;
        let mut restarted = false;

        if candidate || scheduled_recompute {
            let projected_fresh_norm = recompute_residual_with_mode(
                compatibility,
                plan,
                executor,
                graph,
                &workspace.projected_rhs,
                &workspace.solution,
                &mut workspace.matrix_direction,
            )?;
            workspace
                .residual
                .copy_from_slice(&workspace.matrix_direction);
            restarted = true;
            restarts += 1;
            if projected_fresh_norm <= last_tolerance {
                let original_norm = original_residual_norm(
                    rhs,
                    &workspace.projected_rhs,
                    &workspace.matrix_direction,
                );
                if original_norm <= last_tolerance {
                    return Ok(make_diagnostics(
                        &workspace.solution,
                        iteration,
                        initial_residual_norm,
                        original_norm,
                        rhs_norm,
                        last_tolerance,
                        operator_bound,
                        restarts,
                        rhs_projection_norm,
                    ));
                }
                if iteration == options.max_iterations {
                    return Err(CmgError::ResidualVerificationFailed {
                        iteration,
                        residual_norm: original_norm,
                        tolerance: last_tolerance,
                    });
                }
            }
        }

        if iteration == options.max_iterations {
            break;
        }

        // The public solver projected the submitted RHS once. Remove only the
        // component-nullspace roundoff accumulated by Krylov updates before
        // reusing the compatible stationary core.
        components.center_in_place_with_workspace_and_executor(
            &mut workspace.residual,
            &mut workspace.component,
            executor,
        )?;
        plan.apply_compatible_into_prevalidated(
            preconditioner,
            &workspace.residual,
            &mut workspace.preconditioned,
            &mut workspace.cmg,
            options.validation,
            executor,
        )?;
        components.center_in_place_with_workspace_and_executor(
            &mut workspace.preconditioned,
            &mut workspace.component,
            executor,
        )?;
        let new_rho = dot_with_executor(&workspace.residual, &workspace.preconditioned, executor);
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

    recompute_residual_with_mode(
        compatibility,
        plan,
        executor,
        graph,
        &workspace.projected_rhs,
        &workspace.solution,
        &mut workspace.matrix_direction,
    )?;
    let residual_norm =
        original_residual_norm(rhs, &workspace.projected_rhs, &workspace.matrix_direction);
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

/// Solve borrowed contiguous or strided right-hand sides into caller buffers.
///
/// The supplied workspace retains all gather/scatter scratch. No CMG-owned
/// allocation occurs during a successfully validated call.
pub fn solve_pcg_batch_into_with_workspace(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    right_hand_sides: PcgBatchRef<'_>,
    initial_guesses: Option<PcgBatchRef<'_>>,
    mut solutions: PcgBatchMut<'_>,
    diagnostics: &mut [PcgDiagnostics],
    options: PcgOptions,
    workspace: &mut PcgBatchWorkspace,
) -> Result<(), CmgError> {
    validate_batch_buffers(
        graph,
        preconditioner,
        right_hand_sides,
        initial_guesses,
        &solutions,
        diagnostics.len(),
        core::slice::from_ref(workspace),
        GraphCompatibility::Exact,
    )?;
    for (rhs_index, diagnostic) in diagnostics.iter_mut().enumerate() {
        let result = solve_batch_item(
            graph,
            preconditioner,
            right_hand_sides,
            initial_guesses,
            rhs_index,
            options,
            workspace,
            GraphCompatibility::Exact,
        )?;
        *diagnostic = result;
        solutions.copy_rhs_from(rhs_index, &workspace.pcg.solution);
    }
    Ok(())
}

/// Profile validation, gather, certified solve, scatter, and result construction
/// on the exact caller-buffer production path.
///
/// The timers are intended for separate trace runs, not benchmark timings.
#[cfg(feature = "profiling")]
#[allow(clippy::too_many_arguments)]
pub fn profile_pcg_batch_into_with_workspace(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    right_hand_sides: PcgBatchRef<'_>,
    initial_guesses: Option<PcgBatchRef<'_>>,
    mut solutions: PcgBatchMut<'_>,
    diagnostics: &mut [PcgDiagnostics],
    options: PcgOptions,
    workspace: &mut PcgBatchWorkspace,
) -> Result<PcgBatchPhaseProfile, CmgError> {
    let total_start = Instant::now();
    let validation_start = Instant::now();
    validate_batch_buffers(
        graph,
        preconditioner,
        right_hand_sides,
        initial_guesses,
        &solutions,
        diagnostics.len(),
        core::slice::from_ref(workspace),
        GraphCompatibility::Exact,
    )?;
    let validation_nanoseconds = validation_start.elapsed().as_nanos();
    let mut gather_nanoseconds = 0;
    let mut solve_nanoseconds = 0;
    let mut scatter_nanoseconds = 0;
    for (rhs_index, diagnostic) in diagnostics.iter_mut().enumerate() {
        let PcgBatchWorkspace {
            pcg,
            rhs: rhs_staging,
            guess: guess_staging,
            ..
        } = workspace;
        let gather_start = Instant::now();
        let rhs = if let Some(rhs) = right_hand_sides.contiguous_rhs(rhs_index) {
            rhs
        } else {
            right_hand_sides.copy_rhs_into(rhs_index, rhs_staging);
            rhs_staging
        };
        let guess = initial_guesses.map(|guesses| {
            if let Some(guess) = guesses.contiguous_rhs(rhs_index) {
                guess
            } else {
                guesses.copy_rhs_into(rhs_index, guess_staging);
                guess_staging
            }
        } as &[f64]);
        gather_nanoseconds += gather_start.elapsed().as_nanos();
        let solve_start = Instant::now();
        *diagnostic = solve_pcg_core(
            graph,
            preconditioner,
            rhs,
            guess,
            options,
            pcg,
            GraphCompatibility::Exact,
        )?;
        solve_nanoseconds += solve_start.elapsed().as_nanos();
        let scatter_start = Instant::now();
        solutions.copy_rhs_from(rhs_index, &pcg.solution);
        scatter_nanoseconds += scatter_start.elapsed().as_nanos();
    }
    Ok(PcgBatchPhaseProfile {
        validation_nanoseconds,
        gather_nanoseconds,
        solve_nanoseconds,
        scatter_nanoseconds,
        result_construction_nanoseconds: 0,
        total_nanoseconds: total_start.elapsed().as_nanos(),
    })
}

/// Solve a borrowed batch with a retained compatible prepared-topology hierarchy.
pub fn solve_pcg_batch_with_retained_preconditioner_into_with_workspace(
    current_graph: &Laplacian,
    retained_preconditioner: &CmgPreconditioner,
    right_hand_sides: PcgBatchRef<'_>,
    initial_guesses: Option<PcgBatchRef<'_>>,
    mut solutions: PcgBatchMut<'_>,
    diagnostics: &mut [PcgDiagnostics],
    options: PcgOptions,
    workspace: &mut PcgBatchWorkspace,
) -> Result<(), CmgError> {
    validate_batch_buffers(
        current_graph,
        retained_preconditioner,
        right_hand_sides,
        initial_guesses,
        &solutions,
        diagnostics.len(),
        core::slice::from_ref(workspace),
        GraphCompatibility::PreparedTopology,
    )?;
    for (rhs_index, diagnostic) in diagnostics.iter_mut().enumerate() {
        let result = solve_batch_item(
            current_graph,
            retained_preconditioner,
            right_hand_sides,
            initial_guesses,
            rhs_index,
            options,
            workspace,
            GraphCompatibility::PreparedTopology,
        )?;
        *diagnostic = result;
        solutions.copy_rhs_from(rhs_index, &workspace.pcg.solution);
    }
    Ok(())
}

/// Solve borrowed right-hand sides concurrently using caller-owned workspaces.
#[cfg(feature = "parallel")]
#[allow(clippy::too_many_arguments)]
pub fn solve_pcg_batch_into_with_executor(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    right_hand_sides: PcgBatchRef<'_>,
    initial_guesses: Option<PcgBatchRef<'_>>,
    mut solutions: PcgBatchMut<'_>,
    diagnostics: &mut [PcgDiagnostics],
    options: PcgOptions,
    workspaces: &mut [PcgBatchWorkspace],
    executor: &ParallelExecutor,
) -> Result<(), CmgError> {
    solve_pcg_batch_into_with_executor_core(
        graph,
        preconditioner,
        right_hand_sides,
        initial_guesses,
        &mut solutions,
        diagnostics,
        options,
        workspaces,
        executor,
        GraphCompatibility::Exact,
    )
}

/// Solve borrowed right-hand sides concurrently with a retained hierarchy.
#[cfg(feature = "parallel")]
#[allow(clippy::too_many_arguments)]
pub fn solve_pcg_batch_with_retained_preconditioner_into_with_executor(
    current_graph: &Laplacian,
    retained_preconditioner: &CmgPreconditioner,
    right_hand_sides: PcgBatchRef<'_>,
    initial_guesses: Option<PcgBatchRef<'_>>,
    mut solutions: PcgBatchMut<'_>,
    diagnostics: &mut [PcgDiagnostics],
    options: PcgOptions,
    workspaces: &mut [PcgBatchWorkspace],
    executor: &ParallelExecutor,
) -> Result<(), CmgError> {
    solve_pcg_batch_into_with_executor_core(
        current_graph,
        retained_preconditioner,
        right_hand_sides,
        initial_guesses,
        &mut solutions,
        diagnostics,
        options,
        workspaces,
        executor,
        GraphCompatibility::PreparedTopology,
    )
}

/// Solve a borrowed batch serially across RHS while using a parallel plan
/// within each operator and preconditioner application.
#[cfg(feature = "parallel")]
#[allow(clippy::too_many_arguments)]
pub fn solve_pcg_batch_into_with_plan_and_workspace(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    plan: &ParallelCmgPlan,
    right_hand_sides: PcgBatchRef<'_>,
    initial_guesses: Option<PcgBatchRef<'_>>,
    mut solutions: PcgBatchMut<'_>,
    diagnostics: &mut [PcgDiagnostics],
    options: PcgOptions,
    workspace: &mut PcgBatchWorkspace,
    executor: &ParallelExecutor,
) -> Result<(), CmgError> {
    solve_pcg_batch_into_with_plan_core(
        graph,
        preconditioner,
        plan,
        right_hand_sides,
        initial_guesses,
        &mut solutions,
        diagnostics,
        options,
        workspace,
        executor,
        GraphCompatibility::Exact,
    )
}

/// Solve a borrowed batch against the current graph using a retained plan only
/// for preconditioner application.
#[cfg(feature = "parallel")]
#[allow(clippy::too_many_arguments)]
pub fn solve_pcg_batch_with_plan_and_retained_preconditioner_into_with_workspace(
    current_graph: &Laplacian,
    retained_preconditioner: &CmgPreconditioner,
    retained_plan: &ParallelCmgPlan,
    right_hand_sides: PcgBatchRef<'_>,
    initial_guesses: Option<PcgBatchRef<'_>>,
    mut solutions: PcgBatchMut<'_>,
    diagnostics: &mut [PcgDiagnostics],
    options: PcgOptions,
    workspace: &mut PcgBatchWorkspace,
    executor: &ParallelExecutor,
) -> Result<(), CmgError> {
    solve_pcg_batch_into_with_plan_core(
        current_graph,
        retained_preconditioner,
        retained_plan,
        right_hand_sides,
        initial_guesses,
        &mut solutions,
        diagnostics,
        options,
        workspace,
        executor,
        GraphCompatibility::PreparedTopology,
    )
}

#[cfg(feature = "parallel")]
#[allow(clippy::too_many_arguments)]
fn solve_pcg_batch_into_with_plan_core(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    plan: &ParallelCmgPlan,
    right_hand_sides: PcgBatchRef<'_>,
    initial_guesses: Option<PcgBatchRef<'_>>,
    solutions: &mut PcgBatchMut<'_>,
    diagnostics: &mut [PcgDiagnostics],
    options: PcgOptions,
    workspace: &mut PcgBatchWorkspace,
    executor: &ParallelExecutor,
    compatibility: GraphCompatibility,
) -> Result<(), CmgError> {
    validate_batch_buffers(
        graph,
        preconditioner,
        right_hand_sides,
        initial_guesses,
        solutions,
        diagnostics.len(),
        core::slice::from_ref(workspace),
        compatibility,
    )?;
    plan.validate(preconditioner)?;
    for (rhs_index, diagnostic) in diagnostics.iter_mut().enumerate() {
        let result = solve_batch_item_with_plan(
            graph,
            preconditioner,
            plan,
            right_hand_sides,
            initial_guesses,
            rhs_index,
            options,
            workspace,
            executor,
            compatibility,
        )?;
        *diagnostic = result;
        solutions.copy_rhs_from(rhs_index, &workspace.pcg.solution);
    }
    Ok(())
}

#[cfg(feature = "parallel")]
#[allow(clippy::too_many_arguments)]
fn solve_batch_item_with_plan(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    plan: &ParallelCmgPlan,
    right_hand_sides: PcgBatchRef<'_>,
    initial_guesses: Option<PcgBatchRef<'_>>,
    rhs_index: usize,
    options: PcgOptions,
    workspace: &mut PcgBatchWorkspace,
    executor: &ParallelExecutor,
    compatibility: GraphCompatibility,
) -> Result<PcgDiagnostics, CmgError> {
    let PcgBatchWorkspace {
        pcg,
        rhs: rhs_staging,
        guess: guess_staging,
        ..
    } = workspace;
    let rhs = if let Some(rhs) = right_hand_sides.contiguous_rhs(rhs_index) {
        rhs
    } else {
        right_hand_sides.copy_rhs_into(rhs_index, rhs_staging);
        rhs_staging
    };
    let guess = initial_guesses.map(|guesses| {
        if let Some(guess) = guesses.contiguous_rhs(rhs_index) {
            guess
        } else {
            guesses.copy_rhs_into(rhs_index, guess_staging);
            guess_staging
        }
    } as &[f64]);
    solve_pcg_with_plan_core(
        graph,
        preconditioner,
        plan,
        rhs,
        guess,
        options,
        pcg,
        executor,
        compatibility,
    )
}

#[cfg(feature = "parallel")]
#[allow(clippy::too_many_arguments)]
fn solve_pcg_batch_into_with_executor_core(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    right_hand_sides: PcgBatchRef<'_>,
    initial_guesses: Option<PcgBatchRef<'_>>,
    solutions: &mut PcgBatchMut<'_>,
    diagnostics: &mut [PcgDiagnostics],
    options: PcgOptions,
    workspaces: &mut [PcgBatchWorkspace],
    executor: &ParallelExecutor,
    compatibility: GraphCompatibility,
) -> Result<(), CmgError> {
    validate_batch_buffers(
        graph,
        preconditioner,
        right_hand_sides,
        initial_guesses,
        solutions,
        diagnostics.len(),
        workspaces,
        compatibility,
    )?;
    if right_hand_sides.rhs_count == 0 {
        return Ok(());
    }
    let concurrency = executor
        .batch_concurrency(workspaces[0].byte_len(), right_hand_sides.rhs_count)?
        .min(workspaces.len());
    for start in (0..right_hand_sides.rhs_count).step_by(concurrency) {
        let count = concurrency.min(right_hand_sides.rhs_count - start);
        executor.install(|| {
            workspaces[..count]
                .par_iter_mut()
                .enumerate()
                .for_each(|(local_index, workspace)| {
                    workspace.outcome = Some(solve_batch_item(
                        graph,
                        preconditioner,
                        right_hand_sides,
                        initial_guesses,
                        start + local_index,
                        options,
                        workspace,
                        compatibility,
                    ));
                });
        });
        for (local_index, workspace) in workspaces[..count].iter_mut().enumerate() {
            let rhs_index = start + local_index;
            let result = workspace
                .outcome
                .take()
                .ok_or(CmgError::InvalidHierarchy {
                    context: "parallel PCG batch workspace has no outcome",
                })??;
            diagnostics[rhs_index] = result;
            solutions.copy_rhs_from(rhs_index, &workspace.pcg.solution);
        }
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn validate_batch_buffers(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    right_hand_sides: PcgBatchRef<'_>,
    initial_guesses: Option<PcgBatchRef<'_>>,
    solutions: &PcgBatchMut<'_>,
    diagnostic_count: usize,
    workspaces: &[PcgBatchWorkspace],
    compatibility: GraphCompatibility,
) -> Result<(), CmgError> {
    let graph_matches = match compatibility {
        GraphCompatibility::Exact => preconditioner.matches_graph(graph),
        GraphCompatibility::PreparedTopology => preconditioner.matches_prepared_topology(graph),
    };
    if !graph_matches {
        return Err(CmgError::InvalidHierarchy {
            context: "PCG batch graph and preconditioner are incompatible",
        });
    }
    let dimension = graph.vertex_count();
    for (context, actual) in [
        ("PCG batch RHS dimension", right_hand_sides.dimension),
        ("PCG batch solution dimension", solutions.dimension),
    ] {
        if actual != dimension {
            return Err(CmgError::dimension(context, dimension, actual));
        }
    }
    if solutions.rhs_count != right_hand_sides.rhs_count {
        return Err(CmgError::dimension(
            "PCG batch solution count",
            right_hand_sides.rhs_count,
            solutions.rhs_count,
        ));
    }
    if diagnostic_count != right_hand_sides.rhs_count {
        return Err(CmgError::dimension(
            "PCG batch diagnostic count",
            right_hand_sides.rhs_count,
            diagnostic_count,
        ));
    }
    if let Some(guesses) = initial_guesses {
        if guesses.dimension != dimension {
            return Err(CmgError::dimension(
                "PCG batch guess dimension",
                dimension,
                guesses.dimension,
            ));
        }
        if guesses.rhs_count != right_hand_sides.rhs_count {
            return Err(CmgError::dimension(
                "PCG batch guess count",
                right_hand_sides.rhs_count,
                guesses.rhs_count,
            ));
        }
    }
    if right_hand_sides.rhs_count > 0 && workspaces.is_empty() {
        return Err(CmgError::dimension("PCG batch workspace pool", 1, 0));
    }
    for workspace in workspaces {
        workspace.validate(preconditioner)?;
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn solve_batch_item(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    right_hand_sides: PcgBatchRef<'_>,
    initial_guesses: Option<PcgBatchRef<'_>>,
    rhs_index: usize,
    options: PcgOptions,
    workspace: &mut PcgBatchWorkspace,
    compatibility: GraphCompatibility,
) -> Result<PcgDiagnostics, CmgError> {
    let PcgBatchWorkspace {
        pcg,
        rhs: rhs_staging,
        guess: guess_staging,
        ..
    } = workspace;
    let rhs = if let Some(rhs) = right_hand_sides.contiguous_rhs(rhs_index) {
        rhs
    } else {
        right_hand_sides.copy_rhs_into(rhs_index, rhs_staging);
        rhs_staging
    };
    let guess = initial_guesses.map(|guesses| {
        if let Some(guess) = guesses.contiguous_rhs(rhs_index) {
            guess
        } else {
            guesses.copy_rhs_into(rhs_index, guess_staging);
            guess_staging
        }
    } as &[f64]);
    solve_pcg_core(
        graph,
        preconditioner,
        rhs,
        guess,
        options,
        pcg,
        compatibility,
    )
}

/// Solve independent right-hand sides concurrently in a package-owned pool.
///
/// Every RHS uses the unchanged certified serial PCG algorithm and a private
/// reusable workspace. The executor's workspace-memory budget limits the
/// number of simultaneous solves. Results retain input order.
#[cfg(feature = "parallel")]
pub fn solve_pcg_batch_with_executor(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    right_hand_sides: &[Vec<f64>],
    options: PcgOptions,
    executor: &ParallelExecutor,
) -> Result<Vec<PcgResult>, CmgError> {
    if right_hand_sides.is_empty() {
        return Ok(Vec::new());
    }
    if !preconditioner.matches_graph(graph) {
        return Err(CmgError::InvalidHierarchy {
            context: "parallel PCG graph differs from the preconditioner's finest graph",
        });
    }
    for rhs in right_hand_sides {
        if rhs.len() != graph.vertex_count() {
            return Err(CmgError::dimension(
                "solve_pcg_batch_parallel rhs",
                graph.vertex_count(),
                rhs.len(),
            ));
        }
    }

    let first_workspace = PcgWorkspace::new(preconditioner);
    let concurrency =
        executor.batch_concurrency(first_workspace.byte_len(), right_hand_sides.len())?;
    let mut workspaces = Vec::with_capacity(concurrency);
    workspaces.push(first_workspace);
    workspaces.extend((1..concurrency).map(|_| PcgWorkspace::new(preconditioner)));

    let mut results = Vec::with_capacity(right_hand_sides.len());
    for rhs_chunk in right_hand_sides.chunks(concurrency) {
        let chunk_results: Vec<Result<PcgResult, CmgError>> = executor.install(|| {
            workspaces[..rhs_chunk.len()]
                .par_iter_mut()
                .zip(rhs_chunk.par_iter())
                .map(|(workspace, rhs)| {
                    solve_pcg_with_workspace(graph, preconditioner, rhs, options, workspace)
                })
                .collect()
        });
        for result in chunk_results {
            results.push(result?);
        }
    }
    Ok(results)
}

/// Construct a package-owned pool and solve a batch of independent systems.
#[cfg(feature = "parallel")]
pub fn solve_pcg_batch_parallel(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    right_hand_sides: &[Vec<f64>],
    options: PcgOptions,
    parallel: ParallelOptions,
) -> Result<Vec<PcgResult>, CmgError> {
    let executor = ParallelExecutor::new(parallel)?;
    solve_pcg_batch_with_executor(graph, preconditioner, right_hand_sides, options, &executor)
}

#[allow(clippy::too_many_arguments)]
fn make_diagnostics(
    solution: &[f64],
    iterations: usize,
    initial_residual_norm: f64,
    residual_norm: f64,
    rhs_norm: f64,
    tolerance: f64,
    operator_bound: f64,
    restarts: usize,
    rhs_projection_norm: f64,
) -> PcgDiagnostics {
    let solution_norm = euclidean_norm(solution);
    let denominator = rhs_norm + operator_bound * solution_norm;
    let relative_residual = if rhs_norm > 0.0 {
        residual_norm / rhs_norm
    } else {
        residual_norm
    };
    let backward_error = if denominator > 0.0 {
        residual_norm / denominator
    } else {
        0.0
    };
    PcgDiagnostics {
        iterations,
        initial_residual_norm,
        residual_norm,
        relative_residual,
        backward_error,
        tolerance,
        restarts,
        rhs_projection_norm,
    }
}

fn result_from_diagnostics(solution: Vec<f64>, diagnostics: PcgDiagnostics) -> PcgResult {
    PcgResult {
        solution,
        iterations: diagnostics.iterations,
        initial_residual_norm: diagnostics.initial_residual_norm,
        residual_norm: diagnostics.residual_norm,
        relative_residual: diagnostics.relative_residual,
        backward_error: diagnostics.backward_error,
        tolerance: diagnostics.tolerance,
        restarts: diagnostics.restarts,
        rhs_projection_norm: diagnostics.rhs_projection_norm,
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

#[cfg(feature = "parallel")]
fn matvec_with_mode(
    compatibility: GraphCompatibility,
    plan: &ParallelCmgPlan,
    executor: &ParallelExecutor,
    graph: &Laplacian,
    input: &[f64],
    output: &mut [f64],
) -> Result<(), CmgError> {
    match compatibility {
        GraphCompatibility::Exact => plan.finest_matvec_into(graph, input, output, executor),
        GraphCompatibility::PreparedTopology => graph.matvec_into(input, output),
    }
}

#[cfg(feature = "parallel")]
fn recompute_residual_with_mode(
    compatibility: GraphCompatibility,
    plan: &ParallelCmgPlan,
    executor: &ParallelExecutor,
    graph: &Laplacian,
    rhs: &[f64],
    solution: &[f64],
    residual: &mut [f64],
) -> Result<f64, CmgError> {
    matvec_with_mode(compatibility, plan, executor, graph, solution, residual)?;
    for (value, rhs_value) in residual.iter_mut().zip(rhs) {
        *value = *rhs_value - *value;
    }
    Ok(euclidean_norm_with_executor(residual, executor))
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

fn original_residual_norm(
    original_rhs: &[f64],
    projected_rhs: &[f64],
    projected_residual: &[f64],
) -> f64 {
    let values = || {
        original_rhs
            .iter()
            .zip(projected_rhs)
            .zip(projected_residual)
            .map(|((&original, &projected), &residual)| residual + (original - projected))
    };
    let scale = values().map(f64::abs).fold(0.0, f64::max);
    if scale == 0.0 {
        0.0
    } else {
        scale
            * compensated_sum(values().map(|value| {
                let scaled = value / scale;
                scaled * scaled
            }))
            .sqrt()
    }
}

#[cfg(feature = "parallel")]
pub(crate) fn dot_with_executor(left: &[f64], right: &[f64], executor: &ParallelExecutor) -> f64 {
    debug_assert_eq!(left.len(), right.len());
    let options = executor.options();
    let parallel_floor = options
        .min_parallel_len
        .max(options.reduction_chunk_size.saturating_mul(8));
    if left.len() < parallel_floor || executor.thread_count() <= 1 {
        return dot(left, right);
    }
    executor.install(|| fixed_chunk_dot(left, right, options.reduction_chunk_size))
}

#[cfg(feature = "parallel")]
fn fixed_chunk_dot(left: &[f64], right: &[f64], chunk_size: usize) -> f64 {
    debug_assert_eq!(left.len(), right.len());
    let chunk_count = left.len().div_ceil(chunk_size);
    if chunk_count == 0 {
        return 0.0;
    }

    fn reduce_range(
        left: &[f64],
        right: &[f64],
        chunk_size: usize,
        first_chunk: usize,
        last_chunk: usize,
    ) -> f64 {
        if last_chunk - first_chunk == 1 {
            let start = first_chunk * chunk_size;
            let end = left.len().min(start + chunk_size);
            return compensated_sum(
                left[start..end]
                    .iter()
                    .zip(&right[start..end])
                    .map(|(left, right)| left * right),
            );
        }
        let middle = first_chunk + (last_chunk - first_chunk) / 2;
        let (left_sum, right_sum) = rayon::join(
            || reduce_range(left, right, chunk_size, first_chunk, middle),
            || reduce_range(left, right, chunk_size, middle, last_chunk),
        );
        compensated_sum([left_sum, right_sum])
    }

    reduce_range(left, right, chunk_size, 0, chunk_count)
}

#[cfg(all(test, feature = "parallel"))]
mod deterministic_parallel_dot_tests {
    use super::{dot, dot_with_executor};
    use crate::{ParallelExecutor, ParallelOptions};

    #[test]
    fn fixed_chunk_dot_is_thread_count_invariant() {
        let left: Vec<f64> = (0..257)
            .map(|index| ((index * 17) % 101) as f64 / 13.0 - 3.0)
            .collect();
        let right: Vec<f64> = (0..257)
            .map(|index| ((index * 31 + 7) % 113) as f64 / 19.0 - 2.0)
            .collect();
        let mut reference = None;
        for threads in [2, 3, 4] {
            let executor = ParallelExecutor::new(ParallelOptions {
                threads,
                min_parallel_len: 1,
                reduction_chunk_size: 16,
                ..ParallelOptions::default()
            })
            .unwrap();
            let value = dot_with_executor(&left, &right, &executor);
            match reference {
                Some(bits) => assert_eq!(bits, value.to_bits()),
                None => reference = Some(value.to_bits()),
            }
        }
        let fixed = f64::from_bits(reference.unwrap());
        let serial = dot(&left, &right);
        assert!((fixed - serial).abs() <= 2.0e-13 * (1.0 + serial.abs()));
    }
}

fn dot(left: &[f64], right: &[f64]) -> f64 {
    compensated_sum(left.iter().zip(right).map(|(x, y)| x * y))
}

#[cfg(feature = "parallel")]
pub(crate) fn euclidean_norm_with_executor(values: &[f64], executor: &ParallelExecutor) -> f64 {
    let options = executor.options();
    let parallel_floor = options
        .reduction_chunk_size
        .saturating_mul(executor.thread_count())
        .saturating_mul(2);
    let scale = if executor.should_parallel(values.len()) && values.len() >= parallel_floor {
        executor.install(|| {
            values
                .par_chunks(options.reduction_chunk_size)
                .map(|chunk| {
                    chunk
                        .iter()
                        .map(|value| value.abs())
                        .fold(0.0_f64, f64::max)
                })
                .reduce(|| 0.0_f64, f64::max)
        })
    } else {
        values
            .iter()
            .map(|value| value.abs())
            .fold(0.0_f64, f64::max)
    };
    if scale == 0.0 {
        0.0
    } else {
        scale * scaled_square_sum_with_executor(values, scale, executor).sqrt()
    }
}

#[cfg(feature = "parallel")]
fn scaled_square_sum_with_executor(values: &[f64], scale: f64, executor: &ParallelExecutor) -> f64 {
    let options = executor.options();
    let parallel_floor = options
        .min_parallel_len
        .max(options.reduction_chunk_size.saturating_mul(8));
    if values.len() < parallel_floor || executor.thread_count() <= 1 {
        return compensated_sum(values.iter().map(|value| {
            let scaled = *value / scale;
            scaled * scaled
        }));
    }
    executor.install(|| fixed_chunk_scaled_square_sum(values, scale, options.reduction_chunk_size))
}

#[cfg(feature = "parallel")]
fn fixed_chunk_scaled_square_sum(values: &[f64], scale: f64, chunk_size: usize) -> f64 {
    let chunk_count = values.len().div_ceil(chunk_size);
    if chunk_count == 0 {
        return 0.0;
    }

    fn reduce_range(
        values: &[f64],
        scale: f64,
        chunk_size: usize,
        first_chunk: usize,
        last_chunk: usize,
    ) -> f64 {
        if last_chunk - first_chunk == 1 {
            let start = first_chunk * chunk_size;
            let end = values.len().min(start + chunk_size);
            return compensated_sum(values[start..end].iter().map(|value| {
                let scaled = *value / scale;
                scaled * scaled
            }));
        }
        let middle = first_chunk + (last_chunk - first_chunk) / 2;
        let (left_sum, right_sum) = rayon::join(
            || reduce_range(values, scale, chunk_size, first_chunk, middle),
            || reduce_range(values, scale, chunk_size, middle, last_chunk),
        );
        compensated_sum([left_sum, right_sum])
    }

    reduce_range(values, scale, chunk_size, 0, chunk_count)
}

#[cfg(all(test, feature = "parallel"))]
mod deterministic_parallel_norm_sum_tests {
    use super::{compensated_sum, scaled_square_sum_with_executor};
    use crate::{ParallelExecutor, ParallelOptions};

    #[test]
    fn fixed_chunk_scaled_square_sum_is_thread_count_invariant() {
        let values: Vec<f64> = (0..513)
            .map(|index| ((index * 29 + 11) % 137) as f64 / 17.0 - 4.0)
            .collect();
        let scale = values
            .iter()
            .map(|value| value.abs())
            .fold(0.0_f64, f64::max);
        let serial = compensated_sum(values.iter().map(|value| {
            let scaled = *value / scale;
            scaled * scaled
        }));
        let mut reference = None;
        for threads in [2, 3, 4] {
            let executor = ParallelExecutor::new(ParallelOptions {
                threads,
                min_parallel_len: 1,
                reduction_chunk_size: 16,
                ..ParallelOptions::default()
            })
            .unwrap();
            let value = scaled_square_sum_with_executor(&values, scale, &executor);
            match reference {
                Some(bits) => assert_eq!(bits, value.to_bits()),
                None => reference = Some(value.to_bits()),
            }
        }
        let fixed = f64::from_bits(reference.unwrap());
        assert!((fixed - serial).abs() <= 3.0e-13 * (1.0 + serial.abs()));
    }
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

#[cfg(test)]
mod workspace_reuse_tests {
    use super::{PcgWorkspace, solve_pcg_with_workspace};
    use crate::{CmgOptions, CmgPreconditioner, Laplacian, PcgOptions};

    fn fixture() -> (Laplacian, CmgPreconditioner, Vec<f64>) {
        let graph =
            Laplacian::from_edges(128, (0..127).map(|vertex| (vertex, vertex + 1, 1.0))).unwrap();
        let preconditioner = CmgPreconditioner::build(
            &graph,
            CmgOptions {
                direct_threshold: 2,
                ..CmgOptions::default()
            },
        )
        .unwrap();
        let known: Vec<f64> = (0..128).map(|index| (index as f64 / 7.0).cos()).collect();
        let rhs = graph.matvec(&known).unwrap();
        (graph, preconditioner, rhs)
    }

    #[cfg(feature = "parallel")]
    #[test]
    fn required_bytes_matches_allocated_workspace() {
        let (_, preconditioner, _) = fixture();
        assert_eq!(
            PcgWorkspace::required_bytes(&preconditioner),
            PcgWorkspace::new(&preconditioner).byte_len()
        );
    }

    #[test]
    fn solve_overwrites_stale_hot_vectors() {
        let (graph, preconditioner, rhs) = fixture();
        let mut workspace = PcgWorkspace::new(&preconditioner);
        let expected = solve_pcg_with_workspace(
            &graph,
            &preconditioner,
            &rhs,
            PcgOptions::default(),
            &mut workspace,
        )
        .unwrap();
        workspace.preconditioned.fill(f64::NAN);
        workspace.direction.fill(f64::NAN);
        workspace.matrix_direction.fill(f64::NAN);
        let actual = solve_pcg_with_workspace(
            &graph,
            &preconditioner,
            &rhs,
            PcgOptions::default(),
            &mut workspace,
        )
        .unwrap();
        assert_eq!(expected, actual);
    }
}
