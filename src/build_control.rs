//! Cooperative serial construction with deterministic, precharged boundaries.

use crate::CmgError;

/// A caller's decision before the proposed construction operation starts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BuildDecision {
    /// Admit the proposed operation.
    Continue,
    /// Discard the candidate because its result is no longer needed.
    StopUnused,
    /// Interrupt the estimation that owns this candidate.
    Interrupted,
}

/// A separately gated construction operation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BuildOperation {
    /// Primitive arithmetic, record visit, comparison and move allowance.
    Work {
        /// Conservative allowance; `1..=1024` by default, up to 65535
        /// only when the caller opts into cooperative batching.
        units: u16,
    },
    /// Allocator operation, whose elapsed time is not computationally bounded.
    Allocation {
        /// Requested payload/layout bytes, excluding allocator overhead.
        bytes: usize,
    },
}

/// A deterministic boundary observed before its proposed work or allocation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BuildBoundary {
    /// Stable source phase name.
    pub phase: &'static str,
    /// Zero-based operation ordinal, including allocation gates.
    pub chunk_id: u64,
    /// Previously admitted computational allowance.
    pub charged_work: u64,
    /// Operation that has not yet started.
    pub operation: BuildOperation,
}

/// Synchronous admission callback for controlled serial construction.
///
/// A callback may block to park the builder and return after a grant or drain
/// decision. It runs on the constructing thread; no executor or pool is created.
/// A stop unwinds through ordinary `Result` returns and drops private partial
/// state. Allocator/deallocator and operating-system latency are not bounded.
pub trait BuildControl {
    /// Opt into coarse cancellation-only work admission. Zero (the default)
    /// preserves every legacy boundary. Nonzero values are clamped to at least
    /// 1024, with a maximum of 65535. A chunk is prepaid before its first
    /// operation and may span source phases; unused allowance remains charged.
    /// Allocation callbacks still occur immediately before allocations. The
    /// computational cancellation allowance is this batch size, not 1024.
    /// This mode is not the primitive-frontier/BuildPrefix scheduling contract.
    fn cooperative_work_batch(&self) -> u16 {
        0
    }

    /// Opt into groups of at most eight ordered dense-factor update terms.
    /// Each term retains its 128-unit tariff, so one complete group is prepaid
    /// with at most 1024 units before arithmetic starts. Allocation gates and
    /// arithmetic order are unchanged. The default preserves primitive frontiers.
    /// This preference is sampled once per controlled entrypoint.
    fn group_dense_work(&self) -> bool {
        false
    }

    /// Decide whether the operation described by `boundary` may begin.
    fn checkpoint(&mut self, boundary: BuildBoundary) -> BuildDecision;
}

impl<F: FnMut(BuildBoundary) -> BuildDecision> BuildControl for F {
    fn checkpoint(&mut self, boundary: BuildBoundary) -> BuildDecision {
        self(boundary)
    }
}

/// Why a controlled build did not publish its candidate.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BuildStopReason {
    /// The caller no longer needs this candidate.
    StopUnused,
    /// The caller requested interruption.
    Interrupted,
    /// The next complete chunk would exceed the checked work budget.
    WorkBudgetExhausted,
    /// A boundary or work counter could not be incremented safely.
    CounterOverflow,
}

pub(crate) struct BuildMeter<'a> {
    control: &'a mut dyn BuildControl,
    budget: u64,
    charged: u64,
    ordinal: u64,
    batch: u16,
    available: u16,
    #[cfg(feature = "experimental-components")]
    dense_chunk_len: usize,
}

impl<'a> BuildMeter<'a> {
    pub(crate) fn new(budget: u64, control: &'a mut dyn BuildControl) -> Self {
        let requested = control.cooperative_work_batch();
        let batch = if requested == 0 {
            0
        } else {
            requested.max(1024)
        };
        #[cfg(feature = "experimental-components")]
        let dense_chunk_len = if batch != 0 {
            usize::from(batch / 128)
        } else if control.group_dense_work() {
            8
        } else {
            1
        };
        Self {
            control,
            budget,
            charged: 0,
            ordinal: 0,
            batch,
            available: 0,
            #[cfg(feature = "experimental-components")]
            dense_chunk_len,
        }
    }

