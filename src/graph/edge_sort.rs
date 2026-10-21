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

/// Test-only probe: the partition this thread is running, as (classified
/// records, length), so a witness can fire cancellation at a poll inside
/// actual partition work and see what that partition leaves untouched.
#[cfg(all(test, feature = "parallel"))]
mod probe {
    use core::cell::Cell;

    std::thread_local! {
        static PARTITION: Cell<(usize, usize)> = const { Cell::new((0, 0)) };
    }

    pub(super) struct Partitioning((usize, usize));

    pub(super) fn enter(len: usize) -> Partitioning {
        Partitioning(PARTITION.with(|state| state.replace((0, len))))
    }

    impl Drop for Partitioning {
        fn drop(&mut self) {
            PARTITION.with(|state| state.set(self.0));
        }
    }

    pub(super) fn step() {
        PARTITION.with(|state| state.set((state.get().0 + 1, state.get().1)));
    }

    /// Records the running partition has not classified yet, if any runs.
    pub(super) fn remaining() -> Option<usize> {
        let (classified, len) = PARTITION.with(Cell::get);
        (len > 0).then(|| len - classified)
    }
}

fn depth_limit(len: usize) -> usize {
    2 * (usize::BITS - len.leading_zeros()) as usize
}

/// Sort `values` by the total raw-edge order in place on the calling lane: an
/// introsort with three-way partitions around a median-of-three pivot, a
/// heapsort fallback at depth `2 log2(n)` and insertion sort below 17
/// records. Every partition step, insertion step and heap step is counted on
/// `meter`: a partition advances it by 1,024 units every 64 steps, and the
/// meter queries every 65,536 units (about 4,096 steps), inside a partition
/// too. The comparator is total and equal records are identical, so the order
/// equals every other sort's, independent of pivots. Nothing is allocated.
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
/// queries once on entry with its own lane counter and then at the fixed
/// cadence (every 65,536 counted units, about 4,096 partition steps). The tree
/// depends only on the input, so a successful run's query total and each
/// node's logical checkpoints are the same at every worker ceiling, and work
/// between two queries of a lane stays bounded. The chronological order of
/// queries across lanes, and where a cancellation stops, are not fixed.
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
    #[cfg(all(test, feature = "parallel"))]
    let _probe = probe::enter(values.len());
    let last = values.len() - 1;
    let middle = last / 2;
    let mut candidates = [values[0], values[middle], values[last]];
    candidates.sort_unstable_by(compare_raw_edges);
    let pivot = candidates[1];
    let (mut less, mut index, mut greater) = (0, 0, values.len());
    let mut steps = 0;
    while index < greater {
        #[cfg(all(test, feature = "parallel"))]
        {
            crate::execution::visits::visit_within(core::ptr::from_ref(&values[index]).cast());
            probe::step();
        }
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

    /// Endpoints and weight bits: `Edge`'s `PartialEq` equates +0 and -0,
    /// so every order witness compares these instead.
    fn bits(values: &[Edge]) -> Vec<(usize, usize, u64)> {
        values
            .iter()
            .map(|edge| (edge.u(), edge.v(), edge.weight().to_bits()))
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
            // Duplicate endpoint groups whose weights differ only in the
            // sign of zero, interleaved with equal duplicates.
            (
                "signed-zero-groups",
                (0..count)
                    .map(|i| {
                        let u = ((i / 2) % 5) as u32;
                        Edge::from_compact_parts(u, 7, [0.0, -0.0, -0.0, 0.0, 1.0][i % 5])
                    })
                    .collect(),
            ),
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
                assert_eq!(bits(&actual), bits(&expected), "{name} {count}");
                let never = || false;
                let mut polled = raw;
                sort_serial(&mut polled, &mut CancelMeter::new(&never), "sort").unwrap();
                assert_eq!(bits(&polled), bits(&expected), "{name} {count} polled");
            }
        }
        // Signed zeros order by total_cmp inside one endpoint group.
        let mut zeros = vec![
            Edge::from_compact_parts(0, 1, 0.0),
            Edge::from_compact_parts(0, 1, -0.0),
        ];
        sort_serial(&mut zeros, &mut Unmetered, "sort").unwrap();
        assert_eq!(zeros[0].weight().to_bits(), (-0.0_f64).to_bits());
        // The bitwise comparison discriminates what `PartialEq` cannot.
        let swapped = [zeros[1], zeros[0]];
        assert_eq!(swapped, *zeros);
        assert_ne!(bits(&swapped), bits(&zeros));
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
                assert_eq!(bits(&actual), bits(&expected), "{name} ceiling {threads}");
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
        assert_eq!(bits(&again), bits(&expected));
    }

    /// Cancellation after the fork, inside started concurrent lanes. A query
    /// parks every lane at its first poll inside actual partition work past
    /// the root partition (at the W4 ceiling, the four one-lane leaves after
    /// the second fork) until all `ceiling` lanes have arrived on distinct
    /// threads, then fires. Parking is a deterministic barrier with a bounded
    /// timeout that fails the witness instead of hanging; success depends on
    /// no sleep or schedule. Every lane stops at its observing poll: no record
    /// is visited and no later node polls after the firing, all lanes have
    /// joined on return, nothing moves afterwards, and fresh sorts are exact.
    #[cfg(feature = "parallel")]
    #[test]
    fn started_lanes_cancel_inside_partitions_after_the_fork() {
        use crate::execution::{gauge, visits};
        use std::collections::BTreeSet;
        use std::sync::atomic::{AtomicUsize, Ordering};
        use std::sync::{Condvar, Mutex};
        use std::time::{Duration, Instant};
        #[derive(Default)]
        struct Barrier {
            threads: BTreeSet<String>,
            fired: bool,
            timed_out: bool,
            visits_at_fire: usize,
            active_at_fire: usize,
            calls_at_fire: usize,
            untouched: usize,
        }
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
        for ceiling in [4, 1] {
            let label = format!("ceiling {ceiling}");
            let barrier = Mutex::new(Barrier::default());
            let arrived = Condvar::new();
            let calls = AtomicUsize::new(0);
            let query = || {
                let call = calls.fetch_add(1, Ordering::SeqCst);
                let mut state = barrier.lock().unwrap();
                if state.fired {
                    return true;
                }
                // Only polls in the middle of a partition (records left to
                // classify) of a one-lane leaf, after the root partition
                // (400,000 steps) has completed.
                let remaining = probe::remaining().unwrap_or(0);
                if !(gauge::inside() && remaining > 0 && visits::count() >= raw.len()) {
                    return false;
                }
                state.untouched += remaining;
                state
                    .threads
                    .insert(format!("{:?}", std::thread::current().id()));
                if state.threads.len() == ceiling {
                    state.fired = true;
                    state.visits_at_fire = visits::count();
                    state.active_at_fire = gauge::active();
                    state.calls_at_fire = call + 1;
                    arrived.notify_all();
                    return true;
                }
                let deadline = Instant::now() + Duration::from_secs(30);
                while !state.fired {
                    let now = Instant::now();
                    if now >= deadline {
                        // Escape: stop every lane and fail after the join.
                        state.timed_out = true;
                        state.fired = true;
                        arrived.notify_all();
                        break;
                    }
                    state = arrived.wait_timeout(state, deadline - now).unwrap().0;
                }
                true
            };
            gauge::watch(&pool);
            let mut values = raw.clone();
            visits::watch_range(&values);
            let team = Team::new(&pool, ceiling, crate::ParallelOptions::default()).unwrap();
            let result = sort(&mut values, team, &mut CancelMeter::new(&query));
            let (total, returned_calls, active) = (
                visits::count(),
                calls.load(Ordering::SeqCst),
                gauge::active(),
            );
            // Nothing runs after the return (a sleep cannot make this pass
            // falsely for a correct join; it only gives a stray lane time).
            std::thread::sleep(Duration::from_millis(20));
            let (later_total, later_calls) = (visits::count(), calls.load(Ordering::SeqCst));
            visits::unwatch();
            let state = barrier.into_inner().unwrap();
            assert!(!state.timed_out, "{label}: lanes never all arrived");
            assert!(
                matches!(result, Err(CmgError::Cancelled { phase: PHASE })),
                "{label}: {result:?}"
            );
            // Distinct threads were parked inside partitions at once.
            assert_eq!(state.threads.len(), ceiling, "{label}");
            assert_eq!(state.active_at_fire, ceiling, "{label}: concurrent lanes");
            assert_eq!(gauge::max(), ceiling, "{label}: lane ceiling");
            // No partition step after the observation: each lane stopped at
            // its observing poll, so later work stayed untouched.
            assert_eq!(total, state.visits_at_fire, "{label}: steps after firing");
            assert!(state.untouched >= ceiling, "{label}: parked mid-partition");
            assert!(total < 3 * raw.len(), "{label}: {total} steps");
            // The only query after the firing is the coordinator's final
            // poll after the join: no lane reached another node.
            assert_eq!(
                returned_calls,
                state.calls_at_fire + 1,
                "{label}: polls after firing"
            );
            assert_eq!(active, 0, "{label}: every lane joined");
            assert_eq!(
                (later_total, later_calls),
                (total, returned_calls),
                "{label}"
            );
            // Fresh exact sorts on the same team and serially.
            let mut again = raw.clone();
            sort(&mut again, team, &mut CancelMeter::new(&|| false)).unwrap();
            assert_eq!(bits(&again), bits(&expected), "{label}: fresh team sort");
            let mut serial = raw.clone();
            sort_serial(&mut serial, &mut Unmetered, "sort").unwrap();
            assert_eq!(bits(&serial), bits(&expected), "{label}: fresh serial sort");
        }
    }

    /// Work between two consecutive polls stays bounded on every path: at
    /// most one cadence (4,096 partition steps) plus one 64-step segment per
    /// lane, also across the many small nodes deep in the partition tree.
    /// Each query reads the step counter and records the gap under one lock,
    /// so consecutive reads are ordered: a lane preempted between reading and
    /// recording cannot store a stale reading that inflates the next gap.
    #[cfg(feature = "parallel")]
    #[test]
    fn work_between_polls_is_bounded_deep_in_the_partition_tree() {
        use crate::execution::{gauge, visits};
        use std::sync::Mutex;
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
            // (last reading, widest gap)
            let state = Mutex::new((0_usize, 0_usize));
            let query = || {
                let mut state = state.lock().unwrap();
                let now = visits::count();
                state.1 = state.1.max(now - state.0);
                state.0 = now;
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
            let (last, widest) = state.into_inner().unwrap();
            let gap = widest.max(total - last);
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
            assert_eq!(bits(&values), bits(&sorted), "ceiling {threads}");
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
        assert_eq!(bits(&again), bits(&sorted));
    }
}
