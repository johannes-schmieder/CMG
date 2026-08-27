use cmg::{
    CmgOptions, CmgPreconditioner, Laplacian, ParallelCmgPlan, ParallelExecutor, ParallelOptions,
    PcgOptions, PcgResult, PcgWorkspace, profile_pcg_with_plan, solve_pcg_batch_with_executor,
    solve_pcg_with_plan_and_workspace, solve_pcg_with_workspace,
};
use std::env;
use std::error::Error;
use std::fs::{self, File};
use std::hint::black_box;
use std::io::{self, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::OnceLock;
use std::time::{Duration, Instant};

type AnyError = Box<dyn Error>;
type EdgeSpec = (usize, usize, f64);

const PROTOCOL_VERSION: &str = "cmg-scc2-v1";
const GRAPH_MAGIC: &[u8; 8] = b"CMGGRPH1";
const VECTOR_MAGIC: &[u8; 8] = b"CMGVEC01";
const SOURCE_COMMIT: &str = match option_env!("CMG_BENCH_COMMIT") {
    Some(value) => value,
    None => "unknown",
};
const SOURCE_ARCHIVE_SHA256: &str = match option_env!("CMG_BENCH_ARCHIVE_SHA256") {
    Some(value) => value,
    None => "unknown",
};
const APPLY_TARGET: Duration = Duration::from_secs(2);
static CLOCK_TICKS: OnceLock<u64> = OnceLock::new();

#[derive(Debug)]
struct Sample {
    repetition: usize,
    measured: bool,
    order_position: usize,
    started_at_utc: String,
    stage: &'static str,
    wall_ns: u128,
    process_cpu_ns: Option<u128>,
}

#[derive(Default)]
struct Recorder {
    samples: Vec<Sample>,
    next_order: usize,
}

impl Recorder {
    fn marker(&mut self, repetition: usize, measured: bool, stage: &'static str) {
        self.next_order += 1;
        self.samples.push(Sample {
            repetition,
            measured,
            order_position: self.next_order,
            started_at_utc: utc_now(),
            stage,
            wall_ns: 0,
            process_cpu_ns: Some(0),
        });
    }

    fn measure<T, F>(
        &mut self,
        repetition: usize,
        measured: bool,
        stage: &'static str,
        operation: F,
    ) -> Result<T, AnyError>
    where
        F: FnOnce() -> Result<T, AnyError>,
    {
        let started_at_utc = utc_now();
        let cpu_start = process_cpu_ticks();
        let wall_start = Instant::now();
        let output = operation()?;
        let wall_ns = wall_start.elapsed().as_nanos();
        let cpu_end = process_cpu_ticks();
        let process_cpu_ns = match (cpu_start, cpu_end) {
            (Some(start), Some(end)) => Some(
                u128::from(end.saturating_sub(start)).saturating_mul(1_000_000_000)
                    / u128::from(clock_ticks()),
            ),
            _ => None,
        };
        self.next_order += 1;
        self.samples.push(Sample {
            repetition,
            measured,
            order_position: self.next_order,
            started_at_utc,
            stage,
            wall_ns,
            process_cpu_ns,
        });
        Ok(output)
    }

    fn derived_total(&mut self, repetition: usize, measured: bool, first: usize) {
        let chosen = &self.samples[first..];
        let wall_ns = chosen.iter().map(|sample| sample.wall_ns).sum();
        let process_cpu_ns = chosen.iter().try_fold(0_u128, |sum, sample| {
            sample.process_cpu_ns.map(|value| sum + value)
        });
        self.next_order += 1;
        self.samples.push(Sample {
            repetition,
            measured,
            order_position: self.next_order,
            started_at_utc: chosen
                .first()
                .map_or_else(utc_now, |sample| sample.started_at_utc.clone()),
            stage: "solver_total",
            wall_ns,
            process_cpu_ns,
        });
    }
}

fn main() -> Result<(), AnyError> {
    let mut arguments = env::args().skip(1);
    match arguments.next().as_deref() {
        Some("identity") => {
            let output = PathBuf::from(required(&mut arguments, "output")?);
            reject_extra(arguments)?;
            write_identity(&output)
        }
        Some("run") => {
            let input = PathBuf::from(required(&mut arguments, "input-dir")?);
            let hierarchy_threads = parse_positive(
                required(&mut arguments, "hierarchy-threads")?,
                "hierarchy-threads",
            )?;
            let plan_threads =
                parse_positive(required(&mut arguments, "plan-threads")?, "plan-threads")?;
            let solve_threads =
                parse_positive(required(&mut arguments, "solve-threads")?, "solve-threads")?;
            let strategy = required(&mut arguments, "strategy")?;
            let variant = required(&mut arguments, "variant")?;
            let rhs_count = parse_positive(required(&mut arguments, "rhs-count")?, "rhs-count")?;
            let tolerance = required(&mut arguments, "tolerance")?.parse::<f64>()?;
            let warmups = required(&mut arguments, "warmups")?.parse::<usize>()?;
            let repetitions = parse_positive(required(&mut arguments, "repetitions")?, "repetitions")?;
            let output = PathBuf::from(required(&mut arguments, "output")?);
            reject_extra(arguments)?;
            run(
                &input,
                hierarchy_threads,
                plan_threads,
                solve_threads,
                &strategy,
                &variant,
                rhs_count,
                tolerance,
                warmups,
                repetitions,
                &output,
            )
        }
        _ => Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "usage: scc2-diagnostics identity OUTPUT | run INPUT HIERARCHY_THREADS PLAN_THREADS SOLVE_THREADS serial|planned|across-rhs|auto VARIANT RHS_COUNT TOLERANCE WARMUPS REPETITIONS OUTPUT",
        )
        .into()),
    }
}

