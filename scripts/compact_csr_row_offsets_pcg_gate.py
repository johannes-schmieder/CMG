import json
import math
import os
from pathlib import Path
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/csr.rs")
WORKFLOW = Path(".github/workflows/compact-csr-row-offsets-pcg.yml")
SCRIPT = Path("scripts/compact_csr_row_offsets_pcg_gate.py")
RECORD = Path(".ci/performance/compact-csr-row-offsets-pcg-latest.json")
MEMORY_RECORD = Path(".ci/performance/compact-csr-row-offsets-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")
COMPACT_GATE_COMMIT = "2fe726a1fbbfc4e5e743621010e44e906ac6f34b"
COMPACT_GATE_PATH = "scripts/compact_csr_row_offsets_gate.py"


def run(command, *, env=None, timeout=7200, check=True):
    print("+", " ".join(str(item) for item in command), flush=True)
    completed = subprocess.run(
        [str(item) for item in command],
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
            f"command failed ({completed.returncode}): "
            f"{' '.join(str(item) for item in command)}"
        )
    return completed


def load_candidate_transform():
    wrapper = subprocess.check_output(
        ["git", "show", f"{COMPACT_GATE_COMMIT}:{COMPACT_GATE_PATH}"],
        cwd=ROOT,
        text=True,
    )
    old_open = "source_patch = r'''ROW_OFFSETS_INSERT_MARKER"
    new_open = 'source_patch = r"""ROW_OFFSETS_INSERT_MARKER'
    old_close = "    return candidate\n'''\ntext = text[:start] + source_patch + text[end:]\n"
    new_close = '    return candidate\n"""\ntext = text[:start] + source_patch + text[end:]\n'
    if wrapper.count(old_open) != 1 or wrapper.count(old_close) != 1:
        raise RuntimeError("historical compact CSR source-patch delimiters changed")
    wrapper = wrapper.replace(old_open, new_open, 1)
    wrapper = wrapper.replace(old_close, new_close, 1)
    cleanup_marker = '\nold_cleanup = "WORKFLOW.unlink(missing_ok=True)'
    if cleanup_marker not in wrapper:
        raise RuntimeError("historical compact CSR cleanup marker missing")
    wrapper_prefix = wrapper.split(cleanup_marker, 1)[0]
    wrapper_namespace = {
        "__name__": "compact_csr_wrapper_defs",
        "__file__": str(SCRIPT),
    }
    exec(compile(wrapper_prefix, str(SCRIPT), "exec"), wrapper_namespace)
    generated = wrapper_namespace["text"]
    execution_marker = "baseline_source = SOURCE.read_text()"
    if execution_marker not in generated:
        raise RuntimeError("generated compact CSR execution marker missing")
    candidate_namespace = {
        "__name__": "compact_csr_candidate_defs",
        "__file__": str(SCRIPT),
    }
    exec(
        compile(generated.split(execution_marker, 1)[0], str(SCRIPT), "exec"),
        candidate_namespace,
    )
    return candidate_namespace["apply_candidate"]


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


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-compact-offset-pcg-{tag}.time")
    completed = run(
        ["/usr/bin/time", "-v", "-o", time_path, binary, *arguments]
    )
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected full-PCG output: {payloads}")
    rss_line = next(
        line
        for line in time_path.read_text().splitlines()
        if "Maximum resident set size (kbytes):" in line
    )
    payload = payloads[0]
    payload["peak_rss_kib"] = int(rss_line.rsplit(":", 1)[1].strip())
    return payload


