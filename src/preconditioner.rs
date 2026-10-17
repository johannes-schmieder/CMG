//! Stationary recursive CMG preconditioner application.

mod batch;
#[cfg(feature = "experimental-components")]
pub use batch::CmgBatchRequirements;
pub use batch::CmgBatchWorkspace;

#[cfg(feature = "cycle-profiling")]
use crate::cmg_profile::{CmgApplyPhase, CycleRecorder};
use crate::components::CenteringPlan;
use crate::{
    CmgError, CmgHierarchy, CmgOptions, CmgWorkspace, Components, GroundedLdl, Laplacian,
    TerminalReason, ValidationOptions,
};
#[cfg(feature = "parallel")]
use crate::{CsrLaplacian, HierarchyLevel, ParallelExecutor};
#[cfg(feature = "parallel")]
use rayon::prelude::*;
use std::sync::Arc;
#[cfg(feature = "profiling")]
use std::time::Instant;

/// Timing for one hierarchy level considered during parallel-plan construction.
#[cfg(feature = "profiling")]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParallelPlanLevelProfile {
    level: usize,
    vertices: usize,
    edges: usize,
    eligible: bool,
    reason: &'static str,
    construction_nanoseconds: u128,
    row_counts_nanoseconds: u128,
    row_offsets_nanoseconds: u128,
    allocation_nanoseconds: u128,
    scatter_nanoseconds: u128,
    validation_nanoseconds: u128,
    retained_bytes: usize,
}

#[cfg(feature = "profiling")]
impl ParallelPlanLevelProfile {
    /// Return the zero-based hierarchy level.
    #[must_use]
    pub const fn level(&self) -> usize {
        self.level
    }

    /// Return the level's vertex count.
    #[must_use]
    pub const fn vertices(&self) -> usize {
        self.vertices
    }

    /// Return the level's canonical edge count.
    #[must_use]
    pub const fn edges(&self) -> usize {
        self.edges
    }

    /// Return whether the production eligibility rule retained an operator.
    #[must_use]
    pub const fn eligible(&self) -> bool {
        self.eligible
    }

    /// Return the production routing reason.
    #[must_use]
    pub const fn reason(&self) -> &'static str {
        self.reason
    }

    /// Return production operator-construction wall time.
    #[must_use]
    pub const fn construction_nanoseconds(&self) -> u128 {
        self.construction_nanoseconds
    }

    /// Return row-degree counting time.
    #[must_use]
    pub const fn row_counts_nanoseconds(&self) -> u128 {
        self.row_counts_nanoseconds
    }

    /// Return prefix-sum, compact row-offset, and scatter-cursor preparation time.
    #[must_use]
    pub const fn row_offsets_nanoseconds(&self) -> u128 {
        self.row_offsets_nanoseconds
    }

    /// Return column and weight allocation/initialization time.
    #[must_use]
    pub const fn allocation_nanoseconds(&self) -> u128 {
        self.allocation_nanoseconds
    }

    /// Return deterministic edge-scatter time.
    #[must_use]
    pub const fn scatter_nanoseconds(&self) -> u128 {
        self.scatter_nanoseconds
    }

    /// Return production invariant-validation time.
    #[must_use]
    pub const fn validation_nanoseconds(&self) -> u128 {
        self.validation_nanoseconds
    }

    /// Return retained bytes for this level's operator.
    #[must_use]
    pub const fn retained_bytes(&self) -> usize {
        self.retained_bytes
    }
}

/// Production parallel-plan profile, recorded without changing the retained plan.
#[cfg(feature = "profiling")]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParallelPlanBuildProfile {
    levels: Vec<ParallelPlanLevelProfile>,
    total_nanoseconds: u128,
}

#[cfg(feature = "profiling")]
impl ParallelPlanBuildProfile {
    /// Return per-level eligibility and construction records.
    #[must_use]
    pub fn levels(&self) -> &[ParallelPlanLevelProfile] {
        &self.levels
    }

    /// Return complete production-plan construction wall time.
    #[must_use]
    pub const fn total_nanoseconds(&self) -> u128 {
        self.total_nanoseconds
    }
}

/// One phase from the exact production hierarchy control path.
#[cfg(feature = "profiling")]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HierarchyPhaseProfile {
    level: usize,
    phase: &'static str,
    nanoseconds: u128,
}

#[cfg(feature = "profiling")]
impl HierarchyPhaseProfile {
    /// Return the zero-based hierarchy level.
    #[must_use]
    pub const fn level(&self) -> usize {
        self.level
    }

    /// Return the production phase label.
    #[must_use]
    pub const fn phase(&self) -> &'static str {
        self.phase
    }

    /// Return elapsed wall time for this phase.
    #[must_use]
    pub const fn nanoseconds(&self) -> u128 {
        self.nanoseconds
    }
}

/// Production hierarchy and preconditioner-finalization wall times.
#[cfg(feature = "profiling")]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreconditionerBuildProfile {
    hierarchy_nanoseconds: u128,
    hierarchy_phases: Vec<HierarchyPhaseProfile>,
    finalization_nanoseconds: u128,
    total_nanoseconds: u128,
}

#[cfg(feature = "profiling")]
impl PreconditionerBuildProfile {
    /// Return production hierarchy construction wall time.
    #[must_use]
    pub const fn hierarchy_nanoseconds(&self) -> u128 {
        self.hierarchy_nanoseconds
    }

    /// Return per-level records from the shared production hierarchy path.
    #[must_use]
    pub fn hierarchy_phases(&self) -> &[HierarchyPhaseProfile] {
        &self.hierarchy_phases
    }

    /// Return component metadata, centering, terminal factor, and repeat-finalization time.
    #[must_use]
    pub const fn finalization_nanoseconds(&self) -> u128 {
        self.finalization_nanoseconds
    }

    /// Return complete production preconditioner construction wall time.
    #[must_use]
    pub const fn total_nanoseconds(&self) -> u128 {
        self.total_nanoseconds
    }
}

/// Precomputed row-oriented operators for deterministic parallel CMG application.
///
/// The plan is optional and separate from [`CmgPreconditioner`], so serial users
/// retain the compact one-edge-per-undirected-edge hierarchy without duplicated
/// adjacency storage. Operators are retained only for nonterminal levels large
/// enough to use the supplied executor.
#[cfg(feature = "parallel")]
#[derive(Debug, Clone)]
pub struct ParallelCmgPlan {
    level_operators: Vec<Option<CsrLaplacian>>,
    level_lineages: Vec<Arc<()>>,
}

#[cfg(feature = "parallel")]
impl ParallelCmgPlan {
    /// Peak new CMG-owned requested layouts for plan construction, including
    /// descriptor banks, retained earlier operators and current CSR scratch.
    /// Borrowed hierarchy/lineage, Rayon runtime and allocator/OS storage are
    /// excluded. This is not a complete action/runtime admission bound.
    pub fn construction_allocation_bound(
        preconditioner: &CmgPreconditioner,
        executor: &ParallelExecutor,
    ) -> Result<usize, CmgError> {
        use crate::storage::{add, bytes};
        let levels = preconditioner.hierarchy.levels();
        let mut retained = add(
            bytes::<Option<CsrLaplacian>>(levels.len())?,
            bytes::<Arc<()>>(levels.len())?,
        )?;
        let mut peak = retained;
        for level in levels {
            if Self::level_is_eligible(level, executor) {
                let graph = level.graph();
                peak = peak.max(add(
                    retained,
                    CsrLaplacian::construction_allocation_bound_with_executor(graph, executor)?,
                )?);
                retained = add(
                    retained,
                    CsrLaplacian::retained_allocation_bytes(
                        graph.vertex_count(),
                        graph.edge_count(),
                    )?,
                )?;
            }
        }
        Ok(peak)
    }

    /// Admit new CMG-owned plan layouts before allocating, without changing
    /// operator eligibility or parallel setup. Requires separate admission of
    /// live parents/old plans and a policy for the uncovered Rayon runtime.
    pub fn build_with_budget(
        preconditioner: &CmgPreconditioner,
        executor: &ParallelExecutor,
        budget_bytes: usize,
    ) -> Result<Self, CmgError> {
        crate::storage::admit(
            Self::construction_allocation_bound(preconditioner, executor)?,
            budget_bytes,
        )?;
        Self::build(preconditioner, executor)
    }

    pub(crate) fn eligible_operator_count(
        preconditioner: &CmgPreconditioner,
        executor: &ParallelExecutor,
    ) -> usize {
        preconditioner
            .hierarchy
            .levels()
            .iter()
            .filter(|level| Self::level_is_eligible(level, executor))
            .count()
    }

    fn level_is_eligible(level: &HierarchyLevel, executor: &ParallelExecutor) -> bool {
        let graph = level.graph();
        let density_floor = graph
            .vertex_count()
            .saturating_add(graph.vertex_count() / 4);
        level.terminal_reason().is_none()
            && graph.edges().len() >= density_floor
            && executor.should_parallel(graph.edges().len())
    }

    /// Build deterministic row operators for one immutable preconditioner.
    pub fn build(
        preconditioner: &CmgPreconditioner,
        executor: &ParallelExecutor,
    ) -> Result<Self, CmgError> {
        let levels = preconditioner.hierarchy.levels();
        let mut level_operators = crate::storage::try_capacity(levels.len(), "plan operators")?;
        let mut level_lineages = crate::storage::try_capacity(levels.len(), "plan lineages")?;
        for level in levels {
            let graph = level.graph();
            let operator = if Self::level_is_eligible(level, executor) {
                Some(CsrLaplacian::from_laplacian_with_executor(graph, executor)?)
            } else {
                None
            };
            level_operators.push(operator);
            level_lineages.push(Arc::clone(graph.lineage()));
        }
        Ok(Self {
            level_operators,
            level_lineages,
        })
    }

