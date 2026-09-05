//! Experimental fixed-width independent-RHS PCG implementation.
//!
//! Enable `experimental-fused-rhs` and call this module explicitly. Existing
//! scalar and parallel routing never selects this implementation automatically.
//! Four lanes share graph traversals but retain independent PCG coefficients,
//! convergence checks and diagnostics; this is not block CG or multicore PCG.
//! Only zero initial guesses and graph Laplacians are supported here.
//!
//! Performance depends on graph structure, RHS convergence and hardware. The
//! retained workspace is larger than the scalar batch workspace. Benchmark a
//! representative workload before opting in; see `docs/experimental-fused-rhs.md`.

#![allow(clippy::needless_range_loop)]

mod dispatch;
pub use dispatch::*;

use super::{PcgBatchMut, PcgBatchRef, PcgDiagnostics};
use crate::{
    CmgError, CmgPreconditioner, Components, Laplacian, PcgOptions, TerminalReason,
    ValidationOptions,
};
#[cfg(feature = "profiling")]
use std::time::Instant;

pub(crate) type Lane = [f64; 4];
type Mask = [bool; 4];

#[derive(Debug, Clone, Default)]
struct FusedComponentWorkspace {
    sums: Vec<Lane>,
    corrections: Vec<Lane>,
    means: Vec<Lane>,
    scales: Vec<Lane>,
    scale_corrections: Vec<Lane>,
    projection_corrections: Vec<Lane>,
    representatives: Vec<[usize; 4]>,
}

impl FusedComponentWorkspace {
    fn try_new(count: usize) -> Result<Self, CmgError> {
        Ok(Self {
            sums: try_filled(count, [0.0; 4], "fused component sums")?,
            corrections: try_filled(count, [0.0; 4], "fused component corrections")?,
            means: try_filled(count, [0.0; 4], "fused component means")?,
            scales: try_filled(count, [0.0; 4], "fused component scales")?,
            scale_corrections: try_filled(count, [0.0; 4], "fused component scale corrections")?,
            projection_corrections: try_filled(
                count,
                [0.0; 4],
                "fused component projection corrections",
            )?,
            representatives: try_filled(count, [usize::MAX; 4], "fused component representatives")?,
        })
    }

    fn byte_len(&self) -> usize {
        self.sums
            .capacity()
            .saturating_add(self.corrections.capacity())
            .saturating_add(self.means.capacity())
            .saturating_add(self.scales.capacity())
            .saturating_add(self.scale_corrections.capacity())
            .saturating_add(self.projection_corrections.capacity())
            .saturating_mul(core::mem::size_of::<Lane>())
            .saturating_add(
                self.representatives
                    .capacity()
                    .saturating_mul(core::mem::size_of::<[usize; 4]>()),
            )
    }
}

#[derive(Debug, Clone, Default)]
struct FusedLevelWorkspace {
    residual: Vec<Lane>,
    coarse_rhs: Vec<Lane>,
    coarse_correction: Vec<Lane>,
    factor_forward: Vec<Lane>,
    factor_solution: Vec<Lane>,
}

impl FusedLevelWorkspace {
    fn byte_len(&self) -> usize {
        self.residual
            .capacity()
            .saturating_add(self.coarse_rhs.capacity())
            .saturating_add(self.coarse_correction.capacity())
            .saturating_add(self.factor_forward.capacity())
            .saturating_add(self.factor_solution.capacity())
            .saturating_mul(core::mem::size_of::<Lane>())
    }
}

/// Reusable storage for the experimental four-lane fused PCG path.
#[derive(Debug, Clone)]
pub struct FusedPcgWorkspace4 {
    dimension: usize,
    original_rhs: Vec<Lane>,
    projected_rhs: Vec<Lane>,
    solution: Vec<Lane>,
    residual: Vec<Lane>,
    preconditioned: Vec<Lane>,
    direction: Vec<Lane>,
    matrix_direction: Vec<Lane>,
    components: Vec<Components>,
    component_workspaces: Vec<FusedComponentWorkspace>,
    levels: Vec<FusedLevelWorkspace>,
}

impl FusedPcgWorkspace4 {
    /// Allocate a fused workspace for a fixed preconditioner.
    #[must_use]
    pub fn new(preconditioner: &CmgPreconditioner) -> Self {
        Self::try_new(preconditioner).expect("failed to allocate fused PCG workspace")
    }

    /// Fallibly allocate a fused workspace for a fixed preconditioner.
    pub fn try_new(preconditioner: &CmgPreconditioner) -> Result<Self, CmgError> {
        let hierarchy = preconditioner.hierarchy();
        let mut dimensions = try_filled(hierarchy.levels().len(), 0, "fused dimensions")?;
        for (dimension, level) in dimensions.iter_mut().zip(hierarchy.levels()) {
            *dimension = level.graph().vertex_count();
        }
        let dimension = dimensions.first().copied().unwrap_or(0);
        let mut components = Vec::new();
        components
            .try_reserve_exact(dimensions.len())
            .map_err(|_| CmgError::AllocationFailed {
                context: "fused component metadata",
            })?;
        for level in hierarchy.levels() {
            let graph = level.graph();
            components.push(Components::try_from_endpoints(
                graph.vertex_count(),
                graph.edges().iter().map(|edge| (edge.u(), edge.v())),
            )?);
        }
        let mut component_workspaces = Vec::new();
        component_workspaces
            .try_reserve_exact(components.len())
            .map_err(|_| CmgError::AllocationFailed {
                context: "fused component workspaces",
            })?;
        for metadata in &components {
            component_workspaces.push(FusedComponentWorkspace::try_new(metadata.count())?);
        }

        let last = dimensions.len().saturating_sub(1);
        let mut levels = Vec::new();
        levels
            .try_reserve_exact(dimensions.len())
            .map_err(|_| CmgError::AllocationFailed {
                context: "fused CMG levels",
            })?;
        for (index, &level_dimension) in dimensions.iter().enumerate() {
            let coarse_dimension = dimensions.get(index + 1).copied().unwrap_or(0);
            let factor_dimension = if index == last {
                preconditioner
                    .terminal_factor()
                    .map_or(0, |factor| factor.active_dimension())
            } else {
                0
            };
            levels.push(FusedLevelWorkspace {
                residual: try_lanes(level_dimension, "fused CMG residual")?,
                coarse_rhs: try_lanes(coarse_dimension, "fused CMG coarse RHS")?,
                coarse_correction: try_lanes(coarse_dimension, "fused CMG coarse correction")?,
                factor_forward: try_lanes(factor_dimension, "fused LDL forward")?,
                factor_solution: try_lanes(factor_dimension, "fused LDL solution")?,
            });
        }

        Ok(Self {
            dimension,
            original_rhs: try_lanes(dimension, "fused original RHS")?,
            projected_rhs: try_lanes(dimension, "fused projected RHS")?,
            solution: try_lanes(dimension, "fused solution")?,
            residual: try_lanes(dimension, "fused residual")?,
            preconditioned: try_lanes(dimension, "fused preconditioned vector")?,
            direction: try_lanes(dimension, "fused direction")?,
            matrix_direction: try_lanes(dimension, "fused matrix direction")?,
            components,
            component_workspaces,
            levels,
        })
    }

