#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python3 "$root/tools/prepare_source.py"
python3 "$root/tools/check_cmg_vendor.py"
R CMD build "$root" "$@"
