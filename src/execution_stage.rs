//! Borrowed execution and canonical admission for bounded independent chunks.

use crate::{BuildControl, BuildStopReason, CmgError, build_control::BuildMeter};

/// A borrowed, already admitted host pool and a positive stage worker ceiling.
///
/// This creates no pool, retains no owner and does not admit host runtime memory.
/// The host remains responsible for its pool's lifetime and resource admission.
pub struct HostExecutor<'pool> {
    pool: &'pool rayon::ThreadPool,
    workers: usize,
}

impl<'pool> HostExecutor<'pool> {
    /// Borrow an explicit host pool without installing or starting any work.
    pub fn new(pool: &'pool rayon::ThreadPool, workers: usize) -> Result<Self, CmgError> {
        if workers == 0 || workers > pool.current_num_threads() {
            return Err(CmgError::InvalidHierarchy {
                context: "host execution worker ceiling is outside the borrowed pool",
            });
        }
        Ok(Self { pool, workers })
    }

    /// Lend an execution view only while already running on this exact pool.
    ///
    /// The coordinator callback need not be `Send`: serial admission controllers
    /// stay on the calling thread. Calling from outside this pool fails before
    /// the callback runs. The view cannot escape the callback or own the pool.
    /// All callback work must finish synchronously or within joined scopes;
    /// detached tasks are outside the supported interface contract.
    ///
    /// ```compile_fail
    /// let pool = rayon::ThreadPoolBuilder::new().num_threads(1).build().unwrap();
    /// let host = cmg::HostExecutor::new(&pool, 1).unwrap();
    /// pool.install(|| host.with_installed(|team| team)); // Cannot retain a view.
    /// ```
    pub fn with_installed<R>(
        &self,
        operation: impl for<'scope> FnOnce(InstalledTeam<'scope>) -> R,
    ) -> Result<R, CmgError> {
        check_installed(self.pool)?;
        Ok(operation(InstalledTeam {
            pool: self.pool,
            workers: self.workers,
        }))
    }
}

/// A callback-scoped view of the host or CMG-owned installed pool.
///
/// It is never stored in a hierarchy or workspace. Only admitted stages can
/// dispatch through it; it exposes neither an asynchronous spawn nor an owner.
pub struct InstalledTeam<'scope> {
    pool: &'scope rayon::ThreadPool,
    workers: usize,
}

impl InstalledTeam<'_> {
    /// Maximum concurrently executing terminal lanes for each stage.
    pub const fn worker_ceiling(&self) -> usize {
        self.workers
    }
}

fn check_installed(pool: &rayon::ThreadPool) -> Result<(), CmgError> {
    if pool.current_thread_index().is_none() {
        return Err(CmgError::InvalidHierarchy {
            context: "host execution requires its borrowed pool to be installed",
        });
    }
    Ok(())
}

/// Admission policy for independent, fixed-work chunks of a mutable slice.
///
/// The caller admits and allocates the slice and any kernel scratch separately,
/// before dispatch. The dispatcher allocates no task/result/scratch banks.
/// Each kernel invocation must do at most its declared `chunk_work` primitive
/// operations, use only disjoint admitted storage and perform no allocation,
/// nested parallel work or adaptive unbounded iteration. This is a caller
/// contract, not instruction counting or an allocator sandbox.
pub struct ControlledStage;

impl ControlledStage {
    /// Precharge a complete stage on the coordinator, in canonical chunk order.
    ///
    /// Planning costs 128 units. Every logical chunk additionally costs 64 units
    /// for admission, dispatch, cancellation checks and ordered reconciliation,
    /// followed by the declared kernel allowance in `1..=1024`. These are new
    /// stage boundaries; existing serial builder callback traces are unchanged.
    /// Refusal retains every earlier charge and dispatches no kernel work.
    pub fn admit(
        len: usize,
        chunk_len: usize,
        chunk_work: u16,
        work_budget: u64,
        control: &mut dyn BuildControl,
    ) -> Result<AdmittedStage, CmgError> {
        if chunk_len == 0 || chunk_work == 0 || chunk_work > 1024 {
            return Err(CmgError::InvalidHierarchy {
                context: "invalid controlled stage chunk policy",
            });
        }
        let mut meter = BuildMeter::new(work_budget, control);
        meter.work("parallel stage planning", 128)?;
        let chunks = len.div_ceil(chunk_len);
        let chunks_u64 = u64::try_from(chunks).map_err(|_| {
            meter.stopped("parallel stage planning", BuildStopReason::CounterOverflow)
        })?;
        let boundaries = chunks_u64.checked_mul(2).and_then(|n| n.checked_add(1));
        let total = chunks_u64
            .checked_mul(64 + u64::from(chunk_work))
            .and_then(|n| n.checked_add(128));
        let (Some(boundaries), Some(total)) = (boundaries, total) else {
            return Err(meter.stopped("parallel stage planning", BuildStopReason::CounterOverflow));
        };
        for _ in 0..chunks {
            meter.work("parallel stage dispatch", 64)?;
            meter.work("parallel stage kernel", chunk_work)?;
        }
        Ok(AdmittedStage {
            len,
            chunk_len,
            chunks,
            charged_work: total,
            boundaries,
        })
    }
}

