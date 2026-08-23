"""Recover and repair the pinned direct compact-contraction gate.

The original one-shot script is retained in Git history at the commit below.
This wrapper applies two narrowly scoped recovery fixes before executing it:
(1) match the current documented/`#[must_use]` Edge accessor, and
(2) restore the benchmark lockfile after temporary parallel-feature builds.
"""

from pathlib import Path
import subprocess

PINNED_GATE_COMMIT = "23727a222c30f9f6ab4724c7069412f0d5ea3fc2"
source = subprocess.check_output(
    [
        "git",
        "show",
        f"{PINNED_GATE_COMMIT}:scripts/direct_compact_contraction_gate.py",
    ],
    text=True,
)

old = "manifest_original = benchmark_manifest.read_text()\n"
new = (
    old
    + "benchmark_lock = Path('benchmarks/Cargo.lock')\n"
    + "lock_original = benchmark_lock.read_text()\n"
)
if source.count(old) != 1:
    raise SystemExit("benchmark manifest recovery anchor was not unique")
source = source.replace(old, new, 1)

old = """def restore_benchmark_files():
    benchmark_manifest.write_text(manifest_original)
    parallel_benchmark.unlink(missing_ok=True)
"""
new = """def restore_benchmark_files():
    benchmark_manifest.write_text(manifest_original)
    benchmark_lock.write_text(lock_original)
    parallel_benchmark.unlink(missing_ok=True)
"""
if source.count(old) != 1:
    raise SystemExit("benchmark restore recovery anchor was not unique")
source = source.replace(old, new, 1)

old = "'''    pub const fn weight(self) -> f64 {\n"
new = (
    "'''    /// Return the strictly positive edge weight.\n"
    "    #[must_use]\n"
    "    pub const fn weight(self) -> f64 {\n"
)
if source.count(old) != 2:
    raise SystemExit("expected exactly two Edge weight-string anchors")
source = source.replace(old, new)

compiled_path = Path("/tmp/direct_compact_contraction_gate_recovered.py")
compiled_path.write_text(source)
exec(compile(source, str(compiled_path), "exec"), {"__name__": "__main__"})
