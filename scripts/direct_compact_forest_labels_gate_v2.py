from pathlib import Path

SOURCE = Path("scripts/direct_compact_forest_labels_gate.py")
text = SOURCE.read_text()

insert_after = '''FOREST_RETURN_3_NEW = '''fn finish_forest_aggregation_labels(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    heavy_parent: Vec<usize>,
    selected_weight: Vec<f64>,
) -> Result<(ForestAggregationLabels, usize), CmgError> {
'''
'''
public_constants = '''FOREST_PUBLIC_OLD = '''pub fn forest_component_labels(
    parent: &[usize],
) -> Result<(Vec<usize>, usize), CmgError> {
    validate_parent_vector(parent)?;
    Ok(forest_component_labels_trusted(parent))
}
'''
FOREST_PUBLIC_NEW = '''pub fn forest_component_labels(
    parent: &[usize],
) -> Result<(Vec<usize>, usize), CmgError> {
    validate_parent_vector(parent)?;
    let (labels, aggregate_count) = forest_component_labels_trusted(parent);
    let labels = match labels {
        ForestAggregationLabels::Compact(labels) => labels
            .into_iter()
            .map(|label| label as usize)
            .collect(),
        ForestAggregationLabels::Native(labels) => labels,
    };
    Ok((labels, aggregate_count))
}
'''
'''
if text.count(insert_after) != 1:
    raise SystemExit("compact-label return-type marker changed unexpectedly")
text = text.replace(insert_after, insert_after + public_constants, 1)

old_tuple = '''        (FOREST_RETURN_3, FOREST_RETURN_3_NEW, "finish builder return type"),
        (FOREST_COMPONENT_OLD, FOREST_COMPONENT_NEW, "trusted component labeling"),
'''
new_tuple = '''        (FOREST_RETURN_3, FOREST_RETURN_3_NEW, "finish builder return type"),
        (FOREST_PUBLIC_OLD, FOREST_PUBLIC_NEW, "public forest-label compatibility"),
        (FOREST_COMPONENT_OLD, FOREST_COMPONENT_NEW, "trusted component labeling"),
'''
if text.count(old_tuple) != 1:
    raise SystemExit("compact-label forest replacement tuple changed unexpectedly")
text = text.replace(old_tuple, new_tuple, 1)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path("scripts/direct_compact_forest_labels_gate.py").unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("compact-label cleanup marker changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

text = text.replace(
    'SCRIPT = Path("scripts/direct_compact_forest_labels_gate.py")',
    'SCRIPT = Path("scripts/direct_compact_forest_labels_gate_v2.py")',
    1,
)

required = (
    "FOREST_PUBLIC_OLD",
    "FOREST_PUBLIC_NEW",
    "public forest-label compatibility",
    "direct_compact_forest_labels_gate_v2.py",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"compact-label v2 wrapper missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
