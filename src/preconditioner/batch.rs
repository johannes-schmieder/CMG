//! Interleaved, fixed-cycle CMG applications sharing one hierarchy traversal.

use super::CmgPreconditioner;
#[cfg(feature = "parallel")]
use super::ParallelCmgPlan;
#[cfg(feature = "parallel")]
use crate::ParallelExecutor;
use crate::storage::{add, bytes, try_capacity, try_filled};
use crate::{CmgError, TerminalReason};
#[cfg(feature = "parallel")]
use rayon::prelude::*;

#[derive(Debug, Clone)]
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
        if max_rhs == 0 {
            return Err(CmgError::InvalidOption {
                name: "CMG batch capacity",
                value: 0.0,
            });
        }
        if max_rhs == 1 {
            return cmg.workspace_allocation_bytes();
        }
        let mut total = bytes::<BatchLevel>(cmg.hierarchy.levels().len())?;
        for index in 0..cmg.hierarchy.levels().len() {
            let (rows, coarse, factor, components) = shape(cmg, index);
            let values = add(
                add(rows, area(coarse, 2)?)?,
                add(area(factor, 2)?, area(components, 3)?)?,
            )?;
            total = add(total, bytes::<f64>(area(values, max_rhs)?)?)?;
        }
        Ok(total)
    }

    /// Admit all workspace layouts before the first allocation, then allocate
    /// each known-size buffer fallibly. Admission does not cover live parents.
    pub fn try_new(
        cmg: &CmgPreconditioner,
        max_rhs: usize,
        budget_bytes: usize,
    ) -> Result<Self, CmgError> {
        crate::storage::admit(Self::allocation_bytes(cmg, max_rhs)?, budget_bytes)?;
        if max_rhs == 1 {
            return Ok(Self {
                capacity: 1,
                storage: BatchStorage::Scalar(cmg.try_workspace()?),
            });
        }
        let mut levels = try_capacity(cmg.hierarchy.levels().len(), "CMG batch levels")?;
        for index in 0..cmg.hierarchy.levels().len() {
            let (rows, coarse_rows, factor_rows, components) = shape(cmg, index);
            let zero = |rows| try_filled(area(rows, max_rhs)?, 0.0, "CMG batch workspace");
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

impl CmgPreconditioner {
    /// Apply the fixed stationary cycle to component-compatible right-hand sides.
    ///
    /// Inputs and outputs have exactly `vertex_count * columns` entries, with
    /// lane `j` of vertex `i` at `i * columns + j`. `columns` may be zero and
    /// must not exceed workspace capacity. No CMG-owned allocation, fine-level projection,
    /// adaptive solve or stopping test occurs. Each column uses the scalar
    /// cycle's edge, transfer, terminal and compensated centering order. A
    /// capacity-one workspace delegates to the unchanged scalar implementation.
    pub fn apply_compatible_batch_into(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        columns: usize,
        workspace: &mut CmgBatchWorkspace,
    ) -> Result<(), CmgError> {
        self.validate_batch(rhs, output, columns, workspace)?;
        if columns == 0 {
            return Ok(());
        }
        match &mut workspace.storage {
            BatchStorage::Scalar(workspace) => self.apply_compatible_into(rhs, output, workspace),
            BatchStorage::Interleaved(levels) => {
                self.apply_batch_level(0, rhs, output, columns, levels, 1, BatchExecution::serial())
            }
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

    #[allow(clippy::too_many_arguments)]
    fn apply_batch_level(
        &self,
        index: usize,
        rhs: &[f64],
        output: &mut [f64],
        columns: usize,
        workspace: &mut [BatchLevel],
        iterations: usize,
        execution: BatchExecution<'_>,
    ) -> Result<(), CmgError> {
        let level = &self.hierarchy.levels()[index];
        let (local, children) = workspace
            .split_first_mut()
            .ok_or(CmgError::InvalidHierarchy {
                context: "missing CMG batch level",
            })?;
        if let Some(reason) = level.terminal_reason() {
            if reason == TerminalReason::Direct {
                let factor = self
                    .direct_terminal
                    .as_ref()
                    .ok_or(CmgError::InvalidHierarchy {
                        context: "direct terminal is missing its LDL factor",
                    })?;
                factor.solve_batch_compatible(
                    rhs,
                    output,
                    columns,
                    &mut local.forward[..local.factor_rows * columns],
                    &mut local.factor_solution[..local.factor_rows * columns],
                );
            } else {
                execution.rows(output, columns, false, |row, values| {
                    for (lane, value) in values.iter_mut().enumerate() {
                        *value = level.inverse_diagonal()[row] * rhs[row * columns + lane];
                    }
                });
            }
            return Ok(());
        }
        let repeats = self.repeat_counts[index];
        if iterations == 0 || repeats == 0 {
            return Err(CmgError::InvalidHierarchy {
                context: "CMG batch level has zero stationary repeats",
            });
        }
        let residual = &mut local.residual[..local.rows * columns];
        let coarse_rhs = &mut local.coarse_rhs[..local.coarse_rows * columns];
        let correction = &mut local.coarse_correction[..local.coarse_rows * columns];
        for iteration in 0..iterations {
            if iteration == 0 {
                execution.rows(
                    output,
                    columns,
                    execution.parallel_level(index),
                    |row, values| {
                        for (lane, value) in values.iter_mut().enumerate() {
                            *value = level.inverse_diagonal()[row] * rhs[row * columns + lane];
                        }
                    },
                );
            } else {
                execution.matvec(index, level.graph(), output, residual, columns);
                execution.jacobi(
                    index,
                    output,
                    rhs,
                    residual,
                    level.inverse_diagonal(),
                    columns,
                );
            }
            if !coarse_rhs.is_empty() {
                execution.matvec(index, level.graph(), output, residual, columns);
                level.restrict_batch_residual(rhs, residual, coarse_rhs, columns)?;
                self.coarse_centering[index].center_batch(
                    coarse_rhs,
                    columns,
                    &mut local.sums[..local.components * columns],
                    &mut local.corrections[..local.components * columns],
                    &mut local.means[..local.components * columns],
                )?;
                self.apply_batch_level(
                    index + 1,
                    coarse_rhs,
                    correction,
                    columns,
                    children,
                    repeats,
                    execution,
                )?;
                level.prolong_batch_add(correction, output, columns)?;
            }
            execution.matvec(index, level.graph(), output, residual, columns);
            execution.jacobi(
                index,
                output,
                rhs,
                residual,
                level.inverse_diagonal(),
                columns,
            );
        }
        Ok(())
    }
}

#[cfg(feature = "parallel")]
impl ParallelCmgPlan {
    /// Apply interleaved fixed CMG cycles using this plan and its caller-owned
    /// executor. Layout, compatibility and capacity requirements match
    /// [`CmgPreconditioner::apply_compatible_batch_into`]. Row kernels share one
    /// graph traversal across columns; coarse sums retain serial compensated
    /// order, independently of worker count, rather than parallel chunk sums.
    pub fn apply_compatible_batch_into(
        &self,
        cmg: &CmgPreconditioner,
        rhs: &[f64],
        output: &mut [f64],
        columns: usize,
        workspace: &mut CmgBatchWorkspace,
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        self.validate(cmg)?;
        cmg.validate_batch(rhs, output, columns, workspace)?;
        if columns == 0 {
            return Ok(());
        }
        match &mut workspace.storage {
            BatchStorage::Scalar(workspace) => {
                self.apply_compatible_into(cmg, rhs, output, workspace, executor)
            }
            BatchStorage::Interleaved(levels) => cmg.apply_batch_level(
                0,
                rhs,
                output,
                columns,
                levels,
                1,
                BatchExecution {
                    plan: Some(self),
                    executor: Some(executor),
                },
            ),
        }
    }
}

#[derive(Clone, Copy)]
struct BatchExecution<'a> {
    #[cfg(feature = "parallel")]
    plan: Option<&'a ParallelCmgPlan>,
    #[cfg(feature = "parallel")]
    executor: Option<&'a ParallelExecutor>,
    #[cfg(not(feature = "parallel"))]
    marker: core::marker::PhantomData<&'a ()>,
}
impl BatchExecution<'_> {
    fn serial() -> Self {
        Self {
            #[cfg(feature = "parallel")]
            plan: None,
            #[cfg(feature = "parallel")]
            executor: None,
            #[cfg(not(feature = "parallel"))]
            marker: core::marker::PhantomData,
        }
    }
    fn parallel_level(self, index: usize) -> bool {
        #[cfg(feature = "parallel")]
        {
            self.plan
                .and_then(|plan| plan.level_operator(index))
                .is_some()
        }
        #[cfg(not(feature = "parallel"))]
        {
            let _ = index;
            false
        }
    }
    fn rows(
        self,
        output: &mut [f64],
        columns: usize,
        parallel: bool,
        operation: impl Fn(usize, &mut [f64]) + Send + Sync,
    ) {
        #[cfg(feature = "parallel")]
        if let Some(executor) = self.executor.filter(|_| parallel) {
            let chunk = executor.work_chunk_len(output.len() / columns) * columns;
            executor.install(|| {
                output
                    .par_chunks_mut(chunk)
                    .enumerate()
                    .for_each(|(block, values)| {
                        for (offset, row) in values.chunks_exact_mut(columns).enumerate() {
                            operation(block * (chunk / columns) + offset, row);
                        }
                    })
            });
            return;
        }
        let _ = parallel;
        for (row, values) in output.chunks_exact_mut(columns).enumerate() {
            operation(row, values);
        }
    }
    fn matvec(
        self,
        index: usize,
        graph: &crate::Laplacian,
        input: &[f64],
        output: &mut [f64],
        columns: usize,
    ) {
        #[cfg(feature = "parallel")]
        if let Some(operator) = self.plan.and_then(|plan| plan.level_operator(index)) {
            operator.matvec_batch(
                input,
                output,
                columns,
                self.executor.expect("batch plan executor"),
            );
            return;
        }
        let _ = index;
        output.fill(0.0);
        for edge in graph.edges() {
            let u = edge.u() * columns;
            let v = edge.v() * columns;
            for lane in 0..columns {
                let difference = edge.weight() * (input[u + lane] - input[v + lane]);
                output[u + lane] += difference;
                output[v + lane] -= difference;
            }
        }
    }
    fn jacobi(
        self,
        index: usize,
        output: &mut [f64],
        rhs: &[f64],
        matrix: &[f64],
        diagonal: &[f64],
        columns: usize,
    ) {
        self.rows(
            output,
            columns,
            self.parallel_level(index),
            |row, values| {
                for (lane, value) in values.iter_mut().enumerate() {
                    let offset = row * columns + lane;
                    *value += diagonal[row] * (rhs[offset] - matrix[offset]);
                }
            },
        );
    }
}
