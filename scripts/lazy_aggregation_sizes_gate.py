import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
COARSEN = Path('src/coarsen.rs')
SCRIPT = Path('scripts/lazy_aggregation_sizes_gate.py')
WORKFLOW = Path('.github/workflows/lazy-aggregation-sizes-gate.yml')
RECORD = Path('.ci/performance/lazy-aggregation-sizes-latest.json')
STATUS = Path('PERFORMANCE_STATUS.md')
PLAN = Path('PERFORMANCE_PLAN.md')

baseline_source = COARSEN.read_text()
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


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise RuntimeError(f'{label} changed unexpectedly; matches={text.count(old)}')
    return text.replace(old, new, 1)


def apply_candidate():
    text = COARSEN.read_text()
    text = replace_once(
        text,
        '''pub struct Aggregation {
    labels: LabelStorage,
    native_labels: OnceLock<Vec<usize>>,
    sizes: Vec<usize>,
}
''',
        '''pub struct Aggregation {
    labels: LabelStorage,
    native_labels: OnceLock<Vec<usize>>,
    aggregate_count: usize,
    sizes: OnceLock<Vec<usize>>,
}
''',
        'Aggregation fields',
    )
    text = replace_once(
        text,
        '''        Self {
            labels: self.labels.clone(),
            native_labels: OnceLock::new(),
            sizes: self.sizes.clone(),
        }
''',
        '''        Self {
            labels: self.labels.clone(),
            native_labels: OnceLock::new(),
            aggregate_count: self.aggregate_count,
            sizes: OnceLock::new(),
        }
''',
        'Aggregation clone',
    )
    text = replace_once(
        text,
        '''        self.sizes == other.sizes
            && self.fine_dimension() == other.fine_dimension()
            && (0..self.fine_dimension()).all(|index| self.label_at(index) == other.label_at(index))
''',
        '''        self.aggregate_count == other.aggregate_count
            && self.fine_dimension() == other.fine_dimension()
            && (0..self.fine_dimension()).all(|index| self.label_at(index) == other.label_at(index))
''',
        'Aggregation equality',
    )
    text = replace_once(
        text,
        '''    pub fn new(labels: Vec<usize>, aggregate_count: usize) -> Result<Self, CmgError> {
        let mut sizes = vec![0; aggregate_count];
        for &label in &labels {
            if label >= aggregate_count {
                return Err(CmgError::VertexOutOfBounds {
                    vertex: label,
                    vertex_count: aggregate_count,
                });
            }
            sizes[label] += 1;
        }
        Ok(Self::from_validated_parts(labels, sizes))
    }

    pub(crate) fn from_forest_parts(labels: Vec<usize>, sizes: Vec<usize>) -> Self {
        debug_assert_eq!(sizes.iter().sum::<usize>(), labels.len());
        debug_assert!(labels.iter().all(|&label| label < sizes.len()));
        Self::from_validated_parts(labels, sizes)
    }

    fn from_validated_parts(labels: Vec<usize>, sizes: Vec<usize>) -> Self {
        let compact_limit = (u32::MAX as usize).saturating_add(1);
        let labels = if sizes.len() <= compact_limit {
            LabelStorage::Compact(labels.into_iter().map(|label| label as u32).collect())
        } else {
            LabelStorage::Native(labels)
        };
        Self {
            labels,
            native_labels: OnceLock::new(),
            sizes,
        }
    }
''',
        '''    pub fn new(labels: Vec<usize>, aggregate_count: usize) -> Result<Self, CmgError> {
        for &label in &labels {
            if label >= aggregate_count {
                return Err(CmgError::VertexOutOfBounds {
                    vertex: label,
                    vertex_count: aggregate_count,
                });
            }
        }
        Ok(Self::from_validated_parts(labels, aggregate_count))
    }

    pub(crate) fn from_forest_parts(labels: Vec<usize>, sizes: Vec<usize>) -> Self {
        debug_assert_eq!(sizes.iter().sum::<usize>(), labels.len());
        debug_assert!(labels.iter().all(|&label| label < sizes.len()));
        let aggregate_count = sizes.len();
        Self::from_validated_parts(labels, aggregate_count)
    }

    fn from_validated_parts(labels: Vec<usize>, aggregate_count: usize) -> Self {
        let compact_limit = (u32::MAX as usize).saturating_add(1);
        let labels = if aggregate_count <= compact_limit {
            LabelStorage::Compact(labels.into_iter().map(|label| label as u32).collect())
        } else {
            LabelStorage::Native(labels)
        };
        Self {
            labels,
            native_labels: OnceLock::new(),
            aggregate_count,
            sizes: OnceLock::new(),
        }
    }
''',
        'Aggregation constructors',
    )
    text = replace_once(
        text,
        '''    /// Return aggregate sizes.
    #[must_use]
    pub fn sizes(&self) -> &[usize] {
        &self.sizes
    }
''',
        '''    /// Return aggregate sizes.
    ///
    /// Production hierarchy kernels need only the aggregate count. The full
    /// native-width size vector is materialized lazily for API compatibility.
    #[must_use]
    pub fn sizes(&self) -> &[usize] {
        self.sizes.get_or_init(|| {
            let mut sizes = vec![0; self.aggregate_count];
            match &self.labels {
                LabelStorage::Compact(labels) => {
                    for &label in labels {
                        sizes[label as usize] += 1;
                    }
                }
                LabelStorage::Native(labels) => {
                    for &label in labels {
                        sizes[label] += 1;
                    }
                }
            }
            sizes
        })
    }
''',
        'Aggregation sizes accessor',
    )
    text = replace_once(
        text,
        '''    pub fn coarse_dimension(&self) -> usize {
        self.sizes.len()
    }
''',
        '''    pub const fn coarse_dimension(&self) -> usize {
        self.aggregate_count
    }
''',
        'Aggregation coarse dimension',
    )

    old_test = '''        assert!(aggregation.native_labels.get().is_none());

        let fine = [1.0, 2.0, 3.0, 4.0, 5.0];
'''
    new_test = '''        assert!(aggregation.native_labels.get().is_none());
        assert!(aggregation.sizes.get().is_none());

        let fine = [1.0, 2.0, 3.0, 4.0, 5.0];
'''
    text = replace_once(text, old_test, new_test, 'lazy sizes test setup')
    old_assert = '''        assert_eq!(aggregation.labels(), &[0, 0, 1, 2, 2]);
        assert!(aggregation.native_labels.get().is_some());
'''
    new_assert = '''        assert_eq!(aggregation.labels(), &[0, 0, 1, 2, 2]);
        assert!(aggregation.native_labels.get().is_some());
        assert!(aggregation.sizes.get().is_none());
        assert_eq!(aggregation.sizes(), &[2, 1, 2]);
        assert!(aggregation.sizes.get().is_some());
'''
    text = replace_once(text, old_assert, new_assert, 'lazy sizes test assertions')
    old_clone = '''        assert!(cloned.native_labels.get().is_none());
        assert_eq!(cloned.labels(), &[0, 1, 1, 2]);
'''
    new_clone = '''        let _ = aggregation.sizes();
        assert!(cloned.native_labels.get().is_none());
        assert!(cloned.sizes.get().is_none());
        assert_eq!(cloned.labels(), &[0, 1, 1, 2]);
        assert_eq!(cloned.sizes(), &[1, 2, 1]);
'''
    text = replace_once(text, old_clone, new_clone, 'clone cache test')
    COARSEN.write_text(text)


