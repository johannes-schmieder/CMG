from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


components = Path("src/components.rs")
text = components.read_text()
text = replace_once(
    text,
    "    fn validate(&self, component_count: usize) -> Result<(), CmgError> {\n",
    "    pub(crate) fn validate(&self, component_count: usize) -> Result<(), CmgError> {\n",
    "component workspace validation visibility",
)
insert_anchor = "/// Connected-component metadata for a weighted graph.\n"
insert = '''#[derive(Debug, Clone, Default)]
pub(crate) struct CenteringWorkspace {
    sums: Vec<f64>,
    corrections: Vec<f64>,
    means: Vec<f64>,
}

impl CenteringWorkspace {
    fn new(component_count: usize) -> Self {
        Self {
            sums: vec![0.0; component_count],
            corrections: vec![0.0; component_count],
            means: vec![0.0; component_count],
        }
    }

    fn validate(&self, component_count: usize) -> Result<(), CmgError> {
        for (context, actual) in [
            ("CenteringWorkspace sums", self.sums.len()),
            ("CenteringWorkspace corrections", self.corrections.len()),
            ("CenteringWorkspace means", self.means.len()),
        ] {
            if actual != component_count {
                return Err(CmgError::dimension(context, component_count, actual));
            }
        }
        Ok(())
    }

    pub(crate) fn byte_len(&self) -> usize {
        self.sums.len().saturating_mul(3).saturating_mul(8)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum CenteringLabels {
    Single,
    Compact(Vec<u32>),
    Native(Vec<usize>),
}

/// Minimal component metadata needed for internal recursive centering.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct CenteringPlan {
    vertex_count: usize,
    labels: CenteringLabels,
    sizes: Vec<usize>,
}

impl CenteringPlan {
    pub(crate) fn from_laplacian(graph: &Laplacian) -> Self {
        let Components { labels, sizes } = Components::from_laplacian(graph);
        let vertex_count = labels.len();
        let component_count = sizes.len();
        let labels = if component_count <= 1 {
            CenteringLabels::Single
        } else if component_count <= u32::MAX as usize {
            CenteringLabels::Compact(labels.into_iter().map(|label| label as u32).collect())
        } else {
            CenteringLabels::Native(labels)
        };
        Self {
            vertex_count,
            labels,
            sizes,
        }
    }

    pub(crate) fn workspace(&self) -> CenteringWorkspace {
        CenteringWorkspace::new(self.sizes.len())
    }

    pub(crate) fn validate_workspace(
        &self,
        workspace: &CenteringWorkspace,
    ) -> Result<(), CmgError> {
        workspace.validate(self.sizes.len())
    }

    pub(crate) fn byte_len(&self) -> usize {
        let label_bytes = match &self.labels {
            CenteringLabels::Single => 0,
            CenteringLabels::Compact(labels) => labels.len().saturating_mul(4),
            CenteringLabels::Native(labels) => labels
                .len()
                .saturating_mul(core::mem::size_of::<usize>()),
        };
        label_bytes.saturating_add(
            self.sizes
                .len()
                .saturating_mul(core::mem::size_of::<usize>()),
        )
    }

    pub(crate) fn center_in_place_with_workspace(
        &self,
        values: &mut [f64],
        workspace: &mut CenteringWorkspace,
    ) -> Result<(), CmgError> {
        if values.len() != self.vertex_count {
            return Err(CmgError::dimension(
                "CenteringPlan::center_in_place",
                self.vertex_count,
                values.len(),
            ));
        }
        workspace.validate(self.sizes.len())?;
        workspace.sums.fill(0.0);
        workspace.corrections.fill(0.0);

        match &self.labels {
            CenteringLabels::Single => {
                if !self.sizes.is_empty() {
                    for (vertex, value) in values.iter().enumerate() {
                        if !value.is_finite() {
                            return Err(CmgError::NonFiniteMatrixValue {
                                row: vertex,
                                column: 0,
                                value: *value,
                            });
                        }
                        neumaier_add(
                            &mut workspace.sums[0],
                            &mut workspace.corrections[0],
                            *value,
                        );
                    }
                }
            }
            CenteringLabels::Compact(labels) => {
                for (vertex, (value, label)) in values.iter().zip(labels).enumerate() {
                    if !value.is_finite() {
                        return Err(CmgError::NonFiniteMatrixValue {
                            row: vertex,
                            column: 0,
                            value: *value,
                        });
                    }
                    let label = *label as usize;
                    neumaier_add(
                        &mut workspace.sums[label],
                        &mut workspace.corrections[label],
                        *value,
                    );
                }
            }
            CenteringLabels::Native(labels) => {
                for (vertex, (value, label)) in values.iter().zip(labels).enumerate() {
                    if !value.is_finite() {
                        return Err(CmgError::NonFiniteMatrixValue {
                            row: vertex,
                            column: 0,
                            value: *value,
                        });
                    }
                    neumaier_add(
                        &mut workspace.sums[*label],
                        &mut workspace.corrections[*label],
                        *value,
                    );
                }
            }
        }

        for component in 0..self.sizes.len() {
            workspace.sums[component] += workspace.corrections[component];
            workspace.means[component] =
                workspace.sums[component] / self.sizes[component] as f64;
        }
        match &self.labels {
            CenteringLabels::Single => {
                if let Some(mean) = workspace.means.first() {
                    for value in values {
                        *value -= *mean;
                    }
                }
            }
            CenteringLabels::Compact(labels) => {
                for (value, label) in values.iter_mut().zip(labels) {
                    *value -= workspace.means[*label as usize];
                }
            }
            CenteringLabels::Native(labels) => {
                for (value, label) in values.iter_mut().zip(labels) {
                    *value -= workspace.means[*label];
                }
            }
        }
        Ok(())
    }
}

'''
text = replace_once(text, insert_anchor, insert + insert_anchor, "centering plan insertion")
workspace_anchor = '''    pub(crate) fn workspace(&self) -> ComponentWorkspace {
        ComponentWorkspace::new(self.count())
    }
'''
workspace_methods = '''    pub(crate) fn workspace(&self) -> ComponentWorkspace {
        ComponentWorkspace::new(self.count())
    }

    pub(crate) fn validate_workspace(
        &self,
        workspace: &ComponentWorkspace,
    ) -> Result<(), CmgError> {
        workspace.validate(self.count())
    }

    pub(crate) fn byte_len(&self) -> usize {
        self.labels
            .len()
            .saturating_add(self.sizes.len())
            .saturating_mul(core::mem::size_of::<usize>())
    }
'''
text = replace_once(text, workspace_anchor, workspace_methods, "component metadata methods")
components.write_text(text)

