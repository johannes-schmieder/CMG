//! Allocation-free deterministic admission for a stackful serial builder.

use crate::{BuildBoundary, BuildOperation};

/// A global operation identity spanning several controlled construction calls.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PrefixLocation {
    /// Caller-assigned stable task order, independent of scheduling.
    pub task_id: u64,
    /// Zero-based ordinal across all work and allocation gates in this task.
    pub ordinal: u64,
    /// Original phase and entrypoint-local boundary identity.
    pub boundary: BuildBoundary,
}

/// Admission decision before an operation begins.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PrefixDecision {
    /// The entire operation is admitted and charged.
    Continue,
    /// Block on the same construction stack, then retry this exact boundary.
    Park,
    /// The issued prefix is drained; discard the unissued suffix.
    StopUnused,
    /// Break supersedes further construction, including draining.
    Interrupted,
}

/// Terminal construction outcome; this does not release an executor worker.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PrefixOutcome {
    /// The complete candidate was constructed.
    Complete,
    /// The issued prefix was drained and its unissued suffix abandoned.
    Unused,
    /// The next complete operation cannot fit even the full admitted allowance.
    BudgetExhausted,
    /// A fatal operation failed; its kind uses the caller's fixed ordering.
    Fatal {
        /// Stable caller-defined error-kind order, never arrival order.
        kind: u16,
    },
    /// Construction returned after a Break request.
    Interrupted,
}

/// Invalid controller transition or malformed construction boundary.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PrefixError {
    /// Grants must be monotone, within the cap, and precede abandonment.
    InvalidGrant,
    /// Work chunks must contain between one and 1024 tariff units.
    InvalidChunk,
    /// A parked callback retried a different operation.
    ChangedBoundary,
    /// An ordinal or work counter could not be incremented safely.
    CounterOverflow,
    /// A terminal task received another construction operation or transition.
    Finished,
}

/// Observable prefix state, containing no timing or allocation-dependent data.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PrefixSnapshot {
    /// Maximum admitted construction allowance B.
    pub limit: u64,
    /// Cumulative controller-issued allowance G; never reset on Low handoff.
    pub issued: u64,
    /// Admitted computational work, always at most G.
    pub charged: u64,
    /// Ordinal that will identify the next admitted operation.
    pub next_ordinal: u64,
    /// Unstarted operation at a parked or stopped frontier, if any.
    pub pending: Option<PrefixLocation>,
    /// Last admitted operation, identifying a subsequent operation failure.
    pub last_admitted: Option<PrefixLocation>,
    /// A drain was requested; use `drained()` to test acknowledgement.
    pub draining: bool,
    /// Abandonment is pending after all issued work has run.
    pub abandoning: bool,
    /// Break was requested, including after an earlier terminal outcome.
    pub interrupted: bool,
    /// Terminal construction outcome, independently retained when Break arrives.
    pub outcome: Option<PrefixOutcome>,
}

impl PrefixSnapshot {
    /// Whether terminal completion or a non-fitting frontier acknowledges draining.
    /// A pending boundary alone is insufficient: a newer grant may now fit it
    /// before the parked worker has retried that boundary.
    #[must_use]
    pub fn drained(self) -> bool {
        self.outcome.is_some()
            || (self.draining
                && self
                    .pending
                    .is_some_and(|location| match location.boundary.operation {
                        BuildOperation::Work { units } => {
                            u64::from(units) > self.issued - self.charged
                        }
                        BuildOperation::Allocation { .. } => false,
                    }))
    }
}

/// Cumulative prefix admission with no allocation, synchronization or executor.
///
/// The controller owns prepaid grants; only the constructing worker mutates
/// this state. Its callback must block on [`PrefixDecision::Park`] without
/// unwinding or replaying the constructor. The live stack retains private
/// allocations across parks and compatible Low chunks. Every preprocessing,
/// graph, hierarchy, factor and workspace gate must pass through this instance.
/// Entrypoint-local work counters may reset; this instance's counters do not.
///
/// Allocation gates cost zero work and belong to the ordered prefix before the
/// first work chunk that cannot fit. They are not skipped, reordered or executed
/// after that frontier. The caller separately admits their memory and fixed
/// logical fault identity. No allocator or cleanup latency bound is implied.
#[derive(Debug)]
pub struct BuildPrefix {
    task_id: u64,
    state: PrefixSnapshot,
}

