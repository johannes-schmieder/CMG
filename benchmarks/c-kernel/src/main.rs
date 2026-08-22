use cmg::Laplacian;
use std::env;
use std::error::Error;
use std::fs;
use std::hint::black_box;
use std::io;
use std::os::raw::{c_double, c_uint};
use std::time::Instant;

const UPSTREAM_COMMIT: &str = "19752fc102f8cae8e34f66457bfaccb1aaa60375";
const TARGET_EDGE_VISITS: usize = 40_000_000;
const ERROR_TOLERANCE: f64 = 2.0e-12;
const SOURCE_COMMIT: &str = match option_env!("CMG_BENCH_COMMIT") {
    Some(value) => value,
    None => "unknown",
};

unsafe extern "C" {
    fn cmg_reference_sspmv(
        n: c_uint,
        a: *const c_double,
        ia: *const c_uint,
        ja: *const c_uint,
        x: *const c_double,
        y: *mut c_double,
    );
}

#[derive(Debug, Clone)]
struct Config {
    case: String,
    vertices: usize,
    repetitions: usize,
    output: Option<String>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            case: "worker-firm".to_owned(),
            vertices: 100_000,
            repetitions: 5,
            output: None,
        }
    }
}

struct UpperSymmetric {
    n: c_uint,
    values: Vec<f64>,
    row_offsets: Vec<c_uint>,
    columns: Vec<c_uint>,
}

impl UpperSymmetric {
    fn from_graph(graph: &Laplacian) -> Result<Self, Box<dyn Error>> {
        let n = graph.vertex_count();
        let n_c = c_uint::try_from(n)
            .map_err(|_| io::Error::other("vertex count exceeds upstream C index width"))?;
        let total = n
            .checked_add(graph.edge_count())
            .ok_or_else(|| io::Error::other("upper matrix size overflow"))?;
        c_uint::try_from(total)
            .map_err(|_| io::Error::other("matrix nonzeros exceed upstream C index width"))?;

        let mut counts = vec![1_usize; n];
        for edge in graph.edges() {
            counts[edge.u()] += 1;
        }

        let mut row_offsets_usize = vec![0_usize; n + 1];
        let mut running = 0_usize;
        for (offset, &count) in row_offsets_usize.iter_mut().skip(1).zip(&counts) {
            running = running
                .checked_add(count)
                .ok_or_else(|| io::Error::other("row offset overflow"))?;
            *offset = running;
        }
        let row_offsets: Vec<c_uint> = row_offsets_usize
            .iter()
            .map(|&value| {
                c_uint::try_from(value)
                    .map_err(|_| io::Error::other("row offset exceeds upstream C index width"))
            })
            .collect::<Result<_, _>>()?;

        let mut values = vec![0.0; total];
        let mut columns = vec![0_c_uint; total];
        let mut cursor = vec![0_usize; n];
        for (row, &degree) in graph.diagonal().iter().enumerate() {
            let diagonal = row_offsets_usize[row];
            values[diagonal] = degree;
            columns[diagonal] = c_uint::try_from(row)?;
            cursor[row] = diagonal + 1;
        }
        for edge in graph.edges() {
            let position = cursor[edge.u()];
            values[position] = -edge.weight();
            columns[position] = c_uint::try_from(edge.v())?;
            cursor[edge.u()] += 1;
        }

        Ok(Self {
            n: n_c,
            values,
            row_offsets,
            columns,
        })
    }

    fn matvec_into(&self, input: &[f64], output: &mut [f64]) {
        debug_assert_eq!(input.len(), self.n as usize);
        debug_assert_eq!(output.len(), self.n as usize);
        // SAFETY: every array is valid for the declared matrix dimension. The
        // pinned C kernel only reads matrix/input storage and writes n outputs.
        unsafe {
            cmg_reference_sspmv(
                self.n,
                self.values.as_ptr(),
                self.row_offsets.as_ptr(),
                self.columns.as_ptr(),
                input.as_ptr(),
                output.as_mut_ptr(),
            );
        }
    }
}

