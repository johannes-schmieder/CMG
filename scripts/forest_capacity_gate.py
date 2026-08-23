import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
COARSEN = Path('src/coarsen.rs')
HIERARCHY = Path('src/hierarchy.rs')
WORKFLOW = Path('.github/workflows/forest-capacity-gate.yml')
SCRIPT = Path('scripts/forest_capacity_gate.py')
RECORD = Path('.ci/performance/forest-capacity-hint-latest.json')

baseline_coarsen = COARSEN.read_text()
baseline_hierarchy = HIERARCHY.read_text()
source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def run(command, *, env=None, timeout=7200, check=True):
    print('+', ' '.join(str(item) for item in command), flush=True)
    completed = subprocess.run(
        [str(item) for item in command],
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
            f"command failed ({completed.returncode}): {' '.join(str(item) for item in command)}"
        )
    return completed


def build(target):
    env = os.environ.copy()
    env['CARGO_TARGET_DIR'] = str(target)
    run([
        'cargo', 'build', '--release',
        '--manifest-path', 'benchmarks/Cargo.toml',
        '--bin', 'hierarchy-alloc',
    ], env=env)
    return target / 'release' / 'hierarchy-alloc'


def apply_candidate():
    old_contract = '''    /// Form the exact graph Laplacian `R L R^T`.
    pub fn contract(&self, graph: &Laplacian) -> Result<Laplacian, CmgError> {
        self.validate_contract_graph(graph)?;
        let mut coarse_edges = Vec::with_capacity(graph.edge_count());
        for edge in graph.edges() {
            let left = self.labels[edge.u()];
            let right = self.labels[edge.v()];
            if left != right {
                coarse_edges.push(Edge::from_internal_parts(left, right, edge.weight())?);
            }
        }
        Laplacian::from_compact_edges(self.coarse_dimension(), coarse_edges)
    }
'''
    new_contract = '''    /// Form the exact graph Laplacian `R L R^T`.
    pub fn contract(&self, graph: &Laplacian) -> Result<Laplacian, CmgError> {
        self.contract_with_capacity(graph, graph.edge_count())
    }

    pub(crate) fn contract_forest(&self, graph: &Laplacian) -> Result<Laplacian, CmgError> {
        let guaranteed_internal_edges = self
            .fine_dimension()
            .saturating_sub(self.coarse_dimension());
        let capacity = graph
            .edge_count()
            .saturating_sub(guaranteed_internal_edges);
        self.contract_with_capacity(graph, capacity)
    }

    fn contract_with_capacity(
        &self,
        graph: &Laplacian,
        capacity: usize,
    ) -> Result<Laplacian, CmgError> {
        self.validate_contract_graph(graph)?;
        let mut coarse_edges = Vec::with_capacity(capacity);
        for edge in graph.edges() {
            let left = self.labels[edge.u()];
            let right = self.labels[edge.v()];
            if left != right {
                coarse_edges.push(Edge::from_internal_parts(left, right, edge.weight())?);
            }
        }
        Laplacian::from_compact_edges(self.coarse_dimension(), coarse_edges)
    }
'''

    old_parallel = '''    #[cfg(feature = "parallel")]
    pub fn contract_with_executor(
        &self,
        graph: &Laplacian,
        executor: &ParallelExecutor,
    ) -> Result<Laplacian, CmgError> {
        self.validate_contract_graph(graph)?;
        if graph.edge_count() < PARALLEL_SETUP_MIN_ITEMS
            || !executor.should_parallel(graph.edge_count())
        {
            return self.contract(graph);
        }
        let coarse_edges: Result<Vec<Edge>, CmgError> = executor.install(|| {
            graph
                .edges()
                .par_iter()
                .filter_map(|edge| {
                    let left = self.labels[edge.u()];
                    let right = self.labels[edge.v()];
                    (left != right).then(|| Edge::from_internal_parts(left, right, edge.weight()))
                })
                .collect()
        });
        Laplacian::from_compact_edges_with_executor(
            self.coarse_dimension(),
            coarse_edges?,
            executor,
        )
    }
'''
    new_parallel = '''    #[cfg(feature = "parallel")]
    pub fn contract_with_executor(
        &self,
        graph: &Laplacian,
        executor: &ParallelExecutor,
    ) -> Result<Laplacian, CmgError> {
        self.contract_with_executor_impl(graph, executor, false)
    }

    #[cfg(feature = "parallel")]
    pub(crate) fn contract_forest_with_executor(
        &self,
        graph: &Laplacian,
        executor: &ParallelExecutor,
    ) -> Result<Laplacian, CmgError> {
        self.contract_with_executor_impl(graph, executor, true)
    }

    #[cfg(feature = "parallel")]
    fn contract_with_executor_impl(
        &self,
        graph: &Laplacian,
        executor: &ParallelExecutor,
        forest_connected: bool,
    ) -> Result<Laplacian, CmgError> {
        self.validate_contract_graph(graph)?;
        if graph.edge_count() < PARALLEL_SETUP_MIN_ITEMS
            || !executor.should_parallel(graph.edge_count())
        {
            return if forest_connected {
                self.contract_forest(graph)
            } else {
                self.contract(graph)
            };
        }
        let coarse_edges: Result<Vec<Edge>, CmgError> = executor.install(|| {
            graph
                .edges()
                .par_iter()
                .filter_map(|edge| {
                    let left = self.labels[edge.u()];
                    let right = self.labels[edge.v()];
                    (left != right).then(|| Edge::from_internal_parts(left, right, edge.weight()))
                })
                .collect()
        });
        Laplacian::from_compact_edges_with_executor(
            self.coarse_dimension(),
            coarse_edges?,
            executor,
        )
    }
'''

    text = COARSEN.read_text()
    if text.count(old_contract) != 1:
        raise RuntimeError('serial contraction block changed unexpectedly')
    if text.count(old_parallel) != 1:
        raise RuntimeError('parallel contraction block changed unexpectedly')
    text = text.replace(old_contract, new_contract).replace(old_parallel, new_parallel)
    COARSEN.write_text(text)

    hierarchy = HIERARCHY.read_text()
    replacements = {
        '|aggregation, current| aggregation.contract(current),':
            '|aggregation, current| aggregation.contract_forest(current),',
        '|aggregation, current| aggregation.contract_with_executor(current, executor),':
            '|aggregation, current| aggregation.contract_forest_with_executor(current, executor),',
    }
    for old, new in replacements.items():
        if hierarchy.count(old) != 1:
            raise RuntimeError(f'hierarchy call site changed unexpectedly: {old}')
        hierarchy = hierarchy.replace(old, new)
    HIERARCHY.write_text(hierarchy)