    /// Return the graph dimension represented by this workspace.
    #[must_use]
    pub const fn dimension(&self) -> usize {
        self.dimension
    }

    /// Return principal retained bytes for packed numerical arrays and metadata.
    #[must_use]
    pub fn byte_len(&self) -> usize {
        let vectors = self
            .dimension
            .saturating_mul(7)
            .saturating_mul(core::mem::size_of::<Lane>());
        let levels = self
            .levels
            .iter()
            .map(FusedLevelWorkspace::byte_len)
            .sum::<usize>();
        let component_scratch = self
            .component_workspaces
            .iter()
            .map(FusedComponentWorkspace::byte_len)
            .sum::<usize>();
        let component_metadata = self
            .components
            .iter()
            .map(|components| {
                components
                    .labels()
                    .len()
                    .saturating_add(components.sizes().len())
                    .saturating_mul(core::mem::size_of::<usize>())
            })
            .sum::<usize>();
        vectors
            .saturating_add(levels)
            .saturating_add(component_scratch)
            .saturating_add(component_metadata)
    }

    fn validate(&self, preconditioner: &CmgPreconditioner) -> Result<(), CmgError> {
        let levels = preconditioner.hierarchy().levels();
        if self.levels.len() != levels.len()
            || self.components.len() != levels.len()
            || self.component_workspaces.len() != levels.len()
        {
            return Err(CmgError::InvalidHierarchy {
                context: "fused workspace hierarchy level mismatch",
            });
        }
        let dimension = levels[0].graph().vertex_count();
        if self.dimension != dimension
            || [
                self.original_rhs.len(),
                self.projected_rhs.len(),
                self.solution.len(),
                self.residual.len(),
                self.preconditioned.len(),
                self.direction.len(),
                self.matrix_direction.len(),
            ]
            .into_iter()
            .any(|actual| actual != dimension)
        {
            return Err(CmgError::InvalidHierarchy {
                context: "fused workspace fine dimension mismatch",
            });
        }
        for (index, level) in levels.iter().enumerate() {
            let level_dimension = level.graph().vertex_count();
            let coarse_dimension = levels
                .get(index + 1)
                .map_or(0, |coarse| coarse.graph().vertex_count());
            let local = &self.levels[index];
            if local.residual.len() != level_dimension
                || local.coarse_rhs.len() != coarse_dimension
                || local.coarse_correction.len() != coarse_dimension
                || self.components[index].labels().len() != level_dimension
                || self.component_workspaces[index].sums.len() != self.components[index].count()
            {
                return Err(CmgError::InvalidHierarchy {
                    context: "fused workspace recursive dimension mismatch",
                });
            }
        }
        Ok(())
    }
}

/// Phase attribution for the experimental fused caller-buffer path.
#[cfg(feature = "profiling")]
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct FusedPcgBatchPhaseProfile {
    validation_nanoseconds: u128,
    gather_nanoseconds: u128,
    solve_nanoseconds: u128,
    scatter_nanoseconds: u128,
    total_nanoseconds: u128,
    groups_by_rhs_count: [usize; 5],
    iterations_by_active_lanes: [usize; 5],
    preconditioner: FusedPcgPhaseSample,
    matvec: FusedPcgPhaseSample,
    residual_recompute: FusedPcgPhaseSample,
}

/// Kernel timing and call counts indexed by the number of active RHS lanes.
///
/// Index zero is unused. Preconditioner timings include recursive CMG work;
/// its internal matrix-vector products are not counted again in the separate
/// finest-level matvec sample. Timings are diagnostic, instrumented measurements.
#[cfg(feature = "profiling")]
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct FusedPcgPhaseSample {
    nanoseconds_by_active_lanes: [u128; 5],
    calls_by_active_lanes: [usize; 5],
}

#[cfg(feature = "profiling")]
impl FusedPcgPhaseSample {
    /// Return wall-clock nanoseconds for calls with zero through four active lanes.
    #[must_use]
    pub const fn nanoseconds_by_active_lanes(self) -> [u128; 5] {
        self.nanoseconds_by_active_lanes
    }

    /// Return call counts with zero through four active lanes.
    #[must_use]
    pub const fn calls_by_active_lanes(self) -> [usize; 5] {
        self.calls_by_active_lanes
    }

    /// Return total measured wall-clock nanoseconds for this kernel.
    #[must_use]
    pub fn nanoseconds(self) -> u128 {
        self.nanoseconds_by_active_lanes.iter().sum()
    }
}

enum FusedPhase {
    Preconditioner,
    Matvec,
    ResidualRecompute,
}

// Static dispatch keeps clock reads and occupancy counters out of ordinary
// solves, even when both profiling and experimental-fused-rhs are enabled.
trait FusedObserver {
    type Stamp;
    fn start(&self) -> Self::Stamp;
    fn finish(&mut self, phase: FusedPhase, active: &Mask, stamp: Self::Stamp);
    fn iteration(&mut self, active: &Mask);
}

impl FusedObserver for () {
    type Stamp = ();
    #[inline(always)]
    fn start(&self) {}
    #[inline(always)]
    fn finish(&mut self, _: FusedPhase, _: &Mask, _: ()) {}
    #[inline(always)]
    fn iteration(&mut self, _: &Mask) {}
}

#[cfg(feature = "profiling")]
impl FusedObserver for FusedPcgBatchPhaseProfile {
    type Stamp = Instant;

    fn start(&self) -> Instant {
        Instant::now()
    }

