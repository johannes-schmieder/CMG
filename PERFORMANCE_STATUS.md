# CMG performance status

This file is generated from repository evidence so optimization work can
resume without relying on a chat transcript.

- Current source SHA at generation: `f8516e159bef38f0372f6ba07bcb327aa0ff595d`
- CI marker tested SHA: `91ac682dc8e577c1f35e621e11fd97f2dda3ef63`
- CI quality status: `success`
- CI cross-platform status: `success`

## Benchmark-gated source decisions

| Evidence | Decision | Reason |
|---|---|---|
| `.ci/performance/aggregation-kernel-latest.json` | **not retained** |  |
| `.ci/performance/compact-centering-metadata-latest.json` | **accepted** |  |
| `.ci/performance/compatible-apply-latest.json` | **accepted** |  |
| `.ci/performance/inplace-level-output-latest.json` | **accepted** |  |
| `.ci/performance/prevalidated-pcg-apply-latest.json` | **not retained** |  |
| `.ci/performance/recursive-centering-latest.json` | **accepted** |  |

## Qualification and baseline records

| Evidence | Benchmark | Status |
|---|---|---|
| `.ci/performance/c-kernel-latest.json` | `c-kernel-latest` | `recorded` |
| `.ci/performance/cycle-wiring-latest.json` | `cycle-wiring-latest` | `recorded` |
| `.ci/performance/latest.json` | `latest` | `recorded` |
| `.ci/performance/parallel-latest.json` | `parallel-latest` | `recorded` |

## Current workflow and staging inventory

### Workflows

- `.github/workflows/apply-final-qualification.yml`
- `.github/workflows/apply-numeric-hardening.yml`
- `.github/workflows/c-kernel.yml`
- `.github/workflows/consolidate-performance-status.yml`
- `.github/workflows/finalize-release.yml`
- `.github/workflows/format.yml`
- `.github/workflows/inplace-edge-compaction.yml`
- `.github/workflows/large-hierarchy-smoke.yml`
- `.github/workflows/optimize-terminal-factor-memory.yml`
- `.github/workflows/parallel-performance.yml`
- `.github/workflows/performance.yml`
- `.github/workflows/record-benchmark-interface.yml`
- `.github/workflows/record-hierarchy-build-baseline.yml`
- `.github/workflows/repair-terminal-factor-evaluator.yml`
- `.github/workflows/reuse-forest-split-scratch.yml`
- `.github/workflows/rust.yml`
- `.github/workflows/summarize-hierarchy-baseline.yml`
- `.github/workflows/thread-count-smoke.yml`

### Staging scripts

- `scripts/apply_final_qualification.py`
- `scripts/apply_numeric_hardening.py`
- `scripts/finalize_release_docs.py`
- `scripts/inplace_edge_compaction.py`
- `scripts/inplace_edge_compaction_v2.py`
- `scripts/optimize_terminal_factor_memory.py`
- `scripts/reuse_forest_split_scratch.py`
- `scripts/terminal_factor_experiment.py`

## Recovery rule

Before another production source mutation, read the corresponding JSON
decision record, verify that no one-shot workflow for that experiment is
still active, and create a new baseline/candidate gate. Do not infer that
a candidate was retained merely because its staging commit exists.
