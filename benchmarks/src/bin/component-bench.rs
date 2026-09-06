//! Reusable disconnected-graph sentinels, paired setup/solve measurements and
//! cycle-work accounting. No SCC submission or automatic production routing.

#[cfg(feature = "experimental-components")]
use cmg::ComponentBuildOptions;
use cmg::{
    CmgError, CmgOptions, CmgPreconditioner, Components, Laplacian, PcgOptions, PcgWorkspace,
    solve_pcg_with_workspace,
};
use std::hint::black_box;
use std::time::Instant;

#[path = "../component_fixtures.rs"]
mod fixtures;
use fixtures::Case;
#[cfg(feature = "component-allocations")]
#[path = "../requested_allocations.rs"]
mod allocations;

fn build(graph: &Laplacian, route: usize) -> Result<CmgPreconditioner, CmgError> {
    if route == 0 {
        return CmgPreconditioner::build(graph, CmgOptions::default());
    }
    #[cfg(feature = "experimental-components")]
    return CmgPreconditioner::build_component_experiment(
        graph,
        CmgOptions::default(),
        ComponentBuildOptions {
            prune_coarse_isolates: route == 1 || route >= 3,
            factor_terminal_components: route >= 2,
            preserve_unpruned_stopping: route == 4,
        },
    );
    #[cfg(not(feature = "experimental-components"))]
    panic!("experimental route requested from baseline-only build")
}

fn names(route: usize) -> &'static str {
    [
        "baseline",
        "prune",
        "block-ldl",
        "prune+block-ldl",
        "preserve-stopping+block-ldl",
    ][route]
}

struct Sample {
    setup: u128,
    workspace: u128,
    solve: u128,
    apply: u128,
    iterations: Vec<usize>,
    residuals: Vec<f64>,
    tolerances: Vec<f64>,
    retained: usize,
    scratch: usize,
    relative_solution_errors: Vec<f64>,
}

fn measure(
    graph: &Laplacian,
    rhs: &[Vec<f64>],
    targets: &[Vec<f64>],
    route: usize,
) -> Result<Sample, Failure> {
    let start = Instant::now();
    let pre = build(black_box(graph), route).map_err(|e| Failure::new("setup", e))?;
    let setup = start.elapsed().as_nanos();
    let start = Instant::now();
    let mut ws = PcgWorkspace::new(&pre);
    let workspace = start.elapsed().as_nanos();
    let start = Instant::now();
    let results: Vec<_> = rhs
        .iter()
        .map(|b| {
            solve_pcg_with_workspace(graph, &pre, black_box(b), PcgOptions::default(), &mut ws)
        })
        .collect::<Result<_, _>>()
        .map_err(|e| Failure::new("solve", e))?;
    let solve = start.elapsed().as_nanos();
    // Independently recompute the original-system residual outside timings.
    let mut relative_solution_errors = Vec::with_capacity(results.len());
    for ((result, b), target) in results.iter().zip(rhs).zip(targets) {
        let ax = graph.matvec(result.solution()).unwrap();
        let fresh = b
            .iter()
            .zip(ax)
            .map(|(b, ax)| (b - ax).powi(2))
            .sum::<f64>()
            .sqrt();
        if !fresh.is_finite() || fresh > result.tolerance() {
            return Err(Failure {
                stage: "independent-residual",
                error: format!("{fresh} > {}", result.tolerance()),
            });
        }
        let error = result
            .solution()
            .iter()
            .zip(target)
            .map(|(x, y)| (x - y).powi(2))
            .sum::<f64>()
            .sqrt();
        let norm = target.iter().map(|x| x * x).sum::<f64>().sqrt();
        relative_solution_errors.push(error / norm.max(f64::MIN_POSITIVE));
    }
    let mut cmg_ws = pre.workspace();
    let mut output = vec![0.0; graph.vertex_count()];
    pre.apply_into(&rhs[0], &mut output, &mut cmg_ws)
        .map_err(|e| Failure::new("apply-warmup", e))?;
    let start = Instant::now();
    for _ in 0..5 {
        pre.apply_into(black_box(&rhs[0]), black_box(&mut output), &mut cmg_ws)
            .map_err(|e| Failure::new("apply", e))?;
    }
    let apply = start.elapsed().as_nanos() / 5;
    Ok(Sample {
        setup,
        workspace,
        solve,
        apply,
        iterations: results.iter().map(|r| r.iterations()).collect(),
        residuals: results.iter().map(|r| r.residual_norm()).collect(),
        tolerances: results.iter().map(|r| r.tolerance()).collect(),
        retained: pre.retained_bytes(),
        scratch: ws.byte_len(),
        relative_solution_errors,
    })
}

