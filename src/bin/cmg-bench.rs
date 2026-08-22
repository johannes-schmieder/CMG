use cmg::{
    CmgOptions, CmgPreconditioner, CsrLaplacian, Laplacian, PcgOptions, PcgWorkspace,
    solve_pcg_with_workspace,
};
use std::env;
use std::error::Error;
use std::fs;
use std::hint::black_box;
use std::io;
use std::mem::size_of_val;
use std::time::Instant;

const BENCHMARK_BASELINE_COMMIT: &str = "b45b252f88925028e3ad9a73a3f75eeab05f6754";
const TARGET_MATVEC_EDGE_VISITS: usize = 20_000_000;
const SOURCE_COMMIT: &str = match option_env!("CMG_BENCH_COMMIT") {
    Some(value) => value,
    None => "unknown",
};

#[derive(Debug, Clone)]
struct Config {
    case: String,
    vertices: usize,
    rhs_count: usize,
    repetitions: usize,
    direct_threshold: usize,
    output: Option<String>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            case: "worker-firm".to_owned(),
            vertices: 20_000,
            rhs_count: 4,
            repetitions: 3,
            direct_threshold: 700,
            output: None,
        }
    }
}

fn main() -> Result<(), Box<dyn Error>> {
    let config = parse_config()?;
    let logical_cpus = std::thread::available_parallelism()
        .map(usize::from)
        .unwrap_or(1);

    let generation_start = Instant::now();
    let raw_edges = generate_edges(&config.case, config.vertices)?;
    let generation_ns = generation_start.elapsed().as_nanos();

    let mut graph_build_ns = Vec::with_capacity(config.repetitions);
    let mut graph = None;
    for _ in 0..config.repetitions {
        let start = Instant::now();
        let candidate = Laplacian::from_edges(config.vertices, raw_edges.iter().copied())?;
        graph_build_ns.push(start.elapsed().as_nanos());
        graph = Some(candidate);
    }
    let graph = graph.ok_or_else(|| invalid_input("no graph was constructed"))?;

    let cmg_options = CmgOptions {
        direct_threshold: config.direct_threshold,
        ..CmgOptions::default()
    };
    let mut hierarchy_build_ns = Vec::with_capacity(config.repetitions);
    let mut preconditioner = None;
    for _ in 0..config.repetitions {
        let start = Instant::now();
        let candidate = CmgPreconditioner::build(&graph, cmg_options)?;
        hierarchy_build_ns.push(start.elapsed().as_nanos());
        preconditioner = Some(candidate);
    }
    let preconditioner =
        preconditioner.ok_or_else(|| invalid_input("no preconditioner was constructed"))?;

    let right_hand_sides = make_right_hand_sides(&graph, config.rhs_count)?;

    let mut cmg_workspace = preconditioner.workspace();
    let mut apply_output = vec![0.0; graph.vertex_count()];
    preconditioner.apply_into(&right_hand_sides[0], &mut apply_output, &mut cmg_workspace)?;
    black_box(&apply_output);

    let mut apply_ns = Vec::with_capacity(config.repetitions);
    for repetition in 0..config.repetitions {
        let rhs = &right_hand_sides[repetition % right_hand_sides.len()];
        let start = Instant::now();
        preconditioner.apply_into(rhs, &mut apply_output, &mut cmg_workspace)?;
        apply_ns.push(start.elapsed().as_nanos());
        black_box(&apply_output);
    }

    let pcg_options = PcgOptions::default();
    let mut pcg_workspace = PcgWorkspace::new(&preconditioner);
    let warmup = solve_pcg_with_workspace(
        &graph,
        &preconditioner,
        &right_hand_sides[0],
        pcg_options,
        &mut pcg_workspace,
    )?;
    black_box(warmup.solution());

    let mut solve_batch_ns = Vec::with_capacity(config.repetitions);
    let mut final_iterations = Vec::new();
    let mut final_backward_errors = Vec::new();
    for repetition in 0..config.repetitions {
        let start = Instant::now();
        let mut iterations = Vec::with_capacity(right_hand_sides.len());
        let mut backward_errors = Vec::with_capacity(right_hand_sides.len());
        for rhs in &right_hand_sides {
            let result = solve_pcg_with_workspace(
                &graph,
                &preconditioner,
                rhs,
                pcg_options,
                &mut pcg_workspace,
            )?;
            iterations.push(result.iterations());
            backward_errors.push(result.backward_error());
            black_box(result.solution());
        }
        solve_batch_ns.push(start.elapsed().as_nanos());
        if repetition + 1 == config.repetitions {
            final_iterations = iterations;
            final_backward_errors = backward_errors;
        }
    }

    let mut csr_build_ns = Vec::with_capacity(config.repetitions);
    let mut csr = None;
    for _ in 0..config.repetitions {
        let start = Instant::now();
        let candidate = CsrLaplacian::from_laplacian(&graph)?;
        csr_build_ns.push(start.elapsed().as_nanos());
        csr = Some(candidate);
    }
    let csr = csr.ok_or_else(|| invalid_input("no CSR operator was constructed"))?;
    let kernel_input: Vec<f64> = (0..graph.vertex_count())
        .map(|vertex| {
            let code = vertex.wrapping_mul(65_537).wrapping_add(19) % 4_093;
            (code as f64 - 2_046.0) / 257.0
        })
        .collect();
    let mut edge_output = vec![0.0; graph.vertex_count()];
    let mut csr_output = vec![0.0; graph.vertex_count()];
    graph.matvec_into(&kernel_input, &mut edge_output)?;
    csr.matvec_into(&kernel_input, &mut csr_output)?;
    validate_matvec_agreement(&edge_output, &csr_output)?;

    let matvec_loops = (TARGET_MATVEC_EDGE_VISITS / graph.edge_count().max(1)).clamp(8, 2_000);
    let mut edge_matvec_ns = Vec::with_capacity(config.repetitions);
    let mut csr_matvec_ns = Vec::with_capacity(config.repetitions);
    for repetition in 0..config.repetitions {
        if repetition % 2 == 0 {
            edge_matvec_ns.push(time_edge_matvec(
                &graph,
                &kernel_input,
                &mut edge_output,
                matvec_loops,
            )?);
            csr_matvec_ns.push(time_csr_matvec(
                &csr,
                &kernel_input,
                &mut csr_output,
                matvec_loops,
            )?);
        } else {
            csr_matvec_ns.push(time_csr_matvec(
                &csr,
                &kernel_input,
                &mut csr_output,
                matvec_loops,
            )?);
            edge_matvec_ns.push(time_edge_matvec(
                &graph,
                &kernel_input,
                &mut edge_output,
                matvec_loops,
            )?);
        }
    }

    let report = preconditioner.hierarchy().report();
    let graph_bytes = size_of_val(graph.edges()) + size_of_val(graph.diagonal());
    let hierarchy_core_bytes: usize = preconditioner
        .hierarchy()
        .levels()
        .iter()
        .map(|level| {
            size_of_val(level.graph().edges())
                + size_of_val(level.graph().diagonal())
                + size_of_val(level.inverse_diagonal())
        })
        .sum();
    let terminal_factor_bytes = preconditioner
        .terminal_factor()
        .map_or(0, cmg::GroundedLdl::byte_len);
    let cmg_workspace_bytes = cmg_workspace.byte_len();
    let pcg_workspace_bytes = pcg_workspace.byte_len();

    let graph_build_median_ns = median(&mut graph_build_ns);
    let hierarchy_build_median_ns = median(&mut hierarchy_build_ns);
    let preconditioner_apply_median_ns = median(&mut apply_ns);
    let solve_batch_median_ns = median(&mut solve_batch_ns);
    let solve_per_rhs_median_ns = solve_batch_median_ns / config.rhs_count as u128;
    let csr_build_median_ns = median(&mut csr_build_ns);
    let edge_matvec_median_ns = median(&mut edge_matvec_ns);
    let csr_matvec_median_ns = median(&mut csr_matvec_ns);
    let csr_over_edge_matvec = csr_matvec_median_ns as f64 / edge_matvec_median_ns as f64;

    let json = format!(
        concat!(
            "{{\n",
            "  \"schema\": 3,\n",
            "  \"source_commit\": \"{}\",\n",
            "  \"benchmark_baseline_commit\": \"{}\",\n",
            "  \"case\": \"{}\",\n",
            "  \"logical_cpus\": {},\n",
            "  \"vertices\": {},\n",
            "  \"canonical_edges\": {},\n",
            "  \"raw_edges\": {},\n",
            "  \"rhs_count\": {},\n",
            "  \"repetitions\": {},\n",
            "  \"direct_threshold\": {},\n",
            "  \"generation_ns\": {},\n",
            "  \"graph_build_median_ns\": {},\n",
            "  \"hierarchy_build_median_ns\": {},\n",
            "  \"preconditioner_apply_median_ns\": {},\n",
            "  \"solve_batch_median_ns\": {},\n",
            "  \"solve_per_rhs_median_ns\": {},\n",
            "  \"csr_build_median_ns\": {},\n",
            "  \"matvec_loops\": {},\n",
            "  \"edge_matvec_median_ns\": {},\n",
            "  \"csr_matvec_median_ns\": {},\n",
            "  \"csr_over_edge_matvec\": {:.17e},\n",
            "  \"graph_core_bytes\": {},\n",
            "  \"csr_bytes\": {},\n",
            "  \"csr_uses_compact_indices\": {},\n",
            "  \"hierarchy_core_bytes\": {},\n",
            "  \"terminal_factor_bytes\": {},\n",
            "  \"cmg_workspace_bytes\": {},\n",
            "  \"pcg_workspace_bytes\": {},\n",
            "  \"terminal_reason\": \"{:?}\",\n",
            "  \"level_vertices\": {},\n",
            "  \"level_matrix_nonzeros\": {},\n",
            "  \"iterations\": {},\n",
            "  \"backward_errors\": {}\n",
            "}}\n"
        ),
        SOURCE_COMMIT,
        BENCHMARK_BASELINE_COMMIT,
        config.case,
        logical_cpus,
        graph.vertex_count(),
        graph.edge_count(),
        raw_edges.len(),
        config.rhs_count,
        config.repetitions,
        config.direct_threshold,
        generation_ns,
        graph_build_median_ns,
        hierarchy_build_median_ns,
        preconditioner_apply_median_ns,
        solve_batch_median_ns,
        solve_per_rhs_median_ns,
        csr_build_median_ns,
        matvec_loops,
        edge_matvec_median_ns,
        csr_matvec_median_ns,
        csr_over_edge_matvec,
        graph_bytes,
        csr.byte_len(),
        csr.uses_compact_indices(),
        hierarchy_core_bytes,
        terminal_factor_bytes,
        cmg_workspace_bytes,
        pcg_workspace_bytes,
        report.terminal_reason(),
        json_usize_array(report.vertex_counts()),
        json_usize_array(report.matrix_nonzeros()),
        json_usize_array(&final_iterations),
        json_f64_array(&final_backward_errors),
    );

    if let Some(path) = &config.output {
        fs::write(path, &json)?;
    }
    print!("{json}");
    Ok(())
}

