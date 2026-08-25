#!/usr/bin/env python3
"""Run one frozen SCC2 task inside an SGE whole-node allocation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matrix import expand  # noqa: E402
from protocol import canonical_json, read_jsonl, sha256_file, validate_run_id  # noqa: E402


PROJECT_ROOT = Path("/projectnb/welfgr/cmg-benchmarks")
UPSTREAM_COMMIT = "19752fc102f8cae8e34f66457bfaccb1aaa60375"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def read_text(path: Path) -> str:
    return path.read_text().strip()


def run_checked(command: list[str], *, env: dict[str, str], stdout: Path, time_file: Path) -> None:
    stdout.parent.mkdir(parents=True, exist_ok=True)
    with stdout.open("wb") as handle:
        completed = subprocess.run(
            ["/usr/bin/time", "-v", "-o", str(time_file), *command],
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if completed.returncode:
        raise RuntimeError(f"command failed with {completed.returncode}; see {stdout}")


def time_receipt(path: Path) -> dict[str, Any]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if ": " in line:
            key, value = line.strip().split(": ", 1)
            values[key] = value
    numeric = {
        "user_seconds": float(values.get("User time (seconds)", "nan")),
        "system_seconds": float(values.get("System time (seconds)", "nan")),
        "peak_rss_kb": int(values.get("Maximum resident set size (kbytes)", "0")),
        "major_faults": int(values.get("Major (requiring I/O) page faults", "0")),
        "minor_faults": int(values.get("Minor (reclaiming a frame) page faults", "0")),
        "voluntary_context_switches": int(values.get("Voluntary context switches", "0")),
        "involuntary_context_switches": int(values.get("Involuntary context switches", "0")),
    }
    return {"raw": values, **numeric}


def placement_for(config: dict[str, Any], topology: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    name = config["placement"]
    placement = topology["placements"][name]
    prefix = ["numactl", f"--physcpubind={placement['cpu_list']}"]
    memory_policy = "current"
    first_touch = "serial"
    if name == "numa32-interleave":
        node_list = ",".join(str(value) for value in placement["nodes"])
        prefix.append(f"--interleave={node_list}")
        memory_policy = f"interleave:{node_list}"
    if name == "linear32-parallel-touch":
        first_touch = "unsupported-requested-parallel"
    metadata = {
        "CMG_PLACEMENT": name,
        "CMG_CPU_LIST": placement["cpu_list"],
        "CMG_SOCKET_LIST": ",".join(str(value) for value in placement["sockets"]),
        "CMG_NUMA_LIST": ",".join(str(value) for value in placement["nodes"]),
        "CMG_MEMORY_POLICY": memory_policy,
        "CMG_FIRST_TOUCH": first_touch,
    }
    return prefix, metadata


def enrich(
    raw: Path,
    task: dict[str, Any],
    config: dict[str, Any],
    input_dir: Path,
    time_file: Path,
    sequence: int,
) -> None:
    result = json.loads(raw.read_text())
    input_hashes = {
        name: sha256_file(input_dir / name)
        for name in ("graph.bin", "rhs.bin", "truth.bin", "metadata.json")
    }
    result.update(
        configuration_id=config["configuration_id"],
        variant=config["variant"],
        stage_stop=config.get("stage_stop", "solve"),
        execution_sequence=sequence,
        input_hashes=input_hashes,
        host=socket.gethostname(),
        upstream_commit=UPSTREAM_COMMIT,
        process_accounting=time_receipt(time_file),
    )
    result["process_accounting"]["peak_rss_kb_driver"] = result.get("memory", {}).get("peak_rss_kb")
    result["memory"]["peak_rss_kb"] = result["process_accounting"]["peak_rss_kb"]
    raw.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


def enrich_memory(
    raw: Path,
    task: dict[str, Any],
    config: dict[str, Any],
    input_dir: Path,
    time_file: Path,
    sequence: int,
    placement: dict[str, str],
) -> None:
    result = json.loads(raw.read_text())
    result.update(
        configuration_id=config["configuration_id"],
        process_repetition=int(task["process_repetition"]),
        execution_sequence=sequence,
        input_hashes={
            name: sha256_file(input_dir / name)
            for name in ("graph.bin", "rhs.bin", "truth.bin", "metadata.json")
        },
        host=socket.gethostname(),
        upstream_commit=UPSTREAM_COMMIT,
        process_accounting=time_receipt(time_file),
        placement={
            "mode": placement["CMG_PLACEMENT"],
            "cpu_list": placement["CMG_CPU_LIST"],
            "socket_list": placement["CMG_SOCKET_LIST"],
            "numa_node_list": placement["CMG_NUMA_LIST"],
            "memory_policy": placement["CMG_MEMORY_POLICY"],
            "first_touch_policy": placement["CMG_FIRST_TOUCH"],
        },
    )
    result["peak_rss_kb_driver"] = result["peak_rss_kb"]
    result["peak_rss_kb"] = result["process_accounting"]["peak_rss_kb"]
    raw.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("task_file", type=Path)
    parser.add_argument("task_id", type=int)
    args = parser.parse_args()
    validate_run_id(args.run_id)
    tasks = read_jsonl(args.task_file)
    require(1 <= args.task_id <= len(tasks), "task id is outside the manifest")
    task = tasks[args.task_id - 1]
    require(task["task_id"] == args.task_id, "task id does not match its line")

    run_root = PROJECT_ROOT / "runs" / args.run_id
    source_commit = read_text(run_root / "manifests/source-commit.txt")
    archive_sha = read_text(run_root / "manifests/source-archive-sha256.txt")
    environment_id = read_text(run_root / "manifests/environment-id.txt")
    code_root = PROJECT_ROOT / "code-b2" / source_commit
    diagnostics = code_root / "benchmarks/target/release/scc2-diagnostics"
    memory_diagnostics = code_root / "benchmarks/target/release/scc2-memory"
    generator = code_root / "benchmarks/target/release/scc-benchmark"
    require(
        diagnostics.is_file() and memory_diagnostics.is_file() and generator.is_file(),
        "benchmark binaries are missing",
    )

    tmp_root = Path(os.environ["TMPDIR"]) / f"cmg-scc2-{args.task_id}"
    input_dir = tmp_root / "input"
    work_output = tmp_root / "output"
    work_receipts = tmp_root / "receipts"
    for path in (input_dir, work_output, work_receipts):
        path.mkdir(parents=True, exist_ok=True)
    task_output = run_root / "output" / f"task-{args.task_id}"
    task_receipts = run_root / "receipts" / f"task-{args.task_id}"
    task_logs = run_root / "logs" / f"task-{args.task_id}"
    for path in (task_output, task_receipts, task_logs):
        path.mkdir(parents=True, exist_ok=True)

    generate_log = task_logs / "generate.log"
    with generate_log.open("wb") as handle:
        completed = subprocess.run(
            [str(generator), "generate", task["family"], str(task["vertices"]), str(task["rhs_count"]), str(input_dir)],
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
        )
    require(completed.returncode == 0, f"fixture generation failed; see {generate_log}")
    hashes = {path.name: sha256_file(path) for path in sorted(input_dir.iterdir())}
    (work_receipts / "input-sha256.json").write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n")

    topology_path = work_receipts / "topology.json"
    subprocess.run([sys.executable, str(code_root / "benchmarks/scc2/topology.py"), str(topology_path)], check=True)
    topology = json.loads(topology_path.read_text())
    if task["experiment"] == "smoke":
        capabilities_path = work_receipts / "capabilities.json"
        subprocess.run(
            [sys.executable, str(code_root / "benchmarks/scc2/capabilities.py"), str(capabilities_path)],
            check=True,
        )
        shutil.copy2(capabilities_path, task_receipts / "capabilities.json")
    configs = expand(task)
    offset = int(hashlib.sha256(f"{args.run_id}:{args.task_id}".encode()).hexdigest()[:8], 16) % len(configs)
    configs = configs[offset:] + configs[:offset]
    base_env = dict(os.environ)
    base_env.update(
        CMG_RUN_ID=args.run_id,
        CMG_TASK_ID=str(args.task_id),
        CMG_EXPERIMENT=task["experiment"],
        CMG_ENVIRONMENT_ID=environment_id,
        CMG_SOURCE_COMMIT=source_commit,
        CMG_SOURCE_ARCHIVE_SHA256=archive_sha,
        CMG_FAMILY=task["family"],
        CMG_VERTICES=str(task["vertices"]),
        OMP_PROC_BIND="close",
        OMP_PLACES="cores",
    )
    mex_hash = read_text(run_root / "manifests/mex-binary-sha256.txt")
    base_env["CMG_MEX_BINARY_SHA256"] = mex_hash
    matlab_driver = code_root / "benchmarks/matlab"
    upstream = PROJECT_ROOT / f"upstream/cmg-solver-{UPSTREAM_COMMIT}/matlab/cmg"

    completed_configs: list[str] = []
    for sequence, config in enumerate(configs, start=1):
        stem = config["configuration_id"]
        raw = work_output / f"{stem}.json"
        time_file = work_receipts / f"{stem}.time"
        log_file = task_logs / f"{stem}.log"
        prefix, placement_env = placement_for(config, topology)
        command_env = dict(base_env)
        command_env.update(placement_env)
        command_env["RAYON_NUM_THREADS"] = str(config["solve_threads"])
        command_env["CMG_VARIANT"] = config["variant"]
        command_env["CMG_STAGE_STOP"] = config.get("stage_stop", "solve")
        if task["experiment"] == "memory" and config["implementation"] == "rust":
            command = [
                *prefix,
                str(memory_diagnostics), str(input_dir), str(config["solve_threads"]),
                config["stage_stop"], str(config["rhs_count"]), str(raw),
            ]
        elif config["implementation"] == "rust":
            command = [
                *prefix,
                str(diagnostics), "run", str(input_dir),
                str(config["hierarchy_threads"]), str(config["plan_threads"]), str(config["solve_threads"]),
                config["strategy"], config["variant"], str(config["rhs_count"]),
                f"{config['tolerance']:.17g}", str(task["warmups"]), str(task["repetitions"]), str(raw),
            ]
        else:
            command_env.update(
                CMG_INPUT_DIR=str(input_dir),
                CMG_THREADS=str(config["solve_threads"]),
                CMG_REPETITIONS=str(task["repetitions"]),
                CMG_WARMUPS=str(task["warmups"]),
                CMG_RHS_COUNT=str(config["rhs_count"]),
                CMG_TOLERANCE=f"{config['tolerance']:.17g}",
                CMG_STRATEGY=config["strategy"],
                CMG_OUTPUT_FILE=str(raw),
                CMG_UPSTREAM_DIR=str(upstream),
            )
            matlab_entrypoint = (
                "scc2_memory_from_env"
                if task["experiment"] == "memory"
                else "scc2_diagnostics_from_env"
            )
            command = [*prefix, "matlab", "-batch", f"addpath('{matlab_driver}'); {matlab_entrypoint}"]
        run_checked(command, env=command_env, stdout=log_file, time_file=time_file)
        if task["experiment"] == "memory":
            enrich_memory(raw, task, config, input_dir, time_file, sequence, placement_env)
        else:
            enrich(raw, task, config, input_dir, time_file, sequence)
        shutil.copy2(raw, task_output / raw.name)
        shutil.copy2(time_file, task_receipts / time_file.name)
        if task["experiment"] in ("smoke", "baseline"):
            counter_root = work_receipts / "counters"
            counter_root.mkdir(parents=True, exist_ok=True)
            for event_group in ("core", "scheduler", "cache-branch"):
                counter_result = work_output / f"counter-{event_group}-{stem}.json"
                counter_command = list(command)
                counter_env = dict(command_env)
                if config["implementation"] == "rust":
                    counter_command[-3] = "0"
                    counter_command[-2] = "1"
                    counter_command[-1] = str(counter_result)
                else:
                    counter_env["CMG_WARMUPS"] = "0"
                    counter_env["CMG_REPETITIONS"] = "1"
                    counter_env["CMG_OUTPUT_FILE"] = str(counter_result)
                counter_json = counter_root / f"{event_group}-{stem}.json"
                counter_log = task_logs / f"counter-{event_group}-{stem}.log"
                with counter_log.open("wb") as handle:
                    completed = subprocess.run(
                        [
                            sys.executable,
                            str(code_root / "benchmarks/scc2/perf_counters.py"),
                            event_group,
                            str(counter_json),
                            "--",
                            *counter_command,
                        ],
                        env=counter_env,
                        stdout=handle,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                require(completed.returncode == 0, f"counter pass failed; see {counter_log}")
                counter_payload = json.loads(counter_json.read_text())
                for record in counter_payload["records"]:
                    record.update(
                        run_id=args.run_id,
                        task_id=args.task_id,
                        configuration_id=stem,
                        implementation=config["implementation"],
                        family=task["family"],
                        vertices=task["vertices"],
                        solve_threads=config["solve_threads"],
                        strategy=config["strategy"],
                        placement=config["placement"],
                    )
                counter_json.write_text(json.dumps(counter_payload, indent=2, sort_keys=True) + "\n")
                if counter_result.exists():
                    counter_application = json.loads(counter_result.read_text())
                    require(counter_application.get("success") is True, "counter application failed")
                    require(counter_application.get("source_commit") == source_commit, "counter source mismatch")
                destination = task_receipts / "counters"
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copy2(counter_json, destination / counter_json.name)
                for evidence in (counter_json.with_suffix(".perf.csv"), counter_json.with_suffix(".command.log")):
                    if evidence.exists():
                        shutil.copy2(evidence, destination / evidence.name)
        completed_configs.append(stem)

    if task["experiment"] == "routing" and task["family"] in (
        "path", "worker-firm", "dense-worker-firm"
    ):
        auxiliary = task_receipts / "auxiliary-profiles"
        auxiliary.mkdir(parents=True, exist_ok=True)
        scale = int(task["vertices"])
        if task["family"] != "path":
            scale //= 2
        auxiliary_commands = [
            (
                "hierarchy-phases.jsonl",
                [str(code_root / "benchmarks/target/release/hierarchy-phase-profile"), task["family"], str(scale), "5"],
            ),
            (
                "contraction-subphases.jsonl",
                [str(code_root / "benchmarks/target/release/contraction-subphase-profile"), task["family"], str(scale), "5", "comparison"],
            ),
        ]
        for threads in (1, 8, 16, 32):
            auxiliary_commands.append(
                (
                    f"plan-phases-t{threads}.json",
                    [str(code_root / "benchmarks/target/release/plan-phase-profile"), task["family"], str(scale), "5", str(threads)],
                )
            )
        for name, command in auxiliary_commands:
            path = auxiliary / name
            with path.open("wb") as handle:
                completed = subprocess.run(command, env=base_env, stdout=handle, stderr=subprocess.STDOUT, check=False)
            require(completed.returncode == 0, f"auxiliary profile failed: {path}")

    shutil.copy2(topology_path, task_receipts / "topology.json")
    shutil.copy2(work_receipts / "input-sha256.json", task_receipts / "input-sha256.json")
    receipt = {
        "success": True,
        "protocol_version": task["protocol_version"],
        "run_id": args.run_id,
        "task_id": args.task_id,
        "task_sha256": hashlib.sha256((canonical_json(task) + "\n").encode()).hexdigest(),
        "configurations": completed_configs,
        "peak_rss_kb_python": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    temporary = task_receipts / "APPLICATION_SUCCESS.tmp"
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    temporary.replace(task_receipts / "APPLICATION_SUCCESS.json")
    print(f"CMG_SCC2_TASK_SUCCESS task={args.task_id} configurations={len(configs)}")


if __name__ == "__main__":
    main()
