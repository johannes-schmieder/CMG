use cmg::experimental::{
    FusedPcgBatchPhaseProfile, FusedPcgPhaseSample, FusedPcgWorkspace4,
    profile_pcg_batch_fused_width4_into_with_workspace,
    solve_pcg_batch_fused_width4_into_with_workspace,
};
use cmg::{
    CmgOptions, CmgPreconditioner, Laplacian, PcgBatchMut, PcgBatchRef, PcgBatchWorkspace,
    PcgDiagnostics, PcgOptions, profile_pcg_batch_into_with_workspace,
    solve_pcg_batch_into_with_workspace,
};
use std::error::Error;
use std::hint::black_box;
use std::time::Instant;

type AnyError = Box<dyn Error>;

fn main() -> Result<(), AnyError> {
    let mut args = std::env::args().skip(1);
    let family = args.next().unwrap_or_else(|| "worker-firm".to_owned());
    let vertices = parse(args.next(), 100_000, "vertices")?;
    let rhs_count = parse(args.next(), 16, "rhs-count")?;
    let mode = args.next().unwrap_or_else(|| "homogeneous".to_owned());
    let warmups = parse(args.next(), 2, "warmups")?;
    let repetitions = parse(args.next(), 7, "repetitions")?;
    let output_path = args.next();
    if args.next().is_some() || !matches!(mode.as_str(), "homogeneous" | "mixed") {
        return Err("usage: fused-rhs-experiment FAMILY VERTICES RHS_COUNT homogeneous|mixed WARMUPS REPETITIONS [OUTPUT]".into());
    }

    let graph = build_graph(&family, vertices)?;
    let preconditioner = CmgPreconditioner::build(&graph, CmgOptions::default())?;
    let rhs = build_rhs(&graph, rhs_count, &mode)?;
    let options = PcgOptions {
        relative_tolerance: 1.0e-8,
        max_iterations: 1_000,
        ..PcgOptions::default()
    };
    let mut scalar_workspace = PcgBatchWorkspace::new(&preconditioner)?;
    let mut fused_workspace = FusedPcgWorkspace4::try_new(&preconditioner)?;
    let mut scalar_output = vec![0.0; rhs.len()];
    let mut fused_output = vec![0.0; rhs.len()];
    let mut scalar_diagnostics = vec![PcgDiagnostics::default(); rhs_count];
    let mut fused_diagnostics = vec![PcgDiagnostics::default(); rhs_count];

    run_scalar(
        &graph,
        &preconditioner,
        &rhs,
        &mut scalar_output,
        &mut scalar_diagnostics,
        options,
        &mut scalar_workspace,
    )?;
    run_fused(
        &graph,
        &preconditioner,
        &rhs,
        &mut fused_output,
        &mut fused_diagnostics,
        options,
        &mut fused_workspace,
    )?;
    assert_identical(
        &scalar_output,
        &fused_output,
        &scalar_diagnostics,
        &fused_diagnostics,
    )?;

    let mut scalar_ns = Vec::with_capacity(repetitions);
    let mut fused_ns = Vec::with_capacity(repetitions);
    for sequence in 0..warmups + repetitions {
        let scalar_first = sequence % 2 == 0;
        let mut scalar_elapsed = 0;
        let mut fused_elapsed = 0;
        for fused in [!scalar_first, scalar_first] {
            if fused {
                let start = Instant::now();
                run_fused(
                    &graph,
                    &preconditioner,
                    black_box(&rhs),
                    black_box(&mut fused_output),
                    black_box(&mut fused_diagnostics),
                    options,
                    black_box(&mut fused_workspace),
                )?;
                fused_elapsed = start.elapsed().as_nanos();
            } else {
                let start = Instant::now();
                run_scalar(
                    &graph,
                    &preconditioner,
                    black_box(&rhs),
                    black_box(&mut scalar_output),
                    black_box(&mut scalar_diagnostics),
                    options,
                    black_box(&mut scalar_workspace),
                )?;
                scalar_elapsed = start.elapsed().as_nanos();
            }
        }
        assert_identical(
            &scalar_output,
            &fused_output,
            &scalar_diagnostics,
            &fused_diagnostics,
        )?;
        if sequence >= warmups {
            scalar_ns.push(scalar_elapsed);
            fused_ns.push(fused_elapsed);
        }
    }

    let scalar_profile = profile_pcg_batch_into_with_workspace(
        &graph,
        &preconditioner,
        PcgBatchRef::contiguous(&rhs, rhs_count, vertices)?,
        None,
        PcgBatchMut::contiguous(&mut scalar_output, rhs_count, vertices)?,
        &mut scalar_diagnostics,
        options,
        &mut scalar_workspace,
    )?;
    let fused_profile = profile_pcg_batch_fused_width4_into_with_workspace(
        &graph,
        &preconditioner,
        PcgBatchRef::contiguous(&rhs, rhs_count, vertices)?,
        PcgBatchMut::contiguous(&mut fused_output, rhs_count, vertices)?,
        &mut fused_diagnostics,
        options,
        &mut fused_workspace,
    )?;
    assert_identical(
        &scalar_output,
        &fused_output,
        &scalar_diagnostics,
        &fused_diagnostics,
    )?;
    let scalar_median = median(&scalar_ns);
    let fused_median = median(&fused_ns);
    let (ratio_ci_low, ratio_ci_high) = paired_ratio_bootstrap(&scalar_ns, &fused_ns);
    let detailed_profile = detailed_profile_json(fused_profile, &fused_diagnostics)?;
    let payload = format!(
        "{{\"family\":\"{}\",\"vertices\":{},\"edges\":{},\"rhs_count\":{},\"mode\":\"{}\",\"warmups\":{},\"repetitions\":{},\"bitwise_identical\":true,\"scalar_ns\":{:?},\"fused_ns\":{:?},\"scalar_median_ns\":{},\"fused_median_ns\":{},\"fused_over_scalar\":{},\"paired_bootstrap_ratio_ci95\":[{},{}],\"speedup\":{},\"scalar_workspace_bytes\":{},\"fused_workspace_bytes\":{},\"scalar_profile\":{{\"validation_ns\":{},\"gather_ns\":{},\"solve_ns\":{},\"scatter_ns\":{},\"total_ns\":{}}},\"fused_profile\":{{\"validation_ns\":{},\"gather_ns\":{},\"solve_ns\":{},\"scatter_ns\":{},\"total_ns\":{}}},\"fused_detailed_profile\":{}}}",
        family,
        vertices,
        graph.edges().len(),
        rhs_count,
        mode,
        warmups,
        repetitions,
        scalar_ns,
        fused_ns,
        scalar_median,
        fused_median,
        fused_median as f64 / scalar_median as f64,
        ratio_ci_low,
        ratio_ci_high,
        scalar_median as f64 / fused_median as f64,
        scalar_workspace.byte_len(),
        fused_workspace.byte_len(),
        scalar_profile.validation_nanoseconds(),
        scalar_profile.gather_nanoseconds(),
        scalar_profile.solve_nanoseconds(),
        scalar_profile.scatter_nanoseconds(),
        scalar_profile.total_nanoseconds(),
        fused_profile.validation_nanoseconds(),
        fused_profile.gather_nanoseconds(),
        fused_profile.solve_nanoseconds(),
        fused_profile.scatter_nanoseconds(),
        fused_profile.total_nanoseconds(),
        detailed_profile,
    );
    println!("{payload}");
    if let Some(path) = output_path {
        std::fs::write(path, format!("{payload}\n"))?;
    }
    Ok(())
}

