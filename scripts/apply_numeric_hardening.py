from pathlib import Path

ROOT = Path('.')


def replace_once(path: str, old: str, new: str) -> None:
    p = ROOT / path
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{path}: expected one match, found {count}: {old[:80]!r}')
    p.write_text(text.replace(old, new, 1))


# error.rs: make non-finite derived arithmetic a first-class typed error.
replace_once(
    'src/error.rs',
    '''    NonFiniteMatrixValue {
        /// Row index.
        row: usize,
        /// Column index.
        column: usize,
        /// Invalid value.
        value: f64,
    },
    /// A matrix expected to be symmetric was not symmetric within tolerance.
''',
    '''    NonFiniteMatrixValue {
        /// Row index.
        row: usize,
        /// Column index.
        column: usize,
        /// Invalid value.
        value: f64,
    },
    /// A derived numerical quantity overflowed or became non-finite.
    NonFiniteDerivedValue {
        /// Operation that produced the value.
        context: &'static str,
        /// Non-finite result.
        value: f64,
    },
    /// A matrix expected to be symmetric was not symmetric within tolerance.
''',
)
replace_once(
    'src/error.rs',
    '''            Self::NonFiniteMatrixValue {
                row,
                column,
                value,
            } => write!(
                formatter,
                "matrix entry ({row}, {column}) is not finite: {value}"
            ),
            Self::NotSymmetric {
''',
    '''            Self::NonFiniteMatrixValue {
                row,
                column,
                value,
            } => write!(
                formatter,
                "matrix entry ({row}, {column}) is not finite: {value}"
            ),
            Self::NonFiniteDerivedValue { context, value } => {
                write!(formatter, "{context} produced a non-finite value: {value}")
            }
            Self::NotSymmetric {
''',
)

# graph.rs: reject overflow in degrees, matvecs, and energies.
replace_once(
    'src/graph.rs',
    '''        let mut diagonal = vec![0.0; vertex_count];
        for edge in &canonical {
            diagonal[edge.u] += edge.weight;
            diagonal[edge.v] += edge.weight;
        }
''',
    '''        let mut diagonal = vec![0.0; vertex_count];
        for edge in &canonical {
            for vertex in [edge.u, edge.v] {
                let next = diagonal[vertex] + edge.weight;
                if !next.is_finite() {
                    return Err(CmgError::NonFiniteDerivedValue {
                        context: "Laplacian weighted degree accumulation",
                        value: next,
                    });
                }
                diagonal[vertex] = next;
            }
        }
''',
)
replace_once(
    'src/graph.rs',
    '''        let diagonal_nnz = self
            .diagonal
            .iter()
            .filter(|degree| **degree != 0.0)
            .count();
        diagonal_nnz + 2 * self.edges.len()
''',
    '''        let diagonal_nnz = self
            .diagonal
            .iter()
            .filter(|degree| **degree != 0.0)
            .count();
        diagonal_nnz.saturating_add(self.edges.len().saturating_mul(2))
''',
)
replace_once(
    'src/graph.rs',
    '''        output.fill(0.0);
        for edge in &self.edges {
            let difference = edge.weight * (input[edge.u] - input[edge.v]);
            output[edge.u] += difference;
            output[edge.v] -= difference;
        }
        Ok(())
''',
    '''        output.fill(0.0);
        for edge in &self.edges {
            let endpoint_difference = input[edge.u] - input[edge.v];
            if !endpoint_difference.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "Laplacian endpoint difference",
                    value: endpoint_difference,
                });
            }
            let contribution = edge.weight * endpoint_difference;
            if !contribution.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "Laplacian matrix-vector edge contribution",
                    value: contribution,
                });
            }
            let next_u = output[edge.u] + contribution;
            let next_v = output[edge.v] - contribution;
            if !next_u.is_finite() || !next_v.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "Laplacian matrix-vector accumulation",
                    value: if !next_u.is_finite() { next_u } else { next_v },
                });
            }
            output[edge.u] = next_u;
            output[edge.v] = next_v;
        }
        Ok(())
''',
)
replace_once(
    'src/graph.rs',
    '''        Ok(compensated_sum(self.edges.iter().map(|edge| {
            let difference = input[edge.u] - input[edge.v];
            edge.weight * difference * difference
        })))
''',
    '''        let mut terms = Vec::with_capacity(self.edges.len());
        for edge in &self.edges {
            let difference = input[edge.u] - input[edge.v];
            let term = edge.weight * difference * difference;
            if !term.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "Laplacian energy term",
                    value: term,
                });
            }
            terms.push(term);
        }
        let energy = compensated_sum(terms);
        if !energy.is_finite() {
            return Err(CmgError::NonFiniteDerivedValue {
                context: "Laplacian energy accumulation",
                value: energy,
            });
        }
        Ok(energy)
''',
)

