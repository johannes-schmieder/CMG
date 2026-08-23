import subprocess

SOURCE_COMMIT = '46bfb616364d6b8e10e217615bee49a4498bffb4'
SOURCE_PATH = 'scripts/parallel_endpoint_sort_gate.py'

text = subprocess.check_output(
    ['git', 'show', f'{SOURCE_COMMIT}:{SOURCE_PATH}'],
    text=True,
)

for old, new in (
    ('parallel_endpoint_sort_gate.py', 'parallel_endpoint_sort_gate_v2.py'),
    ('parallel-endpoint-sort-gate.yml', 'parallel-endpoint-sort-gate-v2.yml'),
):
    text = text.replace(old, new)

old_parser = '''    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith('{')
    ]
    if len(payloads) != 1:
        raise RuntimeError(f'{tag}: unexpected benchmark output: {payloads}')
    match = re.search(
'''
new_parser = '''    json_start = completed.stdout.find('{')
    if json_start < 0:
        raise RuntimeError(f'{tag}: benchmark JSON object missing')
    payload = json.loads(completed.stdout[json_start:])
    match = re.search(
'''
if text.count(old_parser) != 1:
    raise SystemExit('historical benchmark parser changed unexpectedly')
text = text.replace(old_parser, new_parser, 1)

old_payload = '''    payload = payloads[0]
    payload['peak_rss_kib'] = int(match.group(1))
    return payload
'''
new_payload = '''    payload['peak_rss_kib'] = int(match.group(1))
    return payload
'''
if text.count(old_payload) != 1:
    raise SystemExit('historical payload tail changed unexpectedly')
text = text.replace(old_payload, new_payload, 1)

old_plan = '''if checkpoint_heading not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
'''
new_plan = '''if checkpoint_heading in plan:
    start = plan.index(checkpoint_heading)
    end = plan.index(marker, start)
    plan = plan[:start] + checkpoint + plan[end:]
else:
    plan = plan.replace(marker, checkpoint + marker, 1)
if marker in plan:
'''
if text.count(old_plan) != 1:
    raise SystemExit('historical plan checkpoint logic changed unexpectedly')
text = text.replace(old_plan, new_plan, 1)

required = (
    'json.loads(completed.stdout[json_start:])',
    'parallel_endpoint_sort_gate_v2.py',
    'parallel-endpoint-sort-gate-v2.yml',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f'repaired gate missing marker: {marker}')

exec(
    compile(text, 'scripts/parallel_endpoint_sort_gate_v2.py', 'exec'),
    {'__name__': '__main__'},
)
