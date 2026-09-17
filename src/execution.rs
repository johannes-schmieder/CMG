//! Optional package-owned parallel execution support.

use crate::CmgError;

/// Minimum item count for setup kernels whose parallel path pays sorting or
/// temporary sparse-storage overhead.
#[cfg(feature = "parallel")]
pub(crate) const PARALLEL_SETUP_MIN_ITEMS: usize = 65_536;

/// Options controlling optional package-owned parallel execution.
///
/// A thread count of zero selects [`std::thread::available_parallelism`]. The
/// memory budget applies to concurrently retained solver workspaces; immutable
/// graph and hierarchy storage are not included in that budget.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ParallelOptions {
    /// Number of worker threads, or zero to detect the available parallelism.
    pub threads: usize,
    /// Minimum vector or row count before a within-kernel parallel path is used.
    pub min_parallel_len: usize,
    /// Fixed chunk size reserved for deterministic parallel reductions.
    pub reduction_chunk_size: usize,
    /// Optional budget for concurrently retained solver workspaces, in bytes.
    pub workspace_memory_budget_bytes: Option<usize>,
}

impl Default for ParallelOptions {
    fn default() -> Self {
        Self {
            threads: 0,
            min_parallel_len: 4_096,
            reduction_chunk_size: 16_384,
            workspace_memory_budget_bytes: None,
        }
    }
}

impl ParallelOptions {
    pub(crate) fn resolved_threads(self) -> usize {
        if self.threads == 0 {
            std::thread::available_parallelism()
                .map(usize::from)
                .unwrap_or(1)
        } else {
            self.threads
        }
    }

    /// Validate the execution options without creating a thread pool.
    pub fn validate(self) -> Result<Self, CmgError> {
        if self.min_parallel_len == 0 {
            return Err(CmgError::InvalidOption {
                name: "min_parallel_len",
                value: 0.0,
            });
        }
        if self.reduction_chunk_size == 0 {
            return Err(CmgError::InvalidOption {
                name: "reduction_chunk_size",
                value: 0.0,
            });
        }
        if self.workspace_memory_budget_bytes == Some(0) {
            return Err(CmgError::InvalidOption {
                name: "workspace_memory_budget_bytes",
                value: 0.0,
            });
        }
        Ok(self)
    }
}

/// A reusable package-owned Rayon thread pool.
///
/// This type is available only with the `parallel` Cargo feature. It never
/// configures or relies on Rayon's process-wide global pool.
/// Destruction waits for every worker, including thread-local destructors.
/// Only internal scoped kernels can run on this pool; callers cannot transfer
/// ownership of the executor into a worker task.
#[cfg(feature = "parallel")]
pub struct ParallelExecutor {
    pool: Option<rayon::ThreadPool>,
    workers: JoinedWorkers,
    threads: usize,
    options: ParallelOptions,
}

#[cfg(feature = "parallel")]
impl core::fmt::Debug for ParallelExecutor {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter
            .debug_struct("ParallelExecutor")
            .field("threads", &self.threads)
            .field("options", &self.options)
            .finish_non_exhaustive()
    }
}

#[cfg(feature = "parallel")]
impl ParallelExecutor {
    /// Rayon/crossbeam runtime heap currently has no source-proven finite
    /// admission bound. Worker handles are counted by `CmgStorageReport`, but
    /// queues, deferred reclamation, native thread resources and TLS are not.
    pub const fn runtime_allocation_bound() -> Option<usize> {
        None
    }

    /// Attempt strict runtime-heap admission before pool construction. Currently
    /// returns an uncovered-scope error for every finite budget. Use `new` only
    /// when the caller's policy explicitly permits unbounded runtime storage.
    pub fn new_with_memory_budget(
        options: ParallelOptions,
        budget_bytes: usize,
    ) -> Result<Self, CmgError> {
        options.validate()?;
        let required = Self::runtime_allocation_bound().ok_or(CmgError::InvalidHierarchy {
            context: "Rayon runtime memory coverage is unavailable",
        })?;
        crate::storage::admit(required, budget_bytes)?;
        Self::new(options)
    }
    /// Construct an isolated thread pool with the requested execution policy.
    pub fn new(options: ParallelOptions) -> Result<Self, CmgError> {
        Self::with_spawn(options, |thread| {
            let mut builder = std::thread::Builder::new();
            if let Some(name) = thread.name() {
                builder = builder.name(name.to_owned());
            }
            if let Some(size) = thread.stack_size() {
                builder = builder.stack_size(size);
            }
            builder.spawn(|| thread.run())
        })
    }

