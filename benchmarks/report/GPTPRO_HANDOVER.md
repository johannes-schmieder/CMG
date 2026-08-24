# GPT Pro handover: critical review of the SCC CMG benchmarks

Use the following prompt with GPT Pro. Attach these repository artifacts:

- `output/pdf/benchmarks.pdf`
- `benchmarks/report/data/summary.csv`
- `.ci/performance/scc-latest.json`
- `benchmarks/report/benchmarks.tex`
- `docs/PERFORMANCE.md`
- `benchmarks/README.md`

The PDF is an interim main-run report. The four `batch16` SCC tasks were still
queued when this snapshot was committed, so the repeated-right-hand-side figure
and text are explicitly marked pending. Do not interpret that marker as a
measurement. Review the accepted 180-row main matrix and the six standalone
C-kernel records only. A later report revision will add the 48 batch rows.

## Prompt

Act as an independent performance-engineering and numerical-methods reviewer.
Critically evaluate this benchmark study of the Rust CMG package against the
official MATLAB solver with its C MEX kernels. Do not merely summarize the
report. Recompute important comparisons from `summary.csv`, challenge the
methodology, distinguish measured facts from plausible explanations, and state
what evidence would falsify each explanation.

The official MATLAB/C source is pinned at commit
`19752fc102f8cae8e34f66457bfaccb1aaa60375`. The accepted main study contains
five deterministic connected graph families, sizes 100,000, 300,000, and
1,000,000 vertices, application CPU counts 1, 2, 4, 8, 16, and 32, and both
implementations: 180 result rows in total. Each point is a median of three
measurements after one warm-up. Rust 1.98.0 and MATLAB 2026a Update 2 were run
on whole Intel Xeon Gold 6242 nodes with 32 physical cores, two sockets, four
NUMA nodes, one hardware thread per core, and whole-node linear binding. Rust
uses its package-owned Rayon pool and MATLAB uses the recorded
`maxNumCompThreads` limit. Builds use default release/MEX settings without
native-CPU flags.

At 32 CPUs, the reported geometric-mean Rust/MATLAB ratios across the 15 main
graph/size cases are:

- CMG hierarchy setup: 0.265
- one stationary CMG application: 0.790
- reused-preconditioner PCG: 0.783
- setup plus one solve: 0.666
- process peak RSS: 0.150

At one million vertices, setup-plus-solve medians at 32 CPUs are:

| Family | Rust (s) | MATLAB (s) | MATLAB / Rust |
|---|---:|---:|---:|
| weighted path | 2.964 | 3.325 | 1.12 |
| 2D grid | 1.863 | 2.501 | 1.34 |
| worker-firm degree 3 | 1.276 | 2.444 | 1.91 |
| worker-firm degree 16 | 6.735 | 14.771 | 2.19 |
| weak community | 1.800 | 2.158 | 1.20 |

The geometric-mean 1-to-32-CPU speedup for one-million-vertex setup plus solve
is only 1.05x for Rust and 1.15x for MATLAB. The family-level one-million-vertex
stage speedups are especially important:

| Family | Impl. | Setup | Apply | PCG | Total |
|---|---|---:|---:|---:|---:|
| path | Rust | 0.93x | 0.97x | 0.90x | 0.91x |
| path | MATLAB | 1.18x | 1.01x | 1.07x | 1.11x |
| grid | Rust | 0.85x | 1.06x | 1.11x | 0.99x |
| grid | MATLAB | 1.17x | 0.93x | 0.97x | 1.02x |
| worker-firm d=3 | Rust | 0.83x | 1.75x | 1.41x | 1.13x |
| worker-firm d=3 | MATLAB | 1.23x | 0.99x | 1.04x | 1.13x |
| worker-firm d=16 | Rust | 0.68x | 6.79x | 6.65x | 1.28x |
| worker-firm d=16 | MATLAB | 1.94x | 0.94x | 0.95x | 1.45x |
| weak community | Rust | 0.95x | 1.06x | 1.06x | 0.98x |
| weak community | MATLAB | 1.20x | 1.01x | 1.04x | 1.10x |

