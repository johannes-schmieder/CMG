//! Matched caller-buffer comparison of ordinary and explicit component-portfolio PCG.
use cmg::{
    CmgError, CmgOptions, CmgPreconditioner, ComponentBuildOptions, ComponentPcgExperiment,
    ComponentPcgWorkspace, Components, Laplacian, PcgDiagnostics, PcgOptions, PcgWorkspace,
    solve_pcg_into_with_workspace,
};
#[cfg(not(feature = "component-allocations"))]
use std::{hint::black_box, time::Instant};

#[cfg(feature = "component-allocations")]
#[path = "../requested_allocations.rs"]
mod allocations;
#[path = "../component_fixtures.rs"]
mod fixtures;

#[derive(Clone, Copy)]
enum Route {
    Scalar,
    Portfolio,
}
impl Route {
    fn name(self) -> &'static str {
        match self {
            Self::Scalar => "scalar",
            Self::Portfolio => "portfolio",
        }
    }
}
enum Solver {
    Scalar(Box<CmgPreconditioner>),
    Portfolio(ComponentPcgExperiment),
}
enum Workspace {
    Scalar(Box<PcgWorkspace>),
    Portfolio(ComponentPcgWorkspace),
}
impl Solver {
    fn build(graph: &Laplacian, route: Route) -> Result<Self, CmgError> {
        match route {
            Route::Scalar => Ok(Self::Scalar(Box::new(
                CmgPreconditioner::build_component_experiment(
                    graph,
                    CmgOptions::default(),
                    ComponentBuildOptions {
                        prune_coarse_isolates: true,
                        preserve_unpruned_stopping: true,
                        factor_terminal_components: true,
                    },
                )?,
            ))),
            Route::Portfolio => Ok(Self::Portfolio(ComponentPcgExperiment::build(
                graph,
                CmgOptions::default(),
            )?)),
        }
    }
    fn workspace(&self) -> Result<Workspace, CmgError> {
        match self {
            Self::Scalar(s) => Ok(Workspace::Scalar(Box::new(PcgWorkspace::try_new(s)?))),
            Self::Portfolio(s) => Ok(Workspace::Portfolio(s.workspace()?)),
        }
    }
    fn retained(&self) -> usize {
        match self {
            Self::Scalar(s) => s.retained_bytes(),
            Self::Portfolio(s) => s.retained_bytes(),
        }
    }
    #[cfg(not(feature = "component-allocations"))]
    fn active(&self, n: usize) -> usize {
        match self {
            Self::Scalar(_) => n,
            Self::Portfolio(s) => s.active_vertices(),
        }
    }
    fn solve(
        &self,
        graph: &Laplacian,
        b: &[f64],
        out: &mut [f64],
        ws: &mut Workspace,
    ) -> Result<(PcgDiagnostics, Option<PcgDiagnostics>), CmgError> {
        match (self, ws) {
            (Self::Scalar(s), Workspace::Scalar(w)) => {
                let d = solve_pcg_into_with_workspace(
                    graph,
                    s,
                    b,
                    None,
                    out,
                    PcgOptions::default(),
                    w,
                )?;
                Ok((d, Some(d)))
            }
            (Self::Portfolio(s), Workspace::Portfolio(w)) => {
                let d = s.solve_into(b, None, out, PcgOptions::default(), w)?;
                Ok((d.original(), d.active()))
            }
            _ => unreachable!("workspace route created by solver"),
        }
    }
}
impl Workspace {
    fn bytes(&self) -> usize {
        match self {
            Self::Scalar(w) => w.byte_len(),
            Self::Portfolio(w) => w.byte_len(),
        }
    }
}

