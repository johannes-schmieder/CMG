"""Add an opt-in PCG path backed by a prebuilt ParallelCmgPlan."""

from pathlib import Path


def replace_once(text: str, old: str, new: str, context: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"{context}: expected one anchor, found {text.count(old)}")
    return text.replace(old, new, 1)


# Extend ParallelCmgPlan with a validated public boundary and a prevalidated
# crate-internal path for repeated Krylov applications.
preconditioner_path = Path("src/preconditioner.rs")
preconditioner = preconditioner_path.read_text()
start = preconditioner.index("    pub fn apply_compatible_into(\n")
end = preconditioner.index("    fn validate(", start)
plan_methods = '''    pub fn apply_compatible_into(
        &self,
        preconditioner: &CmgPreconditioner,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        self.apply_compatible_into_with_validation(
            preconditioner,
            rhs,
            output,
            workspace,
            ValidationOptions::default(),
            executor,
        )
    }

    /// Apply a compatible right-hand side with explicit validation tolerances.
    pub fn apply_compatible_into_with_validation(
        &self,
        preconditioner: &CmgPreconditioner,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        validation: ValidationOptions,
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        preconditioner.apply_compatible_into_with_plan(
            rhs,
            output,
            workspace,
            validation,
            self,
            executor,
        )
    }

    pub(crate) fn apply_compatible_into_prevalidated(
        &self,
        preconditioner: &CmgPreconditioner,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        validation: ValidationOptions,
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        preconditioner.apply_compatible_into_with_prevalidated_plan(
            rhs,
            output,
            workspace,
            validation,
            self,
            executor,
        )
    }

'''
preconditioner = preconditioner[:start] + plan_methods + preconditioner[end:]
preconditioner = replace_once(
    preconditioner,
    "    fn validate(&self, preconditioner: &CmgPreconditioner) -> Result<(), CmgError> {\n",
    "    pub(crate) fn validate(\n        &self,\n        preconditioner: &CmgPreconditioner,\n    ) -> Result<(), CmgError> {\n",
    "ParallelCmgPlan validation visibility",
)
matvec_anchor = "    fn matvec_into(\n"
matvec_insert = '''    pub(crate) fn finest_matvec_into(
        &self,
        graph: &Laplacian,
        input: &[f64],
        output: &mut [f64],
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        self.matvec_into(0, graph, input, output, executor)
    }

'''
preconditioner = replace_once(
    preconditioner,
    matvec_anchor,
    matvec_insert + matvec_anchor,
    "ParallelCmgPlan finest matvec insertion",
)
apply_start = preconditioner.index("    #[cfg(feature = \"parallel\")]\n    fn apply_compatible_into_with_plan(")
apply_end = preconditioner.index(
    "    /// Apply with explicit compatibility-validation tolerances.\n", apply_start
)
apply_methods = '''    #[cfg(feature = "parallel")]
    fn apply_compatible_into_with_plan(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        validation: ValidationOptions,
        plan: &ParallelCmgPlan,
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        plan.validate(self)?;
        self.apply_compatible_into_with_prevalidated_plan(
            rhs,
            output,
            workspace,
            validation,
            plan,
            executor,
        )
    }

    #[cfg(feature = "parallel")]
    fn apply_compatible_into_with_prevalidated_plan(
        &self,
        rhs: &[f64],
        output: &mut [f64],
        workspace: &mut CmgWorkspace,
        validation: ValidationOptions,
        plan: &ParallelCmgPlan,
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        let dimension = self.hierarchy.levels()[0].graph().vertex_count();
        if rhs.len() != dimension {
            return Err(CmgError::dimension(
                "ParallelCmgPlan::apply compatible rhs",
                dimension,
                rhs.len(),
            ));
        }
        if output.len() != dimension {
            return Err(CmgError::dimension(
                "ParallelCmgPlan::apply compatible output",
                dimension,
                output.len(),
            ));
        }
        workspace.validate(
            &self.hierarchy,
            self.direct_terminal.as_ref(),
            &self.finest_components,
            &self.coarse_centering,
        )?;
        validation.validate()?;
        self.apply_level_with_plan(0, rhs, output, workspace, 1, plan, executor)
    }

'''
preconditioner = preconditioner[:apply_start] + apply_methods + preconditioner[apply_end:]
preconditioner_path.write_text(preconditioner)

