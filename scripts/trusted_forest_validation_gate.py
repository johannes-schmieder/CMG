import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
FOREST = Path('src/forest.rs')
SCRIPT = Path('scripts/trusted_forest_validation_gate.py')
WORKFLOW = Path('.github/workflows/trusted-forest-validation-gate.yml')
RECORD = Path('.ci/performance/trusted-forest-validation-latest.json')
STATUS = Path('PERFORMANCE_STATUS.md')
PLAN = Path('PERFORMANCE_PLAN.md')

baseline_source = FOREST.read_text()
source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def run(command, *, env=None, timeout=9000, check=True):
    command = [str(value) for value in command]
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


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label} changed unexpectedly; matches={count}')
    return text.replace(old, new, 1)


def build(target):
    env = os.environ.copy()
    env['CARGO_TARGET_DIR'] = str(target)
    run([
        'cargo', 'build', '--release',
        '--manifest-path', 'benchmarks/Cargo.toml',
        '--bin', 'hierarchy-alloc',
        '--bin', 'full-pcg-routing',
    ], env=env)
    return {
        'hierarchy': target / 'release' / 'hierarchy-alloc',
        'pcg': target / 'release' / 'full-pcg-routing',
    }


def apply_candidate():
    text = FOREST.read_text()
    text = replace_once(
        text,
        '''    let mut final_parent = split_forest(&heavy_parent)?;
    drop(heavy_parent);
''',
        '''    let mut final_parent = split_forest_trusted(&heavy_parent)?;
    drop(heavy_parent);
''',
        'trusted hierarchy split call',
    )
    text = replace_once(
        text,
        '''    forest_component_labels(&final_parent)
''',
        '''    Ok(forest_component_labels_trusted(&final_parent))
''',
        'trusted hierarchy component-label call',
    )
    text = replace_once(
        text,
        '''pub fn split_forest(parent: &[usize]) -> Result<Vec<usize>, CmgError> {
    validate_parent(parent)?;
    let n = parent.len();
''',
        '''pub fn split_forest(parent: &[usize]) -> Result<Vec<usize>, CmgError> {
    split_forest_impl(parent, true)
}

fn split_forest_trusted(parent: &[usize]) -> Result<Vec<usize>, CmgError> {
    split_forest_impl(parent, false)
}

fn split_forest_impl(parent: &[usize], validate: bool) -> Result<Vec<usize>, CmgError> {
    if validate {
        validate_parent(parent)?;
    }
    let n = parent.len();
''',
        'split forest checked/trusted wrapper',
    )
    text = replace_once(
        text,
        '''fn forest_component_labels(parent: &[usize]) -> Result<(Vec<usize>, usize), CmgError> {
    validate_parent(parent)?;
    let n = parent.len();
''',
        '''fn forest_component_labels_trusted(parent: &[usize]) -> (Vec<usize>, usize) {
    let n = parent.len();
''',
        'trusted component-label function header',
    )
    text = replace_once(
        text,
        '''    Ok((labels, aggregate_count))
}

/// Compute deterministic connected components of a functional forest.
''',
        '''    (labels, aggregate_count)
}

/// Compute deterministic connected components of a functional forest.
''',
        'trusted component-label return',
    )

    test = '''

#[cfg(test)]
mod trusted_internal_forest_tests {
    use super::{
        forest_component_labels_trusted, forest_components, split_forest,
        split_forest_trusted,
    };

    #[test]
    fn trusted_internal_paths_match_checked_public_paths() {
        let parent = vec![1, 2, 2, 4, 3, 6, 6, 8, 9, 9, 11, 10];
        let checked_split = split_forest(&parent).unwrap();
        let trusted_split = split_forest_trusted(&parent).unwrap();
        assert_eq!(trusted_split, checked_split);

        let (trusted_labels, aggregate_count) =
            forest_component_labels_trusted(&checked_split);
        let (checked_labels, sizes) = forest_components(&checked_split).unwrap();
        assert_eq!(trusted_labels, checked_labels);
        assert_eq!(aggregate_count, sizes.len());
    }

    #[test]
    fn public_split_still_rejects_out_of_range_parent() {
        assert!(split_forest(&[0, 2]).is_err());
    }
}
'''
    if 'mod trusted_internal_forest_tests' not in text:
        text += test
    FOREST.write_text(text)