    /// Build the exact production plan and retain per-level eligibility timings.
    #[cfg(feature = "profiling")]
    pub fn build_profiled(
        preconditioner: &CmgPreconditioner,
        executor: &ParallelExecutor,
    ) -> Result<(Self, ParallelPlanBuildProfile), CmgError> {
        let total_start = Instant::now();
        let mut level_operators = Vec::with_capacity(preconditioner.hierarchy.levels().len());
        let mut profiles = Vec::with_capacity(preconditioner.hierarchy.levels().len());
        for (level_index, level) in preconditioner.hierarchy.levels().iter().enumerate() {
            let graph = level.graph();
            let density_floor = graph
                .vertex_count()
                .saturating_add(graph.vertex_count() / 4);
            let reason = if level.terminal_reason().is_some() {
                "terminal"
            } else if graph.edges().len() < density_floor {
                "below-density-floor"
            } else if !executor.should_parallel(graph.edges().len()) {
                "below-executor-threshold"
            } else {
                "eligible"
            };
            let eligible = reason == "eligible";
            let start = Instant::now();
            let (operator, csr_profile) = if eligible {
                let (operator, profile) =
                    CsrLaplacian::from_laplacian_with_executor_profiled(graph, executor)?;
                (Some(operator), profile)
            } else {
                (None, Default::default())
            };
            let construction_nanoseconds = start.elapsed().as_nanos();
            profiles.push(ParallelPlanLevelProfile {
                level: level_index,
                vertices: graph.vertex_count(),
                edges: graph.edge_count(),
                eligible,
                reason,
                construction_nanoseconds,
                row_counts_nanoseconds: csr_profile.row_counts_nanoseconds,
                row_offsets_nanoseconds: csr_profile.row_offsets_nanoseconds,
                allocation_nanoseconds: csr_profile.allocation_nanoseconds,
                scatter_nanoseconds: csr_profile.scatter_nanoseconds,
                validation_nanoseconds: csr_profile.validation_nanoseconds,
                retained_bytes: operator.as_ref().map_or(0, CsrLaplacian::byte_len),
            });
            level_operators.push(operator);
        }
        let level_lineages = preconditioner
            .hierarchy
            .levels()
            .iter()
            .map(|level| Arc::clone(level.graph().lineage()))
            .collect();
        let plan = Self {
            level_operators,
            level_lineages,
        };
        let profile = ParallelPlanBuildProfile {
            levels: profiles,
            total_nanoseconds: total_start.elapsed().as_nanos(),
        };
        Ok((plan, profile))
    }

    /// Return the number of hierarchy levels carrying a row operator.
    #[must_use]
    pub fn operator_count(&self) -> usize {
        self.level_operators.iter().flatten().count()
    }

    /// Return retained heap bytes for all row operators.
    #[must_use]
    pub fn byte_len(&self) -> usize {
        self.level_operators
            .iter()
            .flatten()
            .map(CsrLaplacian::byte_len)
            .sum()
    }

    /// Apply a component-compatible right-hand side with deterministic parallel
    /// level kernels and caller-owned workspace.
    pub fn apply_compatible_into(
        &self,
        preconditioner: &CmgPreconditioner,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        self.apply_compatible_into_with_validation(
            preconditioner,
            rhs,
            output,
            workspace,
            ValidationOptions::default(),
            executor,
        )
    }

    /// Apply a compatible right-hand side with explicit validation tolerances.
    pub fn apply_compatible_into_with_validation(
        &self,
        preconditioner: &CmgPreconditioner,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        validation: ValidationOptions,
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        preconditioner
            .apply_compatible_into_with_plan(rhs, output, workspace, validation, self, executor)
    }

    pub(crate) fn apply_compatible_into_prevalidated(
        &self,
        preconditioner: &CmgPreconditioner,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        validation: ValidationOptions,
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        preconditioner.apply_compatible_into_with_prevalidated_plan(
            rhs, output, workspace, validation, self, executor,
        )
    }

    pub(crate) fn validate(&self, preconditioner: &CmgPreconditioner) -> Result<(), CmgError> {
        if self.level_operators.len() != preconditioner.hierarchy.levels().len()
            || self.level_lineages.len() != preconditioner.hierarchy.levels().len()
        {
            return Err(CmgError::dimension(
                "ParallelCmgPlan level count",
                preconditioner.hierarchy.levels().len(),
                self.level_operators.len().min(self.level_lineages.len()),
            ));
        }
        for ((operator, lineage), level) in self
            .level_operators
            .iter()
            .zip(&self.level_lineages)
            .zip(preconditioner.hierarchy.levels())
        {
            if !Arc::ptr_eq(lineage, level.graph().lineage())
                || operator
                    .as_ref()
                    .is_some_and(|operator| !operator.shares_lineage(level.graph()))
            {
                return Err(CmgError::InvalidHierarchy {
                    context: "parallel CMG plan belongs to a different hierarchy",
                });
            }
        }
        Ok(())
    }

    fn level_operator(&self, level_index: usize) -> Option<&CsrLaplacian> {
        self.level_operators
            .get(level_index)
            .and_then(Option::as_ref)
    }

    pub(crate) fn finest_matvec_into(
        &self,
        graph: &Laplacian,
        input: &[f64],
        output: &mut [f64],
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        self.matvec_into(0, graph, input, output, executor)
    }

    fn matvec_into(
        &self,
        level_index: usize,
        graph: &Laplacian,
        input: &[f64],
        output: &mut [f64],
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        match self.level_operator(level_index) {
            Some(operator) => operator.matvec_into_parallel(input, output, executor),
            None => graph.matvec_into(input, output),
        }
    }
}

/// An immutable stationary CMG preconditioner.
#[derive(Debug, Clone, PartialEq)]
pub struct CmgPreconditioner {
    hierarchy: CmgHierarchy,
    finest_components: Arc<Components>,
    coarse_centering: Vec<CenteringPlan>,
    direct_terminal: Option<GroundedLdl>,
    repeat_counts: Vec<usize>,
}

crate::storage::storage_fields!(
    CmgPreconditioner,
    hierarchy,
    finest_components,
    coarse_centering,
    direct_terminal,
    repeat_counts
);
#[cfg(feature = "parallel")]
crate::storage::storage_fields!(ParallelCmgPlan, level_operators, level_lineages);

impl CmgPreconditioner {
    /// Conservative requested storage for one scalar application workspace,
    /// before constructing a hierarchy or its caches.
    ///
    /// Bounds [`Self::workspace_allocation_bytes`] when the initial vertex count,
    /// every level dimension/component count, and terminal active dimension are
    /// at most `vertices`, with at most `options.max_levels` stored levels. This
    /// includes level/dimension/centering descriptors, finest projection banks,
    /// per-level residual/coarse/centering arrays, and terminal factor work arrays.
    ///
    /// The query allocates nothing and takes constant work. All options are
    /// validated first; invalid options or size/layout overflow return an error.
    /// The bound excludes the retained preconditioner, input/output buffers,
    /// batch workspaces, parallel runtime/caches, inline root values and allocator
    /// overhead. It is neither cumulative construction admission nor an RSS bound.
    pub fn scalar_workspace_allocation_bound(
        vertices: usize,
        options: CmgOptions,
    ) -> Result<usize, CmgError> {
        let options = options.validate()?;
        CmgWorkspace::allocation_bound(vertices, options.max_levels)
    }

    /// Checked requested layouts for one scalar application workspace.
    /// Excludes allocator overhead and the retained preconditioner itself.
    pub fn workspace_allocation_bytes(&self) -> Result<usize, CmgError> {
        CmgWorkspace::allocation_bytes(
            &self.hierarchy,
            self.direct_terminal.as_ref(),
            &self.finest_components,
            &self.coarse_centering,
        )
    }
    /// Count scalar workspace layouts with bounded work gates and no allocation.
    ///
    /// Returns the same requested byte count as `workspace_allocation_bytes`,
    /// excluding allocator overhead and the retained preconditioner itself.
    #[cfg(feature = "experimental-components")]
    pub fn workspace_allocation_bytes_controlled(
        &self,
        work_budget: u64,
        control: &mut dyn crate::BuildControl,
    ) -> Result<usize, CmgError> {
        let mut meter = crate::build_control::BuildMeter::new(work_budget, control);
        CmgWorkspace::allocation_bytes_controlled(
            &self.hierarchy,
            self.direct_terminal.as_ref(),
            &self.finest_components,
            &self.coarse_centering,
            &mut meter,
        )
    }

    #[cfg(feature = "parallel")]
    pub(crate) fn workspace_bytes(&self) -> usize {
        CmgWorkspace::required_bytes(
            &self.hierarchy,
            self.direct_terminal.as_ref(),
            &self.finest_components,
            &self.coarse_centering,
        )
    }

    pub(crate) fn validate_workspace(&self, workspace: &CmgWorkspace) -> Result<(), CmgError> {
        workspace.validate(
            &self.hierarchy,
            self.direct_terminal.as_ref(),
            &self.finest_components,
            &self.coarse_centering,
        )
    }

    /// Return principal retained heap bytes for the complete immutable
    /// preconditioner, including hierarchy, component metadata, terminal
    /// factorization, and recursive repeat counts.
    #[must_use]
    pub fn retained_bytes(&self) -> usize {
        self.hierarchy
            .retained_bytes()
            .saturating_add(self.component_metadata_bytes())
            .saturating_add(
                self.direct_terminal
                    .as_ref()
                    .map_or(0, GroundedLdl::byte_len),
            )
            .saturating_add(
                self.repeat_counts
                    .capacity()
                    .saturating_mul(core::mem::size_of::<usize>()),
            )
    }

    /// Build the complete hierarchy and any direct terminal factorization.
    pub fn build(graph: &Laplacian, options: CmgOptions) -> Result<Self, CmgError> {
        Self::from_hierarchy(CmgHierarchy::build(graph, options)?)
    }

    /// New requested layouts for an explicitly serial ordinary build, including
    /// hierarchy, centering metadata and direct factor construction. Borrowed
    /// graph/prepared topology, allocator/OS storage, runtime, solve workspaces,
    /// plans and later lazy aggregation accessors are excluded. Arc layouts are
    /// counted under the reviewed, non-ABI-stable standard-library model.
    pub fn serial_construction_allocation_bound(
        vertices: usize,
        edges: usize,
        options: CmgOptions,
    ) -> Result<usize, CmgError> {
        Self::serial_construction_bound_impl(vertices, edges, options, false)
    }

    fn serial_construction_bound_impl(
        n: usize,
        m: usize,
        options: CmgOptions,
        component_factor: bool,
    ) -> Result<usize, CmgError> {
        use crate::storage::{add, arc_bytes, bytes};
        let options = options.validate()?;
        let (hierarchy, scratch) = CmgHierarchy::serial_allocation_parts(n, m, options)?;
        let count = n
            .checked_mul(options.max_levels)
            .ok_or(CmgError::AllocationFailed {
                context: "centering budget overflows",
            })?;
        let metadata = add(
            add(bytes::<[usize; 2]>(count)?, arc_bytes::<Components>()?)?,
            add(
                bytes::<CenteringPlan>(options.max_levels - 1)?,
                bytes::<usize>(options.max_levels)?,
            )?,
        )?;
        // Direct terminals have n < threshold, except the n<=1/empty shortcut.
        // No other terminal reason factors the graph. Pruning only reduces n.
        let terminal_n = n.min(options.direct_threshold.saturating_sub(1).max(1));
        let factor = {
            #[cfg(feature = "experimental-components")]
            if component_factor {
                GroundedLdl::component_construction_allocation_bound(terminal_n, m)?
            } else {
                GroundedLdl::construction_allocation_bound(terminal_n)?
            }
            #[cfg(not(feature = "experimental-components"))]
            {
                let _ = component_factor;
                GroundedLdl::construction_allocation_bound(terminal_n)?
            }
        };
        let finalization = add(metadata, factor.max(bytes::<[usize; 4]>(n)?))?;
        add(hierarchy, scratch.max(finalization))
    }