# Add a separate parallel-plan PCG API by copying the already-qualified serial
# control flow and changing only operator/preconditioner dispatch.
pcg_path = Path("src/pcg.rs")
pcg = pcg_path.read_text()
pcg = replace_once(
    pcg,
    "use crate::{ParallelExecutor, ParallelOptions};\n",
    "use crate::{ParallelCmgPlan, ParallelExecutor, ParallelOptions};\n",
    "parallel PCG imports",
)
serial_start = pcg.index("pub fn solve_pcg_with_workspace(\n")
serial_end = pcg.index("/// Solve multiple right-hand sides sequentially", serial_start)
serial_function = pcg[serial_start:serial_end]
parallel_function = serial_function
parallel_function = replace_once(
    parallel_function,
    '''pub fn solve_pcg_with_workspace(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    rhs: &[f64],
    options: PcgOptions,
    workspace: &mut PcgWorkspace,
) -> Result<PcgResult, CmgError> {
''',
    '''pub fn solve_pcg_with_plan_and_workspace(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    plan: &ParallelCmgPlan,
    rhs: &[f64],
    options: PcgOptions,
    workspace: &mut PcgWorkspace,
    executor: &ParallelExecutor,
) -> Result<PcgResult, CmgError> {
''',
    "parallel PCG function signature",
)
parallel_function = replace_once(
    parallel_function,
    "    workspace.validate(dimension)?;\n\n",
    "    workspace.validate(dimension)?;\n    plan.validate(preconditioner)?;\n\n",
    "parallel PCG one-time plan validation",
)
first_apply = '''    preconditioner.apply_compatible_into_with_validation(
        &workspace.residual,
        &mut workspace.preconditioned,
        &mut workspace.cmg,
        options.validation,
    )?;
'''
first_apply_replacement = '''    plan.apply_compatible_into_prevalidated(
        preconditioner,
        &workspace.residual,
        &mut workspace.preconditioned,
        &mut workspace.cmg,
        options.validation,
        executor,
    )?;
'''
parallel_function = replace_once(
    parallel_function,
    first_apply,
    first_apply_replacement,
    "parallel PCG initial preconditioner application",
)
next_apply = '''        preconditioner.apply_compatible_into_with_validation(
            &workspace.residual,
            &mut workspace.preconditioned,
            &mut workspace.cmg,
            options.validation,
        )?;
'''
next_apply_replacement = '''        plan.apply_compatible_into_prevalidated(
            preconditioner,
            &workspace.residual,
            &mut workspace.preconditioned,
            &mut workspace.cmg,
            options.validation,
            executor,
        )?;
'''
parallel_function = replace_once(
    parallel_function,
    next_apply,
    next_apply_replacement,
    "parallel PCG iterative preconditioner application",
)
parallel_function = replace_once(
    parallel_function,
    "        graph.matvec_into(&workspace.direction, &mut workspace.matrix_direction)?;\n",
    "        plan.finest_matvec_into(\n            graph,\n            &workspace.direction,\n            &mut workspace.matrix_direction,\n            executor,\n        )?;\n",
    "parallel PCG direction matvec",
)
parallel_function = parallel_function.replace(
    "recompute_residual(", "recompute_residual_with_plan(plan, executor, "
)
convenience = '''/// Solve with a prebuilt optional parallel CMG plan and a newly allocated workspace.
#[cfg(feature = "parallel")]
pub fn solve_pcg_with_plan(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    plan: &ParallelCmgPlan,
    rhs: &[f64],
    options: PcgOptions,
    executor: &ParallelExecutor,
) -> Result<PcgResult, CmgError> {
    let mut workspace = PcgWorkspace::new(preconditioner);
    solve_pcg_with_plan_and_workspace(
        graph,
        preconditioner,
        plan,
        rhs,
        options,
        &mut workspace,
        executor,
    )
}

/// Solve with a prebuilt optional parallel CMG plan and caller-owned workspace.
///
/// The submitted right-hand side is projected and the final residual is
/// certified against the original system exactly as in [`solve_pcg_with_workspace`].
#[cfg(feature = "parallel")]
'''
pcg = pcg[:serial_end] + convenience + parallel_function + pcg[serial_end:]
helper_anchor = "fn recompute_residual(\n"
helper = '''#[cfg(feature = "parallel")]
fn recompute_residual_with_plan(
    plan: &ParallelCmgPlan,
    executor: &ParallelExecutor,
    graph: &Laplacian,
    rhs: &[f64],
    solution: &[f64],
    residual: &mut [f64],
) -> Result<f64, CmgError> {
    plan.finest_matvec_into(graph, solution, residual, executor)?;
    for (value, rhs_value) in residual.iter_mut().zip(rhs) {
        *value = *rhs_value - *value;
    }
    Ok(euclidean_norm(residual))
}

'''
pcg = replace_once(pcg, helper_anchor, helper + helper_anchor, "parallel residual helper")
pcg_path.write_text(pcg)

