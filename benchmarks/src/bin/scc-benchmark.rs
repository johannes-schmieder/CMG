use cmg::{
    CmgOptions, CmgPreconditioner, Laplacian, ParallelCmgPlan, ParallelExecutor, ParallelOptions,
    PcgOptions, PcgWorkspace, solve_pcg_batch_with_executor, solve_pcg_with_plan_and_workspace,
};
use std::env;
use std::error::Error;
use std::fs::{self, File};
use std::hint::black_box;
use std::io::{self, BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

type AnyError = Box<dyn Error>;
type EdgeSpec = (usize, usize, f64);

const GRAPH_MAGIC: &[u8; 8] = b"CMGGRPH1";
const VECTOR_MAGIC: &[u8; 8] = b"CMGVEC01";
const SCHEMA: usize = 1;
const APPLY_TARGET: Duration = Duration::from_secs(1);
const SOURCE_COMMIT: &str = match option_env!("CMG_BENCH_COMMIT") {
    Some(value) => value,
    None => "unknown",
};

fn main() -> Result<(), AnyError> {
    let mut arguments = env::args().skip(1);
    match arguments.next().as_deref() {
        Some("generate") => {
            let family = required(&mut arguments, "family")?;
            let vertices = parse_usize(required(&mut arguments, "vertices")?, "vertices")?;
            let rhs_count = parse_usize(required(&mut arguments, "rhs-count")?, "rhs-count")?;
            let output = PathBuf::from(required(&mut arguments, "output-directory")?);
            reject_extra(arguments)?;
            generate(&family, vertices, rhs_count, &output)
        }
        Some("run") => {
            let input = PathBuf::from(required(&mut arguments, "input-directory")?);
            let threads = parse_usize(required(&mut arguments, "threads")?, "threads")?;
            let repetitions = parse_usize(required(&mut arguments, "repetitions")?, "repetitions")?;
            let mode = required(&mut arguments, "mode")?;
            let output = PathBuf::from(required(&mut arguments, "output-file")?);
            reject_extra(arguments)?;
            run(&input, threads, repetitions, &mode, &output)
        }
        _ => Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "usage: scc-benchmark generate FAMILY VERTICES RHS_COUNT OUTPUT_DIR | \
             scc-benchmark run INPUT_DIR THREADS REPETITIONS single|batch16 OUTPUT_JSON",
        )
        .into()),
    }
}

fn required(arguments: &mut impl Iterator<Item = String>, name: &str) -> Result<String, AnyError> {
    arguments.next().ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidInput, format!("missing {name}")).into()
    })
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

fn parse_usize(value: String, name: &str) -> Result<usize, AnyError> {
    let parsed = value.parse::<usize>()?;
    if parsed == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{name} must be positive"),
        )
        .into());
    }
    Ok(parsed)
}

