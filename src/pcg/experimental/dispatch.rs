//! First-call calibration for stable, repeated zero-start batches.

use super::{FusedPcgWorkspace4, solve_pcg_batch_fused_width4_into_with_workspace, try_filled};
use crate::{
    CmgError, CmgOptions, CmgPreconditioner, Laplacian, PcgBatchMut, PcgBatchRef,
    PcgBatchWorkspace, PcgDiagnostics, PcgOptions, PcgWorkspace,
    solve_pcg_batch_into_with_workspace,
};
use std::time::{Duration, Instant};

/// Number of paired measured trials required for a calibrated decision.
pub const CALIBRATION_PAIRS: usize = 5;

/// Explicit policy for the experimental calibrated batch API.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BatchDispatchMode {
    /// Calibrate the first eligible batch, then reuse the decision.
    #[default]
    Auto,
    /// Always run the scalar caller-buffer path.
    Scalar,
    /// Always run four-lane fusion, failing if its workspace cannot fit.
    Fused,
}

/// Numerical path actually executed or selected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BatchDispatchRoute {
    /// Independent serial PCG solves.
    Scalar,
    /// Serial groups of four independent fused PCG solves.
    Fused,
}

/// Why the dispatcher selected its route.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BatchDispatchReason {
    /// Explicit scalar or fused override.
    Forced,
    /// Fewer than four submitted RHS.
    TooFewRhs,
    /// All RHS converged before iteration.
    ZeroIterations,
    /// Insufficient calibration time or incomplete trials.
    TimeBudget,
    /// Candidate workspaces or trial buffers cannot fit the configured budget.
    MemoryBudget,
    /// Fallible candidate allocation failed.
    Allocation,
    /// Clock samples were zero or otherwise unusable.
    InvalidTiming,
    /// Complete timing evidence did not establish the required gain.
    NoClearGain,
    /// Complete timing evidence established the configured gain margin.
    ClearFusedGain,
}

/// Options for an explicitly constructed calibrated solver.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BatchDispatchOptions {
    /// Requested mode; existing CMG APIs are unaffected by this option.
    pub mode: BatchDispatchMode,
    /// Soft extra-work budget after the first mandatory scalar solve.
    /// An in-progress numerical solve is never interrupted.
    pub calibration_budget: Duration,
    /// Required fractional time saving; default 0.10 requires CI upper < 0.90.
    pub minimum_gain: f64,
    /// Principal workspace and calibration-scratch cap, excluding immutable
    /// graph/hierarchy, caller buffers and allocator overhead. None means no cap.
    pub workspace_memory_budget_bytes: Option<usize>,
}

impl Default for BatchDispatchOptions {
    fn default() -> Self {
        Self {
            mode: BatchDispatchMode::Auto,
            calibration_budget: Duration::from_secs(30),
            minimum_gain: 0.10,
            workspace_memory_budget_bytes: None,
        }
    }
}

impl BatchDispatchOptions {
    fn validate(self) -> Result<Self, CmgError> {
        if !self.minimum_gain.is_finite() || !(0.0..1.0).contains(&self.minimum_gain) {
            return Err(CmgError::InvalidOption {
                name: "minimum_gain",
                value: self.minimum_gain,
            });
        }
        Ok(self)
    }
}

/// Immutable evidence from the most recent automatic decision.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BatchCalibrationReport {
    /// Route selected for subsequent calls.
    pub selected: BatchDispatchRoute,
    /// Selection or fallback reason.
    pub reason: BatchDispatchReason,
    /// Raw complete scalar/fused pairs; entries beyond completed_pairs are zero.
    pub scalar_nanoseconds: [u128; CALIBRATION_PAIRS],
    /// Raw complete fused timings paired with scalar_nanoseconds.
    pub fused_nanoseconds: [u128; CALIBRATION_PAIRS],
    /// Number of complete measured pairs, at most five.
    pub completed_pairs: usize,
    /// Median fused/scalar time ratio, only with complete positive samples.
    pub ratio: Option<f64>,
    /// Paired percentile-bootstrap 95% ratio interval.
    pub ratio_ci95: Option<[f64; 2]>,
    /// Extra calibration wall time, including allocation and agreement checks.
    pub extra_nanoseconds: u128,
    /// Conservative checked peak bound, including temporary allocations.
    pub calibration_peak_bound_bytes: usize,
    /// Estimated later batches needed to amortize calibration, not a guarantee.
    pub break_even_batches: Option<u128>,
}

impl BatchCalibrationReport {
    fn scalar(reason: BatchDispatchReason) -> Self {
        Self {
            selected: BatchDispatchRoute::Scalar,
            reason,
            scalar_nanoseconds: [0; CALIBRATION_PAIRS],
            fused_nanoseconds: [0; CALIBRATION_PAIRS],
            completed_pairs: 0,
            ratio: None,
            ratio_ci95: None,
            extra_nanoseconds: 0,
            calibration_peak_bound_bytes: 0,
            break_even_batches: None,
        }
    }
}

/// Per-call report; no allocations or clock reads are needed for a cached call.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BatchDispatchReport {
    /// Path that produced this call's answer (scalar on first automatic calls).
    pub executed: BatchDispatchRoute,
    /// Path selected for the next compatible call.
    pub selected: BatchDispatchRoute,
    /// Selection reason.
    pub reason: BatchDispatchReason,
    /// Whether this call reused a previous decision.
    pub cached: bool,
    /// Principal retained workspace storage after the call.
    pub retained_workspace_bytes: usize,
}

