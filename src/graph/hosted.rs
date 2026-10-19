//! Initial input graph only: bounded mapping and canonical parallel sorting.

use super::{Edge, Laplacian, compare_raw_edges};
use crate::{
    BuildDecision, BuildOperation, BuildStopReason, CmgError, ControlledStage, InstalledTeam,
    build_control::BuildMeter,
};

const MIN_EDGES: usize = 65_536;
const CHUNK: usize = 8;

impl Laplacian {
    /// Construct an initial graph on an already admitted host team.
    ///
    /// At least 65,536 input edges use fixed logical chunks for validation,
    /// mapping and sorting. The logical algorithm and work charges are identical
    /// at every worker ceiling, including one. Smaller inputs retain the serial
    /// algorithm after a 64-unit policy charge. Coarse/hierarchy builders are
    /// unaffected. Successful graph bits match [`Self::from_edges_controlled`].
    ///
    /// The borrowed input and host runtime are excluded from `budget_bytes`.
    /// The bound is unchanged: two compact edge banks, the diagonal, and three
    /// Arc layouts. All scratch and work are admitted before use. No partial
    /// stage is retried. The serial controller stays on the coordinator; workers
    /// use only `cancelled`, a cheap, sticky, allocation-free cancellation query.
    /// All work joins before return or unwind. Cancellation dominates worker
    /// errors after joining; otherwise the earliest logical invalid edge wins.
    #[allow(clippy::too_many_arguments)]
    pub fn from_edges_hosted_controlled(
        vertex_count: usize,
        edges: &[(usize, usize, f64)],
        budget_bytes: usize,
        work_budget: u64,
        team: &InstalledTeam<'_>,
        cancelled: impl Fn() -> bool + Sync,
        control: &mut dyn crate::BuildControl,
    ) -> Result<Self, CmgError> {
        let mut checked_control = |boundary| {
            if cancelled() {
                BuildDecision::Interrupted
            } else {
                control.checkpoint(boundary)
            }
        };
        let mut meter = BuildMeter::new(work_budget, &mut checked_control);
        meter.work("hosted input graph policy", 64)?;
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
            &mut meter,
            "hosted input edge mapping",
            (CHUNK, 1024),
            team,
            &mut raw,
            &cancelled,
            |chunk, output| {
                for (index, value) in output.iter_mut().enumerate() {
                    *value = map_edge(vertex_count, edges[chunk * CHUNK + index])?;
                }
                Ok(())
            },
        )?;
        sort(&mut raw, &mut meter, team, &cancelled)?;
        // The spare sort bank has been dropped. Canonical compensated duplicate
        // reduction and diagonal additions are the exact existing serial loop.
        Self::from_sorted_edges_controlled(vertex_count, raw, &mut meter)
    }
}

fn initialized_bank(len: usize, meter: &mut BuildMeter<'_>) -> Result<Vec<Edge>, CmgError> {
    let mut values = meter.try_capacity(len, "hosted input edge bank")?;
    // Safe Rust requires initialized disjoint output storage. Its first-touch
    // cost remains charged, and the bank never grows after admission.
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

/// Forward all stage charges through one global meter. The local dispatcher
/// meter is only a finite shape/counter proof; it never owns a second allowance.
fn stage(
    meter: &mut BuildMeter<'_>,
    phase: &'static str,
    packet: (usize, u16),
    team: &InstalledTeam<'_>,
    values: &mut [Edge],
    cancelled: &(impl Fn() -> bool + Sync),
    kernel: impl Fn(usize, &mut [Edge]) -> Result<(), CmgError> + Sync,
) -> Result<(), CmgError> {
    let (chunk_len, kernel_work) = packet;
    let mut failure = None;
    let admitted = ControlledStage::admit(
        values.len(),
        chunk_len,
        kernel_work,
        u64::MAX,
        &mut |boundary: crate::BuildBoundary| {
            let result = match boundary.operation {
                BuildOperation::Work { units } => meter.work(phase, units),
                BuildOperation::Allocation { .. } => {
                    unreachable!("stage admission cannot allocate")
                }
            };
            match result {
                Ok(()) => BuildDecision::Continue,
                Err(error) => {
                    failure = Some(error);
                    BuildDecision::StopUnused
                }
            }
        },
    );
    if let Some(error) = failure {
        return Err(error);
    }
    let result = admitted?.run(team, values, cancelled, kernel);
    // Translate dispatcher-local cancellation coordinates to the global meter.
    match result {
        Err(CmgError::BuildStopped {
            reason: BuildStopReason::Interrupted,
            ..
        }) => Err(meter.stopped(phase, BuildStopReason::Interrupted)),
        other => other,
    }
}

fn sort(
    values: &mut Vec<Edge>,
    meter: &mut BuildMeter<'_>,
    team: &InstalledTeam<'_>,
    cancelled: &(impl Fn() -> bool + Sync),
) -> Result<(), CmgError> {
    stage(
        meter,
        "hosted input sort runs",
        (CHUNK, 512),
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
        let (chunk_len, allowance) = merge_policy(width)?;
        stage(
            meter,
            "hosted input merge",
            (chunk_len, allowance),
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
