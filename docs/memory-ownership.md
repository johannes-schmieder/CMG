# CMG memory ownership and admission

The new capacity reports and narrowly scoped admission APIs supplement, rather
than redefine, `CmgMemoryEstimate`, `CmgMemoryReport`, and the repeated-PCG
principal-array interfaces. **Complete construction and runtime admission is
still unavailable.** `complete_build_peak_bytes()` and
`ParallelExecutor::runtime_allocation_bound()` return `None`; the corresponding
strict admission methods reject before allocation. Do not use the principal
estimate or this allocation inventory as a whole-action memory certificate.

## Retained ownership

`CmgStorageReport::measure(&[&owner, ...])` walks private ownership fields and
counts the union of live allocations. It covers ordinary and component
hierarchies, terminal factors, prepared topology/current graphs, plans,
workspaces and executor handle banks. `Vec<T>` roots include spare outer
capacity as well as nested owners; passing two live banks counts their overlap.
Supported `Vec<f64>` and nested numerical banks can also be supplied explicitly
for caller-owned outputs. Borrowed input/output slices are never implicitly
charged. Root values themselves are inline, not separate heap allocations.

The report has two disjoint categories:

* `capacity_bytes`: requested backing capacity for vectors, boxes and strings,
  including descriptor banks, compact buffers retaining larger capacity, lazy
  aggregation label/size caches, and error strings in batch workspace outcomes.
* `arc_allocation_bytes`: the Arc allocation containing its inline payload and
  two atomic reference counts, padded using `Layout`. This is the reviewed Rust
  `ArcInner<T>` model, not a promised stable standard-library ABI. It is tested
  against requested allocations on the validation platform.

Repeated references are de-duplicated by live allocation address. This includes
the finest graph shared with its hierarchy, topology/components shared by
numeric generations, and plan lineage references. Cloned topology maps are
separate allocations even though their component/lineage Arcs are shared.
No observation-sized table, hash set, or other reporting allocation is made.
The current traversal is quadratic in the number of allocation descriptors;
it never scans scalar vector payloads. Report at preparation/retirement
boundaries, not inside numerical kernels, and quiesce other users that could
initialize shared `OnceLock` caches during a report.

Do not add the old principal-array report or fereg's retained-container
supplement to this report: those bytes overlap. Include every still-live old
numeric frame, hierarchy, plan and bank in one measurement. Add independently
owned caller parents, outputs, allocator overhead and runtime resources once.
If measuring an incremental new frame, subtract the union of its already
charged parents, not each object's standalone report.

## Scoped pre-allocation admission

Budgets below refer to **requested allocation layouts**, excluding allocator
metadata, size-class rounding, retained allocator pages and OS resources. They
are not allocator hard caps. Existing parents are borrowed and excluded; the
caller must reserve them before supplying the remaining incremental budget.

| Operation | Before-allocation query and entry point | Covered storage |
|---|---|---|
| Prepared topology | `preparation_allocation_bound`, `prepare_with_budget` | indexed endpoint input, four retained maps, union/find/component scratch, new Arc blocks |
| Assembly scratch | `PreparedLaplacianWorkspace::allocation_bytes`, `new_with_budget` | maximum duplicate-weight buffer |
| Numeric generation | `numeric_allocation_bytes`, `assemble_with_workspace_budgeted` | canonical edges, diagonal, two Arc<Vec> blocks, fresh numeric lineage |
| One batch workspace | `PcgBatchWorkspace::allocation_bytes`, `new_with_budget` | PCG, CMG and component scratch, gather/guess arrays, nested descriptor banks |
| Fixed RHS bank | `bank_allocation_bytes`, `new_bank_with_budget` | every workspace plus the outer capacity bank |
| Serial CSR | `CsrLaplacian::construction_allocation_bound`, `from_laplacian_with_budget` | new row/column/weight arrays and simultaneous cursor bank |
| Parallel plan | `ParallelCmgPlan::construction_allocation_bound`, `build_with_budget` | descriptor banks, earlier retained operators, current serial/parallel CSR temporaries; **not Rayon runtime** |

All size arithmetic is checked, including `Layout::array`'s addressable-size
limit. Budgets are checked before the first covered allocation. Fixed banks use
fallible exact reservation and construct elements one at a time. A failed
element releases all previous elements. An old bank remains the caller's owner:
reserve old plus new bytes before replacement, then drop the old bank before
admitting another action. A fixed bank avoids the automatic solver's lazy
growth; concurrency is still selected using its existing workspace policy.

