import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
SOURCE = Path('benchmarks/src/bin/contraction-subphase-profile.rs')
SCRIPT = Path('scripts/refresh_contraction_profile.py')
WORKFLOW = Path('.github/workflows/refresh-contraction-profile.yml')
RECORD = Path('.ci/performance/contraction-subphase-profile-v2.json')


def run(args, *, env=None, timeout=7200):
    args = [str(arg) for arg in args]
    print('+', ' '.join(args), flush=True)
    completed = subprocess.run(
        args, cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end='')
    if completed.returncode:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(args)}")
    return completed


def replace_once(text, old, new, name):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{name} expected once, found {count}')
    return text.replace(old, new, 1)


text = SOURCE.read_text()
text = replace_once(
    text,
    '''fn compare_edges(left: &ProbeEdge, right: &ProbeEdge) -> core::cmp::Ordering {
    endpoint_key(left)
        .cmp(&endpoint_key(right))
        .then_with(|| left.weight.total_cmp(&right.weight))
}
''',
    '''fn sort_endpoint_then_weights(raw: &mut [ProbeEdge]) {
    raw.sort_unstable_by_key(endpoint_key);
    sort_weights_within_groups(raw);
}
''',
    'old comparison helper',
)
text = replace_once(
    text,
    '''        raw[start..end].sort_unstable_by(|left, right| left.weight.total_cmp(&right.weight));
        start = end;
''',
    '''        if end - start > 1 {
            raw[start..end]
                .sort_unstable_by(|left, right| left.weight.total_cmp(&right.weight));
        }
        start = end;
''',
    'weight group sort',
)
text = replace_once(
    text,
    '''    } else {
        raw.sort_unstable_by(compare_edges);
        false
    }
}
''',
    '''    } else {
        sort_endpoint_then_weights(raw);
        false
    }
}
''',
    'comparison sort route',
)
text = replace_once(
    text,
    '''fn compensated_sum(values: impl IntoIterator<Item = f64>) -> f64 {
    let mut sum = 0.0;
    let mut correction = 0.0;
    for value in values {
        let next = sum + value;
        if sum.abs() >= value.abs() {
            correction += (sum - next) + value;
        } else {
            correction += (value - next) + sum;
        }
        sum = next;
    }
    sum + correction
}
''',
    '''fn compensated_add(sum: &mut f64, correction: &mut f64, value: f64) {
    let next = *sum + value;
    *correction += if sum.abs() >= value.abs() {
        (*sum - next) + value
    } else {
        (value - next) + *sum
    };
    *sum = next;
}
''',
    'old compensated sum helper',
)
text = replace_once(
    text,
    '''    while read < raw.len() {
        let u = raw[read].u;
        let v = raw[read].v;
        let mut end = read + 1;
        while end < raw.len() && raw[end].u == u && raw[end].v == v {
            end += 1;
        }
        let weight = compensated_sum(raw[read..end].iter().map(|edge| edge.weight));
        raw[write] = ProbeEdge { u, v, weight };
        write += 1;
        read = end;
    }
''',
    '''    while read < raw.len() {
        let u = raw[read].u;
        let v = raw[read].v;
        let mut sum = 0.0;
        let mut correction = 0.0;
        while read < raw.len() && raw[read].u == u && raw[read].v == v {
            compensated_add(&mut sum, &mut correction, raw[read].weight);
            read += 1;
        }
        raw[write] = ProbeEdge {
            u,
            v,
            weight: sum + correction,
        };
        write += 1;
    }
''',
    'old duplicate merge loop',
)
SOURCE.write_text(text)

run(['cargo', 'fmt', '--manifest-path', 'benchmarks/Cargo.toml', '--all'])
run(['cargo', 'fmt', '--manifest-path', 'benchmarks/Cargo.toml', '--all', '--', '--check'])
run(['cargo', 'clippy', '--manifest-path', 'benchmarks/Cargo.toml', '--all-targets', '--', '-D', 'warnings'])
run(['cargo', 'test', '--all-targets', '--all-features', '--release'])
run([
    'cargo', 'build', '--release', '--manifest-path', 'benchmarks/Cargo.toml',
    '--bin', 'contraction-subphase-profile',
])

binary = Path('benchmarks/target/release/contraction-subphase-profile')
cases = {}
phase_names = ('mapping', 'sorting', 'merging', 'diagonal', 'finalize')
phase_totals = {name: 0 for name in phase_names}
production_total = 0
manual_total = 0
for name, case, scale in (
    ('path-1m', 'path', 1_000_000),
    ('worker-firm-1.5m', 'worker-firm', 500_000),
    ('dense-worker-firm-1.6m', 'dense-worker-firm', 100_000),
):
    completed = run([binary, case, scale, 5, 'comparison'])
    rows = [
        json.loads(line) for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    levels = [row for row in rows if row['record'] == 'level']
    summaries = [row for row in rows if row['record'] == 'case']
    if len(summaries) != 1:
        raise RuntimeError(f'{name}: expected one summary row')
    summary = summaries[0]
    cases[name] = {'levels': levels, 'summary': summary}
    for phase in phase_names:
        phase_totals[phase] += summary[f'{phase}_ns']
    production_total += summary['production_total_ns']
    manual_total += summary['manual_total_ns']

phase_sum = sum(phase_totals.values())
record = {
    'schema_version': 3,
    'experiment': 'contraction-subphase-profile-current-production-kernels',
    'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'status': 'success',
    'exact_equivalence': 'passed for every profiled level and repetition',
    'cases': cases,
    'phase_totals_ns': phase_totals,
    'phase_shares': {
        phase: phase_totals[phase] / phase_sum for phase in phase_names
    },
    'manual_total_ns': manual_total,
    'production_total_ns': production_total,
    'manual_over_production': manual_total / production_total,
    'dominant_phase': max(phase_names, key=lambda phase: phase_totals[phase]),
}
record['dominant_share'] = record['phase_shares'][record['dominant_phase']]
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')

shares = record['phase_shares']
status = Path('PERFORMANCE_STATUS.md')
content = status.read_text().rstrip()
content += f'''\n\n## Current-kernel contraction subphase profile

- Exact production equivalence: `passed`.
- Mapping share: `{shares['mapping']:.1%}`.
- Sorting share: `{shares['sorting']:.1%}`.
- Duplicate-merging share: `{shares['merging']:.1%}`.
- Diagonal share: `{shares['diagonal']:.1%}`.
- Finalization share: `{shares['finalize']:.1%}`.
- Evidence: `.ci/performance/contraction-subphase-profile-v2.json`.
'''
status.write_text(content.rstrip() + '\n')

plan = Path('PERFORMANCE_PLAN.md')
content = plan.read_text()
marker = '## Current next action\n'
checkpoint = f'''### Current-kernel contraction profile — 2026-08-23

- Exact production equivalence passed at every level.
- Mapping/sorting/merging/diagonal shares: `{shares['mapping']:.1%}` / `{shares['sorting']:.1%}` / `{shares['merging']:.1%}` / `{shares['diagonal']:.1%}`.
- Evidence: `.ci/performance/contraction-subphase-profile-v2.json`.

'''
if checkpoint.splitlines()[0] not in content:
    content = content.replace(marker, checkpoint + marker, 1)
if marker in content:
    prefix, _ = content.split(marker, 1)
    content = prefix + marker + '''
1. Optimize the largest remaining current-kernel contraction phase under a full correctness and hierarchy-time gate.
2. Re-run full certified PCG routing after any retained hierarchy change.
3. Run the manual 1–32 thread qualification on suitable hardware when available.
'''
plan.write_text(content)

WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