fn generate(
    family: &str,
    vertices: usize,
    rhs_count: usize,
    output: &Path,
) -> Result<(), AnyError> {
    if vertices < 2 {
        return Err(
            io::Error::new(io::ErrorKind::InvalidInput, "vertices must be at least 2").into(),
        );
    }
    fs::create_dir_all(output)?;
    let generated = generate_edges(family, vertices)?;
    let graph = Laplacian::from_edges(vertices, generated)?;
    if graph.edge_count() == 0 {
        return Err(
            io::Error::new(io::ErrorKind::InvalidData, "generated graph has no edges").into(),
        );
    }
    let edges: Vec<EdgeSpec> = graph
        .edges()
        .iter()
        .map(|edge| (edge.u(), edge.v(), edge.weight()))
        .collect();
    let mut truths = Vec::with_capacity(rhs_count);
    let mut right_hand_sides = Vec::with_capacity(rhs_count);
    for rhs_index in 0..rhs_count {
        let mut truth: Vec<f64> = (0..vertices)
            .map(|vertex| {
                let first = vertex
                    .wrapping_mul(17)
                    .wrapping_add(rhs_index.wrapping_mul(31))
                    % 257;
                let second = vertex
                    .wrapping_mul(43)
                    .wrapping_add(rhs_index.wrapping_mul(19))
                    % 101;
                (first as f64 - 128.0) / 37.0 + (second as f64 - 50.0) / 113.0
            })
            .collect();
        let mean = truth.iter().sum::<f64>() / vertices as f64;
        truth.iter_mut().for_each(|value| *value -= mean);
        right_hand_sides.push(graph.matvec(&truth)?);
        truths.push(truth);
    }

    write_graph(&output.join("graph.bin"), vertices, &edges)?;
    write_vectors(&output.join("rhs.bin"), &right_hand_sides)?;
    write_vectors(&output.join("truth.bin"), &truths)?;
    let metadata = format!(
        concat!(
            "{{\n",
            "  \"schema\": {schema},\n",
            "  \"family\": \"{family}\",\n",
            "  \"vertices\": {vertices},\n",
            "  \"canonical_edges\": {},\n",
            "  \"matrix_nonzeros\": {},\n",
            "  \"average_degree\": {:.17e},\n",
            "  \"connected_components\": 1,\n",
            "  \"rhs_count\": {rhs_count}\n",
            "}}\n"
        ),
        edges.len(),
        vertices.saturating_add(2_usize.saturating_mul(edges.len())),
        2.0 * edges.len() as f64 / vertices as f64,
        schema = SCHEMA,
        family = family,
        vertices = vertices,
        rhs_count = rhs_count,
    );
    atomic_write(&output.join("metadata.json"), metadata.as_bytes())?;
    println!(
        "CMG_BENCH_GENERATE_SUCCESS family={family} vertices={vertices} edges={}",
        edges.len()
    );
    Ok(())
}

