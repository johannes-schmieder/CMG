use cmg::{CmgOptions, CmgPreconditioner, Laplacian};
use std::error::Error;
use std::hint::black_box;
use std::io;
use std::os::raw::{c_double, c_int, c_uint};
use std::time::Instant;

const CYCLE_DIMENSION_LIMIT: usize = 20_000;
const TARGET_FINE_VISITS: usize = 1_000_000;
const ERROR_TOLERANCE: f64 = 5.0e-10;

type AnyError = Box<dyn Error>;
type EdgeSpec = (usize, usize, f64);

unsafe extern "C" {
    fn cmg_reference_apply_iterative(
        levels: *mut ReferenceLevel,
        level_count: c_uint,
        rhs: *const c_double,
        output: *mut c_double,
    );
}

#[repr(C)]
struct ReferenceLevel {
    cluster_index: *const c_uint,
    coarse_dimension: c_uint,
    matrix_values: *const c_double,
    row_offsets: *const c_uint,
    column_indices: *const c_uint,
    dimension: c_uint,
    inverse_diagonal: *const c_double,
    repeat: c_int,
    is_last: c_uint,
    large_workspace_one: *mut c_double,
    large_workspace_two: *mut c_double,
    small_workspace_one: *mut c_double,
    small_workspace_two: *mut c_double,
}

struct UpperSymmetric {
    values: Vec<f64>,
    row_offsets: Vec<c_uint>,
    columns: Vec<c_uint>,
}

impl UpperSymmetric {
    fn from_graph(graph: &Laplacian) -> Result<Self, AnyError> {
        let dimension = graph.vertex_count();
        let total = dimension
            .checked_add(graph.edge_count())
            .ok_or_else(|| io::Error::other("cycle matrix size overflow"))?;
        c_uint::try_from(dimension)
            .map_err(|_| io::Error::other("cycle dimension exceeds upstream C index width"))?;
        c_uint::try_from(total)
            .map_err(|_| io::Error::other("cycle nonzeros exceed upstream C index width"))?;

        let mut counts = vec![1_usize; dimension];
        for edge in graph.edges() {
            counts[edge.u()] += 1;
        }
        let mut offsets_usize = vec![0_usize; dimension + 1];
        let mut running = 0_usize;
        for (offset, &count) in offsets_usize.iter_mut().skip(1).zip(&counts) {
            running = running
                .checked_add(count)
                .ok_or_else(|| io::Error::other("cycle row-offset overflow"))?;
            *offset = running;
        }
        let row_offsets = offsets_usize
            .iter()
            .map(|&offset| {
                c_uint::try_from(offset)
                    .map_err(|_| io::Error::other("cycle row offset exceeds C index width"))
            })
            .collect::<Result<Vec<_>, _>>()?;

        let mut values = vec![0.0; total];
        let mut columns = vec![c_uint::default(); total];
        let mut cursor = vec![0_usize; dimension];
        for (row, &diagonal) in graph.diagonal().iter().enumerate() {
            let position = offsets_usize[row];
            values[position] = diagonal;
            columns[position] = c_uint::try_from(row)?;
            cursor[row] = position + 1;
        }
        for edge in graph.edges() {
            let position = cursor[edge.u()];
            values[position] = -edge.weight();
            columns[position] = c_uint::try_from(edge.v())?;
            cursor[edge.u()] += 1;
        }
        Ok(Self {
            values,
            row_offsets,
            columns,
        })
    }
}

struct LevelStorage {
    matrix: UpperSymmetric,
    labels: Vec<c_uint>,
    inverse_diagonal: Vec<f64>,
    large_workspace_one: Vec<f64>,
    large_workspace_two: Vec<f64>,
    small_workspace_one: Vec<f64>,
    small_workspace_two: Vec<f64>,
}

struct ReferenceHierarchy {
    levels: Vec<ReferenceLevel>,
    _storage: Vec<LevelStorage>,
}

