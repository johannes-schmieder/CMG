#!/usr/bin/env python3
"""Authorized application-affinity retry, reusing—not replaying—the accepted build."""
import json
from pathlib import Path
import subprocess
import sys

import dispatch_campaign as dc
import dispatch_validator_reuse as reuse
from dispatch_serial_launcher import BINDING_POLICY, selected_cpu


def references(original):
    names = ["manifests/source-commit.txt", "manifests/source-archive-sha256.txt",
             "manifests/dispatch-portable-binary-sha256.txt", "manifests/dispatch-identity.json",
             "manifests/submission-bootstrap.txt", "receipts/BUILD_SUCCESS"]
    names += [str(path.relative_to(original)) for path in sorted((original / "logs").iterdir())
              if path.is_file()]
    names += [f"manifests/tasks/{kind}-{profile}.jsonl" for kind in
              ("dispatch-smoke", "dispatch-validate") for profile in dc.PROFILES]
    return {name: dict(origin=str(original / name), sha256=dc.sha256(original / name)) for name in names}


def verify_references(run, helper_source):
    original = dc.PROJECT / "runs" / reuse.REUSABLE_RUN
    receipt = json.loads((run / "manifests/reused-build.json").read_text())
    dc.require(receipt == dict(original_run_id=original.name, bootstrap_job_id="7469156",
                              launcher_source_commit=helper_source, references=references(original)),
               "reused build reference changed")
    for name, spec in receipt["references"].items():
        path = run / name
        dc.require(path.is_symlink() and path.resolve() == Path(spec["origin"]).resolve()
                   and dc.sha256(path) == spec["sha256"], "reused evidence link mismatch")


def check_failure_logs(stdout, stderr, profile, serial_attempt):
    if serial_attempt:
        prefix = "CMG_DISPATCH_SERIAL_ENV "
        dc.require(stdout.startswith(prefix) and len(stdout.splitlines()) == 1,
                   "missing raw failed-launcher provenance")
        dc.require(json.loads(stdout[len(prefix):]) ==
                   dict(raw=dict(NSLOTS="1", PE=None, SGE_BINDING=None),
                        affinity=list(range(dc.PROFILES[profile][0]))), "unexpected serial environment")
        dc.require('selected = environment.get("SGE_BINDING", "").split()' in stderr
                   and stderr.rstrip().endswith("AttributeError: 'NoneType' object has no attribute 'split'"),
                   "unexpected serial failure boundary")
    else:
        dc.require(stdout == "" and stderr.rstrip().endswith("ValueError: single-slot contract failed"),
                   "failure is not the authorized launcher boundary")


def failed_smokes(original, serial_attempt=False):
    jobs = ("7469449", "7469448") if serial_attempt else ("7469361", "7469362")
    for profile, job in zip(dc.PROFILES, jobs):
        experiment = f"dispatch-smoke-{profile}"
        dc.require(dc.submission(original, experiment) == job, "unexpected failed job")
        queued = subprocess.run(["qstat", "-j", job], text=True, capture_output=True)
        dc.require(queued.returncode != 0 and "do not exist" in queued.stderr, "job not terminal")
        raw = subprocess.check_output(["qacct", "-j", job], text=True)
        fields = [line.split(None, 1) for line in raw.splitlines() if len(line.split(None, 1)) == 2]
        record = {key: value.strip() for key, value in fields}
        dc.require(len(fields) == len(record), "duplicate failed accounting")
        for key, expected in dict(jobnumber=job, taskid="1", slots="1", granted_pe="NONE",
                                  failed="0", exit_status="1").items():
            dc.require(record.get(key) == expected, "unexpected failure accounting")
        dc.require(all(record.get(k) not in (None, "", "0", "-/-") for k in
                       ("hostname", "start_time", "end_time", "ru_wallclock", "maxvmem")),
                   "incomplete failed accounting")
        dc.require(dc.positive(float(record["ru_wallclock"])), "invalid failed walltime")
        log = original / "logs" / experiment
        out, err = list(log.glob(f"*.o{job}.1")), list(log.glob(f"*.e{job}.1"))
        dc.require(len(out) == len(err) == 1, "missing failed scheduler logs")
        check_failure_logs(out[0].read_text(), err[0].read_text(), profile, serial_attempt)
        dc.require(not any(p.is_file() for p in (original / "output" / experiment).rglob("*")) and
                   not any(p.is_file() for p in (original / "receipts" / experiment).rglob("*")),
                   "refusing to replay numerical evidence")


