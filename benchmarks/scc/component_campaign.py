#!/usr/bin/env python3
"""Frozen checkpoint/B caller qualification on two exclusive SCC CPU profiles."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import statistics
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET

PROJECT = Path('/projectnb/welfgr/cmg-benchmarks')
SOURCES = {'checkpoint': '90d06d58edf7de43e6e78855b1b14c4b6311b808',
           'b': '1d0396f805fb358106c592c6362506d3cb01517d'}
PROFILES = {'e5-2680v4': {'slots': 28, 'cpu_type': 'E5-2680v4', 'model': 'E5-2680 v4'},
            'gold-6242': {'slots': 32, 'cpu_type': 'Gold-6242', 'model': 'Gold 6242'}}
FIELDS = ('iterations', 'residuals', 'tolerances', 'relative_solution_errors',
          'solution_bit_hashes', 'diagnostic_bits')
TIMES = ('setup_ns', 'workspace_allocation_ns', 'solve_ns', 'total_ns')
DRIVER_FILES = ('benchmarks/callers/src/main.rs', 'benchmarks/callers/src/veneto.rs',
                'benchmarks/callers/src/bin/accuracy.rs', 'benchmarks/callers/Cargo.toml',
                'benchmarks/callers/Cargo.lock', 'benchmarks/src/component_fixtures.rs',
                'benchmarks/src/requested_allocations.rs')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def read(path):
    return json.loads(Path(path).read_text())


def capture(command, **kwargs):
    return subprocess.check_output(command, text=True, **kwargs).strip()


def run_root(run_id):
    require(re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7,40}-b2v1-component-default', run_id), 'bad component run ID')
    return PROJECT / 'runs' / run_id


def configuration(caller, rhs, parallel=False, threads=1):
    return {'caller': caller, 'rhs': rhs, 'parallel': parallel, 'threads': threads}


def configurations():
    return ([configuration(c, n) for c in ('owned', 'buffer') for n in (4, 16)]
            + [configuration('buffer', 4, True)]
            + [configuration('planned', n, True, t) for n in (4, 16) for t in (1, 4)])


def cell(case, suite, config):
    return {'case': case, 'suite': suite, 'seed': 20260908 if suite == 'large' else 20260915, **config}


def canonical_plan():
    mixed = ('large-path-plus-pairs', 'large-grid-plus-pairs', 'large-weighted-path-plus-pairs')
    controls = [('large', s) for s in ('large-connected-path', 'large-connected-grid',
                'large-sparse-connected-worker-firm', 'large-dense-connected-worker-firm')]
    controls += [('stress', s) for s in ('weighted-connected-path', 'bridged-connected-cliques')]
    tasks = []
    for case in mixed:
        for config in configurations():
            tasks.append({'purpose': 'omitted', 'blocks': 12, 'repetitions': 1,
                          'cells': [cell(case, 'large', config)]})
    for config in configurations():
        tasks.append({'purpose': 'connected', 'blocks': 12, 'repetitions': 3,
                      'cells': [cell(case, suite, config) for suite, case in controls]})
    tasks.append({'purpose': 'dense-control', 'blocks': 30, 'repetitions': 20,
                  'cells': [cell('large-dense-connected-worker-firm', 'large', configuration('buffer', 1))]})
    smoke = {'purpose': 'smoke', 'blocks': 2, 'repetitions': 1,
             'cells': [cell('large-path-plus-pairs', 'large', configuration('planned', 16, True, 4)),
                       cell('weighted-connected-path', 'stress', configuration('owned', 4)),
                       cell('large-dense-connected-worker-firm', 'large', configuration('buffer', 1))]}
    return {'schema': 'cmg-component-scc-v1', 'sources': SOURCES, 'profiles': PROFILES,
            'input_classification': 'repository-generated synthetic graph and RHS data only',
            'rust_toolchain': '1.98.0', 'rustflags': '-C target-cpu=x86-64',
            'smoke': [smoke], 'validate': tasks,
            'statistics': {'statistic': 'median paired checkpoint/B invocation-median total time',
                           'bootstrap_resamples': 10000, 'seed': 20260917,
                           'lower_index': 249, 'upper_index': 9749,
                           'connected_margin': 1 / 1.02, 'target_gain': 1.05,
                           'extensions': 0, 'confidence': 'exploratory paired 95%; no multiplicity adjustment'},
            'accuracy': [['large', 'large-weighted-path-plus-pairs', t, '25', '1000'] for t in ('1e-8', '1e-12')]
                        + [['stress', 'weighted-connected-path', '1e-12', '25', '1000'],
                           ['large', 'large-connected-path', '1e-12', '25', '1000']],
            'resources': {'bootstrap_slots': 4, 'bootstrap_memory_per_core': '6G',
                          'benchmark_memory_per_core': '3G', 'smoke_runtime': '01:00:00',
                          'validate_runtime': '06:00:00', 'array_concurrency_per_profile': 2,
                          'exclusive': True, 'scheduler_binding': False,
                          'pinning': 'first one/four physical cores on socket zero within allowed mask'},
            'scope': 'B vs checkpoint; omitted caller cells plus six connected controls and the dense one-RHS control; no default promotion or historical replay'}


def stage_local(repo, destination):
    destination.mkdir(exist_ok=False)
    write(destination / 'component-plan.json', canonical_plan())
    identities = {}
    for arm, commit in SOURCES.items():
        archive = destination / f'{commit}.tar'
        with archive.open('xb') as stream:
            subprocess.run(['git', 'archive', '--format=tar', commit], cwd=repo, stdout=stream, check=True)
        identities[arm] = {'source': commit, 'archive': archive.name, 'sha256': sha(archive)}
    write(destination / 'component-sources.json', identities)


def context(root):
    source = (root / 'manifests/source-commit.txt').read_text().strip()
    archive_hash = (root / 'manifests/source-archive-sha256.txt').read_text().strip()
    require(re.fullmatch('[0-9a-f]{40}', source), 'bad source identity')
    require(sha(PROJECT / 'source-archives' / f'{source}.tar') == archive_hash, 'driver archive hash mismatch')
    code = PROJECT / 'code-b2' / source
    require(read(root / 'manifests/component/component-plan.json') == canonical_plan(), 'plan not canonical')
    for arm, ident in read(root / 'manifests/component/component-sources.json').items():
        require(ident['source'] == SOURCES[arm], 'numerical source mismatch')
        require(sha(root / 'manifests/component' / ident['archive']) == ident['sha256'], 'numerical archive mismatch')
    # Verify deployed runner and harness against the archived bytes, never just a generated manifest.
    with tarfile.open(PROJECT / 'source-archives' / f'{source}.tar') as archive:
        for item in archive:
            if item.isfile():
                path = code / item.name
                require(path.is_file() and hashlib.sha256(archive.extractfile(item).read()).hexdigest() == sha(path), f'deployed source differs: {item.name}')
    return source, archive_hash, code


def execute_logged(command, log, env, cwd=None):
    with log.open('x') as stream:
        subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True, env=env, cwd=cwd)


def bootstrap(root):
    source, archive_hash, code = context(root)
    require(os.environ.get('NSLOTS') == '4' and os.environ.get('JOB_ID'), 'bootstrap requires four compute slots')
    build = root / 'build'; build.mkdir()
    logs = root / 'logs/build'; logs.mkdir()
    work = root / 'work/component'; work.mkdir()
    toolchain = PROJECT / 'toolchains/rustup/toolchains/1.98.0-x86_64-unknown-linux-gnu/bin'
    env = dict(os.environ, RUSTUP_HOME=str(PROJECT / 'toolchains/rustup'),
               CARGO_HOME=str(PROJECT / 'toolchains/cargo'), RUSTUP_AUTO_INSTALL='0',
               CARGO_BUILD_JOBS='4', RUSTFLAGS=canonical_plan()['rustflags'],
               PATH=str(toolchain) + ':' + os.environ['PATH'], RUSTC=str(toolchain / 'rustc'))
    execute_logged([str(toolchain / 'rustc'), '--version', '--verbose'], logs / 'rustc.log', env)
    execute_logged([str(toolchain / 'cargo'), '--version', '--verbose'], logs / 'cargo.log', env)
    execute_logged([sys.executable, '-m', 'unittest', 'discover', '-s', str(code / 'benchmarks/scc/tests'), '-p', 'test_component_campaign.py', '-v'], logs / 'python-tests.log', env)
    identities = {}
    for arm, commit in SOURCES.items():
        arm_work = work / arm; arm_work.mkdir()
        library = arm_work / 'library'; library.mkdir()
        with tarfile.open(root / 'manifests/component' / f'{commit}.tar') as archive:
            archive.extractall(library, filter='data')
        for name in DRIVER_FILES:
            dest = arm_work / name; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(code / name, dest)
        manifest = arm_work / 'benchmarks/callers/Cargo.toml'
        manifest.write_text(manifest.read_text().replace('path = "../.."', 'path = "../../library"'))
        arm_env = dict(env, CMG_BENCH_COMMIT=commit)
        for mode, features in [('minimal', []), ('parallel', ['--features', 'parallel'])]:
            target = work / f'target-{arm}-{mode}'
            arm_env['CARGO_TARGET_DIR'] = str(target)
            prefix = [str(toolchain / 'cargo')]
            execute_logged(prefix + ['fmt', '--all', '--check', '--manifest-path', str(manifest)], logs / f'{arm}-{mode}-fmt.log', arm_env)
            execute_logged(prefix + ['clippy', '--offline', '--locked', '--manifest-path', str(manifest), '--all-targets'] + features + ['--', '-D', 'warnings'], logs / f'{arm}-{mode}-clippy.log', arm_env)
            execute_logged(prefix + ['test', '--offline', '--release', '--locked', '--manifest-path', str(manifest)] + features, logs / f'{arm}-{mode}-tests.log', arm_env)
            execute_logged(prefix + ['build', '--offline', '--release', '--locked', '--manifest-path', str(manifest), '--bin', 'cmg-component-callers', '--bin', 'accuracy'] + features, logs / f'{arm}-{mode}-build.log', arm_env)
            dest = build / arm / mode; dest.mkdir(parents=True)
            for binary in ('cmg-component-callers', 'accuracy'):
                shutil.copy2(target / 'release' / binary, dest / binary)
            identities[f'{arm}-{mode}'] = {'source': commit, 'features': features,
                'binaries': {b: sha(dest / b) for b in ('cmg-component-callers', 'accuracy')},
                'driver_files': {name: sha(arm_work / name) for name in DRIVER_FILES},
                'manifest_sha256': sha(manifest), 'rustflags': env['RUSTFLAGS']}
        library_env = dict(arm_env, CARGO_TARGET_DIR=str(work / f'target-library-{arm}'))
        execute_logged([str(toolchain / 'cargo'), 'test', '--offline', '--locked', '--all-features', '--manifest-path', str(library / 'Cargo.toml')], logs / f'{arm}-library-tests.log', library_env)
    write(root / 'manifests/component-binaries.json', identities)
    write(root / 'receipts/BUILD_SUCCESS', {'source': source, 'archive_sha256': archive_hash,
          'binary_manifest_sha256': sha(root / 'manifests/component-binaries.json'),
          'plan_sha256': sha(root / 'manifests/component/component-plan.json'),
          'job_id': os.environ['JOB_ID'], 'hostname': os.uname().nodename,
          'slots': 4, 'logs': {p.name: sha(p) for p in sorted(logs.iterdir())}})
    print('CMG_COMPONENT_BOOTSTRAP_SUCCESS', flush=True)


def parse_accounting(text):
    records = []
    for segment in re.split(r'^=+\s*$', text, flags=re.M):
        record = {}
        for line in segment.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                record[parts[0]] = parts[1].strip()
        if 'jobnumber' not in record:
            continue
        require(all(k in record for k in ('jobnumber', 'taskid', 'hostname', 'slots', 'failed', 'exit_status', 'ru_wallclock', 'maxvmem', 'start_time', 'end_time')), 'incomplete accounting')
        records.append(record)
    require(len(records) == 1, 'accounting absent, duplicate, or incomplete')
    return records[0]


def stage_paths(root, stage, profile):
    label = 'bootstrap' if stage == 'bootstrap' else f'{stage}-{profile}'
    return label, root / 'manifests' / f'submission-component-{label}.json', root / 'receipts' / f'ACCEPTED-component-{label}.json'


def submit(root, stage, profile):
    _, _, code = context(root)
    plan = canonical_plan()
    label, receipt, accepted = stage_paths(root, stage, profile)
    require(not receipt.exists() and not accepted.exists(), 'submission/acceptance already exists')
    require(not (root / 'output' / f'component-{label}').exists(), 'output namespace exists')
    if stage != 'bootstrap':
        verify_accepted(root, 'bootstrap', 'all')
    if stage == 'validate':
        for name in PROFILES:
            verify_accepted(root, 'smoke', name)
    reservation = receipt.with_suffix('.reserved'); reservation.mkdir(parents=True)
    logdir = root / 'logs' / f'component-{label}'; logdir.mkdir(parents=True)
    qsub = ['qsub', '-terse', '-P', 'welfgr', '-N', f'cmg-component-{label}', '-o', str(logdir), '-e', str(logdir), '-m', 'n']
    if stage == 'bootstrap':
        qsub += ['-pe', 'omp', '4', '-l', 'mem_per_core=6G,h_rt=01:00:00']
        slots, count = 4, 1
    else:
        p = PROFILES[profile]; slots = p['slots']; count = len(plan[stage])
        runtime = plan['resources'][f'{stage}_runtime']
        qsub += ['-pe', 'omp', str(slots), '-l', f'num_proc={slots},cpu_type={p["cpu_type"]},exclusive=true,mem_per_core=3G,h_rt={runtime}',
                 '-t', f'1-{count}', '-tc', '2' if stage == 'validate' else '1']
    qsub += [str(code / 'benchmarks/scc/run_component.sh'), root.name, stage, profile]
    write(reservation / 'request.json', {'command': qsub, 'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()})
    completed = subprocess.run(qsub, text=True, capture_output=True)
    (reservation / 'qsub.stdout').write_text(completed.stdout)
    (reservation / 'qsub.stderr').write_text(completed.stderr)
    require(completed.returncode == 0, 'qsub failed; preserve reservation and inspect before retry')
    response = completed.stdout.strip()
    require(re.fullmatch(r'[0-9]+(?:\.[0-9]+-[0-9]+:[0-9]+)?', response), 'ambiguous qsub response; inspect reservation and queue')
    job = response.split('.')[0]
    snapshot = subprocess.run(['qstat', '-j', job], text=True, capture_output=True)
    (reservation / 'qstat.txt').write_text(snapshot.stdout + snapshot.stderr)
    write(receipt, {'stage': stage, 'profile': profile, 'job_id': job, 'qsub_response': response,
                    'slots': slots, 'tasks': count, 'command': qsub, 'logdir': str(logdir),
                    'plan_sha256': sha(root / 'manifests/component/component-plan.json')})
    if stage != 'bootstrap' and snapshot.returncode == 0:
        require(re.search(r'exclusive=(?:true|1)', snapshot.stdout, re.I),
                'accepted job snapshot lacks exclusive resource; inspect before proceeding')
    print(response, flush=True)


def host_snapshot(path, expected_job):
    raw = capture(['qhost', '-h', os.uname().nodename.split('.')[0], '-j', '-xml'])
    with path.open('x') as stream:
        stream.write(raw + '\n')
    jobs = {n.attrib['name'] for n in ET.fromstring(raw).iter('job')}
    require(jobs == {expected_job}, f'exclusive host has unexpected scheduler jobs: {jobs}')


def hardware(profile):
    p = PROFILES[profile]
    require(int(os.environ.get('NSLOTS', '0')) == p['slots'], 'slot mismatch')
    allowed = sorted(os.sched_getaffinity(0))
    require(len(allowed) == p['slots'] == os.cpu_count(), 'whole-host CPU mask/count mismatch')
    models = sorted({line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')})
    require(models and all(p['model'] in m for m in models), 'CPU model mismatch')
    table = capture(['lscpu', '-p=CPU,CORE,SOCKET,NODE'])
    physical = []
    for line in table.splitlines():
        if line.startswith('#'):
            continue
        cpu, core, socket, node = map(int, line.split(','))
        if cpu in allowed:
            physical.append({'cpu': cpu, 'core': core, 'socket': socket, 'node': node})
    physical.sort(key=lambda row: (row['socket'], row['core'], row['cpu']))
    require(len({(r['socket'], r['core']) for r in physical}) == p['slots'], 'not one CPU per physical core')
    first = [r['cpu'] for r in physical if r['socket'] == physical[0]['socket']][:4]
    require(len(first) == 4, 'four same-socket physical CPUs unavailable')
    return {'hostname': os.uname().nodename, 'cpu_models': models, 'allowed_cpus': allowed,
            'slots': p['slots'], 'physical': physical, 'benchmark_cpus': first,
            'exclusive_requested': True, 'scheduler_binding_requested': False,
            'raw_nslots': os.environ.get('NSLOTS'), 'raw_pe': os.environ.get('PE'),
            'raw_sge_binding': os.environ.get('SGE_BINDING'), 'job_id': os.environ['JOB_ID'],
            'task_id': os.environ['SGE_TASK_ID'], 'load_start': os.getloadavg()}


def pin(cpus, record, binary, args):
    before = sorted(os.sched_getaffinity(0))
    require(set(cpus) <= set(before), 'pinning outside allowed mask')
    os.sched_setaffinity(0, cpus)
    require(sorted(os.sched_getaffinity(0)) == sorted(cpus), 'pinning failed')
    write(record, {'hostname': os.uname().nodename, 'job_id': os.environ.get('JOB_ID'),
                  'task_id': os.environ.get('SGE_TASK_ID'), 'before': before,
                  'after': sorted(os.sched_getaffinity(0)), 'binary_sha256': sha(binary)})
    os.execv(str(binary), [str(binary)] + args)


def finite(value):
    if isinstance(value, float):
        require(math.isfinite(value), 'nonfinite numerical value')
    elif isinstance(value, dict):
        for item in value.values(): finite(item)
    elif isinstance(value, list):
        for item in value: finite(item)


def invocation(root, directory, key, arm, mode, binary_name, args, cpus, code):
    identity = read(root / 'manifests/component-binaries.json')[f'{arm}-{mode}']
    binary = root / 'build' / arm / mode / binary_name
    require(sha(binary) == identity['binaries'][binary_name], 'binary changed')
    output = directory / f'{key}.jsonl'; error = directory / f'{key}.stderr'; affinity = directory / f'{key}.affinity.json'
    command = [sys.executable, str(code / 'benchmarks/scc/component_campaign.py'), 'pin', ','.join(map(str, cpus)), str(affinity), str(binary)] + args
    env = dict(os.environ, OMP_NUM_THREADS=str(len(cpus)), OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
               BLIS_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1', RAYON_NUM_THREADS=str(len(cpus)))
    with output.open('x') as out, error.open('x') as err:
        result = subprocess.run(command, stdout=out, stderr=err, env=env, timeout=3600)
    require(result.returncode == 0 and not error.read_bytes(), f'invocation failed: {key}')
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    finite(rows)
    require(read(affinity)['after'] == cpus, 'invocation affinity mismatch')
    return rows


def check_samples(rows, arm, c, repetitions):
    environment = [row for row in rows if row['type'] == 'environment']
    inputs = [row for row in rows if row['type'] == 'input']
    samples = [row for row in rows if row['type'] == 'sample']
    require(len(environment) == len(inputs) == 1 and len(samples) == repetitions, 'wrong output counts')
    require(not any(row['type'] == 'failure' for row in rows), 'solver failure')
    env = environment[0]
    for key, expected in {'source': SOURCES[arm], 'suite': c['suite'], 'seed': c['seed'], 'repetitions': repetitions,
                          'rhs_count': c['rhs'], 'warmups': 2, 'caller': c['caller'], 'threads': c['threads'],
                          'parallel_feature': c['parallel'], 'phase_profiling': False, 'allocation_tracking': False}.items():
        require(env[key] == expected, f'environment differs: {key}')
    require(inputs[0]['case'] == c['case'], 'case substring was not exact')
    for i, sample in enumerate(samples):
        require(sample['case'] == c['case'] and sample['round'] == i and sample['route'] == 'baseline', 'sample configuration mismatch')
        require(all(sample[t] > 0 for t in TIMES), 'nonpositive timing')
        require(sample['total_ns'] == sum(sample[t] for t in TIMES[:-1]), 'total mismatch')
        require(all(len(sample[k]) == c['rhs'] for k in FIELDS), 'RHS count mismatch')
        require(all(r <= t and t > 0 for r, t in zip(sample['residuals'], sample['tolerances'])), 'residual certificate failure')
        require(all(sample[k] == samples[0][k] for k in FIELDS), 'nondeterministic numerical fields')
    return inputs[0], samples


def interval(ratios):
    settings = canonical_plan()['statistics']; rng = random.Random(settings['seed'])
    draws = sorted(statistics.median(rng.choices(ratios, k=len(ratios))) for _ in range(settings['bootstrap_resamples']))
    result = [draws[settings['lower_index']], draws[settings['upper_index']]]
    require(result[0] <= statistics.median(ratios) <= result[1], 'bootstrap interval does not contain estimate')
    return result


def benchmark(root, stage, profile, task_id):
    _, _, code = context(root)
    verify_accepted(root, 'bootstrap', 'all')
    if stage == 'validate':
        for name in PROFILES: verify_accepted(root, 'smoke', name)
    task = canonical_plan()[stage][task_id - 1]
    label, receipt, _ = stage_paths(root, stage, profile)
    submission = read(receipt)
    require(submission['job_id'] == os.environ['JOB_ID'], 'job mismatch')
    require(int(os.environ['SGE_TASK_ID']) == task_id and 1 <= task_id <= submission['tasks'], 'task mismatch')
    directory = root / 'output' / f'component-{label}' / f'task-{task_id:03d}'
    directory.mkdir(parents=True, exist_ok=False)
    host = hardware(profile); write(directory / 'host.json', host)
    host_snapshot(directory / 'scheduler-start.xml', host['job_id'])
    orders = [['checkpoint', 'b']] * (task['blocks'] // 2) + [['b', 'checkpoint']] * (task['blocks'] // 2)
    random.Random(20260917 + task_id).shuffle(orders)
    write(directory / 'task.json', {'task': task, 'orders': orders, 'profile': profile,
          'task_id': task_id, 'plan_sha256': sha(root / 'manifests/component/component-plan.json')})
    results = []
    for ci, c in enumerate(task['cells']):
        mode = 'parallel' if c['parallel'] else 'minimal'; ratios = []; reference = None; arms = {}
        for block, order in enumerate(orders):
            values = {}
            for arm in order:
                key = f'cell-{ci:02d}-block-{block:02d}-{arm}'
                args = [str(task['repetitions']), str(c['rhs']), c['case'], '--suite', c['suite'], '--seed', str(c['seed']),
                        '--route', 'baseline', '--caller', c['caller'], '--threads', str(c['threads'])]
                rows = invocation(root, directory, key, arm, mode, 'cmg-component-callers', args, host['benchmark_cpus'][:c['threads']], code)
                inp, samples = check_samples(rows, arm, c, task['repetitions'])
                signature = {'input': inp, **{k: samples[0][k] for k in FIELDS}}
                if reference is None: reference = signature
                require(signature == reference, f'checkpoint/B or repeated numerical mismatch: {key}')
                values[arm] = statistics.median(s['total_ns'] for s in samples)
                arms.setdefault(arm, []).append({t: statistics.median(s[t] for s in samples) for t in TIMES})
            ratios.append(values['checkpoint'] / values['b'])
            print(f'CMG_COMPONENT_PROGRESS task={task_id} cell={ci + 1}/{len(task["cells"])} block={block + 1}/{len(orders)}', flush=True)
        bounds = interval(ratios); point = statistics.median(ratios); margin = canonical_plan()['statistics']['connected_margin']
        results.append({'cell': c, 'ratios': ratios, 'speedup': point, 'interval': bounds, 'arm_phase_medians_ns': arms,
                        'gate': 'qualified' if bounds[0] >= margin else 'clear_loss' if bounds[1] < margin else 'inconclusive',
                        'target_gain_supported': bounds[0] >= 1.05, 'numerical_signature': reference})
    accuracy = []
    if stage == 'smoke':
        for index, args in enumerate(canonical_plan()['accuracy']):
            pair = []
            for arm in SOURCES:
                rows = invocation(root, directory, f'accuracy-{index}-{arm}', arm, 'minimal', 'accuracy', args, host['benchmark_cpus'][:1], code)
                inp = next(r for r in rows if r['type'] == 'input'); value = next(r for r in rows if r['type'] == 'result')
                require(inp['source'] == SOURCES[arm] and value['status'] == 'ok', 'accuracy solver failure')
                require(value['fresh_residual'] <= value['allowed_residual'], 'accuracy certificate failure')
                pair.append({'arm': arm, 'input': inp, 'result': value})
            require(all(pair[0]['result'][k] == pair[1]['result'][k] for k in ('solution_hash', 'iterations', 'restarts', 'allowed_residual', 'backward_error')), 'accuracy checkpoint/B mismatch')
            accuracy.append({'case': args, 'results': pair})
        require(accuracy[1]['results'][1]['result']['relative_solution_error'] < accuracy[0]['results'][1]['result']['relative_solution_error'] / 10, 'strict tolerance did not improve difficult weighted case')
    host_snapshot(directory / 'scheduler-end.xml', host['job_id'])
    write(directory / 'summary.json', {'stage': stage, 'profile': profile, 'task_id': task_id, 'hostname': host['hostname'],
          'job_id': host['job_id'], 'results': results, 'accuracy': accuracy, 'load_end': os.getloadavg(), 'numerical_checks_passed': True})
    inventory = {str(p.relative_to(directory)): sha(p) for p in sorted(directory.iterdir()) if p.is_file()}
    write(directory / 'SUCCESS.json', {'files': inventory, 'plan_sha256': sha(root / 'manifests/component/component-plan.json')})
    print('CMG_COMPONENT_TASK_SUCCESS', flush=True)
    print('CMG_COMPONENT_VALIDATE_SUCCESS', flush=True)


def verify_files(directory, files):
    for name, digest in files.items():
        require(sha(directory / name) == digest, f'evidence changed: {directory / name}')


def verify_accepted(root, stage, profile):
    _, _, path = stage_paths(root, stage, profile)
    accepted = read(path)
    require(accepted['plan_sha256'] == sha(root / 'manifests/component/component-plan.json'), 'accepted plan mismatch')
    verify_files(root, accepted['files'])
    if stage == 'bootstrap':
        build = read(root / 'receipts/BUILD_SUCCESS')
        require(build['source'] == (root / 'manifests/source-commit.txt').read_text().strip()
                and build['archive_sha256'] == (root / 'manifests/source-archive-sha256.txt').read_text().strip(),
                'bootstrap source/archive identity changed')
        require(build['binary_manifest_sha256'] == sha(root / 'manifests/component-binaries.json'), 'build identity changed')
        for key, identity in read(root / 'manifests/component-binaries.json').items():
            arm, mode = key.split('-')
            for binary, digest in identity['binaries'].items(): require(sha(root / 'build' / arm / mode / binary) == digest, 'built binary changed')
    else:
        namespace = root / 'output' / f'component-{stage}-{profile}'
        require({p.name for p in namespace.iterdir()} ==
                {f'task-{task:03d}' for task in range(1, len(canonical_plan()[stage]) + 1)},
                'extra or missing task directories')
        for task in range(1, len(canonical_plan()[stage]) + 1):
            directory = root / 'output' / f'component-{stage}-{profile}' / f'task-{task:03d}'
            verify_files(directory, read(directory / 'SUCCESS.json')['files'])
    return accepted


def accept(root, stage, profile):
    context(root)
    label, submission_path, accepted_path = stage_paths(root, stage, profile)
    if accepted_path.exists():
        verify_accepted(root, stage, profile)
        print(f'CMG_COMPONENT_ACCEPTED {label} (verified existing receipt)')
        return
    submission = read(submission_path); job = submission['job_id']; files = {}; accounting = []
    for task_id in range(1, submission['tasks'] + 1):
        command = ['qacct', '-j', job]
        if stage != 'bootstrap': command += ['-t', str(task_id)]
        result = subprocess.run(command, text=True, capture_output=True)
        require(result.returncode == 0, 'accounting pending')
        record = parse_accounting(result.stdout)
        require(record['jobnumber'] == job and record['taskid'] == ('undefined' if stage == 'bootstrap' else str(task_id)), 'wrong accounting identity')
        require(int(record['slots']) == submission['slots'] and record['failed'] == '0' and record['exit_status'] == '0', 'failed scheduler accounting')
        require(float(record['ru_wallclock']) > 0, 'bad accounting walltime')
        accounting.append(record)
        account_path = root / 'receipts' / 'component-accounting' / f'{job}.{task_id}.txt'
        account_path.parent.mkdir(parents=True, exist_ok=True)
        if account_path.exists(): require(account_path.read_text() == result.stdout, 'accounting changed')
        else:
            with account_path.open('x') as stream: stream.write(result.stdout)
        files[str(account_path.relative_to(root))] = sha(account_path)
        suffix = '' if stage == 'bootstrap' else f'.{task_id}'
        logdir = Path(submission['logdir'])
        out = list(logdir.glob(f'*.o{job}{suffix}')); err = list(logdir.glob(f'*.e{job}{suffix}'))
        require(len(out) == len(err) == 1 and err[0].stat().st_size == 0, 'missing scheduler logs or nonempty stderr')
        marker = 'CMG_COMPONENT_BOOTSTRAP_SUCCESS' if stage == 'bootstrap' else 'CMG_COMPONENT_TASK_SUCCESS'
        require(out[0].read_text().count(marker) == 1, 'missing/duplicate success marker')
        for path in out + err: files[str(path.relative_to(root))] = sha(path)
        if stage == 'bootstrap':
            build = read(root / 'receipts/BUILD_SUCCESS')
            require(build['hostname'].split('.')[0] == record['hostname'].split('.')[0] and build['job_id'] == job, 'bootstrap host/job mismatch')
            verify_files(root / 'logs/build', build['logs'])
            for name in build['logs']: files[f'logs/build/{name}'] = sha(root / 'logs/build' / name)
            for name in ['receipts/BUILD_SUCCESS', 'manifests/component-binaries.json']: files[name] = sha(root / name)
        else:
            require(out[0].read_text().count('CMG_COMPONENT_VALIDATE_SUCCESS') == 1, 'validation marker missing')
            directory = root / 'output' / f'component-{label}' / f'task-{task_id:03d}'
            success = read(directory / 'SUCCESS.json'); verify_files(directory, success['files'])
            summary = read(directory / 'summary.json')
            require(summary['hostname'].split('.')[0] == record['hostname'].split('.')[0] and summary['job_id'] == job and summary['task_id'] == task_id, 'task/qacct mismatch')
            require(summary['numerical_checks_passed'], 'numerical gate failed')
            files[str((directory / 'SUCCESS.json').relative_to(root))] = sha(directory / 'SUCCESS.json')
    write(accepted_path, {'stage': stage, 'profile': profile, 'job_id': job, 'accounting': accounting,
                         'files': files, 'plan_sha256': sha(root / 'manifests/component/component-plan.json')})
    verify_accepted(root, stage, profile)
    print(f'CMG_COMPONENT_ACCEPTED {label}', flush=True)


def summary(root):
    context(root)
    rows = []
    for profile in PROFILES:
        verify_accepted(root, 'validate', profile)
        for task in range(1, len(canonical_plan()['validate']) + 1):
            result = read(root / 'output' / f'component-validate-{profile}' / f'task-{task:03d}' / 'summary.json')
            rows.extend({'profile': profile, 'hostname': result['hostname'], **row} for row in result['results'])
    print(json.dumps({'results': rows, 'main_default_promoted': False}, indent=2, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['stage-local', 'plan', 'submit', 'run', 'accept', 'summary', 'pin'])
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(); values = args.arguments
    if args.action == 'stage-local': stage_local(Path(values[0]), Path(values[1]))
    elif args.action == 'plan': print(json.dumps(canonical_plan(), indent=2))
    elif args.action == 'pin': pin([int(c) for c in values[0].split(',')], Path(values[1]), Path(values[2]), values[3:])
    else:
        root = run_root(values[0])
        if args.action == 'summary': summary(root); return
        stage, profile = values[1:3]
        require(stage in ('bootstrap', 'smoke', 'validate'), 'bad stage')
        require(profile == 'all' if stage == 'bootstrap' else profile in PROFILES, 'bad profile')
        if args.action == 'submit': submit(root, stage, profile)
        elif args.action == 'accept': accept(root, stage, profile)
        elif stage == 'bootstrap': bootstrap(root)
        else: benchmark(root, stage, profile, int(os.environ['SGE_TASK_ID']))


if __name__ == '__main__':
    main()
