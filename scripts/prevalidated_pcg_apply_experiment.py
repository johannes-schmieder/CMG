#!/usr/bin/env python3
"""Patch, evaluate, and record the private PCG apply experiment."""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_source() -> None:
    preconditioner = Path("src/preconditioner.rs")
    text = preconditioner.read_text()
    anchor = "    /// Apply with explicit compatibility-validation tolerances.\n"
    method = '''    /// Apply a compatible right-hand side after the caller has already
    /// validated dimensions, workspace layout, and solver options.
    ///
    /// This crate-private path is used only by PCG, which establishes these
    /// invariants once at entry and retains ownership of the workspace for the
    /// complete solve. Public methods continue to validate every call.
    pub(crate) fn apply_compatible_prevalidated_into(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
    ) -> Result<(), CmgError> {
        let dimension = self.hierarchy.levels()[0].graph().vertex_count();
        debug_assert_eq!(rhs.len(), dimension);
        debug_assert_eq!(output.len(), dimension);
        debug_assert!(
            workspace
                .validate(
                    &self.hierarchy,
                    self.direct_terminal.as_ref(),
                    &self.finest_components,
                    &self.coarse_centering,
                )
                .is_ok(),
            "PCG supplied an incompatible CMG workspace"
        );
        self.apply_level(0, rhs, output, workspace, 1)
    }

'''
    preconditioner.write_text(
        replace_once(text, anchor, method + anchor, "preconditioner insertion")
    )

    pcg = Path("src/pcg.rs")
    text = pcg.read_text()
    old = "preconditioner.apply_compatible_into("
    count = text.count(old)
    if count != 2:
        raise SystemExit(f"expected two PCG compatible-apply calls, found {count}")
    pcg.write_text(text.replace(old, "preconditioner.apply_compatible_prevalidated_into("))


def load_result(
    root: Path, version: str, case_name: str, vertices: int, round_index: int
) -> dict:
    path = root / f"{version}-{case_name}-{vertices}-{round_index}.json"
    return json.loads(path.read_text())


def geometric_mean(values: list[float]) -> float:
    return math.exp(sum(math.log(value) for value in values) / len(values))


