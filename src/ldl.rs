//! Component-grounded, static-degree-ordered LDL^T terminal solver.

use crate::storage::{add, bytes, try_capacity, try_filled};
use crate::{CmgError, Components, Laplacian, ValidationOptions};

fn strict_slots(dimension: usize) -> Result<usize, CmgError> {
    dimension
        .checked_mul(dimension.saturating_sub(1))
        .map(|value| value / 2)
        .ok_or(CmgError::AllocationFailed {
            context: "terminal triangular size overflows",
        })
}

fn dense_bank(dimension: usize) -> Result<Vec<Vec<f64>>, CmgError> {
    let mut rows = try_capacity(dimension, "terminal dense row descriptors")?;
    for _ in 0..dimension {
        rows.push(try_filled(dimension, 0.0, "terminal dense row")?);
    }
    Ok(rows)
}

#[cfg(feature = "experimental-components")]
fn compact_fallibly<T: Copy>(values: Vec<T>) -> Result<Vec<T>, CmgError> {
    if values.len() == values.capacity() {
        return Ok(values);
    }
    let mut compact = try_capacity(values.len(), "terminal compact conversion")?;
    compact.extend_from_slice(&values);
    Ok(compact)
}

crate::storage::storage_fields!(
    GroundedLdl,
    components,
    anchors,
    permutation,
    lower,
    diagonal
);
impl crate::storage::Storage for LowerFactor {
    fn visit(
        &self,
        visitor: &mut dyn FnMut(crate::storage::Allocation) -> Result<(), CmgError>,
    ) -> Result<(), CmgError> {
        match self {
            Self::Packed { values } => values.visit(visitor),
            Self::Sparse {
                row_offsets,
                columns,
                row_values,
                column_offsets,
                rows,
                column_values,
            } => {
                row_offsets.visit(visitor)?;
                columns.visit(visitor)?;
                row_values.visit(visitor)?;
                column_offsets.visit(visitor)?;
                rows.visit(visitor)?;
                column_values.visit(visitor)
            }
        }
    }
}

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
    fn batch_correction(
        &self,
        row: usize,
        input: &[f64],
        columns: usize,
        forward: bool,
        sums: &mut [f64],
    ) {
        sums.fill(0.0);
        let mut add_entry = |source: usize, coefficient: f64| {
            for (lane, value) in sums.iter_mut().enumerate() {
                *value += coefficient * input[source * columns + lane];
            }
        };
        match self {
            Self::Packed { values } if forward => {
                let start = row * row.saturating_sub(1) / 2;
                for (column, &value) in values[start..start + row].iter().enumerate() {
                    add_entry(column, value);
                }
            }
            Self::Packed { values } => {
                let mut index = row * (row + 1) / 2 + row;
                for later in row + 1..input.len() / columns {
                    add_entry(later, values[index]);
                    index += later;
                }
            }
            Self::Sparse {
                row_offsets,
                columns,
                row_values,
                ..
            } if forward => {
                for index in row_offsets[row]..row_offsets[row + 1] {
                    add_entry(columns[index] as usize, row_values[index]);
                }
            }
            Self::Sparse {
                column_offsets,
                rows,
                column_values,
                ..
            } => {
                for index in column_offsets[row]..column_offsets[row + 1] {
                    add_entry(rows[index] as usize, column_values[index]);
                }
            }
        }
    }

    fn from_dense(lower: &[Vec<f64>]) -> Result<Self, CmgError> {
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
            let mut row_offsets = try_capacity(add(dimension, 1)?, "terminal sparse row offsets")?;
            let mut columns = try_capacity(strict_nonzeros, "terminal sparse columns")?;
            let mut row_values = try_capacity(strict_nonzeros, "terminal sparse row values")?;
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
            let mut values = try_capacity(packed_slots, "terminal packed values")?;
            for (row, dense_row) in lower.iter().enumerate() {
                values.extend_from_slice(&dense_row[..row]);
            }
            Ok(Self::Packed { values })
        }
    }

    fn from_sparse_rows(
        row_offsets: Vec<usize>,
        columns: Vec<u32>,
        row_values: Vec<f64>,
    ) -> Result<Self, CmgError> {
        let dimension = row_offsets.len() - 1;
        let strict_nonzeros = columns.len();
        let mut column_counts = try_filled(dimension, 0_usize, "terminal column counts")?;
        for &column in &columns {
            column_counts[column as usize] += 1;
        }
        let mut column_offsets = try_capacity(add(dimension, 1)?, "terminal column offsets")?;
        column_offsets.push(0);
        for count in column_counts {
            column_offsets.push(column_offsets.last().copied().unwrap_or(0) + count);
        }
        let mut next = try_capacity(dimension, "terminal column cursors")?;
        next.extend_from_slice(&column_offsets[..dimension]);
        let mut rows = try_filled(strict_nonzeros, 0_u32, "terminal transposed rows")?;
        let mut column_values = try_filled(strict_nonzeros, 0.0, "terminal transposed values")?;
        for row in 0..dimension {
            for index in row_offsets[row]..row_offsets[row + 1] {
                let column = columns[index] as usize;
                let destination = next[column];
                rows[destination] = row as u32;
                column_values[destination] = row_values[index];
                next[column] += 1;
            }
        }

        Ok(Self::Sparse {
            row_offsets,
            columns,
            row_values,
            column_offsets,
            rows,
            column_values,
        })
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
    pub(crate) fn solve_batch_compatible(
        &self,
        rhs: &[f64],
        solution: &mut [f64],
        columns: usize,
        forward: &mut [f64],
        factor_solution: &mut [f64],
    ) {
        // The output row is temporary correction storage until scattering. This
        // avoids another allocation and loads each factor entry once per tile.
        let scratch_len = columns.min(solution.len());
        let scratch = &mut solution[..scratch_len];
        let dimension = self.active_dimension();
        for row in 0..dimension {
            self.lower
                .batch_correction(row, forward, columns, true, scratch);
            for lane in 0..columns {
                let value = rhs[self.permutation[row] * columns + lane] - scratch[lane];
                forward[row * columns + lane] = value;
                #[cfg(feature = "experimental-components")]
                {
                    factor_solution[row * columns + lane] = value / self.diagonal[row];
                }
            }
        }
        #[cfg(not(feature = "experimental-components"))]
        for row in 0..dimension {
            for lane in 0..columns {
                forward[row * columns + lane] /= self.diagonal[row];
            }
        }
        for row in (0..dimension).rev() {
            self.lower
                .batch_correction(row, factor_solution, columns, false, scratch);
            for lane in 0..columns {
                #[cfg(feature = "experimental-components")]
                {
                    factor_solution[row * columns + lane] -= scratch[lane];
                }
                #[cfg(not(feature = "experimental-components"))]
                {
                    factor_solution[row * columns + lane] =
                        forward[row * columns + lane] - scratch[lane];
                }
            }
        }
        solution.fill(0.0);
        for (row, &vertex) in self.permutation.iter().enumerate() {
            solution[vertex * columns..(vertex + 1) * columns]
                .copy_from_slice(&factor_solution[row * columns..(row + 1) * columns]);
        }
    }

    /// Upper bound on all new requested heap layouts during ordinary terminal
    /// construction, including component discovery, both dense row banks and
    /// simultaneous packed/sparse conversion. Borrowed graph/allocator overhead
    /// is excluded. The bound uses vertex count, not an empirical multiplier.
    pub fn construction_allocation_bound(vertices: usize) -> Result<usize, CmgError> {
        let n = vertices;
        let square = n.checked_mul(n).ok_or(CmgError::AllocationFailed {
            context: "terminal square size overflows",
        })?;
        let q = strict_slots(n)?;
        // Retained component labels/sizes, anchors, permutation, pattern and
        // factor index: at most six native arrays; is_anchor is a boolean bank.
        let metadata = add(bytes::<[usize; 6]>(n)?, bytes::<bool>(n)?)?;
        let dense = add(bytes::<[f64; 2]>(square)?, bytes::<[Vec<f64>; 2]>(n)?)?;
        let conversion = add(
            add(bytes::<[usize; 2]>(add(n, 1)?)?, bytes::<usize>(n)?)?,
            bytes::<(u32, u32, f64, f64)>(q)?,
        )?;
        let factoring = add(add(add(metadata, dense)?, bytes::<f64>(n)?)?, conversion)?;
        Ok(factoring.max(bytes::<[usize; 4]>(n)?))
    }

    /// Admit before the first owned allocation, then use fallible known-size
    /// buffers and the unchanged ordinary LDL arithmetic/order. This covers
    /// terminal construction only, not its parent hierarchy or runtime.
    pub fn factor_with_budget(graph: &Laplacian, budget_bytes: usize) -> Result<Self, CmgError> {
        crate::storage::admit(
            Self::construction_allocation_bound(graph.vertex_count())?,
            budget_bytes,
        )?;
        Self::factor_impl::<false, true>(graph)
    }

    /// Upper bound for the bounded component-factor constructor. Worst-case
    /// fill is triangular in vertices; actual provisioning uses the sum of
    /// per-component triangular sizes and one current block, not one dense
    /// matrix across disconnected components. Runtime/borrowed parents excluded.
    #[cfg(feature = "experimental-components")]
    pub fn component_construction_allocation_bound(
        vertices: usize,
        edges: usize,
    ) -> Result<usize, CmgError> {
        let n = vertices;
        let q = strict_slots(n)?;
        let offset = bytes::<usize>(add(n, 1)?)?;
        let metadata = bytes::<[usize; 4]>(n)?;
        let grouping = add(
            add(add(offset, offset)?, bytes::<[usize; 2]>(n)?)?,
            bytes::<usize>(edges)?,
        )?;
        let factors = add(
            add(bytes::<f64>(n)?, offset)?,
            add(bytes::<u32>(q)?, bytes::<f64>(q)?)?,
        )?;
        let parents = add(add(metadata, grouping)?, factors)?;
        // Input reorder is sequential with the ordered-sparse numeric bank.
        let input = add(add(offset, bytes::<usize>(edges)?)?, bytes::<usize>(n)?)?;
        let numeric = add(
            add(add(offset, offset)?, bytes::<usize>(edges)?)?,
            add(
                add(bytes::<[usize; 2]>(n)?, bytes::<[f64; 2]>(n)?)?,
                add(bytes::<(u32, u32, f64)>(q)?, bytes::<usize>(q)?)?,
            )?,
        )?;
        // Existing row banks coexist with exact compaction, packed conversion,
        // or transposed sparse banks; maximum, not sum, of those alternatives.
        let packed_or_compact = bytes::<f64>(q)?;
        let transposed = add(
            add(offset, bytes::<usize>(n)?)?,
            add(bytes::<u32>(q)?, bytes::<f64>(q)?)?,
        )?;
        let preparation = add(bytes::<[usize; 5]>(n)?, bytes::<bool>(n)?)?;
        Ok(add(
            parents,
            input.max(numeric).max(packed_or_compact).max(transposed),
        )?
        .max(preparation)
        .max(bytes::<[usize; 4]>(n)?))
    }

    /// Budgeted component factorization, retaining sparse ordered updates and
    /// component-local construction. All owned allocation paths are fallible;
    /// the upfront bound includes capacity and old/new conversion overlap.
    #[cfg(feature = "experimental-components")]
    pub fn factor_by_component_with_budget(
        graph: &Laplacian,
        budget_bytes: usize,
    ) -> Result<Self, CmgError> {
        crate::storage::admit(
            Self::component_construction_allocation_bound(
                graph.vertex_count(),
                graph.edge_count(),
            )?,
            budget_bytes,
        )?;
        Self::factor_impl::<true, true>(graph)
    }

    /// Factor the graph Laplacian after grounding one vertex per component.
    pub fn factor(graph: &Laplacian) -> Result<Self, CmgError> {
        Self::factor_impl::<false, false>(graph)
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
        Self::factor_impl::<true, false>(graph)
    }

    fn factor_impl<const BLOCKS: bool, const ADMITTED: bool>(
        graph: &Laplacian,
    ) -> Result<Self, CmgError> {
        let vertex_count = graph.vertex_count();
        let components = if ADMITTED {
            Components::try_from_endpoints(
                vertex_count,
                graph.edges().iter().map(|e| (e.u(), e.v())),
            )?
        } else {
            Components::from_laplacian(graph)
        };

        let mut anchors = try_filled(components.count(), 0, "terminal anchors")?;
        for (vertex, &component) in components.labels().iter().enumerate() {
            anchors[component] = vertex;
        }
        let mut is_anchor = try_filled(vertex_count, false, "terminal anchor flags")?;
        for &anchor in &anchors {
            is_anchor[anchor] = true;
        }

        let mut active_vertices = try_capacity(vertex_count, "terminal permutation")?;
        active_vertices.extend((0..vertex_count).filter(|vertex| !is_anchor[*vertex]));
        let mut pattern_nonzeros = try_filled(vertex_count, 0_usize, "terminal pattern counts")?;
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
            let key = |&vertex: &usize| {
                (
                    components.labels()[vertex],
                    pattern_nonzeros[vertex],
                    vertex,
                )
            };
            if ADMITTED {
                permutation.sort_unstable_by_key(key);
            } else {
                permutation.sort_by_key(key);
            }
            drop(is_anchor);
            drop(pattern_nonzeros);
            let (lower, diagonal, factor_nonzeros) =
                factor_components::<ADMITTED>(graph, &components, &permutation)?;
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
        let key = |&vertex: &usize| (pattern_nonzeros[vertex], vertex);
        if ADMITTED {
            permutation.sort_unstable_by_key(key);
        } else {
            permutation.sort_by_key(key);
        }
        let dimension = permutation.len();

        // Assemble the ordered grounded matrix directly. The previous path
        // first materialized the full graph matrix and then copied the active
        // permutation into this second dense buffer. Direct assembly removes
        // one vertex_count^2 allocation and its complete permutation scan.
        let mut matrix = dense_bank(dimension)?;
        let mut factor_index = try_filled(vertex_count, usize::MAX, "terminal factor index")?;
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
        let lower = LowerFactor::from_dense(&dense_lower)?;

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

        #[cfg(feature = "experimental-components")]
        {
            for row in 0..dimension {
                let value =
                    rhs[self.permutation[row]] - self.lower.forward_correction(row, forward);
                // Later forward rows need the unscaled value. The existing
                // second scratch holds D^-1 y until its backward row is solved.
                forward[row] = value;
                factor_solution[row] = value / self.diagonal[row];
            }
            for row in (0..dimension).rev() {
                factor_solution[row] -= self.lower.backward_correction(row, factor_solution);
            }
        }
        #[cfg(not(feature = "experimental-components"))]
        {
            for row in 0..dimension {
                forward[row] =
                    rhs[self.permutation[row]] - self.lower.forward_correction(row, forward);
            }
            for (value, pivot) in forward.iter_mut().zip(&self.diagonal) {
                *value /= *pivot;
            }
            for row in (0..dimension).rev() {
                factor_solution[row] =
                    forward[row] - self.lower.backward_correction(row, factor_solution);
            }
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
    let mut dense_lower = dense_bank(dimension)?;
    let mut diagonal = try_filled(dimension, 0.0, "terminal diagonal")?;
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
fn factor_components<const ADMITTED: bool>(
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
            try_filled(1, pivot, "terminal singleton pivot")?
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
    let mut offsets = try_capacity(add(count, 1)?, "terminal component offsets")?;
    offsets.push(0);
    for &size in components.sizes() {
        offsets.push(offsets.last().unwrap() + size.saturating_sub(1));
    }
    let mut factor_index = try_filled(
        graph.vertex_count(),
        usize::MAX,
        "terminal component factor index",
    )?;
    for (index, &vertex) in permutation.iter().enumerate() {
        factor_index[vertex] = index;
    }
    // Group only grounded edges with flat counting-sort storage. Do not scan
    // every edge again for every component, or allocate a graph per block.
    let mut edge_offsets = try_filled(add(count, 1)?, 0usize, "terminal component edge offsets")?;
    for edge in graph.edges() {
        if factor_index[edge.u()] != usize::MAX && factor_index[edge.v()] != usize::MAX {
            edge_offsets[components.labels()[edge.u()] + 1] += 1;
        }
    }
    for c in 0..count {
        edge_offsets[c + 1] += edge_offsets[c];
    }
    let mut next = try_capacity(count, "terminal component edge cursors")?;
    next.extend_from_slice(&edge_offsets[..count]);
    let mut edge_indices = try_filled(
        edge_offsets[count],
        0usize,
        "terminal component edge indices",
    )?;
    for (index, edge) in graph.edges().iter().enumerate() {
        if factor_index[edge.u()] != usize::MAX && factor_index[edge.v()] != usize::MAX {
            let c = components.labels()[edge.u()];
            edge_indices[next[c]] = index;
            next[c] += 1;
        }
    }
    let capacity = if ADMITTED {
        components.sizes().iter().try_fold(0, |sum, &size| {
            add(sum, strict_slots(size.saturating_sub(1))?)
        })?
    } else {
        0
    };
    let mut factors = ComponentFactorRows {
        diagonal: try_capacity(dimension, "terminal component diagonal")?,
        row_offsets: try_capacity(add(dimension, 1)?, "terminal component row offsets")?,
        columns: try_capacity(capacity, "terminal component bounded columns")?,
        values: try_capacity(capacity, "terminal component bounded values")?,
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
        factors.append_block::<ADMITTED>(
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
    if ADMITTED {
        columns = compact_fallibly(columns)?;
        values = compact_fallibly(values)?;
    } else {
        columns.shrink_to_fit();
        values.shrink_to_fit();
    }
    let packed_slots = dimension.saturating_mul(dimension.saturating_sub(1)) / 2;
    let sparse_bytes = values
        .len()
        .saturating_mul(24)
        .saturating_add((dimension + 1).saturating_mul(2 * core::mem::size_of::<usize>()));
    let lower = if packed_slots.saturating_mul(8) <= sparse_bytes {
        let mut packed = try_filled(packed_slots, 0.0, "terminal component packed conversion")?;
        for row in 0..dimension {
            let start = row.saturating_mul(row.saturating_sub(1)) / 2;
            for index in row_offsets[row]..row_offsets[row + 1] {
                packed[start + columns[index] as usize] = values[index];
            }
        }
        LowerFactor::Packed { values: packed }
    } else {
        LowerFactor::from_sparse_rows(row_offsets, columns, values)?
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
    fn append_block<const ADMITTED: bool>(
        &mut self,
        graph: &Laplacian,
        permutation: &[usize],
        factor_index: &[usize],
        edge_indices: &[usize],
        base: usize,
    ) -> Result<(), CmgError> {
        let dimension = permutation.len();
        let mut input_offsets =
            try_filled(add(dimension, 1)?, 0usize, "terminal block input offsets")?;
        for &index in edge_indices {
            let edge = graph.edges()[index];
            let column = factor_index[edge.u()].min(factor_index[edge.v()]) - base;
            input_offsets[column + 1] += 1;
        }
        for column in 0..dimension {
            input_offsets[column + 1] += input_offsets[column];
        }
        let mut input_edges = try_filled(edge_indices.len(), 0usize, "terminal block input edges")?;
        {
            let mut next = try_capacity(dimension, "terminal block input cursors")?;
            next.extend_from_slice(&input_offsets[..dimension]);
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
        let capacity = if ADMITTED {
            strict_slots(dimension)?
        } else {
            edge_indices.len()
        };
        let mut entry_rows = try_capacity(capacity, "terminal block entry rows")?;
        let mut entry_columns = try_capacity(capacity, "terminal block entry columns")?;
        let mut entry_values = try_capacity(capacity, "terminal block entry values")?;
        let mut next_in_row = try_capacity(capacity, "terminal block row links")?;
        let mut column_offsets = try_capacity(add(dimension, 1)?, "terminal block column offsets")?;
        column_offsets.push(0);
        let mut first = try_filled(dimension, usize::MAX, "terminal block first links")?;
        let mut last = try_filled(dimension, usize::MAX, "terminal block last links")?;
        let mut work = try_filled(dimension, 0.0, "terminal block numeric work")?;
        let mut diagonal = try_filled(dimension, 0.0, "terminal block diagonal")?;
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

#[cfg(all(test, feature = "experimental-components"))]
mod scaled_forward_tests {
    use super::*;

    fn reference(factor: &GroundedLdl, rhs: &[f64]) -> Vec<f64> {
        let n = factor.active_dimension();
        let mut forward = vec![0.0; n];
        let mut backward = vec![0.0; n];
        for row in 0..n {
            forward[row] =
                rhs[factor.permutation[row]] - factor.lower.forward_correction(row, &forward);
        }
        for (value, pivot) in forward.iter_mut().zip(&factor.diagonal) {
            *value /= pivot;
        }
        for row in (0..n).rev() {
            backward[row] = forward[row] - factor.lower.backward_correction(row, &backward);
        }
        let mut solution = vec![0.0; factor.vertex_count()];
        for (i, &vertex) in factor.permutation.iter().enumerate() {
            solution[vertex] = backward[i];
        }
        solution
    }

    #[test]
    fn fused_scaling_matches_packed_sparse_and_reused_scratch() {
        let mut saw_packed = false;
        let mut saw_sparse = false;
        for n in [0usize, 1, 2, 7, 65] {
            for dense in [false, true] {
                for scale in [1e-100, 1.0, 1e100] {
                    let edges = (0..n).flat_map(|u| {
                        ((u + 1)..n)
                            .filter(move |&v| dense || v == u + 1)
                            .map(move |v| (u, v, scale * (0.5 + ((u * 7 + v * 11) % 17) as f64)))
                    });
                    let graph = Laplacian::from_edges(n + 3, edges).unwrap();
                    for factor in [
                        GroundedLdl::factor(&graph).unwrap(),
                        GroundedLdl::factor_by_component(&graph).unwrap(),
                    ] {
                        saw_packed |= matches!(factor.lower, LowerFactor::Packed { .. });
                        saw_sparse |= matches!(factor.lower, LowerFactor::Sparse { .. });
                        let mut forward = vec![f64::NAN; factor.active_dimension()];
                        let mut backward = forward.clone();
                        let mut actual = vec![f64::NAN; graph.vertex_count()];
                        for offset in [0, 5, 0] {
                            let target: Vec<_> = (0..graph.vertex_count())
                                .map(|v| ((v * 13 + offset) % 31) as f64 - 15.0)
                                .collect();
                            let rhs = graph.matvec(&target).unwrap();
                            let expected = reference(&factor, &rhs);
                            factor
                                .solve_into_compatible(
                                    &rhs,
                                    &mut actual,
                                    &mut forward,
                                    &mut backward,
                                )
                                .unwrap();
                            assert_eq!(
                                actual.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
                                expected.iter().map(|x| x.to_bits()).collect::<Vec<_>>()
                            );
                        }
                    }
                }
            }
        }
        assert!(saw_packed && saw_sparse);
    }
}
