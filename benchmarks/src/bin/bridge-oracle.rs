//! Bounded diagnostic of the frozen analytic weak-bridge accuracy gate.
//! Compile identical bytes against exact baseline/candidate source snapshots.
use cmg::*;
use std::io::Write;
use std::process::ExitCode;

fn main() -> ExitCode {
    const N: usize = 64;
    const BRIDGE_RESISTANCE: f64 = 16_777_216.0;
    const FORWARD_RELATIVE_LIMIT: f64 = 1e-6;
    const RESIDUAL_LIMIT: f64 = 1e-8 * std::f64::consts::SQRT_2;
    let graph = Laplacian::from_edges(
        N,
        (1..N).map(|v| {
            (
                v - 1,
                v,
                if v == 32 {
                    1.0 / BRIDGE_RESISTANCE
                } else {
                    1.0
                },
            )
        }),
    )
    .unwrap();
    let mut reference = [0.0; N];
    for v in (1..N).rev() {
        reference[v - 1] = reference[v] + if v == 32 { BRIDGE_RESISTANCE } else { 1.0 };
    }
    let mean = reference.iter().sum::<f64>() / N as f64;
    for value in &mut reference {
        *value -= mean;
    }
    let scale = reference.iter().map(|x| x.abs()).fold(0.0, f64::max);
    let mut rhs = [0.0; N];
    rhs[0] = 1.0;
    rhs[N - 1] = -1.0;
    let mut input = std::fs::File::create_new(std::env::args().nth(1).unwrap()).unwrap();
    input.write_all(&(N as u64).to_le_bytes()).unwrap();
    for edge in graph.edges() {
        for bits in [edge.u() as u64, edge.v() as u64, edge.weight().to_bits()] {
            input.write_all(&bits.to_le_bytes()).unwrap();
        }
    }
    for value in rhs
        .iter()
        .chain(&reference)
        .chain(&[FORWARD_RELATIVE_LIMIT, RESIDUAL_LIMIT])
    {
        input.write_all(&value.to_bits().to_le_bytes()).unwrap();
    }
    drop(input);
    let mut passed_all = true;
    for threads in [1, 4] {
        let executor = ParallelExecutor::new(ParallelOptions {
            threads,
            min_parallel_len: 1,
            ..Default::default()
        })
        .unwrap();
        for direct_threshold in [8, N + 1] {
            for component in [false, true] {
                let options = CmgOptions {
                    direct_threshold,
                    ..Default::default()
                };
                let pre = if component {
                    let component = ComponentBuildOptions {
                        prune_coarse_isolates: true,
                        preserve_unpruned_stopping: true,
                        factor_terminal_components: true,
                    };
                    #[cfg(feature = "candidate-screen")]
                    {
                        CmgPreconditioner::build_component_experiment_with_executor(
                            &graph, options, component, &executor,
                        )
                    }
                    #[cfg(not(feature = "candidate-screen"))]
                    {
                        CmgPreconditioner::build_component_experiment(&graph, options, component)
                    }
                } else {
                    CmgPreconditioner::build_with_executor(&graph, options, &executor)
                }
                .unwrap();
                let plan = ParallelCmgPlan::build(&pre, &executor).unwrap();
                let mut workspace = PcgWorkspace::try_new(&pre).unwrap();
                let mut solution = [0.0; N];
                let diagnostics = solve_pcg_with_plan_into_with_workspace(
                    &graph,
                    &pre,
                    &plan,
                    &rhs,
                    None,
                    &mut solution,
                    PcgOptions::default(),
                    &mut workspace,
                    &executor,
                );
                let forward = solution
                    .iter()
                    .zip(reference)
                    .map(|(x, r)| (x - r).abs())
                    .fold(0.0, f64::max)
                    / scale;
                let mut residual = rhs;
                for v in 1..N {
                    let resistance = if v == 32 { BRIDGE_RESISTANCE } else { 1.0 };
                    let current = (solution[v - 1] - solution[v]) / resistance;
                    residual[v - 1] -= current;
                    residual[v] += current;
                }
                let residual = residual.iter().map(|r| r * r).sum::<f64>().sqrt();
                let hash = solution.iter().fold(0xcbf29ce484222325u64, |h, x| {
                    (h ^ x.to_bits()).wrapping_mul(0x100000001b3)
                });
                let (certified, native_residual, tolerance, iterations) = match diagnostics {
                    Ok(d) => (
                        d.residual_norm() <= d.tolerance(),
                        d.residual_norm(),
                        d.tolerance(),
                        d.iterations(),
                    ),
                    Err(error) => {
                        eprintln!(
                            "threads={threads} direct={direct_threshold} component={component}: {error:?}"
                        );
                        (false, -1.0, -1.0, 0)
                    }
                };
                let passed = certified
                    && forward.is_finite()
                    && forward <= FORWARD_RELATIVE_LIMIT
                    && residual.is_finite()
                    && residual <= RESIDUAL_LIMIT;
                passed_all &= passed;
                println!(
                    "{{\"threads\":{threads},\"direct_threshold\":{direct_threshold},\"component\":{component},\"certified\":{certified},\"native_residual\":{native_residual:e},\"native_tolerance\":{tolerance:e},\"iterations\":{iterations},\"independent_residual\":{residual:e},\"forward_relative_error\":{forward:e},\"forward_limit\":{FORWARD_RELATIVE_LIMIT:e},\"residual_limit\":{RESIDUAL_LIMIT:e},\"solution_fnv64\":\"{hash:016x}\",\"passed\":{passed}}}"
                );
            }
        }
    }
    if passed_all {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(2)
    }
}
