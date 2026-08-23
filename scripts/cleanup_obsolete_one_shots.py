"""Remove completed one-shot automation and refresh recovery documents."""

import json
from pathlib import Path
import re
import subprocess

record = json.loads(
    Path(".ci/performance/exact-capacity-contraction-latest.json").read_text()
)
if record.get("validation") != "success" or record.get("accepted"):
    raise SystemExit("expected a successfully qualified rejected exact-capacity gate")

head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()

obsolete_workflows = (
    "analyze-csr-index-layout.yml",
    "apply-final-qualification.yml",
    "apply-numeric-hardening.yml",
    "assess-compact-csr-feasibility.yml",
    "assess-compact-label-feasibility.yml",
    "batch-throughput-memory-smoke.yml",
    "compact-csr-columns.yml",
    "compact-label-vector.yml",
    "finalize-release.yml",
    "format.yml",
    "inplace-edge-compaction.yml",
    "large-hierarchy-smoke.yml",
    "record-benchmark-interface.yml",
    "record-hierarchy-build-baseline.yml",
    "reuse-forest-split-scratch.yml",
    "summarize-hierarchy-baseline.yml",
    "thread-count-smoke.yml",
)
obsolete_scripts = (
    "apply_final_qualification.py",
    "apply_numeric_hardening.py",
    "compact_csr_columns.py",
    "compact_label_vector.py",
    "finalize_release_docs.py",
    "reuse_forest_split_scratch.py",
)

missing = []
for name in obsolete_workflows:
    path = Path(".github/workflows") / name
    if not path.exists():
        missing.append(path.as_posix())
for name in obsolete_scripts:
    path = Path("scripts") / name
    if not path.exists():
        missing.append(path.as_posix())
if missing:
    raise SystemExit(f"cleanup inventory changed; missing: {missing}")

plan_path = Path("PERFORMANCE_PLAN.md")
plan = plan_path.read_text()
checkpoint = f'''### One-shot automation cleanup — 2026-08-23

- Removed `{len(obsolete_workflows)}` completed self-removing experiment,
  qualification, formatting, and smoke-test workflows that had remained after
  concurrent CI commits.
- Removed `{len(obsolete_scripts)}` corresponding source-transformation scripts.
- All numerical decisions and benchmark evidence remain in Git history and in
  `.ci/performance/`; persistent workflows are now limited to ordinary Rust CI,
  serial performance, parallel performance, and pinned-C comparison.
- Production numerical source was unchanged.

'''
marker = "## Current next action\n"
if "### One-shot automation cleanup — 2026-08-23" not in plan:
    if marker not in plan:
        raise SystemExit("PERFORMANCE_PLAN current-action marker missing")
    plan = plan.replace(marker, checkpoint + marker, 1)
plan = re.sub(
    r"## Current next action\n.*\Z",
    '''## Current next action

1. Extend `README.md` and benchmark documentation with automatic, explicit
   within-solve, and explicit across-RHS execution guidance, thread selection,
   workspace budgeting, and reproducible benchmark commands.
2. Add read-only per-phase hierarchy instrumentation to identify whether forest
   construction, splitting, contraction, or terminal factorization dominates
   the remaining setup cost on large worker–firm graphs.
3. Begin another production optimization only after that profile identifies a
   stable bottleneck and a baseline/candidate retain-revert gate is prepared.
4. Obtain controlled 8–32-thread and high-memory evidence when suitable hardware
   is available.
''',
    plan,
    flags=re.DOTALL,
)
plan_path.write_text(plan)

status_path = Path("PERFORMANCE_STATUS.md")
status = status_path.read_text()
recovery = f'''## Current recovery point

- Repository head before this cleanup: `{head}`.
- Latest retained numerical checkpoint:
  `701036624e312fa4a8e21a26297d8254b7dc0142`
  (`perf: retain packed endpoint-key ordering after exact gate`).
- The routed exact-capacity candidate completed full qualification but was
  rejected and reverted; production numerical source remains on the previously
  cross-platform-qualified one-pass contraction path.
- `.ci/latest.json` records quality and Ubuntu/macOS/Windows success for
  `bcc4f0ec3b46b692b055a7c5aef54de5f48768fb`, which contains the retained
  packed-key implementation. Subsequent commits changed only documentation,
  benchmark evidence, or completed one-shot automation.

'''
status = re.sub(
    r"## Current recovery point\n.*?(?=## Retained performance work\n)",
    recovery,
    status,
    count=1,
    flags=re.DOTALL,
)
next_section = '''## Next prepared optimization

No numerical candidate is currently staged. The next step is measurement and
user-facing consolidation:

1. document the retained automatic and explicit parallel execution APIs,
   memory budgeting, and benchmark commands;
2. instrument hierarchy setup phases read-only on large path, sparse
   worker–firm, and dense worker–firm graphs;
3. prepare a retain/revert gate only for the phase that dominates measured
   end-to-end setup time.

'''
status = re.sub(
    r"## Next prepared optimization\n.*?(?=## Remaining major work\n)",
    next_section,
    status,
    count=1,
    flags=re.DOTALL,
)
remaining = '''## Remaining major work

- Extend user-facing memory/performance guidance for automatic, explicit
  within-solve, and explicit across-RHS execution.
- Identify the remaining hierarchy-setup bottleneck with read-only phase timing.
- Obtain controlled 8-, 16-, and 32-thread/high-memory evidence on suitable
  hardware; ordinary hosted runners currently expose only four logical CPUs.

'''
status = re.sub(
    r"## Remaining major work\n.*?(?=## Recovery rule\n)",
    remaining,
    status,
    count=1,
    flags=re.DOTALL,
)
status_path.write_text(status)

for name in obsolete_workflows:
    (Path(".github/workflows") / name).unlink()
for name in obsolete_scripts:
    (Path("scripts") / name).unlink()
for path in (
    Path(".github/workflows/cleanup-obsolete-one-shots.yml"),
    Path("scripts/cleanup_obsolete_one_shots.py"),
):
    path.unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass
