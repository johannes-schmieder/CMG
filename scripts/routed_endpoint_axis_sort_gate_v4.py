from pathlib import Path
import subprocess

SOURCE_COMMIT = "29f510fbdbc35d91f7362da58f53de20a10428e2"
SOURCE_PATH = "scripts/routed_endpoint_axis_sort_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "routed_endpoint_axis_sort_gate.py",
    "routed_endpoint_axis_sort_gate_v4.py",
)
text = text.replace(
    "routed-endpoint-axis-sort.yml",
    "routed-endpoint-axis-sort-v4.yml",
)

old_constant = """OLD_TEST_HELPER_CALL = '''    sort_compact_edge_endpoints(raw);
'''
NEW_SORT = '''const ENDPOINT_AXIS_SORT_MIN_EDGES: usize = 2_000_000;
"""
new_constant = """OLD_TEST_HELPER_CALL = '''    sort_compact_edge_endpoints(raw);
'''
OLD_DIRECT_TEST_CALL = '''        sort_compact_edge_endpoints(&mut compact);
'''
NEW_SORT = '''const ENDPOINT_AXIS_SORT_MIN_EDGES: usize = 2_000_000;
"""
if text.count(old_constant) != 1:
    raise SystemExit("historical routed constant marker changed unexpectedly")
text = text.replace(old_constant, new_constant, 1)

old_apply_checks = """    if source.count(OLD_TEST_HELPER_CALL) != 1:
        raise RuntimeError("test-only compact sorter call changed unexpectedly")
    candidate = source.replace(OLD_SORT, NEW_SORT, 1)
"""
new_apply_checks = """    if source.count(OLD_TEST_HELPER_CALL) != 1:
        raise RuntimeError("test-only compact sorter call changed unexpectedly")
    if source.count(OLD_DIRECT_TEST_CALL) != 1:
        raise RuntimeError("direct compact-path test call changed unexpectedly")
    candidate = source.replace(OLD_SORT, NEW_SORT, 1)
"""
if text.count(old_apply_checks) != 1:
    raise SystemExit("historical routed apply checks changed unexpectedly")
text = text.replace(old_apply_checks, new_apply_checks, 1)

old_apply_tail = """    candidate = candidate.replace(
        OLD_TEST_HELPER_CALL,
        "    sort_packed_endpoint_keys(raw);\\n",
        1,
    )
    if "mod routed_endpoint_axis_sort_tests" not in candidate:
"""
new_apply_tail = """    candidate = candidate.replace(
        OLD_TEST_HELPER_CALL,
        "    sort_packed_endpoint_keys(raw);\\n",
        1,
    )
    candidate = candidate.replace(
        OLD_DIRECT_TEST_CALL,
        "        sort_compact_edge_endpoints(6, &mut compact);\\n",
        1,
    )
    if "mod routed_endpoint_axis_sort_tests" not in candidate:
"""
if text.count(old_apply_tail) != 1:
    raise SystemExit("historical routed apply tail changed unexpectedly")
text = text.replace(old_apply_tail, new_apply_tail, 1)

required = (
    "routed_endpoint_axis_sort_gate_v4.py",
    "routed-endpoint-axis-sort-v4.yml",
    "OLD_DIRECT_TEST_CALL",
    "sort_compact_edge_endpoints(6, &mut compact)",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"routed endpoint v4 gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