fn time_edge_matvec(
    graph: &Laplacian,
    input: &[f64],
    output: &mut [f64],
    loops: usize,
) -> Result<u128, cmg::CmgError> {
    let start = Instant::now();
    for _ in 0..loops {
        graph.matvec_into(input, output)?;
        black_box(&*output);
    }
    Ok(start.elapsed().as_nanos() / loops as u128)
}

fn time_csr_matvec(
    graph: &CsrLaplacian,
    input: &[f64],
    output: &mut [f64],
    loops: usize,
) -> Result<u128, cmg::CmgError> {
    let start = Instant::now();
    for _ in 0..loops {
        graph.matvec_into(input, output)?;
        black_box(&*output);
    }
    Ok(start.elapsed().as_nanos() / loops as u128)
}

fn validate_matvec_agreement(left: &[f64], right: &[f64]) -> Result<(), io::Error> {
    for (index, (left_value, right_value)) in left.iter().zip(right).enumerate() {
        let scale = 1.0_f64.max(left_value.abs()).max(right_value.abs());
        if (left_value - right_value).abs() > 4.0e-15 * scale {
            return Err(io::Error::other(format!(
                "edge and CSR matvec differ at {index}: {left_value} versus {right_value}"
            )));
        }
    }
    Ok(())
}

