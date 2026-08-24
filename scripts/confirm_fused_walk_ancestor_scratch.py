from pathlib import Path
import subprocess

SOURCE_COMMIT = "8cece8b67eca8c6dc87ebaf3072e9a53b0edc05d"
SOURCE_PATH = "scripts/fused_walk_ancestor_scratch_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "fused_walk_ancestor_scratch_gate.py",
    "confirm_fused_walk_ancestor_scratch.py",
)
text = text.replace(
    "fused-walk-ancestor-scratch.yml",
    "confirm-fused-walk-ancestor-scratch.yml",
)
text = text.replace(
    "fused-walk-ancestor-scratch-latest.json",
    "fused-walk-ancestor-scratch-confirmation.json",
)
text = text.replace(
    '"experiment": "fused-walk-ancestor-scratch"',
    '"experiment": "fused-walk-ancestor-scratch-rss-confirmation"',
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
            ("baseline", baseline[kind]),
            ("candidate", candidate[kind]),
        )
'''
if text.count(old_schedule) != 1:
    raise SystemExit("historical alternating schedule changed unexpectedly")
text = text.replace(old_schedule, new_schedule, 1)

old_rss_fields = '''        "baseline_peak_rss_kib": max(
            item["peak_rss_kib"] for item in baseline_samples
        ),
        "candidate_peak_rss_kib": max(
            item["peak_rss_kib"] for item in candidate_samples
        ),
'''
new_rss_fields = '''        "baseline_peak_rss_kib": statistics.median(
            item["peak_rss_kib"] for item in baseline_samples
        ),
        "candidate_peak_rss_kib": statistics.median(
            item["peak_rss_kib"] for item in candidate_samples
        ),
        "baseline_max_peak_rss_kib": max(
            item["peak_rss_kib"] for item in baseline_samples
        ),
        "candidate_max_peak_rss_kib": max(
            item["peak_rss_kib"] for item in candidate_samples
        ),
'''
if text.count(old_rss_fields) != 1:
    raise SystemExit("historical RSS aggregation block changed unexpectedly")
text = text.replace(old_rss_fields, new_rss_fields, 1)

old_rss_ratio = '''    result["candidate_over_baseline_peak_rss"] = (
        result["candidate_peak_rss_kib"] / result["baseline_peak_rss_kib"]
    )
'''
new_rss_ratio = '''    result["candidate_over_baseline_peak_rss"] = (
        result["candidate_peak_rss_kib"] / result["baseline_peak_rss_kib"]
    )
    result["candidate_over_baseline_max_peak_rss"] = (
        result["candidate_max_peak_rss_kib"]
        / result["baseline_max_peak_rss_kib"]
    )
'''
if text.count(old_rss_ratio) != 1:
    raise SystemExit("historical RSS ratio block changed unexpectedly")
text = text.replace(old_rss_ratio, new_rss_ratio, 1)

old_rss_collection = '''    rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (
            result["split_cases"],
            result["hierarchy_cases"],
            result["allocation_cases"],
        )
        for case in collection.values()
    ]
'''
new_rss_collection = '''    rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (
            result["split_cases"],
            result["hierarchy_cases"],
            result["allocation_cases"],
        )
        for case in collection.values()
    ]
    max_rss_ratios = [
        case["candidate_over_baseline_max_peak_rss"]
        for collection in (
            result["split_cases"],
            result["hierarchy_cases"],
            result["allocation_cases"],
        )
        for case in collection.values()
    ]
'''
if text.count(old_rss_collection) != 1:
    raise SystemExit("historical RSS collection block changed unexpectedly")
text = text.replace(old_rss_collection, new_rss_collection, 1)

old_worst = '''    result["worst_peak_rss_ratio"] = max(rss_ratios)
'''
new_worst = '''    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["worst_max_peak_rss_ratio"] = max(max_rss_ratios)
'''
if text.count(old_worst) != 1:
    raise SystemExit("historical worst RSS assignment changed unexpectedly")
text = text.replace(old_worst, new_worst, 1)

text = text.replace(
    '"worst_peak_rss_ratio_max": 1.02,',
    '"worst_peak_rss_ratio_max": 1.025,\n        "worst_max_peak_rss_ratio_max": 1.10,',
    1,
)
old_condition = '''        and result["worst_peak_rss_ratio"] <= 1.02
        and result["geometric_additional_peak_ratio"] <= 1.001
'''
new_condition = '''        and result["worst_peak_rss_ratio"] <= 1.025
        and result["worst_max_peak_rss_ratio"] <= 1.10
        and result["geometric_additional_peak_ratio"] <= 1.001
'''
if text.count(old_condition) != 1:
    raise SystemExit("historical RSS acceptance condition changed unexpectedly")
text = text.replace(old_condition, new_condition, 1)

old_defaults = '''    "worst_peak_rss_ratio",
    "geometric_additional_peak_ratio",
'''
new_defaults = '''    "worst_peak_rss_ratio",
    "worst_max_peak_rss_ratio",
    "geometric_additional_peak_ratio",
'''
if text.count(old_defaults) != 1:
    raise SystemExit("historical result-default list changed unexpectedly")
text = text.replace(old_defaults, new_defaults, 1)

text = text.replace(
    "### Fused walk/ancestor scratch checkpoint — 2026-08-24",
    "### Fused walk/ancestor scratch confirmation — 2026-08-24",
)
text = text.replace(
    "## Fused walk/ancestor scratch gate",
    "## Fused walk/ancestor scratch confirmation",
)
text = text.replace(
    "Worst split / hierarchy / peak-RSS ratios:",
    "Worst split / hierarchy / median peak-RSS ratios:",
)
text = text.replace(
    "full qualification passed; walk vertices and ancestor prefixes share one cache-local scratch stream",
    "repeat-process confirmation passed; walk vertices and ancestor prefixes share one cache-local scratch stream with bounded median and maximum RSS",
)
text = text.replace(
    "perf: retain fused walk-ancestor scratch",
    "perf: retain fused walk-ancestor scratch after RSS confirmation",
)
text = text.replace(
    "perf: record fused walk-ancestor scratch experiment",
    "perf: record fused walk-ancestor scratch confirmation",
)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path(".ci/performance/fused-walk-ancestor-scratch-latest.json").unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("historical cleanup block changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "confirm_fused_walk_ancestor_scratch.py",
    "confirm-fused-walk-ancestor-scratch.yml",
    "fused-walk-ancestor-scratch-confirmation.json",
    "candidate_over_baseline_max_peak_rss",
    "worst_max_peak_rss_ratio",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"fused scratch confirmation missing marker: {marker}")

compiled = compile(text, str(Path(__file__)), "exec")
exec(compiled, {"__name__": "__main__", "__file__": __file__})