    /// Admit the explicitly serial ordinary construction path. All vector
    /// provisioning is fallible; small Arc allocations remain infallible on
    /// stable Rust. This is not an executor/runtime admission API.
    pub fn build_serial_with_budget(
        graph: &Laplacian,
        options: CmgOptions,
        budget_bytes: usize,
    ) -> Result<Self, CmgError> {
        crate::storage::admit(
            Self::serial_construction_allocation_bound(
                graph.vertex_count(),
                graph.edge_count(),
                options,
            )?,
            budget_bytes,
        )?;
        let hierarchy = CmgHierarchy::build_serial_with_budget(graph, options, budget_bytes)?;
        Self::from_hierarchy_with_factor_impl::<true>(hierarchy, |graph| {
            GroundedLdl::factor_with_budget(graph, budget_bytes)
        })
    }

    /// Counterpart of the ordinary serial bound for opt-in component setup.
    /// The same hierarchy bound covers either pruning stopping policy; only
    /// the selected terminal-factor construction changes the factor allowance.
    #[cfg(feature = "experimental-components")]
    pub fn component_serial_construction_allocation_bound(
        vertices: usize,
        edges: usize,
        options: CmgOptions,
        experiment: crate::ComponentBuildOptions,
    ) -> Result<usize, CmgError> {
        Self::serial_construction_bound_impl(
            vertices,
            edges,
            options,
            experiment.factor_terminal_components,
        )
    }

    /// Explicitly serial admitted component construction. Does not enable any
    /// experimental option automatically, create a pool, or change certification.
    /// It may be used with a prepared numeric graph without duplicating topology.
    #[cfg(feature = "experimental-components")]
    pub fn build_component_experiment_serial_with_budget(
        graph: &Laplacian,
        options: CmgOptions,
        experiment: crate::ComponentBuildOptions,
        budget_bytes: usize,
    ) -> Result<Self, CmgError> {
        crate::storage::admit(
            Self::component_serial_construction_allocation_bound(
                graph.vertex_count(),
                graph.edge_count(),
                options,
                experiment,
            )?,
            budget_bytes,
        )?;
        let hierarchy = if experiment.prune_coarse_isolates {
            CmgHierarchy::build_pruned_serial_admitted(
                graph,
                options,
                experiment.preserve_unpruned_stopping,
            )?
        } else {
            CmgHierarchy::build_serial_with_budget(graph, options, budget_bytes)?
        };
        if experiment.factor_terminal_components {
            Self::from_hierarchy_with_factor_impl::<true>(hierarchy, |graph| {
                GroundedLdl::factor_by_component_with_budget(graph, budget_bytes)
            })
        } else {
            Self::from_hierarchy_with_factor_impl::<true>(hierarchy, |graph| {
                GroundedLdl::factor_with_budget(graph, budget_bytes)
            })
        }
    }

    /// Build the serial component candidate with cooperative, precharged work.
    ///
    /// This uses the same hierarchy, stopping rules and arithmetic as the admitted
    /// serial builder. The caller-supplied graph is already prepared; construction
    /// owns only private candidate state until success. Computational chunks have
    /// at most 1024 primitive work units. Allocator/OS and cleanup latency are
    /// separately excluded, and allocations are gated before they begin. The work
    /// budget includes conservative allowances rather than measured CPU time.
    /// No thread pool or parallel application plan is created. The callback may
    /// block to park construction, then grant the next chunk or drain the build.
    #[cfg(feature = "experimental-components")]
    pub fn build_component_experiment_serial_controlled(
        graph: &Laplacian,
        options: CmgOptions,
        experiment: crate::ComponentBuildOptions,
        budget_bytes: usize,
        work_budget: u64,
        control: &mut dyn crate::BuildControl,
    ) -> Result<Self, CmgError> {
        Self::build_component_controlled_impl::<false>(
            graph,
            options,
            experiment,
            budget_bytes,
            work_budget,
            control,
        )
    }

    /// Controlled component construction with an opt-in factor-affordability screen.
    ///
    /// Requires `experiment.factor_terminal_components`. At an otherwise direct
    /// terminal, charge 1024 work units for a checked, dimension-only bound on
    /// the complete component factor's charged work. Admit direct factorization
    /// only if that bound fits the then-remaining work budget. Otherwise continue
    /// the existing coarsening/stopping path, before any factor allocation or
    /// numeric work; an empty/singleton refusal returns work-budget exhaustion.
    /// Overflow in the factor bound also declines direct factorization.
    ///
    /// This is a screen, not a reservation or completion guarantee: reporting,
    /// centering, factorization and later obligations retain all existing gates
    /// and can still fail. Memory admission, thresholds and stopping limits are
    /// unchanged. The completed action is fixed across applications, but its
    /// hierarchy may differ from the ordinary controlled constructor under a
    /// finite budget. That constructor remains exact and pays no new charges.
    #[cfg(feature = "experimental-components")]
    pub fn build_component_experiment_serial_controlled_with_factor_preflight(
        graph: &Laplacian,
        options: CmgOptions,
        experiment: crate::ComponentBuildOptions,
        budget_bytes: usize,
        work_budget: u64,
        control: &mut dyn crate::BuildControl,
    ) -> Result<Self, CmgError> {
        if !experiment.factor_terminal_components {
            return Err(CmgError::InvalidOption {
                name: "factor_terminal_components",
                value: 0.0,
            });
        }
        Self::build_component_controlled_impl::<true>(
            graph,
            options,
            experiment,
            budget_bytes,
            work_budget,
            control,
        )
    }

    #[cfg(feature = "experimental-components")]
    fn build_component_controlled_impl<const FACTOR_PREFLIGHT: bool>(
        graph: &Laplacian,
        options: CmgOptions,
        experiment: crate::ComponentBuildOptions,
        budget_bytes: usize,
        work_budget: u64,
        control: &mut dyn crate::BuildControl,
    ) -> Result<Self, CmgError> {
        let mut meter = crate::build_control::BuildMeter::new(work_budget, control);
        // The allocation-bound formula uses fixed-work checked scalar arithmetic.
        meter.work("preconditioner admission", 1024)?;
        crate::storage::admit(
            Self::component_serial_construction_allocation_bound(
                graph.vertex_count(),
                graph.edge_count(),
                options,
                experiment,
            )?,
            budget_bytes,
        )?;
        let mut hierarchy = CmgHierarchy::build_component_controlled::<FACTOR_PREFLIGHT>(
            graph, options, experiment, &mut meter,
        )?;
        meter.work("preconditioner finest components", 64)?;
        let finest = hierarchy
            .levels()
            .first()
            .ok_or(CmgError::InvalidHierarchy {
                context: "hierarchy contains no finest level",
            })?;
        let finest_components = if let Some(components) = finest.graph().prepared_components() {
            Arc::clone(components)
        } else {
            let components = Components::try_from_endpoints_controlled(
                finest.graph().vertex_count(),
                finest
                    .graph()
                    .edges()
                    .iter()
                    .map(|edge| (edge.u(), edge.v())),
                &mut meter,
            )?;
            meter.allocation(
                "finest component owner",
                crate::storage::arc_bytes::<Components>()?,
            )?;
            Arc::new(components)
        };
        let mut coarse_centering =
            meter.try_capacity(hierarchy.levels().len() - 1, "coarse centering plans")?;
        for level in hierarchy.levels().iter().skip(1) {
            meter.work("coarse centering plan", 32)?;
            coarse_centering.push(CenteringPlan::try_from_laplacian_controlled(
                level.graph(),
                &mut meter,
            )?);
        }
        meter.work("preconditioner terminal decision", 64)?;
        let direct_terminal = if hierarchy.report().terminal_reason() == TerminalReason::Direct {
            let terminal = hierarchy
                .levels()
                .last()
                .ok_or(CmgError::InvalidHierarchy {
                    context: "hierarchy contains no terminal level",
                })?;
            Some(if experiment.factor_terminal_components {
                GroundedLdl::factor_by_component_controlled(
                    terminal.graph(),
                    budget_bytes,
                    &mut meter,
                )?
            } else {
                GroundedLdl::factor_controlled(terminal.graph(), budget_bytes, &mut meter)?
            })
        } else {
            None
        };
        let mut repeat_counts =
            meter.try_capacity(hierarchy.levels().len(), "preconditioner repeat counts")?;
        for level in hierarchy.levels() {
            meter.work("preconditioner repeat counts", 16)?;
            repeat_counts.push(level.repeat());
        }
        meter.work("preconditioner factor repeat adjustment", 128)?;
        if hierarchy.levels().len() >= 2 {
            if let Some(factor) = &direct_terminal {
                let penultimate = hierarchy.levels().len() - 2;
                let repeat = repeat_from_nonzeros(
                    hierarchy.levels()[penultimate].graph().matrix_nnz(),
                    factor.factor_nonzeros(),
                );
                repeat_counts[penultimate] = repeat;
                hierarchy.set_repeat(penultimate, repeat)?;
            }
        }
        meter.work("preconditioner publication", 64)?;
        Ok(Self {
            hierarchy,
            finest_components,
            coarse_centering,
            direct_terminal,
            repeat_counts,
        })
    }

    /// New CMG-owned layouts for an admitted executor-aware ordinary build.
    /// Rayon runtime and the borrowed executor/graph are explicitly excluded;
    /// all other exclusions of the serial construction bound also apply.
    #[cfg(feature = "parallel")]
    pub fn executor_construction_cmg_allocation_bound(
        vertices: usize,
        edges: usize,
        options: CmgOptions,
    ) -> Result<usize, CmgError> {
        crate::storage::add(
            Self::serial_construction_allocation_bound(vertices, edges, options)?,
            crate::storage::bytes::<Option<crate::Edge>>(edges)?,
        )
    }

    /// Admit CMG-owned construction without disabling parallel setup. This
    /// does NOT admit runtime memory: use only under a separate caller policy
    /// permitting the existing unbounded Rayon runtime. No new pool is created.
    #[cfg(feature = "parallel")]
    pub fn build_with_executor_and_cmg_budget(
        graph: &Laplacian,
        options: CmgOptions,
        executor: &ParallelExecutor,
        cmg_budget_bytes: usize,
    ) -> Result<Self, CmgError> {
        crate::storage::admit(
            Self::executor_construction_cmg_allocation_bound(
                graph.vertex_count(),
                graph.edge_count(),
                options,
            )?,
            cmg_budget_bytes,
        )?;
        let hierarchy =
            CmgHierarchy::build_executor_admitted::<false, false>(graph, options, executor)?;
        Self::from_hierarchy_with_factor_impl::<true>(hierarchy, |graph| {
            GroundedLdl::factor_with_budget(graph, cmg_budget_bytes)
        })
    }

