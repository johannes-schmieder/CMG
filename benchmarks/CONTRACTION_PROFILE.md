# Contraction subphase profile

The read-only profiler in `benchmarks/src/bin/contraction-subphase-profile.rs`
reconstructs each nonterminal Galerkin contraction and checks it exactly against
the production `Aggregation::contract` result before using any timing.

The 2026-08-23 million-scale profile covers:

- a 1,000,000-vertex path;
- a 1,000,000-vertex, 1,500,000-edge worker–firm graph;
- a 200,000-vertex, 1,600,000-edge dense worker–firm graph.

Across these cases, comparison sorting accounts for 77.99% of attributed manual
contraction time. The remaining aggregate shares are 8.41% duplicate merging,
7.43% edge mapping, 5.99% diagonal construction, and 0.18% final metadata.
Sorting is concentrated in the worker–firm cases; path contraction remains
mapping-dominated and inexpensive.

The machine-readable result is retained at
`.ci/performance/contraction-subphase-profile.json`. The profile changes no
production numerical behavior and does not by itself justify retaining a new
sorting algorithm. Any candidate must preserve endpoint order, edge-weight bit
order within duplicate endpoint groups, compensated duplicate summation,
diagonal bits, matrix nonzeros, operator-norm metadata, hierarchy diagnostics,
and certified solve results.
