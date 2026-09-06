//! Component-grounded, static-degree-ordered LDL^T terminal solver.

use crate::{CmgError, Components, Laplacian, ValidationOptions};

#[derive(Debug, Clone, PartialEq)]
enum LowerFactor {
    Packed {
        values: Vec<f64>,
    },
    Sparse {
        row_offsets: Vec<usize>,
        columns: Vec<u32>,
        row_values: Vec<f64>,
        column_offsets: Vec<usize>,
        rows: Vec<u32>,
        column_values: Vec<f64>,
    },
}

impl LowerFactor {
    fn from_dense(lower: &[Vec<f64>]) -> Self {
        let dimension = lower.len();
        let packed_slots = dimension.saturating_mul(dimension.saturating_sub(1)) / 2;
        let strict_nonzeros = lower
            .iter()
            .enumerate()
            .map(|(row, values)| values[..row].iter().filter(|value| **value != 0.0).count())
            .sum::<usize>();

        let packed_bytes = packed_slots.saturating_mul(core::mem::size_of::<f64>());
        let sparse_bytes = strict_nonzeros
            .saturating_mul(2 * core::mem::size_of::<u32>() + 2 * core::mem::size_of::<f64>())
            .saturating_add(2 * (dimension + 1).saturating_mul(core::mem::size_of::<usize>()));

        if dimension <= u32::MAX as usize && sparse_bytes < packed_bytes {
            let mut row_offsets = Vec::with_capacity(dimension + 1);
            let mut columns = Vec::with_capacity(strict_nonzeros);
            let mut row_values = Vec::with_capacity(strict_nonzeros);
            row_offsets.push(0);
            for (row, values) in lower.iter().enumerate() {
                for (column, value) in values[..row].iter().copied().enumerate() {
                    if value != 0.0 {
                        columns.push(column as u32);
                        row_values.push(value);
                    }
                }
                row_offsets.push(columns.len());
            }

            Self::from_sparse_rows(row_offsets, columns, row_values)
        } else {
            let mut values = Vec::with_capacity(packed_slots);
            for (row, dense_row) in lower.iter().enumerate() {
                values.extend_from_slice(&dense_row[..row]);
            }
            Self::Packed { values }
        }
    }

    fn from_sparse_rows(row_offsets: Vec<usize>, columns: Vec<u32>, row_values: Vec<f64>) -> Self {
        let dimension = row_offsets.len() - 1;
        let strict_nonzeros = columns.len();
        let mut column_counts = vec![0_usize; dimension];
        for &column in &columns {
            column_counts[column as usize] += 1;
        }
        let mut column_offsets = Vec::with_capacity(dimension + 1);
        column_offsets.push(0);
        for count in column_counts {
            column_offsets.push(column_offsets.last().copied().unwrap_or(0) + count);
        }
        let mut next = column_offsets[..dimension].to_vec();
        let mut rows = vec![0_u32; strict_nonzeros];
        let mut column_values = vec![0.0; strict_nonzeros];
        for row in 0..dimension {
            for index in row_offsets[row]..row_offsets[row + 1] {
                let column = columns[index] as usize;
                let destination = next[column];
                rows[destination] = row as u32;
                column_values[destination] = row_values[index];
                next[column] += 1;
            }
        }

        Self::Sparse {
            row_offsets,
            columns,
            row_values,
            column_offsets,
            rows,
            column_values,
        }
    }

    fn forward_correction(&self, row: usize, forward: &[f64]) -> f64 {
        match self {
            Self::Packed { values } => {
                let start = row.saturating_mul(row.saturating_sub(1)) / 2;
                values[start..start + row]
                    .iter()
                    .zip(&forward[..row])
                    .map(|(lower_value, previous)| lower_value * previous)
                    .sum()
            }
            Self::Sparse {
                row_offsets,
                columns,
                row_values,
                ..
            } => (row_offsets[row]..row_offsets[row + 1])
                .map(|index| row_values[index] * forward[columns[index] as usize])
                .sum(),
        }
    }

