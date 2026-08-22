import json
import math
import os
import subprocess
from pathlib import Path


root = Path(os.environ["CMG_EXPERIMENT_RESULTS"])
cycle_ratios: dict[str, float] = {}
solve_ratios: dict[str, float] = {}
records: dict[str, object] = {}
valid = True

for case in ("path", "worker-firm"):
    cycle_baseline = json.loads((root / f"cycle-baseline-{case}.json").read_text())
    cycle_candidate = json.loads((root / f"cycle-candidate-{case}.json").read_text())
    solve_baseline = json.loads((root / f"solve-baseline-{case}.json").read_text())
    solve_candidate = json.loads((root / f"solve-candidate-{case}.json").read_text())

    cycle_ratios[case] = (
        cycle_candidate["cycle"]["rust_median_ns"]
        / cycle_baseline["cycle"]["rust_median_ns"]
    )
    solve_ratios[case] = (
        solve_candidate["solve_per_rhs_median_ns"]
        / solve_baseline["solve_per_rhs_median_ns"]
    )

    valid &= cycle_candidate["cycle"]["quotient_max_scaled_error"] <= 5.0e-10
    valid &= solve_candidate["iterations"] == solve_baseline["iterations"]
    valid &= all(
        math.isfinite(value) and value < 1.0e-6
        for value in solve_candidate["backward_errors"]
    )

    records[case] = {
        "cycle_baseline": cycle_baseline,
        "cycle_candidate": cycle_candidate,
        "solve_baseline": solve_baseline,
        "solve_candidate": solve_candidate,
    }


def geometric_mean(values: dict[str, float]) -> float:
    return math.exp(sum(math.log(value) for value in values.values()) / len(values))


cycle_geomean = geometric_mean(cycle_ratios)
solve_geomean = geometric_mean(solve_ratios)
accepted = (
    valid
    and max(cycle_ratios.values()) <= 1.03
    and cycle_geomean <= 0.98
    and max(solve_ratios.values()) <= 1.03
    and solve_geomean <= 0.995
    and min(solve_ratios.values()) <= 0.985
)

tested_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
record = {
    "schema": 2,
    "tested_sha": tested_sha,
    "completed": True,
    "accepted": accepted,
    "outcome": "accepted" if accepted else "rejected",
    "valid": valid,
    "cycle_ratios": cycle_ratios,
    "cycle_geometric_mean_ratio": cycle_geomean,
    "solve_ratios": solve_ratios,
    "solve_geometric_mean_ratio": solve_geomean,
    "records": records,
}
Path(".ci/performance/recursive-centering-latest.json").write_text(
    json.dumps(record, indent=2) + "\n"
)

