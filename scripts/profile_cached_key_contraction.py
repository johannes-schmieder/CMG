import json
import math
from pathlib import Path
import subprocess

ROOT = Path.cwd()
BENCH = Path("benchmarks/src/bin/contraction-subphase-profile.rs")
WORKFLOW = Path(".github/workflows/profile-cached-key-contraction.yml")
SCRIPT = Path("scripts/profile_cached_key_contraction.py")
RECORD = Path(".ci/performance/contraction-subphase-profile-cached-key.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")


def run(command, *, timeout=7200, check=True):
    command = [str(item) for item in command]
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
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


OLD_STRUCT = '''struct ProbeEdge {
    u: u32,
    v: u32,
    weight: f64,
}
'''
NEW_STRUCT = '''struct ProbeEdge {
    key: u64,
    weight: f64,
}

impl ProbeEdge {
    #[inline]
    const fn from_parts(u: u32, v: u32, weight: f64) -> Self {
        Self {
            key: ((u as u64) << 32) | v as u64,
            weight,
        }
    }

    #[inline]
    const fn u(self) -> u32 {
        (self.key >> 32) as u32
    }

    #[inline]
    const fn v(self) -> u32 {
        self.key as u32
    }
}
'''
OLD_KEY = '''fn endpoint_key(edge: &ProbeEdge) -> u64 {
    (u64::from(edge.u) << 32) | u64::from(edge.v)
}
'''
NEW_KEY = '''fn endpoint_key(edge: &ProbeEdge) -> u64 {
    edge.key
}
'''
OLD_MAP = '''        mapped.push(ProbeEdge {
            u: u32::try_from(u).expect("coarse endpoint fits u32"),
            v: u32::try_from(v).expect("coarse endpoint fits u32"),
            weight: edge.weight(),
        });
'''
NEW_MAP = '''        mapped.push(ProbeEdge::from_parts(
            u32::try_from(u).expect("coarse endpoint fits u32"),
            u32::try_from(v).expect("coarse endpoint fits u32"),
            edge.weight(),
        ));
'''
OLD_MERGE = '''        let u = raw[read].u;
        let v = raw[read].v;
        let mut sum = 0.0;
        let mut correction = 0.0;
        while read < raw.len() && raw[read].u == u && raw[read].v == v {
            compensated_add(&mut sum, &mut correction, raw[read].weight);
            read += 1;
        }
        raw[write] = ProbeEdge {
            u,
            v,
            weight: sum + correction,
        };
'''
NEW_MERGE = '''        let key = raw[read].key;
        let mut sum = 0.0;
        let mut correction = 0.0;
        while read < raw.len() && raw[read].key == key {
            compensated_add(&mut sum, &mut correction, raw[read].weight);
            read += 1;
        }
        raw[write] = ProbeEdge {
            key,
            weight: sum + correction,
        };
'''
OLD_DIAGONAL = '''        diagonal[edge.u as usize] += edge.weight;
        diagonal[edge.v as usize] += edge.weight;
'''
NEW_DIAGONAL = '''        diagonal[edge.u() as usize] += edge.weight;
        diagonal[edge.v() as usize] += edge.weight;
'''
OLD_VERIFY = '''        assert_eq!(candidate.u as usize, reference.u());
        assert_eq!(candidate.v as usize, reference.v());
'''
NEW_VERIFY = '''        assert_eq!(candidate.u() as usize, reference.u());
        assert_eq!(candidate.v() as usize, reference.v());
'''


def patched(source):
    replacements = (
        (OLD_STRUCT, NEW_STRUCT, "probe representation"),
        (OLD_KEY, NEW_KEY, "probe endpoint key"),
        (OLD_MAP, NEW_MAP, "probe mapping"),
        (OLD_MERGE, NEW_MERGE, "probe merge"),
        (OLD_DIAGONAL, NEW_DIAGONAL, "probe diagonal"),
        (OLD_VERIFY, NEW_VERIFY, "probe verification"),
    )
    candidate = source
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    return candidate


def parse_case(output):
    payloads = [
        json.loads(line)
        for line in output.splitlines()
        if line.strip().startswith("{")
    ]
    cases = [item for item in payloads if item.get("record") == "case"]
    if len(cases) != 1:
        raise RuntimeError(f"unexpected profile case output: {cases}")
    return cases[0]


