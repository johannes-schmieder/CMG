use cmg::{
    CmgOptions, CmgPreconditioner, Laplacian, ParallelCmgPlan, ParallelExecutor, ParallelOptions,
};
use std::hint::black_box;
use std::time::Instant;

fn median(values: &mut [u128]) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn graph(case: &str, scale: usize) -> Laplacian {
    match case {
        "path" => Laplacian::from_edges(
            scale,
            (0..scale.saturating_sub(1)).map(|vertex| (vertex, vertex + 1, 1.0)),
        )
        .expect("valid path"),
        "worker-firm" | "dense-worker-firm" => {
            let degree = if case == "worker-firm" { 3 } else { 16 };
            let vertices = scale * 2;
            let edges = (0..scale).flat_map(|worker| {
                (0..degree).map(move |link| {
                    let firm = if link == 0 {
                        worker
                    } else if link == 1 {
                        (worker + 1) % scale
                    } else {
                        ((2 * link + 1) * worker + 17 * link + 3) % scale
                    };
                    (
                        worker,
                        scale + firm,
                        0.25 + ((worker + 7 * link) % 23) as f64 / 16.0,
                    )
                })
            });
            Laplacian::from_edges(vertices, edges).expect("valid worker-firm graph")
        }
        _ => panic!("case must be path, worker-firm, or dense-worker-firm"),
    }
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments
        .next()
        .unwrap_or_else(|| "dense-worker-firm".to_owned());
    let scale = arguments
        .next()
        .map(|value| value.parse().expect("integer scale"))
        .unwrap_or(100_000);
    let repetitions = arguments
        .next()
        .map(|value| value.parse().expect("integer repetitions"))
        .unwrap_or(5);
    let threads = arguments
        .next()
        .map(|value| value.parse().expect("integer threads"))
        .unwrap_or(32);
    let graph = graph(&case, scale);
    let executor = ParallelExecutor::new(ParallelOptions {
        threads,
        ..ParallelOptions::default()
    })
    .expect("executor");
    let preconditioner =
        CmgPreconditioner::build_with_executor(&graph, CmgOptions::default(), &executor)
            .expect("preconditioner");
    black_box(ParallelCmgPlan::build(&preconditioner, &executor).expect("warm plan"));
    let mut samples = Vec::with_capacity(repetitions);
    let mut profiles = Vec::with_capacity(repetitions);
    let mut plan = None;
    for _ in 0..repetitions {
        let start = Instant::now();
        let (candidate, profile) =
            ParallelCmgPlan::build_profiled(&preconditioner, &executor).expect("profiled plan");
        samples.push(start.elapsed().as_nanos());
        profiles.push(profile);
        plan = Some(candidate);
    }
    let plan = plan.expect("measured plan");
    let level_json = profiles
        .first()
        .map(|profile| {
            profile
                .levels()
                .iter()
                .enumerate()
                .map(|(level_index, level)| {
                    let mut level_samples = profiles
                        .iter()
                        .map(|profile| profile.levels()[level_index].construction_nanoseconds())
                        .collect::<Vec<_>>();
                    let level_sample_json = level_samples
                        .iter()
                        .map(u128::to_string)
                        .collect::<Vec<_>>()
                        .join(",");
                    let level_median = median(&mut level_samples);
                    format!(
                        "{{\"level\":{},\"vertices\":{},\"edges\":{},\"eligible\":{},\"reason\":\"{}\",\"retained_bytes\":{},\"construction_samples_ns\":[{}],\"construction_median_ns\":{},\"row_counts_ns\":{},\"row_offsets_ns\":{},\"allocation_ns\":{},\"scatter_ns\":{},\"validation_ns\":{}}}",
                        level.level(), level.vertices(), level.edges(), level.eligible(), level.reason(),
                        level.retained_bytes(), level_sample_json, level_median,
                        level.row_counts_nanoseconds(), level.row_offsets_nanoseconds(),
                        level.allocation_nanoseconds(), level.scatter_nanoseconds(),
                        level.validation_nanoseconds(),
                    )
                })
                .collect::<Vec<_>>()
                .join(",")
        })
        .unwrap_or_default();
    let sample_json = samples
        .iter()
        .map(u128::to_string)
        .collect::<Vec<_>>()
        .join(",");
    let sample_median = median(&mut samples);
    println!(
        "{{\"protocol_version\":\"cmg-scc2-v1\",\"case\":\"{case}\",\"vertices\":{},\"edges\":{},\"threads\":{threads},\"repetitions\":{repetitions},\"operator_count\":{},\"plan_bytes\":{},\"cold_build_samples_ns\":[{}],\"cold_build_median_ns\":{},\"levels\":[{}],\"construction_path\":\"production\",\"subphase_support\":\"production eligibility and complete per-level CSR construction\"}}",
        graph.vertex_count(),
        graph.edge_count(),
        plan.operator_count(),
        plan.byte_len(),
        sample_json,
        sample_median,
        level_json,
    );
}
