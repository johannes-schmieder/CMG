//! Construction and diagnostics for the stationary CMG hierarchy.

#[cfg(feature = "parallel")]
use crate::ParallelExecutor;
use crate::forest::build_forest_aggregation_labels;
use crate::{Aggregation, CmgError, CmgOptions, Laplacian};
use std::time::Instant;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct HierarchyPhaseRecord {
    pub(crate) level: usize,
    pub(crate) phase: &'static str,
    pub(crate) nanoseconds: u128,
}

#[inline]
fn measure_hierarchy_phase<const PROFILE: bool, Output>(
    records: &mut Vec<HierarchyPhaseRecord>,
    level: usize,
    phase: &'static str,
    operation: impl FnOnce() -> Output,
) -> Output {
    if PROFILE {
        let start = Instant::now();
        let output = operation();
        records.push(HierarchyPhaseRecord {
            level,
            phase,
            nanoseconds: start.elapsed().as_nanos(),
        });
        output
    } else {
        operation()
    }
}

/// The reason hierarchy construction terminated.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TerminalReason {
    /// The graph was below the configured direct threshold.
    Direct,
    /// Forest grouping contracted the entire graph to one aggregate.
    FullContraction,
    /// Coarsening removed fewer than two vertices.
    StagnatedVertexReduction,
    /// Cumulative hierarchy nonzeros exceeded the configured fill guard.
    StagnatedFill,
    /// The configured hierarchy-level safety limit was reached.
    MaximumLevels,
}

impl TerminalReason {
    /// Return whether the terminal uses diagonal iteration instead of a direct
    /// factorization.
    #[must_use]
    pub const fn is_iterative(self) -> bool {
        !matches!(self, Self::Direct)
    }
}

// A level owns exactly one transfer representation. Keeping mutually exclusive
// formats in one enum avoids inflating every connected level with unused state.
#[derive(Debug, Clone, PartialEq, Eq)]
enum LevelTransfer {
    Full(Aggregation),
    #[cfg(feature = "experimental-components")]
    Pruned(crate::PrunedTransfer),
}

/// One immutable hierarchy level.
#[derive(Debug, Clone, PartialEq)]
pub struct HierarchyLevel {
    graph: Laplacian,
    inverse_diagonal: Vec<f64>,
    transfer: Option<LevelTransfer>,
    repeat: usize,
    terminal_reason: Option<TerminalReason>,
}

impl HierarchyLevel {
    /// Return the graph at this level.
    #[must_use]
    pub const fn graph(&self) -> &Laplacian {
        &self.graph
    }

    /// Return the upstream damped-Jacobi inverse diagonal `1 / (2 d_i)`.
    /// Isolated vertices receive zero.
    #[must_use]
    pub fn inverse_diagonal(&self) -> &[f64] {
        &self.inverse_diagonal
    }

    /// Return the full fine-to-coarse aggregation, when present.
    ///
    /// Experimental pruned levels instead expose `pruned_transfer`; they do
    /// not pretend that every fine row has a surviving coarse degree of freedom.
    #[must_use]
    pub const fn aggregation(&self) -> Option<&Aggregation> {
        match &self.transfer {
            Some(LevelTransfer::Full(aggregation)) => Some(aggregation),
            _ => None,
        }
    }

    /// Return the experimental partial transfer, if coarse isolates were retired.
    #[cfg(feature = "experimental-components")]
    #[must_use]
    pub const fn pruned_transfer(&self) -> Option<&crate::PrunedTransfer> {
        match &self.transfer {
            Some(LevelTransfer::Pruned(transfer)) => Some(transfer),
            _ => None,
        }
    }

    fn transfer_retained_bytes(&self) -> usize {
        match &self.transfer {
            Some(LevelTransfer::Full(aggregation)) => aggregation.retained_bytes(),
            #[cfg(feature = "experimental-components")]
            Some(LevelTransfer::Pruned(transfer)) => transfer.retained_bytes(),
            None => 0,
        }
    }

