//! Fixed, small upstream integration screen. The SCC driver compiles these
//! identical bytes against baseline/candidate archives. No policy is promoted.
use cmg::*;
use std::time::Instant;

#[cfg(feature = "screen-allocations")]
mod allocations {
    use std::alloc::{GlobalAlloc, Layout, System};
    use std::sync::atomic::{AtomicIsize, Ordering::SeqCst};
    pub static LIVE: AtomicIsize = AtomicIsize::new(0);
    pub static PEAK: AtomicIsize = AtomicIsize::new(0);
    pub struct Observed;
    unsafe impl GlobalAlloc for Observed {
        unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
            let p = unsafe { System.alloc(layout) };
            if !p.is_null() {
                let live = LIVE.fetch_add(layout.size() as isize, SeqCst) + layout.size() as isize;
                PEAK.fetch_max(live, SeqCst);
            }
            p
        }
        unsafe fn dealloc(&self, p: *mut u8, layout: Layout) {
            LIVE.fetch_sub(layout.size() as isize, SeqCst);
            unsafe {
                System.dealloc(p, layout);
            }
        }
    }
}
#[cfg(feature = "screen-allocations")]
#[global_allocator]
static ALLOCATOR: allocations::Observed = allocations::Observed;

fn live() -> isize {
    #[cfg(feature = "screen-allocations")]
    {
        allocations::LIVE.load(std::sync::atomic::Ordering::SeqCst)
    }
    #[cfg(not(feature = "screen-allocations"))]
    {
        0
    }
}
fn begin_peak() {
    #[cfg(feature = "screen-allocations")]
    allocations::PEAK.store(live(), std::sync::atomic::Ordering::SeqCst);
}
fn peak() -> isize {
    #[cfg(feature = "screen-allocations")]
    {
        allocations::PEAK.load(std::sync::atomic::Ordering::SeqCst)
    }
    #[cfg(not(feature = "screen-allocations"))]
    {
        0
    }
}

fn fixture(case: &str) -> Laplacian {
    match case {
        "path" => Laplacian::from_edges(1024, (1..1024).map(|v| (v - 1, v, 1.0))),
        "mixed" => Laplacian::from_edges(
            1474,
            (1..1024)
                .map(|v| (v - 1, v, 1.0))
                .chain((1024..1424).step_by(2).map(|v| (v, v + 1, 0.5))),
        ),
        "dense" => Laplacian::from_edges(
            612,
            (0..256)
                .flat_map(|u| (256..512).map(move |v| (u, v, 1.0)))
                .chain((512..612).step_by(2).map(|u| (u, u + 1, 0.5))),
        ),
        "weak" => Laplacian::from_edges(
            64,
            (1..64).map(|v| (v - 1, v, if v == 32 { 1e-8 } else { 1.0 })),
        ),
        _ => unreachable!(),
    }
    .unwrap()
}

