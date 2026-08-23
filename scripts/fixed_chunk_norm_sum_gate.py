import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
PROFILE_RECORD = Path(".ci/performance/pcg-phase-profile-post-reductions.json")
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
    old = '''    if scale == 0.0 {
        0.0
    } else {
        scale
            * compensated_sum(values.iter().map(|value| {
                let scaled = *value / scale;
                scaled * scaled
            }))
            .sqrt()
    }
}

fn euclidean_norm(values: &[f64]) -> f64 {
'''
    new = '''    if scale == 0.0 {
        0.0
    } else {
        scale * scaled_square_sum_with_executor(values, scale, executor).sqrt()
    }
}

#[cfg(feature = "parallel")]
fn scaled_square_sum_with_executor(
    values: &[f64],
    scale: f64,
    executor: &ParallelExecutor,
) -> f64 {
    let options = executor.options();
    let parallel_floor = options
        .min_parallel_len
        .max(options.reduction_chunk_size.saturating_mul(8));
    if values.len() < parallel_floor || executor.thread_count() <= 1 {
        return compensated_sum(values.iter().map(|value| {
            let scaled = *value / scale;
            scaled * scaled
        }));
    }
    executor.install(|| {
        fixed_chunk_scaled_square_sum(values, scale, options.reduction_chunk_size)
    })
}

#[cfg(feature = "parallel")]
fn fixed_chunk_scaled_square_sum(values: &[f64], scale: f64, chunk_size: usize) -> f64 {
    let chunk_count = values.len().div_ceil(chunk_size);
    if chunk_count == 0 {
        return 0.0;
    }

    fn reduce_range(
        values: &[f64],
        scale: f64,
        chunk_size: usize,
        first_chunk: usize,
        last_chunk: usize,
    ) -> f64 {
        if last_chunk - first_chunk == 1 {
            let start = first_chunk * chunk_size;
            let end = values.len().min(start + chunk_size);
            return compensated_sum(values[start..end].iter().map(|value| {
                let scaled = *value / scale;
                scaled * scaled
            }));
        }
        let middle = first_chunk + (last_chunk - first_chunk) / 2;
        let (left_sum, right_sum) = rayon::join(
            || reduce_range(values, scale, chunk_size, first_chunk, middle),
            || reduce_range(values, scale, chunk_size, middle, last_chunk),
        );
        compensated_sum([left_sum, right_sum])
    }

    reduce_range(values, scale, chunk_size, 0, chunk_count)
}

#[cfg(all(test, feature = "parallel"))]
mod deterministic_parallel_norm_sum_tests {
    use super::{compensated_sum, scaled_square_sum_with_executor};
    use crate::{ParallelExecutor, ParallelOptions};

    #[test]
    fn fixed_chunk_scaled_square_sum_is_thread_count_invariant() {
        let values: Vec<f64> = (0..513)
            .map(|index| ((index * 29 + 11) % 137) as f64 / 17.0 - 4.0)
            .collect();
        let scale = values.iter().map(|value| value.abs()).fold(0.0_f64, f64::max);
        let serial = compensated_sum(values.iter().map(|value| {
            let scaled = *value / scale;
            scaled * scaled
        }));
        let mut reference = None;
        for threads in [2, 3, 4] {
            let executor = ParallelExecutor::new(ParallelOptions {
                threads,
                min_parallel_len: 1,
                reduction_chunk_size: 16,
                ..ParallelOptions::default()
            })
            .unwrap();
            let value = scaled_square_sum_with_executor(&values, scale, &executor);
            match reference {
                Some(bits) => assert_eq!(bits, value.to_bits()),
                None => reference = Some(value.to_bits()),
            }
        }
        let fixed = f64::from_bits(reference.unwrap());
        assert!((fixed - serial).abs() <= 3.0e-13 * (1.0 + serial.abs()));
    }
}

fn euclidean_norm(values: &[f64]) -> f64 {
'''
    if text.count(old) != 1:
        raise RuntimeError("executor-aware norm body marker was not unique")
    PCG.write_text(text.replace(old, new, 1))


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


