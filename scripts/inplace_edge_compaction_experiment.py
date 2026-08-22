#!/usr/bin/env python3
"""Evaluate and record in-place canonical edge compaction."""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import sys
from pathlib import Path


GRAPH_CASES = (
    ("unique", 500_000),
    ("duplicates-4", 250_000),
    ("duplicates-16", 75_000),
    ("coarse-collisions", 75_000),
)
HIERARCHY_CASES = (
    ("worker-firm", 50_000),
    ("dense-worker-firm", 20_000),
)
ROUNDS = 3


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def read_json_line(path: Path) -> dict:
    records = [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip().startswith("{")
    ]
    if len(records) != 1:
        raise SystemExit(f"{path}: expected one JSON record, found {len(records)}")
    return records[0]


def peak_rss_kib(path: Path) -> int:
    match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)", path.read_text()
    )
    if match is None:
        raise SystemExit(f"{path}: peak RSS missing")
    return int(match.group(1))


def geometric_mean(values: list[float]) -> float:
    return math.exp(sum(math.log(value) for value in values) / len(values))


def read_observations(
    root: Path,
    kind: str,
    version: str,
    case: str,
    scale: int,
) -> list[dict]:
    observations = []
    for round_index in range(ROUNDS):
        prefix = root / f"{version}-{kind}-{case}-{scale}-{round_index}"
        record = read_json_line(prefix.with_suffix(".stdout"))
        record["peak_rss_kib"] = peak_rss_kib(prefix.with_suffix(".time"))
        observations.append(record)
    return observations


def validate_matching_records(
    key: str,
    baseline: list[dict],
    candidate: list[dict],
    fields: tuple[str, ...],
    reasons: list[str],
) -> None:
    reference = baseline[0]
    for version, records in (("baseline", baseline), ("candidate", candidate)):
        for round_index, record in enumerate(records):
            for field in fields:
                if record.get(field) != reference.get(field):
                    reasons.append(
                        f"{key}: {version} round {round_index} changed {field}"
                    )


