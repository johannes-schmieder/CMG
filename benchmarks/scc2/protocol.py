"""Shared constants and strict helpers for the SCC2 diagnostics protocol."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


PROTOCOL_VERSION = "cmg-scc2-v1"
UPSTREAM_COMMIT = "19752fc102f8cae8e34f66457bfaccb1aaa60375"
FAMILIES = ("path", "grid", "worker-firm", "dense-worker-firm", "weak-community")
THREADS = (1, 8, 16, 32)
RUN_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7,40}-b2v1(?:-[a-z0-9-]+)?$")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_run_id(value: str) -> str:
    require(bool(RUN_ID_PATTERN.fullmatch(value)), f"invalid SCC2 run id: {value}")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_tree(paths: Iterable[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        require(isinstance(value, dict), f"{path}:{line_number}: row is not an object")
        rows.append(value)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(canonical_json(row) + "\n" for row in rows)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload)
    temporary.replace(path)
