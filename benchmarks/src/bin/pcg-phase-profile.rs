use std::hint::black_box;
use std::time::Instant;

use cmg::{
    CmgOptions, Laplacian, ParallelOptions, ParallelPcgSolver, PcgOptions, PcgPhaseProfile,
    PcgWorkspace, profile_pcg_with_plan, solve_pcg_with_plan_and_workspace,
};

struct BenchGraph {
    graph: Laplacian,
    input_edges: usize,
}

#[derive(Default)]
struct PhaseSamples {
    total: Vec<u128>,
    setup: Vec<u128>,
    preconditioner: Vec<u128>,
    matvec: Vec<u128>,
    dot_products: Vec<u128>,
    vector_updates: Vec<u128>,
    centering: Vec<u128>,
    norms: Vec<u128>,
    residual_recompute: Vec<u128>,
    certification: Vec<u128>,
    unattributed: Vec<u128>,
    calls: Option<[usize; 9]>,
}

impl PhaseSamples {
    fn push(&mut self, profile: &PcgPhaseProfile) {
        let samples = [
            profile.setup(),
            profile.preconditioner(),
            profile.matvec(),
            profile.dot_products(),
            profile.vector_updates(),
            profile.centering(),
            profile.norms(),
            profile.residual_recompute(),
            profile.certification(),
        ];
        let calls = samples.map(|sample| sample.calls());
        match self.calls {
            Some(expected) => assert_eq!(expected, calls, "phase call counts changed"),
            None => self.calls = Some(calls),
        }
        self.total.push(profile.total_nanoseconds());
        self.setup.push(samples[0].nanoseconds());
        self.preconditioner.push(samples[1].nanoseconds());
        self.matvec.push(samples[2].nanoseconds());
        self.dot_products.push(samples[3].nanoseconds());
        self.vector_updates.push(samples[4].nanoseconds());
        self.centering.push(samples[5].nanoseconds());
        self.norms.push(samples[6].nanoseconds());
        self.residual_recompute.push(samples[7].nanoseconds());
        self.certification.push(samples[8].nanoseconds());
        self.unattributed.push(profile.unattributed_nanoseconds());
    }
}

fn median(values: &mut [u128]) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn path_graph(vertices: usize) -> BenchGraph {
    let edges: Vec<_> = (0..vertices.saturating_sub(1))
        .map(|vertex| (vertex, vertex + 1, 0.5 + (vertex % 31) as f64 / 17.0))
        .collect();
    let input_edges = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).expect("valid path graph"),
        input_edges,
    }
}

fn worker_firm_graph(per_side: usize, degree: usize) -> BenchGraph {
    assert!(degree >= 2);
    let vertices = 2 * per_side;
    let firm_offset = per_side;
    let mut edges = Vec::with_capacity(degree * per_side);
    for worker in 0..per_side {
        for link in 0..degree {
            let firm = if link == 0 {
                worker
            } else if link == 1 {
                (worker + 1) % per_side
            } else {
                ((2 * link + 1) * worker + 17 * link + 3) % per_side
            };
            let weight = 0.25 + ((worker + 7 * link) % 23) as f64 / 16.0;
            edges.push((worker, firm_offset + firm, weight));
        }
    }
    let input_edges = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).expect("valid worker-firm graph"),
        input_edges,
    }
}

fn build_case(case: &str, scale: usize) -> BenchGraph {
    match case {
        "path" => path_graph(scale),
        "worker-firm" => worker_firm_graph(scale, 3),
        "dense-worker-firm" => worker_firm_graph(scale, 16),
        _ => panic!("unknown case {case}; expected path, worker-firm, or dense-worker-firm"),
    }
}

fn compatible_rhs(graph: &Laplacian) -> Vec<f64> {
    let mut target: Vec<f64> = (0..graph.vertex_count())
        .map(|vertex| {
            let centered = (vertex % 257) as f64 - 128.0;
            centered / 37.0 + ((vertex * 17) % 19) as f64 / 101.0
        })
        .collect();
    let mean = target.iter().sum::<f64>() / target.len().max(1) as f64;
    for value in &mut target {
        *value -= mean;
    }
    graph.matvec(&target).expect("known compatible rhs")
}

