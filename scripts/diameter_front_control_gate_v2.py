from pathlib import Path
import subprocess

SOURCE_COMMIT = "aa37bbe219b450664aabd6724a2de4e87efdb31b"
SOURCE_PATH = "scripts/diameter_front_control_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "diameter_front_control_gate.py",
    "diameter_front_control_gate_v2.py",
)
text = text.replace(
    "diameter-front-control.yml",
    "diameter-front-control-v2.yml",
)

start = text.index("OLD_HEADER =")
end = text.index("\n\ndef run", start)
CANDIDATE = r'''OLD_HEADER = '''    for start in 0..n {
        let mut current = start;
        let mut continue_walk = true;
        while continue_walk && indegree[current].is_zero() && !visited[current] {
            continue_walk = false;
'''
NEW_HEADER = '''    for start in 0..n {
        let mut current = start;
        'diameter_front: while indegree[current].is_zero() && !visited[current] {
'''
OLD_PREFIX = '''                current = next;
                continue_walk = true;
            }

            if !continue_walk {
'''
NEW_PREFIX = '''                current = next;
                continue 'diameter_front;
            }

'''
OLD_INDEXED_BODY = '''                for index in 0..=k {
                    let vertex = walk[index];
                    ancestors[vertex] += new_ancestors[index];
                    visited[vertex] = true;
                }
'''
NEW_INDEXED_BODY = '''            for index in 0..=k {
                let vertex = walk[index];
                ancestors[vertex] += new_ancestors[index];
                visited[vertex] = true;
            }
            break 'diameter_front;
'''
OLD_ZIPPED_BODY = '''                for (&vertex, &new_ancestor) in
                    walk[..=k].iter().zip(&new_ancestors[..=k])
                {
                    ancestors[vertex] += new_ancestor;
                    visited[vertex] = true;
                }
'''
NEW_ZIPPED_BODY = '''            for (&vertex, &new_ancestor) in
                walk[..=k].iter().zip(&new_ancestors[..=k])
            {
                ancestors[vertex] += new_ancestor;
                visited[vertex] = true;
            }
            break 'diameter_front;
'''
OLD_SUFFIX = '''            }
        }
    }

    for start in 0..n {
'''
NEW_SUFFIX = '''        }
    }

    for start in 0..n {
'''
TEST_MODULE = '''

#[cfg(test)]
mod diameter_front_control_tests {
    use super::split_forest;

    #[test]
    fn labeled_diameter_front_preserves_roots_and_two_cycles() {
        for parent in [
            vec![0],
            vec![1, 0],
            vec![1, 2, 3, 4, 5, 6, 7, 7],
            vec![1, 2, 3, 4, 4, 6, 7, 7],
        ] {
            let split = split_forest(&parent).unwrap();
            assert_eq!(split.len(), parent.len());
            assert!(split.iter().all(|target| *target < split.len()));
        }
    }
}
'''


def apply_candidate(source):
    if source.count(OLD_HEADER) != 1:
        raise RuntimeError("diameter-front header marker changed unexpectedly")
    if source.count(OLD_PREFIX) != 1:
        raise RuntimeError("diameter-front continuation marker changed unexpectedly")
    candidate = source.replace(OLD_HEADER, NEW_HEADER, 1)
    candidate = candidate.replace(OLD_PREFIX, NEW_PREFIX, 1)

    indexed = candidate.count(OLD_INDEXED_BODY)
    zipped = candidate.count(OLD_ZIPPED_BODY)
    if indexed + zipped != 1:
        raise RuntimeError(
            "expected exactly one indexed or zipped terminal ancestor-update body"
        )
    if indexed:
        candidate = candidate.replace(OLD_INDEXED_BODY, NEW_INDEXED_BODY, 1)
    else:
        candidate = candidate.replace(OLD_ZIPPED_BODY, NEW_ZIPPED_BODY, 1)

    if candidate.count(OLD_SUFFIX) != 1:
        raise RuntimeError("diameter-front closing marker changed unexpectedly")
    candidate = candidate.replace(OLD_SUFFIX, NEW_SUFFIX, 1)
    if "mod diameter_front_control_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate
'''
text = text[:start] + CANDIDATE + text[end:]

text = text.replace(
    "# Deliberately do not execute here. This file is prepared for the next serial gate\n# after any active bounded-prefix decision resolves.",
    "# Prepared only. Arm after the active zipped-update decision resolves.",
)

required = (
    "diameter_front_control_gate_v2.py",
    "diameter-front-control-v2.yml",
    "OLD_ZIPPED_BODY",
    "continue 'diameter_front",
    "apply_candidate(baseline_source)",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"adaptive diameter-front gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
# Prepared only. The generated gate remains available as `text`.