    #[inline]
    pub(crate) fn restrict_into(&self, fine: &[f64], coarse: &mut [f64]) -> Result<(), CmgError> {
        match self.transfer.as_ref().ok_or(CmgError::InvalidHierarchy {
            context: "nonterminal level has no transfer",
        })? {
            LevelTransfer::Full(aggregation) => aggregation.restrict_into(fine, coarse),
            #[cfg(feature = "experimental-components")]
            LevelTransfer::Pruned(transfer) => transfer.restrict_into(fine, coarse),
        }
    }

    #[inline]
    pub(crate) fn prolong_add_into(
        &self,
        coarse: &[f64],
        fine: &mut [f64],
    ) -> Result<(), CmgError> {
        match self.transfer.as_ref().ok_or(CmgError::InvalidHierarchy {
            context: "nonterminal level has no transfer",
        })? {
            LevelTransfer::Full(aggregation) => aggregation.prolong_add_into(coarse, fine),
            #[cfg(feature = "experimental-components")]
            LevelTransfer::Pruned(transfer) => transfer.prolong_add_into(coarse, fine),
        }
    }

    #[cfg(feature = "parallel")]
    #[inline]
    pub(crate) fn prolong_add_into_with_executor(
        &self,
        coarse: &[f64],
        fine: &mut [f64],
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        match self.transfer.as_ref().ok_or(CmgError::InvalidHierarchy {
            context: "nonterminal level has no transfer",
        })? {
            LevelTransfer::Full(aggregation) => {
                aggregation.prolong_add_into_with_executor(coarse, fine, executor)
            }
            #[cfg(feature = "experimental-components")]
            LevelTransfer::Pruned(transfer) => transfer.prolong_add_into(coarse, fine),
        }
    }

    /// Return the recursive repeat count.
    ///
    /// A hierarchy built by [`CmgHierarchy::build`] initially carries the
    /// nonzero-ratio estimate. When the hierarchy is owned by a complete CMG
    /// preconditioner, the level preceding a direct terminal is recalibrated
    /// from the grounded LDL factor exactly as in upstream CMG.
    #[must_use]
    pub const fn repeat(&self) -> usize {
        self.repeat
    }

    /// Return the terminal reason when this is the last level.
    #[must_use]
    pub const fn terminal_reason(&self) -> Option<TerminalReason> {
        self.terminal_reason
    }

    /// Return whether this is the terminal level.
    #[must_use]
    pub const fn is_terminal(&self) -> bool {
        self.terminal_reason.is_some()
    }
}

/// Summary diagnostics from hierarchy construction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HierarchyBuildReport {
    terminal_reason: TerminalReason,
    vertex_counts: Vec<usize>,
    matrix_nonzeros: Vec<usize>,
    cumulative_coarsened_nonzeros: usize,
}

impl HierarchyBuildReport {
    fn retained_bytes(&self) -> usize {
        self.vertex_counts
            .capacity()
            .saturating_mul(core::mem::size_of::<usize>())
            .saturating_add(
                self.matrix_nonzeros
                    .capacity()
                    .saturating_mul(core::mem::size_of::<usize>()),
            )
    }

    /// Return the terminal reason.
    #[must_use]
    pub const fn terminal_reason(&self) -> TerminalReason {
        self.terminal_reason
    }

    /// Return the vertex count at each stored level.
    #[must_use]
    pub fn vertex_counts(&self) -> &[usize] {
        &self.vertex_counts
    }

    /// Return the symmetric matrix nonzero count at each stored level.
    #[must_use]
    pub fn matrix_nonzeros(&self) -> &[usize] {
        &self.matrix_nonzeros
    }

    /// Return the cumulative nonzeros counted by the upstream fill guard.
    #[must_use]
    pub const fn cumulative_coarsened_nonzeros(&self) -> usize {
        self.cumulative_coarsened_nonzeros
    }
}

/// A deterministic CMG hierarchy.
#[derive(Debug, Clone, PartialEq)]
pub struct CmgHierarchy {
    levels: Vec<HierarchyLevel>,
    report: HierarchyBuildReport,
}

