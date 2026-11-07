# Proposal: a CMG backend on top of fixest PR #647

**For fixest maintainers. This is a discussion proposal, and nothing here asks for a change to fixest's defaults.**

[fixest PR #647](https://github.com/lrberge/fixest/pull/647) proposes a `demeaner` argument, `MapDemeaner()` and `LsmrDemeaner()` constructors, and an explicit dispatcher where fixest residualizes columns on fixed effects. This proposal adds one more backend to that design: `CmgDemeaner()`, for two-way models such as AKM worker-firm panels. Its numerics live entirely in an optional package, [cmgr](README.md). Native MAP remains the default unless a user selects a backend per call or through session defaults.

## The patch

The patch targets #647 head `0752916d`. It is R-only, doesn't touch C++, and keeps #647's argument positions and dispatcher placement. It separates shared interface fixes from the CMG addition, so they can be reviewed apart:

- [Full patch against #647](https://github.com/johannes-schmieder/fixest-cmg/compare/0752916d36e7376439817712ddf835b662946171...codex/cmg-pr647)
- [Shared interface fixes](https://github.com/johannes-schmieder/fixest-cmg/tree/codex/pr647-interface-fixes)
- [CMG addition only](https://github.com/johannes-schmieder/fixest-cmg/compare/codex/pr647-interface-fixes...codex/cmg-pr647)

**Shared fixes** apply with or without CMG:

- Resolved MAP controls reach the native solver, so `MapDemeaner(fixef_tol = ...)` applies to the actual solve.
- When reusing a single-model native environment, explicit native tolerance and iteration overrides are honored. For a `split` environment, set these controls when you create the environment, because overrides supplied on reuse don't reach the split fits. Supplying native controls to an optional-backend environment is an error.
- Native `demean()` without an explicit backend keeps its existing validation contract.
- `demean()` on a saved fit reuses that fit's backend unless the caller chooses otherwise, and an optional backend's tolerance is never copied into MAP.
- Bare vectors and `data.frame` input with `as.matrix = TRUE` are normalized for optional backends.
- The withinr adapter accepts `within_solve_batch()` ([within PR #84](https://github.com/py-econometrics/within/pull/84)) or `solve_batch()`, and withinr nonconvergence is an error.

These fixes aren't bit-for-bit neutral against pristine upstream. Explicit native tolerances now propagate into split and multiple-model calls, which can change those results, and fits gain saved backend metadata.

**The CMG addition** puts `cmgr` under `Suggests`, adds a thin constructor with its print method and help page, recognizes the new class, adds one dispatcher branch with early scope guards, and adds tests.

## Exact worker elimination

By default, cmgr first removes workers with few employers from the system, using exact algebra. Each such worker's effect is substituted out, CMG solves the smaller system, and the effect is then recovered. This applies only to levels of the larger fixed-effect block with at most five distinct neighbors. Higher-degree workers stay in the solved graph. Nothing is sampled, no observations are dropped, and CMG's numerical code is unchanged.

`schur = FALSE` is an optional comparison and troubleshooting switch. It disables that reduction, so CMG solves the full worker-firm graph for the same model and observations. withinr's additive preconditioner also uses a Schur step, an approximate one of its own.

## Package boundary

fixest keeps formula processing, sample selection, singleton removal, collinearity decisions, estimation, covariance, prediction and backend dispatch. cmgr validates the prepared input, builds the graph, performs elimination and CMG-PCG solves, and checks delivered residuals against both effect blocks. fixest gains no Rust, no new compiled code and no hard dependency.

The contract is one exported function: fixest passes prepared columns, two compact integer ID vectors, level counts, weights and controls to `cmgr::demean_fixest()`, and gets back residualized columns in original units plus diagnostics. Failures raise errors, with no partial results and no silent solver fallback. The [integration guide](https://github.com/johannes-schmieder/fixest-cmg/blob/codex/cmg-pr647/CMG.md) and the `CmgDemeaner` and `demean_fixest` help pages have the details.

## Scope and maturity

`CmgDemeaner()` supports exactly two categorical intercept effects in weighted or unweighted OLS, including IV, multiple outcomes, stepwise regressors, split samples, offsets, FE-only fits, `feols.fit()` and `demean()`. Other specifications and GLM requests raise explicit errors.

Per-call selection is the recommended use in this preview. A global CMG selector makes ordinary no-FE, one-FE and many-FE `feols()` calls fail by design. IV diagnostics and named fixed-effect extraction use native demeaning internally, so their residualization doesn't necessarily match the accuracy of a CMG fit.

cmgr has been tested only on Apple Silicon with Homebrew R 4.6.1, and has no Windows port. It isn't ready for CRAN, so its `Suggests` entry would need resolving before any fixest CRAN release.

## The dispatcher is not a plugin system

#647's dispatcher branches on known classes, so installing cmgr alone adds nothing. A backend still needs a constructor, a recognized class and a dispatcher case, which is what the CMG addition provides. That small, visible surface may be what maintainers prefer. Automatic backend swapping would be a separate design, and this patch doesn't add it.

## Evidence

A [frozen benchmark](benchmarks/results/2026-11-07/README.md) compares native fixest, withinr's additive backend and CMG with and without elimination on six short synthetic panels. It is exploratory, its design favors elimination, and it measures pinned sources rather than this patch. It supports no general speed claim and doesn't identify a bug in fixest. Tests in `tests/cmg_tests.R` and `tests/demeaner_tests.R` compare complete fits against native and dense QR references.

## Questions for maintainers

1. Is constructor-plus-dispatch useful for a backend like this, or should such backends stay outside fixest?
2. What would you want before accepting a third explicit case: a registration interface, narrower scope, or broader platform evidence?
3. Should the shared fixes go into #647 regardless of any decision on CMG?

To try it, see the [preview release](https://github.com/johannes-schmieder/CMG/releases/tag/cmgr-v0.0.3-preview.1).
