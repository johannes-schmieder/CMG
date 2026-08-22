use cmg::Aggregation;
use std::error::Error;
use std::hint::black_box;
use std::io;
use std::os::raw::{c_double, c_uint};
use std::time::Instant;

type AnyError = Box<dyn Error>;

const TARGET_VECTOR_VISITS: usize = 80_000_000;
const ERROR_TOLERANCE: f64 = 2.0e-12;

unsafe extern "C" {
    fn cmg_reference_rmvecmul(
        ci: *const c_uint,
        x: *const c_double,
        n: c_uint,
        y: *mut c_double,
        m: c_uint,
    );

    fn cmg_reference_trmvecmul(
        ci: *const c_uint,
        x: *const c_double,
        m: c_uint,
        y: *mut c_double,
        n: c_uint,
    );
}

pub(crate) struct ProjectionBenchmark {
    coarse_dimension: usize,
    loops: usize,
    restriction_rust_median_ns: u128,
    restriction_c_median_ns: u128,
    restriction_rust_over_c: f64,
    restriction_rust_values_per_second: f64,
    restriction_c_values_per_second: f64,
    restriction_max_abs_error: f64,
    restriction_max_scaled_error: f64,
    prolongation_rust_median_ns: u128,
    prolongation_c_median_ns: u128,
    prolongation_rust_over_c: f64,
    prolongation_rust_values_per_second: f64,
    prolongation_c_values_per_second: f64,
    prolongation_max_abs_error: f64,
    prolongation_max_scaled_error: f64,
}

impl ProjectionBenchmark {
    pub(crate) fn to_json(&self) -> String {
        format!(
            concat!(
                "{{\n",
                "    \"coarse_dimension\": {},\n",
                "    \"loops\": {},\n",
                "    \"restriction_rust_median_ns\": {},\n",
                "    \"restriction_c_median_ns\": {},\n",
                "    \"restriction_rust_over_c\": {:.17e},\n",
                "    \"restriction_rust_values_per_second\": {:.17e},\n",
                "    \"restriction_c_values_per_second\": {:.17e},\n",
                "    \"restriction_max_abs_error\": {:.17e},\n",
                "    \"restriction_max_scaled_error\": {:.17e},\n",
                "    \"prolongation_rust_median_ns\": {},\n",
                "    \"prolongation_c_median_ns\": {},\n",
                "    \"prolongation_rust_over_c\": {:.17e},\n",
                "    \"prolongation_rust_values_per_second\": {:.17e},\n",
                "    \"prolongation_c_values_per_second\": {:.17e},\n",
                "    \"prolongation_max_abs_error\": {:.17e},\n",
                "    \"prolongation_max_scaled_error\": {:.17e}\n",
                "  }}"
            ),
            self.coarse_dimension,
            self.loops,
            self.restriction_rust_median_ns,
            self.restriction_c_median_ns,
            self.restriction_rust_over_c,
            self.restriction_rust_values_per_second,
            self.restriction_c_values_per_second,
            self.restriction_max_abs_error,
            self.restriction_max_scaled_error,
            self.prolongation_rust_median_ns,
            self.prolongation_c_median_ns,
            self.prolongation_rust_over_c,
            self.prolongation_rust_values_per_second,
            self.prolongation_c_values_per_second,
            self.prolongation_max_abs_error,
            self.prolongation_max_scaled_error,
        )
    }
}

struct ReferenceAggregation {
    labels: Vec<c_uint>,
    fine_dimension: c_uint,
    coarse_dimension: c_uint,
}

impl ReferenceAggregation {
    fn from_aggregation(aggregation: &Aggregation) -> Result<Self, AnyError> {
        let fine_dimension = c_uint::try_from(aggregation.fine_dimension())
            .map_err(|_| io::Error::other("fine dimension exceeds upstream C index width"))?;
        let coarse_dimension = c_uint::try_from(aggregation.coarse_dimension())
            .map_err(|_| io::Error::other("coarse dimension exceeds upstream C index width"))?;
        let labels = aggregation
            .labels()
            .iter()
            .map(|&label| {
                c_uint::try_from(label)
                    .map_err(|_| io::Error::other("aggregate label exceeds upstream C index width"))
            })
            .collect::<Result<_, _>>()?;
        Ok(Self {
            labels,
            fine_dimension,
            coarse_dimension,
        })
    }

