from pathlib import Path
import subprocess

SOURCE_COMMIT = "b3bf4d2cd46d7cc2ae0192e9c068a1a79726a1dd"
SOURCE_PATH = "scripts/bounded_prefix_path_rss_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "bounded_prefix_path_rss_gate.py",
    "bounded_prefix_large_path_confirmation.py",
)
text = text.replace(
    "bounded-prefix-path-rss.yml",
    "bounded-prefix-large-path-confirmation.yml",
)
text = text.replace(
    "bounded-prefix-path-rss-latest.json",
    "bounded-prefix-large-path-confirmation.json",
)
text = text.replace(
    '.ci/performance/bounded-ancestor-prefix-memory-latest.json',
    '.ci/performance/bounded-prefix-path-rss-latest.json',
)
text = text.replace(
    "bounded-prefix-path-rss-scaling",
    "bounded-prefix-large-path-confirmation",
)
text = text.replace(
    "/tmp/cmg-bounded-path-rss-",
    "/tmp/cmg-bounded-large-path-",
)

old_specs = '''    specs = (
        ("path-250k", ["path", "250000", "2"]),
        ("path-500k", ["path", "500000", "2"]),
        ("path-1m", ["path", "1000000", "2"]),
        ("path-2m", ["path", "2000000", "2"]),
        ("path-4m", ["path", "4000000", "2"]),
    )
'''
new_specs = '''    specs = (
        ("path-2m", ["path", "2000000", "3"]),
        ("path-4m", ["path", "4000000", "3"]),
        ("path-8m", ["path", "8000000", "3"]),
    )
'''
if text.count(old_specs) != 1:
    raise SystemExit("path-scaling specification block changed unexpectedly")
text = text.replace(old_specs, new_specs, 1)

old_metrics = '''    result["minimum_rss_delta_kib"] = min(rss_deltas)
    result["maximum_rss_delta_kib"] = max(rss_deltas)
    result["rss_delta_range_kib"] = max(rss_deltas) - min(rss_deltas)
    result["largest_case_rss_ratio"] = result["cases"]["path-4m"][
        "candidate_over_baseline_rss"
    ]
    result["largest_case_rss_delta_bytes_per_vertex"] = (
        1024.0 * result["cases"]["path-4m"]["rss_delta_kib"] / 4000000.0
    )
'''
new_metrics = '''    result["minimum_rss_delta_kib"] = min(rss_deltas)
    result["maximum_rss_delta_kib"] = max(rss_deltas)
    result["path_4m_rss_ratio"] = result["cases"]["path-4m"][
        "candidate_over_baseline_rss"
    ]
    result["path_8m_rss_ratio"] = result["cases"]["path-8m"][
        "candidate_over_baseline_rss"
    ]
    result["path_8m_rss_delta_bytes_per_vertex"] = (
        1024.0 * result["cases"]["path-8m"]["rss_delta_kib"] / 8000000.0
    )
'''
if text.count(old_metrics) != 1:
    raise SystemExit("path-scaling RSS metric block changed unexpectedly")
text = text.replace(old_metrics, new_metrics, 1)

old_limits = '''    result["acceptance_limits"] = {
        "geometric_time_ratio_max": 0.98,
        "worst_time_ratio_max": 1.01,
        "maximum_rss_delta_kib_max": 6144,
        "rss_delta_range_kib_max": 3072,
        "largest_case_rss_ratio_max": 1.02,
        "largest_case_rss_delta_bytes_per_vertex_max": 1.5,
        "geometric_additional_peak_ratio_max": 1.0,
        "worst_additional_peak_ratio_max": 1.002,
        "geometric_retained_ratio_max": 1.001,
        "worst_retained_ratio_max": 1.001,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        prior.get("validation") == "success"
        and result["geometric_time_ratio"] <= 0.98
        and result["worst_time_ratio"] <= 1.01
        and result["maximum_rss_delta_kib"] <= 6144
        and result["rss_delta_range_kib"] <= 3072
        and result["largest_case_rss_ratio"] <= 1.02
        and result["largest_case_rss_delta_bytes_per_vertex"] <= 1.5
        and result["geometric_additional_peak_ratio"] <= 1.0
        and result["worst_additional_peak_ratio"] <= 1.002
        and result["geometric_retained_ratio"] <= 1.001
        and result["worst_retained_ratio"] <= 1.001
        and result["max_post_drop_delta_bytes"] == 0
    )
'''
new_limits = '''    result["acceptance_limits"] = {
        "geometric_time_ratio_max": 0.98,
        "worst_time_ratio_max": 1.01,
        "maximum_rss_delta_kib_max": 6144,
        "path_4m_rss_ratio_max": 1.01,
        "path_8m_rss_ratio_max": 1.01,
        "path_8m_rss_delta_bytes_per_vertex_max": 0.75,
        "geometric_additional_peak_ratio_max": 1.0,
        "worst_additional_peak_ratio_max": 1.002,
        "geometric_retained_ratio_max": 1.001,
        "worst_retained_ratio_max": 1.001,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        prior.get("validation") == "success"
        and result["geometric_time_ratio"] <= 0.98
        and result["worst_time_ratio"] <= 1.01
        and result["maximum_rss_delta_kib"] <= 6144
        and result["path_4m_rss_ratio"] <= 1.01
        and result["path_8m_rss_ratio"] <= 1.01
        and result["path_8m_rss_delta_bytes_per_vertex"] <= 0.75
        and result["geometric_additional_peak_ratio"] <= 1.0
        and result["worst_additional_peak_ratio"] <= 1.002
        and result["geometric_retained_ratio"] <= 1.001
        and result["worst_retained_ratio"] <= 1.001
        and result["max_post_drop_delta_bytes"] == 0
    )
'''
if text.count(old_limits) != 1:
    raise SystemExit("path-scaling acceptance block changed unexpectedly")