    fn finish(&mut self, phase: FusedPhase, active: &Mask, stamp: Instant) {
        let elapsed = stamp.elapsed().as_nanos();
        let lanes = active.iter().filter(|&&value| value).count();
        let sample = match phase {
            FusedPhase::Preconditioner => &mut self.preconditioner,
            FusedPhase::Matvec => &mut self.matvec,
            FusedPhase::ResidualRecompute => &mut self.residual_recompute,
        };
        sample.nanoseconds_by_active_lanes[lanes] += elapsed;
        sample.calls_by_active_lanes[lanes] += 1;
    }

    fn iteration(&mut self, active: &Mask) {
        self.iterations_by_active_lanes[active.iter().filter(|&&value| value).count()] += 1;
    }
}

#[cfg(feature = "profiling")]
impl FusedPcgBatchPhaseProfile {
    /// Return validation time.
    #[must_use]
    pub const fn validation_nanoseconds(self) -> u128 {
        self.validation_nanoseconds
    }
    /// Return RHS packing time.
    #[must_use]
    pub const fn gather_nanoseconds(self) -> u128 {
        self.gather_nanoseconds
    }
    /// Return fused numerical solve time.
    #[must_use]
    pub const fn solve_nanoseconds(self) -> u128 {
        self.solve_nanoseconds
    }
    /// Return solution scatter time.
    #[must_use]
    pub const fn scatter_nanoseconds(self) -> u128 {
        self.scatter_nanoseconds
    }
    /// Return complete call time.
    #[must_use]
    pub const fn total_nanoseconds(self) -> u128 {
        self.total_nanoseconds
    }

    /// Return the number of groups containing zero through four submitted RHS.
    ///
    /// Counts include groups whose RHS converge immediately, before iteration.
    #[must_use]
    pub const fn groups_by_rhs_count(self) -> [usize; 5] {
        self.groups_by_rhs_count
    }

    /// Return iteration counts by active lane count at the start of each step.
    ///
    /// This counts group iterations, not iterations summed across individual
    /// RHS. Index zero is always zero. Zero-iteration RHS do not contribute.
    #[must_use]
    pub const fn iterations_by_active_lanes(self) -> [usize; 5] {
        self.iterations_by_active_lanes
    }

    /// Return the number of active RHS-iteration pairs across all groups.
    ///
    /// On success this equals the sum of iterations in the per-RHS diagnostics.
    #[must_use]
    pub fn active_lane_iterations(self) -> usize {
        self.iterations_by_active_lanes
            .iter()
            .enumerate()
            .map(|(lanes, iterations)| lanes * iterations)
            .sum()
    }

    /// Return four times the number of group iterations, including tail capacity.
    ///
    /// Divide active lane iterations by this value to obtain iteration-weighted
    /// occupancy. For all-zero or empty batches this value is zero; occupancy is
    /// undefined. It is not a measurement of SIMD instruction utilization.
    #[must_use]
    pub fn lane_iteration_capacity(self) -> usize {
        4 * self.iterations_by_active_lanes.iter().sum::<usize>()
    }

    /// Return inclusive CMG application timing and lane counts.
    #[must_use]
    pub const fn preconditioner(self) -> FusedPcgPhaseSample {
        self.preconditioner
    }

    /// Return direction matvec timing at the finest level, once per group step.
    #[must_use]
    pub const fn matvec(self) -> FusedPcgPhaseSample {
        self.matvec
    }

    /// Return residual replacement and certification timing with their lane counts.
    ///
    /// Includes fresh residual matvecs, subtraction, norms and convergence checks,
    /// plus the final residual check on iteration exhaustion. Excludes CMG work.
    #[must_use]
    pub const fn residual_recompute(self) -> FusedPcgPhaseSample {
        self.residual_recompute
    }

    /// Return solve time outside the three measured kernels, including overhead.
    ///
    /// Covers initial projection, reductions, centering, vector updates, control
    /// flow and instrumentation. This is a residual, not a separately timed phase.
    #[must_use]
    pub fn other_solve_nanoseconds(self) -> u128 {
        self.solve_nanoseconds.saturating_sub(
            self.preconditioner.nanoseconds()
                + self.matvec.nanoseconds()
                + self.residual_recompute.nanoseconds(),
        )
    }
}

/// Solve arbitrary many zero-start right-hand sides in independent groups of four.
///
/// Accepts contiguous or strided input and output views, including empty batches
/// and incomplete final groups. Reuses the caller's workspace. Each RHS retains
/// scalar PCG arithmetic order and certification. Successful results and
/// diagnostics before the first failing RHS are written in input order; the
/// failing RHS and later outputs are left untouched. Input views, graph identity
/// and workspace compatibility are checked before outputs are changed.
///
/// The workspace is not governed by the parallel solver's memory budget. Use
/// [`FusedPcgWorkspace4::try_new`] for fallible allocation and inspect
/// [`FusedPcgWorkspace4::byte_len`] when deciding whether to retain it.
#[allow(clippy::too_many_arguments)]
pub fn solve_pcg_batch_fused_width4_into_with_workspace(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    right_hand_sides: PcgBatchRef<'_>,
    mut solutions: PcgBatchMut<'_>,
    diagnostics: &mut [PcgDiagnostics],
    options: PcgOptions,
    workspace: &mut FusedPcgWorkspace4,
) -> Result<(), CmgError> {
    validate_batch(
        graph,
        preconditioner,
        right_hand_sides,
        &solutions,
        diagnostics.len(),
        workspace,
    )?;
    if right_hand_sides.rhs_count == 0 {
        return Ok(());
    }
    let options = options.validate()?;
    for start in (0..right_hand_sides.rhs_count).step_by(4) {
        let count = 4.min(right_hand_sides.rhs_count - start);
        gather_group(right_hand_sides, start, count, workspace);
        let outcomes = solve_group(graph, preconditioner, count, options, workspace, &mut ())?;
        for lane in 0..count {
            match &outcomes[lane] {
                Ok(diagnostic) => {
                    diagnostics[start + lane] = *diagnostic;
                    scatter_lane(&mut solutions, start + lane, lane, &workspace.solution);
                }
                Err(error) => return Err(error.clone()),
            }
        }
    }
    Ok(())
}

