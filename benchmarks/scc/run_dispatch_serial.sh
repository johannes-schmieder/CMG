#!/bin/bash -l
# Serial-only adapter; numerical execution remains in the original runner.
set -euo pipefail
if ! type module >/dev/null 2>&1; then
    set +u
    source /etc/profile
    set -u
fi
module purge
module load python3/3.12.4
export PYTHONDONTWRITEBYTECODE=1
# qsub spools this script, so $0 is not the deployed source path.
helper=${CMG_DISPATCH_HELPER:?missing immutable helper directory}
[[ "$helper" =~ ^/projectnb/welfgr/cmg-benchmarks/code-b2/[0-9a-f]{40}/benchmarks/scc$ ]]
exec python3 "$helper/dispatch_serial_launcher.py"
