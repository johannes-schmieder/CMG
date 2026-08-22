//! Reusable caller-owned storage for CMG applications.

use crate::{CmgError, CmgHierarchy, GroundedLdl};

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
    dimensions: Vec<usize>,
}

impl CmgWorkspace {
    pub(crate) fn new(hierarchy: &CmgHierarchy, direct_terminal: Option<&GroundedLdl>) -> Self {
        let dimensions: Vec<usize> = hierarchy
            .levels()
            .iter()
            .map(|level| level.graph().vertex_count())
            .collect();
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
        Self { levels, dimensions }
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

    pub(crate) fn validate(
        &self,
        hierarchy: &CmgHierarchy,
        direct_terminal: Option<&GroundedLdl>,
    ) -> Result<(), CmgError> {
        if self.levels.len() != hierarchy.levels().len() {
            return Err(CmgError::dimension(
                "CmgWorkspace level count",
                hierarchy.levels().len(),
                self.levels.len(),
            ));
        }
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

    pub(crate) fn take_level(&mut self, level: usize) -> LevelWorkspace {
        core::mem::take(&mut self.levels[level])
    }

    pub(crate) fn put_level(&mut self, level: usize, workspace: LevelWorkspace) {
        self.levels[level] = workspace;
    }
}

fn validate_length(context: &'static str, expected: usize, actual: usize) -> Result<(), CmgError> {
    if expected == actual {
        Ok(())
    } else {
        Err(CmgError::dimension(context, expected, actual))
    }
}
