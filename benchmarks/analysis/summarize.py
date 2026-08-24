#!/usr/bin/env python3
"""Validate collected SCC JSON, emit compact data, plots, and LaTeX fragments."""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


FAMILIES = ["path", "grid", "worker-firm", "dense-worker-firm", "weak-community"]
FAMILY_LABELS = {
    "path": "Weighted path",
    "grid": "2D grid",
    "worker-firm": "Worker-firm d=3",
    "dense-worker-firm": "Worker-firm d=16",
    "weak-community": "Weak community",
}
KERNEL_LABELS = {"path": "C-harness path", "worker-firm": "C-harness worker-firm"}
IMPLEMENTATIONS = ["rust", "matlab"]
STAGES = [
    ("preconditioner_setup_median_ns", "CMG setup"),
    ("pcg_median_ns", "PCG solve"),
    ("solver_total_median_ns", "Setup + solve"),
]
THREADS = [1, 2, 4, 8, 16, 32]
COLORS = dict(zip(FAMILIES, plt.get_cmap("tab10").colors))


def load_jsons(roots: Iterable[Path]) -> tuple[list[dict], list[dict]]:
    results: list[dict] = []
    kernels: list[dict] = []
    for root in roots:
        for path in sorted(root.glob("output/task-*/*.json")):
            value = json.loads(path.read_text())
            value["_path"] = str(path)
            if path.name == "c-kernel.json":
                kernels.append(value)
            elif value.get("implementation") in IMPLEMENTATIONS:
                results.append(value)
    return results, kernels


def load_repairs(roots: Iterable[Path]) -> list[dict]:
    repairs = []
    for root in roots:
        path = root / "receipts" / "DERIVED_SOURCE_REPAIR.json"
        if path.exists():
            repair = json.loads(path.read_text())
            repair["_path"] = str(path)
            repairs.append(repair)
    return repairs


def validate(results: list[dict]) -> None:
    seen = set()
    for result in results:
        identity = (
            result["run_id"],
            result["family"],
            int(result["vertices"]),
            result["mode"],
            result["implementation"],
            int(result["threads"]),
        )
        if identity in seen:
            raise ValueError(f"duplicate result {identity}")
        seen.add(identity)
        for key in (
            "preconditioner_setup_median_ns",
            "preconditioner_apply_median_ns",
            "pcg_median_ns",
            "solver_total_median_ns",
            "backward_error",
            "relative_residual",
        ):
            if not math.isfinite(float(result[key])):
                raise ValueError(f"nonfinite {key}: {result['_path']}")
        if not result.get("success"):
            raise ValueError(f"failed result: {result['_path']}")


def seconds(result: dict, field: str) -> float:
    return float(result[field]) / 1e9


def select(results: list[dict], **criteria: object) -> list[dict]:
    return [row for row in results if all(row.get(key) == value for key, value in criteria.items())]


def style() -> None:
    plt.rcParams.update(
        {
            "font.size": 8,
            "axes.titlesize": 9,
            "axes.labelsize": 8,
            "legend.fontsize": 7,
            "figure.dpi": 150,
            "savefig.bbox": "tight",
            "axes.grid": True,
            "grid.alpha": 0.25,
        }
    )


def save(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def plot_size(results: list[dict], output: Path, x_field: str, name: str, x_label: str) -> None:
    fig, axes = plt.subplots(2, 3, figsize=(10.2, 5.8), sharex=False, sharey=False)
    for row_index, implementation in enumerate(IMPLEMENTATIONS):
        for column, (field, title) in enumerate(STAGES):
            axis = axes[row_index, column]
            for family in FAMILIES:
                rows = sorted(
                    select(
                        results,
                        mode="single",
                        implementation=implementation,
                        family=family,
                        threads=32,
                    ),
                    key=lambda item: int(item[x_field]),
                )
                if rows:
                    axis.plot(
                        [int(item[x_field]) for item in rows],
                        [seconds(item, field) for item in rows],
                        marker="o",
                        color=COLORS[family],
                        label=FAMILY_LABELS[family],
                    )
            axis.set_xscale("log")
            axis.set_yscale("log")
            axis.set_title(f"{implementation.capitalize()}: {title}")
            axis.set_xlabel(x_label)
            axis.set_ylabel("Seconds")
    handles = [Line2D([0], [0], color=COLORS[item], marker="o", label=FAMILY_LABELS[item]) for item in FAMILIES]
    fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False)
    fig.subplots_adjust(bottom=0.17, hspace=0.45, wspace=0.32)
    save(fig, output / name)