fn detailed_profile_json(
    profile: FusedPcgBatchPhaseProfile,
    diagnostics: &[PcgDiagnostics],
) -> Result<String, AnyError> {
    let iterations: Vec<usize> = diagnostics.iter().map(|item| item.iterations()).collect();
    let restarts: Vec<usize> = diagnostics.iter().map(|item| item.restarts()).collect();
    let submitted_rhs: usize = profile
        .groups_by_rhs_count()
        .iter()
        .enumerate()
        .map(|(lanes, groups)| lanes * groups)
        .sum();
    let kernel_ns = profile.preconditioner().nanoseconds()
        + profile.matvec().nanoseconds()
        + profile.residual_recompute().nanoseconds();
    if profile.active_lane_iterations() != iterations.iter().sum::<usize>()
        || profile.matvec().calls_by_active_lanes() != profile.iterations_by_active_lanes()
        || submitted_rhs != diagnostics.len()
        || kernel_ns > profile.solve_nanoseconds()
    {
        return Err("fused profile does not reconstruct diagnostics or solve timing".into());
    }
    let capacity = profile.lane_iteration_capacity();
    let occupancy = if capacity == 0 {
        "null".to_owned()
    } else {
        (profile.active_lane_iterations() as f64 / capacity as f64).to_string()
    };
    Ok(format!(
        "{{\"version\":\"cmg-fused-profile-v1\",\"groups_by_rhs_count\":{:?},\"iterations_by_active_lanes\":{:?},\"active_lane_iterations\":{},\"lane_iteration_capacity\":{},\"iteration_weighted_occupancy\":{},\"per_rhs_iterations\":{:?},\"per_rhs_restarts\":{:?},\"preconditioner\":{},\"matvec\":{},\"residual_recompute\":{},\"other_solve_ns\":{}}}",
        profile.groups_by_rhs_count(),
        profile.iterations_by_active_lanes(),
        profile.active_lane_iterations(),
        capacity,
        occupancy,
        iterations,
        restarts,
        phase_sample_json(profile.preconditioner()),
        phase_sample_json(profile.matvec()),
        phase_sample_json(profile.residual_recompute()),
        profile.other_solve_nanoseconds(),
    ))
}