#[derive(Debug, Clone, Copy, PartialEq)]
struct Key {
    rhs_count: usize,
    rhs_stride: usize,
    rhs_value_stride: usize,
    output_stride: usize,
    output_value_stride: usize,
    pcg: PcgOptions,
}

/// CMG-owned scalar/fused dispatch for a stable repeated-batch workload.
///
/// Owns one immutable preconditioner and reusable workspaces; it does not create
/// a thread pool or alter parallel routing. In Auto, the first call returns its
/// scalar baseline while using private buffers for calibration. A candidate
/// numerical error poisons calibration until reset; baseline output remains
/// intact but the call returns an error. Changed shapes/strides/options trigger
/// fresh calibration. Changed RHS values do not: reset when their distribution
/// or the machine/affinity/load regime changes.
#[derive(Debug)]
pub struct CalibratedPcgBatchSolver {
    preconditioner: CmgPreconditioner,
    options: BatchDispatchOptions,
    scalar: Option<PcgBatchWorkspace>,
    fused: Option<FusedPcgWorkspace4>,
    cache: Option<(Key, BatchCalibrationReport)>,
    poisoned: bool,
    #[cfg(test)]
    fault: Option<TestFault>,
}

impl CalibratedPcgBatchSolver {
    /// Build a hierarchy and an initially uncalibrated solver.
    pub fn build(
        graph: &Laplacian,
        cmg: CmgOptions,
        options: BatchDispatchOptions,
    ) -> Result<Self, CmgError> {
        options.validate()?;
        Self::from_preconditioner(CmgPreconditioner::build(graph, cmg)?, options)
    }

    /// Take ownership of an existing immutable hierarchy. Workspaces are lazy.
    pub fn from_preconditioner(
        preconditioner: CmgPreconditioner,
        options: BatchDispatchOptions,
    ) -> Result<Self, CmgError> {
        Ok(Self {
            preconditioner,
            options: options.validate()?,
            scalar: None,
            fused: None,
            cache: None,
            poisoned: false,
            #[cfg(test)]
            fault: None,
        })
    }

    /// Borrow the fixed hierarchy.
    pub fn preconditioner(&self) -> &CmgPreconditioner {
        &self.preconditioner
    }
    /// Borrow the fixed finest graph.
    pub fn graph(&self) -> &Laplacian {
        self.preconditioner.hierarchy().levels()[0].graph()
    }
    /// Return the configured options.
    pub fn options(&self) -> BatchDispatchOptions {
        self.options
    }
    /// Borrow the latest calibration evidence without copying or allocating.
    pub fn calibration_report(&self) -> Option<&BatchCalibrationReport> {
        self.cache.as_ref().map(|(_, r)| r)
    }
    /// Return currently retained principal workspace bytes.
    pub fn retained_workspace_bytes(&self) -> usize {
        self.scalar.as_ref().map_or(0, PcgBatchWorkspace::byte_len)
            + self.fused.as_ref().map_or(0, fused_retained_bytes)
    }
    /// Forget the decision and release the candidate workspace. Clears failures.
    pub fn reset_calibration(&mut self) {
        self.cache = None;
        self.fused = None;
        self.poisoned = false;
    }
    /// Change the explicit mode and discard calibration.
    pub fn set_mode(&mut self, mode: BatchDispatchMode) {
        self.reset_calibration();
        self.options.mode = mode;
        if mode == BatchDispatchMode::Fused {
            self.scalar = None;
        }
    }

    /// Solve zero-start caller-buffer RHS and automatically reuse calibration.
    pub fn solve_batch_into(
        &mut self,
        rhs: PcgBatchRef<'_>,
        output: PcgBatchMut<'_>,
        diagnostics: &mut [PcgDiagnostics],
        pcg: PcgOptions,
    ) -> Result<BatchDispatchReport, CmgError> {
        // Constructing this clock does not read it. Cached/forced calls never do.
        self.solve_using(rhs, output, diagnostics, pcg, &mut WallClock(None))
    }