def sample(binary, case, scale, tag):
    time_path = Path(f'/tmp/cmg-forest-capacity-{tag}.time')
    completed = run([
        '/usr/bin/time', '-v', '-o', time_path,
        binary, case, scale, 2,
    ])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'unexpected benchmark output: {payloads}')
    timing = time_path.read_text()
    match = re.search(r'Maximum resident set size \(kbytes\):\s*(\d+)', timing)
    if match is None:
        raise RuntimeError('peak RSS missing')
    payload = payloads[0]
    payload['peak_rss_kib'] = int(match.group(1))
    return payload


def compare_case(baseline, candidate, name, case, scale):
    baseline_samples = []
    candidate_samples = []
    sequence = (
        ('baseline', baseline),
        ('candidate', candidate),
        ('candidate', candidate),
        ('baseline', baseline),
    )
    for index, (label, binary) in enumerate(sequence):
        observation = sample(binary, case, scale, f'{name}-{label}-{index}')
        (baseline_samples if label == 'baseline' else candidate_samples).append(observation)

    stable = (
        'case', 'scale', 'vertices', 'edges', 'levels',
        'hierarchy_matrix_nonzeros', 'max_post_drop_delta_bytes',
    )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f'{name}: changed stable field {key}')

    def med(samples, key):
        return statistics.median(item[key] for item in samples)

    baseline_time = med(baseline_samples, 'median_ns')
    candidate_time = med(candidate_samples, 'median_ns')
    baseline_peak = med(baseline_samples, 'median_additional_peak_bytes')
    candidate_peak = med(candidate_samples, 'median_additional_peak_bytes')
    baseline_retained = med(baseline_samples, 'median_retained_bytes')
    candidate_retained = med(candidate_samples, 'median_retained_bytes')
    baseline_rss = max(item['peak_rss_kib'] for item in baseline_samples)
    candidate_rss = max(item['peak_rss_kib'] for item in candidate_samples)

    return {
        'case': case,
        'scale': scale,
        'metadata': {key: reference[key] for key in stable},
        'baseline_median_ns': baseline_time,
        'candidate_median_ns': candidate_time,
        'candidate_over_baseline_time': candidate_time / baseline_time,
        'baseline_additional_peak_bytes': baseline_peak,
        'candidate_additional_peak_bytes': candidate_peak,
        'candidate_over_baseline_additional_peak': candidate_peak / baseline_peak,
        'baseline_retained_bytes': baseline_retained,
        'candidate_retained_bytes': candidate_retained,
        'candidate_over_baseline_retained': candidate_retained / baseline_retained,
        'baseline_peak_rss_kib': baseline_rss,
        'candidate_peak_rss_kib': candidate_rss,
        'candidate_over_baseline_peak_rss': candidate_rss / baseline_rss,
    }


result = {
    'schema_version': 1,
    'experiment': 'forest-backed-contraction-capacity-hint',
    'source_sha': source_sha,
    'validation': 'not_run',
    'accepted': False,
    'decision_reason': '',
    'cases': {},
}