fn main() -> Result<(), Box<dyn Error>> {
    let config = parse_config()?;
    if config.vertices < 2 {
        return Err(io::Error::other("vertices must be at least two").into());
    }
    if config.repetitions == 0 {
        return Err(io::Error::other("repetitions must be positive").into());
    }

    let graph = Laplacian::from_edges(
        config.vertices,
        generate_edges(&config.case, config.vertices)?,
    )?;
    let c_matrix = UpperSymmetric::from_graph(&graph)?;
    let input = make_input(graph.vertex_count());
    let mut rust_output = vec![0.0; graph.vertex_count()];
    let mut c_output = vec![0.0; graph.vertex_count()];

    graph.matvec_into(&input, &mut rust_output)?;
    c_matrix.matvec_into(&input, &mut c_output);
    let (max_abs_error, max_scaled_error) = compare_outputs(&rust_output, &c_output);
    if !max_scaled_error.is_finite() || max_scaled_error > ERROR_TOLERANCE {
        return Err(io::Error::other(format!(
            "Rust/C matvec mismatch: scaled error {max_scaled_error:e}"
        ))
        .into());
    }

    let loops = (TARGET_EDGE_VISITS / graph.edge_count().max(1)).clamp(16, 4_000);
    for _ in 0..4 {
        graph.matvec_into(black_box(&input), black_box(&mut rust_output))?;
        c_matrix.matvec_into(black_box(&input), black_box(&mut c_output));
    }

    let mut rust_times = Vec::with_capacity(config.repetitions);
    let mut c_times = Vec::with_capacity(config.repetitions);
    for repetition in 0..config.repetitions {
        if repetition % 2 == 0 {
            rust_times.push(time_rust(&graph, &input, &mut rust_output, loops)?);
            c_times.push(time_c(&c_matrix, &input, &mut c_output, loops));
        } else {
            c_times.push(time_c(&c_matrix, &input, &mut c_output, loops));
            rust_times.push(time_rust(&graph, &input, &mut rust_output, loops)?);
        }
    }

    let rust_median_ns = median(&mut rust_times);
    let c_median_ns = median(&mut c_times);
    let rust_over_c = rust_median_ns as f64 / c_median_ns as f64;
    let edge_visits = graph.edge_count() as f64 * loops as f64;
    let rust_edges_per_second = edge_visits * 1.0e9 / rust_median_ns as f64;
    let c_edges_per_second = edge_visits * 1.0e9 / c_median_ns as f64;

    let json = format!(
        concat!(
            "{{\n",
            "  \"schema\": 1,\n",
            "  \"source_commit\": \"{}\",\n",
            "  \"upstream_commit\": \"{}\",\n",
            "  \"case\": \"{}\",\n",
            "  \"vertices\": {},\n",
            "  \"canonical_edges\": {},\n",
            "  \"loops\": {},\n",
            "  \"repetitions\": {},\n",
            "  \"rust_median_ns\": {},\n",
            "  \"c_median_ns\": {},\n",
            "  \"rust_over_c\": {:.17e},\n",
            "  \"rust_edges_per_second\": {:.17e},\n",
            "  \"c_edges_per_second\": {:.17e},\n",
            "  \"max_abs_error\": {:.17e},\n",
            "  \"max_scaled_error\": {:.17e}\n",
            "}}\n"
        ),
        SOURCE_COMMIT,
        UPSTREAM_COMMIT,
        config.case,
        graph.vertex_count(),
        graph.edge_count(),
        loops,
        config.repetitions,
        rust_median_ns,
        c_median_ns,
        rust_over_c,
        rust_edges_per_second,
        c_edges_per_second,
        max_abs_error,
        max_scaled_error,
    );

    if let Some(path) = config.output {
        fs::write(path, &json)?;
    } else {
        print!("{json}");
    }
    Ok(())
}