def sample(binary, arguments, tag):
    time_path = Path(f'/tmp/cmg-trusted-forest-{tag}.time')
    completed = run([
        '/usr/bin/time', '-v', '-o', time_path,
        binary, *[str(value) for value in arguments],
    ])
    start = completed.stdout.find('{')
    if start < 0:
        raise RuntimeError(f'{tag}: benchmark JSON object missing')
    payload = json.loads(completed.stdout[start:])
    match = re.search(
        r'Maximum resident set size \(kbytes\):\s*(\d+)',
        time_path.read_text(),
    )
    if match is None:
        raise RuntimeError(f'{tag}: peak RSS missing')
    payload['peak_rss_kib'] = int(match.group(1))
    return payload


def alternating_samples(baseline, candidate, arguments, tag):
    observations = {'baseline': [], 'candidate': []}
    order = (
        ('baseline', baseline),
        ('candidate', candidate),
        ('candidate', candidate),
        ('baseline', baseline),
    )
    for index, (label, binary) in enumerate(order):
        observations[label].append(sample(
            binary, arguments, f'{tag}-{label}-{index}'
        ))
    return observations


def median(observations, label, key):
    return statistics.median(item[key] for item in observations[label])


def compare_hierarchy(baseline, candidate, name, case_name, scale):
    observations = alternating_samples(
        baseline, candidate, [case_name, scale, 5], f'hierarchy-{name}'
    )
    stable = (
        'case', 'scale', 'vertices', 'edges', 'levels',
        'hierarchy_matrix_nonzeros', 'max_post_drop_delta_bytes',
    )
    reference = observations['baseline'][0]
    for observation in observations['baseline'][1:] + observations['candidate']:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f'{name}: hierarchy field changed: {key}')

    baseline_time = median(observations, 'baseline', 'median_ns')
    candidate_time = median(observations, 'candidate', 'median_ns')
    baseline_peak = median(observations, 'baseline', 'median_additional_peak_bytes')
    candidate_peak = median(observations, 'candidate', 'median_additional_peak_bytes')
    baseline_retained = median(observations, 'baseline', 'median_retained_bytes')
    candidate_retained = median(observations, 'candidate', 'median_retained_bytes')
    baseline_rss = max(item['peak_rss_kib'] for item in observations['baseline'])
    candidate_rss = max(item['peak_rss_kib'] for item in observations['candidate'])
    return {
        'metadata': {key: reference[key] for key in stable},
        'baseline_median_ns': baseline_time,
        'candidate_median_ns': candidate_time,
        'candidate_over_baseline_time': candidate_time / baseline_time,
        'candidate_over_baseline_additional_peak': candidate_peak / baseline_peak,
        'candidate_over_baseline_retained': candidate_retained / baseline_retained,
        'candidate_over_baseline_peak_rss': candidate_rss / baseline_rss,
    }


def compare_pcg(baseline, candidate, name, case_name, scale):
    left = sample(baseline, [case_name, scale, 5, 4], f'pcg-{name}-baseline')
    right = sample(candidate, [case_name, scale, 5, 4], f'pcg-{name}-candidate')
    stable = (
        'case', 'scale', 'vertices', 'input_edges', 'edges', 'levels',
        'operators', 'plan_bytes', 'workspace_bytes', 'auto_execution',
        'serial_iterations', 'planned_iterations',
    )
    for key in stable:
        if left[key] != right[key]:
            raise RuntimeError(f'{name}: PCG field changed: {key}')
    maximum_difference = max(left['max_scaled_difference'], right['max_scaled_difference'])
    if maximum_difference > 1.0e-8:
        raise RuntimeError(f'{name}: scaled solution difference too large')
    return {
        'metadata': {key: left[key] for key in stable},
        'candidate_over_baseline_serial_time': (
            right['serial_median_ns'] / left['serial_median_ns']
        ),
        'candidate_over_baseline_planned_time': (
            right['planned_median_ns'] / left['planned_median_ns']
        ),
        'maximum_scaled_difference': maximum_difference,
    }