# components.rs: make component sums, tolerances, projections, and norms finite.
p = ROOT / 'src/components.rs'
s = p.read_text()
s = s.replace('let combined = combined_sums(values, &self.labels, self.count());', 'let combined = combined_sums(values, &self.labels, self.count())?;')
s = s.replace('let combined = combined_sums(rhs, &self.labels, self.count());', 'let combined = combined_sums(rhs, &self.labels, self.count())?;')
s = s.replace('let combined = combined_sums(&original, &self.labels, self.count());', 'let combined = combined_sums(&original, &self.labels, self.count())?;')
s = s.replace('.map(|(sum, correction, _)| sum + correction)', '.map(|(sum, _)| sum)')
s = s.replace('for (component, (sum, correction, scale)) in combined.into_iter().enumerate() {\n            let total = sum + correction;', 'for (component, (total, scale)) in combined.into_iter().enumerate() {')
s = s.replace('''            let tolerance = options.compatibility_tolerance * scale.max(1.0);
            if total.abs() > tolerance {
''', '''            let tolerance = options.compatibility_tolerance * scale.max(1.0);
            if !tolerance.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "component compatibility tolerance",
                    value: tolerance,
                });
            }
            if total.abs() > tolerance {
''')
s = s.replace('''            let projection_norm = stable_euclidean_norm(&corrections);
''', '''            let projection_norm = stable_euclidean_norm(&corrections)?;
''')
old = '''fn subtract_component_means(
    values: &mut [f64],
    labels: &[usize],
    totals: &[f64],
    sizes: &[usize],
) {
    let means: Vec<f64> = totals
        .iter()
        .zip(sizes)
        .map(|(sum, size)| *sum / *size as f64)
        .collect();
    for (value, label) in values.iter_mut().zip(labels) {
        *value -= means[*label];
    }
}

fn combined_sums(
    values: &[f64],
    labels: &[usize],
    component_count: usize,
) -> Vec<(f64, f64, f64)> {
    let mut combined = vec![(0.0, 0.0, 0.0); component_count];
    for (value, label) in values.iter().zip(labels) {
        let (sum, correction, scale) = &mut combined[*label];
        neumaier_add(sum, correction, *value);
        *scale += value.abs();
    }
    combined
}
'''
new = '''fn subtract_component_means(
    values: &mut [f64],
    labels: &[usize],
    totals: &[f64],
    sizes: &[usize],
) -> Result<(), CmgError> {
    let means: Vec<f64> = totals
        .iter()
        .zip(sizes)
        .map(|(sum, size)| *sum / *size as f64)
        .collect();
    for (value, label) in values.iter_mut().zip(labels) {
        let corrected = *value - means[*label];
        if !corrected.is_finite() {
            return Err(CmgError::NonFiniteDerivedValue {
                context: "component mean subtraction",
                value: corrected,
            });
        }
        *value = corrected;
    }
    Ok(())
}

fn combined_sums(
    values: &[f64],
    labels: &[usize],
    component_count: usize,
) -> Result<Vec<(f64, f64)>, CmgError> {
    let mut combined = vec![(0.0, 0.0, 0.0); component_count];
    for (value, label) in values.iter().zip(labels) {
        let (sum, correction, scale) = &mut combined[*label];
        neumaier_add(sum, correction, *value);
        *scale += value.abs();
    }
    combined
        .into_iter()
        .map(|(sum, correction, scale)| {
            let total = sum + correction;
            if !total.is_finite() || !scale.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "component sum accumulation",
                    value: if !total.is_finite() { total } else { scale },
                });
            }
            Ok((total, scale))
        })
        .collect()
}
'''
if old not in s:
    raise RuntimeError('components.rs: combined/subtract block not found')
s = s.replace(old, new, 1)
s = s.replace('subtract_component_means(values, &self.labels, &totals, &self.sizes);', 'subtract_component_means(values, &self.labels, &totals, &self.sizes)?;')
old = '''fn stable_euclidean_norm(values: &[f64]) -> f64 {
    let scale = values.iter().map(|value| value.abs()).fold(0.0, f64::max);
    if scale == 0.0 {
        0.0
    } else {
        scale
            * values
                .iter()
                .map(|value| {
                    let scaled = *value / scale;
                    scaled * scaled
                })
                .sum::<f64>()
                .sqrt()
    }
}
'''
new = '''fn stable_euclidean_norm(values: &[f64]) -> Result<f64, CmgError> {
    if values.iter().any(|value| !value.is_finite()) {
        return Err(CmgError::NonFiniteDerivedValue {
            context: "component projection vector",
            value: f64::NAN,
        });
    }
    let scale = values.iter().map(|value| value.abs()).fold(0.0, f64::max);
    let norm = if scale == 0.0 {
        0.0
    } else {
        scale
            * values
                .iter()
                .map(|value| {
                    let scaled = *value / scale;
                    scaled * scaled
                })
                .sum::<f64>()
                .sqrt()
    };
    if !norm.is_finite() {
        return Err(CmgError::NonFiniteDerivedValue {
            context: "component projection norm",
            value: norm,
        });
    }
    Ok(norm)
}
'''
if old not in s:
    raise RuntimeError('components.rs: norm block not found')
s = s.replace(old, new, 1)
p.write_text(s)

