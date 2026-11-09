//! Coarse-row (gather) restriction for team applications.
//!
//! The serial restriction scatters every fine residual into its coarse row in
//! ascending fine order, starting from zero. [`RestrictionRows`] stores the
//! transpose of one level's transfer: the fine members of every coarse row in
//! ascending fine order. A coarse row then sums its members' residuals from
//! zero in that same order, so each coarse value is computed by the serial
//! operation sequence, and rows are independent outputs that team lanes own
//! without atomics. The restricted values are bit-identical to the serial
//! scatter at every worker count.

use crate::CmgError;
use crate::cancel::{LanePoll, Meter, Progress, chunked, lane_tiles};
use crate::execution::Team;
use crate::storage::{add, bytes};

/// Fine members of every coarse row of one transfer, in ascending fine order.
#[derive(Debug, Clone)]
pub(crate) struct RestrictionRows {
    fine_dimension: usize,
    /// `coarse + 1` offsets into `members`.
    offsets: Vec<u32>,
    members: Vec<u32>,
}

crate::storage::storage_fields!(RestrictionRows, offsets, members);

const PHASE: &str = "plan restriction rows";

impl RestrictionRows {
    /// Whether a transfer with these dimensions has compact restriction rows.
    pub(crate) const fn representable(fine: usize, entries: usize) -> bool {
        fine <= u32::MAX as usize + 1 && entries <= u32::MAX as usize
    }

    /// Requested layouts of [`Self::build`] (no scratch is allocated).
    pub(crate) fn allocation_bytes(coarse: usize, entries: usize) -> Result<usize, CmgError> {
        add(bytes::<u32>(add(coarse, 1)?)?, bytes::<u32>(entries)?)
    }

    /// Transpose the `entries` transfer entries `entry(i) = (fine, coarse)`,
    /// given in ascending fine order, by a counted counting sort: row counts,
    /// an inclusive prefix sum, then placement in descending entry order, so
    /// every row's members end ascending and its offset at the row start.
    pub(crate) fn build<M: Meter>(
        fine_dimension: usize,
        coarse_dimension: usize,
        entries: usize,
        entry: impl Fn(usize) -> (usize, usize),
        meter: &mut M,
    ) -> Result<Self, CmgError> {
        if !Self::representable(fine_dimension, entries) {
            return Err(CmgError::InvalidHierarchy {
                context: "restriction rows exceed compact indices",
            });
        }
        let mut offsets = meter.try_filled(add(coarse_dimension, 1)?, 0_u32, PHASE)?;
        chunked(meter, PHASE, entries, 4, |range| {
            for index in range {
                offsets[entry(index).1] += 1;
            }
            Ok(())
        })?;
        let mut running = 0_u32;
        chunked(meter, PHASE, coarse_dimension, 4, |range| {
            for offset in &mut offsets[range] {
                running += *offset;
                *offset = running;
            }
            Ok(())
        })?;
        offsets[coarse_dimension] = running;
        let mut members = meter.try_filled(entries, 0_u32, PHASE)?;
        chunked(meter, PHASE, entries, 4, |range| {
            // Segments run forward; entries are visited last to first.
            for index in range.map(|index| entries - 1 - index) {
                let (fine, coarse) = entry(index);
                offsets[coarse] -= 1;
                members[offsets[coarse] as usize] = fine as u32;
            }
            Ok(())
        })?;
        Ok(Self {
            fine_dimension,
            offsets,
            members,
        })
    }

    /// Requested heap bytes retained.
    pub(crate) fn byte_len(&self) -> usize {
        (self.offsets.len() + self.members.len()).saturating_mul(core::mem::size_of::<u32>())
    }

    /// Whether these rows belong to a transfer of these dimensions.
    pub(crate) fn matches(&self, fine: usize, coarse: usize, entries: usize) -> bool {
        self.fine_dimension == fine
            && self.offsets.len() == coarse + 1
            && self.members.len() == entries
    }

    /// `coarse = R (rhs - product)` for vertex-major lanes on the team: fixed
    /// coarse row blocks, each row's lanes summed from zero over its members
    /// in ascending fine order (the serial scatter's order). Lanes count their
    /// members through their polls; the meter polls after the join.
    pub(crate) fn restrict_residual_lanes<M: Meter>(
        &self,
        rhs: &[f64],
        product: &[f64],
        coarse: &mut [f64],
        columns: usize,
        team: Team<'_>,
        meter: &mut M,
    ) -> Result<(), CmgError> {
        let fine = self.fine_dimension * columns;
        for (context, actual) in [
            ("restriction rows rhs", rhs.len()),
            ("restriction rows matvec", product.len()),
        ] {
            if actual != fine {
                return Err(CmgError::dimension(context, fine, actual));
            }
        }
        let rows = self.offsets.len() - 1;
        if coarse.len() != rows * columns {
            return Err(CmgError::dimension(
                "restriction rows coarse",
                rows * columns,
                coarse.len(),
            ));
        }
        let result =
            super::rows_parallel_with(team, meter.query(), coarse, columns, |row, values, poll| {
                let start = self.offsets[row] as usize;
                let members = &self.members[start..self.offsets[row + 1] as usize];
                // A batch wider than one counted segment traverses the row
                // once per lane tile; every lane keeps the ascending order.
                for lanes in lane_tiles::<LanePoll<'_>>(columns, 1) {
                    let tile = &mut values[lanes.clone()];
                    tile.fill(0.0);
                    chunked(
                        poll,
                        "CMG restriction",
                        members.len(),
                        tile.len(),
                        |range| {
                            for &member in &members[range] {
                                let base = member as usize * columns;
                                for (lane, value) in (lanes.start..).zip(tile.iter_mut()) {
                                    *value += rhs[base + lane] - product[base + lane];
                                }
                            }
                            Ok(())
                        },
                    )?;
                    poll.advance("CMG restriction", tile.len())?;
                }
                Ok(())
            });
        meter.joined("CMG restriction")?;
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cancel::Unmetered;

    #[test]
    fn rows_list_ascending_members_and_bound_the_layout() {
        let labels = [2, 0, 1, 2, 2, 1, 0, 2, 4];
        let rows = RestrictionRows::build(9, 5, 9, |i| (i, labels[i]), &mut Unmetered).unwrap();
        assert_eq!(rows.offsets, [0, 2, 4, 8, 8, 9]);
        assert_eq!(rows.members, [1, 6, 2, 5, 0, 3, 4, 7, 8]);
        assert!(rows.matches(9, 5, 9));
        assert_eq!(
            rows.byte_len(),
            RestrictionRows::allocation_bytes(5, 9).unwrap()
        );
        // A partial transfer: fine rows 1 and 4 have no coarse entry.
        let entries = [(0, 1), (2, 0), (3, 1), (5, 0)];
        let pruned = RestrictionRows::build(6, 2, 4, |i| entries[i], &mut Unmetered).unwrap();
        assert_eq!(pruned.offsets, [0, 2, 4]);
        assert_eq!(pruned.members, [2, 5, 0, 3]);
        let empty = RestrictionRows::build(0, 0, 0, |_| (0, 0), &mut Unmetered).unwrap();
        assert_eq!(empty.offsets, [0]);
        assert!(empty.members.is_empty());
    }
}
