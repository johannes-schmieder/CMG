//! The one canonical edge sort: an in-place introsort by the total raw-edge
//! order, counted and polled, with no allocation. Serial construction sorts
//! on the caller's meter; with a team, partitions fork within the worker
//! ceiling and each lane polls independently.

use super::{Edge, compare_raw_edges};
use crate::CmgError;
#[cfg(feature = "parallel")]
use crate::Team;
#[cfg(feature = "parallel")]
use crate::cancel::LanePoll;
use crate::cancel::{Meter, Progress, heapsort};
#[cfg(feature = "parallel")]
use crate::execution::Lanes;
use core::cmp::Ordering;

/// Ranges at most this long are insertion-sorted.
const SMALL: usize = 16;
/// Partitions shorter than this are not forked.
#[cfg(feature = "parallel")]
const FORK_MIN: usize = 1 << 14;
#[cfg(feature = "parallel")]
const PHASE: &str = "team edge sort";

fn depth_limit(len: usize) -> usize {
    2 * (usize::BITS - len.leading_zeros()) as usize
}

/// Sort `values` by the total raw-edge order in place on the calling lane: an
/// introsort with three-way partitions around a median-of-three pivot, a
/// heapsort fallback at depth `2 log2(n)` and insertion sort below 17
/// records. Every partition step, insertion step and heap step is counted on
/// `meter`, which polls at its fixed cadence, inside a partition too. The
/// comparator is total and equal records are identical, so the order equals
/// every other sort's, independent of pivots. Nothing is allocated.
pub(crate) fn sort_serial<M: Meter>(
    values: &mut [Edge],
    meter: &mut M,
    phase: &'static str,
) -> Result<(), CmgError> {
    serial_range(values, depth_limit(values.len()), meter, phase)
}

fn serial_range<P: Progress>(
    mut values: &mut [Edge],
    mut depth: usize,
    poll: &mut P,
    phase: &'static str,
) -> Result<(), CmgError> {
    loop {
        if values.len() <= SMALL {
            return insertion(values, poll, phase);
        }
        if depth == 0 {
            return heapsort(poll, values, compare_raw_edges, phase);
        }
        depth -= 1;
        let (less, greater) = partition(values, poll, phase)?;
        let (left, rest) = values.split_at_mut(less);
        let right = &mut rest[greater - less..];
        // Recurse into the shorter side and continue with the longer one.
        if left.len() < right.len() {
            serial_range(left, depth, poll, phase)?;
            values = right;
        } else {
            serial_range(right, depth, poll, phase)?;
            values = left;
        }
    }
}

/// The same sort on a team: partitions of at least 16,384 records fork while
/// lanes remain, so at most the worker ceiling of lanes run, and every started
/// lane joins before the meter's final poll. Each logical node of the
/// partition tree (a partition, an insertion leaf or a heapsort fallback)
/// polls once on entry with its own lane counter and then at the fixed cadence.
/// The tree depends only on the input, so the poll schedule is the same at
/// every worker ceiling, and work between two polls of a lane stays bounded.
#[cfg(feature = "parallel")]
pub(crate) fn sort<M: Meter>(
    values: &mut [Edge],
    team: Team<'_>,
    meter: &mut M,
) -> Result<(), CmgError> {
    let query = meter.query();
    let depth = depth_limit(values.len());
    let sorted = team.with_lanes(|lanes| team_range(values, depth, lanes, query));
    meter.joined(PHASE)?;
    sorted
}

