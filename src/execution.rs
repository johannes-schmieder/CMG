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

/// Explicit engineering allowances for an estimated-runtime policy.
///
/// These are configurable planning headroom, NOT measured usage, proved bounds
/// or allocator limits. Defaults are 32 MiB per pool, 2 MiB heap per worker and
/// 8 MiB requested stack per worker. Keep whole-process headroom separately.
#[cfg(feature = "parallel")]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ParallelRuntimeAllowance {
    /// Pool-wide runtime heap allowance in bytes.
    pub pool_heap_bytes: usize,
    /// Additional runtime heap allowance per worker in bytes.
    pub worker_heap_bytes: usize,
    /// Requested stack size per worker; must be positive. The estimated
    /// constructor sets this explicitly, overriding the platform/RUST_MIN_STACK
    /// default. Choose a larger value if the calling application requires it.
    pub worker_stack_bytes: usize,
}

#[cfg(feature = "parallel")]
impl Default for ParallelRuntimeAllowance {
    fn default() -> Self {
        Self {
            pool_heap_bytes: 32 * 1024 * 1024,
            worker_heap_bytes: 2 * 1024 * 1024,
            worker_stack_bytes: 8 * 1024 * 1024,
        }
    }
}

/// A checked planning estimate, separating known handle layouts from heuristic
/// runtime heap and requested thread stacks. This never certifies runtime bytes.
#[cfg(feature = "parallel")]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ParallelRuntimeEstimate {
    threads: usize,
    worker_handles_bytes: usize,
    runtime_heap_allowance_bytes: usize,
    worker_stack_allowance_bytes: usize,
    total_bytes: usize,
}

#[cfg(feature = "parallel")]
impl ParallelRuntimeEstimate {
    /// Resolved worker count used by this estimate.
    pub const fn threads(self) -> usize {
        self.threads
    }
    /// CMG-owned handle-bank layouts. Already included when an executor or its
    /// owning solver is measured with CmgStorageReport; do not count twice.
    pub const fn worker_handles_bytes(self) -> usize {
        self.worker_handles_bytes
    }
    /// Heuristic runtime heap headroom, excluding the known handle bank.
    pub const fn runtime_heap_allowance_bytes(self) -> usize {
        self.runtime_heap_allowance_bytes
    }
    /// Sum of requested worker stack sizes. OS rounding/guard pages and actual
    /// residency are not certified. Do not add another host stack charge.
    pub const fn worker_stack_allowance_bytes(self) -> usize {
        self.worker_stack_allowance_bytes
    }
    /// Checked total of handle layouts and both allowances, used before startup.
    pub const fn total_bytes(self) -> usize {
        self.total_bytes
    }
}

#[cfg(feature = "parallel")]
impl ParallelRuntimeAllowance {
    /// Compute a checked planning estimate without starting workers. A zero
    /// thread request is resolved here; constructor admission uses that same
    /// resolved count rather than detecting available parallelism a second time.
    pub fn estimate(self, options: ParallelOptions) -> Result<ParallelRuntimeEstimate, CmgError> {
        let threads = options.validate()?.resolved_threads();
        if self.worker_stack_bytes == 0 {
            return Err(CmgError::InvalidOption {
                name: "worker_stack_bytes",
                value: 0.0,
            });
        }
        let multiply = |bytes: usize| {
            bytes
                .checked_mul(threads)
                .ok_or(CmgError::AllocationFailed {
                    context: "runtime allowance overflows",
                })
        };
        let worker_handles_bytes = crate::storage::bytes::<std::thread::JoinHandle<()>>(threads)?;
        let runtime_heap_allowance_bytes =
            crate::storage::add(self.pool_heap_bytes, multiply(self.worker_heap_bytes)?)?;
        let worker_stack_allowance_bytes = multiply(self.worker_stack_bytes)?;
        let total_bytes = crate::storage::add(
            worker_handles_bytes,
            crate::storage::add(runtime_heap_allowance_bytes, worker_stack_allowance_bytes)?,
        )?;
        Ok(ParallelRuntimeEstimate {
            threads,
            worker_handles_bytes,
            runtime_heap_allowance_bytes,
            worker_stack_allowance_bytes,
            total_bytes,
        })
    }
}