/// Profile the exact experimental fused caller-buffer path.
///
/// Collects occupancy and kernel timings on a separate instrumented call. Use
/// the ordinary solve entrypoint for performance ratios: profiling introduces
/// clock and counting overhead, while retaining the same numerical operations.
#[cfg(feature = "profiling")]
#[allow(clippy::too_many_arguments)]
pub fn profile_pcg_batch_fused_width4_into_with_workspace(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    right_hand_sides: PcgBatchRef<'_>,
    mut solutions: PcgBatchMut<'_>,
    diagnostics: &mut [PcgDiagnostics],
    options: PcgOptions,
    workspace: &mut FusedPcgWorkspace4,
) -> Result<FusedPcgBatchPhaseProfile, CmgError> {
    let total = Instant::now();
    let validation = Instant::now();
    validate_batch(
        graph,
        preconditioner,
        right_hand_sides,
        &solutions,
        diagnostics.len(),
        workspace,
    )?;
    let validation_nanoseconds = validation.elapsed().as_nanos();
    if right_hand_sides.rhs_count == 0 {
        return Ok(FusedPcgBatchPhaseProfile {
            validation_nanoseconds,
            total_nanoseconds: total.elapsed().as_nanos(),
            ..FusedPcgBatchPhaseProfile::default()
        });
    }
    let options = options.validate()?;
    let mut gather_nanoseconds = 0;
    let mut solve_nanoseconds = 0;
    let mut scatter_nanoseconds = 0;
    let mut profile = FusedPcgBatchPhaseProfile::default();
    for start in (0..right_hand_sides.rhs_count).step_by(4) {
        let count = 4.min(right_hand_sides.rhs_count - start);
        profile.groups_by_rhs_count[count] += 1;
        let timer = Instant::now();
        gather_group(right_hand_sides, start, count, workspace);
        gather_nanoseconds += timer.elapsed().as_nanos();
        let timer = Instant::now();
        let outcomes = solve_group(
            graph,
            preconditioner,
            count,
            options,
            workspace,
            &mut profile,
        )?;
        solve_nanoseconds += timer.elapsed().as_nanos();
        for lane in 0..count {
            match &outcomes[lane] {
                Ok(diagnostic) => {
                    diagnostics[start + lane] = *diagnostic;
                    let timer = Instant::now();
                    scatter_lane(&mut solutions, start + lane, lane, &workspace.solution);
                    scatter_nanoseconds += timer.elapsed().as_nanos();
                }
                Err(error) => return Err(error.clone()),
            }
        }
    }
    Ok(FusedPcgBatchPhaseProfile {
        validation_nanoseconds,
        gather_nanoseconds,
        solve_nanoseconds,
        scatter_nanoseconds,
        total_nanoseconds: total.elapsed().as_nanos(),
        ..profile
    })
}

fn validate_batch(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    rhs: PcgBatchRef<'_>,
    solutions: &PcgBatchMut<'_>,
    diagnostic_count: usize,
    workspace: &FusedPcgWorkspace4,
) -> Result<(), CmgError> {
    if !preconditioner.matches_graph(graph) {
        return Err(CmgError::InvalidHierarchy {
            context: "PCG batch graph and preconditioner are incompatible",
        });
    }
    let dimension = graph.vertex_count();
    for (context, actual) in [
        ("PCG batch RHS dimension", rhs.dimension),
        ("PCG batch solution dimension", solutions.dimension),
    ] {
        if actual != dimension {
            return Err(CmgError::dimension(context, dimension, actual));
        }
    }
    if solutions.rhs_count != rhs.rhs_count {
        return Err(CmgError::dimension(
            "PCG batch solution count",
            rhs.rhs_count,
            solutions.rhs_count,
        ));
    }
    if diagnostic_count != rhs.rhs_count {
        return Err(CmgError::dimension(
            "PCG batch diagnostic count",
            rhs.rhs_count,
            diagnostic_count,
        ));
    }
    workspace.validate(preconditioner)
}

fn gather_group(
    rhs: PcgBatchRef<'_>,
    start: usize,
    count: usize,
    workspace: &mut FusedPcgWorkspace4,
) {
    for vertex in 0..workspace.dimension {
        for lane in 0..count {
            let value = rhs.data[(start + lane) * rhs.rhs_stride + vertex * rhs.value_stride];
            workspace.original_rhs[vertex][lane] = value;
            workspace.projected_rhs[vertex][lane] = value;
        }
    }
}

fn scatter_lane(solutions: &mut PcgBatchMut<'_>, rhs: usize, lane: usize, packed: &[Lane]) {
    for (vertex, values) in packed.iter().enumerate() {
        solutions.data[rhs * solutions.rhs_stride + vertex * solutions.value_stride] = values[lane];
    }
}

fn try_lanes(len: usize, context: &'static str) -> Result<Vec<Lane>, CmgError> {
    try_filled(len, [0.0; 4], context)
}

fn try_filled<T: Clone>(len: usize, value: T, context: &'static str) -> Result<Vec<T>, CmgError> {
    let mut values = Vec::new();
    values
        .try_reserve_exact(len)
        .map_err(|_| CmgError::AllocationFailed { context })?;
    values.resize(len, value);
    Ok(values)
}

