from pathlib import Path
import json
import subprocess

ROOT = Path.cwd()
SELF = Path("scripts/repository_cleanup.py")
WORKFLOW = Path(".github/workflows/cleanup-repository.yml")

KEEP_WORKFLOWS = {
    "rust.yml",
    "performance.yml",
    "parallel-performance.yml",
    "c-kernel.yml",
    "manual-32-thread-qualification.yml",
    "cleanup-repository.yml",
}

KEEP_PERFORMANCE = {
    ".ci/performance/latest.json",
    ".ci/performance/full-pcg-routing-latest.json",
    ".ci/performance/parallel-latest.json",
    ".ci/performance/c-kernel-latest.json",
    ".ci/performance/cumulative-latest.json",
}

OLD_ROOT_DOCS = {
    "PLAN.md",
    "PERFORMANCE_PLAN.md",
    "PERFORMANCE_STATUS.md",
    "PERFORMANCE_GUIDE.md",
    "LARGE_SCALE_QUALIFICATION.md",
    "UPSTREAM.md",
}

README = r'''# CMG in Rust

A deterministic Rust implementation of stationary Combinatorial Multigrid
(CMG) for weighted graph Laplacians and symmetric diagonally dominant
M-matrices (SDDM), with certified preconditioned conjugate-gradient solves.

The implementation follows the official `ikoutis/cmg-solver` source pinned at
commit `19752fc102f8cae8e34f66457bfaccb1aaa60375`. See
[`docs/UPSTREAM.md`](docs/UPSTREAM.md) for provenance and routine coverage.

## Status

The stationary CMG path is implemented and tested on Linux, macOS, and Windows.
The suite covers exact small systems, disconnected graphs, weighted adversarial
cases, deterministic hierarchy construction, SDDM augmentation, terminal
factorization, repeated right-hand sides, and original-system residual
certification.

The default crate has no parallel runtime dependency. Optional multicore
support uses a package-owned Rayon pool behind the `parallel` Cargo feature.
Functional thread-pool coverage extends through 32 threads; controlled
8/16/32-core performance qualification still requires suitable hardware.

## Quick start

For the weighted path

```text
0 --1-- 1 --1-- 2
```

the Laplacian is

```text
L = [ 1 -1  0 ]
    [-1  2 -1 ]
    [ 0 -1  1 ]
```

and `b = [1, 0, -1]` has the zero-mean solution `x = [1, 0, -1]`.

```rust
use cmg::{CmgOptions, CmgPreconditioner, Laplacian, PcgOptions, solve_pcg};

fn main() -> Result<(), cmg::CmgError> {
    let graph = Laplacian::from_edges(
        3,
        [(0, 1, 1.0), (1, 2, 1.0)],
    )?;
    let rhs = [1.0, 0.0, -1.0];

    let cmg = CmgPreconditioner::build(&graph, CmgOptions::default())?;
    let result = solve_pcg(&graph, &cmg, &rhs, PcgOptions::default())?;

    println!("x = {:?}", result.solution());
    println!("iterations = {}", result.iterations());
    println!("backward error = {:.3e}", result.backward_error());
    Ok(())
}
```

Run the complete example with:

```bash
cargo run --example laplacian_pcg
```

A graph Laplacian is singular. Each connected component of the submitted
right-hand side must sum to zero within the configured compatibility tolerance.
Solutions use a deterministic component-wise zero-mean normalization.

## Parallel and repeated-RHS solves

Enable multicore support with:

```toml
cmg = { git = "https://github.com/johannes-schmieder/CMG", features = ["parallel"] }
```

For application code, `ParallelPcgSolver` is the preferred high-level API. It
owns the reusable hierarchy, selectively routed parallel plan, package-owned
thread pool, and reusable workspaces. It chooses among serial PCG, planned
within-solve PCG, and memory-bounded concurrency across independent right-hand
sides.

The default single-RHS routing threshold is **350,000 canonical retained
edges**. This is a measured performance heuristic, not a mathematical CMG
constant, and can be overridden through `ParallelPcgPolicy`.

```rust,ignore
use cmg::{CmgOptions, ParallelOptions, ParallelPcgSolver, PcgOptions};

let solver = ParallelPcgSolver::build(
    &graph,
    CmgOptions::default(),
    ParallelOptions {
        threads: 16,
        workspace_memory_budget_bytes: Some(8 * 1024 * 1024 * 1024),
        ..ParallelOptions::default()
    },
)?;

let mut workspace = solver.workspace();
let batch = solver.solve_batch_with_workspace(
    &right_hand_sides,
    PcgOptions::default(),
    &mut workspace,
)?;
println!("execution = {:?}", batch.report().execution());
```

Reuse the same solver and workspaces whenever the graph and weights are
unchanged. For many RHSs, across-RHS parallelism is generally the lowest-
overhead strategy when memory permits.

## Performance

The optimization campaign substantially reduced both runtime and memory versus
the frozen early Rust baseline. A reproducible cumulative checkpoint reported
roughly 20% faster graph construction, 28% faster hierarchy construction,
4.4x faster stationary CMG application, 2.7x faster solve-per-RHS, and large
workspace reductions. The current four-logical-CPU routing benchmark reaches
about 2.2x planned-versus-serial speedup on its largest dense worker-firm case.

These are controlled project benchmarks, not universal hardware guarantees.
See [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md) for exact interpretation,
current bottlenecks, retained benchmark records, and the 32-core qualification
protocol.

## Repository layout

```text
src/                 numerical library and optional parallel implementation
examples/            small runnable API examples
tests/               correctness, determinism, adversarial, and parity tests
benchmarks/           durable benchmark/profiling harnesses
benchmarks/c-kernel/  isolated comparison with pinned upstream C kernels
docs/                 maintained design, performance, and provenance notes
.github/workflows/    durable CI and benchmark workflows
.ci/                  latest machine-readable CI/performance records only
```

Completed one-shot optimization experiments are intentionally kept in Git
history rather than the current source tree.

## Build and test

```bash
cargo fmt --all -- --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all-targets
cargo test --all-targets --release
cargo test --all-targets --all-features
cargo test --all-targets --all-features --release
cargo build --release --all-features
```

The benchmark crate is checked independently by CI:

```bash
cargo build --release --manifest-path benchmarks/Cargo.toml --all-targets
```

## Documentation

- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md): module map, testing, repository
  policy, and how to make performance changes.
- [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md): current performance evidence,
  routing guidance, bottlenecks, and large-machine qualification.
- [`docs/UPSTREAM.md`](docs/UPSTREAM.md): pinned upstream source, behavioral
  constants, implementation mapping, and attribution.

## Scope

The crate implements stationary CMG, certified PCG, repeated-RHS operation,
optional deterministic multicore kernels, and SDDM wrapping. K-cycles,
flexible CG, GPU kernels, NUMA-specific placement, a C ABI, and Stata
integration are future layers rather than hidden parts of the current package.

## License

GNU GPL version 3 only. See [`LICENSE`](LICENSE) and
[`docs/UPSTREAM.md`](docs/UPSTREAM.md).
'''