def sample(binary, arguments, tag):
    time_path = Path(f'/tmp/cmg-lazy-sizes-{tag}.time')
    completed = run([
        '/usr/bin/time', '-v', '-o', time_path,
        binary, *arguments,
    ])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'{tag}: unexpected output {payloads}')
    match = re.search(
        r'Maximum resident set size \(kbytes\):\s*(\d+)',
        time_path.read_text(),
    )
    if match is None:
        raise RuntimeError(f'{tag}: peak RSS missing')
    payload = payloads[0]
    payload['peak_rss_kib'] = int(match.group(1))
    return payload


def alternating(baseline, candidate, arguments, tag):
    groups = {'baseline': [], 'candidate': []}
    for index, (name, binary) in enumerate((
        ('baseline', baseline),
        ('candidate', candidate),
        ('candidate', candidate),
        ('baseline', baseline),
    )):
        groups[name].append(sample(binary, arguments, f'{tag}-{name}-{index}'))
    return groups


def median(groups, group, key):
    return statistics.median(item[key] for item in groups[group])


def compare_hierarchy(baseline, candidate, name, case_name, scale):
    groups = alternating(
        baseline,
        candidate,
        [case_name, str(scale), '3'],
        f'hierarchy-{name}',
    )
    stable = (
        'case', 'scale', 'vertices', 'edges', 'levels',
        'hierarchy_matrix_nonzeros', 'max_post_drop_delta_bytes',
    )
    reference = groups['baseline'][0]
    for observations in groups.values():
        for item in observations:
            for key in stable:
                if item[key] != reference[key]:
                    raise RuntimeError(f'{name}: changed hierarchy field {key}')
    baseline_time = median(groups, 'baseline', 'median_ns')
    candidate_time = median(groups, 'candidate', 'median_ns')
    baseline_peak = median(groups, 'baseline', 'median_additional_peak_bytes')
    candidate_peak = median(groups, 'candidate', 'median_additional_peak_bytes')
    baseline_retained = median(groups, 'baseline', 'median_retained_bytes')
    candidate_retained = median(groups, 'candidate', 'median_retained_bytes')
    baseline_rss = max(item['peak_rss_kib'] for item in groups['baseline'])
    candidate_rss = max(item['peak_rss_kib'] for item in groups['candidate'])
    return {
        'metadata': {key: reference[key] for key in stable},
        'candidate_over_baseline_time': candidate_time / baseline_time,
        'candidate_over_baseline_additional_peak': candidate_peak / baseline_peak,
        'candidate_over_baseline_retained': candidate_retained / baseline_retained,
        'candidate_over_baseline_peak_rss': candidate_rss / baseline_rss,
        'baseline_retained_bytes': baseline_retained,
        'candidate_retained_bytes': candidate_retained,
    }


