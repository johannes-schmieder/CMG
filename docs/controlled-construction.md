# Cancellable serial construction

## Shared host execution foundation

With `parallel`, `HostExecutor::new(&pool, worker_ceiling)` borrows an already
admitted Rayon pool without creating workers or retaining an owner. Its
`with_installed` callback runs directly on the coordinator only when that exact
pool is installed. The callback may borrow non-Send coordinator state; nothing
is sent to workers except the cancellation query and the stage kernel. The
callback-scoped `InstalledTeam` cannot escape or enter a hierarchy/workspace.
The existing owned `ParallelExecutor` remains available and has the same scoped
stage interface; its coordinator crosses into its pool and must be Send.

Coordinator callbacks must finish synchronously or join all scoped work;
detached Rayon tasks are outside the supported interface contract. External
owned-executor destruction still joins every worker and its TLS destructors.
If a misbehaving detached callback retains the final executor owner on its own
worker, defensive destruction signals termination and detaches the handles to
avoid self/peer-join deadlock. Only that unsupported path retires workers
asynchronously after its remaining work finishes; it has no immediate join
guarantee and is not an additional asynchronous execution API.

`ControlledStage::admit` validates fixed chunk dimensions. It is
allocation-free, performs no scan and charges nothing. The resulting
`AdmittedStage` is consumed once. Chunk boundaries depend only on the slice
length and the chunk length, never on the number of workers.

The caller must admit and prepare all output and kernel scratch storage before
dispatch, including its coexistence with all live parent objects. `run` splits
an exact-length mutable slice into disjoint logical chunks and recursively caps
terminal lanes at the worker ceiling. It allocates no task, result or scratch
banks. Kernels must do bounded work per chunk, use only admitted disjoint
storage, and cannot allocate, launch nested work or run unbounded adaptive
loops. Caller closures and the Rayon runtime are not allocator-sandboxed.
Runtime and stack allowances remain the host's responsibility.

Every admitted chunk runs unless cancellation or panic intervenes; numerical
errors do not race to stop sibling tasks. All tasks join before return/unwind,
and the first logical chunk error wins. Workers query the cheap, sticky
cancellation signal before every chunk, and the coordinator queries it again
after the join. Cancellation dominates numerical errors after joining and is
reported as `CmgError::Cancelled`. Partial output remains private and must be
discarded after failure. Serial fallback is chosen before numerical execution.
This API adds no graph, hierarchy, sampler or batch optimization and makes no
performance claim.

The package declares an empty root Cargo workspace so a standalone CMG
development checkout nested inside an embedding workspace remains independent.
This does not alter dependency features or the runtime.

## Serial numerical construction

The optional component builder provides
`CmgPreconditioner::build_component_experiment_serial_controlled` under
`experimental-components`. It has the same graph, hierarchy stopping rules,
component options, terminal factor arithmetic, and scalar cycle as
`build_component_experiment_serial_with_budget`. Existing entrypoints retain
their implementations. The controlled path replaces opaque setup traversals
with fallible bounded traversals and replaces sorting with deterministic
heapsort. Edge ordering remains endpoint pair then total-ordered weight;
terminal permutation keys include the original vertex as the final tie-break.
Thus sorting changes record movement without changing arithmetic order. The
terminal factor is chosen by the hierarchy options (`direct_threshold` and the
stopping rules) and memory admission only; no work allowance redirects it.

`Laplacian::from_edges_controlled` also bounds input validation, canonical edge
sorting, compensated duplicate aggregation, and graph metadata construction.
Its input is a borrowed slice, avoiding arbitrary user iterator work inside a
bounded construction operation. The public preconditioner builder borrows an
already constructed graph; callers must include their own input preparation in
their resource protocol or use this graph constructor. Scalar application
storage is prepared with `try_workspace_controlled` after admitting
`workspace_allocation_bytes_controlled`. They share their implementation with
`try_workspace` and `workspace_allocation_bytes`, producing the same layout,
zeroed contents, byte count and error classification; only the cancellation
polls are added. Every level, centering-plan and component-bank loop and every
initialization of a bank (in counted chunks of 128 values) contribute to the
cadence below, every allocation is preceded by a poll, and both queries poll
once more before returning.

## Cancellation contract

Each controlled entrypoint takes a `Cancellation`: a caller-owned, cheap,
allocation-free and sticky query. Kernels count conservative primitive units
(arithmetic, record visits, comparisons and moves) in bounded operations of 1
through 1024 units. Inner tree/root walks, forest splitting, edge merging,
dense/sparse factor updates, sorting comparisons and swaps, and storage
initialization all contain these operations. The serial meter polls the query
whenever the running count crosses a fixed cadence of 65,536 units, regardless
of row, group, pivot or level boundaries, and before every allocation. Work
between two polls is therefore at most the cadence plus one bounded operation.
Units are a polling cadence, not instructions, elapsed time, an admission
budget or a claim of equal cost across phases. Nothing is charged, refused or
reported on the basis of work.

