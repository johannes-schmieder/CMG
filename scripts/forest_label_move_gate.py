import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
FOREST_PATH = Path("src/forest.rs")
HIERARCHY_PATH = Path("src/hierarchy.rs")
FOREST_ORIGINAL = FOREST_PATH.read_text()
HIERARCHY_ORIGINAL = HIERARCHY_PATH.read_text()
BASELINE_SHA = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], text=True
).strip()


def run(command, *, env=None, timeout=5400, check=True):
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end="")
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
        )
    return completed


def build(target):
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target)
    run(
        [
            "cargo",
            "build",
            "--release",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--bin",
            "hierarchy-build",
        ],
        env=env,
    )
    return target / "release" / "hierarchy-build"


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-forest-label-{tag}.time")
    completed = run(
        [
            "/usr/bin/time",
            "-v",
            "-o",
            str(time_path),
            str(binary),
            *[str(value) for value in arguments],
        ]
    )
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected benchmark output: {payloads}")
    rss_match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)",
        time_path.read_text(),
    )
    if rss_match is None:
        raise RuntimeError("peak RSS missing")
    return {
        "median_ns": payloads[0]["median_ns"],
        "peak_rss_kib": int(rss_match.group(1)),
        "metadata": payloads[0],
    }


def compare_case(baseline, candidate, arguments, name):
    observations = {"baseline": [], "candidate": []}
    schedule = (
        ("baseline", baseline),
        ("candidate", candidate),
        ("candidate", candidate),
        ("baseline", baseline),
    )
    for index, (label, binary) in enumerate(schedule):
        observations[label].append(
            sample(binary, arguments, f"{name}-{label}-{index}")
        )
    baseline_ns = statistics.median(
        item["median_ns"] for item in observations["baseline"]
    )
    candidate_ns = statistics.median(
        item["median_ns"] for item in observations["candidate"]
    )
    baseline_rss = max(
        item["peak_rss_kib"] for item in observations["baseline"]
    )
    candidate_rss = max(
        item["peak_rss_kib"] for item in observations["candidate"]
    )
    return {
        "arguments": arguments,
        "baseline_median_ns": baseline_ns,
        "candidate_median_ns": candidate_ns,
        "candidate_over_baseline_time": candidate_ns / baseline_ns,
        "baseline_peak_rss_kib": baseline_rss,
        "candidate_peak_rss_kib": candidate_rss,
        "candidate_over_baseline_peak_rss": candidate_rss / baseline_rss,
        "metadata": observations["candidate"][0]["metadata"],
    }


def apply_candidate():
    forest_anchor = (
        "    pub fn aggregate_count(&self) -> usize {\n"
        "        self.sizes.len()\n"
        "    }\n"
    )
    forest_replacement = forest_anchor + (
        "\n"
        "    /// Consume the diagnostics and transfer aggregate labels without cloning.\n"
        "    pub(crate) fn into_labels_and_count(self) -> (Vec<usize>, usize) {\n"
        "        let aggregate_count = self.sizes.len();\n"
        "        (self.labels, aggregate_count)\n"
        "    }\n"
    )
    if FOREST_ORIGINAL.count(forest_anchor) != 1:
        raise RuntimeError("ForestGrouping aggregate_count anchor was not unique")
    FOREST_PATH.write_text(
        FOREST_ORIGINAL.replace(forest_anchor, forest_replacement, 1)
    )

    hierarchy_anchor = (
        "            let grouping = group(&current, options.low_effective_degree_threshold)?;\n"
        "            let aggregation =\n"
        "                Aggregation::new(grouping.labels().to_vec(), grouping.aggregate_count())?;\n"
        "            let coarse_count = aggregation.coarse_dimension();\n"
    )
    hierarchy_replacement = (
        "            let grouping = group(&current, options.low_effective_degree_threshold)?;\n"
        "            let (labels, aggregate_count) = grouping.into_labels_and_count();\n"
        "            let aggregation = Aggregation::new(labels, aggregate_count)?;\n"
        "            let coarse_count = aggregation.coarse_dimension();\n"
    )
    if HIERARCHY_ORIGINAL.count(hierarchy_anchor) != 1:
        raise RuntimeError("hierarchy grouping anchor was not unique")
    HIERARCHY_PATH.write_text(
        HIERARCHY_ORIGINAL.replace(hierarchy_anchor, hierarchy_replacement, 1)
    )


