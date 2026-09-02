#!/usr/bin/env python3
"""Generate the current Rust/MATLAB benchmark report inputs.

The detailed 40-row CSV is a deterministic reduction of the immutable accepted
SCC run.  The compact accepted JSON is checked independently so that the report
cannot silently drift from the maintained performance record.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402


REPORT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = REPORT_ROOT.parents[1]
DATA_ROOT = REPORT_ROOT / "data"
FIGURE_ROOT = REPORT_ROOT / "figures"
CSV_PATH = DATA_ROOT / "current_results.csv"
RECORD_PATH = REPO_ROOT / ".ci" / "performance" / "scc-rust-matlab-current.json"

THREADS = (1, 8, 16, 32)
FAMILIES = ("path", "grid", "worker-firm", "dense-worker-firm", "weak-community")
FAMILY_LABELS = {
    "path": "Weighted path",
    "grid": "2D grid",
    "worker-firm": "Worker-firm d=3",
    "dense-worker-firm": "Worker-firm d=16",
    "weak-community": "Weak community",
}
FAMILY_COLORS = {
    "path": "#4477AA",
    "grid": "#228833",
    "worker-firm": "#CCBB44",
    "dense-worker-firm": "#EE6677",
    "weak-community": "#AA3377",
}
IMPLEMENTATION_STYLES = {
    "rust": {"color": "#0072B2", "linestyle": "-", "marker": "o", "label": "Rust"},
    "matlab": {"color": "#D55E00", "linestyle": "--", "marker": "s", "label": "MATLAB"},
}
METRICS = {
    "preconditioner_setup_median_s": ("preconditioner_setup", "Hierarchy/preconditioner setup"),
    "preconditioner_apply_median_s": ("preconditioner_apply", "Stationary preconditioner apply"),
    "pcg_solve_median_s": ("pcg_solve", "Reused-preconditioner PCG"),
    "solver_total_median_s": ("solver_total", "Setup + plan + solve"),
}


def load_inputs() -> tuple[list[dict[str, str]], dict[str, Any]]:
    with CSV_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    record = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    if len(rows) != 40:
        raise ValueError(f"expected 40 current configurations, found {len(rows)}")
    if set(row["experiment"] for row in rows) != {"baseline"}:
        raise ValueError("current report CSV contains non-baseline rows")
    if set(row["family"] for row in rows) != set(FAMILIES):
        raise ValueError("current report CSV family grid is incomplete")
    if set(int(row["solve_threads"]) for row in rows) != set(THREADS):
        raise ValueError("current report CSV CPU grid is incomplete")
    expected_pairs = {
        (family, implementation, thread)
        for family in FAMILIES
        for implementation in ("rust", "matlab")
        for thread in THREADS
    }
    observed_pairs = {
        (row["family"], row["implementation"], int(row["solve_threads"])) for row in rows
    }
    if observed_pairs != expected_pairs:
        raise ValueError("current report CSV has missing or duplicate configurations")
    for field in ("run_id", "source_commit", "upstream_commit"):
        expected = {
            "run_id": record["run_id"],
            "source_commit": record["source_commit"],
            "upstream_commit": record["upstream_commit"],
        }[field]
        if set(row[field] for row in rows) != {expected}:
            raise ValueError(f"CSV {field} does not match compact accepted record")
    return rows, record


def as_float(row: dict[str, str], key: str) -> float:
    value = float(row[key])
    if not math.isfinite(value):
        raise ValueError(f"non-finite {key} in {row['configuration_id']}")
    return value


def geometric_mean(values: list[float]) -> float:
    if not values or any(value <= 0 for value in values):
        raise ValueError("geometric mean requires positive values")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def index_rows(rows: list[dict[str, str]]) -> dict[tuple[str, str, int], dict[str, str]]:
    return {
        (row["family"], row["implementation"], int(row["solve_threads"])): row
        for row in rows
    }


def report_aggregates(
    by_key: dict[tuple[str, str, int], dict[str, str]],
) -> dict[int, dict[str, dict[str, float]]]:
    aggregates: dict[int, dict[str, dict[str, float]]] = {}
    for thread in THREADS:
        aggregates[thread] = {}
        for csv_metric, (record_metric, _) in METRICS.items():
            rust = geometric_mean([as_float(by_key[family, "rust", thread], csv_metric) for family in FAMILIES])
            matlab = geometric_mean([as_float(by_key[family, "matlab", thread], csv_metric) for family in FAMILIES])
            aggregates[thread][record_metric] = {
                "rust": rust,
                "matlab": matlab,
                "ratio": rust / matlab,
            }
        rust_rss = geometric_mean([as_float(by_key[family, "rust", thread], "peak_rss_kb") for family in FAMILIES])
        matlab_rss = geometric_mean([as_float(by_key[family, "matlab", thread], "peak_rss_kb") for family in FAMILIES])
        aggregates[thread]["process_peak_rss"] = {
            "rust": rust_rss,
            "matlab": matlab_rss,
            "ratio": rust_rss / matlab_rss,
        }
    return aggregates


def assert_close(actual: float, expected: float, label: str) -> None:
    if not math.isclose(actual, expected, rel_tol=2e-8, abs_tol=1e-10):
        raise ValueError(f"{label} mismatch: generated {actual}, accepted {expected}")


def cross_check(
    by_key: dict[tuple[str, str, int], dict[str, str]],
    aggregates: dict[int, dict[str, dict[str, float]]],
    record: dict[str, Any],
) -> None:
    for thread in THREADS:
        accepted = record["thread_geometric_means"][str(thread)]
        for metric in (*[value[0] for value in METRICS.values()], "process_peak_rss"):
            generated = aggregates[thread][metric]
            accepted_metric = accepted[metric]
            rust_key = "rust_kib" if metric == "process_peak_rss" else "rust_seconds"
            matlab_key = "matlab_kib" if metric == "process_peak_rss" else "matlab_seconds"
            assert_close(generated["rust"], accepted_metric[rust_key], f"{thread} CPU {metric} Rust")
            assert_close(generated["matlab"], accepted_metric[matlab_key], f"{thread} CPU {metric} MATLAB")
            assert_close(generated["ratio"], accepted_metric["rust_over_matlab"], f"{thread} CPU {metric} ratio")
    for family in FAMILIES:
        accepted = record["family_at_16_threads"][family]
        rust = by_key[family, "rust", 16]
        matlab = by_key[family, "matlab", 16]
        assert_close(as_float(rust, "solver_total_median_s"), accepted["rust_total_seconds"], f"{family} Rust total")
        assert_close(as_float(matlab, "solver_total_median_s"), accepted["matlab_total_seconds"], f"{family} MATLAB total")
        assert_close(as_float(rust, "iterations_median"), accepted["rust_iterations"], f"{family} Rust iterations")
        assert_close(as_float(matlab, "iterations_median"), accepted["matlab_iterations"], f"{family} MATLAB iterations")


def configure_plot_style() -> None:
    plt.rcParams.update(
        {
            "font.size": 8.5,
            "axes.titlesize": 9.5,
            "axes.labelsize": 8.5,
            "legend.fontsize": 7.5,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.22,
            "grid.linewidth": 0.6,
            "lines.linewidth": 1.8,
            "lines.markersize": 4.5,
            "figure.dpi": 150,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.formatter.useoffset": False,
        }
    )


def cpu_axis(ax: plt.Axes) -> None:
    ax.set_xscale("log", base=2)
    ax.set_xticks(THREADS, [str(thread) for thread in THREADS])
    ax.set_xlim(0.85, 38)
    ax.set_xlabel("Application CPUs")


def plain_linear_y_axis(ax: plt.Axes, data_max: float, target_ticks: int = 6) -> None:
    """Use a rounded zero-based scale with ordinary decimal tick labels."""
    if not math.isfinite(data_max) or data_max <= 0:
        raise ValueError(f"invalid y-axis maximum: {data_max}")
    raw_step = data_max * 1.08 / target_ticks
    magnitude = 10 ** math.floor(math.log10(raw_step))
    normalized = raw_step / magnitude
    step = next(candidate * magnitude for candidate in (1, 2, 2.5, 5, 10) if candidate >= normalized)
    upper = math.ceil(data_max * 1.05 / step) * step
    decimals = max(0, -math.floor(math.log10(step)))
    ax.set_ylim(0, upper)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(step))
    ax.yaxis.set_major_formatter(mticker.StrMethodFormatter(f"{{x:.{decimals}f}}"))


def ratio_y_axis(ax: plt.Axes, upper: float) -> None:
    ax.set_ylim(0, upper)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(0.2))
    ax.yaxis.set_major_formatter(mticker.StrMethodFormatter("{x:.1f}"))


def save_figure(fig: plt.Figure, filename: str) -> None:
    FIGURE_ROOT.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(
        FIGURE_ROOT / filename,
        bbox_inches="tight",
        metadata={"Creator": "CMG current benchmark report generator", "CreationDate": None, "ModDate": None},
    )
    plt.close(fig)


def plot_stage_timings(aggregates: dict[int, dict[str, dict[str, float]]]) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(7.45, 5.25), sharex=True)
    for ax, (_, (metric, title)) in zip(axes.flat, METRICS.items()):
        panel_values = []
        for implementation in ("rust", "matlab"):
            style = IMPLEMENTATION_STYLES[implementation]
            values = [aggregates[thread][metric][implementation] for thread in THREADS]
            panel_values.extend(values)
            ax.plot(THREADS, values, **style)
        ax.set_title(title)
        ax.set_ylabel("Geometric-mean seconds")
        plain_linear_y_axis(ax, max(panel_values))
        cpu_axis(ax)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False, bbox_to_anchor=(0.5, 1.015))
    fig.suptitle("One-million-vertex latency: Rust and MATLAB on shared axes", y=1.055, fontsize=11)
    save_figure(fig, "current_stage_timings.pdf")


def plot_stage_ratios(
    by_key: dict[tuple[str, str, int], dict[str, str]],
    aggregates: dict[int, dict[str, dict[str, float]]],
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(7.45, 5.35), sharex=True)
    for ax, (csv_metric, (metric, title)) in zip(axes.flat, METRICS.items()):
        for family in FAMILIES:
            ratios = [
                as_float(by_key[family, "rust", thread], csv_metric)
                / as_float(by_key[family, "matlab", thread], csv_metric)
                for thread in THREADS
            ]
            ax.plot(
                THREADS,
                ratios,
                color=FAMILY_COLORS[family],
                marker="o",
                linewidth=1.25,
                markersize=3.5,
                label=FAMILY_LABELS[family],
            )
        ax.plot(
            THREADS,
            [aggregates[thread][metric]["ratio"] for thread in THREADS],
            color="#111111",
            marker="D",
            linewidth=2.35,
            markersize=4,
            label="Geometric mean",
            zorder=10,
        )
        ax.axhline(1.0, color="#666666", linestyle=":", linewidth=1)
        ratio_y_axis(ax, 1.2)
        ax.set_title(title)
        ax.set_ylabel("Rust / MATLAB")
        cpu_axis(ax)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 1.04))
    fig.suptitle("Stage ratios by graph family (below 1 favors Rust)", y=1.095, fontsize=11)
    save_figure(fig, "current_stage_ratios.pdf")


def plot_family_totals(by_key: dict[tuple[str, str, int], dict[str, str]]) -> None:
    fig, axes = plt.subplots(2, 3, figsize=(7.45, 5.45))
    for ax, family in zip(axes.flat, FAMILIES):
        panel_max = 0.0
        for implementation in ("rust", "matlab"):
            style = IMPLEMENTATION_STYLES[implementation]
            local = [by_key[family, implementation, thread] for thread in THREADS]
            values = [as_float(row, "solver_total_median_s") for row in local]
            lower = [value - as_float(row, "solver_total_bootstrap_low_s") for value, row in zip(values, local)]
            upper = [as_float(row, "solver_total_bootstrap_high_s") - value for value, row in zip(values, local)]
            panel_max = max(panel_max, *(value + error for value, error in zip(values, upper)))
            ax.errorbar(
                THREADS,
                values,
                yerr=[lower, upper],
                capsize=2,
                elinewidth=0.8,
                **style,
            )
        ax.set_title(FAMILY_LABELS[family])
        ax.set_ylabel("Setup + plan + solve (s)")
        plain_linear_y_axis(ax, panel_max)
        cpu_axis(ax)
    axes.flat[-1].set_axis_off()
    handles, labels = axes.flat[0].get_legend_handles_labels()
    axes.flat[-1].legend(handles, labels, loc="center", frameon=False, fontsize=9)
    axes.flat[-1].text(
        0.5,
        0.3,
        "Points: medians of 7 measured runs\nBars: per-implementation bootstrap intervals",
        ha="center",
        va="center",
        transform=axes.flat[-1].transAxes,
        fontsize=8,
    )
    fig.suptitle("Setup-plus-solve latency by graph family", y=1.015, fontsize=11)
    save_figure(fig, "current_family_totals.pdf")


def plot_family_total_ratios(
    by_key: dict[tuple[str, str, int], dict[str, str]],
    aggregates: dict[int, dict[str, dict[str, float]]],
) -> None:
    fig, ax = plt.subplots(figsize=(7.35, 3.65))
    for family in FAMILIES:
        ratios = [
            as_float(by_key[family, "rust", thread], "solver_total_median_s")
            / as_float(by_key[family, "matlab", thread], "solver_total_median_s")
            for thread in THREADS
        ]
        ax.plot(
            THREADS,
            ratios,
            color=FAMILY_COLORS[family],
            marker="o",
            linewidth=1.5,
            label=FAMILY_LABELS[family],
        )
    ax.plot(
        THREADS,
        [aggregates[thread]["solver_total"]["ratio"] for thread in THREADS],
        color="#111111",
        marker="D",
        linewidth=2.6,
        label="Geometric mean",
        zorder=10,
    )
    ax.axhline(1.0, color="#666666", linestyle=":", linewidth=1)
    ratio_y_axis(ax, 1.0)
    ax.set_ylabel("Rust / MATLAB setup-plus-solve time")
    cpu_axis(ax)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.25), ncol=3, frameon=False)
    ax.set_title("Direct ratio view: below 1 favors Rust")
    save_figure(fig, "current_family_total_ratios.pdf")


def plot_memory(
    by_key: dict[tuple[str, str, int], dict[str, str]],
    aggregates: dict[int, dict[str, dict[str, float]]],
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.45, 3.6))
    left, right = axes
    absolute_values = []
    for implementation in ("rust", "matlab"):
        style = IMPLEMENTATION_STYLES[implementation]
        values = [aggregates[thread]["process_peak_rss"][implementation] / 1024**2 for thread in THREADS]
        absolute_values.extend(values)
        left.plot(THREADS, values, **style)
    left.set_ylabel("Geometric-mean process peak RSS (GiB)")
    left.set_title("Absolute memory")
    plain_linear_y_axis(left, max(absolute_values), target_ticks=7)
    cpu_axis(left)
    left.legend(frameon=False)
    for family in FAMILIES:
        ratios = [
            as_float(by_key[family, "rust", thread], "peak_rss_kb")
            / as_float(by_key[family, "matlab", thread], "peak_rss_kb")
            for thread in THREADS
        ]
        right.plot(
            THREADS,
            ratios,
            color=FAMILY_COLORS[family],
            marker="o",
            linewidth=1.25,
            markersize=3.5,
            label=FAMILY_LABELS[family],
        )
    right.plot(
        THREADS,
        [aggregates[thread]["process_peak_rss"]["ratio"] for thread in THREADS],
        color="#111111",
        marker="D",
        linewidth=2.35,
        markersize=4,
        label="Geometric mean",
        zorder=10,
    )
    right.axhline(1.0, color="#666666", linestyle=":", linewidth=1)
    ratio_y_axis(right, 1.0)
    right.set_ylabel("Rust / MATLAB peak RSS")
    right.set_title("Ratio view (below 1 favors Rust)")
    cpu_axis(right)
    left.set_xlabel("")
    right.set_xlabel("")
    fig.supxlabel("Application CPUs", y=-0.015, fontsize=8.5)
    handles, labels = right.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 1.04))
    fig.suptitle("Process peak resident memory", y=1.13, fontsize=11)
    save_figure(fig, "current_memory.pdf")


def plot_iterations(by_key: dict[tuple[str, str, int], dict[str, str]]) -> None:
    fig, ax = plt.subplots(figsize=(7.35, 3.45))
    positions = list(range(len(FAMILIES)))
    width = 0.36
    rust_values = [as_float(by_key[family, "rust", 16], "iterations_median") for family in FAMILIES]
    matlab_values = [as_float(by_key[family, "matlab", 16], "iterations_median") for family in FAMILIES]
    ax.bar(
        [position - width / 2 for position in positions],
        rust_values,
        width,
        color=IMPLEMENTATION_STYLES["rust"]["color"],
        label="Rust",
    )
    ax.bar(
        [position + width / 2 for position in positions],
        matlab_values,
        width,
        color=IMPLEMENTATION_STYLES["matlab"]["color"],
        label="MATLAB",
    )
    ax.set_xticks(positions, [FAMILY_LABELS[family].replace("Worker-firm ", "WF ") for family in FAMILIES])
    ax.tick_params(axis="x", rotation=12)
    ax.set_ylabel("PCG iterations")
    ax.set_title("Native stopping rules at 16 application CPUs")
    plain_linear_y_axis(ax, max(*rust_values, *matlab_values), target_ticks=7)
    ax.legend(frameon=False, ncol=2)
    save_figure(fig, "current_iterations.pdf")


def tex_escape(text: str) -> str:
    return (
        text.replace("\\", r"\textbackslash{}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("$", r"\$")
        .replace("#", r"\#")
        .replace("_", r"\_")
        .replace("{", r"\{")
        .replace("}", r"\}")
    )


def write_tex(
    by_key: dict[tuple[str, str, int], dict[str, str]],
    aggregates: dict[int, dict[str, dict[str, float]]],
    record: dict[str, Any],
) -> None:
    environment = record["environment"]
    validation = record["validation"]
    lines = [
        "% Generated by benchmarks/report/generate_current_report.py.",
        rf"\newcommand{{\ReportRunId}}{{{tex_escape(record['run_id'])}}}",
        rf"\newcommand{{\ReportJobId}}{{{tex_escape(record['job_id'])}}}",
        rf"\newcommand{{\ReportSourceCommit}}{{{tex_escape(record['source_commit'])}}}",
        rf"\newcommand{{\ReportSourceArchive}}{{{tex_escape(record['source_archive_sha256'])}}}",
        rf"\newcommand{{\ReportRustBinary}}{{{tex_escape(record['rust_binary_sha256'])}}}",
        rf"\newcommand{{\ReportUpstreamCommit}}{{{tex_escape(record['upstream_commit'])}}}",
        rf"\newcommand{{\ReportProtocol}}{{{tex_escape(record['protocol_version'])}}}",
        rf"\newcommand{{\ReportRustVersion}}{{{tex_escape(environment['rustc'])}}}",
        rf"\newcommand{{\ReportMatlabVersion}}{{{tex_escape(environment['matlab'])}}}",
        rf"\newcommand{{\ReportCpuModel}}{{{tex_escape(environment['cpu'])}}}",
        rf"\newcommand{{\ReportWarmups}}{{{environment['warmups']}}}",
        rf"\newcommand{{\ReportRepetitions}}{{{environment['measured_repetitions']}}}",
        rf"\newcommand{{\ReportTolerance}}{{{environment['tolerance']:.0e}}}",
        rf"\newcommand{{\ReportConfigurations}}{{{validation['configurations']}}}",
        rf"\newcommand{{\ReportWarnings}}{{{validation['warnings']}}}",
        rf"\newcommand{{\ReportMaxRustBackward}}{{{validation['maximum_rust_backward_error']:.2e}}}",
        rf"\newcommand{{\ReportMaxRustResidual}}{{{validation['maximum_rust_independent_relative_residual']:.2e}}}",
        rf"\newcommand{{\ReportMaxMatlabResidual}}{{{validation['maximum_matlab_native_relative_residual']:.2e}}}",
        rf"\newcommand{{\ReportSixteenSetupRatio}}{{{aggregates[16]['preconditioner_setup']['ratio']:.3f}}}",
        rf"\newcommand{{\ReportSixteenApplyRatio}}{{{aggregates[16]['preconditioner_apply']['ratio']:.3f}}}",
        rf"\newcommand{{\ReportSixteenPcgRatio}}{{{aggregates[16]['pcg_solve']['ratio']:.3f}}}",
        rf"\newcommand{{\ReportSixteenTotalRatio}}{{{aggregates[16]['solver_total']['ratio']:.3f}}}",
        rf"\newcommand{{\ReportSixteenRssRatio}}{{{aggregates[16]['process_peak_rss']['ratio']:.3f}}}",
        rf"\newcommand{{\ReportSixteenRustTotal}}{{{aggregates[16]['solver_total']['rust']:.3f}}}",
        rf"\newcommand{{\ReportSixteenMatlabTotal}}{{{aggregates[16]['solver_total']['matlab']:.3f}}}",
        rf"\newcommand{{\ReportRustSpeedupSixteen}}{{{aggregates[1]['solver_total']['rust'] / aggregates[16]['solver_total']['rust']:.2f}}}",
        rf"\newcommand{{\ReportMatlabSpeedupSixteen}}{{{aggregates[1]['solver_total']['matlab'] / aggregates[16]['solver_total']['matlab']:.2f}}}",
        rf"\newcommand{{\ReportRustThirtyTwoPenalty}}{{{aggregates[32]['solver_total']['rust'] / aggregates[16]['solver_total']['rust']:.2f}}}",
        rf"\newcommand{{\ReportMatlabThirtyTwoPenalty}}{{{aggregates[32]['solver_total']['matlab'] / aggregates[16]['solver_total']['matlab']:.2f}}}",
        r"\newcommand{\ReportAggregateTable}{%",
        r"\begin{tabular}{rrrrrr}",
        r"\toprule",
        r"CPUs & Setup & Apply & PCG & Total & Peak RSS \\",
        r"\midrule",
    ]
    for thread in THREADS:
        values = aggregates[thread]
        lines.append(
            f"{thread} & {values['preconditioner_setup']['ratio']:.3f} & "
            f"{values['preconditioner_apply']['ratio']:.3f} & {values['pcg_solve']['ratio']:.3f} & "
            f"{values['solver_total']['ratio']:.3f} & {values['process_peak_rss']['ratio']:.3f} \\\\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}%",
            r"}",
            r"\newcommand{\ReportFamilyTable}{%",
            r"\begin{tabular}{lrrrrrr}",
            r"\toprule",
            r"Family & Edges & Rust (s) & MATLAB (s) & R/M & Rust it. & MATLAB it. \\",
            r"\midrule",
        ]
    )
    for family in FAMILIES:
        rust = by_key[family, "rust", 16]
        matlab = by_key[family, "matlab", 16]
        rust_total = as_float(rust, "solver_total_median_s")
        matlab_total = as_float(matlab, "solver_total_median_s")
        label = FAMILY_LABELS[family].replace("-", r"-")
        lines.append(
            f"{label} & {int(as_float(rust, 'canonical_edges')):,} & {rust_total:.3f} & "
            f"{matlab_total:.3f} & {rust_total / matlab_total:.3f} & "
            f"{as_float(rust, 'iterations_median'):.0f} & {as_float(matlab, 'iterations_median'):.0f} \\\\"
        )
    lines.extend([r"\bottomrule", r"\end{tabular}%", r"}"])
    (DATA_ROOT / "current_results.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    rows, record = load_inputs()
    by_key = index_rows(rows)
    aggregates = report_aggregates(by_key)
    cross_check(by_key, aggregates, record)
    configure_plot_style()
    plot_stage_timings(aggregates)
    plot_stage_ratios(by_key, aggregates)
    plot_family_totals(by_key)
    plot_family_total_ratios(by_key, aggregates)
    plot_memory(by_key, aggregates)
    plot_iterations(by_key)
    write_tex(by_key, aggregates, record)
    csv_hash = hashlib.sha256(CSV_PATH.read_bytes()).hexdigest()
    print(
        "CMG_CURRENT_REPORT_INPUTS_SUCCESS "
        f"configurations={len(rows)} figures=6 csv_sha256={csv_hash}"
    )


if __name__ == "__main__":
    main()
