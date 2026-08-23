"""Recover and rerun the prepared parallel solver gate after a test-only repair."""

import subprocess

PINNED_GATE_COMMIT = "c9b90c0f9594dced6ccb1eb7f63efe847bd49d51"
source = subprocess.check_output(
    [
        "git",
        "show",
        f"{PINNED_GATE_COMMIT}:scripts/prepared_parallel_solver_gate.py",
    ],
    text=True,
)

replacements = [
    (
        "WORKFLOW = ROOT / '.github/workflows/prepared-parallel-solver-gate.yml'\n",
        "WORKFLOW = ROOT / '.github/workflows/prepared-parallel-solver-gate-v2.yml'\n",
    ),
    (
        "GATE = ROOT / 'scripts/prepared_parallel_solver_gate.py'\n",
        "GATE = ROOT / 'scripts/prepared_parallel_solver_gate_v2.py'\n",
    ),
    (
        "CANDIDATE = ROOT / 'scripts/add_prepared_parallel_solver.py'\n",
        "CANDIDATE = ROOT / 'scripts/add_prepared_parallel_solver_v2.py'\n",
    ),
]
for old, new in replacements:
    if source.count(old) != 1:
        raise SystemExit(f"prepared solver v2 gate anchor was not unique: {old!r}")
    source = source.replace(old, new, 1)

exec(compile(source, "<prepared_parallel_solver_gate_v2>", "exec"), {"__name__": "__main__"})
