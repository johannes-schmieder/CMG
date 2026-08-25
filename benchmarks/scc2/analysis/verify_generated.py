#!/usr/bin/env python3
"""Verify maintained SCC2 data, report, packet, and checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.repo_root.resolve()
    required = [
        Path("benchmarks/report2/data/results.csv"),
        Path("benchmarks/report2/data/samples.csv"),
        Path("benchmarks/report2/data/phases.csv"),
        Path("benchmarks/report2/data/memory.csv"),
        Path("output/pdf/benchmarks2.pdf"),
        Path("output/gptpro/benchmarks2-packet.zip"),
        Path(".ci/performance/scc-diagnostics-latest.json"),
    ]
    for path in required:
        full = root / path
        if not full.is_file() or full.stat().st_size == 0:
            raise SystemExit(f"missing generated artifact {path}")
    packet = root / "output/gptpro/benchmarks2-packet.zip"
    with zipfile.ZipFile(packet) as archive:
        manifest = json.loads(archive.read("PACKET_MANIFEST.json"))
        for path, identity in manifest.items():
            content = archive.read(path)
            if sha(content) != identity["sha256"] or len(content) != identity["size"]:
                raise SystemExit(f"packet identity mismatch: {path}")
    print(f"CMG_SCC2_GENERATED_SUCCESS packet_sha256={sha(packet.read_bytes())}")


if __name__ == "__main__":
    main()
