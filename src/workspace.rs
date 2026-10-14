//! Reusable caller-owned storage for CMG applications.

use crate::components::{CenteringPlan, CenteringWorkspace, ComponentWorkspace};
use crate::{CmgError, CmgHierarchy, Components, GroundedLdl};

crate::storage::storage_fields!(
    LevelWorkspace,
    residual,
    coarse_rhs,
    coarse_correction,
    factor_forward,
    factor_solution
);
crate::storage::storage_fields!(
    CmgWorkspace,
    levels,
    component_workspace,
    centering_workspaces,
    dimensions,
    projected_rhs
);

#[derive(Debug, Clone, Default)]
pub(crate) struct LevelWorkspace {
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
    component_workspace: ComponentWorkspace,
    centering_workspaces: Vec<CenteringWorkspace>,
    dimensions: Vec<usize>,
    projected_rhs: Vec<f64>,
}

impl CmgWorkspace {
    pub(crate) fn allocation_bytes(
        hierarchy: &CmgHierarchy,
        direct_terminal: Option<&GroundedLdl>,
        finest_components: &Components,
        coarse_centering: &[CenteringPlan],
    ) -> Result<usize, CmgError> {
        use crate::storage::{add, bytes};
        let levels = hierarchy.levels();
        let mut total = add(
            bytes::<LevelWorkspace>(levels.len())?,
            bytes::<usize>(levels.len())?,
        )?;
        total = add(total, bytes::<CenteringWorkspace>(coarse_centering.len())?)?;
        total = add(total, bytes::<f64>(levels[0].graph().vertex_count())?)?;
        total = add(
            total,
            ComponentWorkspace::allocation_bytes(finest_components.count())?,
        )?;
        for plan in coarse_centering {
            total = add(total, plan.workspace_allocation_bytes()?)?;
        }
        for (index, level) in levels.iter().enumerate() {
            total = add(total, bytes::<f64>(level.graph().vertex_count())?)?;
            let coarse = levels
                .get(index + 1)
                .map_or(0, |l| l.graph().vertex_count());
            total = add(total, bytes::<[f64; 2]>(coarse)?)?;
        }
        add(
            total,
            bytes::<[f64; 2]>(direct_terminal.map_or(0, GroundedLdl::active_dimension))?,
        )
    }

    #[cfg(feature = "experimental-components")]
    pub(crate) fn allocation_bytes_controlled(
        hierarchy: &CmgHierarchy,
        direct_terminal: Option<&GroundedLdl>,
        finest_components: &Components,
        coarse_centering: &[CenteringPlan],
        meter: &mut crate::build_control::BuildMeter<'_>,
    ) -> Result<usize, CmgError> {
        use crate::storage::{add, bytes};
        meter.work("CMG workspace allocation fixed layouts", 128)?;
        let levels = hierarchy.levels();
        let mut total = add(
            bytes::<LevelWorkspace>(levels.len())?,
            bytes::<usize>(levels.len())?,
        )?;
        total = add(total, bytes::<CenteringWorkspace>(coarse_centering.len())?)?;
        total = add(total, bytes::<f64>(levels[0].graph().vertex_count())?)?;
        total = add(
            total,
            ComponentWorkspace::allocation_bytes(finest_components.count())?,
        )?;
        for plan in coarse_centering {
            meter.work("CMG workspace allocation centering layouts", 32)?;
            total = add(total, plan.workspace_allocation_bytes()?)?;
        }
        for (index, level) in levels.iter().enumerate() {
            meter.work("CMG workspace allocation level layouts", 64)?;
            total = add(total, bytes::<f64>(level.graph().vertex_count())?)?;
            let coarse = levels
                .get(index + 1)
                .map_or(0, |l| l.graph().vertex_count());
            total = add(total, bytes::<[f64; 2]>(coarse)?)?;
        }
        meter.work("CMG workspace allocation terminal layouts", 32)?;
        add(
            total,
            bytes::<[f64; 2]>(direct_terminal.map_or(0, GroundedLdl::active_dimension))?,
        )
    }

