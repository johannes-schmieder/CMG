//! Stationary recursive CMG preconditioner application.

#[cfg(feature = "parallel")]
use crate::ParallelExecutor;
use crate::components::CenteringPlan;
use crate::{
    CmgError, CmgHierarchy, CmgOptions, CmgWorkspace, Components, GroundedLdl, Laplacian,
    TerminalReason, ValidationOptions,
};

/// An immutable stationary CMG preconditioner.
#[derive(Debug, Clone, PartialEq)]
pub struct CmgPreconditioner {
    hierarchy: CmgHierarchy,
    finest_components: Components,
    coarse_centering: Vec<CenteringPlan>,
    direct_terminal: Option<GroundedLdl>,
    repeat_counts: Vec<usize>,
}

impl CmgPreconditioner {
    /// Build the complete hierarchy and any direct terminal factorization.
    pub fn build(graph: &Laplacian, options: CmgOptions) -> Result<Self, CmgError> {
        Self::from_hierarchy(CmgHierarchy::build(graph, options)?)
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

    fn from_hierarchy(mut hierarchy: CmgHierarchy) -> Result<Self, CmgError> {
        let finest = hierarchy
            .levels()
            .first()
            .ok_or(CmgError::InvalidHierarchy {
                context: "hierarchy contains no finest level",
            })?;
        let finest_components = Components::from_laplacian(finest.graph());
        let coarse_centering = hierarchy
            .levels()
            .iter()
            .skip(1)
            .map(|level| CenteringPlan::from_laplacian(level.graph()))
            .collect();
        let direct_terminal = if hierarchy.report().terminal_reason() == TerminalReason::Direct {
            let terminal = hierarchy
                .levels()
                .last()
                .ok_or(CmgError::InvalidHierarchy {
                    context: "hierarchy contains no terminal level",
                })?;
            Some(GroundedLdl::factor(terminal.graph())?)
        } else {
            None
        };

        let mut repeat_counts: Vec<usize> = hierarchy
            .levels()
            .iter()
            .map(|level| level.repeat())
            .collect();
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

    pub(crate) fn finest_components(&self) -> &Components {
        &self.finest_components
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

        let aggregation = level.aggregation().ok_or(CmgError::InvalidHierarchy {
            context: "nonterminal level has no aggregation",
        })?;
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
        let result = (|| {
            output.fill(0.0);
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

                level.graph().matvec_into(output, &mut local.residual)?;
                for (residual, rhs_value) in local.residual.iter_mut().zip(rhs) {
                    *residual = *rhs_value - *residual;
                }
                aggregation.restrict_into(&local.residual, &mut local.coarse_rhs)?;
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
                local.coarse_correction.fill(0.0);
                self.apply_level(
                    level_index + 1,
                    &local.coarse_rhs,
                    &mut local.coarse_correction,
                    workspace,
                    child_iterations,
                )?;
                aggregation.prolong_add_into(&local.coarse_correction, output)?;

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
}

fn repeat_from_nonzeros(fine_nonzeros: usize, denominator_nonzeros: usize) -> usize {
    if denominator_nonzeros == 0 {
        return 1;
    }
    (fine_nonzeros / denominator_nonzeros)
        .saturating_sub(1)
        .max(1)
}