lib_path = Path("src/lib.rs")
lib = lib_path.read_text()
lib = replace_once(
    lib,
    '''#[cfg(feature = "parallel")]
pub use pcg::{solve_pcg_batch_parallel, solve_pcg_batch_with_executor};
''',
    '''#[cfg(feature = "parallel")]
pub use pcg::{
    solve_pcg_batch_parallel, solve_pcg_batch_with_executor, solve_pcg_with_plan,
    solve_pcg_with_plan_and_workspace,
};
''',
    "parallel PCG exports",
)
lib_path.write_text(lib)

# Extend the permanent parallel tests.
tests_path = Path("tests/parallel.rs")
tests = tests_path.read_text()
tests = replace_once(
    tests,
    '''    maximum_weight_forest_with_executor, solve_pcg_batch, solve_pcg_batch_with_executor,
''',
    '''    maximum_weight_forest_with_executor, solve_pcg_batch, solve_pcg_batch_with_executor,
    solve_pcg_with_plan_and_workspace, solve_pcg_with_workspace,
''',
    "parallel PCG test imports",
)
appendix = r'''

#[test]
fn planned_parallel_pcg_matches_serial_certification() {
    let (graph, rhs) = worker_firm_problem(20_000);
    let cmg_options = CmgOptions {
        direct_threshold: 64,
        ..CmgOptions::default()
    };
    let preconditioner = CmgPreconditioner::build(&graph, cmg_options).unwrap();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 4,
        min_parallel_len: 1,
        ..ParallelOptions::default()
    })
    .unwrap();
    let plan = ParallelCmgPlan::build(&preconditioner, &executor).unwrap();
    assert!(plan.operator_count() > 0);

    let mut serial_workspace = PcgWorkspace::new(&preconditioner);
    let serial = solve_pcg_with_workspace(
        &graph,
        &preconditioner,
        &rhs,
        PcgOptions::default(),
        &mut serial_workspace,
    )
    .unwrap();
    let mut parallel_workspace = PcgWorkspace::new(&preconditioner);
    let parallel = solve_pcg_with_plan_and_workspace(
        &graph,
        &preconditioner,
        &plan,
        &rhs,
        PcgOptions::default(),
        &mut parallel_workspace,
        &executor,
    )
    .unwrap();

    assert_eq!(serial.iterations(), parallel.iterations());
    assert_eq!(serial.restarts(), parallel.restarts());
    assert!(parallel.backward_error() <= parallel.tolerance());
    for (serial_value, parallel_value) in serial.solution().iter().zip(parallel.solution()) {
        let scale = 1.0_f64.max(serial_value.abs()).max(parallel_value.abs());
        assert!((serial_value - parallel_value).abs() <= 5.0e-10 * scale);
    }
}

#[test]
fn one_thread_planned_pcg_is_bitwise_serial() {
    let (graph, right_hand_sides) = path_problem(4_000, 1);
    let cmg_options = CmgOptions {
        direct_threshold: 64,
        ..CmgOptions::default()
    };
    let preconditioner = CmgPreconditioner::build(&graph, cmg_options).unwrap();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 1,
        min_parallel_len: 1,
        ..ParallelOptions::default()
    })
    .unwrap();
    let plan = ParallelCmgPlan::build(&preconditioner, &executor).unwrap();
    assert_eq!(plan.operator_count(), 0);

    let rhs = &right_hand_sides[0];
    let mut serial_workspace = PcgWorkspace::new(&preconditioner);
    let serial = solve_pcg_with_workspace(
        &graph,
        &preconditioner,
        rhs,
        PcgOptions::default(),
        &mut serial_workspace,
    )
    .unwrap();
    let mut planned_workspace = PcgWorkspace::new(&preconditioner);
    let planned = solve_pcg_with_plan_and_workspace(
        &graph,
        &preconditioner,
        &plan,
        rhs,
        PcgOptions::default(),
        &mut planned_workspace,
        &executor,
    )
    .unwrap();

    assert_eq!(serial, planned);
}
'''
if "fn planned_parallel_pcg_matches_serial_certification" in tests:
    raise SystemExit("parallel PCG tests already exist")
