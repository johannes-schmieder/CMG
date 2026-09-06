//! Exclusive recursive-CMG attribution on the certified component fixtures.

#[path = "../component_fixtures.rs"]
mod fixtures;

use cmg::{
    CmgApplyPhase, CmgOptions, CmgPreconditioner, Components, ParallelCmgPlan, ParallelExecutor,
    ParallelOptions, PcgOptions, PcgWorkspace, profile_pcg_with_plan,
    solve_pcg_with_plan_and_workspace,
};

fn main() {
    if let Err(error) = run() {
        eprintln!("component cycle profile failed: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    assert!(
        (4..=5).contains(&args.len()),
        "repetitions RHS-count suite seed [case-substring]"
    );
    let repetitions = args[0].parse::<usize>()?;
    let rhs_count = args[1].parse::<usize>()?;
    let seed = args[3].parse::<u64>()?;
    assert!(repetitions > 0 && rhs_count > 0);
    let cases = match args[2].as_str() {
        "sentinels" => fixtures::sentinels(),
        "stress" => fixtures::stress(seed),
        "large" => fixtures::large(seed),
        _ => panic!("unknown suite"),
    };
    println!(
        "{{\"type\":\"environment\",\"source\":\"{}\",\"suite\":\"{}\",\"seed\":{seed},\"repetitions\":{repetitions},\"rhs_count\":{rhs_count},\"warmups\":2,\"arch\":\"{}\"}}",
        option_env!("CMG_BENCH_COMMIT").unwrap_or("unrecorded"),
        args[2],
        std::env::consts::ARCH
    );
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 1,
        ..ParallelOptions::default()
    })?;
    let mut selected = 0;
    for case in cases
        .into_iter()
        .filter(|c| args.get(4).is_none_or(|f| c.name.contains(f)))
    {
        selected += 1;
        #[cfg(feature = "experimental-components")]
        let pre = CmgPreconditioner::build_component_experiment(
            &case.graph,
            CmgOptions::default(),
            cmg::ComponentBuildOptions {
                prune_coarse_isolates: true,
                preserve_unpruned_stopping: true,
                factor_terminal_components: true,
            },
        )?;
        #[cfg(not(feature = "experimental-components"))]
        let pre = CmgPreconditioner::build(&case.graph, CmgOptions::default())?;
        let plan = ParallelCmgPlan::build(&pre, &executor)?;
        let components = Components::from_laplacian(&case.graph);
        let mut workspace = PcgWorkspace::new(&pre);
        for column in 0..rhs_count {
            let mut target: Vec<_> = (0..case.graph.vertex_count())
                .map(|v| ((v * 7 + column * 11) % 31) as f64 / 16.0)
                .collect();
            components.center_in_place(&mut target)?;
            let rhs = case.graph.matvec(&target)?;
            let reference = solve_pcg_with_plan_and_workspace(
                &case.graph,
                &pre,
                &plan,
                &rhs,
                PcgOptions::default(),
                &mut workspace,
                &executor,
            )?;
            for round in 0..repetitions + 2 {
                let result = profile_pcg_with_plan(
                    &case.graph,
                    &pre,
                    &plan,
                    &rhs,
                    PcgOptions::default(),
                    &executor,
                )?;
                assert!(
                    result
                        .solution()
                        .iter()
                        .zip(reference.solution())
                        .all(|(a, b)| a.to_bits() == b.to_bits())
                );
                assert_eq!(result.iterations(), reference.iterations());
                assert_eq!(
                    result.residual_norm().to_bits(),
                    reference.residual_norm().to_bits()
                );
                assert_eq!(
                    result.tolerance().to_bits(),
                    reference.tolerance().to_bits()
                );
                assert_eq!(result.restarts(), reference.restarts());
                let ax = case.graph.matvec(result.solution())?;
                let residual = rhs
                    .iter()
                    .zip(ax)
                    .map(|(b, a)| (b - a).powi(2))
                    .sum::<f64>()
                    .sqrt();
                assert!(residual.is_finite() && residual <= result.tolerance());
                let profile = result.profile();
                assert!(
                    profile.cycle().attributed_nanoseconds()
                        <= profile.preconditioner().nanoseconds()
                );
                if round < 2 {
                    continue;
                }
                println!(
                    "{{\"type\":\"cycle\",\"case\":\"{}\",\"round\":{},\"rhs_column\":{column},\"iterations\":{},\"residual\":{residual},\"tolerance\":{},\"total_solve_ns\":{},\"cmg_ns\":{},\"attributed_ns\":{},\"bitwise_equal\":true}}",
                    case.name,
                    round - 2,
                    result.iterations(),
                    result.tolerance(),
                    profile.total_nanoseconds(),
                    profile.preconditioner().nanoseconds(),
                    profile.cycle().attributed_nanoseconds()
                );
                for (level, (sample, hierarchy)) in profile
                    .cycle()
                    .levels()
                    .iter()
                    .zip(pre.hierarchy().levels())
                    .enumerate()
                {
                    println!(
                        "{{\"type\":\"cycle_level\",\"case\":\"{}\",\"round\":{},\"rhs_column\":{column},\"level\":{level},\"vertices\":{},\"terminal\":{},\"visits\":{},\"iterations\":{},\"phases\":{{{}}}}}",
                        case.name,
                        round - 2,
                        hierarchy.graph().vertex_count(),
                        hierarchy.is_terminal(),
                        sample.visits(),
                        sample.iterations(),
                        CmgApplyPhase::ALL
                            .iter()
                            .map(|&phase| format!(
                                "\"{}\":{{\"ns\":{},\"calls\":{}}}",
                                phase.name(),
                                sample.nanoseconds(phase),
                                sample.calls(phase)
                            ))
                            .collect::<Vec<_>>()
                            .join(",")
                    );
                }
            }
        }
    }
    assert!(selected > 0, "case filter matched no fixtures");
    Ok(())
}
