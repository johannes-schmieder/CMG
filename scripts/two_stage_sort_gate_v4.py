from pathlib import Path
import subprocess

SOURCE_COMMIT = 'd48ff583059b37164e33679dc177f9fafe116ccf'
SOURCE_PATH = 'scripts/two_stage_sort_gate_v3.py'

text = subprocess.check_output(
    ['git', 'show', f'{SOURCE_COMMIT}:{SOURCE_PATH}'],
    text=True,
)
text = text.replace(
    "def build(target):\n    env = os.environ.copy()",
    "def build(target):\n    target = Path(target)\n    env = os.environ.copy()",
    1,
)
text = text.replace('two_stage_sort_gate_v3', 'two_stage_sort_gate_v4')
text = text.replace('two-stage-sort-gate-v3', 'two-stage-sort-gate-v4')
text = text.replace('two-stage-sort-v3', 'two-stage-sort-v4')
text = text.replace('cmg-two-stage-v3', 'cmg-two-stage-v4')

if 'target = Path(target)' not in text:
    raise SystemExit('launcher repair was not applied')

compiled = compile(text, 'scripts/two_stage_sort_gate_v4.py', 'exec')
exec(compiled, {'__name__': '__main__', '__file__': __file__})