UPSTREAM = r'''# Upstream provenance and implementation coverage

## Pinned source

This project is an independent Rust port of the official CMG implementation:

- Ioannis Koutis and Gary Miller, `ikoutis/cmg-solver`
- pinned commit `19752fc102f8cae8e34f66457bfaccb1aaa60375`
- upstream source path `matlab/cmg`
- upstream license: GNU GPL version 3

The pinned commit includes the 2026 correction to the C forest-component
workspace initialization.

## Source-of-truth order

1. Pinned official MATLAB implementation and C MEX kernels.
2. Published CMG algorithm and its mathematical invariants.
3. Independent dense algebraic oracles and differential tests used for
   verification.

## Production-path coverage

| Upstream behavior | Rust location | Status |
|---|---|---|
| SDDM/Laplacian validation and augmentation | `src/sddm.rs` | implemented |
| heaviest incident-edge forest | `src/forest.rs` | implemented |
| forest splitting / conductance logic | `src/forest.rs` | implemented |
| low-effective-degree correction | `src/forest.rs` | implemented |
| forest components / aggregate labels | `src/forest.rs`, `src/coarsen.rs` | implemented |
| Galerkin coarse contraction | `src/coarsen.rs` | implemented |
| hierarchy stopping logic | `src/hierarchy.rs` | implemented |
| recursive repeat-count logic | `src/hierarchy.rs` | implemented |
| grounded terminal LDL solve | `src/ldl.rs` | implemented |
| stationary recursive CMG cycle | `src/preconditioner.rs` | implemented |
| SDDM preconditioner wrapper | `src/sddm.rs`, `src/preconditioner.rs` | implemented |
| PCG outer solver | `src/pcg.rs` | implemented natively |
| sparse/vector utility kernels | `src/graph.rs`, `src/workspace.rs` | implemented |
| optional multicore execution | `src/execution.rs`, `src/parallel_solver.rs` | Rust extension |

The benchmark-only `benchmarks/c-kernel/` crate compiles isolated pinned C
kernels and checks numerical agreement with the Rust implementation.

## Behavioral constants retained from upstream

- direct terminal below 700 vertices;
- damped Jacobi inverse diagonal `1 / (2 * diag(A))`;
- low-effective-degree threshold `1/8`;
- hierarchy cumulative-nonzero guard `5 * nnz(A_initial)`;
- stagnation when the coarse graph has at least `n - 1` vertices;
- repeat count `max(floor(nnz(A_fine) / nnz(A_coarse) - 1), 1)`;
- one top-level stationary cycle per preconditioner application;
- component grounding for direct Laplacian solves;
- one extra augmentation vertex for strictly dominant SDDM systems.

The Rust API allows relevant constants to be overridden for testing while
keeping the upstream values as defaults.

## Deliberate Rust extensions

The Rust package supports several behaviors beyond the original MATLAB-facing
interface while preserving the stationary CMG algorithm:

- all graph sizes rather than refusing small inputs;
- disconnected Laplacians with explicit component compatibility checks;
- original-system residual and backward-error certification;
- reusable immutable hierarchies and caller-owned workspaces;
- repeated and batched right-hand sides;
- deterministic compact storage and memory accounting;
- optional package-owned parallel execution and automatic routing.

## Attribution

CMG was developed by Ioannis Koutis and Gary Miller. This repository is an
independent Rust port and is not an official upstream release.
'''