impl CmgHierarchy {
    /// Return principal retained heap bytes for every graph, diagonal smoother,
    /// aggregation, and hierarchy report. Allocator bookkeeping is excluded.
    #[must_use]
    pub fn retained_bytes(&self) -> usize {
        self.levels
            .iter()
            .fold(self.report.retained_bytes(), |bytes, level| {
                bytes
                    .saturating_add(level.graph.retained_bytes())
                    .saturating_add(
                        level
                            .inverse_diagonal
                            .capacity()
                            .saturating_mul(core::mem::size_of::<f64>()),
                    )
                    .saturating_add(level.transfer_retained_bytes())
            })
    }

    /// Build a hierarchy from a weighted graph Laplacian.
    pub fn build(graph: &Laplacian, options: CmgOptions) -> Result<Self, CmgError> {
        Self::build_with_kernels::<false, _, _>(
            graph,
            options,
            build_forest_aggregation_labels,
            |aggregation, current| aggregation.contract(current),
        )
    }

    #[cfg(feature = "experimental-components")]
    pub(crate) fn build_pruned(
        graph: &Laplacian,
        options: CmgOptions,
        preserve_stopping: bool,
    ) -> Result<Self, CmgError> {
        if preserve_stopping {
            return Self::build_with_kernels_impl::<false, true, true, _, _>(
                graph,
                options,
                build_forest_aggregation_labels,
                |aggregation, current| aggregation.contract(current),
            )
            .map(|(hierarchy, _)| hierarchy);
        }
        Self::build_with_kernels_impl::<false, true, false, _, _>(
            graph,
            options,
            build_forest_aggregation_labels,
            |aggregation, current| aggregation.contract(current),
        )
        .map(|(hierarchy, _)| hierarchy)
    }

    /// Build a hierarchy with deterministic parallel coarse-graph contraction.
    ///
    /// Forest selection and splitting remain serial in this checkpoint. The
    /// supplied package-owned executor maps and sorts coarse edges while
    /// preserving the exact serial hierarchy.
    #[cfg(feature = "parallel")]
    pub fn build_with_executor(
        graph: &Laplacian,
        options: CmgOptions,
        executor: &ParallelExecutor,
    ) -> Result<Self, CmgError> {
        Self::build_with_kernels::<false, _, _>(
            graph,
            options,
            build_forest_aggregation_labels,
            |aggregation, current| aggregation.contract_with_executor(current, executor),
        )
    }

    #[cfg(all(feature = "parallel", feature = "profiling"))]
    pub(crate) fn build_with_executor_profiled(
        graph: &Laplacian,
        options: CmgOptions,
        executor: &ParallelExecutor,
    ) -> Result<(Self, Vec<HierarchyPhaseRecord>), CmgError> {
        Self::build_with_kernels_profiled(
            graph,
            options,
            build_forest_aggregation_labels,
            |aggregation, current| aggregation.contract_with_executor(current, executor),
        )
    }

    fn build_with_kernels<const PROFILE: bool, Group, Contract>(
        graph: &Laplacian,
        options: CmgOptions,
        group: Group,
        contract: Contract,
    ) -> Result<Self, CmgError>
    where
        Group: FnMut(&Laplacian, f64) -> Result<(Vec<usize>, usize), CmgError>,
        Contract: FnMut(&Aggregation, &Laplacian) -> Result<Laplacian, CmgError>,
    {
        Self::build_with_kernels_impl::<PROFILE, false, false, _, _>(
            graph, options, group, contract,
        )
        .map(|(hierarchy, _)| hierarchy)
    }

    #[cfg(feature = "profiling")]
    fn build_with_kernels_profiled<Group, Contract>(
        graph: &Laplacian,
        options: CmgOptions,
        group: Group,
        contract: Contract,
    ) -> Result<(Self, Vec<HierarchyPhaseRecord>), CmgError>
    where
        Group: FnMut(&Laplacian, f64) -> Result<(Vec<usize>, usize), CmgError>,
        Contract: FnMut(&Aggregation, &Laplacian) -> Result<Laplacian, CmgError>,
    {
        Self::build_with_kernels_impl::<true, false, false, _, _>(graph, options, group, contract)
    }