def scaled_difference(left, right):
    return abs(left - right) / (1.0 + max(abs(left), abs(right)))


def compare_case(baseline, candidate, arguments):
    observations = {"baseline": [], "candidate": []}
    for label, binary in (
        ("baseline", baseline),
        ("candidate", candidate),
        ("candidate", candidate),
        ("baseline", baseline),
    ):
        observations[label].append(sample(binary, arguments))

    baseline_reference = observations["baseline"][0]
    candidate_reference = observations["candidate"][0]
    exact_keys = (
        "case",
        "scale",
        "vertices",
        "input_edges",
        "edges",
        "levels",
        "repetitions",
        "threads",
        "operators",
        "plan_bytes",
        "workspace_bytes",
        "auto_execution",
        "serial_iterations",
        "serial_backward_error",
        "serial_residual_norm",
    )
    for group in observations.values():
        for item in group:
            for key in exact_keys:
                if item[key] != baseline_reference[key]:
                    raise RuntimeError(f"exact structural/serial field changed: {key}")
    for item in observations["candidate"]:
        if item["planned_iterations"] != baseline_reference["planned_iterations"]:
            raise RuntimeError("planned iteration count changed")
        if item["max_scaled_difference"] > 5.0e-10:
            raise RuntimeError("candidate solution differs too much from serial")
        if scaled_difference(
            item["planned_backward_error"],
            baseline_reference["planned_backward_error"],
        ) > 5.0e-10:
            raise RuntimeError("candidate backward error changed too much")
        if scaled_difference(
            item["planned_residual_norm"],
            baseline_reference["planned_residual_norm"],
        ) > 5.0e-10:
            raise RuntimeError("candidate residual norm changed too much")

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
        "baseline_planned": {
            "iterations": baseline_reference["planned_iterations"],
            "backward_error": baseline_reference["planned_backward_error"],
            "residual_norm": baseline_reference["planned_residual_norm"],
            "max_scaled_difference": baseline_reference["max_scaled_difference"],
        },
        "candidate_planned": {
            "iterations": candidate_reference["planned_iterations"],
            "backward_error": candidate_reference["planned_backward_error"],
            "residual_norm": candidate_reference["planned_residual_norm"],
            "max_scaled_difference": candidate_reference["max_scaled_difference"],
        },
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
if profile.get("status") != "success":
    print("post-reduction profile is not yet successful; leaving norm gate armed")
    raise SystemExit(0)

original_pcg = PCG.read_text()
result = {
    "schema_version": 1,
    "experiment": "deterministic-fixed-chunk-norm-sum",
    "baseline_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "profile_source_sha": profile.get("source_sha"),
    "run_id": os.environ.get("GITHUB_RUN_ID"),
    "validation": "not_run",
    "accepted": False,
    "cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-norm-sum-baseline"))
    apply_candidate()
    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(
        [
            "cargo",
            "fmt",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--all",
            "--",
            "--check",
        ]
    )
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
    candidate = build(Path("/tmp/cmg-norm-sum-candidate"))
    result["validation"] = "success"

    specs = [
        ("path-150k", ["path", 150000, 5, 4]),
        ("worker-firm-300k", ["worker-firm", 100000, 5, 4]),
        ("worker-firm-600k", ["worker-firm", 200000, 5, 4]),
        ("dense-worker-firm-400k", ["dense-worker-firm", 25000, 5, 4]),
    ]
    planned_ratios = []
    serial_ratios = []
    maximum_solution_difference = 0.0
    for name, arguments in specs:
        comparison = compare_case(baseline, candidate, arguments)
        result["cases"][name] = comparison
        planned_ratios.append(comparison["planned_ratio"])
        serial_ratios.append(comparison["serial_ratio"])
        maximum_solution_difference = max(
            maximum_solution_difference,
            comparison["candidate_planned"]["max_scaled_difference"],
        )

    result["planned_geometric_time_ratio"] = math.exp(
        sum(math.log(value) for value in planned_ratios) / len(planned_ratios)
    )
    result["planned_best_time_ratio"] = min(planned_ratios)
    result["planned_worst_time_ratio"] = max(planned_ratios)
    result["serial_geometric_time_ratio"] = math.exp(
        sum(math.log(value) for value in serial_ratios) / len(serial_ratios)
    )
    result["serial_worst_time_ratio"] = max(serial_ratios)
    result["maximum_candidate_scaled_solution_difference"] = maximum_solution_difference
    result["acceptance_limits"] = {
        "planned_geometric_time_ratio_max": 0.99,
        "planned_best_time_ratio_max": 0.97,
        "planned_worst_time_ratio_max": 1.04,
        "serial_worst_time_ratio_max": 1.05,
        "maximum_scaled_solution_difference": 5.0e-10,
    }
    result["accepted"] = (
        result["planned_geometric_time_ratio"] <= 0.99
        and result["planned_best_time_ratio"] <= 0.97
        and result["planned_worst_time_ratio"] <= 1.04
        and result["serial_worst_time_ratio"] <= 1.05
        and maximum_solution_difference <= 5.0e-10
    )
    result["decision_reason"] = (
        "full qualification passed; deterministic fixed-chunk squared-norm sums improved planned solves"
        if result["accepted"]
        else "qualification passed but the full-solve timing gate was not met"
    )