fn phase_sample_json(sample: FusedPcgPhaseSample) -> String {
    format!(
        "{{\"ns_by_active_lanes\":{:?},\"calls_by_active_lanes\":{:?},\"total_ns\":{}}}",
        sample.nanoseconds_by_active_lanes(),
        sample.calls_by_active_lanes(),
        sample.nanoseconds(),
    )
}

fn parse(value: Option<String>, default: usize, name: &str) -> Result<usize, AnyError> {
    let value = value.map_or(Ok(default), |value| value.parse::<usize>())?;
    if value == 0 && name != "warmups" {
        return Err(format!("{name} must be positive").into());
    }
    Ok(value)
}

fn build_graph(family: &str, vertices: usize) -> Result<Laplacian, AnyError> {
    let edges = match family {
        "path" => (0..vertices.saturating_sub(1))
            .map(|vertex| (vertex, vertex + 1, deterministic_weight(vertex)))
            .collect(),
        "worker-firm" => worker_firm_edges(vertices, 3),
        "dense-worker-firm" => worker_firm_edges(vertices, 16),
        _ => return Err("family must be path, worker-firm, or dense-worker-firm".into()),
    };
    Ok(Laplacian::from_edges(vertices, edges)?)
}

fn worker_firm_edges(vertices: usize, degree: usize) -> Vec<(usize, usize, f64)> {
    let workers = vertices / 2;
    let firms = vertices - workers;
    let mut edges = Vec::with_capacity(workers * degree);
    for worker in 0..workers {
        for link in 0..degree {
            let firm = if link == 0 {
                worker % firms
            } else if link == 1 {
                (worker + 1) % firms
            } else {
                ((2 * link + 1) * worker + 17 * link + 3) % firms
            };
            let weight = 0.25 + ((worker + 7 * link) % 23) as f64 / 16.0;
            edges.push((worker, workers + firm, weight));
        }
    }
    edges
}

