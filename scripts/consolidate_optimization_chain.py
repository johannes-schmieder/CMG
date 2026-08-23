import json
from pathlib import Path
import subprocess

RECORDS = {
    "profiler_sync": Path(".ci/performance/pcg-profiler-sync.json"),
    "post_reduction_profile": Path(
        ".ci/performance/pcg-phase-profile-post-reductions.json"
    ),
    "norm_sum": Path(".ci/performance/fixed-chunk-norm-sum-latest.json"),
    "vector_updates": Path(
        ".ci/performance/parallel-pcg-vector-updates-latest.json"
    ),
    "compact_index_analysis": Path(
        ".ci/performance/compact-hierarchy-index-analysis.json"
    ),
}

loaded = {}
for name, path in RECORDS.items():
    if not path.exists():
        print(f"waiting for prerequisite record: {path}")
        raise SystemExit(0)
    loaded[name] = json.loads(path.read_text())

ready = (
    loaded["profiler_sync"].get("retained") is True
    and loaded["profiler_sync"].get("validation") == "success"
    and loaded["post_reduction_profile"].get("status") == "success"
    and loaded["norm_sum"].get("validation") == "success"
    and loaded["vector_updates"].get("validation") == "success"
    and loaded["compact_index_analysis"].get("analysis")
    == "compact-hierarchy-index-layout"
)
if not ready:
    print("optimization chain records exist but are not all resolved successfully")
    raise SystemExit(0)

head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
summary = {
    "schema_version": 1,
    "checkpoint": "post-reduction-single-rhs-optimization-chain",
    "source_sha": head,
    "profiler_sync": {
        "retained": loaded["profiler_sync"].get("retained"),
        "validation": loaded["profiler_sync"].get("validation"),
    },
    "post_reduction_profile": {
        "status": loaded["post_reduction_profile"].get("status"),
        "case_count": len(loaded["post_reduction_profile"].get("cases", {})),
    },
    "norm_sum": {
        "accepted": loaded["norm_sum"].get("accepted"),
        "validation": loaded["norm_sum"].get("validation"),
        "planned_geometric_time_ratio": loaded["norm_sum"].get(
            "planned_geometric_time_ratio"
        ),
        "maximum_scaled_solution_difference": loaded["norm_sum"].get(
            "maximum_candidate_scaled_solution_difference"
        ),
    },
    "vector_updates": {
        "accepted": loaded["vector_updates"].get("accepted"),
        "validation": loaded["vector_updates"].get("validation"),
        "planned_geometric_time_ratio": loaded["vector_updates"].get(
            "planned_geometric_time_ratio"
        ),
    },
    "compact_index_analysis": {
        "selected": loaded["compact_index_analysis"].get("selected"),
        "candidate_count": loaded["compact_index_analysis"].get(
            "candidate_count"
        ),
    },
    "records": {name: path.as_posix() for name, path in RECORDS.items()},
}
record = Path(".ci/performance/optimization-chain-latest.json")
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

selected = summary["compact_index_analysis"]["selected"]
selected_text = (
    f'`{selected["struct"]}.{selected["field"]}` in `{selected["path"]}`'
    if selected
    else "none"
)
status_block = f'''## Post-reduction optimization-chain checkpoint

- Recovery SHA at consolidation: `{head}`.
- Production-reduction profiler synchronization: `success`.
- Fresh post-reduction phase profiles: `{summary["post_reduction_profile"]["case_count"]}` cases.
- Deterministic fixed-chunk norm sum: `{"retained" if summary["norm_sum"]["accepted"] else "not retained"}`; planned geometric ratio `{summary["norm_sum"]["planned_geometric_time_ratio"]}`.
- Exact parallel PCG vector updates: `{"retained" if summary["vector_updates"]["accepted"] else "not retained"}`; planned geometric ratio `{summary["vector_updates"]["planned_geometric_time_ratio"]}`.
- Next compact hierarchy-index candidate: {selected_text}.
- Consolidated evidence: `.ci/performance/optimization-chain-latest.json`.
'''
status_path = Path("PERFORMANCE_STATUS.md")
status = status_path.read_text().rstrip()
heading = "## Post-reduction optimization-chain checkpoint\n"
if heading in status:
    start = status.index(heading)
    end = status.find("\n## ", start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + status_block + status[end:]
else:
    status += "\n\n" + status_block
status_path.write_text(status.rstrip() + "\n")

plan_checkpoint = f'''### Consolidated post-reduction checkpoint — 2026-08-23

- Recovery SHA before consolidation: `{head}`.
- Norm-sum decision: **{"retained" if summary["norm_sum"]["accepted"] else "not retained"}**.
- Exact vector-update decision: **{"retained" if summary["vector_updates"]["accepted"] else "not retained"}**.
- Next compact-index candidate: {selected_text}.
- Consolidated evidence: `.ci/performance/optimization-chain-latest.json`.

'''
plan_path = Path("PERFORMANCE_PLAN.md")
plan = plan_path.read_text()
heading = "### Consolidated post-reduction checkpoint — 2026-08-23\n"
marker = "## Current next action\n"
if heading in plan:
    start = plan.index(heading)
    end = plan.index(marker, start)
    plan = plan[:start] + plan_checkpoint + plan[end:]
else:
    plan = plan.replace(marker, plan_checkpoint + marker, 1)
plan_path.write_text(plan)

for path in (
    ".github/workflows/consolidate-optimization-chain.yml",
    "scripts/consolidate_optimization_chain.py",
    ".github/workflows/sync-pcg-profiler-v2.yml",
    "scripts/sync_pcg_profiler_v2.py",
    ".github/workflows/sync-pcg-profiler-v3.yml",
    "scripts/sync_pcg_profiler_v3.py",
    ".github/workflows/profile-post-reductions.yml",
    "scripts/profile_post_reductions.py",
    ".github/workflows/fixed-chunk-norm-sum.yml",
    "scripts/fixed_chunk_norm_sum_gate.py",
    ".github/workflows/parallel-pcg-vector-updates.yml",
    "scripts/parallel_pcg_vector_updates_gate.py",
    ".github/workflows/analyze-compact-hierarchy-indices.yml",
    "scripts/analyze_compact_hierarchy_indices.py",
):
    Path(path).unlink(missing_ok=True)
