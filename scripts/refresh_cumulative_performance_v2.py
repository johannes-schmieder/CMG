from pathlib import Path

SOURCE = Path("scripts/refresh_cumulative_performance.py")
text = SOURCE.read_text()
text = text.replace(
    "refresh_cumulative_performance.py",
    "refresh_cumulative_performance_v2.py",
)
text = text.replace(
    "refresh-cumulative-performance.yml",
    "refresh-cumulative-performance-v2.yml",
)

marker = '''run(["git", "worktree", "add", "--detach", baseline_root, BASELINE_SHA])

result = {
'''
insert = '''run(["git", "worktree", "add", "--detach", baseline_root, BASELINE_SHA])

# The frozen benchmark checkpoint contains the intended Rayon-based source but
# predates the matching Cargo dependency entry. Restore only that missing build
# dependency so the exact frozen source can be measured.
baseline_manifest = baseline_root / "Cargo.toml"
manifest = baseline_manifest.read_text()
if "rayon" not in manifest:
    dependency_heading = "[dependencies]\\n"
    if dependency_heading not in manifest:
        raise RuntimeError("frozen baseline dependencies heading is missing")
    manifest = manifest.replace(
        dependency_heading,
        dependency_heading + 'rayon = "1.11"\\n',
        1,
    )
    baseline_manifest.write_text(manifest)

result = {
'''
if text.count(marker) != 1:
    raise SystemExit("cumulative worktree marker changed unexpectedly")
text = text.replace(marker, insert, 1)

required = (
    "refresh_cumulative_performance_v2.py",
    "refresh-cumulative-performance-v2.yml",
    'rayon = "1.11"',
    "frozen baseline dependencies heading is missing",
)
for item in required:
    if item not in text:
        raise SystemExit(f"repaired cumulative script missing marker: {item}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