PERFORMANCE = r'''# Performance and parallel execution

This document is the maintained performance reference for the crate. Detailed
one-shot optimization experiments are preserved in Git history and GitHub
Actions artifacts; the current tree keeps only durable benchmark harnesses and
a small set of latest machine-readable records.

## Current implementation

The retained production implementation includes:

- compact canonical `u32` edge endpoints with public `usize` dimensions;
- shared Laplacian storage across hierarchy ownership boundaries;
- compact aggregation labels with lazy native-width compatibility views;
- compact CSR columns and row offsets for selected parallel levels;
- compressed terminal factors and reduced CMG/PCG workspaces;
- cached endpoint keys and optimized duplicate contraction;
- reduced forest-splitting branches and compact indegrees;
- package-owned Rayon execution, selectively routed within-solve kernels, and
  memory-bounded concurrency across RHSs;
- deterministic/fixed-order reductions and final original-system residual
  certification.

## Cumulative optimization checkpoint

`.ci/performance/cumulative-latest.json` compares numerical checkpoint
`f50cbd52734ad84af39131c12ad5dae181d8c7b5` with the frozen early Rust
baseline `b45b252f88925028e3ad9a73a3f75eeab05f6754`. Later retained changes are
not included in that frozen cumulative measurement, so it should be read as a
reproducible checkpoint rather than an exact current-head benchmark.

Geometric current/baseline ratios in that checkpoint were:

| Metric | Ratio | Interpretation |
|---|---:|---|
| graph construction | 0.802x | about 20% faster |
| hierarchy construction | 0.721x | about 28% faster |
| stationary CMG application | 0.225x | about 4.4x faster |
| solve per RHS | 0.372x | about 2.7x faster |
| graph core bytes | 0.735x | about 27% less |
| hierarchy core bytes | 0.770x | about 23% less |
| CMG workspace bytes | 0.460x | about 54% less |
| PCG workspace bytes | 0.614x | about 39% less |

These are geometric summaries across the frozen benchmark cases. Individual
cases vary; the JSON record contains the complete measurements.

## Direct comparison with pinned C kernels

The isolated comparison crate in `benchmarks/c-kernel/` checks the pinned
upstream C sparse kernels and stationary recursive cycle against Rust. The
accepted cycle checkpoint measured roughly 0.866x of C time on a path case and
1.008x on a worker-firm case, with quotient-space differences near numerical
roundoff. This is evidence that the repeated CMG hot path is at C parity or
better on the tested cases; it is not an end-to-end MATLAB comparison because
upstream hierarchy construction is primarily MATLAB code.

The latest durable record is `.ci/performance/c-kernel-latest.json`.

## Parallel routing

`ParallelPcgSolver` chooses among:

1. **Serial** — compact edge kernels and one workspace.
2. **Planned** — selected hierarchy levels use deterministic row-owned CSR
   kernels within one solve.
3. **AcrossRightHandSides** — independent serial PCG solves run concurrently,
   sharing the immutable hierarchy and using private reusable workspaces.

The default single-RHS planned threshold is **350,000 canonical retained
edges**, provided more than one worker thread and at least one routed operator
are available. It is a performance heuristic and can be overridden with
`ParallelPcgPolicy`.

The latest durable four-logical-CPU routing record is
`.ci/performance/full-pcg-routing-latest.json`. Its largest dense worker-firm
case reports roughly 2.2x planned-versus-serial full-PCG speedup with unchanged
iterations and solution certificate. Small and path-like systems remain serial
when parallel overhead does not pay.

For multiple RHSs, `.ci/performance/parallel-latest.json` records thread-scaling
benchmarks. Across-RHS concurrency is generally preferred when the configured
workspace budget permits at least two simultaneous workspaces.

## Memory budgeting

Let `W = solver.workspace_bytes()` and let `B` be the configured reusable
workspace budget. Batch concurrency is bounded approximately by

```text
min(thread_count, rhs_count, floor(B / W))
```

The budget excludes the shared graph, hierarchy, optional parallel plan, input
RHSs, result vectors, allocator overhead, and host-process memory. Large
machines should therefore not assume that one full PCG workspace per CPU is
affordable.

## Current bottleneck

After the retained forest, storage, and solve improvements, hierarchy setup is
still dominated by coarse contraction. Current profiling attributes about two
thirds of hierarchy setup to contraction, and roughly three quarters of
contraction time to endpoint sorting. Several alternatives—including bucketed
sorts and scratch-based radix sorts—were benchmarked and rejected because their
end-to-end or memory tradeoffs were worse than the retained cached-key
comparison sort.

This is now a mature optimization point: new setup changes should be accepted
only with a clear end-to-end gain on large worker-firm graphs and bounded peak
memory.

## Durable machine-readable records

The maintained `.ci/performance/` directory intentionally contains only:

- `latest.json` — frozen-baseline performance workflow result;
- `full-pcg-routing-latest.json` — serial versus planned certified PCG matrix;
- `parallel-latest.json` — current hosted-runner thread scaling;
- `c-kernel-latest.json` — pinned C/Rust differential comparison;
- `cumulative-latest.json` — frozen cumulative optimization checkpoint.

Older retained/rejected experiment records are available from Git history.
Raw logs and temporary benchmark details belong in Actions artifacts, not the
current source tree.

## Benchmark discipline

Performance changes should follow this order:

1. keep the production baseline untouched;
2. build baseline and candidate with the same compiler, features, and CPU
   settings;
3. use deterministic identical graphs and RHSs;
4. alternate baseline/candidate measurements and use medians;
5. verify hierarchy metadata, iteration counts, backward errors, and scaled
   solution differences before interpreting time;
6. measure requested allocation and process RSS when memory can change;
7. retain a change only when its end-to-end benefit justifies its complexity
   and memory cost.

Durable benchmark binaries live in `benchmarks/`; see `benchmarks/README.md`.

## 8/16/32-core qualification

The library can construct package-owned pools at 1, 2, 4, 8, 16, and 32
threads. That establishes functional support, not scaling performance.

`.github/workflows/manual-32-thread-qualification.yml` is a manually dispatched,
read-only workflow intended for a configured larger runner or controlled
self-hosted machine. It records machine topology, compiler versions, numerical
agreement, setup and solve timing, iteration counts, hierarchy/workspace bytes,
process peak RSS, and 1/2/4/8/16/32-thread measurements where hardware permits.

A 32-core performance claim should require one controlled machine and commit,
both single-RHS and many-RHS workloads, repeated observations, and graphs large
enough that parallel work dominates scheduling overhead. Until those data
exist, hosted four-CPU results should be described as directional evidence only.

## Workload guidance

- **Small or sparse single RHS:** use serial PCG.
- **Large/dense single RHS:** use `ParallelPcgSolver`; inspect the selected
  execution strategy.
- **Many RHSs on one graph:** reuse one solver and workspace pool; allow the
  memory-aware router to prefer across-RHS concurrency.
- **Large-memory/many-core machines:** set an explicit thread count and workspace
  budget rather than relying on maximum available parallelism.
'''

