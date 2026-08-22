from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


workspace = Path("src/workspace.rs")
text = workspace.read_text()
text = replace_once(
    text,
    "    pub(crate) x: Vec<f64>,\n",
    "",
    "level solution field",
)
text = replace_once(
    text,
    "                    x: vec![0.0; dimension],\n",
    "",
    "level solution allocation",
)
text = replace_once(
    text,
    "                    level.x.len(),\n",
    "",
    "level solution byte accounting",
)
text = replace_once(
    text,
    '            validate_length("CmgWorkspace x", dimension, workspace.x.len())?;\n',
    "",
    "level solution validation",
)
workspace.write_text(text)

preconditioner = Path("src/preconditioner.rs")
text = preconditioner.read_text()
old = '''        let mut local = workspace.take_level(level_index);
        let result = (|| {
            local.x.fill(0.0);
            for iteration in 0..iterations {
                if iteration == 0 {
                    for ((value, inverse_diagonal), rhs_value) in
                        local.x.iter_mut().zip(level.inverse_diagonal()).zip(rhs)
                    {
                        *value = *inverse_diagonal * *rhs_value;
                    }
                } else {
                    level.graph().matvec_into(&local.x, &mut local.residual)?;
                    for (((value, inverse_diagonal), rhs_value), matrix_value) in local
                        .x
                        .iter_mut()
                        .zip(level.inverse_diagonal())
                        .zip(rhs)
                        .zip(&local.residual)
                    {
                        *value += *inverse_diagonal * (*rhs_value - *matrix_value);
                    }
                }

                level.graph().matvec_into(&local.x, &mut local.residual)?;
                for (residual, rhs_value) in local.residual.iter_mut().zip(rhs) {
                    *residual = *rhs_value - *residual;
                }
                aggregation.restrict_into(&local.residual, &mut local.coarse_rhs)?;
                let mut component_workspace = workspace.take_component(level_index + 1);
                let projection = self.level_components[level_index + 1]
                    .project_rhs_in_place_with_workspace(
                        &mut local.coarse_rhs,
                        ValidationOptions {
                            symmetry_tolerance: validation.symmetry_tolerance,
                            compatibility_tolerance: 1.0,
                        },
                        &mut component_workspace,
                    );
                workspace.put_component(level_index + 1, component_workspace);
                projection?;
                local.coarse_correction.fill(0.0);
                self.apply_level(
                    level_index + 1,
                    &local.coarse_rhs,
                    &mut local.coarse_correction,
                    workspace,
                    child_iterations,
                    validation,
                )?;
                aggregation.prolong_add_into(&local.coarse_correction, &mut local.x)?;

                level.graph().matvec_into(&local.x, &mut local.residual)?;
                for (((value, inverse_diagonal), rhs_value), matrix_value) in local
                    .x
                    .iter_mut()
                    .zip(level.inverse_diagonal())
                    .zip(rhs)
                    .zip(&local.residual)
                {
                    *value += *inverse_diagonal * (*rhs_value - *matrix_value);
                }
            }
            output.copy_from_slice(&local.x);
            Ok(())
        })();
'''
new = '''        let mut local = workspace.take_level(level_index);
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
                let mut component_workspace = workspace.take_component(level_index + 1);
                let projection = self.level_components[level_index + 1]
                    .project_rhs_in_place_with_workspace(
                        &mut local.coarse_rhs,
                        ValidationOptions {
                            symmetry_tolerance: validation.symmetry_tolerance,
                            compatibility_tolerance: 1.0,
                        },
                        &mut component_workspace,
                    );
                workspace.put_component(level_index + 1, component_workspace);
                projection?;
                local.coarse_correction.fill(0.0);
                self.apply_level(
                    level_index + 1,
                    &local.coarse_rhs,
                    &mut local.coarse_correction,
                    workspace,
                    child_iterations,
                    validation,
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
'''
text = replace_once(text, old, new, "recursive level output block")
preconditioner.write_text(text)
