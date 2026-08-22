from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


preconditioner = Path("src/preconditioner.rs")
text = preconditioner.read_text()
anchor = "    /// Apply with explicit compatibility-validation tolerances.\n"
method = '''    /// Apply a right-hand side already known to be component-compatible.
    ///
    /// This skips the fine-level compatibility scan and projection performed by
    /// [`Self::apply_into`]. It is intended for Krylov solvers that validate and
    /// project a submitted right-hand side once, then keep residuals in the
    /// Laplacian range. Dimension, workspace, and option checks remain enabled;
    /// recursive coarse-level roundoff handling is unchanged.
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
    /// tolerances for recursive coarse-level roundoff handling.
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
            &self.level_components,
        )?;
        validation.validate()?;
        self.apply_level(0, rhs, output, workspace, 1, validation)
    }

'''
if method in text:
    raise SystemExit("compatible apply method already present")
text = replace_once(text, anchor, method + anchor, "preconditioner insertion")
preconditioner.write_text(text)

pcg = Path("src/pcg.rs")
text = pcg.read_text()
old_call = "preconditioner.apply_into_with_validation"
call_count = text.count(old_call)
if call_count != 2:
    raise SystemExit(f"PCG calls: expected two anchors, found {call_count}")
text = text.replace(old_call, "preconditioner.apply_compatible_into_with_validation")
loop_anchor = '''        if iteration == options.max_iterations {
            break;
        }

        preconditioner.apply_compatible_into_with_validation(
'''
loop_replacement = '''        if iteration == options.max_iterations {
            break;
        }

        // The public solver projected the submitted RHS once. Remove only the
        // component-nullspace roundoff accumulated by Krylov updates before
        // reusing the compatible stationary core.
        components.center_in_place_with_workspace(
            &mut workspace.residual,
            &mut workspace.component,
        )?;
        preconditioner.apply_compatible_into_with_validation(
'''
text = replace_once(text, loop_anchor, loop_replacement, "PCG centering insertion")
pcg.write_text(text)

cycle = Path("benchmarks/c-kernel/src/cycle.rs")
text = cycle.read_text()
old_cycle_call = "preconditioner.apply_into("
cycle_count = text.count(old_cycle_call)
if cycle_count != 3:
    raise SystemExit(f"cycle calls: expected three anchors, found {cycle_count}")
cycle.write_text(text.replace(old_cycle_call, "preconditioner.apply_compatible_into("))

upstream_test = Path("tests/upstream_cycle.rs")
text = upstream_test.read_text()
old_assertion = '''    let reference = reference_apply(&preconditioner, 0, &projected_rhs, 1);
    assert_vector_close(&production, &reference, 1.0e-12);
'''
new_assertion = '''    let reference = reference_apply(&preconditioner, 0, &projected_rhs, 1);
    let mut compatible_workspace = preconditioner.workspace();
    let mut compatible = vec![0.0; graph.vertex_count()];
    preconditioner
        .apply_compatible_into(
            &projected_rhs,
            &mut compatible,
            &mut compatible_workspace,
        )
        .unwrap();
    assert_vector_close(&production, &reference, 1.0e-12);
    assert_vector_close(&compatible, &reference, 1.0e-12);
'''
text = replace_once(text, old_assertion, new_assertion, "upstream cycle assertion")
upstream_test.write_text(text)