    fn with_spawn(
        options: ParallelOptions,
        mut spawn: impl FnMut(rayon::ThreadBuilder) -> std::io::Result<std::thread::JoinHandle<()>>,
    ) -> Result<Self, CmgError> {
        let options = options.validate()?;
        let threads = options.resolved_threads();
        let mut workers = JoinedWorkers::new(threads)?;
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(threads)
            .thread_name(|index| format!("cmg-worker-{index}"))
            .spawn_handler(|thread| {
                workers.handles.push(spawn(thread)?);
                Ok(())
            })
            .build()
            .map_err(|error| CmgError::ParallelRuntime {
                message: error.to_string(),
            })?;
        Ok(Self {
            pool: Some(pool),
            workers,
            threads,
            options,
        })
    }

    /// Return the actual number of worker threads in this pool.
    #[must_use]
    pub const fn thread_count(&self) -> usize {
        self.threads
    }

    /// Return the validated execution options used to create this executor.
    #[must_use]
    pub const fn options(&self) -> ParallelOptions {
        self.options
    }

    /// Return the maximum number of simultaneous workspaces allowed for a
    /// batch of the supplied size.
    pub fn batch_concurrency(
        &self,
        workspace_bytes: usize,
        batch_len: usize,
    ) -> Result<usize, CmgError> {
        if batch_len == 0 {
            return Ok(0);
        }
        let thread_limit = self.threads.min(batch_len).max(1);
        let Some(budget) = self.options.workspace_memory_budget_bytes else {
            return Ok(thread_limit);
        };
        if workspace_bytes > budget {
            return Err(CmgError::MemoryBudgetExceeded {
                required_bytes: workspace_bytes,
                budget_bytes: budget,
            });
        }
        if workspace_bytes == 0 {
            return Ok(thread_limit);
        }
        Ok(thread_limit.min(budget / workspace_bytes).max(1))
    }

    pub(crate) fn install<Operation, Output>(&self, operation: Operation) -> Output
    where
        Operation: FnOnce() -> Output + Send,
        Output: Send,
    {
        self.pool
            .as_ref()
            .expect("live CMG executor")
            .install(operation)
    }

    pub(crate) fn should_parallel(&self, length: usize) -> bool {
        self.threads > 1 && length >= self.options.min_parallel_len
    }

    pub(crate) fn work_chunk_len(&self, length: usize) -> usize {
        let target_chunks = self.threads.saturating_mul(8).max(1);
        let quotient = length / target_chunks;
        let remainder = usize::from(length % target_chunks != 0);
        quotient.saturating_add(remainder).max(1)
    }
}

// ThreadPool::drop signals termination but does not join. Rayon's failed-build
// guard also signals termination before our handle bank unwinds. Keep this bank
// alive until after that signal, including on partial startup failure.
#[cfg(feature = "parallel")]
struct JoinedWorkers {
    handles: Vec<std::thread::JoinHandle<()>>,
}

#[cfg(feature = "parallel")]
impl crate::storage::Storage for std::thread::JoinHandle<()> {
    fn visit(
        &self,
        _: &mut dyn FnMut(crate::storage::Allocation) -> Result<(), CmgError>,
    ) -> Result<(), CmgError> {
        Ok(())
    }
    fn leaf() -> bool {
        true
    }
}
#[cfg(feature = "parallel")]
impl crate::storage::Storage for ParallelExecutor {
    fn visit(
        &self,
        visitor: &mut dyn FnMut(crate::storage::Allocation) -> Result<(), CmgError>,
    ) -> Result<(), CmgError> {
        crate::storage::Storage::visit(&self.workers.handles, visitor)?;
        crate::storage::runtime(visitor)
    }
}