def evaluate() -> bool:
    root = Path(os.environ["RUNNER_TEMP"]) / "prevalidated-apply-results"
    configurations = [
        ("path", 5_000),
        ("worker-firm", 5_000),
        ("path", 20_000),
        ("worker-firm", 20_000),
    ]
    records: dict[str, dict] = {}
    reasons: list[str] = []
    solve_ratios: dict[str, float] = {}
    apply_ratios: dict[str, float] = {}

    for case_name, vertices in configurations:
        key = f"{case_name}-{vertices}"
        baseline_rounds: list[dict] = []
        candidate_rounds: list[dict] = []
        for round_index in range(5):
            baseline = load_result(root, "baseline", case_name, vertices, round_index)
            candidate = load_result(root, "candidate", case_name, vertices, round_index)
            baseline_rounds.append(baseline)
            candidate_rounds.append(candidate)
            for field in (
                "canonical_edges",
                "terminal_reason",
                "level_vertices",
                "level_matrix_nonzeros",
                "iterations",
            ):
                if baseline.get(field) != candidate.get(field):
                    reasons.append(f"{key} round {round_index}: changed {field}")
            before_errors = baseline["backward_errors"]
            after_errors = candidate["backward_errors"]
            if len(before_errors) != len(after_errors):
                reasons.append(f"{key} round {round_index}: changed error vector length")
            else:
                for index, (before, after) in enumerate(
                    zip(before_errors, after_errors, strict=True)
                ):
                    tolerance = max(
                        5.0e-12, 1.0e-6 * max(abs(float(before)), 1.0e-30)
                    )
                    if not math.isfinite(float(after)) or abs(after - before) > tolerance:
                        reasons.append(
                            f"{key} round {round_index}: backward error {index} changed"
                        )

        baseline_solve = statistics.median(
            record["solve_per_rhs_median_ns"] for record in baseline_rounds
        )
        candidate_solve = statistics.median(
            record["solve_per_rhs_median_ns"] for record in candidate_rounds
        )
        baseline_apply = statistics.median(
            record["preconditioner_apply_median_ns"] for record in baseline_rounds
        )
        candidate_apply = statistics.median(
            record["preconditioner_apply_median_ns"] for record in candidate_rounds
        )
        solve_ratios[key] = candidate_solve / baseline_solve
        apply_ratios[key] = candidate_apply / baseline_apply
        records[key] = {
            "baseline_solve_median_ns": baseline_solve,
            "candidate_solve_median_ns": candidate_solve,
            "baseline_public_apply_median_ns": baseline_apply,
            "candidate_public_apply_median_ns": candidate_apply,
            "baseline_rounds": baseline_rounds,
            "candidate_rounds": candidate_rounds,
        }

    solve_geomean = geometric_mean(list(solve_ratios.values()))
    large_geomean = geometric_mean(
        [solve_ratios["path-20000"], solve_ratios["worker-firm-20000"]]
    )
    if max(solve_ratios.values()) > 1.03:
        reasons.append("one solve configuration regressed by more than 3%")
    if solve_geomean > 1.0:
        reasons.append("overall solve geometric mean did not improve")
    if large_geomean > 1.01:
        reasons.append("large-case solve geometric mean regressed by more than 1%")
    if min(solve_ratios.values()) > 0.99:
        reasons.append("no configuration demonstrated at least a 1% improvement")

    accepted = not reasons
    record = {
        "schema": 1,
        "baseline_sha": os.environ["BASELINE_SHA"],
        "accepted": accepted,
        "reasons": reasons,
        "solve_ratios": solve_ratios,
        "solve_geometric_mean_ratio": solve_geomean,
        "large_solve_geometric_mean_ratio": large_geomean,
        "public_apply_ratios": apply_ratios,
        "records": records,
    }
    output = Path(".ci/performance/prevalidated-pcg-apply-latest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + "\n")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a") as stream:
            stream.write(f"accepted={str(accepted).lower()}\n")
    print(json.dumps({"accepted": accepted, "solve_ratios": solve_ratios}, indent=2))
    return accepted