DEVELOPMENT = r'''# Development guide

## Repository policy

The current tree is the maintained product; Git history and Actions artifacts
are the experiment archive. Do not leave completed one-shot optimization
scripts, self-removing workflows, recovery launchers, or per-experiment JSON
records on `main`.

Keep durable files only when they serve one of these roles:

- production library code;
- correctness or regression tests;
- runnable examples;
- reusable benchmark/profiling harnesses;
- durable CI/benchmark workflows;
- maintained user/developer documentation;
- a small number of current machine-readable status records.

## Module map

| Module | Responsibility |
|---|---|
| `graph.rs` | canonical weighted Laplacian storage, matvec, graph metadata |
| `components.rs` | connected components and quotient-space operations |
| `forest.rs` | heavy-edge forest, splitting, correction, component labels |
| `coarsen.rs` | aggregate mappings and Galerkin contraction |
| `hierarchy.rs` | recursive hierarchy construction and stopping rules |
| `ldl.rs` | grounded terminal factorization and solve |
| `preconditioner.rs` | stationary recursive CMG cycle |
| `pcg.rs` | certified PCG and repeated-RHS interfaces |
| `sddm.rs` / `sddm_solver.rs` | SDDM validation, augmentation, solve wrapper |
| `workspace.rs` | reusable numerical scratch storage |
| `execution.rs` | optional package-owned parallel executor |
| `csr.rs` | deterministic row-oriented operators for parallel kernels |
| `parallel_solver.rs` | automatic memory-aware parallel routing |
| `pcg_profile.rs` | profiling-only PCG instrumentation |

The public API is re-exported from `src/lib.rs`; implementation details should
remain private unless a stable user-facing need exists.

## Correctness invariants

Performance work must preserve:

- deterministic canonical graph construction;
- exact symmetry of the represented Laplacian;
- component-wise compatibility checks for singular systems;
- the pinned stationary CMG hierarchy/repeat semantics;
- deterministic grounding/normalization conventions;
- final residual/backward-error verification against the original system;
- identical hierarchy metadata unless the algorithm itself is deliberately
  changed and separately reviewed.

Never accept a faster benchmark because it merely relaxes a tolerance, changes
an RHS, changes the matrix, skips certification, or silently changes the
stationary algorithm.

## Build and test

Before retaining source changes:

```bash
cargo fmt --all -- --check
cargo fmt --manifest-path benchmarks/Cargo.toml --all -- --check
cargo clippy --all-targets --all-features -- -D warnings
cargo clippy --manifest-path benchmarks/Cargo.toml --all-targets -- -D warnings
RUSTDOCFLAGS="-D warnings" cargo doc --no-deps --document-private-items --all-features
cargo test --all-targets
cargo test --all-targets --release
cargo test --all-targets --all-features
cargo test --all-targets --all-features --release
cargo build --release --all-features
cargo build --release --manifest-path benchmarks/Cargo.toml --all-targets
```

`rust.yml` runs the cross-platform qualification matrix on Ubuntu, macOS, and
Windows. The latest published status is `.ci/latest.json`.

## Performance work

Use the durable binaries in `benchmarks/` rather than committing a new
long-lived benchmark executable for every hypothesis. If a one-shot gate is
needed, it may be staged temporarily, but it should remove itself after recording
its decision; completed experiment artifacts should not remain on `main`.

The durable benchmark workflows are:

- `performance.yml` — frozen-baseline serial performance plus certified
  serial/planned routing matrix;
- `parallel-performance.yml` — hosted-runner parallel scaling;
- `c-kernel.yml` — pinned C/Rust differential comparison;
- `manual-32-thread-qualification.yml` — controlled large-machine scaling.

See `docs/PERFORMANCE.md` for the evidence policy and interpretation.

## Documentation maintenance

Keep the root directory small. User-facing orientation belongs in `README.md`;
maintained technical notes belong in `docs/`. Avoid chronological status logs
in the current tree: commit history already records chronology.

When an implementation constant or routing threshold changes, update the code
comment, tests, `README.md` if user-visible, and the corresponding maintained
document in the same checkpoint.

## Release boundary

The crate currently targets stationary CMG and certified PCG. C ABI/Stata
integration, flexible Krylov methods, GPU support, and NUMA-specific tuning
should be developed as explicit later layers rather than mixed into cleanup or
micro-optimization work.
'''

