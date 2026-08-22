# Pinned C kernel comparison

This isolated benchmark crate compares the Rust edge-list Laplacian matrix-vector product with the official CMG C `sspmv` arithmetic loop pinned at upstream commit `19752fc102f8cae8e34f66457bfaccb1aaa60375`.

The C source is benchmark-only. It is not linked into the Rust CMG library. The adapter replaces MATLAB-specific declarations with fixed-width standalone types; the numerical loop is unchanged.

Run from the repository root:

```bash
cargo run --release --manifest-path benchmarks/c-kernel/Cargo.toml -- \
  --case worker-firm --vertices 100000
```

Hosted-runner timings are directional. The benchmark first verifies numerical agreement and exits before timing if the scaled error exceeds `2e-12`.
