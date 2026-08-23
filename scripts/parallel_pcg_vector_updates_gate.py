import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
PROFILE_RECORD = Path(".ci/performance/pcg-phase-profile-post-reductions.json")
NORM_RECORD = Path(".ci/performance/fixed-chunk-norm-sum-latest.json")
PCG = Path("src/pcg.rs")


def run(command, *, env=None, timeout=7200, check=True):
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end="")
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
        )
    return completed


def build(target):
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target)
    run(
        [
            "cargo",
            "build",
            "--release",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--bin",
            "full-pcg-routing",
        ],
        env=env,
    )
    return target / "release" / "full-pcg-routing"


def apply_candidate():
    text = PCG.read_text()
    planned_marker = '''#[cfg(feature = "parallel")]
pub fn solve_pcg_with_plan_and_workspace(
'''
    if text.count(planned_marker) != 1:
        raise RuntimeError("planned PCG marker was not unique")
    start = text.index(planned_marker)
    helper_marker = '''#[cfg(feature = "parallel")]
pub(crate) fn euclidean_norm_with_executor(
'''
    if text.count(helper_marker) != 1:
        raise RuntimeError("executor-aware norm helper marker was not unique")
    end = text.index(helper_marker, start)
    head = text[:start]
    planned = text[start:end]
    tail = text[end:]

    update_pattern = re.compile(
        r'''(?ms)^\s*for\s+(?P<index>\w+)\s+in\s+0\.\.graph\.vertex_count\(\)\s*\{\s*
'''
        r'''\s*workspace\.solution\[(?P=index)\]\s*\+=\s*(?P<step>\w+)\s*\*\s*workspace\.direction\[(?P=index)\]\s*;\s*
'''
        r'''\s*workspace\.residual\[(?P=index)\]\s*-=\s*(?P=step)\s*\*\s*workspace\.(?P<matrix>matvec|matrix_direction)\[(?P=index)\]\s*;\s*
'''
        r'''\s*\}\s*'''
    )
    update_match = update_pattern.search(planned)
    if update_match is None:
        raise RuntimeError("planned solution/residual update loop was not found")
    update_call = (
        "        update_solution_and_residual_with_executor(\n"
        "            &mut workspace.solution,\n"
        "            &mut workspace.residual,\n"
        "            &workspace.direction,\n"
        f"            &workspace.{update_match.group('matrix')},\n"
        f"            {update_match.group('step')},\n"
        "            executor,\n"
        "        );\n"
    )
    planned = planned[: update_match.start()] + update_call + planned[update_match.end() :]

    direction_pattern = re.compile(
        r'''(?ms)^\s*for\s+(?P<index>\w+)\s+in\s+0\.\.graph\.vertex_count\(\)\s*\{\s*
'''
        r'''\s*workspace\.direction\[(?P=index)\]\s*=\s*workspace\.preconditioned\[(?P=index)\]\s*\+\s*(?P<beta>\w+)\s*\*\s*workspace\.direction\[(?P=index)\]\s*;\s*
'''
        r'''\s*\}\s*'''
    )
    direction_match = direction_pattern.search(planned)
    if direction_match is None:
        raise RuntimeError("planned search-direction update loop was not found")
    direction_call = (
        "        update_direction_with_executor(\n"
        "            &mut workspace.direction,\n"
        "            &workspace.preconditioned,\n"
        f"            {direction_match.group('beta')},\n"
        "            executor,\n"
        "        );\n"
    )
    planned = (
        planned[: direction_match.start()]
        + direction_call
        + planned[direction_match.end() :]
    )

    helpers = '''#[cfg(feature = "parallel")]
fn update_solution_and_residual_with_executor(
    solution: &mut [f64],
    residual: &mut [f64],
    direction: &[f64],
    matrix_direction: &[f64],
    step: f64,
    executor: &ParallelExecutor,
) {
    debug_assert_eq!(solution.len(), residual.len());
    debug_assert_eq!(solution.len(), direction.len());
    debug_assert_eq!(solution.len(), matrix_direction.len());
    if executor.should_parallel(solution.len()) && executor.thread_count() > 1 {
        executor.install(|| {
            solution
                .par_iter_mut()
                .zip(residual.par_iter_mut())
                .zip(direction.par_iter())
                .zip(matrix_direction.par_iter())
                .for_each(|(((solution, residual), direction), matrix_direction)| {
                    *solution += step * *direction;
                    *residual -= step * *matrix_direction;
                });
        });
    } else {
        for index in 0..solution.len() {
            solution[index] += step * direction[index];
            residual[index] -= step * matrix_direction[index];
        }
    }
}

#[cfg(feature = "parallel")]
fn update_direction_with_executor(
    direction: &mut [f64],
    preconditioned: &[f64],
    beta: f64,
    executor: &ParallelExecutor,
) {
    debug_assert_eq!(direction.len(), preconditioned.len());
    if executor.should_parallel(direction.len()) && executor.thread_count() > 1 {
        executor.install(|| {
            direction
                .par_iter_mut()
                .zip(preconditioned.par_iter())
                .for_each(|(direction, preconditioned)| {
                    *direction = *preconditioned + beta * *direction;
                });
        });
    } else {
        for index in 0..direction.len() {
            direction[index] = preconditioned[index] + beta * direction[index];
        }
    }
}

#[cfg(all(test, feature = "parallel"))]
mod deterministic_parallel_vector_update_tests {
    use super::{
        update_direction_with_executor, update_solution_and_residual_with_executor,
    };
    use crate::{ParallelExecutor, ParallelOptions};

    fn executor(threads: usize) -> ParallelExecutor {
        ParallelExecutor::new(ParallelOptions {
            threads,
            min_parallel_len: 1,
            ..ParallelOptions::default()
        })
        .unwrap()
    }

    #[test]
    fn fused_solution_residual_update_is_bitwise_thread_invariant() {
        let initial_solution: Vec<f64> = (0..257).map(|i| i as f64 / 19.0 - 2.0).collect();
        let initial_residual: Vec<f64> = (0..257).map(|i| i as f64 / 23.0 - 3.0).collect();
        let direction: Vec<f64> = (0..257).map(|i| i as f64 / 29.0 - 1.0).collect();
        let matrix_direction: Vec<f64> =
            (0..257).map(|i| i as f64 / 31.0 - 4.0).collect();
        let mut reference = None;
        for threads in [1, 2, 4] {
            let mut solution = initial_solution.clone();
            let mut residual = initial_residual.clone();
            update_solution_and_residual_with_executor(
                &mut solution,
                &mut residual,
                &direction,
                &matrix_direction,
                0.37,
                &executor(threads),
            );
            let bits = (
                solution.iter().map(|value| value.to_bits()).collect::<Vec<_>>(),
                residual.iter().map(|value| value.to_bits()).collect::<Vec<_>>(),
            );
            match &reference {
                Some(reference) => assert_eq!(reference, &bits),
                None => reference = Some(bits),
            }
        }
    }

    #[test]
    fn direction_update_is_bitwise_thread_invariant() {
        let initial: Vec<f64> = (0..257).map(|i| i as f64 / 17.0 - 2.0).collect();
        let preconditioned: Vec<f64> = (0..257).map(|i| i as f64 / 13.0 - 1.0).collect();
        let mut reference = None;
        for threads in [1, 2, 4] {
            let mut direction = initial.clone();
            update_direction_with_executor(
                &mut direction,
                &preconditioned,
                0.23,
                &executor(threads),
            );
            let bits = direction
                .iter()
                .map(|value| value.to_bits())
                .collect::<Vec<_>>();
            match &reference {
                Some(reference) => assert_eq!(reference, &bits),
                None => reference = Some(bits),
            }
        }
    }
}

'''
    PCG.write_text(head + planned + helpers + tail)


