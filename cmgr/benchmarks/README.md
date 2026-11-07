# Benchmark harness

This directory holds a fixed comparison of five complete `feols()` configurations on six synthetic AKM-style panels. The results are in [`results/2026-11-07`](results/2026-11-07/README.md).

The two CMG configurations differ in one step. With exact worker elimination, cmgr first removes workers with at most five distinct firms using exact algebra, CMG solves the smaller system, and those workers' effects are then recovered. Without it, CMG solves the full worker-firm graph for the same model and observations.

| Method | Configuration |
|---|---|
| `native_default` | native fixest: `fixef.tol = 1e-6`, `fixef.iter = 10000` |
| `native_tight` | native fixest: `fixef.tol = 1e-10`, `fixef.iter = 20000` |
| `withinr_additive` | `LsmrDemeaner(fixef_atol = 1e-8, fixef_btol = 1e-8, fixef_maxiter = 10000L, preconditioner = "additive")` |
| `cmg_off` | `CmgDemeaner(fixef_tol = 1e-8, fixef_maxiter = 10000L, schur = FALSE)`: no elimination |
| `cmg_schur` | `CmgDemeaner(fixef_tol = 1e-8, fixef_maxiter = 10000L, schur = TRUE)`: with elimination |

withinr's additive preconditioner applies its own approximate Schur step, so `withinr_additive` is not a method without elimination.

The plan is fixed before the first fit: one warmup and three timed calls per method and case, for 120 fits. There is no calibration, adaptive rerun or exclusion of any method. Each timed call covers the whole `feols()` call, including backend setup. Garbage collection and the independent reference solve are outside the timer.

## Exact sources for the published results

The published data came from these revisions, not from the release tarballs.