fn parse_config() -> Result<Config, Box<dyn Error>> {
    let mut config = Config::default();
    let mut arguments = env::args().skip(1);
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--case" => config.case = next_value(&mut arguments, "--case")?,
            "--vertices" => {
                config.vertices =
                    parse_usize(next_value(&mut arguments, "--vertices")?, "vertices")?;
            }
            "--rhs" => {
                config.rhs_count = parse_usize(next_value(&mut arguments, "--rhs")?, "rhs")?;
            }
            "--repetitions" => {
                config.repetitions =
                    parse_usize(next_value(&mut arguments, "--repetitions")?, "repetitions")?;
            }
            "--direct-threshold" => {
                config.direct_threshold = parse_usize(
                    next_value(&mut arguments, "--direct-threshold")?,
                    "direct-threshold",
                )?;
            }
            "--output" => config.output = Some(next_value(&mut arguments, "--output")?),
            "--help" | "-h" => {
                println!(
                    "cmg-bench [--case path|grid|worker-firm] [--vertices N] \\\n                     [--rhs N] [--repetitions N] [--direct-threshold N] [--output FILE]"
                );
                std::process::exit(0);
            }
            _ => return Err(invalid_input(format!("unknown argument: {argument}")).into()),
        }
    }
    if config.vertices < 2 {
        return Err(invalid_input("vertices must be at least 2").into());
    }
    if config.rhs_count == 0 {
        return Err(invalid_input("rhs must be positive").into());
    }
    if config.repetitions == 0 {
        return Err(invalid_input("repetitions must be positive").into());
    }
    if config.direct_threshold == 0 {
        return Err(invalid_input("direct-threshold must be positive").into());
    }
    Ok(config)
}

