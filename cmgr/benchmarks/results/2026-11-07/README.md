# Five-method comparison on six synthetic panels, 2026-11-07

This is a focused, exploratory comparison of complete `feols()` calls on synthetic AKM-style panels. It uses one seed, one panel per case and three timed repeats, and it doesn't establish general speed rankings. The data and settings are recorded in this directory; [the benchmark instructions](../../README.md) explain how to reproduce it. The timings were measured at the pinned source revisions listed in [`manifest.json`](manifest.json), not on the release tarballs.

## What was compared

Every method fits `y ~ x1 + x2 + x3 + x4 | worker + firm` with the same observations and returns the demeaned columns. The time covers the whole call, including backend setup.

The two CMG configurations differ in one step. By default, cmgr works on the larger fixed-effect block, which is workers here, and removes levels with at most five distinct neighbors using exact algebra: each such worker's effect is substituted out, CMG solves the smaller system, and the worker's effect is then recovered. Workers with more than five firms stay in the graph. Nothing is sampled, no observations are dropped, and CMG's own numerics are unchanged. `schur = FALSE` switches this reduction off, so CMG solves the full worker-firm graph for the same model and observations.

| Column | Configuration |
|---|---|
| Native default | native fixest, `fixef.tol = 1e-6`, `fixef.iter = 10000` |
| Native tight | native fixest, `fixef.tol = 1e-10`, `fixef.iter = 20000` |
| withinr additive | `LsmrDemeaner(fixef_atol = 1e-8, fixef_btol = 1e-8, fixef_maxiter = 10000L, preconditioner = "additive")`; this preconditioner applies withinr's own approximate Schur step |
| CMG, full graph | `CmgDemeaner(fixef_tol = 1e-8, fixef_maxiter = 10000L, schur = FALSE)` |
| CMG, with elimination | `CmgDemeaner(fixef_tol = 1e-8, fixef_maxiter = 10000L, schur = TRUE)` |

## Timings

Median elapsed seconds for complete calls, from [`summary.csv`](summary.csv):

| Case | Native default | Native tight | withinr additive | CMG, full graph | CMG, with elimination |
|---|---:|---:|---:|---:|---:|
| 0.5% random moves (`random_0.005`) | 1.485 | 10.481 | 0.029 | 0.031 | 0.015 |
| 1% random moves (`random_0.01`) | 0.979 | 4.233 | 0.045 | 0.045 | 0.018 |
| 2% random moves (`random_0.02`) | 0.350 | 0.646 | 0.046 | 0.046 | 0.018 |
| 5% random moves (`random_0.05`) | 0.128 | 0.329 | 0.043 | 0.046 | 0.026 |
| 20% random moves (`random_0.2`) | 0.051 | 0.067 | 0.053 | 0.116 | 0.045 |
| 5% mostly neighboring moves (`local_0.05`) | 3.071 | 18.567 | 0.042 | 0.044 | 0.023 |

These times are at the solver-specific settings shown above, and they don't represent equal achieved accuracy, as the [next section](#accuracy) shows. Tightening or loosening any method's tolerance can change its time. No fit produced an error or warning.

## Accuracy

Tolerances have different meanings and implementations across these solvers. Native MAP has stopping checks on iterate changes and on SSR changes, withinr's LSMR uses its `atol` and `btol` settings, and CMG's `fixef_tol` bounds a normalized fixed-effect moment recomputed from the returned residuals. The same number therefore doesn't mean the same accuracy, and a tolerance of `1e-6` on one solver can't be compared directly with `1e-8` on another.

As a simple way to illustrate this, I compared each fit with an independent sparse direct solve and checked it against the following thresholds. They are ad hoc choices for this comparison, not universal requirements for correct results, and many applications need much less than this.

| Metric | Threshold | Measures |
|---|---|---|
| Coefficients | `1e-7` | Largest absolute difference from reference coefficients |
| Regression residuals | `1e-5` | Largest absolute difference from reference regression residuals |
| Demeaned columns | `1e-5` | Largest absolute difference across the demeaned outcome and controls |
| Normalized FE moment | `1e-8` | Largest value, across worker and firm groups and columns, of abs(sum of the projected column within the group) / (sqrt(observations in that group) × Euclidean norm of the centered original column); weights are all one here |

