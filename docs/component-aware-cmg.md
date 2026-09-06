# Component-aware CMG: first development checkpoint

This opt-in study addresses [issue #2](https://github.com/johannes-schmieder/CMG/issues/2).
The branch starts from `main` at `90e1fe0b0c14065155532711246ede6678bb4935`.
Existing builders, SDDM normalization, automatic routing, and SCC campaigns keep
their existing behavior. This is a local development prototype, not a promoted
algorithm or a platform qualification.

## Current approach

Start with two independent changes that retain one global PCG recurrence:

1. Remove **exactly isolated coarse vertices after contraction**. Preserve every
   parent row and its pre/post smoothing. A collapsed component's coarse range
   has dimension zero; removing that coarse degree of freedom does not mean the
   original component has been solved exactly.
2. Factor disconnected direct terminals one component at a time. Keep the same
   anchor and relative degree ordering within each block, and store the final
   factors in shared buffers. One-vertex grounded blocks use their scalar pivot
   without a dense allocation. No heavy solver or executor is constructed per
   component.

Both switches are under `experimental-components` and are off by default:

```rust
# #[cfg(feature = "experimental-components")]
# fn example(graph: &cmg::Laplacian) -> Result<(), cmg::CmgError> {
use cmg::{CmgOptions, CmgPreconditioner, ComponentBuildOptions};
let preconditioner = CmgPreconditioner::build_component_experiment(
    graph,
    CmgOptions::default(),
    ComponentBuildOptions {
        prune_coarse_isolates: true,
        factor_terminal_components: true,
    },
)?;
// Use the existing certified solve_pcg / caller-workspace / planned interfaces.
# Ok(())
# }
```

Keep baseline, pruning-only, block-LDL-only, and combined builds available. These
separate reduced vector work, changed hierarchy/terminal behavior, and dense
factorization work. A component portfolio with independent Krylov recurrences
is deferred until residual costs justify its larger ownership and scheduling
changes. A block-diagonal collection of fixed component preconditioners under
one global PCG remains another possible intermediate design.

## Why this addresses the observed pathology

The original direct threshold uses the complete level vertex count. A graph
with 1,001 connected components can never drop below the default threshold of
700 while retaining one representative per component. A material component
therefore continues coarsening, with tiny representatives scanned repeatedly.
Recursive repeats multiply these scans even when setup and PCG iteration counts
look ordinary.

For the 4,096-vertex path plus 1,000 independent pairs, the baseline dimensions
are `6096, 2024, 1256, 1064, 1016, 1004, 1001`. Pruning gives
`6096, 1024, 256`. The baseline's nonterminal cycle-weighted vertex count is
502,592, of which 484,000 represent isolated vertices. The candidate count is
10,192 with no isolated vertices at nonterminal coarse levels. This is a work
model, not a count of all arithmetic instructions or all finest-level scans.

Block factorization addresses an independent threshold cliff. With 349 pairs
the baseline immediately factors a 349-dimensional grounded matrix; with 350
pairs it coarsens first and factors an empty grounded matrix. Per-component
factorization reduces dense arithmetic from `O((sum d_c)^3)` to
`O(sum d_c^3)`, with dense temporary storage bounded by the largest block.
It does not remove finest-level PCG operations.

## Transfer and numerical contract

`Aggregation` continues to describe a complete partition with valid coarse
indices for every fine vertex. A pruned level instead holds `PrunedTransfer`, a
flat ordered list of `(fine_vertex, active_coarse_vertex)` entries. It retains
both the original aggregate count and the active child dimension. The full
aggregation is released; there are no sentinel indices presented as ordinary
coarse vertices and no inactive child vectors.

`HierarchyLevel::aggregation()` is `None` on a pruned level; inspection code must
check `pruned_transfer()`. `graph()` and the existing vertex-count report always
describe the actual stored graph. The transfer exposes the pre-pruning coarse
count separately. The experimental benchmark handles both kinds; existing SCC
exporters have not been extended to accept this experimental representation.

If `E` selects nonisolated coarse vertices, the transfer is `R_a = E R`, its
prolongation is `R_a^T`, and the child graph is exactly `R_a L R_a^T`. Only whole
isolated coarse vertices are removed, using exact zero degree, never a weight
threshold. Within each surviving aggregate the summation order is unchanged.
The compact graph preserves vertex order and positive edge values.

The existing symmetric pre/post damped-Jacobi cycle, coarse component centering,
and fixed recursive repeat counts remain in use. Build-time pruning changes
which terminal is reached and can change repeat counts; equality to the old
preconditioner is not promised. No inner tolerance-stopped solve, RHS-dependent
route, timing probe, or flexible preconditioner is introduced. Positivity and
symmetry are on the component-compatible quotient space; intermediate grounded
corrections need not have zero mean. Final PCG centering and original-system
certification remain authoritative.

The block factor's exposed permutation is grouped by deterministic component
order. Its local degree order and highest-index anchors match the baseline.
One-component builds retain the ordinary factor path. New sparse factor buffers
discard spare capacity before retention. Pruned-transfer retained storage is
included in hierarchy reports; ordinary workspace sizing follows actual compact
child dimensions. The workspace budget still has its documented meaning: it
does not admit total process RSS or every setup allocation.

## Validation and experiment

`tests/component_experiment.rs` covers:

- connected/disabled hierarchy and application identity;
- expected retirement on the mixed regression fixture;
- transpose and Galerkin identities for partial transfers and dimension errors;
- range linearity, symmetry, positivity, and dirty-workspace reuse;
- empty/all-isolated graphs, empty coarse levels, and incompatible isolated RHSs;
- grounded reference agreement with interleaved components;
- independently certified known solutions and heterogeneous component weights;
- no correction leaking into an untouched component;
- prepared numerical generations, current-operator certification, and rejection
  of unrelated topology lineage;
- deterministic planned execution with 1/2/4 threads and a single-workspace
  concurrency budget.

`component-bench` supplies connected paths/grids, material-plus-pairs,
material-plus-isolates, two material components, all-pairs, all-triangles, and
both sides of the direct threshold. It rotates the four arms and records raw
paired samples after two warm-up rounds. Total includes setup, PCG workspace
allocation, solves, normal certification, and result allocation. Extra external
verification is performed for every sample. Structural work and retained bytes
are reported separately; setup peak/RSS and allocation-free execution have not
been qualified by this harness.

See `benchmarks/README.md` for the exact command. Small local results are
development evidence, not production thresholds or cross-platform speed claims.

## Remaining design work

- Pruning currently occurs after an accepted contraction. Finest-level isolates
  and ordinary pre-contraction stagnation/fill/maximum-level decisions remain.
  General component-local direct or stalled terminals are not implemented.
- Finest-level matvec, projection, vector updates, and global stopping still
  include all components. Sparse RHS skipping and per-component stopping are
  portfolio opportunities, not benefits of this prototype.
- Setup is serial. Existing planned application works, but partial-transfer
  prolongation currently uses a deterministic serial scatter. A single package
  executor still controls any parallel work.
- Qualify additional weighted, bridge-heavy, dense and heterogeneous graphs,
  threshold-adjacent material cases, fresh wirings, larger sizes, and multiple
  RHSs before setting policy. Benchmark a separately compiled baseline as well
  as the in-binary reference before promotion. Do not start with a giant
  Cartesian SCC campaign.
- Assess setup allocation failure/peak accounting and downstream report/API
  consumers before proposing production integration. The generic component
  partition and component scheduler in issue #2 remain future work.
- Keep SDDM pre-augmentation partitioning separate. Mixed singular/nonsingular
  systems need a deliberate gauge contract: the existing global extraction can
  shift a singular block by the unrelated augmentation vertex. For
  `diag([[1,-1],[-1,1]],[2])` and RHS `[1,-1,4]`, current monolithic extraction
  gives `[1.5,0.5,2]`, whereas separately centered blocks give `[0.5,-0.5,2]`.
  Both have zero residual. Raw solution equality is not a valid acceptance gate
  without specifying the common normalization.
- Independent component PCG, if added, needs explicit absolute-tolerance
  allocation, global certification, iteration-budget semantics, and fail-closed
  error aggregation. Fixed component cycles under global PCG avoid that redesign
  in this checkpoint.
