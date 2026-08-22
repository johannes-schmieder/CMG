# CMG performance benchmarks

This directory is a separate Cargo crate so benchmark-only dependencies and
release settings do not enter the numerical library.

## Entry points

- `cmg-bench`: the main end-to-end CMG setup, application, solve, and batch
  benchmark used by the performance workflows.
- `terminal-build`: repeated direct-terminal construction below the default
  threshold; used to qualify dense-factor setup changes.
- `hierarchy-build`: scalable hierarchy-only construction for paths, grids,
  sparse worker–firm graphs, and denser worker–firm graphs. Input graph
  construction occurs before the timer.
- `graph-build`: direct `Laplacian::from_edges` construction with unique and
  duplicate-rich edge streams.

## Examples

```bash
cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin terminal-build -- 9

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin hierarchy-build -- worker-firm 50000 3

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin graph-build -- duplicates-16 75000 5
```

Performance decisions must compare baseline and candidate binaries on the same
runner, alternate their run order, verify the full numerical test suite, and
record machine-readable evidence under `.ci/performance/`. Hosted-runner timing
is treated as a regression gate, not as a substitute for dedicated 8–32-core
hardware qualification.