#[cfg(feature = "parallel")]
fn team_range(
    mut values: &mut [Edge],
    mut depth: usize,
    lanes: Lanes<'_>,
    query: Option<crate::Cancellation<'_>>,
) -> Result<(), CmgError> {
    loop {
        let mut node = LanePoll::new(query);
        node.advance(PHASE, crate::cancel::POLL_UNITS as usize)?;
        if values.len() <= SMALL {
            return insertion(values, &mut node, PHASE);
        }
        if depth == 0 {
            return heapsort(&mut node, values, compare_raw_edges, PHASE);
        }
        depth -= 1;
        let (less, greater) = partition(values, &mut node, PHASE)?;
        let total = values.len();
        let (left, rest) = values.split_at_mut(less);
        let right = &mut rest[greater - less..];
        if lanes.count() > 1 && total >= FORK_MIN {
            let (a, b) = lanes.join(
                |lanes| team_range(left, depth, lanes, query),
                |lanes| team_range(right, depth, lanes, query),
            );
            return a.and(b);
        }
        // Recurse into the shorter side and continue with the longer one.
        if left.len() < right.len() {
            team_range(left, depth, lanes.single(), query)?;
            values = right;
        } else {
            team_range(right, depth, lanes.single(), query)?;
            values = left;
        }
    }
}

fn insertion<P: Progress>(
    values: &mut [Edge],
    poll: &mut P,
    phase: &'static str,
) -> Result<(), CmgError> {
    for index in 1..values.len() {
        let mut current = index;
        while current > 0 && compare_raw_edges(&values[current], &values[current - 1]).is_lt() {
            values.swap(current, current - 1);
            current -= 1;
        }
        poll.advance(phase, 16 * (index - current + 1))?;
    }
    Ok(())
}