def compare_case(baseline, candidate, arguments, name):
    baseline_samples = []
    candidate_samples = []
    for index, (label, binary) in enumerate(
        (
            ("baseline", baseline),
            ("candidate", candidate),
            ("candidate", candidate),
            ("baseline", baseline),
            ("candidate", candidate),
            ("baseline", baseline),
        )
    ):
        observation = sample(binary, arguments, f"{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(
            observation
        )

    stable = (
        "case",
        "scale",
        "vertices",
        "input_edges",
        "edges",
        "levels",
        "repetitions",
        "threads",
        "operators",
        "workspace_bytes",
        "auto_execution",
        "serial_iterations",
        "planned_iterations",
    )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: unstable full-PCG metadata for {key}")
        if observation["max_scaled_difference"] > 1.0e-8:
            raise RuntimeError(f"{name}: serial/planned solution mismatch")
        for field in (
            "serial_backward_error",
            "planned_backward_error",
            "serial_residual_norm",
            "planned_residual_norm",
            "speedup",
        ):
            if not math.isfinite(observation[field]):
                raise RuntimeError(f"{name}: non-finite {field}")
        if "Planned" not in observation["auto_execution"]:
            raise RuntimeError(f"{name}: auto router did not select planned execution")

    result = {
        "arguments": arguments,
        "metadata": {key: reference[key] for key in stable},
    }
    for field in (
        "planned_median_ns",
        "serial_median_ns",
        "plan_bytes",
        "peak_rss_kib",
    ):
        baseline_value = statistics.median(item[field] for item in baseline_samples)
        candidate_value = statistics.median(item[field] for item in candidate_samples)
        result[f"baseline_{field}"] = baseline_value
        result[f"candidate_{field}"] = candidate_value
        result[f"candidate_over_baseline_{field}"] = candidate_value / baseline_value
    result["baseline_max_scaled_difference"] = max(
        item["max_scaled_difference"] for item in baseline_samples
    )
    result["candidate_max_scaled_difference"] = max(
        item["max_scaled_difference"] for item in candidate_samples
    )
    result["baseline_max_planned_backward_error"] = max(
        item["planned_backward_error"] for item in baseline_samples
    )
    result["candidate_max_planned_backward_error"] = max(
        item["planned_backward_error"] for item in candidate_samples
    )
    return result


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    planned_ratio = result.get("geometric_planned_time_ratio", 1.0)
    plan_ratio = result.get("geometric_plan_bytes_ratio", 1.0)
    checkpoint = f'''### Compact CSR row-offset full-PCG checkpoint — 2026-08-24

- Compact checked CSR row offsets were **{decision}** after full certified-PCG qualification.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric planned-solve / plan-size ratios: `{planned_ratio:.3f}x` / `{plan_ratio:.3f}x`.
- Worst planned-solve ratio: `{result.get("worst_planned_time_ratio", 1.0):.3f}x`; worst process-RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/compact-csr-row-offsets-pcg-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Compact CSR row-offset full-PCG checkpoint — 2026-08-24\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Compact CSR row-offset full-PCG gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Planned-solve / plan-size ratios: `{planned_ratio:.3f}x` / `{plan_ratio:.3f}x`.
- Worst planned-solve / process-RSS ratios: `{result.get("worst_planned_time_ratio", 1.0):.3f}x` / `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/compact-csr-row-offsets-pcg-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Compact CSR row-offset full-PCG gate\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + block + status[end:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")


baseline_source = SOURCE.read_text()
memory = json.loads(MEMORY_RECORD.read_text())
result = {
    "schema_version": 1,
    "experiment": "compact-csr-row-offsets-full-pcg",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "cases": {},
    "prior_memory_record": memory,
}

try:
    apply_candidate = load_candidate_transform()
    baseline = build(Path("/tmp/cmg-compact-offset-pcg-baseline"))
    SOURCE.write_text(apply_candidate(baseline_source))

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
    run(
        [
            "cargo",
            "clippy",
            "--all-targets",
            "--all-features",
            "--",
            "-D",
            "warnings",
        ]
    )
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
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    result["validation"] = "success"

    candidate = build(Path("/tmp/cmg-compact-offset-pcg-candidate"))
    specs = (
        ("worker-firm-600k", ["worker-firm", "200000", "5", "4"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "5", "4"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "5", "4"]),
        ("dense-worker-firm-800k", ["dense-worker-firm", "50000", "5", "4"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "5", "4"]),
    )
    for name, arguments in specs:
        result["cases"][name] = compare_case(
            baseline, candidate, arguments, name
        )

    planned_ratios = [
        case["candidate_over_baseline_planned_median_ns"]
        for case in result["cases"].values()
    ]
    serial_ratios = [
        case["candidate_over_baseline_serial_median_ns"]
        for case in result["cases"].values()
    ]
    plan_ratios = [
        case["candidate_over_baseline_plan_bytes"]
        for case in result["cases"].values()
    ]
    rss_ratios = [
        case["candidate_over_baseline_peak_rss_kib"]
        for case in result["cases"].values()
    ]
    result["geometric_planned_time_ratio"] = geometric(planned_ratios)
    result["worst_planned_time_ratio"] = max(planned_ratios)
    result["geometric_serial_time_ratio"] = geometric(serial_ratios)
    result["worst_serial_time_ratio"] = max(serial_ratios)
    result["geometric_plan_bytes_ratio"] = geometric(plan_ratios)
    result["worst_plan_bytes_ratio"] = max(plan_ratios)
    result["geometric_peak_rss_ratio"] = geometric(rss_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["maximum_scaled_difference"] = max(
        max(
            case["baseline_max_scaled_difference"],
            case["candidate_max_scaled_difference"],
        )
        for case in result["cases"].values()
    )
    result["prior_memory_geometric_plan_ratio"] = memory.get(
        "geometric_plan_bytes_ratio", 1.0
    )
    result["prior_memory_worst_plan_ratio"] = memory.get(
        "worst_plan_bytes_ratio", 1.0
    )
    result["acceptance_limits"] = {
        "geometric_planned_time_ratio_max": 1.005,
        "worst_planned_time_ratio_max": 1.03,
        "geometric_serial_time_ratio_max": 1.02,
        "worst_serial_time_ratio_max": 1.05,
        "geometric_plan_bytes_ratio_max": 0.96,
        "worst_plan_bytes_ratio_max": 0.99,
        "geometric_peak_rss_ratio_max": 1.01,
        "worst_peak_rss_ratio_max": 1.03,
        "maximum_scaled_difference": 1.0e-8,
        "prior_memory_geometric_plan_ratio_max": 0.96,
        "prior_memory_worst_plan_ratio_max": 0.99,
    }
    result["accepted"] = (
        memory.get("validation") == "success"
        and result["geometric_planned_time_ratio"] <= 1.005
        and result["worst_planned_time_ratio"] <= 1.03
        and result["geometric_serial_time_ratio"] <= 1.02
        and result["worst_serial_time_ratio"] <= 1.05
        and result["geometric_plan_bytes_ratio"] <= 0.96
        and result["worst_plan_bytes_ratio"] <= 0.99
        and result["geometric_peak_rss_ratio"] <= 1.01
        and result["worst_peak_rss_ratio"] <= 1.03
        and result["maximum_scaled_difference"] <= 1.0e-8
        and result["prior_memory_geometric_plan_ratio"] <= 0.96
        and result["prior_memory_worst_plan_ratio"] <= 0.99
    )
    result["decision_reason"] = (
        "full certified-PCG timing and numerical gates passed; compact CSR row offsets retained"
        if result["accepted"]
        else "correctness passed, but complete solve timing or memory gates were not all met"
    )
except Exception as error:
    result["validation"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"experiment failed safely: {error}"
    print(result["decision_reason"], flush=True)

if not result.get("accepted", False):
    SOURCE.write_text(baseline_source)
    run(["cargo", "fmt", "--all"], check=False)

for key in (
    "geometric_planned_time_ratio",
    "worst_planned_time_ratio",
    "geometric_serial_time_ratio",
    "worst_serial_time_ratio",
    "geometric_plan_bytes_ratio",
    "worst_plan_bytes_ratio",
    "geometric_peak_rss_ratio",
    "worst_peak_rss_ratio",
    "maximum_scaled_difference",
    "prior_memory_geometric_plan_ratio",
    "prior_memory_worst_plan_ratio",
):
    result.setdefault(key, 1.0)
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
update_documents(result)

WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
for stale in (
    ".github/workflows/compact-csr-row-offsets.yml",
    ".github/workflows/compact-csr-row-offsets-v2.yml",
    ".github/workflows/compact-csr-row-offsets-v3.yml",
    ".github/workflows/compact-csr-row-offsets-v4.yml",
    "scripts/compact_csr_row_offsets_gate.py",
    "scripts/compact_csr_row_offsets_gate_v2.py",
    "scripts/compact_csr_row_offsets_gate_v3.py",
    "scripts/compact_csr_row_offsets_gate_v4.py",
):
    Path(stale).unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass

run(["git", "config", "user.name", "github-actions[bot]"])
run(
    [
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ]
)
run(["git", "add", "-A"])
message = (
    "perf: retain compact CSR row offsets after full PCG gate"
    if result.get("accepted", False)
    else "perf: record compact CSR row-offset full-PCG experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push compact CSR full-PCG decision")