    /// Component counterpart of the CMG-owned executor construction bound.
    /// Covers either pruning/stopping policy, but never the Rayon runtime.
    #[cfg(all(feature = "parallel", feature = "experimental-components"))]
    pub fn component_executor_construction_cmg_allocation_bound(
        vertices: usize,
        edges: usize,
        options: CmgOptions,
        experiment: crate::ComponentBuildOptions,
    ) -> Result<usize, CmgError> {
        crate::storage::add(
            Self::component_serial_construction_allocation_bound(
                vertices, edges, options, experiment,
            )?,
            crate::storage::bytes::<Option<crate::Edge>>(edges)?,
        )
    }

    /// Opt-in component setup with a CMG-owned-only budget and existing pool.
    /// Retains parallel map/sort thresholds, stopping and factor policies.
    /// Runtime memory remains uncovered; this is not strict parallel admission.
    #[cfg(all(feature = "parallel", feature = "experimental-components"))]
    pub fn build_component_experiment_with_executor_and_cmg_budget(
        graph: &Laplacian,
        options: CmgOptions,
        experiment: crate::ComponentBuildOptions,
        executor: &ParallelExecutor,
        cmg_budget_bytes: usize,
    ) -> Result<Self, CmgError> {
        crate::storage::admit(
            Self::component_executor_construction_cmg_allocation_bound(
                graph.vertex_count(),
                graph.edge_count(),
                options,
                experiment,
            )?,
            cmg_budget_bytes,
        )?;
        let hierarchy = if !experiment.prune_coarse_isolates {
            CmgHierarchy::build_executor_admitted::<false, false>(graph, options, executor)?
        } else if experiment.preserve_unpruned_stopping {
            CmgHierarchy::build_executor_admitted::<true, true>(graph, options, executor)?
        } else {
            CmgHierarchy::build_executor_admitted::<true, false>(graph, options, executor)?
        };
        if experiment.factor_terminal_components {
            Self::from_hierarchy_with_factor_impl::<true>(hierarchy, |graph| {
                GroundedLdl::factor_by_component_with_budget(graph, cmg_budget_bytes)
            })
        } else {
            Self::from_hierarchy_with_factor_impl::<true>(hierarchy, |graph| {
                GroundedLdl::factor_with_budget(graph, cmg_budget_bytes)
            })
        }
    }

    /// Build an opt-in component experiment with independent setup changes.
    ///
    /// Uses the ordinary certified PCG, workspace, and fixed cycle application
    /// paths. Active-count pruning can change termination and repeat counts;
    /// preserving unpruned stopping separates storage pruning from that change.
    /// Terminal component factors use ordered sparse numerical updates.
    #[cfg(feature = "experimental-components")]
    pub fn build_component_experiment(
        graph: &Laplacian,
        options: CmgOptions,
        experiment: crate::ComponentBuildOptions,
    ) -> Result<Self, CmgError> {
        let hierarchy = if experiment.prune_coarse_isolates {
            CmgHierarchy::build_pruned(graph, options, experiment.preserve_unpruned_stopping)?
        } else {
            CmgHierarchy::build(graph, options)?
        };
        if experiment.factor_terminal_components {
            Self::from_hierarchy_with_factor(hierarchy, GroundedLdl::factor_by_component)
        } else {
            Self::from_hierarchy(hierarchy)
        }
    }

    /// Build with deterministic parallel hierarchy contraction and sorting.
    ///
    /// The resulting hierarchy, terminal factor, and repeat counts are exactly
    /// the same as [`Self::build`].
    #[cfg(feature = "parallel")]
    pub fn build_with_executor(
        graph: &Laplacian,
        options: CmgOptions,
        executor: &ParallelExecutor,
    ) -> Result<Self, CmgError> {
        Self::from_hierarchy(CmgHierarchy::build_with_executor(graph, options, executor)?)
    }

    /// Build the explicit component path using an existing package-owned pool.
    /// No extra executor or duplicate hierarchy is created. Contraction/sorting
    /// retain the ordinary executor thresholds; forest work and factorization
    /// remain serial. Options, stopping rules and certification are unchanged.
    ///
    /// This is not whole-build memory admission: see [`crate::CmgMemoryEstimate`]
    /// and the unresolved construction/runtime limits in the ownership guide.
    #[cfg(all(feature = "experimental-components", feature = "parallel"))]
    pub fn build_component_experiment_with_executor(
        graph: &Laplacian,
        options: CmgOptions,
        experiment: crate::ComponentBuildOptions,
        executor: &ParallelExecutor,
    ) -> Result<Self, CmgError> {
        let hierarchy = if experiment.prune_coarse_isolates {
            CmgHierarchy::build_pruned_with_executor(
                graph,
                options,
                experiment.preserve_unpruned_stopping,
                executor,
            )?
        } else {
            CmgHierarchy::build_with_executor(graph, options, executor)?
        };
        if experiment.factor_terminal_components {
            Self::from_hierarchy_with_factor(hierarchy, GroundedLdl::factor_by_component)
        } else {
            Self::from_hierarchy(hierarchy)
        }
    }

    /// Build through the production hierarchy and finalization paths while timing both stages.
    #[cfg(all(feature = "parallel", feature = "profiling"))]
    pub fn build_with_executor_profiled(
        graph: &Laplacian,
        options: CmgOptions,
        executor: &ParallelExecutor,
    ) -> Result<(Self, PreconditionerBuildProfile), CmgError> {
        let total_start = Instant::now();
        let hierarchy_start = Instant::now();
        let (hierarchy, hierarchy_phases) =
            CmgHierarchy::build_with_executor_profiled(graph, options, executor)?;
        let hierarchy_nanoseconds = hierarchy_start.elapsed().as_nanos();
        let finalization_start = Instant::now();
        let preconditioner = Self::from_hierarchy(hierarchy)?;
        let finalization_nanoseconds = finalization_start.elapsed().as_nanos();
        let profile = PreconditionerBuildProfile {
            hierarchy_nanoseconds,
            hierarchy_phases: hierarchy_phases
                .into_iter()
                .map(|record| HierarchyPhaseProfile {
                    level: record.level,
                    phase: record.phase,
                    nanoseconds: record.nanoseconds,
                })
                .collect(),
            finalization_nanoseconds,
            total_nanoseconds: total_start.elapsed().as_nanos(),
        };
        Ok((preconditioner, profile))
    }

    fn from_hierarchy(hierarchy: CmgHierarchy) -> Result<Self, CmgError> {
        Self::from_hierarchy_with_factor(hierarchy, GroundedLdl::factor)
    }

    fn from_hierarchy_with_factor(
        hierarchy: CmgHierarchy,
        factor: impl FnOnce(&Laplacian) -> Result<GroundedLdl, CmgError>,
    ) -> Result<Self, CmgError> {
        Self::from_hierarchy_with_factor_impl::<false>(hierarchy, factor)
    }

    fn from_hierarchy_with_factor_impl<const ADMITTED: bool>(
        mut hierarchy: CmgHierarchy,
        factor: impl FnOnce(&Laplacian) -> Result<GroundedLdl, CmgError>,
    ) -> Result<Self, CmgError> {
        let finest = hierarchy
            .levels()
            .first()
            .ok_or(CmgError::InvalidHierarchy {
                context: "hierarchy contains no finest level",
            })?;
        let finest_components = if let Some(components) = finest.graph().prepared_components() {
            Arc::clone(components)
        } else if ADMITTED {
            Arc::new(Components::try_from_endpoints(
                finest.graph().vertex_count(),
                finest
                    .graph()
                    .edges()
                    .iter()
                    .map(|edge| (edge.u(), edge.v())),
            )?)
        } else {
            Arc::new(Components::from_laplacian(finest.graph()))
        };
        let coarse_centering = if ADMITTED {
            let mut plans = crate::storage::try_capacity(
                hierarchy.levels().len() - 1,
                "coarse centering plans",
            )?;
            for level in hierarchy.levels().iter().skip(1) {
                plans.push(CenteringPlan::try_from_laplacian(level.graph())?);
            }
            plans
        } else {
            hierarchy
                .levels()
                .iter()
                .skip(1)
                .map(|level| CenteringPlan::from_laplacian(level.graph()))
                .collect()
        };
        let direct_terminal = if hierarchy.report().terminal_reason() == TerminalReason::Direct {
            let terminal = hierarchy
                .levels()
                .last()
                .ok_or(CmgError::InvalidHierarchy {
                    context: "hierarchy contains no terminal level",
                })?;
            Some(factor(terminal.graph())?)
        } else {
            None
        };

        let mut repeat_counts: Vec<usize> = if ADMITTED {
            let mut counts = crate::storage::try_capacity(
                hierarchy.levels().len(),
                "preconditioner repeat counts",
            )?;
            counts.extend(hierarchy.levels().iter().map(|level| level.repeat()));
            counts
        } else {
            hierarchy
                .levels()
                .iter()
                .map(|level| level.repeat())
                .collect()
        };
        if hierarchy.levels().len() >= 2 {
            if let Some(factor) = &direct_terminal {
                let penultimate = hierarchy.levels().len() - 2;
                let repeat = repeat_from_nonzeros(
                    hierarchy.levels()[penultimate].graph().matrix_nnz(),
                    factor.factor_nonzeros(),
                );
                repeat_counts[penultimate] = repeat;
                hierarchy.set_repeat(penultimate, repeat)?;
            }
        }

        Ok(Self {
            hierarchy,
            finest_components,
            coarse_centering,
            direct_terminal,
            repeat_counts,
        })
    }

    /// Return the immutable hierarchy.
    #[must_use]
    pub const fn hierarchy(&self) -> &CmgHierarchy {
        &self.hierarchy
    }

    /// Return effective recursive repeat counts for all levels.
    #[must_use]
    pub fn repeat_counts(&self) -> &[usize] {
        &self.repeat_counts
    }

    /// Return the direct terminal factor when the hierarchy ends directly.
    #[must_use]
    pub const fn terminal_factor(&self) -> Option<&GroundedLdl> {
        self.direct_terminal.as_ref()
    }

    pub(crate) fn matches_graph(&self, graph: &Laplacian) -> bool {
        let finest = self.hierarchy.levels()[0].graph();
        finest.shares_lineage(graph) || finest == graph
    }

    pub(crate) fn matches_prepared_topology(&self, graph: &Laplacian) -> bool {
        self.hierarchy.levels()[0]
            .graph()
            .shares_prepared_topology(graph)
    }

    pub(crate) fn finest_components(&self) -> &Components {
        &self.finest_components
    }

    pub(crate) fn finest_component_bytes(&self) -> usize {
        self.finest_components.byte_len()
    }

    /// Return retained heap bytes for fine validation and coarse centering metadata.
    #[must_use]
    pub fn component_metadata_bytes(&self) -> usize {
        self.finest_components.byte_len()
            + self
                .coarse_centering
                .iter()
                .map(CenteringPlan::byte_len)
                .sum::<usize>()
    }

