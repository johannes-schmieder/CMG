//! Bounded preparation for the existing serial interleaved application kernel.
use super::{BatchLevel, BatchStorage, CmgBatchWorkspace, CmgPreconditioner, area, shape};
use crate::build_control::BuildMeter;
use crate::storage::{add, bytes};
use crate::{BuildControl, BuildStopReason, CmgError, CmgWorkspace};

/// Allocation-free requirements for a subsequent controlled batch construction.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CmgBatchRequirements {
    /// Requested heap layouts, excluding retained parents, caller buffers,
    /// inline root storage and allocator overhead.
    pub allocation_bytes: usize,
    /// Complete charged constructor work, including its repeated requirements
    /// query. The query that returned this value has already charged its own
    /// work separately. Callback, allocator and cleanup latency are excluded.
    pub initialization_work: u64,
}

const REQUIREMENTS: &str = "CMG batch workspace requirements";

// Only arithmetic/layout errors pass through this helper: it must never hide
// an allocator error or a callback decision as a prospective overflow refusal.
fn checked<T>(value: Result<T, CmgError>, meter: &BuildMeter<'_>) -> Result<T, CmgError> {
    value.map_err(|_| meter.stopped(REQUIREMENTS, BuildStopReason::CounterOverflow))
}

fn requirements(
    cmg: &CmgPreconditioner,
    max_rhs: usize,
    meter: &mut BuildMeter<'_>,
) -> Result<CmgBatchRequirements, CmgError> {
    let before = meter.remaining();
    meter.work(REQUIREMENTS, 128)?;
    if max_rhs == 0 {
        return Err(CmgError::InvalidOption {
            name: "CMG batch capacity",
            value: 0.0,
        });
    }
    let level_count = cmg.hierarchy.levels().len();
    let mut allocation_bytes = if max_rhs == 1 {
        // Keep scalar sizing and all its cancellation/budget errors intact.
        match CmgWorkspace::allocation_bytes_controlled(
            &cmg.hierarchy,
            cmg.direct_terminal.as_ref(),
            &cmg.finest_components,
            &cmg.coarse_centering,
            meter,
        ) {
            Err(CmgError::AllocationFailed { .. } | CmgError::InvalidHierarchy { .. }) => {
                return Err(meter.stopped(REQUIREMENTS, BuildStopReason::CounterOverflow));
            }
            result => result?,
        }
    } else {
        checked(bytes::<BatchLevel>(level_count), meter)?
    };
    let mut values = 0_usize;
    for index in 0..level_count {
        meter.work(REQUIREMENTS, 256)?;
        let (rows, coarse, factor, components) = shape(cmg, index);
        let local = checked(
            (|| {
                add(
                    add(rows, area(coarse, 2)?)?,
                    add(area(factor, 2)?, area(components, 3)?)?,
                )
            })(),
            meter,
        )?;
        let lanes = checked(area(local, max_rhs), meter)?;
        values = checked(add(values, lanes), meter)?;
        if max_rhs > 1 {
            let bank_bytes = checked(bytes::<f64>(lanes), meter)?;
            allocation_bytes = checked(add(allocation_bytes, bank_bytes), meter)?;
        }
    }
    meter.work(REQUIREMENTS, 64)?;
    let overflow = || meter.stopped(REQUIREMENTS, BuildStopReason::CounterOverflow);
    let levels = u64::try_from(level_count).map_err(|_| overflow())?;
    let values = u64::try_from(values).map_err(|_| overflow())?;
    let repeated_query = before - meter.remaining();
    let work = if max_rhs == 1 {
        // Existing scalar constructor: capacities/finalization cost 208;
        // dimensions + five banks + publication cost 160 per level; each
        // centering plan costs 96. All fills cost 8 per entry. V already
        // contains residual/coarse/factor and three centering banks; add the
        // finest projected RHS and seven finest-component banks (one usize).
        let finest = u64::try_from(cmg.hierarchy.levels()[0].graph().vertex_count())
            .map_err(|_| overflow())?;
        let components = u64::try_from(cmg.finest_components.count()).map_err(|_| overflow())?;
        let plans = u64::try_from(cmg.coarse_centering.len()).map_err(|_| overflow())?;
        let entries = values
            .checked_add(finest)
            .and_then(|v| v.checked_add(components.checked_mul(7)?))
            .ok_or_else(overflow)?;
        repeated_query
            .checked_add(272) // scalar 208 + batch admission 64
            .and_then(|w| w.checked_add(levels.checked_mul(160)?))
            .and_then(|w| w.checked_add(plans.checked_mul(96)?))
            .and_then(|w| w.checked_add(entries.checked_mul(8)?))
    } else {
        // Repeat sizing; admission64 + root capacity16 + finish64; each level
        // metadata128 + eight capacities16 + store32; zero initialization8.
        repeated_query
            .checked_add(144)
            .and_then(|w| w.checked_add(levels.checked_mul(288)?))
            .and_then(|w| w.checked_add(values.checked_mul(8)?))
    }
    .ok_or_else(overflow)?;
    Ok(CmgBatchRequirements {
        allocation_bytes,
        initialization_work: work,
    })
}

