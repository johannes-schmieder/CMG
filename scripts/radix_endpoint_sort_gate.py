import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/graph.rs")
WORKFLOW = Path(".github/workflows/radix-endpoint-sort.yml")
SCRIPT = Path("scripts/radix_endpoint_sort_gate.py")
RECORD = Path(".ci/performance/radix-endpoint-sort-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")


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
            f"command failed ({completed.returncode}): {' '.join(str(item) for item in command)}"
        )
    return completed


def build_benchmark(target):
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
            "hierarchy-build",
        ],
        env=env,
    )
    return target / "release" / "hierarchy-build"


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-radix-{tag}.time")
    completed = run(
        [
            "/usr/bin/time",
            "-v",
            "-o",
            time_path,
            binary,
            *arguments,
        ]
    )
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected hierarchy benchmark output: {payloads}")
    rss_match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)",
        time_path.read_text(),
    )
    if rss_match is None:
        raise RuntimeError("peak RSS missing from /usr/bin/time output")
    payload = payloads[0]
    payload["peak_rss_kib"] = int(rss_match.group(1))
    return payload


def compare_case(baseline, candidate, arguments, name):
    baseline_samples = []
    candidate_samples = []
    schedule = (
        ("baseline", baseline),
        ("candidate", candidate),
        ("candidate", candidate),
        ("baseline", baseline),
    )
    for index, (label, binary) in enumerate(schedule):
        observation = sample(binary, arguments, f"{name}-{label}-{index}")
        target = baseline_samples if label == "baseline" else candidate_samples
        target.append(observation)

    stable_keys = ("case", "scale", "vertices", "edges", "repetitions")
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable_keys:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: unstable benchmark metadata for {key}")

    baseline_ns = statistics.median(item["median_ns"] for item in baseline_samples)
    candidate_ns = statistics.median(item["median_ns"] for item in candidate_samples)
    baseline_rss = max(item["peak_rss_kib"] for item in baseline_samples)
    candidate_rss = max(item["peak_rss_kib"] for item in candidate_samples)
    return {
        "arguments": arguments,
        "baseline_median_ns": baseline_ns,
        "candidate_median_ns": candidate_ns,
        "candidate_over_baseline_time": candidate_ns / baseline_ns,
        "baseline_peak_rss_kib": baseline_rss,
        "candidate_peak_rss_kib": candidate_rss,
        "candidate_over_baseline_peak_rss": candidate_rss / baseline_rss,
        "metadata": {key: reference[key] for key in stable_keys},
    }


OLD_SORT = '''fn sort_compact_edges_two_stage(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
    let mut start = 0;
    while start < raw.len() {
        let key = endpoint_key(&raw[start]);
        let mut end = start + 1;
        while end < raw.len() && endpoint_key(&raw[end]) == key {
            end += 1;
        }
        if end - start > 1 {
            raw[start..end].sort_unstable_by(|left, right| left.weight.total_cmp(&right.weight));
        }
        start = end;
    }
}
'''

