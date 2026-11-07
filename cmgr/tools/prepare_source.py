#!/usr/bin/env python3
"""Generate the R package's offline CMG payload from this repository's HEAD.

The repository root is the only maintained CMG implementation. Generated source
and provenance are ignored by Git but included in the standalone R source tarball.
"""
import json
from pathlib import Path
import re
import subprocess
import sys

from cmg_vendor import DIRECTORIES, ROOT_FILES

package = Path(__file__).resolve().parents[1]
source = package.parent
if not (source / 'Cargo.toml').is_file() or not (source / 'src/lib.rs').is_file():
    raise SystemExit('Run this tool from the cmgr directory inside the CMG repository.')

# Never silently build a stale committed solver when its working source differs.
changed = subprocess.check_output(
    ['git', '-C', str(source), 'diff', 'HEAD', '--name-only', '--',
     *sorted(ROOT_FILES), *sorted(DIRECTORIES)], text=True).splitlines()
untracked = subprocess.check_output(
    ['git', '-C', str(source), 'ls-files', '--others', '--exclude-standard', '--',
     *sorted(ROOT_FILES), *sorted(DIRECTORIES)], text=True).splitlines()
if changed or untracked:
    raise SystemExit('Commit CMG source changes before packaging: ' + ', '.join(changed + untracked))

subprocess.run([sys.executable, str(package / 'tools/cmg_vendor.py'), 'refresh',
                '--source', str(source), '--rev', 'HEAD',
                '--destination', str(package / 'src/rust/vendor/cmg'),
                '--record', str(package / 'inst/CMG-SOURCE.json')], check=True)
record = json.loads((package / 'inst/CMG-SOURCE.json').read_text())
version = re.search(r'^version\s*=\s*"([^"]+)"',
                    (package / 'src/rust/vendor/cmg/Cargo.toml').read_text(), re.M).group(1)
(package / 'inst/CMG-SOURCE').write_text(
    'Repository: ' + record['repository'] + '\nCommit: ' + record['revision'] +
    '\nVersion: ' + version + '\nLicense: MIT\n' +
    'Payload: generated from the canonical repository root; see CMG-SOURCE.json.\n' +
    'Adapter configuration: upstream defaults (max_levels = 32).\n')
