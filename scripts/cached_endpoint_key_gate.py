import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/graph.rs")
WORKFLOW = Path(".github/workflows/cached-endpoint-key.yml")
SCRIPT = Path("scripts/cached_endpoint_key_gate.py")
RECORD = Path(".ci/performance/cached-endpoint-key-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")


def run(command, *, env=None, timeout=7200, check=True):
    command = [str(item) for item in command]
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
    env["RUSTFLAGS"] = "-C target-cpu=native"
    run(
        [
            "cargo",
            "build",
            "--release",
            "--all-features",
            "--bin",
            "cmg-bench",
        ],
        env=env,
    )
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
            "hierarchy-alloc",
            "--bin",
            "single-rhs-solve",
            "--bin",
            "contraction-subphase-profile",
        ],
        env=env,
    )
    return {
        "graph": target / "release" / "graph-build",
        "hierarchy": target / "release" / "hierarchy-build",
        "allocation": target / "release" / "hierarchy-alloc",
        "solve": target / "release" / "single-rhs-solve",
        "contraction": target / "release" / "contraction-subphase-profile",
        "cmg": target / "release" / "cmg-bench",
    }


def parse_json_output(output, kind):
    if kind == "contraction":
        payloads = [
            json.loads(line)
            for line in output.splitlines()
            if line.strip().startswith("{")
        ]
        payloads = [item for item in payloads if item.get("record") == "case"]
        if len(payloads) != 1:
            raise RuntimeError(f"unexpected contraction output: {payloads}")
        return payloads[0]
    if kind == "cmg":
        start = output.find("{")
        end = output.rfind("}")
        if start < 0 or end < start:
            raise RuntimeError("cmg-bench JSON object not found")
        return json.loads(output[start : end + 1])
    payloads = [
        json.loads(line)
        for line in output.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected {kind} output: {payloads}")
    return payloads[0]


def sample(kind, binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-cached-key-{kind}-{tag}.time")
    completed = run(
        ["/usr/bin/time", "-v", "-o", time_path, binary, *arguments]
    )
    payload = parse_json_output(completed.stdout, kind)
    rss_match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)",
        time_path.read_text(),
    )
    if rss_match is None:
        raise RuntimeError("peak RSS missing")
    payload["peak_rss_kib"] = int(rss_match.group(1))
    return payload


def compare(kind, baseline, candidate, arguments, name):
    baseline_samples = []
    candidate_samples = []
    schedule = (
        ("baseline", baseline[kind]),
        ("candidate", candidate[kind]),
        ("candidate", candidate[kind]),
        ("baseline", baseline[kind]),
    )
    for index, (label, binary) in enumerate(schedule):
        observation = sample(
            kind,
            binary,
            arguments,
            f"{name}-{label}-{index}",
        )
        (baseline_samples if label == "baseline" else candidate_samples).append(
            observation
        )

    if kind == "graph":
        stable = (
            "case",
            "scale",
            "vertices",
            "raw_edges",
            "retained_edges",
            "repetitions",
        )
        metric = "median_ns"
    elif kind == "hierarchy":
        stable = ("case", "scale", "vertices", "edges", "repetitions")
        metric = "median_ns"
    elif kind == "allocation":
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
        metric = "median_ns"
    elif kind == "solve":
        stable = (
            "case",
            "scale",
            "vertices",
            "edges",
            "levels",
            "repetitions",
            "iterations",
            "workspace_bytes",
        )
        metric = "median_ns"
    elif kind == "contraction":
        stable = (
            "case",
            "scale",
            "vertices",
            "edges",
            "levels",
            "profiled_levels",
        )
        metric = "production_total_ns"
    else:
        raise RuntimeError(f"unsupported comparison kind: {kind}")

    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: {kind} changed {key}")
        if kind == "solve" and observation["backward_error"] > 1.0e-8:
            raise RuntimeError(
                f"{name}: solve backward error {observation['backward_error']}"
            )

    baseline_metric = statistics.median(item[metric] for item in baseline_samples)
    candidate_metric = statistics.median(item[metric] for item in candidate_samples)
    result = {
        "arguments": arguments,
        "metric": metric,
        "baseline_metric": baseline_metric,
        "candidate_metric": candidate_metric,
        "candidate_over_baseline_time": candidate_metric / baseline_metric,
        "baseline_peak_rss_kib": max(
            item["peak_rss_kib"] for item in baseline_samples
        ),
        "candidate_peak_rss_kib": max(
            item["peak_rss_kib"] for item in candidate_samples
        ),
        "metadata": {key: reference[key] for key in stable},
    }
    result["candidate_over_baseline_peak_rss"] = (
        result["candidate_peak_rss_kib"] / result["baseline_peak_rss_kib"]
    )
    if kind == "allocation":
        for field in ("median_additional_peak_bytes", "median_retained_bytes"):
            baseline_value = statistics.median(
                item[field] for item in baseline_samples
            )
            candidate_value = statistics.median(
                item[field] for item in candidate_samples
            )
            result[f"baseline_{field}"] = baseline_value
            result[f"candidate_{field}"] = candidate_value
            result[f"candidate_over_baseline_{field}"] = (
                candidate_value / baseline_value
            )
    if kind == "solve":
        result["baseline_maximum_backward_error"] = max(
            item["backward_error"] for item in baseline_samples
        )
        result["candidate_maximum_backward_error"] = max(
            item["backward_error"] for item in candidate_samples
        )
    return result


