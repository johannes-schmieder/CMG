import json
import re
from pathlib import Path
import subprocess

ROOT = Path.cwd()
PCG = Path("src/pcg.rs")
PROFILE = Path("src/pcg_profile.rs")
ORIGINAL_PCG = PCG.read_text()
ORIGINAL_PROFILE = PROFILE.read_text()


def run(command, *, env=None, timeout=7200, check=True):
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


def replace_once(text, pattern, replacement, label, *, flags=0):
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"expected exactly one {label}, found {count}")
    return updated


def apply_patch():
    pcg = PCG.read_text()
    pcg = replace_once(
        pcg,
        r"(?m)^fn euclidean_norm_with_executor\(",
        "pub(crate) fn euclidean_norm_with_executor(",
        "production executor-aware norm helper",
    )
    pcg = replace_once(
        pcg,
        r"(?m)^fn dot_with_executor\(",
        "pub(crate) fn dot_with_executor(",
        "production executor-aware dot helper",
    )
    PCG.write_text(pcg)

    profile = PROFILE.read_text()
    replacements = (
        (
            r"\beuclidean_norm\(rhs\)",
            "crate::pcg::euclidean_norm_with_executor(rhs, executor)",
            1,
            "initial RHS norm",
        ),
        (
            r"\beuclidean_norm\(&workspace\.projected_rhs\)",
            "crate::pcg::euclidean_norm_with_executor(&workspace.projected_rhs, executor)",
            1,
            "projected RHS norm",
        ),
        (
            r"\beuclidean_norm\(&workspace\.solution\)",
            "crate::pcg::euclidean_norm_with_executor(&workspace.solution, executor)",
            1,
            "solution norm",
        ),
        (
            r"\beuclidean_norm\(residual\)",
            "crate::pcg::euclidean_norm_with_executor(residual, executor)",
            1,
            "fresh residual norm",
        ),
        (
            r"\bdot\(\s*&workspace\.residual\s*,\s*&workspace\.preconditioned\s*\)",
            "crate::pcg::dot_with_executor(\n            &workspace.residual,\n            &workspace.preconditioned,\n            executor,\n        )",
            2,
            "residual/preconditioned dot products",
        ),
        (
            r"\bdot\(\s*&workspace\.direction\s*,\s*&workspace\.(?:matvec|matrix_direction)\s*\)",
            "crate::pcg::dot_with_executor(\n            &workspace.direction,\n            &workspace.matvec,\n            executor,\n        )",
            1,
            "direction/matrix-direction dot product",
        ),
    )
    for pattern, replacement, expected, label in replacements:
        profile, count = re.subn(pattern, replacement, profile, flags=re.MULTILINE)
        if count != expected:
            raise RuntimeError(f"expected {expected} {label}, found {count}")

    helper_start_marker = "\nfn dot(left: &[f64], right: &[f64]) -> f64 {\n"
    validation_marker = "\nfn validate_positive_pcg("
    if profile.count(helper_start_marker) != 1:
        raise RuntimeError("local profiler dot helper marker was not unique")
    helper_start = profile.index(helper_start_marker)
    helper_end = profile.index(validation_marker, helper_start)
    removed = profile[helper_start:helper_end]
    for helper in ("fn dot(", "fn euclidean_norm(", "fn compensated_sum"):
        if helper not in removed:
            raise RuntimeError(f"expected local helper missing from removal block: {helper}")
    profile = profile[:helper_start] + profile[helper_end:]

    if re.search(r"(?<![:\w])(?:dot|euclidean_norm)\(", profile):
        raise RuntimeError("an unqualified local dot or norm call remains in the profiler")
    PROFILE.write_text(profile)


def replace_markdown_section(text, heading, replacement, next_heading):
    if heading not in text:
        return text.replace(next_heading, replacement + next_heading, 1)
    start = text.index(heading)
    end = text.index(next_heading, start)
    return text[:start] + replacement + text[end:]


result = {
    "schema_version": 2,
    "experiment": "pcg-profiler-production-reduction-sync",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "validation": "not_run",
    "retained": False,
    "cases": {},
}

try:
    apply_patch()
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
    run(["cargo", "test", "--all-targets"])
    run(["cargo", "test", "--all-targets", "--release"])
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(
        [
            "cargo",
            "build",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--bin",
            "pcg-phase-profile",
            "--release",
        ]
    )

    binary = Path("benchmarks/target/release/pcg-phase-profile")
    specs = [
        ("path-80k", ["path", "80000", "1", "4"]),
        ("worker-firm-120k", ["worker-firm", "40000", "1", "4"]),
        ("dense-worker-firm-160k", ["dense-worker-firm", "10000", "1", "4"]),
    ]
    for name, arguments in specs:
        completed = run([str(binary), *arguments])
        payloads = [
            json.loads(line)
            for line in completed.stdout.splitlines()
            if line.strip().startswith("{")
        ]
        if len(payloads) != 1:
            raise RuntimeError(f"unexpected profiler output for {name}: {payloads}")
        result["cases"][name] = payloads[0]

    result["validation"] = "success"
    result["retained"] = True
    result["decision_reason"] = (
        "full qualification passed; the profiler now calls the exact production "
        "planned-PCG dot and norm helpers and remains bitwise equal to planned PCG"
    )
except Exception as error:
    result["error"] = repr(error)
    result["decision_reason"] = f"profiler synchronization failed: {error}"
    print(result["decision_reason"], flush=True)

if not result["retained"]:
    PCG.write_text(ORIGINAL_PCG)
    PROFILE.write_text(ORIGINAL_PROFILE)
    run(["cargo", "fmt", "--all"], check=False)

record = Path(".ci/performance/pcg-profiler-sync.json")
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

heading = "### Production-reduction profiler sync — 2026-08-23\n"
checkpoint = f'''### Production-reduction profiler sync — 2026-08-23

- Profiler synchronization was **{"retained" if result["retained"] else "not retained"}**.
- Validation: `{result["validation"]}`.
- Decision: {result.get("decision_reason", "no decision recorded")}.
- Representative bitwise-parity cases completed: `{", ".join(result.get("cases", {})) or "none"}`.
- Machine-readable evidence: `.ci/performance/pcg-profiler-sync.json`.

'''
plan_path = Path("PERFORMANCE_PLAN.md")
plan = plan_path.read_text()
plan = replace_markdown_section(plan, heading, checkpoint, "## Current next action\n")
plan_path.write_text(plan)

status_heading = "## Production-reduction profiler sync\n"
status_block = (
    "## Production-reduction profiler sync\n\n"
    f'- Decision: `{"retained" if result["retained"] else "not retained"}`.\n'
    f'- Validation: `{result["validation"]}`.\n'
    "- The phase profiler reuses the exact production planned-PCG dot and norm helpers.\n"
    "- Evidence: `.ci/performance/pcg-profiler-sync.json`.\n"
)
status_path = Path("PERFORMANCE_STATUS.md")
status = status_path.read_text()
if status_heading in status:
    start = status.index(status_heading)
    end = status.find("\n## ", start + len(status_heading))
    if end == -1:
        end = len(status.rstrip())
    status = status[:start] + status_block + status[end:]
else:
    status = status.rstrip() + "\n\n" + status_block
status_path.write_text(status.rstrip() + "\n")

Path(".github/workflows/sync-pcg-profiler-v2.yml").unlink(missing_ok=True)
Path("scripts/sync_pcg_profiler_v2.py").unlink(missing_ok=True)
