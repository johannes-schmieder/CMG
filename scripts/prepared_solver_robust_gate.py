"""Requalify the prepared parallel PCG solver with robust interleaved timing."""

from pathlib import Path
import re
import subprocess

ROOT = Path.cwd()
HISTORICAL_GATE = "c9b90c0f9594dced6ccb1eb7f63efe847bd49d51"
HISTORICAL_CANDIDATE = "eeb2ca727cfb96da6064797ed47437db8a896f55"

source = subprocess.check_output(
    [
        "git",
        "show",
        f"{HISTORICAL_GATE}:scripts/prepared_parallel_solver_gate.py",
    ],
    text=True,
)
candidate_path = ROOT / "scripts/add_prepared_parallel_solver_robust.py"
candidate_path.write_text(
    subprocess.check_output(
        [
            "git",
            "show",
            f"{HISTORICAL_CANDIDATE}:scripts/add_prepared_parallel_solver.py",
        ],
        text=True,
    )
)

replacements = [
    (
        "RESULT = ROOT / '.ci/performance/prepared-parallel-solver-latest.json'\n",
        "RESULT = ROOT / '.ci/performance/prepared-parallel-solver-robust-latest.json'\n",
    ),
    (
        "WORKFLOW = ROOT / '.github/workflows/prepared-parallel-solver-gate.yml'\n",
        "WORKFLOW = ROOT / '.github/workflows/prepared-parallel-solver-robust.yml'\n",
    ),
    (
        "GATE = ROOT / 'scripts/prepared_parallel_solver_gate.py'\n",
        "GATE = ROOT / 'scripts/prepared_solver_robust_gate.py'\n",
    ),
    (
        "CANDIDATE = ROOT / 'scripts/add_prepared_parallel_solver.py'\n",
        "CANDIDATE = ROOT / 'scripts/add_prepared_parallel_solver_robust.py'\n",
    ),
    (
        "'experiment': 'prepared-parallel-pcg-solver',\n",
        "'experiment': 'prepared-parallel-pcg-solver-robust',\n",
    ),
    (
        "`.ci/performance/prepared-parallel-solver-latest.json`.\n",
        "`.ci/performance/prepared-parallel-solver-robust-latest.json`.\n",
    ),
]
for old, new in replacements:
    if source.count(old) != 1:
        raise SystemExit(f"robust-gate anchor was not unique: {old!r}")
    source = source.replace(old, new, 1)

# Use seven measured samples per strategy rather than two.
if source.count("rhs_count, 2, 4]") != 4:
    raise SystemExit("unexpected four-thread specification count")
source = source.replace("rhs_count, 2, 4]", "rhs_count, 7, 4]")
if source.count("rhs_count, 2, 2]") != 2:
    raise SystemExit("unexpected two-thread specification count")
source = source.replace("rhs_count, 2, 2]", "rhs_count, 7, 2]")

