use cmg::{
    CmgError, CmgOptions, CmgPreconditioner, Laplacian, ParallelCmgPlan, ParallelExecutor,
    ParallelOptions, PcgBatchMut, PcgBatchRef, PcgBatchWorkspace, PcgDiagnostics, PcgOptions,
    PreparedLaplacianTopology, RepeatedPcgMemoryReport, profile_pcg_batch_into_with_workspace,
    profile_pcg_with_plan, solve_pcg_batch, solve_pcg_batch_into_with_executor,
    solve_pcg_batch_into_with_plan_and_workspace, solve_pcg_batch_into_with_workspace,
    solve_pcg_batch_with_retained_preconditioner_into_with_workspace,
};
use std::alloc::{GlobalAlloc, Layout, System};
use std::fmt::Write as _;
use std::hint::black_box;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::time::Instant;

struct TrackingAllocator;

static TRACKING: AtomicBool = AtomicBool::new(false);
static ALLOCATION_COUNT: AtomicUsize = AtomicUsize::new(0);
static REQUESTED_BYTES: AtomicUsize = AtomicUsize::new(0);
static LIVE_BYTES: AtomicUsize = AtomicUsize::new(0);
static PEAK_LIVE_BYTES: AtomicUsize = AtomicUsize::new(0);

#[global_allocator]
static GLOBAL_ALLOCATOR: TrackingAllocator = TrackingAllocator;

unsafe impl GlobalAlloc for TrackingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let pointer = unsafe { System.alloc(layout) };
        if !pointer.is_null() {
            allocation(layout.size());
        }
        pointer
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        let pointer = unsafe { System.alloc_zeroed(layout) };
        if !pointer.is_null() {
            allocation(layout.size());
        }
        pointer
    }

    unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
        deallocation(layout.size());
        unsafe { System.dealloc(pointer, layout) };
    }

    unsafe fn realloc(&self, pointer: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        let new_pointer = unsafe { System.realloc(pointer, layout, new_size) };
        if !new_pointer.is_null() && TRACKING.load(Ordering::Relaxed) {
            ALLOCATION_COUNT.fetch_add(1, Ordering::Relaxed);
            REQUESTED_BYTES.fetch_add(new_size, Ordering::Relaxed);
            if new_size >= layout.size() {
                let live = LIVE_BYTES.fetch_add(new_size - layout.size(), Ordering::Relaxed)
                    + new_size
                    - layout.size();
                PEAK_LIVE_BYTES.fetch_max(live, Ordering::Relaxed);
            } else {
                LIVE_BYTES.fetch_sub(layout.size() - new_size, Ordering::Relaxed);
            }
        }
        new_pointer
    }
}

fn allocation(bytes: usize) {
    if TRACKING.load(Ordering::Relaxed) {
        ALLOCATION_COUNT.fetch_add(1, Ordering::Relaxed);
        REQUESTED_BYTES.fetch_add(bytes, Ordering::Relaxed);
        let live = LIVE_BYTES.fetch_add(bytes, Ordering::Relaxed) + bytes;
        PEAK_LIVE_BYTES.fetch_max(live, Ordering::Relaxed);
    }
}

fn deallocation(bytes: usize) {
    if TRACKING.load(Ordering::Relaxed) {
        LIVE_BYTES.fetch_sub(bytes, Ordering::Relaxed);
    }
}

#[derive(Debug)]
struct Config {
    case: String,
    input_edges: usize,
    rhs_count: usize,
    threads: usize,
    repetitions: usize,
    output: Option<String>,
}

impl Config {
    fn parse() -> Self {
        let mut config = Self {
            case: "balanced".to_owned(),
            input_edges: 25_000,
            rhs_count: 61,
            threads: 1,
            repetitions: 5,
            output: None,
        };
        let mut arguments = std::env::args().skip(1);
        while let Some(argument) = arguments.next() {
            let mut value = || arguments.next().expect("flag requires a value");
            match argument.as_str() {
                "--case" => config.case = value(),
                "--edges" => config.input_edges = value().parse().expect("integer edge count"),
                "--rhs" => config.rhs_count = value().parse().expect("integer RHS count"),
                "--threads" => config.threads = value().parse().expect("integer thread count"),
                "--repetitions" => {
                    config.repetitions = value().parse().expect("integer repetition count")
                }
                "--output" => config.output = Some(value()),
                "--help" => {
                    println!(
                        "fixed-topology-sequence --case balanced|chain --edges N --rhs N --threads N --repetitions N [--output FILE]"
                    );
                    std::process::exit(0);
                }
                _ => panic!("unknown argument {argument}"),
            }
        }
        assert!(matches!(config.case.as_str(), "balanced" | "chain"));
        assert!(config.input_edges > 0);
        assert!(config.rhs_count > 0);
        assert!(config.threads > 0);
        assert!(config.repetitions > 0);
        config
    }
}

