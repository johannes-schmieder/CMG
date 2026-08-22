#!/usr/bin/env python3
"""Evaluate and record the direct terminal-factor assembly experiment."""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
from pathlib import Path


CASES = ("grid-24x24", "path-600", "worker-firm-300x300")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def read_round(path: Path) -> dict[str, dict]:
    parsed: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("{"):
            item = json.loads(line)
            parsed[item["case"]] = item
    if set(parsed) != set(CASES):
        raise SystemExit(
            f"{path}: cases {sorted(parsed)} differ from expected {sorted(CASES)}"
        )
    return parsed


def evaluate() -> bool:
    root = Path(os.environ["RUNNER_TEMP"]) / "terminal-factor-results"
    baseline_rounds = [read_round(root / f"baseline-{index}.jsonl") for index in range(3)]
    candidate_rounds = [read_round(root / f"candidate-{index}.jsonl") for index in range(3)]

    reasons: list[str] = []
    cases: dict[str, dict] = {}
    ratios: list[float] = []
    for case in CASES:
        first = baseline_rounds[0][case]
        for round_index, round_records in enumerate(
            baseline_rounds + candidate_rounds
        ):
            record = round_records[case]
            for field in ("case", "vertices", "edges", "repetitions"):
                if record[field] != first[field]:
                    reasons.append(f"{case}: round {round_index} changed {field}")

        baseline_values = [round_[case]["median_ns"] for round_ in baseline_rounds]
        candidate_values = [round_[case]["median_ns"] for round_ in candidate_rounds]
        baseline_median = statistics.median(baseline_values)
        candidate_median = statistics.median(candidate_values)
        ratio = candidate_median / baseline_median
        ratios.append(ratio)
        vertices = int(first["vertices"])
        cases[case] = {
            "vertices": vertices,
            "edges": int(first["edges"]),
            "baseline_round_medians_ns": baseline_values,
            "candidate_round_medians_ns": candidate_values,
            "baseline_median_ns": baseline_median,
            "candidate_median_ns": candidate_median,
            "candidate_over_baseline": ratio,
            "dense_buffer_bytes_saved": vertices * vertices * 8,
        }

    geometric_ratio = math.exp(sum(math.log(value) for value in ratios) / len(ratios))
    worst_ratio = max(ratios)
    if geometric_ratio > 1.02:
        reasons.append("geometric terminal-build ratio exceeded 1.02")
    if worst_ratio > 1.05:
        reasons.append("one terminal-build case exceeded 1.05")

    accepted = not reasons
    record = {
        "schema": 1,
        "experiment": "direct-terminal-factor-assembly",
        "baseline_sha": os.environ["BASELINE_SHA"],
        "accepted": accepted,
        "reasons": reasons,
        "validation": "success",
        "dense_buffer_bytes_saved_at_threshold_700": 700 * 700 * 8,
        "geometric_candidate_over_baseline": geometric_ratio,
        "worst_candidate_over_baseline": worst_ratio,
        "acceptance_limits": {
            "geometric_ratio_max": 1.02,
            "per_case_ratio_max": 1.05,
        },
        "cases": cases,
    }
    output = Path(".ci/performance/terminal-factor-latest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + "\n")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a") as stream:
            stream.write(f"accepted={str(accepted).lower()}\n")
    print(json.dumps(record, indent=2))
    return accepted


