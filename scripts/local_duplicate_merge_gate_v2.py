from pathlib import Path
import subprocess

SOURCE_COMMIT = "e192923a5f8d3dd28c65d8fb57165684e060f54c"
SOURCE_PATH = "scripts/local_duplicate_merge_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace("local_duplicate_merge_gate.py", "local_duplicate_merge_gate_v2.py")
text = text.replace("local-duplicate-merge.yml", "local-duplicate-merge-v2.yml")

old_loop = '''                for edge_index in group_start..group_end {
                    compensated_add(&mut sum, &mut correction, raw[edge_index].weight);
                }
'''
new_loop = '''                for edge in &raw[group_start..group_end] {
                    compensated_add(&mut sum, &mut correction, edge.weight);
                }
'''
if text.count(old_loop) != 1:
    raise SystemExit("historical range-index merge loop changed unexpectedly")
text = text.replace(old_loop, new_loop, 1)

required = (
    "local_duplicate_merge_gate_v2.py",
    "local-duplicate-merge-v2.yml",
    "for edge in &raw[group_start..group_end]",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired local-merge gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