fn write_identity(output: &Path) -> Result<(), AnyError> {
    let executable = env::current_exe()?;
    let binary_sha256 = sha256sum(&executable)?;
    let rustc = Command::new("rustc").arg("--version").output()?;
    let rustc = String::from_utf8_lossy(&rustc.stdout).trim().to_owned();
    let features = "parallel,profiling";
    let target = format!("{}-{}", env::consts::ARCH, env::consts::OS);
    let payload = format!(
        "{{\n  \"protocol_version\":{},\n  \"source_commit\":{},\n  \"source_archive_sha256\":{},\n  \"binary_sha256\":{},\n  \"rustc\":{},\n  \"features\":{},\n  \"target\":{}\n}}\n",
        json_string(PROTOCOL_VERSION),
        json_string(SOURCE_COMMIT),
        json_string(SOURCE_ARCHIVE_SHA256),
        json_string(&binary_sha256),
        json_string(&rustc),
        json_string(features),
        json_string(&target),
    );
    atomic_write(output, payload.as_bytes())?;
    println!("CMG_SCC2_IDENTITY_SUCCESS binary_sha256={binary_sha256}");
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn run(
    input: &Path,
    hierarchy_threads: usize,
    plan_threads: usize,
    solve_threads: usize,
    strategy: &str,
    variant: &str,
    rhs_count: usize,
    tolerance: f64,
    warmups: usize,
    repetitions: usize,
    output: &Path,
) -> Result<(), AnyError> {
    if !matches!(strategy, "serial" | "planned" | "across-rhs" | "auto") {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid strategy").into());
    }
    if !matches!(
        variant,
        "fresh-all" | "reuse-hierarchy" | "reuse-plan" | "reuse-workspace" | "serial-no-plan"
    ) {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid variant").into());
    }
    if !tolerance.is_finite() || tolerance <= 0.0 {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid tolerance").into());
    }
    let load_start = Instant::now();
    let (vertices, edges) = read_graph(&input.join("graph.bin"))?;
    let right_hand_sides = read_vectors(&input.join("rhs.bin"))?;
    let truths = read_vectors(&input.join("truth.bin"))?;
    let metadata_path = input.join("metadata.json");
    let family = read_family(&metadata_path)?;
    let connected = read_metadata_usize(&metadata_path, "connected_components")? == 1;
    let input_load_ns = load_start.elapsed().as_nanos();
    if rhs_count > right_hand_sides.len() || rhs_count > truths.len() {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "fixture has too few RHSs").into());
    }
    let graph_start = Instant::now();
    let graph = Laplacian::from_edges(vertices, edges.iter().copied())?;
    let graph_build_ns = graph_start.elapsed().as_nanos();
    let hierarchy_executor = ParallelExecutor::new(ParallelOptions {
        threads: hierarchy_threads,
        min_parallel_len: 16_384,
        ..ParallelOptions::default()
    })?;
    let plan_executor = ParallelExecutor::new(ParallelOptions {
        threads: plan_threads,
        min_parallel_len: 16_384,
        ..ParallelOptions::default()
    })?;
    let solve_executor = ParallelExecutor::new(ParallelOptions {
        threads: solve_threads,
        min_parallel_len: 16_384,
        ..ParallelOptions::default()
    })?;
    let cmg_options = CmgOptions::default();
    let pcg_options = PcgOptions {
        relative_tolerance: tolerance,
        max_iterations: 1_000,
        ..PcgOptions::default()
    };
    let selected_rhs = &right_hand_sides[..rhs_count];

    let mut recorder = Recorder::default();
    let reuse_hierarchy = matches!(
        variant,
        "reuse-hierarchy" | "reuse-plan" | "reuse-workspace" | "serial-no-plan"
    );
    let reuse_plan = matches!(variant, "reuse-plan" | "reuse-workspace");
    let reuse_workspace = variant == "reuse-workspace";
    let omit_plan = variant == "serial-no-plan";
    let reused_preconditioner = if reuse_hierarchy {
        Some(
            recorder.measure(0, false, "retained_preconditioner_setup", || {
                Ok(Box::new(CmgPreconditioner::build_with_executor(
                    &graph,
                    cmg_options,
                    &hierarchy_executor,
                )?))
            })?,
        )
    } else {
        None
    };
    let reused_plan = if reuse_plan {
        Some(
            recorder.measure(0, false, "retained_parallel_plan_setup", || {
                Ok(Box::new(ParallelCmgPlan::build(
                    reused_preconditioner
                        .as_deref()
                        .ok_or_else(|| io::Error::other("plan reuse needs a hierarchy"))?,
                    &plan_executor,
                )?))
            })?,
        )
    } else {
        None
    };
    let mut reused_workspaces = if reuse_workspace {
        Some(
            recorder.measure(0, false, "retained_workspace_allocation", || {
                let preconditioner = reused_preconditioner
                    .as_deref()
                    .ok_or_else(|| io::Error::other("workspace reuse needs a hierarchy"))?;
                Ok((0..1)
                    .map(|_| PcgWorkspace::new(preconditioner))
                    .collect::<Vec<_>>())
            })?,
        )
    } else {
        None
    };
    let mut retained_preconditioner = None;
    let mut retained_plan = None;
    let mut final_results: Vec<PcgResult> = Vec::new();
    for sequence in 0..(warmups + repetitions) {
        let measured = sequence >= warmups;
        let repetition = if measured {
            sequence - warmups + 1
        } else {
            sequence + 1
        };
        let first = recorder.samples.len();
        let fresh_preconditioner = if reuse_hierarchy {
            recorder.marker(repetition, measured, "preconditioner_setup_reused");
            None
        } else {
            Some(
                recorder.measure(repetition, measured, "preconditioner_setup", || {
                    Ok(Box::new(CmgPreconditioner::build_with_executor(
                        &graph,
                        cmg_options,
                        &hierarchy_executor,
                    )?))
                })?,
            )
        };
        let preconditioner = fresh_preconditioner
            .as_deref()
            .or(reused_preconditioner.as_deref())
            .ok_or_else(|| io::Error::other("missing preconditioner"))?;
        let fresh_plan = if omit_plan {
            recorder.marker(repetition, measured, "parallel_plan_setup_omitted");
            None
        } else if reuse_plan {
            recorder.marker(repetition, measured, "parallel_plan_setup_reused");
            None
        } else {
            Some(
                recorder.measure(repetition, measured, "parallel_plan_setup", || {
                    Ok(Box::new(ParallelCmgPlan::build(
                        preconditioner,
                        &plan_executor,
                    )?))
                })?,
            )
        };
        let plan = fresh_plan.as_deref().or(reused_plan.as_deref());
        let mut fresh_workspaces = if reuse_workspace {
            recorder.marker(repetition, measured, "workspace_allocation_reused");
            None
        } else {
            Some(
                recorder.measure(repetition, measured, "workspace_allocation", || {
                    Ok((0..1)
                        .map(|_| PcgWorkspace::new(preconditioner))
                        .collect::<Vec<_>>())
                })?,
            )
        };
        let workspaces = fresh_workspaces
            .as_deref_mut()
            .or(reused_workspaces.as_deref_mut())
            .ok_or_else(|| io::Error::other("missing workspace"))?;
        let results = recorder.measure(repetition, measured, "pcg_solve", || {
            solve_strategy(
                strategy,
                &graph,
                preconditioner,
                plan,
                selected_rhs,
                pcg_options,
                workspaces,
                &solve_executor,
                connected,
            )
        })?;
        require_results(&results, tolerance)?;
        recorder.derived_total(repetition, measured, first);
        if measured {
            final_results = results;
            if let Some(value) = fresh_preconditioner {
                retained_preconditioner = Some(value);
            }
            if let Some(value) = fresh_plan {
                retained_plan = Some(value);
            }
        }
    }
    let preconditioner = retained_preconditioner
        .as_deref()
        .or(reused_preconditioner.as_deref())
        .ok_or_else(|| io::Error::other("no measured preconditioner"))?;
    let plan = retained_plan.as_deref().or(reused_plan.as_deref());
    let plan_operator_count = plan.map_or(0, ParallelCmgPlan::operator_count);
    let plan_bytes = plan.map_or(0, ParallelCmgPlan::byte_len);
    let actual_strategy = actual_strategy(
        strategy,
        rhs_count,
        &graph,
        plan,
        &solve_executor,
        connected,
    );
    let route_reason = route_reason(
        strategy,
        rhs_count,
        &graph,
        plan,
        &solve_executor,
        connected,
    );

    let mut apply_loops = 0_usize;
    if rhs_count == 1 {
        let mut workspace = preconditioner.workspace();
        let mut result = vec![0.0; vertices];
        apply_loops = calibrate_apply(
            preconditioner,
            plan,
            actual_strategy,
            &solve_executor,
            &selected_rhs[0],
            &mut result,
            &mut workspace,
        )?;
        for repetition in 1..=repetitions {
            recorder.measure(repetition, true, "preconditioner_apply", || {
                for _ in 0..apply_loops {
                    apply_strategy(
                        preconditioner,
                        plan,
                        actual_strategy,
                        &selected_rhs[0],
                        &mut result,
                        &mut workspace,
                        &solve_executor,
                    )?;
                    black_box(&result);
                }
                Ok(())
            })?;
        }
    }

    let mut phase_records = Vec::new();
    let mut plan_levels_json = "[]".to_owned();
    for repetition in 1..=repetitions {
        let (profiled_preconditioner, setup_profile) =
            CmgPreconditioner::build_with_executor_profiled(
                &graph,
                cmg_options,
                &hierarchy_executor,
            )?;
        if profiled_preconditioner != *preconditioner {
            return Err(io::Error::other(
                "profiled hierarchy differs from the production hierarchy",
            )
            .into());
        }
        phase_records.push((
            repetition,
            None,
            "production_hierarchy",
            setup_profile.hierarchy_nanoseconds(),
            1,
        ));
        phase_records.push((
            repetition,
            None,
            "preconditioner_finalization",
            setup_profile.finalization_nanoseconds(),
            1,
        ));
        phase_records.push((
            repetition,
            None,
            "complete_preconditioner",
            setup_profile.total_nanoseconds(),
            1,
        ));
        for phase in setup_profile.hierarchy_phases() {
            phase_records.push((
                repetition,
                Some(phase.level()),
                phase.phase(),
                phase.nanoseconds(),
                1,
            ));
        }
        if !omit_plan {
            let (profiled_plan, plan_profile) =
                ParallelCmgPlan::build_profiled(preconditioner, &plan_executor)?;
            if profiled_plan.operator_count() != plan_operator_count
                || profiled_plan.byte_len() != plan_bytes
            {
                return Err(
                    io::Error::other("profiled plan differs from the production plan").into(),
                );
            }
            phase_records.push((
                repetition,
                None,
                "parallel_plan_complete",
                plan_profile.total_nanoseconds(),
                1,
            ));
            if repetition == 1 {
                plan_levels_json = format!(
                    "[{}]",
                    plan_profile
                        .levels()
                        .iter()
                        .map(|level| format!(
                            "{{\"level\":{},\"vertices\":{},\"edges\":{},\"eligible\":{},\"reason\":{},\"retained_bytes\":{},\"row_counts_ns\":{},\"row_offsets_ns\":{},\"allocation_ns\":{},\"scatter_ns\":{},\"validation_ns\":{}}}",
                            level.level(), level.vertices(), level.edges(), level.eligible(),
                            json_string(level.reason()), level.retained_bytes(),
                            level.row_counts_nanoseconds(), level.row_offsets_nanoseconds(),
                            level.allocation_nanoseconds(), level.scatter_nanoseconds(),
                            level.validation_nanoseconds(),
                        ))
                        .collect::<Vec<_>>()
                        .join(",")
                );
            }
            for level in plan_profile.levels() {
                phase_records.push((
                    repetition,
                    Some(level.level()),
                    if level.eligible() {
                        "parallel_plan_level_eligible"
                    } else {
                        level.reason()
                    },
                    level.construction_nanoseconds(),
                    1,
                ));
                for (phase, nanoseconds) in [
                    ("parallel_plan_row_counts", level.row_counts_nanoseconds()),
                    ("parallel_plan_row_offsets", level.row_offsets_nanoseconds()),
                    ("parallel_plan_allocation", level.allocation_nanoseconds()),
                    ("parallel_plan_edge_scatter", level.scatter_nanoseconds()),
                    ("parallel_plan_validation", level.validation_nanoseconds()),
                ] {
                    phase_records.push((repetition, Some(level.level()), phase, nanoseconds, 1));
                }
            }
        }
    }
    if rhs_count == 1 && actual_strategy == "planned" && plan.is_some() {
        for repetition in 1..=repetitions {
            let profiled = profile_pcg_with_plan(
                &graph,
                preconditioner,
                plan.ok_or_else(|| io::Error::other("profile needs a plan"))?,
                &selected_rhs[0],
                pcg_options,
                &solve_executor,
            )?;
            let reference = final_results
                .first()
                .ok_or_else(|| io::Error::other("missing unprofiled PCG reference"))?;
            if profiled.iterations() != reference.iterations()
                || profiled.restarts() != reference.restarts()
                || profiled.backward_error().to_bits() != reference.backward_error().to_bits()
            {
                return Err(io::Error::other(
                    "profiled PCG certificate differs from the production solve",
                )
                .into());
            }
            let profile = profiled.profile();
            phase_records.push((
                repetition,
                None,
                "outer_setup",
                profile.setup().nanoseconds(),
                profile.setup().calls(),
            ));
            phase_records.push((
                repetition,
                None,
                "preconditioner",
                profile.preconditioner().nanoseconds(),
                profile.preconditioner().calls(),
            ));
            phase_records.push((
                repetition,
                None,
                "finest_matvec",
                profile.matvec().nanoseconds(),
                profile.matvec().calls(),
            ));
            phase_records.push((
                repetition,
                None,
                "dot_products",
                profile.dot_products().nanoseconds(),
                profile.dot_products().calls(),
            ));
            phase_records.push((
                repetition,
                None,
                "vector_updates",
                profile.vector_updates().nanoseconds(),
                profile.vector_updates().calls(),
            ));
            phase_records.push((
                repetition,
                None,
                "centering_projection",
                profile.centering().nanoseconds(),
                profile.centering().calls(),
            ));
            phase_records.push((
                repetition,
                None,
                "norms",
                profile.norms().nanoseconds(),
                profile.norms().calls(),
            ));
            phase_records.push((
                repetition,
                None,
                "residual_recompute",
                profile.residual_recompute().nanoseconds(),
                profile.residual_recompute().calls(),
            ));
            phase_records.push((
                repetition,
                None,
                "certification",
                profile.certification().nanoseconds(),
                profile.certification().calls(),
            ));
            phase_records.push((
                repetition,
                None,
                "unattributed",
                profile.unattributed_nanoseconds(),
                1,
            ));
        }
    }

    let numerical =
        numerical_diagnostics(&graph, &final_results, selected_rhs, &truths[..rhs_count]);
    let level_vertices: Vec<_> = preconditioner
        .hierarchy()
        .levels()
        .iter()
        .map(|level| level.graph().vertex_count())
        .collect();
    let level_nonzeros: Vec<_> = preconditioner
        .hierarchy()
        .levels()
        .iter()
        .map(|level| level.graph().matrix_nnz())
        .collect();
    let level_repeats = preconditioner.repeat_counts().to_vec();
    let hierarchy_bytes = hierarchy_bytes(preconditioner);
    let terminal_bytes = preconditioner
        .terminal_factor()
        .map_or(0, |factor| factor.byte_len());
    let workspace_bytes = PcgWorkspace::new(preconditioner).byte_len();
    let workspace_concurrency = if actual_strategy == "across-rhs" {
        solve_executor.batch_concurrency(workspace_bytes, rhs_count)?
    } else {
        1
    };
    let binary_sha256 = sha256sum(&env::current_exe()?)?;
    let run_id = env::var("CMG_RUN_ID").unwrap_or_else(|_| "local".to_owned());
    let task_id = env::var("CMG_TASK_ID")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(1);
    let environment_id = env::var("CMG_ENVIRONMENT_ID").unwrap_or_else(|_| "unknown".to_owned());
    let experiment = env::var("CMG_EXPERIMENT").unwrap_or_else(|_| "diagnostic".to_owned());
    let placement = env::var("CMG_PLACEMENT").unwrap_or_else(|_| "current".to_owned());
    let cpu_list = env::var("CMG_CPU_LIST").unwrap_or_default();
    let socket_list = env::var("CMG_SOCKET_LIST").unwrap_or_default();
    let numa_list = env::var("CMG_NUMA_LIST").unwrap_or_default();
    let memory_policy = env::var("CMG_MEMORY_POLICY").unwrap_or_else(|_| "current".to_owned());
    let first_touch = env::var("CMG_FIRST_TOUCH").unwrap_or_else(|_| "current".to_owned());
    let payload = format!(
        concat!(
            "{{\n",
            "  \"protocol_version\":{},\n",
            "  \"run_id\":{},\n",
            "  \"task_id\":{},\n",
            "  \"source_commit\":{},\n",
            "  \"source_archive_sha256\":{},\n",
            "  \"binary_sha256\":{},\n",
            "  \"environment_id\":{},\n",
            "  \"implementation\":\"rust\",\n",
            "  \"experiment\":{},\n",
            "  \"family\":{},\n",
            "  \"vertices\":{},\n",
            "  \"canonical_edges\":{},\n",
            "  \"matrix_nonzeros\":{},\n",
            "  \"strategy\":{},\n",
            "  \"actual_strategy\":{},\n",
            "  \"route_reason\":{},\n",
            "  \"variant\":{},\n",
            "  \"hierarchy_threads\":{},\n",
            "  \"plan_threads\":{},\n",
            "  \"solve_threads\":{},\n",
            "  \"rhs_count\":{},\n",
            "  \"workspace_concurrency\":{},\n",
            "  \"warmups\":{},\n",
            "  \"repetitions\":{},\n",
            "  \"tolerance\":{:.17e},\n",
            "  \"max_iterations\":1000,\n",
            "  \"input_load_ns\":{},\n",
            "  \"graph_assembly_ns\":{},\n",
            "  \"apply_loops\":{},\n",
            "  \"samples\":{},\n",
            "  \"phases\":{},\n",
            "  \"plan_levels\":{},\n",
            "  \"hierarchy\":{{\"levels\":{},\"vertices\":{},\"matrix_nonzeros\":{},\"repeats\":{},\"terminal_reason\":{},\"plan_operator_count\":{}}},\n",
            "  \"numerical\":{},\n",
            "  \"memory\":{{\"graph_bytes\":{},\"hierarchy_bytes\":{},\"terminal_factor_bytes\":{},\"plan_bytes\":{},\"workspace_bytes_each\":{},\"workspace_pool_bytes\":{},\"peak_rss_kb\":{}}},\n",
            "  \"placement\":{{\"mode\":{},\"cpu_list\":{},\"socket_list\":{},\"numa_node_list\":{},\"memory_policy\":{},\"first_touch_policy\":{}}},\n",
            "  \"warnings\":[],\n",
            "  \"success\":true\n",
            "}}\n"
        ),
        json_string(PROTOCOL_VERSION),
        json_string(&run_id),
        task_id,
        json_string(SOURCE_COMMIT),
        json_string(SOURCE_ARCHIVE_SHA256),
        json_string(&binary_sha256),
        json_string(&environment_id),
        json_string(&experiment),
        json_string(&family),
        vertices,
        graph.edge_count(),
        graph.matrix_nnz(),
        json_string(strategy),
        json_string(actual_strategy),
        json_string(route_reason),
        json_string(variant),
        hierarchy_threads,
        plan_threads,
        solve_threads,
        rhs_count,
        workspace_concurrency,
        warmups,
        repetitions,
        tolerance,
        input_load_ns,
        graph_build_ns,
        apply_loops,
        samples_json(&recorder.samples, apply_loops),
        phases_json(&phase_records),
        plan_levels_json,
        level_vertices.len(),
        usize_json(&level_vertices),
        usize_json(&level_nonzeros),
        usize_json(&level_repeats),
        json_string(&format!(
            "{:?}",
            preconditioner.hierarchy().report().terminal_reason()
        )),
        plan_operator_count,
        numerical,
        graph_bytes(&graph),
        hierarchy_bytes,
        terminal_bytes,
        plan_bytes,
        workspace_bytes,
        workspace_bytes.saturating_mul(workspace_concurrency),
        peak_rss_kb(),
        json_string(&placement),
        json_string(&cpu_list),
        json_string(&socket_list),
        json_string(&numa_list),
        json_string(&memory_policy),
        json_string(&first_touch),
    );
    atomic_write(output, payload.as_bytes())?;
    println!(
        "CMG_SCC2_RUST_SUCCESS family={family} vertices={vertices} hierarchy_threads={hierarchy_threads} plan_threads={plan_threads} solve_threads={solve_threads} strategy={strategy} rhs={rhs_count}"
    );
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn solve_strategy(
    strategy: &str,
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    plan: Option<&ParallelCmgPlan>,
    rhs: &[Vec<f64>],
    options: PcgOptions,
    workspaces: &mut [PcgWorkspace],
    executor: &ParallelExecutor,
    connected: bool,
) -> Result<Vec<PcgResult>, AnyError> {
    let actual = actual_strategy(strategy, rhs.len(), graph, plan, executor, connected);
    match actual {
        "serial" => rhs
            .iter()
            .map(|vector| {
                solve_pcg_with_workspace(graph, preconditioner, vector, options, &mut workspaces[0])
                    .map_err(Into::into)
            })
            .collect(),
        "planned" => rhs
            .iter()
            .map(|vector| {
                solve_pcg_with_plan_and_workspace(
                    graph,
                    preconditioner,
                    plan.ok_or_else(|| io::Error::other("planned solve needs a plan"))?,
                    vector,
                    options,
                    &mut workspaces[0],
                    executor,
                )
                .map_err(Into::into)
            })
            .collect(),
        "across-rhs" => Ok(solve_pcg_batch_with_executor(
            graph,
            preconditioner,
            rhs,
            options,
            executor,
        )?),
        _ => unreachable!(),
    }
}