def prepare(helper):
    original = dc.PROJECT / "runs" / reuse.REUSABLE_RUN
    previous = dc.PROJECT / "runs" / reuse.SERIAL_RETRY_RUN
    run = dc.PROJECT / "runs" / reuse.APPLICATION_RETRY_RUN
    proof = reuse.verify(original, helper)
    dc.bootstrap_gate(original)
    failed_smokes(original)
    verify_references(previous, "292fed7675122b0f3ff1768a5727d806a5c42902")
    failed_smokes(previous, serial_attempt=True)
    for prior in (original, previous):
        for profile in dc.PROFILES:
            experiment = f"dispatch-validate-{profile}"
            dc.require(not (prior / "manifests" / f"submission-{experiment}.txt").exists()
                       and not (prior / "manifests" / f"submission-{experiment}.lock").exists()
                       and not (prior / "output" / experiment).exists(), "validation already started")
    links = references(original)
    # Exclusive root creation prevents duplicate attempts, including partial setup.
    run.mkdir()
    for name in ("manifests/tasks", "logs", "output", "receipts", "work"):
        (run / name).mkdir(parents=True, exist_ok=True)
    for name, spec in links.items():
        (run / name).symlink_to(spec["origin"])
    dc.exclusive_json(run / "manifests/reused-build.json",
                      dict(original_run_id=original.name, bootstrap_job_id="7469156",
                           launcher_source_commit=proof["validator_source_commit"], references=links))
    with (run / "manifests/run-id.txt").open("x") as handle:
        handle.write(run.name + "\n")
    reuse.verify(run, helper)
    print(f"CMG_DISPATCH_APPLICATION_RETRY_READY run={run.name}")


def accept(run, kind, profile, helper):
    proof = reuse.verify(run, helper)
    results, records = dc.accept_stage(run, kind, profile)
    for result in results:
        task = result["task"]
        launch = json.loads((run / "work" / f"launcher-{task['experiment']}-{task['task_id']}.json").read_text())
        cpu = selected_cpu(launch["raw"], launch["initial_cpus"])
        dc.require(launch["bound_cpus"] == result["allocated_cpus"] == [cpu]
                   and launch["binding_policy"] == BINDING_POLICY
                   and launch["hostname"] == result["hostname"] and launch["task"] == task
                   and launch["job_id"] == dc.submission(run, task["experiment"])
                   and launch["raw"]["JOB_ID"] == launch["job_id"]
                   and launch["raw"]["SGE_TASK_ID"] == str(task["task_id"])
                   and launch["launcher_source_commit"] == proof["validator_source_commit"],
                   "serial launcher provenance mismatch")
    return results, records


def main():
    action, *args = sys.argv[1:]
    helper = Path(__file__).resolve().parent
    if action == "prepare":
        dc.require(not args, "prepare takes no arguments")
        prepare(helper)
        return
    run = Path(args[0]).resolve()
    dc.require(run == dc.PROJECT / "runs" / reuse.APPLICATION_RETRY_RUN, "unexpected retry namespace")
    if action == "gate":
        reuse.verify(run, helper)
        dc.gate(run, args[1])
        if args[1] == "dispatch-validate":
            for profile in dc.PROFILES:
                accept(run, "dispatch-smoke", profile, helper)
        print("CMG_DISPATCH_SERIAL_GATE_SUCCESS")
    elif action == "accept":
        results, records = accept(run, args[1], args[2], helper)
        print(json.dumps(dict(results=results, accounting=records)))
    elif action == "summary":
        results, records = [], []
        for profile in dc.PROFILES:
            values, accounts = accept(run, "dispatch-validate", profile, helper)
            results.extend(values); records.extend(accounts)
        print(json.dumps(dict(promotion=dc.promotion(results), accounting=records)))
    else:
        raise ValueError("unknown serial retry action")


if __name__ == "__main__":
    main()
