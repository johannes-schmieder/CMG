use std::fmt::Write as _;
use std::hint::black_box;
use std::time::Instant;

use cmg::{
    Aggregation, CmgHierarchy, CmgOptions, CmgPreconditioner, GroundedLdl, Laplacian,
    TerminalReason, forest_components, maximum_weight_forest, split_forest,
};

struct BenchGraph {
    graph: Laplacian,
    vertices: usize,
    edges: usize,
}

#[derive(Debug, Default)]
struct PhaseTimes {
    graph_clone_ns: u128,
    heavy_edge_ns: u128,
    forest_split_ns: u128,
    low_degree_ns: u128,
    forest_components_ns: u128,
    aggregation_ns: u128,
    contraction_ns: u128,
    inverse_diagonal_ns: u128,
    bookkeeping_ns: u128,
    total_ns: u128,
}

#[derive(Debug)]
struct RetainedLevel {
    graph: Laplacian,
    inverse_diagonal: Vec<f64>,
    aggregation: Option<Aggregation>,
}

#[derive(Debug)]
struct ManualProfile {
    times: PhaseTimes,
    terminal_reason: TerminalReason,
    vertex_counts: Vec<usize>,
    matrix_nonzeros: Vec<usize>,
    cumulative_coarsened_nonzeros: usize,
    retained_summary: RetainedSummary,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct RetainedSummary {
    vertices: usize,
    edges: usize,
    inverse_values: usize,
    labels: usize,
    aggregate_sizes: usize,
}

fn median(mut values: Vec<u128>) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn path_graph(vertices: usize) -> BenchGraph {
    let edges: Vec<_> = (0..vertices.saturating_sub(1))
        .map(|vertex| (vertex, vertex + 1, 1.0))
        .collect();
    let edge_count = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).expect("valid path graph"),
        vertices,
        edges: edge_count,
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
    let edge_count = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).expect("valid worker-firm graph"),
        vertices,
        edges: edge_count,
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

fn terminal_name(reason: TerminalReason) -> &'static str {
    match reason {
        TerminalReason::Direct => "direct",
        TerminalReason::FullContraction => "full_contraction",
        TerminalReason::StagnatedVertexReduction => "stagnated_vertex_reduction",
        TerminalReason::StagnatedFill => "stagnated_fill",
        TerminalReason::MaximumLevels => "maximum_levels",
    }
}

fn corrected_parent(
    graph: &Laplacian,
    split_parent: &[usize],
    selected_weight: &[f64],
    threshold: f64,
) -> Vec<usize> {
    let mut final_parent = split_parent.to_vec();
    let has_low_effective_degree = graph
        .diagonal()
        .iter()
        .zip(selected_weight)
        .any(|(degree, weight)| *degree > 0.0 && *weight / *degree < threshold);

    if has_low_effective_degree {
        let mut selected_incident_weight = vec![0.0; graph.vertex_count()];
        for (vertex, &parent) in split_parent.iter().enumerate() {
            if parent != vertex {
                let weight = selected_weight[vertex];
                selected_incident_weight[vertex] += weight;
                selected_incident_weight[parent] += weight;
            }
        }
        for (vertex, (&degree, &tree_weight)) in graph
            .diagonal()
            .iter()
            .zip(&selected_incident_weight)
            .enumerate()
        {
            if degree > 0.0 && tree_weight / degree < threshold {
                final_parent[vertex] = vertex;
            }
        }
    }
    final_parent
}

fn inverse_diagonal(graph: &Laplacian) -> Vec<f64> {
    graph
        .diagonal()
        .iter()
        .map(|degree| if *degree > 0.0 { 0.5 / *degree } else { 0.0 })
        .collect()
}

fn retain_level(
    levels: &mut Vec<RetainedLevel>,
    graph: Laplacian,
    aggregation: Option<Aggregation>,
    times: &mut PhaseTimes,
) {
    let inverse_start = Instant::now();
    let inverse_diagonal = inverse_diagonal(&graph);
    times.inverse_diagonal_ns += inverse_start.elapsed().as_nanos();

    let bookkeeping_start = Instant::now();
    levels.push(RetainedLevel {
        graph,
        inverse_diagonal,
        aggregation,
    });
    times.bookkeeping_ns += bookkeeping_start.elapsed().as_nanos();
}

fn retained_summary(levels: &[RetainedLevel]) -> RetainedSummary {
    RetainedSummary {
        vertices: levels.iter().map(|level| level.graph.vertex_count()).sum(),
        edges: levels.iter().map(|level| level.graph.edge_count()).sum(),
        inverse_values: levels
            .iter()
            .map(|level| level.inverse_diagonal.len())
            .sum(),
        labels: levels
            .iter()
            .filter_map(|level| level.aggregation.as_ref())
            .map(|aggregation| aggregation.labels().len())
            .sum(),
        aggregate_sizes: levels
            .iter()
            .filter_map(|level| level.aggregation.as_ref())
            .map(|aggregation| aggregation.sizes().len())
            .sum(),
    }
}

