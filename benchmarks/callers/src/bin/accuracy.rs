//! Diagnostic accuracy study. Timings here are not performance qualification.
use cmg::{
    CmgOptions, CmgPreconditioner, Components, Laplacian, PcgOptions, PcgWorkspace,
    solve_pcg_with_workspace,
};
use std::time::Instant;
use std::{fs::OpenOptions, io::Write};

#[allow(dead_code)]
#[path = "../../../src/component_fixtures.rs"]
mod fixtures;

fn add(sum: &mut f64, correction: &mut f64, value: f64) {
    let next = *sum + value;
    *correction += if sum.abs() >= value.abs() {
        (*sum - next) + value
    } else {
        (value - next) + *sum
    };
    *sum = next;
}

fn norm(values: &[f64]) -> f64 {
    values.iter().fold(0.0_f64, |a, &x| a.hypot(x))
}

fn difference(left: &[f64], right: &[f64]) -> Vec<f64> {
    left.iter().zip(right).map(|(a, b)| a - b).collect()
}

fn hash(values: &[f64]) -> String {
    let mut hash = 0xcbf29ce484222325u64;
    for value in values {
        for byte in value.to_bits().to_le_bytes() {
            hash = (hash ^ u64::from(byte)).wrapping_mul(0x100000001b3);
        }
    }
    format!("{hash:016x}")
}

// Independent weighted-forest reference: subtree RHS sums give edge currents;
// integrating current/weight gives potentials. No CMG hierarchy or Krylov loop.
fn forest_solve(graph: &Laplacian, rhs: &[f64]) -> Option<Vec<f64>> {
    let mut centered = rhs.to_vec();
    Components::from_laplacian(graph)
        .center_in_place(&mut centered)
        .unwrap();
    forest_solve_on_range(graph, &centered)
}

fn forest_solve_on_range(graph: &Laplacian, rhs: &[f64]) -> Option<Vec<f64>> {
    let n = graph.vertex_count();
    let mut adjacency = vec![Vec::new(); n];
    for edge in graph.edges() {
        adjacency[edge.u()].push((edge.v(), edge.weight()));
        adjacency[edge.v()].push((edge.u(), edge.weight()));
    }
    let mut parent = vec![usize::MAX; n];
    let mut weight = vec![0.0; n];
    let mut order = Vec::with_capacity(n);
    for root in 0..n {
        if parent[root] != usize::MAX {
            continue;
        }
        parent[root] = root;
        let mut cursor = order.len();
        order.push(root);
        while cursor < order.len() {
            let u = order[cursor];
            cursor += 1;
            for &(v, w) in &adjacency[u] {
                if parent[v] == usize::MAX {
                    parent[v] = u;
                    weight[v] = w;
                    order.push(v);
                } else if v != parent[u] {
                    return None;
                }
            }
        }
    }
    let components = Components::from_laplacian(graph);
    let mut sums = rhs.to_vec();
    let mut correction = vec![0.0; n];
    for &v in order.iter().rev() {
        if parent[v] != v {
            // Keep both terms across levels; rounding them together at every
            // parent discards the compensation on a long path.
            let high = sums[v];
            let low = correction[v];
            add(&mut sums[parent[v]], &mut correction[parent[v]], high);
            add(&mut sums[parent[v]], &mut correction[parent[v]], low);
        }
    }
    let mut solution = vec![0.0; n];
    for &v in &order {
        if parent[v] != v {
            solution[v] = solution[parent[v]] + (sums[v] + correction[v]) / weight[v];
        }
    }
    components.center_in_place(&mut solution).unwrap();
    Some(solution)
}