    fn backward_correction(&self, row: usize, solution: &[f64]) -> f64 {
        match self {
            Self::Packed { values } => {
                // Consecutive packed rows differ in length by one. Advance the
                // column position instead of multiplying triangular indices
                // for every coefficient; retain the reference summation order.
                let mut index = row.saturating_mul(row.saturating_add(1)) / 2 + row;
                solution
                    .iter()
                    .enumerate()
                    .skip(row + 1)
                    .map(|(later, &value)| {
                        let product = values[index] * value;
                        index += later;
                        product
                    })
                    .sum()
            }
            Self::Sparse {
                column_offsets,
                rows,
                column_values,
                ..
            } => (column_offsets[row]..column_offsets[row + 1])
                .map(|index| column_values[index] * solution[rows[index] as usize])
                .sum(),
        }
    }

    fn byte_len(&self) -> usize {
        match self {
            Self::Packed { values } => values.len().saturating_mul(core::mem::size_of::<f64>()),
            Self::Sparse {
                row_offsets,
                columns,
                row_values,
                column_offsets,
                rows,
                column_values,
            } => row_offsets
                .len()
                .saturating_add(column_offsets.len())
                .saturating_mul(core::mem::size_of::<usize>())
                .saturating_add(
                    columns
                        .len()
                        .saturating_add(rows.len())
                        .saturating_mul(core::mem::size_of::<u32>()),
                )
                .saturating_add(
                    row_values
                        .len()
                        .saturating_add(column_values.len())
                        .saturating_mul(core::mem::size_of::<f64>()),
                ),
        }
    }
}

/// A deterministic direct solver for a graph Laplacian on its quotient space.
///
/// One highest-index vertex is grounded in each connected component. The
/// remaining grounded matrix is ordered by static row nonzero count and then by
/// original vertex index, matching the simple degree-ordering principle used by
/// the upstream terminal factorization.
#[derive(Debug, Clone, PartialEq)]
pub struct GroundedLdl {
    vertex_count: usize,
    components: Components,
    anchors: Vec<usize>,
    permutation: Vec<usize>,
    lower: LowerFactor,
    diagonal: Vec<f64>,
    factor_nonzeros: usize,
}

impl GroundedLdl {
    /// Factor the graph Laplacian after grounding one vertex per component.
    pub fn factor(graph: &Laplacian) -> Result<Self, CmgError> {
        Self::factor_impl::<false>(graph)
    }

    /// Factor disconnected blocks independently, preserving each block's
    /// highest-index anchor and static degree order.
    ///
    /// This experimental builder groups the exposed permutation by component.
    /// Ordered sparse updates skip zero factor entries, including for a single
    /// connected block. Retained factors use shared packed or sparse buffers,
    /// without one solver allocation per component.
    #[cfg(feature = "experimental-components")]
    pub fn factor_by_component(graph: &Laplacian) -> Result<Self, CmgError> {
        Self::factor_impl::<true>(graph)
    }