fn actual_strategy<'a>(
    strategy: &'a str,
    rhs_count: usize,
    graph: &Laplacian,
    plan: Option<&ParallelCmgPlan>,
    executor: &ParallelExecutor,
    connected: bool,
) -> &'a str {
    if strategy != "auto" {
        return strategy;
    }
    if rhs_count > 1 && executor.thread_count() > 1 {
        "across-rhs"
    } else if executor.thread_count() > 1
        && plan.is_some_and(|value| {
            value.operator_count() > 0 || eligible_parallel_vector_work(graph, executor, connected)
        })
        && graph.edge_count() >= 350_000
    {
        "planned"
    } else {
        "serial"
    }
}

fn route_reason<'a>(
    strategy: &'a str,
    rhs_count: usize,
    graph: &Laplacian,
    plan: Option<&ParallelCmgPlan>,
    executor: &ParallelExecutor,
    connected: bool,
) -> &'a str {
    if strategy != "auto" {
        return "forced-by-benchmark";
    }
    if rhs_count > 1 && executor.thread_count() > 1 {
        "multiple-rhs-and-multiple-workers"
    } else if executor.thread_count() <= 1 {
        "single-worker"
    } else if graph.edge_count() < 350_000 {
        "below-minimum-planned-edges"
    } else if plan.is_none() {
        "parallel-plan-unavailable"
    } else if plan.is_some_and(|value| value.operator_count() > 0) {
        "eligible-planned-operator-single-rhs"
    } else if eligible_parallel_vector_work(graph, executor, connected) {
        "eligible-planned-vector-single-rhs"
    } else {
        "no-eligible-planned-work"
    }
}

