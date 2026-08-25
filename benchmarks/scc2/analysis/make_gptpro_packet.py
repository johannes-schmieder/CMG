#!/usr/bin/env python3
"""Create the deterministic GPT Pro SCC2 packet with an internal manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("output/gptpro/benchmarks2-packet.zip"))
    args = parser.parse_args()
    root = args.repo_root.resolve()
    explicit = [
        Path("benchmarks2.md"),
        Path("output/pdf/benchmarks2.pdf"),
        Path(".ci/performance/scc-diagnostics-latest.json"),
    ]
    explicit.extend(sorted(Path("benchmarks/report2").rglob("*")))
    files = sorted({path for path in explicit if (root / path).is_file()})
    manifest = {
        path.as_posix(): {"sha256": digest((root / path).read_bytes()), "size": (root / path).stat().st_size}
        for path in files
    }
    manifest_content = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    output = (root / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            info = zipfile.ZipInfo(path.as_posix(), (1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (root / path).read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        info = zipfile.ZipInfo("PACKET_MANIFEST.json", (1980, 1, 1, 0, 0, 0))
        info.external_attr = 0o100644 << 16
        archive.writestr(info, manifest_content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    temporary.replace(output)
    print(f"CMG_SCC2_PACKET_SUCCESS files={len(files)} sha256={digest(output.read_bytes())}")


if __name__ == "__main__":
    main()
