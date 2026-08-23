from pathlib import Path
import subprocess

SOURCE_COMMIT = "6aa71475b2d5e077f217d29177af7928731b566c"
SOURCE_PATH = "scripts/parallel_two_stage_sort_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "parallel_two_stage_sort_gate.py",
    "parallel_two_stage_sort_gate_v2.py",
)
text = text.replace(
    "parallel-two-stage-sort.yml",
    "parallel-two-stage-sort-v2.yml",
)

start = text.index("def apply_candidate(source):")
end = text.index("\n\ndef update_documents", start)
replacement = '''def apply_candidate(source):
    function_start = source.index("    pub(crate) fn from_compact_edges_with_executor(")
    function_end = source.index("    fn from_sorted_raw_edges", function_start)
    function = source[function_start:function_end]
    if function.count(OLD_PARALLEL) != 1:
        raise RuntimeError("compact parallel sort site changed unexpectedly")
    function = function.replace(OLD_PARALLEL, NEW_PARALLEL, 1)
    source = source[:function_start] + function + source[function_end:]
    if source.count(OLD_HELPER) != 1:
        raise RuntimeError("serial two-stage helper changed unexpectedly")
    return source.replace(OLD_HELPER, NEW_HELPER, 1)
'''
text = text[:start] + replacement + text[end:]

required = (
    "parallel_two_stage_sort_gate_v2.py",
    "parallel-two-stage-sort-v2.yml",
    'function_start = source.index("    pub(crate) fn from_compact_edges_with_executor(")',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired gate missing marker: {marker}")

compiled = compile(text, str(Path(__file__)), "exec")
exec(compiled, {"__name__": "__main__", "__file__": __file__})