Prepared topology accepts a slice at the admitted boundary so an arbitrary
iterator cannot violate the declared input count. Its bound sums the live
indexed input `(u64, usize)[m]`, canonical keys `u64[m]`, offsets
`usize[m+1]`, two input maps `usize[m]`, and four component arrays
`usize[n]` (parents, root labels, labels, sizes). Adding the two retained Arcs
is conservative even when scratch retires earlier. No sort heap scratch is
needed by its unstable sort. Numeric assembly reuses duplicate scratch and
adds only its new graph allocations. Old frames and the topology remain
separately live. Stable Rust still lacks fallible `Arc::new`: these small Arc
allocations can abort on allocator exhaustion. The admitted byte model is not
a promise that every historical constructor has become fallible.

Workspace queries follow the same dimensions as their constructors: six PCG
vectors, seven-vector compatibility scratch, the CMG projected RHS and
compatibility scratch, per-level residual/coarse/factor vectors, coarse
centering scratch, dimension metadata and the outer banks. Two additional
vectors stage batch RHSs/guesses. Independent allocation tests check exact and
one-byte-short budgets, overlap, zero reporting allocations, spare capacities,
overflow and every injected allocation failure in fixed-bank construction.

### CSR and plan construction proof

CSR construction now fallibly reserves its known-size banks before filling;
prefix sums reuse the counts as scatter cursors. Serial peak is retained row
offsets, columns and weights plus `usize[n]` cursors. The compact/native offset
choice depends on `2*m`, independently of the compact/native column choice
depending on `n`. Both choices use checked layouts even for dimensions too large
to allocate in tests. Existing graph/lineage allocations are borrowed, not new.

Parallel construction preserves its density/thread/length routing and row
partition. Let `R` be row-offset bytes, `U=sizeof(usize)`, `N=(n+1)*U`, `E=m*U`,
`C` the new retained CSR layouts and `B` the exact row-fill descriptor bank.
The bound takes the maximum of sequential stages:

* counting/first prefix sum: `R + 3*n*U`;
* auxiliary prefix sums: `R + 2*N + 2*n*U`;
* incoming-edge scatter: `R + 2*N + n*U + E`;
* row fill: `C + 2*N + E + B`.

The row-fill descriptor count is `ceil(n / work_chunk_len(n))`; its capacity is
reserved once rather than grown. All CMG-owned allocations occur on the caller
before parallel fill. Rayon task/queue allocations remain explicitly uncovered;
the presence of a finite CMG-owned layout bound does not certify that runtime.

Plan construction reserves both level descriptor banks first, then builds levels
in their original order. At each eligible level, add its construction bound to
both descriptor banks and all earlier retained operators; take the maximum
across levels. Shared lineage clones allocate nothing. Partial failure drops all
earlier operators and both banks. The profiling-only result adds its own profile
bank and is outside this admitted entry point. Old plans remain separately live.

`ParallelPcgSolver::plan_construction_allocation_bound` and
`try_plan_with_budget` expose the same boundary inside the solver's existing
lazy-plan owner. After initialization the required incremental bytes are zero.
This avoids a second plan/pool. It does not change automatic routing or impose
admission on other, unbudgeted solver methods. Callers must initialize the plan
at their admitted preparation boundary before relying on allocation-free reuse.

Independent allocator fixtures check the serial CSR and eligible serial-CSR
plan peaks against these formulas, one-byte-short rejection before allocation,
all fallible allocation failures, repeated plan initialization and old/new plan
overlap. Dense parallel CSR numerical identity is checked separately. Runtime
activity is not silently subtracted from a sampled parallel process peak.

## Fill guard proof and remaining construction scopes

The builder adds the current level's nonzeros before comparing the cumulative
count to `factor * initial_nnz`. It can therefore retain a crossing level.
Direct and full-contraction terminals can also exit before that comparison.
Every contraction preserves nonnegative graph weights and cannot increase
nonzeros, so one additional finest-sized level bounds the crossing/unchecked
terminal. The estimate rounds the floating threshold outward and also applies
the independent `max_levels * initial_nnz` bound. A path fixture with fill
factor one demonstrates actual `StagnatedFill` and a retained sum larger than
the old factor-only bound. This establishes the defective sub-bound; it does
not claim that every old aggregate estimate was exceeded on that fixture.

