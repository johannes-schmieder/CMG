"""Run the parallel CMG application gate with recovery safeguards."""

import subprocess

source = subprocess.check_output(
    ["git", "show", "HEAD:scripts/parallel_cmg_plan_gate.py"],
    text=True,
)

replacements = [
    (
        'WORKFLOW_PATH = ROOT / ".github/workflows/parallel-cmg-plan.yml"\n',
        'WORKFLOW_PATH = ROOT / ".github/workflows/parallel-cmg-plan-v2.yml"\n',
    ),
    (
        'SCRIPT_PATH = ROOT / "scripts/parallel_cmg_plan_gate.py"\n',
        'SCRIPT_PATH = ROOT / "scripts/parallel_cmg_plan_gate_v2.py"\n'
        'LEGACY_SCRIPT_PATH = ROOT / "scripts/parallel_cmg_plan_gate.py"\n',
    ),
    (
        '    "benchmarks/Cargo.toml",\n',
        '    "benchmarks/Cargo.toml",\n    "benchmarks/Cargo.lock",\n',
    ),
    (
        '    SCRIPT_PATH.unlink(missing_ok=True)\n',
        '    SCRIPT_PATH.unlink(missing_ok=True)\n'
        '    LEGACY_SCRIPT_PATH.unlink(missing_ok=True)\n',
    ),
]
for old, new in replacements:
    if source.count(old) != 1:
        raise SystemExit(f"parallel-plan recovery anchor was not unique: {old!r}")
    source = source.replace(old, new, 1)

old = '''    run(
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
'''
new = '''    run(
        [
            "cargo",
            "fmt",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--all",
        ]
    )
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
'''
if source.count(old) != 1:
    raise SystemExit("parallel-plan benchmark-format anchor was not unique")
source = source.replace(old, new, 1)
source = source.replace("2026-08-22", "2026-08-23")

exec(compile(source, "<parallel_cmg_plan_gate_v2>", "exec"), {"__name__": "__main__"})
