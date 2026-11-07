//! Native two-way FE adapter. No R API is called from Rust or its workers.
#![deny(unsafe_op_in_unsafe_fn)]
use cmg::{
    BuildContext, BuildOptions, CmgOptions, CmgPreconditioner, Laplacian, ParallelOptions,
    PcgOptions, PcgWorkspace, SolveContext, Team, solve_pcg_into,
};
use rayon::prelude::*;
mod schur;
use std::{
    ffi::c_char,
    panic::{AssertUnwindSafe, catch_unwind},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread::JoinHandle,
    time::Instant,
};

#[repr(C)]
#[derive(Clone, Copy)]
pub struct Column {
    input: *const f64,
    output: *mut f64,
}
#[repr(C)]
#[derive(Clone, Copy)]
pub struct Input {
    n: usize,
    p: usize,
    na: usize,
    nb: usize,
    a: *const i32,
    b: *const i32,
    weights: *const f64,
    columns: *const Column,
    tol: f64,
    maxiter: usize,
    threads: usize,
    schur: usize,
    iterations: *mut i32,
    moments: *mut f64,
    relative_residuals: *mut f64,
    stats: *mut f64,
    reduction_stats: *mut f64,
}
// The C caller protects all R objects and waits for finish, including on
// interrupt. Inputs are immutable; each output has one exclusive writer.
struct Request(Input);
unsafe impl Send for Request {}
struct Job {
    cancel: Arc<AtomicBool>,
    handle: JoinHandle<Result<(), String>>,
}

