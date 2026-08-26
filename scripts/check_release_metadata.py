#!/usr/bin/env python3
"""Check that CMG release metadata is internally consistent."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SEMVER_TAG = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)(?P<rc>-rc[1-9]\d*)?$")


def fail(message: str) -> None:
    print(f"release metadata error: {message}", file=sys.stderr)
    raise SystemExit(1)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def cargo_version() -> str:
    match = re.search(
        r'(?ms)^\[package\]\s*$.*?^version\s*=\s*"([^"]+)"\s*$',
        read(ROOT / "Cargo.toml"),
    )
    if not match:
        fail("could not find package.version in Cargo.toml")
    return match.group(1)


def current_tag() -> str | None:
    github_tag = os.environ.get("GITHUB_REF_NAME")
    if os.environ.get("GITHUB_REF_TYPE") == "tag" and github_tag:
        return github_tag
    result = subprocess.run(
        ["git", "describe", "--tags", "--exact-match", "HEAD"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def validate_stata_metadata(version: str, release_date: str) -> None:
    ado_files = sorted(ROOT.glob("*.ado"))
    pkg_files = sorted(ROOT.glob("*.pkg"))
    if not ado_files and not pkg_files:
        return
    if not ado_files or not pkg_files:
        fail("Stata packaging must contain both a root-level .ado and .pkg file")

    primary = ROOT / "cmg.ado"
    if not primary.exists():
        fail("expected primary Stata program at cmg.ado")
    stata_date = datetime.strptime(release_date, "%Y-%m-%d").strftime("%d%b%Y").lower()
    header = re.search(r"(?m)^\*!\s+cmg\s+(\S+)\s+(\S+)\s*$", read(primary))
    if not header or header.groups() != (version, stata_date):
        fail(f"cmg.ado must contain '*! cmg {version} {stata_date}'")

    distribution_date = release_date.replace("-", "")
    for pkg in pkg_files:
        if not re.search(
            rf"(?m)^d Distribution-Date:\s*{distribution_date}\s*$", read(pkg)
        ):
            fail(f"{pkg.name} must contain Distribution-Date: {distribution_date}")


def main() -> None:
    version = cargo_version()
    lock = read(ROOT / "Cargo.lock")
    lock_entry = re.search(
        r'(?ms)^\[\[package\]\]\s*\nname = "cmg"\s*\nversion = "([^"]+)"', lock
    )
    if not lock_entry or lock_entry.group(1) != version:
        fail("the root cmg version in Cargo.lock must match Cargo.toml")

    changelog = read(ROOT / "CHANGELOG.md")
    if not re.search(r"(?m)^## Unreleased\s*$", changelog):
        fail("CHANGELOG.md must contain an Unreleased section")

    tag = current_tag()
    if tag is None:
        print(f"release metadata is consistent for development version {version}")
        return
    match = SEMVER_TAG.fullmatch(tag)
    if not match:
        fail(f"tag {tag!r} is not vX.Y.Z or vX.Y.Z-rcN")
    if match.group("version") != version:
        fail(f"tag {tag} does not match Cargo version {version}")

    if match.group("rc"):
        print(f"release-candidate metadata is consistent for {tag}")
        return

    heading = re.search(
        rf"(?m)^## {re.escape(version)} - (\d{{4}}-\d{{2}}-\d{{2}})\s*$", changelog
    )
    if not heading:
        fail(f"CHANGELOG.md needs a dated section for final release {version}")
    try:
        datetime.strptime(heading.group(1), "%Y-%m-%d")
    except ValueError as error:
        fail(f"invalid changelog release date: {error}")
    validate_stata_metadata(version, heading.group(1))
    print(f"final-release metadata is consistent for {tag}")


if __name__ == "__main__":
    main()
