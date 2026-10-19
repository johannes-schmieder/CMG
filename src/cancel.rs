//! Cooperative cancellation for bounded serial construction.
//!
//! Construction kernels count conservative primitive units (arithmetic, record
//! visits, comparisons and moves) while they work. The meter polls the caller's
//! sticky, allocation-free cancellation query whenever the running count crosses
//! a fixed threshold and before every allocation. Work between polls is therefore
//! bounded by the threshold plus one bounded operation, regardless of row, group,
//! level or factor-block boundaries. Units are a polling cadence, not an
//! admission budget: nothing is charged, refused or reported. Allocator,
//! operating-system and cleanup latency are not bounded.

use crate::CmgError;

/// Counted units between two cancellation polls on the serial meter.
pub(crate) const POLL_UNITS: u32 = 1 << 16;

/// A caller-owned, sticky cancellation query. It must be cheap, allocation-free
/// and remain true once it has returned true for the current construction.
pub type Cancellation<'a> = &'a (dyn Fn() -> bool + Sync);

pub(crate) struct CancelMeter<'a> {
    cancel: Cancellation<'a>,
    pending: u32,
    threshold: u32,
}

impl<'a> CancelMeter<'a> {
    pub(crate) fn new(cancel: Cancellation<'a>) -> Self {
        Self {
            cancel,
            pending: 0,
            threshold: POLL_UNITS,
        }
    }

    /// Poll at every work and allocation boundary. Tests use this to cancel
    /// at each bounded operation of a small fixture.
    #[cfg(test)]
    pub(crate) fn every_operation(cancel: Cancellation<'a>) -> Self {
        Self {
            cancel,
            pending: 0,
            threshold: 1,
        }
    }

    pub(crate) fn cancelled(phase: &'static str) -> CmgError {
        CmgError::Cancelled { phase }
    }

    #[inline]
    fn poll(&mut self, phase: &'static str) -> Result<(), CmgError> {
        self.pending = 0;
        if (self.cancel)() {
            Err(Self::cancelled(phase))
        } else {
            Ok(())
        }
    }

    /// Count one bounded operation of at most 1024 primitive units.
    #[inline]
    pub(crate) fn work(&mut self, phase: &'static str, units: u16) -> Result<(), CmgError> {
        if units == 0 || units > 1024 {
            return Err(CmgError::InvalidHierarchy {
                context: "invalid bounded construction chunk",
            });
        }
        self.pending += u32::from(units);
        if self.pending >= self.threshold {
            self.poll(phase)
        } else {
            Ok(())
        }
    }

    /// Poll before an allocation. The requested size is admitted separately
    /// through the caller's closed-form memory bound.
    #[inline]
    pub(crate) fn allocation(
        &mut self,
        phase: &'static str,
        _bytes: usize,
    ) -> Result<(), CmgError> {
        self.poll(phase)
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

    // Allocation-free heapsort. Each comparison and bounded swap is counted.
    // Callers use constant-work comparators and scalar/record Copy payloads.
    pub(crate) fn sort_by<T: Copy>(
        &mut self,
        values: &mut [T],
        mut compare: impl FnMut(&T, &T) -> core::cmp::Ordering,
        phase: &'static str,
    ) -> Result<(), CmgError> {
        fn sift<T: Copy>(
            meter: &mut CancelMeter<'_>,
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
}

/// Test support: count polls and cancel exactly at one zero-based poll.
#[cfg(test)]
pub(crate) struct StopAt {
    polls: std::sync::atomic::AtomicUsize,
    stop: Option<usize>,
}

#[cfg(test)]
impl StopAt {
    pub(crate) fn never() -> Self {
        Self::at(None)
    }

    pub(crate) fn at(stop: Option<usize>) -> Self {
        Self {
            polls: std::sync::atomic::AtomicUsize::new(0),
            stop,
        }
    }

    pub(crate) fn query(&self) -> bool {
        let index = self
            .polls
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        self.stop.is_some_and(|stop| index >= stop)
    }

    pub(crate) fn polls(&self) -> usize {
        self.polls.load(std::sync::atomic::Ordering::Relaxed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn polls_at_threshold_and_allocations_and_reports_the_phase() {
        let stop = StopAt::never();
        let query = || stop.query();
        let mut meter = CancelMeter::new(&query);
        for _ in 0..(POLL_UNITS / 1024 - 1) {
            meter.work("phase", 1024).unwrap();
        }
        assert_eq!(stop.polls(), 0);
        meter.work("phase", 1024).unwrap();
        assert_eq!(stop.polls(), 1);
        meter.allocation("phase", 8).unwrap();
        assert_eq!(stop.polls(), 2);
        let stop = StopAt::at(Some(0));
        let query = || stop.query();
        let mut meter = CancelMeter::new(&query);
        assert_eq!(
            meter.allocation("alloc", 0),
            Err(CmgError::Cancelled { phase: "alloc" })
        );
    }

    #[test]
    fn heapsort_matches_the_standard_sort_and_stops_inside_one_long_run() {
        let mut values: Vec<u32> = (0..5000).map(|i| (i * 7919) % 5003).collect();
        let mut expected = values.clone();
        expected.sort_unstable();
        let never = || false;
        CancelMeter::new(&never)
            .sort_by(&mut values, u32::cmp, "sort")
            .unwrap();
        assert_eq!(values, expected);
        // A single long sort is still interruptible between bounded steps.
        let stop = StopAt::at(Some(1));
        let query = || stop.query();
        let mut values: Vec<u32> = (0..50_000).rev().collect();
        assert_eq!(
            CancelMeter::new(&query).sort_by(&mut values, u32::cmp, "sort"),
            Err(CmgError::Cancelled { phase: "sort" })
        );
        assert_eq!(stop.polls(), 2);
    }
}