fn deterministic_weight(seed: usize) -> f64 {
    0.5 + (seed.wrapping_mul(1_103_515_245).wrapping_add(12_345) % 1_024) as f64 / 512.0
}

fn build_rhs(graph: &Laplacian, count: usize, mode: &str) -> Result<Vec<f64>, AnyError> {
    let mut packed = Vec::with_capacity(count * graph.vertex_count());
    let homogeneous: Vec<f64> = (0..graph.vertex_count())
        .map(|vertex| (vertex % 257) as f64 / 37.0 + ((vertex * 17) % 19) as f64 / 101.0)
        .collect();
    for rhs_index in 0..count {
        let target: Vec<f64> = if mode == "homogeneous" || rhs_index % 4 == 1 {
            homogeneous.clone()
        } else if rhs_index % 4 == 0 {
            vec![0.0; graph.vertex_count()]
        } else {
            (0..graph.vertex_count())
                .map(|vertex| {
                    let seed = vertex.wrapping_mul(97).wrapping_add(rhs_index * 7919);
                    (seed % 65_521) as f64 / 8192.0 - 4.0
                })
                .collect()
        };
        packed.extend(graph.matvec(&target)?);
    }
    Ok(packed)
}

#[allow(clippy::too_many_arguments)]
fn run_scalar(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    rhs: &[f64],
    output: &mut [f64],
    diagnostics: &mut [PcgDiagnostics],
    options: PcgOptions,
    workspace: &mut PcgBatchWorkspace,
) -> Result<(), AnyError> {
    solve_pcg_batch_into_with_workspace(
        graph,
        preconditioner,
        PcgBatchRef::contiguous(rhs, diagnostics.len(), graph.vertex_count())?,
        None,
        PcgBatchMut::contiguous(output, diagnostics.len(), graph.vertex_count())?,
        diagnostics,
        options,
        workspace,
    )?;
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn run_fused(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    rhs: &[f64],
    output: &mut [f64],
    diagnostics: &mut [PcgDiagnostics],
    options: PcgOptions,
    workspace: &mut FusedPcgWorkspace4,
) -> Result<(), AnyError> {
    solve_pcg_batch_fused_width4_into_with_workspace(
        graph,
        preconditioner,
        PcgBatchRef::contiguous(rhs, diagnostics.len(), graph.vertex_count())?,
        PcgBatchMut::contiguous(output, diagnostics.len(), graph.vertex_count())?,
        diagnostics,
        options,
        workspace,
    )?;
    Ok(())
}

fn assert_identical(
    scalar: &[f64],
    fused: &[f64],
    scalar_diagnostics: &[PcgDiagnostics],
    fused_diagnostics: &[PcgDiagnostics],
) -> Result<(), AnyError> {
    if scalar_diagnostics != fused_diagnostics
        || scalar
            .iter()
            .zip(fused)
            .any(|(left, right)| left.to_bits() != right.to_bits())
    {
        return Err("fused result is not bitwise identical to scalar".into());
    }
    Ok(())
}

fn median(values: &[u128]) -> u128 {
    let mut values = values.to_vec();
    values.sort_unstable();
    values[values.len() / 2]
}

fn paired_ratio_bootstrap(scalar: &[u128], fused: &[u128]) -> (f64, f64) {
    let mut state = 0x243f_6a88_85a3_08d3_u64;
    let mut scalar_sample = vec![0; scalar.len()];
    let mut fused_sample = vec![0; fused.len()];
    let mut ratios = Vec::with_capacity(10_000);
    for _ in 0..10_000 {
        for index in 0..scalar.len() {
            state = state
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1);
            let chosen = (state as usize) % scalar.len();
            scalar_sample[index] = scalar[chosen];
            fused_sample[index] = fused[chosen];
        }
        ratios.push(median(&fused_sample) as f64 / median(&scalar_sample) as f64);
    }
    ratios.sort_by(f64::total_cmp);
    (ratios[249], ratios[9_749])
}