fn solve_group<Observer: FusedObserver>(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    count: usize,
    options: PcgOptions,
    workspace: &mut FusedPcgWorkspace4,
    observer: &mut Observer,
) -> Result<[Result<PcgDiagnostics, CmgError>; 4], CmgError> {
    let mut active = std::array::from_fn(|lane| lane < count);
    let mut outcomes: [Option<Result<PcgDiagnostics, CmgError>>; 4] = std::array::from_fn(|_| None);
    let (projection_norms, projection_errors) = project_rhs_width4(
        &mut workspace.projected_rhs,
        &workspace.components[0],
        options.validation,
        &mut workspace.component_workspaces[0],
        &active,
    );
    for lane in 0..count {
        if let Some(error) = projection_errors[lane].clone() {
            active[lane] = false;
            outcomes[lane] = Some(Err(error));
        }
    }

    for vertex in 0..workspace.dimension {
        for lane in 0..count {
            if active[lane] {
                workspace.solution[vertex][lane] = 0.0;
                workspace.residual[vertex][lane] = workspace.projected_rhs[vertex][lane];
            }
        }
    }
    let rhs_norms = norms_width4(&workspace.original_rhs, &active);
    let initial_residual_norms = rhs_norms;
    let projected_initial_norms = norms_width4(&workspace.projected_rhs, &active);
    let operator_bound = graph.operator_norm_bound();
    let mut last_tolerances = [0.0; 4];
    for lane in 0..count {
        if !active[lane] {
            continue;
        }
        last_tolerances[lane] = options.absolute_tolerance
            + options.relative_tolerance * (rhs_norms[lane] + operator_bound * 0.0);
        if initial_residual_norms[lane] <= last_tolerances[lane] {
            outcomes[lane] = Some(Ok(make_diagnostics_width4(
                &workspace.solution,
                lane,
                0,
                initial_residual_norms[lane],
                initial_residual_norms[lane],
                rhs_norms[lane],
                last_tolerances[lane],
                operator_bound,
                0,
                projection_norms[lane],
            )));
            active[lane] = false;
        } else if projected_initial_norms[lane] == 0.0 {
            outcomes[lane] = Some(Err(CmgError::ResidualVerificationFailed {
                iteration: 0,
                residual_norm: initial_residual_norms[lane],
                tolerance: last_tolerances[lane],
            }));
            active[lane] = false;
        }
    }

    if active.iter().any(|value| *value) {
        let stamp = observer.start();
        fused_preconditioner_apply(
            preconditioner,
            &workspace.residual,
            &mut workspace.preconditioned,
            &mut workspace.levels,
            &workspace.components,
            &mut workspace.component_workspaces,
            &active,
        )?;
        observer.finish(FusedPhase::Preconditioner, &active, stamp);
        center_width4(
            &mut workspace.preconditioned,
            &workspace.components[0],
            &mut workspace.component_workspaces[0],
            &active,
        )?;
    }
    let mut rho = dots_width4(&workspace.residual, &workspace.preconditioned, &active);
    for lane in 0..count {
        if active[lane] {
            if let Err(error) = validate_positive_width4(0, "r^T M r", rho[lane]) {
                outcomes[lane] = Some(Err(error));
                active[lane] = false;
            }
        }
    }
    for vertex in 0..workspace.dimension {
        for lane in 0..count {
            if active[lane] {
                workspace.direction[vertex][lane] = workspace.preconditioned[vertex][lane];
            }
        }
    }

    let mut restarts = [0_usize; 4];
    for iteration in 1..=options.max_iterations {
        if !active.iter().any(|value| *value) {
            break;
        }
        observer.iteration(&active);
        let stamp = observer.start();
        matvec_width4(
            graph,
            &workspace.direction,
            &mut workspace.matrix_direction,
            &active,
        )?;
        observer.finish(FusedPhase::Matvec, &active, stamp);
        let curvature = dots_width4(&workspace.direction, &workspace.matrix_direction, &active);
        let mut alpha = [0.0; 4];
        for lane in 0..count {
            if !active[lane] {
                continue;
            }
            if let Err(error) = validate_positive_width4(iteration, "p^T A p", curvature[lane]) {
                outcomes[lane] = Some(Err(error));
                active[lane] = false;
                continue;
            }
            alpha[lane] = rho[lane] / curvature[lane];
            if let Err(error) = validate_finite_width4(iteration, "alpha", alpha[lane]) {
                outcomes[lane] = Some(Err(error));
                active[lane] = false;
            }
        }
        for vertex in 0..workspace.dimension {
            for lane in 0..count {
                if active[lane] {
                    workspace.solution[vertex][lane] +=
                        alpha[lane] * workspace.direction[vertex][lane];
                    workspace.residual[vertex][lane] -=
                        alpha[lane] * workspace.matrix_direction[vertex][lane];
                }
            }
        }
        center_width4(
            &mut workspace.solution,
            &workspace.components[0],
            &mut workspace.component_workspaces[0],
            &active,
        )?;

        let solution_norms = norms_width4(&workspace.solution, &active);
        let recursive_norms = norms_width4(&workspace.residual, &active);
        let mut recompute = [false; 4];
        for lane in 0..count {
            if active[lane] {
                last_tolerances[lane] = options.absolute_tolerance
                    + options.relative_tolerance
                        * (rhs_norms[lane] + operator_bound * solution_norms[lane]);
                recompute[lane] = recursive_norms[lane] <= last_tolerances[lane]
                    || iteration % options.residual_recompute_interval == 0;
            }
        }
        if recompute.iter().any(|value| *value) {
            let stamp = observer.start();
            matvec_width4(
                graph,
                &workspace.solution,
                &mut workspace.matrix_direction,
                &recompute,
            )?;
            for vertex in 0..workspace.dimension {
                for lane in 0..count {
                    if recompute[lane] {
                        workspace.matrix_direction[vertex][lane] = workspace.projected_rhs[vertex]
                            [lane]
                            - workspace.matrix_direction[vertex][lane];
                        workspace.residual[vertex][lane] = workspace.matrix_direction[vertex][lane];
                    }
                }
            }
            let fresh_norms = norms_width4(&workspace.matrix_direction, &recompute);
            for lane in 0..count {
                if !recompute[lane] {
                    continue;
                }
                restarts[lane] += 1;
                if fresh_norms[lane] <= last_tolerances[lane] {
                    let original_norm = original_residual_norm_width4(
                        &workspace.original_rhs,
                        &workspace.projected_rhs,
                        &workspace.matrix_direction,
                        lane,
                    );
                    if original_norm <= last_tolerances[lane] {
                        outcomes[lane] = Some(Ok(make_diagnostics_width4(
                            &workspace.solution,
                            lane,
                            iteration,
                            initial_residual_norms[lane],
                            original_norm,
                            rhs_norms[lane],
                            last_tolerances[lane],
                            operator_bound,
                            restarts[lane],
                            projection_norms[lane],
                        )));
                        active[lane] = false;
                    } else if iteration == options.max_iterations {
                        outcomes[lane] = Some(Err(CmgError::ResidualVerificationFailed {
                            iteration,
                            residual_norm: original_norm,
                            tolerance: last_tolerances[lane],
                        }));
                        active[lane] = false;
                    }
                }
            }
            observer.finish(FusedPhase::ResidualRecompute, &recompute, stamp);
        }
        if iteration == options.max_iterations || !active.iter().any(|value| *value) {
            break;
        }

        center_width4(
            &mut workspace.residual,
            &workspace.components[0],
            &mut workspace.component_workspaces[0],
            &active,
        )?;
        let stamp = observer.start();
        fused_preconditioner_apply(
            preconditioner,
            &workspace.residual,
            &mut workspace.preconditioned,
            &mut workspace.levels,
            &workspace.components,
            &mut workspace.component_workspaces,
            &active,
        )?;
        observer.finish(FusedPhase::Preconditioner, &active, stamp);
        center_width4(
            &mut workspace.preconditioned,
            &workspace.components[0],
            &mut workspace.component_workspaces[0],
            &active,
        )?;
        let new_rho = dots_width4(&workspace.residual, &workspace.preconditioned, &active);
        let mut beta = [0.0; 4];
        for lane in 0..count {
            if !active[lane] {
                continue;
            }
            if let Err(error) = validate_positive_width4(iteration, "new r^T M r", new_rho[lane]) {
                outcomes[lane] = Some(Err(error));
                active[lane] = false;
                continue;
            }
            if !recompute[lane] {
                beta[lane] = new_rho[lane] / rho[lane];
                if let Err(error) = validate_finite_width4(iteration, "beta", beta[lane]) {
                    outcomes[lane] = Some(Err(error));
                    active[lane] = false;
                }
            }
        }
        for vertex in 0..workspace.dimension {
            for lane in 0..count {
                if active[lane] {
                    workspace.direction[vertex][lane] = if recompute[lane] {
                        workspace.preconditioned[vertex][lane]
                    } else {
                        workspace.preconditioned[vertex][lane]
                            + beta[lane] * workspace.direction[vertex][lane]
                    };
                }
            }
        }
        rho = new_rho;
    }

    if active.iter().any(|value| *value) {
        let stamp = observer.start();
        matvec_width4(
            graph,
            &workspace.solution,
            &mut workspace.matrix_direction,
            &active,
        )?;
        for vertex in 0..workspace.dimension {
            for lane in 0..count {
                if active[lane] {
                    workspace.matrix_direction[vertex][lane] = workspace.projected_rhs[vertex]
                        [lane]
                        - workspace.matrix_direction[vertex][lane];
                }
            }
        }
        for lane in 0..count {
            if active[lane] {
                let residual_norm = original_residual_norm_width4(
                    &workspace.original_rhs,
                    &workspace.projected_rhs,
                    &workspace.matrix_direction,
                    lane,
                );
                outcomes[lane] = Some(Err(CmgError::MaximumIterations {
                    iterations: options.max_iterations,
                    residual_norm,
                    tolerance: last_tolerances[lane],
                }));
            }
        }
        observer.finish(FusedPhase::ResidualRecompute, &active, stamp);
    }

    Ok(std::array::from_fn(|lane| {
        outcomes[lane].take().unwrap_or({
            Err(CmgError::InvalidHierarchy {
                context: "inactive fused PCG lane has no outcome",
            })
        })
    }))
}

