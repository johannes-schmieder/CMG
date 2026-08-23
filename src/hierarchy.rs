//! Construction and diagnostics for the stationary CMG hierarchy.

use crate::{Aggregation, CmgError, CmgOptions, ForestGrouping, Laplacian, build_forest_grouping};
#[cfg(feature = "parallel")]
use crate::{ParallelExecutor, build_forest_grouping_with_executor};

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

/// One immutable hierarchy level.
#[derive(Debug, Clone, PartialEq)]
pub struct HierarchyLevel {
    graph: Laplacian,
    inverse_diagonal: Vec<f64>,
    aggregation: Option<Aggregation>,
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

    /// Return the fine-to-coarse aggregation for a non-direct level.
    #[must_use]
    pub const fn aggregation(&self) -> Option<&Aggregation> {
        self.aggregation.as_ref()
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
    /// Build a hierarchy from a weighted graph Laplacian.
    pub fn build(graph: &Laplacian, options: CmgOptions) -> Result<Self, CmgError> {
        Self::build_with_kernels(
            graph,
            options,
            build_forest_grouping,
            |aggregation, current| aggregation.contract(current),
        )
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
        Self::build_with_kernels(
            graph,
            options,
            |current, threshold| build_forest_grouping_with_executor(current, threshold, executor),
            |aggregation, current| aggregation.contract_with_executor(current, executor),
        )
    }

    fn build_with_kernels<Group, Contract>(
        graph: &Laplacian,
        options: CmgOptions,
        mut group: Group,
        mut contract: Contract,
    ) -> Result<Self, CmgError>
    where
        Group: FnMut(&Laplacian, f64) -> Result<ForestGrouping, CmgError>,
        Contract: FnMut(&Aggregation, &Laplacian) -> Result<Laplacian, CmgError>,
    {
        let options = options.validate()?;
        let initial_nonzeros = graph.matrix_nnz();
        let mut cumulative_nonzeros = 0_usize;
        let mut current = graph.clone();
        let mut levels = Vec::new();
        let terminal_reason;

        loop {
            let n = current.vertex_count();
            if n <= 1 || n < options.direct_threshold {
                terminal_reason = TerminalReason::Direct;
                levels.push(make_level(current, None, 0, Some(terminal_reason)));
                break;
            }

            let grouping = group(&current, options.low_effective_degree_threshold)?;
            let (labels, sizes) = grouping.into_aggregation_parts();
            let aggregation = Aggregation::from_forest_parts(labels, sizes);
            let coarse_count = aggregation.coarse_dimension();

            if coarse_count == 1 {
                terminal_reason = TerminalReason::FullContraction;
                levels.push(make_level(
                    current,
                    Some(aggregation),
                    0,
                    Some(terminal_reason),
                ));
                break;
            }

            cumulative_nonzeros = cumulative_nonzeros.saturating_add(current.matrix_nnz());
            if coarse_count >= n.saturating_sub(1) {
                terminal_reason = TerminalReason::StagnatedVertexReduction;
                levels.push(make_level(
                    current,
                    Some(aggregation),
                    0,
                    Some(terminal_reason),
                ));
                break;
            }

            let fill_limit = options.max_hierarchy_nnz_factor * initial_nonzeros as f64;
            if cumulative_nonzeros as f64 > fill_limit {
                terminal_reason = TerminalReason::StagnatedFill;
                levels.push(make_level(
                    current,
                    Some(aggregation),
                    0,
                    Some(terminal_reason),
                ));
                break;
            }

            if levels.len() + 1 >= options.max_levels {
                terminal_reason = TerminalReason::MaximumLevels;
                levels.push(make_level(
                    current,
                    Some(aggregation),
                    0,
                    Some(terminal_reason),
                ));
                break;
            }

            let coarse = contract(&aggregation, &current)?;
            let repeat = repeat_from_nonzeros(current.matrix_nnz(), coarse.matrix_nnz());
            levels.push(make_level(current, Some(aggregation), repeat, None));
            current = coarse;
        }

        let report = HierarchyBuildReport {
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
        };
        Ok(Self { levels, report })
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
        aggregation,
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