result = {
    "schema_version": 1,
    "experiment": "move-forest-labels-into-aggregation",
    "baseline_sha": BASELINE_SHA,
    "accepted": False,
    "validation": "not_run",
    "decision_reason": "",
    "cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-forest-label-baseline"))
    apply_candidate()
    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(
        [
            "cargo",
            "clippy",
            "--all-targets",
            "--all-features",
            "--",
            "-D",
            "warnings",
        ]
    )
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps"], env=doc_env)
    run(["cargo", "test", "--all-targets"])
    run(["cargo", "test", "--all-targets", "--release"])
    run(["cargo", "build", "--release"])
    candidate = build(Path("/tmp/cmg-forest-label-candidate"))
    result["validation"] = "success"

    specs = [
        ("path-250k", ["path", 250_000, 1]),
        ("worker-firm-750k", ["worker-firm", 250_000, 1]),
        ("dense-worker-firm-800k", ["dense-worker-firm", 50_000, 1]),
    ]
    time_ratios = []
    memory_ratios = []
    for name, arguments in specs:
        comparison = compare_case(baseline, candidate, arguments, name)
        result["cases"][name] = comparison
        time_ratios.append(comparison["candidate_over_baseline_time"])
        memory_ratios.append(comparison["candidate_over_baseline_peak_rss"])

    geometric_time = math.exp(
        sum(math.log(value) for value in time_ratios) / len(time_ratios)
    )
    geometric_memory = math.exp(
        sum(math.log(value) for value in memory_ratios) / len(memory_ratios)
    )
    result["geometric_candidate_over_baseline_time"] = geometric_time
    result["worst_candidate_over_baseline_time"] = max(time_ratios)
    result["geometric_candidate_over_baseline_peak_rss"] = geometric_memory
    result["best_candidate_over_baseline_peak_rss"] = min(memory_ratios)
    result["worst_candidate_over_baseline_peak_rss"] = max(memory_ratios)
    result["acceptance_limits"] = {
        "geometric_time_ratio_max": 1.05,
        "per_case_time_ratio_max": 1.15,
        "geometric_peak_rss_ratio_max": 1.01,
        "worst_peak_rss_ratio_max": 1.02,
        "required_signal": "best RSS <= 0.997 or geometric time <= 0.985",
    }
    signal = min(memory_ratios) <= 0.997 or geometric_time <= 0.985
    result["accepted"] = (
        geometric_time <= 1.05
        and max(time_ratios) <= 1.15
        and geometric_memory <= 1.01
        and max(memory_ratios) <= 1.02
        and signal
    )
    result["decision_reason"] = (
        "qualification passed and clone removal produced measurable benefit"
        if result["accepted"]
        else "qualification passed but timing/memory did not meet the retention gate"
    )
except Exception as error:
    result["decision_reason"] = f"experiment failed: {error}"
    result["error"] = repr(error)
    print(result["decision_reason"], flush=True)

if not result["accepted"]:
    FOREST_PATH.write_text(FOREST_ORIGINAL)
    HIERARCHY_PATH.write_text(HIERARCHY_ORIGINAL)
    run(["cargo", "fmt", "--all"], check=False)

record = Path(".ci/performance/forest-label-move-latest.json")
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

plan_path = Path("PERFORMANCE_PLAN.md")
plan = plan_path.read_text()
marker = "## Current next action\n"
if marker not in plan:
    raise RuntimeError("current-next-action heading missing")
status = "retained" if result["accepted"] else "not retained"
time_ratio = result.get("geometric_candidate_over_baseline_time")
memory_ratio = result.get("geometric_candidate_over_baseline_peak_rss")
evidence = ""
if time_ratio is not None and memory_ratio is not None:
    evidence = (
        f" Geometric setup ratio: {time_ratio:.3f}; geometric peak-RSS "
        f"ratio: {memory_ratio:.3f}."
    )
checkpoint = "\n".join(
    [
        "### Zero-copy forest-label checkpoint — 2026-08-22",
        "",
        f"- Moving `ForestGrouping.labels` directly into `Aggregation` was **{status}**.{evidence}",
        "- Full formatting, Clippy, rustdoc, debug/release tests, and release build status:",
        f"  `{result['validation']}`.",
        "- Machine-readable evidence:",
        "  `.ci/performance/forest-label-move-latest.json`.",
        "",
        "",
    ]
)
if "### Zero-copy forest-label checkpoint — 2026-08-22" not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
plan_path.write_text(plan)

Path(".github/workflows/forest-label-move.yml").unlink(missing_ok=True)
Path("scripts/forest_label_move_gate.py").unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass

run(["git", "config", "user.name", "github-actions[bot]"])
run(
    [
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ]
)
run(["git", "add", "-A"])
message = (
    "perf: move forest labels into aggregation"
    if result["accepted"]
    else "perf: record zero-copy forest-label experiment"
)
run(["git", "commit", "-m", message])
run(["git", "pull", "--rebase", "origin", "main"])
run(["git", "push", "origin", "HEAD:main"])
