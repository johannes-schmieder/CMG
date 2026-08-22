from pathlib import Path

path = Path("src/pcg.rs")
text = path.read_text()

replacements = [
    ("    fresh_residual: Vec<f64>,\n    original_residual: Vec<f64>,\n", ""),
    ("            fresh_residual: vec![0.0; dimension],\n            original_residual: vec![0.0; dimension],\n", ""),
    (".saturating_mul(8)\n            .saturating_add(self.component.byte_len())", ".saturating_mul(6)\n            .saturating_add(self.component.byte_len())"),
    ("            (\"PcgWorkspace fresh residual\", self.fresh_residual.len()),\n            (\n                \"PcgWorkspace original residual\",\n                self.original_residual.len(),\n            ),\n", ""),
    ("    workspace.fresh_residual.fill(0.0);\n    workspace.original_residual.fill(0.0);\n", ""),
    ("                &mut workspace.fresh_residual,\n", "                &mut workspace.matrix_direction,\n"),
    ("                .copy_from_slice(&workspace.fresh_residual);\n", "                .copy_from_slice(&workspace.matrix_direction);\n"),
    ("                    &workspace.fresh_residual,\n                    &mut workspace.original_residual,\n", "                    &workspace.matrix_direction,\n"),
    ("        &mut workspace.fresh_residual,\n", "        &mut workspace.matrix_direction,\n"),
    ("        &workspace.fresh_residual,\n        &mut workspace.original_residual,\n", "        &workspace.matrix_direction,\n"),
]
for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"PCG workspace anchor count {count}: {old[:70]!r}")
    text = text.replace(old, new, 1)

old = """fn original_residual_norm(
    original_rhs: &[f64],
    projected_rhs: &[f64],
    projected_residual: &[f64],
    original_residual: &mut [f64],
) -> f64 {
    for (((output, original), projected), residual) in original_residual
        .iter_mut()
        .zip(original_rhs)
        .zip(projected_rhs)
        .zip(projected_residual)
    {
        *output = *residual + (*original - *projected);
    }
    euclidean_norm(original_residual)
}
"""
new = """fn original_residual_norm(
    original_rhs: &[f64],
    projected_rhs: &[f64],
    projected_residual: &[f64],
) -> f64 {
    let values = || {
        original_rhs
            .iter()
            .zip(projected_rhs)
            .zip(projected_residual)
            .map(|((&original, &projected), &residual)| residual + (original - projected))
    };
    let scale = values().map(f64::abs).fold(0.0, f64::max);
    if scale == 0.0 {
        0.0
    } else {
        scale
            * compensated_sum(values().map(|value| {
                let scaled = value / scale;
                scaled * scaled
            }))
            .sqrt()
    }
}
"""
if text.count(old) != 1:
    raise SystemExit("original residual norm anchor changed")
path.write_text(text.replace(old, new, 1))