# coarsen.rs: reject empty aggregate IDs and arithmetic overflow.
replace_once(
    'src/coarsen.rs',
    '''        for &label in &labels {
            if label >= aggregate_count {
                return Err(CmgError::InvalidAggregateLabel {
                    label,
                    aggregate_count,
                });
            }
            sizes[label] += 1;
        }
        Ok(Self {
''',
    '''        for &label in &labels {
            if label >= aggregate_count {
                return Err(CmgError::InvalidAggregateLabel {
                    label,
                    aggregate_count,
                });
            }
            sizes[label] += 1;
        }
        if sizes.contains(&0) {
            return Err(CmgError::InvalidHierarchy {
                context: "aggregation contains an empty coarse label",
            });
        }
        Ok(Self {
''',
)
replace_once(
    'src/coarsen.rs',
    '''        for (value, label) in fine.iter().zip(&self.labels) {
            coarse[*label] += *value;
        }
        Ok(())
''',
    '''        for (value, label) in fine.iter().zip(&self.labels) {
            let next = coarse[*label] + *value;
            if !next.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "aggregation restriction",
                    value: next,
                });
            }
            coarse[*label] = next;
        }
        Ok(())
''',
)
replace_once(
    'src/coarsen.rs',
    '''        for (value, label) in fine.iter_mut().zip(&self.labels) {
            *value = coarse[*label];
        }
        Ok(())
''',
    '''        for (value, label) in fine.iter_mut().zip(&self.labels) {
            let coarse_value = coarse[*label];
            if !coarse_value.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "aggregation prolongation",
                    value: coarse_value,
                });
            }
            *value = coarse_value;
        }
        Ok(())
''',
)
replace_once(
    'src/coarsen.rs',
    '''        for (value, label) in fine.iter_mut().zip(&self.labels) {
            *value += coarse[*label];
        }
        Ok(())
''',
    '''        for (value, label) in fine.iter_mut().zip(&self.labels) {
            let next = *value + coarse[*label];
            if !next.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "aggregation additive prolongation",
                    value: next,
                });
            }
            *value = next;
        }
        Ok(())
''',
)

# sddm.rs: reject non-finite aggregate sums, dense positive entries, and matvec overflow.
replace_once(
    'src/sddm.rs',
    '''            let value = compensated_sum(entries[start..cursor].iter().map(|entry| entry.2));
            if value > 0.0 {
''',
    '''            let value = compensated_sum(entries[start..cursor].iter().map(|entry| entry.2));
            if !value.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "SDDM duplicate off-diagonal accumulation",
                    value,
                });
            }
            if value > 0.0 {
''',
)
replace_once(
    'src/sddm.rs',
    '''        for (u, v, value) in &canonical {
            let weight = -*value;
            row_off_diagonal_abs[*u] += weight;
            row_off_diagonal_abs[*v] += weight;
        }
''',
    '''        for (u, v, value) in &canonical {
            let weight = -*value;
            for row in [*u, *v] {
                let next = row_off_diagonal_abs[row] + weight;
                if !next.is_finite() {
                    return Err(CmgError::NonFiniteDerivedValue {
                        context: "SDDM absolute off-diagonal row sum",
                        value: next,
                    });
                }
                row_off_diagonal_abs[row] = next;
            }
        }
''',
)
replace_once(
    'src/sddm.rs',
    '''            for (column, &forward) in values.iter().enumerate().skip(row + 1) {
                let reverse = matrix[column][row];
                if !close(forward, reverse, options.symmetry_tolerance) {
''',
    '''            for (column, &forward) in values.iter().enumerate().skip(row + 1) {
                let reverse = matrix[column][row];
                if forward > 0.0 {
                    return Err(CmgError::PositiveOffDiagonal {
                        row,
                        column,
                        value: forward,
                    });
                }
                if reverse > 0.0 {
                    return Err(CmgError::PositiveOffDiagonal {
                        row: column,
                        column: row,
                        value: reverse,
                    });
                }
                if !close(forward, reverse, options.symmetry_tolerance) {
''',
)
replace_once(
    'src/sddm.rs',
    '''        for ((output_value, diagonal_value), input_value) in
            output.iter_mut().zip(&self.diagonal).zip(input)
        {
            *output_value = *diagonal_value * *input_value;
        }
        for (u, v, value) in &self.off_diagonal {
            output[*u] += *value * input[*v];
            output[*v] += *value * input[*u];
        }
        Ok(())
''',
    '''        for ((output_value, diagonal_value), input_value) in
            output.iter_mut().zip(&self.diagonal).zip(input)
        {
            let product = *diagonal_value * *input_value;
            if !product.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "SDDM diagonal matrix-vector product",
                    value: product,
                });
            }
            *output_value = product;
        }
        for (u, v, value) in &self.off_diagonal {
            let contribution_u = *value * input[*v];
            let contribution_v = *value * input[*u];
            let next_u = output[*u] + contribution_u;
            let next_v = output[*v] + contribution_v;
            if !next_u.is_finite() || !next_v.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "SDDM matrix-vector accumulation",
                    value: if !next_u.is_finite() { next_u } else { next_v },
                });
            }
            output[*u] = next_u;
            output[*v] = next_v;
        }
        Ok(())
''',
)
replace_once(
    'src/sddm.rs',
    '''        let excess: Vec<f64> = self
            .diagonal
            .iter()
            .zip(&self.row_off_diagonal_abs)
            .map(|(diagonal, off_sum)| (*diagonal - *off_sum).max(0.0))
            .collect();
''',
    '''        let mut excess = Vec::with_capacity(n);
        for (diagonal, off_sum) in self.diagonal.iter().zip(&self.row_off_diagonal_abs) {
            let value = *diagonal - *off_sum;
            if !value.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "SDDM diagonal-dominance excess",
                    value,
                });
            }
            excess.push(value.max(0.0));
        }
''',
)
replace_once(
    'src/sddm.rs',
    '''        let mut lifted = rhs.to_vec();
        if self.augmented {
            lifted.push(-compensated_sum(rhs.iter().copied()));
        }
        Ok(lifted)
''',
    '''        let mut lifted = rhs.to_vec();
        if self.augmented {
            let sum = compensated_sum(rhs.iter().copied());
            if !sum.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "SDDM lifted right-hand-side sum",
                    value: sum,
                });
            }
            lifted.push(-sum);
        }
        Ok(lifted)
''',
)
replace_once(
    'src/sddm.rs',
    '''        if self.augmented {
            let anchor = solution[self.original_dimension];
            Ok(solution[..self.original_dimension]
                .iter()
                .map(|value| *value - anchor)
                .collect())
        } else {
            Ok(solution.to_vec())
        }
''',
    '''        if self.augmented {
            let anchor = solution[self.original_dimension];
            solution[..self.original_dimension]
                .iter()
                .map(|value| {
                    let extracted = *value - anchor;
                    if !extracted.is_finite() {
                        return Err(CmgError::NonFiniteDerivedValue {
                            context: "SDDM solution extraction",
                            value: extracted,
                        });
                    }
                    Ok(extracted)
                })
                .collect()
        } else {
            if solution.iter().any(|value| !value.is_finite()) {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "SDDM solution extraction",
                    value: f64::NAN,
                });
            }
            Ok(solution.to_vec())
        }
''',
)

