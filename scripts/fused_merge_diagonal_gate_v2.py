from pathlib import Path
import subprocess

SOURCE_COMMIT = "89c559f5fb2c14fc91e4fad5c53e18a7e0cb39f9"
SOURCE_PATH = "scripts/fused_merge_diagonal_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace("fused_merge_diagonal_gate.py", "fused_merge_diagonal_gate_v2.py")
text = text.replace("fused-merge-diagonal.yml", "fused-merge-diagonal-v2.yml")

old_stable = '''    stable = ("case", "scale", "vertices", "edges", "repetitions")
    reference = baseline_samples[0]
'''
new_stable = '''    stable = ("case", "scale", "vertices", "repetitions")
    stable += (
        ("raw_edges", "retained_edges")
        if kind == "graph"
        else ("edges",)
    )
    reference = baseline_samples[0]
'''
if text.count(old_stable) != 1:
    raise SystemExit("historical compare metadata block changed unexpectedly")
text = text.replace(old_stable, new_stable, 1)

old_specs = '''    specs = (
        ("path-1m", ["path", "1000000", "3"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
    )
    for name, arguments in specs:
        result["graph_cases"][name] = compare("graph", baseline, candidate, arguments, name)
        result["hierarchy_cases"][name] = compare(
            "hierarchy", baseline, candidate, arguments, name
        )
'''
new_specs = '''    graph_specs = (
        ("unique-1m", ["unique", "1000000", "3"]),
        ("duplicates-4-1m", ["duplicates-4", "250000", "3"]),
        ("duplicates-16-1.6m", ["duplicates-16", "100000", "3"]),
        ("coarse-collisions-1.6m", ["coarse-collisions", "100000", "3"]),
    )
    hierarchy_specs = (
        ("path-1m", ["path", "1000000", "3"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
    )
    for name, arguments in graph_specs:
        result["graph_cases"][name] = compare("graph", baseline, candidate, arguments, name)
    for name, arguments in hierarchy_specs:
        result["hierarchy_cases"][name] = compare(
            "hierarchy", baseline, candidate, arguments, name
        )
'''
if text.count(old_specs) != 1:
    raise SystemExit("historical benchmark specification block changed unexpectedly")
text = text.replace(old_specs, new_specs, 1)

required = (
    "fused_merge_diagonal_gate_v2.py",
    "fused-merge-diagonal-v2.yml",
    '"duplicates-16-1.6m"',
    'if kind == "graph"',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired fused gate missing marker: {marker}")

compiled = compile(text, str(Path(__file__)), "exec")
exec(compiled, {"__name__": "__main__", "__file__": __file__})
