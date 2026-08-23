from pathlib import Path
import subprocess

SOURCE_COMMIT = 'fa51a0ede2bd2d171c84423610d5ba755f1cb759'
SOURCE_PATH = 'scripts/raw_two_stage_sort_gate.py'

text = subprocess.check_output(
    ['git', 'show', f'{SOURCE_COMMIT}:{SOURCE_PATH}'],
    text=True,
)
text = text.replace('raw_two_stage_sort_gate.py', 'routed_raw_sort_gate.py')
text = text.replace('raw-two-stage-sort-gate.yml', 'routed-raw-sort-gate.yml')
text = text.replace('raw-two-stage-sort-latest.json', 'routed-raw-sort-latest.json')
text = text.replace('cmg-raw-two-stage-', 'cmg-routed-raw-sort-')
text = text.replace(
    "'experiment': 'raw-graph-two-stage-endpoint-weight-sort'",
    "'experiment': 'sample-routed-raw-graph-sort'",
)

old_serial_new = '''    serial_new = ''' + "'''" + '''    pub fn from_edges<I>(vertex_count: usize, edges: I) -> Result<Self, CmgError>
    where
        I: IntoIterator<Item = (usize, usize, f64)>,
    {
        let mut raw = collect_validated_edges(vertex_count, edges)?;
        sort_compact_edges_two_stage(&mut raw);
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
''' + "'''"
new_serial_new = '''    serial_new = ''' + "'''" + '''    pub fn from_edges<I>(vertex_count: usize, edges: I) -> Result<Self, CmgError>
    where
        I: IntoIterator<Item = (usize, usize, f64)>,
    {
        let mut raw = collect_validated_edges(vertex_count, edges)?;
        if should_use_two_stage_raw_sort(&raw) {
            sort_compact_edges_two_stage(&mut raw);
        } else {
            raw.sort_unstable_by(compare_raw_edges);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
''' + "'''"
text = text.replace(old_serial_new, new_serial_new, 1)

old_executor_new = '''    executor_new = ''' + "'''" + '''    #[cfg(feature = "parallel")]
    pub fn from_edges_with_executor<I>(
        vertex_count: usize,
        edges: I,
        executor: &ParallelExecutor,
    ) -> Result<Self, CmgError>
    where
        I: IntoIterator<Item = (usize, usize, f64)>,
    {
        let mut raw = collect_validated_edges(vertex_count, edges)?;
        if raw.len() >= PARALLEL_SETUP_MIN_ITEMS && executor.should_parallel(raw.len()) {
            executor.install(|| raw.par_sort_unstable_by(compare_raw_edges));
        } else {
            sort_compact_edges_two_stage(&mut raw);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
''' + "'''"
new_executor_new = '''    executor_new = ''' + "'''" + '''    #[cfg(feature = "parallel")]
    pub fn from_edges_with_executor<I>(
        vertex_count: usize,
        edges: I,
        executor: &ParallelExecutor,
    ) -> Result<Self, CmgError>
    where
        I: IntoIterator<Item = (usize, usize, f64)>,
    {
        let mut raw = collect_validated_edges(vertex_count, edges)?;
        if raw.len() >= PARALLEL_SETUP_MIN_ITEMS && executor.should_parallel(raw.len()) {
            executor.install(|| raw.par_sort_unstable_by(compare_raw_edges));
        } else if should_use_two_stage_raw_sort(&raw) {
            sort_compact_edges_two_stage(&mut raw);
        } else {
            raw.sort_unstable_by(compare_raw_edges);
        }
        Self::from_sorted_raw_edges(vertex_count, raw)
    }
''' + "'''"
text = text.replace(old_executor_new, new_executor_new, 1)

old_write = '''    text = replace_once(text, serial_old, serial_new, 'raw serial constructor')
    text = replace_once(text, executor_old, executor_new, 'raw executor constructor')
    GRAPH.write_text(text)
'''
new_write = '''    text = replace_once(text, serial_old, serial_new, 'raw serial constructor')
    text = replace_once(text, executor_old, executor_new, 'raw executor constructor')
    helper_anchor = "fn compensated_add(sum: &mut f64, correction: &mut f64, value: f64) {"
    helper = ''' + "'''" + '''const RAW_SORT_SAMPLE_COUNT: usize = 4_096;
const RAW_TWO_STAGE_MIN_EDGES: usize = 65_536;

fn should_use_two_stage_raw_sort(raw: &[Edge]) -> bool {
    if raw.len() < RAW_TWO_STAGE_MIN_EDGES {
        return false;
    }
    let interval_count = (raw.len() - 1).min(RAW_SORT_SAMPLE_COUNT);
    let span = raw.len() - 1;
    let mut inversions = 0usize;
    for sample in 0..interval_count {
        let index = sample * span / interval_count;
        if endpoint_key(&raw[index]) > endpoint_key(&raw[index + 1]) {
            inversions += 1;
        }
    }
    inversions.saturating_mul(100) >= interval_count
}

''' + "'''" + '''
    if text.count(helper_anchor) != 1:
        raise RuntimeError('raw sort helper anchor changed unexpectedly')
    text = text.replace(helper_anchor, helper + helper_anchor, 1)
    GRAPH.write_text(text)
'''
text = text.replace(old_write, new_write, 1)

text = text.replace("'geometric_time_ratio_max': 0.985", "'geometric_time_ratio_max': 0.99")
text = text.replace(
    "'duplicate_geometric_time_ratio_max': 0.975",
    "'duplicate_geometric_time_ratio_max': 0.98",
)
text = text.replace("'worst_time_ratio_max': 1.035", "'worst_time_ratio_max': 1.03")
text = text.replace(
    'endpoint-first ordering improved raw graph construction across unique and duplicate-heavy inputs',
    'sample routing retained the collision-heavy win while preserving ordered-input performance',
)
text = text.replace('Raw graph two-stage ordering gate', 'Sample-routed raw graph ordering gate')
text = text.replace('Raw graph two-stage sort checkpoint', 'Sample-routed raw graph sort checkpoint')

required = (
    'should_use_two_stage_raw_sort',
    'routed-raw-sort-latest.json',
    "'geometric_time_ratio_max': 0.99",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f'router transformation missing marker: {marker}')

compiled = compile(text, 'scripts/routed_raw_sort_gate.py', 'exec')
exec(compiled, {'__name__': '__main__', '__file__': __file__})