fn assert_same_solution(expected: &[f64], actual: &[f64]) {
    assert_eq!(expected.len(), actual.len());
    for (&left, &right) in expected.iter().zip(actual) {
        assert_eq!(left.to_bits(), right.to_bits());
    }
}

fn phase_json(name: &str, calls: usize, median_ns: u128, total_ns: u128) -> String {
    let share = if total_ns == 0 {
        0.0
    } else {
        median_ns as f64 / total_ns as f64
    };
    format!(
        "\"{name}\":{{\"median_ns\":{median_ns},\"calls\":{calls},\"share\":{share:.17e}}}"
    )
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().unwrap_or_else(|| "worker-firm".to_owned());
    let scale = arguments
        .next()
        .map(|argument| argument.parse::<usize>().expect("scale must be an integer"))
        .unwrap_or(200_000);
    let repetitions = arguments
        .next()
        .map(|argument| {
            argument
                .parse::<usize>()
                .expect("repetitions must be an integer")
        })
        .unwrap_or(3)
        .max(1);
    let threads = arguments
        .next()
        .map(|argument| {
            argument
                .parse::<usize>()
                .expect("threads must be an integer")
        })
        .unwrap_or(4)
        .max(1);

    let bench = build_case(&case, scale);
    let rhs = compatible_rhs(&bench.graph);
    let solver = ParallelPcgSolver::build(
        &bench.graph,
        CmgOptions::default(),
        ParallelOptions {
            threads,
            ..ParallelOptions::default()
        },
    )
    .expect("parallel solver should build");
    let options = PcgOptions::default();
    let mut production_workspace = PcgWorkspace::new(solver.preconditioner());

    let warm = solve_pcg_with_plan_and_workspace(
        solver.graph(),
        solver.preconditioner(),
        solver.plan(),
        &rhs,
        options,
        &mut production_workspace,
        solver.executor(),
    )
    .expect("planned solve should converge");
    black_box(&warm);

    let mut production_ns = Vec::with_capacity(repetitions);
    let mut phases = PhaseSamples::default();
    let mut reference_iterations = None;
    let mut reference_restarts = None;
    let mut reference_residual_bits = None;
    let mut reference_backward_bits = None;

    for repetition in 0..repetitions {
        let (production_elapsed, production) = if repetition % 2 == 0 {
            let start = Instant::now();
            let result = solve_pcg_with_plan_and_workspace(
                solver.graph(),
                solver.preconditioner(),
                solver.plan(),
                black_box(&rhs),
                options,
                &mut production_workspace,
                solver.executor(),
            )
            .expect("planned solve should converge");
            (start.elapsed().as_nanos(), result)
        } else {
            let profiled = profile_pcg_with_plan(
                solver.graph(),
                solver.preconditioner(),
                solver.plan(),
                black_box(&rhs),
                options,
                solver.executor(),
            )
            .expect("profiled solve should converge");
            assert_same_solution(warm.solution(), profiled.solution());
            phases.push(profiled.profile());

            let start = Instant::now();
            let result = solve_pcg_with_plan_and_workspace(
                solver.graph(),
                solver.preconditioner(),
                solver.plan(),
                black_box(&rhs),
                options,
                &mut production_workspace,
                solver.executor(),
            )
            .expect("planned solve should converge");
            (start.elapsed().as_nanos(), result)
        };

        assert_same_solution(warm.solution(), production.solution());
        production_ns.push(production_elapsed);
        reference_iterations.get_or_insert(production.iterations());
        reference_restarts.get_or_insert(production.restarts());
        reference_residual_bits.get_or_insert(production.residual_norm().to_bits());
        reference_backward_bits.get_or_insert(production.backward_error().to_bits());
        assert_eq!(reference_iterations, Some(production.iterations()));
        assert_eq!(reference_restarts, Some(production.restarts()));
        assert_eq!(
            reference_residual_bits,
            Some(production.residual_norm().to_bits())
        );
        assert_eq!(
            reference_backward_bits,
            Some(production.backward_error().to_bits())
        );

        if repetition % 2 == 0 {
            let profiled = profile_pcg_with_plan(
                solver.graph(),
                solver.preconditioner(),
                solver.plan(),
                black_box(&rhs),
                options,
                solver.executor(),
            )
            .expect("profiled solve should converge");
            assert_same_solution(production.solution(), profiled.solution());
            assert_eq!(production.iterations(), profiled.iterations());
            assert_eq!(production.restarts(), profiled.restarts());
            assert_eq!(
                production.residual_norm().to_bits(),
                profiled.residual_norm().to_bits()
            );
            assert_eq!(
                production.backward_error().to_bits(),
                profiled.backward_error().to_bits()
            );
            phases.push(profiled.profile());
        }
    }

    let production_median_ns = median(&mut production_ns);
    let total_ns = median(&mut phases.total);
    let setup_ns = median(&mut phases.setup);
    let preconditioner_ns = median(&mut phases.preconditioner);
    let matvec_ns = median(&mut phases.matvec);
    let dot_ns = median(&mut phases.dot_products);
    let update_ns = median(&mut phases.vector_updates);
    let centering_ns = median(&mut phases.centering);
    let norms_ns = median(&mut phases.norms);
    let recompute_ns = median(&mut phases.residual_recompute);
    let certification_ns = median(&mut phases.certification);
    let unattributed_ns = median(&mut phases.unattributed);
    let calls = phases.calls.expect("profile calls should be recorded");

    let parallel_core_ns = preconditioner_ns
        .saturating_add(matvec_ns)
        .saturating_add(recompute_ns);
    let serial_outer_ns = dot_ns
        .saturating_add(update_ns)
        .saturating_add(centering_ns)
        .saturating_add(norms_ns)
        .saturating_add(certification_ns);
    let parallel_core_share = parallel_core_ns as f64 / total_ns.max(1) as f64;
    let serial_outer_share = serial_outer_ns as f64 / total_ns.max(1) as f64;
    let profile_overhead = total_ns as f64 / production_median_ns.max(1) as f64;

    let phase_json = [
        phase_json("setup", calls[0], setup_ns, total_ns),
        phase_json("preconditioner", calls[1], preconditioner_ns, total_ns),
        phase_json("matvec", calls[2], matvec_ns, total_ns),
        phase_json("dot_products", calls[3], dot_ns, total_ns),
        phase_json("vector_updates", calls[4], update_ns, total_ns),
        phase_json("centering", calls[5], centering_ns, total_ns),
        phase_json("norms", calls[6], norms_ns, total_ns),
        phase_json(
            "residual_recompute",
            calls[7],
            recompute_ns,
            total_ns,
        ),
        phase_json(
            "certification",
            calls[8],
            certification_ns,
            total_ns,
        ),
    ]
    .join(",");

    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"vertices\":{},\"input_edges\":{},\"edges\":{},\"threads\":{threads},\"repetitions\":{repetitions},\"operators\":{},\"iterations\":{},\"restarts\":{},\"residual_norm\":{:.17e},\"backward_error\":{:.17e},\"production_median_ns\":{production_median_ns},\"profile_total_median_ns\":{total_ns},\"profile_overhead_ratio\":{profile_overhead:.17e},\"parallel_core_median_ns\":{parallel_core_ns},\"parallel_core_share\":{parallel_core_share:.17e},\"serial_outer_median_ns\":{serial_outer_ns},\"serial_outer_share\":{serial_outer_share:.17e},\"unattributed_median_ns\":{unattributed_ns},\"phases\":{{{phase_json}}}}}",
        bench.graph.vertex_count(),
        bench.input_edges,
        bench.graph.edge_count(),
        solver.plan().operator_count(),
        warm.iterations(),
        warm.restarts(),
        warm.residual_norm(),
        warm.backward_error(),
    );
}
