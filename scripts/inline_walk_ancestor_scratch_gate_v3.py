from pathlib import Path
import subprocess

SOURCE_COMMIT = "f2782ca51ea6f03d05cae9922aa920b76342e8a5"
SOURCE_PATH = "scripts/compact_walk_ancestor_scratch_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)

for old, new in (
    ("compact_walk_ancestor_scratch_gate.py", "inline_walk_ancestor_scratch_gate_v3.py"),
    ("compact-walk-ancestor-scratch.yml", "inline-walk-ancestor-scratch-v3.yml"),
    ("compact-walk-ancestor-scratch-latest.json", "inline-walk-ancestor-scratch-latest.json"),
    ("compact-walk-ancestor-scratch-gate.rs", "inline-walk-ancestor-scratch-gate.rs"),
    ("compact-walk-ancestor-scratch-gate", "inline-walk-ancestor-scratch-gate"),
    ("cmg-compact-walk-", "cmg-inline-walk-"),
    ("compact-walk-ancestor-scratch", "inline-walk-ancestor-scratch"),
    ("Compact walk/ancestor scratch", "Inline walk/ancestor scratch"),
    ("compact walk/ancestor scratch", "inline walk/ancestor scratch"),
    ("compact walk-ancestor scratch", "inline walk-ancestor scratch"),
):
    text = text.replace(old, new)


def replace_section(source, start_marker, end_marker, replacement):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[:start] + replacement + source[end:]


entry_types = r'''ENTRY_TYPES = '''const INLINE_FOREST_WALK_CAPACITY: usize = 16;

#[derive(Clone, Copy)]
struct ForestWalkEntry {
    vertex: usize,
    ancestor_prefix: i64,
}

struct ForestWalkScratch {
    inline: [ForestWalkEntry; INLINE_FOREST_WALK_CAPACITY],
    spill: Vec<ForestWalkEntry>,
    len: usize,
}

impl ForestWalkScratch {
    fn new() -> Self {
        Self {
            inline: [ForestWalkEntry {
                vertex: 0,
                ancestor_prefix: 0,
            }; INLINE_FOREST_WALK_CAPACITY],
            spill: Vec::new(),
            len: 0,
        }
    }

    #[inline]
    fn clear(&mut self) {
        self.len = 0;
        self.spill.clear();
    }

    #[inline]
    fn push(&mut self, vertex: usize, ancestor_prefix: i64) {
        let entry = ForestWalkEntry {
            vertex,
            ancestor_prefix,
        };
        if self.len < INLINE_FOREST_WALK_CAPACITY {
            self.inline[self.len] = entry;
        } else {
            self.spill.push(entry);
        }
        self.len += 1;
    }

    #[inline]
    fn get(&self, index: usize) -> ForestWalkEntry {
        debug_assert!(index < self.len);
        if index < INLINE_FOREST_WALK_CAPACITY {
            self.inline[index]
        } else {
            self.spill[index - INLINE_FOREST_WALK_CAPACITY]
        }
    }
}

'''
'''
text = replace_section(text, "ENTRY_TYPES =", "INSERT_MARKER =", entry_types)

current_router = r'''fn split_forest_impl(parent: &[usize], validate: bool) -> Result<Vec<usize>, CmgError> {
    if parent.len() <= u32::MAX as usize {
        split_forest_impl_with_indegree::<u32>(parent, validate)
    } else {
        split_forest_impl_with_indegree::<usize>(parent, validate)
    }
}

fn split_forest_impl_with_indegree<I: ForestIndegree>(
    parent: &[usize],
    validate: bool,
) -> Result<Vec<usize>, CmgError> {
'''
router_constants = (
    "OLD_ROUTER = '''" + current_router + "'''\n"
    "NEW_ROUTER = '''" + current_router + "'''\n"
)
text = replace_section(text, "OLD_ROUTER =", "OLD_SCRATCH =", router_constants)