def update_plan() -> None:
    accepted = os.environ["ACCEPTED"] == "true"
    record = json.loads(
        Path(".ci/performance/prevalidated-pcg-apply-latest.json").read_text()
    )
    plan = Path("PERFORMANCE_PLAN.md")
    text = plan.read_text()

    retained_anchor = (
        "- `.ci/performance/compact-centering-metadata-latest.json`: "
        "accepted coarse-centering metadata experiment.\n"
    )
    retained_entry = (
        "- `.ci/performance/prevalidated-pcg-apply-latest.json`: "
        f"{'accepted' if accepted else 'rejected'} prevalidated PCG apply experiment.\n"
    )
    if retained_entry not in text:
        text = replace_once(
            text,
            retained_anchor,
            retained_anchor + retained_entry,
            "retained-record anchor",
        )

    old_next = (
        "1. Measure a crate-private prevalidated apply path that skips repeated "
        "workspace/options checks inside PCG.\n"
        "2. Continue large setup profiling, especially coarse contraction allocation "
        "and sorting.\n"
        "3. Obtain 8–32-thread and high-memory evidence when a suitable runner is "
        "available.\n"
        "4. Remove remaining obsolete one-shot workflows and staging scripts after "
        "the active checkpoint is secure.\n"
    )
    new_next = (
        "1. Continue large setup profiling, especially coarse contraction allocation "
        "and sorting.\n"
        "2. Qualify the direct-terminal build benchmark and evaluate eliminating one "
        "dense factor-construction buffer.\n"
        "3. Obtain 8–32-thread and high-memory evidence when a suitable runner is "
        "available.\n"
        "4. Remove remaining obsolete one-shot workflows and staging scripts after "
        "the active checkpoint is secure.\n"
    )

    if accepted:
        implemented_anchor = (
            "- Recursive coarse residuals use deterministic component centering instead "
            "of repeating public-boundary compatibility validation, stable-representative "
            "correction, and projection-norm work.\n"
        )
        implemented_entry = (
            "- PCG validates its private CMG workspace and dimensions once at solve entry, "
            "then uses a crate-private prevalidated stationary apply path. Public apply "
            "methods retain their complete per-call checks.\n"
        )
        if implemented_entry not in text:
            text = replace_once(
                text,
                implemented_anchor,
                implemented_anchor + implemented_entry,
                "implemented-work anchor",
            )

        result_anchor = (
            "The compact coarse-centering metadata experiment was retained after "
            "cross-checking hierarchy structure, iterations, backward errors, and the "
            "pinned-C stationary cycle. Candidate metadata was `0.623x` and `0.794x` of "
            "the old label-only lower bound on path and worker–firm cases. The same-run "
            "geometric-mean solve ratio was `0.991x` and the C-cycle ratio was `0.934x`.\n"
        )
        result_entry = (
            "\nThe private prevalidated PCG apply experiment preserved hierarchy "
            "structure, iteration counts, and original-system backward errors. Across "
            "alternating 5,000- and 20,000-vertex path and worker–firm runs, the solve "
            f"geometric-mean ratio was `{record['solve_geometric_mean_ratio']:.3f}x`; "
            f"the 20,000-vertex ratio was `{record['large_solve_geometric_mean_ratio']:.3f}x`.\n"
        )
        if result_entry not in text:
            text = replace_once(
                text, result_anchor, result_anchor + result_entry, "result anchor"
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
        new_hot = (
            "1. Single-RHS production PCG remains mostly serial even when the optional "
            "parallel feature is enabled.\n"
            "2. Coarse contraction still allocates endpoint triples and sorts at every "
            "level.\n"
            "3. Terminal setup materializes dense temporary matrices before retaining "
            "compressed factors.\n"
            "4. Aggregation maps remain native-width `usize`; compact storage could "
            "reduce bandwidth, but must be evaluated without duplicating labels.\n"
            "5. Hosted hardware has qualified only 1–4 threads.\n"
        )
        text = replace_once(text, old_hot, new_hot, "hot-spot block")

        checkpoint_anchor = (
            "| 2026-08-22 | compact centering metadata | Full component metadata "
            "retained only at the finest level; memory, solve, and C-cycle gates passed |\n"
        )
        checkpoint_entry = (
            "| 2026-08-22 | prevalidated PCG apply | Repeated public-boundary checks "
            "removed from the private PCG loop; numerical and timing gates passed |\n"
        )
        if checkpoint_entry not in text:
            text = replace_once(
                text,
                checkpoint_anchor,
                checkpoint_anchor + checkpoint_entry,
                "checkpoint anchor",
            )
    else:
        rejected_anchor = (
            "- Duplicating native and compact aggregation labels is not accepted without "
            "an end-to-end memory and speed win.\n"
        )
        rejected_entry = (
            "- Skipping public-compatible apply checks inside PCG was benchmarked and "
            "rejected because it did not produce a stable end-to-end solve improvement; "
            "the fully checked path remains.\n"
        )
        if rejected_entry not in text:
            text = replace_once(
                text,
                rejected_anchor,
                rejected_anchor + rejected_entry,
                "rejected-experiment anchor",
            )

    if old_next in text:
        text = text.replace(old_next, new_next, 1)
    elif new_next not in text:
        raise SystemExit("next-action block changed")
    plan.write_text(text)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: prevalidated_pcg_apply_experiment.py patch|evaluate|plan")
    mode = sys.argv[1]
    if mode == "patch":
        patch_source()
    elif mode == "evaluate":
        evaluate()
    elif mode == "plan":
        update_plan()
    else:
        raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    main()
