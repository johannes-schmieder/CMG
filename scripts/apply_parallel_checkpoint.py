#!/usr/bin/env python3
"""Apply the first optional parallel execution checkpoint."""

from pathlib import Path
import base64
import gzip
import shutil

FILES = {
    "scripts/payloads/Cargo_toml.b64": "Cargo.toml",
    "scripts/payloads/PERFORMANCE_PLAN_md.b64": "PERFORMANCE_PLAN.md",
    "scripts/payloads/_github_workflows_rust_yml.b64": ".github/workflows/rust.yml",
    "scripts/payloads/src_error_rs.b64": "src/error.rs",
    "scripts/payloads/src_execution_rs.b64": "src/execution.rs",
    "scripts/payloads/src_lib_rs.b64": "src/lib.rs",
    "scripts/payloads/src_csr_rs.b64": "src/csr.rs",
    "scripts/payloads/src_pcg_rs.b64": "src/pcg.rs",
    "scripts/payloads/tests_parallel_rs.b64": "tests/parallel.rs",
}

for payload_name, target_name in FILES.items():
    encoded = Path(payload_name).read_text(encoding="ascii").strip()
    decoded = gzip.decompress(base64.b64decode(encoded))
    target = Path(target_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(decoded)

Path(".ci/apply-parallel-failure.log").unlink(missing_ok=True)
Path(".github/workflows/apply-parallel-checkpoint.yml").unlink(missing_ok=True)
Path("scripts/apply_parallel_checkpoint.py").unlink(missing_ok=True)
shutil.rmtree("scripts/payloads")