impl ReferenceHierarchy {
    fn from_preconditioner(preconditioner: &CmgPreconditioner) -> Result<Self, AnyError> {
        if preconditioner.terminal_factor().is_some() {
            return Err(io::Error::other(
                "recursive C comparison requires an iterative terminal",
            )
            .into());
        }
        let hierarchy_levels = preconditioner.hierarchy().levels();
        if hierarchy_levels.len() < 2 {
            return Err(io::Error::other(
                "recursive C comparison requires at least two hierarchy levels",
            )
            .into());
        }

        let mut storage = Vec::with_capacity(hierarchy_levels.len());
        for level in hierarchy_levels {
            let dimension = level.graph().vertex_count();
            let aggregation = level.aggregation();
            let labels = aggregation
                .map(|value| {
                    value
                        .labels()
                        .iter()
                        .map(|&label| {
                            c_uint::try_from(label).map_err(|_| {
                                io::Error::other("cycle aggregate label exceeds C index width")
                            })
                        })
                        .collect::<Result<Vec<_>, _>>()
                })
                .transpose()?
                .unwrap_or_default();
            let coarse_dimension = aggregation
                .map(cmg::Aggregation::coarse_dimension)
                .unwrap_or(0);
            storage.push(LevelStorage {
                matrix: UpperSymmetric::from_graph(level.graph())?,
                labels,
                inverse_diagonal: level.inverse_diagonal().to_vec(),
                large_workspace_one: vec![0.0; dimension],
                large_workspace_two: vec![0.0; dimension],
                small_workspace_one: vec![0.0; coarse_dimension],
                small_workspace_two: vec![0.0; coarse_dimension],
            });
        }

        let mut levels = Vec::with_capacity(hierarchy_levels.len());
        for (index, (level, local)) in hierarchy_levels.iter().zip(&mut storage).enumerate() {
            let aggregation = level.aggregation();
            let coarse_dimension = aggregation
                .map(cmg::Aggregation::coarse_dimension)
                .unwrap_or(0);
            levels.push(ReferenceLevel {
                cluster_index: local.labels.as_ptr(),
                coarse_dimension: c_uint::try_from(coarse_dimension)?,
                matrix_values: local.matrix.values.as_ptr(),
                row_offsets: local.matrix.row_offsets.as_ptr(),
                column_indices: local.matrix.columns.as_ptr(),
                dimension: c_uint::try_from(level.graph().vertex_count())?,
                inverse_diagonal: local.inverse_diagonal.as_ptr(),
                repeat: c_int::try_from(preconditioner.repeat_counts()[index])?,
                is_last: c_uint::from(index + 1 == hierarchy_levels.len()),
                large_workspace_one: local.large_workspace_one.as_mut_ptr(),
                large_workspace_two: local.large_workspace_two.as_mut_ptr(),
                small_workspace_one: local.small_workspace_one.as_mut_ptr(),
                small_workspace_two: local.small_workspace_two.as_mut_ptr(),
            });
        }
        Ok(Self {
            levels,
            _storage: storage,
        })
    }

    fn apply_into(&mut self, rhs: &[f64], output: &mut [f64]) -> Result<(), AnyError> {
        let dimension = self
            .levels
            .first()
            .map(|level| level.dimension as usize)
            .ok_or_else(|| io::Error::other("reference hierarchy is empty"))?;
        if rhs.len() != dimension || output.len() != dimension {
            return Err(io::Error::other("reference cycle dimension mismatch").into());
        }
        let level_count = c_uint::try_from(self.levels.len())?;
        // SAFETY: descriptors point into storage owned by `self`, all vectors
        // match their declared dimensions, and the C adapter writes only those
        // workspaces and the supplied output vector.
        unsafe {
            cmg_reference_apply_iterative(
                self.levels.as_mut_ptr(),
                level_count,
                rhs.as_ptr(),
                output.as_mut_ptr(),
            );
        }
        Ok(())
    }
}

