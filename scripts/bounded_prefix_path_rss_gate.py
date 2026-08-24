import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/forest.rs")
WORKFLOW = Path(".github/workflows/bounded-prefix-path-rss.yml")
SCRIPT = Path("scripts/bounded_prefix_path_rss_gate.py")
RECORD = Path(".ci/performance/bounded-prefix-path-rss-latest.json")
PRIOR = Path(".ci/performance/bounded-ancestor-prefix-memory-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")

OLD_DECLARATIONS = '''    let mut walk = Vec::new();
    let mut new_ancestors = Vec::new();
'''
NEW_DECLARATIONS = '''    let mut walk = Vec::new();
    let mut ancestor_prefix = [0_u8; 7];
'''
OLD_PATH_STATE = '''            let mut ancestors_in_path = 0_i64;
            walk.clear();
            walk.push(current);
            new_ancestors.clear();
            new_ancestors.push(0_i64);
            let mut k = 0_usize;
'''
NEW_PATH_STATE = '''            let mut ancestors_in_path = 0_u8;
            walk.clear();
            walk.push(current);
            ancestor_prefix[0] = 0;
            let mut k = 0_usize;
'''
OLD_RECORDING = '''                k += 1;
                walk.push(current);
                if visited[current] {
                    new_ancestors.push(ancestors_in_path);
                } else {
                    ancestors_in_path += 1;
                    new_ancestors.push(ancestors_in_path);
                }
'''
NEW_RECORDING = '''                k += 1;
                walk.push(current);
                ancestors_in_path += u8::from(!visited[current]);
                if k < ancestor_prefix.len() {
                    ancestor_prefix[k] = ancestors_in_path;
                } else {
                    debug_assert!(visited[current]);
                }
'''
OLD_UPDATE = '''                    ancestors[vertex] += new_ancestors[index];
'''
NEW_UPDATE = '''                    ancestors[vertex] += i64::from(ancestor_prefix[index.min(6)]);
'''
TEST_MODULE = '''

#[cfg(test)]
mod bounded_ancestor_prefix_tests {
    #[test]
    fn prefix_table_covers_all_unvisited_diameter_steps() {
        let mut prefix = [0_u8; 7];
        let visited = [false, false, true, false, false, true, false, true, true];
        let mut count = 0_u8;
        for index in 1..visited.len() {
            count += u8::from(!visited[index]);
            if index < prefix.len() {
                prefix[index] = count;
            }
        }
        assert_eq!(prefix, [0, 1, 1, 2, 3, 3, 4]);
        assert_eq!(prefix[6], 4);
    }
}
'''


def apply_candidate(source):
    candidate = source
    for old, new, name in (
        (OLD_DECLARATIONS, NEW_DECLARATIONS, "declaration"),
        (OLD_PATH_STATE, NEW_PATH_STATE, "reset"),
        (OLD_RECORDING, NEW_RECORDING, "recording"),
    ):
        if candidate.count(old) != 1:
            raise RuntimeError(f"bounded-prefix {name} marker changed")
        candidate = candidate.replace(old, new, 1)
    if candidate.count(OLD_UPDATE) != 2:
        raise RuntimeError("bounded-prefix update sites changed")
    candidate = candidate.replace(OLD_UPDATE, NEW_UPDATE)
    if "mod bounded_ancestor_prefix_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def run(command, *, env=None, timeout=12000, check=True):
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
            "hierarchy-alloc",
        ],
        env=env,
    )
    return target / "release" / "hierarchy-alloc"


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-bounded-path-rss-{tag}.time")
    completed = run(
        ["/usr/bin/time", "-v", "-o", time_path, binary, *arguments]
    )
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected allocation output: {payloads}")
    match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)",
        time_path.read_text(),
    )
    if match is None:
        raise RuntimeError("peak RSS missing")
    payload = payloads[0]
    payload["peak_rss_kib"] = int(match.group(1))
    return payload