/// Dijkstra three-way partition around the median of the first, middle and
/// last records. Returns `(less, greater)`: `values[..less] < pivot`,
/// `values[less..greater] == pivot` and `values[greater..] > pivot`.
fn partition<P: Progress>(
    values: &mut [Edge],
    poll: &mut P,
    phase: &'static str,
) -> Result<(usize, usize), CmgError> {
    let last = values.len() - 1;
    let middle = last / 2;
    let mut candidates = [values[0], values[middle], values[last]];
    candidates.sort_unstable_by(compare_raw_edges);
    let pivot = candidates[1];
    let (mut less, mut index, mut greater) = (0, 0, values.len());
    let mut steps = 0;
    while index < greater {
        #[cfg(all(test, feature = "parallel"))]
        crate::execution::visits::visit_within(core::ptr::from_ref(&values[index]).cast());
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
            poll.advance(phase, 64 * 16)?;
            steps = 0;
        }
    }
    poll.advance(phase, 16 * steps.max(1))?;
    Ok((less, greater))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cancel::{CancelMeter, Unmetered};

    /// Edges with a deterministic key pattern and `duplicates` copies of each
    /// key run; weights cycle through ties, signed zeros and a subnormal so the
    /// total weight order is exercised inside equal endpoint groups.
    fn edges(count: usize, duplicates: usize) -> Vec<Edge> {
        let weights = [1.0, -0.0, 0.0, f64::from_bits(1), 3.5, 1.0, 1e300];
        (0..count)
            .map(|i| {
                let j = (i / duplicates) * 2_654_435_761 % 100_003;
                let u = (j % 997) as u32;
                Edge::from_compact_parts(u, u + 1 + (j % 13) as u32, weights[i % weights.len()])
            })
            .collect()
    }

    fn key(rank: usize) -> Edge {
        let rank = rank as u32;
        Edge::from_compact_parts(rank / 4093, 4093 + rank % 4093, 1.0)
    }

    /// Inputs that stress partitioning: random with few to many duplicates,
    /// already sorted, reversed, all equal, two values, organ pipe, sawtooth
    /// and Musser's median-of-three killer.
    fn inputs(count: usize) -> Vec<(&'static str, Vec<Edge>)> {
        let mut sorted = edges(count, 1);
        sorted.sort_by(compare_raw_edges);
        let reversed: Vec<Edge> = sorted.iter().rev().copied().collect();
        let half = count / 2;
        let organ: Vec<Edge> = (0..count)
            .map(|i| key(if i < half { i } else { count - i }))
            .collect();
        let sawtooth: Vec<Edge> = (0..count).map(|i| key(i % 97)).collect();
        // Median-of-three killer (Musser): pairs pivot candidates so every
        // median is the second smallest, driving naive quicksort quadratic.
        let k = count / 2;
        let mut killer = vec![key(0); count];
        for i in 0..k {
            if i % 2 == 0 {
                killer[i] = key(i + 1);
                killer[i + 1] = key(k + i + 1);
            }
            killer[k + i] = key(2 * (i + 1));
        }
        vec![
            ("random", edges(count, 1)),
            ("random-dup7", edges(count, 7)),
            ("random-dup-all", edges(count, count)),
            ("sorted", sorted),
            ("reversed", reversed),
            ("all-equal", vec![key(5); count]),
            ("two-values", (0..count).map(|i| key(i % 2)).collect()),
            ("organ-pipe", organ),
            ("sawtooth", sawtooth),
            ("median-of-three-killer", killer),
        ]
    }

    #[test]
    fn serial_sort_equals_the_standard_sort_on_adversarial_inputs() {
        for count in [0, 1, 2, 16, 17, 1_000, 50_001] {
            for (name, raw) in inputs(count) {
                let mut expected = raw.clone();
                expected.sort_by(compare_raw_edges);
                let mut actual = raw.clone();
                sort_serial(&mut actual, &mut Unmetered, "sort").unwrap();
                assert_eq!(actual, expected, "{name} {count}");
                let never = || false;
                let mut polled = raw;
                sort_serial(&mut polled, &mut CancelMeter::new(&never), "sort").unwrap();
                assert_eq!(polled, expected, "{name} {count} polled");
            }
        }
        // Signed zeros order by total_cmp inside one endpoint group.
        let mut zeros = vec![
            Edge::from_compact_parts(0, 1, 0.0),
            Edge::from_compact_parts(0, 1, -0.0),
        ];
        sort_serial(&mut zeros, &mut Unmetered, "sort").unwrap();
        assert_eq!(zeros[0].weight().to_bits(), (-0.0_f64).to_bits());
    }

    #[cfg(feature = "parallel")]
    #[test]
    fn team_sort_equals_the_standard_sort_at_every_ceiling() {
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(8)
            .build()
            .unwrap();
        for (name, raw) in inputs(200_000) {
            let mut expected = raw.clone();
            expected.sort_by(compare_raw_edges);
            for threads in [1, 2, 3, 8] {
                let team = Team::new(&pool, threads, crate::ParallelOptions::default()).unwrap();
                let mut actual = raw.clone();
                sort(&mut actual, team, &mut Unmetered).unwrap();
                assert_eq!(actual, expected, "{name} ceiling {threads}");
            }
        }
    }

    /// Cancellation observed inside the first partition of a 400,000-record
    /// sort stops it there: after the observing poll the serial sort visits
    /// nothing more and each team lane at most one poll interval of partition
    /// steps (4,096) plus one 64-step segment; untouched records are never
    /// partitioned, every lane has joined, and a fresh sort is exact.
    #[cfg(feature = "parallel")]
    #[test]
    fn sorts_cancel_inside_a_partition_and_stop_untouched_work() {
        use crate::execution::{gauge, visits};
        use std::sync::atomic::{AtomicUsize, Ordering};
        let _witness = gauge::WITNESS
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(4)
            .build()
            .unwrap();
        let raw = edges(400_000, 3);
        let mut expected = raw.clone();
        expected.sort_by(compare_raw_edges);
        let interval = crate::cancel::POLL_UNITS as usize / 16;
        for ceiling in [0, 1, 4] {
            for stop in [0, 1, 2] {
                let calls = AtomicUsize::new(0);
                let at = AtomicUsize::new(usize::MAX);
                let query = || {
                    if calls.fetch_add(1, Ordering::SeqCst) < stop {
                        return false;
                    }
                    let _ = at.compare_exchange(
                        usize::MAX,
                        visits::count(),
                        Ordering::SeqCst,
                        Ordering::SeqCst,
                    );
                    true
                };
                let mut values = raw.clone();
                visits::watch_range(&values);
                let result = if ceiling == 0 {
                    sort_serial(&mut values, &mut CancelMeter::new(&query), "sort")
                } else {
                    let team =
                        Team::new(&pool, ceiling, crate::ParallelOptions::default()).unwrap();
                    sort(&mut values, team, &mut CancelMeter::new(&query))
                };
                let total = visits::count();
                visits::unwatch();
                let label = format!("ceiling {ceiling}, stop {stop}");
                assert!(matches!(result, Err(CmgError::Cancelled { .. })), "{label}");
                let settled = calls.load(Ordering::SeqCst);
                std::thread::sleep(std::time::Duration::from_millis(5));
                assert_eq!(
                    calls.load(Ordering::SeqCst),
                    settled,
                    "{label}: lanes joined"
                );
                let at = at.load(Ordering::SeqCst);
                let lanes = ceiling.max(1);
                assert!(
                    total - at <= lanes * (interval + 64),
                    "{label}: {} steps after the observation",
                    total - at
                );
                // The first partition alone takes 400,000 steps.
                assert!(total < raw.len(), "{label}: {total} steps");
            }
        }
        let mut again = raw;
        sort_serial(&mut again, &mut CancelMeter::new(&|| false), "sort").unwrap();
        assert_eq!(again, expected);
    }

    /// Work between two consecutive polls stays bounded on every path: at
    /// most one cadence (4,096 partition steps) plus one 64-step segment per
    /// lane, also across the many small nodes deep in the partition tree.
    #[cfg(feature = "parallel")]
    #[test]
    fn work_between_polls_is_bounded_deep_in_the_partition_tree() {
        use crate::execution::{gauge, visits};
        use std::sync::atomic::{AtomicUsize, Ordering};
        let _witness = gauge::WITNESS
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(4)
            .build()
            .unwrap();
        let raw = edges(1 << 20, 1);
        let interval = crate::cancel::POLL_UNITS as usize / 16;
        for ceiling in [0, 1, 4] {
            let last = AtomicUsize::new(0);
            let widest = AtomicUsize::new(0);
            let query = || {
                let now = visits::count();
                let previous = last.swap(now, Ordering::SeqCst);
                widest.fetch_max(now.saturating_sub(previous), Ordering::SeqCst);
                false
            };
            let mut values = raw.clone();
            visits::watch_range(&values);
            if ceiling == 0 {
                sort_serial(&mut values, &mut CancelMeter::new(&query), "sort").unwrap();
            } else {
                let team = Team::new(&pool, ceiling, crate::ParallelOptions::default()).unwrap();
                sort(&mut values, team, &mut CancelMeter::new(&query)).unwrap();
            }
            let total = visits::count();
            visits::unwatch();
            let gap = widest
                .load(Ordering::SeqCst)
                .max(total - last.load(Ordering::SeqCst));
            assert!(
                gap <= ceiling.max(1) * (interval + 64),
                "ceiling {ceiling}: {gap} partition steps between polls"
            );
            assert!(total > 16 * raw.len(), "{total} partition steps");
        }
    }

    #[cfg(feature = "parallel")]
    #[test]
    fn team_sort_polls_inside_partitions_and_cancels() {
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(8)
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
        // Every logical node polls on entry with its own counter, so the
        // schedule depends on the input only, never on the ceiling.
        for threads in [1, 2, 3, 8] {
            let team = Team::new(&pool, threads, crate::ParallelOptions::default()).unwrap();
            let polls = std::sync::atomic::AtomicUsize::new(0);
            let count = || {
                polls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                false
            };
            let mut values = raw.clone();
            sort(&mut values, team, &mut CancelMeter::new(&count)).unwrap();
            assert_eq!(values, sorted, "ceiling {threads}");
            assert_eq!(
                polls.load(std::sync::atomic::Ordering::SeqCst),
                total,
                "ceiling {threads}"
            );
        }
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