    fn restrict_into(&self, fine: &[f64], coarse: &mut [f64]) {
        debug_assert_eq!(fine.len(), self.fine_dimension as usize);
        debug_assert_eq!(coarse.len(), self.coarse_dimension as usize);
        // SAFETY: labels are valid zero-based coarse indices and all slices
        // match the dimensions passed to the pinned C kernel.
        unsafe {
            cmg_reference_rmvecmul(
                self.labels.as_ptr(),
                fine.as_ptr(),
                self.fine_dimension,
                coarse.as_mut_ptr(),
                self.coarse_dimension,
            );
        }
    }

    fn prolong_into(&self, coarse: &[f64], fine: &mut [f64]) {
        debug_assert_eq!(coarse.len(), self.coarse_dimension as usize);
        debug_assert_eq!(fine.len(), self.fine_dimension as usize);
        // SAFETY: labels are valid zero-based coarse indices and all slices
        // match the dimensions passed to the pinned C kernel.
        unsafe {
            cmg_reference_trmvecmul(
                self.labels.as_ptr(),
                coarse.as_ptr(),
                self.coarse_dimension,
                fine.as_mut_ptr(),
                self.fine_dimension,
            );
        }
    }
}

pub(crate) fn benchmark(
    case: &str,
    fine_dimension: usize,
    repetitions: usize,
) -> Result<ProjectionBenchmark, AnyError> {
    let aggregation = make_aggregation(case, fine_dimension)?;
    let reference = ReferenceAggregation::from_aggregation(&aggregation)?;
    let fine_input = make_input(fine_dimension, 65_537, 19);
    let coarse_input = make_input(aggregation.coarse_dimension(), 32_771, 41);
    let mut rust_coarse = vec![0.0; aggregation.coarse_dimension()];
    let mut c_coarse = vec![0.0; aggregation.coarse_dimension()];
    let mut rust_fine = vec![0.0; fine_dimension];
    let mut c_fine = vec![0.0; fine_dimension];

    aggregation.restrict_into(&fine_input, &mut rust_coarse)?;
    reference.restrict_into(&fine_input, &mut c_coarse);
    let (restriction_max_abs_error, restriction_max_scaled_error) =
        compare_outputs(&rust_coarse, &c_coarse);
    verify_error("restriction", restriction_max_scaled_error)?;

    aggregation.prolong_into(&coarse_input, &mut rust_fine)?;
    reference.prolong_into(&coarse_input, &mut c_fine);
    let (prolongation_max_abs_error, prolongation_max_scaled_error) =
        compare_outputs(&rust_fine, &c_fine);
    verify_error("prolongation", prolongation_max_scaled_error)?;

    let loops = (TARGET_VECTOR_VISITS / fine_dimension.max(1)).clamp(16, 8_000);
    for _ in 0..4 {
        aggregation.restrict_into(black_box(&fine_input), black_box(&mut rust_coarse))?;
        reference.restrict_into(black_box(&fine_input), black_box(&mut c_coarse));
        aggregation.prolong_into(black_box(&coarse_input), black_box(&mut rust_fine))?;
        reference.prolong_into(black_box(&coarse_input), black_box(&mut c_fine));
    }

    let mut restriction_rust_times = Vec::with_capacity(repetitions);
    let mut restriction_c_times = Vec::with_capacity(repetitions);
    let mut prolongation_rust_times = Vec::with_capacity(repetitions);
    let mut prolongation_c_times = Vec::with_capacity(repetitions);
    for repetition in 0..repetitions {
        if repetition % 2 == 0 {
            restriction_rust_times.push(time_rust_restriction(
                &aggregation,
                &fine_input,
                &mut rust_coarse,
                loops,
            )?);
            restriction_c_times.push(time_c_restriction(
                &reference,
                &fine_input,
                &mut c_coarse,
                loops,
            ));
            prolongation_rust_times.push(time_rust_prolongation(
                &aggregation,
                &coarse_input,
                &mut rust_fine,
                loops,
            )?);
            prolongation_c_times.push(time_c_prolongation(
                &reference,
                &coarse_input,
                &mut c_fine,
                loops,
            ));
        } else {
            restriction_c_times.push(time_c_restriction(
                &reference,
                &fine_input,
                &mut c_coarse,
                loops,
            ));
            restriction_rust_times.push(time_rust_restriction(
                &aggregation,
                &fine_input,
                &mut rust_coarse,
                loops,
            )?);
            prolongation_c_times.push(time_c_prolongation(
                &reference,
                &coarse_input,
                &mut c_fine,
                loops,
            ));
            prolongation_rust_times.push(time_rust_prolongation(
                &aggregation,
                &coarse_input,
                &mut rust_fine,
                loops,
            )?);
        }
    }

    let restriction_rust_median_ns = median(&mut restriction_rust_times);
    let restriction_c_median_ns = median(&mut restriction_c_times);
    let prolongation_rust_median_ns = median(&mut prolongation_rust_times);
    let prolongation_c_median_ns = median(&mut prolongation_c_times);
    let visits = fine_dimension as f64 * loops as f64;

    Ok(ProjectionBenchmark {
        coarse_dimension: aggregation.coarse_dimension(),
        loops,
        restriction_rust_median_ns,
        restriction_c_median_ns,
        restriction_rust_over_c: restriction_rust_median_ns as f64 / restriction_c_median_ns as f64,
        restriction_rust_values_per_second: visits * 1.0e9 / restriction_rust_median_ns as f64,
        restriction_c_values_per_second: visits * 1.0e9 / restriction_c_median_ns as f64,
        restriction_max_abs_error,
        restriction_max_scaled_error,
        prolongation_rust_median_ns,
        prolongation_c_median_ns,
        prolongation_rust_over_c: prolongation_rust_median_ns as f64
            / prolongation_c_median_ns as f64,
        prolongation_rust_values_per_second: visits * 1.0e9 / prolongation_rust_median_ns as f64,
        prolongation_c_values_per_second: visits * 1.0e9 / prolongation_c_median_ns as f64,
        prolongation_max_abs_error,
        prolongation_max_scaled_error,
    })
}