The reference uses R and Matrix, with one firm anchor per connected component and no cmgr calls. On a small fixture it agreed with dense dummy-variable QR to 5.88e-13 ([`oracle-check.txt`](oracle-check.txt)).

**withinr and CMG.** Both CMG configurations and withinr stayed below all four thresholds in every case, with different achieved accuracy. Largest values across all six cases, including warmups:

| Method | Coefficients | Residuals | Demeaned columns | FE moment |
|---|---:|---:|---:|---:|
| withinr additive | 2.12e-14 | 5.04e-6 | 6.18e-6 | 4.01e-9 |
| CMG, full graph | 1.98e-14 | 1.03e-8 | 7.82e-9 | 1.49e-11 |
| CMG, with elimination | 1.89e-14 | 1.65e-9 | 1.10e-9 | 9.84e-13 |

**Native fixest.** Native fixest is somewhat less accurate on particularly difficult graphs in these measures, mainly in residuals and demeaned columns, even where slopes agree closely. Largest values per case, including warmups (true slopes range from 0.25 to 1):

| Case | Setting | Coefficients | Residuals | Demeaned columns | FE moment |
|---|---|---:|---:|---:|---:|
| `random_0.005` | default | 2.86e-6 | 0.151 | 0.211 | 4.60e-6 |
| | tight | 3.71e-10 | 1.77e-3 | 4.95e-3 | 4.35e-8 |
| `random_0.01` | default | 5.34e-7 | 0.0587 | 0.154 | 5.00e-6 |
| | tight | 9.95e-11 | 1.04e-3 | 2.83e-3 | 6.44e-8 |
| `random_0.02` | default | 1.11e-10 | 4.10e-4 | 5.81e-3 | 9.17e-7 |
| | tight | 1.65e-12 | 2.20e-4 | 3.36e-4 | 5.31e-8 |
| `random_0.05` | default | 6.02e-14 | 1.90e-5 | 4.88e-5 | 3.09e-8 |
| | tight | 1.71e-14 | 1.22e-9 | 1.20e-8 | 5.16e-12 |
| `random_0.2` | default | 2.26e-14 | 3.61e-6 | 3.42e-6 | 2.51e-8 |
| | tight | 2.80e-14 | 1.14e-10 | 2.31e-10 | 1.68e-12 |
| `local_0.05` | default | 4.56e-6 | 0.233 | 0.287 | 2.98e-6 |
| | tight | 3.86e-10 | 1.50e-3 | 4.09e-3 | 3.41e-8 |

How these compare with the thresholds depends on the graph:

- **Default tolerance.** Every case is above at least one threshold, by very different amounts. At 20% mobility only the moment is above, at 2.505e-8 against 1e-8. On the low-mobility and mostly neighboring graphs, residuals and demeaned columns differ by up to 0.233 and 0.287, and coefficients exceed `1e-7` in three cases, with a largest difference of 4.56e-6.
- **Tight tolerance.** All four thresholds are met at 5% and 20% mobility. In the other cases coefficients stay within 3.86e-10, while residuals, demeaned columns and moments remain above their thresholds.

## Findings

These findings compare medians at the settings above, so they aren't time-to-identical-accuracy comparisons.

**The elimination step helps on these panels.** Full-graph CMG took 1.77 to 2.58 times as long as CMG with elimination. Elimination often used more PCG iterations (for example 25 against 13 at 0.5% mobility), but each iteration works on a smaller system. These panels favor the step: with six periods, only a worker at six distinct firms can stay in the graph.

**withinr's additive backend is a close competitor.** Compared with full-graph CMG, withinr's median was within about 7% in five cases and 2.19 times faster at 20% mobility (0.053 s against 0.116 s). CMG with elimination had the lower median in all six cases, with withinr taking 1.18 to 2.56 times as long. At 20% mobility that ratio is 1.18, a difference of only 8 ms, which is too small to treat as a meaningful ranking. Both methods use a Schur step, in different forms: exact and partial in cmgr, approximate in withinr's preconditioner.

**Achieved accuracy differs, and that affects any time comparison.** Largest residual differences were 5.04e-6 for withinr, 1.03e-8 for full-graph CMG and 1.65e-9 for CMG with elimination. Tightening withinr's tolerance or loosening CMG's would change their times, and that wasn't measured.