NEW_SORT = '''const RADIX_ENDPOINT_SORT_MIN_ITEMS: usize = 131_072;

fn sort_compact_edges_two_stage(raw: &mut [Edge]) {
    if !endpoint_keys_are_sorted(raw) {
        if raw.len() >= RADIX_ENDPOINT_SORT_MIN_ITEMS {
            radix_sort_endpoint_keys(raw);
        } else {
            raw.sort_unstable_by_key(endpoint_key);
        }
    }
    sort_duplicate_edge_weights(raw);
}

fn endpoint_keys_are_sorted(raw: &[Edge]) -> bool {
    raw.windows(2)
        .all(|pair| endpoint_key(&pair[0]) <= endpoint_key(&pair[1]))
}

fn sort_duplicate_edge_weights(raw: &mut [Edge]) {
    let mut start = 0;
    while start < raw.len() {
        let key = endpoint_key(&raw[start]);
        let mut end = start + 1;
        while end < raw.len() && endpoint_key(&raw[end]) == key {
            end += 1;
        }
        if end - start > 1 {
            raw[start..end].sort_unstable_by(|left, right| left.weight.total_cmp(&right.weight));
        }
        start = end;
    }
}

fn radix_sort_endpoint_keys(raw: &mut [Edge]) {
    if raw.len() < 2 {
        return;
    }

    let reference = endpoint_key(&raw[0]);
    let varying_mask = raw
        .iter()
        .skip(1)
        .fold(0_u64, |mask, edge| mask | (reference ^ endpoint_key(edge)));
    if varying_mask == 0 {
        return;
    }

    let mut scratch = vec![raw[0]; raw.len()];
    let mut source_is_raw = true;
    for shift in (0..64).step_by(8) {
        if ((varying_mask >> shift) & 0xff) == 0 {
            continue;
        }
        if source_is_raw {
            radix_endpoint_pass(raw, &mut scratch, shift);
        } else {
            radix_endpoint_pass(&scratch, raw, shift);
        }
        source_is_raw = !source_is_raw;
    }
    if !source_is_raw {
        raw.copy_from_slice(&scratch);
    }
}

fn radix_endpoint_pass(source: &[Edge], destination: &mut [Edge], shift: usize) {
    debug_assert_eq!(source.len(), destination.len());
    let mut counts = [0_usize; 256];
    for edge in source {
        let digit = ((endpoint_key(edge) >> shift) & 0xff) as usize;
        counts[digit] += 1;
    }

    let mut offsets = [0_usize; 256];
    let mut running = 0_usize;
    for (offset, count) in offsets.iter_mut().zip(counts) {
        *offset = running;
        running += count;
    }

    for &edge in source {
        let digit = ((endpoint_key(&edge) >> shift) & 0xff) as usize;
        destination[offsets[digit]] = edge;
        offsets[digit] += 1;
    }
}
'''

TEST_MODULE = '''

#[cfg(test)]
mod radix_endpoint_sort_tests {
    use super::{
        Edge, compare_raw_edges, endpoint_keys_are_sorted, radix_sort_endpoint_keys,
        sort_duplicate_edge_weights,
    };

    fn generated_edges() -> Vec<Edge> {
        let mut edges = Vec::new();
        for index in 0..8_192_usize {
            let left = (37 * index + 11) % 2_003;
            let mut right = (97 * index + 29) % 2_003;
            if right == left {
                right = (right + 1) % 2_003;
            }
            for duplicate in 0..3_usize {
                let weight = 0.25 + ((index + 19 * duplicate) % 127) as f64 / 64.0;
                edges.push(Edge::from_internal_parts(left, right, weight).unwrap());
            }
        }
        edges.reverse();
        edges
    }

    #[test]
    fn radix_endpoint_sort_matches_reference_total_order() {
        let mut candidate = generated_edges();
        let mut reference = candidate.clone();
        reference.sort_unstable_by(compare_raw_edges);

        radix_sort_endpoint_keys(&mut candidate);
        sort_duplicate_edge_weights(&mut candidate);

        assert_eq!(candidate, reference);
        assert!(endpoint_keys_are_sorted(&candidate));
    }

    #[test]
    fn sorted_fast_path_still_orders_duplicate_weights() {
        let mut edges = vec![
            Edge::from_internal_parts(0, 1, 3.0).unwrap(),
            Edge::from_internal_parts(0, 1, 1.0).unwrap(),
            Edge::from_internal_parts(0, 1, 2.0).unwrap(),
            Edge::from_internal_parts(1, 2, 4.0).unwrap(),
        ];
        assert!(endpoint_keys_are_sorted(&edges));
        sort_duplicate_edge_weights(&mut edges);
        assert_eq!(edges[0].weight(), 1.0);
        assert_eq!(edges[1].weight(), 2.0);
        assert_eq!(edges[2].weight(), 3.0);
    }
}
'''


