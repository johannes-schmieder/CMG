#!/usr/bin/env python3
"""Apply the scheduler-selected serial CPU after shell/module initialization."""
import json
import os
from pathlib import Path
import socket

import dispatch_campaign as dc


def selected_cpu(environment, initial_cpus):
    dc.require(environment.get("NSLOTS") in (None, "1"), "unexpected raw NSLOTS")
    dc.require(environment.get("PE") in (None, "", "NONE"), "expected a serial allocation")
    selected = environment.get("SGE_BINDING", "").split()
    dc.require(len(selected) == 1 and selected[0].isdigit(), "scheduler did not select one OS CPU")
    cpu = int(selected[0])
    dc.require(cpu in initial_cpus, "scheduler CPU is outside allowed affinity")
    return cpu


def main():
    run = dc.PROJECT / "runs" / os.environ["CMG_RUN_ID"]
    task_file = Path(os.environ["CMG_TASK_FILE"])
    task_id = int(os.environ["SGE_TASK_ID"])
    task = dc.manifest(task_file)[task_id - 1]
    dc.require(task["task_id"] == task_id and task_file ==
               run / "manifests/tasks" / f"{task['experiment']}.jsonl", "wrong task path")
    initial = sorted(os.sched_getaffinity(0))
    raw = {name: os.environ.get(name) for name in ("NSLOTS", "PE", "SGE_BINDING")}
    # Emit even on failure so the exact failed operand is observable.
    print("CMG_DISPATCH_SERIAL_ENV " + json.dumps(dict(raw=raw, affinity=initial)), flush=True)
    cpu = selected_cpu(raw, initial)
    os.sched_setaffinity(0, {cpu})
    bound = sorted(os.sched_getaffinity(0))
    dc.require(bound == [cpu], "failed to apply scheduler-selected affinity")
    provenance = dict(raw=raw, initial_cpus=initial, bound_cpus=bound,
                      hostname=socket.gethostname(), job_id=os.environ["JOB_ID"], task=task,
                      launcher_source_commit=Path(__file__).resolve().parents[2].name)
    dc.exclusive_json(run / "work" / f"launcher-{task['experiment']}-{task_id}.json", provenance)
    # NSLOTS is absent on serial SCC jobs. This is normalization, not a claim
    # about accounting: accept still independently requires qacct slots=1.
    os.environ["NSLOTS"] = "1"
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    source, _, _, _ = dc.identity(run)
    runner = dc.PROJECT / "code-b2" / source / "benchmarks/scc/run_task.sh"
    os.execv("/bin/bash", ["bash", str(runner)])


if __name__ == "__main__":
    main()