    fn build_with_kernels_impl<
        const PROFILE: bool,
        const PRUNE: bool,
        const PRESERVE_STOPPING: bool,
        Group,
        Contract,
    >(
        graph: &Laplacian,
        options: CmgOptions,
        mut group: Group,
        mut contract: Contract,
    ) -> Result<(Self, Vec<HierarchyPhaseRecord>), CmgError>
    where
        Group: FnMut(&Laplacian, f64) -> Result<(Vec<usize>, usize), CmgError>,
        Contract: FnMut(&Aggregation, &Laplacian) -> Result<Laplacian, CmgError>,
    {
        let mut phase_records = Vec::new();
        let options = measure_hierarchy_phase::<PROFILE, _>(
            &mut phase_records,
            0,
            "option_validation",
            || options.validate(),
        )?;
        let initial_nonzeros = graph.matrix_nnz();
        let mut cumulative_nonzeros = 0_usize;
        let mut current = measure_hierarchy_phase::<PROFILE, _>(
            &mut phase_records,
            0,
            "graph_clone_reference_setup",
            || graph.clone(),
        );
        let mut levels = Vec::new();
        #[cfg(feature = "experimental-components")]
        let mut retired_vertices = 0usize;
        let terminal_reason;

        loop {
            let level_index = levels.len();
            let n = current.vertex_count();
            let decision_n = n;
            #[cfg(feature = "experimental-components")]
            let decision_n = decision_n + retired_vertices;
            let direct = measure_hierarchy_phase::<PROFILE, _>(
                &mut phase_records,
                level_index,
                "direct_terminal_check",
                || n == 0 || decision_n <= 1 || decision_n < options.direct_threshold,
            );
            if direct {
                terminal_reason = TerminalReason::Direct;
                let level = measure_hierarchy_phase::<PROFILE, _>(
                    &mut phase_records,
                    level_index,
                    "inverse_diagonal_and_level_finalization",
                    || make_level(current, None, 0, Some(terminal_reason)),
                );
                levels.push(level);
                break;
            }

            let (labels, aggregate_count) = measure_hierarchy_phase::<PROFILE, _>(
                &mut phase_records,
                level_index,
                "forest_select_split_low_degree_and_label",
                || group(&current, options.low_effective_degree_threshold),
            )?;
            let aggregation = measure_hierarchy_phase::<PROFILE, _>(
                &mut phase_records,
                level_index,
                "aggregation_construction",
                || Aggregation::from_forest_labels(labels, aggregate_count),
            );
            let coarse_count = aggregation.coarse_dimension();
            let decision_coarse_count = coarse_count;
            #[cfg(feature = "experimental-components")]
            let decision_coarse_count = decision_coarse_count + retired_vertices;
            let terminal = measure_hierarchy_phase::<PROFILE, _>(
                &mut phase_records,
                level_index,
                "hierarchy_bookkeeping_and_fill_checks",
                || {
                    if decision_coarse_count == 1 {
                        return Some(TerminalReason::FullContraction);
                    }
                    cumulative_nonzeros = cumulative_nonzeros.saturating_add(current.matrix_nnz());
                    if decision_coarse_count >= decision_n.saturating_sub(1) {
                        return Some(TerminalReason::StagnatedVertexReduction);
                    }
                    let fill_limit = options.max_hierarchy_nnz_factor * initial_nonzeros as f64;
                    if cumulative_nonzeros as f64 > fill_limit {
                        return Some(TerminalReason::StagnatedFill);
                    }
                    if levels.len() + 1 >= options.max_levels {
                        return Some(TerminalReason::MaximumLevels);
                    }
                    None
                },
            );
            if let Some(reason) = terminal {
                terminal_reason = reason;
                let level = measure_hierarchy_phase::<PROFILE, _>(
                    &mut phase_records,
                    level_index,
                    "inverse_diagonal_and_level_finalization",
                    || make_level(current, Some(aggregation), 0, Some(terminal_reason)),
                );
                levels.push(level);
                break;
            }

            let coarse = measure_hierarchy_phase::<PROFILE, _>(
                &mut phase_records,
                level_index,
                "coarse_edge_map_sort_merge_and_graph_finalization",
                || contract(&aggregation, &current),
            )?;
            #[cfg(feature = "experimental-components")]
            let (coarse, pruned_transfer) = if PRUNE {
                match crate::component_experiment::prune(&aggregation, &coarse) {
                    Some((transfer, compact)) => {
                        // Isolates contribute zero matrix nonzeros. Keep just
                        // their count for the ordinary vertex stopping checks;
                        // no retired row survives in graph or cycle storage.
                        if PRESERVE_STOPPING {
                            retired_vertices += coarse.vertex_count() - compact.vertex_count();
                        }
                        (compact, Some(transfer))
                    }
                    None => (coarse, None),
                }
            } else {
                (coarse, None)
            };
            let repeat = measure_hierarchy_phase::<PROFILE, _>(
                &mut phase_records,
                level_index,
                "repeat_count_initialization",
                || repeat_from_nonzeros(current.matrix_nnz(), coarse.matrix_nnz()),
            );
            let level = measure_hierarchy_phase::<PROFILE, _>(
                &mut phase_records,
                level_index,
                "inverse_diagonal_and_level_finalization",
                || make_level(current, Some(aggregation), repeat, None),
            );
            #[cfg(feature = "experimental-components")]
            let level = if let Some(transfer) = pruned_transfer {
                let mut level = level;
                level.transfer = Some(LevelTransfer::Pruned(transfer));
                level
            } else {
                level
            };
            levels.push(level);
            current = coarse;
        }

        let report = measure_hierarchy_phase::<PROFILE, _>(
            &mut phase_records,
            levels.len().saturating_sub(1),
            "hierarchy_report_bookkeeping",
            || HierarchyBuildReport {
                terminal_reason,
                vertex_counts: levels
                    .iter()
                    .map(|level| level.graph.vertex_count())
                    .collect(),
                matrix_nonzeros: levels
                    .iter()
                    .map(|level| level.graph.matrix_nnz())
                    .collect(),
                cumulative_coarsened_nonzeros: cumulative_nonzeros,
            },
        );
        Ok((Self { levels, report }, phase_records))
    }