fn make_aggregation(case: &str, fine_dimension: usize) -> Result<Aggregation, AnyError> {
    let group_size = match case {
        "path" => 4,
        "worker-firm" => 7,
        _ => return Err(io::Error::other("case must be path or worker-firm").into()),
    };
    let coarse_dimension = fine_dimension.div_ceil(group_size).max(1);
    let labels = match case {
        "path" => (0..fine_dimension)
            .map(|index| index / group_size)
            .collect(),
        "worker-firm" => (0..fine_dimension)
            .map(|index| {
                index.wrapping_mul(2_654_435_761).wrapping_add(index >> 3) % coarse_dimension
            })
            .collect(),
        _ => unreachable!(),
    };
    Ok(Aggregation::new(labels, coarse_dimension)?)
}

fn time_rust_restriction(
    aggregation: &Aggregation,
    fine: &[f64],
    coarse: &mut [f64],
    loops: usize,
) -> Result<u128, AnyError> {
    let start = Instant::now();
    for _ in 0..loops {
        aggregation.restrict_into(black_box(fine), black_box(&mut *coarse))?;
    }
    Ok(start.elapsed().as_nanos())
}

fn time_c_restriction(
    aggregation: &ReferenceAggregation,
    fine: &[f64],
    coarse: &mut [f64],
    loops: usize,
) -> u128 {
    let start = Instant::now();
    for _ in 0..loops {
        aggregation.restrict_into(black_box(fine), black_box(&mut *coarse));
    }
    start.elapsed().as_nanos()
}

fn time_rust_prolongation(
    aggregation: &Aggregation,
    coarse: &[f64],
    fine: &mut [f64],
    loops: usize,
) -> Result<u128, AnyError> {
    let start = Instant::now();
    for _ in 0..loops {
        aggregation.prolong_into(black_box(coarse), black_box(&mut *fine))?;
    }
    Ok(start.elapsed().as_nanos())
}

fn time_c_prolongation(
    aggregation: &ReferenceAggregation,
    coarse: &[f64],
    fine: &mut [f64],
    loops: usize,
) -> u128 {
    let start = Instant::now();
    for _ in 0..loops {
        aggregation.prolong_into(black_box(coarse), black_box(&mut *fine));
    }
    start.elapsed().as_nanos()
}

fn make_input(length: usize, multiplier: usize, offset: usize) -> Vec<f64> {
    (0..length)
        .map(|index| {
            let code = index.wrapping_mul(multiplier).wrapping_add(offset) % 4_093;
            (code as f64 - 2_046.0) / 257.0
        })
        .collect()
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

fn verify_error(kernel: &str, scaled_error: f64) -> Result<(), AnyError> {
    if scaled_error.is_finite() && scaled_error <= ERROR_TOLERANCE {
        Ok(())
    } else {
        Err(io::Error::other(format!(
            "Rust/C {kernel} mismatch: scaled error {scaled_error:e}"
        ))
        .into())
    }
}

fn median(values: &mut [u128]) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pinned_c_projection_kernels_match_rust() {
        for case in ["path", "worker-firm"] {
            let result = benchmark(case, 64, 1).unwrap();
            assert!(result.restriction_max_scaled_error <= ERROR_TOLERANCE);
            assert!(result.prolongation_max_scaled_error <= ERROR_TOLERANCE);
        }
    }
}
