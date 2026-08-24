from pathlib import Path
import subprocess

SOURCE_COMMIT = "fb3d51f158b4c933a7b71d5f5952c701a958b2f9"
SOURCE_PATH = "scripts/refresh_cumulative_performance_v3.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "refresh_cumulative_performance_v3.py",
    "refresh_cumulative_performance_v4.py",
)
text = text.replace(
    "refresh-cumulative-performance-v3.yml",
    "refresh-cumulative-performance-v4.yml",
)
text = text.replace(
    "cmg-cumulative-v3-",
    "cmg-cumulative-v4-",
)
text = text.replace(
    "cmg-cumulative-v3-baseline",
    "cmg-cumulative-v4-baseline",
)
text = text.replace(
    "cmg-cumulative-v3-baseline-target",
    "cmg-cumulative-v4-baseline-target",
)
text = text.replace(
    "cmg-cumulative-v3-current-target",
    "cmg-cumulative-v4-current-target",
)

old_parser = '''    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected benchmark output: {payloads}")
    rss_match = re.search(
'''
new_parser = '''    json_start = completed.stdout.find("{")
    json_end = completed.stdout.rfind("}")
    if json_start < 0 or json_end < json_start:
        raise RuntimeError(
            f"benchmark JSON object was not found: {completed.stdout[-2000:]}"
        )
    payload = json.loads(completed.stdout[json_start : json_end + 1])
    rss_match = re.search(
'''
if text.count(old_parser) != 1:
    raise SystemExit("historical cumulative JSON parser changed unexpectedly")
text = text.replace(old_parser, new_parser, 1)

old_assignment = '''    payload = payloads[0]
    payload["process_peak_rss_kib"] = int(rss_match.group(1))
'''
new_assignment = '''    payload["process_peak_rss_kib"] = int(rss_match.group(1))
'''
if text.count(old_assignment) != 1:
    raise SystemExit("historical cumulative payload assignment changed unexpectedly")
text = text.replace(old_assignment, new_assignment, 1)

required = (
    "refresh_cumulative_performance_v4.py",
    "refresh-cumulative-performance-v4.yml",
    'json_start = completed.stdout.find("{")',
    "json.loads(completed.stdout[json_start : json_end + 1])",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"cumulative v4 launcher missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
