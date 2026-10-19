//! Initial input graph only: bounded mapping and canonical parallel sorting.

use super::{Edge, Laplacian, compare_raw_edges};
use crate::{CmgError, ControlledStage, InstalledTeam, cancel::CancelMeter};

const MIN_EDGES: usize = 65_536;
const CHUNK: usize = 8;

impl Laplacian {
    /// Construct an initial graph on an already admitted host team.
    ///
    /// At least 65,536 input edges use fixed logical chunks for validation,
    /// mapping and sorting. The logical chunking is identical at every worker
    /// ceiling, including one. Smaller inputs retain the serial algorithm.
    /// Coarse/hierarchy builders are unaffected. Successful graph bits match
    /// [`Self::from_edges_controlled`].
    ///
    /// The borrowed input and host runtime are excluded from `budget_bytes`.
    /// The bound is unchanged: two compact edge banks, the diagonal, and three
    /// Arc layouts. All scratch is admitted before use. No partial stage is
    /// retried. `cancelled` is a cheap, sticky, allocation-free cancellation
    /// query polled by the coordinator between bounded serial operations and by
    /// workers before every logical chunk. All work joins before return or
    /// unwind. Cancellation dominates worker errors after joining; otherwise the
    /// earliest logical invalid edge wins.
    pub fn from_edges_hosted_controlled(
        vertex_count: usize,
        edges: &[(usize, usize, f64)],
        budget_bytes: usize,
        team: &InstalledTeam<'_>,
        cancelled: crate::Cancellation<'_>,
    ) -> Result<Self, CmgError> {
        let mut meter = CancelMeter::new(cancelled);
        if edges.len() < MIN_EDGES {
            return Self::from_edges_metered(vertex_count, edges, budget_bytes, &mut meter);
        }
        meter.work("input graph memory bound", 128)?;
        let edge_bytes = crate::storage::bytes::<Edge>(edges.len())?;
        let mut required = crate::storage::add(edge_bytes, edge_bytes)?;
        required = crate::storage::add(required, crate::storage::bytes::<f64>(vertex_count)?)?;
        required = crate::storage::add(required, crate::storage::arc_bytes::<Vec<Edge>>()?)?;
        required = crate::storage::add(required, crate::storage::arc_bytes::<Vec<f64>>()?)?;
        required = crate::storage::add(required, crate::storage::arc_bytes::<()>()?)?;
        crate::storage::admit(required, budget_bytes)?;
        let mut raw = initialized_bank(edges.len(), &mut meter)?;
        stage(
            "hosted input edge mapping",
            CHUNK,
            team,
            &mut raw,
            cancelled,
            |chunk, output| {
                for (index, value) in output.iter_mut().enumerate() {
                    *value = map_edge(vertex_count, edges[chunk * CHUNK + index])?;
                }
                Ok(())
            },
        )?;
        sort(&mut raw, &mut meter, team, cancelled)?;
        // The spare sort bank has been dropped. Canonical compensated duplicate
        // reduction and diagonal additions are the exact existing serial loop.
        Self::from_sorted_edges_controlled(vertex_count, raw, &mut meter)
    }
}

fn initialized_bank(len: usize, meter: &mut CancelMeter<'_>) -> Result<Vec<Edge>, CmgError> {
    let mut values = meter.try_capacity(len, "hosted input edge bank")?;
    // Safe Rust requires initialized disjoint output storage. Its first-touch
    // cost is counted for cancellation polling; the bank never grows after admission.
    for start in (0..len).step_by(128) {
        let count = (len - start).min(128);
        meter.work("hosted input edge initialization", (8 * count) as u16)?;
        for _ in 0..count {
            values.push(Edge::from_compact_parts(0, 0, 0.0));
        }
    }
    Ok(values)
}

fn map_edge(
    vertex_count: usize,
    (left, right, weight): (usize, usize, f64),
) -> Result<Edge, CmgError> {
    if left >= vertex_count {
        return Err(CmgError::VertexOutOfBounds {
            vertex: left,
            vertex_count,
        });
    }
    if right >= vertex_count {
        return Err(CmgError::VertexOutOfBounds {
            vertex: right,
            vertex_count,
        });
    }
    if left > u32::MAX as usize {
        return Err(CmgError::VertexIndexTooWide {
            vertex: left,
            maximum: u32::MAX as usize,
        });
    }
    if right > u32::MAX as usize {
        return Err(CmgError::VertexIndexTooWide {
            vertex: right,
            maximum: u32::MAX as usize,
        });
    }
    if left == right {
        return Err(CmgError::SelfLoop { vertex: left });
    }
    if !weight.is_finite() || weight <= 0.0 {
        return Err(CmgError::InvalidEdgeWeight {
            u: left,
            v: right,
            weight,
        });
    }
    let (u, v) = if left < right {
        (left, right)
    } else {
        (right, left)
    };
    Ok(Edge::from_compact_parts(u as u32, v as u32, weight))
}

/// Dispatch one stage of fixed logical chunks, translating its cancellation to
/// the stage's phase. Chunk boundaries never depend on the worker ceiling.
fn stage(
    phase: &'static str,
    chunk_len: usize,
    team: &InstalledTeam<'_>,
    values: &mut [Edge],
    cancelled: crate::Cancellation<'_>,
    kernel: impl Fn(usize, &mut [Edge]) -> Result<(), CmgError> + Sync,
) -> Result<(), CmgError> {
    match ControlledStage::admit(values.len(), chunk_len)?.run(team, values, cancelled, kernel) {
        Err(CmgError::Cancelled { .. }) => Err(CancelMeter::cancelled(phase)),
        other => other,
    }
}