def close(left, right, tolerance=5e-12):
    return abs(left - right) <= tolerance * (1.0 + max(abs(left), abs(right)))


def compare_pcg(baseline, candidate, name, case_name, scale):
    groups = alternating(
        baseline,
        candidate,
        [case_name, str(scale), '3', '4'],
        f'pcg-{name}',
    )
    exact = (
        'case', 'scale', 'vertices', 'input_edges', 'edges', 'levels',
        'threads', 'operators', 'plan_bytes', 'workspace_bytes',
        'auto_execution', 'serial_iterations', 'planned_iterations',
    )
    floating = (
        'serial_backward_error', 'planned_backward_error',
        'serial_residual_norm', 'planned_residual_norm',
    )
    reference = groups['baseline'][0]
    for observations in groups.values():
        for item in observations:
            for key in exact:
                if item[key] != reference[key]:
                    raise RuntimeError(f'{name}: changed PCG field {key}')
            for key in floating:
                if not close(item[key], reference[key]):
                    raise RuntimeError(f'{name}: changed PCG numerical field {key}')
            if item['max_scaled_difference'] > 1e-8:
                raise RuntimeError(f'{name}: serial/planned solution difference too large')
    baseline_serial = median(groups, 'baseline', 'serial_median_ns')
    candidate_serial = median(groups, 'candidate', 'serial_median_ns')
    baseline_planned = median(groups, 'baseline', 'planned_median_ns')
    candidate_planned = median(groups, 'candidate', 'planned_median_ns')
    return {
        'metadata': {key: reference[key] for key in exact},
        'candidate_over_baseline_serial_time': candidate_serial / baseline_serial,
        'candidate_over_baseline_planned_time': candidate_planned / baseline_planned,
        'maximum_scaled_difference': max(
            item['max_scaled_difference']
            for observations in groups.values()
            for item in observations
        ),
    }


result = {
    'schema_version': 1,
    'experiment': 'lazy-aggregation-size-compatibility-cache',
    'source_sha': source_sha,
    'validation': 'not_run',
    'accepted': False,
    'decision_reason': '',
    'hierarchy_cases': {},
    'pcg_cases': {},
}