def matched(results: list[dict], mode: str = "single") -> dict[tuple, dict[str, dict]]:
    grouped: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for row in results:
        if row["mode"] != mode:
            continue
        key = (row["family"], int(row["vertices"]), int(row["threads"]))
        grouped[key][row["implementation"]] = row
    return grouped


def plot_ratios(results: list[dict], output: Path) -> None:
    groups = matched(results)
    fig, axes = plt.subplots(1, 3, figsize=(10.2, 3.2))
    for axis_index, (axis, (field, title)) in enumerate(zip(axes, STAGES)):
        for family in FAMILIES:
            points = []
            for (case, vertices, threads), pair in groups.items():
                if case == family and threads == 32 and set(pair) == set(IMPLEMENTATIONS):
                    points.append((vertices, seconds(pair["rust"], field) / seconds(pair["matlab"], field)))
            points.sort()
            if points:
                axis.plot(*zip(*points), marker="o", color=COLORS[family])
        axis.axhline(1, color="black", linewidth=0.8)
        axis.set_xscale("log")
        axis.set_yscale("log")
        axis.set_title(title)
        axis.set_xlabel("Vertices")
        if axis_index == 0:
            axis.set_ylabel("Rust / MATLAB time")
    handles = [Line2D([0], [0], color=COLORS[item], marker="o", label=FAMILY_LABELS[item]) for item in FAMILIES]
    fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False)
    fig.subplots_adjust(bottom=0.25, wspace=0.32)
    save(fig, output / "rust_matlab_ratios.pdf")


def plot_memory(results: list[dict], output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2), sharey=True)
    for axis, implementation in zip(axes, IMPLEMENTATIONS):
        for family in FAMILIES:
            rows = sorted(
                select(results, mode="single", implementation=implementation, family=family, threads=32),
                key=lambda item: int(item["vertices"]),
            )
            if rows:
                axis.plot(
                    [int(item["vertices"]) for item in rows],
                    [float(item["process_accounting"]["peak_rss_kb"]) / 1024**2 for item in rows],
                    marker="o",
                    color=COLORS[family],
                )
        axis.set_xscale("log")
        axis.set_yscale("log")
        axis.set_title(implementation.capitalize())
        axis.set_xlabel("Vertices")
        axis.set_ylabel("Peak RSS (GiB)")
    handles = [Line2D([0], [0], color=COLORS[item], marker="o", label=FAMILY_LABELS[item]) for item in FAMILIES]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False)
    fig.subplots_adjust(bottom=0.3, wspace=0.22)
    save(fig, output / "memory_by_size.pdf")


def plot_apply(results: list[dict], output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2), sharey=True)
    for axis, implementation in zip(axes, IMPLEMENTATIONS):
        for family in FAMILIES:
            rows = sorted(
                select(results, mode="single", implementation=implementation, family=family, threads=32),
                key=lambda item: int(item["vertices"]),
            )
            if rows:
                axis.plot(
                    [int(item["vertices"]) for item in rows],
                    [seconds(item, "preconditioner_apply_median_ns") for item in rows],
                    marker="o",
                    color=COLORS[family],
                )
        axis.set_xscale("log")
        axis.set_yscale("log")
        axis.set_title(implementation.capitalize())
        axis.set_xlabel("Vertices")
        axis.set_ylabel("Seconds per stationary apply")
    handles = [Line2D([0], [0], color=COLORS[item], marker="o", label=FAMILY_LABELS[item]) for item in FAMILIES]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False)
    fig.subplots_adjust(bottom=0.3, wspace=0.22)
    save(fig, output / "preconditioner_apply_32.pdf")