# Alternate automatic execution before and after the explicit strategies so it
# is not systematically timed last.
old_loop = '''    for repetition in 0..repetitions {
        for strategy in orders[repetition % orders.len()] {
            let start = Instant::now();
            let results = run_explicit(
                strategy,
                &solver,
                black_box(&right_hand_sides),
                options,
                &mut serial_workspace,
                &mut planned_workspace,
            );
            let elapsed = start.elapsed().as_nanos();
            black_box(results);
            match strategy {
                ExplicitStrategy::Serial => serial_times.push(elapsed),
                ExplicitStrategy::AcrossRhs => across_times.push(elapsed),
                ExplicitStrategy::Planned => planned_times.push(elapsed),
            }
        }
        let start = Instant::now();
        let results = solver
            .solve_batch_with_workspace(
                black_box(&right_hand_sides),
                options,
                &mut auto_workspace,
            )
            .expect("automatic solve should converge");
        auto_times.push(start.elapsed().as_nanos());
        black_box(results);
    }

    let serial_ns = median(serial_times);
    let across_ns = median(across_times);
    let planned_ns = median(planned_times);
    let auto_ns = median(auto_times);
    let best_explicit_ns = serial_ns.min(across_ns).min(planned_ns);
    let report = solver
        .select_batch_execution(rhs_count)
        .expect("routing report should be available");
'''
new_loop = '''    for repetition in 0..repetitions {
        if repetition % 2 == 0 {
            let start = Instant::now();
            let results = solver
                .solve_batch_with_workspace(
                    black_box(&right_hand_sides),
                    options,
                    &mut auto_workspace,
                )
                .expect("automatic solve should converge");
            auto_times.push(start.elapsed().as_nanos());
            black_box(results);
        }
        for strategy in orders[repetition % orders.len()] {
            let start = Instant::now();
            let results = run_explicit(
                strategy,
                &solver,
                black_box(&right_hand_sides),
                options,
                &mut serial_workspace,
                &mut planned_workspace,
            );
            let elapsed = start.elapsed().as_nanos();
            black_box(results);
            match strategy {
                ExplicitStrategy::Serial => serial_times.push(elapsed),
                ExplicitStrategy::AcrossRhs => across_times.push(elapsed),
                ExplicitStrategy::Planned => planned_times.push(elapsed),
            }
        }
        if repetition % 2 == 1 {
            let start = Instant::now();
            let results = solver
                .solve_batch_with_workspace(
                    black_box(&right_hand_sides),
                    options,
                    &mut auto_workspace,
                )
                .expect("automatic solve should converge");
            auto_times.push(start.elapsed().as_nanos());
            black_box(results);
        }
    }

    let serial_ns = median(serial_times);
    let across_ns = median(across_times);
    let planned_ns = median(planned_times);
    let auto_ns = median(auto_times);
    let best_explicit_ns = serial_ns.min(across_ns).min(planned_ns);
    let report = solver
        .select_batch_execution(rhs_count)
        .expect("routing report should be available");
    let selected_explicit_ns = match report.execution() {
        ParallelPcgExecution::Serial => serial_ns,
        ParallelPcgExecution::Planned => planned_ns,
        ParallelPcgExecution::AcrossRightHandSides => across_ns,
    };
'''
if source.count(old_loop) != 1:
    raise SystemExit("timing-loop anchor was not unique")
source = source.replace(old_loop, new_loop, 1)

old_json = '''\\"best_explicit_ns\\":{best_explicit_ns},\\"auto_over_best\\":{:.17e},\\"serial_over_best\\":{:.17e}'''
new_json = '''\\"best_explicit_ns\\":{best_explicit_ns},\\"selected_explicit_ns\\":{selected_explicit_ns},\\"auto_over_selected\\":{:.17e},\\"auto_over_best\\":{:.17e},\\"serial_over_best\\":{:.17e}'''
if source.count(old_json) != 1:
    raise SystemExit("benchmark JSON anchor was not unique")
source = source.replace(old_json, new_json, 1)
old_args = '''        report.concurrency(),
        auto_ns as f64 / best_explicit_ns as f64,
        serial_ns as f64 / best_explicit_ns as f64,
'''
new_args = '''        report.concurrency(),
        auto_ns as f64 / selected_explicit_ns as f64,
        auto_ns as f64 / best_explicit_ns as f64,
        serial_ns as f64 / best_explicit_ns as f64,
'''
if source.count(old_args) != 1:
    raise SystemExit("benchmark JSON argument anchor was not unique")
source = source.replace(old_args, new_args, 1)

# Replace the aggregate decision block while retaining the original strict
# automatic-to-best limits and adding stricter automatic-to-selected limits.
metrics_start = source.index("    ratios = []\n")
metrics_end = source.index("except Exception as error:", metrics_start)
metrics = '''    best_ratios = []
    selected_ratios = []
    maximum_difference = 0.0
    maximum_workspace_excess = 0
    for name, arguments in specs:
        observation = sample(binary, arguments, name)
        result['cases'][name] = observation
        best_ratios.append(observation['auto_over_best'])
        selected_ratios.append(observation['auto_over_selected'])
        maximum_difference = max(
            maximum_difference,
            observation['max_across_difference'],
            observation['max_planned_difference'],
            observation['max_auto_difference'],
        )
        maximum_workspace_excess = max(
            maximum_workspace_excess,
            observation['retained_workspace_bytes']
            - observation['workspace_pool_bytes'],
        )
    result['geometric_auto_over_best'] = math.exp(
        sum(math.log(value) for value in best_ratios) / len(best_ratios)
    )
    result['maximum_auto_over_best'] = max(best_ratios)
    result['geometric_auto_over_selected'] = math.exp(
        sum(math.log(value) for value in selected_ratios) / len(selected_ratios)
    )
    result['maximum_auto_over_selected'] = max(selected_ratios)
    result['maximum_scaled_difference'] = maximum_difference
    result['maximum_workspace_excess_bytes'] = maximum_workspace_excess
    result['acceptance_limits'] = {
        'geometric_auto_over_best_max': 1.04,
        'maximum_auto_over_best_max': 1.12,
        'geometric_auto_over_selected_max': 1.03,
        'maximum_auto_over_selected_max': 1.08,
        'maximum_scaled_difference': 5.0e-9,
        'maximum_workspace_excess_bytes': 0,
    }
    result['accepted'] = (
        result['geometric_auto_over_best'] <= 1.04
        and result['maximum_auto_over_best'] <= 1.12
        and result['geometric_auto_over_selected'] <= 1.03
        and result['maximum_auto_over_selected'] <= 1.08
        and maximum_difference <= 5.0e-9
        and maximum_workspace_excess <= 0
    )
    result['decision_reason'] = (
        'robust qualification passed; prepared solver matched its selected strategy and stayed near the best explicit strategy with reusable bounded workspaces'
        if result['accepted']
        else 'robust qualification passed but timing, numerical, or workspace-retention gates were not met'
    )
'''
source = source[:metrics_start] + metrics + source[metrics_end:]

