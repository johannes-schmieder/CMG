# Controlled serial construction

The optional component builder provides
`CmgPreconditioner::build_component_experiment_serial_controlled` under
`experimental-components`. It has the same graph, hierarchy stopping rules,
component options, terminal factor arithmetic, and scalar cycle as
`build_component_experiment_serial_with_budget`. Existing entrypoints retain
their implementations. The controlled path replaces opaque setup traversals
with fallible bounded traversals and, by default, replaces sorting with
deterministic heapsort. Edge ordering remains endpoint pair then total-ordered weight;
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

In default mode, `Work { units }` precharges a conservative allowance of 1 through 1024 primitive
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

## Bounded metadata and first-application reservation

`workspace_allocation_bytes_controlled` evaluates the same requested-layout
formula as `workspace_allocation_bytes`, with separate bounded work gates for
each level and centering plan and no allocations. Use it when the metadata scan
is itself inside an optional preparation budget.

`scalar_cycle_work_bound_controlled` computes a checked primitive-work allowance
for one `apply_compatible_into` call with a valid workspace. The metadata query
is also gated per level. For a level with n vertices, m edges and c child
vertices, one nonterminal stationary iteration is bounded by

    D = 256 + 64 n + 64 m + 64 c.

This covers up to three edge matvecs (the first iteration needs only two), both
smoothing sweeps, restriction/prolongation, coarse component centering and
fixed bookkeeping. Transfers contain at most n entries and coarse component
counts are at most c. The construction query evaluates the stored recursive
repeat counts bottom-up: a nonterminal call with k iterations costs at most
k times (D plus its child call bound). A zero-dimensional child is skipped.
Terminal calls ignore k, as the application kernel does. A diagonal terminal
costs at most 64 + 8 n. A direct terminal costs at most 256 + 32 n + 32 s,
where s is the actual strict factor storage traversed once in each triangular
solve. For packed factors this includes explicitly stored zero slots; the
numerical nonzero count cannot bound packed traversal. At the top, 128 plus
128 times the level count covers workspace/public validation. These are
conservative scalar arithmetic, record-access, comparison and move allowances,
not instruction or elapsed-time estimates. Compiler loop/runtime mechanics
and callback/OS latency are not an instruction-count contract.

Checked overflow declines the query through `BuildStopped::CounterOverflow`;
a wrapped or saturated underestimate is never returned. The allowance excludes
caller gather/scatter, RHS preparation, coefficient reconstruction, original
residual certification and solver restart work. A handoff caller must reserve
those costs separately before starting the first cycle. Metadata tests cover
known direct factors including packed zeros, multilevel repetition, checked
overflow, deterministic cancellation and work-budget denial.

## Cumulative grants and retained construction

`BuildPrefix` is an allocation-free controller ledger. It starts with a fixed
full allowance B and grant G=0. `grant(G)` accepts only monotone cumulative
values within B. `admit` precharges each complete operation or returns `Park`
without changing its charged work or ordinal. A repeated parked admission must
supply exactly the same boundary. The constructing callback blocks on its
existing worker stack; returning and rebuilding is not a continuation.
Entrypoint-local counters may reset while the prefix's global task/ordinal
identity continues across input preparation, graph, hierarchy and workspace.
Callers must route their own preprocessing and wrapper construction through the
same ledger and pass the full cap, rather than G, to each CMG constructor.

`drain_issued_prefix` freezes the current grant. The first chunk that cannot fit
is retained as the pending frontier, with unused grant remainder intact.
`PrefixSnapshot::drained()` checks the pending price against the current grant;
pending identity alone cannot acknowledge draining after a newer grant.
Allocation gates have zero computational price and remain in their original
position before that frontier. `abandon_unissued_suffix` runs the same issued
prefix before returning `StopUnused`. It cannot truncate issued work based on
when the command arrives. A chunk that cannot fit even B terminates with
`BudgetExhausted`. `interrupt` requests Break immediately; an earlier fatal
outcome is still retained for diagnostics. `finish` records completion or a
fatal kind. The last admitted operation identifies an executed failure site;
a pending boundary identifies an unexecuted operation, not a hypothetical fault.

