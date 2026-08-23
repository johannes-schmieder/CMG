"""Recover the prepared-solver candidate with its test helper repaired."""

import subprocess

PINNED_CANDIDATE_COMMIT = "eeb2ca727cfb96da6064797ed47437db8a896f55"
source = subprocess.check_output(
    [
        "git",
        "show",
        f"{PINNED_CANDIDATE_COMMIT}:scripts/add_prepared_parallel_solver.py",
    ],
    text=True,
)

helper_anchor = "fn routing_worker_firm_graph(per_side: usize, degree: usize) -> Laplacian {\n"
path_helper = '''fn routing_path_graph(vertices: usize) -> Laplacian {
    let edges = (0..vertices.saturating_sub(1)).map(|vertex| {
        (vertex, vertex + 1, 0.5 + (vertex % 17) as f64 / 11.0)
    });
    Laplacian::from_edges(vertices, edges).unwrap()
}

'''
if source.count(helper_anchor) != 1:
    raise SystemExit("prepared-solver test helper anchor was not unique")
source = source.replace(helper_anchor, path_helper + helper_anchor, 1)

replacements = [
    ("let path = path_graph(1_001);", "let path = routing_path_graph(1_001);"),
    ("let graph = path_graph(128);", "let graph = routing_path_graph(128);"),
]
for old, new in replacements:
    if source.count(old) != 1:
        raise SystemExit(f"prepared-solver path helper use was not unique: {old}")
    source = source.replace(old, new, 1)

exec(compile(source, "<add_prepared_parallel_solver_v2>", "exec"), {"__name__": "__main__"})