fn run(
    input_directory: &Path,
    threads: usize,
    repetitions: usize,
    mode: &str,
    output: &Path,
) -> Result<(), AnyError> {
    if !matches!(mode, "single" | "batch16") {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "mode must be single or batch16",
        )
        .into());
    }
    let load_start = Instant::now();
    let (vertices, edges) = read_graph(&input_directory.join("graph.bin"))?;
    let right_hand_sides = read_vectors(&input_directory.join("rhs.bin"))?;
    let truths = read_vectors(&input_directory.join("truth.bin"))?;
    let family = read_family(&input_directory.join("metadata.json"))?;
    let input_load_ns = load_start.elapsed().as_nanos();
    if right_hand_sides.is_empty() || truths.len() != right_hand_sides.len() {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "invalid RHS/truth bundle").into());
    }
    if mode == "batch16" && right_hand_sides.len() < 16 {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "batch16 requires 16 RHSs").into());
    }

    let graph_start = Instant::now();
    let graph = Laplacian::from_edges(vertices, edges.iter().copied())?;
    let graph_build_ns = graph_start.elapsed().as_nanos();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads,
        min_parallel_len: 16_384,
        ..ParallelOptions::default()
    })?;
    let cmg_options = CmgOptions::default();
    let pcg_options = PcgOptions {
        relative_tolerance: 1.0e-8,
        max_iterations: 1_000,
        ..PcgOptions::default()
    };

    black_box(CmgPreconditioner::build_with_executor(
        &graph,
        cmg_options,
        &executor,
    )?);
    let mut setup_ns = Vec::with_capacity(repetitions);
    let mut preconditioner = None;
    for _ in 0..repetitions {
        let start = Instant::now();
        let candidate = CmgPreconditioner::build_with_executor(&graph, cmg_options, &executor)?;
        setup_ns.push(start.elapsed().as_nanos());
        preconditioner = Some(candidate);
    }
    let preconditioner = preconditioner
        .ok_or_else(|| io::Error::other("preconditioner setup produced no result"))?;

    black_box(ParallelCmgPlan::build(&preconditioner, &executor)?);
    let mut plan_ns = Vec::with_capacity(repetitions);
    let mut plan = None;
    for _ in 0..repetitions {
        let start = Instant::now();
        let candidate = ParallelCmgPlan::build(&preconditioner, &executor)?;
        plan_ns.push(start.elapsed().as_nanos());
        plan = Some(candidate);
    }
    let plan = plan.ok_or_else(|| io::Error::other("parallel plan setup produced no result"))?;

    let mut cmg_workspace = preconditioner.workspace();
    let mut apply_output = vec![0.0; vertices];
    let rhs = &right_hand_sides[0];
    plan.apply_compatible_into(
        &preconditioner,
        rhs,
        &mut apply_output,
        &mut cmg_workspace,
        &executor,
    )?;
    let apply_loops = calibrate_apply(
        &preconditioner,
        &plan,
        &executor,
        rhs,
        &mut apply_output,
        &mut cmg_workspace,
    )?;
    let mut apply_ns = Vec::with_capacity(repetitions);
    for _ in 0..repetitions {
        let start = Instant::now();
        for _ in 0..apply_loops {
            plan.apply_compatible_into(
                &preconditioner,
                rhs,
                &mut apply_output,
                &mut cmg_workspace,
                &executor,
            )?;
            black_box(&apply_output);
        }
        apply_ns.push(start.elapsed().as_nanos() / apply_loops as u128);
    }

    let (
        solve_ns,
        total_ns,
        result,
        batch_count,
        batch_iterations,
        batch_relative_residuals,
        batch_backward_errors,
        batch_truth_scaled_errors,
        batch_max_backward_error,
    ) = if mode == "single" {
        let mut workspace = PcgWorkspace::new(&preconditioner);
        black_box(solve_pcg_with_plan_and_workspace(
            &graph,
            &preconditioner,
            &plan,
            rhs,
            pcg_options,
            &mut workspace,
            &executor,
        )?);
        let mut solve_ns = Vec::with_capacity(repetitions);
        let mut final_result = None;
        for _ in 0..repetitions {
            let start = Instant::now();
            let candidate = solve_pcg_with_plan_and_workspace(
                &graph,
                &preconditioner,
                &plan,
                rhs,
                pcg_options,
                &mut workspace,
                &executor,
            )?;
            solve_ns.push(start.elapsed().as_nanos());
            final_result = Some(candidate);
        }
        let mut total_ns = Vec::with_capacity(repetitions);
        for _ in 0..repetitions {
            let start = Instant::now();
            let local_preconditioner =
                CmgPreconditioner::build_with_executor(&graph, cmg_options, &executor)?;
            let local_plan = ParallelCmgPlan::build(&local_preconditioner, &executor)?;
            let mut local_workspace = PcgWorkspace::new(&local_preconditioner);
            let total_result = solve_pcg_with_plan_and_workspace(
                &graph,
                &local_preconditioner,
                &local_plan,
                rhs,
                pcg_options,
                &mut local_workspace,
                &executor,
            )?;
            require_certified(total_result.backward_error())?;
            black_box(total_result);
            total_ns.push(start.elapsed().as_nanos());
        }
        let final_result =
            final_result.ok_or_else(|| io::Error::other("PCG produced no result"))?;
        require_certified(final_result.backward_error())?;
        let iterations = vec![final_result.iterations()];
        let max_backward_error = final_result.backward_error();
        let relative_residuals = vec![final_result.relative_residual()];
        let backward_errors = vec![final_result.backward_error()];
        let truth_errors = vec![centered_scaled_difference(
            final_result.solution(),
            &truths[0],
        )];
        (
            solve_ns,
            total_ns,
            final_result,
            1,
            iterations,
            relative_residuals,
            backward_errors,
            truth_errors,
            max_backward_error,
        )
    } else {
        let batch = &right_hand_sides[..16];
        black_box(solve_pcg_batch_with_executor(
            &graph,
            &preconditioner,
            batch,
            pcg_options,
            &executor,
        )?);
        let mut solve_ns = Vec::with_capacity(repetitions);
        let mut final_results = None;
        for _ in 0..repetitions {
            let start = Instant::now();
            let candidate = solve_pcg_batch_with_executor(
                &graph,
                &preconditioner,
                batch,
                pcg_options,
                &executor,
            )?;
            solve_ns.push(start.elapsed().as_nanos());
            final_results = Some(candidate);
        }
        let mut results =
            final_results.ok_or_else(|| io::Error::other("batch PCG produced no result"))?;
        let batch_iterations = results.iter().map(|result| result.iterations()).collect();
        let batch_relative_residuals = results
            .iter()
            .map(|result| result.relative_residual())
            .collect();
        let batch_backward_errors: Vec<f64> = results
            .iter()
            .map(|result| result.backward_error())
            .collect();
        let batch_truth_scaled_errors = results
            .iter()
            .zip(&truths)
            .map(|(result, truth)| centered_scaled_difference(result.solution(), truth))
            .collect();
        let batch_max_backward_error = results
            .iter()
            .map(|result| result.backward_error())
            .fold(0.0_f64, f64::max);
        require_certified(batch_max_backward_error)?;
        let representative = results.remove(0);
        (
            solve_ns.clone(),
            solve_ns,
            representative,
            16,
            batch_iterations,
            batch_relative_residuals,
            batch_backward_errors,
            batch_truth_scaled_errors,
            batch_max_backward_error,
        )
    };

    let truth_error = centered_scaled_difference(result.solution(), &truths[0]);
    let level_vertices: Vec<usize> = preconditioner
        .hierarchy()
        .levels()
        .iter()
        .map(|level| level.graph().vertex_count())
        .collect();
    let level_nonzeros: Vec<usize> = preconditioner
        .hierarchy()
        .levels()
        .iter()
        .map(|level| level.graph().matrix_nnz())
        .collect();
    let json = format!(
        concat!(
            "{{\n",
            "  \"schema\": {schema},\n",
            "  \"implementation\": \"rust\",\n",
            "  \"source_commit\": \"{source_commit}\",\n",
            "  \"family\": \"{family}\",\n",
            "  \"mode\": \"{mode}\",\n",
            "  \"vertices\": {vertices},\n",
            "  \"canonical_edges\": {},\n",
            "  \"matrix_nonzeros\": {},\n",
            "  \"threads\": {},\n",
            "  \"repetitions\": {repetitions},\n",
            "  \"batch_count\": {batch_count},\n",
            "  \"batch_iterations\": {},\n",
            "  \"batch_relative_residuals\": {},\n",
            "  \"batch_backward_errors\": {},\n",
            "  \"batch_truth_scaled_errors\": {},\n",
            "  \"batch_max_backward_error\": {:.17e},\n",
            "  \"input_load_ns\": {input_load_ns},\n",
            "  \"graph_build_ns\": {graph_build_ns},\n",
            "  \"preconditioner_setup_samples_ns\": {},\n",
            "  \"preconditioner_setup_median_ns\": {},\n",
            "  \"parallel_plan_setup_samples_ns\": {},\n",
            "  \"parallel_plan_setup_median_ns\": {},\n",
            "  \"preconditioner_apply_loops\": {apply_loops},\n",
            "  \"preconditioner_apply_samples_ns\": {},\n",
            "  \"preconditioner_apply_median_ns\": {},\n",
            "  \"pcg_samples_ns\": {},\n",
            "  \"pcg_median_ns\": {},\n",
            "  \"solver_total_samples_ns\": {},\n",
            "  \"solver_total_median_ns\": {},\n",
            "  \"iterations\": {},\n",
            "  \"residual_norm\": {:.17e},\n",
            "  \"relative_residual\": {:.17e},\n",
            "  \"backward_error\": {:.17e},\n",
            "  \"truth_scaled_error\": {:.17e},\n",
            "  \"levels\": {},\n",
            "  \"level_vertices\": {},\n",
            "  \"level_matrix_nonzeros\": {},\n",
            "  \"repeat_counts\": {},\n",
            "  \"plan_operators\": {},\n",
            "  \"plan_bytes\": {},\n",
            "  \"workspace_bytes\": {},\n",
            "  \"warnings\": [],\n",
            "  \"native_converged\": true,\n",
            "  \"success\": true\n",
            "}}\n"
        ),
        graph.edge_count(),
        graph.matrix_nnz(),
        executor.thread_count(),
        json_usize(&batch_iterations),
        json_f64(&batch_relative_residuals),
        json_f64(&batch_backward_errors),
        json_f64(&batch_truth_scaled_errors),
        batch_max_backward_error,
        json_u128(&setup_ns),
        median_u128(&setup_ns),
        json_u128(&plan_ns),
        median_u128(&plan_ns),
        json_u128(&apply_ns),
        median_u128(&apply_ns),
        json_u128(&solve_ns),
        median_u128(&solve_ns),
        json_u128(&total_ns),
        median_u128(&total_ns),
        result.iterations(),
        result.residual_norm(),
        result.relative_residual(),
        result.backward_error(),
        truth_error,
        level_vertices.len(),
        json_usize(&level_vertices),
        json_usize(&level_nonzeros),
        json_usize(preconditioner.repeat_counts()),
        plan.operator_count(),
        plan.byte_len(),
        PcgWorkspace::new(&preconditioner).byte_len(),
        schema = SCHEMA,
        source_commit = SOURCE_COMMIT,
        family = family,
        mode = mode,
        vertices = vertices,
        repetitions = repetitions,
        batch_count = batch_count,
        input_load_ns = input_load_ns,
        graph_build_ns = graph_build_ns,
        apply_loops = apply_loops,
    );
    atomic_write(output, json.as_bytes())?;
    println!(
        "CMG_BENCH_RUST_SUCCESS family={family} vertices={vertices} threads={} mode={mode}",
        executor.thread_count()
    );
    Ok(())
}