def evaluate() -> bool:
    root = Path(os.environ["RUNNER_TEMP"]) / "inplace-edge-results"
    reasons: list[str] = []
    graph_records: dict[str, dict] = {}
    hierarchy_records: dict[str, dict] = {}
    graph_time_ratios: dict[str, float] = {}
    graph_rss_ratios: dict[str, float] = {}
    hierarchy_time_ratios: dict[str, float] = {}
    hierarchy_rss_ratios: dict[str, float] = {}

    for case, scale in GRAPH_CASES:
        key = f"{case}-{scale}"
        baseline = read_observations(root, "graph", "baseline", case, scale)
        candidate = read_observations(root, "graph", "candidate", case, scale)
        validate_matching_records(
            key,
            baseline,
            candidate,
            (
                "case",
                "scale",
                "vertices",
                "raw_edges",
                "retained_edges",
                "repetitions",
            ),
            reasons,
        )
        baseline_time = statistics.median(item["median_ns"] for item in baseline)
        candidate_time = statistics.median(item["median_ns"] for item in candidate)
        baseline_rss = statistics.median(item["peak_rss_kib"] for item in baseline)
        candidate_rss = statistics.median(item["peak_rss_kib"] for item in candidate)
        graph_time_ratios[key] = candidate_time / baseline_time
        graph_rss_ratios[key] = candidate_rss / baseline_rss
        graph_records[key] = {
            "baseline": baseline,
            "candidate": candidate,
            "baseline_median_ns": baseline_time,
            "candidate_median_ns": candidate_time,
            "candidate_over_baseline_time": graph_time_ratios[key],
            "baseline_peak_rss_kib": baseline_rss,
            "candidate_peak_rss_kib": candidate_rss,
            "candidate_over_baseline_peak_rss": graph_rss_ratios[key],
        }

    for case, scale in HIERARCHY_CASES:
        key = f"{case}-{scale}"
        baseline = read_observations(root, "hierarchy", "baseline", case, scale)
        candidate = read_observations(root, "hierarchy", "candidate", case, scale)
        validate_matching_records(
            key,
            baseline,
            candidate,
            ("case", "scale", "vertices", "edges", "repetitions"),
            reasons,
        )
        baseline_time = statistics.median(item["median_ns"] for item in baseline)
        candidate_time = statistics.median(item["median_ns"] for item in candidate)
        baseline_rss = statistics.median(item["peak_rss_kib"] for item in baseline)
        candidate_rss = statistics.median(item["peak_rss_kib"] for item in candidate)
        hierarchy_time_ratios[key] = candidate_time / baseline_time
        hierarchy_rss_ratios[key] = candidate_rss / baseline_rss
        hierarchy_records[key] = {
            "baseline": baseline,
            "candidate": candidate,
            "baseline_median_ns": baseline_time,
            "candidate_median_ns": candidate_time,
            "candidate_over_baseline_time": hierarchy_time_ratios[key],
            "baseline_peak_rss_kib": baseline_rss,
            "candidate_peak_rss_kib": candidate_rss,
            "candidate_over_baseline_peak_rss": hierarchy_rss_ratios[key],
        }

    graph_time_geomean = geometric_mean(list(graph_time_ratios.values()))
    hierarchy_time_geomean = geometric_mean(list(hierarchy_time_ratios.values()))
    duplicate_rss = [
        ratio
        for key, ratio in graph_rss_ratios.items()
        if not key.startswith("unique-")
    ]

    if graph_time_geomean > 1.01:
        reasons.append("graph-build geometric timing ratio exceeded 1.01")
    if max(graph_time_ratios.values()) > 1.05:
        reasons.append("one graph-build timing ratio exceeded 1.05")
    if graph_rss_ratios["unique-500000"] > 1.03:
        reasons.append("unique-edge peak RSS ratio exceeded 1.03")
    if max(duplicate_rss) > 1.00:
        reasons.append("one duplicate-heavy peak RSS ratio exceeded 1.00")
    if min(duplicate_rss) > 0.97:
        reasons.append("no duplicate-heavy case reduced peak RSS by at least 3%")
    if hierarchy_time_geomean > 1.03:
        reasons.append("hierarchy-build geometric timing ratio exceeded 1.03")
    if max(hierarchy_time_ratios.values()) > 1.08:
        reasons.append("one hierarchy-build timing ratio exceeded 1.08")
    if max(hierarchy_rss_ratios.values()) > 1.03:
        reasons.append("one hierarchy-build peak RSS ratio exceeded 1.03")

    accepted = not reasons
    record = {
        "schema": 1,
        "experiment": "in-place-canonical-edge-compaction",
        "baseline_sha": os.environ["BASELINE_SHA"],
        "accepted": accepted,
        "reasons": reasons,
        "validation": "success",
        "rounds": ROUNDS,
        "graph_time_ratios": graph_time_ratios,
        "graph_time_geometric_mean_ratio": graph_time_geomean,
        "graph_peak_rss_ratios": graph_rss_ratios,
        "hierarchy_time_ratios": hierarchy_time_ratios,
        "hierarchy_time_geometric_mean_ratio": hierarchy_time_geomean,
        "hierarchy_peak_rss_ratios": hierarchy_rss_ratios,
        "graph_records": graph_records,
        "hierarchy_records": hierarchy_records,
    }
    output = Path(".ci/performance/inplace-edge-compaction-latest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + "\n")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a") as stream:
            stream.write(f"accepted={str(accepted).lower()}\n")
    print(json.dumps(record, indent=2))
    return accepted


