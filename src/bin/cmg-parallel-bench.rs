use cmg::{
    CmgOptions, CmgPreconditioner, CsrLaplacian, Laplacian, ParallelExecutor,
    ParallelOptions, PcgOptions, PcgResult, PcgWorkspace, solve_pcg_batch,
    solve_pcg_batch_with_executor,
};
use std::env;
use std::error::Error;
use std::fs;
use std::hint::black_box;
use std::io;
use std::time::Instant;

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
    threads: usize,
    memory_budget_bytes: Option<usize>,
    output: Option<String>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            case: "worker-firm".to_owned(),
            vertices: 20_000,
            rhs_count: 16,
            repetitions: 3,
            direct_threshold: 700,
            threads: 0,
            memory_budget_bytes: None,
            output: None,
        }
    }
}

fn main() -> Result<(), Box<dyn Error>> {
    let config = parse_config()?;
    let logical_cpus = std::thread::available_parallelism()
        .map(usize::from)
        .unwrap_or(1);

    let raw_edges = generate_edges(&config.case, config.vertices)?;
    let graph = Laplacian::from_edges(config.vertices, raw_edges.iter().copied())?;
    let preconditioner = CmgPreconditioner::build(
        &graph,
        CmgOptions {
            direct_threshold: config.direct_threshold,
            ..CmgOptions::default()
        },
    )?;
    let right_hand_sides = make_right_hand_sides(&graph, config.rhs_count)?;
    let pcg_options = PcgOptions::default();
    let workspace_bytes = PcgWorkspace::new(&preconditioner).byte_len();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: config.threads,
        min_parallel_len: 1,
        workspace_memory_budget_bytes: config.memory_budget_bytes,
        ..ParallelOptions::default()
    })?;
    let batch_concurrency =
        executor.batch_concurrency(workspace_bytes, right_hand_sides.len())?;

    let serial_warmup = solve_pcg_batch(
        &graph,
        &preconditioner,
        &right_hand_sides,
        pcg_options,
    )?;
    let parallel_warmup = solve_pcg_batch_with_executor(
        &graph,
        &preconditioner,
        &right_hand_sides,
        pcg_options,
        &executor,
    )?;
    validate_solve_agreement(&serial_warmup, &parallel_warmup)?;
    black_box(&serial_warmup);
    black_box(&parallel_warmup);

    let mut serial_batch_ns = Vec::with_capacity(config.repetitions);
    let mut parallel_batch_ns = Vec::with_capacity(config.repetitions);
    let mut final_results = Vec::new();
    for repetition in 0..config.repetitions {
        if repetition % 2 == 0 {
            serial_batch_ns.push(time_serial_batch(
                &graph,
                &preconditioner,
                &right_hand_sides,
                pcg_options,
            )?);
            let (elapsed, results) = time_parallel_batch(
                &graph,
                &preconditioner,
                &right_hand_sides,
                pcg_options,
                &executor,
            )?;
            parallel_batch_ns.push(elapsed);
            final_results = results;
        } else {
            let (elapsed, results) = time_parallel_batch(
                &graph,
                &preconditioner,
                &right_hand_sides,
                pcg_options,
                &executor,
            )?;
            parallel_batch_ns.push(elapsed);
            final_results = results;
            serial_batch_ns.push(time_serial_batch(
                &graph,
                &preconditioner,
                &right_hand_sides,
                pcg_options,
            )?);
        }
    }

    let csr = CsrLaplacian::from_laplacian(&graph)?;
    let kernel_input: Vec<f64> = (0..graph.vertex_count())
        .map(|vertex| {
            let code = vertex.wrapping_mul(65_537).wrapping_add(19) % 4_093;
            (code as f64 - 2_046.0) / 257.0
        })
        .collect();
    let mut serial_output = vec![0.0; graph.vertex_count()];
    let mut parallel_output = vec![0.0; graph.vertex_count()];
    csr.matvec_into(&kernel_input, &mut serial_output)?;
    csr.matvec_into_parallel(&kernel_input, &mut parallel_output, &executor)?;
    if serial_output != parallel_output {
        return Err(io::Error::other("parallel CSR matvec changed row arithmetic").into());
    }

    let matvec_loops =
        (TARGET_MATVEC_EDGE_VISITS / graph.edge_count().max(1)).clamp(8, 2_000);
    let mut serial_csr_ns = Vec::with_capacity(config.repetitions);
    let mut parallel_csr_ns = Vec::with_capacity(config.repetitions);
    for repetition in 0..config.repetitions {
        if repetition % 2 == 0 {
            serial_csr_ns.push(time_serial_csr(
                &csr,
                &kernel_input,
                &mut serial_output,
                matvec_loops,
            )?);
            parallel_csr_ns.push(time_parallel_csr(
                &csr,
                &kernel_input,
                &mut parallel_output,
                matvec_loops,
                &executor,
            )?);
        } else {
            parallel_csr_ns.push(time_parallel_csr(
                &csr,
                &kernel_input,
                &mut parallel_output,
                matvec_loops,
                &executor,
            )?);
            serial_csr_ns.push(time_serial_csr(
                &csr,
                &kernel_input,
                &mut serial_output,
                matvec_loops,
            )?);
        }
    }

    let serial_batch_median_ns = median(&mut serial_batch_ns);
    let parallel_batch_median_ns = median(&mut parallel_batch_ns);
    let serial_csr_median_ns = median(&mut serial_csr_ns);
    let parallel_csr_median_ns = median(&mut parallel_csr_ns);
    let iterations: Vec<usize> = final_results.iter().map(PcgResult::iterations).collect();
    let backward_errors: Vec<f64> = final_results
        .iter()
        .map(PcgResult::backward_error)
        .collect();

    let json = format!(
        concat!(
            "{{\n",
            "  \"schema\": 1,\n",
            "  \"source_commit\": \"{}\",\n",
            "  \"case\": \"{}\",\n",
            "  \"logical_cpus\": {},\n",
            "  \"executor_threads\": {},\n",
            "  \"vertices\": {},\n",
            "  \"canonical_edges\": {},\n",
            "  \"rhs_count\": {},\n",
            "  \"repetitions\": {},\n",
            "  \"workspace_bytes\": {},\n",
            "  \"workspace_memory_budget_bytes\": {},\n",
            "  \"batch_concurrency\": {},\n",
            "  \"serial_batch_median_ns\": {},\n",
            "  \"parallel_batch_median_ns\": {},\n",
            "  \"batch_speedup\": {:.17e},\n",
            "  \"serial_per_rhs_median_ns\": {},\n",
            "  \"parallel_per_rhs_median_ns\": {},\n",
            "  \"matvec_loops\": {},\n",
            "  \"serial_csr_matvec_median_ns\": {},\n",
            "  \"parallel_csr_matvec_median_ns\": {},\n",
            "  \"csr_matvec_speedup\": {:.17e},\n",
            "  \"iterations\": {},\n",
            "  \"backward_errors\": {}\n",
            "}}\n"
        ),
        SOURCE_COMMIT,
        config.case,
        logical_cpus,
        executor.thread_count(),
        graph.vertex_count(),
        graph.edge_count(),
        config.rhs_count,
        config.repetitions,
        workspace_bytes,
        json_optional_usize(config.memory_budget_bytes),
        batch_concurrency,
        serial_batch_median_ns,
        parallel_batch_median_ns,
        serial_batch_median_ns as f64 / parallel_batch_median_ns as f64,
        serial_batch_median_ns / config.rhs_count as u128,
        parallel_batch_median_ns / config.rhs_count as u128,
        matvec_loops,
        serial_csr_median_ns,
        parallel_csr_median_ns,
        serial_csr_median_ns as f64 / parallel_csr_median_ns as f64,
        json_usize_array(&iterations),
        json_f64_array(&backward_errors),
    );

    if let Some(path) = &config.output {
        fs::write(path, &json)?;
    }
    print!("{json}");
    Ok(())
}