# ldl.rs: use compensated products and reject non-finite factors/solutions.
replace_once(
    'src/ldl.rs',
    '''use crate::{CmgError, Components, Laplacian, ValidationOptions};
''',
    '''use crate::graph::compensated_sum;
use crate::{CmgError, Components, Laplacian, ValidationOptions};
''',
)
replace_once(
    'src/ldl.rs',
    '''        for column in 0..dimension {
            let mut pivot = matrix[column][column];
            for previous in 0..column {
                let value = lower[column][previous];
                pivot -= value * value * diagonal[previous];
            }
            if !pivot.is_finite() || pivot <= 0.0 {
''',
    '''        for column in 0..dimension {
            let correction = compensated_sum((0..column).map(|previous| {
                let value = lower[column][previous];
                value * value * diagonal[previous]
            }));
            let pivot = matrix[column][column] - correction;
            if !pivot.is_finite() || pivot <= 0.0 {
''',
)
replace_once(
    'src/ldl.rs',
    '''            for row in (column + 1)..dimension {
                let mut value = matrix[row][column];
                for previous in 0..column {
                    value -= lower[row][previous] * lower[column][previous] * diagonal[previous];
                }
                lower[row][column] = value / pivot;
            }
''',
    '''            for row in (column + 1)..dimension {
                let correction = compensated_sum((0..column).map(|previous| {
                    lower[row][previous] * lower[column][previous] * diagonal[previous]
                }));
                let factor_value = (matrix[row][column] - correction) / pivot;
                if !factor_value.is_finite() {
                    return Err(CmgError::NonFiniteDerivedValue {
                        context: "grounded LDL lower-factor entry",
                        value: factor_value,
                    });
                }
                lower[row][column] = factor_value;
            }
''',
)
replace_once(
    'src/ldl.rs',
    '''        for row in 0..dimension {
            let correction: f64 = self.lower[row][..row]
                .iter()
                .zip(&forward[..row])
                .map(|(lower_value, previous)| lower_value * previous)
                .sum();
            forward[row] = rhs[self.permutation[row]] - correction;
        }
        for (value, pivot) in forward.iter_mut().zip(&self.diagonal) {
            *value /= *pivot;
        }
        for row in (0..dimension).rev() {
            let correction: f64 = self.lower[(row + 1)..]
                .iter()
                .zip(&factor_solution[(row + 1)..])
                .map(|(lower_row, later_solution)| lower_row[row] * later_solution)
                .sum();
            factor_solution[row] = forward[row] - correction;
        }
''',
    '''        for row in 0..dimension {
            let correction = compensated_sum(
                self.lower[row][..row]
                    .iter()
                    .zip(&forward[..row])
                    .map(|(lower_value, previous)| lower_value * previous),
            );
            let value = rhs[self.permutation[row]] - correction;
            if !value.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "grounded LDL forward substitution",
                    value,
                });
            }
            forward[row] = value;
        }
        for (value, pivot) in forward.iter_mut().zip(&self.diagonal) {
            *value /= *pivot;
            if !value.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "grounded LDL diagonal solve",
                    value: *value,
                });
            }
        }
        for row in (0..dimension).rev() {
            let correction = compensated_sum(
                self.lower[(row + 1)..]
                    .iter()
                    .zip(&factor_solution[(row + 1)..])
                    .map(|(lower_row, later_solution)| lower_row[row] * later_solution),
            );
            let value = forward[row] - correction;
            if !value.is_finite() {
                return Err(CmgError::NonFiniteDerivedValue {
                    context: "grounded LDL backward substitution",
                    value,
                });
            }
            factor_solution[row] = value;
        }
''',
)

