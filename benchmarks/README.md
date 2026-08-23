# CMG performance benchmarks

This directory is a separate Cargo crate so benchmark-only code and build
settings do not enter the numerical library. All benchmark binaries print one
JSON object suitable for machine-readable comparison.

## Entry points

| Binary | Purpose | Positional arguments |
|---|---|---|
| `graph-build` | Canonical graph construction, including duplicate-rich streams | case, scale, repetitions |
| `hierarchy-build` | Serial CMG hierarchy construction | case, scale, repetitions |
| `hierarchy-alloc` | Hierarchy construction with exact requested-allocation tracking | case, scale, repetitions |
| `terminal-build` | Repeated direct-terminal construction | terminal size |
| `single-rhs-solve` | Certified serial PCG with a reused hierarchy and workspace | case, scale, repetitions |
| `parallel-cmg-apply` | Serial versus selectively planned CMG application | case, scale, repetitions, threads |
| `parallel-pcg-solve` | Serial versus selectively planned complete PCG solve | case, scale, repetitions, threads |
| `prepared-solver-auto` | Automatic versus explicit serial, within-solve, and across-RHS strategies | case, scale, RHS count, repetitions, threads |

Supported synthetic cases are generally `path`, `worker-firm`, and
`dense-worker-firm`; graph-construction benchmarks also provide their own
unique and duplicate-rich cases. The `scale` for worker-firm cases is the
number of vertices on each side of the bipartite graph.

## Reproducible commands

Build the complete benchmark crate once:

```bash
cargo build --release --manifest-path benchmarks/Cargo.toml --all-targets
```

Representative serial measurements:

```bash
cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin graph-build -- duplicates-16 75000 5

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin hierarchy-build -- worker-firm 500000 5

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin hierarchy-alloc -- worker-firm 500000 5

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin single-rhs-solve -- worker-firm 100000 7

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin terminal-build -- 700
```

Representative four-thread measurements:

```bash
cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin parallel-cmg-apply -- dense-worker-firm 50000 7 4

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin parallel-pcg-solve -- worker-firm 200000 7 4

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin prepared-solver-auto -- worker-firm 100000 8 7 4
```

For an installed release binary, use `/usr/bin/time -v` on Linux to retain
process peak RSS alongside the JSON output:

```bash
/usr/bin/time -v \
  benchmarks/target/release/prepared-solver-auto \
  worker-firm 100000 8 7 4
```

## Comparison protocol

Performance decisions in this repository follow these rules:

1. Build baseline and candidate with the same Rust compiler, release profile,
   feature set, and CPU-target settings.
2. Generate identical deterministic graphs and right-hand sides.
3. Warm both binaries and interleave their measured run order.
4. Use medians from several samples rather than one wall-clock observation.
5. Verify hierarchy diagnostics, iteration counts, residual certificates,
   backward errors, and scaled solution differences before interpreting timing.
6. Measure setup, application, solve, retained workspace, exact requested
   allocation, and process RSS separately when relevant.
7. Record each retained or rejected decision under `.ci/performance/` and in
   `PERFORMANCE_PLAN.md`.

Hosted-runner timing is a regression and routing gate, not a substitute for
controlled 8–32-core, high-memory, or NUMA qualification. The persistent
`performance.yml` and `parallel-performance.yml` workflows can also be launched
manually with their `workflow_dispatch` inputs.