def plot_input_assembly(results: list[dict], output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2), sharey=False)
    for axis, implementation in zip(axes, IMPLEMENTATIONS):
        for family in FAMILIES:
            rows = sorted(
                select(results, mode="single", implementation=implementation, family=family, threads=32),
                key=lambda item: int(item["vertices"]),
            )
            if rows:
                axis.plot(
                    [int(item["vertices"]) for item in rows],
                    [float(item["input_load_ns"]) / 1e9 + float(item["graph_build_ns"]) / 1e9 for item in rows],
                    marker="o",
                    color=COLORS[family],
                )
        axis.set_xscale("log")
        axis.set_yscale("log")
        axis.set_title(implementation.capitalize())
        axis.set_xlabel("Vertices")
        axis.set_ylabel("Input load + assembly (seconds)")
    handles = [Line2D([0], [0], color=COLORS[item], marker="o", label=FAMILY_LABELS[item]) for item in FAMILIES]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False)
    fig.subplots_adjust(bottom=0.3, wspace=0.3)
    save(fig, output / "input_assembly_32.pdf")


def plot_iterations(results: list[dict], output: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(7.4, 5.6))
    for column, implementation in enumerate(IMPLEMENTATIONS):
        for family in FAMILIES:
            rows = sorted(
                select(results, mode="single", implementation=implementation, family=family, threads=32),
                key=lambda item: int(item["vertices"]),
            )
            if not rows:
                continue
            vertices = [int(item["vertices"]) for item in rows]
            iterations = [max(1, int(item["iterations"])) for item in rows]
            axes[0, column].plot(vertices, iterations, marker="o", color=COLORS[family])
            axes[1, column].plot(
                vertices,
                [seconds(item, "pcg_median_ns") / iteration for item, iteration in zip(rows, iterations)],
                marker="o",
                color=COLORS[family],
            )
        axes[0, column].set_title(implementation.capitalize())
        axes[0, column].set_ylabel("PCG iterations")
        axes[1, column].set_ylabel("Seconds / iteration")
        for axis in axes[:, column]:
            axis.set_xscale("log")
            axis.set_yscale("log")
            axis.set_xlabel("Vertices")
    handles = [Line2D([0], [0], color=COLORS[item], marker="o", label=FAMILY_LABELS[item]) for item in FAMILIES]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False)
    fig.subplots_adjust(bottom=0.22, hspace=0.4, wspace=0.3)
    save(fig, output / "iterations_time_per_iteration.pdf")


def plot_cpu_family(results: list[dict], output: Path, family: str) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(10.2, 3.25))
    sizes = sorted({int(row["vertices"]) for row in select(results, mode="single", family=family)})
    size_colors = dict(zip(sizes, plt.get_cmap("viridis")([0.12, 0.52, 0.88][: len(sizes)])))
    for axis, (field, title) in zip(axes, STAGES):
        for implementation, linestyle in (("rust", "-"), ("matlab", "--")):
            for size in sizes:
                rows = sorted(
                    select(
                        results,
                        mode="single",
                        family=family,
                        implementation=implementation,
                        vertices=size,
                    ),
                    key=lambda item: int(item["threads"]),
                )
                if rows:
                    axis.plot(
                        [int(item["threads"]) for item in rows],
                        [seconds(item, field) for item in rows],
                        color=size_colors[size],
                        linestyle=linestyle,
                        marker="o",
                    )
        axis.set_xscale("log", base=2)
        axis.set_yscale("log")
        axis.set_xticks(THREADS, labels=[str(item) for item in THREADS])
        axis.set_title(title)
        axis.set_xlabel("Application CPUs")
        axis.set_ylabel("Seconds")
    handles = [Line2D([0], [0], color=size_colors[size], marker="o", label=f"{size:,} vertices") for size in sizes]
    handles += [
        Line2D([0], [0], color="black", linestyle="-", label="Rust"),
        Line2D([0], [0], color="black", linestyle="--", label="MATLAB"),
    ]
    fig.suptitle(FAMILY_LABELS[family])
    fig.legend(handles=handles, loc="lower center", ncol=len(handles), frameon=False)
    fig.subplots_adjust(bottom=0.26, top=0.82, wspace=0.34)
    save(fig, output / f"cpu_scaling_{family}.pdf")