fn sort(
    values: &mut Vec<Edge>,
    meter: &mut CancelMeter<'_>,
    team: &InstalledTeam<'_>,
    cancelled: crate::Cancellation<'_>,
) -> Result<(), CmgError> {
    stage(
        "hosted input sort runs",
        CHUNK,
        team,
        values,
        cancelled,
        |_, run| {
            // At most 28 compare/swap steps and seven loop starts. No library sort,
            // allocation, recursion, or data-dependent unbounded work is hidden here.
            for index in 1..run.len() {
                let mut current = index;
                while current > 0 && compare_raw_edges(&run[current], &run[current - 1]).is_lt() {
                    run.swap(current, current - 1);
                    current -= 1;
                }
            }
            Ok(())
        },
    )?;
    let mut scratch = initialized_bank(values.len(), meter)?;
    let mut width = CHUNK;
    while width < values.len() {
        meter.work("hosted input merge planning", 64)?;
        let (chunk_len, _) = merge_policy(width)?;
        stage(
            "hosted input merge",
            chunk_len,
            team,
            &mut scratch,
            cancelled,
            |chunk, out| {
                merge_chunk(values, width, chunk * chunk_len, out);
                Ok(())
            },
        )?;
        std::mem::swap(values, &mut scratch);
        width = width.checked_mul(2).ok_or(CmgError::InvalidHierarchy {
            context: "hosted merge width overflows",
        })?;
    }
    Ok(())
}

fn merge_policy(width: usize) -> Result<(usize, u16), CmgError> {
    // A merge-position search takes <=ceil(log2(width+1)) steps. Sixteen
    // primitive arithmetic/access/comparison/move operations per search step
    // and per emitted record, plus 64 fixed operations, conservatively cover
    // the branch-heavy path. These are logical operations, not instructions.
    // At enormous widths reduce the output packet, always to a power of two;
    // it still divides each merge pair. A Vec<Edge> fits below isize::MAX bytes,
    // hence search_steps<=59 on 64-bit and the final one-record packet fits.
    let search_steps = usize::BITS - width.leading_zeros();
    let fixed = 64 + 16 * search_steps;
    let mut chunk_len = CHUNK;
    while chunk_len > 1 && fixed + 16 * chunk_len as u32 > 1024 {
        chunk_len /= 2;
    }
    let allowance = fixed + 16 * chunk_len as u32;
    if allowance > 1024 {
        return Err(CmgError::InvalidHierarchy {
            context: "hosted merge packet exceeds its bounded allowance",
        });
    }
    Ok((chunk_len, allowance as u16))
}

fn merge_chunk(input: &[Edge], width: usize, start: usize, output: &mut [Edge]) {
    // Width is a power-of-two multiple of CHUNK, so an output chunk cannot
    // cross a merge-pair boundary. A Vec<Edge> fits below isize::MAX bytes;
    // doubling its largest relevant run width cannot overflow usize.
    let span = width * 2;
    let pair = start / span * span;
    let middle = (pair + width).min(input.len());
    let end = (pair + span).min(input.len());
    let left = &input[pair..middle];
    let right = &input[middle..end];
    let rank = start - pair;
    let mut low = rank.saturating_sub(right.len());
    let mut high = rank.min(left.len());
    while low < high {
        let i = low + (high - low) / 2;
        let j = rank - i;
        // Locate the first valid partition, choosing the left record on ties.
        if j > 0 && i < left.len() && !compare_raw_edges(&right[j - 1], &left[i]).is_lt() {
            low = i + 1;
        } else {
            high = i;
        }
    }
    let mut i = low;
    let mut j = rank - i;
    for value in output {
        if i < left.len() && (j == right.len() || !compare_raw_edges(&left[i], &right[j]).is_gt()) {
            *value = left[i];
            i += 1;
        } else {
            *value = right[j];
            j += 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn merge_packets_cover_every_representable_edge_bank() {
        let max_edges = isize::MAX as usize / std::mem::size_of::<Edge>();
        let mut width = CHUNK;
        while width < max_edges {
            let (chunk, allowance) = merge_policy(width).unwrap();
            assert!(chunk.is_power_of_two());
            assert_eq!(width % chunk, 0);
            assert!((1..=1024).contains(&allowance));
            assert_eq!(
                allowance as u32,
                64 + 16 * (usize::BITS - width.leading_zeros()) + 16 * chunk as u32
            );
            width *= 2;
        }
    }

    #[test]
    fn merge_partitions_match_canonical_sort_for_ties_and_ragged_runs() {
        for width in [8, 16, 32, 64, 128] {
            for tail in 0..2 * CHUNK {
                let len = 3 * width + tail;
                let mut input: Vec<_> = (0..len)
                    .map(|i| {
                        Edge::from_compact_parts(
                            (i * 37 % 11) as u32,
                            12,
                            [0.125, 1.0, 1e12][i * 13 % 3],
                        )
                    })
                    .collect();
                for run in input.chunks_mut(width) {
                    run.sort_unstable_by(compare_raw_edges);
                }
                let mut expected = input.clone();
                for pair in expected.chunks_mut(2 * width) {
                    pair.sort_unstable_by(compare_raw_edges);
                }
                let mut actual = input.clone();
                for (chunk, out) in actual.chunks_mut(CHUNK).enumerate() {
                    merge_chunk(&input, width, chunk * CHUNK, out);
                }
                assert_eq!(actual, expected);
            }
        }
    }
}
