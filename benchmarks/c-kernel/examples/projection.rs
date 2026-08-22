use cmg::Aggregation;
use std::env;
use std::error::Error;
use std::fs;
use std::hint::black_box;
use std::io;
use std::os::raw::{c_double, c_uint};
use std::time::Instant;

const UPSTREAM_COMMIT: &str = "19752fc102f8cae8e34f66457bfaccb1aaa60375";
const TARGET_FINE_ITEMS: usize = 80_000_000;
const ERROR_TOLERANCE: f64 = 2.0e-12;
const SOURCE_COMMIT: &str = match option_env!("CMG_BENCH_COMMIT") {
    Some(value) => value,
    None => "unknown",
};

type AnyError = Box<dyn Error>;

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

#[derive(Debug, Clone)]
struct Config {
    vertices: usize,
    repetitions: usize,
    output: Option<String>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            vertices: 100_000,
            repetitions: 7,
            output: None,
        }
    }
}

struct ProjectionData {
    aggregation: Aggregation,
    labels_c: Vec<c_uint>,
    fine_dimension_c: c_uint,
    coarse_dimension_c: c_uint,
    fine_input: Vec<f64>,
    coarse_input: Vec<f64>,
}

impl ProjectionData {
    fn new(vertices: usize) -> Result<Self, AnyError> {
        if vertices < 2 {
            return Err(io::Error::other("vertices must be at least two").into());
        }
        let coarse_dimension = vertices.div_ceil(4);
        let fine_dimension_c = c_uint::try_from(vertices)
            .map_err(|_| io::Error::other("fine dimension exceeds upstream C index width"))?;
        let coarse_dimension_c = c_uint::try_from(coarse_dimension)
            .map_err(|_| io::Error::other("coarse dimension exceeds upstream C index width"))?;

        let labels: Vec<usize> = (0..vertices)
            .map(|index| index.wrapping_mul(65_537) % vertices / 4)
            .collect();
        let labels_c = labels
            .iter()
            .map(|&label| {
                c_uint::try_from(label)
                    .map_err(|_| io::Error::other("aggregate label exceeds upstream C index width"))
            })
            .collect::<Result<Vec<_>, _>>()?;
        let aggregation = Aggregation::new(labels, coarse_dimension)?;
        let fine_input = make_input(vertices, 31_337);
        let coarse_input = make_input(coarse_dimension, 65_537);

        Ok(Self {
            aggregation,
            labels_c,
            fine_dimension_c,
            coarse_dimension_c,
            fine_input,
            coarse_input,
        })
    }

    fn c_restrict_into(&self, output: &mut [f64]) {
        debug_assert_eq!(output.len(), self.aggregation.coarse_dimension());
        // SAFETY: labels and vectors have the dimensions passed to the pinned C
        // kernel; every label was validated against the coarse dimension.
        unsafe {
            cmg_reference_rmvecmul(
                self.labels_c.as_ptr(),
                self.fine_input.as_ptr(),
                self.fine_dimension_c,
                output.as_mut_ptr(),
                self.coarse_dimension_c,
            );
        }
    }

    fn c_prolong_into(&self, output: &mut [f64]) {
        debug_assert_eq!(output.len(), self.aggregation.fine_dimension());
        // SAFETY: labels and vectors have the dimensions passed to the pinned C
        // kernel; every label was validated against the coarse dimension.
        unsafe {
            cmg_reference_trmvecmul(
                self.labels_c.as_ptr(),
                self.coarse_input.as_ptr(),
                self.coarse_dimension_c,
                output.as_mut_ptr(),
                self.fine_dimension_c,
            );
        }
    }
}