struct Fixture {
    vertices: usize,
    endpoints: Vec<(usize, usize)>,
    frames: Vec<Vec<f64>>,
    changes: Vec<f64>,
}

fn fixture(config: &Config) -> Fixture {
    match config.case.as_str() {
        "balanced" => balanced_fixture(config.input_edges),
        "chain" => chain_fixture(config.input_edges),
        _ => unreachable!(),
    }
}

fn balanced_fixture(input_edges: usize) -> Fixture {
    let vertices = 144;
    let endpoints = (0..input_edges)
        .map(|observation| {
            let worker = observation % 80;
            let firm = (worker + observation / 80 + 17 * (observation / 512)) % 64;
            (worker, 80 + firm)
        })
        .collect::<Vec<_>>();
    frames_from_changes(
        vertices,
        endpoints,
        &[0.24, 0.08, 0.007, 0.001, 1.8e-5, 1.0e-6],
    )
}

fn chain_fixture(input_edges: usize) -> Fixture {
    let vertices = 9_000;
    let endpoints = (0..input_edges)
        .map(|observation| {
            let first = observation % 5_000;
            let second = (first + (observation / 5_000) % 2) % 4_000;
            (first, 5_000 + second)
        })
        .collect::<Vec<_>>();
    frames_from_changes(
        vertices,
        endpoints,
        &[1.56, 0.512, 0.347, 0.115, 0.00805, 3.28e-5],
    )
}

fn frames_from_changes(
    vertices: usize,
    endpoints: Vec<(usize, usize)>,
    changes: &[f64],
) -> Fixture {
    let mut current = (0..endpoints.len())
        .map(|edge| 0.5 + ((edge * 37 + 11) % 257) as f64 / 128.0)
        .collect::<Vec<_>>();
    let mut frames = vec![current.clone()];
    for (frame, &change) in changes.iter().enumerate() {
        for (edge, weight) in current.iter_mut().enumerate() {
            // The pattern spans [-0.5, 1.0], including 1.0 in every large
            // fixture. Thus `change` is the realized maximum relative change,
            // while even the 1.56 chain transition stays strictly positive.
            let pattern = 1.5 * ((edge * 29 + frame * 17) % 101) as f64 / 100.0 - 0.5;
            *weight *= 1.0 + change * pattern;
        }
        frames.push(current.clone());
    }
    Fixture {
        vertices,
        endpoints,
        frames,
        changes: std::iter::once(0.0)
            .chain(changes.iter().copied())
            .collect(),
    }
}

fn weighted_edges(endpoints: &[(usize, usize)], weights: &[f64]) -> Vec<(usize, usize, f64)> {
    endpoints
        .iter()
        .zip(weights)
        .map(|(&(u, v), &weight)| (u, v, weight))
        .collect()
}

fn targets(vertices: usize, rhs_count: usize, frame: usize) -> Vec<Vec<f64>> {
    (0..rhs_count)
        .map(|rhs| {
            (0..vertices)
                .map(|vertex| {
                    let base = ((vertex * 31 + rhs * 17 + 7) % 101) as f64 / 13.0 - 3.75;
                    base + 0.0025 * frame as f64 * ((vertex + 3 * rhs + 1) as f64 / 19.0).sin()
                })
                .collect()
        })
        .collect()
}

fn right_hand_sides(graph: &Laplacian, targets: &[Vec<f64>]) -> Vec<Vec<f64>> {
    targets
        .iter()
        .map(|target| graph.matvec(target).expect("valid target"))
        .collect()
}

fn packed(vectors: &[Vec<f64>]) -> Vec<f64> {
    vectors.iter().flatten().copied().collect()
}

