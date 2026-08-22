#!/usr/bin/env python3
"""Apply the crate-private prevalidated CMG path used by the PCG experiment."""

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


preconditioner = Path("src/preconditioner.rs")
text = preconditioner.read_text()
anchor = "    /// Apply with explicit compatibility-validation tolerances.\n"
method = '''    /// Apply a compatible right-hand side after the caller has already
    /// validated dimensions, workspace layout, and solver options.
    ///
    /// This crate-private path is used only by PCG, which establishes these
    /// invariants once at entry and retains ownership of the workspace for the
    /// complete solve. Public methods continue to validate every call.
    pub(crate) fn apply_compatible_prevalidated_into(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
    ) -> Result<(), CmgError> {
        let dimension = self.hierarchy.levels()[0].graph().vertex_count();
        debug_assert_eq!(rhs.len(), dimension);
        debug_assert_eq!(output.len(), dimension);
        debug_assert!(
            workspace
                .validate(
                    &self.hierarchy,
                    self.direct_terminal.as_ref(),
                    &self.finest_components,
                    &self.coarse_centering,
                )
                .is_ok(),
            "PCG supplied an incompatible CMG workspace"
        );
        self.apply_level(0, rhs, output, workspace, 1)
    }

'''
preconditioner.write_text(
    replace_once(text, anchor, method + anchor, "preconditioner insertion")
)

pcg = Path("src/pcg.rs")
text = pcg.read_text()
old = '''    preconditioner.apply_compatible_into_with_validation(
        &workspace.residual,
        &mut workspace.preconditioned,
        &mut workspace.cmg,
        options.validation,
    )?;
'''
new = '''    preconditioner.apply_compatible_prevalidated_into(
        &workspace.residual,
        &mut workspace.preconditioned,
        &mut workspace.cmg,
    )?;
'''
count = text.count(old)
if count != 2:
    raise SystemExit(f"PCG compatible-apply calls: expected two anchors, found {count}")
pcg.write_text(text.replace(old, new))