fn eligible_parallel_vector_work(
    graph: &Laplacian,
    executor: &ParallelExecutor,
    connected: bool,
) -> bool {
    let options = executor.options();
    let vector_floor = options
        .min_parallel_len
        .max(options.reduction_chunk_size.saturating_mul(8));
    connected && graph.vertex_count() >= vector_floor
}

fn require_results(results: &[PcgResult], tolerance: f64) -> Result<(), AnyError> {
    if results.is_empty()
        || results.iter().any(|result| {
            !result.backward_error().is_finite() || result.backward_error() > tolerance * 1.1
        })
    {
        return Err(
            io::Error::other("PCG result failed independent certification threshold").into(),
        );
    }
    Ok(())
}

fn numerical_diagnostics(
    graph: &Laplacian,
    results: &[PcgResult],
    rhs: &[Vec<f64>],
    truths: &[Vec<f64>],
) -> String {
    let mut rows = Vec::new();
    for ((result, right_hand_side), truth) in results.iter().zip(rhs).zip(truths) {
        let matvec = graph
            .matvec(result.solution())
            .expect("certified graph matvec");
        let residual: Vec<_> = right_hand_side
            .iter()
            .zip(matvec)
            .map(|(left, right)| left - right)
            .collect();
        let residual_norm = norm(&residual);
        let rhs_norm = norm(right_hand_side);
        let relative = residual_norm / rhs_norm.max(f64::MIN_POSITIVE);
        let backward = residual_norm
            / (rhs_norm + graph.operator_norm_bound() * norm(result.solution()))
                .max(f64::MIN_POSITIVE);
        let centered = centered_error(result.solution(), truth);
        let energy = energy_error(graph, result.solution(), truth);
        rows.push(format!(
            "{{\"iterations\":{},\"restarts\":{},\"native_relative_residual\":{:.17e},\"independent_relative_residual\":{relative:.17e},\"backward_error\":{backward:.17e},\"reference_scaled_error\":{centered:.17e},\"energy_norm_error\":{energy:.17e}}}",
            result.iterations(), result.restarts(), result.relative_residual()
        ));
    }
    format!("{{\"all_rhs\":[{}],\"converged\":true}}", rows.join(","))
}