def update_plan() -> None:
    record = json.loads(Path(".ci/performance/terminal-factor-latest.json").read_text())
    accepted = bool(record["accepted"])
    plan_path = Path("PERFORMANCE_PLAN.md")
    text = plan_path.read_text()

    retained_anchor = (
        "- `.ci/performance/prevalidated-pcg-apply-latest.json`: "
        "rejected prevalidated PCG apply experiment.\n"
    )
    retained_entry = (
        "- `.ci/performance/terminal-factor-latest.json`: "
        f"{'accepted' if accepted else 'rejected'} direct terminal-factor assembly experiment.\n"
    )
    if retained_entry not in text:
        text = replace_once(
            text,
            retained_anchor,
            retained_anchor + retained_entry,
            "retained terminal record",
        )

    compact_result = (
        "The compact coarse-centering metadata experiment was retained after "
        "cross-checking hierarchy structure, iterations, backward errors, and the "
        "pinned-C stationary cycle. Candidate metadata was `0.623x` and `0.794x` of "
        "the old label-only lower bound on path and worker–firm cases. The same-run "
        "geometric-mean solve ratio was `0.991x` and the C-cycle ratio was `0.934x`.\n"
    )
    rejection_result = (
        "\nThe private prevalidated-PCG apply experiment was not retained. It preserved "
        "hierarchy structure, iterations, and backward errors, but its solve-time "
        "geometric-mean ratio was `1.012x` and the 20,000-vertex ratio was `1.008x`.\n"
    )
    terminal_result = (
        "\nThe direct terminal-factor assembly experiment removed one dense setup "
        "buffer while preserving the complete numerical test suite. Its geometric "
        f"terminal-build ratio was `{record['geometric_candidate_over_baseline']:.3f}x` "
        f"and its worst case was `{record['worst_candidate_over_baseline']:.3f}x`; the "
        f"candidate was {'retained' if accepted else 'not retained'}.\n"
    )
    additions = ""
    if rejection_result not in text:
        additions += rejection_result
    if terminal_result not in text:
        additions += terminal_result
    if additions:
        text = replace_once(
            text,
            compact_result,
            compact_result + additions,
            "performance result insertion",
        )

    terminal_anchor = (
        "- `GroundedLdl`, `CmgWorkspace`, and `PcgWorkspace` report retained "
        "principal heap bytes.\n"
    )
    accepted_bullet = (
        "- Terminal LDL construction assembles the ordered grounded matrix directly "
        "from graph diagonals and active edges, eliminating one dense full-graph "
        "matrix and its permutation copy.\n"
    )
    if accepted and accepted_bullet not in text:
        text = replace_once(
            text,
            terminal_anchor,
            terminal_anchor + accepted_bullet,
            "accepted terminal implementation",
        )

    old_hot = (
        "1. The compatible public method still validates dimensions, workspace "
        "structure, and options on every PCG application. A crate-private prevalidated "
        "core may remove small repeated checks inside PCG.\n"
        "2. Single-RHS production PCG remains mostly serial even when the optional "
        "parallel feature is enabled.\n"
        "3. Coarse contraction still allocates endpoint triples and sorts at every "
        "level.\n"
        "4. Terminal setup materializes dense temporary matrices before retaining "
        "compressed factors.\n"
        "5. Aggregation maps remain native-width `usize`; compact storage could "
        "reduce bandwidth, but must be evaluated without duplicating labels.\n"
        "6. Hosted hardware has qualified only 1–4 threads.\n"
    )
    if accepted:
        new_hot = (
            "1. Single-RHS production PCG remains mostly serial even when the optional "
            "parallel feature is enabled.\n"
            "2. Coarse contraction still allocates endpoint triples and sorts at every "
            "level.\n"
            "3. Aggregation maps remain native-width `usize`; compact storage could "
            "reduce bandwidth, but must be evaluated without duplicating labels.\n"
            "4. Hosted hardware has qualified only 1–4 threads.\n"
        )
    else:
        new_hot = (
            "1. Single-RHS production PCG remains mostly serial even when the optional "
            "parallel feature is enabled.\n"
            "2. Coarse contraction still allocates endpoint triples and sorts at every "
            "level.\n"
            "3. Terminal setup still materializes two dense factor-construction buffers; "
            "the measured direct-assembly variant was not retained.\n"
            "4. Aggregation maps remain native-width `usize`; compact storage could "
            "reduce bandwidth, but must be evaluated without duplicating labels.\n"
            "5. Hosted hardware has qualified only 1–4 threads.\n"
        )
    if old_hot in text:
        text = text.replace(old_hot, new_hot, 1)
    elif new_hot not in text:
        raise SystemExit("current hot-spot block changed")

    rejected_anchor = (
        "- Skipping public-compatible apply checks inside PCG was benchmarked and "
        "rejected because it did not produce a stable end-to-end solve improvement; "
        "the fully checked path remains.\n"
    )
    rejected_terminal = (
        "- Direct terminal-factor assembly was benchmarked and not retained because "
        "its setup timing exceeded the conservative gate.\n"
    )
    if not accepted and rejected_terminal not in text:
        text = replace_once(
            text,
            rejected_anchor,
            rejected_anchor + rejected_terminal,
            "rejected terminal experiment",
        )

    checkpoint_anchor = (
        "| 2026-08-22 | compact centering metadata | Full component metadata "
        "retained only at the finest level; memory, solve, and C-cycle gates passed |\n"
    )
    prevalidated_checkpoint = (
        "| 2026-08-22 | prevalidated PCG apply | Rejected: numerical results matched, "
        "but end-to-end solve timing did not improve |\n"
    )
    terminal_checkpoint = (
        "| 2026-08-22 | direct terminal-factor assembly | "
        f"{'Retained' if accepted else 'Rejected'} after full tests and same-host build timing |\n"
    )
    checkpoint_additions = ""
    if prevalidated_checkpoint not in text:
        checkpoint_additions += prevalidated_checkpoint
    if terminal_checkpoint not in text:
        checkpoint_additions += terminal_checkpoint
    if checkpoint_additions:
        text = replace_once(
            text,
            checkpoint_anchor,
            checkpoint_anchor + checkpoint_additions,
            "checkpoint insertion",
        )

    old_next = (
        "1. Continue large setup profiling, especially coarse contraction allocation "
        "and sorting.\n"
        "2. Qualify the direct-terminal build benchmark and evaluate eliminating one "
        "dense factor-construction buffer.\n"
        "3. Obtain 8–32-thread and high-memory evidence when a suitable runner is "
        "available.\n"
        "4. Remove remaining obsolete one-shot workflows and staging scripts after "
        "the active checkpoint is secure.\n"
    )
    new_next = (
        "1. Profile hierarchy construction on larger sparse and denser worker–firm "
        "cases, focusing on coarse contraction allocation and sorting.\n"
        "2. Obtain 8–32-thread and high-memory evidence when a suitable runner is "
        "available.\n"
        "3. Remove remaining obsolete one-shot workflows and staging scripts after "
        "the active checkpoint is secure.\n"
    )
    if old_next in text:
        text = text.replace(old_next, new_next, 1)
    elif new_next not in text:
        raise SystemExit("current-next-action block changed")

    plan_path.write_text(text)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"evaluate", "plan"}:
        raise SystemExit("usage: terminal_factor_experiment.py evaluate|plan")
    if sys.argv[1] == "evaluate":
        evaluate()
    else:
        update_plan()


if __name__ == "__main__":
    main()