#[cfg(feature = "parallel")]
impl JoinedWorkers {
    fn new(threads: usize) -> Result<Self, CmgError> {
        let mut handles = Vec::new();
        handles
            .try_reserve_exact(threads)
            .map_err(|_| CmgError::AllocationFailed {
                context: "executor worker handles",
            })?;
        Ok(Self { handles })
    }

    fn join(&mut self) {
        for handle in self.handles.drain(..) {
            // Do not panic again while cleaning up a caller panic. The private
            // scoped-only install API prevents self/peer joins: no public task
            // can own the executor. Do not expose asynchronous spawn/install
            // without redesigning this invariant.
            let _ = handle.join();
        }
    }
}

#[cfg(feature = "parallel")]
impl Drop for JoinedWorkers {
    fn drop(&mut self) {
        self.join();
    }
}

#[cfg(feature = "parallel")]
impl Drop for ParallelExecutor {
    fn drop(&mut self) {
        drop(self.pool.take());
        self.workers.join();
    }
}

#[cfg(all(test, feature = "parallel"))]
mod lifetime_tests {
    use super::*;
    use std::cell::RefCell;
    use std::sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    };

    struct Exit(Arc<AtomicUsize>);
    impl Drop for Exit {
        fn drop(&mut self) {
            self.0.fetch_add(1, Ordering::SeqCst);
        }
    }
    thread_local! { static EXIT: RefCell<Option<Exit>> = const { RefCell::new(None) }; }

    fn observed(
        threads: usize,
        fail_at: Option<usize>,
        completed: Arc<AtomicUsize>,
    ) -> Result<ParallelExecutor, CmgError> {
        let mut started = 0;
        ParallelExecutor::with_spawn(
            ParallelOptions {
                threads,
                ..Default::default()
            },
            move |thread| {
                if Some(started) == fail_at {
                    return Err(std::io::Error::other("injected startup failure"));
                }
                started += 1;
                let completed = Arc::clone(&completed);
                std::thread::Builder::new().spawn(move || {
                    EXIT.with(|slot| *slot.borrow_mut() = Some(Exit(completed)));
                    thread.run();
                })
            },
        )
    }

    #[test]
    fn retirement_waits_for_actual_workers_and_tls_across_pool_sizes() {
        for threads in [1, 4, 1, 8, 2, 4, 1] {
            let completed = Arc::new(AtomicUsize::new(0));
            let executor = observed(threads, None, Arc::clone(&completed)).unwrap();
            assert_eq!(executor.install(rayon::current_num_threads), threads);
            assert_eq!(executor.workers.handles.len(), threads);
            // Final ownership stays outside the pool. All entries are its real
            // workers; the completion counter runs in their TLS destructors.
            assert!(
                !executor
                    .workers
                    .handles
                    .iter()
                    .any(|h| h.thread().id() == std::thread::current().id())
            );
            drop(executor);
            assert_eq!(completed.load(Ordering::SeqCst), threads);
        }
    }

    #[test]
    fn partial_startup_joins_before_returning_error() {
        for failed_index in [0, 1, 3] {
            let completed = Arc::new(AtomicUsize::new(0));
            assert!(observed(4, Some(failed_index), Arc::clone(&completed)).is_err());
            assert_eq!(completed.load(Ordering::SeqCst), failed_index);
        }
    }

    #[test]
    fn scoped_panic_unwinds_and_joins_without_second_panic() {
        let completed = Arc::new(AtomicUsize::new(0));
        let result = std::panic::catch_unwind({
            let completed = Arc::clone(&completed);
            move || {
                let executor = observed(4, None, completed).unwrap();
                executor.install(|| panic!("scoped kernel panic"));
            }
        });
        assert!(result.is_err());
        assert_eq!(completed.load(Ordering::SeqCst), 4);
    }

    #[test]
    fn join_error_does_not_double_panic() {
        let result = std::panic::catch_unwind(|| {
            let mut workers = JoinedWorkers::new(1).unwrap();
            workers
                .handles
                .push(std::thread::spawn(|| panic!("worker panic")));
            panic!("caller panic");
        });
        assert!(result.is_err());
    }
}
