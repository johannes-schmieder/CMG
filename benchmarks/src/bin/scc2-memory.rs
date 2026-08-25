use cmg::{
    CmgOptions, CmgPreconditioner, Laplacian, ParallelCmgPlan, ParallelExecutor, ParallelOptions,
    PcgOptions, PcgWorkspace, solve_pcg_batch_with_executor, solve_pcg_with_workspace,
};
use std::env;
use std::error::Error;
use std::fs::{self, File};
use std::io::{self, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Instant;

type AnyError = Box<dyn Error>;
type EdgeSpec = (usize, usize, f64);

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

fn main() -> Result<(), AnyError> {
    let mut arguments = env::args().skip(1);
    let input = PathBuf::from(required(&mut arguments, "input-dir")?);
    let threads = required(&mut arguments, "threads")?.parse::<usize>()?;
    let stage = required(&mut arguments, "stage")?;
    let rhs_count = required(&mut arguments, "rhs-count")?.parse::<usize>()?;
    let output = PathBuf::from(required(&mut arguments, "output")?);
    if arguments.next().is_some() || threads == 0 || rhs_count == 0 {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid arguments").into());
    }
    if !matches!(
        stage.as_str(),
        "baseline"
            | "input"
            | "graph"
            | "hierarchy"
            | "plan"
            | "workspace-one"
            | "workspace-pool"
            | "solve"
            | "batch"
    ) {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid stage").into());
    }
    run(&input, threads, &stage, rhs_count, &output)
}

fn run(
    input: &Path,
    threads: usize,
    stop: &str,
    rhs_count: usize,
    output: &Path,
) -> Result<(), AnyError> {
    let started = Instant::now();
    let mut checkpoints = vec![checkpoint("baseline", started, 0)];
    let family = env::var("CMG_FAMILY").unwrap_or_else(|_| "unknown".to_owned());
    let vertices_from_env = env::var("CMG_VERTICES")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(0);
    if stop == "baseline" {
        return finish(
            output,
            stop,
            threads,
            rhs_count,
            &family,
            vertices_from_env,
            0,
            0,
            0,
            0,
            0,
            0,
            checkpoints,
            None,
        );
    }

    let (vertices, edges) = read_graph(&input.join("graph.bin"))?;
    let right_hand_sides = read_vectors(&input.join("rhs.bin"))?;
    let _truths = read_vectors(&input.join("truth.bin"))?;
    if rhs_count > right_hand_sides.len() {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "too few RHSs").into());
    }
    let input_bytes = edges.len() * std::mem::size_of::<EdgeSpec>()
        + right_hand_sides
            .iter()
            .map(|value| std::mem::size_of_val(value.as_slice()))
            .sum::<usize>();
    checkpoints.push(checkpoint("input", started, input_bytes));
    if stop == "input" {
        return finish(
            output,
            stop,
            threads,
            rhs_count,
            &family,
            vertices,
            edges.len(),
            0,
            input_bytes,
            0,
            0,
            0,
            checkpoints,
            None,
        );
    }

    let graph = Laplacian::from_edges(vertices, edges.iter().copied())?;
    let graph_bytes = graph.edge_count() * std::mem::size_of::<cmg::Edge>()
        + std::mem::size_of_val(graph.diagonal());
    checkpoints.push(checkpoint("graph", started, graph_bytes));
    if stop == "graph" {
        return finish(
            output,
            stop,
            threads,
            rhs_count,
            &family,
            vertices,
            graph.edge_count(),
            graph.matrix_nnz(),
            input_bytes,
            graph_bytes,
            0,
            0,
            checkpoints,
            None,
        );
    }

    let executor = ParallelExecutor::new(ParallelOptions {
        threads,
        min_parallel_len: 16_384,
        ..ParallelOptions::default()
    })?;
    let preconditioner =
        CmgPreconditioner::build_with_executor(&graph, CmgOptions::default(), &executor)?;
    let hierarchy_bytes = hierarchy_bytes(&preconditioner);
    checkpoints.push(checkpoint("hierarchy", started, hierarchy_bytes));
    if stop == "hierarchy" {
        return finish(
            output,
            stop,
            threads,
            rhs_count,
            &family,
            vertices,
            graph.edge_count(),
            graph.matrix_nnz(),
            input_bytes,
            graph_bytes,
            hierarchy_bytes,
            0,
            checkpoints,
            None,
        );
    }

    let plan = ParallelCmgPlan::build(&preconditioner, &executor)?;
    let plan_bytes = plan.byte_len();
    checkpoints.push(checkpoint("plan", started, plan_bytes));
    if stop == "plan" {
        return finish(
            output,
            stop,
            threads,
            rhs_count,
            &family,
            vertices,
            graph.edge_count(),
            graph.matrix_nnz(),
            input_bytes,
            graph_bytes,
            hierarchy_bytes,
            plan_bytes,
            checkpoints,
            None,
        );
    }

    let one_workspace = PcgWorkspace::new(&preconditioner);
    let workspace_bytes = one_workspace.byte_len();
    checkpoints.push(checkpoint("workspace-one", started, workspace_bytes));
    if stop == "workspace-one" {
        return finish(
            output,
            stop,
            threads,
            rhs_count,
            &family,
            vertices,
            graph.edge_count(),
            graph.matrix_nnz(),
            input_bytes,
            graph_bytes,
            hierarchy_bytes,
            plan_bytes,
            checkpoints,
            None,
        );
    }

    let concurrency = executor.batch_concurrency(workspace_bytes, rhs_count)?;
    let mut workspace_pool = (0..concurrency)
        .map(|_| PcgWorkspace::new(&preconditioner))
        .collect::<Vec<_>>();
    checkpoints.push(checkpoint(
        "workspace-pool",
        started,
        workspace_bytes.saturating_mul(concurrency),
    ));
    if stop == "workspace-pool" {
        return finish(
            output,
            stop,
            threads,
            rhs_count,
            &family,
            vertices,
            graph.edge_count(),
            graph.matrix_nnz(),
            input_bytes,
            graph_bytes,
            hierarchy_bytes,
            plan_bytes,
            checkpoints,
            None,
        );
    }

    let options = PcgOptions {
        relative_tolerance: 1.0e-8,
        max_iterations: 1_000,
        ..PcgOptions::default()
    };
    let numerical = if stop == "solve" {
        let result = solve_pcg_with_workspace(
            &graph,
            &preconditioner,
            &right_hand_sides[0],
            options,
            &mut workspace_pool[0],
        )?;
        checkpoints.push(checkpoint("solve", started, 0));
        Some((result.iterations(), result.backward_error()))
    } else {
        let results = solve_pcg_batch_with_executor(
            &graph,
            &preconditioner,
            &right_hand_sides[..rhs_count],
            options,
            &executor,
        )?;
        checkpoints.push(checkpoint("batch", started, 0));
        Some((
            results.iter().map(|value| value.iterations()).sum(),
            results
                .iter()
                .map(|value| value.backward_error())
                .fold(0.0, f64::max),
        ))
    };
    finish(
        output,
        stop,
        threads,
        rhs_count,
        &family,
        vertices,
        graph.edge_count(),
        graph.matrix_nnz(),
        input_bytes,
        graph_bytes,
        hierarchy_bytes,
        plan_bytes,
        checkpoints,
        numerical,
    )
}

