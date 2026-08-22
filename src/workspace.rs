//! Reusable caller-owned storage for CMG applications.

use crate::components::ComponentWorkspace;
use crate::{CmgError, CmgHierarchy, Components, GroundedLdl};

#[derive(Debug, Clone, Default)]
pub(crate) struct LevelWorkspace {
    pub(crate) x: Vec<f64>,
    pub(crate) matvec: Vec<f64>,
    pub(crate) residual: Vec<f64>,
    pub(crate) coarse_rhs: Vec<f64>,
    pub(crate) coarse_correction: Vec<f64>,
    pub(crate) factor_forward: Vec<f64>,
    pub(crate) factor_solution: Vec<f64>,
}

/// Reusable work arrays for applying one fixed CMG preconditioner.
#[derive(Debug, Clone)]
pub struct CmgWorkspace {
    levels: Vec<LevelWorkspace>,
    component_workspaces: Vec<ComponentWorkspace>,
    dimensions: Vec<usize>,
    projected_rhs: Vec<f64>,
}

impl CmgWorkspace {
    pub(crate) fn new(
        hierarchy: &CmgHierarchy,
        direct_terminal: Option<&GroundedLdl>,
        level_components: &[Components],
    ) -> Self {
        let dimensions: Vec<usize> = hierarchy
            .levels()
            .iter()
            .map(|level| level.graph().vertex_count())
            .collect();
        let projected_rhs = vec![0.0; dimensions.first().copied().unwrap_or(0)];
        let last = dimensions.len().saturating_sub(1);
        let levels = dimensions
            .iter()
            .enumerate()
            .map(|(index, &dimension)| {
                let coarse_dimension = dimensions.get(index + 1).copied().unwrap_or(0);
                let factor_dimension = if index == last {
                    direct_terminal
                        .map(GroundedLdl::active_dimension)
                        .unwrap_or(0)
                } else {
                    0
                };
                LevelWorkspace {
                    x: vec![0.0; dimension],
                    matvec: vec![0.0; dimension],
                    residual: vec![0.0; dimension],
                    coarse_rhs: vec![0.0; coarse_dimension],
                    coarse_correction: vec![0.0; coarse_dimension],
                    factor_forward: vec![0.0; factor_dimension],
                    factor_solution: vec![0.0; factor_dimension],
                }
            })
            .collect();
        debug_assert_eq!(level_components.len(), dimensions.len());
        let component_workspaces = level_components.iter().map(Components::workspace).collect();
        Self {
            levels,
            component_workspaces,
            dimensions,
            projected_rhs,
        }
    }

    /// Return the number of hierarchy levels represented by this workspace.
    #[must_use]
    pub fn level_count(&self) -> usize {
        self.levels.len()
    }

    /// Return fine-to-coarse vector dimensions.
    #[must_use]
    pub fn dimensions(&self) -> &[usize] {
        &self.dimensions
    }

    /// Return the number of heap bytes reserved by the principal work arrays.
    #[must_use]
    pub fn byte_len(&self) -> usize {
        let level_bytes: usize = self
            .levels
            .iter()
            .map(|level| {
                [
                    level.x.len(),
                    level.matvec.len(),
                    level.residual.len(),
                    level.coarse_rhs.len(),
                    level.coarse_correction.len(),
                    level.factor_forward.len(),
                    level.factor_solution.len(),
                ]
                .into_iter()
                .sum::<usize>()
                .saturating_mul(8)
            })
            .sum();
        let component_bytes: usize = self
            .component_workspaces
            .iter()
            .map(ComponentWorkspace::byte_len)
            .sum();
        level_bytes
            .saturating_add(component_bytes)
            .saturating_add(self.projected_rhs.len().saturating_mul(8))
    }

    pub(crate) fn validate(
        &self,
        hierarchy: &CmgHierarchy,
        direct_terminal: Option<&GroundedLdl>,
        level_components: &[Components],
    ) -> Result<(), CmgError> {
        if self.levels.len() != hierarchy.levels().len() {
            return Err(CmgError::dimension(
                "CmgWorkspace level count",
                hierarchy.levels().len(),
                self.levels.len(),
            ));
        }
        if self.component_workspaces.len() != level_components.len() {
            return Err(CmgError::dimension(
                "CmgWorkspace component level count",
                level_components.len(),
                self.component_workspaces.len(),
            ));
        }
        let fine_dimension = hierarchy
            .levels()
            .first()
            .map(|level| level.graph().vertex_count())
            .unwrap_or(0);
        validate_length(
            "CmgWorkspace projected rhs",
            fine_dimension,
            self.projected_rhs.len(),
        )?;
        let last = self.levels.len().saturating_sub(1);
        for (index, (workspace, level)) in self.levels.iter().zip(hierarchy.levels()).enumerate() {
            let dimension = level.graph().vertex_count();
            validate_length("CmgWorkspace x", dimension, workspace.x.len())?;
            validate_length("CmgWorkspace matvec", dimension, workspace.matvec.len())?;
            validate_length("CmgWorkspace residual", dimension, workspace.residual.len())?;
            let coarse_dimension = hierarchy
                .levels()
                .get(index + 1)
                .map(|coarse| coarse.graph().vertex_count())
                .unwrap_or(0);
            validate_length(
                "CmgWorkspace coarse rhs",
                coarse_dimension,
                workspace.coarse_rhs.len(),
            )?;
            validate_length(
                "CmgWorkspace coarse correction",
                coarse_dimension,
                workspace.coarse_correction.len(),
            )?;
            let factor_dimension = if index == last {
                direct_terminal
                    .map(GroundedLdl::active_dimension)
                    .unwrap_or(0)
            } else {
                0
            };
            validate_length(
                "CmgWorkspace factor forward",
                factor_dimension,
                workspace.factor_forward.len(),
            )?;
            validate_length(
                "CmgWorkspace factor solution",
                factor_dimension,
                workspace.factor_solution.len(),
            )?;
        }
        Ok(())
    }

    pub(crate) fn take_projected_rhs(&mut self) -> Vec<f64> {
        core::mem::take(&mut self.projected_rhs)
    }

    pub(crate) fn put_projected_rhs(&mut self, projected_rhs: Vec<f64>) {
        self.projected_rhs = projected_rhs;
    }

    pub(crate) fn take_level(&mut self, level: usize) -> LevelWorkspace {
        core::mem::take(&mut self.levels[level])
    }

    pub(crate) fn put_level(&mut self, level: usize, workspace: LevelWorkspace) {
        self.levels[level] = workspace;
    }

    pub(crate) fn take_component(&mut self, level: usize) -> ComponentWorkspace {
        core::mem::take(&mut self.component_workspaces[level])
    }

    pub(crate) fn put_component(&mut self, level: usize, workspace: ComponentWorkspace) {
        self.component_workspaces[level] = workspace;
    }
}

fn validate_length(context: &'static str, expected: usize, actual: usize) -> Result<(), CmgError> {
    if expected == actual {
        Ok(())
    } else {
        Err(CmgError::dimension(context, expected, actual))
    }
}