    fn solve_using<C: Clock>(
        &mut self,
        rhs: PcgBatchRef<'_>,
        mut output: PcgBatchMut<'_>,
        diagnostics: &mut [PcgDiagnostics],
        pcg: PcgOptions,
        clock: &mut C,
    ) -> Result<BatchDispatchReport, CmgError> {
        self.validate(rhs, &output, diagnostics.len())?;
        if rhs.rhs_count > 0 {
            pcg.validate()?;
        }
        if self.poisoned {
            return Err(CmgError::CalibrationFailed {
                context: "reset required after numerical calibration failure",
            });
        }
        let key = Key {
            rhs_count: rhs.rhs_count,
            rhs_stride: rhs.rhs_stride,
            rhs_value_stride: rhs.value_stride,
            output_stride: output.rhs_stride,
            output_value_stride: output.value_stride,
            pcg,
        };
        if self.cache.as_ref().is_some_and(|(old, _)| *old != key) {
            self.reset_calibration();
        }
        if self.options.mode != BatchDispatchMode::Auto {
            let route = if self.options.mode == BatchDispatchMode::Scalar {
                BatchDispatchRoute::Scalar
            } else {
                BatchDispatchRoute::Fused
            };
            self.ensure(route)?;
            self.run(route, rhs, output, diagnostics, pcg)?;
            return Ok(self.report(route, route, BatchDispatchReason::Forced, false));
        }
        if let Some((_, report)) = self.cache {
            self.run(report.selected, rhs, output, diagnostics, pcg)?;
            return Ok(self.report(report.selected, report.selected, report.reason, true));
        }
        self.ensure(BatchDispatchRoute::Scalar)?;
        if rhs.rhs_count < 4 {
            self.run(BatchDispatchRoute::Scalar, rhs, output, diagnostics, pcg)?;
            let report = BatchCalibrationReport::scalar(BatchDispatchReason::TooFewRhs);
            self.cache = Some((key, report));
            return Ok(self.report(
                BatchDispatchRoute::Scalar,
                report.selected,
                report.reason,
                false,
            ));
        }
        let start = clock.now();
        self.run(
            BatchDispatchRoute::Scalar,
            rhs,
            reborrow(&mut output),
            diagnostics,
            pcg,
        )?;
        let baseline_ns = elapsed(start, clock.now());
        let calibration_start = clock.now();
        let result = self.calibrate(
            rhs,
            &output,
            diagnostics,
            pcg,
            baseline_ns,
            calibration_start,
            clock,
        );
        let mut report = match result {
            Ok(report) => report,
            Err(error) => {
                self.fused = None;
                self.poisoned = true;
                return Err(error);
            }
        };
        report.extra_nanoseconds = elapsed(calibration_start, clock.now());
        if report.selected == BatchDispatchRoute::Scalar {
            self.fused = None;
        } else {
            let saving = median(&report.scalar_nanoseconds) - median(&report.fused_nanoseconds);
            report.break_even_batches = Some(report.extra_nanoseconds.div_ceil(saving));
        }
        self.cache = Some((key, report));
        Ok(self.report(
            BatchDispatchRoute::Scalar,
            report.selected,
            report.reason,
            false,
        ))
    }

    #[allow(clippy::too_many_arguments)]
    fn calibrate<C: Clock>(
        &mut self,
        rhs: PcgBatchRef<'_>,
        baseline: &PcgBatchMut<'_>,
        diagnostics: &[PcgDiagnostics],
        pcg: PcgOptions,
        baseline_ns: u128,
        start: Duration,
        clock: &mut C,
    ) -> Result<BatchCalibrationReport, CmgError> {
        let mut report = BatchCalibrationReport::scalar(BatchDispatchReason::NoClearGain);
        if diagnostics.iter().all(|item| item.iterations() == 0) {
            report.reason = BatchDispatchReason::ZeroIterations;
            return Ok(report);
        }
        if baseline_ns == 0 {
            report.reason = BatchDispatchReason::InvalidTiming;
            return Ok(report);
        }
        let budget = self.options.calibration_budget.as_nanos();
        // One fused warmup plus ten measured solves. This conservative estimate
        // avoids beginning a minutes-long trial under a seconds-long budget.
        if baseline_ns.saturating_mul(11) > budget || elapsed(start, clock.now()) >= budget {
            report.reason = BatchDispatchReason::TimeBudget;
            return Ok(report);
        }
        let scratch_len = output_span(baseline)?;
        let scratch_bytes = add(
            mul(scratch_len, 8)?,
            mul(diagnostics.len(), std::mem::size_of::<PcgDiagnostics>())?,
        )?;
        // Bootstrap samples are stack-resident: 10,000 ratios and two five-item samples.
        let analysis_bytes = 10_000 * 8 + 2 * CALIBRATION_PAIRS * 16;
        let peak = add(
            add(
                self.retained_workspace_bytes(),
                fused_peak_bound(&self.preconditioner)?,
            )?,
            add(scratch_bytes, analysis_bytes)?,
        )?;
        report.calibration_peak_bound_bytes = peak;
        if self.check_budget(peak).is_err() {
            report.reason = BatchDispatchReason::MemoryBudget;
            return Ok(report);
        }
        let allocated = (|| {
            #[cfg(test)]
            if self.fault == Some(TestFault::Allocation) {
                return Err(CmgError::AllocationFailed {
                    context: "injected candidate",
                });
            }
            self.ensure(BatchDispatchRoute::Fused)?;
            let scratch = try_filled(scratch_len, 0.0, "calibration output")?;
            let trial_diagnostics = try_filled(
                diagnostics.len(),
                PcgDiagnostics::default(),
                "calibration diagnostics",
            )?;
            self.check_budget(add(
                self.retained_workspace_bytes(),
                add(
                    add(
                        mul(scratch.capacity(), 8)?,
                        mul(
                            trial_diagnostics.capacity(),
                            std::mem::size_of::<PcgDiagnostics>(),
                        )?,
                    )?,
                    analysis_bytes,
                )?,
            )?)?;
            Ok((scratch, trial_diagnostics))
        })();
        let (mut scratch, mut trial_diagnostics) = match allocated {
            Ok(value) => value,
            Err(CmgError::AllocationFailed { .. }) => {
                report.reason = BatchDispatchReason::Allocation;
                return Ok(report);
            }
            Err(CmgError::MemoryBudgetExceeded { .. }) => {
                report.reason = BatchDispatchReason::MemoryBudget;
                return Ok(report);
            }
            Err(error) => return Err(error),
        };
        if elapsed(start, clock.now()) >= budget {
            report.reason = BatchDispatchReason::TimeBudget;
            return Ok(report);
        }
        self.trial(
            BatchDispatchRoute::Fused,
            rhs,
            baseline,
            diagnostics,
            pcg,
            &mut scratch,
            &mut trial_diagnostics,
        )?;
        for pair in 0..CALIBRATION_PAIRS {
            let routes = if pair % 2 == 0 {
                [BatchDispatchRoute::Scalar, BatchDispatchRoute::Fused]
            } else {
                [BatchDispatchRoute::Fused, BatchDispatchRoute::Scalar]
            };
            let mut times = [0; 2];
            for route in routes {
                if elapsed(start, clock.now()) >= budget {
                    report.reason = BatchDispatchReason::TimeBudget;
                    return Ok(report);
                }
                let tick = clock.now();
                self.run(
                    route,
                    rhs,
                    PcgBatchMut::strided(
                        &mut scratch,
                        rhs.rhs_count,
                        rhs.dimension,
                        baseline.rhs_stride,
                        baseline.value_stride,
                    )?,
                    &mut trial_diagnostics,
                    pcg,
                )?;
                let ns = elapsed(tick, clock.now());
                self.agreement(baseline, diagnostics, &scratch, &trial_diagnostics)?;
                times[usize::from(route == BatchDispatchRoute::Fused)] = ns;
            }
            report.scalar_nanoseconds[pair] = times[0];
            report.fused_nanoseconds[pair] = times[1];
            report.completed_pairs += 1;
        }
        if elapsed(start, clock.now()) >= budget {
            report.reason = BatchDispatchReason::TimeBudget;
            return Ok(report);
        }
        classify(&mut report, self.options.minimum_gain);
        if elapsed(start, clock.now()) >= budget {
            report.selected = BatchDispatchRoute::Scalar;
            report.reason = BatchDispatchReason::TimeBudget;
        }
        Ok(report)
    }