def sample(binary, arguments):
    completed = run([str(binary), *map(str, arguments)])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected benchmark output: {payloads}")
    return payloads[0]


def compare_case(baseline, candidate, arguments):
    observations = {"baseline": [], "candidate": []}
    for label, binary in (
        ("baseline", baseline),
        ("candidate", candidate),
        ("candidate", candidate),
        ("baseline", baseline),
    ):
        observations[label].append(sample(binary, arguments))
    reference = observations["baseline"][0]
    timing_keys = {"serial_median_ns", "planned_median_ns", "speedup"}
    stable_keys = sorted(set(reference) - timing_keys)
    for group in observations.values():
        for item in group:
            if set(item) != set(reference):
                raise RuntimeError("benchmark payload keys changed")
            for key in stable_keys:
                if item[key] != reference[key]:
                    raise RuntimeError(f"exact benchmark field changed: {key}")
    baseline_serial = statistics.median(
        item["serial_median_ns"] for item in observations["baseline"]
    )
    candidate_serial = statistics.median(
        item["serial_median_ns"] for item in observations["candidate"]
    )
    baseline_planned = statistics.median(
        item["planned_median_ns"] for item in observations["baseline"]
    )
    candidate_planned = statistics.median(
        item["planned_median_ns"] for item in observations["candidate"]
    )
    return {
        "arguments": arguments,
        "metadata": {key: reference[key] for key in stable_keys},
        "serial_ratio": candidate_serial / baseline_serial,
        "planned_ratio": candidate_planned / baseline_planned,
        "baseline_serial_ns": baseline_serial,
        "candidate_serial_ns": candidate_serial,
        "baseline_planned_ns": baseline_planned,
        "candidate_planned_ns": candidate_planned,
    }


