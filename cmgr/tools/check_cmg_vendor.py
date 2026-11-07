#!/usr/bin/env python3
"""Fail packaging on CMG payload, revision, or lockfile drift."""
import json
from pathlib import Path
import re
import subprocess
from cmg_vendor import inventory, REPOSITORY

root = Path(__file__).resolve().parents[1]
record = json.loads((root / 'inst/CMG-SOURCE.json').read_text())
assert record['repository'] == REPOSITORY
assert re.fullmatch('[0-9a-f]{40}', record['revision'])
assert inventory(root / 'src/rust/vendor/cmg') == record['files'], 'CMG vendor drift'
assert record['revision'] in (root / 'inst/CMG-SOURCE').read_text(), 'CMG pin mismatch'
# Cargo is already required for the native build. Let it parse TOML and reject
# stale lockfiles without requiring Python 3.11's tomllib or a third-party parser.
subprocess.run(
    ['cargo', 'metadata', '--format-version', '1', '--locked', '--offline'],
    cwd=root / 'src/rust', check=True, stdout=subprocess.DEVNULL,
)
print('CMGR_CMG_VENDOR=PASS ' + record['revision'])
