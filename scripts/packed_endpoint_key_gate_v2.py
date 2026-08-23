"""Repair and rerun the packed endpoint-key retain/revert gate."""

import ast
import base64
import gzip
import subprocess

HISTORICAL_SCRIPT_COMMIT = "a8bfeac905f9e70ce30aa1c53759e7c45f5f33e3"
wrapper = subprocess.check_output(
    [
        "git",
        "show",
        f"{HISTORICAL_SCRIPT_COMMIT}:scripts/packed_endpoint_key_gate.py",
    ],
    text=True,
)
module = ast.parse(wrapper)
payload = None
for statement in module.body:
    if isinstance(statement, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "PAYLOAD"
        for target in statement.targets
    ):
        payload = ast.literal_eval(statement.value)
        break
if not isinstance(payload, str):
    raise SystemExit("historical packed-key PAYLOAD was not found")
source = gzip.decompress(base64.b64decode(payload)).decode("utf-8")

replacements = [
    (
        'WORKFLOW = Path(".github/workflows/packed-endpoint-key.yml")',
        'WORKFLOW = Path(".github/workflows/packed-endpoint-key-v2.yml")',
    ),
    (
        'SCRIPT = Path("scripts/packed_endpoint_key_gate.py")',
        'SCRIPT = Path("scripts/packed_endpoint_key_gate_v2.py")',
    ),
]
for old, new in replacements:
    if source.count(old) != 1:
        raise SystemExit(f"packed-key v2 path anchor was not unique: {old}")
    source = source.replace(old, new, 1)

start = source.index("def prepare_parallel_benchmark() -> None:\n")
continuation = source.index(
    '    source = Path("benchmarks/src/bin/hierarchy-build.rs").read_text()\n',
    start,
)
new_prefix = '''def prepare_parallel_benchmark() -> None:
    dependency = 'cmg = { path = "..", features = ["parallel"] }'
    if MANIFEST_ORIGINAL.count(dependency) != 1:
        raise RuntimeError("benchmark parallel dependency anchor was not unique")
    manifest = MANIFEST_ORIGINAL + "\\n\\n[[bin]]\\nname = \\\"hierarchy-build-parallel\\\"\\npath = \\\"src/bin/hierarchy-build-parallel.rs\\\"\\n"
    BENCH_MANIFEST.write_text(manifest)

'''
source = source[:start] + new_prefix + source[continuation:]

old = '''    if "### Packed endpoint-key checkpoint — 2026-08-23" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
'''
new = '''    checkpoint_pattern = re.compile(
        r"### Packed endpoint-key checkpoint — 2026-08-23\\n.*?(?=## Current next action\\n)",
        re.DOTALL,
    )
    if checkpoint_pattern.search(plan):
        plan = checkpoint_pattern.sub(checkpoint, plan, count=1)
    else:
        plan = plan.replace(marker, checkpoint + marker, 1)
'''
if source.count(old) != 1:
    raise SystemExit("packed-key checkpoint replacement anchor was not unique")
source = source.replace(old, new, 1)

exec(compile(source, "<packed_endpoint_key_gate_v2>", "exec"), {"__name__": "__main__"})