fn calibrate_apply(
    preconditioner: &CmgPreconditioner,
    plan: &ParallelCmgPlan,
    executor: &ParallelExecutor,
    rhs: &[f64],
    output: &mut [f64],
    workspace: &mut cmg::CmgWorkspace,
) -> Result<usize, AnyError> {
    let mut loops = 1_usize;
    loop {
        let start = Instant::now();
        for _ in 0..loops {
            plan.apply_compatible_into(preconditioner, rhs, output, workspace, executor)?;
            black_box(&output);
        }
        if start.elapsed() >= APPLY_TARGET || loops >= 1_048_576 {
            return Ok(loops);
        }
        loops = loops.saturating_mul(2);
    }
}

fn generate_edges(family: &str, vertices: usize) -> Result<Vec<EdgeSpec>, AnyError> {
    match family {
        "path" => Ok((0..vertices - 1)
            .map(|vertex| (vertex, vertex + 1, deterministic_weight(vertex)))
            .collect()),
        "grid" => Ok(grid_edges(0, vertices)),
        "worker-firm" => Ok(worker_firm_edges(vertices, 3)),
        "dense-worker-firm" => Ok(worker_firm_edges(vertices, 16)),
        "weak-community" => Ok(weak_community_edges(vertices)),
        _ => Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "family must be path, grid, worker-firm, dense-worker-firm, or weak-community",
        )
        .into()),
    }
}