# pcg.rs: replace the core driver and diagnostic helpers with finite-safe versions.
p = ROOT / 'src/pcg.rs'
s = p.read_text()
start = s.index('pub fn solve_pcg_with_workspace(')
end = s.index('\n/// Solve a batch of right-hand sides sequentially', start)
new_driver = r'''pub fn solve_pcg_with_workspace(
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    rhs: &[f64],
    options: PcgOptions,
    workspace: &mut PcgWorkspace,
) -> Result<PcgResult, CmgError> {
    let options = options.validate()?;
    let n = graph.vertex_count();
    if rhs.len() != n {
        return Err(CmgError::dimension("solve_pcg rhs", n, rhs.len()));
    }
    if preconditioner.hierarchy().levels()[0].graph() != graph {
        return Err(CmgError::InvalidHierarchy {
            context: "preconditioner fine graph does not match PCG operator",
        });
    }
    workspace.validate(preconditioner)?;

    let original_rhs_norm = finite_pcg(
        euclidean_norm(rhs),
        0,
        "original right-hand-side norm",
    )?;
    let projection_norm = workspace.components.project_rhs_in_place(
        &mut workspace.projected_rhs,
        options.validation,
    )?;
    workspace.projected_rhs.copy_from_slice(rhs);
    let projection_norm = workspace.components.project_rhs_in_place(
        &mut workspace.projected_rhs,
        options.validation,
    )?;
    let projected_rhs_norm = finite_pcg(
        euclidean_norm(&workspace.projected_rhs),
        0,
        "projected right-hand-side norm",
    )?;
    let operator_bound = finite_pcg(graph.operator_norm_bound(), 0, "operator norm bound")?;
    let initial_tolerance = allowed_residual(
        options,
        original_rhs_norm,
        operator_bound,
        0.0,
        0,
    )?;

    workspace.solution.fill(0.0);
    workspace.residual.copy_from_slice(&workspace.projected_rhs);
    workspace.direction.fill(0.0);
    workspace.preconditioned.fill(0.0);
    workspace.matvec.fill(0.0);

    if projected_rhs_norm == 0.0 {
        if original_rhs_norm <= initial_tolerance {
            return make_result(
                workspace.solution.clone(),
                0,
                0,
                original_rhs_norm,
                original_rhs_norm,
                projection_norm,
                initial_tolerance,
                original_rhs_norm,
                operator_bound,
            );
        }
        return Err(CmgError::IncompatibleLaplacianRhs {
            component: 0,
            sum: original_rhs_norm,
            tolerance: initial_tolerance,
        });
    }

    if original_rhs_norm <= initial_tolerance {
        return make_result(
            workspace.solution.clone(),
            0,
            0,
            original_rhs_norm,
            original_rhs_norm,
            projection_norm,
            initial_tolerance,
            original_rhs_norm,
            operator_bound,
        );
    }

    preconditioner.apply_into_with_validation(
        &workspace.residual,
        &mut workspace.preconditioned,
        &mut workspace.preconditioner,
        options.validation,
    )?;
    workspace
        .direction
        .copy_from_slice(&workspace.preconditioned);
    let mut rho = dot_product(&workspace.residual, &workspace.preconditioned);
    validate_positive(rho, 0, "initial preconditioned residual product")?;
    let mut restarts = 0_usize;
    let mut last_tolerance = initial_tolerance;

    for iteration in 1..=options.max_iterations {
        graph.matvec_into(&workspace.direction, &mut workspace.matvec)?;
        let direction_curvature = dot_product(&workspace.direction, &workspace.matvec);
        validate_positive(direction_curvature, iteration, "search direction curvature")?;
        let alpha = rho / direction_curvature;
        if !alpha.is_finite() {
            return Err(CmgError::PcgBreakdown {
                iteration,
                quantity: "step length",
                value: alpha,
            });
        }

        for ((solution_value, residual_value), (direction_value, matrix_value)) in workspace
            .solution
            .iter_mut()
            .zip(&mut workspace.residual)
            .zip(workspace.direction.iter().zip(&workspace.matvec))
        {
            *solution_value += alpha * *direction_value;
            *residual_value -= alpha * *matrix_value;
        }

        let solution_norm = finite_pcg(
            euclidean_norm(&workspace.solution),
            iteration,
            "solution norm",
        )?;
        last_tolerance = allowed_residual(
            options,
            original_rhs_norm,
            operator_bound,
            solution_norm,
            iteration,
        )?;
        let recursive_residual_norm = finite_pcg(
            euclidean_norm(&workspace.residual),
            iteration,
            "recursive residual norm",
        )?;

        let periodic_recompute = iteration % options.residual_recompute_interval == 0;
        if recursive_residual_norm <= last_tolerance {
            let fresh_projected_norm = finite_pcg(
                recompute_residual(
                    graph,
                    &workspace.projected_rhs,
                    &workspace.solution,
                    &mut workspace.residual,
                    &mut workspace.matvec,
                )?,
                iteration,
                "fresh projected residual norm",
            )?;
            let original_residual_norm = finite_pcg(
                original_residual_norm(
                    graph,
                    rhs,
                    &workspace.solution,
                    &mut workspace.original_residual,
                    &mut workspace.matvec,
                )?,
                iteration,
                "fresh original residual norm",
            )?;
            if original_residual_norm <= last_tolerance {
                return make_result(
                    workspace.solution.clone(),
                    iteration,
                    restarts,
                    original_residual_norm,
                    projected_rhs_norm,
                    projection_norm,
                    last_tolerance,
                    original_rhs_norm,
                    operator_bound,
                );
            }
            if fresh_projected_norm == 0.0 {
                return Err(CmgError::ResidualVerificationFailed {
                    iteration,
                    residual_norm: original_residual_norm,
                    tolerance: last_tolerance,
                });
            }
            preconditioner.apply_into_with_validation(
                &workspace.residual,
                &mut workspace.preconditioned,
                &mut workspace.preconditioner,
                options.validation,
            )?;
            rho = dot_product(&workspace.residual, &workspace.preconditioned);
            validate_positive(rho, iteration, "restarted preconditioned residual product")?;
            workspace
                .direction
                .copy_from_slice(&workspace.preconditioned);
            restarts += 1;
            continue;
        }

        if periodic_recompute {
            let fresh_projected_norm = finite_pcg(
                recompute_residual(
                    graph,
                    &workspace.projected_rhs,
                    &workspace.solution,
                    &mut workspace.residual,
                    &mut workspace.matvec,
                )?,
                iteration,
                "periodic fresh projected residual norm",
            )?;
            let original_residual_norm = finite_pcg(
                original_residual_norm(
                    graph,
                    rhs,
                    &workspace.solution,
                    &mut workspace.original_residual,
                    &mut workspace.matvec,
                )?,
                iteration,
                "periodic fresh original residual norm",
            )?;
            if original_residual_norm <= last_tolerance {
                return make_result(
                    workspace.solution.clone(),
                    iteration,
                    restarts,
                    original_residual_norm,
                    projected_rhs_norm,
                    projection_norm,
                    last_tolerance,
                    original_rhs_norm,
                    operator_bound,
                );
            }
            if fresh_projected_norm == 0.0 {
                return Err(CmgError::ResidualVerificationFailed {
                    iteration,
                    residual_norm: original_residual_norm,
                    tolerance: last_tolerance,
                });
            }
            preconditioner.apply_into_with_validation(
                &workspace.residual,
                &mut workspace.preconditioned,
                &mut workspace.preconditioner,
                options.validation,
            )?;
            rho = dot_product(&workspace.residual, &workspace.preconditioned);
            validate_positive(rho, iteration, "periodic preconditioned residual product")?;
            workspace
                .direction
                .copy_from_slice(&workspace.preconditioned);
            restarts += 1;
            continue;
        }

        preconditioner.apply_into_with_validation(
            &workspace.residual,
            &mut workspace.preconditioned,
            &mut workspace.preconditioner,
            options.validation,
        )?;
        let new_rho = dot_product(&workspace.residual, &workspace.preconditioned);
        validate_positive(new_rho, iteration, "preconditioned residual product")?;
        let beta = new_rho / rho;
        if !beta.is_finite() || beta < 0.0 {
            return Err(CmgError::PcgBreakdown {
                iteration,
                quantity: "direction update coefficient",
                value: beta,
            });
        }
        for (direction_value, preconditioned_value) in workspace
            .direction
            .iter_mut()
            .zip(&workspace.preconditioned)
        {
            *direction_value = *preconditioned_value + beta * *direction_value;
        }
        rho = new_rho;
    }

    let _fresh_projected_norm = finite_pcg(
        recompute_residual(
            graph,
            &workspace.projected_rhs,
            &workspace.solution,
            &mut workspace.residual,
            &mut workspace.matvec,
        )?,
        options.max_iterations,
        "maximum-iteration projected residual norm",
    )?;
    let original_residual_norm = finite_pcg(
        original_residual_norm(
            graph,
            rhs,
            &workspace.solution,
            &mut workspace.original_residual,
            &mut workspace.matvec,
        )?,
        options.max_iterations,
        "maximum-iteration original residual norm",
    )?;
    let solution_norm = finite_pcg(
        euclidean_norm(&workspace.solution),
        options.max_iterations,
        "maximum-iteration solution norm",
    )?;
    last_tolerance = allowed_residual(
        options,
        original_rhs_norm,
        operator_bound,
        solution_norm,
        options.max_iterations,
    )?;
    if original_residual_norm <= last_tolerance {
        return make_result(
            workspace.solution.clone(),
            options.max_iterations,
            restarts,
            original_residual_norm,
            projected_rhs_norm,
            projection_norm,
            last_tolerance,
            original_rhs_norm,
            operator_bound,
        );
    }
    Err(CmgError::MaximumIterations {
        iterations: options.max_iterations,
        residual_norm: original_residual_norm,
        tolerance: last_tolerance,
    })
}
'''
new_driver = new_driver.replace('''    let projection_norm = workspace.components.project_rhs_in_place(
        &mut workspace.projected_rhs,
        options.validation,
    )?;
    workspace.projected_rhs.copy_from_slice(rhs);
''', '''    workspace.projected_rhs.copy_from_slice(rhs);
''', 1)
s = s[:start] + new_driver + s[end:]
helper_start = s.index('fn allowed_residual(')
new_helpers = r'''fn allowed_residual(
    options: PcgOptions,
    rhs_norm: f64,
    operator_bound: f64,
    solution_norm: f64,
    iteration: usize,
) -> Result<f64, CmgError> {
    let operator_solution = finite_pcg(
        operator_bound * solution_norm,
        iteration,
        "operator-solution norm bound",
    )?;
    let scale = finite_pcg(
        rhs_norm + operator_solution,
        iteration,
        "residual certificate scale",
    )?;
    finite_pcg(
        options.absolute_tolerance + options.relative_tolerance * scale,
        iteration,
        "residual tolerance",
    )
}

fn make_result(
    solution: Vec<f64>,
    iterations: usize,
    restarts: usize,
    residual_norm: f64,
    projected_rhs_norm: f64,
    projection_norm: f64,
    tolerance: f64,
    original_rhs_norm: f64,
    operator_bound: f64,
) -> Result<PcgResult, CmgError> {
    let relative_residual = if original_rhs_norm > 0.0 {
        residual_norm / original_rhs_norm
    } else {
        residual_norm
    };
    let solution_norm = finite_pcg(euclidean_norm(&solution), iterations, "result solution norm")?;
    let denominator = finite_pcg(
        original_rhs_norm + operator_bound * solution_norm,
        iterations,
        "result backward-error denominator",
    )?;
    let backward_error = if denominator > 0.0 {
        residual_norm / denominator
    } else {
        0.0
    };
    let relative_residual = finite_pcg(relative_residual, iterations, "relative residual")?;
    let backward_error = finite_pcg(backward_error, iterations, "backward error")?;
    Ok(PcgResult {
        solution,
        iterations,
        restarts,
        residual_norm,
        relative_residual,
        backward_error,
        projected_rhs_norm,
        projection_norm,
        tolerance,
    })
}

fn recompute_residual(
    graph: &Laplacian,
    rhs: &[f64],
    solution: &[f64],
    residual: &mut [f64],
    matvec: &mut [f64],
) -> Result<f64, CmgError> {
    graph.matvec_into(solution, matvec)?;
    for ((residual_value, rhs_value), matrix_value) in residual.iter_mut().zip(rhs).zip(matvec) {
        *residual_value = *rhs_value - *matrix_value;
    }
    Ok(euclidean_norm(residual))
}

fn original_residual_norm(
    graph: &Laplacian,
    rhs: &[f64],
    solution: &[f64],
    residual: &mut [f64],
    matvec: &mut [f64],
) -> Result<f64, CmgError> {
    graph.matvec_into(solution, matvec)?;
    for ((residual_value, rhs_value), matrix_value) in residual.iter_mut().zip(rhs).zip(matvec) {
        *residual_value = *rhs_value - *matrix_value;
    }
    Ok(euclidean_norm(residual))
}

fn finite_pcg(value: f64, iteration: usize, quantity: &'static str) -> Result<f64, CmgError> {
    if value.is_finite() {
        Ok(value)
    } else {
        Err(CmgError::PcgBreakdown {
            iteration,
            quantity,
            value,
        })
    }
}

fn validate_positive(
    value: f64,
    iteration: usize,
    quantity: &'static str,
) -> Result<(), CmgError> {
    if value.is_finite() && value > 0.0 {
        Ok(())
    } else {
        Err(CmgError::PcgBreakdown {
            iteration,
            quantity,
            value,
        })
    }
}

fn dot_product(left: &[f64], right: &[f64]) -> f64 {
    compensated_sum(left.iter().zip(right).map(|(l, r)| l * r))
}

fn euclidean_norm(values: &[f64]) -> f64 {
    if values.iter().any(|value| !value.is_finite()) {
        return f64::NAN;
    }
    let scale = values.iter().map(|value| value.abs()).fold(0.0, f64::max);
    if scale == 0.0 {
        0.0
    } else {
        scale
            * compensated_sum(values.iter().map(|value| {
                let scaled = *value / scale;
                scaled * scaled
            }))
            .sqrt()
    }
}
'''
s = s[:helper_start] + new_helpers
p.write_text(s)

