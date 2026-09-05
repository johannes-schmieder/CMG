#!/usr/bin/env python3
"""Apply explicitly nonexclusive application affinity after module initialization."""
import hashlib
import json
import os
from pathlib import Path
import socket

import dispatch_campaign as dc

BINDING_POLICY = dict(binding_source="application", exclusive_cpu=False,
                      cpu_selection="sha256-job-task-modulo-sorted-allowed-v1")


def selected_cpu(environment, initial_cpus):
    dc.require(environment.get("NSLOTS") in (None, "1"), "unexpected raw NSLOTS")
    dc.require(environment.get("PE") in (None, "", "NONE"), "expected a serial allocation")
    dc.require(bool(initial_cpus) and all(type(cpu) is int and cpu >= 0 for cpu in initial_cpus)
               and initial_cpus == sorted(set(initial_cpus)), "invalid allowed CPU mask")
    identifiers = [environment.get(name) for name in ("JOB_ID", "SGE_TASK_ID")]
    dc.require(all(isinstance(value, str) and value.isascii() and value.isdecimal()
                   and int(value) > 0 for value in identifiers), "invalid scheduler identifiers")
    # Spread independent jobs without load probing or choosing a favorable timing.
    # This is NOT an exclusive scheduler-selected CPU, even when NSLOTS is one.
    digest = hashlib.sha256(":".join(identifiers).encode("ascii")).digest()
    return initial_cpus[int.from_bytes(digest, "big") % len(initial_cpus)]


def main():
    run = dc.PROJECT / "runs" / os.environ["CMG_RUN_ID"]
    task_file = Path(os.environ["CMG_TASK_FILE"])
    task_id = int(os.environ["SGE_TASK_ID"])
    rows = dc.manifest(task_file)
    dc.require(1 <= task_id <= len(rows), "task ID outside manifest")
    task = rows[task_id - 1]
    dc.require(task["task_id"] == task_id and task_file ==
               run / "manifests/tasks" / f"{task['experiment']}.jsonl", "wrong task path")
    initial = sorted(os.sched_getaffinity(0))
    raw = {name: os.environ.get(name) for name in ("NSLOTS", "PE", "SGE_BINDING", "JOB_ID", "SGE_TASK_ID")}
    # Emit even on failure so the exact failed operand is observable.
    print("CMG_DISPATCH_SERIAL_ENV " + json.dumps(dict(raw=raw, affinity=initial)), flush=True)
    cpu = selected_cpu(raw, initial)
    os.sched_setaffinity(0, {cpu})
    bound = sorted(os.sched_getaffinity(0))
    dc.require(bound == [cpu], "failed to apply application affinity")
    provenance = dict(raw=raw, initial_cpus=initial, bound_cpus=bound,
                      hostname=socket.gethostname(), job_id=os.environ["JOB_ID"], task=task,
                      binding_policy=BINDING_POLICY,
                      launcher_source_commit=Path(__file__).resolve().parents[2].name)
    dc.exclusive_json(run / "work" / f"launcher-{task['experiment']}-{task_id}.json", provenance)
    # NSLOTS may be absent on serial SCC jobs. This is normalization, not a claim
    # about accounting: accept still independently requires qacct slots=1.
    os.environ["NSLOTS"] = "1"
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    source, _, _, _ = dc.identity(run)
    runner = dc.PROJECT / "code-b2" / source / "benchmarks/scc/run_task.sh"
    os.execv("/bin/bash", ["bash", str(runner)])


if __name__ == "__main__":
    main()
