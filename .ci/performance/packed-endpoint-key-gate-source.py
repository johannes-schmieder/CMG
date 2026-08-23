"""Benchmark-gate packed endpoint-key ordering for canonical graph edges."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
GRAPH = Path("src/graph.rs")
BENCH_MANIFEST = Path("benchmarks/Cargo.toml")
BENCH_LOCK = Path("benchmarks/Cargo.lock")
PARALLEL_BENCH = Path("benchmarks/src/bin/hierarchy-build-parallel.rs")
WORKFLOW = Path(".github/workflows/packed-endpoint-key.yml")
SCRIPT = Path("scripts/packed_endpoint_key_gate.py")
RECORD = Path(".ci/performance/packed-endpoint-key-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")

BASELINE_SHA = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
GRAPH_ORIGINAL = GRAPH.read_text()
MANIFEST_ORIGINAL = BENCH_MANIFEST.read_text()
LOCK_ORIGINAL = BENCH_LOCK.read_text()


def run(command: list[str], *, env: dict[str, str] | None = None, timeout: int = 7200) -> subprocess.CompletedProcess[str]:
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
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}")
    return completed


def prepare_parallel_benchmark() -> None:
    dependency = 'cmg = { path = ".." }'
    if MANIFEST_ORIGINAL.count(dependency) != 1:
        raise RuntimeError("benchmark dependency anchor was not unique")
    manifest = MANIFEST_ORIGINAL.replace(
        dependency,
        'cmg = { path = "..", features = ["parallel"] }',
        1,
    )
    manifest += '''

[[bin]]
name = "hierarchy-build-parallel"
path = "src/bin/hierarchy-build-parallel.rs"
'''
    BENCH_MANIFEST.write_text(manifest)

    source = Path("benchmarks/src/bin/hierarchy-build.rs").read_text()
    import_anchor = "use cmg::{CmgOptions, CmgPreconditioner, Laplacian};"
    if source.count(import_anchor) != 1:
        raise RuntimeError("parallel benchmark import anchor was not unique")
    source = source.replace(
        import_anchor,
        "use cmg::{CmgOptions, CmgPreconditioner, Laplacian, ParallelExecutor, ParallelOptions};",
        1,
    )
    graph_anchor = "    let bench_graph = build_case(&case, scale);\n"
    if source.count(graph_anchor) != 1:
        raise RuntimeError("parallel benchmark graph anchor was not unique")
    source = source.replace(
        graph_anchor,
        graph_anchor
        + '''    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 4,
        ..ParallelOptions::default()
    })
    .expect("parallel executor should build");
''',
        1,
    )
    build_anchor = (
        "CmgPreconditioner::build(black_box(&bench_graph.graph), "
        "CmgOptions::default())"
    )
    if source.count(build_anchor) != 2:
        raise RuntimeError("parallel benchmark build anchors were not exactly two")
    source = source.replace(
        build_anchor,
        "CmgPreconditioner::build_with_executor(black_box(&bench_graph.graph), "
        "CmgOptions::default(), &executor)",
    )
    PARALLEL_BENCH.write_text(source)


def restore_benchmark_files() -> None:
    BENCH_MANIFEST.write_text(MANIFEST_ORIGINAL)
    BENCH_LOCK.write_text(LOCK_ORIGINAL)
    PARALLEL_BENCH.unlink(missing_ok=True)


def build(target: Path) -> dict[str, Path]:
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
            "graph-build",
            "--bin",
            "hierarchy-build",
            "--bin",
            "hierarchy-build-parallel",
        ],
        env=env,
    )
    return {
        "graph": target / "release" / "graph-build",
        "serial": target / "release" / "hierarchy-build",
        "parallel": target / "release" / "hierarchy-build-parallel",
    }


def sample(binary: Path, arguments: list[object], tag: str) -> dict[str, object]:
    timing_path = Path(f"/tmp/cmg-packed-key-{tag}.time")
    completed = run(
        [
            "/usr/bin/time",
            "-v",
            "-o",
            str(timing_path),
            str(binary),
            *[str(value) for value in arguments],
        ]
    )
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected benchmark output: {payloads}")
    timing = timing_path.read_text()
    rss = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", timing)
    if rss is None:
        raise RuntimeError("peak RSS was not reported")
    return {
        "median_ns": int(payloads[0]["median_ns"]),
        "peak_rss_kib": int(rss.group(1)),
        "metadata": payloads[0],
    }


def compare_case(
    baseline: Path,
    candidate: Path,
    arguments: list[object],
    name: str,
) -> dict[str, object]:
    baseline_samples: list[dict[str, object]] = []
    candidate_samples: list[dict[str, object]] = []
    sequence = (
        ("baseline", baseline),
        ("candidate", candidate),
        ("candidate", candidate),
        ("baseline", baseline),
    )
    for index, (label, binary) in enumerate(sequence):
        observation = sample(binary, arguments, f"{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(observation)

    baseline_ns = statistics.median(int(item["median_ns"]) for item in baseline_samples)
    candidate_ns = statistics.median(int(item["median_ns"]) for item in candidate_samples)
    baseline_rss = max(int(item["peak_rss_kib"]) for item in baseline_samples)
    candidate_rss = max(int(item["peak_rss_kib"]) for item in candidate_samples)
    baseline_metadata = baseline_samples[0]["metadata"]
    candidate_metadata = candidate_samples[0]["metadata"]
    baseline_stable = {
        key: value for key, value in baseline_metadata.items() if key != "median_ns"
    }
    candidate_stable = {
        key: value for key, value in candidate_metadata.items() if key != "median_ns"
    }
    if baseline_stable != candidate_stable:
        raise RuntimeError(f"benchmark metadata changed for {name}")
    return {
        "arguments": arguments,
        "baseline_median_ns": baseline_ns,
        "candidate_median_ns": candidate_ns,
        "candidate_over_baseline_time": candidate_ns / baseline_ns,
        "baseline_peak_rss_kib": baseline_rss,
        "candidate_peak_rss_kib": candidate_rss,
        "candidate_over_baseline_peak_rss": candidate_rss / baseline_rss,
        "metadata": candidate_metadata,
    }


def apply_candidate() -> None:
    old = '''fn compare_raw_edges(left: &Edge, right: &Edge) -> core::cmp::Ordering {
    left.u
        .cmp(&right.u)
        .then(left.v.cmp(&right.v))
        .then_with(|| left.weight.total_cmp(&right.weight))
}
'''
    new = '''fn compare_raw_edges(left: &Edge, right: &Edge) -> core::cmp::Ordering {
    let left_endpoints = (u64::from(left.u) << 32) | u64::from(left.v);
    let right_endpoints = (u64::from(right.u) << 32) | u64::from(right.v);
    left_endpoints
        .cmp(&right_endpoints)
        .then_with(|| left.weight.total_cmp(&right.weight))
}
'''
    source = GRAPH_ORIGINAL
    if source.count(old) != 1:
        raise RuntimeError("edge comparator anchor was not unique")
    GRAPH.write_text(source.replace(old, new, 1))


def geometric_mean(values: list[float]) -> float:
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result: dict[str, object]) -> None:
    accepted = bool(result["accepted"])
    status_word = "retained" if accepted else "not retained"
    time_ratio = result.get("geometric_candidate_over_baseline_time")
    rss_ratio = result.get("geometric_candidate_over_baseline_peak_rss")
    evidence = ""
    if isinstance(time_ratio, float) and isinstance(rss_ratio, float):
        evidence = f" Geometric time/RSS ratios: {time_ratio:.3f}/{rss_ratio:.3f}."

    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-next-action marker missing")
    checkpoint = f'''### Packed endpoint-key checkpoint — 2026-08-23

- Replacing the two-endpoint comparator with one packed 64-bit endpoint key was
  **{status_word}**.{evidence}
- Validation status: `{result['validation']}`.
- Decision: {result['decision_reason']}.
- Machine-readable evidence:
  `.ci/performance/packed-endpoint-key-latest.json`.

'''
    if "### Packed endpoint-key checkpoint — 2026-08-23" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    next_actions = (
        '''## Current next action

1. Complete ordinary Ubuntu/macOS/Windows qualification of the retained packed
   endpoint-key source.
2. Profile reusable contraction buffers and compact aggregation labels.
3. Obtain controlled 8–32-thread and high-memory evidence when suitable hardware
   is available.
4. Remove obsolete one-shot workflows, staging scripts, and committed Python
   cache files after active gates are secure.
'''
        if accepted
        else
        '''## Current next action

1. Keep the qualified direct compact-edge comparator and profile reusable
   contraction buffers or compact aggregation labels instead.
2. Obtain controlled 8–32-thread and high-memory evidence when suitable hardware
   is available.
3. Remove obsolete one-shot workflows, staging scripts, and committed Python
   cache files after active gates are secure.
'''
    )
    plan = re.sub(r"## Current next action\n.*\Z", next_actions, plan, flags=re.DOTALL)
    PLAN.write_text(plan)

    status = STATUS.read_text()
    gate = f'''## Latest resolved benchmark gate

The packed endpoint-key experiment completed with validation
`{result['validation']}` and was **{status_word}**.{evidence}
The decision record is `.ci/performance/packed-endpoint-key-latest.json`.

'''
    status = re.sub(
        r"## Latest resolved benchmark gate\n.*?(?=## Next prepared optimization\n)",
        gate,
        status,
        count=1,
        flags=re.DOTALL,
    )
    STATUS.write_text(status)


result: dict[str, object] = {
    "schema_version": 1,
    "experiment": "packed-endpoint-key-ordering",
    "baseline_sha": BASELINE_SHA,
    "accepted": False,
    "validation": "not_run",
    "decision_reason": "",
    "cases": {},
}

try:
    prepare_parallel_benchmark()
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    baseline = build(Path("/tmp/cmg-packed-key-baseline"))
    apply_candidate()

    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all", "--", "--check"])
    run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
    run([
        "cargo",
        "clippy",
        "--manifest-path",
        "benchmarks/Cargo.toml",
        "--all-targets",
        "--",
        "-D",
        "warnings",
    ])
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps", "--document-private-items", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets"])
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--all-features"])

    candidate = build(Path("/tmp/cmg-packed-key-candidate"))
    result["validation"] = "success"
    specs = [
        ("graph-unique-1m", "graph", ["unique", 1_000_000, 3]),
        ("graph-duplicates4-1m", "graph", ["duplicates-4", 250_000, 3]),
        ("graph-duplicates16-1m", "graph", ["duplicates-16", 62_500, 3]),
        ("graph-collisions-1m", "graph", ["coarse-collisions", 62_500, 3]),
        ("serial-path-1m", "serial", ["path", 1_000_000, 2]),
        ("serial-worker-firm-1.5m", "serial", ["worker-firm", 500_000, 2]),
        ("serial-dense-worker-firm-1.6m", "serial", ["dense-worker-firm", 100_000, 2]),
        ("parallel-path-1m", "parallel", ["path", 1_000_000, 2]),
        ("parallel-worker-firm-1.5m", "parallel", ["worker-firm", 500_000, 2]),
        ("parallel-dense-worker-firm-1.6m", "parallel", ["dense-worker-firm", 100_000, 2]),
    ]
    time_ratios: list[float] = []
    rss_ratios: list[float] = []
    for name, mode, arguments in specs:
        comparison = compare_case(baseline[mode], candidate[mode], arguments, name)
        result["cases"][name] = comparison
        time_ratios.append(float(comparison["candidate_over_baseline_time"]))
        rss_ratios.append(float(comparison["candidate_over_baseline_peak_rss"]))

    geometric_time = geometric_mean(time_ratios)
    geometric_rss = geometric_mean(rss_ratios)
    result.update(
        {
            "geometric_candidate_over_baseline_time": geometric_time,
            "maximum_candidate_over_baseline_time": max(time_ratios),
            "geometric_candidate_over_baseline_peak_rss": geometric_rss,
            "maximum_candidate_over_baseline_peak_rss": max(rss_ratios),
            "acceptance_limits": {
                "geometric_time_ratio_max": 0.99,
                "maximum_time_ratio_max": 1.06,
                "geometric_peak_rss_ratio_max": 1.01,
                "maximum_peak_rss_ratio_max": 1.03,
            },
        }
    )
    result["accepted"] = (
        geometric_time <= 0.99
        and max(time_ratios) <= 1.06
        and geometric_rss <= 1.01
        and max(rss_ratios) <= 1.03
    )
    result["decision_reason"] = (
        "full qualification passed; packed endpoint ordering produced a stable material timing improvement without memory regression"
        if result["accepted"]
        else "qualification passed, but packed endpoint ordering did not meet the material timing and memory gates"
    )
except Exception as error:
    result["decision_reason"] = f"experiment failed: {error}"
    result["error"] = repr(error)
    print(result["decision_reason"], flush=True)
finally:
    if not result["accepted"]:
        GRAPH.write_text(GRAPH_ORIGINAL)
    restore_benchmark_files()

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
update_documents(result)
WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)

run(["git", "config", "user.name", "github-actions[bot]"])
run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
run(["git", "add", "-A"])
message = (
    "perf: retain packed endpoint-key ordering"
    if result["accepted"]
    else "perf: record packed endpoint-key experiment"
)
run(["git", "commit", "-m", message])
run(["git", "pull", "--rebase", "origin", "main"])
run(["git", "push", "origin", "HEAD:main"])