/// A reusable package-owned Rayon thread pool.
///
/// This type is available only with the `parallel` Cargo feature. It never
/// configures or relies on Rayon's process-wide global pool.
/// With supported synchronous/scoped use, external destruction waits for every
/// worker, including thread-local destructors. Detached coordinator work is not
/// supported. If such work retains the final executor owner on its own worker,
/// destruction signals termination and detaches handles to avoid self/peer
/// joins; workers then retire asynchronously after outstanding work finishes.
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
    /// Install a scoped stage coordinator on this owned pool.
    ///
    /// The same admitted-stage dispatcher is available on borrowed host pools.
    /// Unlike a borrowed host coordinator, this closure crosses into the pool
    /// and therefore must be `Send`. No execution view can escape its callback.
    /// The callback must complete all work synchronously or within joined
    /// scopes; launching detached Rayon tasks is outside this API's contract.
    pub fn with_installed<R: Send>(
        &self,
        operation: impl for<'scope> FnOnce(crate::InstalledTeam<'scope>) -> R + Send,
    ) -> Result<R, CmgError> {
        let pool = self.pool.as_ref().expect("live CMG executor");
        let host = crate::HostExecutor::new(pool, self.threads)?;
        self.install(|| host.with_installed(operation))
    }

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

    /// Explicitly admit an ESTIMATE before worker/handle allocation, returning
    /// the charged estimate for reporting. This is not strict runtime admission
    /// and installs no allocator cap. Requests the allowance's worker stack
    /// size explicitly; the ordinary constructor's stack policy is unchanged.
    pub fn new_with_estimated_memory_budget(
        mut options: ParallelOptions,
        allowance: ParallelRuntimeAllowance,
        budget_bytes: usize,
    ) -> Result<(Self, ParallelRuntimeEstimate), CmgError> {
        let estimate = allowance.estimate(options)?;
        crate::storage::admit(estimate.total_bytes(), budget_bytes)?;
        options.threads = estimate.threads();
        let executor = Self::with_spawn_and_stack_size(
            options,
            Some(allowance.worker_stack_bytes),
            Self::spawn_worker,
        )?;
        Ok((executor, estimate))
    }

    /// Construct an isolated thread pool with the requested execution policy.
    pub fn new(options: ParallelOptions) -> Result<Self, CmgError> {
        Self::with_spawn(options, Self::spawn_worker)
    }

    fn spawn_worker(thread: rayon::ThreadBuilder) -> std::io::Result<std::thread::JoinHandle<()>> {
        let mut builder = std::thread::Builder::new();
        if let Some(name) = thread.name() {
            builder = builder.name(name.to_owned());
        }
        if let Some(size) = thread.stack_size() {
            builder = builder.stack_size(size);
        }
        builder.spawn(|| thread.run())
    }

    fn with_spawn(
        options: ParallelOptions,
        spawn: impl FnMut(rayon::ThreadBuilder) -> std::io::Result<std::thread::JoinHandle<()>>,
    ) -> Result<Self, CmgError> {
        Self::with_spawn_and_stack_size(options, None, spawn)
    }

    fn with_spawn_and_stack_size(
        options: ParallelOptions,
        stack_size: Option<usize>,
        mut spawn: impl FnMut(rayon::ThreadBuilder) -> std::io::Result<std::thread::JoinHandle<()>>,
    ) -> Result<Self, CmgError> {
        let options = options.validate()?;
        let threads = options.resolved_threads();
        let mut workers = JoinedWorkers::new(threads)?;
        let mut builder = rayon::ThreadPoolBuilder::new()
            .num_threads(threads)
            .thread_name(|index| format!("cmg-worker-{index}"));
        if let Some(size) = stack_size {
            builder = builder.stack_size(size);
        }
        let pool = builder
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
            // Do not panic again while cleaning up a caller panic. Final drop
            // on this pool's own worker detaches before reaching this path.
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
        let own_worker = self
            .pool
            .as_ref()
            .is_some_and(|pool| pool.current_thread_index().is_some());
        drop(self.pool.take());
        if own_worker {
            // A caller can misuse its coordinator callback to spawn detached
            // work holding the final Arc<ParallelExecutor>. Joining this worker
            // or peers that await its exit can deadlock. Signal termination
            // above, then detach; supported external retirement still joins.
            self.workers.handles.clear();
        } else {
            self.workers.join();
        }
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
    fn explicit_stack_request_reaches_spawn_and_partial_startup_joins() {
        let completed = Arc::new(AtomicUsize::new(0));
        let mut started = 0;
        let result = ParallelExecutor::with_spawn_and_stack_size(
            ParallelOptions {
                threads: 4,
                ..Default::default()
            },
            Some(8 * 1024 * 1024),
            |thread| {
                assert_eq!(thread.stack_size(), Some(8 * 1024 * 1024));
                if started == 2 {
                    return Err(std::io::Error::other("injected startup failure"));
                }
                started += 1;
                let completed = Arc::clone(&completed);
                std::thread::Builder::new()
                    .stack_size(thread.stack_size().unwrap())
                    .spawn(move || {
                        EXIT.with(|slot| *slot.borrow_mut() = Some(Exit(completed)));
                        thread.run();
                    })
            },
        );
        assert!(result.is_err());
        assert_eq!(completed.load(Ordering::SeqCst), 2);
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
    fn detached_last_owner_on_worker_retires_without_self_join() {
        const CHILD: &str = "CMG_DETACHED_LAST_OWNER_CHILD";
        if std::env::var_os(CHILD).is_none() {
            // A regression must fail this bounded child, never hang the suite.
            let mut child = std::process::Command::new(std::env::current_exe().unwrap())
                .env(CHILD, "1")
                .args([
                    "execution::lifetime_tests::detached_last_owner_on_worker_retires_without_self_join",
                    "--exact",
                    "--test-threads=1",
                ])
                .spawn()
                .unwrap();
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
            loop {
                if let Some(status) = child.try_wait().unwrap() {
                    assert!(
                        status.success(),
                        "detached retirement child failed: {status}"
                    );
                    return;
                }
                if std::time::Instant::now() >= deadline {
                    child.kill().unwrap();
                    let _ = child.wait();
                    panic!("detached retirement child exceeded 20 seconds");
                }
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
        }
        for threads in [1, 4] {
            let completed = Arc::new(AtomicUsize::new(0));
            let executor = Arc::new(observed(threads, None, Arc::clone(&completed)).unwrap());
            let worker_owner = Arc::clone(&executor);
            let (release, released) = std::sync::mpsc::channel();
            let (finished, finish) = std::sync::mpsc::channel();
            executor
                .with_installed(move |_| {
                    // Intentionally violate the synchronous callback contract
                    // to verify defensive final-owner retirement containment.
                    rayon::spawn(move || {
                        released
                            .recv_timeout(std::time::Duration::from_secs(5))
                            .unwrap();
                        assert!(
                            worker_owner
                                .pool
                                .as_ref()
                                .unwrap()
                                .current_thread_index()
                                .is_some()
                        );
                        drop(worker_owner);
                        finished.send(()).unwrap();
                    });
                })
                .unwrap();
            drop(executor);
            release.send(()).unwrap();
            finish
                .recv_timeout(std::time::Duration::from_secs(5))
                .unwrap();
            // Retirement is asynchronous only on this unsupported path. Prove
            // all actual workers eventually finish their TLS destructors.
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
            while completed.load(Ordering::SeqCst) != threads {
                assert!(
                    std::time::Instant::now() < deadline,
                    "workers did not retire"
                );
                std::thread::sleep(std::time::Duration::from_millis(1));
            }
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