tests_path.write_text(tests + appendix)

# Add a stable end-to-end benchmark target.
benchmark_path = Path("benchmarks/src/bin/parallel-pcg-solve.rs")
if benchmark_path.exists():
    raise SystemExit("parallel PCG benchmark already exists")
benchmark_path.write_text(r'''use std::hint::black_box;
use std::time::Instant;

use cmg::{
    CmgOptions, CmgPreconditioner, Laplacian, ParallelCmgPlan, ParallelExecutor,
    ParallelOptions, PcgOptions, PcgWorkspace, solve_pcg_with_plan_and_workspace,
    solve_pcg_with_workspace,
};

struct BenchGraph {
    graph: Laplacian,
    vertices: usize,
    edges: usize,
}

fn median(mut values: Vec<u128>) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn path_graph(vertices: usize) -> BenchGraph {
    let edges: Vec<_> = (0..vertices.saturating_sub(1))
        .map(|vertex| (vertex, vertex + 1, 0.5 + (vertex % 31) as f64 / 17.0))
        .collect();
    let edge_count = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).expect("valid path graph"),
        vertices,
        edges: edge_count,
    }
}

fn worker_firm_graph(per_side: usize, degree: usize) -> BenchGraph {
    assert!(degree >= 2);
    let vertices = 2 * per_side;
    let firm_offset = per_side;
    let mut edges = Vec::with_capacity(degree * per_side);
    for worker in 0..per_side {
        for link in 0..degree {
            let firm = if link == 0 {
                worker
            } else if link == 1 {
                (worker + 1) % per_side
            } else {
                ((2 * link + 1) * worker + 17 * link + 3) % per_side
            };
            let weight = 0.25 + ((worker + 7 * link) % 23) as f64 / 16.0;
            edges.push((worker, firm_offset + firm, weight));
        }
    }
    let edge_count = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).expect("valid worker-firm graph"),
        vertices,
        edges: edge_count,
    }
}

fn build_case(case: &str, scale: usize) -> BenchGraph {
    match case {
        "path" => path_graph(scale),
        "worker-firm" => worker_firm_graph(scale, 3),
        "dense-worker-firm" => worker_firm_graph(scale, 16),
        _ => panic!("unknown case {case}; expected path, worker-firm, or dense-worker-firm"),
    }
}

fn compatible_rhs(graph: &Laplacian) -> Vec<f64> {
    let mut target: Vec<f64> = (0..graph.vertex_count())
        .map(|vertex| {
            let centered = (vertex % 257) as f64 - 128.0;
            centered / 37.0 + ((vertex * 17) % 19) as f64 / 101.0
        })
        .collect();
    let mean = target.iter().sum::<f64>() / target.len().max(1) as f64;
    for value in &mut target {
        *value -= mean;
    }
    graph.matvec(&target).expect("known compatible rhs")
}

fn max_scaled_difference(left: &[f64], right: &[f64]) -> f64 {
    left.iter()
        .zip(right)
        .map(|(left, right)| {
            let scale = 1.0_f64.max(left.abs()).max(right.abs());
            (left - right).abs() / scale
        })
        .fold(0.0, f64::max)
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().unwrap_or_else(|| "worker-firm".to_owned());
    let scale = arguments
        .next()
        .map(|argument| argument.parse::<usize>().expect("scale must be an integer"))
        .unwrap_or(100_000);
    let repetitions = arguments
        .next()
        .map(|argument| argument.parse::<usize>().expect("repetitions must be an integer"))
        .unwrap_or(3)
        .max(1);
    let threads = arguments
        .next()
        .map(|argument| argument.parse::<usize>().expect("threads must be an integer"))
        .unwrap_or(4)
        .max(1);

    let bench_graph = build_case(&case, scale);
    let rhs = compatible_rhs(&bench_graph.graph);
    let preconditioner = CmgPreconditioner::build(
        &bench_graph.graph,
        CmgOptions {
            direct_threshold: 64,
            ..CmgOptions::default()
        },
    )
    .expect("CMG preconditioner should build");
    let executor = ParallelExecutor::new(ParallelOptions {
        threads,
        min_parallel_len: 16_384,
        ..ParallelOptions::default()
    })
    .expect("parallel executor should build");
    let plan_start = Instant::now();
    let plan = ParallelCmgPlan::build(&preconditioner, &executor)
        .expect("parallel CMG plan should build");
    let plan_build_ns = plan_start.elapsed().as_nanos();
    let options = PcgOptions::default();
    let mut serial_workspace = PcgWorkspace::new(&preconditioner);
    let mut parallel_workspace = PcgWorkspace::new(&preconditioner);

    let serial_warm = solve_pcg_with_workspace(
        &bench_graph.graph,
        &preconditioner,
        &rhs,
        options,
        &mut serial_workspace,
    )
    .expect("serial warm-up should converge");
    let parallel_warm = solve_pcg_with_plan_and_workspace(
        &bench_graph.graph,
        &preconditioner,
        &plan,
        &rhs,
        options,
        &mut parallel_workspace,
        &executor,
    )
    .expect("planned warm-up should converge");
    assert_eq!(serial_warm.iterations(), parallel_warm.iterations());
    let max_difference =
        max_scaled_difference(serial_warm.solution(), parallel_warm.solution());
    assert!(
        max_difference <= 5.0e-9,
        "parallel PCG changed solution by {max_difference}"
    );

    let mut serial_times = Vec::with_capacity(repetitions);
    let mut parallel_times = Vec::with_capacity(repetitions);
    let mut serial_result = serial_warm;
    let mut parallel_result = parallel_warm;
    for repetition in 0..repetitions {
        if repetition % 2 == 0 {
            let start = Instant::now();
            serial_result = solve_pcg_with_workspace(
                &bench_graph.graph,
                &preconditioner,
                black_box(&rhs),
                options,
                &mut serial_workspace,
            )
            .expect("serial solve should converge");
            serial_times.push(start.elapsed().as_nanos());
            black_box(&serial_result);

            let start = Instant::now();
            parallel_result = solve_pcg_with_plan_and_workspace(
                &bench_graph.graph,
                &preconditioner,
                &plan,
                black_box(&rhs),
                options,
                &mut parallel_workspace,
                &executor,
            )
            .expect("parallel solve should converge");
            parallel_times.push(start.elapsed().as_nanos());
            black_box(&parallel_result);
        } else {
            let start = Instant::now();
            parallel_result = solve_pcg_with_plan_and_workspace(
                &bench_graph.graph,
                &preconditioner,
                &plan,
                black_box(&rhs),
                options,
                &mut parallel_workspace,
                &executor,
            )
            .expect("parallel solve should converge");
            parallel_times.push(start.elapsed().as_nanos());
            black_box(&parallel_result);

            let start = Instant::now();
            serial_result = solve_pcg_with_workspace(
                &bench_graph.graph,
                &preconditioner,
                black_box(&rhs),
                options,
                &mut serial_workspace,
            )
            .expect("serial solve should converge");
            serial_times.push(start.elapsed().as_nanos());
            black_box(&serial_result);
        }
    }

    let serial_ns = median(serial_times);
    let parallel_ns = median(parallel_times);
    let max_difference =
        max_scaled_difference(serial_result.solution(), parallel_result.solution());
    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"vertices\":{},\"edges\":{},\"levels\":{},\"threads\":{},\"operators\":{},\"serial_median_ns\":{serial_ns},\"parallel_median_ns\":{parallel_ns},\"speedup\":{:.17e},\"serial_iterations\":{},\"parallel_iterations\":{},\"serial_backward_error\":{:.17e},\"parallel_backward_error\":{:.17e},\"serial_residual_norm\":{:.17e},\"parallel_residual_norm\":{:.17e},\"max_scaled_difference\":{max_difference:.17e},\"plan_build_ns\":{plan_build_ns},\"plan_bytes\":{},\"workspace_bytes\":{}}}",
        bench_graph.vertices,
        bench_graph.edges,
        preconditioner.hierarchy().levels().len(),
        executor.thread_count(),
        plan.operator_count(),
        serial_ns as f64 / parallel_ns as f64,
        serial_result.iterations(),
        parallel_result.iterations(),
        serial_result.backward_error(),
        parallel_result.backward_error(),
        serial_result.residual_norm(),
        parallel_result.residual_norm(),
        plan.byte_len(),
        serial_workspace.byte_len(),
    );
}
''')