fn time_rust(
    graph: &Laplacian,
    input: &[f64],
    output: &mut [f64],
    loops: usize,
) -> Result<u128, Box<dyn Error>> {
    let start = Instant::now();
    for _ in 0..loops {
        graph.matvec_into(black_box(input), black_box(&mut *output))?;
    }
    Ok(start.elapsed().as_nanos())
}

fn time_c(matrix: &UpperSymmetric, input: &[f64], output: &mut [f64], loops: usize) -> u128 {
    let start = Instant::now();
    for _ in 0..loops {
        matrix.matvec_into(black_box(input), black_box(&mut *output));
    }
    start.elapsed().as_nanos()
}

fn compare_outputs(left: &[f64], right: &[f64]) -> (f64, f64) {
    left.iter()
        .zip(right)
        .fold((0.0_f64, 0.0_f64), |state, (&x, &y)| {
            let absolute = (x - y).abs();
            let scale = 1.0_f64.max(x.abs()).max(y.abs());
            (state.0.max(absolute), state.1.max(absolute / scale))
        })
}

fn median(values: &mut [u128]) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn make_input(vertices: usize) -> Vec<f64> {
    (0..vertices)
        .map(|vertex| {
            let code = vertex.wrapping_mul(65_537).wrapping_add(19) % 4_093;
            (code as f64 - 2_046.0) / 257.0
        })
        .collect()
}

fn generate_edges(
    case: &str,
    vertices: usize,
) -> Result<Vec<(usize, usize, f64)>, Box<dyn Error>> {
    match case {
        "path" => Ok((0..vertices - 1)
            .map(|u| (u, u + 1, 1.0 + (u % 17) as f64 / 31.0))
            .collect()),
        "worker-firm" => {
            let workers = (vertices * 3 / 5).clamp(1, vertices - 1);
            let firms = vertices - workers;
            let mut edges = Vec::with_capacity(workers * 2);
            for worker in 0..workers {
                let first = workers + worker % firms;
                let second = workers + worker.wrapping_mul(17).wrapping_add(3) % firms;
                edges.push((
                    worker,
                    first,
                    0.75 + (worker % 29) as f64 / 23.0,
                ));
                if second != first {
                    edges.push((
                        worker,
                        second,
                        0.5 + (worker % 37) as f64 / 19.0,
                    ));
                }
            }
            Ok(edges)
        }
        _ => Err(io::Error::other("case must be path or worker-firm").into()),
    }
}

fn parse_config() -> Result<Config, Box<dyn Error>> {
    let mut config = Config::default();
    let mut arguments = env::args().skip(1);
    while let Some(argument) = arguments.next() {
        let value = arguments
            .next()
            .ok_or_else(|| io::Error::other(format!("missing value for {argument}")))?;
        match argument.as_str() {
            "--case" => config.case = value,
            "--vertices" => config.vertices = value.parse()?,
            "--repetitions" => config.repetitions = value.parse()?,
            "--output" => config.output = Some(value),
            _ => return Err(io::Error::other(format!("unknown option {argument}")).into()),
        }
    }
    Ok(config)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pinned_c_kernel_matches_rust_on_small_graphs() {
        for case in ["path", "worker-firm"] {
            let graph =
                Laplacian::from_edges(64, generate_edges(case, 64).unwrap()).unwrap();
            let matrix = UpperSymmetric::from_graph(&graph).unwrap();
            let input = make_input(64);
            let mut rust = vec![0.0; 64];
            let mut c = vec![0.0; 64];
            graph.matvec_into(&input, &mut rust).unwrap();
            matrix.matvec_into(&input, &mut c);
            let (_, scaled) = compare_outputs(&rust, &c);
            assert!(scaled <= ERROR_TOLERANCE, "{case}: {scaled:e}");
        }
    }
}