# sddm_solver.rs: reject an infinite bound and non-finite input norms explicitly.
replace_once(
    'src/sddm_solver.rs',
    '''        let operator_norm_bound = 2.0 * matrix.diagonal().iter().copied().fold(0.0, f64::max);
        Ok(Self {
''',
    '''        let operator_norm_bound = 2.0 * matrix.diagonal().iter().copied().fold(0.0, f64::max);
        if !operator_norm_bound.is_finite() {
            return Err(CmgError::NonFiniteDerivedValue {
                context: "SDDM operator norm bound",
                value: operator_norm_bound,
            });
        }
        Ok(Self {
''',
)
replace_once(
    'src/sddm_solver.rs',
    '''        let rhs_norm = euclidean_norm(rhs);
        let mut solve_options = requested_options;
''',
    '''        let rhs_norm = euclidean_norm(rhs);
        if !rhs_norm.is_finite() {
            return Err(CmgError::PcgBreakdown {
                iteration: 0,
                quantity: "original SDDM right-hand-side norm",
                value: rhs_norm,
            });
        }
        let mut solve_options = requested_options;
''',
)
replace_once(
    'src/sddm_solver.rs',
    '''            let solution_norm = euclidean_norm(&solution);
            let residual_norm = euclidean_norm(&workspace.original_residual);
''',
    '''            let solution_norm = euclidean_norm(&solution);
            let residual_norm = euclidean_norm(&workspace.original_residual);
            if !solution_norm.is_finite() {
                return Err(CmgError::PcgBreakdown {
                    iteration: total_iterations,
                    quantity: "original SDDM solution norm",
                    value: solution_norm,
                });
            }
''',
)
replace_once(
    'src/sddm_solver.rs',
    '''fn euclidean_norm(values: &[f64]) -> f64 {
    let scale = values.iter().map(|value| value.abs()).fold(0.0, f64::max);
''',
    '''fn euclidean_norm(values: &[f64]) -> f64 {
    if values.iter().any(|value| !value.is_finite()) {
        return f64::NAN;
    }
    let scale = values.iter().map(|value| value.abs()).fold(0.0, f64::max);
''',
)