A true query returns `CmgError::Cancelled { phase }`, naming the phase of the
next unstarted operation, through ordinary `Result` propagation. The private
partial candidate is dropped; no partially constructed graph or preconditioner
is returned and the query is not consulted again. Memory admission uses
`MemoryBudgetExceeded` and is checked before the first allocation; allocator
failure uses `AllocationFailed`. Internal invalid state remains an error.

The query runs on the constructing thread (and, in hosted stages, on workers
between chunks). It may block, for example to park a construction, and then
return either value. Neither serial construction nor scalar application creates
a pool or installs a Rayon executor. Allocator, deallocator, query, page-fault,
join and operating-system latency are excluded from the bounded work between
polls. In particular this is not a hard real-time cancellation guarantee or a
bound on cleanup latency. Small Arc allocations retain stable Rust's existing
infallible allocation semantics. Controlled boxed component-label conversion
requires exact capacity so that conversion cannot hide an allocation; excess
capacity is a reported allocation error rather than an unchecked shrink.

Graph and preconditioner memory bounds retain the existing requested-layout
model and its allocator/runtime exclusions. The graph input tuple bank is
borrowed and excluded from the graph constructor's allocation bound. The caller
must account for its coexistence with candidate storage. Scalar workspace bytes
are reported by `workspace_allocation_bytes` (or its controlled form).

## Fixed-cycle symmetry and positivity

The mathematical contract is on each component's compatible quotient/range,
where a positive-weight Laplacian A is positive definite. It does not assert
positive definiteness on the full vertex space containing component constants.
Write S = (1/2) diag(A)^(-1) on nonisolated vertices. The graph energy inequality
A <= 2 diag(A) gives 0 < A^(1/2) S A^(1/2) <= I on that range. Isolates have zero
compatible right-hand sides and carry no positive-dimensional range.

Restriction is the transpose of prolongation P and the coarse graph is the
Galerkin product P^T A P. Pruning deletes only isolated coarse vertices; their
coarse range is empty, so it does not change that identity on the retained
range. Coarse component centering is the orthogonal projection onto its range;
exact restricted compatible residuals already lie in that range. Direct
terminals use a positive-pivot grounded LDL solve with component normalization,
which represents the inverse on the quotient. Other terminals use S, which has
the same positive contraction property. Positive-pivot checks remain errors.

For a coarse inverse C that is symmetric positive and bounded by the coarse
inverse on the range, one symmetric pre/post smoothing cycle has operator

    B = 2S - SAS + (I - SA) P C P^T (I - AS).

In energy coordinates, its error operator is

    I - A^(1/2) B A^(1/2)
      = (I - T) (I - A^(1/2) P C P^T A^(1/2)) (I - T),
    T = A^(1/2) S A^(1/2).

It is positive semidefinite and has eigenvalues strictly below one on the
compatible range. Thus B is symmetric positive and A^(1/2) B A^(1/2) <= I.
Repeating this fixed stationary correction k >= 1 times replaces the energy
error operator E by E^k, retaining those properties. Induction over the stored
hierarchy proves the fixed cycle's quotient-space symmetry and positivity.
Repeat counts are fixed during construction, and the controlled builder leaves
them unchanged. These statements concern exact arithmetic; finite-precision
application and centering retain the existing numerical certification rules.
RHS-dependent hierarchy changes, adaptive repeat counts, a non-transpose
transfer, or arbitrary indefinite/non-Laplacian inputs are outside this argument.

`apply_compatible_into` applies that existing scalar fixed cycle with a prepared
workspace. Its caller must supply a compatible RHS. Controlled construction
adds no adaptive selection during application. The application kernel has no
cancellation hook yet; the caller polls between applications.

## Validation

The controlled unit and integration tests compare complete hierarchy/factor
structures, floating-point factor and cycle bits, and repeat counts with the
existing admitted serial builder. Fixtures include empty, isolated, connected,
disconnected, sparse and dense graphs, nontrivial scales, and all component
option combinations. Unit tests poll after every bounded operation and cancel
at each poll of small graph, forest, contraction, pruning, component, factor
and complete-candidate examples; integration tests cancel at every poll of the
public builders, verify deterministic poll schedules and exercise a blocking
query that parks and then continues or cancels. Workspace tests cancel at every
poll of component, centering and complete scalar workspaces and of the layout
query, observe polls inside each bank's initialization, and then rebuild a
workspace that equals the ordinary one and applies identical cycle bits. An independent allocator
tracer verifies that every actual allocation follows a fresh poll, that the
observed requested-layout peak is within the declared bound, and that
cancellation at each poll releases all candidate storage. These tests support
implementation equivalence and cancellation behavior; the positivity argument
above is independent of sampled numerical positivity.

History: before 2026-10-19 the controlled builders also carried a construction
work ledger with per-boundary callbacks, a work budget, a factor-affordability
preflight, cumulative prefix grants and a scalar-cycle work bound. Johannes
Schmieder removed that optional accounting on 2026-10-19; cancellation and
memory admission are the remaining controls. The 2026-10-13 Mac validation of
the former interface (Rust 1.97.1, 200 tests across 32 groups) and commit
`977b1e3`, which separated zero-sized Arc and vector identity namespaces in the
storage report, remain part of the record.