fn grid_edges(offset: usize, vertices: usize) -> Vec<EdgeSpec> {
    let width = (vertices as f64).sqrt().ceil() as usize;
    let mut edges = Vec::with_capacity(vertices.saturating_mul(2));
    for local in 0..vertices {
        let column = local % width;
        if column + 1 < width && local + 1 < vertices {
            edges.push((
                offset + local,
                offset + local + 1,
                deterministic_weight(offset.wrapping_add(local)),
            ));
        }
        if local + width < vertices {
            edges.push((
                offset + local,
                offset + local + width,
                deterministic_weight(offset.wrapping_add(local).wrapping_mul(31)),
            ));
        }
    }
    edges
}

fn worker_firm_edges(vertices: usize, degree: usize) -> Vec<EdgeSpec> {
    let workers = vertices / 2;
    let firms = vertices - workers;
    let firm_offset = workers;
    let mut edges = Vec::with_capacity(workers.saturating_mul(degree));
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
            edges.push((worker, firm_offset + firm, weight));
        }
    }
    edges
}

fn weak_community_edges(vertices: usize) -> Vec<EdgeSpec> {
    let communities = 16_usize.min(vertices);
    let mut edges = Vec::with_capacity(vertices.saturating_mul(2));
    let mut previous_end = None;
    for community in 0..communities {
        let start = community * vertices / communities;
        let end = (community + 1) * vertices / communities;
        let count = end - start;
        edges.extend(grid_edges(start, count));
        if let Some(previous) = previous_end {
            edges.push((previous, start, 1.0));
        }
        previous_end = Some(end - 1);
    }
    edges
}

