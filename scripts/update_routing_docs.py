#!/usr/bin/env python3
"""Refresh live performance documentation from a full-PCG routing record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PLAN_HEADING = "### Full certified PCG canonical-edge routing checkpoint — 2026-08-23"


def replace_between(text: str, start_heading: str, end_heading: str, body: str) -> str:
    start = text.index(start_heading)
    end = text.index(end_heading, start)
    return text[:start] + body.rstrip() + "\n\n" + text[end:]


def routing_table(data: dict[str, Any]) -> str:
    rows = [
        "| Case | Input edges | Canonical edges | Auto route | Planned speedup | Iterations |",
        "|---|---:|---:|---|---:|---:|",
    ]
    for name, case in data["cases"].items():
        rows.append(
            f"| {name} | {case.get('input_edges', case['edges']):,} | "
            f"{case['edges']:,} | {case['auto_execution']} | "
            f"{case['speedup']:.3f}x | "
            f"{case['serial_iterations']} / {case['planned_iterations']} |"
        )
    return "\n".join(rows)


def plan_checkpoint(data: dict[str, Any]) -> str:
    mismatches = ", ".join(data.get("route_mismatches", [])) or "none"
    failures = ", ".join(data.get("numerical_failures", [])) or "none"
    return f"""{PLAN_HEADING}

- The default prepared single-RHS router uses planned execution from
  **350,000 canonical retained edges** when a parallel hierarchy operator
  exists and the executor has more than one thread.
- Tested routing-matrix SHA: `{data['tested_sha']}`; run `{data['run_id']}`.
- Status: `{data['status']}`; numerical failures: `{failures}`; routing
  mismatches: `{mismatches}`.
- Maximum scaled serial/planned solution difference:
  `{data['maximum_scaled_difference']:.3e}`.
- Geometric planned speedup across the matrix:
  `{data['geometric_speedup']:.3f}x`.

{routing_table(data)}

- Machine-readable evidence:
  `.ci/performance/full-pcg-routing-latest.json`.
"""


def update_plan(path: Path, data: dict[str, Any]) -> None:
    text = path.read_text()
    checkpoint = plan_checkpoint(data).rstrip() + "\n\n"
    next_heading = "## Current next action"
    if PLAN_HEADING in text:
        start = text.index(PLAN_HEADING)
        end = text.index(next_heading, start)
        text = text[:start] + checkpoint + text[end:]
    else:
        text = text.replace(next_heading, checkpoint + next_heading, 1)

    hot_spots = """## Current hot spots

1. Planned PCG still performs dot products, Euclidean norms, Krylov vector
   updates, component centering, and residual reconstruction mostly serially.
2. Coarse contraction remains the dominant measured setup phase on
   worker–firm and dense worker–firm graphs.
3. Controlled 8-, 16-, and 32-thread/high-memory behavior is still unknown on
   ordinary hosted runners.
4. Further index compaction or duplicated sparse metadata must demonstrate an
   end-to-end memory benefit rather than only a local structure-size benefit.

"""
    text = replace_between(
        text,
        "## Current hot spots",
        "## Rejected or deferred experiments",
        hot_spots,
    )

    next_actions = """## Current next action

1. Add a read-only planned-PCG phase profiler that separately measures finest
   matvec, CMG application, reductions/norms, vector updates, component
   centering, and residual certification while preserving the certified result.
2. Prototype fixed-chunk deterministic parallel reductions and vector updates
   only if that profile shows material outer-loop headroom; gate retention on
   full certified solve time and numerical agreement.
3. Continue coarse-contraction profiling, especially reusable buffers and
   routed temporary capacity after the retained packed-key/unstable-sort work.
4. Obtain controlled 8–32-thread and high-memory evidence on a larger or
   self-hosted runner.
"""
    start = text.index(next_heading)
    text = text[:start] + next_actions
    path.write_text(text)


def update_status(path: Path, data: dict[str, Any]) -> None:
    text = path.read_text()
    mismatches = ", ".join(data.get("route_mismatches", [])) or "none"
    failures = ", ".join(data.get("numerical_failures", [])) or "none"
    section = f"""## Qualified full-PCG router

The default prepared single-RHS router uses planned execution from **350,000
canonical retained edges** when a parallel hierarchy operator exists and more
than one thread is available. The threshold remains overridable through
`ParallelPcgPolicy`.

- Routing-matrix SHA: `{data['tested_sha']}`; run `{data['run_id']}`.
- Status: `{data['status']}`.
- Numerical failures: `{failures}`.
- Routing mismatches: `{mismatches}`.
- Maximum scaled serial/planned solution difference:
  `{data['maximum_scaled_difference']:.3e}`.
- Geometric planned speedup: `{data['geometric_speedup']:.3f}x`.

{routing_table(data)}

These are directional measurements from an ordinary four-logical-CPU hosted
runner, not a claim about 8–32-core or NUMA scaling.

"""
    text = replace_between(
        text,
        "## Qualified full-PCG router",
        "## Other retained evidence",
        section,
    )
    path.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--routing", type=Path, required=True)
    args = parser.parse_args()

    data = json.loads(args.routing.read_text())
    if data.get("status") != "success":
        raise SystemExit("refusing to publish failed routing evidence")
    if data.get("numerical_failures"):
        raise SystemExit("refusing to publish routing evidence with numerical failures")

    update_plan(args.candidate / "PERFORMANCE_PLAN.md", data)
    update_status(args.candidate / "PERFORMANCE_STATUS.md", data)


if __name__ == "__main__":
    main()