For the dense one-million-vertex Rust case, the actual stage medians sharpen
the puzzle. At one CPU, setup is 2.666 s, PCG is 5.869 s, and total is 8.596 s.
At 32 CPUs, setup is 3.921 s, parallel-plan construction is 1.831 s, PCG is
0.883 s, and total is 6.735 s. Thus the solve kernel scales usefully but setup
and plan costs erase much of its benefit. For path, grid, and weak-community
graphs, PCG is roughly flat or slower at 32 CPUs. MATLAB PCG and stationary
apply are approximately flat across CPU counts in nearly every family.

All main cases passed scheduler, identity, timing, memory, and numerical checks.
The maximum Rust backward error is `9.95e-9`; native MATLAB PCG reports success.
Independent residual and reference-solution diagnostics are retained because
the packages use different native stopping rules. Twelve of 15 graph/size cases
have matching intermediate hierarchy sizes and a one-vertex difference in the
terminal level; dense cases match exactly. MATLAB returns a nonzero hierarchy
status flag for the three dense 32-CPU points, which remain included. The
standalone kernel study finds Rust/C SpMV ratios of 0.666 to 0.915 and bounded
recursive-cycle ratios of 0.737 to 0.803; it is not a complete C solver.

There is one disclosed provenance repair. The immutable main archive wrote
`source_commit=unknown` in 90 Rust and six standalone-C JSON files because the
wrapper and compiled drivers used different environment-variable names. A
derived copy changes only those 96 fields using the exact source manifest and
records the raw-tree digest plus every before/after hash. MATLAB identities,
input hashes, environment IDs, timings, and numerical values were already
correct. Assess whether this bookkeeping correction affects confidence in the
study, but do not describe it as a rerun or altered measurement.

Please produce a rigorous review with the following sections:

1. **Executive verdict.** What has actually been established about Rust versus
   MATLAB/C, and how strong is the evidence?
2. **Performance decomposition.** Recompute cross-implementation ratios,
   scaling, speedup, efficiency, time shares, and any useful Amdahl-style bounds
   by graph family and stage. Explain where Rust's advantage comes from.
3. **Why high-core scaling is weak.** Evaluate at least these competing
   mechanisms: serial hierarchy construction; one-time parallel-plan cost;
   insufficient work per parallel operator on sparse graphs; memory-bandwidth
   saturation; synchronization and reduction costs; Rayon scheduling overhead;
   NUMA placement and first-touch effects across two sockets/four NUMA nodes;
   CPU-frequency changes; MATLAB sparse/MEX kernels that may remain serial; and
   measurement noise or order effects. Identify which mechanisms are directly
   supported, merely consistent with the data, or contradicted.
4. **Fairness and validity.** Assess identical-input construction, native
   stopping-rule differences, iteration counts, hierarchy differences, the
   dense MATLAB warning flag, process-level RSS, three repetitions, execution
   order rotation, thread controls, build flags, and the source-identity repair.
   Flag any comparison that should be weakened or reframed.
5. **Optimize or test next?** Decide whether current evidence warrants more code
   optimization, more diagnostic testing, or both. Rank proposed actions by
   expected information value and likely end-to-end benefit. Separate changes
   aimed at single-RHS latency, repeated-RHS throughput, setup reuse, and memory.
6. **Targeted next experiments.** Specify a minimal experiment matrix capable of
   distinguishing the main scaling hypotheses. Consider 1/8/16/32 cores,
   one-socket versus two-socket binding, compact versus spread placement,
   first-touch policy, larger edge counts, stage/component profiling, hardware
   counters, memory bandwidth, CPU time versus wall time, plan reuse, setup
   reuse, and at least five repetitions. State expected outcomes under each
   hypothesis.
7. **Optimization candidates.** Review whether effort should focus on parallel
   hierarchy construction, contraction/endpoint sorting, parallel-plan build or
   caching, CMG apply granularity thresholds, sparse matvec/projection kernels,
   reductions, workspace placement, or batching. Explain why each candidate is
   or is not supported by the measurements.
8. **Documentation corrections.** Identify overclaims, missing caveats, unclear
   plots, or additional tables/figures needed before presenting this as the
   maintained performance reference.

Be concrete. Cite exact rows or figure patterns for important claims. Do not
attribute causality from elapsed-time curves alone. Treat the pending batch16
results as unknown, and explain how those results could change the recommendation
when they arrive.