fn deterministic_weight(seed: usize) -> f64 {
    0.5 + (seed.wrapping_mul(1_103_515_245).wrapping_add(12_345) % 1_024) as f64 / 512.0
}

fn write_graph(path: &Path, vertices: usize, edges: &[EdgeSpec]) -> Result<(), AnyError> {
    let mut writer = BufWriter::new(File::create(path)?);
    writer.write_all(GRAPH_MAGIC)?;
    write_u64(&mut writer, vertices)?;
    write_u64(&mut writer, edges.len())?;
    for &(u, v, weight) in edges {
        write_u64(&mut writer, u)?;
        write_u64(&mut writer, v)?;
        writer.write_all(&weight.to_le_bytes())?;
    }
    writer.flush()?;
    Ok(())
}

fn read_graph(path: &Path) -> Result<(usize, Vec<EdgeSpec>), AnyError> {
    let mut reader = BufReader::new(File::open(path)?);
    require_magic(&mut reader, GRAPH_MAGIC)?;
    let vertices = read_usize(&mut reader, "vertices")?;
    let edge_count = read_usize(&mut reader, "edges")?;
    let mut edges = Vec::with_capacity(edge_count);
    for _ in 0..edge_count {
        let u = read_usize(&mut reader, "edge endpoint")?;
        let v = read_usize(&mut reader, "edge endpoint")?;
        let mut bytes = [0_u8; 8];
        reader.read_exact(&mut bytes)?;
        edges.push((u, v, f64::from_le_bytes(bytes)));
    }
    Ok((vertices, edges))
}

fn write_vectors(path: &Path, vectors: &[Vec<f64>]) -> Result<(), AnyError> {
    let dimension = vectors.first().map_or(0, Vec::len);
    if dimension == 0 || vectors.iter().any(|vector| vector.len() != dimension) {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid vector bundle").into());
    }
    let mut writer = BufWriter::new(File::create(path)?);
    writer.write_all(VECTOR_MAGIC)?;
    write_u64(&mut writer, dimension)?;
    write_u64(&mut writer, vectors.len())?;
    for vector in vectors {
        for value in vector {
            writer.write_all(&value.to_le_bytes())?;
        }
    }
    writer.flush()?;
    Ok(())
}

fn read_vectors(path: &Path) -> Result<Vec<Vec<f64>>, AnyError> {
    let mut reader = BufReader::new(File::open(path)?);
    require_magic(&mut reader, VECTOR_MAGIC)?;
    let dimension = read_usize(&mut reader, "vector dimension")?;
    let count = read_usize(&mut reader, "vector count")?;
    let mut vectors = Vec::with_capacity(count);
    for _ in 0..count {
        let mut vector = Vec::with_capacity(dimension);
        for _ in 0..dimension {
            let mut bytes = [0_u8; 8];
            reader.read_exact(&mut bytes)?;
            vector.push(f64::from_le_bytes(bytes));
        }
        vectors.push(vector);
    }
    Ok(vectors)
}

fn require_magic(reader: &mut impl Read, expected: &[u8; 8]) -> Result<(), AnyError> {
    let mut actual = [0_u8; 8];
    reader.read_exact(&mut actual)?;
    if &actual != expected {
        return Err(
            io::Error::new(io::ErrorKind::InvalidData, "invalid binary input magic").into(),
        );
    }
    Ok(())
}