try:
    baseline = build(Path('/tmp/cmg-lazy-sizes-baseline'))
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
    run(['cargo', 'test', '--all-targets'])
    run(['cargo', 'test', '--all-targets', '--release'])
    run(['cargo', 'test', '--all-targets', '--all-features'])
    run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
    run(['cargo', 'build', '--release', '--all-features'])
    candidate = build(Path('/tmp/cmg-lazy-sizes-candidate'))
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

    hierarchy = list(result['hierarchy_cases'].values())
    geometric = lambda values: math.exp(
        statistics.fmean(math.log(value) for value in values)
    )
    time_ratios = [item['candidate_over_baseline_time'] for item in hierarchy]
    peak_ratios = [item['candidate_over_baseline_additional_peak'] for item in hierarchy]
    retained_ratios = [item['candidate_over_baseline_retained'] for item in hierarchy]
    rss_ratios = [item['candidate_over_baseline_peak_rss'] for item in hierarchy]
    serial_ratios = [
        item['candidate_over_baseline_serial_time']
        for item in result['pcg_cases'].values()
    ]
    planned_ratios = [
        item['candidate_over_baseline_planned_time']
        for item in result['pcg_cases'].values()
    ]
    result.update({
        'hierarchy_geometric_time_ratio': geometric(time_ratios),
        'hierarchy_worst_time_ratio': max(time_ratios),
        'geometric_additional_peak_ratio': geometric(peak_ratios),
        'worst_additional_peak_ratio': max(peak_ratios),
        'geometric_retained_ratio': geometric(retained_ratios),
        'worst_retained_ratio': max(retained_ratios),
        'worst_peak_rss_ratio': max(rss_ratios),
        'pcg_serial_geometric_time_ratio': geometric(serial_ratios),
        'pcg_serial_worst_time_ratio': max(serial_ratios),
        'pcg_planned_geometric_time_ratio': geometric(planned_ratios),
        'pcg_planned_worst_time_ratio': max(planned_ratios),
    })
    limits = {
        'geometric_retained_ratio_max': 0.98,
        'worst_retained_ratio_max': 0.995,
        'geometric_additional_peak_ratio_max': 1.0,
        'worst_additional_peak_ratio_max': 1.03,
        'hierarchy_geometric_time_ratio_max': 1.03,
        'hierarchy_worst_time_ratio_max': 1.08,
        'worst_peak_rss_ratio_max': 1.04,
        'pcg_serial_geometric_time_ratio_max': 1.03,
        'pcg_serial_worst_time_ratio_max': 1.08,
        'pcg_planned_geometric_time_ratio_max': 1.03,
        'pcg_planned_worst_time_ratio_max': 1.08,
    }
    result['acceptance_limits'] = limits
    result['accepted'] = all((
        result['geometric_retained_ratio'] <= limits['geometric_retained_ratio_max'],
        result['worst_retained_ratio'] <= limits['worst_retained_ratio_max'],
        result['geometric_additional_peak_ratio'] <= limits['geometric_additional_peak_ratio_max'],
        result['worst_additional_peak_ratio'] <= limits['worst_additional_peak_ratio_max'],
        result['hierarchy_geometric_time_ratio'] <= limits['hierarchy_geometric_time_ratio_max'],
        result['hierarchy_worst_time_ratio'] <= limits['hierarchy_worst_time_ratio_max'],
        result['worst_peak_rss_ratio'] <= limits['worst_peak_rss_ratio_max'],
        result['pcg_serial_geometric_time_ratio'] <= limits['pcg_serial_geometric_time_ratio_max'],
        result['pcg_serial_worst_time_ratio'] <= limits['pcg_serial_worst_time_ratio_max'],
        result['pcg_planned_geometric_time_ratio'] <= limits['pcg_planned_geometric_time_ratio_max'],
        result['pcg_planned_worst_time_ratio'] <= limits['pcg_planned_worst_time_ratio_max'],
    ))
    result['decision_reason'] = (
        'full qualification passed; aggregate-size vectors are absent from normal hierarchy retention and remain lazily available through the public API'
        if result['accepted']
        else 'qualification passed but memory, hierarchy timing, or complete PCG timing missed a fixed gate'
    )
except Exception as error:
    result['validation'] = 'failure'
    result['decision_reason'] = f'experiment failed: {error}'
    result['error'] = repr(error)
    print(result['decision_reason'], flush=True)

if not result['accepted']:
    COARSEN.write_text(baseline_source)
    run(['cargo', 'fmt', '--all'], check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

status_name = 'retained' if result['accepted'] else 'not retained'
status = STATUS.read_text().rstrip()
heading = '## Lazy aggregation-size gate\n'
block = f'''{heading}
- Decision: `{status_name}`.
- Validation: `{result['validation']}`.
- Geometric retained-memory ratio: `{result.get('geometric_retained_ratio', 'n/a')}`.
- Geometric hierarchy-time ratio: `{result.get('hierarchy_geometric_time_ratio', 'n/a')}`.
- Serial/planned PCG geometric ratios: `{result.get('pcg_serial_geometric_time_ratio', 'n/a')}` / `{result.get('pcg_planned_geometric_time_ratio', 'n/a')}`.
- Public aggregate sizes remain available through a thread-safe lazy compatibility cache.
- Evidence: `.ci/performance/lazy-aggregation-sizes-latest.json`.
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
checkpoint_heading = '### Lazy aggregation-size checkpoint — 2026-08-23\n'
checkpoint = f'''{checkpoint_heading}
- Candidate was **{status_name}**.
- Validation: `{result['validation']}`.
- Geometric retained / hierarchy-time ratios: `{result.get('geometric_retained_ratio', 'n/a')}` / `{result.get('hierarchy_geometric_time_ratio', 'n/a')}`.
- Serial/planned PCG ratios: `{result.get('pcg_serial_geometric_time_ratio', 'n/a')}` / `{result.get('pcg_planned_geometric_time_ratio', 'n/a')}`.
- Evidence: `.ci/performance/lazy-aggregation-sizes-latest.json`.

'''
if checkpoint_heading not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
    prefix, _ = plan.split(marker, 1)
    plan = prefix + marker + '''
1. Re-run the full routing matrix if lazy aggregate sizes are retained.
2. Continue sort-dominant contraction work only with a design that clears both speed and peak-memory gates.
3. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
PLAN.write_text(plan)

SCRIPT.unlink(missing_ok=True)
WORKFLOW.unlink(missing_ok=True)
