#!/bin/bash
# Deploy one immutable clean Git snapshot and frozen SCC2 manifests.
set -euo pipefail

run_id=${1:?usage: deploy.sh RUN_ID SOURCE_SHA}
source_sha=${2:?usage: deploy.sh RUN_ID SOURCE_SHA}
project_root=/projectnb/welfgr/cmg-benchmarks
repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

python3 benchmarks/scc/protocol.py >/dev/null 2>&1 || true
python3 - "$run_id" "$source_sha" <<'PY'
import re
import sys
run_id, source_sha = sys.argv[1:]
if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7,40}-b2v1(?:-[a-z0-9-]+)?", run_id):
    raise SystemExit(f"invalid run id: {run_id}")
if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
    raise SystemExit(f"invalid source sha: {source_sha}")
if source_sha[:7] not in run_id:
    raise SystemExit("run id does not contain source SHA prefix")
PY
test "$(git rev-parse HEAD)" = "$source_sha"
test -z "$(git status --porcelain)"

temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
archive="$temporary/$source_sha.tar"
git archive --format=tar --output="$archive" "$source_sha"
archive_sha=$(shasum -a 256 "$archive" | awk '{print $1}')
mkdir -p "$temporary/tasks"
for kind in smoke baseline routing reuse numa memory accuracy batch matched-edge; do
    python3 benchmarks/scc/tasks/generate_tasks.py "$kind" "$temporary/tasks/$kind.jsonl"
done
find "$temporary/tasks" -type f -print0 | sort -z | xargs -0 shasum -a 256 > "$temporary/task-manifests.sha256"

ssh scc "test ! -e '$project_root/runs/$run_id' && mkdir -p '$project_root/runs/$run_id/manifests' '$project_root/runs/$run_id/logs' '$project_root/runs/$run_id/work' '$project_root/runs/$run_id/output' '$project_root/runs/$run_id/receipts' '$project_root/source-archives' '$project_root/code-b2'"
if ! ssh scc "test -f '$project_root/source-archives/$source_sha.tar'"; then
    rsync -a "$archive" "scc:$project_root/source-archives/$source_sha.tar"
fi
remote_archive_sha=$(ssh scc "sha256sum '$project_root/source-archives/$source_sha.tar' | cut -d' ' -f1")
test "$remote_archive_sha" = "$archive_sha"
if ! ssh scc "test -d '$project_root/code-b2/$source_sha'"; then
    ssh scc "mkdir '$project_root/code-b2/$source_sha' && tar -xf '$project_root/source-archives/$source_sha.tar' -C '$project_root/code-b2/$source_sha'"
fi
rsync -a "$temporary/tasks/" "scc:$project_root/runs/$run_id/manifests/tasks/"
rsync -a "$temporary/task-manifests.sha256" "scc:$project_root/runs/$run_id/manifests/task-manifests.sha256"
ssh scc "printf '%s\n' '$source_sha' > '$project_root/runs/$run_id/manifests/source-commit.txt' && printf '%s\n' '$archive_sha' > '$project_root/runs/$run_id/manifests/source-archive-sha256.txt' && printf '%s\n' '$run_id' > '$project_root/runs/$run_id/manifests/run-id.txt' && cd '$project_root/code-b2/$source_sha' && find . -type f -not -path './target/*' -not -path './benchmarks/target/*' -print0 | sort -z | xargs -0 sha256sum > '$project_root/runs/$run_id/manifests/source-files-sha256.txt'"
echo "CMG_SCC2_DEPLOY_SUCCESS run=$run_id source=$source_sha archive_sha256=$archive_sha"