fn time_serial_batch(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    right_hand_sides: &[Vec<f64>],
    options: PcgOptions,
) -> Result<u128, cmg::CmgError> {
    let start = Instant::now();
    let results = solve_pcg_batch(graph, preconditioner, right_hand_sides, options)?;
    black_box(&results);
    Ok(start.elapsed().as_nanos())
}

fn time_parallel_batch(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    right_hand_sides: &[Vec<f64>],
    options: PcgOptions,
    executor: &ParallelExecutor,
) -> Result<(u128, Vec<PcgResult>), cmg::CmgError> {
    let start = Instant::now();
    let results = solve_pcg_batch_with_executor(
        graph,
        preconditioner,
        right_hand_sides,
        options,
        executor,
    )?;
    let elapsed = start.elapsed().as_nanos();
    black_box(&results);
    Ok((elapsed, results))
}

fn time_serial_csr(
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

fn time_parallel_csr(
    graph: &CsrLaplacian,
    input: &[f64],
    output: &mut [f64],
    loops: usize,
    executor: &ParallelExecutor,
) -> Result<u128, cmg::CmgError> {
    let start = Instant::now();
    for _ in 0..loops {
        graph.matvec_into_parallel(input, output, executor)?;
        black_box(&*output);
    }
    Ok(start.elapsed().as_nanos() / loops as u128)
}

fn validate_solve_agreement(
    serial: &[PcgResult],
    parallel: &[PcgResult],
) -> Result<(), io::Error> {
    if serial.len() != parallel.len() {
        return Err(io::Error::other("parallel batch changed the result count"));
    }
    for (index, (left, right)) in serial.iter().zip(parallel).enumerate() {
        if left.iterations() != right.iterations()
            || left.solution() != right.solution()
            || left.backward_error().to_bits() != right.backward_error().to_bits()
        {
            return Err(io::Error::other(format!(
                "parallel batch changed certified result {index}"
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
                    parse_usize(next_value(&mut arguments, "--vertices")?, 'vertices')?;
            }
            "--rhs" => {
                config.rhs_count = parse_usize(next_value(&mut arguments, "--rhs")?, 'r')?;
            }
            "--repetitions" => {
                config.repetitions = parse_usize(next_value(&mut arguments, "--repetitions")?, "repetitions")?;
            }
            "--direct-threshold" => {
                config.direct_threshold = parse_usize(
                    next_value(&mut arguments, "--direct-threshold")?,
                    "direct-threshold",
                )?;
            }
            "--threads" => {
                config.threads =
                    parse_usize(next_value(&mut arguments, "--threads")?, "threads")?;
            }
            "--memory-budget" => {
                config.memory_budget_bytes = Some(parse_usize(
                    next_value(&mut arguments, "--memory-budget")?,
                    "memory-budget",
                )?);
            }
            "--output" => config.output = Some(next_value(&mut arguments, "--output")?),
            "--help" | "-h" => {
                println!(
                    "cmg-parallel-bench [--case path|grid|worker-firm] [--vertices N] \\n                     [--rhs N] [--repetitions N] [--direct-threshold N] \\n                     [--threads N] [--memory-budget BYTES] [--output FILE]"
                );
                std::process::exit(0);
            }
            _ => return Err(invalid_input(format!("unknown argument: {argument}")).into()),
        }
    }
    if config.vertices < 2 {
        return Err(invalid_input('vertices must be at least 2').into());
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
    if config.memory_budget_bytes == Some(0) {
        return Err(invalid_input("memory-budget must be positive").into());
    }
    Ok(config)
}

fn next_value(arguments: &mut impl Iterator<Item = String>, flag: &str) -> Result<String, io::Error> {
    arguments.next().ok_or_else(|| invalid_input(format!("missing value for {flag}")))
}

fn parse_usize(value: String, name: &str) -> Result<usize, io::Error> {
    value.parse::<usize>().map_err(|error| invalid_input(format!("invalid {name} value {value:?}: {error}")))
}

fn invalid_input(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message.into())
}

fn generate_edges(case: &str, vertices: usize) -> Result<Vec<(usize, usize, f64)>, io::Error> {
    match case {
        "path" => Ok((0..vertices - 1).map(|u| (u, u + 1, deterministic_weight(u))).collect()),
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
            edges.push((vertex, vertex + width, deterministic_weight(vertex.wrapping_mul(31))));
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
                edges.push((worker, worker_count + second, deterministic_weight(worker.wrapping_mul(17).wrapping_add(3))));
            }
        }
        if firm_count > 2 && worker % 7 == 0 {
            let third = worker.wrapping_mul(69_621).wrapping_add(5) % firm_count;
            if third != first {
                edges.push((worker, worker_count + third, deterministic_weight(worker.wrapping_mul(97).wrapping_add(11))));
            }
        }
    }
    edges
}

fn deterministic_weight(seed: usize) -> f64 {
    0.5 + (seed.wrapping_mul(1_103_515_245).wrapping_add(12_345) % 1_024) as f64 / 512.0
}

fn make_right_hand_sides(graph: &Laplacian, count: usize) -> Result<Vec<Vec<f64>>, cmg::CmgError> {
    (0..count).map(|rhs_index| {
        let target: Vec<f64> = (0..graph.vertex_count()).map(|vertex| {
            let code = vertex.wrapping_mul(31).wrapping_add(rhs_index.wrapping_mul(17)) % 101;
            (code as f64 - 50.0) / 13.0
        }).collect();
        graph.matvec(&target)
    }).collect()
}

fn median(values: &mut [u128]) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn json_optional_usize(value: Option<usize>) -> String {
    value.map_or_else(|| "null".to_owned(), |value| value.to_string())
}

fn json_usize_array(values: &mut [usize]) -> String {
    format!("[{}]", values.iter().map(usize::to_string).collect::<Vec<_>>().join(","))
}

fn json_f64_array(values: &[f64]) -> String {
    format!("[{}]", values.iter().map(|value| format!("{value:.17e}")).collect::<Vec_>>().join(","))
}