fn profile_manual(graph: &Laplacian, options: CmgOptions) -> ManualProfile {
    let total_start = Instant::now();
    let options = options.validate().expect("valid CMG options");
    let initial_nonzeros = graph.matrix_nnz();
    let mut cumulative_nonzeros = 0_usize;

    let clone_start = Instant::now();
    let mut current = graph.clone();
    let mut times = PhaseTimes {
        graph_clone_ns: clone_start.elapsed().as_nanos(),
        ..PhaseTimes::default()
    };

    let mut levels = Vec::new();
    let mut vertex_counts = Vec::new();
    let mut matrix_nonzeros = Vec::new();
    let terminal_reason;

    loop {
        let bookkeeping_start = Instant::now();
        let n = current.vertex_count();
        let current_nonzeros = current.matrix_nnz();
        vertex_counts.push(n);
        matrix_nonzeros.push(current_nonzeros);
        let direct = n <= 1 || n < options.direct_threshold;
        times.bookkeeping_ns += bookkeeping_start.elapsed().as_nanos();

        if direct {
            terminal_reason = TerminalReason::Direct;
            retain_level(&mut levels, current, None, &mut times);
            break;
        }

        let heavy_start = Instant::now();
        let (heavy_parent, selected_weight) = maximum_weight_forest(&current);
        times.heavy_edge_ns += heavy_start.elapsed().as_nanos();

        let split_start = Instant::now();
        let split_parent = split_forest(&heavy_parent).expect("valid heavy-edge forest");
        times.forest_split_ns += split_start.elapsed().as_nanos();

        let low_degree_start = Instant::now();
        let final_parent = corrected_parent(
            &current,
            &split_parent,
            &selected_weight,
            options.low_effective_degree_threshold,
        );
        times.low_degree_ns += low_degree_start.elapsed().as_nanos();

        let components_start = Instant::now();
        let (labels, sizes) = forest_components(&final_parent).expect("valid corrected forest");
        times.forest_components_ns += components_start.elapsed().as_nanos();

        let aggregation_start = Instant::now();
        let aggregation = Aggregation::new(labels, sizes.len()).expect("valid forest aggregation");
        times.aggregation_ns += aggregation_start.elapsed().as_nanos();

        let bookkeeping_start = Instant::now();
        let coarse_count = aggregation.coarse_dimension();
        if coarse_count == 1 {
            times.bookkeeping_ns += bookkeeping_start.elapsed().as_nanos();
            terminal_reason = TerminalReason::FullContraction;
            retain_level(&mut levels, current, Some(aggregation), &mut times);
            break;
        }

        cumulative_nonzeros = cumulative_nonzeros.saturating_add(current_nonzeros);
        if coarse_count >= n.saturating_sub(1) {
            times.bookkeeping_ns += bookkeeping_start.elapsed().as_nanos();
            terminal_reason = TerminalReason::StagnatedVertexReduction;
            retain_level(&mut levels, current, Some(aggregation), &mut times);
            break;
        }

        let fill_limit = options.max_hierarchy_nnz_factor * initial_nonzeros as f64;
        if cumulative_nonzeros as f64 > fill_limit {
            times.bookkeeping_ns += bookkeeping_start.elapsed().as_nanos();
            terminal_reason = TerminalReason::StagnatedFill;
            retain_level(&mut levels, current, Some(aggregation), &mut times);
            break;
        }

        if levels.len() + 1 >= options.max_levels {
            times.bookkeeping_ns += bookkeeping_start.elapsed().as_nanos();
            terminal_reason = TerminalReason::MaximumLevels;
            retain_level(&mut levels, current, Some(aggregation), &mut times);
            break;
        }
        times.bookkeeping_ns += bookkeeping_start.elapsed().as_nanos();

        let contraction_start = Instant::now();
        let coarse = aggregation
            .contract(&current)
            .expect("deterministic coarse graph");
        times.contraction_ns += contraction_start.elapsed().as_nanos();

        let bookkeeping_start = Instant::now();
        let repeat = if coarse.matrix_nnz() == 0 {
            1
        } else {
            (current_nonzeros / coarse.matrix_nnz())
                .saturating_sub(1)
                .max(1)
        };
        black_box(repeat);
        times.bookkeeping_ns += bookkeeping_start.elapsed().as_nanos();

        retain_level(&mut levels, current, Some(aggregation), &mut times);
        current = coarse;
    }

    let summary = retained_summary(&levels);
    black_box(&levels);
    times.total_ns = total_start.elapsed().as_nanos();

    ManualProfile {
        times,
        terminal_reason,
        vertex_counts,
        matrix_nonzeros,
        cumulative_coarsened_nonzeros: cumulative_nonzeros,
        retained_summary: summary,
    }
}

