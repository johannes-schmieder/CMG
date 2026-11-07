# cmgr: two-way fixed-effect projection with CMG

**Status: experimental preview.** cmgr is not on CRAN and is not part of upstream fixest.

cmgr residualizes numeric columns on two categorical intercept fixed effects, such as worker and firm in an AKM-style panel. It solves the projection with the [CMG](../README.md) graph solver, using CMG as a preconditioner for conjugate gradients (CMG-PCG). You can call it directly, or through an experimental fixest backend that handles formulas, samples and inference as usual.

CMG (Combinatorial Multigrid) is due to Koutis, Miller and Tolliver. The solver in this repository is an independent Rust port; see the [root README](../README.md) for attribution.

## Install

You need Rust 1.85 or later with Cargo (tested with 1.85.1) and a working R C/C++ toolchain. The declared minimum R is 4.0, but only R 4.6.1 has been tested. There is no Windows port.

The recipe below uses a separate library, so your regular fixest is left alone. It installs the experimental fixest source along with cmgr. If you only want `demean_fixest()`, you can skip both fixest installs.

```r
lib <- path.expand("~/R/cmg-preview")
dir.create(lib, recursive = TRUE, showWarnings = FALSE)
.libPaths(c(lib, .libPaths()))

# Ordinary fixest from CRAN brings in its R dependencies.
install.packages("fixest", lib = lib, repos = "https://cloud.r-project.org")

base <- "https://github.com/johannes-schmieder/CMG/releases/download/cmgr-v0.0.3-preview.1"
install.packages(file.path(base, "cmgr_0.0.3.tar.gz"),
                 lib = lib, repos = NULL, type = "source")
install.packages(file.path(base, "fixest_0.14.2.9000.tar.gz"),
                 lib = lib, repos = NULL, type = "source")
```

In later sessions, run `.libPaths(c(path.expand("~/R/cmg-preview"), .libPaths()))` before loading either package.

To build from a Git clone instead (this also needs Git and Python 3.9 or later):

```sh
git clone https://github.com/johannes-schmieder/CMG
mkdir cmgr-build && cd cmgr-build
../CMG/cmgr/tools/build.sh
mkdir -p "$HOME/R/cmg-preview"
R CMD INSTALL --library="$HOME/R/cmg-preview" cmgr_*.tar.gz
```

`remotes::install_github(subdir = "cmgr")` is not a supported route. The solver payload in the source package is generated from the repository root, and generating it requires Git metadata that a GitHub source archive doesn't contain.

## Example

```r
library(cmgr)

# Small low-mobility panel: workers mostly stay at a home firm.
set.seed(1)
n_workers <- 2000L; n_years <- 6L; n_firms <- 100L
worker <- rep(seq_len(n_workers), each = n_years)
home   <- sample(rep_len(seq_len(n_firms), n_workers))
firm   <- home[worker]
moved  <- runif(length(worker)) < 0.05
firm[moved] <- sample.int(n_firms, sum(moved), replace = TRUE)
x <- rnorm(length(worker))
y <- 0.5 * x + rnorm(n_workers)[worker] + rnorm(n_firms)[firm] + rnorm(length(worker))

ans <- demean_fixest(y, cbind(x = x), list(worker, firm), c(n_workers, n_firms),
                     nthreads = 2L)
str(ans$y_demean)   # residualized outcome, in original units
ans$reduction       # how many workers were eliminated (explained below)
ans$timing          # size of the solved graph; native setup and solve seconds
```

With the experimental fixest source installed, the same data can go through an ordinary `feols()` call:

```r
library(fixest)
d <- data.frame(y, x, worker, firm)
fit <- feols(y ~ x | worker + firm, d, demeaner = CmgDemeaner(), nthreads = 2)
```