impl CmgBatchWorkspace {
    /// Size batch storage and bound every remaining construction charge.
    ///
    /// This query allocates nothing. Its validation and level scan are charged
    /// and cancellable. Arithmetic/layout overflow is a `CounterOverflow` stop.
    /// The result is a prospective bound, not a reservation or an identity
    /// token; construction repeats the query against its actual hierarchy.
    /// Capacity one retains the scalar representation. Existing scalar APIs
    /// do not call this query and retain their original boundary sequence.
    pub fn requirements_controlled(
        cmg: &CmgPreconditioner,
        max_rhs: usize,
        work_budget: u64,
        control: &mut dyn BuildControl,
    ) -> Result<CmgBatchRequirements, CmgError> {
        requirements(cmg, max_rhs, &mut BuildMeter::new(work_budget, control))
    }

    /// Admit all layouts before allocating, then initialize through bounded
    /// work and preallocation gates. No partial workspace is published on error.
    ///
    /// `work_budget` must cover the complete constructor, including its own
    /// requirements query. Actual allocation failures remain `AllocationFailed`;
    /// a caller must not reinterpret them as a prospective scalar fallback.
    pub fn try_new_controlled(
        cmg: &CmgPreconditioner,
        max_rhs: usize,
        budget_bytes: usize,
        work_budget: u64,
        control: &mut dyn BuildControl,
    ) -> Result<Self, CmgError> {
        let mut meter = BuildMeter::new(work_budget, control);
        let required = requirements(cmg, max_rhs, &mut meter)?;
        meter.work("CMG batch workspace admission", 64)?;
        crate::storage::admit(required.allocation_bytes, budget_bytes)?;
        if max_rhs == 1 {
            let workspace = CmgWorkspace::try_new_controlled(
                &cmg.hierarchy,
                cmg.direct_terminal.as_ref(),
                &cmg.finest_components,
                &cmg.coarse_centering,
                &mut meter,
            )?;
            return Ok(Self {
                capacity: 1,
                storage: BatchStorage::Scalar(workspace),
            });
        }
        let mut levels = meter.try_capacity(cmg.hierarchy.levels().len(), "CMG batch levels")?;
        for index in 0..cmg.hierarchy.levels().len() {
            meter.work("CMG batch level dimensions", 128)?;
            let (rows, coarse_rows, factor_rows, components) = shape(cmg, index);
            let mut zero = |rows, phase| meter.try_filled(area(rows, max_rhs)?, 0.0, phase);
            let level = BatchLevel {
                rows,
                coarse_rows,
                factor_rows,
                components,
                residual: zero(rows, "CMG batch residual")?,
                coarse_rhs: zero(coarse_rows, "CMG batch coarse RHS")?,
                coarse_correction: zero(coarse_rows, "CMG batch coarse correction")?,
                forward: zero(factor_rows, "CMG batch factor forward")?,
                factor_solution: zero(factor_rows, "CMG batch factor solution")?,
                sums: zero(components, "CMG batch component sums")?,
                corrections: zero(components, "CMG batch component corrections")?,
                means: zero(components, "CMG batch component means")?,
            };
            meter.work("CMG batch store level", 32)?;
            levels.push(level);
        }
        meter.work("CMG batch workspace finish", 64)?;
        Ok(Self {
            capacity: max_rhs,
            storage: BatchStorage::Interleaved(levels),
        })
    }
}
