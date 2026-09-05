//! Fresh-RHS holdout qualification of the frozen first-call dispatch policy.
use cmg::experimental::{
    BatchDispatchOptions, BatchDispatchRoute, CalibratedPcgBatchSolver, FusedPcgWorkspace4,
    solve_pcg_batch_fused_width4_into_with_workspace,
};
use cmg::{
    CmgOptions, Laplacian, PcgBatchMut, PcgBatchRef, PcgBatchWorkspace, PcgDiagnostics, PcgOptions,
    solve_pcg_batch_into_with_workspace,
};
use std::error::Error;
use std::hint::black_box;
use std::time::Instant;

type AnyError = Box<dyn Error>;

fn main() -> Result<(), AnyError> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args == ["identity"] {
        println!(
            "{{\"source_commit\":\"{}\",\"source_archive_sha256\":\"{}\"}}",
            option_env!("CMG_BENCH_COMMIT").unwrap_or("unknown"),
            option_env!("CMG_BENCH_ARCHIVE_SHA256").unwrap_or("unknown")
        );
        return Ok(());
    }
    if args.len() != 5 {
        return Err("usage: fused-dispatch-experiment VERTICES DEGREE RHS_COUNT distinct|heterogeneous RHS_SEED".into());
    }
    let n: usize = args[0].parse()?;
    let degree: usize = args[1].parse()?;
    let count: usize = args[2].parse()?;
    let mode = &args[3];
    let seed: u64 = args[4].parse()?;
    if n < 4
        || n % 2 != 0
        || !(3..=16).contains(&degree)
        || !(4..=16).contains(&count)
        || !matches!(mode.as_str(), "distinct" | "heterogeneous")
    {
        return Err("invalid dispatch fixture arguments".into());
    }
    let graph = graph(n, degree)?;
    let mut auto = CalibratedPcgBatchSolver::build(
        &graph,
        CmgOptions::default(),
        BatchDispatchOptions {
            workspace_memory_budget_bytes: Some(1024 * 1024 * 1024),
            ..BatchDispatchOptions::default()
        },
    )?;
    let pcg = PcgOptions {
        relative_tolerance: 1e-8,
        max_iterations: 1000,
        ..PcgOptions::default()
    };
    let first_rhs = rhs(&graph, count, mode, seed)?;
    let mut outputs = [
        vec![0.0; n * count],
        vec![0.0; n * count],
        vec![0.0; n * count],
    ];
    let mut diagnostics = [
        vec![PcgDiagnostics::default(); count],
        vec![PcgDiagnostics::default(); count],
        vec![PcgDiagnostics::default(); count],
    ];
    let start = Instant::now();
    let first = auto.solve_batch_into(
        PcgBatchRef::contiguous(&first_rhs, count, n)?,
        PcgBatchMut::contiguous(&mut outputs[2], count, n)?,
        &mut diagnostics[2],
        pcg,
    )?;
    let first_call_ns = start.elapsed().as_nanos();
    let calibration = *auto
        .calibration_report()
        .ok_or("missing calibration report")?;
    if first.cached || first.executed != BatchDispatchRoute::Scalar {
        return Err("first Auto call must return its scalar baseline".into());
    }
    // Direct comparators share exactly the same immutable hierarchy as Auto.
    // Allocate and warm them outside the holdout measurements.
    let mut scalar = PcgBatchWorkspace::new(auto.preconditioner())?;
    let mut fused = FusedPcgWorkspace4::try_new(auto.preconditioner())?;
    for route in 0..2 {
        direct(
            route,
            &auto,
            &first_rhs,
            &mut outputs[route],
            &mut diagnostics[route],
            pcg,
            &mut scalar,
            &mut fused,
        )?;
    }
    identical(&outputs, &diagnostics)?;
    let mut times = [[0_u128; 7]; 3];
    let retained = auto.retained_workspace_bytes();
    let mut seeds = [0_u64; 7];
    for sample in 0..7 {
        seeds[sample] = seed.wrapping_add(1 + sample as u64);
        let fresh = rhs(&graph, count, mode, seeds[sample])?;
        // Rotate order; every scalar/fused/Auto triple solves the same fresh RHS.
        for offset in 0..3 {
            let route = (sample + offset) % 3;
            let start = Instant::now();
            if route == 2 {
                let report = auto.solve_batch_into(
                    PcgBatchRef::contiguous(black_box(&fresh), count, n)?,
                    PcgBatchMut::contiguous(black_box(&mut outputs[2]), count, n)?,
                    black_box(&mut diagnostics[2]),
                    pcg,
                )?;
                times[2][sample] = start.elapsed().as_nanos();
                if !report.cached
                    || report.executed != first.selected
                    || report.retained_workspace_bytes != retained
                {
                    return Err("holdout Auto did not reuse its frozen workspace/decision".into());
                }
            } else {
                direct(
                    route,
                    &auto,
                    black_box(&fresh),
                    black_box(&mut outputs[route]),
                    black_box(&mut diagnostics[route]),
                    pcg,
                    &mut scalar,
                    &mut fused,
                )?;
                times[route][sample] = start.elapsed().as_nanos();
            }
        }
        identical(&outputs, &diagnostics)?;
    }
    if times.iter().flatten().any(|&ns| ns == 0) {
        return Err("zero timing".into());
    }
    let selected = usize::from(first.selected == BatchDispatchRoute::Fused);
    println!(
        "{{\"schema\":\"cmg-dispatch-case-v1\",\"source_commit\":\"{}\",\"source_archive_sha256\":\"{}\",\"vertices\":{n},\"degree\":{degree},\"edges\":{},\"rhs_count\":{count},\"mode\":\"{mode}\",\"rhs_seed\":{seed},\"holdout_seeds\":{seeds:?},\"repetitions\":7,\"bitwise_identical\":true,\"first_executed\":\"Scalar\",\"selected\":\"{:?}\",\"reason\":\"{:?}\",\"first_call_ns\":{first_call_ns},\"calibration_extra_ns\":{},\"calibration_pairs\":{},\"calibration_scalar_ns\":{:?},\"calibration_fused_ns\":{:?},\"calibration_ratio\":{},\"calibration_ci95\":{},\"break_even_batches\":{},\"peak_bound_bytes\":{},\"retained_workspace_bytes\":{retained},\"workspace_budget_bytes\":1073741824,\"scalar_workspace_bytes\":{},\"fused_workspace_bytes\":{},\"scalar_ns\":{:?},\"fused_ns\":{:?},\"auto_ns\":{:?},\"fused_over_scalar\":{},\"auto_over_scalar\":{},\"auto_over_selected\":{},\"cached_holdout\":true}}",
        option_env!("CMG_BENCH_COMMIT").unwrap_or("unknown"),
        option_env!("CMG_BENCH_ARCHIVE_SHA256").unwrap_or("unknown"),
        graph.edges().len(),
        first.selected,
        first.reason,
        calibration.extra_nanoseconds,
        calibration.completed_pairs,
        calibration.scalar_nanoseconds,
        calibration.fused_nanoseconds,
        calibration.ratio.map_or("null".into(), |v| v.to_string()),
        calibration
            .ratio_ci95
            .map_or("null".into(), |v| format!("{v:?}")),
        calibration
            .break_even_batches
            .map_or("null".into(), |v| v.to_string()),
        calibration.calibration_peak_bound_bytes,
        scalar.byte_len(),
        fused.byte_len(),
        times[0],
        times[1],
        times[2],
        ratio(&times[0], &times[1]),
        ratio(&times[0], &times[2]),
        ratio(&times[selected], &times[2])
    );
    Ok(())
}