if accepted:
    plan = Path("PERFORMANCE_PLAN.md")
    text = plan.read_text()

    retained_anchor = (
        "- `.ci/performance/inplace-level-output-latest.json`: accepted "
        "caller-output hierarchy workspace experiment.\n"
    )
    retained_entry = (
        "- `.ci/performance/recursive-centering-latest.json`: accepted "
        "internal coarse-residual centering experiment.\n"
    )
    if retained_entry not in text:
        if text.count(retained_anchor) != 1:
            raise SystemExit("retained-record anchor changed")
        text = text.replace(retained_anchor, retained_anchor + retained_entry, 1)

    metadata_anchor = (
        "- PCG projects the submitted RHS once, maintains residuals in the quotient "
        "space, removes only accumulated component-nullspace roundoff, and reuses "
        "the compatible stationary core on later applications.\n"
    )
    metadata_entry = (
        "- Recursive coarse residuals use deterministic component centering rather "
        "than repeating public-boundary compatibility validation and exact-correction passes.\n"
    )
    if metadata_entry not in text:
        if text.count(metadata_anchor) != 1:
            raise SystemExit("centering implementation anchor changed")
        text = text.replace(metadata_anchor, metadata_anchor + metadata_entry, 1)

    timing_anchor = (
        "This is a substantial reduction from the preceding same-run Rust/C ratios "
        "of `1.84x` and `2.36x`. Remaining differences are now concentrated inside "
        "recursive level application rather than fine-level public compatibility handling.\n"
    )
    timing_paragraph = (
        "\nReplacing recursive full compatibility projection with centering produced "
        f"same-run cycle ratios of path `{cycle_ratios['path']:.3f}x` and "
        f"worker–firm `{cycle_ratios['worker-firm']:.3f}x`; full solve ratios were "
        f"`{solve_ratios['path']:.3f}x` and `{solve_ratios['worker-firm']:.3f}x`, "
        "with unchanged iteration counts and retained pinned-C quotient-space agreement.\n"
    )
    if timing_paragraph not in text:
        if text.count(timing_anchor) != 1:
            raise SystemExit("centering timing anchor changed")
        text = text.replace(timing_anchor, timing_anchor + timing_paragraph, 1)

    old_hot = (
        "1. Every recursive coarse RHS still uses full public-quality compatibility "
        "projection: compatibility and scale accumulation, mean removal, representative "
        "search, two correction passes, and projection-norm calculation. Internally "
        "generated residuals are compatible in exact arithmetic; deterministic component "
        "centering may be sufficient and substantially cheaper, but it must pass symmetry, "
        "positivity, adversarial PCG, and C-differential gates.\n"
    )
    new_hot = (
        "1. Coarse component metadata and component scratch remain allocated at every "
        "hierarchy level even though recursive application now needs only centering; a "
        "follow-up memory cleanup must preserve public validation and C parity.\n"
    )
    if old_hot not in text:
        raise SystemExit("current-hot-spot anchor changed")
    text = text.replace(old_hot, new_hot, 1)

    log_anchor = (
        "| 2026-08-22 | in-place hierarchy output | Per-level solution scratch "
        "removed; C parity, iterations, residuals, timing, and memory gates passed |\n"
    )
    log_entry = (
        "| 2026-08-22 | recursive coarse centering | Full recursive compatibility "
        "projection replaced by deterministic centering; C parity and solve gates passed |\n"
    )
    if log_entry not in text:
        if text.count(log_anchor) != 1:
            raise SystemExit("checkpoint-log anchor changed")
        text = text.replace(log_anchor, log_anchor + log_entry, 1)

    old_next = '''1. Qualify the in-place hierarchy-output checkpoint on Ubuntu, macOS, and Windows and refresh matched serial/parallel records.
2. Measure deterministic component centering in place of full recursive coarse-RHS compatibility projection, retaining it only if full-cycle parity, PCG convergence, symmetry, positivity, and real solve time improve.
3. If recursive centering is retained, remove now-unneeded coarse component metadata and scratch, then measure a crate-private prevalidated apply path that skips repeated workspace/options checks inside PCG.
4. Continue large setup profiling and obtain 8–32-thread evidence when a suitable runner is available.
5. Remove obsolete one-shot staging workflows and scripts after the active qualification checkpoint is secure.'''
    new_next = '''1. Qualify the in-place-output plus recursive-centering checkpoint on Ubuntu, macOS, and Windows and refresh matched serial/parallel records.
2. Remove now-unneeded coarse component metadata and scratch while retaining finest-level public validation, then benchmark memory and solve time.
3. Measure a crate-private prevalidated apply path that skips repeated workspace/options checks inside PCG only after the metadata cleanup is qualified.
4. Continue large setup profiling and obtain 8–32-thread evidence when a suitable runner is available.
5. Remove obsolete failed-experiment logs and one-shot workflows after the active qualification checkpoint is secure.'''
    if old_next not in text:
        raise SystemExit("current-next-action anchor changed")
    plan.write_text(text.replace(old_next, new_next, 1))

with open(os.environ["GITHUB_OUTPUT"], "a") as output:
    output.write(f"accepted={str(accepted).lower()}\n")