fixest still handles formula processing, sample selection, singleton removal, collinearity, OLS/IV estimation, covariance and prediction. Select the backend per call, as here. The [integration guide](https://github.com/johannes-schmieder/fixest-cmg/blob/codex/cmg-two-way/CMG.md) covers this path, including why a global CMG selector isn't recommended in this preview.

## Exact worker elimination

A worker with few employers doesn't need to be part of the graph that CMG solves. Given the firm effects, that worker's effect is just the worker's weighted mean of the outcome net of those firm effects. Substituting this into the system removes the worker exactly and connects the worker's firms directly. Once the smaller system is solved, the worker's effect is recovered by back-substitution. In linear algebra this is a partial Schur complement.

cmgr does this by default, as a preprocessing step:

- It works on the fixed-effect block with more levels, normally workers. On a tie it uses the first block.
- It eliminates only levels with **at most five distinct neighbors**. Repeated worker-firm observations are combined before counting.
- Higher-degree workers stay in the graph as ordinary vertices, so the reduction is partial.
- Nothing is sampled or approximated, and no observations are dropped. Stayers add no firm-to-firm edges, but their observations and within-worker variation stay in the regression.
- CMG itself is unchanged: same solver source, options and PCG implementation.

Delivered residuals are checked against both original fixed-effect blocks, whichever graph was solved.

### Turning it off

`schur = FALSE` is an optional comparison and troubleshooting switch; the default is `TRUE`. It disables the algebraic reduction, so CMG solves the full worker-firm graph. The model and the observations stay the same, and CMG stays in use.

```r
off <- demean_fixest(y, cbind(x = x), list(worker, firm), c(n_workers, n_firms),
                     nthreads = 2L, schur = FALSE)
max(abs(ans$y_demean - off$y_demean))   # same projection, up to solver tolerance
c(on = ans$timing[["vertices"]], off = off$timing[["vertices"]])
```

Most users can ignore the switch. It is there to measure what the reduction contributes on your data, or to rule the reduction out when diagnosing a problem. For paired timings, keep the tolerance, threads, sample and formula the same. Include setup time, because that is where the reduction is built.

The reduction can apply only when many workers have few distinct employers, as in short panels and low mobility. Long panels with many high-degree workers will eliminate fewer of them, and this preview has no measurements for that case. The published timings cover six short synthetic panels and don't show that the default is faster in general.

## Scope and limits

**Supported:** exactly two categorical intercept effects, with positive weights or unit weights, and disconnected graphs. Through fixest this includes OLS, IV, multiple outcomes, stepwise regressors, split samples, offsets, FE-only fits, `feols.fit()` and `demean()`.

**Not supported:**

- three or more effects, or one-way models
- varying slopes
- stepwise fixed-effect specifications
- GLM/PPML, or any IRLS reweighting interface
- a graph cache across calls (each call builds its own hierarchy)
- a total-process memory cap

**Native parts of a fixest CMG fit:** IV diagnostics and named fixed-effect extraction use native demeaning internally, so their residualization doesn't necessarily reach the accuracy of the CMG projection.

**Status limits:** the only tested configuration is Homebrew R 4.6.1 on Apple Silicon (M2 Ultra, macOS 26.6.2) with Apple clang 21 and Rust 1.85.1. CRAN builds of R, Linux and Windows aren't qualified, and the C bridge is Unix-specific. It is not ready for CRAN: `R CMD check` reports a warning for Rust runtime termination symbols and a hidden-files note for Cargo metadata.

## What the tolerance means

`fixef_tol` (called `tol` in `demean_fixest()`) bounds the largest weighted fixed-effect moment, divided by the square root of the group weight and the weighted norm of the centered input column. This check is recomputed from the residuals actually returned. If it fails, or the solver hits its iteration limit, or the input is invalid, you get an error. There is no partial result and no silent fallback to another solver.

This is a different quantity from fixest's native stopping tolerance, so equal numbers don't mean equal accuracy. It also doesn't guarantee coefficient accuracy on ill-conditioned graphs. For a serious comparison, check results independently, as the [benchmark](benchmarks/results/2026-11-07/README.md) does.

Threading: `nthreads` sets the size of a Rayon pool created for the call. Columns are solved concurrently, with one numerical lane each. Interrupting from R requests cancellation, and all native work is joined before an error is raised. That error is an ordinary R error, so `try()` and error handlers can catch it, and a loop that wraps calls in `try()` may continue after an interrupt.

## Source arrangement

| Location | Contents |
|---|---|
| Repository root | The only maintained CMG solver |
| `cmgr/R`, `cmgr/src/bridge.c` | R interface and C boundary |
| `cmgr/src/rust/src` | Rust adapter (`lib.rs`) and exact elimination (`schur.rs`) |
| `cmgr/src/rust/vendor/cmg` | Generated, untracked snapshot of the root solver |
| `cmgr/src/rust/registry` | Cached third-party crates for offline compilation |
| `cmgr/tools` | Source preparation and `build.sh` |
| `cmgr/benchmarks` | Benchmark harness and published results |

Building a source package copies the exact committed root solver into the tarball, so installation compiles offline with `--locked` and doesn't need the enclosing repository. Preparation refuses to package uncommitted changes, whether tracked, staged or untracked, to the root solver or cmgr's maintained adapter files. Generated payloads stay untracked. `inst/CMG-SOURCE.json` records the source commit and a hash for every copied file. To see which solver you have installed:

```r
readLines(system.file("CMG-SOURCE", package = "cmgr"))
```

The adapter uses the solver's default options, including `max_levels = 32`.

## Checks

From a Git clone, with the packages installed:

```sh
R_LIBS_USER="$HOME/R/cmg-preview" Rscript cmgr/tests/configuration.R
R_LIBS_USER="$HOME/R/cmg-preview" Rscript cmgr/tests/projection.R
R_LIBS_USER="$HOME/R/cmg-preview" Rscript cmgr/tests/schur.R
```

These compare projections with independent dense weighted QR, covering degree boundaries, reversed effect order, repeated spells, disconnected and stayer-only components, and failure handling. Separately, an independent check ran 100 random small weighted problems in both effect orders, with elimination on and off. All 400 projections agreed with dense weighted QR, with a largest absolute difference of 1.75e-11. Targeted tests also cover interruption and recovery with elimination on.

## Further reading

- [Integration proposal](PROPOSAL.md): the small fixest patch and the package boundary
- [Benchmark results](benchmarks/results/2026-11-07/README.md): six synthetic panels, five methods, with limitations
- [Benchmark instructions](benchmarks/README.md): how to reproduce them
- [fixest integration guide](https://github.com/johannes-schmieder/fixest-cmg/blob/codex/cmg-two-way/CMG.md)
- [`cmgr-v0.0.3-preview.1` release](https://github.com/johannes-schmieder/CMG/releases/tag/cmgr-v0.0.3-preview.1)