fn write_u64(writer: &mut impl Write, value: usize) -> Result<(), AnyError> {
    writer.write_all(&u64::try_from(value)?.to_le_bytes())?;
    Ok(())
}

fn read_usize(reader: &mut impl Read, context: &str) -> Result<usize, AnyError> {
    let mut bytes = [0_u8; 8];
    reader.read_exact(&mut bytes)?;
    usize::try_from(u64::from_le_bytes(bytes)).map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("{context} exceeds usize"),
        )
        .into()
    })
}

fn read_family(path: &Path) -> Result<String, AnyError> {
    let metadata = fs::read_to_string(path)?;
    let marker = "\"family\":";
    let tail = metadata
        .split_once(marker)
        .map(|(_, tail)| tail)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "metadata lacks family"))?;
    let start = tail
        .find('"')
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "invalid family"))?
        + 1;
    let end = tail[start..]
        .find('"')
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "invalid family"))?
        + start;
    Ok(tail[start..end].to_owned())
}

fn centered_scaled_difference(solution: &[f64], truth: &[f64]) -> f64 {
    let solution_mean = solution.iter().sum::<f64>() / solution.len().max(1) as f64;
    let truth_mean = truth.iter().sum::<f64>() / truth.len().max(1) as f64;
    solution
        .iter()
        .zip(truth)
        .map(|(&left, &right)| {
            let left = left - solution_mean;
            let right = right - truth_mean;
            (left - right).abs() / (1.0 + left.abs().max(right.abs()))
        })
        .fold(0.0, f64::max)
}

fn require_certified(backward_error: f64) -> Result<(), AnyError> {
    if backward_error.is_finite() && backward_error <= 1.000_001e-8 {
        Ok(())
    } else {
        Err(io::Error::other(format!(
            "PCG backward-error certification failed: {backward_error:e}"
        ))
        .into())
    }
}

fn median_u128(values: &[u128]) -> u128 {
    let mut sorted = values.to_vec();
    sorted.sort_unstable();
    sorted[sorted.len() / 2]
}

fn json_u128(values: &[u128]) -> String {
    format!(
        "[{}]",
        values
            .iter()
            .map(u128::to_string)
            .collect::<Vec<_>>()
            .join(",")
    )
}

fn json_usize(values: &[usize]) -> String {
    format!(
        "[{}]",
        values
            .iter()
            .map(usize::to_string)
            .collect::<Vec<_>>()
            .join(",")
    )
}

fn json_f64(values: &[f64]) -> String {
    format!(
        "[{}]",
        values
            .iter()
            .map(|value| format!("{value:.17e}"))
            .collect::<Vec<_>>()
            .join(",")
    )
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), AnyError> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, bytes)?;
    fs::rename(temporary, path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn graph_families_are_connected_and_deterministic() {
        for family in [
            "path",
            "grid",
            "worker-firm",
            "dense-worker-firm",
            "weak-community",
        ] {
            let left =
                Laplacian::from_edges(1_600, generate_edges(family, 1_600).unwrap()).unwrap();
            let right =
                Laplacian::from_edges(1_600, generate_edges(family, 1_600).unwrap()).unwrap();
            assert_eq!(left, right, "{family}");
            assert_eq!(
                cmg::Components::from_laplacian(&left).count(),
                1,
                "{family}"
            );
        }
    }

    #[test]
    fn binary_round_trip_preserves_graph_and_vectors() {
        let root = env::temp_dir().join(format!("cmg-scc-benchmark-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let edges = generate_edges("grid", 128).unwrap();
        write_graph(&root.join("graph.bin"), 128, &edges).unwrap();
        let (vertices, recovered) = read_graph(&root.join("graph.bin")).unwrap();
        assert_eq!(vertices, 128);
        assert_eq!(edges, recovered);
        let vectors = vec![vec![1.25; 128], vec![-3.5; 128]];
        write_vectors(&root.join("vectors.bin"), &vectors).unwrap();
        assert_eq!(read_vectors(&root.join("vectors.bin")).unwrap(), vectors);
        fs::remove_dir_all(root).unwrap();
    }
}