    #[allow(clippy::too_many_arguments)]
    fn trial(
        &mut self,
        route: BatchDispatchRoute,
        rhs: PcgBatchRef<'_>,
        baseline: &PcgBatchMut<'_>,
        diagnostics: &[PcgDiagnostics],
        pcg: PcgOptions,
        scratch: &mut [f64],
        trial_diagnostics: &mut [PcgDiagnostics],
    ) -> Result<(), CmgError> {
        self.run(
            route,
            rhs,
            PcgBatchMut::strided(
                scratch,
                rhs.rhs_count,
                rhs.dimension,
                baseline.rhs_stride,
                baseline.value_stride,
            )?,
            trial_diagnostics,
            pcg,
        )?;
        self.agreement(baseline, diagnostics, scratch, trial_diagnostics)
    }

    fn agreement(
        &self,
        baseline: &PcgBatchMut<'_>,
        diagnostics: &[PcgDiagnostics],
        scratch: &[f64],
        trial_diagnostics: &[PcgDiagnostics],
    ) -> Result<(), CmgError> {
        #[cfg(test)]
        if self.fault == Some(TestFault::Mismatch) {
            return Err(CmgError::CalibrationFailed {
                context: "injected mismatch",
            });
        }
        for rhs in 0..baseline.rhs_count {
            for vertex in 0..baseline.dimension {
                let index = rhs * baseline.rhs_stride + vertex * baseline.value_stride;
                if baseline.data[index].to_bits() != scratch[index].to_bits() {
                    return Err(CmgError::CalibrationFailed {
                        context: "solution bits differ from scalar",
                    });
                }
            }
            if !diagnostics_identical(diagnostics[rhs], trial_diagnostics[rhs]) {
                return Err(CmgError::CalibrationFailed {
                    context: "diagnostics differ from scalar",
                });
            }
        }
        Ok(())
    }

    fn ensure(&mut self, route: BatchDispatchRoute) -> Result<(), CmgError> {
        match route {
            BatchDispatchRoute::Scalar if self.scalar.is_none() => {
                let bytes = add(
                    PcgWorkspace::required_bytes(&self.preconditioner),
                    mul(self.graph().vertex_count(), 16)?,
                )?;
                self.check_budget(add(self.retained_workspace_bytes(), bytes)?)?;
                let workspace = PcgBatchWorkspace::new(&self.preconditioner)?;
                self.check_budget(add(self.retained_workspace_bytes(), workspace.byte_len())?)?;
                self.scalar = Some(workspace);
            }
            BatchDispatchRoute::Fused if self.fused.is_none() => {
                self.check_budget(add(
                    self.retained_workspace_bytes(),
                    fused_peak_bound(&self.preconditioner)?,
                )?)?;
                let workspace = FusedPcgWorkspace4::try_new(&self.preconditioner)?;
                self.check_budget(add(
                    self.retained_workspace_bytes(),
                    fused_retained_bytes(&workspace),
                )?)?;
                self.fused = Some(workspace);
            }
            _ => {}
        }
        Ok(())
    }