    /// Allocate reusable storage compatible with this preconditioner.
    #[must_use]
    pub fn workspace(&self) -> CmgWorkspace {
        CmgWorkspace::new(
            &self.hierarchy,
            self.direct_terminal.as_ref(),
            &self.finest_components,
            &self.coarse_centering,
        )
    }

    /// Fallibly allocate reusable storage compatible with this preconditioner.
    pub fn try_workspace(&self) -> Result<CmgWorkspace, CmgError> {
        CmgWorkspace::try_new(
            &self.hierarchy,
            self.direct_terminal.as_ref(),
            &self.finest_components,
            &self.coarse_centering,
        )
    }

    /// Fallibly prepare scalar application storage with bounded work gates.
    ///
    /// Allocation requests are reported separately to the callback. This does
    /// not create an application plan or an executor. Use the existing scalar
    /// `apply_compatible_into` with the returned workspace for a fixed cycle.
    #[cfg(feature = "experimental-components")]
    pub fn try_workspace_controlled(
        &self,
        work_budget: u64,
        control: &mut dyn crate::BuildControl,
    ) -> Result<CmgWorkspace, CmgError> {
        let mut meter = crate::build_control::BuildMeter::new(work_budget, control);
        CmgWorkspace::try_new_controlled(
            &self.hierarchy,
            self.direct_terminal.as_ref(),
            &self.finest_components,
            &self.coarse_centering,
            &mut meter,
        )
    }

    /// Bound primitive scalar work for one fixed compatible cycle.
    ///
    /// The returned allowance includes workspace validation, stored recursive
    /// repeats, smoothing, transfers, centering, and terminal triangular solves.
    /// Packed factors count stored zero slots as well as nonzeros. It excludes
    /// caller gather/scatter, RHS preparation, allocation, and residual checks.
    /// This metadata query itself is bounded and cancellable; the application
    /// kernel is unchanged and does not call the control callback. Overflow is
    /// reported as `BuildStopped` with `CounterOverflow` before a bound is used.
    #[cfg(feature = "experimental-components")]
    pub fn scalar_cycle_work_bound_controlled(
        &self,
        work_budget: u64,
        control: &mut dyn crate::BuildControl,
    ) -> Result<u64, CmgError> {
        let mut meter = crate::build_control::BuildMeter::new(work_budget, control);
        let phase = "scalar cycle work bound";
        meter.work(phase, 128)?;
        let mut child = 0u64;
        for (index, level) in self.hierarchy.levels().iter().enumerate().rev() {
            meter.work(phase, 256)?;
            let error = || meter.stopped(phase, crate::BuildStopReason::CounterOverflow);
            let n = u64::try_from(level.graph().vertex_count()).map_err(|_| error())?;
            let m = u64::try_from(level.graph().edge_count()).map_err(|_| error())?;
            let checked_sum = |terms: &[u64]| {
                terms
                    .iter()
                    .try_fold(0u64, |a, &b| a.checked_add(b))
                    .ok_or_else(error)
            };
            child = match level.terminal_reason() {
                Some(TerminalReason::Direct) => {
                    let factor =
                        self.direct_terminal
                            .as_ref()
                            .ok_or(CmgError::InvalidHierarchy {
                                context: "direct terminal is missing its LDL factor",
                            })?;
                    let slots = u64::try_from(factor.scalar_solve_slots()).map_err(|_| error())?;
                    checked_sum(&[
                        256,
                        n.checked_mul(32).ok_or_else(error)?,
                        slots.checked_mul(32).ok_or_else(error)?,
                    ])?
                }
                Some(_) => checked_sum(&[64, n.checked_mul(8).ok_or_else(error)?])?,
                None => {
                    let c = self
                        .hierarchy
                        .levels()
                        .get(index + 1)
                        .ok_or(CmgError::InvalidHierarchy {
                            context: "nonterminal level has no child",
                        })?
                        .graph()
                        .vertex_count();
                    let coarse = u64::try_from(c).map_err(|_| error())?;
                    let dimensions = checked_sum(&[n, m, coarse])?;
                    let local = checked_sum(&[256, dimensions.checked_mul(64).ok_or_else(error)?])?;
                    let one = checked_sum(&[local, if c == 0 { 0 } else { child }])?;
                    let iterations = if index == 0 {
                        1
                    } else {
                        self.repeat_counts[index - 1]
                    };
                    if iterations == 0 {
                        return Err(CmgError::InvalidHierarchy {
                            context: "nonterminal level has zero stationary iterations",
                        });
                    }
                    one.checked_mul(u64::try_from(iterations).map_err(|_| error())?)
                        .ok_or_else(error)?
                }
            };
        }
        meter.work(phase, 64)?;
        let count = u64::try_from(self.hierarchy.levels().len())
            .map_err(|_| meter.stopped(phase, crate::BuildStopReason::CounterOverflow))?;
        child
            .checked_add(128)
            .and_then(|value| {
                count
                    .checked_mul(128)
                    .and_then(|validation| value.checked_add(validation))
            })
            .ok_or_else(|| meter.stopped(phase, crate::BuildStopReason::CounterOverflow))
    }

    /// Apply the preconditioner using a newly allocated workspace.
    pub fn apply(&self, rhs: &[f64]) -> Result<Vec<f64>, CmgError> {
        let mut workspace = self.workspace();
        let mut output = vec![0.0; self.hierarchy.levels()[0].graph().vertex_count()];
        self.apply_into(rhs, &mut output, &mut workspace)?;
        Ok(output)
    }

    /// Apply the preconditioner into caller-owned output and workspace.
    pub fn apply_into(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
    ) -> Result<(), CmgError> {
        self.apply_into_with_validation(rhs, output, workspace, ValidationOptions::default())
    }

    /// Apply a right-hand side already known to be component-compatible.
    ///
    /// This skips the fine-level compatibility scan and projection performed by
    /// [`Self::apply_into`]. It is intended for Krylov solvers that validate and
    /// project a submitted right-hand side once, then keep residuals in the
    /// Laplacian range. Dimension, workspace, and option checks remain enabled;
    /// recursive coarse-level roundoff is removed by deterministic component centering.
    pub fn apply_compatible_into(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
    ) -> Result<(), CmgError> {
        self.apply_compatible_into_with_validation(
            rhs,
            output,
            workspace,
            ValidationOptions::default(),
        )
    }

    /// Apply an already compatible right-hand side with explicit validation
    /// tolerances for public validation; recursive coarse residuals are centered.
    ///
    /// Callers are responsible for ensuring component-wise compatibility. An
    /// incompatible right-hand side does not represent a solvable Laplacian
    /// system and should use [`Self::apply_into`] instead.
    pub fn apply_compatible_into_with_validation(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        validation: ValidationOptions,
    ) -> Result<(), CmgError> {
        let dimension = self.hierarchy.levels()[0].graph().vertex_count();
        if rhs.len() != dimension {
            return Err(CmgError::dimension(
                "CmgPreconditioner::apply compatible rhs",
                dimension,
                rhs.len(),
            ));
        }
        if output.len() != dimension {
            return Err(CmgError::dimension(
                "CmgPreconditioner::apply compatible output",
                dimension,
                output.len(),
            ));
        }
        workspace.validate(
            &self.hierarchy,
            self.direct_terminal.as_ref(),
            &self.finest_components,
            &self.coarse_centering,
        )?;
        validation.validate()?;
        self.apply_level(0, rhs, output, workspace, 1)
    }

    /// Profile the exact serial cycle for an already compatible right-hand side.
    ///
    /// Timings are exclusive across phases and recursive levels. As with
    /// `apply_compatible_into`, the caller supplies a compatible RHS.
    #[cfg(feature = "cycle-profiling")]
    pub fn profile_apply_compatible_into(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
    ) -> Result<crate::CmgApplyProfile, CmgError> {
        let dimension = self.hierarchy.levels()[0].graph().vertex_count();
        for (context, actual) in [
            ("CmgPreconditioner::apply compatible rhs", rhs.len()),
            ("CmgPreconditioner::apply compatible output", output.len()),
        ] {
            if actual != dimension {
                return Err(CmgError::dimension(context, dimension, actual));
            }
        }
        self.validate_workspace(workspace)?;
        let mut profile = crate::CmgApplyProfile::new(self.hierarchy.levels().len());
        self.apply_level_recorded(0, rhs, output, workspace, 1, &mut profile)?;
        Ok(profile)
    }

    #[cfg(feature = "cycle-profiling")]
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn apply_profiled_with_plan(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        plan: &ParallelCmgPlan,
        executor: &ParallelExecutor,
        profile: &mut crate::CmgApplyProfile,
    ) -> Result<(), CmgError> {
        self.apply_level_with_plan_recorded(0, rhs, output, workspace, 1, plan, executor, profile)
    }

    pub(crate) fn apply_compatible_into_prevalidated(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
    ) -> Result<(), CmgError> {
        self.apply_level(0, rhs, output, workspace, 1)
    }

    #[cfg(feature = "parallel")]
    fn apply_compatible_into_with_plan(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        validation: ValidationOptions,
        plan: &ParallelCmgPlan,
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        plan.validate(self)?;
        let dimension = self.hierarchy.levels()[0].graph().vertex_count();
        if rhs.len() != dimension {
            return Err(CmgError::dimension(
                "ParallelCmgPlan::apply compatible rhs",
                dimension,
                rhs.len(),
            ));
        }
        if output.len() != dimension {
            return Err(CmgError::dimension(
                "ParallelCmgPlan::apply compatible output",
                dimension,
                output.len(),
            ));
        }
        self.validate_workspace(workspace)?;
        validation.validate()?;
        self.apply_compatible_into_with_prevalidated_plan(
            rhs, output, workspace, validation, plan, executor,
        )
    }

    #[cfg(feature = "parallel")]
    fn apply_compatible_into_with_prevalidated_plan(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        validation: ValidationOptions,
        plan: &ParallelCmgPlan,
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        debug_assert!(validation.validate().is_ok());
        self.apply_level_with_plan(0, rhs, output, workspace, 1, plan, executor)
    }

    /// Apply with explicit compatibility-validation tolerances.
    ///
    /// A component sum accepted as floating-point roundoff is projected to
    /// exact zero before the stationary CMG cycle is evaluated.
    pub fn apply_into_with_validation(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        validation: ValidationOptions,
    ) -> Result<(), CmgError> {
        let dimension = self.hierarchy.levels()[0].graph().vertex_count();
        if rhs.len() != dimension {
            return Err(CmgError::dimension(
                "CmgPreconditioner::apply rhs",
                dimension,
                rhs.len(),
            ));
        }
        if output.len() != dimension {
            return Err(CmgError::dimension(
                "CmgPreconditioner::apply output",
                dimension,
                output.len(),
            ));
        }
        workspace.validate(
            &self.hierarchy,
            self.direct_terminal.as_ref(),
            &self.finest_components,
            &self.coarse_centering,
        )?;
        let mut projected_rhs = workspace.take_projected_rhs();
        projected_rhs.copy_from_slice(rhs);
        let result = (|| {
            let mut component_workspace = workspace.take_component();
            let projection = self.finest_components.project_rhs_in_place_with_workspace(
                &mut projected_rhs,
                validation,
                &mut component_workspace,
            );
            workspace.put_component(component_workspace);
            projection?;
            self.apply_level(0, &projected_rhs, output, workspace, 1)
        })();
        workspace.put_projected_rhs(projected_rhs);
        result
    }