fn main() {
    // Frozen before measuring: four synthetic graphs, ordinary/explicit
    // component setup, one/four workers, four sequential caller-buffer RHSs.
    let input_dir = std::env::args().nth(1);
    for case in ["path", "mixed", "dense", "weak"] {
        let graph = fixture(case);
        let mut known: Vec<_> = (0..graph.vertex_count())
            .map(|v| ((v * 7) % 19) as f64)
            .collect();
        Components::from_laplacian(&graph)
            .center_in_place(&mut known)
            .unwrap();
        let rhs = graph.matvec(&known).unwrap();
        if let Some(dir) = &input_dir {
            use std::io::Write;
            let mut f = std::fs::File::create_new(format!("{dir}/{case}.bin")).unwrap();
            f.write_all(&(graph.vertex_count() as u64).to_le_bytes())
                .unwrap();
            f.write_all(&(graph.edge_count() as u64).to_le_bytes())
                .unwrap();
            for e in graph.edges() {
                for v in [e.u() as u64, e.v() as u64, e.weight().to_bits()] {
                    f.write_all(&v.to_le_bytes()).unwrap();
                }
            }
            for v in known.iter().chain(&rhs) {
                f.write_all(&v.to_bits().to_le_bytes()).unwrap();
            }
        }
        for component in [false, true] {
            for threads in [1, 4] {
                let base = live();
                begin_peak();
                let start = Instant::now();
                let executor = ParallelExecutor::new(ParallelOptions {
                    threads,
                    min_parallel_len: 1,
                    reduction_chunk_size: 256,
                    ..Default::default()
                })
                .unwrap();
                let options = CmgOptions {
                    direct_threshold: 64,
                    ..Default::default()
                };
                let pre = if component {
                    let component = ComponentBuildOptions {
                        prune_coarse_isolates: true,
                        preserve_unpruned_stopping: true,
                        factor_terminal_components: true,
                    };
                    #[cfg(feature = "candidate-screen")]
                    {
                        CmgPreconditioner::build_component_experiment_with_executor(
                            &graph, options, component, &executor,
                        )
                        .unwrap()
                    }
                    #[cfg(not(feature = "candidate-screen"))]
                    {
                        CmgPreconditioner::build_component_experiment(&graph, options, component)
                            .unwrap()
                    }
                } else {
                    CmgPreconditioner::build_with_executor(&graph, options, &executor).unwrap()
                };
                let plan = ParallelCmgPlan::build(&pre, &executor).unwrap();
                let setup = start.elapsed().as_nanos();
                let prepare = Instant::now();
                let mut ws = PcgWorkspace::try_new(&pre).unwrap();
                let mut output = vec![0.0; graph.vertex_count()];
                let workspace = prepare.elapsed().as_nanos();
                let retained = live() - base;
                let solving = Instant::now();
                let mut hash = 0xcbf29ce484222325u64;
                let mut iterations = 0;
                let mut diagnostics = PcgDiagnostics::default();
                for _ in 0..4 {
                    diagnostics = solve_pcg_with_plan_into_with_workspace(
                        &graph,
                        &pre,
                        &plan,
                        &rhs,
                        None,
                        &mut output,
                        PcgOptions {
                            relative_tolerance: 1e-12,
                            ..Default::default()
                        },
                        &mut ws,
                        &executor,
                    )
                    .unwrap();
                    iterations += diagnostics.iterations();
                    for &x in &output {
                        hash = (hash ^ x.to_bits()).wrapping_mul(0x100000001b3);
                    }
                }
                let solve = solving.elapsed().as_nanos();
                let ending = Instant::now();
                drop(executor);
                let shutdown = ending.elapsed().as_nanos();
                let total = start.elapsed().as_nanos();
                let observed_peak = peak() - base;
                let error = output
                    .iter()
                    .zip(&known)
                    .map(|(a, b)| (a - b).powi(2))
                    .sum::<f64>()
                    .sqrt()
                    / known.iter().map(|a| a * a).sum::<f64>().sqrt();
                // Independent edge traversal, outside the timed region.
                let mut residual = rhs.clone();
                for edge in graph.edges() {
                    let delta = edge.weight() * (output[edge.u()] - output[edge.v()]);
                    residual[edge.u()] -= delta;
                    residual[edge.v()] += delta;
                }
                let residual = residual.iter().map(|v| v * v).sum::<f64>().sqrt();
                // Independent accumulation can round differently. Preserve its
                // value; the production certificate is the exact acceptance gate.
                assert!(diagnostics.residual_norm() <= diagnostics.tolerance());
                println!(
                    "{{\"case\":\"{case}\",\"component\":{component},\"workers\":{threads},\"rhs\":4,\"setup_ns\":{setup},\"workspace_ns\":{workspace},\"solve_ns\":{solve},\"shutdown_ns\":{shutdown},\"total_ns\":{total},\"peak_requested_increment\":{observed_peak},\"retained_requested_increment\":{retained},\"iterations\":{iterations},\"solution_fnv64\":\"{hash:016x}\",\"residual\":{residual:e},\"coefficient_relative_error\":{error:e},\"certified\":true,\"discarded_or_fallback\":\"not_exercised\",\"allocation_instrumentation\":{}}}",
                    cfg!(feature = "screen-allocations")
                );
            }
        }
    }
}
