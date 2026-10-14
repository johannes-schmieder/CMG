# Controlled serial construction

The optional component builder provides
`CmgPreconditioner::build_component_experiment_serial_controlled` under
`experimental-components`. It has the same graph, hierarchy stopping rules,
component options, terminal factor arithmetic, and scalar cycle as
`build_component_experiment_serial_with_budget`. Existing entrypoints retain
their implementations. The controlled path replaces opaque setup traversals
with fallible bounded traversals and replaces sorting with deterministic
heapsort. Edge ordering remains endpoint pair then total-ordered weight;
terminal permutation keys include the original vertex as the final tie-break.
Thus sorting changes record movement without changing arithmetic order.

`Laplacian::from_edges_controlled` also bounds input validation, canonical edge
sorting, compensated duplicate aggregation, and graph metadata construction.
Its input is a borrowed slice, avoiding arbitrary user iterator work inside a
bounded construction operation. The public preconditioner builder borrows an
already constructed graph; callers must include their own input preparation in
their resource protocol or use this graph constructor. Preparation of scalar
application storage is available through `try_workspace_controlled`.

## Admission and stopping contract

`BuildControl::checkpoint` receives a `BuildBoundary` before each proposed
operation. `phase` is a deterministic source phase and `chunk_id` is a checked,
zero-based ordinal across work and allocation operations. `charged_work` is the
previously admitted work allowance. Each entrypoint starts a new ordinal and
work counter. A caller composing graph, hierarchy, and workspace construction
can use one callback-owned ledger to account across those calls.

`Work { units }` precharges a conservative allowance of 1 through 1024 primitive
arithmetic, record visit, comparison, and move units. Inner tree/root walks,
forest splitting, edge merging, dense/sparse factor updates, sorting comparisons
and swaps, and storage initialization all contain these boundaries. Scalar
initialization and checked memory-bound formulas are also precharged. Units are
an implementation accounting convention, not instructions, elapsed time, or a
claim of equal cost across phases. A future implementation must maintain the
per-chunk bound; changing charges or loop grouping changes the deterministic
boundary trace and needs review.

`Allocation { bytes }` gates every allocation separately. Work counters and the
fixed work budget are checked before calling the control callback. A denied
chunk performs none of its proposed work. `StopUnused`, `Interrupted`, work-budget
exhaustion, and checked counter overflow remain distinguishable through
`CmgError::BuildStopped`. Memory admission uses `MemoryBudgetExceeded`;
allocator failure uses `AllocationFailed`. Internal invalid state remains an
error rather than becoming a candidate-policy rejection.

Callbacks run on the constructing thread and may block for a grant or drain.
Neither construction nor scalar application creates a pool or installs a Rayon
executor. A stop returns through ordinary `Result` propagation and drops the
private partial candidate; no partially constructed preconditioner is returned.
No callback occurs after the stop. Allocator, deallocator, callback, page-fault,
and operating-system latency are excluded from the computational chunk bound.
In particular this is not a hard real-time cancellation guarantee or a bound on
cleanup latency. Small Arc allocations retain stable Rust's existing infallible
allocation semantics. Controlled boxed component-label conversion requires exact
capacity so that conversion cannot hide an allocation; excess capacity is a
reported allocation error rather than an unchecked shrink.

Graph and preconditioner memory bounds retain the existing requested-layout
model and its allocator/runtime exclusions. The graph input tuple bank is
borrowed and excluded from the graph constructor's allocation bound. The caller
must account for its coexistence with candidate storage. Scalar workspace bytes
are reported by `workspace_allocation_bytes`; its level scan is ordinary work,
so a caller operating inside its own bounded protocol must charge that scan.

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
adds no adaptive selection during application and does not itself bound or
interrupt the application kernel.

## Validation

The controlled unit and integration tests compare complete hierarchy/factor
structures, floating-point factor and cycle bits, and repeat counts with the
existing admitted serial builder. Fixtures include empty, isolated, connected,
disconnected, sparse and dense graphs, nontrivial scales, and all component
option combinations. Separate tests cover every boundary on small graph,
forest, factor, pruning, workspace and complete-candidate examples; verify
budget rejection before callbacks; compare deterministic traces; and exercise
blocking park/grant/drain control. These tests support implementation equivalence
and stopping behavior; the positivity argument above is independent of sampled
numerical positivity.

On 2026-10-13, Mac validation with Rust 1.97.1 passed `cargo test --offline
--all-features` (200 tests across 32 test groups), `cargo check --offline`,
`cargo clippy --offline --all-features --all-targets -- -D warnings`, formatting,
and `git diff --check`. The independent allocator tracer verifies every actual
allocation/reallocation has a matching gate, observed requested-layout peak is
within the declared bound, and cancellation at each allocation gate releases
all candidate storage. Existing admission tests initially reproduced a baseline
storage-report undercount: a zero-sized Arc payload address could equal an
adjacent vector allocation base. Commit `977b1e3` separates those identity
namespaces, adds a deterministic regression, and restores those checks. No
performance or cross-platform qualification claim follows from this validation.