    #[cfg(feature = "parallel")]
    fn apply_level_with_plan(
        &self,
        level_index: usize,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        iterations: usize,
        plan: &ParallelCmgPlan,
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        let level = &self.hierarchy.levels()[level_index];
        let dimension = level.graph().vertex_count();
        if rhs.len() != dimension || output.len() != dimension {
            return Err(CmgError::InvalidHierarchy {
                context: "parallel recursive vector dimension does not match hierarchy level",
            });
        }

        if let Some(reason) = level.terminal_reason() {
            if reason == TerminalReason::Direct {
                let factor = self
                    .direct_terminal
                    .as_ref()
                    .ok_or(CmgError::InvalidHierarchy {
                        context: "direct terminal is missing its LDL factor",
                    })?;
                let mut local = workspace.take_level(level_index);
                let result = factor.solve_into_compatible(
                    rhs,
                    output,
                    &mut local.factor_forward,
                    &mut local.factor_solution,
                );
                workspace.put_level(level_index, local);
                return result;
            }
            assign_scaled_planned(output, level.inverse_diagonal(), rhs, executor, false);
            return Ok(());
        }

        if iterations == 0 {
            return Err(CmgError::InvalidHierarchy {
                context: "nonterminal level has zero stationary iterations",
            });
        }
        let child_iterations = self.repeat_counts[level_index];
        if child_iterations == 0 {
            return Err(CmgError::InvalidHierarchy {
                context: "nonterminal level has zero child recursive repeats",
            });
        }
        let parallel_level = plan.level_operator(level_index).is_some();

        let mut local = workspace.take_level(level_index);
        // With a zero-dimensional child, restriction and correction have no
        // output. Keep both smoothing sweeps, but avoid forming the unused
        // middle residual and making an empty recursive call.
        #[cfg(feature = "experimental-components")]
        let has_coarse_work = !local.coarse_rhs.is_empty();
        #[cfg(not(feature = "experimental-components"))]
        let has_coarse_work = true;
        let result = (|| {
            for iteration in 0..iterations {
                if iteration == 0 {
                    assign_scaled_planned(
                        output,
                        level.inverse_diagonal(),
                        rhs,
                        executor,
                        parallel_level,
                    );
                } else {
                    plan.matvec_into(
                        level_index,
                        level.graph(),
                        output,
                        &mut local.residual,
                        executor,
                    )?;
                    jacobi_add_planned(
                        output,
                        level.inverse_diagonal(),
                        rhs,
                        &local.residual,
                        executor,
                        parallel_level,
                    );
                }

                if has_coarse_work {
                    plan.matvec_into(
                        level_index,
                        level.graph(),
                        output,
                        &mut local.residual,
                        executor,
                    )?;
                    #[cfg(feature = "experimental-components")]
                    level.restrict_residual_into(rhs, &local.residual, &mut local.coarse_rhs)?;
                    #[cfg(not(feature = "experimental-components"))]
                    {
                        residual_from_matvec_planned(
                            &mut local.residual,
                            rhs,
                            executor,
                            parallel_level,
                        );
                        level.restrict_into(&local.residual, &mut local.coarse_rhs)?;
                    }
                    let centering = &self.coarse_centering[level_index];
                    let mut centering_workspace = workspace.take_centering(level_index);
                    let centering_result = centering.center_in_place_with_workspace_and_executor(
                        &mut local.coarse_rhs,
                        &mut centering_workspace,
                        executor,
                    );
                    workspace.put_centering(level_index, centering_workspace);
                    centering_result?;
                    self.apply_level_with_plan(
                        level_index + 1,
                        &local.coarse_rhs,
                        &mut local.coarse_correction,
                        workspace,
                        child_iterations,
                        plan,
                        executor,
                    )?;
                    if parallel_level {
                        level.prolong_add_into_with_executor(
                            &local.coarse_correction,
                            output,
                            executor,
                        )?;
                    } else {
                        level.prolong_add_into(&local.coarse_correction, output)?;
                    }
                }

                plan.matvec_into(
                    level_index,
                    level.graph(),
                    output,
                    &mut local.residual,
                    executor,
                )?;
                jacobi_add_planned(
                    output,
                    level.inverse_diagonal(),
                    rhs,
                    &local.residual,
                    executor,
                    parallel_level,
                );
            }
            Ok(())
        })();
        workspace.put_level(level_index, local);
        result
    }

    #[allow(clippy::too_many_arguments)]
    #[cfg(feature = "cycle-profiling")]
    fn apply_level_with_plan_recorded<R: CycleRecorder>(
        &self,
        level_index: usize,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        iterations: usize,
        plan: &ParallelCmgPlan,
        executor: &ParallelExecutor,
        recorder: &mut R,
    ) -> Result<(), CmgError> {
        let level = &self.hierarchy.levels()[level_index];
        let dimension = level.graph().vertex_count();
        if rhs.len() != dimension || output.len() != dimension {
            return Err(CmgError::InvalidHierarchy {
                context: "parallel recursive vector dimension does not match hierarchy level",
            });
        }

        recorder.enter(level_index);
        if let Some(reason) = level.terminal_reason() {
            let stamp = R::start();
            if reason == TerminalReason::Direct {
                let factor = self
                    .direct_terminal
                    .as_ref()
                    .ok_or(CmgError::InvalidHierarchy {
                        context: "direct terminal is missing its LDL factor",
                    })?;
                let mut local = workspace.take_level(level_index);
                let result = factor.solve_into_compatible(
                    rhs,
                    output,
                    &mut local.factor_forward,
                    &mut local.factor_solution,
                );
                workspace.put_level(level_index, local);
                recorder.finish(level_index, CmgApplyPhase::Terminal, stamp);
                return result;
            }
            assign_scaled_planned(output, level.inverse_diagonal(), rhs, executor, false);
            recorder.finish(level_index, CmgApplyPhase::Terminal, stamp);
            return Ok(());
        }

        if iterations == 0 {
            return Err(CmgError::InvalidHierarchy {
                context: "nonterminal level has zero stationary iterations",
            });
        }
        let child_iterations = self.repeat_counts[level_index];
        if child_iterations == 0 {
            return Err(CmgError::InvalidHierarchy {
                context: "nonterminal level has zero child recursive repeats",
            });
        }
        let parallel_level = plan.level_operator(level_index).is_some();

        let mut local = workspace.take_level(level_index);
        // With a zero-dimensional child, restriction and correction have no
        // output. Keep both smoothing sweeps, but avoid forming the unused
        // middle residual and making an empty recursive call.
        #[cfg(feature = "experimental-components")]
        let has_coarse_work = !local.coarse_rhs.is_empty();
        #[cfg(not(feature = "experimental-components"))]
        let has_coarse_work = true;
        let result = (|| {
            for iteration in 0..iterations {
                recorder.iteration(level_index);
                let stamp = R::start();
                if iteration == 0 {
                    assign_scaled_planned(
                        output,
                        level.inverse_diagonal(),
                        rhs,
                        executor,
                        parallel_level,
                    );
                } else {
                    plan.matvec_into(
                        level_index,
                        level.graph(),
                        output,
                        &mut local.residual,
                        executor,
                    )?;
                    jacobi_add_planned(
                        output,
                        level.inverse_diagonal(),
                        rhs,
                        &local.residual,
                        executor,
                        parallel_level,
                    );
                }

                recorder.finish(
                    level_index,
                    if iteration == 0 {
                        CmgApplyPhase::Initialization
                    } else {
                        CmgApplyPhase::Smoothing
                    },
                    stamp,
                );

                if has_coarse_work {
                    let stamp = R::start();
                    plan.matvec_into(
                        level_index,
                        level.graph(),
                        output,
                        &mut local.residual,
                        executor,
                    )?;
                    recorder.finish(level_index, CmgApplyPhase::ResidualMatvec, stamp);
                    let stamp = R::start();
                    #[cfg(feature = "experimental-components")]
                    level.restrict_residual_into(rhs, &local.residual, &mut local.coarse_rhs)?;
                    #[cfg(not(feature = "experimental-components"))]
                    {
                        residual_from_matvec_planned(
                            &mut local.residual,
                            rhs,
                            executor,
                            parallel_level,
                        );
                        level.restrict_into(&local.residual, &mut local.coarse_rhs)?;
                    }
                    recorder.finish(level_index, CmgApplyPhase::Restriction, stamp);
                    let stamp = R::start();
                    let centering = &self.coarse_centering[level_index];
                    let mut centering_workspace = workspace.take_centering(level_index);
                    let centering_result = centering.center_in_place_with_workspace_and_executor(
                        &mut local.coarse_rhs,
                        &mut centering_workspace,
                        executor,
                    );
                    workspace.put_centering(level_index, centering_workspace);
                    centering_result?;
                    recorder.finish(level_index, CmgApplyPhase::Centering, stamp);
                    self.apply_level_with_plan_recorded(
                        level_index + 1,
                        &local.coarse_rhs,
                        &mut local.coarse_correction,
                        workspace,
                        child_iterations,
                        plan,
                        executor,
                        recorder,
                    )?;
                    let stamp = R::start();
                    if parallel_level {
                        level.prolong_add_into_with_executor(
                            &local.coarse_correction,
                            output,
                            executor,
                        )?;
                    } else {
                        level.prolong_add_into(&local.coarse_correction, output)?;
                    }
                    recorder.finish(level_index, CmgApplyPhase::Prolongation, stamp);
                }

                let stamp = R::start();
                plan.matvec_into(
                    level_index,
                    level.graph(),
                    output,
                    &mut local.residual,
                    executor,
                )?;
                jacobi_add_planned(
                    output,
                    level.inverse_diagonal(),
                    rhs,
                    &local.residual,
                    executor,
                    parallel_level,
                );
                recorder.finish(level_index, CmgApplyPhase::Smoothing, stamp);
            }
            Ok(())
        })();
        workspace.put_level(level_index, local);
        result
    }