def update_documents(result):
    rows = []
    for name, case in result["cases"].items():
        rows.append(
            f"| {name} | {case['production_total_ns']} | "
            f"{case['manual_over_production']:.3f}x | "
            f"{case['mapping_share']:.1%} | {case['sorting_share']:.1%} | "
            f"{case['merge_diagonal_share']:.1%} |"
        )
    checkpoint = f'''### Cached-key contraction profile — 2026-08-24

- The benchmark-only profiler now mirrors the retained packed-key `Edge` layout rather than recomputing endpoint keys.
- Production/manual equivalence passed for every profiled hierarchy level.

| Case | Production contraction ns | Manual/production | Map share | Sort share | Merge+diagonal share |
|---|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

- Weighted mapping / sorting / merge+diagonal / finalize shares: `{result['weighted_mapping_share']:.1%}` / `{result['weighted_sorting_share']:.1%}` / `{result['weighted_merge_diagonal_share']:.1%}` / `{result['weighted_finalize_share']:.1%}`.
- Evidence: `.ci/performance/contraction-subphase-profile-cached-key.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Cached-key contraction profile — 2026-08-24\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Cached-key contraction profile

- Weighted mapping / sorting / merge+diagonal / finalize shares: `{result['weighted_mapping_share']:.1%}` / `{result['weighted_sorting_share']:.1%}` / `{result['weighted_merge_diagonal_share']:.1%}` / `{result['weighted_finalize_share']:.1%}`.
- The benchmark proxy uses the retained cached endpoint-key representation.
- Evidence: `.ci/performance/contraction-subphase-profile-cached-key.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Cached-key contraction profile\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + block + status[end:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")


original = BENCH.read_text()
result = {
    "schema_version": 1,
    "profile": "cached-endpoint-key-contraction-subphases",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "status": "not_run",
    "cases": {},
}
try:
    BENCH.write_text(patched(original))
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all", "--", "--check"])
    run(["cargo", "clippy", "--manifest-path", "benchmarks/Cargo.toml", "--all-targets", "--", "-D", "warnings"])
    run(["cargo", "build", "--release", "--manifest-path", "benchmarks/Cargo.toml", "--bin", "contraction-subphase-profile"])
    binary = Path("benchmarks/target/release/contraction-subphase-profile")
    specs = (
        ("path-500k", ["path", "500000", "7", "comparison"]),
        ("worker-firm-750k", ["worker-firm", "250000", "7", "comparison"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "7", "comparison"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "7", "comparison"]),
    )
    totals = {
        "mapping_ns": 0,
        "sorting_ns": 0,
        "merging_ns": 0,
        "diagonal_ns": 0,
        "finalize_ns": 0,
        "manual_total_ns": 0,
        "production_total_ns": 0,
    }
    for name, arguments in specs:
        observed = parse_case(run([binary, *arguments]).stdout)
        if not 0.80 <= observed["manual_over_production"] <= 1.25:
            raise RuntimeError(
                f"{name}: profile proxy diverged from production: "
                f"{observed['manual_over_production']}"
            )
        manual_total = observed["manual_total_ns"]
        case = dict(observed)
        case["mapping_share"] = observed["mapping_ns"] / manual_total
        case["sorting_share"] = observed["sorting_ns"] / manual_total
        case["merge_diagonal_share"] = (
            observed["merging_ns"] + observed["diagonal_ns"]
        ) / manual_total
        case["finalize_share"] = observed["finalize_ns"] / manual_total
        result["cases"][name] = case
        for key in totals:
            totals[key] += observed[key]
    result["weighted_mapping_share"] = totals["mapping_ns"] / totals["manual_total_ns"]
    result["weighted_sorting_share"] = totals["sorting_ns"] / totals["manual_total_ns"]
    result["weighted_merge_diagonal_share"] = (
        totals["merging_ns"] + totals["diagonal_ns"]
    ) / totals["manual_total_ns"]
    result["weighted_finalize_share"] = totals["finalize_ns"] / totals["manual_total_ns"]
    result["weighted_manual_over_production"] = (
        totals["manual_total_ns"] / totals["production_total_ns"]
    )
    result["totals"] = totals
    result["status"] = "success"
    update_documents(result)
except Exception as error:
    result["status"] = "failure"
    result["error"] = repr(error)
    print(f"cached-key contraction profile failed: {error}", flush=True)
finally:
    BENCH.write_text(original)
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"], check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass

run(["git", "config", "user.name", "github-actions[bot]"])
run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
run(["git", "add", "-A"])
run(["git", "commit", "-m", "perf: record cached-key contraction profile"])
for _ in range(12):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push cached-key contraction profile")

if result["status"] != "success":
    raise SystemExit(1)
