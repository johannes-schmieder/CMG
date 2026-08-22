from pathlib import Path

ROOT = Path('.')


def replace_exact(path: str, old: str, new: str, expected: int = 1) -> None:
    file_path = ROOT / path
    text = file_path.read_text()
    count = text.count(old)
    if count != expected:
        raise RuntimeError(
            f"{path}: expected {expected} match(es), found {count}: {old[:120]!r}"
        )
    file_path.write_text(text.replace(old, new, expected))


preconditioner = ROOT / 'src/preconditioner.rs'
text = preconditioner.read_text()
if 'fn finite_preconditioner_value(' not in text:
    replace_exact(
        'src/preconditioner.rs',
        '''            for ((value, inverse_diagonal), rhs_value) in
                output.iter_mut().zip(level.inverse_diagonal()).zip(rhs)
            {
                *value = *inverse_diagonal * *rhs_value;
            }
            return Ok(());
''',
        '''            for ((value, inverse_diagonal), rhs_value) in
                output.iter_mut().zip(level.inverse_diagonal()).zip(rhs)
            {
                *value = finite_preconditioner_value(
                    *inverse_diagonal * *rhs_value,
                    "iterative terminal Jacobi application",
                )?;
            }
            return Ok(());
''',
    )
    replace_exact(
        'src/preconditioner.rs',
        '''                        *value = *inverse_diagonal * *rhs_value;
''',
        '''                        *value = finite_preconditioner_value(
                            *inverse_diagonal * *rhs_value,
                            "CMG initial Jacobi smoothing",
                        )?;
''',
    )
    replace_exact(
        'src/preconditioner.rs',
        '''                        *value += *inverse_diagonal * (*rhs_value - *matrix_value);
''',
        '''                        let increment = finite_preconditioner_value(
                            *inverse_diagonal * (*rhs_value - *matrix_value),
                            "CMG Jacobi smoothing increment",
                        )?;
                        *value = finite_preconditioner_value(
                            *value + increment,
                            "CMG Jacobi smoothing accumulation",
                        )?;
''',
        expected=2,
    )
    replace_exact(
        'src/preconditioner.rs',
        '''                    *residual = *rhs_value - *matrix_value;
''',
        '''                    *residual = finite_preconditioner_value(
                        *rhs_value - *matrix_value,
                        "CMG stationary residual",
                    )?;
''',
    )
    replace_exact(
        'src/preconditioner.rs',
        '''fn repeat_from_nonzeros(fine_nonzeros: usize, denominator_nonzeros: usize) -> usize {
''',
        '''fn finite_preconditioner_value(
    value: f64,
    context: &'static str,
) -> Result<f64, CmgError> {
    if value.is_finite() {
        Ok(value)
    } else {
        Err(CmgError::NonFiniteDerivedValue { context, value })
    }
}

fn repeat_from_nonzeros(fine_nonzeros: usize, denominator_nonzeros: usize) -> usize {
''',
    )

nonfinite = ROOT / 'tests/nonfinite.rs'
nonfinite_text = nonfinite.read_text()
if 'iterative_terminal_rejects_jacobi_overflow' not in nonfinite_text:
    nonfinite_text += r'''

#[test]
fn iterative_terminal_rejects_jacobi_overflow() {
    let graph = Laplacian::from_edges(2, [(0, 1, f64::MIN_POSITIVE)]).unwrap();
    let preconditioner = CmgPreconditioner::build(
        &graph,
        CmgOptions {
            direct_threshold: 1,
            ..CmgOptions::default()
        },
    )
    .unwrap();
    assert!(matches!(
        preconditioner.apply(&[10.0, -10.0]),
        Err(CmgError::NonFiniteDerivedValue { .. })
    ));
}

#[test]
fn direct_terminal_rejects_solution_overflow() {
    let graph = Laplacian::from_edges(2, [(0, 1, f64::MIN_POSITIVE)]).unwrap();
    let preconditioner = CmgPreconditioner::build(&graph, CmgOptions::default()).unwrap();
    assert!(matches!(
        preconditioner.apply(&[10.0, -10.0]),
        Err(CmgError::NonFiniteDerivedValue { .. })
    ));
}
'''
    nonfinite.write_text(nonfinite_text)