fn median(values: &mut [u128]) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn time_result<T>(operation: impl FnOnce() -> Result<T, CmgError>) -> (u128, Result<T, CmgError>) {
    let start = Instant::now();
    let result = operation();
    (start.elapsed().as_nanos(), result)
}

fn measure_pair(
    warmups: usize,
    repetitions: usize,
    mut operation: impl FnMut(bool) -> Result<(), CmgError>,
) -> ((u128, usize), (u128, usize)) {
    for repetition in 0..warmups {
        let first = repetition % 2 == 0;
        let _ = black_box(operation(first));
        let _ = black_box(operation(!first));
    }

    let mut first_samples = Vec::with_capacity(repetitions);
    let mut second_samples = Vec::with_capacity(repetitions);
    let mut first_failures = 0;
    let mut second_failures = 0;
    for repetition in 0..repetitions {
        let first = repetition % 2 == 0;
        for choice in [first, !first] {
            let (elapsed, result) = time_result(|| operation(choice));
            if choice {
                first_failures += usize::from(result.is_err());
                first_samples.push(elapsed);
            } else {
                second_failures += usize::from(result.is_err());
                second_samples.push(elapsed);
            }
            let _ = black_box(result);
        }
    }
    (
        (median(&mut first_samples), first_failures),
        (median(&mut second_samples), second_failures),
    )
}

fn reset_allocation_counters() {
    ALLOCATION_COUNT.store(0, Ordering::Relaxed);
    REQUESTED_BYTES.store(0, Ordering::Relaxed);
    LIVE_BYTES.store(0, Ordering::Relaxed);
    PEAK_LIVE_BYTES.store(0, Ordering::Relaxed);
}

fn error_kind(error: &CmgError) -> &'static str {
    match error {
        CmgError::MaximumIterations { .. } => "maximum_iterations",
        CmgError::ResidualVerificationFailed { .. } => "residual_verification_failed",
        CmgError::PcgBreakdown { .. } => "pcg_breakdown",
        CmgError::MemoryBudgetExceeded { .. } => "memory_budget_exceeded",
        _ => "other",
    }
}