BENCHMARK_README = r'''# CMG benchmark and profiling harnesses

`benchmarks/` is a separate, non-published Cargo crate. Benchmark-only code and
profiling features therefore stay outside the numerical library's normal
runtime dependency path. Binaries print machine-readable JSON.

## Core benchmarks

| Binary | Purpose |
|---|---|
| `graph-build` | canonical graph construction, including duplicate-rich inputs |
| `hierarchy-build` | serial CMG hierarchy construction |
| `hierarchy-alloc` | hierarchy construction with requested-allocation tracking |
| `terminal-build` | direct-terminal construction |
| `single-rhs-solve` | certified serial PCG with a reused hierarchy/workspace |
| `parallel-cmg-apply` | serial versus selectively planned CMG application |
| `parallel-pcg-solve` | serial versus planned complete PCG solve |
| `full-pcg-routing` | routing crossover matrix used by durable workflows |
| `prepared-solver-auto` | automatic versus explicit serial/within/across-RHS strategies |

## Profiling tools

| Binary | Purpose |
|---|---|
| `hierarchy-phase-profile` | hierarchy phase attribution |
| `contraction-subphase-profile` | contraction mapping/sort/merge/finalization attribution |
| `pcg-phase-profile` | certified outer-PCG phase attribution |

`benchmarks/c-kernel/` is an isolated crate for the pinned upstream C kernel and
stationary-cycle comparison.

## Build

```bash
cargo build --release --manifest-path benchmarks/Cargo.toml --all-targets
```

Representative runs:

```bash
cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin hierarchy-build -- worker-firm 500000 5

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin hierarchy-alloc -- worker-firm 500000 5

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin single-rhs-solve -- worker-firm 100000 7

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin parallel-pcg-solve -- worker-firm 200000 7 4

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin prepared-solver-auto -- worker-firm 100000 8 7 4
```

For process RSS on Linux, wrap the release binary with `/usr/bin/time -v`.

## Comparison protocol

1. Build baseline and candidate with the same compiler, feature set, and CPU
   settings.
2. Use identical deterministic graphs and RHSs.
3. Warm both binaries and interleave measured order.
4. Use medians from repeated observations.
5. Verify hierarchy metadata, iterations, residual certificates, backward
   errors, and scaled solution differences before interpreting time.
6. Measure setup, application, solve, requested allocation, retained memory, and
   process RSS separately when relevant.
7. Retain only changes with a clear end-to-end benefit and acceptable memory
   tradeoff.

The current tree stores only durable latest records under `.ci/performance/`.
Detailed historical experiment evidence remains available through Git history
and Actions artifacts. See `docs/PERFORMANCE.md`.
'''

