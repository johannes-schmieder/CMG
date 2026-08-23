"""Qualify the selectively routed optional ParallelCmgPlan."""

from pathlib import Path
import subprocess

PINNED_PLAN_COMMIT = "399c226e7e01ceb21d945a17c81648548ab86359"
plan_payload_path = Path("scripts/parallel_cmg_plan.patch.gz.b64")
plan_payload_path.write_bytes(
    subprocess.check_output(
        [
            "git",
            "show",
            f"{PINNED_PLAN_COMMIT}:scripts/parallel_cmg_plan.patch.gz.b64",
        ]
    )
)
source = subprocess.check_output(
    [
        "git",
        "show",
        f"{PINNED_PLAN_COMMIT}:scripts/parallel_cmg_plan_gate.py",
    ],
    text=True,
)

simple_replacements = [
    (
        'RESULT_PATH = ROOT / ".ci/performance/parallel-cmg-plan-latest.json"\n',
        'RESULT_PATH = ROOT / ".ci/performance/parallel-cmg-routed-plan-latest.json"\n',
    ),
    (
        'WORKFLOW_PATH = ROOT / ".github/workflows/parallel-cmg-plan.yml"\n',
        'WORKFLOW_PATH = ROOT / ".github/workflows/parallel-cmg-routed-plan.yml"\n',
    ),
    (
        'SCRIPT_PATH = ROOT / "scripts/parallel_cmg_plan_gate.py"\n',
        'SCRIPT_PATH = ROOT / "scripts/parallel_cmg_routed_gate.py"\n'
        'ROUTING_PATCH_RECORD = ROOT / "scripts/parallel_cmg_routing.patch.gz.b64"\n',
    ),
    (
        'TOUCHED_EXISTING = [\n    "benchmarks/Cargo.toml",\n',
        'TOUCHED_EXISTING = [\n    "benchmarks/Cargo.toml",\n'
        '    "benchmarks/Cargo.lock",\n',
    ),
    (
        '    PATCH_RECORD.unlink(missing_ok=True)\n',
        '    PATCH_RECORD.unlink(missing_ok=True)\n'
        '    ROUTING_PATCH_RECORD.unlink(missing_ok=True)\n',
    ),
    (
        '    "experiment": "parallel-cmg-plan",\n',
        '    "experiment": "parallel-cmg-routed-plan",\n',
    ),
    (
        '    if not PATCH_RECORD.exists():\n        raise RuntimeError("parallel CMG patch payload is missing")\n',
        '    if not PATCH_RECORD.exists():\n'
        '        raise RuntimeError("parallel CMG patch payload is missing")\n'
        '    if not ROUTING_PATCH_RECORD.exists():\n'
        '        raise RuntimeError("parallel CMG routing patch payload is missing")\n',
    ),
    (
        '    run(["git", "apply", str(patch_path)])\n\n    run(["cargo", "fmt", "--all"])\n',
        '    run(["git", "apply", str(patch_path)])\n'
        '    routing_bytes = gzip.decompress(\n'
        '        base64.b64decode(ROUTING_PATCH_RECORD.read_bytes())\n'
        '    )\n'
        '    routing_path = Path("/tmp/parallel-cmg-routing.patch")\n'
        '    routing_path.write_bytes(routing_bytes)\n'
        '    run(["git", "apply", "--check", str(routing_path)])\n'
        '    run(["git", "apply", str(routing_path)])\n\n'
        '    run(["cargo", "fmt", "--all"])\n',
    ),
    (
        '    "perf: add optional parallel CMG application plan"\n',
        '    "perf: add selectively routed parallel CMG plan"\n',
    ),
    (
        '    else "perf: record parallel CMG application experiment"\n',
        '    else "perf: record selectively routed parallel CMG experiment"\n',
    ),
]
for old, new in simple_replacements:
    if source.count(old) != 1:
        raise SystemExit(f"routed-plan recovery anchor was not unique: {old!r}")
    source = source.replace(old, new, 1)

format_check = '''    run(
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
'''
format_and_check = '''    run(
        [
            "cargo",
            "fmt",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--all",
        ]
    )
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
'''
if source.count(format_check) != 1:
    raise SystemExit("routed-plan benchmark-format anchor was not unique")
source = source.replace(format_check, format_and_check, 1)