    #[cfg(feature = "parallel")]
    pub(crate) fn required_bytes(
        hierarchy: &CmgHierarchy,
        direct_terminal: Option<&GroundedLdl>,
        finest_components: &Components,
        coarse_centering: &[CenteringPlan],
    ) -> usize {
        let levels = hierarchy.levels();
        let last = levels.len().saturating_sub(1);
        let level_values = levels
            .iter()
            .enumerate()
            .fold(0_usize, |values, (index, level)| {
                let dimension = level.graph().vertex_count();
                let coarse_dimension = levels
                    .get(index + 1)
                    .map(|coarse| coarse.graph().vertex_count())
                    .unwrap_or(0);
                let factor_dimension = if index == last {
                    direct_terminal
                        .map(GroundedLdl::active_dimension)
                        .unwrap_or(0)
                } else {
                    0
                };
                values
                    .saturating_add(dimension)
                    .saturating_add(coarse_dimension.saturating_mul(2))
                    .saturating_add(factor_dimension.saturating_mul(2))
            });
        let centering_bytes = coarse_centering
            .iter()
            .map(CenteringPlan::workspace_bytes)
            .fold(0_usize, usize::saturating_add);
        level_values
            .saturating_mul(core::mem::size_of::<f64>())
            .saturating_add(finest_components.workspace_bytes())
            .saturating_add(centering_bytes)
            .saturating_add(
                levels
                    .first()
                    .map(|level| level.graph().vertex_count())
                    .unwrap_or(0)
                    .saturating_mul(core::mem::size_of::<f64>()),
            )
    }