#[allow(clippy::too_many_arguments)]
fn finish(
    output: &Path,
    stage: &str,
    threads: usize,
    rhs_count: usize,
    family: &str,
    vertices: usize,
    edges: usize,
    matrix_nonzeros: usize,
    input_bytes: usize,
    graph_bytes: usize,
    hierarchy_bytes: usize,
    plan_bytes: usize,
    checkpoints: Vec<String>,
    numerical: Option<(usize, f64)>,
) -> Result<(), AnyError> {
    let binary_sha256 = sha256sum(&env::current_exe()?)?;
    let numerical = numerical.map_or_else(
        || "null".to_owned(),
        |(iterations, backward)| {
            format!("{{\"iterations\":{iterations},\"max_backward_error\":{backward:.17e}}}")
        },
    );
    let payload = format!(
        concat!(
            "{{\"protocol_version\":\"cmg-scc2-v1\",",
            "\"record_type\":\"memory-stage\",\"run_id\":{},\"task_id\":{},",
            "\"source_commit\":{},\"source_archive_sha256\":{},\"binary_sha256\":{},",
            "\"environment_id\":{},\"implementation\":\"rust\",\"family\":{},",
            "\"vertices\":{},\"canonical_edges\":{},\"matrix_nonzeros\":{},",
            "\"threads\":{},\"rhs_count\":{},\"stage\":{},",
            "\"owned_bytes\":{{\"input\":{},\"graph\":{},\"hierarchy\":{},\"plan\":{}}},",
            "\"checkpoints\":[{}],\"numerical\":{},\"peak_rss_kb\":{},\"success\":true}}\n"
        ),
        json_string(&env::var("CMG_RUN_ID").unwrap_or_else(|_| "local".to_owned())),
        env::var("CMG_TASK_ID")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .unwrap_or(1),
        json_string(SOURCE_COMMIT),
        json_string(SOURCE_ARCHIVE_SHA256),
        json_string(&binary_sha256),
        json_string(&env::var("CMG_ENVIRONMENT_ID").unwrap_or_else(|_| "unknown".to_owned())),
        json_string(family),
        vertices,
        edges,
        matrix_nonzeros,
        threads,
        rhs_count,
        json_string(stage),
        input_bytes,
        graph_bytes,
        hierarchy_bytes,
        plan_bytes,
        checkpoints.join(","),
        numerical,
        peak_rss_kb(),
    );
    atomic_write(output, payload.as_bytes())?;
    println!("CMG_SCC2_MEMORY_SUCCESS stage={stage} threads={threads}");
    Ok(())
}

fn checkpoint(stage: &str, started: Instant, owned_bytes: usize) -> String {
    format!(
        "{{\"stage\":{},\"elapsed_ns\":{},\"peak_rss_kb\":{},\"owned_bytes\":{owned_bytes}}}",
        json_string(stage),
        started.elapsed().as_nanos(),
        peak_rss_kb(),
    )
}

fn hierarchy_bytes(preconditioner: &CmgPreconditioner) -> usize {
    preconditioner
        .hierarchy()
        .levels()
        .iter()
        .map(|level| {
            level.graph().edge_count() * std::mem::size_of::<cmg::Edge>()
                + std::mem::size_of_val(level.graph().diagonal())
                + std::mem::size_of_val(level.inverse_diagonal())
                + level.aggregation().map_or(0, |aggregation| {
                    std::mem::size_of_val(aggregation.labels())
                        + std::mem::size_of_val(aggregation.sizes())
                })
        })
        .sum::<usize>()
        + preconditioner.component_metadata_bytes()
        + preconditioner
            .terminal_factor()
            .map_or(0, |factor| factor.byte_len())
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
        edges.push((
            usize::try_from(read_u64(&mut reader)?)?,
            usize::try_from(read_u64(&mut reader)?)?,
            f64::from_bits(read_u64(&mut reader)?),
        ));
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

fn required(arguments: &mut impl Iterator<Item = String>, name: &str) -> Result<String, AnyError> {
    arguments.next().ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidInput, format!("missing {name}")).into()
    })
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

fn json_string(value: &str) -> String {
    format!("{:?}", value)
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
