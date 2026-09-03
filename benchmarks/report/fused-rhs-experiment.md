# Experimental four-lane fused RHS benchmark

This report is intentionally separate from the accepted production benchmark
record. It evaluates the opt-in `experimental-fused-rhs` implementation without
changing any existing solver route.

## Local ARM64 result (2026-09-02)

The benchmark used `-C target-cpu=native`, two warmups, seven paired measured
repetitions, alternating scalar-first and fused-first order. Every warmup,
measurement, and phase-profile call checked solutions and all diagnostics for
bitwise equality with the existing scalar caller-buffer solver.

| Family | Vertices | Edges | RHS | Mix | Scalar median | Fused median | Speedup | Fused/scalar 95% paired bootstrap CI |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| path | 50,000 | 49,999 | 16 | homogeneous | 538.1 ms | 507.3 ms | 1.06x | [0.925, 0.957] |
| worker-firm | 50,000 | 74,996 | 16 | homogeneous | 572.3 ms | 350.2 ms | 1.63x | [0.603, 0.618] |
| dense-worker-firm | 50,000 | 399,930 | 16 | homogeneous | 2,244.1 ms | 763.7 ms | 2.94x | [0.337, 0.346] |
| worker-firm | 50,000 | 74,996 | 16 | mixed | 446.6 ms | 318.9 ms | 1.40x | [0.713, 0.719] |

The caller-buffer pack/scatter cost was small relative to the numerical solve:
about 1.3--1.8 ms per fused call in these cases. The principal fused workspace
was 15.3 MB for worker-firm versus 4.5 MB for the scalar batch workspace, and
23.1 MB versus 6.3 MB for dense-worker-firm. This is the expected cost of keeping
four interleaved copies of every PCG and recursive CMG work vector.

The SCC2 workflow contains separate `fused-smoke` and `fused` manifests. It
builds both portable and `target-cpu=cascadelake` binaries and records their
hashes. The full manifest covers one-million-vertex worker-firm and
dense-worker-firm graphs, 4/16/32 RHS, homogeneous/mixed convergence, and seven
paired repetitions.