pub(crate) struct CycleBenchmark {
    dimension: usize,
    hierarchy_levels: usize,
    loops: usize,
    rust_median_ns: u128,
    c_median_ns: u128,
    rust_over_c: f64,
    raw_max_abs_error: f64,
    raw_max_scaled_error: f64,
    quotient_max_abs_error: f64,
    quotient_max_scaled_error: f64,
}

impl CycleBenchmark {
    pub(crate) fn to_json(&self) -> String {
        format!(
            concat!(
                "{{\n",
                "    \"dimension\": {},\n",
                "    \"hierarchy_levels\": {},\n",
                "    \"loops\": {},\n",
                "    \"rust_median_ns\": {},\n",
                "    \"c_median_ns\": {},\n",
                "    \"rust_over_c\": {:.17e},\n",
                "    \"raw_max_abs_error\": {:.17e},\n",
                "    \"raw_max_scaled_error\": {:.17e},\n",
                "    \"quotient_max_abs_error\": {:.17e},\n",
                "    \"quotient_max_scaled_error\": {:.17e}\n",
                "  }}"
            ),
            self.dimension,
            self.hierarchy_levels,
            self.loops,
            self.rust_median_ns,
            self.c_median_ns,
            self.rust_over_c,
            self.raw_max_abs_error,
            self.raw_max_scaled_error,
            self.quotient_max_abs_error,
            self.quotient_max_scaled_error,
        )
    }
}

pub(crate) fn benchmark(
    case: &str,
    requested_dimension: usize,
    repetitions: usize,
) -> Result<CycleBenchmark, AnyError> {
    let dimension = requested_dimension.min(CYCLE_DIMENSION_LIMIT).max(128);
    let (graph, rhs) = make_problem(case, dimension)?;
    let preconditioner = CmgPreconditioner::build(
        &graph,
        CmgOptions {
            direct_threshold: 1,
            ..CmgOptions::default()
        },
    )?;
    let hierarchy_levels = preconditioner.hierarchy().levels().len();
    let mut reference = ReferenceHierarchy::from_preconditioner(&preconditioner)?;
    let mut rust_workspace = preconditioner.workspace();
    let mut rust_output = vec![0.0; dimension];
    let mut c_output = vec![0.0; dimension];

    preconditioner.apply_into(&rhs, &mut rust_output, &mut rust_workspace)?;
    reference.apply_into(&rhs, &mut c_output)?;
    let (raw_max_abs_error, raw_max_scaled_error) = compare_outputs(&rust_output, &c_output);
    let mut rust_quotient = rust_output.clone();
    let mut c_quotient = c_output.clone();
    center_in_place(&mut rust_quotient);
    center_in_place(&mut c_quotient);
    let (quotient_max_abs_error, quotient_max_scaled_error) =
        compare_outputs(&rust_quotient, &c_quotient);
    if !quotient_max_scaled_error.is_finite() || quotient_max_scaled_error > ERROR_TOLERANCE {
        return Err(io::Error::other(format!(
            "Rust/C recursive cycle mismatch: raw={raw_max_scaled_error:e}, quotient={quotient_max_scaled_error:e}"
        ))
        .into());
    }

    for _ in 0..3 {
        preconditioner.apply_into(
            black_box(&rhs),
            black_box(&mut rust_output),
            &mut rust_workspace,
        )?;
        reference.apply_into(black_box(&rhs), black_box(&mut c_output))?;
    }

    let loops = (TARGET_FINE_VISITS / dimension).clamp(4, 64);
    let mut rust_times = Vec::with_capacity(repetitions);
    let mut c_times = Vec::with_capacity(repetitions);
    for repetition in 0..repetitions {
        if repetition % 2 == 0 {
            rust_times.push(time_rust(
                &preconditioner,
                &rhs,
                &mut rust_output,
                &mut rust_workspace,
                loops,
            )?);
            c_times.push(time_c(&mut reference, &rhs, &mut c_output, loops)?);
        } else {
            c_times.push(time_c(&mut reference, &rhs, &mut c_output, loops)?);
            rust_times.push(time_rust(
                &preconditioner,
                &rhs,
                &mut rust_output,
                &mut rust_workspace,
                loops,
            )?);
        }
    }
    let rust_median_ns = median(&mut rust_times);
    let c_median_ns = median(&mut c_times);

    Ok(CycleBenchmark {
        dimension,
        hierarchy_levels,
        loops,
        rust_median_ns,
        c_median_ns,
        rust_over_c: rust_median_ns as f64 / c_median_ns as f64,
        raw_max_abs_error,
        raw_max_scaled_error,
        quotient_max_abs_error,
        quotient_max_scaled_error,
    })
}

