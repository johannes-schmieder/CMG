//! In-place canonical edge sort on a team: bounded, polled and capped at the
//! worker ceiling, with no allocation.

use super::{Edge, compare_raw_edges};
use crate::cancel::{LanePoll, Meter, Progress, heapsort};
use crate::execution::Lanes;
use crate::{CmgError, Team};
use core::cmp::Ordering;

/// Ranges at most this long are insertion-sorted.
const SMALL: usize = 16;
/// Partitions shorter than this are not forked.
const FORK_MIN: usize = 1 << 14;
const PHASE: &str = "team edge sort";

/// Sort `values` by the total raw-edge order in place: an introsort with
/// three-way partitions around a median-of-three pivot and a heapsort fallback
/// at depth `2 log2(n)`. Partitions fork while lanes remain, so at most the
/// worker ceiling of lanes run; each lane counts every partition step,
/// insertion step and heap step and polls the meter's query at the fixed
/// cadence. The comparator is total, so the order equals every other sort's,
/// independent of pivots and scheduling. Nothing is allocated.
pub(crate) fn sort<M: Meter>(
    values: &mut [Edge],
    team: Team<'_>,
    meter: &mut M,
) -> Result<(), CmgError> {
    let query = meter.query();
    let depth = 2 * (usize::BITS - values.len().leading_zeros()) as usize;
    let sorted =
        team.with_lanes(|lanes| sort_range(values, depth, lanes, &mut LanePoll::new(query), query));
    meter.joined(PHASE)?;
    sorted
}

fn sort_range(
    mut values: &mut [Edge],
    mut depth: usize,
    lanes: Lanes<'_>,
    poll: &mut LanePoll<'_>,
    query: Option<crate::Cancellation<'_>>,
) -> Result<(), CmgError> {
    loop {
        if values.len() <= SMALL {
            return insertion(values, poll);
        }
        if depth == 0 {
            return heapsort(poll, values, compare_raw_edges, PHASE);
        }
        depth -= 1;
        let (less, greater) = partition(values, poll)?;
        let total = values.len();
        let (left, rest) = values.split_at_mut(less);
        let right = &mut rest[greater - less..];
        if lanes.count() > 1 && total >= FORK_MIN {
            let (a, b) = lanes.join(
                |lanes| sort_range(left, depth, lanes, &mut LanePoll::new(query), query),
                |lanes| sort_range(right, depth, lanes, &mut LanePoll::new(query), query),
            );
            return a.and(b);
        }
        // Recurse into the shorter side and continue with the longer one.
        if left.len() < right.len() {
            sort_range(left, depth, lanes.single(), poll, query)?;
            values = right;
        } else {
            sort_range(right, depth, lanes.single(), poll, query)?;
            values = left;
        }
    }
}

fn insertion(values: &mut [Edge], poll: &mut LanePoll<'_>) -> Result<(), CmgError> {
    for index in 1..values.len() {
        let mut current = index;
        while current > 0 && compare_raw_edges(&values[current], &values[current - 1]).is_lt() {
            values.swap(current, current - 1);
            current -= 1;
        }
        poll.advance(PHASE, 16 * (index - current + 1))?;
    }
    Ok(())
}

/// Dijkstra three-way partition around the median of the first, middle and
/// last records. Returns `(less, greater)`: `values[..less] < pivot`,
/// `values[less..greater] == pivot` and `values[greater..] > pivot`.
fn partition(values: &mut [Edge], poll: &mut LanePoll<'_>) -> Result<(usize, usize), CmgError> {
    let last = values.len() - 1;
    let middle = last / 2;
    let mut candidates = [values[0], values[middle], values[last]];
    candidates.sort_unstable_by(compare_raw_edges);
    let pivot = candidates[1];
    let (mut less, mut index, mut greater) = (0, 0, values.len());
    let mut steps = 0;
    while index < greater {
        match compare_raw_edges(&values[index], &pivot) {
            Ordering::Less => {
                values.swap(less, index);
                less += 1;
                index += 1;
            }
            Ordering::Greater => {
                greater -= 1;
                values.swap(index, greater);
            }
            Ordering::Equal => index += 1,
        }
        steps += 1;
        if steps == 64 {
            poll.advance(PHASE, 64 * 16)?;
            steps = 0;
        }
    }
    poll.advance(PHASE, 16 * steps.max(1))?;
    Ok((less, greater))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cancel::{CancelMeter, Unmetered};

    fn edges(count: usize, duplicates: usize) -> Vec<Edge> {
        (0..count)
            .map(|i| {
                let j = (i / duplicates) * 2_654_435_761 % 100_003;
                let u = (j % 997) as u32;
                Edge::from_compact_parts(u, u + 1 + (j % 13) as u32, 1.0 + (j % 5) as f64)
            })
            .collect()
    }

    #[test]
    fn team_sort_equals_the_standard_sort_at_every_ceiling() {
        for duplicates in [1, 7, 10_000] {
            let raw = edges(200_000, duplicates);
            let mut expected = raw.clone();
            expected.sort_by(compare_raw_edges);
            for threads in [1, 2, 3, 8] {
                let pool = rayon::ThreadPoolBuilder::new()
                    .num_threads(8)
                    .build()
                    .unwrap();
                let team = Team::new(&pool, threads, crate::ParallelOptions::default()).unwrap();
                let mut actual = raw.clone();
                sort(&mut actual, team, &mut Unmetered).unwrap();
                assert_eq!(
                    actual, expected,
                    "duplicates {duplicates} ceiling {threads}"
                );
            }
        }
    }

    #[test]
    fn team_sort_polls_inside_partitions_and_cancels() {
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(4)
            .build()
            .unwrap();
        let team = Team::new(&pool, 4, crate::ParallelOptions::default()).unwrap();
        let raw = edges(400_000, 3);
        let polls = std::sync::atomic::AtomicUsize::new(0);
        let count = || {
            polls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            false
        };
        let mut sorted = raw.clone();
        sort(&mut sorted, team, &mut CancelMeter::new(&count)).unwrap();
        // About 16 units per record per partition level: many lane polls.
        let total = polls.load(std::sync::atomic::Ordering::SeqCst);
        assert!(
            total > 400_000 * 16 / crate::cancel::POLL_UNITS as usize,
            "{total}"
        );
        for stop in [0, total / 3, total / 2] {
            let calls = std::sync::atomic::AtomicUsize::new(0);
            let cancel = || calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst) >= stop;
            let mut values = raw.clone();
            assert!(matches!(
                sort(&mut values, team, &mut CancelMeter::new(&cancel)),
                Err(CmgError::Cancelled { .. })
            ));
        }
        let mut again = raw;
        sort(&mut again, team, &mut Unmetered).unwrap();
        assert_eq!(again, sorted);
    }
}