fn validate_positive_width4(
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

fn validate_finite_width4(
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

#[allow(clippy::too_many_arguments)]
fn make_diagnostics_width4(
    solution: &[Lane],
    lane: usize,
    iterations: usize,
    initial_residual_norm: f64,
    residual_norm: f64,
    rhs_norm: f64,
    tolerance: f64,
    operator_bound: f64,
    restarts: usize,
    rhs_projection_norm: f64,
) -> PcgDiagnostics {
    let solution_norm = norm_lane(solution, lane);
    let denominator = rhs_norm + operator_bound * solution_norm;
    PcgDiagnostics {
        iterations,
        initial_residual_norm,
        residual_norm,
        relative_residual: if rhs_norm > 0.0 {
            residual_norm / rhs_norm
        } else {
            residual_norm
        },
        backward_error: if denominator > 0.0 {
            residual_norm / denominator
        } else {
            0.0
        },
        tolerance,
        restarts,
        rhs_projection_norm,
    }
}

fn neumaier_add(sum: &mut f64, correction: &mut f64, value: f64) {
    let next = *sum + value;
    *correction += if sum.abs() >= value.abs() {
        (*sum - next) + value
    } else {
        (value - next) + *sum
    };
    *sum = next;
}

fn project_rhs_width4(
    values: &mut [Lane],
    components: &Components,
    options: ValidationOptions,
    workspace: &mut FusedComponentWorkspace,
    requested: &Mask,
) -> ([f64; 4], [Option<CmgError>; 4]) {
    let mut active = *requested;
    let mut errors: [Option<CmgError>; 4] = std::array::from_fn(|_| None);
    workspace.sums.fill([0.0; 4]);
    workspace.corrections.fill([0.0; 4]);
    workspace.scales.fill([0.0; 4]);
    workspace.scale_corrections.fill([0.0; 4]);
    for (vertex, (&label, lanes)) in components.labels().iter().zip(values.iter()).enumerate() {
        for lane in 0..4 {
            if !active[lane] {
                continue;
            }
            let value = lanes[lane];
            if !value.is_finite() {
                errors[lane] = Some(CmgError::NonFiniteMatrixValue {
                    row: vertex,
                    column: 0,
                    value,
                });
                active[lane] = false;
                continue;
            }
            neumaier_add(
                &mut workspace.sums[label][lane],
                &mut workspace.corrections[label][lane],
                value,
            );
            neumaier_add(
                &mut workspace.scales[label][lane],
                &mut workspace.scale_corrections[label][lane],
                value.abs(),
            );
        }
    }
    for component in 0..components.count() {
        for lane in 0..4 {
            if active[lane] {
                workspace.sums[component][lane] += workspace.corrections[component][lane];
                workspace.scales[component][lane] += workspace.scale_corrections[component][lane];
            }
        }
    }
    for lane in 0..4 {
        if !active[lane] {
            continue;
        }
        for component in 0..components.count() {
            let sum = workspace.sums[component][lane];
            let tolerance =
                options.compatibility_tolerance * workspace.scales[component][lane].max(1.0);
            if sum.abs() > tolerance {
                errors[lane] = Some(CmgError::IncompatibleLaplacianRhs {
                    component,
                    sum,
                    tolerance,
                });
                active[lane] = false;
                break;
            }
        }
    }
    for component in 0..components.count() {
        for lane in 0..4 {
            if active[lane] {
                workspace.means[component][lane] =
                    workspace.sums[component][lane] / components.sizes()[component] as f64;
            }
        }
    }
    for (&label, lanes) in components.labels().iter().zip(values.iter_mut()) {
        for lane in 0..4 {
            if active[lane] {
                lanes[lane] -= workspace.means[label][lane];
            }
        }
    }
    workspace.representatives.fill([usize::MAX; 4]);
    for (vertex, (&label, lanes)) in components.labels().iter().zip(values.iter()).enumerate() {
        for lane in 0..4 {
            if !active[lane] {
                continue;
            }
            let current = workspace.representatives[label][lane];
            if current == usize::MAX
                || lanes[lane].abs() < values[current][lane].abs()
                || (lanes[lane].abs() == values[current][lane].abs() && vertex < current)
            {
                workspace.representatives[label][lane] = vertex;
            }
        }
    }
    workspace.projection_corrections.fill([0.0; 4]);
    for _ in 0..2 {
        workspace.sums.fill([0.0; 4]);
        workspace.corrections.fill([0.0; 4]);
        for (&label, lanes) in components.labels().iter().zip(values.iter()) {
            for lane in 0..4 {
                if active[lane] {
                    neumaier_add(
                        &mut workspace.sums[label][lane],
                        &mut workspace.corrections[label][lane],
                        lanes[lane],
                    );
                }
            }
        }
        for component in 0..components.count() {
            for lane in 0..4 {
                if active[lane] {
                    workspace.sums[component][lane] += workspace.corrections[component][lane];
                    let residual_sum = workspace.sums[component][lane];
                    let representative = workspace.representatives[component][lane];
                    values[representative][lane] -= residual_sum;
                    workspace.projection_corrections[component][lane] += residual_sum;
                }
            }
        }
    }
    let mut projection_norms = [0.0; 4];
    for lane in 0..4 {
        if !active[lane] {
            continue;
        }
        let mut scale = 0.0_f64;
        for component in 0..components.count() {
            let mean = workspace.means[component][lane];
            let correction = workspace.projection_corrections[component][lane];
            scale = scale.max(mean.abs()).max((mean + correction).abs());
        }
        if scale != 0.0 {
            let mut squared = 0.0;
            for component in 0..components.count() {
                let mean = workspace.means[component][lane];
                let correction = workspace.projection_corrections[component][lane];
                let regular = mean / scale;
                let representative = (mean + correction) / scale;
                squared += (components.sizes()[component] - 1) as f64 * regular * regular
                    + representative * representative;
            }
            projection_norms[lane] = scale * squared.sqrt();
        }
    }
    (projection_norms, errors)
}

fn center_width4(
    values: &mut [Lane],
    components: &Components,
    workspace: &mut FusedComponentWorkspace,
    active: &Mask,
) -> Result<(), CmgError> {
    if values.len() != components.labels().len() {
        return Err(CmgError::InvalidHierarchy {
            context: "fused centering dimension mismatch",
        });
    }
    workspace.sums.fill([0.0; 4]);
    workspace.corrections.fill([0.0; 4]);
    for (vertex, (&label, lanes)) in components.labels().iter().zip(values.iter()).enumerate() {
        for lane in 0..4 {
            if active[lane] {
                let value = lanes[lane];
                if !value.is_finite() {
                    return Err(CmgError::NonFiniteMatrixValue {
                        row: vertex,
                        column: 0,
                        value,
                    });
                }
                neumaier_add(
                    &mut workspace.sums[label][lane],
                    &mut workspace.corrections[label][lane],
                    value,
                );
            }
        }
    }
    for component in 0..components.count() {
        for lane in 0..4 {
            if active[lane] {
                workspace.sums[component][lane] += workspace.corrections[component][lane];
                workspace.means[component][lane] =
                    workspace.sums[component][lane] / components.sizes()[component] as f64;
            }
        }
    }
    for (&label, lanes) in components.labels().iter().zip(values.iter_mut()) {
        for lane in 0..4 {
            if active[lane] {
                lanes[lane] -= workspace.means[label][lane];
            }
        }
    }
    Ok(())
}

fn matvec_width4(
    graph: &Laplacian,
    input: &[Lane],
    output: &mut [Lane],
    active: &Mask,
) -> Result<(), CmgError> {
    let dimension = graph.vertex_count();
    if input.len() != dimension || output.len() != dimension {
        return Err(CmgError::InvalidHierarchy {
            context: "fused matvec dimension mismatch",
        });
    }
    for values in output.iter_mut() {
        for lane in 0..4 {
            if active[lane] {
                values[lane] = 0.0;
            }
        }
    }
    for edge in graph.edges() {
        let u = edge.u();
        let v = edge.v();
        let weight = edge.weight();
        for lane in 0..4 {
            if active[lane] {
                let difference = weight * (input[u][lane] - input[v][lane]);
                output[u][lane] += difference;
                output[v][lane] -= difference;
            }
        }
    }
    Ok(())
}

fn dots_width4(left: &[Lane], right: &[Lane], active: &Mask) -> Lane {
    let mut sums = [0.0; 4];
    let mut corrections = [0.0; 4];
    for (left, right) in left.iter().zip(right) {
        for lane in 0..4 {
            if active[lane] {
                neumaier_add(
                    &mut sums[lane],
                    &mut corrections[lane],
                    left[lane] * right[lane],
                );
            }
        }
    }
    for lane in 0..4 {
        if active[lane] {
            sums[lane] += corrections[lane];
        }
    }
    sums
}

fn norms_width4(values: &[Lane], active: &Mask) -> Lane {
    let mut scales = [0.0_f64; 4];
    for values in values {
        for lane in 0..4 {
            if active[lane] {
                scales[lane] = scales[lane].max(values[lane].abs());
            }
        }
    }
    let mut sums = [0.0; 4];
    let mut corrections = [0.0; 4];
    for values in values {
        for lane in 0..4 {
            if active[lane] && scales[lane] != 0.0 {
                let scaled = values[lane] / scales[lane];
                neumaier_add(&mut sums[lane], &mut corrections[lane], scaled * scaled);
            }
        }
    }
    for lane in 0..4 {
        if active[lane] && scales[lane] != 0.0 {
            sums[lane] = scales[lane] * (sums[lane] + corrections[lane]).sqrt();
        }
    }
    sums
}

fn norm_lane(values: &[Lane], lane: usize) -> f64 {
    let scale = values
        .iter()
        .map(|values| values[lane].abs())
        .fold(0.0, f64::max);
    if scale == 0.0 {
        return 0.0;
    }
    let mut sum = 0.0;
    let mut correction = 0.0;
    for values in values {
        let scaled = values[lane] / scale;
        neumaier_add(&mut sum, &mut correction, scaled * scaled);
    }
    scale * (sum + correction).sqrt()
}

fn original_residual_norm_width4(
    original_rhs: &[Lane],
    projected_rhs: &[Lane],
    projected_residual: &[Lane],
    lane: usize,
) -> f64 {
    let mut scale = 0.0_f64;
    for ((original, projected), residual) in original_rhs
        .iter()
        .zip(projected_rhs)
        .zip(projected_residual)
    {
        scale = scale.max((residual[lane] + (original[lane] - projected[lane])).abs());
    }
    if scale == 0.0 {
        return 0.0;
    }
    let mut sum = 0.0;
    let mut correction = 0.0;
    for ((original, projected), residual) in original_rhs
        .iter()
        .zip(projected_rhs)
        .zip(projected_residual)
    {
        let value = residual[lane] + (original[lane] - projected[lane]);
        let scaled = value / scale;
        neumaier_add(&mut sum, &mut correction, scaled * scaled);
    }
    scale * (sum + correction).sqrt()
}

fn fused_preconditioner_apply(
    preconditioner: &CmgPreconditioner,
    rhs: &[Lane],
    output: &mut [Lane],
    levels: &mut [FusedLevelWorkspace],
    components: &[Components],
    component_workspaces: &mut [FusedComponentWorkspace],
    active: &Mask,
) -> Result<(), CmgError> {
    fused_apply_level(
        preconditioner,
        0,
        rhs,
        output,
        levels,
        components,
        component_workspaces,
        active,
        1,
    )
}

#[allow(clippy::too_many_arguments)]
fn fused_apply_level(
    preconditioner: &CmgPreconditioner,
    level_index: usize,
    rhs: &[Lane],
    output: &mut [Lane],
    levels: &mut [FusedLevelWorkspace],
    components: &[Components],
    component_workspaces: &mut [FusedComponentWorkspace],
    active: &Mask,
    iterations: usize,
) -> Result<(), CmgError> {
    let hierarchy_levels = preconditioner.hierarchy().levels();
    let level = &hierarchy_levels[level_index];
    let dimension = level.graph().vertex_count();
    if rhs.len() != dimension || output.len() != dimension {
        return Err(CmgError::InvalidHierarchy {
            context: "fused recursive vector dimension mismatch",
        });
    }
    if let Some(reason) = level.terminal_reason() {
        if reason == TerminalReason::Direct {
            let factor = preconditioner
                .terminal_factor()
                .ok_or(CmgError::InvalidHierarchy {
                    context: "direct terminal is missing its LDL factor",
                })?;
            let mut local = core::mem::take(&mut levels[level_index]);
            let result = factor.solve_width4_into_compatible(
                rhs,
                output,
                &mut local.factor_forward,
                &mut local.factor_solution,
                active,
            );
            levels[level_index] = local;
            return result;
        }
        for vertex in 0..dimension {
            let inverse_diagonal = level.inverse_diagonal()[vertex];
            for lane in 0..4 {
                if active[lane] {
                    output[vertex][lane] = inverse_diagonal * rhs[vertex][lane];
                }
            }
        }
        return Ok(());
    }

    let aggregation = level.aggregation().ok_or(CmgError::InvalidHierarchy {
        context: "nonterminal level has no aggregation",
    })?;
    if iterations == 0 {
        return Err(CmgError::InvalidHierarchy {
            context: "nonterminal level has zero stationary iterations",
        });
    }
    let child_iterations = preconditioner.repeat_counts()[level_index];
    if child_iterations == 0 {
        return Err(CmgError::InvalidHierarchy {
            context: "nonterminal level has zero child recursive repeats",
        });
    }

    let mut local = core::mem::take(&mut levels[level_index]);
    let result = (|| {
        for iteration in 0..iterations {
            if iteration == 0 {
                for vertex in 0..dimension {
                    let inverse_diagonal = level.inverse_diagonal()[vertex];
                    for lane in 0..4 {
                        if active[lane] {
                            output[vertex][lane] = inverse_diagonal * rhs[vertex][lane];
                        }
                    }
                }
            } else {
                matvec_width4(level.graph(), output, &mut local.residual, active)?;
                for vertex in 0..dimension {
                    let inverse_diagonal = level.inverse_diagonal()[vertex];
                    for lane in 0..4 {
                        if active[lane] {
                            output[vertex][lane] += inverse_diagonal
                                * (rhs[vertex][lane] - local.residual[vertex][lane]);
                        }
                    }
                }
            }

            matvec_width4(level.graph(), output, &mut local.residual, active)?;
            for vertex in 0..dimension {
                for lane in 0..4 {
                    if active[lane] {
                        local.residual[vertex][lane] =
                            rhs[vertex][lane] - local.residual[vertex][lane];
                    }
                }
            }
            restrict_width4(aggregation, &local.residual, &mut local.coarse_rhs, active)?;
            center_width4(
                &mut local.coarse_rhs,
                &components[level_index + 1],
                &mut component_workspaces[level_index + 1],
                active,
            )?;
            fused_apply_level(
                preconditioner,
                level_index + 1,
                &local.coarse_rhs,
                &mut local.coarse_correction,
                levels,
                components,
                component_workspaces,
                active,
                child_iterations,
            )?;
            prolong_add_width4(aggregation, &local.coarse_correction, output, active)?;
            matvec_width4(level.graph(), output, &mut local.residual, active)?;
            for vertex in 0..dimension {
                let inverse_diagonal = level.inverse_diagonal()[vertex];
                for lane in 0..4 {
                    if active[lane] {
                        output[vertex][lane] +=
                            inverse_diagonal * (rhs[vertex][lane] - local.residual[vertex][lane]);
                    }
                }
            }
        }
        Ok(())
    })();
    levels[level_index] = local;
    result
}

fn restrict_width4(
    aggregation: &crate::Aggregation,
    fine: &[Lane],
    coarse: &mut [Lane],
    active: &Mask,
) -> Result<(), CmgError> {
    if fine.len() != aggregation.fine_dimension() || coarse.len() != aggregation.coarse_dimension()
    {
        return Err(CmgError::InvalidHierarchy {
            context: "fused restriction dimension mismatch",
        });
    }
    for values in coarse.iter_mut() {
        for lane in 0..4 {
            if active[lane] {
                values[lane] = 0.0;
            }
        }
    }
    for (vertex, values) in fine.iter().enumerate() {
        let label = aggregation.label_at(vertex);
        for lane in 0..4 {
            if active[lane] {
                coarse[label][lane] += values[lane];
            }
        }
    }
    Ok(())
}

fn prolong_add_width4(
    aggregation: &crate::Aggregation,
    coarse: &[Lane],
    fine: &mut [Lane],
    active: &Mask,
) -> Result<(), CmgError> {
    if fine.len() != aggregation.fine_dimension() || coarse.len() != aggregation.coarse_dimension()
    {
        return Err(CmgError::InvalidHierarchy {
            context: "fused prolongation dimension mismatch",
        });
    }
    for (vertex, values) in fine.iter_mut().enumerate() {
        let label = aggregation.label_at(vertex);
        for lane in 0..4 {
            if active[lane] {
                values[lane] += coarse[label][lane];
            }
        }
    }
    Ok(())
}