fn graph(n: usize, degree: usize) -> Result<Laplacian, AnyError> {
    let workers = n / 2;
    let mut edges = Vec::with_capacity(workers * degree);
    for worker in 0..workers {
        for link in 0..degree {
            // First two links form one connected bipartite cycle. Fixed graph seed.
            let firm = match link {
                0 => worker,
                1 => (worker + 1) % workers,
                _ => ((2 * link + 1) * worker + 17 * link + 3) % workers,
            };
            edges.push((
                worker,
                workers + firm,
                0.25 + ((worker + 7 * link) % 23) as f64 / 16.0,
            ));
        }
    }
    Ok(Laplacian::from_edges(n, edges)?)
}

fn rhs(graph: &Laplacian, count: usize, mode: &str, seed: u64) -> Result<Vec<f64>, AnyError> {
    let n = graph.vertex_count();
    let mut packed = Vec::with_capacity(n * count);
    for lane in 0..count {
        let mut state = seed.wrapping_mul(7919).wrapping_add(lane as u64 * 65521);
        let target: Vec<_> = (0..n)
            .map(|vertex| {
                state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
                if mode == "heterogeneous" && lane % 4 == 0 {
                    0.0
                } else if mode == "heterogeneous" && lane % 4 == 1 {
                    // Smooth target, distinct from the random lanes and across batches.
                    let phase = ((seed % 101) as f64 + lane as f64) / 101.0;
                    (vertex as f64 / n as f64 * std::f64::consts::TAU + phase).sin()
                } else {
                    (state >> 11) as f64 / ((1_u64 << 53) as f64) - 0.5
                }
            })
            .collect();
        packed.extend(graph.matvec(&target)?);
    }
    Ok(packed)
}