def plot_speedup_efficiency(results: list[dict], output: Path) -> None:
    fig, axes = plt.subplots(2, 3, figsize=(10.2, 6.0), sharex=True)
    for column, (field, title) in enumerate(STAGES):
        for family in FAMILIES:
            for implementation, linestyle in (("rust", "-"), ("matlab", "--")):
                rows = sorted(
                    select(
                        results,
                        mode="single",
                        family=family,
                        vertices=1_000_000,
                        implementation=implementation,
                    ),
                    key=lambda item: int(item["threads"]),
                )
                if not rows or int(rows[0]["threads"]) != 1:
                    continue
                base = seconds(rows[0], field)
                cpus = [int(item["threads"]) for item in rows]
                speedup = [base / seconds(item, field) for item in rows]
                axes[0, column].plot(cpus, speedup, color=COLORS[family], linestyle=linestyle, marker="o")
                axes[1, column].plot(cpus, [value / cpu for value, cpu in zip(speedup, cpus)], color=COLORS[family], linestyle=linestyle, marker="o")
        axes[0, column].set_title(title)
        axes[0, column].set_ylabel("Speedup vs 1 CPU")
        axes[1, column].set_ylabel("Parallel efficiency")
        axes[1, column].set_xlabel("Application CPUs")
        axes[1, column].axhline(1, color="black", linewidth=0.7)
        for axis in axes[:, column]:
            axis.set_xscale("log", base=2)
            axis.set_xticks(THREADS, labels=[str(item) for item in THREADS])
    handles = [Line2D([0], [0], color=COLORS[item], label=FAMILY_LABELS[item]) for item in FAMILIES]
    handles += [Line2D([0], [0], color="black", linestyle="-", label="Rust"), Line2D([0], [0], color="black", linestyle="--", label="MATLAB")]
    fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False)
    fig.subplots_adjust(bottom=0.17, hspace=0.35, wspace=0.3)
    save(fig, output / "speedup_efficiency_1m.pdf")


def plot_batch(results: list[dict], output: Path) -> None:
    rows = [item for item in results if item["mode"] == "batch16"]
    if not rows:
        fig, axis = plt.subplots(figsize=(7.2, 2.2))
        axis.axis("off")
        axis.text(
            0.5,
            0.58,
            "Batch16 SCC results pending",
            ha="center",
            va="center",
            fontsize=13,
            weight="bold",
        )
        axis.text(
            0.5,
            0.38,
            "Worker-firm d=3 and d=16; 300,000 and 1,000,000 vertices",
            ha="center",
            va="center",
        )
        save(fig, output / "batch16_scaling.pdf")
        return
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2), sharey=True)
    for axis, family in zip(axes, ("worker-firm", "dense-worker-firm")):
        for implementation, linestyle in (("rust", "-"), ("matlab", "--")):
            for size, color in ((300_000, "#2c7fb8"), (1_000_000, "#d95f0e")):
                chosen = sorted(
                    select(rows, family=family, implementation=implementation, vertices=size),
                    key=lambda item: int(item["threads"]),
                )
                if chosen:
                    axis.plot(
                        [int(item["threads"]) for item in chosen],
                        [seconds(item, "pcg_median_ns") / 16 for item in chosen],
                        color=color,
                        linestyle=linestyle,
                        marker="o",
                    )
        axis.set_xscale("log", base=2)
        axis.set_yscale("log")
        axis.set_xticks(THREADS, labels=[str(item) for item in THREADS])
        axis.set_title(FAMILY_LABELS[family])
        axis.set_xlabel("Application CPUs")
        axis.set_ylabel("Seconds / RHS (batch of 16)")
    handles = [
        Line2D([0], [0], color="#2c7fb8", label="300,000 vertices"),
        Line2D([0], [0], color="#d95f0e", label="1,000,000 vertices"),
        Line2D([0], [0], color="black", linestyle="-", label="Rust"),
        Line2D([0], [0], color="black", linestyle="--", label="MATLAB"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False)
    fig.subplots_adjust(bottom=0.25, wspace=0.25)
    save(fig, output / "batch16_scaling.pdf")


def plot_kernels(kernels: list[dict], output: Path) -> None:
    fig, axes = plt.subplots(1, 4, figsize=(12.2, 3.25))
    for case, color in (("path", COLORS["path"]), ("worker-firm", COLORS["worker-firm"])):
        rows = sorted((item for item in kernels if item["case"] == case), key=lambda item: int(item["vertices"]))
        if not rows:
            continue
        x = [int(item["vertices"]) for item in rows]
        axes[0].plot(x, [float(item["rust_median_ns"]) / 1e9 / int(item["loops"]) for item in rows], marker="o", color=color, linestyle="-", label=f"{KERNEL_LABELS[case]} Rust")
        axes[0].plot(x, [float(item["c_median_ns"]) / 1e9 / int(item["loops"]) for item in rows], marker="o", color=color, linestyle="--", label=f"{KERNEL_LABELS[case]} C")
        axes[1].plot(x, [float(item["projection"]["restriction_rust_median_ns"]) / 1e9 / int(item["projection"]["loops"]) for item in rows], marker="o", color=color, linestyle="-", label=f"{KERNEL_LABELS[case]} Rust")
        axes[1].plot(x, [float(item["projection"]["restriction_c_median_ns"]) / 1e9 / int(item["projection"]["loops"]) for item in rows], marker="o", color=color, linestyle="--", label=f"{KERNEL_LABELS[case]} C")
        axes[2].plot(x, [float(item["projection"]["prolongation_rust_median_ns"]) / 1e9 / int(item["projection"]["loops"]) for item in rows], marker="o", color=color, linestyle="-", label=f"{KERNEL_LABELS[case]} Rust")
        axes[2].plot(x, [float(item["projection"]["prolongation_c_median_ns"]) / 1e9 / int(item["projection"]["loops"]) for item in rows], marker="o", color=color, linestyle="--", label=f"{KERNEL_LABELS[case]} C")
        cycle = rows[0]["cycle"]
        axes[3].scatter([int(cycle["dimension"])], [float(cycle["rust_median_ns"]) / 1e9 / int(cycle["loops"])], marker="o", color=color)
        axes[3].scatter([int(cycle["dimension"])], [float(cycle["c_median_ns"]) / 1e9 / int(cycle["loops"])], marker="x", color=color)
    for axis, title in zip(axes, ("SpMV per call", "Restriction per call", "Prolongation per call", "Recursive cycle per call")):
        axis.set_xscale("log")
        axis.set_yscale("log")
        axis.set_title(title)
        axis.set_xlabel("Vertices")
        axis.set_ylabel("Seconds")
    axes[3].text(0.5, 0.04, "Capped at 20,000 vertices", transform=axes[3].transAxes, ha="center", fontsize=7)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, frameon=False)
    fig.subplots_adjust(bottom=0.26, wspace=0.32)
    save(fig, output / "c_kernel_scope.pdf")