    fn factor_impl<const BLOCKS: bool>(graph: &Laplacian) -> Result<Self, CmgError> {
        let vertex_count = graph.vertex_count();
        let components = Components::from_laplacian(graph);

        let mut anchors = vec![0; components.count()];
        for (vertex, &component) in components.labels().iter().enumerate() {
            anchors[component] = vertex;
        }
        let mut is_anchor = vec![false; vertex_count];
        for &anchor in &anchors {
            is_anchor[anchor] = true;
        }

        let active_vertices: Vec<usize> = (0..vertex_count)
            .filter(|vertex| !is_anchor[*vertex])
            .collect();
        let mut pattern_nonzeros = vec![0_usize; vertex_count];
        for &vertex in &active_vertices {
            // Every active grounded row retains its positive diagonal.
            pattern_nonzeros[vertex] = 1;
        }
        for edge in graph.edges() {
            if !is_anchor[edge.u()] && !is_anchor[edge.v()] {
                pattern_nonzeros[edge.u()] += 1;
                pattern_nonzeros[edge.v()] += 1;
            }
        }

        let mut permutation = active_vertices;
        #[cfg(feature = "experimental-components")]
        if BLOCKS {
            permutation.sort_by_key(|&vertex| {
                (
                    components.labels()[vertex],
                    pattern_nonzeros[vertex],
                    vertex,
                )
            });
            drop(is_anchor);
            drop(pattern_nonzeros);
            let (lower, diagonal, factor_nonzeros) =
                factor_components(graph, &components, &permutation)?;
            return Ok(Self {
                vertex_count,
                components,
                anchors,
                permutation,
                lower,
                diagonal,
                factor_nonzeros,
            });
        }
        permutation.sort_by_key(|&vertex| (pattern_nonzeros[vertex], vertex));
        let dimension = permutation.len();

        // Assemble the ordered grounded matrix directly. The previous path
        // first materialized the full graph matrix and then copied the active
        // permutation into this second dense buffer. Direct assembly removes
        // one vertex_count^2 allocation and its complete permutation scan.
        let mut matrix = vec![vec![0.0; dimension]; dimension];
        let mut factor_index = vec![usize::MAX; vertex_count];
        for (factor_vertex, &original_vertex) in permutation.iter().enumerate() {
            factor_index[original_vertex] = factor_vertex;
            matrix[factor_vertex][factor_vertex] = graph.diagonal()[original_vertex];
        }
        for edge in graph.edges() {
            let factor_u = factor_index[edge.u()];
            let factor_v = factor_index[edge.v()];
            if factor_u == usize::MAX || factor_v == usize::MAX {
                continue;
            }
            matrix[factor_u][factor_v] -= edge.weight();
            matrix[factor_v][factor_u] -= edge.weight();
        }

        let (dense_lower, diagonal) = factor_dense(&matrix, &permutation)?;

        let strict_nonzeros = dense_lower
            .iter()
            .enumerate()
            .map(|(row, values)| values[..row].iter().filter(|value| **value != 0.0).count())
            .sum::<usize>();
        let factor_nonzeros = dimension.saturating_add(strict_nonzeros);
        let lower = LowerFactor::from_dense(&dense_lower);

        Ok(Self {
            vertex_count,
            components,
            anchors,
            permutation,
            lower,
            diagonal,
            factor_nonzeros,
        })
    }

    /// Return the original graph dimension.
    #[must_use]
    pub const fn vertex_count(&self) -> usize {
        self.vertex_count
    }

    /// Return the grounded vertex in each component.
    #[must_use]
    pub fn anchors(&self) -> &[usize] {
        &self.anchors
    }

    /// Return factor-order positions as original graph vertex indices.
    #[must_use]
    pub fn permutation(&self) -> &[usize] {
        &self.permutation
    }

    /// Return the dimension of the grounded positive-definite system.
    #[must_use]
    pub fn active_dimension(&self) -> usize {
        self.permutation.len()
    }

    /// Return the number of nonzeros in the unit lower factor.
    ///
    /// This is the denominator used by the upstream repeat heuristic before a
    /// direct terminal level.
    #[must_use]
    pub const fn factor_nonzeros(&self) -> usize {
        self.factor_nonzeros
    }

    /// Return the principal heap bytes retained by the factorization.
    #[must_use]
    pub fn byte_len(&self) -> usize {
        self.anchors
            .len()
            .saturating_add(self.permutation.len())
            .saturating_mul(core::mem::size_of::<usize>())
            .saturating_add(
                self.diagonal
                    .len()
                    .saturating_mul(core::mem::size_of::<f64>()),
            )
            .saturating_add(self.lower.byte_len())
            .saturating_add(
                self.components
                    .labels()
                    .len()
                    .saturating_add(self.components.sizes().len())
                    .saturating_mul(core::mem::size_of::<usize>()),
            )
    }

    /// Solve a compatible Laplacian system using default validation tolerances.
    pub fn solve(&self, rhs: &[f64]) -> Result<Vec<f64>, CmgError> {
        self.solve_with_validation(rhs, ValidationOptions::default())
    }