impl BuildPrefix {
    /// Start a task with its fixed cap and no issued work.
    #[must_use]
    pub const fn new(task_id: u64, limit: u64) -> Self {
        Self {
            task_id,
            state: PrefixSnapshot {
                limit,
                issued: 0,
                charged: 0,
                next_ordinal: 0,
                pending: None,
                last_admitted: None,
                draining: false,
                abandoning: false,
                interrupted: false,
                outcome: None,
            },
        }
    }

    /// Issue a cumulative prepaid grant; a later grant resumes a drained task.
    /// The caller implements its logical epoch rule and certificate-before-grant order.
    pub fn grant(&mut self, cumulative: u64) -> Result<(), PrefixError> {
        self.open()?;
        if self.state.abandoning || cumulative < self.state.issued || cumulative > self.state.limit
        {
            return Err(PrefixError::InvalidGrant);
        }
        self.state.issued = cumulative;
        self.state.draining = false;
        Ok(())
    }

    /// Freeze G and request execution through its maximal fitting boundary prefix.
    /// A parked frontier acknowledges draining without losing its unused remainder.
    pub fn drain_issued_prefix(&mut self) -> Result<(), PrefixError> {
        if self.state.outcome.is_none() {
            self.state.draining = true;
        }
        Ok(())
    }

    /// Drain existing issued work, then stop before the first unissued operation.
    /// This never cancels an already issued suffix based on command arrival time.
    pub fn abandon_unissued_suffix(&mut self) -> Result<(), PrefixError> {
        if self.state.outcome.is_none() {
            self.state.draining = true;
            self.state.abandoning = true;
        }
        Ok(())
    }

    /// Request immediate Break; preserve any earlier fatal outcome for diagnostics.
    pub fn interrupt(&mut self) {
        self.state.interrupted = true;
    }

    /// Admit one complete operation or leave the identical boundary pending.
    ///
    /// Pass the fixed full cap (or a larger cap) to each underlying CMG entrypoint,
    /// never the current grant: the callback must be able to park before CMG
    /// returns a budget error. A parked retry charges nothing until it continues.
    pub fn admit(&mut self, boundary: BuildBoundary) -> Result<PrefixDecision, PrefixError> {
        if self.state.interrupted {
            return Ok(PrefixDecision::Interrupted);
        }
        self.open()?;
        let location = PrefixLocation {
            task_id: self.task_id,
            ordinal: self.state.next_ordinal,
            boundary,
        };
        if self
            .state
            .pending
            .is_some_and(|pending| pending != location)
        {
            return Err(PrefixError::ChangedBoundary);
        }
        let units = match boundary.operation {
            BuildOperation::Work { units } if (1..=1024).contains(&units) => u64::from(units),
            BuildOperation::Work { .. } => return Err(PrefixError::InvalidChunk),
            BuildOperation::Allocation { .. } => 0,
        };
        let charged = self
            .state
            .charged
            .checked_add(units)
            .ok_or(PrefixError::CounterOverflow)?;
        let ordinal = self
            .state
            .next_ordinal
            .checked_add(1)
            .ok_or(PrefixError::CounterOverflow)?;
        if charged > self.state.limit {
            self.state.pending = Some(location);
            self.state.outcome = Some(PrefixOutcome::BudgetExhausted);
            return Ok(PrefixDecision::StopUnused);
        }
        if charged > self.state.issued {
            self.state.pending = Some(location);
            if self.state.abandoning {
                self.state.outcome = Some(PrefixOutcome::Unused);
                return Ok(PrefixDecision::StopUnused);
            }
            return Ok(PrefixDecision::Park);
        }
        self.state.charged = charged;
        self.state.next_ordinal = ordinal;
        self.state.last_admitted = Some(location);
        self.state.pending = None;
        Ok(PrefixDecision::Continue)
    }

    /// Register a completed or failed constructor before its worker enters terminal hold.
    /// Fatal errors belong to `last_admitted`; a stopped frontier belongs to `pending`.
    /// The owning controller arbitrates Break/fatal/success/refusal after prescribed joins.
    pub fn finish(&mut self, outcome: PrefixOutcome) -> Result<(), PrefixError> {
        if let Some(previous) = self.state.outcome {
            return if previous == outcome {
                Ok(())
            } else {
                Err(PrefixError::Finished)
            };
        }
        if self.state.pending.is_some() && outcome == PrefixOutcome::Complete {
            return Err(PrefixError::ChangedBoundary);
        }
        self.state.outcome = Some(outcome);
        Ok(())
    }

    /// Read the deterministic ledger and frontier without altering it.
    #[must_use]
    pub const fn snapshot(&self) -> PrefixSnapshot {
        self.state
    }

