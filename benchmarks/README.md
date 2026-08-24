# CMG benchmark and profiling harnesses

`benchmarks/` is a separate, non-published Cargo crate. Benchmark-only code and profiling features therefore stay outside the numerical library's normal runtime dependency path. Binaries print machine-readable JSON.

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

`benchmarks/c-kernel/` is an isolated crate for the pinned upstream C kernel and stationary-cycle comparison.

## Build

```bash
cargo build --release --manifest-path benchmarks/Cargo.toml --all-targets
```

Representative runs:

```bash
cargo run --release --manifest-path benchmarks/Cargo.toml -- \
  --bin hierarchy-build -- worker-firm 500000 5

cargo run --release --manifest-path benchmarks/Cargo.toml -- \
  --bin hierarchy-alloc -- worker-firm 500000 5

cargo run --release --manifest-path benchmarks/Cargo.toml -- \
  --bin single-rhs-solve -- worker-firm 100000 7

cargo run --release --manifest-path benchmarks/Cargo.toml -- \
  --bin parallel-pcg-solve -- worker-firm 200000 7 4

cargo run --release --manifest-path benchmarks/Cargo.toml -- \
  --bin prepared-solver-auto -- worker-firm 100000 8 7 4
```

For process RSS on Linux, wrap the release binary with `/usr/bin/time -v`.

## Comparison protocol

1. Build baseline and candidate with the same compiler, feature set, and CPU settings.
2. Use identical deterministic graphs and RHSs.
3. Warm both binaries and interleave measured order.
4. Use medians from repeated observations.
5. Verify hierarchy metadata, iterations, residual certificates, backward errors, and scaled solution differences before interpreting time.
6. Measure setup, application, solve, requested allocation, retained memory, and process RSS separately when relevant.
7. Retain only changes with a clear end-to-end benefit and acceptable memory tradeoff.

The current tree stores only durable latest records under `.ci/performance/`. Detailed historical experiment evidence remains available through Git history and Actions artifacts. See `docs/PERFORMANCE.md`.