try:
    baseline_binary = build(Path('/tmp/cmg-forest-capacity-baseline'))
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
    candidate_binary = build(Path('/tmp/cmg-forest-capacity-candidate'))
    result['validation'] = 'success'

    specs = (
        ('path-1m', 'path', 1_000_000),
        ('worker-firm-1.5m', 'worker-firm', 500_000),
        ('dense-worker-firm-1.6m', 'dense-worker-firm', 100_000),
    )
    for name, case, scale in specs:
        result['cases'][name] = compare_case(
            baseline_binary, candidate_binary, name, case, scale
        )

    time_ratios = [item['candidate_over_baseline_time'] for item in result['cases'].values()]
    peak_ratios = [
        item['candidate_over_baseline_additional_peak']
        for item in result['cases'].values()
    ]
    retained_ratios = [
        item['candidate_over_baseline_retained'] for item in result['cases'].values()
    ]
    rss_ratios = [
        item['candidate_over_baseline_peak_rss'] for item in result['cases'].values()
    ]
    result['geometric_time_ratio'] = math.exp(
        sum(math.log(value) for value in time_ratios) / len(time_ratios)
    )
    result['worst_time_ratio'] = max(time_ratios)
    result['geometric_additional_peak_ratio'] = math.exp(
        sum(math.log(value) for value in peak_ratios) / len(peak_ratios)
    )
    result['worst_additional_peak_ratio'] = max(peak_ratios)
    result['worst_retained_ratio'] = max(retained_ratios)
    result['worst_peak_rss_ratio'] = max(rss_ratios)
    result['acceptance_limits'] = {
        'geometric_time_ratio_max': 1.02,
        'worst_time_ratio_max': 1.06,
        'geometric_additional_peak_ratio_max': 0.98,
        'worst_additional_peak_ratio_max': 1.01,
        'worst_retained_ratio_max': 1.002,
        'worst_peak_rss_ratio_max': 1.03,
    }
    result['accepted'] = (
        result['geometric_time_ratio'] <= 1.02
        and result['worst_time_ratio'] <= 1.06
        and result['geometric_additional_peak_ratio'] <= 0.98
        and result['worst_additional_peak_ratio'] <= 1.01
        and result['worst_retained_ratio'] <= 1.002
        and result['worst_peak_rss_ratio'] <= 1.03
    )
    result['decision_reason'] = (
        'full qualification passed; the forest-derived upper bound reduced hierarchy peak allocation without a material timing regression'
        if result['accepted']
        else 'qualification passed but one or more timing or memory gates were missed'
    )
except Exception as error:
    result['validation'] = 'failure'
    result['decision_reason'] = f'experiment failed: {error}'
    result['error'] = repr(error)
    print(result['decision_reason'], flush=True)

if not result['accepted']:
    COARSEN.write_text(baseline_coarsen)
    HIERARCHY.write_text(baseline_hierarchy)
    run(['cargo', 'fmt', '--all'], check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

status_path = Path('PERFORMANCE_STATUS.md')
status = status_path.read_text().rstrip()
heading = '## Forest-backed contraction capacity gate\n'
ratio = result.get('geometric_time_ratio')
peak = result.get('geometric_additional_peak_ratio')
status_name = 'retained' if result['accepted'] else 'not retained'
block = f'''{heading}
- Decision: `{status_name}`.
- Validation: `{result['validation']}`.
- Geometric hierarchy-time ratio: `{ratio if ratio is not None else 'n/a'}`.
- Geometric exact additional-peak ratio: `{peak if peak is not None else 'n/a'}`.
- Production uses the hint only for CMG forest aggregations; the public generic aggregation path is unchanged.
- Evidence: `.ci/performance/forest-capacity-hint-latest.json`.
'''
if heading in status:
    start = status.index(heading)
    end = status.find('\n## ', start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + block + status[end:]
else:
    status += '\n\n' + block
status_path.write_text(status.rstrip() + '\n')

plan_path = Path('PERFORMANCE_PLAN.md')
plan = plan_path.read_text()
marker = '## Current next action\n'
checkpoint_heading = '### Forest-backed contraction capacity checkpoint — 2026-08-23\n'
checkpoint = f'''{checkpoint_heading}
- Candidate was **{status_name}**.
- Validation: `{result['validation']}`.
- Geometric hierarchy-time ratio: `{ratio if ratio is not None else 'n/a'}`.
- Geometric exact additional-peak ratio: `{peak if peak is not None else 'n/a'}`.
- Evidence: `.ci/performance/forest-capacity-hint-latest.json`.

'''
if checkpoint_heading not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
    prefix, _ = plan.split(marker, 1)
    plan = prefix + marker + '''
1. Re-profile contraction sorting, mapping, duplicate aggregation, and diagonal assembly after the capacity decision.
2. Evaluate reusable contraction work buffers only if the profile shows allocation or initialization remains material.
3. Re-run full certified PCG routing after any retained hierarchy change.
4. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
plan_path.write_text(plan)

WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