result = {
    'schema_version': 1,
    'experiment': 'trusted-internal-forest-validation-elision',
    'source_sha': source_sha,
    'validation': 'not_run',
    'accepted': False,
    'decision_reason': '',
    'hierarchy_cases': {},
    'pcg_cases': {},
}

try:
    baseline = build(Path('/tmp/cmg-trusted-forest-baseline'))
    apply_candidate()
    run(['cargo', 'fmt', '--all'])
    run(['cargo', 'fmt', '--all', '--', '--check'])
    run([
        'cargo', 'fmt', '--manifest-path', 'benchmarks/Cargo.toml',
        '--all', '--', '--check',
    ])
    run(['cargo', 'clippy', '--all-targets', '--all-features', '--', '-D', 'warnings'])
    run([
        'cargo', 'clippy', '--manifest-path', 'benchmarks/Cargo.toml',
        '--all-targets', '--', '-D', 'warnings',
    ])
    doc_env = os.environ.copy()
    doc_env['RUSTDOCFLAGS'] = '-D warnings'
    run(['cargo', 'doc', '--no-deps', '--document-private-items', '--all-features'], env=doc_env)
    run(['cargo', 'test', '--all-targets', '--all-features'])
    run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
    run(['cargo', 'build', '--release', '--all-features'])
    candidate = build(Path('/tmp/cmg-trusted-forest-candidate'))
    result['validation'] = 'success'

    hierarchy_specs = (
        ('path-1m', 'path', 1_000_000),
        ('worker-firm-1.5m', 'worker-firm', 500_000),
        ('worker-firm-3m', 'worker-firm', 1_000_000),
        ('dense-worker-firm-1.6m', 'dense-worker-firm', 100_000),
    )
    for name, case_name, scale in hierarchy_specs:
        result['hierarchy_cases'][name] = compare_hierarchy(
            baseline['hierarchy'], candidate['hierarchy'], name, case_name, scale
        )

    pcg_specs = (
        ('path-250k', 'path', 250_000),
        ('worker-firm-600k', 'worker-firm', 200_000),
        ('dense-worker-firm-800k', 'dense-worker-firm', 50_000),
    )
    for name, case_name, scale in pcg_specs:
        result['pcg_cases'][name] = compare_pcg(
            baseline['pcg'], candidate['pcg'], name, case_name, scale
        )

    geometric = lambda values: math.exp(
        sum(math.log(value) for value in values) / len(values)
    )
    hierarchy_time = [
        item['candidate_over_baseline_time']
        for item in result['hierarchy_cases'].values()
    ]
    hierarchy_peak = [
        item['candidate_over_baseline_additional_peak']
        for item in result['hierarchy_cases'].values()
    ]
    hierarchy_retained = [
        item['candidate_over_baseline_retained']
        for item in result['hierarchy_cases'].values()
    ]
    hierarchy_rss = [
        item['candidate_over_baseline_peak_rss']
        for item in result['hierarchy_cases'].values()
    ]
    serial_time = [
        item['candidate_over_baseline_serial_time']
        for item in result['pcg_cases'].values()
    ]
    planned_time = [
        item['candidate_over_baseline_planned_time']
        for item in result['pcg_cases'].values()
    ]

    result['hierarchy_geometric_time_ratio'] = geometric(hierarchy_time)
    result['hierarchy_worst_time_ratio'] = max(hierarchy_time)
    result['pcg_serial_geometric_time_ratio'] = geometric(serial_time)
    result['pcg_serial_worst_time_ratio'] = max(serial_time)
    result['pcg_planned_geometric_time_ratio'] = geometric(planned_time)
    result['pcg_planned_worst_time_ratio'] = max(planned_time)
    result['worst_additional_peak_ratio'] = max(hierarchy_peak)
    result['worst_retained_ratio'] = max(hierarchy_retained)
    result['worst_peak_rss_ratio'] = max(hierarchy_rss)
    result['acceptance_limits'] = {
        'hierarchy_geometric_time_ratio_max': 0.99,
        'hierarchy_worst_time_ratio_max': 1.04,
        'pcg_serial_geometric_time_ratio_max': 1.03,
        'pcg_serial_worst_time_ratio_max': 1.08,
        'pcg_planned_geometric_time_ratio_max': 1.03,
        'pcg_planned_worst_time_ratio_max': 1.08,
        'worst_additional_peak_ratio_max': 1.001,
        'worst_retained_ratio_max': 1.001,
        'worst_peak_rss_ratio_max': 1.02,
    }
    result['accepted'] = (
        result['hierarchy_geometric_time_ratio'] <= 0.99
        and result['hierarchy_worst_time_ratio'] <= 1.04
        and result['pcg_serial_geometric_time_ratio'] <= 1.03
        and result['pcg_serial_worst_time_ratio'] <= 1.08
        and result['pcg_planned_geometric_time_ratio'] <= 1.03
        and result['pcg_planned_worst_time_ratio'] <= 1.08
        and result['worst_additional_peak_ratio'] <= 1.001
        and result['worst_retained_ratio'] <= 1.001
        and result['worst_peak_rss_ratio'] <= 1.02
    )
    result['decision_reason'] = (
        'full qualification passed; the trusted hierarchy path skips redundant parent-range validation while public checked APIs remain unchanged'
        if result['accepted']
        else 'qualification passed but validation elision did not produce a stable material hierarchy gain or a regression gate was exceeded'
    )