fn centered_error(solution: &[f64], truth: &[f64]) -> f64 {
    let solution_mean = solution.iter().sum::<f64>() / solution.len().max(1) as f64;
    let truth_mean = truth.iter().sum::<f64>() / truth.len().max(1) as f64;
    solution
        .iter()
        .zip(truth)
        .map(|(left, right)| {
            let left = left - solution_mean;
            let right = right - truth_mean;
            (left - right).abs() / (1.0 + left.abs().max(right.abs()))
        })
        .fold(0.0, f64::max)
}

fn energy_error(graph: &Laplacian, solution: &[f64], truth: &[f64]) -> f64 {
    let error: Vec<_> = solution
        .iter()
        .zip(truth)
        .map(|(left, right)| left - right)
        .collect();
    let numerator = graph.energy(&error).unwrap_or(f64::NAN).max(0.0).sqrt();
    let denominator = graph.energy(truth).unwrap_or(f64::NAN).max(0.0).sqrt();
    numerator / denominator.max(f64::MIN_POSITIVE)
}

fn norm(values: &[f64]) -> f64 {
    values.iter().map(|value| value * value).sum::<f64>().sqrt()
}

fn calibrate_apply(
    preconditioner: &CmgPreconditioner,
    plan: Option<&ParallelCmgPlan>,
    strategy: &str,
    executor: &ParallelExecutor,
    rhs: &[f64],
    output: &mut [f64],
    workspace: &mut cmg::CmgWorkspace,
) -> Result<usize, AnyError> {
    let mut loops = 1;
    loop {
        let start = Instant::now();
        for _ in 0..loops {
            apply_strategy(
                preconditioner,
                plan,
                strategy,
                rhs,
                output,
                workspace,
                executor,
            )?;
        }
        if start.elapsed() >= APPLY_TARGET || loops >= 1_048_576 {
            return Ok(loops);
        }
        loops *= 2;
    }
}