    fn run(
        &mut self,
        route: BatchDispatchRoute,
        rhs: PcgBatchRef<'_>,
        output: PcgBatchMut<'_>,
        diagnostics: &mut [PcgDiagnostics],
        pcg: PcgOptions,
    ) -> Result<(), CmgError> {
        let graph = self.preconditioner.hierarchy().levels()[0].graph();
        match route {
            BatchDispatchRoute::Scalar => solve_pcg_batch_into_with_workspace(
                graph,
                &self.preconditioner,
                rhs,
                None,
                output,
                diagnostics,
                pcg,
                self.scalar.as_mut().expect("scalar workspace prepared"),
            ),
            BatchDispatchRoute::Fused => solve_pcg_batch_fused_width4_into_with_workspace(
                graph,
                &self.preconditioner,
                rhs,
                output,
                diagnostics,
                pcg,
                self.fused.as_mut().expect("fused workspace prepared"),
            ),
        }
    }

    fn validate(
        &self,
        rhs: PcgBatchRef<'_>,
        output: &PcgBatchMut<'_>,
        diagnostics: usize,
    ) -> Result<(), CmgError> {
        for (context, expected, actual) in [
            (
                "calibrated RHS dimension",
                self.graph().vertex_count(),
                rhs.dimension,
            ),
            (
                "calibrated output dimension",
                rhs.dimension,
                output.dimension,
            ),
            ("calibrated output count", rhs.rhs_count, output.rhs_count),
            ("calibrated diagnostics count", rhs.rhs_count, diagnostics),
        ] {
            if expected != actual {
                return Err(CmgError::dimension(context, expected, actual));
            }
        }
        Ok(())
    }

    fn check_budget(&self, required_bytes: usize) -> Result<(), CmgError> {
        if let Some(budget_bytes) = self.options.workspace_memory_budget_bytes {
            if required_bytes > budget_bytes {
                return Err(CmgError::MemoryBudgetExceeded {
                    required_bytes,
                    budget_bytes,
                });
            }
        }
        Ok(())
    }
    fn report(
        &self,
        executed: BatchDispatchRoute,
        selected: BatchDispatchRoute,
        reason: BatchDispatchReason,
        cached: bool,
    ) -> BatchDispatchReport {
        BatchDispatchReport {
            executed,
            selected,
            reason,
            cached,
            retained_workspace_bytes: self.retained_workspace_bytes(),
        }
    }
}

fn reborrow<'a>(view: &'a mut PcgBatchMut<'_>) -> PcgBatchMut<'a> {
    PcgBatchMut {
        data: view.data,
        rhs_count: view.rhs_count,
        dimension: view.dimension,
        rhs_stride: view.rhs_stride,
        value_stride: view.value_stride,
    }
}
fn output_span(view: &PcgBatchMut<'_>) -> Result<usize, CmgError> {
    if view.rhs_count == 0 || view.dimension == 0 {
        return Ok(0);
    }
    add(
        add(
            mul(view.rhs_count - 1, view.rhs_stride)?,
            mul(view.dimension - 1, view.value_stride)?,
        )?,
        1,
    )
}
fn add(a: usize, b: usize) -> Result<usize, CmgError> {
    a.checked_add(b).ok_or(CmgError::InvalidHierarchy {
        context: "dispatch memory bound overflow",
    })
}
fn mul(a: usize, b: usize) -> Result<usize, CmgError> {
    a.checked_mul(b).ok_or(CmgError::InvalidHierarchy {
        context: "dispatch memory bound overflow",
    })
}

// Bounds all packed vectors, worst-case component counts, recursive buffers,
// Vec headers and component-construction scratch without building a workspace.
fn fused_peak_bound(preconditioner: &CmgPreconditioner) -> Result<usize, CmgError> {
    let levels = preconditioner.hierarchy().levels();
    let mut bytes = mul(levels[0].graph().vertex_count(), 7 * 32)?;
    for (index, level) in levels.iter().enumerate() {
        let n = level.graph().vertex_count();
        let coarse = levels
            .get(index + 1)
            .map_or(0, |l| l.graph().vertex_count());
        // Seven component arrays (six f64 lanes, one usize lane), metadata,
        // and parent/root scratch (using usize width, not assuming 64 bits).
        let component = mul(n, 6 * 32 + 8 * std::mem::size_of::<usize>())?;
        bytes = add(bytes, add(component, mul(add(n, mul(coarse, 2)?)?, 32)?)?)?;
    }
    let factor = preconditioner
        .terminal_factor()
        .map_or(0, |f| f.active_dimension());
    bytes = add(bytes, mul(factor, 64)?)?;
    add(
        bytes,
        mul(
            levels.len(),
            std::mem::size_of::<super::FusedLevelWorkspace>()
                + std::mem::size_of::<super::FusedComponentWorkspace>()
                + std::mem::size_of::<crate::Components>()
                + std::mem::size_of::<usize>(),
        )?,
    )
}
fn fused_retained_bytes(workspace: &FusedPcgWorkspace4) -> usize {
    workspace.byte_len()
        + workspace.levels.capacity() * std::mem::size_of::<super::FusedLevelWorkspace>()
        + workspace.component_workspaces.capacity()
            * std::mem::size_of::<super::FusedComponentWorkspace>()
        + workspace.components.capacity() * std::mem::size_of::<crate::Components>()
}

