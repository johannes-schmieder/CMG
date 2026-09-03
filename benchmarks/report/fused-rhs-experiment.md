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

The SCC2 workflow contains separate `fused-smoke` and `fused` manifests. The
fused tasks use the portable binary and request all 28 slots on Broadwell hosts
satisfying `num_proc=28` and `cpu_type=E5-2680v4`. The full 12-task manifest
covers one-million-vertex worker-firm and dense-worker-firm graphs, 4/16/32 RHS,
homogeneous/mixed convergence, and seven paired repetitions. Each result records
the host, CPU model, host processor count, allocation, source archive, and binary
hash.

An earlier full-array submission, job 7428004, requested 32 Gold-6242 slots and
therefore excluded the intended older 28-core host population. It was canceled
while still queued, before any array task ran and with zero `output/fused`
artifacts. Its completed, namespaced `fused-smoke` evidence remains immutable.

A subsequent corrected-run bootstrap, job 7428372 under run
`20260903T114405Z-fc4d89a-b2v1-fused-28core`, completed the Rust stages but
exited 127 before MATLAB and Python validation because its ad hoc compute-job
launcher used non-login Bash and did not initialize Lmod. It produced no
`BUILD_SUCCESS` receipt or benchmark artifacts. The maintained workflow now
initializes Lmod explicitly and submits bootstraps through a guarded compute-job
entrypoint with 6 GB per core.
