//! Reusable disconnected-graph sentinels, paired setup/solve measurements and
//! cycle-work accounting. No SCC submission or automatic production routing.

use cmg::{
    CmgOptions, CmgPreconditioner, ComponentBuildOptions, Components, Laplacian, PcgOptions,
    PcgWorkspace, solve_pcg_with_workspace,
};
use std::hint::black_box;
use std::time::Instant;

struct Case {
    name: &'static str,
    graph: Laplacian,
}

fn paths(name: &'static str, lengths: &[usize], pairs: usize, isolates: usize) -> Case {
    let mut edges = Vec::new();
    let mut n = 0;
    for &size in lengths {
        edges.extend((0..size.saturating_sub(1)).map(|v| (n + v, n + v + 1, 1.0)));
        n += size;
    }
    edges.extend((0..pairs).map(|c| (n + 2 * c, n + 2 * c + 1, 1.0)));
    Case {
        name,
        graph: Laplacian::from_edges(n + 2 * pairs + isolates, edges).unwrap(),
    }
}

fn grid(name: &'static str, side: usize, pairs: usize) -> Case {
    let mut edges = Vec::new();
    let n = side * side;
    for v in 0..n {
        if v % side + 1 < side {
            edges.push((v, v + 1, 1.0));
        }
        if v + side < n {
            edges.push((v, v + side, 1.0));
        }
    }
    edges.extend((0..pairs).map(|c| (n + 2 * c, n + 2 * c + 1, 1.0)));
    Case {
        name,
        graph: Laplacian::from_edges(n + 2 * pairs, edges).unwrap(),
    }
}

fn triangles() -> Case {
    Case {
        name: "all-triangles",
        graph: Laplacian::from_edges(
            3000,
            (0..1000).flat_map(|c| {
                [
                    (3 * c, 3 * c + 1, 1.0),
                    (3 * c + 1, 3 * c + 2, 2.0),
                    (3 * c, 3 * c + 2, 0.5),
                ]
            }),
        )
        .unwrap(),
    }
}

fn cases() -> Vec<Case> {
    vec![
        paths("connected-path", &[4096], 0, 0),
        paths("path-plus-pairs", &[4096], 1000, 0),
        paths("path-plus-isolates", &[4096], 0, 1000),
        paths("two-paths-plus-pairs", &[2048, 2048], 1000, 0),
        paths("all-pairs", &[], 1000, 0),
        paths("pairs-below-threshold", &[], 349, 0),
        paths("pairs-at-threshold", &[], 350, 0),
        grid("connected-grid", 32, 0),
        grid("grid-plus-pairs", 32, 1000),
        triangles(),
    ]
}

fn build(graph: &Laplacian, route: usize) -> CmgPreconditioner {
    if route == 0 {
        return CmgPreconditioner::build(graph, CmgOptions::default()).unwrap();
    }
    CmgPreconditioner::build_component_experiment(
        graph,
        CmgOptions::default(),
        ComponentBuildOptions {
            prune_coarse_isolates: route == 1 || route == 3,
            factor_terminal_components: route == 2 || route == 3,
        },
    )
    .unwrap()
}

fn names(route: usize) -> &'static str {
    ["baseline", "prune", "block-ldl", "prune+block-ldl"][route]
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
}

