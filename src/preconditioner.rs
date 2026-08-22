//! Stationary recursive CMG preconditioner application.

use crate::{
    CmgError, CmgHierarchy, CmgOptions, CmgWorkspace, Components, GroundedLdl, Laplacian,
    TerminalReason, ValidationOptions,
};

/// An immutable stationary CMG preconditioner.
#[derive(Debug, Clone, PartialEq)]
pub struct CmgPreconditioner {
    hierarchy: CmgHierarchy,
    components: Components,
    direct_terminal: Option<GroundedLdl>,
    repeat_counts: Vec<usize>,
}

impl CmgPreconditioner {
    /// Build the complete hierarchy and any direct terminal factorization.
    pub fn build(graph: &Laplacian, options: CmgOptions) -> Result<Self, CmgError> {
        let hierarchy = CmgHierarchy::build(graph, options)?;
        let components = Components::from_laplacian(graph);
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
                repeat_counts[penultimate] = repeat_from_nonzeros(
                    hierarchy.levels()[penultimate].graph().matrix_nnz(),
                    factor.factor_nonzeros(),
                );
            }
        }

        Ok(Self {
            hierarchy,
            components,
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

    /// Allocate reusable storage compatible with this preconditioner.
    #[must_use]
    pub fn workspace(&self) -> CmgWorkspace {
        CmgWorkspace::new(&self.hierarchy, self.direct_terminal.as_ref())
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

    /// Apply with explicit compatibility-validation tolerances.
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
        self.components.validate_rhs(rhs, validation)?;
        workspace.validate(&self.hierarchy, self.direct_terminal.as_ref())?;
        self.apply_level(0, rhs, output, workspace)
    }

    fn apply_level(
        &self,
        level_index: usize,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
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
        let repeat = self.repeat_counts[level_index];
        if repeat == 0 {
            return Err(CmgError::InvalidHierarchy {
                context: "nonterminal level has zero recursive repeats",
            });
        }

        let mut local = workspace.take_level(level_index);
        let result = (|| {
            local.x.fill(0.0);
            for iteration in 0..repeat {
                if iteration == 0 {
                    for ((value, inverse_diagonal), rhs_value) in
                        local.x.iter_mut().zip(level.inverse_diagonal()).zip(rhs)
                    {
                        *value = *inverse_diagonal * *rhs_value;
                    }
                } else {
                    level.graph().matvec_into(&local.x, &mut local.matvec)?;
                    for (((value, inverse_diagonal), rhs_value), matrix_value) in local
                        .x
                        .iter_mut()
                        .zip(level.inverse_diagonal())
                        .zip(rhs)
                        .zip(&local.matvec)
                    {
                        *value += *inverse_diagonal * (*rhs_value - *matrix_value);
                    }
                }

                level.graph().matvec_into(&local.x, &mut local.matvec)?;
                for ((residual, rhs_value), matrix_value) in
                    local.residual.iter_mut().zip(rhs).zip(&local.matvec)
                {
                    *residual = *rhs_value - *matrix_value;
                }
                aggregation.restrict_into(&local.residual, &mut local.coarse_rhs)?;
                local.coarse_correction.fill(0.0);
                self.apply_level(
                    level_index + 1,
                    &local.coarse_rhs,
                    &mut local.coarse_correction,
                    workspace,
                )?;
                aggregation.prolong_add_into(&local.coarse_correction, &mut local.x)?;

                level.graph().matvec_into(&local.x, &mut local.matvec)?;
                for (((value, inverse_diagonal), rhs_value), matrix_value) in local
                    .x
                    .iter_mut()
                    .zip(level.inverse_diagonal())
                    .zip(rhs)
                    .zip(&local.matvec)
                {
                    *value += *inverse_diagonal * (*rhs_value - *matrix_value);
                }
            }
            output.copy_from_slice(&local.x);
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