fn apply_strategy(
    preconditioner: &CmgPreconditioner,
    plan: Option<&ParallelCmgPlan>,
    strategy: &str,
    rhs: &[f64],
    output: &mut [f64],
    workspace: &mut cmg::CmgWorkspace,
    executor: &ParallelExecutor,
) -> Result<(), AnyError> {
    if matches!(strategy, "serial") || plan.is_none() {
        preconditioner.apply_compatible_into(rhs, output, workspace)?;
    } else {
        plan.ok_or_else(|| io::Error::other("planned apply needs a plan"))?
            .apply_compatible_into(preconditioner, rhs, output, workspace, executor)?;
    }
    Ok(())
}

fn samples_json(samples: &[Sample], apply_loops: usize) -> String {
    let rows = samples
        .iter()
        .map(|sample| {
            let normalized_wall = if sample.stage == "preconditioner_apply" && apply_loops > 0 { sample.wall_ns / apply_loops as u128 } else { sample.wall_ns };
            let normalized_cpu = sample.process_cpu_ns.map(|value| if sample.stage == "preconditioner_apply" && apply_loops > 0 { value / apply_loops as u128 } else { value });
            format!(
                "{{\"repetition\":{},\"measured\":{},\"order_position\":{},\"started_at_utc\":{},\"stage\":{},\"wall_ns\":{},\"process_cpu_ns\":{}}}",
                sample.repetition, sample.measured, sample.order_position, json_string(&sample.started_at_utc), json_string(sample.stage), normalized_wall,
                normalized_cpu.map_or_else(|| "null".to_owned(), |value| value.to_string())
            )
        })
        .collect::<Vec<_>>();
    format!("[{}]", rows.join(","))
}