# Replace the stale plan checkpoint rather than only inserting when absent.
plan_start = source.index("if PLAN.exists():\n")
plan_end = source.index("WORKFLOW.unlink(missing_ok=True)\n", plan_start)
plan_block = """if PLAN.exists():
    text = PLAN.read_text()
    marker = '## Current next action\\n'
    status = 'retained' if result['accepted'] else 'not retained'
    checkpoint = f'''### Prepared parallel solver robust checkpoint — 2026-08-23

- The memory-aware prepared solver candidate was **{status}** after robust,
  interleaved seven-sample timing.
- Validation status: `{result['validation']}`.
- Geometric/worst automatic-to-best explicit timing ratios:
  `{result.get('geometric_auto_over_best', float('nan')):.3f}x` /
  `{result.get('maximum_auto_over_best', float('nan')):.3f}x`.
- Geometric/worst automatic-to-selected explicit timing ratios:
  `{result.get('geometric_auto_over_selected', float('nan')):.3f}x` /
  `{result.get('maximum_auto_over_selected', float('nan')):.3f}x`.
- Machine-readable evidence:
  `.ci/performance/prepared-parallel-solver-robust-latest.json`.

'''
    pattern = re.compile(
        r'### Prepared parallel solver checkpoint — 2026-08-23\\n.*?(?=## Current next action\\n)',
        re.DOTALL,
    )
    if pattern.search(text):
        text = pattern.sub(checkpoint, text, count=1)
    elif marker in text:
        text = text.replace(marker, checkpoint + marker, 1)
    else:
        raise RuntimeError('PERFORMANCE_PLAN current-next-action heading missing')
    next_actions = (
        '''## Current next action

1. Complete ordinary Ubuntu/macOS/Windows qualification of the retained prepared
   solver abstraction.
2. Profile packed contraction keys and reusable contraction buffers.
3. Obtain controlled 8–32-thread and high-memory evidence when suitable hardware
   is available.
4. Remove obsolete one-shot workflows, staging scripts, and committed Python
   cache files after active gates are secure.
'''
        if result['accepted']
        else
        '''## Current next action

1. Keep the explicit serial, planned, and across-RHS APIs; do not add automatic
   routing until broader hardware evidence supports a stable rule.
2. Profile packed contraction keys and reusable contraction buffers.
3. Obtain controlled 8–32-thread and high-memory evidence when suitable hardware
   is available.
4. Remove obsolete one-shot workflows, staging scripts, and committed Python
   cache files after active gates are secure.
'''
    )
    text = re.sub(r'## Current next action\\n.*\\Z', next_actions, text, flags=re.DOTALL)
    PLAN.write_text(text)

"""
source = source[:plan_start] + plan_block + source[plan_end:]

source = source.replace(
    "'perf: add prepared memory-aware parallel PCG solver'",
    "'perf: retain robust prepared parallel PCG solver'",
)
source = source.replace(
    "'perf: record prepared parallel solver experiment'",
    "'perf: record robust prepared solver experiment'",
)

exec(compile(source, "<prepared_solver_robust_gate>", "exec"), {"__name__": "__main__"})
