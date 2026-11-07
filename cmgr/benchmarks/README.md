# Fresh fixest integration benchmark

This is one fixed, focused comparison of native fixest (default/tight), withinr additive-Schwarz LSMR, CMG, and CMG with exact low-degree worker elimination. The source versions and package builds must be installed into a supplied R library before running. The benchmark uses the new PR-647-shaped public configuration constructors in the integration branch.

The default plan is six synthetic AKM-style cases, one warmup and three timed calls per method/case: 120 fits total. Data, common accuracy gates, case ordering and method ordering are fixed before the first fit. There is no calibration, failed-method exclusion or adaptive rerun. Each call includes construction of the backend, estimation and return of demeaned columns. GC and independent-reference calculations are outside the timer. Output must be new or empty.

```sh
RAYON_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 Rscript --vanilla benchmark.R \
  --library /path/to/R-library \
  --output /path/to/new-results \
  --source CMG=/path/to/CMG \
  --source fixest=/path/to/fixest-integration \
  --source within=/path/to/within \
  --build-manifest /path/to/package-build-receipt.txt
```

`--source` and `--build-manifest` can be repeated. Each `--source` must be a Git checkout; metadata records HEAD, branch, status and tracked diffs. For an archive checkout without `.git`, use its build manifest instead. A build manifest should establish any untracked/copied package-source identity. Installed library binary and R-code database SHA-256 hashes are also recorded. Paths are provenance rather than dependencies for regeneration: replace them with your local checkouts. `shasum` or `sha256sum` is used when available; otherwise hashes are explicitly missing.

For an **untimed tiny integration smoke**, append `--smoke` with a different empty output directory. This runs the same five methods once on each of two tiny panels and validates the independent reference against dense QR. It deliberately reports no elapsed benchmark times.

The script sets the thread environment before loading numerical libraries: fixest and Rayon budgets are four, BLAS budget is one. Withinr has no per-call thread argument; the Rayon global pool obeys the process environment. Run in a fresh `Rscript --vanilla` process to exclude user startup code. The environment settings are recorded; actual CPU utilization is not measured.

All observations are retained, including failures, warnings, and returned convergence flags. `summary.csv` requires every measured call and warmup to pass before qualifying a method/case. `ratios.csv` has no numerical ratio for pairs involving an invalid result. Memory usage is explicitly unmeasured: a shared-process RSS high-water mark would confound methods and persistent runtime pools.

Successful `feols()` calls ordinarily omit `convStatus`; they do not return `TRUE`. The raw `converged` column preserves this as `NA`, and `no_reported_nonconvergence` records the corresponding acceptance check. Native iteration-limit failures return `FALSE`; the external adapters raise an error on solver nonconvergence. Independently, every method must satisfy all four original-operator accuracy checks. An absent flag never bypasses those checks.

To regenerate tables from retained raw results:

```sh
Rscript --vanilla summarize.R /path/to/results
```

The data generation and direct reference preserve the November 7 six-case mobility experiment. CMGr, fixest and withinr numerical implementations are not modified by this harness.