    /// Solve a compatible Laplacian system with explicit validation tolerances.
    ///
    /// The returned gauge sets every component anchor to zero.
    pub fn solve_with_validation(
        &self,
        rhs: &[f64],
        options: ValidationOptions,
    ) -> Result<Vec<f64>, CmgError> {
        if rhs.len() != self.vertex_count {
            return Err(CmgError::dimension(
                "GroundedLdl::solve",
                self.vertex_count,
                rhs.len(),
            ));
        }
        self.components.validate_rhs(rhs, options)?;
        let mut solution = vec![0.0; self.vertex_count];
        let mut forward = vec![0.0; self.active_dimension()];
        let mut factor_solution = vec![0.0; self.active_dimension()];
        self.solve_into_compatible(rhs, &mut solution, &mut forward, &mut factor_solution)?;
        Ok(solution)
    }

    pub(crate) fn solve_into_compatible(
        &self,
        rhs: &[f64],
        solution: &mut [f64],
        forward: &mut [f64],
        factor_solution: &mut [f64],
    ) -> Result<(), CmgError> {
        if rhs.len() != self.vertex_count {
            return Err(CmgError::dimension(
                "GroundedLdl::solve_into rhs",
                self.vertex_count,
                rhs.len(),
            ));
        }
        if solution.len() != self.vertex_count {
            return Err(CmgError::dimension(
                "GroundedLdl::solve_into solution",
                self.vertex_count,
                solution.len(),
            ));
        }
        let dimension = self.active_dimension();
        if forward.len() != dimension {
            return Err(CmgError::dimension(
                "GroundedLdl::solve_into forward",
                dimension,
                forward.len(),
            ));
        }
        if factor_solution.len() != dimension {
            return Err(CmgError::dimension(
                "GroundedLdl::solve_into factor solution",
                dimension,
                factor_solution.len(),
            ));
        }

        for row in 0..dimension {
            forward[row] = rhs[self.permutation[row]] - self.lower.forward_correction(row, forward);
        }
        for (value, pivot) in forward.iter_mut().zip(&self.diagonal) {
            *value /= *pivot;
        }
        for row in (0..dimension).rev() {
            factor_solution[row] =
                forward[row] - self.lower.backward_correction(row, factor_solution);
        }

        solution.fill(0.0);
        for (factor_index, &vertex) in self.permutation.iter().enumerate() {
            solution[vertex] = factor_solution[factor_index];
        }
        Ok(())
    }
}

fn factor_dense(
    matrix: &[Vec<f64>],
    permutation: &[usize],
) -> Result<(Vec<Vec<f64>>, Vec<f64>), CmgError> {
    let dimension = permutation.len();
    let mut dense_lower = vec![vec![0.0; dimension]; dimension];
    let mut diagonal = vec![0.0; dimension];
    for (row, values) in dense_lower.iter_mut().enumerate() {
        values[row] = 1.0;
    }

    for column in 0..dimension {
        let mut pivot = matrix[column][column];
        for (previous, diagonal_value) in diagonal.iter().copied().enumerate().take(column) {
            let value = dense_lower[column][previous];
            pivot -= value * value * diagonal_value;
        }
        if !pivot.is_finite() || pivot <= 0.0 {
            return Err(CmgError::NonPositivePivot {
                vertex: permutation[column],
                value: pivot,
            });
        }
        diagonal[column] = pivot;

        for row in (column + 1)..dimension {
            let mut value = matrix[row][column];
            for (previous, diagonal_value) in diagonal.iter().copied().enumerate().take(column) {
                value -=
                    dense_lower[row][previous] * dense_lower[column][previous] * diagonal_value;
            }
            dense_lower[row][column] = value / pivot;
        }
    }

    Ok((dense_lower, diagonal))
}