LICENSE = r'''CMG Rust Port
Copyright (C) 2026 Johannes Schmieder and contributors

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, version 3 of the License.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

SPDX-License-Identifier: GPL-3.0-only

The complete license text is available from the Free Software Foundation at
https://www.gnu.org/licenses/gpl-3.0.txt and should accompany packaged releases
of this project.

The upstream CMG implementation is Copyright (c) 2008-2020 Ioannis Koutis and
Gary Miller and is distributed under GNU GPL version 3. See docs/UPSTREAM.md.
'''


def tracked_files():
    return subprocess.check_output(["git", "ls-files"], text=True).splitlines()


active_path = Path(".ci/active-optimization-gates.json")
if active_path.exists():
    active = json.loads(active_path.read_text())
    if active.get("workflows"):
        raise SystemExit(
            "repository cleanup refused: active optimization workflows are recorded"
        )

before = tracked_files()
removed = []

for raw in before:
    path = Path(raw)
    remove = False
    if raw.startswith(".github/workflows/"):
        remove = path.name not in KEEP_WORKFLOWS
    elif raw.startswith("scripts/"):
        remove = raw != str(SELF)
    elif raw.startswith(".ci/performance/"):
        remove = raw not in KEEP_PERFORMANCE
    elif raw in OLD_ROOT_DOCS:
        remove = True
    elif raw == ".ci/active-optimization-gates.json":
        remove = True
    elif raw == "benchmarks/EVIDENCE.md":
        remove = True

    if remove and path.exists():
        path.unlink()
        removed.append(raw)

