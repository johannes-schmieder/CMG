from pathlib import Path
import subprocess

SOURCE_COMMIT = "f2782ca51ea6f03d05cae9922aa920b76342e8a5"
SOURCE_PATH = "scripts/compact_walk_ancestor_scratch_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)

text = text.replace(
    "compact_walk_ancestor_scratch_gate.py",
    "inline_walk_ancestor_scratch_gate.py",
)
text = text.replace(
    "compact-walk-ancestor-scratch.yml",
    "inline-walk-ancestor-scratch.yml",
)
text = text.replace(
    "compact-walk-ancestor-scratch-latest.json",
    "inline-walk-ancestor-scratch-latest.json",
)
text = text.replace(
    "compact-walk-ancestor-scratch-gate.rs",
    "inline-walk-ancestor-scratch-gate.rs",
)
text = text.replace(
    "compact-walk-ancestor-scratch-gate",
    "inline-walk-ancestor-scratch-gate",
)
text = text.replace(
    "cmg-compact-walk-",
    "cmg-inline-walk-",
)
text = text.replace(
    "compact-walk-ancestor-scratch",
    "inline-walk-ancestor-scratch",
)
text = text.replace(
    "Compact walk/ancestor scratch",
    "Inline walk/ancestor scratch",
)
text = text.replace(
    "compact walk/ancestor scratch",
    "inline walk/ancestor scratch",
)
text = text.replace(
    "compact walk-ancestor scratch",
    "inline walk-ancestor scratch",
)

start = text.index("ENTRY_TYPES =")
end = text.index("\n\ndef run", start)
source_patch = r'''INLINE_TYPES = '''const INLINE_FOREST_WALK_CAPACITY: usize = 16;

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
INSERT_MARKER = '''trait ForestIndegree: Copy {
'''
OLD_SCRATCH = '''    let mut walk = Vec::new();
    let mut new_ancestors = Vec::new();
'''
NEW_SCRATCH = '''    let mut walk = ForestWalkScratch::new();
'''
OLD_INIT = '''            walk.clear();
            walk.push(current);
            new_ancestors.clear();
            new_ancestors.push(0_i64);
'''
NEW_INIT = '''            walk.clear();
            walk.push(current, 0);
'''
OLD_TERMINATED = '''                let terminated = current == walk[k] || (k > 0 && current == walk[k - 1]);
'''
NEW_TERMINATED = '''                let terminated = current == walk.get(k).vertex
                    || (k > 0 && current == walk.get(k - 1).vertex);
'''
OLD_PUSH = '''                k += 1;
                walk.push(current);
                ancestors_in_path += i64::from(u8::from(!visited[current]));
                new_ancestors.push(ancestors_in_path);
'''
NEW_PUSH = '''                k += 1;
                ancestors_in_path += i64::from(u8::from(!visited[current]));
                walk.push(current, ancestors_in_path);
'''
OLD_CUT = '''                let middle = k / 2;
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
'''
NEW_CUT = '''                let middle = k / 2;
                let middle_vertex = walk.get(middle).vertex;
                forest[middle_vertex] = middle_vertex;
                let next = walk.get(middle + 1).vertex;
                indegree[next].decrement();
                let removed = ancestors[middle_vertex];
                for index in (middle + 1)..=k {
                    ancestors[walk.get(index).vertex] -= removed;
                }
                for index in 0..=middle {
                    let entry = walk.get(index);
                    visited[entry.vertex] = true;
                    ancestors[entry.vertex] += entry.ancestor_prefix;
                }
'''
OLD_TERMINAL = '''            if !continue_walk {
                for index in 0..=k {
                    let vertex = walk[index];
                    ancestors[vertex] += new_ancestors[index];
                    visited[vertex] = true;
                }
            }
'''
NEW_TERMINAL = '''            if !continue_walk {
                for index in 0..=k {
                    let entry = walk.get(index);
                    ancestors[entry.vertex] += entry.ancestor_prefix;
                    visited[entry.vertex] = true;
                }
            }
'''
TEST_MODULE = '''

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


def apply_candidate(source):
    if source.count(INSERT_MARKER) != 1:
        raise RuntimeError("ForestIndegree insertion marker changed unexpectedly")
    candidate = source.replace(INSERT_MARKER, INLINE_TYPES + INSERT_MARKER, 1)
    replacements = (
        (OLD_SCRATCH, NEW_SCRATCH, "scratch vectors"),
        (OLD_INIT, NEW_INIT, "walk initialization"),
        (OLD_TERMINATED, NEW_TERMINATED, "termination lookup"),
        (OLD_PUSH, NEW_PUSH, "walk push"),
        (OLD_CUT, NEW_CUT, "diameter cut updates"),
        (OLD_TERMINAL, NEW_TERMINAL, "terminal updates"),
    )
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if "new_ancestors" in candidate:
        raise RuntimeError("separate ancestor-prefix scratch remains")
    if "mod inline_walk_ancestor_scratch_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate
'''
text = text[:start] + source_patch + text[end:]

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
text = text.replace(
    '"split_geometric_time_ratio_max": 0.94,',
    '"split_geometric_time_ratio_max": 0.96,',
)
text = text.replace(
    '"hierarchy_geometric_time_ratio_max": 0.985,',
    '"hierarchy_geometric_time_ratio_max": 0.992,',
)
text = text.replace(
    'result["split_geometric_time_ratio"] <= 0.94',
    'result["split_geometric_time_ratio"] <= 0.96',
)
text = text.replace(
    'result["hierarchy_geometric_time_ratio"] <= 0.985',
    'result["hierarchy_geometric_time_ratio"] <= 0.992',
)
text = text.replace(
    "perf: retain compact walk-ancestor scratch",
    "perf: retain inline walk-ancestor scratch",
)
text = text.replace(
    "perf: record compact walk-ancestor scratch experiment",
    "perf: record inline walk-ancestor scratch experiment",
)

required = (
    "inline_walk_ancestor_scratch_gate.py",
    "inline-walk-ancestor-scratch.yml",
    "inline-walk-ancestor-scratch-latest.json",
    "ForestWalkScratch",
    "INLINE_FOREST_WALK_CAPACITY",
    "inline_scratch_spills_and_reuses_exactly",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"inline scratch gate missing marker: {marker}")

compiled = compile(text, str(Path(__file__)), "exec")
exec(compiled, {"__name__": "__main__", "__file__": __file__})