(ROOT / 'tests/nonfinite.rs').write_text(r'''use cmg::{
    Aggregation, CmgError, CmgOptions, CmgPreconditioner, Laplacian, PcgOptions, SddmMatrix,
    SddmSolver, ValidationOptions, solve_pcg,
};

#[test]
fn graph_rejects_degree_overflow() {
    let weight = 0.75 * f64::MAX;
    assert!(matches!(
        Laplacian::from_edges(3, [(0, 1, weight), (0, 2, weight)]),
        Err(CmgError::NonFiniteDerivedValue { .. })
    ));
}

#[test]
fn graph_matvec_rejects_edge_contribution_overflow() {
    let graph = Laplacian::from_edges(2, [(0, 1, 0.5 * f64::MAX)]).unwrap();
    assert!(matches!(
        graph.matvec(&[2.0, -2.0]),
        Err(CmgError::NonFiniteDerivedValue { .. })
    ));
}

#[test]
fn sddm_rejects_absolute_row_sum_overflow() {
    let weight = -0.75 * f64::MAX;
    assert!(matches!(
        SddmMatrix::from_parts(
            vec![f64::MAX, f64::MAX, f64::MAX],
            [(0, 1, weight), (0, 2, weight)],
            ValidationOptions::default(),
        ),
        Err(CmgError::NonFiniteDerivedValue { .. })
    ));
}

#[test]
fn pcg_rejects_infinite_operator_bound_instead_of_false_success() {
    let graph = Laplacian::from_edges(2, [(0, 1, 0.75 * f64::MAX)]).unwrap();
    let preconditioner = CmgPreconditioner::build(&graph, CmgOptions::default()).unwrap();
    assert!(matches!(
        solve_pcg(
            &graph,
            &preconditioner,
            &[1.0, -1.0],
            PcgOptions::default(),
        ),
        Err(CmgError::PcgBreakdown { .. })
    ));
}

#[test]
fn sddm_solver_rejects_infinite_operator_bound_at_build_time() {
    let matrix = SddmMatrix::from_parts(
        vec![f64::MAX],
        std::iter::empty(),
        ValidationOptions::default(),
    )
    .unwrap();
    assert!(matches!(
        SddmSolver::from_matrix(
            &matrix,
            CmgOptions::default(),
            ValidationOptions::default(),
        ),
        Err(CmgError::NonFiniteDerivedValue { .. })
    ));
}

#[test]
fn aggregation_rejects_unused_coarse_labels() {
    assert!(matches!(
        Aggregation::new(vec![0, 0], 2),
        Err(CmgError::InvalidHierarchy { .. })
    ));
}
''')