    pub(crate) fn new(
        hierarchy: &CmgHierarchy,
        direct_terminal: Option<&GroundedLdl>,
        finest_components: &Components,
        coarse_centering: &[CenteringPlan],
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
                    residual: vec![0.0; dimension],
                    coarse_rhs: vec![0.0; coarse_dimension],
                    coarse_correction: vec![0.0; coarse_dimension],
                    factor_forward: vec![0.0; factor_dimension],
                    factor_solution: vec![0.0; factor_dimension],
                }
            })
            .collect();
        debug_assert_eq!(coarse_centering.len(), dimensions.len().saturating_sub(1));
        let component_workspace = finest_components.workspace();
        let centering_workspaces = coarse_centering
            .iter()
            .map(CenteringPlan::workspace)
            .collect();
        Self {
            levels,
            component_workspace,
            centering_workspaces,
            dimensions,
            projected_rhs,
        }
    }

    pub(crate) fn try_new(
        hierarchy: &CmgHierarchy,
        direct_terminal: Option<&GroundedLdl>,
        finest_components: &Components,
        coarse_centering: &[CenteringPlan],
    ) -> Result<Self, CmgError> {
        let level_count = hierarchy.levels().len();
        let mut dimensions = Vec::new();
        dimensions
            .try_reserve_exact(level_count)
            .map_err(|_| CmgError::AllocationFailed {
                context: "CMG workspace dimensions",
            })?;
        dimensions.extend(
            hierarchy
                .levels()
                .iter()
                .map(|level| level.graph().vertex_count()),
        );
        let projected_rhs = try_zeroed(
            dimensions.first().copied().unwrap_or(0),
            "CMG workspace projected RHS",
        )?;
        let last = dimensions.len().saturating_sub(1);
        let mut levels = Vec::new();
        levels
            .try_reserve_exact(dimensions.len())
            .map_err(|_| CmgError::AllocationFailed {
                context: "CMG workspace levels",
            })?;
        for (index, &dimension) in dimensions.iter().enumerate() {
            let coarse_dimension = dimensions.get(index + 1).copied().unwrap_or(0);
            let factor_dimension = if index == last {
                direct_terminal
                    .map(GroundedLdl::active_dimension)
                    .unwrap_or(0)
            } else {
                0
            };
            levels.push(LevelWorkspace {
                residual: try_zeroed(dimension, "CMG workspace residual")?,
                coarse_rhs: try_zeroed(coarse_dimension, "CMG workspace coarse RHS")?,
                coarse_correction: try_zeroed(coarse_dimension, "CMG workspace coarse correction")?,
                factor_forward: try_zeroed(factor_dimension, "CMG workspace factor forward")?,
                factor_solution: try_zeroed(factor_dimension, "CMG workspace factor solution")?,
            });
        }
        debug_assert_eq!(coarse_centering.len(), dimensions.len().saturating_sub(1));
        let component_workspace = finest_components.try_workspace()?;
        let mut centering_workspaces = Vec::new();
        centering_workspaces
            .try_reserve_exact(coarse_centering.len())
            .map_err(|_| CmgError::AllocationFailed {
                context: "CMG centering workspaces",
            })?;
        for plan in coarse_centering {
            centering_workspaces.push(plan.try_workspace()?);
        }
        Ok(Self {
            levels,
            component_workspace,
            centering_workspaces,
            dimensions,
            projected_rhs,
        })
    }

    /// Allocate and initialize a private workspace through bounded admission gates.
    #[cfg(feature = "experimental-components")]
    pub(crate) fn try_new_controlled(
        hierarchy: &CmgHierarchy,
        direct_terminal: Option<&GroundedLdl>,
        finest_components: &Components,
        coarse_centering: &[CenteringPlan],
        meter: &mut crate::build_control::BuildMeter<'_>,
    ) -> Result<Self, CmgError> {
        let mut dimensions =
            meter.try_capacity(hierarchy.levels().len(), "CMG workspace dimensions")?;
        for level in hierarchy.levels() {
            meter.work("CMG workspace dimensions", 16)?;
            dimensions.push(level.graph().vertex_count());
        }
        let projected_rhs = meter.try_filled(
            dimensions.first().copied().unwrap_or(0),
            0.0,
            "CMG workspace projected RHS",
        )?;
        let last = dimensions.len().saturating_sub(1);
        let mut levels = meter.try_capacity(dimensions.len(), "CMG workspace levels")?;
        for (index, &dimension) in dimensions.iter().enumerate() {
            meter.work("CMG workspace level dimensions", 32)?;
            let coarse_dimension = dimensions.get(index + 1).copied().unwrap_or(0);
            let factor_dimension = if index == last {
                direct_terminal
                    .map(GroundedLdl::active_dimension)
                    .unwrap_or(0)
            } else {
                0
            };
            let level = LevelWorkspace {
                residual: meter.try_filled(dimension, 0.0, "CMG workspace residual")?,
                coarse_rhs: meter.try_filled(coarse_dimension, 0.0, "CMG workspace coarse RHS")?,
                coarse_correction: meter.try_filled(
                    coarse_dimension,
                    0.0,
                    "CMG workspace coarse correction",
                )?,
                factor_forward: meter.try_filled(
                    factor_dimension,
                    0.0,
                    "CMG workspace factor forward",
                )?,
                factor_solution: meter.try_filled(
                    factor_dimension,
                    0.0,
                    "CMG workspace factor solution",
                )?,
            };
            meter.work("CMG workspace store level", 32)?;
            levels.push(level);
        }
        debug_assert_eq!(coarse_centering.len(), dimensions.len().saturating_sub(1));
        let component_workspace = finest_components.try_workspace_controlled(meter)?;
        let mut centering_workspaces =
            meter.try_capacity(coarse_centering.len(), "CMG centering workspaces")?;
        for plan in coarse_centering {
            meter.work("CMG centering workspace plan", 16)?;
            let workspace = plan.try_workspace_controlled(meter)?;
            meter.work("CMG centering workspace store", 32)?;
            centering_workspaces.push(workspace);
        }
        meter.work("CMG workspace finish", 32)?;
        Ok(Self {
            levels,
            component_workspace,
            centering_workspaces,
            dimensions,
            projected_rhs,
        })
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
        let centering_bytes: usize = self
            .centering_workspaces
            .iter()
            .map(CenteringWorkspace::byte_len)
            .sum();
        level_bytes
            .saturating_add(self.component_workspace.byte_len())
            .saturating_add(centering_bytes)
            .saturating_add(self.projected_rhs.len().saturating_mul(8))
    }

    pub(crate) fn validate(
        &self,
        hierarchy: &CmgHierarchy,
        direct_terminal: Option<&GroundedLdl>,
        finest_components: &Components,
        coarse_centering: &[CenteringPlan],
    ) -> Result<(), CmgError> {
        if self.levels.len() != hierarchy.levels().len() {
            return Err(CmgError::dimension(
                "CmgWorkspace level count",
                hierarchy.levels().len(),
                self.levels.len(),
            ));
        }
        if self.centering_workspaces.len() != coarse_centering.len() {
            return Err(CmgError::dimension(
                "CmgWorkspace centering level count",
                coarse_centering.len(),
                self.centering_workspaces.len(),
            ));
        }
        finest_components.validate_workspace(&self.component_workspace)?;
        for (plan, centering) in coarse_centering.iter().zip(&self.centering_workspaces) {
            plan.validate_workspace(centering)?;
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

    pub(crate) fn take_component(&mut self) -> ComponentWorkspace {
        core::mem::take(&mut self.component_workspace)
    }

    pub(crate) fn put_component(&mut self, workspace: ComponentWorkspace) {
        self.component_workspace = workspace;
    }

    pub(crate) fn take_centering(&mut self, level: usize) -> CenteringWorkspace {
        core::mem::take(&mut self.centering_workspaces[level])
    }

    pub(crate) fn put_centering(&mut self, level: usize, workspace: CenteringWorkspace) {
        self.centering_workspaces[level] = workspace;
    }
}

fn try_zeroed(len: usize, context: &'static str) -> Result<Vec<f64>, CmgError> {
    let mut values = Vec::new();
    values
        .try_reserve_exact(len)
        .map_err(|_| CmgError::AllocationFailed { context })?;
    values.resize(len, 0.0);
    Ok(values)
}

fn validate_length(context: &'static str, expected: usize, actual: usize) -> Result<(), CmgError> {
    if expected == actual {
        Ok(())
    } else {
        Err(CmgError::dimension(context, expected, actual))
    }
}

#[cfg(test)]
mod tests {
    use crate::{CmgOptions, CmgPreconditioner, Laplacian};

    #[test]
    fn recursive_apply_overwrites_stale_coarse_corrections() {
        let graph =
            Laplacian::from_edges(128, (0..127).map(|vertex| (vertex, vertex + 1, 1.0))).unwrap();
        let preconditioner = CmgPreconditioner::build(
            &graph,
            CmgOptions {
                direct_threshold: 2,
                ..CmgOptions::default()
            },
        )
        .unwrap();
        let known: Vec<f64> = (0..128).map(|index| (index as f64 / 11.0).sin()).collect();
        let rhs = graph.matvec(&known).unwrap();
        let mut workspace = preconditioner.workspace();
        let mut output = vec![0.0; 128];
        preconditioner
            .apply_compatible_into(&rhs, &mut output, &mut workspace)
            .unwrap();
        let expected = output.clone();

        output.fill(f64::NAN);
        for level in &mut workspace.levels {
            level.coarse_correction.fill(f64::NAN);
        }
        preconditioner
            .apply_compatible_into(&rhs, &mut output, &mut workspace)
            .unwrap();
        assert_eq!(
            expected
                .iter()
                .map(|value| value.to_bits())
                .collect::<Vec<_>>(),
            output
                .iter()
                .map(|value| value.to_bits())
                .collect::<Vec<_>>()
        );
    }
}

#[cfg(all(test, feature = "experimental-components"))]
mod controlled_workspace_tests {
    use super::CmgWorkspace;
    use crate::build_control::{
        BuildBoundary, BuildDecision, BuildMeter, BuildOperation, BuildStopReason,
    };
    use crate::components::CenteringPlan;
    use crate::{CmgError, CmgOptions, CmgPreconditioner, Components, Laplacian};

    #[test]
    fn controlled_workspace_matches_ordinary_zeroed_storage_and_application() {
        for direct_threshold in [2, 128] {
            let graph =
                Laplacian::from_edges(66, (0..63).map(|vertex| (vertex, vertex + 1, 1.0))).unwrap();
            let preconditioner = CmgPreconditioner::build(
                &graph,
                CmgOptions {
                    direct_threshold,
                    ..CmgOptions::default()
                },
            )
            .unwrap();
            let hierarchy = preconditioner.hierarchy();
            let terminal = preconditioner.terminal_factor();
            let components = Components::from_laplacian(&graph);
            let plans: Vec<_> = hierarchy.levels()[1..]
                .iter()
                .map(|level| CenteringPlan::from_laplacian(level.graph()))
                .collect();
            let mut ordinary =
                CmgWorkspace::try_new(hierarchy, terminal, &components, &plans).unwrap();
            let mut allocations = 0;
            let mut control = |boundary: BuildBoundary| {
                if let BuildOperation::Allocation { bytes } = boundary.operation {
                    allocations += bytes;
                }
                BuildDecision::Continue
            };
            let mut controlled = CmgWorkspace::try_new_controlled(
                hierarchy,
                terminal,
                &components,
                &plans,
                &mut BuildMeter::new(u64::MAX, &mut control),
            )
            .unwrap();
            assert_eq!(format!("{controlled:?}"), format!("{ordinary:?}"));
            assert_eq!(
                allocations,
                CmgWorkspace::allocation_bytes(hierarchy, terminal, &components, &plans).unwrap()
            );
            controlled
                .validate(hierarchy, terminal, &components, &plans)
                .unwrap();
            let known: Vec<_> = (0..66).map(|vertex| (vertex as f64 / 7.0).sin()).collect();
            let rhs = graph.matvec(&known).unwrap();
            let mut expected = vec![0.0; 66];
            let mut actual = vec![0.0; 66];
            preconditioner
                .apply_compatible_into(&rhs, &mut expected, &mut ordinary)
                .unwrap();
            preconditioner
                .apply_compatible_into(&rhs, &mut actual, &mut controlled)
                .unwrap();
            assert!(
                actual
                    .iter()
                    .zip(&expected)
                    .all(|(actual, expected)| actual.to_bits() == expected.to_bits())
            );
        }
    }

    #[test]
    fn controlled_workspace_stops_before_every_boundary() {
        let graph =
            Laplacian::from_edges(16, (0..15).map(|vertex| (vertex, vertex + 1, 1.0))).unwrap();
        let preconditioner = CmgPreconditioner::build(
            &graph,
            CmgOptions {
                direct_threshold: 2,
                ..CmgOptions::default()
            },
        )
        .unwrap();
        let hierarchy = preconditioner.hierarchy();
        let terminal = preconditioner.terminal_factor();
        let components = Components::from_laplacian(&graph);
        let plans: Vec<_> = hierarchy.levels()[1..]
            .iter()
            .map(|level| CenteringPlan::from_laplacian(level.graph()))
            .collect();
        let mut boundaries = Vec::new();
        let mut record = |boundary| {
            boundaries.push(boundary);
            BuildDecision::Continue
        };
        CmgWorkspace::try_new_controlled(
            hierarchy,
            terminal,
            &components,
            &plans,
            &mut BuildMeter::new(u64::MAX, &mut record),
        )
        .unwrap();
        for expected in &boundaries {
            for (decision, reason) in [
                (BuildDecision::StopUnused, BuildStopReason::StopUnused),
                (BuildDecision::Interrupted, BuildStopReason::Interrupted),
            ] {
                let mut observed = Vec::new();
                let mut control = |boundary: BuildBoundary| {
                    observed.push(boundary);
                    if boundary.chunk_id == expected.chunk_id {
                        decision
                    } else {
                        BuildDecision::Continue
                    }
                };
                let error = CmgWorkspace::try_new_controlled(
                    hierarchy,
                    terminal,
                    &components,
                    &plans,
                    &mut BuildMeter::new(u64::MAX, &mut control),
                )
                .unwrap_err();
                assert_eq!(observed, boundaries[..=expected.chunk_id as usize]);
                assert!(matches!(error, CmgError::BuildStopped {
                    reason: actual_reason, phase, chunk_id, charged_work,
                } if actual_reason == reason
                    && phase == expected.phase
                    && chunk_id == expected.chunk_id
                    && charged_work == expected.charged_work));
            }
            if let BuildOperation::Work { units } = expected.operation {
                let mut calls = 0;
                let mut control = |_| {
                    calls += 1;
                    BuildDecision::Continue
                };
                let error = CmgWorkspace::try_new_controlled(
                    hierarchy,
                    terminal,
                    &components,
                    &plans,
                    &mut BuildMeter::new(
                        expected.charged_work + u64::from(units) - 1,
                        &mut control,
                    ),
                )
                .unwrap_err();
                assert_eq!(calls, expected.chunk_id);
                assert!(matches!(error, CmgError::BuildStopped {
                    reason: BuildStopReason::WorkBudgetExhausted, phase, chunk_id, charged_work,
                } if phase == expected.phase
                    && chunk_id == expected.chunk_id
                    && charged_work == expected.charged_work));
            }
        }
    }
}

#[cfg(all(test, feature = "experimental-components"))]
mod controlled_workspace_allocation_tests {
    use crate::build_control::{BuildBoundary, BuildDecision, BuildOperation, BuildStopReason};
    use crate::{CmgError, CmgOptions, CmgPreconditioner, Laplacian};

    #[test]
    fn controlled_allocation_bytes_match_and_stop_before_each_chunk() {
        for direct_threshold in [2, 128] {
            let graph =
                Laplacian::from_edges(66, (0..63).map(|vertex| (vertex, vertex + 1, 1.0))).unwrap();
            let preconditioner = CmgPreconditioner::build(
                &graph,
                CmgOptions {
                    direct_threshold,
                    ..CmgOptions::default()
                },
            )
            .unwrap();
            let expected_bytes = preconditioner.workspace_allocation_bytes().unwrap();
            let mut boundaries = Vec::new();
            let mut record = |boundary| {
                boundaries.push(boundary);
                BuildDecision::Continue
            };
            assert_eq!(
                preconditioner
                    .workspace_allocation_bytes_controlled(u64::MAX, &mut record)
                    .unwrap(),
                expected_bytes
            );
            let mut complete_budget = 0;
            for expected in &boundaries {
                let BuildOperation::Work { units } = expected.operation else {
                    panic!("layout counting must not request allocation");
                };
                assert!((1..=1024).contains(&units));
                complete_budget = expected.charged_work + u64::from(units);
                for (decision, reason) in [
                    (BuildDecision::StopUnused, BuildStopReason::StopUnused),
                    (BuildDecision::Interrupted, BuildStopReason::Interrupted),
                ] {
                    let mut observed = Vec::new();
                    let mut control = |boundary: BuildBoundary| {
                        observed.push(boundary);
                        if boundary.chunk_id == expected.chunk_id {
                            decision
                        } else {
                            BuildDecision::Continue
                        }
                    };
                    let error = preconditioner
                        .workspace_allocation_bytes_controlled(u64::MAX, &mut control)
                        .unwrap_err();
                    assert_eq!(observed, boundaries[..=expected.chunk_id as usize]);
                    assert!(matches!(error, CmgError::BuildStopped {
                        reason: actual_reason, phase, chunk_id, charged_work,
                    } if actual_reason == reason
                        && phase == expected.phase
                        && chunk_id == expected.chunk_id
                        && charged_work == expected.charged_work));
                }
                let mut calls = 0;
                let mut control = |_| {
                    calls += 1;
                    BuildDecision::Continue
                };
                let error = preconditioner
                    .workspace_allocation_bytes_controlled(complete_budget - 1, &mut control)
                    .unwrap_err();
                assert_eq!(calls, expected.chunk_id);
                assert!(matches!(error, CmgError::BuildStopped {
                    reason: BuildStopReason::WorkBudgetExhausted, phase, chunk_id, charged_work,
                } if phase == expected.phase
                    && chunk_id == expected.chunk_id
                    && charged_work == expected.charged_work));
            }
            let mut control = |_| BuildDecision::Continue;
            assert_eq!(
                preconditioner
                    .workspace_allocation_bytes_controlled(complete_budget, &mut control)
                    .unwrap(),
                expected_bytes
            );
        }
    }
}