fn main() -> Result<(), AnyError> {
    let config = parse_config()?;
    if config.repetitions == 0 {
        return Err(io::Error::other("repetitions must be positive").into());
    }
    let data = ProjectionData::new(config.vertices)?;
    let loops = (TARGET_FINE_ITEMS / config.vertices).clamp(16, 4_000);

    let mut rust_restricted = vec![0.0; data.aggregation.coarse_dimension()];
    let mut c_restricted = vec![0.0; data.aggregation.coarse_dimension()];
    let mut rust_prolonged = vec![0.0; data.aggregation.fine_dimension()];
    let mut c_prolonged = vec![0.0; data.aggregation.fine_dimension()];

    data.aggregation
        .restrict_into(&data.fine_input, &mut rust_restricted)?;
    data.c_restrict_into(&mut c_restricted);
    data.aggregation
        .prolong_into(&data.coarse_input, &mut rust_prolonged)?;
    data.c_prolong_into(&mut c_prolonged);

    let (restrict_max_abs_error, restrict_max_scaled_error) =
        compare_outputs(&rust_restricted, &c_restricted);
    let (prolong_max_abs_error, prolong_max_scaled_error) =
        compare_outputs(&rust_prolonged, &c_prolonged);
    if restrict_max_scaled_error > ERROR_TOLERANCE
        || prolong_max_scaled_error > ERROR_TOLERANCE
        || !restrict_max_scaled_error.is_finite()
        || !prolong_max_scaled_error.is_finite()
    {
        return Err(io::Error::other(format!(
            "Rust/C projection mismatch: restrict={restrict_max_scaled_error:e}, prolong={prolong_max_scaled_error:e}"
        ))
        .into());
    }

    for _ in 0..4 {
        data.aggregation
            .restrict_into(black_box(&data.fine_input), black_box(&mut rust_restricted))?;
        data.c_restrict_into(black_box(&mut c_restricted));
        data.aggregation
            .prolong_into(black_box(&data.coarse_input), black_box(&mut rust_prolonged))?;
        data.c_prolong_into(black_box(&mut c_prolonged));
    }

    let mut rust_restrict_times = Vec::with_capacity(config.repetitions);
    let mut c_restrict_times = Vec::with_capacity(config.repetitions);
    let mut rust_prolong_times = Vec::with_capacity(config.repetitions);
    let mut c_prolong_times = Vec::with_capacity(config.repetitions);

    for repetition in 0..config.repetitions {
        if repetition % 2 == 0 {
            rust_restrict_times.push(time_rust_restrict(
                &data,
                &mut rust_restricted,
                loops,
            )?);
            c_restrict_times.push(time_c_restrict(&data, &mut c_restricted, loops));
            rust_prolong_times.push(time_rust_prolong(
                &data,
                &mut rust_prolonged,
                loops,
            )?);
            c_prolong_times.push(time_c_prolong(&data, &mut c_prolonged, loops));
        } else {
            c_prolong_times.push(time_c_prolong(&data, &mut c_prolonged, loops));
            rust_prolong_times.push(time_rust_prolong(
                &data,
                &mut rust_prolonged,
                loops,
            )?);
            c_restrict_times.push(time_c_restrict(&data, &mut c_restricted, loops));
            rust_restrict_times.push(time_rust_restrict(
                &data,
                &mut rust_restricted,
                loops,
            )?);
        }
    }

    let rust_restrict_median_ns = median(&mut rust_restrict_times);
    let c_restrict_median_ns = median(&mut c_restrict_times);
    let rust_prolong_median_ns = median(&mut rust_prolong_times);
    let c_prolong_median_ns = median(&mut c_prolong_times);

    let json = format!(
        concat!(
            "{{\n",
            "  \"schema\": 1,\n",
            "  \"source_commit\": \"{}\",\n",
            "  \"upstream_commit\": \"{}\",\n",
            "  \"fine_dimension\": {},\n",
            "  \"coarse_dimension\": {},\n",
            "  \"loops\": {},\n",
            "  \"repetitions\": {},\n",
            "  \"rust_restrict_median_ns\": {},\n",
            "  \"c_restrict_median_ns\": {},\n",
            "  \"rust_over_c_restrict\": {:.17e},\n",
            "  \"rust_prolong_median_ns\": {},\n",
            "  \"c_prolong_median_ns\": {},\n",
            "  \"rust_over_c_prolong\": {:.17e},\n",
            "  \"restrict_max_abs_error\": {:.17e},\n",
            "  \"restrict_max_scaled_error\": {:.17e},\n",
            "  \"prolong_max_abs_error\": {:.17e},\n",
            "  \"prolong_max_scaled_error\": {:.17e}\n",
            "}}\n"
        ),
        SOURCE_COMMIT,
        UPSTREAM_COMMIT,
        data.aggregation.fine_dimension(),
        data.aggregation.coarse_dimension(),
        loops,
        config.repetitions,
        rust_restrict_median_ns,
        c_restrict_median_ns,
        rust_restrict_median_ns as f64 / c_restrict_median_ns as f64,
        rust_prolong_median_ns,
        c_prolong_median_ns,
        rust_prolong_median_ns as f64 / c_prolong_median_ns as f64,
        restrict_max_abs_error,
        restrict_max_scaled_error,
        prolong_max_abs_error,
        prolong_max_scaled_error,
    );

    if let Some(path) = config.output {
        fs::write(path, &json)?;
    } else {
        print!("{json}");
    }
    Ok(())
}

