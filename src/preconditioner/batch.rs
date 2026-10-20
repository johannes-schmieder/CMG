//! Interleaved, fixed-cycle CMG applications sharing one hierarchy traversal.

use super::cycle::{Cycle, LevelParts, Parts, Store};
use super::{ApplyContext, CmgPreconditioner};
use crate::CmgError;
use crate::cancel::Meter;
use crate::storage::{add, bytes};

#[derive(Debug, Clone, Default)]
struct BatchLevel {
    rows: usize,
    coarse_rows: usize,
    factor_rows: usize,
    components: usize,
    residual: Vec<f64>,
    coarse_rhs: Vec<f64>,
    coarse_correction: Vec<f64>,
    forward: Vec<f64>,
    factor_solution: Vec<f64>,
    sums: Vec<f64>,
    corrections: Vec<f64>,
    means: Vec<f64>,
}
crate::storage::storage_fields!(
    BatchLevel,
    residual,
    coarse_rhs,
    coarse_correction,
    forward,
    factor_solution,
    sums,
    corrections,
    means
);

/// Reusable, fallibly admitted storage for interleaved stationary CMG cycles.
///
/// Capacity bounds the number of right-hand sides per application. Each call
/// packs only its actual columns, in vertex-major order; a smaller call reuses
/// the corresponding prefixes without touching or reading spare capacity.
#[derive(Debug, Clone)]
pub struct CmgBatchWorkspace {
    capacity: usize,
    storage: BatchStorage,
}

// Keep the scalar workspace inline: boxing would add an infallible allocation
// to the admitted constructor, solely to save a small fixed root-value size.
#[allow(clippy::large_enum_variant)]
#[derive(Debug, Clone)]
enum BatchStorage {
    Scalar(crate::CmgWorkspace),
    Interleaved(Vec<BatchLevel>),
}
impl crate::storage::Storage for BatchStorage {
    fn visit(
        &self,
        visitor: &mut dyn FnMut(crate::storage::Allocation) -> Result<(), CmgError>,
    ) -> Result<(), CmgError> {
        match self {
            Self::Scalar(workspace) => crate::storage::Storage::visit(workspace, visitor),
            Self::Interleaved(levels) => crate::storage::Storage::visit(levels, visitor),
        }
    }
}
crate::storage::storage_fields!(CmgBatchWorkspace, storage);

fn area(rows: usize, columns: usize) -> Result<usize, CmgError> {
    rows.checked_mul(columns).ok_or(CmgError::AllocationFailed {
        context: "CMG batch dimensions overflow",
    })
}

fn shape(cmg: &CmgPreconditioner, index: usize) -> (usize, usize, usize, usize) {
    let levels = cmg.hierarchy.levels();
    let rows = levels[index].graph().vertex_count();
    let coarse_rows = levels
        .get(index + 1)
        .map_or(0, |l| l.graph().vertex_count());
    let factor_rows = if index + 1 == levels.len() {
        cmg.direct_terminal
            .as_ref()
            .map_or(0, crate::GroundedLdl::active_dimension)
    } else {
        0
    };
    let components = cmg
        .coarse_centering
        .get(index)
        .map_or(0, |p| p.component_count());
    (rows, coarse_rows, factor_rows, components)
}

impl CmgBatchWorkspace {
    /// Checked requested heap layouts for this workspace, excluding the borrowed
    /// hierarchy, caller buffers, allocator overhead and any parallel runtime.
    pub fn allocation_bytes(cmg: &CmgPreconditioner, max_rhs: usize) -> Result<usize, CmgError> {
        Self::allocation_bytes_metered(cmg, max_rhs, &mut crate::cancel::Unmetered)
    }

    /// Cancellable `allocation_bytes`: the same value and errors, polling
    /// `cancel` between bounded level steps and once before returning.
    pub fn allocation_bytes_controlled(
        cmg: &CmgPreconditioner,
        max_rhs: usize,
        cancel: crate::Cancellation<'_>,
    ) -> Result<usize, CmgError> {
        Self::allocation_bytes_metered(cmg, max_rhs, &mut crate::cancel::CancelMeter::new(cancel))
    }

