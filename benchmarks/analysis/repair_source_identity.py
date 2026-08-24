#!/usr/bin/env python3
"""Create an auditable derived run with source identity repaired from its manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_run", type=Path)
    parser.add_argument("derived_run", type=Path)
    args = parser.parse_args()

    raw = args.raw_run.resolve()
    derived = args.derived_run.resolve()
    if not raw.is_dir():
        raise SystemExit(f"raw run does not exist: {raw}")
    if derived.exists():
        raise SystemExit(f"derived run already exists: {derived}")
    if raw.name != derived.name:
        raise SystemExit("derived run must retain the raw run basename")

    expected_source = (raw / "manifests" / "source-commit.txt").read_text().strip()
    if not expected_source or expected_source == "unknown":
        raise SystemExit("raw run manifest has no usable source identity")
    raw_tree_sha256 = tree_digest(raw)
    derived.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(raw, derived, copy_function=shutil.copy2)

    changes: list[dict[str, str]] = []
    for path in sorted(derived.glob("output/task-*/*.json")):
        result = json.loads(path.read_text())
        implementation = result.get("implementation")
        is_kernel = "case" in result and "upstream_commit" in result
        if implementation == "matlab":
            if result.get("source_commit") != expected_source:
                raise SystemExit(f"MATLAB source identity does not match manifest: {path}")
            continue
        if implementation != "rust" and not is_kernel:
            continue
        if result.get("source_commit") != "unknown":
            raise SystemExit(f"unexpected repair input in {path}")

        before = path.read_bytes()
        before_value = json.loads(before)
        result["source_commit"] = expected_source
        comparable_before = dict(before_value)
        comparable_after = dict(result)
        comparable_before.pop("source_commit")
        comparable_after.pop("source_commit")
        if comparable_before != comparable_after:
            raise SystemExit(f"repair would change non-source data in {path}")
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        changes.append(
            {
                "path": str(path.relative_to(derived)),
                "before_sha256": hashlib.sha256(before).hexdigest(),
                "after_sha256": sha256(path),
                "before_source_commit": "unknown",
                "after_source_commit": expected_source,
            }
        )

    rust_count = sum("/rust-t" in f"/{change['path']}" for change in changes)
    kernel_count = len(changes) - rust_count
    if rust_count != 90 or kernel_count != 6:
        raise SystemExit(
            f"expected 90 Rust and 6 C-kernel repairs, found {rust_count} and {kernel_count}"
        )

    receipt = {
        "schema": 1,
        "operation": "derive_source_identity_from_immutable_run_manifest",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_run": str(raw),
        "derived_run": str(derived),
        "run_id": raw.name,
        "expected_source_commit": expected_source,
        "raw_tree_sha256": raw_tree_sha256,
        "changed_fields": ["source_commit"],
        "changed_files": len(changes),
        "rust_files": rust_count,
        "c_kernel_files": kernel_count,
        "changes": changes,
    }
    receipt_path = derived / "receipts" / "DERIVED_SOURCE_REPAIR.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    receipt["derived_tree_sha256_before_validation"] = tree_digest(derived)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(
        "CMG_SOURCE_REPAIR_SUCCESS "
        f"run={raw.name} files={len(changes)} receipt={receipt_path}"
    )


if __name__ == "__main__":
    main()
