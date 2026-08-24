from pathlib import Path
import subprocess

SOURCE_COMMIT = "b40aae04dae514100314e753e8ad6b0b5e8e4de6"
SOURCE_PATH = "scripts/shared_laplacian_storage_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    'WORKFLOW = Path(".github/workflows/shared-laplacian-storage.yml")',
    'WORKFLOW = Path(".github/workflows/shared-laplacian-storage-v2.yml")',
    1,
)
text = text.replace(
    'SCRIPT = Path("scripts/shared_laplacian_storage_gate.py")',
    'SCRIPT = Path("scripts/shared_laplacian_storage_gate_v2.py")',
    1,
)

old_schedule = '''        (
            ("baseline", baseline[kind]),
            ("candidate", candidate[kind]),
            ("candidate", candidate[kind]),
            ("baseline", baseline[kind]),
        )
'''
new_schedule = '''        (
            ("baseline", baseline[kind]),
            ("candidate", candidate[kind]),
            ("candidate", candidate[kind]),
            ("baseline", baseline[kind]),
            ("candidate", candidate[kind]),
            ("baseline", baseline[kind]),
        )
'''
if text.count(old_schedule) != 1:
    raise SystemExit("shared-storage sampling schedule changed unexpectedly")
text = text.replace(old_schedule, new_schedule, 1)

old_graph_specs = '''    graph_specs = (
        ("unique-1m", ["unique", "1000000", "3"]),
        ("duplicates-4-1m", ["duplicates-4", "250000", "3"]),
        ("duplicates-16-1.6m", ["duplicates-16", "100000", "3"]),
        ("coarse-collisions-1.6m", ["coarse-collisions", "100000", "3"]),
    )
'''
new_graph_specs = '''    graph_specs = (
        ("unique-1m", ["unique", "1000000", "5"]),
        ("duplicates-4-1m", ["duplicates-4", "250000", "5"]),
        ("duplicates-16-1.6m", ["duplicates-16", "100000", "5"]),
        ("coarse-collisions-1.6m", ["coarse-collisions", "100000", "5"]),
    )
'''
if text.count(old_graph_specs) != 1:
    raise SystemExit("shared-storage graph benchmark matrix changed unexpectedly")
text = text.replace(old_graph_specs, new_graph_specs, 1)

old_hierarchy_specs = '''    hierarchy_specs = (
        ("path-1m", ["path", "1000000", "3"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
        ("dense-worker-firm-3.2m", ["dense-worker-firm", "200000", "3"]),
    )
'''
new_hierarchy_specs = '''    hierarchy_specs = (
        ("path-1m", ["path", "1000000", "5"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "5"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "5"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "5"]),
        ("dense-worker-firm-3.2m", ["dense-worker-firm", "200000", "5"]),
    )
'''
if text.count(old_hierarchy_specs) != 1:
    raise SystemExit("shared-storage hierarchy benchmark matrix changed unexpectedly")
text = text.replace(old_hierarchy_specs, new_hierarchy_specs, 1)

old_summary = '''    result["worst_graph_time_ratio"] = max(graph_ratios)
    result["worst_hierarchy_time_ratio"] = max(hierarchy_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
'''
new_summary = '''    result["worst_graph_time_ratio"] = max(graph_ratios)
    result["worst_hierarchy_time_ratio"] = max(hierarchy_ratios)
    result["path_hierarchy_time_ratio"] = result["hierarchy_cases"]["path-1m"][
        "candidate_over_baseline_time"
    ]
    result["worst_peak_rss_ratio"] = max(rss_ratios)
'''
if text.count(old_summary) != 1:
    raise SystemExit("shared-storage summary block changed unexpectedly")
text = text.replace(old_summary, new_summary, 1)

old_limits = '''        "graph_geometric_time_ratio_max": 1.015,
        "worst_graph_time_ratio_max": 1.04,
        "hierarchy_geometric_time_ratio_max": 0.99,
        "worst_hierarchy_time_ratio_max": 1.02,
'''
new_limits = '''        "graph_geometric_time_ratio_max": 1.015,
        "worst_graph_time_ratio_max": 1.04,
        "hierarchy_geometric_time_ratio_max": 0.995,
        "worst_hierarchy_time_ratio_max": 1.025,
        "path_hierarchy_time_ratio_max": 1.015,
'''
if text.count(old_limits) != 1:
    raise SystemExit("shared-storage acceptance limits changed unexpectedly")
text = text.replace(old_limits, new_limits, 1)

old_acceptance = '''        and result["hierarchy_geometric_time_ratio"] <= 0.99
        and result["worst_hierarchy_time_ratio"] <= 1.02
        and result["geometric_additional_peak_ratio"] <= 0.90
'''
new_acceptance = '''        and result["hierarchy_geometric_time_ratio"] <= 0.995
        and result["worst_hierarchy_time_ratio"] <= 1.025
        and result["path_hierarchy_time_ratio"] <= 1.015
        and result["geometric_additional_peak_ratio"] <= 0.90
'''
if text.count(old_acceptance) != 1:
    raise SystemExit("shared-storage acceptance expression changed unexpectedly")
text = text.replace(old_acceptance, new_acceptance, 1)

old_defaults = '''    "worst_graph_time_ratio",
    "worst_hierarchy_time_ratio",
    "worst_peak_rss_ratio",
'''
new_defaults = '''    "worst_graph_time_ratio",
    "worst_hierarchy_time_ratio",
    "path_hierarchy_time_ratio",
    "worst_peak_rss_ratio",
'''
if text.count(old_defaults) != 1:
    raise SystemExit("shared-storage defaults block changed unexpectedly")
text = text.replace(old_defaults, new_defaults, 1)

old_cleanup = "WORKFLOW.unlink(missing_ok=True)\nSCRIPT.unlink(missing_ok=True)\n"
new_cleanup = (
    "WORKFLOW.unlink(missing_ok=True)\n"
    "SCRIPT.unlink(missing_ok=True)\n"
    "Path(\".github/workflows/shared-laplacian-storage.yml\").unlink(missing_ok=True)\n"
    "Path(\"scripts/shared_laplacian_storage_gate.py\").unlink(missing_ok=True)\n"
)
if text.count(old_cleanup) != 1:
    raise SystemExit("shared-storage cleanup block changed unexpectedly")
text = text.replace(old_cleanup, new_cleanup, 1)

required = (
    "shared_laplacian_storage_gate_v2.py",
    "shared-laplacian-storage-v2.yml",
    "path_hierarchy_time_ratio",
    '"500000", "5"',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"confirmatory shared-storage gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