    #[cfg(feature = "experimental-components")]
    pub(crate) fn dense_chunk_len(&self) -> usize {
        self.dense_chunk_len
    }

    pub(crate) fn stopped(&self, phase: &'static str, reason: BuildStopReason) -> CmgError {
        CmgError::BuildStopped {
            reason,
            phase,
            chunk_id: self.ordinal,
            charged_work: self.charged,
        }
    }

    fn gate(&mut self, phase: &'static str, operation: BuildOperation) -> Result<(), CmgError> {
        let next = self
            .ordinal
            .checked_add(1)
            .ok_or_else(|| self.stopped(phase, BuildStopReason::CounterOverflow))?;
        let units = match operation {
            BuildOperation::Work { units } => u64::from(units),
            _ => 0,
        };
        let charged = self
            .charged
            .checked_add(units)
            .ok_or_else(|| self.stopped(phase, BuildStopReason::CounterOverflow))?;
        if charged > self.budget {
            return Err(self.stopped(phase, BuildStopReason::WorkBudgetExhausted));
        }
        match self.control.checkpoint(BuildBoundary {
            phase,
            chunk_id: self.ordinal,
            charged_work: self.charged,
            operation,
        }) {
            BuildDecision::Continue => {}
            BuildDecision::StopUnused => {
                return Err(self.stopped(phase, BuildStopReason::StopUnused));
            }
            BuildDecision::Interrupted => {
                return Err(self.stopped(phase, BuildStopReason::Interrupted));
            }
        }
        self.ordinal = next;
        self.charged = charged;
        Ok(())
    }

    #[inline(always)]
    pub(crate) fn work(&mut self, phase: &'static str, units: u16) -> Result<(), CmgError> {
        // Credit is always <= batch, and primitive mode retains zero credit.
        // A nonzero funded operation therefore already satisfies admission and
        // maximum-unit checks; consuming it changes no boundary or counter.
        if units != 0 && units <= self.available {
            self.available -= units;
            return Ok(());
        }
        self.work_slow(phase, units)
    }

    #[inline(never)]
    fn work_slow(&mut self, phase: &'static str, units: u16) -> Result<(), CmgError> {
        let maximum = if self.batch == 0 { 1024 } else { self.batch };
        if units == 0 || units > maximum {
            return Err(CmgError::InvalidHierarchy {
                context: "invalid controlled build chunk allowance",
            });
        }
        if self.batch == 0 {
            return self.gate(phase, BuildOperation::Work { units });
        }
        if self.available < units {
            let remaining = self.budget - self.charged;
            let grant = u64::from(self.batch - self.available).min(remaining) as u16;
            if self.available.saturating_add(grant) < units {
                return Err(self.stopped(phase, BuildStopReason::WorkBudgetExhausted));
            }
            self.gate(phase, BuildOperation::Work { units: grant })?;
            self.available += grant;
        }
        self.available -= units;
        Ok(())
    }

    pub(crate) fn allocation(&mut self, phase: &'static str, bytes: usize) -> Result<(), CmgError> {
        self.gate(phase, BuildOperation::Allocation { bytes })
    }

    pub(crate) fn try_capacity<T>(
        &mut self,
        count: usize,
        phase: &'static str,
    ) -> Result<Vec<T>, CmgError> {
        self.work(phase, 16)?;
        self.allocation(phase, crate::storage::bytes::<T>(count)?)?;
        crate::storage::try_capacity(count, phase)
    }

    #[cfg(feature = "experimental-components")]
    pub(crate) fn try_filled<T: Copy>(
        &mut self,
        count: usize,
        value: T,
        phase: &'static str,
    ) -> Result<Vec<T>, CmgError> {
        let mut result = self.try_capacity(count, phase)?;
        for _ in 0..count {
            self.work(phase, 8)?;
            result.push(value);
        }
        Ok(result)
    }