#[allow(clippy::too_many_arguments)]
fn direct(
    route: usize,
    auto: &CalibratedPcgBatchSolver,
    rhs: &[f64],
    output: &mut [f64],
    diagnostics: &mut [PcgDiagnostics],
    pcg: PcgOptions,
    scalar: &mut PcgBatchWorkspace,
    fused: &mut FusedPcgWorkspace4,
) -> Result<(), AnyError> {
    let count = diagnostics.len();
    let n = auto.graph().vertex_count();
    if route == 0 {
        solve_pcg_batch_into_with_workspace(
            auto.graph(),
            auto.preconditioner(),
            PcgBatchRef::contiguous(rhs, count, n)?,
            None,
            PcgBatchMut::contiguous(output, count, n)?,
            diagnostics,
            pcg,
            scalar,
        )?;
    } else {
        solve_pcg_batch_fused_width4_into_with_workspace(
            auto.graph(),
            auto.preconditioner(),
            PcgBatchRef::contiguous(rhs, count, n)?,
            PcgBatchMut::contiguous(output, count, n)?,
            diagnostics,
            pcg,
            fused,
        )?;
    }
    Ok(())
}

fn diag_bits(d: PcgDiagnostics) -> [u64; 8] {
    [
        d.iterations() as u64,
        d.restarts() as u64,
        d.initial_residual_norm().to_bits(),
        d.residual_norm().to_bits(),
        d.relative_residual().to_bits(),
        d.backward_error().to_bits(),
        d.tolerance().to_bits(),
        d.rhs_projection_norm().to_bits(),
    ]
}
fn identical(
    outputs: &[Vec<f64>; 3],
    diagnostics: &[Vec<PcgDiagnostics>; 3],
) -> Result<(), AnyError> {
    for route in 1..3 {
        if outputs[0]
            .iter()
            .zip(&outputs[route])
            .any(|(a, b)| a.to_bits() != b.to_bits())
            || diagnostics[0]
                .iter()
                .zip(&diagnostics[route])
                .any(|(&a, &b)| diag_bits(a) != diag_bits(b))
        {
            return Err("solution or diagnostics are not bitwise identical".into());
        }
    }
    Ok(())
}
fn median(mut values: [u128; 7]) -> u128 {
    values.sort_unstable();
    values[3]
}
fn ratio(denominator: &[u128; 7], numerator: &[u128; 7]) -> String {
    let point = median(*numerator) as f64 / median(*denominator) as f64;
    let mut rng = 0x243f_6a88_85a3_08d3_u64;
    let mut ratios = [0.0; 10_000];
    for value in &mut ratios {
        let mut a = [0; 7];
        let mut b = [0; 7];
        for i in 0..7 {
            rng = rng.wrapping_mul(6364136223846793005).wrapping_add(1);
            let j = (rng % 7) as usize;
            a[i] = denominator[j];
            b[i] = numerator[j];
        }
        *value = median(b) as f64 / median(a) as f64;
    }
    ratios.sort_by(f64::total_cmp);
    format!(
        "{{\"ratio\":{point},\"ci95\":[{},{}]}}",
        ratios[249], ratios[9749]
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fresh_fixtures_and_identical_ratio() {
        let graph = graph(100, 8).unwrap();
        let a = rhs(&graph, 5, "heterogeneous", 101).unwrap();
        let b = rhs(&graph, 5, "heterogeneous", 102).unwrap();
        assert!(a[..100].iter().all(|&v| v == 0.0));
        assert_ne!(&a[100..200], &b[100..200]);
        assert_ne!(&a[200..300], &a[300..400]);
        assert_eq!(
            ratio(&[10; 7], &[5; 7]),
            "{\"ratio\":0.5,\"ci95\":[0.5,0.5]}"
        );
    }
}