workspace = Path("src/workspace.rs")
text = workspace.read_text()
text = replace_once(
    text,
    "use crate::components::ComponentWorkspace;\nuse crate::{CmgError, CmgHierarchy, Components, GroundedLdl};\n",
    "use crate::components::{CenteringPlan, CenteringWorkspace, ComponentWorkspace};\nuse crate::{CmgError, CmgHierarchy, Components, GroundedLdl};\n",
    "workspace imports",
)
text = replace_once(
    text,
    '''    component_workspaces: Vec<ComponentWorkspace>,
''',
    '''    component_workspace: ComponentWorkspace,
    centering_workspaces: Vec<CenteringWorkspace>,
''',
    "workspace component fields",
)
text = replace_once(
    text,
    '''        level_components: &[Components],
''',
    '''        finest_components: &Components,
        coarse_centering: &[CenteringPlan],
''',
    "workspace constructor arguments",
)
text = replace_once(
    text,
    '''        debug_assert_eq!(level_components.len(), dimensions.len());
        let component_workspaces = level_components.iter().map(Components::workspace).collect();
        Self {
            levels,
            component_workspaces,
            dimensions,
            projected_rhs,
        }
''',
    '''        debug_assert_eq!(coarse_centering.len(), dimensions.len().saturating_sub(1));
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
''',
    "workspace constructor body",
)
old_component_bytes = '''        let component_bytes: usize = self
            .component_workspaces
            .iter()
            .map(ComponentWorkspace::byte_len)
            .sum();
        level_bytes
            .saturating_add(component_bytes)
            .saturating_add(self.projected_rhs.len().saturating_mul(8))
'''
new_component_bytes = '''        let centering_bytes: usize = self
            .centering_workspaces
            .iter()
            .map(CenteringWorkspace::byte_len)
            .sum();
        level_bytes
            .saturating_add(self.component_workspace.byte_len())
            .saturating_add(centering_bytes)
            .saturating_add(self.projected_rhs.len().saturating_mul(8))
'''
text = replace_once(text, old_component_bytes, new_component_bytes, "workspace byte accounting")
text = replace_once(
    text,
    '''        level_components: &[Components],
''',
    '''        finest_components: &Components,
        coarse_centering: &[CenteringPlan],
''',
    "workspace validation arguments",
)
text = replace_once(
    text,
    '''        if self.component_workspaces.len() != level_components.len() {
            return Err(CmgError::dimension(
                "CmgWorkspace component level count",
                level_components.len(),
                self.component_workspaces.len(),
            ));
        }
''',
    '''        if self.centering_workspaces.len() != coarse_centering.len() {
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
''',
    "workspace component validation",
)
text = replace_once(
    text,
    '''    pub(crate) fn take_component(&mut self, level: usize) -> ComponentWorkspace {
        core::mem::take(&mut self.component_workspaces[level])
    }

    pub(crate) fn put_component(&mut self, level: usize, workspace: ComponentWorkspace) {
        self.component_workspaces[level] = workspace;
    }
''',
    '''    pub(crate) fn take_component(&mut self) -> ComponentWorkspace {
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
''',
    "workspace take-put methods",
)
workspace.write_text(text)