**Native tight depends on mobility.** At 5% mobility it took 12.65 times as long as CMG with elimination and 7.65 times as long as withinr. At 20% mobility those ratios fall to 1.49 and 1.26, and native tight was faster than full-graph CMG (0.067 s against 0.116 s). On these panels, higher mobility makes the native method much faster, in line with low-mobility graphs being the harder case.

The published ratios cover only pairs where both sides met all four thresholds, so `ratios.csv` has no ratio for the other pairs. The times for those other cells remain in the table above.

## Panels and topology

Each case has 120,000 observations: 20,000 workers over 6 periods at 1,000 roughly equal-sized firms, with 4 independent Gaussian controls and unit weights. The outcome combines the controls, worker effects, firm effects and Gaussian noise. In `local_0.05`, 99% of moves go to a neighboring firm.

From [`topology.csv`](topology.csv):

| Case | Annual mobility | Ever moved | Connected components | Firms in largest component | Distinct worker-firm edges |
|---|---:|---:|---:|---:|---:|
| `random_0.005` | 0.50% | 2.5% | 501 | 87 | 20,499 |
| `random_0.01` | 0.98% | 4.8% | 173 | 804 | 20,983 |
| `random_0.02` | 1.96% | 9.4% | 21 | 980 | 21,958 |
| `random_0.05` | 4.95% | 22.5% | 1 | 1,000 | 24,947 |
| `random_0.2` | 20.14% | 67.5% | 1 | 1,000 | 40,139 |
| `local_0.05` | 4.95% | 22.5% | 1 | 1,000 | 24,706 |

The three lowest-mobility panels are disconnected, so they aren't one connected labor market. The full graph has 21,000 vertices in every case.

## Limitations

- **Design.** One seed and one generated panel per case; repeats time the same data. The panels are synthetic, balanced and unit-weighted, so they don't represent all AKM designs, weighted or unbalanced panels, or many regressors.
- **Favorable to elimination.** Short panels and low mobility produce mostly low-degree workers. Nothing here supports a claim about long panels or many high-degree workers.
- **Timing precision.** There are three timed repeats, with a partial cyclic method order and one-millisecond timer resolution on calls as short as 15 ms. Medians are exploratory, with no confidence intervals. Minimum and maximum times are in `summary.csv`, and every call is in [`timings.csv`](timings.csv).
- **Threads.** Budgets were four for fixest and Rayon and one for BLAS. Actual CPU use wasn't measured, and withinr has no per-call thread setting.
- **Memory.** Not measured. There is no peak-memory result and no large administrative-data run.
- **Platform.** One machine and one operating system.
- **withinr source.** The proposed bindings at a pinned PR commit ([within PR #84](https://github.com/py-econometrics/within/pull/84)), not latest Rust `main` or the R-universe binding.

## Sources and data

Exact source revisions, hashes and settings are in [`manifest.json`](manifest.json), and [the benchmark instructions](../../README.md) rebuild from the same pinned commits.

**Environment.** Apple M2 Ultra, 24 cores, 192 GiB; macOS 26.6.2; R 4.6.1; Rust 1.85.1; Apple clang 21; OpenBLAS 0.3.34; fixest with OpenMP. The run took about three minutes.

**Data in this directory:**

- [`summary.csv`](summary.csv): medians, ranges, largest differences and iterations per case and method. Its `valid` and `failed_gates` columns record whether all four thresholds were met and which were exceeded.
- [`ratios.csv`](ratios.csv): pairwise ratios of medians. Its `qualified` column marks the pairs where both sides met all four thresholds, and only those have a ratio.
- [`timings.csv`](timings.csv), [`warmups.csv`](warmups.csv): every timed call and warmup
- [`topology.csv`](topology.csv): realized panel graphs
- [`plan.R`](plan.R): fixed plan and thresholds, named `gates` in the file
- [`oracle-check.txt`](oracle-check.txt): reference check against dense QR
- [`manifest.json`](manifest.json): sources, hashes, hardware, software and settings
- [`audit-receipt.json`](audit-receipt.json), [`SHA256SUMS`](SHA256SUMS): data checksums

The CSV files are the record, and this page is a reading of them. All 90 timed rows, 30 warmups, 30 medians and 120 ratio rows, along with the threshold flags, were recomputed independently from the raw data and matched.