/// A single-use reservation for a completely admitted controlled stage.
///
/// No controller, executor, slice or scratch owner is retained. Consuming the
/// reservation prevents accidental redispatch without another work admission.
#[derive(Debug)]
pub struct AdmittedStage {
    len: usize,
    chunk_len: usize,
    chunks: usize,
    charged_work: u64,
    boundaries: u64,
}

impl AdmittedStage {
    /// Full charge, including stage planning and dispatch overhead.
    pub const fn charged_work(&self) -> u64 {
        self.charged_work
    }

    /// Execute disjoint bounded chunks and join before returning or unwinding.
    ///
    /// `cancelled` must be a cheap, allocation-free, cancellation-only query
    /// that remains true once cancellation is requested during this stage;
    /// it cannot admit work or access a serial controller. It is polled before
    /// each kernel invocation and after the join. A true result interrupts the
    /// stage and dominates kernel errors. Otherwise the earliest failing chunk
    /// in logical order wins, independent of scheduling. All admitted chunks
    /// run unless cancelled or unwinding; kernel errors do not cancel siblings.
    /// Outputs remain private partial state on error and must not be published.
    /// No callbacks or arithmetic from existing serial builders are replaced.
    pub fn run<T: Send>(
        self,
        executor: &InstalledTeam<'_>,
        values: &mut [T],
        cancelled: impl Fn() -> bool + Sync,
        kernel: impl Fn(usize, &mut [T]) -> Result<(), CmgError> + Sync,
    ) -> Result<(), CmgError> {
        check_installed(executor.pool)?;
        if values.len() != self.len {
            return Err(CmgError::InvalidHierarchy {
                context: "controlled stage output length differs from admission",
            });
        }
        let result = self.dispatch(
            values,
            0,
            self.chunks,
            executor.workers.min(self.chunks).max(1),
            &cancelled,
            &kernel,
        );
        if cancelled() {
            return Err(self.interrupted());
        }
        result
    }

    fn interrupted(&self) -> CmgError {
        CmgError::BuildStopped {
            reason: BuildStopReason::Interrupted,
            phase: "parallel stage execution",
            chunk_id: self.boundaries,
            charged_work: self.charged_work,
        }
    }

    fn dispatch<T: Send, C: Fn() -> bool + Sync, F>(
        &self,
        values: &mut [T],
        first: usize,
        chunks: usize,
        lanes: usize,
        cancelled: &C,
        kernel: &F,
    ) -> Result<(), CmgError>
    where
        F: Fn(usize, &mut [T]) -> Result<(), CmgError> + Sync,
    {
        if lanes <= 1 || chunks <= 1 {
            let mut error = None;
            for (offset, chunk) in values.chunks_mut(self.chunk_len).enumerate() {
                if cancelled() {
                    return Err(self.interrupted());
                }
                if let Err(failure) = kernel(first + offset, chunk) {
                    if error.is_none() {
                        error = Some(failure);
                    }
                }
            }
            return error.map_or(Ok(()), Err);
        }
        let left_lanes = lanes / 2;
        let left_chunks = ((chunks as u128 * left_lanes as u128) / lanes as u128) as usize;
        // Every left chunk is full; the potentially short chunk is rightmost.
        let (left, right) = values.split_at_mut(left_chunks * self.chunk_len);
        let (a, b) = rayon::join(
            || self.dispatch(left, first, left_chunks, left_lanes, cancelled, kernel),
            || {
                self.dispatch(
                    right,
                    first + left_chunks,
                    chunks - left_chunks,
                    lanes - left_lanes,
                    cancelled,
                    kernel,
                )
            },
        );
        a.and(b)
    }
}