fn print_structure(case: &Case, route: usize) -> Result<(), Failure> {
    let pre = build(&case.graph, route).map_err(|e| Failure::new("structure-setup", e))?;
    let mut calls = 1u128;
    let mut iterations = 1u128;
    for (index, level) in pre.hierarchy().levels().iter().enumerate() {
        // Terminal applications ignore the supplied iteration count.
        let visits = if level.is_terminal() {
            calls
        } else {
            calls * iterations
        };
        let isolates = level
            .graph()
            .diagonal()
            .iter()
            .filter(|&&d| d == 0.0)
            .count();
        let represented = level
            .aggregation()
            .map(|a| a.coarse_dimension())
            .unwrap_or(0);
        #[cfg(feature = "experimental-components")]
        let represented = level
            .pruned_transfer()
            .map(|t| t.represented_coarse_dimension())
            .unwrap_or(represented);
        let active = pre
            .hierarchy()
            .levels()
            .get(index + 1)
            .map(|l| l.graph().vertex_count())
            .unwrap_or(0);
        println!(
            "{{\"type\":\"level\",\"case\":\"{}\",\"route\":\"{}\",\"level\":{index},\"vertices\":{},\"edges\":{},\"isolates\":{isolates},\"repeat\":{},\"terminal\":{},\"cycle_or_terminal_visits\":{visits},\"represented_coarse_vertices\":{represented},\"active_coarse_vertices\":{active}}}",
            case.name,
            names(route),
            level.graph().vertex_count(),
            level.graph().edge_count(),
            level.repeat(),
            level.is_terminal()
        );
        calls = visits;
        iterations = level.repeat() as u128;
    }
    Ok(())
}

#[derive(Debug)]
struct Failure {
    stage: &'static str,
    error: String,
}
impl Failure {
    fn new(stage: &'static str, error: CmgError) -> Self {
        Self {
            stage,
            error: error.to_string(),
        }
    }
    fn emit(&self, case: &str, route: usize, round: Option<usize>) {
        println!(
            "{{\"type\":\"failure\",\"case\":{},\"route\":{},\"stage\":{},\"round\":{},\"error\":{}}}",
            json_string(case),
            json_string(names(route)),
            json_string(self.stage),
            round.map_or("null".to_owned(), |r| r.to_string()),
            json_string(&self.error)
        );
    }
}
fn json_string(value: &str) -> String {
    let mut out = String::from("\"");
    for ch in value.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\"' => out.push_str("\\\""),
            c if (c as u32) < 32 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('\"');
    out
}

fn main() {
    let mut args = std::env::args().skip(1);
    let mut positional = Vec::new();
    let mut suite = "sentinels".to_owned();
    let mut seed = 20260906u64;
    let mut route = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--suite" => suite = args.next().expect("suite value"),
            "--seed" => {
                seed = args
                    .next()
                    .expect("seed value")
                    .parse()
                    .expect("integer seed")
            }
            "--route" => {
                route = Some(match args.next().expect("route value").as_str() {
                    "baseline" => 0,
                    "prune" => 1,
                    "block-ldl" => 2,
                    "combined" => 3,
                    "preserve-stopping" => 4,
                    _ => panic!("unknown route"),
                })
            }
            _ => {
                assert!(!arg.starts_with("--"), "unknown flag {arg}");
                positional.push(arg);
            }
        }
    }
    assert!(
        positional.len() <= 3,
        "expected repetitions, RHS count, optional case substring"
    );
    let repetitions = positional
        .first()
        .map(|x| x.parse::<usize>().expect("integer repetitions"))
        .unwrap_or(9);
    let rhs_count = positional
        .get(1)
        .map(|x| x.parse::<usize>().expect("integer RHS count"))
        .unwrap_or(1);
    let filter = positional.get(2).map(String::as_str).unwrap_or("");
    assert!(repetitions > 0 && rhs_count > 0);
    let routes = route.map_or_else(
        || {
            if cfg!(feature = "experimental-components") {
                (0..5).collect::<Vec<_>>()
            } else {
                vec![0]
            }
        },
        |r| vec![r],
    );
    assert!(
        cfg!(feature = "experimental-components") || routes == [0],
        "baseline-only build"
    );
    println!(
        "{{\"type\":\"environment\",\"source\":{},\"os\":{},\"arch\":{},\"suite\":{},\"seed\":{seed},\"repetitions\":{repetitions},\"rhs_count\":{rhs_count},\"warmups\":2,\"parallel_execution\":false,\"allocation_tracking\":{}}}",
        json_string(option_env!("CMG_BENCH_COMMIT").unwrap_or("unrecorded")),
        json_string(std::env::consts::OS),
        json_string(std::env::consts::ARCH),
        json_string(&suite),
        cfg!(feature = "component-allocations")
    );
    let cases = match suite.as_str() {
        "sentinels" => fixtures::sentinels(),
        "stress" => fixtures::stress(seed),
        "large" => fixtures::large(seed),
        _ => panic!("unknown suite"),
    };
    let mut selected = 0;
    let mut failures = 0;
    for case in cases.into_iter().filter(|c| c.name.contains(filter)) {
        selected += 1;
        let components = Components::from_laplacian(&case.graph);
        let targets: Vec<_> = (0..rhs_count)
            .map(|column| {
                let mut x: Vec<_> = (0..case.graph.vertex_count())
                    .map(|v| ((v * 7 + column * 11) % 31) as f64 / 16.0)
                    .collect();
                components.center_in_place(&mut x).unwrap();
                x
            })
            .collect();
        let rhs: Vec<_> = targets
            .iter()
            .map(|x| case.graph.matvec(x).unwrap())
            .collect();
        for &route in &routes {
            if let Err(error) = print_structure(&case, route) {
                error.emit(case.name, route, None);
                failures += 1;
            }
            #[cfg(feature = "component-allocations")]
            if let Err(error) = allocation_probe(&case, &rhs, route) {
                error.emit(case.name, route, None);
                failures += 1;
            }
        }
        for round in 0..repetitions + 2 {
            for offset in 0..routes.len() {
                let route = routes[(round + offset) % routes.len()];
                let s = match measure(&case.graph, &rhs, &targets, route) {
                    Ok(s) => s,
                    Err(error) => {
                        error.emit(case.name, route, round.checked_sub(2));
                        failures += 1;
                        continue;
                    }
                };
                if round < 2 {
                    continue;
                }
                let total = s.setup + s.workspace + s.solve;
                println!(
                    "{{\"type\":\"sample\",\"case\":{},\"route\":{},\"round\":{},\"setup_ns\":{},\"workspace_allocation_ns\":{},\"solve_ns\":{},\"total_ns\":{total},\"apply_ns\":{},\"iterations\":{:?},\"residuals\":{:?},\"tolerances\":{:?},\"relative_solution_errors\":{:?},\"retained_preconditioner_bytes\":{},\"pcg_workspace_bytes\":{}}}",
                    json_string(case.name),
                    json_string(names(route)),
                    round - 2,
                    s.setup,
                    s.workspace,
                    s.solve,
                    s.apply,
                    s.iterations,
                    s.residuals,
                    s.tolerances,
                    s.relative_solution_errors,
                    s.retained,
                    s.scratch
                );
            }
        }
    }
    assert!(selected > 0, "case filter matched no fixtures");
    if failures > 0 {
        eprintln!("{failures} failed attempts retained in JSONL");
        std::process::exit(1);
    }
}

