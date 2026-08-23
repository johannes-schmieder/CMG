import ast
import json
import os
from pathlib import Path
import subprocess

ROOT = Path.cwd()
HISTORICAL_SCRIPT_COMMIT = '35344303031b6ea97cd57df147f9cf55648d7cb0'
HISTORICAL_SCRIPT_PATH = 'scripts/compact_aggregation_labels_gate.py'
RECORD = Path('.ci/performance/compact-labels-pcg-diagnostic.json')
SELF = Path('scripts/diagnose_compact_labels_pcg.py')
WORKFLOW = Path('.github/workflows/diagnose-compact-labels-pcg.yml')
SOURCE_PATHS = (Path('src/coarsen.rs'), Path('src/forest.rs'), Path('src/hierarchy.rs'))


def run(command, *, env=None, timeout=9000, check=True):
    command = [str(item) for item in command]
    print('+', ' '.join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end='')
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
        )
    return completed


def build(target):
    env = os.environ.copy()
    env['CARGO_TARGET_DIR'] = str(target)
    run([
        'cargo', 'build', '--release',
        '--manifest-path', 'benchmarks/Cargo.toml',
        '--bin', 'full-pcg-routing',
    ], env=env)
    return target / 'release' / 'full-pcg-routing'


def sample(binary, tag):
    completed = run([binary, 'path', '250000', '3', '4'])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'{tag}: unexpected output {payloads}')
    return payloads[0]


def apply_historical_candidate():
    source = subprocess.check_output(
        ['git', 'show', f'{HISTORICAL_SCRIPT_COMMIT}:{HISTORICAL_SCRIPT_PATH}'],
        text=True,
    )
    tree = ast.parse(source, filename=HISTORICAL_SCRIPT_PATH)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == 'apply_candidate'
    )
    namespace = {
        'COARSEN': Path('src/coarsen.rs'),
        'FOREST': Path('src/forest.rs'),
        'HIERARCHY': Path('src/hierarchy.rs'),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), HISTORICAL_SCRIPT_PATH, 'exec'), namespace)
    namespace['apply_candidate']()


result = {
    'schema_version': 1,
    'experiment': 'compact-labels-path-pcg-diagnostic',
    'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'status': 'not_run',
}

try:
    baseline_binary = build(Path('/tmp/cmg-label-pcg-baseline'))
    result['baseline_runs'] = [sample(baseline_binary, f'baseline-{index}') for index in range(2)]

    apply_historical_candidate()
    run(['cargo', 'fmt', '--all'])
    run(['cargo', 'clippy', '--all-targets', '--all-features', '--', '-D', 'warnings'])
    run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
    candidate_binary = build(Path('/tmp/cmg-label-pcg-candidate'))
    result['candidate_runs'] = [sample(candidate_binary, f'candidate-{index}') for index in range(2)]

    fields = (
        'levels', 'operators', 'plan_bytes', 'workspace_bytes',
        'serial_iterations', 'planned_iterations', 'serial_backward_error',
        'planned_backward_error', 'serial_residual_norm', 'planned_residual_norm',
        'max_scaled_difference',
    )
    result['comparison'] = {
        field: {
            'baseline': [item[field] for item in result['baseline_runs']],
            'candidate': [item[field] for item in result['candidate_runs']],
        }
        for field in fields
    }
    result['status'] = 'success'
except Exception as error:
    result['status'] = 'failure'
    result['error'] = repr(error)
    print(f'diagnostic failed: {error}', flush=True)
finally:
    run(['git', 'checkout', '--', *SOURCE_PATHS], check=False)
    run(['cargo', 'fmt', '--all'], check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
SELF.unlink(missing_ok=True)
WORKFLOW.unlink(missing_ok=True)