#[cfg(not(feature = "component-allocations"))]
struct Sample {
    setup: u128,
    workspace: u128,
    solve: u128,
    retained: usize,
    scratch: usize,
    active: usize,
    diagnostics: Vec<PcgDiagnostics>,
    active_iterations: Vec<Option<usize>>,
    fresh: Vec<f64>,
    errors: Vec<f64>,
    hashes: Vec<String>,
}
#[cfg(not(feature = "component-allocations"))]
fn norm(x: &[f64]) -> f64 {
    x.iter().map(|x| x * x).sum::<f64>().sqrt()
}
#[cfg(not(feature = "component-allocations"))]
fn measure(
    graph: &Laplacian,
    rhs: &[Vec<f64>],
    targets: &[Vec<f64>],
    route: Route,
) -> Result<Sample, String> {
    let clock = Instant::now();
    let solver = Solver::build(black_box(graph), route).map_err(|e| format!("setup: {e}"))?;
    let setup = clock.elapsed().as_nanos();
    let clock = Instant::now();
    let mut ws = solver.workspace().map_err(|e| format!("workspace: {e}"))?;
    let mut solutions = vec![vec![0.0; graph.vertex_count()]; rhs.len()];
    let mut reports = Vec::with_capacity(rhs.len());
    let workspace = clock.elapsed().as_nanos();
    let clock = Instant::now();
    for (b, out) in rhs.iter().zip(&mut solutions) {
        reports.push(
            solver
                .solve(graph, black_box(b), black_box(out), &mut ws)
                .map_err(|e| format!("solve: {e}"))?,
        );
    }
    let solve = clock.elapsed().as_nanos();
    let mut fresh = Vec::new();
    let mut errors = Vec::new();
    let mut hashes = Vec::new();
    for (((x, b), target), (d, _)) in solutions.iter().zip(rhs).zip(targets).zip(&reports) {
        let ax = graph.matvec(x).map_err(|e| e.to_string())?;
        let residual = norm(&b.iter().zip(ax).map(|(b, a)| b - a).collect::<Vec<_>>());
        let tolerance = PcgOptions::default().absolute_tolerance
            + PcgOptions::default().relative_tolerance
                * (norm(b) + graph.operator_norm_bound() * norm(x));
        if !residual.is_finite()
            || !d.tolerance().is_finite()
            || residual > d.tolerance()
            || residual > tolerance * (1.0 + 1e-14)
        {
            return Err(format!(
                "independent full certificate: {residual} > {} / {tolerance}",
                d.tolerance()
            ));
        }
        fresh.push(residual);
        errors.push(
            norm(&x.iter().zip(target).map(|(x, y)| x - y).collect::<Vec<_>>())
                / norm(target).max(f64::MIN_POSITIVE),
        );
        hashes.push(hash(x.iter().map(|x| x.to_bits())));
    }
    Ok(Sample {
        setup,
        workspace,
        solve,
        retained: solver.retained(),
        scratch: ws.bytes(),
        active: solver.active(graph.vertex_count()),
        diagnostics: reports.iter().map(|r| r.0).collect(),
        active_iterations: reports
            .iter()
            .map(|r| r.1.map(PcgDiagnostics::iterations))
            .collect(),
        fresh,
        errors,
        hashes,
    })
}
fn hash(values: impl IntoIterator<Item = u64>) -> String {
    let mut h = 0xcbf29ce484222325u64;
    for value in values {
        for byte in value.to_le_bytes() {
            h = (h ^ u64::from(byte)).wrapping_mul(0x100000001b3);
        }
    }
    format!("{h:016x}")
}
fn json(value: &str) -> String {
    let mut out = String::from("\"");
    for c in value.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c.is_control() => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}
#[cfg(not(feature = "component-allocations"))]
fn emit(case: &str, route: Route, round: usize, n: usize, s: Sample) {
    let active_iterations = s
        .active_iterations
        .iter()
        .map(|v| v.map_or_else(|| "null".into(), |n| n.to_string()))
        .collect::<Vec<_>>()
        .join(",");
    println!(
        "{{\"type\":\"sample\",\"case\":{},\"route\":{},\"round\":{round},\"vertices\":{n},\"active_vertices\":{},\"direct_vertices\":{},\"setup_ns\":{},\"workspace_allocation_ns\":{},\"solve_ns\":{},\"total_ns\":{},\"retained_preconditioner_bytes\":{},\"pcg_workspace_bytes\":{},\"iterations\":{:?},\"active_iterations\":[{active_iterations}],\"residuals\":{:?},\"fresh_residuals\":{:?},\"tolerances\":{:?},\"backward_errors\":{:?},\"rhs_projection_norms\":{:?},\"relative_solution_errors\":{:?},\"solution_bit_hashes\":{:?}}}",
        json(case),
        json(route.name()),
        s.active,
        n - s.active,
        s.setup,
        s.workspace,
        s.solve,
        s.setup + s.workspace + s.solve,
        s.retained,
        s.scratch,
        s.diagnostics
            .iter()
            .map(|d| d.iterations())
            .collect::<Vec<_>>(),
        s.diagnostics
            .iter()
            .map(|d| d.residual_norm())
            .collect::<Vec<_>>(),
        s.fresh,
        s.diagnostics
            .iter()
            .map(|d| d.tolerance())
            .collect::<Vec<_>>(),
        s.diagnostics
            .iter()
            .map(|d| d.backward_error())
            .collect::<Vec<_>>(),
        s.diagnostics
            .iter()
            .map(|d| d.rhs_projection_norm())
            .collect::<Vec<_>>(),
        s.errors,
        s.hashes
    );
}

#[cfg(feature = "component-allocations")]
fn allocation_probe(
    graph: &Laplacian,
    rhs: &[Vec<f64>],
    route: Route,
    case: &str,
) -> Result<(), String> {
    let start = allocations::begin();
    let solver = Solver::build(graph, route).map_err(|e| e.to_string())?;
    let setup = allocations::end(start);
    let start = allocations::begin();
    let mut ws = solver.workspace().map_err(|e| e.to_string())?;
    let workspace = allocations::end(start);
    let mut out = vec![0.0; graph.vertex_count()];
    solver
        .solve(graph, &rhs[0], &mut out, &mut ws)
        .map_err(|e| e.to_string())?;
    let start = allocations::begin();
    for b in rhs {
        solver
            .solve(graph, b, &mut out, &mut ws)
            .map_err(|e| e.to_string())?;
    }
    let warm = allocations::end(start);
    println!(
        "{{\"type\":\"allocations\",\"case\":{},\"route\":{},\"setup_peak_additional_bytes\":{},\"setup_live_additional_bytes\":{},\"workspace_peak_additional_bytes\":{},\"workspace_live_additional_bytes\":{},\"reported_retained_bytes\":{},\"reported_workspace_bytes\":{},\"warm_solve_allocations\":{}}}",
        json(case),
        json(route.name()),
        setup.peak_additional,
        setup.live_additional,
        workspace.peak_additional,
        workspace.live_additional,
        solver.retained(),
        ws.bytes(),
        warm.allocations
    );
    if warm.allocations > 0 {
        return Err("warm caller-buffer solve allocated".into());
    }
    Ok(())
}

