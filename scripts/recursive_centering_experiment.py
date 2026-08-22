from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


preconditioner = Path("src/preconditioner.rs")
text = preconditioner.read_text()
old_projection = '''            let components = &self.level_components[level_index + 1];
            let mut component_workspace = workspace.take_component(level_index + 1);
            let projection = components.project_rhs_in_place_with_workspace(
                &mut local.coarse_rhs,
                ValidationOptions {
                    symmetry_tolerance: validation.symmetry_tolerance,
                    compatibility_tolerance: 1.0,
                },
                &mut component_workspace,
            );
            workspace.put_component(level_index + 1, component_workspace);
            projection?;
'''
new_projection = '''            let components = &self.level_components[level_index + 1];
            let mut component_workspace = workspace.take_component(level_index + 1);
            // Restricted residuals are component-compatible in exact
            // arithmetic. Remove only floating-point null-space drift before
            // the recursive solve instead of repeating full public-boundary
            // compatibility validation and exact correction passes.
            let centering = components.center_in_place_with_workspace(
                &mut local.coarse_rhs,
                &mut component_workspace,
            );
            workspace.put_component(level_index + 1, component_workspace);
            centering?;
'''
text = replace_once(
    text,
    old_projection,
    new_projection,
    "recursive coarse projection",
)
text = text.replace(
    "recursive coarse-level roundoff handling is unchanged.",
    "recursive coarse-level roundoff is removed by deterministic component centering.",
)
text = text.replace(
    "tolerances for recursive coarse-level roundoff handling.",
    "tolerances for public validation; recursive coarse residuals are centered.",
)
preconditioner.write_text(text)

upstream_test = Path("tests/upstream_cycle.rs")
text = upstream_test.read_text()
helper_anchor = '''fn reference_apply(
'''
center_helper = '''fn center_rhs(graph: &Laplacian, rhs: &[f64]) -> Vec<f64> {
    let mut centered = rhs.to_vec();
    Components::from_laplacian(graph)
        .center_in_place(&mut centered)
        .unwrap();
    centered
}

'''
if center_helper in text:
    raise SystemExit("center helper already present")
text = replace_once(text, helper_anchor, center_helper + helper_anchor, "test helper insertion")
old_coarse = '''        let coarse_graph = preconditioner.hierarchy().levels()[level_index + 1].graph();
        let coarse_rhs = project_rhs(coarse_graph, &aggregation.restrict(&residual).unwrap(), 1.0);
'''
new_coarse = '''        let coarse_graph = preconditioner.hierarchy().levels()[level_index + 1].graph();
        let coarse_rhs = center_rhs(coarse_graph, &aggregation.restrict(&residual).unwrap());
'''
text = replace_once(text, old_coarse, new_coarse, "independent reference centering")
upstream_test.write_text(text)