def apply_candidate(source):
    if OLD_SORT not in source:
        raise RuntimeError("expected compact-edge sort implementation was not found")
    candidate = source.replace(OLD_SORT, NEW_SORT, 1)
    if "mod radix_endpoint_sort_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def update_documents(result):
    accepted = result.get("accepted", False)
    status_word = "retained" if accepted else "not retained"
    geometric_time = result.get("geometric_candidate_over_baseline_time")
    worst_time = result.get("worst_candidate_over_baseline_time")
    worst_rss = result.get("worst_candidate_over_baseline_peak_rss")
    checkpoint = f'''### Radix endpoint-sort checkpoint — 2026-08-23

- Adaptive sorted-input/radix endpoint ordering was **{status_word}**.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric hierarchy-build ratio: `{geometric_time:.3f}x`.
- Worst per-case hierarchy-build ratio: `{worst_time:.3f}x`.
- Worst peak-RSS ratio: `{worst_rss:.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/radix-endpoint-sort-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Radix endpoint-sort checkpoint — 2026-08-23\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Radix endpoint-sort gate

- Decision: `{status_word}`.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric hierarchy-build ratio: `{geometric_time:.3f}x`.
- Worst peak-RSS ratio: `{worst_rss:.3f}x`.
- Evidence: `.ci/performance/radix-endpoint-sort-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Radix endpoint-sort gate\n"
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
result = {
    "schema_version": 1,
    "experiment": "adaptive-radix-endpoint-sort",
    "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "accepted": False,
    "validation": "not_run",
    "cases": {},
}

try:
    baseline_binary = build_benchmark(Path("/tmp/cmg-radix-baseline"))
    SOURCE.write_text(apply_candidate(baseline_source))

    run(["cargo", "fmt", "--all"])
    run(
        [
            "cargo",
            "fmt",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--all",
        ]
    )
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
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    result["validation"] = "success"

    candidate_binary = build_benchmark(Path("/tmp/cmg-radix-candidate"))
    specs = (
        ("path-1m", ["path", "1000000", "1"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "1"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "1"]),
        ("dense-worker-firm-3.2m", ["dense-worker-firm", "200000", "1"]),
    )
    time_ratios = []
    rss_ratios = []
    for name, arguments in specs:
        comparison = compare_case(baseline_binary, candidate_binary, arguments, name)
        result["cases"][name] = comparison
        time_ratios.append(comparison["candidate_over_baseline_time"])
        rss_ratios.append(comparison["candidate_over_baseline_peak_rss"])

    result["geometric_candidate_over_baseline_time"] = math.exp(
        sum(math.log(value) for value in time_ratios) / len(time_ratios)
    )
    result["worst_candidate_over_baseline_time"] = max(time_ratios)
    result["best_candidate_over_baseline_time"] = min(time_ratios)
    result["geometric_candidate_over_baseline_peak_rss"] = math.exp(
        sum(math.log(value) for value in rss_ratios) / len(rss_ratios)
    )
    result["worst_candidate_over_baseline_peak_rss"] = max(rss_ratios)
    result["acceptance_limits"] = {
        "geometric_time_ratio_max": 0.96,
        "worst_time_ratio_max": 1.05,
        "worker_firm_time_ratio_max": 0.97,
        "dense_time_ratio_max": 0.92,
        "worst_peak_rss_ratio_max": 1.20,
    }
    worker_ratio = result["cases"]["worker-firm-1.5m"]["candidate_over_baseline_time"]
    dense_ratio = max(
        result["cases"]["dense-worker-firm-1.6m"]["candidate_over_baseline_time"],
        result["cases"]["dense-worker-firm-3.2m"]["candidate_over_baseline_time"],
    )
    result["accepted"] = (
        result["geometric_candidate_over_baseline_time"] <= 0.96
        and result["worst_candidate_over_baseline_time"] <= 1.05
        and worker_ratio <= 0.97
        and dense_ratio <= 0.92
        and result["worst_candidate_over_baseline_peak_rss"] <= 1.20
    )
    result["decision_reason"] = (
        "full qualification passed and large worker-firm hierarchy setup improved materially"
        if result["accepted"]
        else "correctness passed, but timing or temporary-memory limits were not met"
    )
except Exception as error:
    result["validation"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"experiment failed safely: {error}"
    print(result["decision_reason"], flush=True)

if not result.get("accepted", False):
    SOURCE.write_text(baseline_source)
    run(["cargo", "fmt", "--all"], check=False)

# Ensure document formatting remains possible even when a benchmark failed before ratios existed.
result.setdefault("geometric_candidate_over_baseline_time", 1.0)
result.setdefault("worst_candidate_over_baseline_time", 1.0)
result.setdefault("geometric_candidate_over_baseline_peak_rss", 1.0)
result.setdefault("worst_candidate_over_baseline_peak_rss", 1.0)
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
    "perf: retain adaptive radix endpoint sorting"
    if result.get("accepted", False)
    else "perf: record radix endpoint-sort experiment"
)
run(["git", "commit", "-m", message])
run(["git", "pull", "--rebase", "origin", "main"])
run(["git", "push", "origin", "HEAD:main"])