fn main() {
    let mut args = std::env::args().skip(1);
    let mut positional = Vec::new();
    let mut suite = "sentinels".to_string();
    let mut seed = 20260906u64;
    let mut route = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--suite" => suite = args.next().expect("suite"),
            "--seed" => seed = args.next().expect("seed").parse().expect("integer seed"),
            "--route" => {
                route = Some(match args.next().expect("route").as_str() {
                    "scalar" => Route::Scalar,
                    "portfolio" => Route::Portfolio,
                    _ => panic!("unknown route"),
                })
            }
            _ => {
                assert!(!arg.starts_with("--"), "unknown flag");
                positional.push(arg);
            }
        }
    }
    assert!(
        positional.len() <= 3,
        "expected repetitions, RHS count, optional filter"
    );
    let repetitions = positional
        .first()
        .map_or(9, |s| s.parse::<usize>().expect("repetitions"));
    let rhs_count = positional
        .get(1)
        .map_or(1, |s| s.parse::<usize>().expect("RHS count"));
    let filter = positional.get(2).map_or("", String::as_str);
    assert!(repetitions > 0 && rhs_count > 0);
    let routes = route.map_or_else(|| vec![Route::Scalar, Route::Portfolio], |r| vec![r]);
    println!(
        "{{\"type\":\"environment\",\"source\":{},\"os\":{},\"arch\":{},\"suite\":{},\"seed\":{seed},\"repetitions\":{repetitions},\"rhs_count\":{rhs_count},\"warmups\":2,\"allocation_tracking\":{},\"parallel_execution\":false,\"phase_profiling\":false,\"options\":{{\"relative_tolerance\":1e-8,\"absolute_tolerance\":0,\"max_iterations\":1000,\"residual_recompute_interval\":25,\"tiny_component_threshold\":2}}}}",
        json(option_env!("CMG_BENCH_COMMIT").unwrap_or("unrecorded")),
        json(std::env::consts::OS),
        json(std::env::consts::ARCH),
        json(&suite),
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
        let targets: Vec<Vec<f64>> = (0..rhs_count)
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
        let graph_hash = hash(
            [case.graph.vertex_count() as u64].into_iter().chain(
                case.graph
                    .edges()
                    .iter()
                    .flat_map(|e| [e.u() as u64, e.v() as u64, e.weight().to_bits()]),
            ),
        );
        println!(
            "{{\"type\":\"fixture\",\"case\":{},\"graph_hash\":{},\"rhs_hashes\":{:?},\"target_hashes\":{:?},\"vertices\":{},\"edges\":{}}}",
            json(case.name),
            json(&graph_hash),
            rhs.iter()
                .map(|b| hash(b.iter().map(|v| v.to_bits())))
                .collect::<Vec<_>>(),
            targets
                .iter()
                .map(|b| hash(b.iter().map(|v| v.to_bits())))
                .collect::<Vec<_>>(),
            case.graph.vertex_count(),
            case.graph.edge_count()
        );
        #[cfg(feature = "component-allocations")]
        {
            for &route in &routes {
                if let Err(e) = allocation_probe(&case.graph, &rhs, route, case.name) {
                    println!(
                        "{{\"type\":\"failure\",\"case\":{},\"route\":{},\"error\":{}}}",
                        json(case.name),
                        json(route.name()),
                        json(&e)
                    );
                    failures += 1;
                }
            }
            continue;
        }
        #[cfg(not(feature = "component-allocations"))]
        for round in 0..repetitions + 2 {
            for offset in 0..routes.len() {
                let route = routes[(round + offset) % routes.len()];
                match measure(&case.graph, &rhs, &targets, route) {
                    Ok(s) if round >= 2 => {
                        emit(case.name, route, round - 2, case.graph.vertex_count(), s)
                    }
                    Ok(_) => (),
                    Err(e) => {
                        println!(
                            "{{\"type\":\"failure\",\"case\":{},\"route\":{},\"round\":{round},\"error\":{}}}",
                            json(case.name),
                            json(route.name()),
                            json(&e)
                        );
                        failures += 1;
                    }
                }
            }
        }
    }
    assert!(selected > 0, "filter matched no fixtures");
    if failures > 0 {
        eprintln!("{failures} failed attempts retained in JSONL");
        std::process::exit(1);
    }
}