Path("docs").mkdir(exist_ok=True)
Path("README.md").write_text(README)
Path("docs/UPSTREAM.md").write_text(UPSTREAM)
Path("docs/PERFORMANCE.md").write_text(PERFORMANCE)
Path("docs/DEVELOPMENT.md").write_text(DEVELOPMENT)
Path("benchmarks/README.md").write_text(BENCHMARK_README)
Path("LICENSE").write_text(LICENSE)

# Documentation-only changes should not launch full performance jobs. Those
# workflows remain source/benchmark/workflow driven.
for workflow_name in ("performance.yml", "parallel-performance.yml"):
    path = Path(".github/workflows") / workflow_name
    text = path.read_text()
    text = text.replace("      - 'PERFORMANCE_PLAN.md'\n", "")
    path.write_text(text)

cargo = Path("Cargo.toml").read_text()
cargo = cargo.replace(
    'description = "Deterministic Rust port of the Combinatorial Multigrid preconditioner"',
    'description = "Deterministic Rust implementation of stationary Combinatorial Multigrid with certified PCG"',
)
Path("Cargo.toml").write_text(cargo)

Path(".gitignore").write_text(
    "target/\n"
    "**/*.rs.bk\n"
    ".DS_Store\n"
    "__pycache__/\n"
    "*.py[cod]\n"
    "*.profraw\n"
    "qualification-output/\n"
)

# Remove the cleanup launcher itself from the maintained tree.
WORKFLOW.unlink(missing_ok=True)
SELF.unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass

# Guard against accidental deletion of durable records/workflows.
for required in KEEP_PERFORMANCE:
    if not Path(required).exists():
        raise SystemExit(f"cleanup removed required performance record: {required}")
for required in KEEP_WORKFLOWS - {"cleanup-repository.yml"}:
    if not (Path(".github/workflows") / required).exists():
        raise SystemExit(f"cleanup removed required workflow: {required}")

subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
subprocess.run(
    [
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ],
    check=True,
)
subprocess.run(["git", "add", "-A"], check=True)

# Fail if numerical source or tests changed in this repository-maintenance pass.
changed = subprocess.check_output(
    ["git", "diff", "--cached", "--name-only"], text=True
).splitlines()
for path in changed:
    if path.startswith("src/") or path.startswith("tests/") or path.startswith("examples/"):
        raise SystemExit(f"cleanup unexpectedly modified numerical/test path: {path}")

message = f"chore: simplify repository tree and remove {len(removed)} stale files"
subprocess.run(["git", "commit", "-m", message], check=True)
for _ in range(5):
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
    pushed = subprocess.run(["git", "push", "origin", "HEAD:main"])
    if pushed.returncode == 0:
        break
else:
    raise SystemExit("failed to push repository cleanup")

print(f"removed_stale_files={len(removed)}")
print(f"changed_files={len(changed)}")