fn metrics(graph: &Laplacian, rhs: &[f64], target: &[f64], solution: &[f64]) -> String {
    let residual = difference(rhs, &graph.matvec(solution).unwrap());
    let error = difference(solution, target);
    let components = Components::from_laplacian(graph);
    let mut error_norms = vec![0.0_f64; components.count()];
    let mut target_norms = error_norms.clone();
    let mut rhs_norms = error_norms.clone();
    let mut residual_norms = error_norms.clone();
    for (v, &c) in components.labels().iter().enumerate() {
        error_norms[c] = error_norms[c].hypot(error[v]);
        target_norms[c] = target_norms[c].hypot(target[v]);
        rhs_norms[c] = rhs_norms[c].hypot(rhs[v]);
        residual_norms[c] = residual_norms[c].hypot(residual[v]);
    }
    let largest = components
        .sizes()
        .iter()
        .enumerate()
        .max_by_key(|(_, size)| *size)
        .unwrap()
        .0;
    let energy_error = (graph.energy(&error).unwrap() / graph.energy(target).unwrap()).sqrt();
    let max_component_relative_residual = rhs_norms
        .iter()
        .zip(&residual_norms)
        .filter(|(b, _)| **b > 0.0)
        .map(|(b, r)| r / b)
        .fold(0.0_f64, f64::max);
    format!(
        "\"relative_solution_error\":{},\"relative_energy_error\":{},\"fresh_residual\":{},\"relative_residual\":{},\"largest_component_error\":{},\"largest_component_relative_residual\":{},\"max_component_relative_residual\":{},\"solution_hash\":\"{}\"",
        norm(&error) / norm(target),
        energy_error,
        norm(&residual),
        norm(&residual) / norm(rhs),
        error_norms[largest] / target_norms[largest],
        residual_norms[largest] / rhs_norms[largest],
        max_component_relative_residual,
        hash(solution),
    )
}

fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    assert!(
        args.len() == 5 || (args.len() == 7 && args[5] == "--export"),
        "suite case tolerance restart_interval max_iterations [--export PATH]"
    );
    let suite = &args[0];
    let cases = match suite.as_str() {
        "large" => fixtures::large(20260908),
        "stress" => fixtures::stress(20260906),
        "sentinels" => fixtures::sentinels(),
        _ => panic!("unknown suite"),
    };
    let case = cases.into_iter().find(|x| x.name == args[1]).unwrap();
    let tolerance: f64 = args[2].parse().unwrap();
    let restart: usize = args[3].parse().unwrap();
    let max_iterations: usize = args[4].parse().unwrap();
    let graph = &case.graph;
    let components = Components::from_laplacian(graph);
    let mut target: Vec<_> = (0..graph.vertex_count())
        .map(|v| ((v * 7) % 31) as f64 / 16.0)
        .collect();
    components.center_in_place(&mut target).unwrap();
    let rhs = graph.matvec(&target).unwrap();
    let source = option_env!("CMG_BENCH_COMMIT").unwrap_or("unrecorded");
    assert_ne!(
        source, "unrecorded",
        "record the numerical source at build time"
    );
    println!(
        "{{\"type\":\"input\",\"source\":\"{source}\",\"case\":\"{}\",\"vertices\":{},\"edges\":{},\"components\":{},\"rhs_hash\":\"{}\",\"target_hash\":\"{}\",\"rhs_norm\":{},\"operator_bound\":{},\"tolerance\":{tolerance},\"restart_interval\":{restart},\"max_iterations\":{max_iterations}}}",
        case.name,
        graph.vertex_count(),
        graph.edge_count(),
        components.count(),
        hash(&rhs),
        hash(&target),
        norm(&rhs),
        graph.operator_norm_bound(),
    );
    let mut projected_rhs = rhs.clone();
    let projection_norm = components
        .project_rhs_in_place(&mut projected_rhs, PcgOptions::default().validation)
        .unwrap();
    if args.len() == 7 {
        let mut centered_rhs = rhs.clone();
        components.center_in_place(&mut centered_rhs).unwrap();
        let forest_uniform = forest_solve_on_range(graph, &centered_rhs)
            .map_or_else(|| "null".to_owned(), |v| format!("{v:?}"));
        let forest_projected = forest_solve_on_range(graph, &projected_rhs)
            .map_or_else(|| "null".to_owned(), |v| format!("{v:?}"));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&args[6])
            .unwrap();
        write!(file, "{{\"source\":\"{source}\",\"vertices\":{},\"rhs\":{rhs:?},\"centered_rhs\":{centered_rhs:?},\"projected_rhs\":{projected_rhs:?},\"target\":{target:?},\"forest_uniform\":{forest_uniform},\"forest_projected\":{forest_projected},\"edges\":[", graph.vertex_count()).unwrap();
        for (index, edge) in graph.edges().iter().enumerate() {
            if index != 0 {
                write!(file, ",").unwrap();
            }
            write!(file, "[{},{},{}]", edge.u(), edge.v(), edge.weight()).unwrap();
        }
        writeln!(file, "]}}").unwrap();
        return;
    }
    let reference = forest_solve(graph, &rhs);
    if let Some(reference) = &reference {
        println!(
            "{{\"type\":\"forest_reference\",{}}}",
            metrics(graph, &rhs, &target, reference)
        );
    }
    let projected_reference = forest_solve_on_range(graph, &projected_rhs);
    if let Some(reference) = &projected_reference {
        println!(
            "{{\"type\":\"forest_projected_reference\",\"projection_norm\":{projection_norm},{}}}",
            metrics(graph, &rhs, &target, reference)
        );
    }
    let start = Instant::now();
    let preconditioner = CmgPreconditioner::build(graph, CmgOptions::default()).unwrap();
    let setup_ns = start.elapsed().as_nanos();
    let mut workspace = PcgWorkspace::new(&preconditioner);
    let start = Instant::now();
    let result = solve_pcg_with_workspace(
        graph,
        &preconditioner,
        &rhs,
        PcgOptions {
            relative_tolerance: tolerance,
            residual_recompute_interval: restart,
            max_iterations,
            ..PcgOptions::default()
        },
        &mut workspace,
    );
    let solve_ns = start.elapsed().as_nanos();
    match result {
        Ok(result) => {
            let reference_error = reference
                .as_ref()
                .map_or(0.0, |x| norm(&difference(result.solution(), x)) / norm(x));
            let projected_reference_error = projected_reference
                .as_ref()
                .map_or(0.0, |x| norm(&difference(result.solution(), x)) / norm(x));
            println!(
                "{{\"type\":\"result\",\"status\":\"ok\",\"setup_ns\":{setup_ns},\"solve_ns\":{solve_ns},\"iterations\":{},\"restarts\":{},\"allowed_residual\":{},\"backward_error\":{},\"error_vs_forest\":{reference_error},\"error_vs_projected_forest\":{projected_reference_error},{}}}",
                result.iterations(),
                result.restarts(),
                result.tolerance(),
                result.backward_error(),
                metrics(graph, &rhs, &target, result.solution()),
            );
        }
        Err(error) => {
            let message = error.to_string();
            println!(
                "{{\"type\":\"result\",\"status\":\"error\",\"setup_ns\":{setup_ns},\"solve_ns\":{solve_ns},\"error\":{message:?}}}"
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn subtree_compensation_survives_parent_cancellation() {
        let graph =
            Laplacian::from_edges(5, [(0, 1, 1.0), (1, 2, 1e16), (2, 3, 1e16), (3, 4, 1e16)])
                .unwrap();
        let rhs = [0.0, -1.0, 1e16, 1.0, -1e16];
        let solution = forest_solve_on_range(&graph, &rhs).unwrap();
        // The subtree below vertex 1 has exact zero total current. Collapsing
        // a child's compensated pair would instead lose the unit at vertex 3.
        assert!((solution[0] - solution[1]).abs() < 1e-14);
        assert!((solution[4] - solution[0] + 2.0).abs() < 1e-14);
    }

    #[test]
    fn forest_reference_solves_weighted_branches_pairs_and_isolates() {
        let graph = Laplacian::from_edges(7, [(0, 1, 0.5), (0, 2, 3.0), (2, 3, 0.25), (4, 5, 2.0)])
            .unwrap();
        let mut target = vec![3.0, -2.0, 4.0, 1.0, 0.5, -0.5, 0.0];
        Components::from_laplacian(&graph)
            .center_in_place(&mut target)
            .unwrap();
        let rhs = graph.matvec(&target).unwrap();
        let actual = forest_solve(&graph, &rhs).unwrap();
        assert!(norm(&difference(&actual, &target)) < 1e-13);
        assert!(norm(&difference(&graph.matvec(&actual).unwrap(), &rhs)) < 1e-13);
    }

    #[test]
    fn forest_reference_rejects_cycles() {
        let graph = Laplacian::from_edges(3, [(0, 1, 1.0), (1, 2, 1.0), (2, 0, 1.0)]).unwrap();
        assert!(forest_solve(&graph, &[1.0, 0.0, -1.0]).is_none());
    }
}