fn phases_json(phases: &[(usize, Option<usize>, &'static str, u128, usize)]) -> String {
    format!(
        "[{}]",
        phases
            .iter()
            .map(|(repetition, level, phase, wall_ns, calls)| {
                let level = level.map_or_else(|| "null".to_owned(), |value| value.to_string());
                format!("{{\"repetition\":{repetition},\"level\":{level},\"phase\":{},\"wall_ns\":{wall_ns},\"process_cpu_ns\":null,\"calls\":{calls}}}", json_string(phase))
            })
            .collect::<Vec<_>>()
            .join(",")
    )
}

fn hierarchy_bytes(preconditioner: &CmgPreconditioner) -> usize {
    preconditioner
        .hierarchy()
        .levels()
        .iter()
        .map(|level| {
            graph_bytes(level.graph())
                + std::mem::size_of_val(level.inverse_diagonal())
                + level.aggregation().map_or(0, |aggregation| {
                    std::mem::size_of_val(aggregation.labels())
                        + std::mem::size_of_val(aggregation.sizes())
                })
        })
        .sum::<usize>()
        + preconditioner.component_metadata_bytes()
}

fn graph_bytes(graph: &Laplacian) -> usize {
    graph.edge_count() * std::mem::size_of::<cmg::Edge>() + std::mem::size_of_val(graph.diagonal())
}

fn peak_rss_kb() -> u64 {
    fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|content| {
            content.lines().find_map(|line| {
                line.strip_prefix("VmHWM:")
                    .and_then(|value| value.split_whitespace().next())
                    .and_then(|value| value.parse().ok())
            })
        })
        .unwrap_or(0)
}

fn clock_ticks() -> u64 {
    *CLOCK_TICKS.get_or_init(|| {
        Command::new("getconf")
            .arg("CLK_TCK")
            .output()
            .ok()
            .and_then(|output| String::from_utf8(output.stdout).ok())
            .and_then(|value| value.trim().parse().ok())
            .unwrap_or(100)
    })
}

fn process_cpu_ticks() -> Option<u64> {
    let content = fs::read_to_string("/proc/self/stat").ok()?;
    let end = content.rfind(')')?;
    let fields: Vec<_> = content[(end + 2)..].split_whitespace().collect();
    let user = fields.get(11)?.parse::<u64>().ok()?;
    let system = fields.get(12)?.parse::<u64>().ok()?;
    Some(user.saturating_add(system))
}

fn utc_now() -> String {
    Command::new("date")
        .args(["-u", "+%Y-%m-%dT%H:%M:%S.%NZ"])
        .output()
        .ok()
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "unknown".to_owned())
}