fn write_diagnostic_record(
    receipt: &mut String,
    frame: usize,
    route: &str,
    result: &Result<(), CmgError>,
    diagnostics: &[PcgDiagnostics],
) -> std::fmt::Result {
    match result {
        Ok(()) => {
            let iterations: usize = diagnostics.iter().map(|value| value.iterations()).sum();
            let restarts: usize = diagnostics.iter().map(|value| value.restarts()).sum();
            let maximum_residual = diagnostics
                .iter()
                .map(|value| value.residual_norm())
                .fold(0.0_f64, f64::max);
            let maximum_relative_residual = diagnostics
                .iter()
                .map(|value| value.relative_residual())
                .fold(0.0_f64, f64::max);
            let maximum_backward_error = diagnostics
                .iter()
                .map(|value| value.backward_error())
                .fold(0.0_f64, f64::max);
            let minimum_tolerance = diagnostics
                .iter()
                .map(|value| value.tolerance())
                .fold(f64::INFINITY, f64::min);
            writeln!(
                receipt,
                "{{\"record\":\"diagnostics\",\"frame\":{frame},\"route\":\"{route}\",\"certified\":true,\"iteration_sum\":{iterations},\"restart_sum\":{restarts},\"maximum_residual_norm\":{maximum_residual:.17e},\"maximum_relative_residual\":{maximum_relative_residual:.17e},\"maximum_backward_error\":{maximum_backward_error:.17e},\"minimum_tolerance\":{minimum_tolerance:.17e}}}"
            )
        }
        Err(error) => writeln!(
            receipt,
            "{{\"record\":\"diagnostics\",\"frame\":{frame},\"route\":\"{route}\",\"certified\":false,\"error\":\"{}\"}}",
            error_kind(error),
        ),
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    TRACKING.store(false, Ordering::Relaxed);
    let config = Config::parse();
    let fixture = fixture(&config);
    let topology = PreparedLaplacianTopology::prepare(fixture.vertices, fixture.endpoints.clone())?;
    let mut assembly_workspace = topology.workspace()?;
    let graphs: Vec<_> = fixture
        .frames
        .iter()
        .map(|weights| topology.assemble_with_workspace(weights, &mut assembly_workspace))
        .collect::<Result<_, _>>()?;
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: config.threads,
        ..ParallelOptions::default()
    })?;
    let options = PcgOptions::default();
    let preconditioners: Vec<_> = graphs
        .iter()
        .map(|graph| CmgPreconditioner::build(graph, CmgOptions::default()))
        .collect::<Result<_, _>>()?;
    let plans: Vec<_> = preconditioners
        .iter()
        .map(|preconditioner| ParallelCmgPlan::build(preconditioner, &executor))
        .collect::<Result<_, _>>()?;
    let all_targets: Vec<_> = (0..graphs.len())
        .map(|frame| targets(fixture.vertices, config.rhs_count, frame))
        .collect();
    let all_rhs: Vec<_> = graphs
        .iter()
        .zip(&all_targets)
        .map(|(graph, targets)| right_hand_sides(graph, targets))
        .collect();
    let all_packed_rhs: Vec<_> = all_rhs.iter().map(|rhs| packed(rhs)).collect();
    let all_packed_targets: Vec<_> = all_targets.iter().map(|target| packed(target)).collect();

    let mut receipt = String::new();
    writeln!(
        receipt,
        "{{\"record\":\"header\",\"schema\":4,\"case\":\"{}\",\"vertices\":{},\"input_edges\":{},\"canonical_edges\":{},\"rhs\":{},\"threads\":{},\"warmups\":2,\"repetitions\":{},\"alternating_pairs\":true,\"change_semantics\":\"maximum_relative\"}}",
        config.case,
        fixture.vertices,
        fixture.endpoints.len(),
        topology.canonical_edge_count(),
        config.rhs_count,
        config.threads,
        config.repetitions,
    )?;

    let mut last_diagnostics = vec![PcgDiagnostics::default(); config.rhs_count];
    let mut last_output = vec![0.0; fixture.vertices * config.rhs_count];
    for frame in 0..graphs.len() {
        let graph = &graphs[frame];
        let realized_change = if frame == 0 {
            0.0
        } else {
            fixture.frames[frame]
                .iter()
                .zip(&fixture.frames[frame - 1])
                .map(|(current, previous)| ((current - previous) / previous).abs())
                .fold(0.0_f64, f64::max)
        };
        let weighted = weighted_edges(&fixture.endpoints, &fixture.frames[frame]);
        let (
            (legacy_assembly_ns, legacy_assembly_failures),
            (prepared_assembly_ns, prepared_assembly_failures),
        ) = measure_pair(2, config.repetitions, |legacy| {
            if legacy {
                black_box(Laplacian::from_edges(
                    fixture.vertices,
                    weighted.iter().copied(),
                )?);
            } else {
                black_box(
                    topology
                        .assemble_with_workspace(&fixture.frames[frame], &mut assembly_workspace)?,
                );
            }
            Ok(())
        });
        let ((fresh_setup_ns, fresh_setup_failures), (plan_setup_ns, plan_setup_failures)) =
            measure_pair(2, config.repetitions, |fresh| {
                if fresh {
                    black_box(CmgPreconditioner::build_with_executor(
                        graph,
                        CmgOptions::default(),
                        &executor,
                    )?);
                } else {
                    black_box(ParallelCmgPlan::build(&preconditioners[frame], &executor)?);
                }
                Ok(())
            });

        let packed_rhs = &all_packed_rhs[frame];
        let rhs_view = || {
            PcgBatchRef::contiguous(packed_rhs, config.rhs_count, fixture.vertices)
                .expect("prevalidated packed RHS")
        };
        let mut caller_workspace = PcgBatchWorkspace::new(&preconditioners[frame])?;
        let mut executor_workspaces = (0..config.threads.min(config.rhs_count))
            .map(|_| PcgBatchWorkspace::new(&preconditioners[frame]))
            .collect::<Result<Vec<_>, _>>()?;
        let mut planned_workspace = PcgBatchWorkspace::new(&preconditioners[frame])?;
        let ((owned_ns, owned_failures), (caller_ns, caller_failures)) =
            measure_pair(2, config.repetitions, |owned| {
                if owned {
                    black_box(solve_pcg_batch(
                        graph,
                        &preconditioners[frame],
                        &all_rhs[frame],
                        options,
                    )?);
                    Ok(())
                } else {
                    solve_pcg_batch_into_with_workspace(
                        graph,
                        &preconditioners[frame],
                        rhs_view(),
                        None,
                        PcgBatchMut::contiguous(
                            &mut last_output,
                            config.rhs_count,
                            fixture.vertices,
                        )?,
                        &mut last_diagnostics,
                        options,
                        &mut caller_workspace,
                    )
                }
            });
        let ((executor_ns, executor_failures), (planned_ns, planned_failures)) =
            measure_pair(2, config.repetitions, |across_rhs| {
                if across_rhs {
                    solve_pcg_batch_into_with_executor(
                        graph,
                        &preconditioners[frame],
                        rhs_view(),
                        None,
                        PcgBatchMut::contiguous(
                            &mut last_output,
                            config.rhs_count,
                            fixture.vertices,
                        )?,
                        &mut last_diagnostics,
                        options,
                        &mut executor_workspaces,
                        &executor,
                    )
                } else {
                    solve_pcg_batch_into_with_plan_and_workspace(
                        graph,
                        &preconditioners[frame],
                        &plans[frame],
                        rhs_view(),
                        None,
                        PcgBatchMut::contiguous(
                            &mut last_output,
                            config.rhs_count,
                            fixture.vertices,
                        )?,
                        &mut last_diagnostics,
                        options,
                        &mut planned_workspace,
                        &executor,
                    )
                }
            });

        let previous = frame.saturating_sub(1);
        let mut previous_workspace = PcgBatchWorkspace::new(&preconditioners[previous])?;
        let mut frame_zero_workspace = PcgBatchWorkspace::new(&preconditioners[0])?;
        let (
            (retained_previous_ns, retained_previous_failures),
            (retained_zero_ns, retained_zero_failures),
        ) = measure_pair(2, config.repetitions, |previous_frame| {
            if previous_frame {
                solve_pcg_batch_with_retained_preconditioner_into_with_workspace(
                    graph,
                    &preconditioners[previous],
                    rhs_view(),
                    None,
                    PcgBatchMut::contiguous(&mut last_output, config.rhs_count, fixture.vertices)?,
                    &mut last_diagnostics,
                    options,
                    &mut previous_workspace,
                )
            } else {
                solve_pcg_batch_with_retained_preconditioner_into_with_workspace(
                    graph,
                    &preconditioners[0],
                    rhs_view(),
                    None,
                    PcgBatchMut::contiguous(&mut last_output, config.rhs_count, fixture.vertices)?,
                    &mut last_diagnostics,
                    options,
                    &mut frame_zero_workspace,
                )
            }
        });

        let guess_frame = frame.saturating_sub(1);
        let guess = PcgBatchRef::contiguous(
            &all_packed_targets[guess_frame],
            config.rhs_count,
            fixture.vertices,
        )?;
        let ((warm_zero_ns, warm_zero_failures), (warm_ns, warm_failures)) =
            measure_pair(2, config.repetitions, |zero_start| {
                solve_pcg_batch_with_retained_preconditioner_into_with_workspace(
                    graph,
                    &preconditioners[previous],
                    rhs_view(),
                    (!zero_start).then_some(guess),
                    PcgBatchMut::contiguous(&mut last_output, config.rhs_count, fixture.vertices)?,
                    &mut last_diagnostics,
                    options,
                    &mut previous_workspace,
                )
            });

        last_diagnostics.fill(PcgDiagnostics::default());
        let caller_diagnostic_result = solve_pcg_batch_into_with_workspace(
            graph,
            &preconditioners[frame],
            rhs_view(),
            None,
            PcgBatchMut::contiguous(&mut last_output, config.rhs_count, fixture.vertices)?,
            &mut last_diagnostics,
            options,
            &mut caller_workspace,
        );
        write_diagnostic_record(
            &mut receipt,
            frame,
            "serial_fresh",
            &caller_diagnostic_result,
            &last_diagnostics,
        )?;

        last_diagnostics.fill(PcgDiagnostics::default());
        let executor_diagnostic_result = solve_pcg_batch_into_with_executor(
            graph,
            &preconditioners[frame],
            rhs_view(),
            None,
            PcgBatchMut::contiguous(&mut last_output, config.rhs_count, fixture.vertices)?,
            &mut last_diagnostics,
            options,
            &mut executor_workspaces,
            &executor,
        );
        write_diagnostic_record(
            &mut receipt,
            frame,
            "across_rhs_fresh",
            &executor_diagnostic_result,
            &last_diagnostics,
        )?;

        last_diagnostics.fill(PcgDiagnostics::default());
        let planned_diagnostic_result = solve_pcg_batch_into_with_plan_and_workspace(
            graph,
            &preconditioners[frame],
            &plans[frame],
            rhs_view(),
            None,
            PcgBatchMut::contiguous(&mut last_output, config.rhs_count, fixture.vertices)?,
            &mut last_diagnostics,
            options,
            &mut planned_workspace,
            &executor,
        );
        write_diagnostic_record(
            &mut receipt,
            frame,
            "within_operator_fresh",
            &planned_diagnostic_result,
            &last_diagnostics,
        )?;

        last_diagnostics.fill(PcgDiagnostics::default());
        let retained_previous_diagnostic_result =
            solve_pcg_batch_with_retained_preconditioner_into_with_workspace(
                graph,
                &preconditioners[previous],
                rhs_view(),
                None,
                PcgBatchMut::contiguous(&mut last_output, config.rhs_count, fixture.vertices)?,
                &mut last_diagnostics,
                options,
                &mut previous_workspace,
            );
        write_diagnostic_record(
            &mut receipt,
            frame,
            "retained_previous_zero_start",
            &retained_previous_diagnostic_result,
            &last_diagnostics,
        )?;

        last_diagnostics.fill(PcgDiagnostics::default());
        let retained_zero_diagnostic_result =
            solve_pcg_batch_with_retained_preconditioner_into_with_workspace(
                graph,
                &preconditioners[0],
                rhs_view(),
                None,
                PcgBatchMut::contiguous(&mut last_output, config.rhs_count, fixture.vertices)?,
                &mut last_diagnostics,
                options,
                &mut frame_zero_workspace,
            );
        write_diagnostic_record(
            &mut receipt,
            frame,
            "retained_frame_zero",
            &retained_zero_diagnostic_result,
            &last_diagnostics,
        )?;

        last_diagnostics.fill(PcgDiagnostics::default());
        let warm_diagnostic_result =
            solve_pcg_batch_with_retained_preconditioner_into_with_workspace(
                graph,
                &preconditioners[previous],
                rhs_view(),
                Some(guess),
                PcgBatchMut::contiguous(&mut last_output, config.rhs_count, fixture.vertices)?,
                &mut last_diagnostics,
                options,
                &mut previous_workspace,
            );
        write_diagnostic_record(
            &mut receipt,
            frame,
            "retained_previous_warm",
            &warm_diagnostic_result,
            &last_diagnostics,
        )?;
        let iteration_sum: usize = last_diagnostics
            .iter()
            .map(|diagnostics| diagnostics.iterations())
            .sum();
        let restart_sum: usize = last_diagnostics
            .iter()
            .map(|diagnostics| diagnostics.restarts())
            .sum();
        let maximum_backward_error = last_diagnostics
            .iter()
            .map(|diagnostics| diagnostics.backward_error())
            .fold(0.0_f64, f64::max);
        let failures = legacy_assembly_failures
            + prepared_assembly_failures
            + fresh_setup_failures
            + plan_setup_failures
            + owned_failures
            + caller_failures
            + executor_failures
            + planned_failures;
        writeln!(
            receipt,
            "{{\"record\":\"frame\",\"frame\":{frame},\"registered_change\":{:.17e},\"realized_max_relative_change\":{realized_change:.17e},\"legacy_assembly_median_ns\":{legacy_assembly_ns},\"prepared_assembly_median_ns\":{prepared_assembly_ns},\"fresh_setup_median_ns\":{fresh_setup_ns},\"plan_setup_median_ns\":{plan_setup_ns},\"owned_batch_median_ns\":{owned_ns},\"caller_batch_median_ns\":{caller_ns},\"executor_batch_median_ns\":{executor_ns},\"planned_batch_median_ns\":{planned_ns},\"retained_previous_median_ns\":{retained_previous_ns},\"retained_previous_failures\":{retained_previous_failures},\"retained_zero_median_ns\":{retained_zero_ns},\"retained_zero_failures\":{retained_zero_failures},\"warm_zero_reference_median_ns\":{warm_zero_ns},\"warm_zero_reference_failures\":{warm_zero_failures},\"warm_previous_median_ns\":{warm_ns},\"warm_previous_failures\":{warm_failures},\"iteration_sum\":{iteration_sum},\"restart_sum\":{restart_sum},\"maximum_backward_error\":{maximum_backward_error:.17e},\"required_route_failures\":{failures}}}",
            fixture.changes[frame],
        )?;
    }

    let last = graphs.len() - 1;
    let graph = &graphs[last];
    let packed_rhs = &all_packed_rhs[last];
    let mut audit_output = vec![0.0; packed_rhs.len()];
    let mut audit_diagnostics = vec![PcgDiagnostics::default(); config.rhs_count];
    let mut audit_workspace = PcgBatchWorkspace::new(&preconditioners[last])?;
    reset_allocation_counters();
    TRACKING.store(true, Ordering::SeqCst);
    solve_pcg_batch_into_with_workspace(
        graph,
        &preconditioners[last],
        PcgBatchRef::contiguous(packed_rhs, config.rhs_count, fixture.vertices)?,
        None,
        PcgBatchMut::contiguous(&mut audit_output, config.rhs_count, fixture.vertices)?,
        &mut audit_diagnostics,
        options,
        &mut audit_workspace,
    )?;
    TRACKING.store(false, Ordering::SeqCst);
    let caller_allocations = ALLOCATION_COUNT.load(Ordering::Relaxed);
    let caller_requested_bytes = REQUESTED_BYTES.load(Ordering::Relaxed);
    let caller_peak_live_bytes = PEAK_LIVE_BYTES.load(Ordering::Relaxed);

    reset_allocation_counters();
    TRACKING.store(true, Ordering::SeqCst);
    black_box(solve_pcg_batch(
        graph,
        &preconditioners[last],
        &all_rhs[last],
        options,
    )?);
    TRACKING.store(false, Ordering::SeqCst);
    let owned_allocations = ALLOCATION_COUNT.load(Ordering::Relaxed);
    let owned_requested_bytes = REQUESTED_BYTES.load(Ordering::Relaxed);
    let owned_peak_live_bytes = PEAK_LIVE_BYTES.load(Ordering::Relaxed);

    let memory = RepeatedPcgMemoryReport::with_parallel_plan(
        &topology,
        &assembly_workspace,
        graph,
        &preconditioners[0],
        Some(&plans[0]),
        &[PcgBatchWorkspace::new(&preconditioners[0])?],
        config.rhs_count,
        true,
    )?;
    writeln!(
        receipt,
        "{{\"record\":\"allocation_memory\",\"caller_allocations\":{caller_allocations},\"caller_requested_bytes\":{caller_requested_bytes},\"caller_peak_live_bytes\":{caller_peak_live_bytes},\"owned_allocations\":{owned_allocations},\"owned_requested_bytes\":{owned_requested_bytes},\"owned_peak_live_bytes\":{owned_peak_live_bytes},\"prepared_topology_bytes\":{},\"assembly_workspace_bytes\":{},\"current_graph_bytes\":{},\"retained_preconditioner_bytes\":{},\"shared_component_bytes\":{},\"parallel_plan_bytes\":{},\"workspace_pool_bytes\":{},\"total_solver_retained_bytes\":{},\"caller_logical_bytes\":{}}}",
        memory.prepared_topology_bytes(),
        memory.numeric_assembly_scratch_bytes(),
        memory.current_numeric_graph_bytes(),
        memory.retained_preconditioner_bytes(),
        memory.shared_component_metadata_bytes(),
        memory.parallel_plan_bytes(),
        memory.workspace_pool_bytes(),
        memory.total_solver_retained_bytes(),
        memory.caller_logical_bytes(),
    )?;

    let last_weighted = weighted_edges(&fixture.endpoints, &fixture.frames[last]);
    let (legacy_profile_assembly_ns, legacy_profile_graph) =
        time_result(|| Laplacian::from_edges(fixture.vertices, last_weighted.iter().copied()));
    black_box(legacy_profile_graph?);
    let (prepared_profile_assembly_ns, prepared_profile_graph) = time_result(|| {
        topology.assemble_with_workspace(&fixture.frames[last], &mut assembly_workspace)
    });
    black_box(prepared_profile_graph?);
    let (profiled_preconditioner, setup_profile) =
        CmgPreconditioner::build_with_executor_profiled(graph, CmgOptions::default(), &executor)?;
    let (profiled_plan, plan_profile) =
        ParallelCmgPlan::build_profiled(&profiled_preconditioner, &executor)?;
    let profiled_pcg = profile_pcg_with_plan(
        graph,
        &profiled_preconditioner,
        &profiled_plan,
        &all_rhs[last][0],
        options,
        &executor,
    )?;
    let mut production_profile_reference = vec![0.0; packed_rhs.len()];
    let mut production_profile_diagnostics = vec![PcgDiagnostics::default(); config.rhs_count];
    let mut production_profile_workspace = PcgBatchWorkspace::new(&profiled_preconditioner)?;
    solve_pcg_batch_into_with_workspace(
        graph,
        &profiled_preconditioner,
        PcgBatchRef::contiguous(packed_rhs, config.rhs_count, fixture.vertices)?,
        None,
        PcgBatchMut::contiguous(
            &mut production_profile_reference,
            config.rhs_count,
            fixture.vertices,
        )?,
        &mut production_profile_diagnostics,
        options,
        &mut production_profile_workspace,
    )?;
    let mut profile_output = vec![0.0; packed_rhs.len()];
    let mut profile_diagnostics = vec![PcgDiagnostics::default(); config.rhs_count];
    let mut profile_workspace = PcgBatchWorkspace::new(&profiled_preconditioner)?;
    let batch_profile = profile_pcg_batch_into_with_workspace(
        graph,
        &profiled_preconditioner,
        PcgBatchRef::contiguous(packed_rhs, config.rhs_count, fixture.vertices)?,
        None,
        PcgBatchMut::contiguous(&mut profile_output, config.rhs_count, fixture.vertices)?,
        &mut profile_diagnostics,
        options,
        &mut profile_workspace,
    )?;
    assert_eq!(profile_diagnostics, production_profile_diagnostics);
    assert!(
        profile_output
            .iter()
            .zip(&production_profile_reference)
            .all(|(profiled, production)| profiled.to_bits() == production.to_bits())
    );
    let pcg_profile = profiled_pcg.profile();
    for phase in setup_profile.hierarchy_phases() {
        writeln!(
            receipt,
            "{{\"record\":\"hierarchy_phase\",\"level\":{},\"phase\":\"{}\",\"nanoseconds\":{}}}",
            phase.level(),
            phase.phase(),
            phase.nanoseconds(),
        )?;
    }
    writeln!(
        receipt,
        "{{\"record\":\"profile\",\"legacy_graph_assembly_ns\":{legacy_profile_assembly_ns},\"prepared_graph_assembly_ns\":{prepared_profile_assembly_ns},\"hierarchy_ns\":{},\"preconditioner_finalization_ns\":{},\"complete_preconditioner_ns\":{},\"plan_ns\":{},\"batch_validation_ns\":{},\"batch_gather_ns\":{},\"batch_solve_ns\":{},\"batch_scatter_ns\":{},\"batch_result_construction_ns\":{},\"pcg_matvec_ns\":{},\"pcg_preconditioner_ns\":{},\"pcg_residual_recompute_ns\":{},\"pcg_certification_ns\":{},\"pcg_profile_total_ns\":{}}}",
        setup_profile.hierarchy_nanoseconds(),
        setup_profile.finalization_nanoseconds(),
        setup_profile.total_nanoseconds(),
        plan_profile.total_nanoseconds(),
        batch_profile.validation_nanoseconds(),
        batch_profile.gather_nanoseconds(),
        batch_profile.solve_nanoseconds(),
        batch_profile.scatter_nanoseconds(),
        batch_profile.result_construction_nanoseconds(),
        pcg_profile.matvec().nanoseconds(),
        pcg_profile.preconditioner().nanoseconds(),
        pcg_profile.residual_recompute().nanoseconds(),
        pcg_profile.certification().nanoseconds(),
        pcg_profile.total_nanoseconds(),
    )?;

    if let Some(output) = config.output {
        std::fs::write(output, &receipt)?;
    }
    print!("{receipt}");
    Ok(())
}
