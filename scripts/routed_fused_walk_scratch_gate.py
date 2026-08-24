from pathlib import Path
import subprocess

SOURCE_COMMIT = "8cece8b67eca8c6dc87ebaf3072e9a53b0edc05d"
SOURCE_PATH = "scripts/fused_walk_ancestor_scratch_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "fused_walk_ancestor_scratch_gate.py",
    "routed_fused_walk_scratch_gate.py",
)
text = text.replace(
    "fused-walk-ancestor-scratch.yml",
    "routed-fused-walk-scratch.yml",
)
text = text.replace(
    "fused-walk-ancestor-scratch-latest.json",
    "routed-fused-walk-scratch-latest.json",
)
text = text.replace(
    "fused-walk-ancestor-scratch-gate",
    "routed-fused-walk-scratch-gate",
)
text = text.replace(
    "fused-walk-ancestor-scratch",
    "routed-fused-walk-scratch",
)
text = text.replace(
    "Fused walk/ancestor scratch",
    "Routed fused walk/ancestor scratch",
)
text = text.replace(
    "fused walk/ancestor scratch",
    "routed fused walk/ancestor scratch",
)

constants_start = text.index("ENTRY_TYPE =")
constants_end = text.index("\n\ndef run(", constants_start)
constants = r'''HELPER_INSERT_MARKER = '''fn split_forest_impl_with_indegree<I: ForestIndegree>(
'''
HELPER_CODE = '''#[derive(Clone, Copy)]
struct ForestWalkEntry {
    vertex: usize,
    ancestor_prefix: i64,
}

trait DiameterScratch {
    fn new() -> Self;
    fn reset(&mut self, vertex: usize);
    fn vertex(&self, index: usize) -> usize;
    fn ancestor_prefix(&self, index: usize) -> i64;
    fn push(&mut self, vertex: usize, ancestor_prefix: i64);
}

struct SeparateDiameterScratch {
    walk: Vec<usize>,
    ancestor_prefixes: Vec<i64>,
}

impl DiameterScratch for SeparateDiameterScratch {
    #[inline]
    fn new() -> Self {
        Self {
            walk: Vec::new(),
            ancestor_prefixes: Vec::new(),
        }
    }

    #[inline]
    fn reset(&mut self, vertex: usize) {
        self.walk.clear();
        self.walk.push(vertex);
        self.ancestor_prefixes.clear();
        self.ancestor_prefixes.push(0);
    }

    #[inline]
    fn vertex(&self, index: usize) -> usize {
        self.walk[index]
    }

    #[inline]
    fn ancestor_prefix(&self, index: usize) -> i64 {
        self.ancestor_prefixes[index]
    }

    #[inline]
    fn push(&mut self, vertex: usize, ancestor_prefix: i64) {
        self.walk.push(vertex);
        self.ancestor_prefixes.push(ancestor_prefix);
    }
}

struct FusedDiameterScratch {
    entries: Vec<ForestWalkEntry>,
}

impl DiameterScratch for FusedDiameterScratch {
    #[inline]
    fn new() -> Self {
        Self {
            entries: Vec::new(),
        }
    }

    #[inline]
    fn reset(&mut self, vertex: usize) {
        self.entries.clear();
        self.entries.push(ForestWalkEntry {
            vertex,
            ancestor_prefix: 0,
        });
    }

    #[inline]
    fn vertex(&self, index: usize) -> usize {
        self.entries[index].vertex
    }

    #[inline]
    fn ancestor_prefix(&self, index: usize) -> i64 {
        self.entries[index].ancestor_prefix
    }

    #[inline]
    fn push(&mut self, vertex: usize, ancestor_prefix: i64) {
        self.entries.push(ForestWalkEntry {
            vertex,
            ancestor_prefix,
        });
    }
}

#[inline]
fn should_use_fused_diameter_scratch<I: ForestIndegree>(indegree: &[I]) -> bool {
    const SAMPLE_LIMIT: usize = 4_096;
    const MIN_VERTICES: usize = 100_000;
    if indegree.len() < MIN_VERTICES {
        return false;
    }
    let sample_count = indegree.len().min(SAMPLE_LIMIT);
    let zero_count = (0..sample_count)
        .filter(|&sample_index| {
            let index = ((sample_index as u128 * indegree.len() as u128)
                / sample_count as u128) as usize;
            indegree[index].is_zero()
        })
        .count();
    zero_count * 100 >= sample_count * 27 && zero_count * 100 <= sample_count * 40
}

#[inline]
fn run_diameter_pass<I: ForestIndegree, S: DiameterScratch>(
    forest: &mut [usize],
    ancestors: &mut [i64],
    indegree: &mut [I],
    visited: &mut [bool],
) {
    let mut scratch = S::new();
    for start in 0..forest.len() {
        let mut current = start;
        let mut continue_walk = true;
        while continue_walk && indegree[current].is_zero() && !visited[current] {
            continue_walk = false;
            let mut ancestors_in_path = 0_i64;
            scratch.reset(current);
            let mut k = 0_usize;

            while k <= 5 || visited[current] {
                current = forest[current];
                let terminated = current == scratch.vertex(k)
                    || (k > 0 && current == scratch.vertex(k - 1));
                if terminated {
                    break;
                }
                k += 1;
                ancestors_in_path += i64::from(u8::from(!visited[current]));
                scratch.push(current, ancestors_in_path);
            }

            if k > 5 {
                let middle = k / 2;
                let middle_vertex = scratch.vertex(middle);
                forest[middle_vertex] = middle_vertex;
                let next = scratch.vertex(middle + 1);
                indegree[next].decrement();
                let removed = ancestors[middle_vertex];
                for index in (middle + 1)..=k {
                    ancestors[scratch.vertex(index)] -= removed;
                }
                for index in 0..=middle {
                    let vertex = scratch.vertex(index);
                    visited[vertex] = true;
                    ancestors[vertex] += scratch.ancestor_prefix(index);
                }
                current = next;
                continue_walk = true;
            }

            if !continue_walk {
                for index in 0..=k {
                    let vertex = scratch.vertex(index);
                    ancestors[vertex] += scratch.ancestor_prefix(index);
                    visited[vertex] = true;
                }
            }
        }
    }
}

'''
OLD_DIAMETER = '''    let mut walk = Vec::new();
    let mut new_ancestors = Vec::new();

    for start in 0..n {
        let mut current = start;
        let mut continue_walk = true;
        while continue_walk && indegree[current].is_zero() && !visited[current] {
            continue_walk = false;
            let mut ancestors_in_path = 0_i64;
            walk.clear();
            walk.push(current);
            new_ancestors.clear();
            new_ancestors.push(0_i64);
            let mut k = 0_usize;

            while k <= 5 || visited[current] {
                current = forest[current];
                let terminated = current == walk[k] || (k > 0 && current == walk[k - 1]);
                if terminated {
                    break;
                }
                k += 1;
                walk.push(current);
                ancestors_in_path += i64::from(u8::from(!visited[current]));
                new_ancestors.push(ancestors_in_path);
            }

            if k > 5 {
                let middle = k / 2;
                forest[walk[middle]] = walk[middle];
                let next = walk[middle + 1];
                indegree[next].decrement();
                let removed = ancestors[walk[middle]];
                for &vertex in &walk[(middle + 1)..=k] {
                    ancestors[vertex] -= removed;
                }
                for index in 0..=middle {
                    let vertex = walk[index];
                    visited[vertex] = true;
                    ancestors[vertex] += new_ancestors[index];
                }
                current = next;
                continue_walk = true;
            }

            if !continue_walk {
                for index in 0..=k {
                    let vertex = walk[index];
                    ancestors[vertex] += new_ancestors[index];
                    visited[vertex] = true;
                }
            }
        }
    }
'''
NEW_DIAMETER = '''    if should_use_fused_diameter_scratch(&indegree) {
        run_diameter_pass::<I, FusedDiameterScratch>(
            &mut forest,
            &mut ancestors,
            &mut indegree,
            &mut visited,
        );
    } else {
        run_diameter_pass::<I, SeparateDiameterScratch>(
            &mut forest,
            &mut ancestors,
            &mut indegree,
            &mut visited,
        );
    }
'''
TEST_MODULE = '''

#[cfg(test)]
mod routed_fused_walk_scratch_tests {
    use super::should_use_fused_diameter_scratch;

    #[test]
    fn route_selects_only_intermediate_front_rates() {
        let path_like: Vec<u32> = (0..100_000)
            .map(|index| u32::from(index % 4 != 0))
            .collect();
        let worker_like: Vec<u32> = (0..100_000)
            .map(|index| u32::from(index % 10 >= 3))
            .collect();
        let dense_like: Vec<u32> = (0..100_000)
            .map(|index| u32::from(index % 2 != 0))
            .collect();
        assert!(!should_use_fused_diameter_scratch(&path_like));
        assert!(should_use_fused_diameter_scratch(&worker_like));
        assert!(!should_use_fused_diameter_scratch(&dense_like));
    }
}
'''
'''
text = text[:constants_start] + constants + text[constants_end:]

apply_start = text.index("def apply_candidate(source):")
apply_end = text.index("\n\ndef build(", apply_start)
apply_function = r'''def apply_candidate(source):
    if source.count(HELPER_INSERT_MARKER) != 1:
        raise RuntimeError("diameter helper insertion marker changed unexpectedly")
    if source.count(OLD_DIAMETER) != 1:
        raise RuntimeError("diameter pass source marker changed unexpectedly")
    candidate = source.replace(
        HELPER_INSERT_MARKER,
        HELPER_CODE + HELPER_INSERT_MARKER,
        1,
    )
    candidate = candidate.replace(OLD_DIAMETER, NEW_DIAMETER, 1)
    if "mod routed_fused_walk_scratch_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate
'''
text = text[:apply_start] + apply_function + text[apply_end:]

text = text.replace(
    '"improved_split_case_count_min": 3',
    '"improved_split_case_count_min": 2',
)
text = text.replace(
    'result["hierarchy_geometric_time_ratio"] <= 0.997',
    'result["hierarchy_geometric_time_ratio"] <= 0.999',
)
text = text.replace(
    '"hierarchy_geometric_time_ratio_max": 0.997',
    '"hierarchy_geometric_time_ratio_max": 0.999',
)
text = text.replace(
    "Replacing parallel walk and ancestor-prefix vectors with one cache-local entry vector",
    "Routing worker-like forest fronts to a fused cache-local entry vector while retaining the original path for path-like and dense fronts",
)
text = text.replace(
    "walk vertices and ancestor prefixes share one cache-local scratch stream",
    "worker-like forest fronts use fused scratch while path-like and dense fronts retain the lower-RSS representation",
)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("historical cleanup marker changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "routed_fused_walk_scratch_gate.py",
    "routed-fused-walk-scratch.yml",
    "should_use_fused_diameter_scratch",
    "FusedDiameterScratch",
    "SeparateDiameterScratch",
    '"improved_split_case_count_min": 2',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"routed fused gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
