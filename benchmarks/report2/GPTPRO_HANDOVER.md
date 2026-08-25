# GPT Pro handover

Critically evaluate the attached SCC2 CMG diagnostics packet. Recompute all
headline ratios from `data/samples.csv`; do not accept prose summaries without
checking them. Separate native-workflow comparisons from matched-work and
matched-accuracy comparisons.

Determine which hypotheses about hierarchy construction, plan creation,
operator granularity, reductions, memory bandwidth, runtime overhead, frequency,
NUMA placement, MATLAB utilization, and stopping rules are supported or
falsified. Quantify the maximum plausible end-to-end gain from optimizing each
of hierarchy setup, plan construction, PCG kernels, routing, NUMA behavior,
batching, and memory. Treat single-RHS latency, repeated-RHS throughput, retained
object reuse, and peak memory as separate objectives.

Recommend an ordered optimization program only for measured bottlenecks. For
each recommendation, state its predicted end-to-end impact, the cases most
likely to benefit, a falsification experiment, numerical/determinism constraints,
and a benchmark gate for retention. Explain specifically why performance does
or does not scale to 16 and 32 cores, and distinguish direct evidence from
inference.