    fn apply_level(
        &self,
        level_index: usize,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        iterations: usize,
    ) -> Result<(), CmgError> {
        let level = &self.hierarchy.levels()[level_index];
        let dimension = level.graph().vertex_count();
        if rhs.len() != dimension || output.len() != dimension {
            return Err(CmgError::InvalidHierarchy {
                context: "recursive vector dimension does not match hierarchy level",
            });
        }

        if let Some(reason) = level.terminal_reason() {
            if reason == TerminalReason::Direct {
                let factor = self
                    .direct_terminal
                    .as_ref()
                    .ok_or(CmgError::InvalidHierarchy {
                        context: "direct terminal is missing its LDL factor",
                    })?;
                let mut local = workspace.take_level(level_index);
                let result = factor.solve_into_compatible(
                    rhs,
                    output,
                    &mut local.factor_forward,
                    &mut local.factor_solution,
                );
                workspace.put_level(level_index, local);
                return result;
            }
            for ((value, inverse_diagonal), rhs_value) in
                output.iter_mut().zip(level.inverse_diagonal()).zip(rhs)
            {
                *value = *inverse_diagonal * *rhs_value;
            }
            return Ok(());
        }

        if iterations == 0 {
            return Err(CmgError::InvalidHierarchy {
                context: "nonterminal level has zero stationary iterations",
            });
        }
        let child_iterations = self.repeat_counts[level_index];
        if child_iterations == 0 {
            return Err(CmgError::InvalidHierarchy {
                context: "nonterminal level has zero child recursive repeats",
            });
        }

        let mut local = workspace.take_level(level_index);
        // With a zero-dimensional child, restriction and correction have no
        // output. Keep both smoothing sweeps, but avoid forming the unused
        // middle residual and making an empty recursive call.
        #[cfg(feature = "experimental-components")]
        let has_coarse_work = !local.coarse_rhs.is_empty();
        #[cfg(not(feature = "experimental-components"))]
        let has_coarse_work = true;
        let result = (|| {
            for iteration in 0..iterations {
                if iteration == 0 {
                    for ((value, inverse_diagonal), rhs_value) in
                        output.iter_mut().zip(level.inverse_diagonal()).zip(rhs)
                    {
                        *value = *inverse_diagonal * *rhs_value;
                    }
                } else {
                    level.graph().matvec_into(output, &mut local.residual)?;
                    for (((value, inverse_diagonal), rhs_value), matrix_value) in output
                        .iter_mut()
                        .zip(level.inverse_diagonal())
                        .zip(rhs)
                        .zip(&local.residual)
                    {
                        *value += *inverse_diagonal * (*rhs_value - *matrix_value);
                    }
                }

                if has_coarse_work {
                    level.graph().matvec_into(output, &mut local.residual)?;
                    #[cfg(feature = "experimental-components")]
                    level.restrict_residual_into(rhs, &local.residual, &mut local.coarse_rhs)?;
                    #[cfg(not(feature = "experimental-components"))]
                    {
                        for (residual, rhs_value) in local.residual.iter_mut().zip(rhs) {
                            *residual = *rhs_value - *residual;
                        }
                        level.restrict_into(&local.residual, &mut local.coarse_rhs)?;
                    }
                    let centering = &self.coarse_centering[level_index];
                    let mut centering_workspace = workspace.take_centering(level_index);
                    // Restricted residuals are component-compatible in exact
                    // arithmetic. Remove only floating-point null-space drift before
                    // the recursive solve instead of repeating full public-boundary
                    // compatibility validation and exact correction passes.
                    let centering_result = centering.center_in_place_with_workspace(
                        &mut local.coarse_rhs,
                        &mut centering_workspace,
                    );
                    workspace.put_centering(level_index, centering_workspace);
                    centering_result?;
                    self.apply_level(
                        level_index + 1,
                        &local.coarse_rhs,
                        &mut local.coarse_correction,
                        workspace,
                        child_iterations,
                    )?;
                    level.prolong_add_into(&local.coarse_correction, output)?;
                }

                level.graph().matvec_into(output, &mut local.residual)?;
                for (((value, inverse_diagonal), rhs_value), matrix_value) in output
                    .iter_mut()
                    .zip(level.inverse_diagonal())
                    .zip(rhs)
                    .zip(&local.residual)
                {
                    *value += *inverse_diagonal * (*rhs_value - *matrix_value);
                }
            }
            Ok(())
        })();
        workspace.put_level(level_index, local);
        result
    }

    #[allow(clippy::too_many_arguments)]
    #[cfg(feature = "cycle-profiling")]
    fn apply_level_recorded<R: CycleRecorder>(
        &self,
        level_index: usize,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        iterations: usize,
        recorder: &mut R,
    ) -> Result<(), CmgError> {
        let level = &self.hierarchy.levels()[level_index];
        let dimension = level.graph().vertex_count();
        if rhs.len() != dimension || output.len() != dimension {
            return Err(CmgError::InvalidHierarchy {
                context: "recursive vector dimension does not match hierarchy level",
            });
        }

        recorder.enter(level_index);
        if let Some(reason) = level.terminal_reason() {
            let stamp = R::start();
            if reason == TerminalReason::Direct {
                let factor = self
                    .direct_terminal
                    .as_ref()
                    .ok_or(CmgError::InvalidHierarchy {
                        context: "direct terminal is missing its LDL factor",
                    })?;
                let mut local = workspace.take_level(level_index);
                let result = factor.solve_into_compatible(
                    rhs,
                    output,
                    &mut local.factor_forward,
                    &mut local.factor_solution,
                );
                workspace.put_level(level_index, local);
                recorder.finish(level_index, CmgApplyPhase::Terminal, stamp);
                return result;
            }
            for ((value, inverse_diagonal), rhs_value) in
                output.iter_mut().zip(level.inverse_diagonal()).zip(rhs)
            {
                *value = *inverse_diagonal * *rhs_value;
            }
            recorder.finish(level_index, CmgApplyPhase::Terminal, stamp);
            return Ok(());
        }

        if iterations == 0 {
            return Err(CmgError::InvalidHierarchy {
                context: "nonterminal level has zero stationary iterations",
            });
        }
        let child_iterations = self.repeat_counts[level_index];
        if child_iterations == 0 {
            return Err(CmgError::InvalidHierarchy {
                context: "nonterminal level has zero child recursive repeats",
            });
        }

        let mut local = workspace.take_level(level_index);
        // With a zero-dimensional child, restriction and correction have no
        // output. Keep both smoothing sweeps, but avoid forming the unused
        // middle residual and making an empty recursive call.
        #[cfg(feature = "experimental-components")]
        let has_coarse_work = !local.coarse_rhs.is_empty();
        #[cfg(not(feature = "experimental-components"))]
        let has_coarse_work = true;
        let result = (|| {
            for iteration in 0..iterations {
                recorder.iteration(level_index);
                let stamp = R::start();
                if iteration == 0 {
                    for ((value, inverse_diagonal), rhs_value) in
                        output.iter_mut().zip(level.inverse_diagonal()).zip(rhs)
                    {
                        *value = *inverse_diagonal * *rhs_value;
                    }
                } else {
                    level.graph().matvec_into(output, &mut local.residual)?;
                    for (((value, inverse_diagonal), rhs_value), matrix_value) in output
                        .iter_mut()
                        .zip(level.inverse_diagonal())
                        .zip(rhs)
                        .zip(&local.residual)
                    {
                        *value += *inverse_diagonal * (*rhs_value - *matrix_value);
                    }
                }

                recorder.finish(
                    level_index,
                    if iteration == 0 {
                        CmgApplyPhase::Initialization
                    } else {
                        CmgApplyPhase::Smoothing
                    },
                    stamp,
                );

                if has_coarse_work {
                    let stamp = R::start();
                    level.graph().matvec_into(output, &mut local.residual)?;
                    recorder.finish(level_index, CmgApplyPhase::ResidualMatvec, stamp);
                    let stamp = R::start();
                    #[cfg(feature = "experimental-components")]
                    level.restrict_residual_into(rhs, &local.residual, &mut local.coarse_rhs)?;
                    #[cfg(not(feature = "experimental-components"))]
                    {
                        for (residual, rhs_value) in local.residual.iter_mut().zip(rhs) {
                            *residual = *rhs_value - *residual;
                        }
                        level.restrict_into(&local.residual, &mut local.coarse_rhs)?;
                    }
                    recorder.finish(level_index, CmgApplyPhase::Restriction, stamp);
                    let stamp = R::start();
                    let centering = &self.coarse_centering[level_index];
                    let mut centering_workspace = workspace.take_centering(level_index);
                    // Restricted residuals are component-compatible in exact
                    // arithmetic. Remove only floating-point null-space drift before
                    // the recursive solve instead of repeating full public-boundary
                    // compatibility validation and exact correction passes.
                    let centering_result = centering.center_in_place_with_workspace(
                        &mut local.coarse_rhs,
                        &mut centering_workspace,
                    );
                    workspace.put_centering(level_index, centering_workspace);
                    centering_result?;
                    recorder.finish(level_index, CmgApplyPhase::Centering, stamp);
                    self.apply_level_recorded(
                        level_index + 1,
                        &local.coarse_rhs,
                        &mut local.coarse_correction,
                        workspace,
                        child_iterations,
                        recorder,
                    )?;
                    let stamp = R::start();
                    level.prolong_add_into(&local.coarse_correction, output)?;
                    recorder.finish(level_index, CmgApplyPhase::Prolongation, stamp);
                }

                let stamp = R::start();
                level.graph().matvec_into(output, &mut local.residual)?;
                for (((value, inverse_diagonal), rhs_value), matrix_value) in output
                    .iter_mut()
                    .zip(level.inverse_diagonal())
                    .zip(rhs)
                    .zip(&local.residual)
                {
                    *value += *inverse_diagonal * (*rhs_value - *matrix_value);
                }
                recorder.finish(level_index, CmgApplyPhase::Smoothing, stamp);
            }
            Ok(())
        })();
        workspace.put_level(level_index, local);
        result
    }
}

#[cfg(feature = "parallel")]
fn assign_scaled_planned(
    output: &mut [f64],
    inverse_diagonal: &[f64],
    rhs: &[f64],
    executor: &ParallelExecutor,
    parallel: bool,
) {
    if parallel {
        assign_scaled_parallel(output, inverse_diagonal, rhs, executor);
    } else {
        for ((value, &diagonal), &right) in output.iter_mut().zip(inverse_diagonal).zip(rhs) {
            *value = diagonal * right;
        }
    }
}

#[cfg(feature = "parallel")]
fn jacobi_add_planned(
    output: &mut [f64],
    inverse_diagonal: &[f64],
    rhs: &[f64],
    matvec: &[f64],
    executor: &ParallelExecutor,
    parallel: bool,
) {
    if parallel {
        jacobi_add_parallel(output, inverse_diagonal, rhs, matvec, executor);
    } else {
        for (((value, &diagonal), &right), &product) in
            output.iter_mut().zip(inverse_diagonal).zip(rhs).zip(matvec)
        {
            *value += diagonal * (right - product);
        }
    }
}

#[cfg(all(feature = "parallel", not(feature = "experimental-components")))]
fn residual_from_matvec_planned(
    matvec: &mut [f64],
    rhs: &[f64],
    executor: &ParallelExecutor,
    parallel: bool,
) {
    if parallel {
        residual_from_matvec_parallel(matvec, rhs, executor);
    } else {
        for (value, &right) in matvec.iter_mut().zip(rhs) {
            *value = right - *value;
        }
    }
}