| Component | Repository | Revision |
|---|---|---|
| cmgr package | [johannes-schmieder/CMG](https://github.com/johannes-schmieder/CMG) | `1f577d5e030d1f4b5fbeb99967a38f2bc0a66e47` |
| Harness | [johannes-schmieder/CMG](https://github.com/johannes-schmieder/CMG) | `8d73db3f4221d27bbd2be036383e6e4540200dfd` |
| fixest integration | [johannes-schmieder/fixest-cmg](https://github.com/johannes-schmieder/fixest-cmg) | `47b832bd4795927a36f05822c1f062b3f117a2ce` |
| withinr | [py-econometrics/within](https://github.com/py-econometrics/within), [PR #84](https://github.com/py-econometrics/within/pull/84) | `6a85c94b9c42ba6862e1e03e1bb370a067514fcd` |

The fixest integration is based on upstream fixest `cf9461331033e6f7276ad39d8203751690e619d4`. The withinr revision is the proposed R binding from PR #84, with its paired Rust crates in the same workspace. It isn't latest Rust `main` or the R-universe binding, so `install.packages("withinr")` will not reproduce it.

The [`manifest.json`](results/2026-11-07/manifest.json) file records package versions, hashes, hardware and settings. Its installed-file hashes identify the timed build; a rebuild on another machine need not match them byte for byte. Its script hashes refer to the pinned harness. The `summarize.R` on `main` differs from the pinned one only in the display text of `comparison.md`.

## Build a fresh library

You need R, Git, Python 3.9 or later, `rustup`, and an R C/C++ toolchain. The measured fixest build had OpenMP enabled through libomp. Without OpenMP, native fixest timings will differ.

```sh
root="$PWD/cmgr-benchmark"        # any new directory
lib="$root/library"
mkdir -p "$lib" "$root/pkg"

rustup toolchain install 1.85.1
export RUSTUP_TOOLCHAIN=1.85.1

# Two CMG checkouts: the measured package and the measured harness are different revisions.
git clone https://github.com/johannes-schmieder/CMG "$root/CMG-package"
git -C "$root/CMG-package" checkout 1f577d5e030d1f4b5fbeb99967a38f2bc0a66e47
git clone https://github.com/johannes-schmieder/CMG "$root/CMG-harness"
git -C "$root/CMG-harness" checkout 8d73db3f4221d27bbd2be036383e6e4540200dfd

git clone https://github.com/johannes-schmieder/fixest-cmg "$root/fixest"
git -C "$root/fixest" checkout 47b832bd4795927a36f05822c1f062b3f117a2ce

git clone https://github.com/py-econometrics/within "$root/within"
git -C "$root/within" fetch origin pull/84/head
git -C "$root/within" checkout 6a85c94b9c42ba6862e1e03e1bb370a067514fcd

# Ordinary dependencies, plus Matrix for the reference solve. CRAN fixest is replaced below.
Rscript --vanilla -e 'install.packages(c("fixest", "Matrix"), lib = commandArgs(TRUE)[1], repos = "https://cloud.r-project.org")' "$lib"

# cmgr: build a source package in the new, empty directory, which generates the
# solver payload from Git, then install it.
(cd "$root/pkg" && "$root/CMG-package/cmgr/tools/build.sh")
R_LIBS_USER="$lib" R CMD INSTALL --library="$lib" "$root/pkg"/cmgr_*.tar.gz

# fixest integration.
R_LIBS_USER="$lib" R CMD INSTALL --library="$lib" "$root/fixest"

# withinr, built against the Rust crates in the same workspace.
(cd "$root/within" && R_LIBS_USER="$lib" WITHINR_DEV=true R CMD INSTALL --library="$lib" withinr)
```

cmgr compiles offline from its bundled sources. withinr's Rust dependencies are resolved by Cargo, so that step may need network access. If an installer reports a missing R dependency, install it into the same library.

Dependency versions from CRAN aren't pinned by this recipe. The manifest lists the package versions in the measured run, and the installed packages should report the same versions for fixest, cmgr and withinr.

## Run

Run the commands below in the same shell session as the build, or set `root` and `lib` again first.

Start with the untimed smoke test. It runs each method once on two tiny panels, cross-checks the reference solve against dense QR, and writes `smoke.csv` without reporting any timing. It finishes with `UNTIMED_SMOKE_COMPLETE`.

```sh
cd "$root/CMG-harness/cmgr/benchmarks"

RAYON_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 Rscript --vanilla benchmark.R \
  --library "$lib" \
  --output "$root/smoke" \
  --source CMG="$root/CMG-harness" \
  --source CMG_package="$root/CMG-package" \
  --source fixest="$root/fixest" \
  --source within="$root/within" \
  --smoke
```

For the full plan, leave out `--smoke` and use a different output directory. On the reference machine it took about three minutes and finished with `FIVE_METHOD_BENCHMARK_COMPLETE`.

```sh
RAYON_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 Rscript --vanilla benchmark.R \
  --library "$lib" \
  --output "$root/results" \
  --source CMG="$root/CMG-harness" \
  --source CMG_package="$root/CMG-package" \
  --source fixest="$root/fixest" \
  --source within="$root/within"
```

Notes on the options:

- **Output directory.** It must be new or empty.
- **Threads.** Run in a fresh `Rscript --vanilla` process. The script sets the thread variables before loading numerical libraries: fixest and Rayon get four threads, and BLAS gets one. withinr has no per-call thread argument, so it follows the process's Rayon setting.
- **`--source`.** It can be repeated and must point to a Git checkout. The run records HEAD, branch, status and tracked differences. Artifacts from building in place can appear in the recorded status, but they don't stop the run.
- **`--build-manifest`.** It can be repeated, and copies any file that establishes a package's identity into the results, for example when a source has no Git metadata.
- **Sharing results.** `identity.R` and `session-info.txt` record absolute paths and machine details such as hostname and login. Review them before publishing.

To regenerate the summary tables from retained raw results:

```sh
Rscript --vanilla summarize.R "$root/results"
```

This overwrites `summary.csv` and `ratios.csv` and writes `comparison.md` in that directory, so use a copy if you want to keep the originals.

| Output | Contents |
|---|---|
| `timings.csv`, `warmups.csv` | Every fit, with elapsed time, errors, flags, warnings and any thresholds exceeded |
| `summary.csv` | Median, minimum and maximum time, plus largest differences, per case and method |
| `ratios.csv` | Pairwise ratios of medians, only when both sides met all four thresholds |
| `topology.csv` | Realized mobility, connected components and graph size per case |
| `plan.R` | The fixed plan and thresholds, named `gates` in the file |
| `oracle-check.txt` | Reference solve compared with dense QR |

## Interpreting results

Tolerances have different meanings and implementations across these solvers, so the same number doesn't mean the same accuracy. As a simple illustration, each fit is compared with an independent sparse direct solve (R/Matrix, no cmgr calls) using ad hoc thresholds. They aren't universal requirements for correct results.

| Metric | Threshold | Measures |
|---|---|---|
| Coefficients | `1e-7` | Largest absolute difference from reference coefficients |
| Regression residuals | `1e-5` | Largest absolute difference from reference regression residuals |
| Demeaned columns | `1e-5` | Largest absolute difference across the demeaned outcome and controls |
| Normalized FE moment | `1e-8` | Largest value, across worker and firm groups and columns, of abs(sum of the projected column within the group) / (sqrt(observations in that group) × Euclidean norm of the centered original column); weights are all one here |

A result's `valid` flag also requires that the warmup and all three timed calls keep the full sample, report no nonconvergence and produce finite metrics.

When reading the results:

1. **Times aren't at equal accuracy.** Each time is for that method's settings. Tightening or loosening a tolerance can change it, and this benchmark doesn't measure time to identical accuracy.
2. **Ratios are limited.** `ratios.csv` gives a ratio only when both sides met all four thresholds. All other times stay visible in `summary.csv`.
3. **Read the metric and the size of the difference.** Being above a threshold doesn't mean the coefficients are wrong. Slopes can agree closely while residuals or demeaned columns differ more.
4. **An absent convergence flag isn't a certificate.** Successful `feols()` calls usually omit `convStatus`, which appears as `NA` in `converged`. The independent comparison still applies.
5. **Don't over-read small differences.** Timer resolution is one millisecond and there are three repeats on one dataset per case. Medians are exploratory, not confidence intervals.
6. **Thread settings are budgets.** Actual CPU use isn't measured, and each backend uses the budget in its own way.
7. **Memory isn't measured.** Process-level peak memory can't fairly separate methods that share a process and its runtime pools.
8. **Report the graph with the times.** Short panels and low mobility are well suited to elimination, so results don't transfer directly to long panels or many high-degree workers.

A run with any other source revision, including the release packages, is a separate experiment. It can use this harness, but keep its results separate and record its own source identities.