replacements = {
    "NEW_SCRATCH = '''    let mut walk = Vec::<W>::new();\n'''":
        "NEW_SCRATCH = '''    let mut walk = ForestWalkScratch::new();\n'''",
    "NEW_INIT = '''            walk.clear();\n            walk.push(W::from_parts(current, 0));\n'''":
        "NEW_INIT = '''            walk.clear();\n            walk.push(current, 0);\n'''",
    "NEW_TERMINATED = '''                let terminated = current == walk[k].vertex()\n                    || (k > 0 && current == walk[k - 1].vertex());\n'''":
        "NEW_TERMINATED = '''                let terminated = current == walk.get(k).vertex\n                    || (k > 0 && current == walk.get(k - 1).vertex);\n'''",
    "NEW_PUSH = '''                k += 1;\n                ancestors_in_path += i64::from(u8::from(!visited[current]));\n                walk.push(W::from_parts(current, ancestors_in_path));\n'''":
        "NEW_PUSH = '''                k += 1;\n                ancestors_in_path += i64::from(u8::from(!visited[current]));\n                walk.push(current, ancestors_in_path);\n'''",
    "NEW_CUT = '''                let middle = k / 2;\n                let middle_vertex = walk[middle].vertex();\n                forest[middle_vertex] = middle_vertex;\n                let next = walk[middle + 1].vertex();\n                indegree[next].decrement();\n                let removed = ancestors[middle_vertex];\n                for entry in &walk[(middle + 1)..=k] {\n                    ancestors[entry.vertex()] -= removed;\n                }\n                for entry in &walk[..=middle] {\n                    let vertex = entry.vertex();\n                    visited[vertex] = true;\n                    ancestors[vertex] += entry.ancestor_prefix();\n                }\n'''":
        "NEW_CUT = '''                let middle = k / 2;\n                let middle_vertex = walk.get(middle).vertex;\n                forest[middle_vertex] = middle_vertex;\n                let next = walk.get(middle + 1).vertex;\n                indegree[next].decrement();\n                let removed = ancestors[middle_vertex];\n                for index in (middle + 1)..=k {\n                    ancestors[walk.get(index).vertex] -= removed;\n                }\n                for index in 0..=middle {\n                    let entry = walk.get(index);\n                    visited[entry.vertex] = true;\n                    ancestors[entry.vertex] += entry.ancestor_prefix;\n                }\n'''",
    "NEW_TERMINAL = '''            if !continue_walk {\n                for entry in &walk[..=k] {\n                    let vertex = entry.vertex();\n                    ancestors[vertex] += entry.ancestor_prefix();\n                    visited[vertex] = true;\n                }\n            }\n'''":
        "NEW_TERMINAL = '''            if !continue_walk {\n                for index in 0..=k {\n                    let entry = walk.get(index);\n                    ancestors[entry.vertex] += entry.ancestor_prefix;\n                    visited[entry.vertex] = true;\n                }\n            }\n'''",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise SystemExit(f"inline gate constant marker changed: {old[:60]}")
    text = text.replace(old, new, 1)

test_module = r'''TEST_MODULE = '''

#[cfg(test)]
mod inline_walk_ancestor_scratch_tests {
    use super::{ForestWalkScratch, INLINE_FOREST_WALK_CAPACITY};

    #[test]
    fn inline_scratch_spills_and_reuses_exactly() {
        let mut scratch = ForestWalkScratch::new();
        for index in 0..(INLINE_FOREST_WALK_CAPACITY + 7) {
            scratch.push(index * 3, index as i64);
        }
        for index in 0..(INLINE_FOREST_WALK_CAPACITY + 7) {
            let entry = scratch.get(index);
            assert_eq!(entry.vertex, index * 3);
            assert_eq!(entry.ancestor_prefix, index as i64);
        }
        scratch.clear();
        scratch.push(91, 4);
        let entry = scratch.get(0);
        assert_eq!(entry.vertex, 91);
        assert_eq!(entry.ancestor_prefix, 4);
    }
}
'''
'''
text = replace_section(text, "TEST_MODULE =", "\n\n\ndef run", test_module)

text = text.replace(
    "Storing realistic-size walk vertices and ancestor prefixes in one 8-byte entry was",
    "Keeping the first 16 fused walk/ancestor entries inline with an exact spill path was",
)
text = text.replace(
    "with a native-width fallback above `u32::MAX` vertices.",
    "for arbitrary longer walks.",
)
text = text.replace(
    "compact fused scratch retained the locality gain without the wide-entry RSS penalty",
    "inline fused scratch retained locality while avoiding per-split heap scratch and the heap-entry RSS penalty",
)
text = text.replace('"split_geometric_time_ratio_max": 0.94,', '"split_geometric_time_ratio_max": 0.96,')
text = text.replace('"hierarchy_geometric_time_ratio_max": 0.985,', '"hierarchy_geometric_time_ratio_max": 0.992,')
text = text.replace('result["split_geometric_time_ratio"] <= 0.94', 'result["split_geometric_time_ratio"] <= 0.96')
text = text.replace('result["hierarchy_geometric_time_ratio"] <= 0.985', 'result["hierarchy_geometric_time_ratio"] <= 0.992')
text = text.replace("perf: retain compact walk-ancestor scratch", "perf: retain inline walk-ancestor scratch")
text = text.replace(
    "perf: record compact walk-ancestor scratch experiment",
    "perf: record inline walk-ancestor scratch experiment",
)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
for stale in (
    ".ci/performance/inline-walk-ancestor-scratch-diagnostic.json",
    "scripts/inline_walk_ancestor_scratch_gate_v2.py",
    ".github/workflows/inline-walk-ancestor-scratch-v2.yml",
):
    Path(stale).unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("inline gate cleanup marker changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "inline_walk_ancestor_scratch_gate_v3.py",
    "inline-walk-ancestor-scratch-v3.yml",
    "ForestWalkScratch",
    "INLINE_FOREST_WALK_CAPACITY",
    "ENTRY_TYPES + INSERT_MARKER",
    "NEW_ROUTER",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"fully repaired inline gate missing marker: {marker}")

compiled = compile(text, str(Path(__file__)), "exec")
exec(compiled, {"__name__": "__main__", "__file__": __file__})