def write_csv(results: list[dict], path: Path) -> None:
    fields = [
        "run_id", "environment_id", "family", "mode", "vertices", "canonical_edges",
        "matrix_nonzeros", "implementation", "threads", "repetitions", "batch_count",
        "preconditioner_setup_median_ns", "parallel_plan_setup_median_ns",
        "preconditioner_apply_median_ns", "pcg_median_ns", "solver_total_median_ns",
        "iterations", "relative_residual", "backward_error", "truth_scaled_error", "levels",
        "hostname", "cpu_model", "peak_rss_kb",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for result in sorted(results, key=lambda item: (item["mode"], item["family"], item["vertices"], item["implementation"], item["threads"])):
            row = {field: result.get(field, "") for field in fields}
            row["peak_rss_kb"] = result["process_accounting"].get("peak_rss_kb", "")
            writer.writerow(row)


def write_latest(results: list[dict], repairs: list[dict], path: Path) -> None:
    rows = []
    for result in results:
        if result["mode"] == "single" and int(result["threads"]) == 32:
            rows.append(
                {
                    key: result[key]
                    for key in (
                        "family", "vertices", "canonical_edges", "implementation", "threads",
                        "preconditioner_setup_median_ns", "pcg_median_ns", "solver_total_median_ns",
                        "iterations", "relative_residual", "backward_error",
                    )
                }
            )
    payload = {
        "schema": 1,
        "run_ids": sorted({row["run_id"] for row in results}),
        "environment_ids": sorted({row["environment_id"] for row in results}),
        "source_commits": sorted({row["source_commit"] for row in results}),
        "upstream_commits": sorted(
            {row["upstream_commit"] for row in results if row.get("upstream_commit")}
        ),
        "scope": "SCC Gold-6242, default release/MEX builds, 32 application CPUs",
        "derived_source_repairs": [
            {
                key: repair[key]
                for key in (
                    "run_id",
                    "expected_source_commit",
                    "raw_tree_sha256",
                    "changed_fields",
                    "changed_files",
                )
            }
            for repair in repairs
        ],
        "results": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def latex_escape(value: str) -> str:
    return value.replace("_", r"\_").replace("%", r"\%")


def write_tex(results: list[dict], kernels: list[dict], repairs: list[dict], path: Path) -> None:
    main = [row for row in results if row["mode"] == "single"]
    hosts = sorted({row["hostname"] for row in main})
    cpu = sorted({row["cpu_model"] for row in main})
    environments = sorted({row["environment_id"] for row in results})
    source_commits = sorted({row["source_commit"] for row in results})
    matlab_releases = sorted({row.get("matlab_release", "unknown") for row in main if row["implementation"] == "matlab"})
    max_rss_gib = max(float(row["process_accounting"]["peak_rss_kb"]) / 1024**2 for row in results)
    max_backward = max(float(row["backward_error"]) for row in results)
    max_relative_residual = max(float(row["relative_residual"]) for row in results)
    pairs_32 = [pair for (family, vertices, threads), pair in matched(main).items() if threads == 32 and set(pair) == set(IMPLEMENTATIONS)]

    def geometric_mean(values: list[float]) -> float:
        return math.exp(sum(math.log(value) for value in values) / len(values))

    stage_geomeans = {
        field: geometric_mean([seconds(pair["rust"], field) / seconds(pair["matlab"], field) for pair in pairs_32])
        for field, _ in STAGES
    }
    apply_geomean = geometric_mean(
        [
            seconds(pair["rust"], "preconditioner_apply_median_ns")
            / seconds(pair["matlab"], "preconditioner_apply_median_ns")
            for pair in pairs_32
        ]
    )
    memory_geomean = geometric_mean(
        [
            float(pair["rust"]["process_accounting"]["peak_rss_kb"])
            / float(pair["matlab"]["process_accounting"]["peak_rss_kb"])
            for pair in pairs_32
        ]
    )
    total_speedups: dict[str, float] = {}
    for implementation in IMPLEMENTATIONS:
        values = []
        for family in FAMILIES:
            one = select(main, family=family, implementation=implementation, vertices=1_000_000, threads=1)
            thirty_two = select(main, family=family, implementation=implementation, vertices=1_000_000, threads=32)
            if one and thirty_two:
                values.append(seconds(one[0], "solver_total_median_ns") / seconds(thirty_two[0], "solver_total_median_ns"))
        total_speedups[implementation] = geometric_mean(values)
    warning_points = sum(
        int(row.get("hierarchy_flag", 0) != 0)
        for row in main
        if row["implementation"] == "matlab" and int(row["threads"]) == 32
    )
    hierarchy_differences = 0
    terminal_offset_cases = 0
    for pair in pairs_32:
        rust_levels = [int(value) for value in pair["rust"]["level_vertices"]]
        matlab_levels = [int(value) for value in pair["matlab"]["level_vertices"]]
        if rust_levels != matlab_levels:
            hierarchy_differences += 1
            if (
                len(rust_levels) == len(matlab_levels)
                and rust_levels[:-1] == matlab_levels[:-1]
                and rust_levels[-1] == matlab_levels[-1] + 1
            ):
                terminal_offset_cases += 1
    batch_pairs = [
        pair
        for (_, _, threads), pair in matched(results, mode="batch16").items()
        if threads == 32 and set(pair) == set(IMPLEMENTATIONS)
    ]
    batch_ratio = (
        geometric_mean(
            [
                seconds(pair["rust"], "pcg_median_ns")
                / seconds(pair["matlab"], "pcg_median_ns")
                for pair in batch_pairs
            ]
        )
        if batch_pairs
        else math.nan
    )
    batch_speedups = {}
    for implementation in IMPLEMENTATIONS:
        values = []
        for family in ("worker-firm", "dense-worker-firm"):
            for vertices in (300_000, 1_000_000):
                one = select(
                    results,
                    mode="batch16",
                    family=family,
                    vertices=vertices,
                    implementation=implementation,
                    threads=1,
                )
                thirty_two = select(
                    results,
                    mode="batch16",
                    family=family,
                    vertices=vertices,
                    implementation=implementation,
                    threads=32,
                )
                if one and thirty_two:
                    values.append(
                        seconds(one[0], "pcg_median_ns")
                        / seconds(thirty_two[0], "pcg_median_ns")
                    )
        batch_speedups[implementation] = geometric_mean(values) if values else math.nan
    if batch_pairs:
        batch_summary = (
            "At 32 application CPUs, the geometric-mean Rust-to-MATLAB "
            f"normalized per-RHS ratio is {batch_ratio:.3f}. Relative to one CPU, "
            "the corresponding Rust and MATLAB geometric-mean speedups are "
            f"{batch_speedups['rust']:.2f} and {batch_speedups['matlab']:.2f}."
        )
    else:
        batch_summary = (
            "The scheduled SCC batch supplement was still pending at report "
            "snapshot time, so no repeated-RHS performance conclusion is made here."
        )
    batch_ratio_value = "pending" if math.isnan(batch_ratio) else f"{batch_ratio:.3f}"
    rust_batch_speedup_value = (
        "pending"
        if math.isnan(batch_speedups["rust"])
        else f"{batch_speedups['rust']:.2f}"
    )
    matlab_batch_speedup_value = (
        "pending"
        if math.isnan(batch_speedups["matlab"])
        else f"{batch_speedups['matlab']:.2f}"
    )
    kernel_spmv_ratios = [float(row["rust_over_c"]) for row in kernels]
    kernel_cycle_ratios = [float(row["cycle"]["rust_over_c"]) for row in kernels]
    provenance_files = sum(int(repair["changed_files"]) for repair in repairs)
    provenance_runs = sorted({repair["run_id"] for repair in repairs})
    rust_truth_max = max(
        float(row["truth_scaled_error"])
        for row in main
        if row["implementation"] == "rust"
    )
    matlab_truth_max = max(
        float(row["truth_scaled_error"])
        for row in main
        if row["implementation"] == "matlab"
    )
    lines = [
        r"\newcommand{\BenchmarkRunIds}{" + latex_escape(", ".join(sorted({row["run_id"] for row in results}))) + "}",
        r"\newcommand{\BenchmarkEnvironmentIds}{" + latex_escape(", ".join(environments)) + "}",
        r"\newcommand{\BenchmarkHosts}{" + latex_escape(", ".join(hosts)) + "}",
        r"\newcommand{\BenchmarkCpuModel}{" + latex_escape(", ".join(cpu)) + "}",
        r"\newcommand{\BenchmarkSourceCommits}{" + latex_escape(", ".join(source_commits)) + "}",
        r"\newcommand{\BenchmarkMatlabReleases}{" + latex_escape(", ".join(matlab_releases)) + "}",
        rf"\newcommand{{\BenchmarkMaxRssGiB}}{{{max_rss_gib:.2f}}}",
        rf"\newcommand{{\BenchmarkMaxBackwardError}}{{{max_backward:.2e}}}",
        rf"\newcommand{{\BenchmarkMaxRelativeResidual}}{{{max_relative_residual:.2e}}}",
        rf"\newcommand{{\BenchmarkSetupRatio}}{{{stage_geomeans['preconditioner_setup_median_ns']:.3f}}}",
        rf"\newcommand{{\BenchmarkApplyRatio}}{{{apply_geomean:.3f}}}",
        rf"\newcommand{{\BenchmarkPcgRatio}}{{{stage_geomeans['pcg_median_ns']:.3f}}}",
        rf"\newcommand{{\BenchmarkTotalRatio}}{{{stage_geomeans['solver_total_median_ns']:.3f}}}",
        rf"\newcommand{{\BenchmarkMemoryRatio}}{{{memory_geomean:.3f}}}",
        rf"\newcommand{{\BenchmarkRustTotalSpeedup}}{{{total_speedups['rust']:.2f}}}",
        rf"\newcommand{{\BenchmarkMatlabTotalSpeedup}}{{{total_speedups['matlab']:.2f}}}",
        rf"\newcommand{{\BenchmarkMatlabWarningPoints}}{{{warning_points}}}",
        rf"\newcommand{{\BenchmarkHierarchyDifferenceCases}}{{{hierarchy_differences}}}",
        rf"\newcommand{{\BenchmarkTerminalOffsetCases}}{{{terminal_offset_cases}}}",
        rf"\newcommand{{\BenchmarkRustTruthMax}}{{{rust_truth_max:.2e}}}",
        rf"\newcommand{{\BenchmarkMatlabTruthMax}}{{{matlab_truth_max:.2e}}}",
        rf"\newcommand{{\BenchmarkBatchRatio}}{{{batch_ratio_value}}}",
        rf"\newcommand{{\BenchmarkRustBatchSpeedup}}{{{rust_batch_speedup_value}}}",
        rf"\newcommand{{\BenchmarkMatlabBatchSpeedup}}{{{matlab_batch_speedup_value}}}",
        r"\newcommand{\BenchmarkBatchSummary}{" + latex_escape(batch_summary) + "}",
        rf"\newcommand{{\BenchmarkKernelSpmvRatioLow}}{{{min(kernel_spmv_ratios):.3f}}}",
        rf"\newcommand{{\BenchmarkKernelSpmvRatioHigh}}{{{max(kernel_spmv_ratios):.3f}}}",
        rf"\newcommand{{\BenchmarkKernelCycleRatioLow}}{{{min(kernel_cycle_ratios):.3f}}}",
        rf"\newcommand{{\BenchmarkKernelCycleRatioHigh}}{{{max(kernel_cycle_ratios):.3f}}}",
        rf"\newcommand{{\BenchmarkProvenanceRepairFiles}}{{{provenance_files}}}",
        r"\newcommand{\BenchmarkProvenanceRepairRuns}{"
        + latex_escape(", ".join(provenance_runs) if provenance_runs else "none")
        + "}",
        r"\newcommand{\BenchmarkPostprocessPython}{" + latex_escape(platform.python_version()) + "}",
        r"\newcommand{\BenchmarkPostprocessMatplotlib}{" + latex_escape(matplotlib.__version__) + "}",
        r"\newcommand{\BenchmarkMillionTable}{%",
        r"\begin{tabular}{llrrrr}",
        r"\toprule",
        r"Family & Implementation & Vertices & Edges & Iterations & Total (s) \\",
        r"\midrule",
    ]
    for family in FAMILIES:
        for implementation in IMPLEMENTATIONS:
            chosen = select(main, family=family, implementation=implementation, vertices=1_000_000, threads=32)
            if chosen:
                row = chosen[0]
                lines.append(
                    f"{latex_escape(FAMILY_LABELS[family])} & {implementation.capitalize()} & "
                    f"{int(row['vertices']):,} & {int(row['canonical_edges']):,} & "
                    f"{int(row['iterations'])} & {seconds(row, 'solver_total_median_ns'):.3g} \\\\"
                )
    lines += [r"\bottomrule", r"\end{tabular}%", r"}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="append", required=True, type=Path)
    parser.add_argument("--figures", required=True, type=Path)
    parser.add_argument("--summary-csv", required=True, type=Path)
    parser.add_argument("--results-tex", required=True, type=Path)
    parser.add_argument("--latest-json", required=True, type=Path)
    args = parser.parse_args()
    style()
    results, kernels = load_jsons(args.run)
    repairs = load_repairs(args.run)
    validate(results)
    write_csv(results, args.summary_csv)
    write_latest(results, repairs, args.latest_json)
    write_tex(results, kernels, repairs, args.results_tex)
    plot_size(results, args.figures, "vertices", "size_scaling_32.pdf", "Vertices")
    plot_size(results, args.figures, "canonical_edges", "edge_scaling_32.pdf", "Canonical edges")
    plot_ratios(results, args.figures)
    plot_memory(results, args.figures)
    plot_apply(results, args.figures)
    plot_input_assembly(results, args.figures)
    plot_iterations(results, args.figures)
    for family in FAMILIES:
        plot_cpu_family(results, args.figures, family)
    plot_speedup_efficiency(results, args.figures)
    plot_batch(results, args.figures)
    plot_kernels(kernels, args.figures)
    print(f"CMG_ANALYSIS_SUCCESS results={len(results)} kernels={len(kernels)}")


if __name__ == "__main__":
    main()
