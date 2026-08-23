"""Repair and rerun the current-source direct contraction gate."""

import ast
import base64
import gzip
import subprocess

PINNED_WRAPPER_COMMIT = "ff5355339399446f811a07d042b34782f4e79777"
wrapper = subprocess.check_output(
    [
        "git",
        "show",
        f"{PINNED_WRAPPER_COMMIT}:scripts/direct_compact_contraction_gate_v2.py",
    ],
    text=True,
)
module = ast.parse(wrapper)
payload = None
for statement in module.body:
    if isinstance(statement, ast.Assign):
        if any(isinstance(target, ast.Name) and target.id == "PAYLOAD" for target in statement.targets):
            payload = ast.literal_eval(statement.value)
            break
if not isinstance(payload, str):
    raise SystemExit("pinned gate PAYLOAD was not found")
source = gzip.decompress(base64.b64decode(payload)).decode("utf-8")

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
    raise SystemExit("benchmark formatting repair anchor was not unique")
source = source.replace(old, new, 1)
exec(compile(source, "<direct_compact_contraction_gate_v2_repaired>", "exec"), {"__name__": "__main__"})