fn zero(n: usize) -> Result<Vec<f64>, String> {
    let mut v = Vec::new();
    v.try_reserve_exact(n).map_err(|e| e.to_string())?;
    v.resize(n, 0.0);
    Ok(v)
}
fn add(sum: &mut f64, correction: &mut f64, x: f64) {
    let next = *sum + x;
    *correction += if sum.abs() >= x.abs() {
        (*sum - next) + x
    } else {
        (x - next) + *sum
    };
    *sum = next;
}
fn checkpoint(cancel: &AtomicBool) -> Result<(), String> {
    if cancel.load(Ordering::Relaxed) {
        Err("cancelled".into())
    } else {
        Ok(())
    }
}
struct View<'a> {
    x: &'a [f64],
    out: &'a mut [f64],
    iterations: &'a mut i32,
    moment: &'a mut f64,
    relative_residual: &'a mut f64,
}
struct Prepared<'a> {
    a: &'a [i32],
    b: &'a [i32],
    w: &'a [f64],
    na: usize,
    graph: Laplacian,
    pre: CmgPreconditioner,
    reduction: Option<schur::Reduction>,
    degree: Vec<f64>,
    tol: f64,
    maxiter: usize,
}
impl Prepared<'_> {
    fn project(
        &self,
        v: &mut View<'_>,
        team: Option<Team<'_>>,
        cancel: &AtomicBool,
    ) -> Result<(), String> {
        let n = self.a.len();
        let q = self.degree.len();
        let mut scale = 0.0f64;
        for (i, &x) in v.x.iter().enumerate() {
            if i % 4096 == 0 {
                checkpoint(cancel)?;
            }
            if !x.is_finite() {
                return Err("nonfinite input column".into());
            }
            scale = scale.max(x.abs());
        }
        if scale == 0.0 {
            v.out.fill(0.0);
            *v.iterations = 0;
            *v.moment = 0.0;
            *v.relative_residual = 0.0;
            return Ok(());
        }
        let (mut mean, mut mc, mut sw, mut wc) = (0.0, 0.0, 0.0, 0.0);
        for (i, (&x, &w)) in v.x.iter().zip(self.w).enumerate() {
            if i % 4096 == 0 {
                checkpoint(cancel)?;
            }
            add(&mut mean, &mut mc, w * (x / scale));
            add(&mut sw, &mut wc, w);
        }
        mean = (mean + mc) / (sw + wc);
        let mut rhs = zero(q)?;
        let mut correction = zero(q)?;
        let mut vnorm2 = 0.0;
        let mut vc = 0.0;
        for i in 0..n {
            if i % 4096 == 0 {
                checkpoint(cancel)?;
            }
            let x = v.x[i] / scale - mean;
            let wx = self.w[i] * x;
            let a = self.a[i] as usize - 1;
            let b = self.na + self.b[i] as usize - 1;
            add(&mut rhs[a], &mut correction[a], wx);
            add(&mut rhs[b], &mut correction[b], -wx);
            add(&mut vnorm2, &mut vc, wx * x);
        }
        for (a, b) in rhs.iter_mut().zip(&correction) {
            *a += b;
        }
        let reduced_rhs = self
            .reduction
            .as_ref()
            .map(|reduction| {
                reduction.rhs(
                    self.a, self.b, self.na, self.w, v.x, scale, mean, &rhs, cancel,
                )
            })
            .transpose()?;
        let solve_rhs = reduced_rhs.as_deref().unwrap_or(&rhs);
        let norm = solve_rhs.iter().map(|v| v * v).sum::<f64>().sqrt();
        let vnorm = (vnorm2 + vc).max(0.0).sqrt();
        let mut solution = zero(self.graph.vertex_count())?;
        if norm > 0.0 {
            let minimum_degree = self.degree.iter().copied().fold(f64::INFINITY, f64::min);
            let target =
                (self.tol * 0.01 * norm).min(self.tol * 0.01 * vnorm * minimum_degree.sqrt());
            if !target.is_finite() || target <= 0.0 {
                return Err("requested residual target underflowed".into());
            }
            let mut workspace = PcgWorkspace::try_new(&self.pre).map_err(|e| e.to_string())?;
            let query = || cancel.load(Ordering::Relaxed);
            let diagnostics = solve_pcg_into(
                &self.graph,
                &self.pre,
                solve_rhs,
                None,
                &mut solution,
                PcgOptions {
                    relative_tolerance: 0.0,
                    absolute_tolerance: target,
                    max_iterations: self.maxiter,
                    ..Default::default()
                },
                &mut workspace,
                SolveContext {
                    team,
                    cancel: Some(&query),
                    ..Default::default()
                },
            )
            .map_err(|e| e.to_string())?;
            *v.iterations = i32::try_from(diagnostics.iterations()).map_err(|e| e.to_string())?;
            *v.relative_residual = diagnostics.relative_residual();
        } else {
            *v.iterations = 0;
            *v.relative_residual = 0.0;
        }
        if let Some(reduction) = &self.reduction {
            solution = reduction.recover(self.na, &rhs, &solution, cancel)?;
        }
        rhs.fill(0.0);
        correction.fill(0.0);
        for i in 0..n {
            if i % 4096 == 0 {
                checkpoint(cancel)?;
            }
            let a = self.a[i] as usize - 1;
            let b = self.na + self.b[i] as usize - 1;
            let residual = (v.x[i] / scale - mean) - (solution[a] - solution[b]);
            v.out[i] = residual * scale;
            if !v.out[i].is_finite() {
                return Err("nonfinite residualized output".into());
            }
            // Re-read the delivered output, including its rounding on rescaling.
            let wr = self.w[i] * (v.out[i] / scale);
            add(&mut rhs[a], &mut correction[a], wr);
            add(&mut rhs[b], &mut correction[b], wr);
        }
        let moment = rhs
            .iter()
            .zip(correction)
            .zip(&self.degree)
            .map(|((&r, c), &d)| (r + c).abs() / d.sqrt())
            .fold(0.0, f64::max);
        *v.moment = if vnorm > 0.0 { moment / vnorm } else { moment };
        if !v.moment.is_finite() || *v.moment > self.tol {
            return Err(format!(
                "weighted FE moment certificate failed: {:.3e} > {:.3e}",
                v.moment, self.tol
            ));
        }
        checkpoint(cancel)
    }
}
impl Request {
    // Safety follows from the registered C entry point's dimension/storage
    // checks and protected lifetime. Raw pointers never escape this joined job.
    fn run(self, cancel: &AtomicBool) -> Result<(), String> {
        let input = self.0;
        if input.n < 2
            || input.schur > 1
            || input.p == 0
            || input.na == 0
            || input.nb == 0
            || input.na > input.n
            || input.nb > input.n
            || input.threads == 0
            || input.maxiter == 0
            || !input.tol.is_finite()
            || input.tol <= 0.0
            || input.tol >= 1.0
        {
            return Err("invalid native dimensions or controls".into());
        }
        let q = input
            .na
            .checked_add(input.nb)
            .ok_or("vertex count overflow")?;
        let start = Instant::now();
        let a = unsafe { std::slice::from_raw_parts(input.a, input.n) };
        let b = unsafe { std::slice::from_raw_parts(input.b, input.n) };
        let weights = if input.weights.is_null() {
            None
        } else {
            Some(unsafe { std::slice::from_raw_parts(input.weights, input.n) })
        };
        let mut w = zero(input.n)?;
        let mut max_weight = 1.0f64;
        if let Some(weights) = weights {
            max_weight = 0.0;
            for (i, &x) in weights.iter().enumerate() {
                if i % 4096 == 0 {
                    checkpoint(cancel)?;
                }
                if !x.is_finite() || x <= 0.0 {
                    return Err("weights must be finite and strictly positive".into());
                }
                max_weight = max_weight.max(x);
            }
        }
        let mut degree = zero(q)?;
        for i in 0..input.n {
            if i % 4096 == 0 {
                checkpoint(cancel)?;
            }
            if a[i] < 1 || a[i] as usize > input.na || b[i] < 1 || b[i] as usize > input.nb {
                return Err("fixed-effect ID outside its declared range".into());
            }
            w[i] = weights.map_or(1.0, |v| v[i] / max_weight);
            if w[i] == 0.0 {
                return Err(
                    "weight normalization underflowed; weights have excessive dynamic range".into(),
                );
            }
            degree[a[i] as usize - 1] += w[i];
            degree[input.na + b[i] as usize - 1] += w[i];
        }
        if degree.iter().any(|d| *d <= 0.0 || !d.is_finite()) {
            return Err("declared fixed-effect levels must all occur".into());
        }
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(input.threads)
            .build()
            .map_err(|e| e.to_string())?;
        let team = Team::new(
            &pool,
            input.threads,
            ParallelOptions {
                threads: input.threads,
                ..Default::default()
            },
        )
        .map_err(|e| e.to_string())?;
        let query = || cancel.load(Ordering::Relaxed);
        let context = BuildContext {
            team: Some(team),
            cancel: Some(&query),
            budget_bytes: None,
        };
        let (reduction, graph) = if input.schur == 1 {
            let (reduction, graph) =
                schur::Reduction::build(a, b, input.na, input.nb, &w, context, cancel)?;
            (Some(reduction), graph)
        } else {
            let graph = Laplacian::from_edges_with(
                q,
                (0..input.n).map(|i| (a[i] as usize - 1, input.na + b[i] as usize - 1, w[i])),
                context,
            )
            .map_err(|e| e.to_string())?;
            (None, graph)
        };
        let vertices = graph.vertex_count();
        let reduction_stats = unsafe { std::slice::from_raw_parts_mut(input.reduction_stats, 5) };
        reduction_stats.copy_from_slice(&[
            q as f64,
            reduction.as_ref().map_or(0, |r| r.effect()) as f64,
            reduction.as_ref().map_or(0, |r| r.eliminated) as f64,
            reduction.as_ref().map_or(0, |r| r.retained()) as f64,
            if reduction.is_some() {
                schur::MAX_DEGREE as f64
            } else {
                0.0
            },
        ]);
        let pre = CmgPreconditioner::build_with(
            &graph,
            BuildOptions::from(CmgOptions::default()),
            context,
        )
        .map_err(|e| e.to_string())?;
        let setup = start.elapsed().as_secs_f64();
        let levels = pre.hierarchy().levels().len();
        let edges = graph.edges().len();
        let prepared = Prepared {
            a,
            b,
            w: &w,
            na: input.na,
            graph,
            pre,
            reduction,
            degree,
            tol: input.tol,
            maxiter: input.maxiter,
        };
        let raw = unsafe { std::slice::from_raw_parts(input.columns, input.p) };
        let iterations = unsafe { std::slice::from_raw_parts_mut(input.iterations, input.p) };
        let moments = unsafe { std::slice::from_raw_parts_mut(input.moments, input.p) };
        let residuals =
            unsafe { std::slice::from_raw_parts_mut(input.relative_residuals, input.p) };
        let mut views: Vec<_> = raw
            .iter()
            .zip(iterations)
            .zip(moments)
            .zip(residuals)
            .map(|(((col, iterations), moment), relative_residual)| View {
                x: unsafe { std::slice::from_raw_parts(col.input, input.n) },
                out: unsafe { std::slice::from_raw_parts_mut(col.output, input.n) },
                iterations,
                moment,
                relative_residual,
            })
            .collect();
        // Independent RHS solves use one lane each, with no nested fork.
        // Collect in column order so the reported error does not depend on scheduling.
        let outcomes: Vec<_> = pool.install(|| {
            views
                .par_iter_mut()
                .map(|v| prepared.project(v, Some(team.single_lane()), cancel))
                .collect()
        });
        for result in outcomes {
            result?;
        }
        checkpoint(cancel)?;
        let stats = unsafe { std::slice::from_raw_parts_mut(input.stats, 6) };
        stats.copy_from_slice(&[
            vertices as f64,
            edges as f64,
            levels as f64,
            setup,
            start.elapsed().as_secs_f64() - setup,
            start.elapsed().as_secs_f64(),
        ]);
        Ok(())
    }
}
unsafe fn message(dest: *mut c_char, capacity: usize, text: &str) {
    if capacity == 0 {
        return;
    }
    let length = text.len().min(capacity - 1);
    unsafe {
        std::ptr::copy_nonoverlapping(text.as_ptr(), dest.cast(), length);
        *dest.add(length) = 0;
    }
}
/// Start one joined native job; see bridge.c for the protected pointer contract.
///
/// # Safety
/// Input and all its buffers must be valid, correctly sized and protected until
/// finish joins the job. Outputs must be disjoint and exclusively writable.
/// Error must point to capacity writable bytes (or capacity must be zero).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cmgr_start(
    input: *const Input,
    error: *mut c_char,
    capacity: usize,
) -> *mut std::ffi::c_void {
    let outcome = catch_unwind(AssertUnwindSafe(|| {
        let request = Request(unsafe { *input });
        let cancel = Arc::new(AtomicBool::new(false));
        let worker_cancel = cancel.clone();
        let handle = std::thread::Builder::new()
            .name("cmgr-coordinator".into())
            .spawn(move || {
                catch_unwind(AssertUnwindSafe(|| request.run(&worker_cancel)))
                    .unwrap_or_else(|_| Err("native panic".into()))
            })
            .map_err(|e| e.to_string())?;
        Ok::<_, String>(Box::into_raw(Box::new(Job { cancel, handle })).cast())
    }))
    .unwrap_or_else(|_| Err("native startup panic".into()));
    match outcome {
        Ok(job) => job,
        Err(e) => {
            unsafe { message(error, capacity, &e) };
            std::ptr::null_mut()
        }
    }
}
/// Observe completion without touching R objects.
///
/// # Safety
/// Job must be a live handle returned by cmgr_start, not yet finished.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cmgr_done(job: *const std::ffi::c_void) -> i32 {
    i32::from(unsafe { &*job.cast::<Job>() }.handle.is_finished())
}
/// Request cooperative cancellation; the caller must still call finish.
///
/// # Safety
/// Job must be a live handle returned by cmgr_start, not yet finished.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cmgr_cancel(job: *const std::ffi::c_void) {
    unsafe { &*job.cast::<Job>() }
        .cancel
        .store(true, Ordering::Relaxed);
}
/// Join all work before the caller may release any input/output buffer.
///
/// # Safety
/// Job must be a live handle and may be consumed exactly once. Error must point
/// to capacity writable bytes (or capacity must be zero).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cmgr_finish(
    job: *mut std::ffi::c_void,
    error: *mut c_char,
    capacity: usize,
) -> i32 {
    let job = unsafe { Box::from_raw(job.cast::<Job>()) };
    match job
        .handle
        .join()
        .unwrap_or_else(|_| Err("native coordinator panic".into()))
    {
        Ok(()) => 0,
        Err(e) => {
            unsafe { message(error, capacity, &e) };
            1
        }
    }
}
