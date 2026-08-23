"""Record the packed-key decision and contraction-survival profile."""

import json
from pathlib import Path
import re
import subprocess

profile = json.loads(
    Path(".ci/performance/contraction-survival-profile.json").read_text()
)
rows = profile["rows"]
lookup = {(row["case"], row["level"]): row for row in rows}
path0 = lookup[("path", 0)]
worker0 = lookup[("worker-firm", 0)]
worker1 = lookup[("worker-firm", 1)]
dense0 = lookup[("dense-worker-firm", 0)]
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()

plan_path = Path("PERFORMANCE_PLAN.md")
plan = plan_path.read_text()
checkpoint = f'''### Contraction edge-survival profile — 2026-08-23

- This was a read-only benchmark; production numerical source was unchanged.
- First path level: `{path0['survival_ratio']:.3f}` of edges survive,
  `{path0['potential_saved_bytes'] / 1_000_000:.1f}` MB of temporary compact-edge
  reservation is avoidable, and the median counting scan was
  `{path0['median_count_scan_ns'] / 1_000_000:.3f}` ms.
- First worker–firm level: `{worker0['survival_ratio']:.3f}` survive,
  `{worker0['potential_saved_bytes'] / 1_000_000:.1f}` MB is avoidable, and the
  scan was `{worker0['median_count_scan_ns'] / 1_000_000:.3f}` ms. The next
  level still has `{worker1['potential_saved_bytes'] / 1_000_000:.1f}` MB of
  avoidable reservation.
- First dense worker–firm level: `{dense0['survival_ratio']:.3f}` survive, so an
  unconditional second pass would add work for only
  `{dense0['potential_saved_bytes'] / 1_000_000:.1f}` MB of potential savings.
- The next candidate must be routed by edge count and contraction ratio;
  dense/high-survival levels keep the one-pass path.
- Machine-readable evidence:
  `.ci/performance/contraction-survival-profile.json`.

'''
marker = "## Current next action\n"
if "### Contraction edge-survival profile — 2026-08-23" not in plan:
    if marker not in plan:
        raise SystemExit("live-plan current-action marker missing")
    plan = plan.replace(marker, checkpoint + marker, 1)
plan = re.sub(
    r"## Current next action\n.*\Z",
    '''## Current next action

1. Benchmark an exact-capacity serial contraction path when the fine edge count
   is at least 250,000 and the coarse/fine vertex ratio is at most 0.5.
2. For executor builds, test the same routed counting pass followed by serial
   exact-capacity mapping and deterministic parallel sorting; retain it only if
   four-thread timing stays within the existing gate and memory improves.
3. Audit and remove obsolete one-shot workflows and staging scripts after the
   routed contraction decision is closed.
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

- Repository head before this status refresh: `{head}`.
- Latest retained numerical checkpoint:
  `701036624e312fa4a8e21a26297d8254b7dc0142`
  (`perf: retain packed endpoint-key ordering after exact gate`).
- The packed-key candidate passed full serial/all-feature qualification and the
  exact requested-allocation gate; the subsequent contraction profile was
  benchmark-only and did not alter numerical source.
- `.ci/latest.json` predates the retained packed-key source. This status commit
  intentionally triggers formatting, Clippy, rustdoc, benchmark-crate checks,
  debug/release tests, release build, and Ubuntu/macOS/Windows testing on the
  exact retained implementation.
- Do not begin another production numerical mutation until that record reports
  quality and cross-platform status `success`.

'''
status = re.sub(
    r"## Current recovery point\n.*?(?=## Retained performance work\n)",
    recovery,
    status,
    count=1,
    flags=re.DOTALL,
)
retained_anchor = (
    "- Graph construction compacts validated duplicate edges in retained 16-byte\n"
    "  storage and trims unused capacity before levels are retained.\n"
)
retained_line = (
    "- Canonical edge sorting compares one packed 64-bit endpoint key; exact\n"
    "  allocation and retained bytes are unchanged.\n"
)
if retained_line not in status:
    if retained_anchor not in status:
        raise SystemExit("retained-work anchor missing")
    status = status.replace(retained_anchor, retained_anchor + retained_line, 1)
evidence_anchor = (
    "- Maximum scaled solution difference and retained workspace excess were both\n"
    "  exactly zero in the benchmark matrix.\n"
)
evidence = f'''- Packed endpoint ordering improved the original hierarchy timing gate to
  `0.970x`; the exact-allocation recheck measured `0.981x` geometrically, with
  exactly `1.000000x` additional-peak and retained requested bytes.
- The contraction survival profile found `{path0['potential_saved_bytes'] / 1_000_000:.1f}` MB
  and `{worker0['potential_saved_bytes'] / 1_000_000:.1f}` MB of avoidable first-level
  reservation on path and worker–firm cases, but only
  `{dense0['potential_saved_bytes'] / 1_000_000:.1f}` MB on dense worker–firm.
'''
if evidence not in status:
    if evidence_anchor not in status:
        raise SystemExit("measured-evidence anchor missing")
    status = status.replace(evidence_anchor, evidence_anchor + evidence, 1)
remaining = '''## Remaining major work

- Complete ordinary Ubuntu/macOS/Windows qualification of the retained packed
  endpoint-key source.
- Qualify the routed exact-capacity contraction candidate.
- Extend user-facing memory/performance guidance for automatic, explicit
  within-solve, and explicit across-RHS execution.
- Obtain controlled 8-, 16-, and 32-thread/high-memory evidence on suitable
  hardware; ordinary hosted runners currently expose only four logical CPUs.
- Remove obsolete self-removing workflows and staging scripts after active gates
  are secure.

'''
status = re.sub(
    r"## Remaining major work\n.*?(?=## Recovery rule\n)",
    remaining,
    status,
    count=1,
    flags=re.DOTALL,
)
status_path.write_text(status)

for path in (
    Path(".github/workflows/checkpoint-contraction-profile.yml"),
    Path(".github/workflows/checkpoint-contraction-profile-v2.yml"),
    Path("scripts/checkpoint_contraction_profile.py"),
    Path(".ci/performance/packed-endpoint-key-exact-v2-run-status.json"),
):
    path.unlink(missing_ok=True)