    fn open(&self) -> Result<(), PrefixError> {
        if self.state.outcome.is_some() {
            Err(PrefixError::Finished)
        } else {
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn work(local: u64, units: u16) -> BuildBoundary {
        BuildBoundary {
            phase: "witness",
            chunk_id: local,
            charged_work: 0,
            operation: BuildOperation::Work { units },
        }
    }

    #[test]
    fn drain_retains_remainder_and_low_grants_never_reset() {
        let mut prefix = BuildPrefix::new(7, 20);
        prefix.grant(7).unwrap();
        assert_eq!(prefix.admit(work(0, 5)), Ok(PrefixDecision::Continue));
        prefix.drain_issued_prefix().unwrap();
        assert_eq!(prefix.admit(work(1, 3)), Ok(PrefixDecision::Park));
        let parked = prefix.snapshot();
        assert_eq!(
            (parked.issued, parked.charged, parked.next_ordinal),
            (7, 5, 1)
        );
        assert_eq!(prefix.admit(work(1, 3)), Ok(PrefixDecision::Park));
        assert_eq!(prefix.snapshot(), parked);
        assert!(parked.drained());
        assert_eq!(prefix.admit(work(2, 3)), Err(PrefixError::ChangedBoundary));
        assert_eq!(prefix.grant(6), Err(PrefixError::InvalidGrant));
        prefix.grant(10).unwrap();
        // A drain arriving before the parked callback retries must not acknowledge
        // the old frontier: the new cumulative grant now admits that chunk.
        prefix.drain_issued_prefix().unwrap();
        assert!(!prefix.snapshot().drained());
        assert_eq!(prefix.admit(work(1, 3)), Ok(PrefixDecision::Continue));
        prefix.abandon_unissued_suffix().unwrap();
        assert_eq!(prefix.admit(work(0, 2)), Ok(PrefixDecision::Continue));
        let allocation = BuildBoundary {
            operation: BuildOperation::Allocation { bytes: 24 },
            ..work(1, 1)
        };
        assert_eq!(prefix.admit(allocation), Ok(PrefixDecision::Continue));
        assert_eq!(prefix.admit(work(2, 1)), Ok(PrefixDecision::StopUnused));
        let stopped = prefix.snapshot();
        assert_eq!(stopped.outcome, Some(PrefixOutcome::Unused));
        assert_eq!((stopped.charged, stopped.next_ordinal), (10, 4));
        assert_eq!(stopped.pending.unwrap().ordinal, 4);
        assert_eq!(stopped.last_admitted.unwrap().task_id, 7);
    }

    #[test]
    fn cap_invalid_chunks_and_break_are_distinct() {
        let mut prefix = BuildPrefix::new(0, 3);
        assert_eq!(prefix.grant(4), Err(PrefixError::InvalidGrant));
        assert_eq!(prefix.admit(work(0, 0)), Err(PrefixError::InvalidChunk));
        assert_eq!(prefix.admit(work(0, 1025)), Err(PrefixError::InvalidChunk));
        prefix.grant(3).unwrap();
        assert_eq!(prefix.admit(work(0, 2)), Ok(PrefixDecision::Continue));
        assert_eq!(prefix.admit(work(1, 2)), Ok(PrefixDecision::StopUnused));
        assert_eq!(
            prefix.snapshot().outcome,
            Some(PrefixOutcome::BudgetExhausted)
        );
        assert_eq!(prefix.snapshot().charged, 2);
        prefix.interrupt();
        assert_eq!(prefix.admit(work(1, 2)), Ok(PrefixDecision::Interrupted));
        assert_eq!(prefix.snapshot().charged, 2);
    }

    #[test]
    fn fatal_identity_is_the_executed_gate_not_an_unissued_fault() {
        let mut prefix = BuildPrefix::new(9, 100);
        prefix.grant(5).unwrap();
        let allocation = BuildBoundary {
            operation: BuildOperation::Allocation { bytes: 80 },
            ..work(0, 1)
        };
        prefix.admit(work(0, 5)).unwrap();
        prefix.admit(allocation).unwrap();
        prefix.finish(PrefixOutcome::Fatal { kind: 2 }).unwrap();
        prefix.abandon_unissued_suffix().unwrap();
        prefix.interrupt();
        let terminal = prefix.snapshot();
        assert!(terminal.interrupted);
        assert_eq!(terminal.outcome, Some(PrefixOutcome::Fatal { kind: 2 }));
        assert_eq!(terminal.last_admitted.unwrap().boundary, allocation);
        assert_eq!(terminal.next_ordinal, 2);
        assert_eq!(terminal.pending, None);
    }
}