fn sha256sum(path: &Path) -> Result<String, AnyError> {
    let output = Command::new("sha256sum").arg(path).output()?;
    if !output.status.success() {
        return Err(io::Error::other("sha256sum failed").into());
    }
    String::from_utf8(output.stdout)?
        .split_whitespace()
        .next()
        .map(str::to_owned)
        .ok_or_else(|| io::Error::other("sha256sum produced no digest").into())
}

fn read_graph(path: &Path) -> Result<(usize, Vec<EdgeSpec>), AnyError> {
    let mut reader = BufReader::new(File::open(path)?);
    let mut magic = [0_u8; 8];
    reader.read_exact(&mut magic)?;
    if &magic != GRAPH_MAGIC {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "invalid graph magic").into());
    }
    let vertices = usize::try_from(read_u64(&mut reader)?)?;
    let count = usize::try_from(read_u64(&mut reader)?)?;
    let mut edges = Vec::with_capacity(count);
    for _ in 0..count {
        let u = usize::try_from(read_u64(&mut reader)?)?;
        let v = usize::try_from(read_u64(&mut reader)?)?;
        let weight = f64::from_bits(read_u64(&mut reader)?);
        edges.push((u, v, weight));
    }
    Ok((vertices, edges))
}

fn read_vectors(path: &Path) -> Result<Vec<Vec<f64>>, AnyError> {
    let mut reader = BufReader::new(File::open(path)?);
    let mut magic = [0_u8; 8];
    reader.read_exact(&mut magic)?;
    if &magic != VECTOR_MAGIC {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "invalid vector magic").into());
    }
    let dimension = usize::try_from(read_u64(&mut reader)?)?;
    let count = usize::try_from(read_u64(&mut reader)?)?;
    let mut values = vec![vec![0.0; dimension]; count];
    for vector in &mut values {
        for value in vector {
            *value = f64::from_bits(read_u64(&mut reader)?);
        }
    }
    Ok(values)
}

fn read_u64(reader: &mut impl Read) -> Result<u64, io::Error> {
    let mut bytes = [0_u8; 8];
    reader.read_exact(&mut bytes)?;
    Ok(u64::from_le_bytes(bytes))
}

fn read_family(path: &Path) -> Result<String, AnyError> {
    let content = fs::read_to_string(path)?;
    let marker = "\"family\"";
    let start = content
        .find(marker)
        .ok_or_else(|| io::Error::other("missing family"))?;
    let tail = &content[(start + marker.len())..];
    let colon = tail
        .find(':')
        .ok_or_else(|| io::Error::other("invalid family"))?;
    let quoted = &tail[(colon + 1)..];
    let first = quoted
        .find('"')
        .ok_or_else(|| io::Error::other("invalid family"))?;
    let rest = &quoted[(first + 1)..];
    let second = rest
        .find('"')
        .ok_or_else(|| io::Error::other("invalid family"))?;
    Ok(rest[..second].to_owned())
}

fn read_metadata_usize(path: &Path, field: &str) -> Result<usize, AnyError> {
    let content = fs::read_to_string(path)?;
    let marker = format!("\"{field}\"");
    let start = content
        .find(&marker)
        .ok_or_else(|| io::Error::other(format!("missing {field}")))?;
    let tail = &content[(start + marker.len())..];
    let colon = tail
        .find(':')
        .ok_or_else(|| io::Error::other(format!("invalid {field}")))?;
    let digits = tail[(colon + 1)..].trim_start();
    let end = digits
        .find(|character: char| !character.is_ascii_digit())
        .unwrap_or(digits.len());
    if end == 0 {
        return Err(io::Error::other(format!("invalid {field}")).into());
    }
    Ok(digits[..end].parse()?)
}

fn required(arguments: &mut impl Iterator<Item = String>, name: &str) -> Result<String, AnyError> {
    arguments.next().ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidInput, format!("missing {name}")).into()
    })
}

fn parse_positive(value: String, name: &str) -> Result<usize, AnyError> {
    let value = value.parse::<usize>()?;
    if value == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{name} must be positive"),
        )
        .into());
    }
    Ok(value)
}

fn reject_extra(mut arguments: impl Iterator<Item = String>) -> Result<(), AnyError> {
    if let Some(value) = arguments.next() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("unexpected argument {value}"),
        )
        .into());
    }
    Ok(())
}

fn json_string(value: &str) -> String {
    let mut output = String::from("\"");
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            character if character.is_control() => {
                output.push_str(&format!("\\u{:04x}", character as u32))
            }
            character => output.push(character),
        }
    }
    output.push('"');
    output
}

fn usize_json(values: &[usize]) -> String {
    format!(
        "[{}]",
        values
            .iter()
            .map(usize::to_string)
            .collect::<Vec<_>>()
            .join(",")
    )
}

fn atomic_write(path: &Path, content: &[u8]) -> Result<(), io::Error> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temporary = path.with_extension("tmp");
    let mut file = File::create(&temporary)?;
    file.write_all(content)?;
    file.sync_all()?;
    fs::rename(temporary, path)
}