def compare(baseline, candidate, arguments, name):
    baseline_samples = []
    candidate_samples = []
    schedule = (
        ("baseline", baseline),
        ("candidate", candidate),
        ("candidate", candidate),
        ("baseline", baseline),
        ("baseline", baseline),
        ("candidate", candidate),
    )
    for index, (label, binary) in enumerate(schedule):
        observation = sample(binary, arguments, f"{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(
            observation
        )

    stable = (
        "case",
        "scale",
        "vertices",
        "edges",
        "repetitions",
        "levels",
        "hierarchy_matrix_nonzeros",
        "max_post_drop_delta_bytes",
    )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: unstable metadata for {key}")

    baseline_rss = statistics.median(item["peak_rss_kib"] for item in baseline_samples)
    candidate_rss = statistics.median(item["peak_rss_kib"] for item in candidate_samples)
    baseline_ns = statistics.median(item["median_ns"] for item in baseline_samples)
    candidate_ns = statistics.median(item["median_ns"] for item in candidate_samples)
    baseline_peak = statistics.median(
        item["median_additional_peak_bytes"] for item in baseline_samples
    )
    candidate_peak = statistics.median(
        item["median_additional_peak_bytes"] for item in candidate_samples
    )
    baseline_retained = statistics.median(
        item["median_retained_bytes"] for item in baseline_samples
    )
    candidate_retained = statistics.median(
        item["median_retained_bytes"] for item in candidate_samples
    )
    return {
        "arguments": arguments,
        "metadata": {key: reference[key] for key in stable},
        "baseline_median_ns": baseline_ns,
        "candidate_median_ns": candidate_ns,
        "candidate_over_baseline_time": candidate_ns / baseline_ns,
        "baseline_median_peak_rss_kib": baseline_rss,
        "candidate_median_peak_rss_kib": candidate_rss,
        "rss_delta_kib": candidate_rss - baseline_rss,
        "candidate_over_baseline_rss": candidate_rss / baseline_rss,
        "baseline_additional_peak_bytes": baseline_peak,
        "candidate_additional_peak_bytes": candidate_peak,
        "candidate_over_baseline_additional_peak": candidate_peak / baseline_peak,
        "baseline_retained_bytes": baseline_retained,
        "candidate_retained_bytes": candidate_retained,
        "candidate_over_baseline_retained": candidate_retained / baseline_retained,
    }


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    checkpoint = f'''### Bounded ancestor-prefix path-scaling checkpoint — 2026-08-24

- The bounded-prefix candidate was **{decision}** after path RSS scaling from 250k to 4m vertices.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric hierarchy time ratio: `{result.get("geometric_time_ratio", 1.0):.3f}x`.
- Maximum median-RSS delta: `{result.get("maximum_rss_delta_kib", 0):.0f}` KiB; largest-case ratio `{result.get("largest_case_rss_ratio", 1.0):.3f}x`.
- Exact additional-peak / retained ratios: `{result.get("geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/bounded-prefix-path-rss-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Bounded ancestor-prefix path-scaling checkpoint — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Re-profile forest-split subphases if the bounded prefix is retained.\n"
        "2. Run the prepared branch-free diameter-front gate.\n"
        "3. Refresh cumulative retained optimization and memory guidance.\n"
        "4. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Bounded ancestor-prefix path-scaling gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Hierarchy timing ratio: `{result.get("geometric_time_ratio", 1.0):.3f}x`.
- Maximum RSS delta / largest-case ratio: `{result.get("maximum_rss_delta_kib", 0):.0f}` KiB / `{result.get("largest_case_rss_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/bounded-prefix-path-rss-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Bounded ancestor-prefix path-scaling gate\n"
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
prior = json.loads(PRIOR.read_text())
result = {
    "schema_version": 1,
    "experiment": "bounded-prefix-path-rss-scaling",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "cases": {},
    "prior_memory_record": prior,
}

try:
    baseline = build(Path("/tmp/cmg-bounded-path-rss-baseline"))
    SOURCE.write_text(apply_candidate(baseline_source))
    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    result["validation"] = "success"
    candidate = build(Path("/tmp/cmg-bounded-path-rss-candidate"))

    specs = (
        ("path-250k", ["path", "250000", "2"]),
        ("path-500k", ["path", "500000", "2"]),
        ("path-1m", ["path", "1000000", "2"]),
        ("path-2m", ["path", "2000000", "2"]),
        ("path-4m", ["path", "4000000", "2"]),
    )
    for name, arguments in specs:
        result["cases"][name] = compare(baseline, candidate, arguments, name)

    time_ratios = [case["candidate_over_baseline_time"] for case in result["cases"].values()]
    rss_deltas = [case["rss_delta_kib"] for case in result["cases"].values()]
    peak_ratios = [
        case["candidate_over_baseline_additional_peak"]
        for case in result["cases"].values()
    ]
    retained_ratios = [
        case["candidate_over_baseline_retained"]
        for case in result["cases"].values()
    ]
    result["geometric_time_ratio"] = geometric(time_ratios)
    result["worst_time_ratio"] = max(time_ratios)
    result["minimum_rss_delta_kib"] = min(rss_deltas)
    result["maximum_rss_delta_kib"] = max(rss_deltas)
    result["rss_delta_range_kib"] = max(rss_deltas) - min(rss_deltas)
    result["largest_case_rss_ratio"] = result["cases"]["path-4m"][
        "candidate_over_baseline_rss"
    ]
    result["largest_case_rss_delta_bytes_per_vertex"] = (
        1024.0 * result["cases"]["path-4m"]["rss_delta_kib"] / 4000000.0
    )
    result["geometric_additional_peak_ratio"] = geometric(peak_ratios)
    result["worst_additional_peak_ratio"] = max(peak_ratios)
    result["geometric_retained_ratio"] = geometric(retained_ratios)
    result["worst_retained_ratio"] = max(retained_ratios)
    result["max_post_drop_delta_bytes"] = max(
        case["metadata"]["max_post_drop_delta_bytes"]
        for case in result["cases"].values()
    )
    result["acceptance_limits"] = {
        "geometric_time_ratio_max": 0.98,
        "worst_time_ratio_max": 1.01,
        "maximum_rss_delta_kib_max": 6144,
        "rss_delta_range_kib_max": 3072,
        "largest_case_rss_ratio_max": 1.02,
        "largest_case_rss_delta_bytes_per_vertex_max": 1.5,
        "geometric_additional_peak_ratio_max": 1.0,
        "worst_additional_peak_ratio_max": 1.002,
        "geometric_retained_ratio_max": 1.001,
        "worst_retained_ratio_max": 1.001,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        prior.get("validation") == "success"
        and result["geometric_time_ratio"] <= 0.98
        and result["worst_time_ratio"] <= 1.01
        and result["maximum_rss_delta_kib"] <= 6144
        and result["rss_delta_range_kib"] <= 3072
        and result["largest_case_rss_ratio"] <= 1.02
        and result["largest_case_rss_delta_bytes_per_vertex"] <= 1.5
        and result["geometric_additional_peak_ratio"] <= 1.0
        and result["worst_additional_peak_ratio"] <= 1.002
        and result["geometric_retained_ratio"] <= 1.001
        and result["worst_retained_ratio"] <= 1.001
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "the RSS difference is a bounded non-scaling allocator/mapping step, while exact memory is unchanged and hierarchy speed improves materially"
        if result["accepted"]
        else "the RSS difference scales, timing did not reproduce, or exact-memory limits were not met"
    )
except Exception as error:
    result["validation"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"experiment failed safely: {error}"

if not result.get("accepted", False):
    SOURCE.write_text(baseline_source)
    run(["cargo", "fmt", "--all"], check=False)

for key in (
    "geometric_time_ratio",
    "worst_time_ratio",
    "minimum_rss_delta_kib",
    "maximum_rss_delta_kib",
    "rss_delta_range_kib",
    "largest_case_rss_ratio",
    "largest_case_rss_delta_bytes_per_vertex",
    "geometric_additional_peak_ratio",
    "worst_additional_peak_ratio",
    "geometric_retained_ratio",
    "worst_retained_ratio",
):
    result.setdefault(key, 1.0 if "ratio" in key else 0)
result.setdefault("max_post_drop_delta_bytes", 0)
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
update_documents(result)
WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass
run(["git", "config", "user.name", "github-actions[bot]"])
run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
run(["git", "add", "-A"])
message = (
    "perf: retain bounded ancestor prefixes after RSS scaling"
    if result.get("accepted", False)
    else "perf: record bounded-prefix path RSS scaling"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push bounded-prefix path RSS decision")
if result.get("validation") != "success":
    raise SystemExit(1)