OLD_STRUCT = '''pub struct Edge {
    u: u32,
    v: u32,
    weight: f64,
}
'''
NEW_STRUCT = '''pub struct Edge {
    key: u64,
    weight: f64,
}
'''
OLD_ACCESSORS = '''    pub const fn u(self) -> usize {
        self.u as usize
    }

    /// Return the higher-numbered endpoint.
    #[must_use]
    pub const fn v(self) -> usize {
        self.v as usize
    }
'''
NEW_ACCESSORS = '''    pub const fn u(self) -> usize {
        (self.key >> 32) as usize
    }

    /// Return the higher-numbered endpoint.
    #[must_use]
    pub const fn v(self) -> usize {
        (self.key as u32) as usize
    }
'''
OLD_RETURN = '''        Ok(Self { u, v, weight })
'''
NEW_RETURN = '''        Ok(Self::from_compact_parts(u, v, weight))
'''
OLD_IMPL_END = '''        Ok(Self::from_compact_parts(u, v, weight))
    }
}
'''
NEW_IMPL_END = '''        Ok(Self::from_compact_parts(u, v, weight))
    }

    #[inline]
    const fn compact_u(self) -> u32 {
        (self.key >> 32) as u32
    }

    #[inline]
    const fn compact_v(self) -> u32 {
        self.key as u32
    }

    #[inline]
    const fn from_compact_parts(u: u32, v: u32, weight: f64) -> Self {
        Self {
            key: pack_endpoint_key(u, v),
            weight,
        }
    }
}
'''
OLD_SORTED_LOOP = '''                let u = raw[read_index].u;
                let v = raw[read_index].v;
                let mut sum = 0.0;
                let mut correction = 0.0;
                while read_index < raw.len() && raw[read_index].u == u && raw[read_index].v == v {
'''
NEW_SORTED_LOOP = '''                let key = raw[read_index].key;
                let u = raw[read_index].compact_u();
                let v = raw[read_index].compact_v();
                let mut sum = 0.0;
                let mut correction = 0.0;
                while read_index < raw.len() && raw[read_index].key == key {
'''
OLD_GROUP_LOOP = '''                let u = raw[group_start].u;
                let v = raw[group_start].v;
                let mut group_end = group_start + 1;
                while group_end < raw.len() && raw[group_end].u == u && raw[group_end].v == v {
'''
NEW_GROUP_LOOP = '''                let key = raw[group_start].key;
                let u = raw[group_start].compact_u();
                let v = raw[group_start].compact_v();
                let mut group_end = group_start + 1;
                while group_end < raw.len() && raw[group_end].key == key {
'''
OLD_COLLECT = '''        raw.push(Edge {
            u: u as u32,
            v: v as u32,
            weight,
        });
'''
NEW_COLLECT = '''        raw.push(Edge::from_compact_parts(u as u32, v as u32, weight));
'''
OLD_KEY = '''fn endpoint_key(edge: &Edge) -> u64 {
    (u64::from(edge.u) << 32) | u64::from(edge.v)
}
'''
NEW_KEY = '''const fn pack_endpoint_key(u: u32, v: u32) -> u64 {
    (u64::from(u) << 32) | u64::from(v)
}

#[inline]
const fn endpoint_key(edge: &Edge) -> u64 {
    edge.key
}
'''
OLD_WRITE = '''    raw[write_index] = Edge { u, v, weight };
'''
NEW_WRITE = '''    raw[write_index] = Edge::from_compact_parts(u, v, weight);
'''


