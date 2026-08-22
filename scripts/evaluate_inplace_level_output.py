import json
import math
import os
import subprocess
from pathlib import Path


root = Path(os.environ["CMG_EXPERIMENT_RESULTS"])
cycle_ratios: dict[str, float] = {}
solve_ratios: dict[str, float] = {}
cmg_memory_ratios: dict[str, float] = {}
pcg_memory_ratios: dict[str, float] = {}
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
    cmg_memory_ratios[case] = (
        solve_candidate["cmg_workspace_bytes"]
        / solve_baseline["cmg_workspace_bytes"]
    )
    pcg_memory_ratios[case] = (
        solve_candidate["pcg_workspace_bytes"]
        / solve_baseline["pcg_workspace_bytes"]
    )

    valid &= cycle_candidate["cycle"]["quotient_max_scaled_error"] <= 5.0e-10
    valid &= solve_candidate["iterations"] == solve_baseline["iterations"]
    valid &= all(
        math.isfinite(value) and value < 1.0e-6
        for value in solve_candidate["backward_errors"]
    )
    valid &= solve_candidate["cmg_workspace_bytes"] < solve_baseline["cmg_workspace_bytes"]
    valid &= solve_candidate["pcg_workspace_bytes"] < solve_baseline["pcg_workspace_bytes"]

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
cmg_memory_geomean = geometric_mean(cmg_memory_ratios)
pcg_memory_geomean = geometric_mean(pcg_memory_ratios)

accepted = (
    valid
    and max(cycle_ratios.values()) <= 1.05
    and max(solve_ratios.values()) <= 1.05
    and cmg_memory_geomean <= 0.85
    and pcg_memory_geomean <= 0.95
)

tested_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
record = {
    "schema": 1,
    "tested_sha": tested_sha,
    "accepted": accepted,
    "valid": valid,
    "cycle_ratios": cycle_ratios,
    "cycle_geometric_mean_ratio": cycle_geomean,
    "solve_ratios": solve_ratios,
    "solve_geometric_mean_ratio": solve_geomean,
    "cmg_workspace_ratios": cmg_memory_ratios,
    "cmg_workspace_geometric_mean_ratio": cmg_memory_geomean,
    "pcg_workspace_ratios": pcg_memory_ratios,
    "pcg_workspace_geometric_mean_ratio": pcg_memory_geomean,
    "records": records,
}
Path(".ci/performance/inplace-level-output-latest.json").write_text(
    json.dumps(record, indent=2) + "\n"
)

if accepted:
    plan = Path("PERFORMANCE_PLAN.md")
    text = plan.read_text()

    retained_anchor = (
        "- `.ci/performance/compatible-apply-latest.json`: accepted "
        "compatible-RHS stationary-core experiment.\n"
    )
    retained_entry = (
        "- `.ci/performance/inplace-level-output-latest.json`: accepted "
        "caller-output hierarchy workspace experiment.\n"
    )
    if retained_entry not in text:
        if text.count(retained_anchor) != 1:
            raise SystemExit("retained-record anchor changed")
        text = text.replace(retained_anchor, retained_anchor + retained_entry, 1)

    workspace_anchor = (
        "- CMG matvec and residual roles share one full vector per hierarchy level.\n"
    )
    workspace_entry = (
        "- Nonterminal CMG levels now iterate directly in caller-owned output, "
        "eliminating a separate solution vector and final copy at every hierarchy level.\n"
    )
    if workspace_entry not in text:
        if text.count(workspace_anchor) != 1:
            raise SystemExit("workspace-plan anchor changed")
        text = text.replace(workspace_anchor, workspace_anchor + workspace_entry, 1)

    memory_paragraph_anchor = (
        "- `GroundedLdl`, `CmgWorkspace`, and `PcgWorkspace` report retained "
        "principal heap bytes.\n"
    )
    memory_paragraph = (
        "\nThe in-place hierarchy-output checkpoint reduced retained CMG workspace "
        f"to path `{cmg_memory_ratios['path']:.3f}x` and worker–firm "
        f"`{cmg_memory_ratios['worker-firm']:.3f}x`; complete PCG workspace fell "
        f"to `{pcg_memory_ratios['path']:.3f}x` and "
        f"`{pcg_memory_ratios['worker-firm']:.3f}x`. Same-run cycle ratios were "
        f"`{cycle_ratios['path']:.3f}x` and `{cycle_ratios['worker-firm']:.3f}x`, "
        f"and solve ratios were `{solve_ratios['path']:.3f}x` and "
        f"`{solve_ratios['worker-firm']:.3f}x`, with unchanged iterations.\n"
    )
    if memory_paragraph not in text:
        if text.count(memory_paragraph_anchor) != 1:
            raise SystemExit("workspace paragraph anchor changed")
        text = text.replace(
            memory_paragraph_anchor,
            memory_paragraph_anchor + memory_paragraph,
            1,
        )

    log_anchor = (
        "| 2026-08-22 | `6d5f4cca` | Compatible stationary core retained; "
        "solve time improved with unchanged iterations |\n"
    )
    log_entry = (
        "| 2026-08-22 | in-place hierarchy output | Per-level solution scratch "
        "removed; C parity, iterations, residuals, timing, and memory gates passed |\n"
    )
    if log_entry not in text:
        if text.count(log_anchor) != 1:
            raise SystemExit("checkpoint-log anchor changed")
        text = text.replace(log_anchor, log_anchor + log_entry, 1)

    old_next = '''1. Run fresh three-platform and matched serial/parallel qualification on `6d5f4cca`.
2. Measure deterministic component centering in place of full recursive coarse-RHS compatibility projection, retaining it only if full-cycle parity, PCG convergence, symmetry, positivity, and real solve time improve.
3. If recursive centering is retained, measure a crate-private prevalidated apply path that skips repeated workspace/options checks inside PCG.
4. Continue large setup profiling and obtain 8–32-thread evidence when a suitable runner is available.
5. Remove obsolete one-shot staging workflows and scripts after the active qualification checkpoint is secure.'''
    new_next = '''1. Qualify the in-place hierarchy-output checkpoint on Ubuntu, macOS, and Windows and refresh matched serial/parallel records.
2. Measure deterministic component centering in place of full recursive coarse-RHS compatibility projection, retaining it only if full-cycle parity, PCG convergence, symmetry, positivity, and real solve time improve.
3. If recursive centering is retained, remove now-unneeded coarse component metadata and scratch, then measure a crate-private prevalidated apply path that skips repeated workspace/options checks inside PCG.
4. Continue large setup profiling and obtain 8–32-thread evidence when a suitable runner is available.
5. Remove obsolete one-shot staging workflows and scripts after the active qualification checkpoint is secure.'''
    if old_next not in text:
        raise SystemExit("current-next-action anchor changed")
    plan.write_text(text.replace(old_next, new_next, 1))

with open(os.environ["GITHUB_OUTPUT"], "a") as output:
    output.write(f"accepted={str(accepted).lower()}\n")
