#!/usr/bin/env python3
"""Validate and summarize one scalar current-head SCC Rust/C kernel run."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path


UPSTREAM_COMMIT = "19752fc102f8cae8e34f66457bfaccb1aaa60375"
EXPECTED = {
    ("path", 100_000),
    ("path", 300_000),
    ("path", 1_000_000),
    ("worker-firm", 100_000),
    ("worker-firm", 300_000),
    ("worker-firm", 1_000_000),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def accounting_field(content: str, name: str) -> str | None:
    match = re.search(rf"^{re.escape(name)}\s+(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else None


def ratio_range(rows: list[dict], *keys: str) -> list[float]:
    values = []
    for row in rows:
        value = row
        for key in keys:
            value = value[key]
        values.append(float(value))
    return [min(values), max(values)]


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: validate_c_kernel_run.py RUN_ROOT JOB_ID")
    run_root = Path(sys.argv[1])
    job_id = sys.argv[2].split(".", 1)[0]
    source = (run_root / "manifests/source-commit.txt").read_text().strip()
    archive = (run_root / "manifests/source-archive-sha256.txt").read_text().strip()
    environment_id = (run_root / "manifests/environment-id.txt").read_text().strip()
    identity = json.loads((run_root / "manifests/c-kernel-identity.json").read_text())
    success = run_root / "receipts/c-kernel/SUCCESS"
    require(success.exists() and "success=true" in success.read_text(), "missing application success receipt")

    rows = []
    seen = set()
    for path in sorted((run_root / "output/c-kernel").glob("*.json")):
        row = json.loads(path.read_text())
        key = (row.get("case"), int(row.get("vertices", -1)))
        require(key in EXPECTED and key not in seen, f"unexpected or duplicate result: {path}")
        seen.add(key)
        require(row.get("schema") == 3, f"wrong schema: {path}")
        require(row.get("source_commit") == source, f"wrong source: {path}")
        require(row.get("source_archive_sha256") == archive, f"wrong archive: {path}")
        require(row.get("binary_sha256") == identity["binary_sha256"], f"wrong binary: {path}")
        require(row.get("upstream_commit") == UPSTREAM_COMMIT, f"wrong upstream: {path}")
        require(int(row.get("repetitions", 0)) == 7, f"wrong repetition count: {path}")
        for value in (
            row["rust_over_c"],
            row["projection"]["restriction_rust_over_c"],
            row["projection"]["prolongation_rust_over_c"],
            row["cycle"]["rust_over_c"],
        ):
            require(math.isfinite(value) and value > 0, f"invalid timing ratio: {path}")
        require(row["max_scaled_error"] <= 2e-12, f"SpMV mismatch: {path}")
        require(row["projection"]["restriction_max_scaled_error"] <= 2e-12, f"restriction mismatch: {path}")
        require(row["projection"]["prolongation_max_scaled_error"] <= 2e-12, f"prolongation mismatch: {path}")
        require(row["cycle"]["quotient_max_scaled_error"] <= 5e-10, f"cycle mismatch: {path}")
        rows.append(row)
    require(seen == EXPECTED, f"incomplete result set: {sorted(EXPECTED - seen)}")

    accounting_path = run_root / f"receipts/accounting/{job_id}.txt"
    require(accounting_path.exists(), "missing qacct receipt")
    accounting = accounting_path.read_text()
    failed = accounting_field(accounting, "failed")
    exit_status = accounting_field(accounting, "exit_status")
    require(failed == "0" and exit_status == "0", f"scheduler failure: failed={failed} exit_status={exit_status}")

    compact_rows = []
    for row in rows:
        compact_rows.append(
            {
                "case": row["case"],
                "vertices": row["vertices"],
                "canonical_edges": row["canonical_edges"],
                "spmv_rust_over_c": row["rust_over_c"],
                "restriction_rust_over_c": row["projection"]["restriction_rust_over_c"],
                "prolongation_rust_over_c": row["projection"]["prolongation_rust_over_c"],
                "cycle_dimension": row["cycle"]["dimension"],
                "cycle_rust_over_c": row["cycle"]["rust_over_c"],
                "max_scaled_error": max(
                    row["max_scaled_error"],
                    row["projection"]["restriction_max_scaled_error"],
                    row["projection"]["prolongation_max_scaled_error"],
                    row["cycle"]["quotient_max_scaled_error"],
                ),
            }
        )
    summary = {
        "schema": 1,
        "success": True,
        "run_id": run_root.name,
        "job_id": job_id,
        "source_commit": source,
        "source_archive_sha256": archive,
        "binary_sha256": identity["binary_sha256"],
        "upstream_commit": UPSTREAM_COMMIT,
        "environment_id": environment_id,
        "host": accounting_field(accounting, "hostname"),
        "slots": int(accounting_field(accounting, "slots") or 0),
        "wallclock_seconds": int(accounting_field(accounting, "ru_wallclock") or 0),
        "maxvmem": accounting_field(accounting, "maxvmem"),
        "repetitions": 7,
        "scope": "SCC Gold-6242, isolated serial pinned-C kernels",
        "ratios": {
            "spmv_rust_over_c": ratio_range(rows, "rust_over_c"),
            "restriction_rust_over_c": ratio_range(rows, "projection", "restriction_rust_over_c"),
            "prolongation_rust_over_c": ratio_range(rows, "projection", "prolongation_rust_over_c"),
            "bounded_cycle_rust_over_c": ratio_range(rows, "cycle", "rust_over_c"),
        },
        "results": compact_rows,
    }
    output = run_root / "receipts/C_KERNEL_RUN_VALIDATION.json"
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print("CMG_SCC2_C_KERNEL_RUN_VALIDATION_SUCCESS cases=6")


if __name__ == "__main__":
    main()