fn time_rust(
    preconditioner: &CmgPreconditioner,
    rhs: &[f64],
    output: &mut [f64],
    workspace: &mut cmg::CmgWorkspace,
    loops: usize,
) -> Result<u128, AnyError> {
    let start = Instant::now();
    for _ in 0..loops {
        preconditioner.apply_into(black_box(rhs), black_box(&mut *output), workspace)?;
    }
    Ok(start.elapsed().as_nanos())
}

fn time_c(
    reference: &mut ReferenceHierarchy,
    rhs: &[f64],
    output: &mut [f64],
    loops: usize,
) -> Result<u128, AnyError> {
    let start = Instant::now();
    for _ in 0..loops {
        reference.apply_into(black_box(rhs), black_box(&mut *output))?;
    }
    Ok(start.elapsed().as_nanos())
}

fn make_problem(case: &str, dimension: usize) -> Result<(Laplacian, Vec<f64>), AnyError> {
    let edges = generate_edges(case, dimension)?;
    let graph = Laplacian::from_edges(dimension, edges)?;
    let mut rhs = vec![0.0; dimension];
    match case {
        "path" => {
            for pair in 0..8 {
                let value = 2.0_f64.powi(-(pair as i32));
                rhs[pair] += value;
                rhs[dimension - 1 - pair] -= value;
            }
        }
        "worker-firm" => {
            let workers = dimension * 3 / 5;
            for pair in 0..8 {
                let value = 2.0_f64.powi(-(pair as i32));
                rhs[pair] += value;
                rhs[workers + pair] -= value;
            }
        }
        _ => return Err(io::Error::other("case must be path or worker-firm").into()),
    }
    Ok((graph, rhs))
}

fn generate_edges(case: &str, dimension: usize) -> Result<Vec<EdgeSpec>, AnyError> {
    match case {
        "path" => Ok((0..dimension - 1)
            .map(|left| (left, left + 1, 1.0 + (left % 17) as f64 / 31.0))
            .collect()),
        "worker-firm" => {
            let workers = dimension * 3 / 5;
            let firms = dimension - workers;
            let mut edges = Vec::with_capacity(workers * 2);
            for worker in 0..workers {
                let first = workers + worker % firms;
                let second = workers + (worker + 1) % firms;
                edges.push((worker, first, 0.75 + (worker % 29) as f64 / 23.0));
                edges.push((worker, second, 0.5 + (worker % 37) as f64 / 19.0));
            }
            Ok(edges)
        }
        _ => Err(io::Error::other("case must be path or worker-firm").into()),
    }
}

fn center_in_place(values: &mut [f64]) {
    let mean = values.iter().sum::<f64>() / values.len() as f64;
    for value in values {
        *value -= mean;
    }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iterative_recursive_cycle_matches_pinned_c() {
        for case in ["path", "worker-firm"] {
            let result = benchmark(case, 512, 1).unwrap();
            assert!(result.hierarchy_levels >= 2);
            assert!(result.quotient_max_scaled_error <= ERROR_TOLERANCE);
        }
    }
}