    fn allocation_bytes_metered<M: Meter>(
        cmg: &CmgPreconditioner,
        max_rhs: usize,
        meter: &mut M,
    ) -> Result<usize, CmgError> {
        if max_rhs == 0 {
            return Err(CmgError::InvalidOption {
                name: "CMG batch capacity",
                value: 0.0,
            });
        }
        if max_rhs == 1 {
            return cmg.workspace_allocation_bytes_metered(meter);
        }
        let mut total = bytes::<BatchLevel>(cmg.hierarchy.levels().len())?;
        for index in 0..cmg.hierarchy.levels().len() {
            meter.work("CMG batch layout", 64)?;
            let (rows, coarse, factor, components) = shape(cmg, index);
            let values = add(
                add(rows, area(coarse, 2)?)?,
                add(area(factor, 2)?, area(components, 3)?)?,
            )?;
            total = add(total, bytes::<f64>(area(values, max_rhs)?)?)?;
        }
        meter.checkpoint("CMG batch layout")?;
        Ok(total)
    }

    /// Admit all workspace layouts before the first allocation, then allocate
    /// each known-size buffer fallibly. Admission does not cover live parents.
    pub fn try_new(
        cmg: &CmgPreconditioner,
        max_rhs: usize,
        budget_bytes: usize,
    ) -> Result<Self, CmgError> {
        Self::try_new_metered(cmg, max_rhs, budget_bytes, &mut crate::cancel::Unmetered)
    }

    /// Cancellable `try_new` with the same layout, contents and errors. It
    /// polls `cancel` while computing the layout, before every allocation, at
    /// the fixed construction cadence while zero-filling and before returning.
    /// A true query drops the partial workspace and returns
    /// [`CmgError::Cancelled`].
    pub fn try_new_controlled(
        cmg: &CmgPreconditioner,
        max_rhs: usize,
        budget_bytes: usize,
        cancel: crate::Cancellation<'_>,
    ) -> Result<Self, CmgError> {
        Self::try_new_metered(
            cmg,
            max_rhs,
            budget_bytes,
            &mut crate::cancel::CancelMeter::new(cancel),
        )
    }

    fn try_new_metered<M: Meter>(
        cmg: &CmgPreconditioner,
        max_rhs: usize,
        budget_bytes: usize,
        meter: &mut M,
    ) -> Result<Self, CmgError> {
        if max_rhs == 1 {
            crate::storage::admit(cmg.workspace_allocation_bytes_metered(meter)?, budget_bytes)?;
            return Ok(Self {
                capacity: 1,
                storage: BatchStorage::Scalar(cmg.try_workspace_metered(meter)?),
            });
        }
        crate::storage::admit(
            Self::allocation_bytes_metered(cmg, max_rhs, meter)?,
            budget_bytes,
        )?;
        let mut levels = meter.try_capacity(cmg.hierarchy.levels().len(), "CMG batch levels")?;
        for index in 0..cmg.hierarchy.levels().len() {
            meter.work("CMG batch level", 64)?;
            let (rows, coarse_rows, factor_rows, components) = shape(cmg, index);
            let mut zero =
                |rows| meter.try_filled(area(rows, max_rhs)?, 0.0, "CMG batch workspace");
            levels.push(BatchLevel {
                rows,
                coarse_rows,
                factor_rows,
                components,
                residual: zero(rows)?,
                coarse_rhs: zero(coarse_rows)?,
                coarse_correction: zero(coarse_rows)?,
                forward: zero(factor_rows)?,
                factor_solution: zero(factor_rows)?,
                sums: zero(components)?,
                corrections: zero(components)?,
                means: zero(components)?,
            });
        }
        meter.checkpoint("CMG batch workspace finish")?;
        Ok(Self {
            capacity: max_rhs,
            storage: BatchStorage::Interleaved(levels),
        })
    }

    /// Maximum number of interleaved right-hand sides admitted at construction.
    pub const fn capacity(&self) -> usize {
        self.capacity
    }