preconditioner = Path("src/preconditioner.rs")
text = preconditioner.read_text()
text = replace_once(
    text,
    "#[cfg(feature = \"parallel\")]\nuse crate::ParallelExecutor;\n",
    "#[cfg(feature = \"parallel\")]\nuse crate::ParallelExecutor;\nuse crate::components::CenteringPlan;\n",
    "preconditioner centering import",
)
text = replace_once(
    text,
    '''    level_components: Vec<Components>,
''',
    '''    finest_components: Components,
    coarse_centering: Vec<CenteringPlan>,
''',
    "preconditioner component fields",
)
text = replace_once(
    text,
    '''        let level_components = hierarchy
            .levels()
            .iter()
            .map(|level| Components::from_laplacian(level.graph()))
            .collect();
''',
    '''        let finest = hierarchy
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
''',
    "preconditioner component construction",
)
text = replace_once(
    text,
    '''            hierarchy,
            level_components,
            direct_terminal,
''',
    '''            hierarchy,
            finest_components,
            coarse_centering,
            direct_terminal,
''',
    "preconditioner component initialization",
)
text = replace_once(
    text,
    '''    pub(crate) fn finest_components(&self) -> &Components {
        &self.level_components[0]
    }
''',
    '''    pub(crate) fn finest_components(&self) -> &Components {
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
''',
    "preconditioner metadata diagnostics",
)
text = replace_once(
    text,
    '''            &self.level_components,
''',
    '''            &self.finest_components,
            &self.coarse_centering,
''',
    "preconditioner workspace construction",
)
if text.count("            &self.level_components,\n") != 2:
    raise SystemExit(
        f"preconditioner validation arguments: expected two anchors, found {text.count('            &self.level_components,\\n')}"
    )
text = text.replace(
    "            &self.level_components,\n",
    "            &self.finest_components,\n            &self.coarse_centering,\n",
)
text = replace_once(
    text,
    "            let mut component_workspace = workspace.take_component(0);\n            let projection = self.level_components[0].project_rhs_in_place_with_workspace(\n",
    "            let mut component_workspace = workspace.take_component();\n            let projection = self.finest_components.project_rhs_in_place_with_workspace(\n",
    "fine component projection",
)
text = replace_once(
    text,
    "            workspace.put_component(0, component_workspace);\n",
    "            workspace.put_component(component_workspace);\n",
    "fine component workspace return",
)
text = replace_once(
    text,
    '''                let components = &self.level_components[level_index + 1];
                let mut component_workspace = workspace.take_component(level_index + 1);
''',
    '''                let centering = &self.coarse_centering[level_index];
                let mut centering_workspace = workspace.take_centering(level_index);
''',
    "recursive centering metadata",
)
text = replace_once(
    text,
    '''                let centering = components.center_in_place_with_workspace(
                    &mut local.coarse_rhs,
                    &mut component_workspace,
                );
                workspace.put_component(level_index + 1, component_workspace);
                centering?;
''',
    '''                let centering_result = centering.center_in_place_with_workspace(
                    &mut local.coarse_rhs,
                    &mut centering_workspace,
                );
                workspace.put_centering(level_index, centering_workspace);
                centering_result?;
''',
    "recursive centering operation",
)
preconditioner.write_text(text)

benchmark = Path("src/bin/cmg-bench.rs")
text = benchmark.read_text()
text = replace_once(
    text,
    "    let terminal_factor_bytes = preconditioner\n",
    "    let component_metadata_bytes = preconditioner.component_metadata_bytes();\n    let terminal_factor_bytes = preconditioner\n",
    "benchmark metadata measurement",
)
text = replace_once(
    text,
    '''            "  \\\"hierarchy_core_bytes\\\": {},\\n",
            "  \\\"terminal_factor_bytes\\\": {},\\n",
''',
    '''            "  \\\"hierarchy_core_bytes\\\": {},\\n",
            "  \\\"component_metadata_bytes\\\": {},\\n",
            "  \\\"terminal_factor_bytes\\\": {},\\n",
''',
    "benchmark metadata JSON field",
)
text = replace_once(
    text,
    '''        hierarchy_core_bytes,
        terminal_factor_bytes,
''',
    '''        hierarchy_core_bytes,
        component_metadata_bytes,
        terminal_factor_bytes,
''',
    "benchmark metadata format argument",
)
benchmark.write_text(text)