fn diagnostics_identical(a: PcgDiagnostics, b: PcgDiagnostics) -> bool {
    a.iterations() == b.iterations()
        && a.restarts() == b.restarts()
        && [
            a.initial_residual_norm(),
            a.residual_norm(),
            a.relative_residual(),
            a.backward_error(),
            a.tolerance(),
            a.rhs_projection_norm(),
        ]
        .map(f64::to_bits)
            == [
                b.initial_residual_norm(),
                b.residual_norm(),
                b.relative_residual(),
                b.backward_error(),
                b.tolerance(),
                b.rhs_projection_norm(),
            ]
            .map(f64::to_bits)
}

trait Clock {
    fn now(&mut self) -> Duration;
}
struct WallClock(Option<Instant>);
impl Clock for WallClock {
    fn now(&mut self) -> Duration {
        self.0.get_or_insert_with(Instant::now).elapsed()
    }
}
fn elapsed(start: Duration, end: Duration) -> u128 {
    end.saturating_sub(start).as_nanos()
}
fn median(values: &[u128; CALIBRATION_PAIRS]) -> u128 {
    let mut values = *values;
    values.sort_unstable();
    values[CALIBRATION_PAIRS / 2]
}
fn classify(report: &mut BatchCalibrationReport, minimum_gain: f64) {
    if report.completed_pairs != CALIBRATION_PAIRS {
        report.reason = BatchDispatchReason::TimeBudget;
        return;
    }
    if report.scalar_nanoseconds.contains(&0) || report.fused_nanoseconds.contains(&0) {
        report.reason = BatchDispatchReason::InvalidTiming;
        return;
    }
    let ratio =
        median(&report.fused_nanoseconds) as f64 / median(&report.scalar_nanoseconds) as f64;
    let mut rng = 0x243f_6a88_85a3_08d3_u64;
    let mut ratios = [0.0; 10_000];
    for sample in &mut ratios {
        let mut scalar = [0; CALIBRATION_PAIRS];
        let mut fused = [0; CALIBRATION_PAIRS];
        for i in 0..CALIBRATION_PAIRS {
            rng = rng.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1);
            let chosen = (rng % CALIBRATION_PAIRS as u64) as usize;
            scalar[i] = report.scalar_nanoseconds[chosen];
            fused[i] = report.fused_nanoseconds[chosen];
        }
        *sample = median(&fused) as f64 / median(&scalar) as f64;
    }
    ratios.sort_by(f64::total_cmp);
    let ci = [ratios[249], ratios[9_749]];
    if !ratio.is_finite()
        || !ci.iter().all(|value| value.is_finite() && *value > 0.0)
        || !(ci[0] <= ratio && ratio <= ci[1])
    {
        report.reason = BatchDispatchReason::InvalidTiming;
        return;
    }
    report.ratio = Some(ratio);
    report.ratio_ci95 = Some(ci);
    if ci[1] < 1.0 - minimum_gain {
        report.selected = BatchDispatchRoute::Fused;
        report.reason = BatchDispatchReason::ClearFusedGain;
    } else {
        report.reason = BatchDispatchReason::NoClearGain;
    }
}

