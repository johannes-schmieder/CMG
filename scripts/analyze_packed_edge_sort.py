import json
from pathlib import Path
import re
import subprocess

PREREQUISITE = Path('.ci/performance/compact-hierarchy-index-latest.json')
prerequisite = json.loads(PREREQUISITE.read_text()) if PREREQUISITE.exists() else {}
if prerequisite.get('validation') not in {'success', 'skipped'}:
    print('compact hierarchy-index gate is unresolved; leaving sort analysis armed')
    raise SystemExit(0)

sites = []
for path in sorted(Path('src').glob('*.rs')):
    text = path.read_text()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.search(r'\b(?:par_)?sort(?:_unstable)?_by\s*\(', line):
            continue
        start = index
        end = min(len(lines), index + 30)
        snippet_lines = []
        balance = 0
        started = False
        for cursor in range(start, end):
            source = lines[cursor]
            snippet_lines.append(source)
            for character in source:
                if character in '([{':
                    balance += 1
                    started = True
                elif character in ')]}':
                    balance -= 1
            if started and balance <= 0 and cursor > start:
                end = cursor + 1
                break
        snippet = '\n'.join(snippet_lines)
        endpoint_tokens = sum(
            snippet.count(token)
            for token in (
                '.u', '.v', '.left', '.right', '.source', '.target',
                'endpoint', 'first', 'second', 'pair_key', 'packed',
            )
        )
        sites.append({
            'path': path.as_posix(),
            'start_line': start + 1,
            'end_line': end,
            'snippet': snippet,
            'endpoint_token_count': endpoint_tokens,
            'already_uses_packed_key': bool(
                re.search(r'pair_key|packed_(?:key|endpoint)|u64\s*::', snippet)
            ),
            'uses_total_cmp': 'total_cmp' in snippet,
            'candidate_for_packed_pair_key': (
                endpoint_tokens >= 2
                and not re.search(r'pair_key|packed_(?:key|endpoint)', snippet)
            ),
        })

result = {
    'schema_version': 1,
    'analysis': 'packed-edge-sort-sites',
    'source_sha': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], text=True
    ).strip(),
    'prerequisite_source_sha': prerequisite.get('baseline_sha'),
    'site_count': len(sites),
    'candidate_count': sum(site['candidate_for_packed_pair_key'] for site in sites),
    'sites': sites,
}
record = Path('.ci/performance/packed-edge-sort-analysis.json')
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

candidates = [site for site in sites if site['candidate_for_packed_pair_key']]
checkpoint = f'''### Packed endpoint-key sort analysis — 2026-08-23

- Sort/comparator sites inspected: `{len(sites)}`.
- Candidate sites still comparing endpoints separately: `{len(candidates)}`.
- No production source was changed.
- Machine-readable evidence: `.ci/performance/packed-edge-sort-analysis.json`.

'''
plan_path = Path('PERFORMANCE_PLAN.md')
plan = plan_path.read_text()
heading = '### Packed endpoint-key sort analysis — 2026-08-23\n'
marker = '## Current next action\n'
if heading in plan:
    start = plan.index(heading)
    end = plan.index(marker, start)
    plan = plan[:start] + checkpoint + plan[end:]
else:
    plan = plan.replace(marker, checkpoint + marker, 1)
plan_path.write_text(plan)

status_path = Path('PERFORMANCE_STATUS.md')
status = status_path.read_text().rstrip()
heading = '## Packed endpoint-key sort analysis\n'
block = (
    '## Packed endpoint-key sort analysis\n\n'
    f'- Comparator sites inspected: `{len(sites)}`.\n'
    f'- Candidate sites: `{len(candidates)}`.\n'
    '- No production source changed.\n'
    '- Evidence: `.ci/performance/packed-edge-sort-analysis.json`.\n'
)
if heading in status:
    start = status.index(heading)
    end = status.find('\n## ', start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + block + status[end:]
else:
    status += '\n\n' + block
status_path.write_text(status.rstrip() + '\n')

Path('.github/workflows/analyze-packed-edge-sort.yml').unlink(missing_ok=True)
Path('scripts/analyze_packed_edge_sort.py').unlink(missing_ok=True)
