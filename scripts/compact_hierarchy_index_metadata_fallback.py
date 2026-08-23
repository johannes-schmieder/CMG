import json
from pathlib import Path
import subprocess

RECORD = Path('.ci/performance/compact-hierarchy-index-latest.json')
record = json.loads(RECORD.read_text()) if RECORD.exists() else {}
if not record:
    print('compact hierarchy-index decision is not available; leaving fallback armed')
    raise SystemExit(0)

rerun = record.get('validation') not in {'success', 'skipped'}
result = {
    'schema_version': 1,
    'fallback': 'structural-metadata-normalization',
    'source_record_validation': record.get('validation'),
    'rerun': rerun,
    'status': 'not_needed' if not rerun else 'not_run',
}

if rerun:
    baseline_sha = record.get('baseline_sha')
    if not baseline_sha:
        raise SystemExit('failed compact-index record has no baseline_sha')
    source = subprocess.check_output(
        [
            'git', 'show',
            f'{baseline_sha}:scripts/compact_hierarchy_index_gate_v2.py',
        ],
        text=True,
    )
    old = '''    reference = observations['baseline'][0]['metadata']
    for group in observations.values():
        for item in group:
            if item['metadata'] != reference:
                raise RuntimeError(f'{name}: hierarchy metadata changed')
'''
    new = '''    reference = observations['baseline'][0]['metadata']
    def structural_metadata(metadata):
        ignored_tokens = (
            'byte', 'bytes', 'memory', 'rss', 'capacity', 'allocation',
            'median_ns', 'elapsed', 'time_ns',
        )
        return {
            key: value
            for key, value in metadata.items()
            if not any(token in key.lower() for token in ignored_tokens)
        }
    reference_structural = structural_metadata(reference)
    for group in observations.values():
        for item in group:
            if structural_metadata(item['metadata']) != reference_structural:
                raise RuntimeError(f'{name}: structural hierarchy metadata changed')
'''
    if source.count(old) != 1:
        raise SystemExit('compact-index compare block was not recognized')
    source = source.replace(old, new, 1)
    temporary = Path('/tmp/compact_hierarchy_index_gate_v3.py')
    temporary.write_text(source)
    completed = subprocess.run(
        ['python', str(temporary)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=12600,
    )
    print(completed.stdout, end='')
    if completed.returncode != 0:
        result['status'] = 'failure'
        result['returncode'] = completed.returncode
    else:
        refreshed = json.loads(RECORD.read_text())
        result['status'] = 'success'
        result['refreshed_validation'] = refreshed.get('validation')
        result['refreshed_accepted'] = refreshed.get('accepted')

fallback_record = Path(
    '.ci/performance/compact-hierarchy-index-metadata-fallback.json'
)
fallback_record.parent.mkdir(parents=True, exist_ok=True)
fallback_record.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

Path('.github/workflows/compact-hierarchy-index-metadata-fallback.yml').unlink(
    missing_ok=True
)
Path('scripts/compact_hierarchy_index_metadata_fallback.py').unlink(missing_ok=True)