    fn validate(&self, cmg: &CmgPreconditioner, columns: usize) -> Result<(), CmgError> {
        if columns > self.capacity {
            return Err(CmgError::dimension(
                "CMG batch capacity",
                self.capacity,
                columns,
            ));
        }
        let levels = match &self.storage {
            BatchStorage::Scalar(workspace) => return cmg.validate_workspace(workspace),
            BatchStorage::Interleaved(levels) => levels,
        };
        if levels.len() != cmg.hierarchy.levels().len() {
            return Err(CmgError::dimension(
                "CMG batch levels",
                cmg.hierarchy.levels().len(),
                levels.len(),
            ));
        }
        for (index, level) in levels.iter().enumerate() {
            if (
                level.rows,
                level.coarse_rows,
                level.factor_rows,
                level.components,
            ) != shape(cmg, index)
            {
                return Err(CmgError::InvalidHierarchy {
                    context: "CMG batch workspace has different level dimensions",
                });
            }
        }
        Ok(())
    }
}

impl LevelParts for BatchLevel {
    #[inline]
    fn parts(&mut self, columns: usize) -> Parts<'_> {
        Parts {
            residual: &mut self.residual[..self.rows * columns],
            coarse_rhs: &mut self.coarse_rhs[..self.coarse_rows * columns],
            coarse_correction: &mut self.coarse_correction[..self.coarse_rows * columns],
            forward: &mut self.forward[..self.factor_rows * columns],
            solution: &mut self.factor_solution[..self.factor_rows * columns],
            sums: &mut self.sums[..self.components * columns],
            corrections: &mut self.corrections[..self.components * columns],
            means: &mut self.means[..self.components * columns],
        }
    }
}

/// Interleaved levels of one batch application with its lane count.
struct Interleaved<'w> {
    levels: &'w mut [BatchLevel],
    columns: usize,
}

impl Store for Interleaved<'_> {
    type Width = usize;
    type Level = BatchLevel;
    const TEAM_CENTERING: bool = false;

    #[inline]
    fn width(&self) -> usize {
        self.columns
    }

    #[inline]
    fn take(&mut self, index: usize) -> BatchLevel {
        core::mem::take(&mut self.levels[index])
    }

    #[inline]
    fn put(&mut self, index: usize, level: BatchLevel) {
        self.levels[index] = level;
    }
}

impl CmgPreconditioner {
    /// Apply the fixed stationary cycle to component-compatible right-hand sides.
    ///
    /// Inputs and outputs have exactly `vertex_count * columns` entries, with
    /// lane `j` of vertex `i` at `i * columns + j`. `columns` may be zero and
    /// must not exceed workspace capacity. No CMG-owned allocation, fine-level
    /// projection, adaptive solve or stopping test occurs. Each column uses the
    /// scalar cycle's edge, transfer, terminal and compensated centering order;
    /// coarse sums keep that serial order independently of the context's team.
    /// A capacity-one workspace runs the scalar cycle under the same context.
    pub fn apply_compatible_batch_into(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        columns: usize,
        workspace: &mut CmgBatchWorkspace,
        context: ApplyContext<'_>,
    ) -> Result<(), CmgError> {
        context.validate(self)?;
        self.validate_batch(rhs, output, columns, workspace)?;
        if columns == 0 {
            return Ok(());
        }
        match &mut workspace.storage {
            BatchStorage::Scalar(workspace) => {
                self.apply_compatible_into_with(rhs, output, workspace, context)
            }
            BatchStorage::Interleaved(levels) => with_meter!(context.cancel, meter => {
                Cycle {
                    cmg: self,
                    store: Interleaved { levels, columns },
                    #[cfg(feature = "parallel")]
                    plan: context.plan,
                    #[cfg(feature = "parallel")]
                    team: context.team,
                }
                .level(0, rhs, output, 1, meter, &mut ())
            }),
        }
    }

    fn validate_batch(
        &self,
        rhs: &[f64],
        output: &[f64],
        columns: usize,
        workspace: &CmgBatchWorkspace,
    ) -> Result<(), CmgError> {
        let expected = area(self.hierarchy.levels()[0].graph().vertex_count(), columns)?;
        for (context, actual) in [
            ("CMG batch RHS", rhs.len()),
            ("CMG batch output", output.len()),
        ] {
            if actual != expected {
                return Err(CmgError::dimension(context, expected, actual));
            }
        }
        workspace.validate(self, columns)
    }
}
