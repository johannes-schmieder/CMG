"""Expand frozen SCC2 task rows into exact benchmark configurations."""

from __future__ import annotations

from typing import Any


def configuration_id(config: dict[str, Any]) -> str:
    fields = (
        config["implementation"],
        config["strategy"],
        config.get("variant", "fresh-all"),
        f"h{config['hierarchy_threads']}",
        f"p{config['plan_threads']}",
        f"s{config['solve_threads']}",
        f"r{config['rhs_count']}",
        f"tol{config['tolerance']:.0e}",
        config.get("placement", "current"),
        config.get("stage_stop", "solve"),
        f"mrep{config.get('process_repetition', 0)}",
    )
    return "-".join(str(value).replace("+", "").replace(".", "p") for value in fields)


def expand(task: dict[str, Any]) -> list[dict[str, Any]]:
    experiment = task["experiment"]
    rows: list[dict[str, Any]] = []

    def add(
        implementation: str,
        hierarchy_threads: int,
        plan_threads: int,
        solve_threads: int,
        strategy: str,
        *,
        tolerance: float | None = None,
        rhs_count: int | None = None,
        variant: str = "fresh-all",
        placement: str | None = None,
        stage_stop: str | None = None,
    ) -> None:
        row = {
            "implementation": implementation,
            "hierarchy_threads": hierarchy_threads,
            "plan_threads": plan_threads,
            "solve_threads": solve_threads,
            "strategy": strategy,
            "variant": variant,
            "rhs_count": rhs_count if rhs_count is not None else int(task["rhs_count"]),
            "tolerance": tolerance if tolerance is not None else float(task["tolerance"]),
            "placement": placement or f"thread{solve_threads}",
        }
        if stage_stop:
            row["stage_stop"] = stage_stop
        if "process_repetition" in task:
            row["process_repetition"] = int(task["process_repetition"])
        row["configuration_id"] = configuration_id(row)
        rows.append(row)

    if experiment in ("smoke", "baseline"):
        for implementation in task["implementations"]:
            for threads in task["threads"]:
                add(implementation, threads, threads if implementation == "rust" else 0, threads,
                    "auto" if implementation == "rust" else "native-sequential")
    elif experiment == "routing":
        for threads_text, strategies in task["strategies"].items():
            threads = int(threads_text)
            for strategy in strategies:
                add("rust", threads, threads, threads, strategy)
    elif experiment == "reuse":
        for hierarchy_threads in task["hierarchy_threads"]:
            add("rust", hierarchy_threads, 32, 32, "planned", variant="fresh-all")
        best_setup = min(task["hierarchy_threads"])
        for solve_threads in task["solve_threads"]:
            for variant in task["variants"]:
                strategy = "serial" if variant == "serial-no-plan" else "planned"
                candidate = (best_setup, solve_threads, solve_threads, strategy, variant)
                existing = {
                    (
                        row["hierarchy_threads"], row["plan_threads"], row["solve_threads"],
                        row["strategy"], row["variant"],
                    )
                    for row in rows
                }
                if candidate not in existing:
                    add("rust", best_setup, solve_threads, solve_threads, strategy, variant=variant)
    elif experiment == "numa":
        thread_by_placement = {
            "numa8-compact": 8,
            "socket16-compact": 16,
            "sockets16-split": 16,
            "linear32": 32,
            "numa32-spread": 32,
            "numa32-interleave": 32,
            "linear32-parallel-touch": 32,
        }
        for implementation in task["implementations"]:
            for placement in task["placements"]:
                threads = thread_by_placement[placement]
                if implementation == "matlab" and placement not in (
                    "socket16-compact", "sockets16-split", "linear32"
                ):
                    continue
                add(implementation, threads, threads if implementation == "rust" else 0, threads,
                    "auto" if implementation == "rust" else "native-sequential", placement=placement)
    elif experiment == "memory":
        implementation = task["implementations"][0]
        threads = int(task["threads"][0])
        for stage in task["stages"]:
            add(implementation, threads, threads if implementation == "rust" else 0, threads,
                "auto" if implementation == "rust" else "native-sequential", stage_stop=stage)
    elif experiment == "accuracy":
        for implementation in task["implementations"]:
            for threads in task["threads"]:
                for tolerance in task["tolerances"]:
                    add(implementation, threads, threads if implementation == "rust" else 0, threads,
                        "auto" if implementation == "rust" else "native-sequential", tolerance=tolerance)
    elif experiment == "batch":
        threads = int(task["threads"][0])
        for strategy in task["rust_strategies"]:
            add("rust", threads, threads, threads, strategy)
        for strategy in task["matlab_strategies"]:
            add("matlab", threads, 0, threads, strategy)
    elif experiment == "matched-edge":
        for threads in task["rust_threads"]:
            add("rust", threads, threads, threads, "auto")
        for threads in task["matlab_threads"]:
            add("matlab", threads, 0, threads, "native-sequential")
    else:
        raise ValueError(f"unsupported experiment {experiment}")

    identifiers = [row["configuration_id"] for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"duplicate configurations in task {task['task_id']}")
    return rows