The builder's nonzeros exclude isolated diagonals. Consequently nonzeros do
not bound the vertex vectors of levels retaining isolated representatives.
The estimate uses `vertices * max_levels` for that separate dimension; pruning
only reduces it. This is deliberately pessimistic, especially for connected
graphs, and can reject more work than the previous estimate. Principal
hierarchy allowances cover inverse diagonals, component metadata and both
aggregation representations/caches. Finest workspace allowances include both
CMG and PCG compatibility scratch; they must cover all-isolated inputs even
with `max_levels=1`. Zero-thread requests resolve available parallelism rather
than being estimated as one worker. For reproducible admission, pass the
already chosen explicit thread count.

Prepared keys and group offsets retain their input-sized reserved capacities,
even when many duplicate inputs canonicalize to one edge. The repeated estimate
now uses input counts for those two maps; a duplicate-rich regression would
exceed the previous canonical-count sub-bound.

The proof applies to positive finite fill factors and every termination reason
(direct, full contraction, vertex stagnation, fill stagnation, level limit),
including the preserved-stopping and active-count experiments. It changes no
stopping decision, repeat count or numerical reduction.

The following inventory identifies **unresolved peak scopes** rather than
certifying a whole-build bound:

| Stage | Simultaneous allocations that a full bound must cover |
|---|---|
| Ordinary edge ingestion | iterator-owned input, growing raw edge bank, diagonal, cached endpoint-sort keys, old/new buffers during growth/shrink, Arc finalization |
| Components and forest | retained parents plus parent/root/label/size arrays; heavy-parent/selected-weight vectors; forest split ancestors, indegrees, visited flags and growing path buffers |
| Contraction and pruning | all retained levels plus mapped edge bank, sort scratch, coarse diagonal, aggregation, isolate map, compact graph and transfer entries; parallel result-collection banks |
| Ordinary LDL | component/permutation/pattern maps, matrix and lower dense row banks, diagonal and packed/sparse retained conversion simultaneously |
| Component LDL | component offsets and edge grouping, factor index, one block's sparse column/row links and work arrays, accumulated rows from earlier blocks, final packed or transposed sparse conversion |
| Plans outside scoped entry points | profiled-result banks, concurrent independent callers and Rayon runtime; new ordinary-plan CMG layouts are covered above, not full solver construction |
| Automatic repeated solves | old workspace bank plus newly allocated descriptors/workspaces, lazy plan setup, owned-result banks and per-chunk Result buffers |
| Failure/retry | already retained parents, partially built current owners and any separately retained failed attempt; errors do not make those parents disappear |

Sequential stages use a maximum only **after** adding their common live
parents. Replacements and final factor conversion require sums. The existing
principal estimate does not prove these full overlaps; the downstream
construction supplement remains incomplete too. A complete fallible build
arena/provisioning design and a checked capacity model for remaining growth
paths remain necessary. Ordinary ingestion/forest/contraction currently use
data-dependent growth and library sort/collection allocation; terminal LDL
converts among simultaneously live row/column/pattern representations. A full
implementation must either replace those with fallibly reserved bounded banks
or carry a stage-specific allocation ledger through every ordinary and component
builder, including failed partial owners and conversion overlap. This is a broad
cross-builder change, not a constant multiplier or a consequence of the new
plan bound. No weak-graph solver alternative was removed to simplify inventory.

## Runtime scope and lifetime

The lockfile pins Rayon 1.12.0, rayon-core 1.13.0, crossbeam-deque 0.8.7,
crossbeam-epoch 0.9.20 and crossbeam-utils 0.8.22. Rayon creates worker and
broadcast deques, registry/thread descriptors, sleep state and an injector.
Crossbeam workers begin with 64 slots, dynamically resize, and defer old-buffer
reclamation through the epoch collector. The injector adds linked blocks as
jobs arrive. Public thread-count/stack options do not provision or cap those
heaps. Thread spawn adds native runtime resources and TLS outside these layouts.
Thread joining does not force all process-global deferred reclamation or
allocator caches to return memory to the OS.

The executor report counts only its CMG-owned join-handle capacity and sets
`runtime_heap_unbounded=true`. Strict runtime admission rejects before spawning
any worker. The ordinary constructor remains available under an explicitly
unbounded-runtime policy. Joined shutdown supplies an actual worker lifetime
boundary; it does not resolve the runtime peak. Introducing a bounded executor
would require a separately validated scheduling/provisioning design. No dynamic
allocator hard limit is installed, because ordinary infallible allocations
could otherwise abort the host.

This work is not sufficient to recommend memory-certified fereg adoption.
Retained container reconstruction can be replaced by the new union report, and
workspace/prepared-stage supplements can use the scoped APIs. Full setup,
runtime, allocator/OS and all fereg-owned construction/inference/recovery/
publication scopes still require their own coverage. Historical component
performance gates and downstream development gates remain separate.
