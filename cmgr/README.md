# cmgr: experimental two-way fixed-effect projection

This optional R package residualizes columns against two categorical intercept
fixed effects using CMG-PCG. The companion fixest branch adds an explicit
`demeaner` option; ordinary fixest calls retain the native implementation.

```r
library(fixest) # companion codex/cmg-two-way branch
fit <- feols(y ~ x1 + x2 | worker + firm, data,
             demeaner = CmgDemeaner(fixef_tol = 1e-8), nthreads = 4)
```

Use the configuration's `fixef_tol` and `fixef_maxiter` controls. The previous
`tol` and `maxiter` names remain compatibility aliases; supplying both names
for the same control is an error. Do not also pass
`fixef.tol`, `fixef.iter` or `fixef.algo`. Formula processing, sample selection,
singleton removal, collinearity decisions, OLS/IV estimation, covariance and
prediction remain in fixest. Named-effect recovery still uses native fixest.

The supported interface is two categorical intercept effects, including weighted
OLS, IV, multiple outcomes, stepwise regressors, split samples, offsets,
FE-only fits, `feols.fit()` and `demean()`. Three or more effects, varying slopes,
stepwise fixed-effect specifications and GLM/PPML are rejected. The constructor
and dispatcher are experimental and should be reconciled with fixest PR #647
before proposing an upstream interface.


## Optional exact worker elimination

Elimination is opt-in, so the existing full-graph path remains available:

```r
cmg_only <- feols(y ~ x1 + x2 | worker + firm, data,
                  demeaner = CmgDemeaner(schur = FALSE), nthreads = 4)
cmg_schur <- feols(y ~ x1 + x2 | worker + firm, data,
                   demeaner = CmgDemeaner(schur = TRUE), nthreads = 4)
```

`schur = TRUE` selects the FE block with more levels (normally workers; ties
select the first block in the prepared ID order). It eliminates levels with
at most five distinct neighbors after combining repeated worker--firm
observations. Higher-degree workers remain as vertices of the system actually
solved. Stayers create no firm edges, but their observations and within-worker
variation remain in the regression. No sampling is used, and CMG's source,
options and PCG implementation are unchanged. This option is available through
both `CmgDemeaner()` and `cmgr::demean_fixest()`.

Use the same tolerance, threads, sample and formula for paired on/off timings.
Both modes check the original weighted FE moments. The low-level function's
`reduction` diagnostics report the selected block and eliminated/retained level
counts; named `timing` entries report the actual graph size and native setup and
projection times. Include setup in complete-call comparisons. Turning
elimination off isolates its incremental contribution conditional on CMG; it
does not create a Schur-without-CMG solver. Native fixest remains a third
comparison with its own, numerically different convergence criterion.

## Build locally

This version has been built and tested on Apple Silicon macOS with R 4.6.1 and
Rust 1.85.1. Its Unix C bridge is not yet a Windows port. Rust 1.85 is the declared
minimum inherited from CMG; the integration is checked on Rust 1.85.1.

The R package lives in `cmgr/` in the CMG repository. The repository root is
the sole maintained CMG implementation. Generated copies under
`cmgr/src/rust/vendor/cmg` and their provenance records are ignored by Git.
A standalone R source tarball includes that exact snapshot, so installation
can compile offline without requiring the enclosing repository.

Put `cargo`, R and a C compiler on PATH. Building from a repository checkout
also requires Python 3.9+ and Git. Commit changes to the root CMG source before
packaging: the preparation step refuses to substitute an older committed
solver for modified working source.

From the CMG repository root:

```sh
mkdir -p /path/to/local-R-library /path/to/package-output
cd /path/to/package-output
/path/to/CMG/cmgr/tools/build.sh
R CMD INSTALL --library=/path/to/local-R-library cmgr_0.0.2.tar.gz
R_LIBS_USER=/path/to/local-R-library R CMD INSTALL \
  --library=/path/to/local-R-library /path/to/fixest-cmg
```

`R CMD INSTALL /path/to/CMG/cmgr` also prepares the snapshot automatically.
`inst/CMG-SOURCE.json` records the exact root commit and every copied file hash.
The adapter uses upstream defaults, including `max_levels = 32`. Registry
sources retain their licenses; Cargo validates the lockfile and compiles offline.
The fixest integration is experimental and is not in upstream fixest or on CRAN.

The constructor follows fixest PR #647's configuration-object interface.
`fixest::CmgDemeaner()` is the frontend convenience function;
`cmgr::CmgDemeaner()` supplies its validated configuration. The package's
low-level `demean_fixest()` keeps its `tol` and `maxiter` argument names.

## Numerical and execution contract

With signed dummy matrix Z = [D1, -D2], the adapter constructs the weighted
bipartite Laplacian L = Z'WZ once per call, builds one hierarchy, and solves
L theta = Z'Wv for every input column when elimination is disabled. With
`schur = TRUE`, it instead constructs the exact partial Schur graph once,
transforms each RHS, solves through the same CMG-PCG API, and recovers the
eliminated potentials. Weighted within-worker centering avoids cancellation in
the reduced RHS; stayer contributions are exactly zero, and the largest cell
balances only each eliminated worker's summation roundoff. Returned residuals are v - Z theta in the
original observation units. Weights are normalized by their maximum; underflow
to zero is rejected. Each column is scaled and its global weighted mean removed
before the solve. Component null spaces are handled by CMG.

`tol` bounds the largest weighted FE moment, divided by the square root of the
group weight and the weighted norm of the centered input. The adapter checks
the actual delivered residuals. PCG uses an absolute graph-residual target
derived from that tolerance, the RHS norm and the minimum group weight. Failure,
invalid input or certificate failure raises an error; there is no silent fallback.
This tolerance is different from fixest's MAP stopping tolerance and is not a
universal forward-error or coefficient-accuracy guarantee on ill-conditioned
graphs. Downstream numerical comparisons are necessary.

The C bridge protects R buffers and passes separate column pointers without
combining y and X into another input matrix. Rust constructs one Rayon pool per
call. Graph construction uses that team; independent column solves each use a
single numerical lane, up to `nthreads` concurrent solves. R's main thread polls
for interruption, requests cooperative cancellation, and joins all work before
releasing buffers or reporting an error. Workers never call R. There is no graph
cache across calls, warm-start handle, total process memory cap or IRLS reweight
API in this version. Each concurrent solve owns a workspace.

`demean_fixest()` exposes prepared-ID projection and returns iterations, FE
moments, graph residual diagnostics and native setup/projection timing. Its ID
vectors must be compact one-based integers with every declared level present;
weights must be positive. fixest performs its own sample preprocessing first.

## Checks

`tests/schur.R` checks exact low-degree elimination against dense weighted QR,
including degree boundaries, reversed FE order, repeated spells, disconnected
and stayer-only components, repeatability and numerical failure.
`tests/projection.R` compares weighted/disconnected projections with independent
dense dummy-variable QR and exercises invalid input and convergence failure.
The companion fixest backend tests compare complete model results and
supported paths across native MAP, withinr and CMG where supported. The retained experiment also includes a
SIGINT-and-recovery check and the speed-study script and results. Timing on
synthetic examples does not establish performance on actual AKM datasets.
