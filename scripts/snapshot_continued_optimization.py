import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
FINAL_TRIGGER = Path('.ci/performance/packed-edge-sort-latest.json')
if not FINAL_TRIGGER.exists():
    print('packed endpoint-key decision is unresolved; leaving snapshot armed')
    raise SystemExit(0)

record_paths = {
    'profiler_sync': '.ci/performance/pcg-profiler-sync.json',
    'post_reduction_profile': '.ci/performance/pcg-phase-profile-post-reductions.json',
    'norm_sum': '.ci/performance/fixed-chunk-norm-sum-latest.json',
    'vector_updates': '.ci/performance/parallel-pcg-vector-updates-latest.json',
    'compact_index_analysis': '.ci/performance/compact-hierarchy-index-analysis.json',
    'compact_index': '.ci/performance/compact-hierarchy-index-latest.json',
    'packed_sort_analysis': '.ci/performance/packed-edge-sort-analysis.json',
    'packed_sort': '.ci/performance/packed-edge-sort-latest.json',
    'cross_platform_ci': '.ci/latest.json',
}
records = {}
for name, raw_path in record_paths.items():
    path = Path(raw_path)
    if path.exists():
        records[name] = json.loads(path.read_text())

head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
summary = {
    'schema_version': 1,
    'checkpoint': 'continued-single-rhs-and-hierarchy-optimization',
    'source_sha': head,
    'records': records,
    'record_paths': record_paths,
}
summary_path = Path('.ci/performance/continued-optimization-status.json')
summary_path.parent.mkdir(parents=True, exist_ok=True)
summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')


def decision(name):
    record = records.get(name, {})
    if 'accepted' in record:
        return 'retained' if record.get('accepted') else 'not retained'
    if 'retained' in record:
        return 'retained' if record.get('retained') else 'not retained'
    return record.get('status') or record.get('validation') or 'missing'


def ratio(name, key):
    value = records.get(name, {}).get(key)
    return 'n/a' if value is None else f'{value:.3f}x'

selected = records.get('compact_index_analysis', {}).get('selected')
selected_text = (
    f'`{selected.get("struct")}.{selected.get("field")}`'
    if selected else 'none'
)
ci = records.get('cross_platform_ci', {})
block = f'''## Continued optimization recovery snapshot

- Snapshot source SHA: `{head}`.
- Latest recorded tested SHA: `{ci.get('tested_sha', 'missing')}`.
- Cross-platform quality: `{ci.get('quality', 'missing')}`; tests: `{ci.get('cross_platform_tests', 'missing')}`.
- Profiler synchronization: `{decision('profiler_sync')}`.
- Deterministic norm-sum gate: `{decision('norm_sum')}`; planned ratio `{ratio('norm_sum', 'planned_geometric_time_ratio')}`.
- Exact vector-update gate: `{decision('vector_updates')}`; planned ratio `{ratio('vector_updates', 'planned_geometric_time_ratio')}`.
- Compact hierarchy-index candidate: {selected_text}; decision `{decision('compact_index')}`; peak-RSS ratio `{ratio('compact_index', 'geometric_candidate_over_baseline_peak_rss')}`.
- Packed endpoint-key sort: `{decision('packed_sort')}`; setup ratio `{ratio('packed_sort', 'geometric_candidate_over_baseline_time')}`.
- Full machine-readable recovery state: `.ci/performance/continued-optimization-status.json`.
'''
status_path = Path('PERFORMANCE_STATUS.md')
status = status_path.read_text().rstrip()
heading = '## Continued optimization recovery snapshot\n'
if heading in status:
    start = status.index(heading)
    end = status.find('\n## ', start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + block + status[end:]
else:
    status += '\n\n' + block
status_path.write_text(status.rstrip() + '\n')

plan_checkpoint = f'''### Continued optimization recovery snapshot — 2026-08-23

- Recovery SHA: `{head}`.
- Norm-sum decision: **{decision('norm_sum')}**.
- Exact vector-update decision: **{decision('vector_updates')}**.
- Compact hierarchy-index decision: **{decision('compact_index')}**.
- Packed endpoint-key sort decision: **{decision('packed_sort')}**.
- Machine-readable recovery state: `.ci/performance/continued-optimization-status.json`.

'''
plan_path = Path('PERFORMANCE_PLAN.md')
plan = plan_path.read_text()
heading = '### Continued optimization recovery snapshot — 2026-08-23\n'
marker = '## Current next action\n'
if heading in plan:
    start = plan.index(heading)
    end = plan.index(marker, start)
    plan = plan[:start] + plan_checkpoint + plan[end:]
else:
    plan = plan.replace(marker, plan_checkpoint + marker, 1)
plan_path.write_text(plan)

temporary_paths = (
    '.github/workflows/snapshot-continued-optimization.yml',
    'scripts/snapshot_continued_optimization.py',
    '.github/workflows/sync-pcg-profiler-v2.yml',
    'scripts/sync_pcg_profiler_v2.py',
    '.github/workflows/sync-pcg-profiler-v3.yml',
    'scripts/sync_pcg_profiler_v3.py',
    '.github/workflows/resolve-profiler-sync-failure.yml',
    'scripts/resolve_profiler_sync_failure.py',
    '.github/workflows/profile-post-reductions.yml',
    'scripts/profile_post_reductions.py',
    '.github/workflows/fixed-chunk-norm-sum.yml',
    'scripts/fixed_chunk_norm_sum_gate.py',
    '.github/workflows/parallel-pcg-vector-updates.yml',
    'scripts/parallel_pcg_vector_updates_gate.py',
    '.github/workflows/resolve-failed-optimization-gates.yml',
    'scripts/resolve_failed_optimization_gates.py',
    '.github/workflows/analyze-compact-hierarchy-indices.yml',
    'scripts/analyze_compact_hierarchy_indices.py',
    '.github/workflows/consolidate-optimization-chain.yml',
    'scripts/consolidate_optimization_chain.py',
    '.github/workflows/compact-hierarchy-index-v2.yml',
    'scripts/compact_hierarchy_index_gate_v2.py',
    '.github/workflows/compact-hierarchy-index-metadata-fallback.yml',
    'scripts/compact_hierarchy_index_metadata_fallback.py',
    '.github/workflows/analyze-packed-edge-sort.yml',
    'scripts/analyze_packed_edge_sort.py',
    '.github/workflows/packed-edge-sort-gate.yml',
    'scripts/packed_edge_sort_gate.py',
)
for raw_path in temporary_paths:
    Path(raw_path).unlink(missing_ok=True)