#[cfg(feature = "component-allocations")]
fn allocation_probe(case: &Case, rhs: &[Vec<f64>], route: usize) -> Result<(), Failure> {
    use cmg::{CmgMemoryEstimate, CmgProblemSize, ParallelOptions, solve_pcg_into_with_workspace};
    let graph = &case.graph;
    let estimate = CmgMemoryEstimate::conservative(
        CmgProblemSize {
            vertices: graph.vertex_count(),
            input_edges: graph.edge_count(),
            canonical_edges: graph.edge_count(),
            right_hand_sides: rhs.len(),
        },
        CmgOptions::default(),
        ParallelOptions {
            threads: 1,
            ..ParallelOptions::default()
        },
    )
    .map_err(|e| Failure::new("estimate", e))?;
    let start = allocations::begin();
    let pre = build(graph, route).map_err(|e| Failure::new("allocation-setup", e))?;
    let setup = allocations::end(start);
    let start = allocations::begin();
    let mut ws = PcgWorkspace::new(&pre);
    let workspace = allocations::end(start);
    let mut cmg_ws = pre.workspace();
    let mut out = vec![0.0; graph.vertex_count()];
    pre.apply_into(&rhs[0], &mut out, &mut cmg_ws)
        .map_err(|e| Failure::new("allocation-apply-warmup", e))?;
    solve_pcg_into_with_workspace(
        graph,
        &pre,
        &rhs[0],
        None,
        &mut out,
        PcgOptions::default(),
        &mut ws,
    )
    .map_err(|e| Failure::new("allocation-pcg-warmup", e))?;
    let start = allocations::begin();
    for b in rhs {
        pre.apply_into(b, &mut out, &mut cmg_ws)
            .map_err(|e| Failure::new("allocation-apply", e))?;
    }
    let apply = allocations::end(start);
    let start = allocations::begin();
    for b in rhs {
        solve_pcg_into_with_workspace(
            graph,
            &pre,
            b,
            None,
            &mut out,
            PcgOptions::default(),
            &mut ws,
        )
        .map_err(|e| Failure::new("allocation-pcg", e))?;
    }
    let pcg = allocations::end(start);
    println!(
        "{{\"type\":\"allocations\",\"case\":{},\"route\":{},\"setup_peak_additional_bytes\":{},\"setup_live_additional_bytes\":{},\"setup_allocations\":{},\"workspace_live_additional_bytes\":{},\"workspace_allocations\":{},\"reported_preconditioner_bytes\":{},\"reported_workspace_bytes\":{},\"conservative_build_peak_bytes\":{},\"warm_apply_allocations\":{},\"warm_pcg_into_allocations\":{}}}",
        json_string(case.name),
        json_string(names(route)),
        setup.peak_additional,
        setup.live_additional,
        setup.allocations,
        workspace.live_additional,
        workspace.allocations,
        pre.retained_bytes(),
        ws.byte_len(),
        estimate.build_peak_bytes(),
        apply.allocations,
        pcg.allocations
    );
    if apply.allocations != 0 || pcg.allocations != 0 {
        return Err(Failure {
            stage: "unexpected-warm-allocation",
            error: "caller-buffer loops allocated".to_owned(),
        });
    }
    Ok(())
}