source = source.replace(
    "### Parallel CMG application checkpoint — 2026-08-22",
    "### Selectively routed parallel CMG checkpoint — 2026-08-23",
)
source = source.replace(
    "The optional `ParallelCmgPlan` candidate",
    "The selectively routed `ParallelCmgPlan` candidate",
)
source = source.replace(
    ".ci/performance/parallel-cmg-plan-latest.json",
    ".ci/performance/parallel-cmg-routed-plan-latest.json",
)
source = source.replace("2026-08-22", "2026-08-23")

start_marker = '    binary = ROOT / "benchmarks/target/release/parallel-cmg-apply"\n'
end_marker = "except Exception as error:\n"
start = source.find(start_marker)
end = source.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit("routed-plan benchmark decision block was not found")
replacement = '''    binary = ROOT / "benchmarks/target/release/parallel-cmg-apply"
    specs = [
        ("path-250k", ["path", 250_000, 5, 4]),
        ("worker-firm-300k", ["worker-firm", 100_000, 5, 4]),
        ("worker-firm-600k", ["worker-firm", 200_000, 4, 4]),
        ("dense-worker-firm-800k", ["dense-worker-firm", 50_000, 5, 4]),
    ]
    speedups: list[float] = []
    for name, arguments in specs:
        first = sample(binary, arguments, f"{name}-first")
        second = sample(binary, arguments, f"{name}-second")
        if invariant_payload(first) != invariant_payload(second):
            raise RuntimeError(f"non-timing benchmark metadata changed for {name}")
        serial_ns = statistics.median(
            [first["serial_median_ns"], second["serial_median_ns"]]
        )
        parallel_ns = statistics.median(
            [first["parallel_median_ns"], second["parallel_median_ns"]]
        )
        speedup = serial_ns / parallel_ns
        case = invariant_payload(first)
        case.update(
            {
                "serial_median_ns": serial_ns,
                "parallel_median_ns": parallel_ns,
                "speedup": speedup,
                "peak_rss_kib": max(first["peak_rss_kib"], second["peak_rss_kib"]),
                "plan_bytes_per_edge": (
                    first["plan_bytes"] / first["edges"] if first["edges"] else 0.0
                ),
            }
        )
        result["cases"][name] = case
        speedups.append(speedup)

    geometric_speedup = math.exp(
        sum(math.log(value) for value in speedups) / len(speedups)
    )
    maximum_difference = max(
        case["max_scaled_difference"] for case in result["cases"].values()
    )
    maximum_plan_bytes_per_edge = max(
        case["plan_bytes_per_edge"] for case in result["cases"].values()
    )
    path_case = result["cases"]["path-250k"]
    small_worker_firm = result["cases"]["worker-firm-300k"]
    large_worker_firm = result["cases"]["worker-firm-600k"]
    dense_worker_firm = result["cases"]["dense-worker-firm-800k"]
    result.update(
        {
            "geometric_speedup": geometric_speedup,
            "minimum_speedup": min(speedups),
            "maximum_scaled_difference": maximum_difference,
            "active_case_count": sum(
                case["operators"] > 0 for case in result["cases"].values()
            ),
            "maximum_plan_bytes_per_edge": maximum_plan_bytes_per_edge,
            "acceptance_limits": {
                "maximum_scaled_difference": 5.0e-10,
                "geometric_speedup_min": 1.12,
                "path_operator_count": 0,
                "path_speedup_min": 0.93,
                "small_worker_firm_speedup_min": 0.94,
                "large_worker_firm_speedup_min": 1.05,
                "dense_worker_firm_speedup_min": 1.50,
                "active_plan_bytes_per_edge_max": 128.0,
            },
        }
    )
    result["accepted"] = (
        maximum_difference <= 5.0e-10
        and geometric_speedup >= 1.12
        and path_case["operators"] == 0
        and path_case["speedup"] >= 0.93
        and small_worker_firm["speedup"] >= 0.94
        and large_worker_firm["operators"] >= 1
        and large_worker_firm["speedup"] >= 1.05
        and dense_worker_firm["operators"] >= 1
        and dense_worker_firm["speedup"] >= 1.50
        and maximum_plan_bytes_per_edge <= 128.0
    )
    result["decision_reason"] = (
        "full qualification passed; selective routing avoids path CSR while accelerating large worker-firm and dense levels"
        if result["accepted"]
        else "qualification passed but selective routing, speedup, or memory retention gates were not met"
    )
'''
source = source[:start] + replacement + source[end:]

exec(compile(source, "<parallel_cmg_routed_gate>", "exec"), {"__name__": "__main__"})
