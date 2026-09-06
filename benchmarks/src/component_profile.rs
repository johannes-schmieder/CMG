//! Phase attribution on the same component fixtures, separately from timing runs.

use std::time::Instant;

use cmg::{
    Components, ParallelCmgPlan, ParallelExecutor, ParallelOptions, PcgOptions, PcgWorkspace,
    profile_pcg_with_plan, solve_pcg_with_plan_and_workspace, solve_pcg_with_workspace,
};

use super::{Case, Failure, build, json_string, names, solution_bit_hash};

pub(crate) fn run(
    case: &Case,
    rhs: &[Vec<f64>],
    route: usize,
    repetitions: usize,
) -> Result<(), Failure> {
    let start = Instant::now();
    let pre = build(&case.graph, route).map_err(|e| Failure::new("profile-build", e))?;
    let build_ns = start.elapsed().as_nanos();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 1,
        ..ParallelOptions::default()
    })
    .map_err(|e| Failure::new("profile-executor", e))?;
    let plan =
        ParallelCmgPlan::build(&pre, &executor).map_err(|e| Failure::new("profile-plan", e))?;
    assert_eq!(plan.operator_count(), 0, "one-thread serial operator path");
    if let Some(factor) = pre.terminal_factor() {
        let terminal = pre.hierarchy().levels().last().unwrap().graph();
        let components = Components::from_laplacian(terminal);
        let scan_slots: u128 = components
            .sizes()
            .iter()
            .map(|&size| {
                let grounded = size.saturating_sub(1) as u128;
                grounded * grounded.saturating_sub(1) / 2
            })
            .sum();
        println!(
            "{{\"type\":\"profile_structure\",\"case\":{},\"route\":{},\"build_ns\":{build_ns},\"terminal_vertices\":{},\"factor_dimension\":{},\"factor_strict_nonzeros\":{},\"component_factor_scan_slots\":{scan_slots}}}",
            json_string(case.name),
            json_string(names(route)),
            terminal.vertex_count(),
            factor.active_dimension(),
            factor.factor_nonzeros() - factor.active_dimension(),
        );
    }
    let mut workspace = PcgWorkspace::new(&pre);
    for (column, b) in rhs.iter().enumerate() {
        let scalar =
            solve_pcg_with_workspace(&case.graph, &pre, b, PcgOptions::default(), &mut workspace)
                .map_err(|e| Failure::new("profile-scalar-reference", e))?;
        let planned = solve_pcg_with_plan_and_workspace(
            &case.graph,
            &pre,
            &plan,
            b,
            PcgOptions::default(),
            &mut workspace,
            &executor,
        )
        .map_err(|e| Failure::new("profile-planned-reference", e))?;
        if scalar != planned || !same_bits(scalar.solution(), planned.solution()) {
            return Err(Failure {
                stage: "profile-serial-plan-mismatch",
                error: format!("RHS {column}: scalar and one-thread planned results differ"),
            });
        }
        for round in 0..repetitions + 2 {
            let result = profile_pcg_with_plan(
                &case.graph,
                &pre,
                &plan,
                b,
                PcgOptions::default(),
                &executor,
            )
            .map_err(|e| Failure::new("profile-solve", e))?;
            let diagnostics_match = scalar.iterations() == result.iterations()
                && scalar.restarts() == result.restarts()
                && same_bits(
                    &[
                        scalar.initial_residual_norm(),
                        scalar.residual_norm(),
                        scalar.relative_residual(),
                        scalar.backward_error(),
                        scalar.tolerance(),
                        scalar.rhs_projection_norm(),
                    ],
                    &[
                        result.initial_residual_norm(),
                        result.residual_norm(),
                        result.relative_residual(),
                        result.backward_error(),
                        result.tolerance(),
                        result.rhs_projection_norm(),
                    ],
                );
            if !diagnostics_match || !same_bits(scalar.solution(), result.solution()) {
                return Err(Failure {
                    stage: "profile-arithmetic-mismatch",
                    error: format!("RHS {column}, round {round}: profiled result differs"),
                });
            }
            let ax = case.graph.matvec(result.solution()).unwrap();
            let fresh = b
                .iter()
                .zip(ax)
                .map(|(b, ax)| (b - ax).powi(2))
                .sum::<f64>()
                .sqrt();
            if !fresh.is_finite() || fresh > result.tolerance() {
                return Err(Failure {
                    stage: "profile-independent-residual",
                    error: format!("{fresh} > {}", result.tolerance()),
                });
            }
            if round < 2 {
                continue;
            }
            let profile = result.profile();
            let phases = [
                ("setup", profile.setup()),
                ("cmg", profile.preconditioner()),
                ("matvec", profile.matvec()),
                ("dot_products", profile.dot_products()),
                ("vector_updates", profile.vector_updates()),
                ("centering", profile.centering()),
                ("norms", profile.norms()),
                ("residual_recompute", profile.residual_recompute()),
                ("certification", profile.certification()),
            ];
            let phases = phases
                .iter()
                .map(|(name, sample)| {
                    format!(
                        "{}:{{\"ns\":{},\"calls\":{}}}",
                        json_string(name),
                        sample.nanoseconds(),
                        sample.calls()
                    )
                })
                .collect::<Vec<_>>()
                .join(",");
            println!(
                "{{\"type\":\"profile\",\"case\":{},\"route\":{},\"round\":{},\"rhs_column\":{column},\"total_ns\":{},\"unattributed_ns\":{},\"phases\":{{{phases}}},\"iterations\":{},\"residual\":{},\"tolerance\":{},\"solution_bit_hash\":{},\"bitwise_equal_to_scalar\":true}}",
                json_string(case.name),
                json_string(names(route)),
                round - 2,
                profile.total_nanoseconds(),
                profile.unattributed_nanoseconds(),
                result.iterations(),
                result.residual_norm(),
                result.tolerance(),
                json_string(&solution_bit_hash(result.solution())),
            );
        }
    }
    Ok(())
}

fn same_bits(left: &[f64], right: &[f64]) -> bool {
    left.len() == right.len()
        && left
            .iter()
            .zip(right)
            .all(|(a, b)| a.to_bits() == b.to_bits())
}