#[cfg(feature = "experimental-components")]
fn factor_components(
    graph: &Laplacian,
    components: &Components,
    permutation: &[usize],
) -> Result<(LowerFactor, Vec<f64>, usize), CmgError> {
    let dimension = permutation.len();
    if dimension <= 1 {
        let diagonal = if let Some(&vertex) = permutation.first() {
            let pivot = graph.diagonal()[vertex];
            if !pivot.is_finite() || pivot <= 0.0 {
                return Err(CmgError::NonPositivePivot {
                    vertex,
                    value: pivot,
                });
            }
            vec![pivot]
        } else {
            Vec::new()
        };
        return Ok((
            LowerFactor::Packed { values: Vec::new() },
            diagonal,
            dimension,
        ));
    }
    let count = components.count();
    let mut offsets = Vec::with_capacity(count + 1);
    offsets.push(0);
    for &size in components.sizes() {
        offsets.push(offsets.last().unwrap() + size.saturating_sub(1));
    }
    let mut factor_index = vec![usize::MAX; graph.vertex_count()];
    for (index, &vertex) in permutation.iter().enumerate() {
        factor_index[vertex] = index;
    }
    // Group only grounded edges with flat counting-sort storage. Do not scan
    // every edge again for every component, or allocate a graph per block.
    let mut edge_offsets = vec![0usize; count + 1];
    for edge in graph.edges() {
        if factor_index[edge.u()] != usize::MAX && factor_index[edge.v()] != usize::MAX {
            edge_offsets[components.labels()[edge.u()] + 1] += 1;
        }
    }
    for c in 0..count {
        edge_offsets[c + 1] += edge_offsets[c];
    }
    let mut next = edge_offsets[..count].to_vec();
    let mut edge_indices = vec![0usize; edge_offsets[count]];
    for (index, edge) in graph.edges().iter().enumerate() {
        if factor_index[edge.u()] != usize::MAX && factor_index[edge.v()] != usize::MAX {
            let c = components.labels()[edge.u()];
            edge_indices[next[c]] = index;
            next[c] += 1;
        }
    }
    let mut factors = ComponentFactorRows {
        diagonal: Vec::with_capacity(dimension),
        row_offsets: Vec::with_capacity(dimension + 1),
        columns: Vec::new(),
        values: Vec::new(),
    };
    factors.row_offsets.push(0);
    for component in 0..count {
        let start = offsets[component];
        let end = offsets[component + 1];
        let size = end - start;
        if size == 0 {
            continue;
        }
        if size == 1 {
            let vertex = permutation[start];
            let pivot = graph.diagonal()[vertex];
            if !pivot.is_finite() || pivot <= 0.0 {
                return Err(CmgError::NonPositivePivot {
                    vertex,
                    value: pivot,
                });
            }
            factors.diagonal.push(pivot);
            factors.row_offsets.push(factors.columns.len());
            continue;
        }
        factors.append_block(
            graph,
            &permutation[start..end],
            &factor_index,
            &edge_indices[edge_offsets[component]..edge_offsets[component + 1]],
            start,
        )?;
    }
    let ComponentFactorRows {
        diagonal,
        row_offsets,
        mut columns,
        mut values,
    } = factors;
    let factor_nonzeros = dimension + values.len();
    // byte_len reports principal factor storage; discard growth slack before
    // these buffers become retained state (the legacy dense path sizes exactly).
    columns.shrink_to_fit();
    values.shrink_to_fit();
    let packed_slots = dimension.saturating_mul(dimension.saturating_sub(1)) / 2;
    let sparse_bytes = values
        .len()
        .saturating_mul(24)
        .saturating_add((dimension + 1).saturating_mul(2 * core::mem::size_of::<usize>()));
    let lower = if packed_slots.saturating_mul(8) <= sparse_bytes {
        let mut packed = vec![0.0; packed_slots];
        for row in 0..dimension {
            let start = row.saturating_mul(row.saturating_sub(1)) / 2;
            for index in row_offsets[row]..row_offsets[row + 1] {
                packed[start + columns[index] as usize] = values[index];
            }
        }
        LowerFactor::Packed { values: packed }
    } else {
        LowerFactor::from_sparse_rows(row_offsets, columns, values)
    };
    Ok((lower, diagonal, factor_nonzeros))
}

// Nonzero columns support ordered left-looking updates; row links visit prior
// columns in exactly the dense reference's increasing-column arithmetic order.
#[cfg(feature = "experimental-components")]
struct ComponentFactorRows {
    diagonal: Vec<f64>,
    row_offsets: Vec<usize>,
    columns: Vec<u32>,
    values: Vec<f64>,
}