fn time_rust_restrict(
    data: &ProjectionData,
    output: &mut [f64],
    loops: usize,
) -> Result<u128, AnyError> {
    let start = Instant::now();
    for _ in 0..loops {
        data.aggregation
            .restrict_into(black_box(&data.fine_input), black_box(&mut *output))?;
    }
    Ok(start.elapsed().as_nanos())
}

fn time_c_restrict(data: &ProjectionData, output: &mut [f64], loops: usize) -> u128 {
    let start = Instant::now();
    for _ in 0..loops {
        data.c_restrict_into(black_box(&mut *output));
    }
    start.elapsed().as_nanos()
}

fn time_rust_prolong(
    data: &ProjectionData,
    output: &mut [f64],
    loops: usize,
) -> Result<u128, AnyError> {
    let start = Instant::now();
    for _ in 0..loops {
        data.aggregation
            .prolong_into(black_box(&data.coarse_input), black_box(&mut *output))?;
    }
    Ok(start.elapsed().as_nanos())
}

fn time_c_prolong(data: &ProjectionData, output: &mut [f64], loops: usize) -> u128 {
    let start = Instant::now();
    for _ in 0..loops {
        data.c_prolong_into(black_box(&mut *output));
    }
    start.elapsed().as_nanos()
}

fn make_input(length: usize, multiplier: usize) -> Vec<f64> {
    (0..length)
        .map(|index| {
            let code = index.wrapping_mul(multiplier).wrapping_add(19) % 4_093;
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

fn median(values: &mut [u128]) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn parse_config() -> Result<Config, AnyError> {
    let mut config = Config::default();
    let mut arguments = env::args().skip(1);
    while let Some(argument) = arguments.next() {
        let value = arguments
            .next()
            .ok_or_else(|| io::Error::other(format!("missing value for {argument}")))?;
        match argument.as_str() {
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
    fn pinned_c_projection_matches_rust() {
        for vertices in [64, 65, 257] {
            let data = ProjectionData::new(vertices).unwrap();
            let mut rust_restricted = vec![0.0; data.aggregation.coarse_dimension()];
            let mut c_restricted = vec![0.0; data.aggregation.coarse_dimension()];
            let mut rust_prolonged = vec![0.0; data.aggregation.fine_dimension()];
            let mut c_prolonged = vec![0.0; data.aggregation.fine_dimension()];

            data.aggregation
                .restrict_into(&data.fine_input, &mut rust_restricted)
                .unwrap();
            data.c_restrict_into(&mut c_restricted);
            data.aggregation
                .prolong_into(&data.coarse_input, &mut rust_prolonged)
                .unwrap();
            data.c_prolong_into(&mut c_prolonged);

            assert_eq!(rust_restricted, c_restricted);
            assert_eq!(rust_prolonged, c_prolonged);
        }
    }
}