fn next_value(
    arguments: &mut impl Iterator<Item = String>,
    flag: &str,
) -> Result<String, io::Error> {
    arguments
        .next()
        .ok_or_else(|| invalid_input(format!("missing value for {flag}")))
}

fn parse_usize(value: String, name: &str) -> Result<usize, io::Error> {
    value
        .parse::<usize>()
        .map_err(|error| invalid_input(format!("invalid {name} value {value:?}: {error}")))
}

fn invalid_input(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message.into())
}

fn generate_edges(case: &str, vertices: usize) -> Result<Vec<(usize, usize, f64)>, io::Error> {
    match case {
        "path" => Ok((0..vertices - 1)
            .map(|u| (u, u + 1, deterministic_weight(u)))
            .collect()),
        "grid" => Ok(grid_edges(vertices)),
        "worker-firm" => Ok(worker_firm_edges(vertices)),
        _ => Err(invalid_input(format!("unknown graph case: {case}"))),
    }
}

fn grid_edges(vertices: usize) -> Vec<(usize, usize, f64)> {
    let width = (vertices as f64).sqrt().ceil() as usize;
    let mut edges = Vec::with_capacity(vertices.saturating_mul(2));
    for vertex in 0..vertices {
        let column = vertex % width;
        if column + 1 < width && vertex + 1 < vertices {
            edges.push((vertex, vertex + 1, deterministic_weight(vertex)));
        }
        if vertex + width < vertices {
            edges.push((
                vertex,
                vertex + width,
                deterministic_weight(vertex.wrapping_mul(31)),
            ));
        }
    }
    edges
}

fn worker_firm_edges(vertices: usize) -> Vec<(usize, usize, f64)> {
    let firm_count = (vertices / 5).clamp(1, vertices - 1);
    let worker_count = vertices - firm_count;
    let mut edges = Vec::with_capacity(worker_count.saturating_mul(3));
    for worker in 0..worker_count {
        let first = worker % firm_count;
        edges.push((worker, worker_count + first, deterministic_weight(worker)));
        if firm_count > 1 {
            let second = worker.wrapping_mul(48_271).wrapping_add(1) % firm_count;
            if second != first {
                edges.push((
                    worker,
                    worker_count + second,
                    deterministic_weight(worker.wrapping_mul(17).wrapping_add(3)),
                ));
            }
        }
        if firm_count > 2 && worker % 7 == 0 {
            let third = worker.wrapping_mul(69_621).wrapping_add(5) % firm_count;
            if third != first {
                edges.push((
                    worker,
                    worker_count + third,
                    deterministic_weight(worker.wrapping_mul(97).wrapping_add(11)),
                ));
            }
        }
    }
    edges
}

fn deterministic_weight(seed: usize) -> f64 {
    0.5 + (seed.wrapping_mul(1_103_515_245).wrapping_add(12_345) % 1_024) as f64 / 512.0
}

fn make_right_hand_sides(graph: &Laplacian, count: usize) -> Result<Vec<Vec<f64>>, cmg::CmgError> {
    (0..count)
        .map(|rhs_index| {
            let target: Vec<f64> = (0..graph.vertex_count())
                .map(|vertex| {
                    let code = vertex
                        .wrapping_mul(31)
                        .wrapping_add(rhs_index.wrapping_mul(17))
                        % 101;
                    (code as f64 - 50.0) / 13.0
                })
                .collect();
            graph.matvec(&target)
        })
        .collect()
}

fn median(values: &mut [u128]) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn json_usize_array(values: &[usize]) -> String {
    format!(
        "[{}]",
        values
            .iter()
            .map(usize::to_string)
            .collect::<Vec<_>>()
            .join(",")
    )
}

fn json_f64_array(values: &[f64]) -> String {
    format!(
        "[{}]",
        values
            .iter()
            .map(|value| format!("{value:.17e}"))
            .collect::<Vec<_>>()
            .join(",")
    )
}