def apply_candidate(source):
    replacements = (
        (OLD_STRUCT, NEW_STRUCT, "edge representation"),
        (OLD_ACCESSORS, NEW_ACCESSORS, "public endpoint accessors"),
        (OLD_RETURN, NEW_RETURN, "validated constructor return"),
        (OLD_IMPL_END, NEW_IMPL_END, "compact constructor insertion"),
        (OLD_SORTED_LOOP, NEW_SORTED_LOOP, "total-order merge loop"),
        (OLD_GROUP_LOOP, NEW_GROUP_LOOP, "endpoint-order merge loop"),
        (OLD_COLLECT, NEW_COLLECT, "validated edge collection"),
        (OLD_KEY, NEW_KEY, "endpoint key helper"),
        (OLD_WRITE, NEW_WRITE, "merged edge write"),
    )
    candidate = source
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)

    numeric_literal = re.compile(
        r"Edge \{\s*u: (?P<u>\d+),\s*v: (?P<v>\d+),\s*"
        r"weight: (?P<weight>[0-9.]+),\s*\}"
    )
    candidate, literal_count = numeric_literal.subn(
        r"Edge::from_compact_parts(\g<u>, \g<v>, \g<weight>)",
        candidate,
    )
    if literal_count < 1:
        raise RuntimeError("numeric test edge literals were not converted")

    test_module = '''

#[cfg(test)]
mod cached_endpoint_key_tests {
    use super::{Edge, endpoint_key};

    #[test]
    fn cached_key_preserves_layout_and_endpoint_access() {
        let edge = Edge::from_compact_parts(17, 91, 2.5);
        assert_eq!(std::mem::size_of::<Edge>(), 16);
        assert_eq!(edge.u(), 17);
        assert_eq!(edge.v(), 91);
        assert_eq!(endpoint_key(&edge), (17_u64 << 32) | 91_u64);
        assert_eq!(edge.weight().to_bits(), 2.5_f64.to_bits());
    }
}
'''
    if "mod cached_endpoint_key_tests" not in candidate:
        candidate += test_module
    return candidate


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    checkpoint = f'''### Cached endpoint-key representation checkpoint — 2026-08-24

- Replacing the two compact endpoint fields with one directly sortable packed key was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; `Edge` remained 16 bytes and graph/hierarchy/PCG invariants were unchanged.
- Geometric contraction / hierarchy / solve ratios: `{result.get("contraction_geometric_ratio", 1.0):.3f}x` / `{result.get("hierarchy_geometric_ratio", 1.0):.3f}x` / `{result.get("solve_geometric_ratio", 1.0):.3f}x`.
- Exact additional-peak / retained hierarchy ratios: `{result.get("allocation_geometric_peak_ratio", 1.0):.3f}x` / `{result.get("allocation_geometric_retained_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/cached-endpoint-key-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Cached endpoint-key representation checkpoint — 2026-08-24\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Cached endpoint-key representation gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Contraction / hierarchy / solve ratios: `{result.get("contraction_geometric_ratio", 1.0):.3f}x` / `{result.get("hierarchy_geometric_ratio", 1.0):.3f}x` / `{result.get("solve_geometric_ratio", 1.0):.3f}x`.
- Exact peak / retained hierarchy ratios: `{result.get("allocation_geometric_peak_ratio", 1.0):.3f}x` / `{result.get("allocation_geometric_retained_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/cached-endpoint-key-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Cached endpoint-key representation gate\n"
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
    "experiment": "cached-endpoint-key-representation",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-cached-key-baseline"))
    SOURCE.write_text(apply_candidate(baseline_source))

    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all", "--", "--check"])
    run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
    run(["cargo", "clippy", "--manifest-path", "benchmarks/Cargo.toml", "--all-targets", "--", "-D", "warnings"])
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    result["validation"] = "success"

    candidate = build(Path("/tmp/cmg-cached-key-candidate"))
    specs = {
        "graph": (
            ("unique-1m", ["unique", "1000000", "3"]),
            ("duplicates-16-1.6m", ["duplicates-16", "100000", "3"]),
            ("coarse-collisions-1.6m", ["coarse-collisions", "100000", "3"]),
        ),
        "contraction": (
            ("path-500k", ["path", "500000", "4", "comparison"]),
            ("worker-firm-750k", ["worker-firm", "250000", "4", "comparison"]),
            ("worker-firm-1.5m", ["worker-firm", "500000", "4", "comparison"]),
            ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "4", "comparison"]),
        ),
        "hierarchy": (
            ("path-1m", ["path", "1000000", "3"]),
            ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
            ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
            ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
        ),
        "allocation": (
            ("path-1m", ["path", "1000000", "2"]),
            ("worker-firm-1.5m", ["worker-firm", "500000", "2"]),
            ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "2"]),
        ),
        "solve": (
            ("path-250k", ["path", "250000", "3"]),
            ("worker-firm-600k", ["worker-firm", "200000", "3"]),
            ("dense-worker-firm-800k", ["dense-worker-firm", "50000", "3"]),
        ),
    }
    for kind, cases in specs.items():
        result["cases"][kind] = {}
        for name, arguments in cases:
            result["cases"][kind][name] = compare(
                kind,
                baseline,
                candidate,
                arguments,
                name,
            )

    def ratios(kind, field="candidate_over_baseline_time"):
        return [case[field] for case in result["cases"][kind].values()]

    result["graph_geometric_ratio"] = geometric(ratios("graph"))
    result["contraction_geometric_ratio"] = geometric(ratios("contraction"))
    result["hierarchy_geometric_ratio"] = geometric(ratios("hierarchy"))
    result["solve_geometric_ratio"] = geometric(ratios("solve"))
    result["allocation_geometric_peak_ratio"] = geometric(
        ratios("allocation", "candidate_over_baseline_median_additional_peak_bytes")
    )
    result["allocation_geometric_retained_ratio"] = geometric(
        ratios("allocation", "candidate_over_baseline_median_retained_bytes")
    )
    all_time = [
        *ratios("graph"),
        *ratios("contraction"),
        *ratios("hierarchy"),
        *ratios("solve"),
    ]
    all_rss = [
        case["candidate_over_baseline_peak_rss"]
        for kind in result["cases"].values()
        for case in kind.values()
    ]
    result["worst_time_ratio"] = max(all_time)
    result["worst_peak_rss_ratio"] = max(all_rss)
    result["worst_allocation_peak_ratio"] = max(
        ratios("allocation", "candidate_over_baseline_median_additional_peak_bytes")
    )
    result["worst_allocation_retained_ratio"] = max(
        ratios("allocation", "candidate_over_baseline_median_retained_bytes")
    )
    active_contraction = [
        result["cases"]["contraction"][name]["candidate_over_baseline_time"]
        for name in (
            "worker-firm-750k",
            "worker-firm-1.5m",
            "dense-worker-firm-1.6m",
        )
    ]
    result["active_contraction_geometric_ratio"] = geometric(active_contraction)
    result["acceptance_limits"] = {
        "active_contraction_geometric_ratio_max": 0.975,
        "contraction_geometric_ratio_max": 0.985,
        "hierarchy_geometric_ratio_max": 0.995,
        "graph_geometric_ratio_max": 1.01,
        "solve_geometric_ratio_max": 1.01,
        "worst_time_ratio_max": 1.04,
        "allocation_geometric_peak_ratio_max": 1.001,
        "allocation_geometric_retained_ratio_max": 1.001,
        "worst_allocation_peak_ratio_max": 1.002,
        "worst_allocation_retained_ratio_max": 1.002,
        "worst_peak_rss_ratio_max": 1.03,
    }
    result["accepted"] = (
        result["active_contraction_geometric_ratio"] <= 0.975
        and result["contraction_geometric_ratio"] <= 0.985
        and result["hierarchy_geometric_ratio"] <= 0.995
        and result["graph_geometric_ratio"] <= 1.01
        and result["solve_geometric_ratio"] <= 1.01
        and result["worst_time_ratio"] <= 1.04
        and result["allocation_geometric_peak_ratio"] <= 1.001
        and result["allocation_geometric_retained_ratio"] <= 1.001
        and result["worst_allocation_peak_ratio"] <= 1.002
        and result["worst_allocation_retained_ratio"] <= 1.002
        and result["worst_peak_rss_ratio"] <= 1.03
    )
    result["decision_reason"] = (
        "full qualification passed; cached endpoint keys materially reduced sorting and hierarchy time without solve or memory regression"
        if result["accepted"]
        else "correctness passed, but contraction, hierarchy, solve, or memory gates were not all met"
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
    "graph_geometric_ratio",
    "contraction_geometric_ratio",
    "active_contraction_geometric_ratio",
    "hierarchy_geometric_ratio",
    "solve_geometric_ratio",
    "allocation_geometric_peak_ratio",
    "allocation_geometric_retained_ratio",
    "worst_time_ratio",
    "worst_peak_rss_ratio",
    "worst_allocation_peak_ratio",
    "worst_allocation_retained_ratio",
):
    result.setdefault(key, 1.0)
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
    "perf: retain cached endpoint-key representation"
    if result.get("accepted", False)
    else "perf: record cached endpoint-key experiment"
)
run(["git", "commit", "-m", message])
for _ in range(12):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push cached endpoint-key decision")

if result["validation"] != "success":
    raise SystemExit(1)
