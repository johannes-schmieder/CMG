//! Initial input graph only: bounded mapping and canonical parallel sorting.

use super::{Edge, Laplacian, compare_raw_edges, map_edge};
use crate::cancel::{CancelMeter, Meter};
use crate::{CmgError, ControlledStage, Team};

pub(super) const MIN_EDGES: usize = 65_536;
const CHUNK: usize = 8;

/// Validate, map and sort a large borrowed input in fixed logical chunks on a
/// team, then reduce duplicates with the serial canonical merge.
///
/// The logical chunking is identical at every worker ceiling, including one.
/// Successful graph bits match the serial construction. The borrowed input and
/// team runtime are outside the caller's admitted bound; all scratch belongs
/// to that bound and is provisioned before dispatch. The meter's query is
/// polled by workers before every logical chunk and after each join; the
/// coordinator's meter polls between its bounded serial operations.
/// Cancellation dominates worker errors after joining; otherwise the earliest
/// logical invalid edge wins. No partial stage is retried.
pub(super) fn build<M: Meter>(
    vertex_count: usize,
    edges: &[(usize, usize, f64)],
    team: Team<'_>,
    meter: &mut M,
) -> Result<Laplacian, CmgError> {
    meter.work("input graph memory bound", 128)?;
    let mut raw = initialized_bank(edges.len(), meter)?;
    stage(
        "hosted input edge mapping",
        CHUNK,
        team,
        &mut raw,
        meter.query(),
        |chunk, output| {
            for (index, value) in output.iter_mut().enumerate() {
                *value = map_edge(vertex_count, edges[chunk * CHUNK + index])?;
            }
            Ok(())
        },
    )?;
    sort(&mut raw, meter, team)?;
    // The spare sort bank has been dropped; reduce in the canonical order.
    Laplacian::from_sorted_edges(vertex_count, raw, true, meter)
}

fn initialized_bank<M: Meter>(len: usize, meter: &mut M) -> Result<Vec<Edge>, CmgError> {
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

/// Dispatch one stage of fixed logical chunks on at most the team's worker
/// ceiling, translating its cancellation to the stage's phase. Chunk
/// boundaries never depend on the worker ceiling.
pub(crate) fn stage<T: Send>(
    phase: &'static str,
    chunk_len: usize,
    team: Team<'_>,
    values: &mut [T],
    query: Option<crate::Cancellation<'_>>,
    kernel: impl Fn(usize, &mut [T]) -> Result<(), CmgError> + Sync,
) -> Result<(), CmgError> {
    let cancelled = || query.is_some_and(|cancel| cancel());
    match ControlledStage::admit(values.len(), chunk_len)?.run(team, values, cancelled, kernel) {
        Err(CmgError::Cancelled { .. }) => Err(CancelMeter::cancelled(phase)),
        other => other,
    }
}

/// The initial-graph sort: bounded insertion runs, then bounded merge packets
/// between `values` and the spare bank admitted by
/// `Laplacian::construction_allocation_bound`, every stage polled and capped
/// at the worker ceiling. The spare bank is dropped before returning.
fn sort<M: Meter>(values: &mut Vec<Edge>, meter: &mut M, team: Team<'_>) -> Result<(), CmgError> {
    stage(
        "hosted input sort runs",
        CHUNK,
        team,
        values,
        meter.query(),
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
            meter.query(),
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