#[cfg(feature = "parallel")]
fn assign_scaled_parallel(
    output: &mut [f64],
    inverse_diagonal: &[f64],
    rhs: &[f64],
    executor: &ParallelExecutor,
) {
    if executor.should_parallel(output.len()) {
        executor.install(|| {
            output
                .par_iter_mut()
                .zip(inverse_diagonal.par_iter())
                .zip(rhs.par_iter())
                .for_each(|((value, inverse_diagonal), rhs_value)| {
                    *value = *inverse_diagonal * *rhs_value;
                });
        });
    } else {
        for ((value, inverse_diagonal), rhs_value) in
            output.iter_mut().zip(inverse_diagonal).zip(rhs)
        {
            *value = *inverse_diagonal * *rhs_value;
        }
    }
}

#[cfg(feature = "parallel")]
fn jacobi_add_parallel(
    output: &mut [f64],
    inverse_diagonal: &[f64],
    rhs: &[f64],
    matrix_value: &[f64],
    executor: &ParallelExecutor,
) {
    if executor.should_parallel(output.len()) {
        executor.install(|| {
            output
                .par_iter_mut()
                .zip(inverse_diagonal.par_iter())
                .zip(rhs.par_iter())
                .zip(matrix_value.par_iter())
                .for_each(|(((value, inverse_diagonal), rhs_value), matrix_value)| {
                    *value += *inverse_diagonal * (*rhs_value - *matrix_value);
                });
        });
    } else {
        for (((value, inverse_diagonal), rhs_value), matrix_value) in output
            .iter_mut()
            .zip(inverse_diagonal)
            .zip(rhs)
            .zip(matrix_value)
        {
            *value += *inverse_diagonal * (*rhs_value - *matrix_value);
        }
    }
}

#[cfg(all(feature = "parallel", not(feature = "experimental-components")))]
fn residual_from_matvec_parallel(residual: &mut [f64], rhs: &[f64], executor: &ParallelExecutor) {
    if executor.should_parallel(residual.len()) {
        executor.install(|| {
            residual
                .par_iter_mut()
                .zip(rhs.par_iter())
                .for_each(|(value, rhs_value)| *value = *rhs_value - *value);
        });
    } else {
        for (value, rhs_value) in residual.iter_mut().zip(rhs) {
            *value = *rhs_value - *value;
        }
    }
}

fn repeat_from_nonzeros(fine_nonzeros: usize, denominator_nonzeros: usize) -> usize {
    if denominator_nonzeros == 0 {
        return 1;
    }
    (fine_nonzeros / denominator_nonzeros)
        .saturating_sub(1)
        .max(1)
}

#[cfg(all(test, feature = "experimental-components"))]
mod controlled_cycle_bound_tests {
    use super::*;
    use crate::{BuildDecision, BuildOperation, BuildStopReason, ComponentBuildOptions};

    #[test]
    fn rounded_matvec_probe_matches_unchanged_cycle_on_the_selected_hierarchy() {
        // Preserve the original weak-edge probe as a diagnostic, not as a
        // relaxed linearity certificate. Floating L*x and affine combinations
        // need not lie exactly in the component range, and cancellation can be
        // amplified by the weak edge. Exact compatible dipoles retain the
        // independent 1e-11 checks in tests/factor_preflight.rs.
        for disconnected in [false, true] {
            for scale in [1e-90, 1.0, 1e90] {
                let components = if disconnected { 2 } else { 1 };
                let n = if disconnected { 50 } else { 24 };
                let graph = Laplacian::from_edges(
                    n,
                    (0..components).flat_map(|c| {
                        (1..24).map(move |v| {
                            (
                                c * 24 + v - 1,
                                c * 24 + v,
                                scale
                                    * if v == 12 {
                                        1.0 / 65536.0
                                    } else {
                                        1.0 + (v % 5) as f64 / 4.0
                                    },
                            )
                        })
                    }),
                )
                .unwrap();
                let selected = CmgPreconditioner::build_component_experiment_serial_controlled_with_factor_preflight(
                    &graph,
                    CmgOptions { max_levels: 8, ..Default::default() },
                    ComponentBuildOptions {
                        prune_coarse_isolates: true,
                        preserve_unpruned_stopping: true,
                        factor_terminal_components: true,
                    },
                    usize::MAX,
                    200_000,
                    &mut |_| BuildDecision::Continue,
                ).unwrap();
                let legacy = CmgPreconditioner::from_hierarchy_with_factor(
                    selected.hierarchy.clone(),
                    GroundedLdl::factor_by_component,
                )
                .unwrap();
                assert_eq!(selected, legacy);
                let u = graph
                    .matvec(&(0..n).map(|i| (i % 7) as f64 - 3.0).collect::<Vec<_>>())
                    .unwrap();
                let v = graph
                    .matvec(
                        &(0..n)
                            .map(|i| ((i * 3) % 11) as f64 - 5.0)
                            .collect::<Vec<_>>(),
                    )
                    .unwrap();
                let input: Vec<_> = u.iter().zip(&v).map(|(a, b)| 1.25 * a - 0.75 * b).collect();
                let outputs: Vec<_> = [&u, &v, &input]
                    .into_iter()
                    .map(|rhs| {
                        let output = selected.apply(rhs).unwrap();
                        let old = legacy.apply(rhs).unwrap();
                        assert_eq!(
                            output.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
                            old.iter().map(|x| x.to_bits()).collect::<Vec<_>>()
                        );
                        output
                    })
                    .collect();
                let expected: Vec<_> = outputs[0]
                    .iter()
                    .zip(&outputs[1])
                    .map(|(a, b)| 1.25 * a - 0.75 * b)
                    .collect();
                let output_scale = outputs[2]
                    .iter()
                    .chain(&expected)
                    .fold(0.0_f64, |s, x| s.max(x.abs()));
                let defect = outputs[2]
                    .iter()
                    .zip(&expected)
                    .fold(0.0_f64, |s, (a, b)| s.max((a - b).abs()))
                    / output_scale;
                let moment_defect = [&u, &v, &input]
                    .into_iter()
                    .flat_map(|rhs| {
                        (0..components).map(move |c| {
                            rhs[c * 24..(c + 1) * 24].iter().sum::<f64>().abs() / scale
                        })
                    })
                    .fold(0.0_f64, f64::max);
                assert!(defect.is_finite() && moment_defect.is_finite());
                eprintln!(
                    "rounded matvec diagnostic: disconnected={disconnected}, scale={scale:e}, relative_linearity_defect={defect:e}, component_sum_defect_over_edge_scale={moment_defect:e}; legacy selected-hierarchy action is bit-exact"
                );
            }
        }
    }

    #[test]
    fn direct_bound_counts_packed_zero_slots_and_ignores_terminal_repeats() {
        for (n, edges, expected) in [
            (0, vec![], 512),
            (1, vec![], 544),
            (2, vec![(0, 1, 1.0)], 576),
            (3, vec![(0, 1, 1.0), (0, 2, 1.0), (1, 2, 1.0)], 640),
            // Two independent triangles: four active vertices, six packed
            // strict slots, including four explicit zeros between components.
            (
                6,
                vec![
                    (0, 1, 1.0),
                    (0, 2, 1.0),
                    (1, 2, 1.0),
                    (3, 4, 1.0),
                    (3, 5, 1.0),
                    (4, 5, 1.0),
                ],
                896,
            ),
        ] {
            let graph = Laplacian::from_edges(n, edges).unwrap();
            let mut pre = CmgPreconditioner::build(&graph, CmgOptions::default()).unwrap();
            assert_eq!(
                pre.scalar_cycle_work_bound_controlled(u64::MAX, &mut |_| BuildDecision::Continue)
                    .unwrap(),
                expected
            );
            pre.repeat_counts[0] = usize::MAX;
            assert_eq!(
                pre.scalar_cycle_work_bound_controlled(u64::MAX, &mut |_| BuildDecision::Continue)
                    .unwrap(),
                expected
            );
        }
    }

    #[test]
    fn cycle_bound_covers_recursion_and_has_checked_cancellable_metadata() {
        let edges = (0..256).flat_map(|v| {
            [
                (v % 16 < 15).then_some((v, v + 1, 1.0)),
                (v < 240).then_some((v, v + 16, 2.0)),
            ]
            .into_iter()
            .flatten()
        });
        let graph = Laplacian::from_edges(256, edges).unwrap();
        let mut pre = CmgPreconditioner::build(
            &graph,
            CmgOptions {
                direct_threshold: 2,
                ..CmgOptions::default()
            },
        )
        .unwrap();
        assert!(pre.hierarchy.levels().len() >= 3);
        let mut trace = Vec::new();
        let bound = pre
            .scalar_cycle_work_bound_controlled(u64::MAX, &mut |event| {
                trace.push(event);
                BuildDecision::Continue
            })
            .unwrap();
        assert!(bound > 64 * (graph.vertex_count() + graph.edge_count()) as u64);
        assert_eq!(trace.len(), pre.hierarchy.levels().len() + 2);
        assert!(
            trace
                .iter()
                .all(|event| matches!(event.operation, BuildOperation::Work { units: 1..=1024 }))
        );
        for stop in 0..trace.len() {
            let mut seen = 0;
            let result = pre.scalar_cycle_work_bound_controlled(u64::MAX, &mut |event| {
                assert_eq!(trace[seen], event);
                seen += 1;
                if seen == stop + 1 {
                    BuildDecision::Interrupted
                } else {
                    BuildDecision::Continue
                }
            });
            assert!(matches!(
                result,
                Err(CmgError::BuildStopped {
                    reason: BuildStopReason::Interrupted,
                    ..
                })
            ));
            assert_eq!(seen, stop + 1);
        }
        let budget = trace
            .iter()
            .map(|event| match event.operation {
                BuildOperation::Work { units } => u64::from(units),
                _ => 0,
            })
            .sum();
        assert_eq!(
            pre.scalar_cycle_work_bound_controlled(budget, &mut |_| BuildDecision::Continue)
                .unwrap(),
            bound
        );
        assert!(matches!(
            pre.scalar_cycle_work_bound_controlled(budget - 1, &mut |_| BuildDecision::Continue),
            Err(CmgError::BuildStopped {
                reason: BuildStopReason::WorkBudgetExhausted,
                ..
            })
        ));
        // The child is nonterminal, so its repetitions multiply all of its
        // descendant work, and a nonrepresentable result must never wrap.
        pre.repeat_counts[0] = usize::MAX;
        assert!(matches!(
            pre.scalar_cycle_work_bound_controlled(u64::MAX, &mut |_| BuildDecision::Continue),
            Err(CmgError::BuildStopped {
                reason: BuildStopReason::CounterOverflow,
                ..
            })
        ));
    }
}