fn verify_profile(profile: &ManualProfile, hierarchy: &CmgHierarchy) {
    assert_eq!(
        profile.terminal_reason,
        hierarchy.report().terminal_reason(),
        "manual terminal reason differs from production"
    );
    assert_eq!(
        profile.vertex_counts,
        hierarchy.report().vertex_counts(),
        "manual vertex counts differ from production"
    );
    assert_eq!(
        profile.matrix_nonzeros,
        hierarchy.report().matrix_nonzeros(),
        "manual nonzero counts differ from production"
    );
    assert_eq!(
        profile.cumulative_coarsened_nonzeros,
        hierarchy.report().cumulative_coarsened_nonzeros(),
        "manual fill accounting differs from production"
    );
}

fn json_array(values: &[usize]) -> String {
    let mut output = String::from("[");
    for (index, value) in values.iter().enumerate() {
        if index > 0 {
            output.push(',');
        }
        write!(&mut output, "{value}").expect("writing to a String cannot fail");
    }
    output.push(']');
    output
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().unwrap_or_else(|| "worker-firm".to_owned());
    let scale = arguments
        .next()
        .map(|argument| argument.parse::<usize>().expect("scale must be an integer"))
        .unwrap_or(100_000);
    let repetitions = arguments
        .next()
        .map(|argument| {
            argument
                .parse::<usize>()
                .expect("repetitions must be an integer")
        })
        .unwrap_or(5)
        .max(1);

    let bench_graph = build_case(&case, scale);
    let options = CmgOptions::default();

    let warm_manual = profile_manual(&bench_graph.graph, options);
    let warm_hierarchy =
        CmgHierarchy::build(&bench_graph.graph, options).expect("production hierarchy");
    verify_profile(&warm_manual, &warm_hierarchy);
    black_box(
        CmgPreconditioner::build(&bench_graph.graph, options).expect("production preconditioner"),
    );

    let mut manual_profiles = Vec::with_capacity(repetitions);
    let mut hierarchy_elapsed = Vec::with_capacity(repetitions);
    let mut preconditioner_elapsed = Vec::with_capacity(repetitions);

    for repetition in 0..repetitions {
        if repetition % 2 == 0 {
            let profile = profile_manual(black_box(&bench_graph.graph), options);
            verify_profile(&profile, &warm_hierarchy);
            manual_profiles.push(profile);

            let start = Instant::now();
            black_box(
                CmgHierarchy::build(black_box(&bench_graph.graph), options)
                    .expect("production hierarchy"),
            );
            hierarchy_elapsed.push(start.elapsed().as_nanos());

            let start = Instant::now();
            black_box(
                CmgPreconditioner::build(black_box(&bench_graph.graph), options)
                    .expect("production preconditioner"),
            );
            preconditioner_elapsed.push(start.elapsed().as_nanos());
        } else {
            let start = Instant::now();
            black_box(
                CmgPreconditioner::build(black_box(&bench_graph.graph), options)
                    .expect("production preconditioner"),
            );
            preconditioner_elapsed.push(start.elapsed().as_nanos());

            let start = Instant::now();
            black_box(
                CmgHierarchy::build(black_box(&bench_graph.graph), options)
                    .expect("production hierarchy"),
            );
            hierarchy_elapsed.push(start.elapsed().as_nanos());

            let profile = profile_manual(black_box(&bench_graph.graph), options);
            verify_profile(&profile, &warm_hierarchy);
            manual_profiles.push(profile);
        }
    }

    let mut terminal_elapsed = Vec::new();
    if warm_hierarchy.report().terminal_reason() == TerminalReason::Direct {
        let terminal = warm_hierarchy
            .levels()
            .last()
            .expect("hierarchy has a terminal level")
            .graph();
        black_box(GroundedLdl::factor(terminal).expect("terminal factorization"));
        terminal_elapsed.reserve(repetitions);
        for _ in 0..repetitions {
            let start = Instant::now();
            black_box(GroundedLdl::factor(terminal).expect("terminal factorization"));
            terminal_elapsed.push(start.elapsed().as_nanos());
        }
    }

    let phase_values = |field: fn(&PhaseTimes) -> u128| {
        manual_profiles
            .iter()
            .map(|profile| field(&profile.times))
            .collect::<Vec<_>>()
    };
    let phases = [
        (
            "graph_clone",
            median(phase_values(|times| times.graph_clone_ns)),
        ),
        (
            "heavy_edge",
            median(phase_values(|times| times.heavy_edge_ns)),
        ),
        (
            "forest_split",
            median(phase_values(|times| times.forest_split_ns)),
        ),
        (
            "low_degree",
            median(phase_values(|times| times.low_degree_ns)),
        ),
        (
            "forest_components",
            median(phase_values(|times| times.forest_components_ns)),
        ),
        (
            "aggregation",
            median(phase_values(|times| times.aggregation_ns)),
        ),
        (
            "contraction",
            median(phase_values(|times| times.contraction_ns)),
        ),
        (
            "inverse_diagonal",
            median(phase_values(|times| times.inverse_diagonal_ns)),
        ),
        (
            "bookkeeping",
            median(phase_values(|times| times.bookkeeping_ns)),
        ),
    ];
    let manual_total_ns = median(phase_values(|times| times.total_ns));
    let attributed_ns: u128 = phases.iter().map(|(_, value)| *value).sum();
    let (dominant_phase, dominant_ns) = phases
        .iter()
        .max_by_key(|(_, value)| *value)
        .copied()
        .expect("phase list is nonempty");
    let hierarchy_median_ns = median(hierarchy_elapsed);
    let preconditioner_median_ns = median(preconditioner_elapsed);
    let terminal_factor_median = if terminal_elapsed.is_empty() {
        None
    } else {
        Some(median(terminal_elapsed))
    };

    let baseline = &manual_profiles[0];
    for profile in &manual_profiles[1..] {
        assert_eq!(profile.terminal_reason, baseline.terminal_reason);
        assert_eq!(profile.vertex_counts, baseline.vertex_counts);
        assert_eq!(profile.matrix_nonzeros, baseline.matrix_nonzeros);
        assert_eq!(profile.retained_summary, baseline.retained_summary);
    }

    let mut phase_json = String::from("{");
    for (index, (name, value)) in phases.iter().enumerate() {
        if index > 0 {
            phase_json.push(',');
        }
        write!(&mut phase_json, "\"{name}\":{value}").expect("writing to a String cannot fail");
    }
    phase_json.push('}');

    let terminal_json =
        terminal_factor_median.map_or_else(|| "null".to_owned(), |value| value.to_string());
    let dominant_share = dominant_ns as f64 / manual_total_ns.max(1) as f64;
    let attributed_share = attributed_ns as f64 / manual_total_ns.max(1) as f64;
    let hierarchy_over_manual = hierarchy_median_ns as f64 / manual_total_ns.max(1) as f64;
    let finalization_estimate = preconditioner_median_ns.saturating_sub(hierarchy_median_ns);

    println!(
        concat!(
            "{{",
            "\"case\":\"{case}\",",
            "\"scale\":{scale},",
            "\"vertices\":{vertices},",
            "\"edges\":{edges},",
            "\"repetitions\":{repetitions},",
            "\"levels\":{levels},",
            "\"terminal_reason\":\"{terminal_reason}\",",
            "\"vertex_counts\":{vertex_counts},",
            "\"matrix_nonzeros\":{matrix_nonzeros},",
            "\"production_hierarchy_median_ns\":{hierarchy_median_ns},",
            "\"production_preconditioner_median_ns\":{preconditioner_median_ns},",
            "\"preconditioner_finalization_estimate_ns\":{finalization_estimate},",
            "\"terminal_factor_median_ns\":{terminal_json},",
            "\"manual_total_median_ns\":{manual_total_ns},",
            "\"manual_attributed_ns\":{attributed_ns},",
            "\"manual_unattributed_ns\":{unattributed_ns},",
            "\"manual_attributed_share\":{attributed_share:.6},",
            "\"production_hierarchy_over_manual\":{hierarchy_over_manual:.6},",
            "\"dominant_phase\":\"{dominant_phase}\",",
            "\"dominant_phase_share\":{dominant_share:.6},",
            "\"phase_median_ns\":{phase_json},",
            "\"retained_vertices\":{retained_vertices},",
            "\"retained_edges\":{retained_edges},",
            "\"retained_inverse_values\":{retained_inverse_values},",
            "\"retained_labels\":{retained_labels},",
            "\"retained_aggregate_sizes\":{retained_aggregate_sizes}",
            "}}"
        ),
        vertices = bench_graph.vertices,
        edges = bench_graph.edges,
        levels = baseline.vertex_counts.len(),
        terminal_reason = terminal_name(baseline.terminal_reason),
        vertex_counts = json_array(&baseline.vertex_counts),
        matrix_nonzeros = json_array(&baseline.matrix_nonzeros),
        unattributed_ns = manual_total_ns.saturating_sub(attributed_ns),
        retained_vertices = baseline.retained_summary.vertices,
        retained_edges = baseline.retained_summary.edges,
        retained_inverse_values = baseline.retained_summary.inverse_values,
        retained_labels = baseline.retained_summary.labels,
        retained_aggregate_sizes = baseline.retained_summary.aggregate_sizes,
    );
}