except Exception as error:
    result["error"] = repr(error)
    result["decision_reason"] = f"fixed-chunk norm-sum experiment failed: {error}"
    print(result["decision_reason"], flush=True)

if not result["accepted"]:
    PCG.write_text(original_pcg)
    run(["cargo", "fmt", "--all"], check=False)

record = Path(".ci/performance/fixed-chunk-norm-sum-latest.json")
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

rows = []
for name, case in result.get("cases", {}).items():
    rows.append(
        f'| {name} | {case["serial_ratio"]:.3f}x | '
        f'{case["planned_ratio"]:.3f}x | '
        f'{case["candidate_planned"]["max_scaled_difference"]:.2e} |'
    )
checkpoint = f'''### Deterministic fixed-chunk norm-sum checkpoint — 2026-08-23

- Fixed-chunk squared-norm sums were **{"retained" if result["accepted"] else "not retained"}**.
- Validation: `{result["validation"]}`.
- Decision: {result.get("decision_reason", "no decision recorded")}.

| Case | Serial solve ratio | Planned solve ratio | Planned/serial scaled difference |
|---|---:|---:|---:|
''' + ("\n".join(rows) if rows else "| no completed timing cases | — | — | — |") + '''

- The scale pass and squared-sum combine tree are deterministic across thread scheduling and thread counts. Iterations and residual certificates were independently gated.
- Machine-readable evidence: `.ci/performance/fixed-chunk-norm-sum-latest.json`.

'''
plan_path = Path("PERFORMANCE_PLAN.md")
plan = replace_section(
    plan_path.read_text(),
    "### Deterministic fixed-chunk norm-sum checkpoint — 2026-08-23\n",
    checkpoint,
    "## Current next action\n",
)
plan_path.write_text(plan)

status_path = Path("PERFORMANCE_STATUS.md")
status = status_path.read_text().rstrip()
status_heading = "## Deterministic fixed-chunk norm-sum gate\n"
status_block = (
    "## Deterministic fixed-chunk norm-sum gate\n\n"
    f'- Decision: `{"retained" if result["accepted"] else "not retained"}`.\n'
    f'- Validation: `{result["validation"]}`.\n'
    f'- Planned geometric full-solve ratio: `{result.get("planned_geometric_time_ratio", float("nan")):.3f}x`.\n'
    f'- Maximum scaled solution difference: `{result.get("maximum_candidate_scaled_solution_difference", float("nan")):.3e}`.\n'
    "- Evidence: `.ci/performance/fixed-chunk-norm-sum-latest.json`.\n"
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

Path(".github/workflows/fixed-chunk-norm-sum.yml").unlink(missing_ok=True)
Path("scripts/fixed_chunk_norm_sum_gate.py").unlink(missing_ok=True)