except Exception as error:
    result['validation'] = 'failure'
    result['decision_reason'] = f'experiment failed: {error}'
    result['error'] = repr(error)
    print(result['decision_reason'], flush=True)

if not result['accepted']:
    FOREST.write_text(baseline_source)
run(['cargo', 'fmt', '--all'], check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

status = STATUS.read_text().rstrip()
heading = '## Trusted internal forest validation gate\n'
decision = 'retained' if result['accepted'] else 'not retained'
block = f'''{heading}
- Decision: `{decision}`.
- Validation: `{result['validation']}`.
- Hierarchy geometric / worst time ratios: `{result.get('hierarchy_geometric_time_ratio', 'n/a')}` / `{result.get('hierarchy_worst_time_ratio', 'n/a')}`.
- Serial / planned PCG geometric ratios: `{result.get('pcg_serial_geometric_time_ratio', 'n/a')}` / `{result.get('pcg_planned_geometric_time_ratio', 'n/a')}`.
- Public `split_forest` and `forest_components` validation remain unchanged.
- Evidence: `.ci/performance/trusted-forest-validation-latest.json`.
'''
if heading in status:
    start = status.index(heading)
    end = status.find('\n## ', start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + block + status[end:]
else:
    status += '\n\n' + block
STATUS.write_text(status.rstrip() + '\n')

plan = PLAN.read_text()
marker = '## Current next action\n'
checkpoint_heading = '### Trusted internal forest validation checkpoint — 2026-08-23\n'
checkpoint = f'''{checkpoint_heading}
- Candidate was **{decision}**.
- Validation: `{result['validation']}`.
- Hierarchy / planned-PCG geometric ratios: `{result.get('hierarchy_geometric_time_ratio', 'n/a')}` / `{result.get('pcg_planned_geometric_time_ratio', 'n/a')}`.
- Evidence: `.ci/performance/trusted-forest-validation-latest.json`.

'''
if checkpoint_heading in plan:
    start = plan.index(checkpoint_heading)
    end = plan.index(marker, start)
    plan = plan[:start] + checkpoint + plan[end:]
else:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
    prefix, _ = plan.split(marker, 1)
    plan = prefix + marker + '''
1. Re-profile forest splitting/component labeling after the trusted-validation decision.
2. Refresh cumulative retained optimization and memory guidance.
3. Run the manual 1–32 thread qualification on suitable hardware when available.
4. Defer further sort variants unless a materially larger stable opportunity appears.
'''
PLAN.write_text(plan)

SCRIPT.unlink(missing_ok=True)
WORKFLOW.unlink(missing_ok=True)