def replace_section(text, heading, replacement, next_heading):
    if heading not in text:
        return text.replace(next_heading, replacement + next_heading, 1)
    start = text.index(heading)
    end = text.index(next_heading, start)
    return text[:start] + replacement + text[end:]


profile = json.loads(PROFILE_RECORD.read_text()) if PROFILE_RECORD.exists() else {}
norm = json.loads(NORM_RECORD.read_text()) if NORM_RECORD.exists() else {}
if profile.get("status") != "success" or norm.get("validation") != "success":
    print("prerequisite profile/norm gate is unresolved; leaving vector-update gate armed")
    raise SystemExit(0)

original_pcg = PCG.read_text()
result = {
    "schema_version": 1,
    "experiment": "parallel-pcg-vector-updates",
    "baseline_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "profile_source_sha": profile.get("source_sha"),
    "norm_gate_accepted": norm.get("accepted"),
    "run_id": os.environ.get("GITHUB_RUN_ID"),
    "validation": "not_run",
    "accepted": False,
    "cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-vector-update-baseline"))
    apply_candidate()
    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
    run(
        [
            "cargo",
            "clippy",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ]
    )
    run(["cargo", "test", "--all-targets"])
    run(["cargo", "test", "--all-targets", "--release"])
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    candidate = build(Path("/tmp/cmg-vector-update-candidate"))
    result["validation"] = "success"

    specs = [
        ("path-150k", ["path", 150000, 5, 4]),
        ("worker-firm-300k", ["worker-firm", 100000, 5, 4]),
        ("worker-firm-600k", ["worker-firm", 200000, 5, 4]),
        ("dense-worker-firm-400k", ["dense-worker-firm", 25000, 5, 4]),
    ]
    planned_ratios = []
    serial_ratios = []
    for name, arguments in specs:
        comparison = compare_case(baseline, candidate, arguments)
        result["cases"][name] = comparison
        planned_ratios.append(comparison["planned_ratio"])
        serial_ratios.append(comparison["serial_ratio"])

    result["planned_geometric_time_ratio"] = math.exp(
        sum(math.log(value) for value in planned_ratios) / len(planned_ratios)
    )
    result["planned_best_time_ratio"] = min(planned_ratios)
    result["planned_worst_time_ratio"] = max(planned_ratios)
    result["serial_geometric_time_ratio"] = math.exp(
        sum(math.log(value) for value in serial_ratios) / len(serial_ratios)
    )
    result["serial_worst_time_ratio"] = max(serial_ratios)
    result["acceptance_limits"] = {
        "planned_geometric_time_ratio_max": 0.985,
        "planned_best_time_ratio_max": 0.95,
        "planned_worst_time_ratio_max": 1.04,
        "serial_worst_time_ratio_max": 1.05,
    }
    result["accepted"] = (
        result["planned_geometric_time_ratio"] <= 0.985
        and result["planned_best_time_ratio"] <= 0.95
        and result["planned_worst_time_ratio"] <= 1.04
        and result["serial_worst_time_ratio"] <= 1.05
    )
    result["decision_reason"] = (
        "full qualification passed; exact parallel PCG vector updates improved planned solves"
        if result["accepted"]
        else "qualification passed but the exact full-solve timing gate was not met"
    )