text = text.replace(old_limits, new_limits, 1)

text = text.replace(
    "the RSS difference is a bounded non-scaling allocator/mapping step, while exact memory is unchanged and hierarchy speed improves materially",
    "large path cases confirm that the RSS step does not scale, exact memory is unchanged, and hierarchy speed improves materially",
)
text = text.replace(
    "the RSS difference scales, timing did not reproduce, or exact-memory limits were not met",
    "large-case RSS, timing, or exact-memory confirmation limits were not all met",
)
text = text.replace(
    "perf: retain bounded ancestor prefixes after RSS scaling",
    "perf: retain bounded ancestor prefixes after large-path confirmation",
)
text = text.replace(
    "perf: record bounded-prefix path RSS scaling",
    "perf: record bounded-prefix large-path confirmation",
)

# Replace the document writer wholesale to avoid stale small-case terminology.
start = text.index("def update_documents(result):")
end = text.index("\n\nbaseline_source =", start)
writer = r'''def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    checkpoint = f'''### Bounded ancestor-prefix large-path confirmation — 2026-08-24

- The bounded-prefix candidate was **{decision}** after repeated 2m, 4m, and 8m-vertex path runs.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric hierarchy time ratio: `{result.get("geometric_time_ratio", 1.0):.3f}x`.
- Maximum median-RSS delta: `{result.get("maximum_rss_delta_kib", 0):.0f}` KiB; 4m / 8m ratios `{result.get("path_4m_rss_ratio", 1.0):.3f}x` / `{result.get("path_8m_rss_ratio", 1.0):.3f}x`.
- Exact additional-peak / retained hierarchy ratios: `{result.get("geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/bounded-prefix-large-path-confirmation.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Bounded ancestor-prefix large-path confirmation — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Re-profile forest-split subphases if the bounded prefix is retained.\n"
        "2. Run the prepared branch-free diameter-front gate.\n"
        "3. Refresh cumulative retained optimization and memory guidance.\n"
        "4. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Bounded ancestor-prefix large-path confirmation

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Hierarchy timing ratio: `{result.get("geometric_time_ratio", 1.0):.3f}x`.
- 4m / 8m RSS ratios: `{result.get("path_4m_rss_ratio", 1.0):.3f}x` / `{result.get("path_8m_rss_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/bounded-prefix-large-path-confirmation.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Bounded ancestor-prefix large-path confirmation\n"
    if heading in status:
        begin = status.index(heading)
        finish = status.find("\n## ", begin + len(heading))
        if finish == -1:
            finish = len(status)
        status = status[:begin] + block + status[finish:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")
'''
text = text[:start] + writer + text[end:]

# Update defaults appended after exceptions.
text = text.replace(
    '    "rss_delta_range_kib",\n    "largest_case_rss_ratio",\n    "largest_case_rss_delta_bytes_per_vertex",',
    '    "path_4m_rss_ratio",\n    "path_8m_rss_ratio",\n    "path_8m_rss_delta_bytes_per_vertex",',
)

required = (
    "bounded_prefix_large_path_confirmation.py",
    "bounded-prefix-large-path-confirmation.yml",
    "bounded-prefix-large-path-confirmation.json",
    '"path-8m"',
    '"path_8m_rss_ratio"',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"large-path confirmation missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