    /// Return all levels from fine to coarse.
    #[must_use]
    pub fn levels(&self) -> &[HierarchyLevel] {
        &self.levels
    }

    /// Return the build report.
    #[must_use]
    pub const fn report(&self) -> &HierarchyBuildReport {
        &self.report
    }

    pub(crate) fn set_repeat(&mut self, level_index: usize, repeat: usize) -> Result<(), CmgError> {
        let level = self
            .levels
            .get_mut(level_index)
            .ok_or(CmgError::InvalidHierarchy {
                context: "repeat update references a missing hierarchy level",
            })?;
        if level.is_terminal() || repeat == 0 {
            return Err(CmgError::InvalidHierarchy {
                context: "repeat update must target a nonterminal level with a positive count",
            });
        }
        level.repeat = repeat;
        Ok(())
    }
}

fn make_level(
    graph: Laplacian,
    aggregation: Option<Aggregation>,
    repeat: usize,
    terminal_reason: Option<TerminalReason>,
) -> HierarchyLevel {
    let inverse_diagonal = graph
        .diagonal()
        .iter()
        .map(|degree| if *degree > 0.0 { 0.5 / *degree } else { 0.0 })
        .collect();
    HierarchyLevel {
        graph,
        inverse_diagonal,
        transfer: aggregation.map(LevelTransfer::Full),
        repeat,
        terminal_reason,
    }
}

fn repeat_from_nonzeros(fine_nonzeros: usize, coarse_nonzeros: usize) -> usize {
    if coarse_nonzeros == 0 {
        return 1;
    }
    (fine_nonzeros / coarse_nonzeros).saturating_sub(1).max(1)
}
