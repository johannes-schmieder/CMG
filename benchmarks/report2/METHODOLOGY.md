# Methodology

The second SCC campaign diagnoses the scaling mechanisms observed in the first
CMG benchmark study. It uses the official MATLAB solver and pinned C MEX kernels
at upstream commit `19752fc102f8cae8e34f66457bfaccb1aaa60375`, together with
the repository's Rust production paths. Accepted results are tied to a clean
Git archive, compiled binary hashes, canonical fixture hashes, an environment
hash, topology-derived CPU lists, and successful SGE accounting.

Primary latency runs use two warmups and seven retained repetitions. Routing,
accuracy, and batch diagnostics retain five repetitions. Wall and process CPU
time are collected around each major stage. Profiling runs verify that retained
hierarchies, plans, iteration counts, and numerical certificates agree with the
unprofiled production path. Separate processes measure runtime baseline, input,
graph, hierarchy, plan, workspace, solve, and batch high-water memory.

The primary native-workflow comparison uses tolerance `1e-8` and a 1,000-iteration
limit. Interpretation always accompanies timing with native status, independent
relative residual, backward error, gauge-centered reference error, energy-norm
error, iteration count, and hierarchy metadata.
