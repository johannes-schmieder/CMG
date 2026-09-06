//! Opt-in component experiments. Ordinary builders retain the upstream cycle.

use crate::{Aggregation, CmgError, Laplacian};

/// Independent experimental changes, for attribution against the ordinary builder.
///
/// These options change setup only. Every resulting preconditioner is fixed
/// across applications; RHS values and convergence never determine its structure.
#[derive(Debug, Clone, Copy, Default)]
pub struct ComponentBuildOptions {
    /// Retire isolated coarse vertices after contraction, retaining parent smoothing.
    pub prune_coarse_isolates: bool,
    /// Factor disconnected direct terminals one block at a time.
    pub factor_terminal_components: bool,
}

/// Restriction to surviving coarse vertices, with transpose prolongation.
///
/// Unlike an [`Aggregation`], some fine rows have no coarse entry. Only whole
/// isolated coarse vertices are removed. Entries retain increasing fine-vertex
/// order, so restriction keeps the original order of floating-point additions.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PrunedTransfer {
    fine_dimension: usize,
    coarse_dimension: usize,
    represented_coarse_dimension: usize,
    entries: Vec<(u32, u32)>,
}

impl PrunedTransfer {
    /// Dimension of the complete parent level, including retired components.
    pub const fn fine_dimension(&self) -> usize {
        self.fine_dimension
    }

    /// Dimension of the compact child graph.
    pub const fn coarse_dimension(&self) -> usize {
        self.coarse_dimension
    }

    /// Number of aggregates before isolated coarse vertices were removed.
    pub const fn represented_coarse_dimension(&self) -> usize {
        self.represented_coarse_dimension
    }

    /// Surviving `(fine_vertex, coarse_vertex)` entries in fine-vertex order.
    pub fn entries(&self) -> &[(u32, u32)] {
        &self.entries
    }

    pub(crate) fn retained_bytes(&self) -> usize {
        self.entries
            .capacity()
            .saturating_mul(core::mem::size_of::<(u32, u32)>())
    }

    /// Restrict into the compact child, ignoring retired coarse degrees of freedom.
    pub fn restrict_into(&self, fine: &[f64], coarse: &mut [f64]) -> Result<(), CmgError> {
        self.validate(fine.len(), coarse.len())?;
        coarse.fill(0.0);
        for &(fine_index, coarse_index) in &self.entries {
            coarse[coarse_index as usize] += fine[fine_index as usize];
        }
        Ok(())
    }

    /// Add the transpose action; fine rows without an entry remain unchanged.
    pub fn prolong_add_into(&self, coarse: &[f64], fine: &mut [f64]) -> Result<(), CmgError> {
        self.validate(fine.len(), coarse.len())?;
        for &(fine_index, coarse_index) in &self.entries {
            fine[fine_index as usize] += coarse[coarse_index as usize];
        }
        Ok(())
    }

    fn validate(&self, fine: usize, coarse: usize) -> Result<(), CmgError> {
        if fine != self.fine_dimension {
            return Err(CmgError::dimension(
                "PrunedTransfer fine",
                self.fine_dimension,
                fine,
            ));
        }
        if coarse != self.coarse_dimension {
            return Err(CmgError::dimension(
                "PrunedTransfer coarse",
                self.coarse_dimension,
                coarse,
            ));
        }
        Ok(())
    }
}

pub(crate) fn prune(
    aggregation: &Aggregation,
    coarse: &Laplacian,
) -> Result<Option<(PrunedTransfer, Laplacian)>, CmgError> {
    if coarse.diagonal().iter().all(|&degree| degree > 0.0) {
        return Ok(None);
    }
    let mut map = vec![usize::MAX; coarse.vertex_count()];
    let mut dimension = 0;
    for (vertex, &degree) in coarse.diagonal().iter().enumerate() {
        if degree > 0.0 {
            map[vertex] = dimension;
            dimension += 1;
        }
    }
    let mut entries = Vec::new();
    // The public graph representation already bounds vertex indices to u32.
    for fine in 0..aggregation.fine_dimension() {
        let label = aggregation.label_at(fine);
        if map[label] != usize::MAX {
            entries.push((fine as u32, map[label] as u32));
        }
    }
    entries.shrink_to_fit();
    let graph = Laplacian::from_edges(
        dimension,
        coarse
            .edges()
            .iter()
            .map(|edge| (map[edge.u()], map[edge.v()], edge.weight())),
    )?;
    Ok(Some((
        PrunedTransfer {
            fine_dimension: aggregation.fine_dimension(),
            coarse_dimension: dimension,
            represented_coarse_dimension: coarse.vertex_count(),
            entries,
        },
        graph,
    )))
}