#[cfg(feature = "experimental-components")]
impl ComponentFactorRows {
    fn append_block(
        &mut self,
        graph: &Laplacian,
        permutation: &[usize],
        factor_index: &[usize],
        edge_indices: &[usize],
        base: usize,
    ) -> Result<(), CmgError> {
        let dimension = permutation.len();
        let mut input_offsets = vec![0usize; dimension + 1];
        for &index in edge_indices {
            let edge = graph.edges()[index];
            let column = factor_index[edge.u()].min(factor_index[edge.v()]) - base;
            input_offsets[column + 1] += 1;
        }
        for column in 0..dimension {
            input_offsets[column + 1] += input_offsets[column];
        }
        let mut input_edges = vec![0usize; edge_indices.len()];
        {
            let mut next = input_offsets[..dimension].to_vec();
            for &index in edge_indices {
                let edge = graph.edges()[index];
                let column = factor_index[edge.u()].min(factor_index[edge.v()]) - base;
                input_edges[next[column]] = index;
                next[column] += 1;
            }
        }
        // The arithmetic scan reads only rows and values. Keep the row-link
        // bookkeeping in separate arrays so it does not occupy that scan's
        // cache lines.
        let mut entry_rows = Vec::with_capacity(edge_indices.len());
        let mut entry_columns = Vec::with_capacity(edge_indices.len());
        let mut entry_values = Vec::with_capacity(edge_indices.len());
        let mut next_in_row = Vec::with_capacity(edge_indices.len());
        let mut column_offsets = Vec::with_capacity(dimension + 1);
        column_offsets.push(0);
        let mut first = vec![usize::MAX; dimension];
        let mut last = vec![usize::MAX; dimension];
        let mut work = vec![0.0; dimension];
        let mut diagonal = vec![0.0; dimension];
        for column in 0..dimension {
            work[column + 1..].fill(0.0);
            for &index in &input_edges[input_offsets[column]..input_offsets[column + 1]] {
                let edge = graph.edges()[index];
                let row = factor_index[edge.u()].max(factor_index[edge.v()]) - base;
                work[row] -= edge.weight();
            }
            let mut pivot = graph.diagonal()[permutation[column]];
            let mut link = first[column];
            while link != usize::MAX {
                let previous = entry_values[link];
                let k = entry_columns[link] as usize;
                pivot -= previous * previous * diagonal[k];
                // Column rows are increasing, so the suffix after this link
                // contains precisely the rows below the current pivot.
                let end = column_offsets[k + 1];
                for (&row, &value) in entry_rows[link + 1..end]
                    .iter()
                    .zip(&entry_values[link + 1..end])
                {
                    work[row as usize] -= value * previous * diagonal[k];
                }
                link = next_in_row[link];
            }
            if !pivot.is_finite() || pivot <= 0.0 {
                return Err(CmgError::NonPositivePivot {
                    vertex: permutation[column],
                    value: pivot,
                });
            }
            diagonal[column] = pivot;
            for row in column + 1..dimension {
                if work[row] == 0.0 {
                    continue;
                }
                let value = work[row] / pivot;
                if !value.is_finite() {
                    return Err(CmgError::NonFiniteMatrixValue {
                        row: permutation[row],
                        column: permutation[column],
                        value,
                    });
                }
                if value != 0.0 {
                    let index = entry_values.len();
                    entry_rows.push(row as u32);
                    entry_columns.push(column as u32);
                    entry_values.push(value);
                    next_in_row.push(usize::MAX);
                    if last[row] == usize::MAX {
                        first[row] = index;
                    } else {
                        next_in_row[last[row]] = index;
                    }
                    last[row] = index;
                }
            }
            column_offsets.push(entry_values.len());
        }
        self.diagonal.extend(diagonal);
        for mut link in first {
            while link != usize::MAX {
                self.columns
                    .push((base + entry_columns[link] as usize) as u32);
                self.values.push(entry_values[link]);
                link = next_in_row[link];
            }
            self.row_offsets.push(self.columns.len());
        }
        Ok(())
    }
}