fn measure(graph: &Laplacian, rhs: &[Vec<f64>], route: usize) -> Sample {
    let start = Instant::now();
    let pre = build(black_box(graph), route);
    let setup = start.elapsed().as_nanos();
    let start = Instant::now();
    let mut ws = PcgWorkspace::new(&pre);
    let workspace = start.elapsed().as_nanos();
    let start = Instant::now();
    let results: Vec<_> = rhs
        .iter()
        .map(|b| {
            solve_pcg_with_workspace(graph, &pre, black_box(b), PcgOptions::default(), &mut ws)
                .expect("every arm must solve and certify the same fixture")
        })
        .collect();
    let solve = start.elapsed().as_nanos();
    // Independently recompute the original-system residual outside timings.
    for (result, b) in results.iter().zip(rhs) {
        let ax = graph.matvec(result.solution()).unwrap();
        let fresh = b
            .iter()
            .zip(ax)
            .map(|(b, ax)| (b - ax).powi(2))
            .sum::<f64>()
            .sqrt();
        assert!(fresh <= result.tolerance(), "independent residual failed");
    }
    let mut cmg_ws = pre.workspace();
    let mut output = vec![0.0; graph.vertex_count()];
    pre.apply_into(&rhs[0], &mut output, &mut cmg_ws).unwrap();
    let start = Instant::now();
    for _ in 0..5 {
        pre.apply_into(black_box(&rhs[0]), black_box(&mut output), &mut cmg_ws)
            .unwrap();
    }
    let apply = start.elapsed().as_nanos() / 5;
    Sample {
        setup,
        workspace,
        solve,
        apply,
        iterations: results.iter().map(|r| r.iterations()).collect(),
        residuals: results.iter().map(|r| r.residual_norm()).collect(),
        tolerances: results.iter().map(|r| r.tolerance()).collect(),
        retained: pre.retained_bytes(),
        scratch: ws.byte_len(),
    }
}

fn print_structure(case: &Case, route: usize) {
    let pre = build(&case.graph, route);
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
            .pruned_transfer()
            .map(|t| t.represented_coarse_dimension())
            .or_else(|| level.aggregation().map(|a| a.coarse_dimension()))
            .unwrap_or(0);
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
}

fn main() {
    let mut args = std::env::args().skip(1);
    let repetitions = args
        .next()
        .map(|x| x.parse::<usize>().expect("integer repetitions"))
        .unwrap_or(9);
    let rhs_count = args
        .next()
        .map(|x| x.parse::<usize>().expect("integer RHS count"))
        .unwrap_or(1);
    let filter = args.next().unwrap_or_default();
    assert!(repetitions > 0 && rhs_count > 0);
    println!(
        "{{\"type\":\"environment\",\"source\":\"{}\",\"os\":\"{}\",\"arch\":\"{}\",\"repetitions\":{repetitions},\"rhs_count\":{rhs_count},\"warmups\":2,\"parallel_execution\":false}}",
        option_env!("CMG_BENCH_COMMIT").unwrap_or("unrecorded"),
        std::env::consts::OS,
        std::env::consts::ARCH
    );
    for case in cases().into_iter().filter(|c| c.name.contains(&filter)) {
        let components = Components::from_laplacian(&case.graph);
        let rhs: Vec<_> = (0..rhs_count)
            .map(|column| {
                let mut x: Vec<_> = (0..case.graph.vertex_count())
                    .map(|v| ((v * 7 + column * 11) % 31) as f64 / 16.0)
                    .collect();
                components.center_in_place(&mut x).unwrap();
                case.graph.matvec(&x).unwrap()
            })
            .collect();
        for route in 0..4 {
            print_structure(&case, route);
        }
        for round in 0..repetitions + 2 {
            // Rotate all arms, including warm-ups; raw samples preserve pairing.
            for offset in 0..4 {
                let route = (round + offset) % 4;
                let s = measure(&case.graph, &rhs, route);
                if round < 2 {
                    continue;
                }
                let total = s.setup + s.workspace + s.solve;
                println!(
                    "{{\"type\":\"sample\",\"case\":\"{}\",\"route\":\"{}\",\"round\":{},\"setup_ns\":{},\"workspace_allocation_ns\":{},\"solve_ns\":{},\"total_ns\":{total},\"apply_ns\":{},\"iterations\":{:?},\"residuals\":{:?},\"tolerances\":{:?},\"retained_preconditioner_bytes\":{},\"pcg_workspace_bytes\":{}}}",
                    case.name,
                    names(route),
                    round - 2,
                    s.setup,
                    s.workspace,
                    s.solve,
                    s.apply,
                    s.iterations,
                    s.residuals,
                    s.tolerances,
                    s.retained,
                    s.scratch
                );
            }
        }
    }
}