    #[cfg(feature = "experimental-components")]
    pub(crate) fn copy_slice<T: Copy>(
        &mut self,
        values: &[T],
        phase: &'static str,
    ) -> Result<Vec<T>, CmgError> {
        let mut result = self.try_capacity(values.len(), phase)?;
        for &value in values {
            self.work(phase, 8)?;
            result.push(value);
        }
        Ok(result)
    }

    // Allocation-free heapsort. Each comparison and bounded swap is precharged.
    // Callers use constant-work comparators and scalar/record Copy payloads.
    pub(crate) fn sort_by<T: Copy>(
        &mut self,
        values: &mut [T],
        mut compare: impl FnMut(&T, &T) -> core::cmp::Ordering,
        phase: &'static str,
    ) -> Result<(), CmgError> {
        if self.batch != 0 {
            let depth = 2 * (usize::BITS - values.len().max(1).leading_zeros()) as usize;
            return self.introsort(values, &mut compare, phase, depth);
        }
        self.heapsort(values, &mut compare, phase)
    }

    fn heapsort<T: Copy>(
        &mut self,
        values: &mut [T],
        mut compare: impl FnMut(&T, &T) -> core::cmp::Ordering,
        phase: &'static str,
    ) -> Result<(), CmgError> {
        fn sift<T: Copy>(
            meter: &mut BuildMeter<'_>,
            values: &mut [T],
            mut root: usize,
            end: usize,
            compare: &mut impl FnMut(&T, &T) -> core::cmp::Ordering,
            phase: &'static str,
        ) -> Result<(), CmgError> {
            loop {
                meter.work(phase, 64)?;
                // root < end and slice lengths cannot exceed isize::MAX bytes.
                let child = root * 2 + 1;
                if child >= end {
                    return Ok(());
                }
                let mut largest = child;
                if child + 1 < end && compare(&values[child], &values[child + 1]).is_lt() {
                    largest += 1;
                }
                if !compare(&values[root], &values[largest]).is_lt() {
                    return Ok(());
                }
                values.swap(root, largest);
                root = largest;
            }
        }
        self.work(phase, 16)?;
        for root in (0..values.len() / 2).rev() {
            sift(self, values, root, values.len(), &mut compare, phase)?;
        }
        for end in (1..values.len()).rev() {
            self.work(phase, 16)?;
            values.swap(0, end);
            sift(self, values, 0, end, &mut compare, phase)?;
        }
        Ok(())
    }
    fn introsort<T: Copy>(
        &mut self,
        mut values: &mut [T],
        compare: &mut impl FnMut(&T, &T) -> core::cmp::Ordering,
        phase: &'static str,
        mut depth: usize,
    ) -> Result<(), CmgError> {
        while values.len() > 16 {
            if depth == 0 {
                return self.heapsort(values, compare, phase);
            }
            depth -= 1;
            self.work(phase, 128)?;
            let mut pivot = [
                values[0],
                values[values.len() / 2],
                values[values.len() - 1],
            ];
            if compare(&pivot[0], &pivot[1]).is_gt() {
                pivot.swap(0, 1);
            }
            if compare(&pivot[1], &pivot[2]).is_gt() {
                pivot.swap(1, 2);
            }
            if compare(&pivot[0], &pivot[1]).is_gt() {
                pivot.swap(0, 1);
            }
            let pivot = pivot[1];
            let mut left = 0;
            let mut right = values.len();
            loop {
                while left < right {
                    self.work(phase, 64)?;
                    if !compare(&values[left], &pivot).is_lt() {
                        break;
                    }
                    left += 1;
                }
                while left < right {
                    self.work(phase, 64)?;
                    if !compare(&values[right - 1], &pivot).is_gt() {
                        break;
                    }
                    right -= 1;
                }
                if left >= right {
                    break;
                }
                self.work(phase, 32)?;
                values.swap(left, right - 1);
                left += 1;
                right -= 1;
            }
            let split = left;
            // Defensive no-progress fallback; the median pivot normally prevents it.
            if split == 0 || split == values.len() {
                return self.heapsort(values, compare, phase);
            }
            let (a, b) = values.split_at_mut(split);
            if a.len() < b.len() {
                self.introsort(a, compare, phase, depth)?;
                values = b;
            } else {
                self.introsort(b, compare, phase, depth)?;
                values = a;
            }
        }
        for i in 1..values.len() {
            let mut j = i;
            while j > 0 {
                self.work(phase, 64)?;
                if !compare(&values[j], &values[j - 1]).is_lt() {
                    break;
                }
                values.swap(j, j - 1);
                j -= 1;
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod cooperative_tests {
    use super::*;
    struct Observer {
        boundaries: Vec<BuildBoundary>,
        stop: Option<usize>,
        batch: u16,
    }
    impl BuildControl for Observer {
        fn cooperative_work_batch(&self) -> u16 {
            self.batch
        }
        fn checkpoint(&mut self, b: BuildBoundary) -> BuildDecision {
            self.boundaries.push(b);
            if self.stop == Some(self.boundaries.len() - 1) {
                BuildDecision::Interrupted
            } else {
                BuildDecision::Continue
            }
        }
    }
    #[test]
    fn funded_fast_path_preserves_every_boundary_error_and_counter() {
        struct Trace {
            batch: u16,
            stop: Option<(usize, BuildDecision)>,
            boundaries: Vec<BuildBoundary>,
        }
        impl BuildControl for Trace {
            fn cooperative_work_batch(&self) -> u16 {
                self.batch
            }
            fn checkpoint(&mut self, b: BuildBoundary) -> BuildDecision {
                self.boundaries.push(b);
                self.stop
                    .filter(|(i, _)| *i + 1 == self.boundaries.len())
                    .map_or(BuildDecision::Continue, |(_, decision)| decision)
            }
        }
        for batch in [0, 1024, u16::MAX] {
            for budget in [0, 150, 1024, 65535, 65700, u64::MAX] {
                for decision in [BuildDecision::Interrupted, BuildDecision::StopUnused] {
                    for stop in [None, Some(0), Some(1), Some(3)] {
                        let trace = || Trace {
                            batch,
                            stop: stop.map(|i| (i, decision)),
                            boundaries: vec![],
                        };
                        let mut actual_control = trace();
                        let mut expected_control = trace();
                        {
                            let mut actual = BuildMeter::new(budget, &mut actual_control);
                            let mut expected = BuildMeter::new(budget, &mut expected_control);
                            for units in [1, 0, 128, 0, u16::MAX, 22, 8, 1024, 65000, 500, 1] {
                                // work_slow is the unchanged pre-optimization body.
                                let a = actual.work("trace", units);
                                let b = expected.work_slow("trace", units);
                                assert_eq!(a, b);
                                assert_eq!(
                                    (actual.available, actual.charged, actual.ordinal),
                                    (expected.available, expected.charged, expected.ordinal)
                                );
                                assert_eq!(
                                    actual.allocation("allocation", 32),
                                    expected.allocation("allocation", 32)
                                );
                            }
                        }
                        assert_eq!(actual_control.boundaries, expected_control.boundaries);
                    }
                }
            }
        }
    }
    #[test]
    fn prepaid_reservoir_handles_small_remaining_budget_and_allocation_interrupt() {
        let mut c = Observer {
            boundaries: vec![],
            stop: None,
            batch: u16::MAX,
        };
        {
            let mut m = BuildMeter::new(150, &mut c);
            m.work("first", 128).unwrap();
            assert_eq!((m.charged, m.available), (150, 22));
            m.work("last", 22).unwrap();
            assert_eq!(m.available, 0);
            assert!(matches!(
                m.work("exhausted", 1),
                Err(CmgError::BuildStopped {
                    reason: BuildStopReason::WorkBudgetExhausted,
                    ..
                })
            ));
        }
        assert_eq!(c.boundaries.len(), 1);
        let mut c = Observer {
            boundaries: vec![],
            stop: Some(1),
            batch: u16::MAX,
        };
        let mut m = BuildMeter::new(u64::MAX, &mut c);
        m.work("first", 1).unwrap();
        assert_eq!(m.available, 65534);
        assert!(matches!(
            m.allocation("allocate", 64),
            Err(CmgError::BuildStopped {
                reason: BuildStopReason::Interrupted,
                ..
            })
        ));
        let mut c = Observer {
            boundaries: vec![],
            stop: None,
            batch: 0,
        };
        let mut m = BuildMeter::new(129, &mut c);
        m.work("one", 128).unwrap();
        m.work("two", 1).unwrap();
        assert_eq!(
            c.boundaries
                .iter()
                .map(|b| (b.phase, b.chunk_id, b.charged_work))
                .collect::<Vec<_>>(),
            vec![("one", 0, 0), ("two", 1, 128)]
        );
    }
    #[test]
    fn cooperative_sort_matches_reference_on_adversarial_orders_and_stops() {
        for n in [0, 1, 15, 16, 17, 127, 1024] {
            let cases = vec![
                (0..n).collect::<Vec<_>>(),
                (0..n).rev().collect(),
                vec![4; n],
                (0..n).map(|i| (i * 97) % 37).collect(),
                (0..n).map(|i| i.min(n - i)).collect(),
            ];
            for values in cases {
                let mut expected = values.clone();
                expected.sort_unstable();
                let mut c = Observer {
                    boundaries: vec![],
                    stop: None,
                    batch: 1024,
                };
                let mut actual = values.clone();
                BuildMeter::new(u64::MAX, &mut c)
                    .sort_by(&mut actual, usize::cmp, "sort")
                    .unwrap();
                assert_eq!(actual, expected);
                for stop in 0..c.boundaries.len() {
                    let mut observer = Observer {
                        boundaries: vec![],
                        stop: Some(stop),
                        batch: 1024,
                    };
                    assert!(matches!(
                        BuildMeter::new(u64::MAX, &mut observer).sort_by(
                            &mut values.clone(),
                            usize::cmp,
                            "sort"
                        ),
                        Err(CmgError::BuildStopped {
                            reason: BuildStopReason::Interrupted,
                            ..
                        })
                    ));
                }
                let mut c = Observer {
                    boundaries: vec![],
                    stop: None,
                    batch: u16::MAX,
                };
                let mut actual = values.clone();
                BuildMeter::new(u64::MAX, &mut c)
                    .introsort(&mut actual, &mut usize::cmp, "forced fallback", 0)
                    .unwrap();
                assert_eq!(actual, expected);
            }
        }
    }
    #[test]
    #[cfg(feature = "experimental-components")]
    fn graph_and_preconditioner_remain_exact_with_cooperative_batches() {
        use crate::{CmgOptions, CmgPreconditioner, ComponentBuildOptions, Laplacian};
        let mut input = vec![];
        for i in 0..511 {
            input.extend([
                (i, i + 1, 1e-8),
                (i, i + 1, 1.),
                (i, i + 1, 1e8),
                (i, i + 1, 1.),
            ]);
        }
        for i in 0..509 {
            input.push((i, i + 3, 0.3));
        }
        for reverse in [false, true] {
            if reverse {
                input.reverse();
            }
            let graph = Laplacian::from_edges(513, input.iter().copied()).unwrap();
            for bits in 0..8 {
                let experiment = ComponentBuildOptions {
                    prune_coarse_isolates: bits & 1 != 0,
                    preserve_unpruned_stopping: bits & 2 != 0,
                    factor_terminal_components: bits & 4 != 0,
                };
                let expected = CmgPreconditioner::build_component_experiment_serial_with_budget(
                    &graph,
                    CmgOptions::default(),
                    experiment,
                    usize::MAX,
                )
                .unwrap();
                for batch in [1024, 8192, u16::MAX] {
                    let mut c = Observer {
                        boundaries: vec![],
                        stop: None,
                        batch,
                    };
                    let actual =
                        Laplacian::from_edges_controlled(513, &input, usize::MAX, u64::MAX, &mut c)
                            .unwrap();
                    assert_eq!(actual, graph);
                    let pre = CmgPreconditioner::build_component_experiment_serial_controlled(
                        &actual,
                        CmgOptions::default(),
                        experiment,
                        usize::MAX,
                        u64::MAX,
                        &mut c,
                    )
                    .unwrap();
                    assert_eq!(
                        pre.hierarchy().levels().len(),
                        expected.hierarchy().levels().len()
                    );
                    for (a, b) in pre
                        .hierarchy()
                        .levels()
                        .iter()
                        .zip(expected.hierarchy().levels())
                    {
                        assert_eq!(a.graph(), b.graph());
                        assert_eq!(a.repeat(), b.repeat());
                    }
                    let mut rhs = vec![0.; 513];
                    rhs[0] = 1.;
                    rhs[510] = -1.;
                    let mut a = vec![0.; 513];
                    let mut b = a.clone();
                    pre.apply_compatible_into(&rhs, &mut a, &mut pre.workspace())
                        .unwrap();
                    expected
                        .apply_compatible_into(&rhs, &mut b, &mut expected.workspace())
                        .unwrap();
                    assert_eq!(a, b);
                    assert_eq!(pre.hierarchy(), expected.hierarchy());
                    assert_eq!(pre.terminal_factor(), expected.terminal_factor());
                    assert_eq!(pre.repeat_counts(), expected.repeat_counts());
                }
            }
        }
    }

    #[test]
    #[cfg(feature = "experimental-components")]
    fn cooperative_candidate_denies_every_boundary_and_reservoir_refills() {
        use crate::{CmgOptions, CmgPreconditioner, ComponentBuildOptions, Laplacian};
        let graph = Laplacian::from_edges(12, (0..11).map(|i| (i, i + 1, 1.))).unwrap();
        let mut trace = Observer {
            boundaries: vec![],
            stop: None,
            batch: 1024,
        };
        CmgPreconditioner::build_component_experiment_serial_controlled(
            &graph,
            CmgOptions::default(),
            ComponentBuildOptions::default(),
            usize::MAX,
            u64::MAX,
            &mut trace,
        )
        .unwrap();
        assert!(
            trace
                .boundaries
                .iter()
                .any(|b| b.phase == "terminal dense numeric")
        );
        struct StopAt {
            seen: usize,
            stop: usize,
            decision: BuildDecision,
        }
        impl BuildControl for StopAt {
            fn cooperative_work_batch(&self) -> u16 {
                1024
            }
            fn checkpoint(&mut self, _: BuildBoundary) -> BuildDecision {
                let stop = self.seen == self.stop;
                self.seen += 1;
                if stop {
                    self.decision
                } else {
                    BuildDecision::Continue
                }
            }
        }
        for stop in 0..trace.boundaries.len() {
            for decision in [BuildDecision::Interrupted, BuildDecision::StopUnused] {
                let mut c = StopAt {
                    seen: 0,
                    stop,
                    decision,
                };
                let error = CmgPreconditioner::build_component_experiment_serial_controlled(
                    &graph,
                    CmgOptions::default(),
                    ComponentBuildOptions::default(),
                    usize::MAX,
                    u64::MAX,
                    &mut c,
                )
                .unwrap_err();
                assert!(matches!(error, CmgError::BuildStopped { .. }));
                assert_eq!(c.seen, stop + 1);
            }
        }
        let mut c = Observer {
            boundaries: vec![],
            stop: None,
            batch: 1024,
        };
        let mut m = BuildMeter::new(1030, &mut c);
        m.work("first", 1020).unwrap();
        assert_eq!(m.available, 4);
        m.work("tail", 10).unwrap();
        assert_eq!((m.charged, m.available), (1030, 0));
        assert!(m.work("beyond", 1).is_err());
    }
}
