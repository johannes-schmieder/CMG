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
        /// Conservative allowance; always in `1..=1024`.
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
}

impl<'a> BuildMeter<'a> {
    pub(crate) fn new(budget: u64, control: &'a mut dyn BuildControl) -> Self {
        Self {
            control,
            budget,
            charged: 0,
            ordinal: 0,
        }
    }

    #[cfg(feature = "experimental-components")]
    pub(crate) fn remaining(&self) -> u64 {
        // gate only commits a charged value after checking it against budget.
        self.budget - self.charged
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

    pub(crate) fn work(&mut self, phase: &'static str, units: u16) -> Result<(), CmgError> {
        if units == 0 || units > 1024 {
            return Err(CmgError::InvalidHierarchy {
                context: "invalid controlled build chunk allowance",
            });
        }
        self.gate(phase, BuildOperation::Work { units })
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
}