except Exception as error:
    result["error"] = repr(error)
    result["decision_reason"] = f"parallel PCG vector-update experiment failed: {error}"
    print(result["decision_reason"], flush=True)

if not result["accepted"]:
    PCG.write_text(original_pcg)
    run(["cargo", "fmt", "--all"], check=False)

record = Path(".ci/performance/parallel-pcg-vector-updates-latest.json")
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

rows = []
for name, case in result.get("cases", {}).items():
    rows.append(
        f'| {name} | {case["serial_ratio"]:.3f}x | {case["planned_ratio"]:.3f}x |'
    )
checkpoint = f'''### Exact parallel PCG vector-update checkpoint — 2026-08-23

- Fused elementwise PCG updates were **{"retained" if result["accepted"] else "not retained"}**.
- Validation: `{result["validation"]}`.
- Decision: {result.get("decision_reason", "no decision recorded")}.

| Case | Serial solve ratio | Planned solve ratio |
|---|---:|---:|
''' + ("\n".join(rows) if rows else "| no completed timing cases | — | — |") + '''

- Every non-timing benchmark field was required to remain bit-for-bit identical, including iterations, solutions, residuals, and backward-error certificates.
- Machine-readable evidence: `.ci/performance/parallel-pcg-vector-updates-latest.json`.

'''
plan_path = Path("PERFORMANCE_PLAN.md")
plan_path.write_text(
    replace_section(
        plan_path.read_text(),
        "### Exact parallel PCG vector-update checkpoint — 2026-08-23\n",
        checkpoint,
        "## Current next action\n",
    )
)

status_path = Path("PERFORMANCE_STATUS.md")
status = status_path.read_text().rstrip()
status_heading = "## Exact parallel PCG vector-update gate\n"
status_block = (
    "## Exact parallel PCG vector-update gate\n\n"
    f'- Decision: `{"retained" if result["accepted"] else "not retained"}`.\n'
    f'- Validation: `{result["validation"]}`.\n'
    f'- Planned geometric full-solve ratio: `{result.get("planned_geometric_time_ratio", float("nan")):.3f}x`.\n'
    "- Evidence: `.ci/performance/parallel-pcg-vector-updates-latest.json`.\n"
)
if status_heading in status:
    start = status.index(status_heading)
    end = status.find("\n## ", start + len(status_heading))
    if end == -1:
        end = len(status)
    status = status[:start] + status_block + status[end:]
else:
    status += "\n\n" + status_block
status_path.write_text(status.rstrip() + "\n")

Path(".github/workflows/parallel-pcg-vector-updates.yml").unlink(missing_ok=True)
Path("scripts/parallel_pcg_vector_updates_gate.py").unlink(missing_ok=True)