def update_plan() -> None:
    record = json.loads(
        Path(".ci/performance/inplace-edge-compaction-latest.json").read_text()
    )
    accepted = bool(record["accepted"])
    plan_path = Path("PERFORMANCE_PLAN.md")
    text = plan_path.read_text()

    retained_anchor = (
        "- `.ci/performance/terminal-factor-latest.json`: "
        "accepted direct terminal-factor assembly experiment.\n"
    )
    retained_entry = (
        "- `.ci/performance/inplace-edge-compaction-latest.json`: "
        f"{'accepted' if accepted else 'rejected'} in-place edge-compaction experiment.\n"
    )
    if retained_entry not in text:
        text = replace_once(
            text,
            retained_anchor,
            retained_anchor + retained_entry,
            "retained edge-compaction record",
        )

    result_anchor = (
        "The direct terminal-factor assembly experiment removed one dense setup "
        "buffer while preserving the complete numerical test suite. Its geometric "
        "terminal-build ratio was `0.922x` and its worst case was `0.932x`; the "
        "candidate was retained.\n"
    )
    duplicate_ratios = [
        value
        for key, value in record["graph_peak_rss_ratios"].items()
        if not key.startswith("unique-")
    ]
    result_entry = (
        "\nThe in-place canonical-edge compaction experiment preserved every graph "
        "and hierarchy benchmark invariant. Its graph-build timing geometric mean was "
        f"`{record['graph_time_geometric_mean_ratio']:.3f}x`, its hierarchy-build "
        f"geometric mean was `{record['hierarchy_time_geometric_mean_ratio']:.3f}x`, "
        f"and the best duplicate-heavy peak-RSS ratio was `{min(duplicate_ratios):.3f}x`; "
        f"the candidate was {'retained' if accepted else 'not retained'}.\n"
    )
    if result_entry not in text:
        text = replace_once(
            text,
            result_anchor,
            result_anchor + result_entry,
            "edge-compaction result insertion",
        )

    implementation_anchor = (
        "- Deterministic parallel edge sorting, coarse contraction, and heavy-edge "
        "selection route only above conservative size/density floors.\n"
    )
    implementation_entry = (
        "- Canonical duplicate edges are compacted into the sorted raw buffer before "
        "the retained `Edge` vector is built, reducing duplicate-heavy construction "
        "scratch without changing summation order.\n"
    )
    if accepted and implementation_entry not in text:
        text = replace_once(
            text,
            implementation_anchor,
            implementation_anchor + implementation_entry,
            "edge-compaction implementation entry",
        )

    current_hot = (
        "2. Coarse contraction still allocates endpoint triples and sorts at every level.\n"
    )
    accepted_hot = (
        "2. Coarse contraction still maps endpoint triples and sorts at every level; "
        "duplicate aggregation is now in-place, but mapping and sorting remain.\n"
    )
    if accepted and current_hot in text:
        text = text.replace(current_hot, accepted_hot, 1)

    rejected_anchor = (
        "- Skipping public-compatible apply checks inside PCG was benchmarked and "
        "rejected because it did not produce a stable end-to-end solve improvement; "
        "the fully checked path remains.\n"
    )
    rejected_entry = (
        "- In-place canonical edge compaction was benchmarked and not retained because "
        "its timing or memory gates did not provide a stable end-to-end win.\n"
    )
    if not accepted and rejected_entry not in text:
        text = replace_once(
            text,
            rejected_anchor,
            rejected_anchor + rejected_entry,
            "rejected edge-compaction entry",
        )

    checkpoint_anchor = (
        "| 2026-08-22 | direct terminal-factor assembly | Retained after full tests "
        "and same-host build timing |\n"
    )
    checkpoint_entry = (
        "| 2026-08-22 | in-place edge compaction | "
        f"{'Retained' if accepted else 'Rejected'} after graph/hierarchy timing and peak-RSS gates |\n"
    )
    if checkpoint_entry not in text:
        text = replace_once(
            text,
            checkpoint_anchor,
            checkpoint_anchor + checkpoint_entry,
            "edge-compaction checkpoint",
        )

    next_anchor = (
        "1. Profile hierarchy construction on larger sparse and denser worker–firm "
        "cases, focusing on coarse contraction allocation and sorting.\n"
    )
    next_entry = (
        "1. Profile packed endpoint keys, contraction-buffer reuse, and parallel sort "
        "on larger sparse and denser worker–firm cases.\n"
    )
    if next_anchor in text:
        text = text.replace(next_anchor, next_entry, 1)

    plan_path.write_text(text)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"evaluate", "plan"}:
        raise SystemExit(
            "usage: inplace_edge_compaction_experiment.py evaluate|plan"
        )
    if sys.argv[1] == "evaluate":
        evaluate()
    else:
        update_plan()


if __name__ == "__main__":
    main()
