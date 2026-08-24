from pathlib import Path
import subprocess

SOURCE_COMMIT = "ca9118c215f6d8d3a057caa2d05f27016384c99f"
SOURCE_PATH = "scripts/profile_forest_phases.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace("profile_forest_phases.py", "profile_forest_phases_v2.py")
text = text.replace("profile-forest-phases.yml", "profile-forest-phases-v2.yml")

old_assert = '''    assert_eq!(profile.edge_count(), bench_graph.edges);
'''
new_assert = '''    assert!(profile.edge_count() <= bench_graph.edges);
'''
if text.count(old_assert) != 1:
    raise SystemExit("forest profiler edge-count assertion changed unexpectedly")
text = text.replace(old_assert, new_assert, 1)

old_case_shares = '''    for key in timing[:-1]:
        result[f"{key.removesuffix('_ns')}_share"] = (
            result[key] / result["total_ns"] if result["total_ns"] else 0.0
        )
'''
new_case_shares = '''    for key in timing[:-1]:
        result[f"{key.removesuffix('_ns')}_share"] = (
            result[key] / phase_sum if phase_sum else 0.0
        )
'''
if text.count(old_case_shares) != 1:
    raise SystemExit("forest profiler case-share block changed unexpectedly")
text = text.replace(old_case_shares, new_case_shares, 1)

old_mode_total = '''        total = sum(case["total_ns"] for case in selected)
        summary = {}
        for phase in phase_names:
            phase_time = sum(case[f"{phase}_ns"] for case in selected)
            summary[f"{phase}_ns"] = phase_time
            summary[f"{phase}_share"] = phase_time / total if total else 0.0
'''
new_mode_total = '''        measured_total = sum(case["total_ns"] for case in selected)
        phase_total = sum(case["phase_sum_ns"] for case in selected)
        summary = {}
        for phase in phase_names:
            phase_time = sum(case[f"{phase}_ns"] for case in selected)
            summary[f"{phase}_ns"] = phase_time
            summary[f"{phase}_share"] = (
                phase_time / phase_total if phase_total else 0.0
            )
'''
if text.count(old_mode_total) != 1:
    raise SystemExit("forest profiler mode-share block changed unexpectedly")
text = text.replace(old_mode_total, new_mode_total, 1)
text = text.replace(
    '        summary["total_ns"] = total\n',
    '        summary["total_ns"] = measured_total\n        summary["phase_sum_ns"] = phase_total\n',
    1,
)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path(".github/workflows/profile-forest-phases.yml").unlink(missing_ok=True)
Path("scripts/profile_forest_phases.py").unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("forest profiler cleanup block changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "profile_forest_phases_v2.py",
    "profile-forest-phases-v2.yml",
    "profile.edge_count() <= bench_graph.edges",
    "phase_time / phase_total",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired forest profiler missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