#[cfg(test)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TestFault {
    Allocation,
    Mismatch,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;

    struct ScriptClock(VecDeque<Duration>);
    impl Clock for ScriptClock {
        fn now(&mut self) -> Duration {
            self.0.pop_front().expect("unexpected clock read")
        }
    }
    struct NoClock;
    impl Clock for NoClock {
        fn now(&mut self) -> Duration {
            panic!("cached/forced solve read clock")
        }
    }
    fn clock(scalar: u64, fused: u64) -> ScriptClock {
        let mut now = scalar;
        let mut times = vec![0, now, now, now, now];
        for pair in 0..5 {
            let samples = if pair % 2 == 0 {
                [scalar, fused]
            } else {
                [fused, scalar]
            };
            for sample in samples {
                times.extend([now, now]);
                now += sample;
                times.push(now);
            }
        }
        times.extend([now, now, now]);
        ScriptClock(times.into_iter().map(Duration::from_nanos).collect())
    }
    fn solver(direct: usize) -> CalibratedPcgBatchSolver {
        let graph =
            Laplacian::from_edges(96, (0..95).map(|i| (i, i + 1, 0.5 + (i % 7) as f64))).unwrap();
        CalibratedPcgBatchSolver::build(
            &graph,
            CmgOptions {
                direct_threshold: direct,
                ..CmgOptions::default()
            },
            BatchDispatchOptions::default(),
        )
        .unwrap()
    }
    fn rhs(solver: &CalibratedPcgBatchSolver, count: usize, zero: bool) -> Vec<f64> {
        (0..count)
            .flat_map(|lane| {
                let x: Vec<_> = (0..96)
                    .map(|i| {
                        if zero && lane % 4 == 0 {
                            0.0
                        } else {
                            ((i * 17 + lane * 23) % 97) as f64 / 7.0
                        }
                    })
                    .collect();
                solver.graph().matvec(&x).unwrap()
            })
            .collect()
    }
    fn solve<C: Clock>(
        solver: &mut CalibratedPcgBatchSolver,
        rhs: &[f64],
        count: usize,
        clock: &mut C,
    ) -> (Vec<f64>, Vec<PcgDiagnostics>, BatchDispatchReport) {
        let mut output = vec![-13.0; rhs.len()];
        let mut diagnostics = vec![PcgDiagnostics::default(); count];
        let report = solver
            .solve_using(
                PcgBatchRef::contiguous(rhs, count, 96).unwrap(),
                PcgBatchMut::contiguous(&mut output, count, 96).unwrap(),
                &mut diagnostics,
                PcgOptions::default(),
                clock,
            )
            .unwrap();
        (output, diagnostics, report)
    }

    #[test]
    fn policy_gains_regressions_noise_and_bad_samples() {
        for (fused, expected) in [
            ([50; 5], BatchDispatchReason::ClearFusedGain),
            ([110; 5], BatchDispatchReason::NoClearGain),
            ([90; 5], BatchDispatchReason::NoClearGain),
            ([30, 40, 50, 160, 170], BatchDispatchReason::NoClearGain),
            ([0, 50, 50, 50, 50], BatchDispatchReason::InvalidTiming),
        ] {
            let mut report = BatchCalibrationReport::scalar(BatchDispatchReason::NoClearGain);
            report.completed_pairs = 5;
            report.scalar_nanoseconds = [100; 5];
            report.fused_nanoseconds = fused;
            classify(&mut report, 0.1);
            assert_eq!(report.reason, expected);
        }
        let mut report = BatchCalibrationReport::scalar(BatchDispatchReason::NoClearGain);
        report.completed_pairs = 4;
        classify(&mut report, 0.1);
        assert_eq!(report.reason, BatchDispatchReason::TimeBudget);
    }

    #[test]
    fn calibrated_and_forced_calls_are_bitwise_and_cached_without_clock_reads() {
        for direct in [2, 700] {
            for count in [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 32] {
                let mut solver = solver(direct);
                let values = rhs(&solver, count, true);
                let (first, diagnostics, report) =
                    solve(&mut solver, &values, count, &mut clock(100, 50));
                assert_eq!(report.executed, BatchDispatchRoute::Scalar);
                if count >= 4 {
                    assert_eq!(report.selected, BatchDispatchRoute::Fused);
                    let calibration = solver.calibration_report().unwrap();
                    assert_eq!(calibration.completed_pairs, 5);
                    assert_eq!(calibration.ratio_ci95, Some([0.5, 0.5]));
                    assert!(calibration.break_even_batches.unwrap() > 0);
                }
                let (cached, cached_diags, report) =
                    solve(&mut solver, &values, count, &mut NoClock);
                assert!(report.cached);
                assert_eq!(
                    first.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
                    cached.iter().map(|v| v.to_bits()).collect::<Vec<_>>()
                );
                assert!(
                    diagnostics
                        .iter()
                        .zip(&cached_diags)
                        .all(|(&a, &b)| diagnostics_identical(a, b))
                );
                for mode in [BatchDispatchMode::Scalar, BatchDispatchMode::Fused] {
                    solver.set_mode(mode);
                    let (forced, forced_diags, report) =
                        solve(&mut solver, &values, count, &mut NoClock);
                    assert_eq!(report.reason, BatchDispatchReason::Forced);
                    assert_eq!(first, forced);
                    assert_eq!(diagnostics, forced_diags);
                }
            }
        }
    }

    #[test]
    fn soft_deadline_incomplete_pairs_and_zero_batches_fall_back() {
        let mut solver = solver(700);
        let values = rhs(&solver, 4, false);
        let mut times = clock(100, 50);
        times.0[11] = Duration::from_secs(31); // Before pair two.
        times.0.truncate(12);
        times.0.push_back(Duration::from_secs(31));
        let (_, _, report) = solve(&mut solver, &values, 4, &mut times);
        assert_eq!(report.reason, BatchDispatchReason::TimeBudget);
        assert_eq!(solver.calibration_report().unwrap().completed_pairs, 1);
        assert!(solver.fused.is_none());
        solve(&mut solver, &values, 4, &mut NoClock);
        solver.reset_calibration();
        let (_, _, report) = solve(&mut solver, &values, 4, &mut clock(4_000_000_000, 1));
        assert_eq!(report.reason, BatchDispatchReason::TimeBudget);
        solver.reset_calibration();
        let (_, _, report) = solve(&mut solver, &vec![0.0; 384], 4, &mut clock(100, 50));
        assert_eq!(report.reason, BatchDispatchReason::ZeroIterations);
        assert!(solver.fused.is_none());
    }

    #[test]
    fn memory_bounds_and_allocation_failure_are_safe() {
        let mut solver = solver(2);
        let values = rhs(&solver, 4, false);
        solver.options.workspace_memory_budget_bytes = Some(1);
        let mut output = vec![-13.0; 384];
        let mut diagnostics = vec![PcgDiagnostics::default(); 4];
        let error = solver
            .solve_batch_into(
                PcgBatchRef::contiguous(&values, 4, 96).unwrap(),
                PcgBatchMut::contiguous(&mut output, 4, 96).unwrap(),
                &mut diagnostics,
                PcgOptions::default(),
            )
            .unwrap_err();
        assert!(matches!(error, CmgError::MemoryBudgetExceeded { .. }));
        assert!(output.iter().all(|&v| v == -13.0));
        solver.options.workspace_memory_budget_bytes = None;
        solver.ensure(BatchDispatchRoute::Scalar).unwrap();
        let scalar_bytes = solver.retained_workspace_bytes();
        solver.options.workspace_memory_budget_bytes = Some(scalar_bytes);
        let (_, _, report) = solve(&mut solver, &values, 4, &mut clock(100, 50));
        assert_eq!(report.reason, BatchDispatchReason::MemoryBudget);
        assert_eq!(solver.retained_workspace_bytes(), scalar_bytes);
        solver.set_mode(BatchDispatchMode::Fused);
        assert!(solver.ensure(BatchDispatchRoute::Fused).is_err());
        solver.set_mode(BatchDispatchMode::Auto);
        solver.options.workspace_memory_budget_bytes = None;
        solver.fault = Some(TestFault::Allocation);
        let (_, _, report) = solve(&mut solver, &values, 4, &mut clock(100, 50));
        assert_eq!(report.reason, BatchDispatchReason::Allocation);
        assert!(solver.fused.is_none());
        assert!(mul(usize::MAX, 2).is_err());
        assert!(add(usize::MAX, 1).is_err());
        assert!(try_filled::<u128>(usize::MAX, 0, "test").is_err());
        solver.fault = None;
        solver.reset_calibration();
        solve(&mut solver, &values, 4, &mut clock(100, 50));
        assert!(
            solver.retained_workspace_bytes()
                <= solver
                    .calibration_report()
                    .unwrap()
                    .calibration_peak_bound_bytes
        );
    }

    #[test]
    fn numerical_mismatch_preserves_baseline_and_requires_reset() {
        let mut solver = solver(2);
        let values = rhs(&solver, 4, false);
        solver.options.mode = BatchDispatchMode::Scalar;
        let (baseline, base_diags, _) = solve(&mut solver, &values, 4, &mut NoClock);
        solver.set_mode(BatchDispatchMode::Auto);
        solver.fault = Some(TestFault::Mismatch);
        let mut output = vec![-13.0; 384];
        let mut diagnostics = vec![PcgDiagnostics::default(); 4];
        let err = solver
            .solve_using(
                PcgBatchRef::contiguous(&values, 4, 96).unwrap(),
                PcgBatchMut::contiguous(&mut output, 4, 96).unwrap(),
                &mut diagnostics,
                PcgOptions::default(),
                &mut clock(100, 50),
            )
            .unwrap_err();
        assert!(matches!(err, CmgError::CalibrationFailed { .. }));
        assert_eq!(output, baseline);
        assert_eq!(diagnostics, base_diags);
        assert!(solver.poisoned);
        assert!(
            solver
                .solve_using(
                    PcgBatchRef::contiguous(&values, 4, 96).unwrap(),
                    PcgBatchMut::contiguous(&mut output, 4, 96).unwrap(),
                    &mut diagnostics,
                    PcgOptions::default(),
                    &mut NoClock
                )
                .is_err()
        );
        assert_eq!(output, baseline);
        solver.reset_calibration();
        solver.fault = None;
        solve(&mut solver, &values, 4, &mut clock(100, 50));
    }

    #[test]
    fn cache_key_invalidation_and_strided_disconnected_views() {
        let graph = Laplacian::from_edges(6, [(0, 1, 1.0), (1, 2, 2.0), (3, 4, 0.5)]).unwrap();
        let mut solver = CalibratedPcgBatchSolver::build(
            &graph,
            CmgOptions::default(),
            BatchDispatchOptions::default(),
        )
        .unwrap();
        let mut rhs = vec![91.0; 6 * 7];
        for lane in 0..5 {
            let v = graph
                .matvec(
                    &(0..6)
                        .map(|i| (i * 17 + lane * 11) as f64)
                        .collect::<Vec<_>>(),
                )
                .unwrap();
            for i in 0..6 {
                rhs[i * 7 + lane] = v[i];
            }
        }
        for (step, interval) in [25, 25, 7, 7].into_iter().enumerate() {
            let mut output = vec![-77.0; 6 * 8];
            let mut diags = vec![PcgDiagnostics::default(); 5];
            let report = solver
                .solve_using(
                    PcgBatchRef::strided(&rhs, 5, 6, 1, 7).unwrap(),
                    PcgBatchMut::strided(&mut output, 5, 6, 1, 8).unwrap(),
                    &mut diags,
                    PcgOptions {
                        residual_recompute_interval: interval,
                        ..PcgOptions::default()
                    },
                    &mut clock(100, 50),
                )
                .unwrap();
            assert_eq!(report.cached, step % 2 == 1);
            for row in output.chunks(8) {
                assert!(row[5..].iter().all(|&v| v == -77.0));
            }
        }
        // RHS count changes invalidate the decision, including an ineligible tail.
        let mut output = vec![0.0; 6];
        let mut diags = vec![PcgDiagnostics::default(); 1];
        let report = solver
            .solve_using(
                PcgBatchRef::strided(&rhs, 1, 6, 1, 7).unwrap(),
                PcgBatchMut::contiguous(&mut output, 1, 6).unwrap(),
                &mut diags,
                PcgOptions::default(),
                &mut NoClock,
            )
            .unwrap();
        assert!(!report.cached);
        assert_eq!(report.reason, BatchDispatchReason::TooFewRhs);
    }
}