(ROOT / 'tests/exhaustive_small.rs').write_text(r'''use cmg::{
    CmgOptions, CmgPreconditioner, Components, Laplacian, PcgOptions, SddmMatrix, SddmSolver,
    ValidationOptions, solve_pcg,
};

struct Lcg(u64);

impl Lcg {
    fn new(seed: u64) -> Self {
        Self(seed)
    }

    fn next_u64(&mut self) -> u64 {
        self.0 = self
            .0
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        self.0
    }

    fn unit(&mut self) -> f64 {
        let bits = self.next_u64() >> 11;
        bits as f64 / ((1_u64 << 53) as f64)
    }

    fn usize(&mut self, upper: usize) -> usize {
        (self.next_u64() as usize) % upper
    }
}

fn pcg_options() -> PcgOptions {
    PcgOptions {
        relative_tolerance: 1.0e-10,
        absolute_tolerance: 1.0e-13,
        max_iterations: 2_000,
        residual_recompute_interval: 7,
        ..PcgOptions::default()
    }
}

fn forced_multilevel_options() -> CmgOptions {
    CmgOptions {
        direct_threshold: 2,
        max_levels: 64,
        ..CmgOptions::default()
    }
}

fn centered_target(graph: &Laplacian) -> Vec<f64> {
    let mut target: Vec<f64> = (0..graph.vertex_count())
        .map(|index| {
            let x = index as f64 + 1.0;
            (0.731 * x).sin() + 0.17 * x - 0.03 * x * x
        })
        .collect();
    Components::from_laplacian(graph)
        .center_in_place(&mut target)
        .unwrap();
    target
}

fn assert_vectors_close(left: &[f64], right: &[f64], tolerance: f64) {
    assert_eq!(left.len(), right.len());
    for (index, (left_value, right_value)) in left.iter().zip(right).enumerate() {
        let scale = 1.0_f64.max(left_value.abs()).max(right_value.abs());
        assert!(
            (left_value - right_value).abs() <= tolerance * scale,
            "index {index}: {left_value:.17e} differs from {right_value:.17e}"
        );
    }
}

fn assert_laplacian_solution(graph: &Laplacian) {
    let target = centered_target(graph);
    let rhs = graph.matvec(&target).unwrap();
    let preconditioner = CmgPreconditioner::build(graph, forced_multilevel_options()).unwrap();
    let result = solve_pcg(graph, &preconditioner, &rhs, pcg_options()).unwrap();
    assert!(result.residual_norm() <= result.tolerance());
    assert!(result.backward_error().is_finite());
    assert_vectors_close(result.solution(), &target, 2.0e-7);
}

#[test]
fn every_simple_graph_through_five_vertices_solves_known_targets() {
    for vertex_count in 1..=5 {
        let possible_edges: Vec<(usize, usize)> = (0..vertex_count)
            .flat_map(|left| ((left + 1)..vertex_count).map(move |right| (left, right)))
            .collect();
        for mask in 0_u64..(1_u64 << possible_edges.len()) {
            let edges = possible_edges
                .iter()
                .enumerate()
                .filter(|(index, _)| (mask & (1_u64 << index)) != 0)
                .map(|(_, &(left, right))| (left, right, 1.0));
            let graph = Laplacian::from_edges(vertex_count, edges).unwrap();
            assert_laplacian_solution(&graph);
        }
    }
}

#[test]
fn deterministic_random_weighted_graphs_solve_known_targets() {
    let mut rng = Lcg::new(0x5eed_cafe_1234_5678);
    for case in 0..120 {
        let vertex_count = 2 + rng.usize(14);
        let mut edges = Vec::new();
        for left in 0..vertex_count {
            for right in (left + 1)..vertex_count {
                if rng.unit() < 0.20 + 0.03 * ((case % 5) as f64) {
                    let exponent = rng.usize(9) as i32 - 4;
                    let weight = 10_f64.powi(exponent) * (0.25 + rng.unit());
                    edges.push((left, right, weight));
                    if rng.unit() < 0.25 {
                        edges.push((right, left, 0.4 * weight));
                        edges.push((left, right, 0.6 * weight));
                    }
                }
            }
        }
        let graph = Laplacian::from_edges(vertex_count, edges).unwrap();
        assert_laplacian_solution(&graph);
    }
}

#[test]
fn deterministic_random_sddm_systems_solve_known_targets() {
    let mut rng = Lcg::new(0x9e37_79b9_7f4a_7c15);
    for _case in 0..120 {
        let dimension = 1 + rng.usize(10);
        let mut off_diagonal = Vec::new();
        let mut diagonal = vec![0.0; dimension];
        for left in 0..dimension {
            for right in (left + 1)..dimension {
                if rng.unit() < 0.35 {
                    let exponent = rng.usize(7) as i32 - 3;
                    let weight = 10_f64.powi(exponent) * (0.25 + rng.unit());
                    off_diagonal.push((left, right, -weight));
                    diagonal[left] += weight;
                    diagonal[right] += weight;
                }
            }
        }
        for value in &mut diagonal {
            *value += 0.1 + 1.9 * rng.unit();
        }
        let matrix = SddmMatrix::from_parts(
            diagonal,
            off_diagonal,
            ValidationOptions::default(),
        )
        .unwrap();
        let target: Vec<f64> = (0..dimension)
            .map(|index| {
                let x = index as f64 + 1.0;
                (0.37 * x).cos() - 0.11 * x
            })
            .collect();
        let rhs = matrix.matvec(&target).unwrap();
        let solver = SddmSolver::from_matrix(
            &matrix,
            forced_multilevel_options(),
            ValidationOptions::default(),
        )
        .unwrap();
        let result = solver.solve(&rhs, pcg_options()).unwrap();
        assert!(result.residual_norm() <= result.tolerance());
        assert_vectors_close(result.solution(), &target, 3.0e-7);
    }
}
''')
