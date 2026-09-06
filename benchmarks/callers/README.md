# Component caller qualification

This standalone crate reuses the component fixtures while depending on CMG with
`default-features = false`. It measures the ordinary owning-result, caller-buffer,
and optional planned paths. It does not enable profiling. The main benchmark
crate retains its existing dependency features.

Run `cargo run --release --manifest-path benchmarks/callers/Cargo.toml -- 1 4
--suite stress --seed 20260914 --route baseline --caller buffer` (on one line).
The first two positional values are recorded repetitions and RHS count; the
optional third value filters case names. Every invocation has two warmups.
Enable `parallel` and use `--caller planned --threads 1` or `4` to exercise the
executor builder and plan. Thread-pool and plan construction are included in
setup. The owning path allocates results in solve; the buffer path allocates
outputs and diagnostics in workspace preparation. Both are included in total.
Input construction, independent residual checks and fingerprints are untimed.

Existing experimental routes require `experimental-components`. Allocation
checks require the separate `component-allocations` build and are serial. Never
use instrumented timings as performance evidence. Setup allocation records use
conservative estimates; fingerprints cover full returned vectors and all PCG
diagnostics. Graph/RHS fingerprints permit cross-build input checks.

`--suite veneto --input PATH` reads the public teaching extract described in
`docs/teaching/README.md`, using observation counts as edge weights. It runs both
the complete graph and its largest connected component. Verify the documented
SHA-256 before recording evidence; source IDs and outcomes are never printed.
Record the clean library SHA in `CMG_BENCH_COMMIT` at build time, hash the harness
and binary, rotate external invocation order, and preserve raw JSONL and failures.
