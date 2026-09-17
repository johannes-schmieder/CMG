#!/bin/bash
# Diagnose, never promote, the frozen oracle on two immutable source snapshots.
set -euo pipefail
test "${NSLOTS:-0}" = 28
test -n "${JOB_ID:-}"
run=${1:?}
code=$(pwd)
test "$(sha256sum "$run/manifests/screen-reference/baseline.tar" | cut -d' ' -f1)" = "$(cat "$run/manifests/screen-reference/archive-sha256.txt")"
mkdir "$run/work/oracle" "$run/work/oracle/baseline-source" "$run/output/oracle"
tar -xf "$run/manifests/screen-reference/baseline.tar" -C "$run/work/oracle/baseline-source"
sha256sum "$code/benchmarks/src/bin/bridge-oracle.rs" > "$run/manifests/oracle-harness.sha256"
for variant in baseline candidate; do
    driver="$run/work/oracle/$variant"
    mkdir "$driver" "$driver/src"
    cp "$code/benchmarks/src/bin/bridge-oracle.rs" "$driver/src/main.rs"
    source_path="$code"
    if [[ "$variant" == baseline ]]; then source_path="$run/work/oracle/baseline-source"; fi
    printf '[package]\nname="bridge-oracle"\nversion="0.0.0"\nedition="2024"\n[dependencies]\ncmg={path="%s",features=["parallel","experimental-components"]}\n[features]\ncandidate-screen=[]\n' "$source_path" > "$driver/Cargo.toml"
    features=''
    if [[ "$variant" == candidate ]]; then features=candidate-screen; fi
    cargo build --offline --release --manifest-path "$driver/Cargo.toml" --features "$features" --target-dir "$driver/target" > "$run/logs/oracle-build-$variant.log" 2>&1
    status=0
    "$driver/target/release/bridge-oracle" "$run/output/oracle/$variant-input.bin" > "$run/output/oracle/$variant.jsonl" 2> "$run/logs/oracle-$variant.stderr" || status=$?
    [[ "$status" == 0 || "$status" == 2 ]]
    printf '%s\n' "$status" > "$run/output/oracle/$variant-exit.txt"
done
cmp "$run/output/oracle/baseline-input.bin" "$run/output/oracle/candidate-input.bin"
find "$run/work/oracle" -type f \( -name bridge-oracle -o -name Cargo.lock \) -print0 | sort -z | xargs -0 sha256sum > "$run/manifests/oracle-builds.sha256"
find "$run/output/oracle" -type f -print0 | sort -z | xargs -0 sha256sum > "$run/manifests/oracle-results.sha256"
python3 - "$run" <<'PY'
import json, pathlib, sys
run = pathlib.Path(sys.argv[1])
root = run / 'output/oracle'
def rows(variant):
    values = [json.loads(line) for line in (root / (variant + '.jsonl')).read_text().splitlines()]
    assert len(values) == 8
    assert {(v['threads'], v['direct_threshold'], v['component']) for v in values} == {(t, d, c) for t in (1, 4) for d in (8, 65) for c in (False, True)}
    assert all(v['forward_limit'] == 1e-6 for v in values)
    assert int((root / (variant + '-exit.txt')).read_text()) == (0 if all(v['passed'] for v in values) else 2)
    return values
baseline, candidate = rows('baseline'), rows('candidate')
def dependencies(path):
    packages = {}
    for entry in path.read_text().split('[[package]]')[1:]:
        fields = {}
        for line in entry.splitlines():
            if line.startswith(('name = ', 'version = ', 'checksum = ')):
                key, value = line.split(' = ', 1)
                fields[key] = value.strip('"')
        if fields.get('name') not in ('cmg', 'bridge-oracle'):
            packages[fields['name']] = (fields['version'], fields.get('checksum'))
    return packages
assert dependencies(run / 'work/oracle/baseline/Cargo.lock') == dependencies(run / 'work/oracle/candidate/Cargo.lock') == dependencies(pathlib.Path.cwd() / 'Cargo.lock')
record = {'candidate_source': (run / 'manifests/source-commit.txt').read_text().strip(),
          'baseline_source': (run / 'manifests/screen-reference/source.txt').read_text().strip(),
          'baseline_passed': all(v['passed'] for v in baseline),
          'candidate_passed': all(v['passed'] for v in candidate),
          'numerical_records_identical': baseline == candidate,
          'scope': 'Diagnostic completion is not accuracy qualification. Frozen forward/residual gates are unchanged.'}
with (root / 'summary.json').open('x') as output:
    json.dump(record, output, indent=2)
    output.write('\n')
print('CMG_BRIDGE_ORACLE_DIAGNOSIS_COMPLETE ' + json.dumps(record, sort_keys=True))
PY