The ledger supplies neither an executor nor synchronization. The enclosing
service owns commands, blocking, the persistent Low-session lifetime, logical
certificate/epoch order and terminal arbitration. Completion and fatal outcomes
never release a worker themselves. The service must hold its existing worker
until its prescribed ownership boundary and join before publication or rollback.
Repeated full construction under differently sized grants has identical admitted
boundary traces, hierarchy/factors and scalar-cycle outputs.

A service can preallocate its fixed synchronization and owned inputs before
launch, then admit later fallible typed allocations at stable logical gates.
This is distinct from preallocating all hierarchy arenas before launch. It
preserves the bounded work and requested-layout admission contract, not a bound
on allocation latency or schedule independence of real operating-system OOM.
Successful arithmetic and faults injected at fixed logical gates must remain
repeatable. Existing small infallible Arc allocations retain the limitation
stated above; a caller requiring every runtime allocation to be fallible must
account for that separately before claiming overlap eligibility.

## Prefix witness scope

The focused Rust 1.85.1 prefix checks exercise three state-machine witnesses:
non-fitting chunks retain their grant remainder across drain/resume; cap refusal,
malformed chunks and Break remain distinct; and an executed allocation failure
has a stable logical identity while a later Break preserves its fatal record.
The drain witness includes a newer grant followed by drain before the worker
retries, preventing stale pending state from acknowledging unfinished work.

A separate threaded witness constructs the graph, hierarchy and scalar workspace
on one retained stack under grants of 257 and 4096 units. Its complete admitted
operation trace, hierarchy, terminal factor and scalar cycle match the full-grant
reference. Strict library/controlled-build Clippy and formatting also pass.
These are construction witnesses, not qualification of a host executor lease,
P-1 foreground reductions, independent foreground certification or Low-session
ownership. Those integration properties require their own witnesses.

The successful-run guarantee assumes fixed input and successful allocation
outcomes. Faults injected at the same logical gate must preserve prescribed
prefixes and terminal arbitration under schedule perturbation; the host service
must test that contract across both tasks. Exogenous operating-system OOM may
occur at a different gate under a different schedule, so identical real-OOM
routes are not promised. Neither early completion nor a fatal result authorizes
returning a leased worker early. Drain reaches the issued frontier or an intrinsic
terminal event; explicit Break may truncate it. The host joins before publishing
or rolling back and only then transfers worker and candidate ownership.


## Opt-in cooperative batching

`BuildControl::cooperative_work_batch()` defaults to zero, retaining the original
frontiers and heapsort. A nonzero request (clamped to at least 1024 and at most
65535) enables a prepaid work reservoir and allocation-free introsort. This is
a cancellation-only alternative, not the primitive-frontier `BuildPrefix`
contract. An operation consumes its allowance before execution; replenishment
respects the remaining budget and may be smaller than the configured reservoir.
Unused allowance remains charged, so reported work is reserved allowance rather
than an exact count of executed primitive operations. Allocation gates remain
unconditional, even while computational allowance remains available.

At most the configured allowance can execute between callbacks; allocator,
cleanup, callback and OS latency remain excluded. A reservation may span source
phases, so its phase names the first operation requesting replenishment, not
every covered operation. Dense-factor chunks preserve term evaluation order.
Introsort charges pivot selection, scans and swaps, recurses on the smaller
partition, and uses the existing metered heapsort if the depth budget expires.
The graph and terminal keys retain their canonical total orders. Tests cover
all component option combinations, complete hierarchy/factor/repeat equality,
application bits, adversarial sort inputs, budget tails and interruption or
StopUnused at every boundary of a small complete candidate.
