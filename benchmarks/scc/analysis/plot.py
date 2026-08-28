#!/usr/bin/env python3
"""Generate deterministic SCC2 report figures from reduced CSV data."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


FAMILIES = ("path", "grid", "worker-firm", "dense-worker-firm", "weak-community")
COLORS = {
    "path": "#4477AA",
    "grid": "#228833",
    "worker-firm": "#CCBB44",
    "dense-worker-firm": "#EE6677",
    "weak-community": "#AA3377",
}
LINESTYLES = {"rust": "-", "matlab": "--"}


def read(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def number(row: dict[str, str], key: str) -> float | None:
    try:
        value = float(row.get(key, ""))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def save(fig: plt.Figure, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", metadata={"Creator": "CMG SCC2 deterministic reducer"})
    plt.close(fig)


def empty(ax: plt.Axes, label: str) -> None:
    ax.text(0.5, 0.5, f"No accepted {label} data", ha="center", va="center", transform=ax.transAxes)
    ax.set_axis_off()


def line_panel(
    ax: plt.Axes,
    rows: list[dict[str, str]],
    x_key: str,
    y_key: str,
    title: str,
    *,
    group: Callable[[dict[str, str]], tuple[str, str]] = lambda row: (row["family"], row["implementation"]),
) -> None:
    grouped: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        x = number(row, x_key)
        y = number(row, y_key)
        if x is not None and y is not None:
            grouped[group(row)].append((x, y))
    if not grouped:
        empty(ax, title)
        return
    for (family, implementation), values in sorted(grouped.items()):
        values.sort()
        ax.plot(
            [value[0] for value in values], [value[1] for value in values],
            marker="o", color=COLORS.get(family, "#444444"),
            linestyle=LINESTYLES.get(implementation, "-"),
            label=f"{family}, {implementation}",
        )
    ax.set_title(title)
    ax.grid(True, alpha=0.25)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-root", type=Path, required=True)
    args = parser.parse_args()
    data = args.report_root / "data"
    figures = args.report_root / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    results = read(data / "results.csv")
    samples = read(data / "samples.csv")
    phases = read(data / "phases.csv")
    memory = read(data / "memory.csv")
    batch = read(data / "batch.csv")

    baseline = [row for row in results if row.get("experiment") == "baseline"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.1))
    for ax, (key, title) in zip(
        axes,
        (
            ("preconditioner_setup_median_s", "Hierarchy/preconditioner setup"),
            ("pcg_solve_median_s", "PCG solve"),
            ("solver_total_median_s", "Setup + plan + solve"),
        ),
    ):
        line_panel(ax, baseline, "solve_threads", key, title)
        ax.set_xlabel("CPUs")
        ax.set_ylabel("seconds")
        ax.set_xscale("log", base=2)
        ax.set_yscale("log")
    handles, labels = axes[-1].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, -0.02), ncol=4, fontsize=7)
    save(fig, figures / "latency-one-million.pdf")

    slot_rows = [row for row in samples if row.get("experiment") == "baseline" and row.get("stage") in ("pcg_solve", "solver_total")]
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 4))
    for ax, stage in zip(axes, ("pcg_solve", "solver_total")):
        line_panel(ax, [row for row in slot_rows if row["stage"] == stage], "solve_threads", "cpu_slot_seconds", stage.replace("_", " ").title())
        ax.set_xlabel("CPUs")
        ax.set_ylabel("CPU-slot seconds")
        ax.set_xscale("log", base=2)
    save(fig, figures / "slot-seconds.pdf")

    routing = [row for row in results if row.get("experiment") == "routing"]
    fig, axes = plt.subplots(1, len(FAMILIES), figsize=(16, 3.5), sharey=False)
    for ax, family in zip(axes, FAMILIES):
        local = [row for row in routing if row["family"] == family]
        grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for row in local:
            x, y = number(row, "solve_threads"), number(row, "solver_total_median_s")
            if x is not None and y is not None:
                grouped[row["strategy"]].append((x, y))
        if not grouped:
            empty(ax, family)
            continue
        for strategy, values in sorted(grouped.items()):
            values.sort()
            ax.plot(*zip(*values), marker="o", label=strategy)
        ax.set_title(family)
        ax.set_xlabel("CPUs")
        ax.set_xscale("log", base=2)
        ax.grid(True, alpha=0.25)
    axes[0].set_ylabel("seconds")
    handles, labels = axes[-1].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, -0.02), ncol=3)
    save(fig, figures / "rust-routing.pdf")

    fig, ax = plt.subplots(figsize=(10, 4.8))
    dense = [row for row in baseline if row["family"] == "dense-worker-firm" and row["implementation"] == "rust"]
    dense.sort(key=lambda row: float(row["solve_threads"]))
    if dense:
        x = [float(row["solve_threads"]) for row in dense]
        setup = [float(row.get("preconditioner_setup_median_s", 0)) for row in dense]
        plan = [float(row.get("parallel_plan_setup_median_s", 0)) for row in dense]
        solve = [float(row.get("pcg_solve_median_s", 0)) for row in dense]
        ax.bar(x, setup, label="hierarchy")
        ax.bar(x, plan, bottom=setup, label="plan")
        ax.bar(x, solve, bottom=[a + b for a, b in zip(setup, plan)], label="PCG")
        ax.set_xticks(x)
        ax.legend()
        ax.set_xlabel("CPUs")
        ax.set_ylabel("seconds")
    else:
        empty(ax, "decomposition")
    ax.set_title("Dense worker–firm Rust setup/plan/solve decomposition")
    save(fig, figures / "setup-plan-solve-decomposition.pdf")

    pcg_phase_names = {"preconditioner", "finest_matvec", "dot_products", "vector_updates", "centering_projection", "norms", "residual_recompute", "certification", "unattributed"}
    for filename, accepted, title in (
        ("pcg-phase-shares.pdf", pcg_phase_names, "PCG production phase shares"),
        ("hierarchy-phase-shares.pdf", {"production_hierarchy", "preconditioner_finalization", "complete_preconditioner"}, "Setup production phase timings"),
        ("plan-level-construction.pdf", {"parallel_plan_complete", "parallel_plan_level_eligible", "terminal", "below-density-floor", "below-executor-threshold"}, "Parallel-plan level timings"),
    ):
        fig, ax = plt.subplots(figsize=(10, 5))
        chosen = [row for row in phases if row.get("phase") in accepted]
        grouped: dict[str, list[float]] = defaultdict(list)
        for row in chosen:
            grouped[row["phase"]].append(float(row["wall_s"]))
        if grouped:
            labels = sorted(grouped)
            ax.bar(labels, [statistics_median(grouped[label]) for label in labels])
            ax.tick_params(axis="x", rotation=35)
            ax.set_ylabel("median seconds per record")
        else:
            empty(ax, title)
        ax.set_title(title)
        save(fig, figures / filename)

    utilization = [row for row in samples if row.get("experiment") == "baseline" and row.get("stage") in ("preconditioner_setup", "parallel_plan_setup", "pcg_solve")]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for ax, stage in zip(axes, ("preconditioner_setup", "parallel_plan_setup", "pcg_solve")):
        line_panel(ax, [row for row in utilization if row["stage"] == stage], "solve_threads", "average_active_cpus", stage.replace("_", " ").title())
        ax.set_xlabel("requested CPUs")
        ax.set_ylabel("average active CPUs")
        ax.plot([1, 32], [1, 32], color="#777777", linestyle=":", linewidth=1)
    save(fig, figures / "active-cpus.pdf")

    numa = [row for row in results if row.get("experiment") == "numa"]
    fig, ax = plt.subplots(figsize=(11, 4.8))
    if numa:
        labels = [f"{row['family']}\n{row['implementation']}\n{row['placement_mode']}" for row in numa]
        values = [float(row["solver_total_median_s"]) for row in numa]
        ax.bar(range(len(values)), values)
        ax.set_xticks(range(len(labels)), labels, rotation=75, ha="right", fontsize=7)
        ax.set_ylabel("seconds")
    else:
        empty(ax, "NUMA")
    ax.set_title("NUMA placement and memory-policy comparison")
    save(fig, figures / "numa-placement.pdf")

    fig, ax = plt.subplots(figsize=(11, 4.8))
    if memory:
        grouped_memory: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        for row in memory:
            grouped_memory[(row["family"], row["implementation"], row["stage"])].append(float(row["peak_rss_kb"]) / 1024**2)
        labels = ["/".join(key) for key in sorted(grouped_memory)]
        values = [statistics_median(grouped_memory[key]) for key in sorted(grouped_memory)]
        ax.bar(range(len(values)), values)
        ax.set_xticks(range(len(labels)), labels, rotation=80, ha="right", fontsize=6)
        ax.set_ylabel("process peak RSS (GiB)")
    else:
        empty(ax, "memory-stage")
    ax.set_title("Separate-process memory decomposition")
    save(fig, figures / "memory-decomposition.pdf")

    accuracy = [row for row in results if row.get("experiment") == "accuracy"]
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 4))
    for ax, error_key, title in zip(axes, ("backward_error_max", "energy_norm_error_max"), ("Backward error", "Energy-norm error")):
        for row in accuracy:
            x, y = number(row, error_key), number(row, "pcg_solve_median_s")
            if x is not None and y is not None:
                ax.scatter(x, y, color=COLORS.get(row["family"], "#444444"), marker="o" if row["implementation"] == "rust" else "x")
        if not accuracy:
            empty(ax, "accuracy")
        else:
            ax.set_xscale("log")
            ax.set_yscale("log")
            ax.set_xlabel(title)
            ax.set_ylabel("PCG seconds")
            ax.grid(True, alpha=0.25)
    save(fig, figures / "accuracy-time-frontier.pdf")

    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for ax, y_key, title in zip(axes, ("rhs_per_second", "seconds_per_rhs"), ("Throughput", "Normalized latency")):
        line_panel(ax, batch, "solve_threads", y_key, title, group=lambda row: (row["family"], row["implementation"]))
        ax.set_xlabel("CPUs")
        ax.set_ylabel("RHS/s" if y_key == "rhs_per_second" else "seconds/RHS")
        ax.set_xscale("log", base=2)
        ax.set_yscale("log")
    save(fig, figures / "batch-throughput.pdf")

    matched = [row for row in results if row.get("experiment") == "matched-edge"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    line_panel(ax, matched, "solve_threads", "solver_total_median_s", "Approximately eight-million-edge scaling")
    ax.set_xlabel("CPUs")
    ax.set_ylabel("seconds")
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    save(fig, figures / "matched-edge-scaling.pdf")
    print(f"CMG_SCC2_PLOTS_SUCCESS figures={len(list(figures.glob('*.pdf')))}")


def statistics_median(values: list[float]) -> float:
    values = sorted(values)
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


if __name__ == "__main__":
    main()
