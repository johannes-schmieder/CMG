from pathlib import Path

SOURCE = Path("scripts/routed_fused_walk_scratch_gate.py")
text = SOURCE.read_text()

opening = "constants = r'''HELPER_INSERT_MARKER = '''"
closing = "\n'''\ntext = text[:constants_start] + constants + text[constants_end:]"
if text.count(opening) != 1:
    raise SystemExit("routed constants opening delimiter changed unexpectedly")
if text.count(closing) != 1:
    raise SystemExit("routed constants closing delimiter changed unexpectedly")
text = text.replace(
    opening,
    'constants = r"""HELPER_INSERT_MARKER = \'\'\'',
    1,
)
text = text.replace(
    closing,
    '\n"""\ntext = text[:constants_start] + constants + text[constants_end:]',
    1,
)
text = text.replace(
    "routed_fused_walk_scratch_gate.py",
    "routed_fused_walk_scratch_gate_v3.py",
)
text = text.replace(
    "routed-fused-walk-scratch.yml",
    "routed-fused-walk-scratch-v3.yml",
)

required = (
    'constants = r"""HELPER_INSERT_MARKER',
    "routed_fused_walk_scratch_gate_v3.py",
    "routed-fused-walk-scratch-v3.yml",
    "should_use_fused_diameter_scratch",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"routed v3 gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
