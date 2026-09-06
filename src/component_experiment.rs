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
    /// Keep retired representatives in vertex-based stopping decisions.
    ///
    /// With pruning enabled, this separates compact storage from early direct
    /// factorization. Retired representatives carry no edges, vectors or work.
    /// An empty compact child always terminates directly. Ignored without pruning.
    pub preserve_unpruned_stopping: bool,
    /// Use ordered sparse factors for direct terminals, one component at a time.
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
        if self.has_fine_prefix() {
            for (&value, &(_, coarse_index)) in fine.iter().zip(&self.entries) {
                coarse[coarse_index as usize] += value;
            }
        } else {
            for &(fine_index, coarse_index) in &self.entries {
                coarse[coarse_index as usize] += fine[fine_index as usize];
            }
        }
        Ok(())
    }

    pub(crate) fn restrict_residual_into(
        &self,
        rhs: &[f64],
        matrix_value: &[f64],
        coarse: &mut [f64],
    ) -> Result<(), CmgError> {
        self.validate(rhs.len(), coarse.len())?;
        self.validate(matrix_value.len(), coarse.len())?;
        coarse.fill(0.0);
        if self.has_fine_prefix() {
            for ((&b, &ax), &(_, coarse_index)) in rhs.iter().zip(matrix_value).zip(&self.entries) {
                coarse[coarse_index as usize] += b - ax;
            }
        } else {
            for &(fine, coarse_index) in &self.entries {
                coarse[coarse_index as usize] += rhs[fine as usize] - matrix_value[fine as usize];
            }
        }
        Ok(())
    }

    /// Add the transpose action; fine rows without an entry remain unchanged.
    pub fn prolong_add_into(&self, coarse: &[f64], fine: &mut [f64]) -> Result<(), CmgError> {
        self.validate(fine.len(), coarse.len())?;
        if self.has_fine_prefix() {
            for (value, &(_, coarse_index)) in fine.iter_mut().zip(&self.entries) {
                *value += coarse[coarse_index as usize];
            }
        } else {
            for &(fine_index, coarse_index) in &self.entries {
                fine[fine_index as usize] += coarse[coarse_index as usize];
            }
        }
        Ok(())
    }

    fn has_fine_prefix(&self) -> bool {
        // Entries are unique and sorted by fine index, so this last-index test
        // proves that every preceding fine row is represented contiguously.
        self.entries
            .last()
            .is_none_or(|&(fine, _)| fine as usize + 1 == self.entries.len())
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
) -> Option<(PrunedTransfer, Laplacian)> {
    let (map, graph) = coarse.without_isolated_vertices()?;
    let mut entries = Vec::new();
    // The public graph representation already bounds vertex indices to u32.
    for fine in 0..aggregation.fine_dimension() {
        let label = aggregation.label_at(fine);
        if map[label] != usize::MAX {
            entries.push((fine as u32, map[label] as u32));
        }
    }
    entries.shrink_to_fit();
    Some((
        PrunedTransfer {
            fine_dimension: aggregation.fine_dimension(),
            coarse_dimension: graph.vertex_count(),
            represented_coarse_dimension: coarse.vertex_count(),
            entries,
        },
        graph,
    ))
}

#[cfg(test)]
mod residual_restriction_tests {
    use super::*;

    #[test]
    fn pruned_residual_restriction_preserves_order_and_ignores_retired_rows() {
        let rhs = [1e100, 1.0, -1e100, 3.0, -0.0, 5.0, f64::NAN, f64::NAN];
        let ax = [0.0, 0.5, 0.0, -0.0, 0.0, 1.0, f64::NAN, f64::NAN];
        for entries in [
            vec![(0, 0), (1, 0), (2, 0), (3, 0)],
            vec![(0, 0), (2, 0), (4, 1), (5, 1)],
            vec![],
        ] {
            let transfer = PrunedTransfer {
                fine_dimension: 8,
                coarse_dimension: 2,
                represented_coarse_dimension: 4,
                entries,
            };
            let residual: Vec<_> = rhs.iter().zip(ax).map(|(b, a)| b - a).collect();
            let mut expected = [f64::NAN; 2];
            transfer.restrict_into(&residual, &mut expected).unwrap();
            let mut actual = [f64::NAN; 2];
            transfer
                .restrict_residual_into(&rhs, &ax, &mut actual)
                .unwrap();
            assert_eq!(actual.map(f64::to_bits), expected.map(f64::to_bits));
            assert!(
                transfer
                    .restrict_residual_into(&rhs[..7], &ax, &mut actual)
                    .is_err()
            );
            assert!(
                transfer
                    .restrict_residual_into(&rhs, &ax[..7], &mut actual)
                    .is_err()
            );
            assert_eq!(actual.map(f64::to_bits), expected.map(f64::to_bits));
        }
    }
}
