from pathlib import Path
import subprocess

SOURCE_COMMIT = 'd36164b639c03d343ef1385c0cfbc2336b505bf7'
SOURCE_PATH = 'scripts/owned_split_forest_gate.py'

text = subprocess.check_output(
    ['git', 'show', f'{SOURCE_COMMIT}:{SOURCE_PATH}'],
    text=True,
)

replacements = (
    ('owned_split_forest_gate.py', 'owned_component_labels_gate.py'),
    ('owned-split-forest-gate.yml', 'owned-component-labels-gate.yml'),
    ('owned-split-forest-latest.json', 'owned-component-labels-latest.json'),
    ('owned-internal-forest-split', 'owned-internal-component-labels'),
    ('/tmp/cmg-owned-split-', '/tmp/cmg-owned-labels-'),
    ('Owned internal forest split gate', 'Owned internal component-label gate'),
    ('Owned internal forest split checkpoint', 'Owned internal component-label checkpoint'),
    (
        'full qualification passed; the internal hierarchy path consumes the heavy-parent vector during forest splitting and avoids one full clone',
        'full qualification passed; the hierarchy path releases final-parent storage and reuses the disjoint-set allocation as returned labels',
    ),
    (
        'qualification passed but the removed clone did not produce a stable end-to-end memory/time win',
        'qualification passed but consuming component labeling did not produce a stable end-to-end memory/time win',
    ),
    (
        'Public borrowed `split_forest` behavior is unchanged.',
        'Public `forest_components` diagnostic behavior is unchanged.',
    ),
)
for old, new in replacements:
    text = text.replace(old, new)

start = text.index('def apply_candidate():\n')
end = text.index('\n\ndef sample(', start)
new_apply = '''def apply_candidate():
    text = FOREST.read_text()
    text = replace_once(
        text,
        ''' + "'''" + '''    forest_component_labels(&final_parent)
''' + "'''" + ''',
        ''' + "'''" + '''    forest_component_labels_owned(final_parent)
''' + "'''" + ''',
        'lean hierarchy component-label call',
    )
    old_function = ''' + "'''" + '''fn forest_component_labels(parent: &[usize]) -> Result<(Vec<usize>, usize), CmgError> {
    validate_parent(parent)?;
    let n = parent.len();
    let mut disjoint_set: Vec<usize> = (0..n).collect();
    for (vertex, &target) in parent.iter().enumerate() {
        union_min_root(&mut disjoint_set, vertex, target);
    }
    for vertex in 0..n {
        disjoint_set[vertex] = find_root(&mut disjoint_set, vertex);
    }

    let mut root_to_label = vec![usize::MAX; n];
    let mut labels = vec![0; n];
    let mut aggregate_count = 0usize;
    for (vertex, &root) in disjoint_set.iter().enumerate() {
        let label = if root_to_label[root] == usize::MAX {
            let next = aggregate_count;
            aggregate_count += 1;
            root_to_label[root] = next;
            next
        } else {
            root_to_label[root]
        };
        labels[vertex] = label;
    }
    Ok((labels, aggregate_count))
}
''' + "'''" + '''
    new_function = ''' + "'''" + '''fn forest_component_labels_owned(parent: Vec<usize>) -> Result<(Vec<usize>, usize), CmgError> {
    validate_parent(&parent)?;
    let n = parent.len();
    let mut labels: Vec<usize> = (0..n).collect();
    for (vertex, &target) in parent.iter().enumerate() {
        union_min_root(&mut labels, vertex, target);
    }
    for vertex in 0..n {
        labels[vertex] = find_root(&mut labels, vertex);
    }
    drop(parent);

    let mut root_to_label = vec![usize::MAX; n];
    let mut aggregate_count = 0usize;
    for root_or_label in &mut labels {
        let root = *root_or_label;
        let label = if root_to_label[root] == usize::MAX {
            let next = aggregate_count;
            aggregate_count += 1;
            root_to_label[root] = next;
            next
        } else {
            root_to_label[root]
        };
        *root_or_label = label;
    }
    Ok((labels, aggregate_count))
}
''' + "'''" + '''
    text = replace_once(
        text,
        old_function,
        new_function,
        'owned component-label implementation',
    )
    test = ''' + "'''" + '''

#[cfg(test)]
mod owned_component_label_tests {
    use super::{forest_component_labels_owned, forest_components};

    #[test]
    fn consuming_labels_match_public_component_labels() {
        let parent = vec![1, 2, 2, 4, 3, 6, 6, 8, 9, 9, 11, 10];
        let (expected, sizes) = forest_components(&parent).unwrap();
        let (observed, aggregate_count) = forest_component_labels_owned(parent).unwrap();
        assert_eq!(observed, expected);
        assert_eq!(aggregate_count, sizes.len());
    }
}
''' + "'''" + '''
    if 'mod owned_component_label_tests' not in text:
        text += test
    FOREST.write_text(text)
'''
text = text[:start] + new_apply + text[end:]

old_actions = '''1. If owned splitting is retained, benchmark consuming component labeling that reuses the final-parent allocation as its returned label vector.
2. Refresh cumulative large-graph hierarchy memory guidance.
3. Continue sort-dominant contraction work only with a design that clears both speed and peak-memory gates.
4. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
new_actions = '''1. Refresh cumulative large-graph hierarchy memory guidance after the component-label decision.
2. Re-profile forest and contraction phases after retained memory changes.
3. Continue sort-dominant contraction work only with a design that clears both speed and peak-memory gates.
4. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
if old_actions not in text:
    raise SystemExit('historical next-action block changed unexpectedly')
text = text.replace(old_actions, new_actions, 1)

required = (
    'forest_component_labels_owned(final_parent)',
    'drop(parent);',
    'owned-component-labels-latest.json',
    'mod owned_component_label_tests',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f'owned component-label transformation missing {marker}')

exec(
    compile(text, 'scripts/owned_component_labels_gate.py', 'exec'),
    {'__name__': '__main__'},
)
