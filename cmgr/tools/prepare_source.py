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

try:
    git_root = subprocess.check_output(
        ['git', '-C', str(source), 'rev-parse', '--show-toplevel'],
        text=True, stderr=subprocess.PIPE).strip()
except subprocess.CalledProcessError:
    git_root = ''
if not git_root or Path(git_root).resolve() != source.resolve():
    raise SystemExit(
        'Preparing CMG requires a Git checkout. Install a generated cmgr source '
        'tarball from https://github.com/johannes-schmieder/CMG/releases instead.')

# The provenance commit must identify both the solver and the maintained R
# adapter. Git ignores exclude generated payloads, native builds and caches;
# untracked maintained files must be committed rather than silently packaged.
source_paths = [*sorted(ROOT_FILES), *sorted(DIRECTORIES), 'cmgr']
changed = subprocess.check_output(
    ['git', '-C', str(source), 'diff', 'HEAD', '--name-only', '-z', '--',
     *source_paths], text=True).split('\0')
untracked = subprocess.check_output(
    ['git', '-C', str(source), 'ls-files', '--others', '--exclude-standard', '-z', '--',
     *source_paths], text=True).split('\0')
changed = [path for path in changed + untracked if path]
if changed:
    raise SystemExit('Commit CMG and cmgr source changes before packaging: ' + ', '.join(changed))

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
