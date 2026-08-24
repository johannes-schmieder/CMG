from pathlib import Path

SOURCE = Path("scripts/cached_endpoint_key_gate.py")
text = SOURCE.read_text()

text = text.replace(
    "cached_endpoint_key_gate.py",
    "cached_endpoint_key_gate_v2.py",
)
text = text.replace(
    "cached-endpoint-key.yml",
    "cached-endpoint-key-v2.yml",
)

old_pack = '''const fn pack_endpoint_key(u: u32, v: u32) -> u64 {
    (u64::from(u) << 32) | u64::from(v)
}
'''
new_pack = '''const fn pack_endpoint_key(u: u32, v: u32) -> u64 {
    ((u as u64) << 32) | v as u64
}
'''
if text.count(old_pack) != 1:
    raise SystemExit("cached-key const packing marker changed unexpectedly")
text = text.replace(old_pack, new_pack, 1)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
try:
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path("scripts/cached_endpoint_key_gate.py").unlink(missing_ok=True)
try:
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("cached-key cleanup marker changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "cached_endpoint_key_gate_v2.py",
    "cached-endpoint-key-v2.yml",
    "((u as u64) << 32) | v as u64",
    'Path("scripts/cached_endpoint_key_gate.py").unlink',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"cached-key v2 launcher missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
