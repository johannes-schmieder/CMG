#' Configure the optional two-way CMG backend
#'
#' Creates a validated configuration for two-way fixed-effect projection with
#' CMG-preconditioned conjugate gradients. It is used by the experimental fixest
#' backend, which supports OLS with two categorical intercept effects.
#'
#' \strong{Exact worker elimination.} A worker with few employers does not need
#' to be part of the graph that CMG solves. Given the firm effects, that worker's
#' effect is the worker's weighted mean of the outcome net of those firm effects.
#' Substituting this into the system removes the worker exactly and connects the
#' worker's firms directly. After the smaller system is solved, the worker's
#' effect is recovered.
#'
#' This is applied by default, as preprocessing inside cmgr. It works on the
#' fixed-effect block with more levels (normally workers; the first block on a
#' tie) and eliminates only levels with at most five distinct neighbors. Repeated
#' worker-firm observations are combined before counting. Higher-degree levels
#' stay in the solved graph. Nothing is sampled or approximated, no observations
#' are dropped, and CMG's options and PCG implementation are unchanged.
#'
#' \code{schur = FALSE} is an optional comparison and troubleshooting switch. It
#' disables this algebraic reduction, so CMG solves the full worker-firm graph
#' for the same model and observations. It does not select another solver or
#' estimator.
#'
#' \strong{Tolerance.} \code{fixef_tol} bounds the largest weighted group moment,
#' divided by the square root of the group weight and the weighted norm of the
#' centered input column. This is recomputed from the returned observations, and
#' failure raises an error. It is not equivalent to fixest's native stopping
#' tolerance, and it is not a guarantee of coefficient accuracy on ill-conditioned
#' graphs.
#'
#' @param fixef_tol Numeric scalar, finite and strictly between zero and one.
#'   Relative weighted fixed-effect moment tolerance; see Details.
#' @param fixef_maxiter Positive whole number. Maximum PCG iterations per column.
#' @param schur \code{TRUE} (default) or \code{FALSE}. Whether to apply exact
#'   worker elimination before the CMG solve; see Details.
#' @param tol,maxiter Compatibility aliases for \code{fixef_tol} and
#'   \code{fixef_maxiter}. Supplying an alias together with its canonical argument
#'   is an error.
#' @return A list of class \code{c("fixest_demeaner_cmg", "fixest_demeaner")} with
#'   elements \code{fixef_tol}, \code{fixef_maxiter} (an integer) and \code{schur}.
#' @seealso \code{\link{demean_fixest}}
#' @examples
#' CmgDemeaner()
#' CmgDemeaner(fixef_tol = 1e-10)
#' CmgDemeaner(schur = FALSE)
#' @export
CmgDemeaner <- function(fixef_tol = 1e-8, fixef_maxiter = 10000L, schur = TRUE,
                       tol, maxiter) {
    if (!missing(tol)) {
        if (!missing(fixef_tol)) stop("Supply only one of fixef_tol and tol.")
        fixef_tol <- tol
    }
    if (!missing(maxiter)) {
        if (!missing(fixef_maxiter)) stop("Supply only one of fixef_maxiter and maxiter.")
        fixef_maxiter <- maxiter
    }
    if (!is.numeric(fixef_tol) || length(fixef_tol) != 1L || !is.finite(fixef_tol) ||
        fixef_tol <= 0 || fixef_tol >= 1)
        stop("fixef_tol must be a finite number strictly between zero and one.")
    if (!is.numeric(fixef_maxiter) || length(fixef_maxiter) != 1L || !is.finite(fixef_maxiter) ||
        fixef_maxiter < 1 || fixef_maxiter > .Machine$integer.max || fixef_maxiter != floor(fixef_maxiter))
        stop("fixef_maxiter must be a positive integer.")
    if (!is.logical(schur) || length(schur) != 1L || is.na(schur))
        stop("schur must be TRUE or FALSE.")
    structure(list(fixef_tol = fixef_tol, fixef_maxiter = as.integer(fixef_maxiter), schur = schur),
              class = c("fixest_demeaner_cmg", "fixest_demeaner"))
}

#' Project prepared columns on two categorical fixed effects
#'
#' Residualizes numeric columns on two categorical intercept effects, using
#' CMG-preconditioned conjugate gradients in native code.
#'
#' Input rows must already match the estimator's selected sample; no rows are
#' removed. Returned columns are in their original units and are not multiplied
#' by square-root weights.
#'
#' Each call builds one hierarchy, which serves all of its columns. Nothing is
#' cached across calls. Named-effect recovery, GLM or IRLS reweighting, varying
#' slopes and a total-process memory cap are not provided.
#'
#' Invalid input, an exhausted iteration budget or a failed moment check raises an
#' error rather than returning partial results. An interrupt requests native
#' cancellation, and all native work is joined before an error is raised. That
#' error is an ordinary R error, so \code{try()} and error handlers can catch it.
#'
#' @param y Numeric outcome vector, list of numeric outcome vectors, or scalar
#'   zero if absent.
#' @param X_raw Numeric regressor matrix, or scalar zero if absent.
#' @param fe_id_list List of two integer ID vectors of equal length (at least two
#'   observations), with compact one-based IDs.
#' @param r_nb_id_Q Two positive whole numbers giving the level counts for the ID
#'   vectors. Every declared level must occur.
#' @param weights Positive finite observation weights, or \code{NULL} for unit
#'   weights.
#' @param tol Numeric scalar, finite and strictly between zero and one. Relative
#'   weighted fixed-effect moment tolerance; see \code{\link{CmgDemeaner}}.
#' @param maxiter Positive whole number. Maximum PCG iterations per column.
#' @param nthreads Whole number from 1 to 1024. Maximum concurrent numerical
#'   workers.
#' @param schur \code{TRUE} (default) or \code{FALSE}. Whether to apply exact
#'   worker elimination before the CMG solve. \code{FALSE} makes CMG solve the full
#'   worker-firm graph for the same projection; see \code{\link{CmgDemeaner}}.
#' @return A list with elements:
#' \describe{
#' \item{\code{X_demean}}{Residualized regressor matrix with the column names of
#'   \code{X_raw}, or a 1-by-1 zero matrix if \code{X_raw} is absent.}
#' \item{\code{y_demean}}{Residualized outcome, with a vector or list shape
#'   matching \code{y}, or scalar zero if absent.}
#' \item{\code{iterations}}{PCG iterations for each column.}
#' \item{\code{means}}{Zero, because no warm-start state is exposed.}
#' \item{\code{fe_moment}}{Normalized weighted moment for each column, checked
#'   against both original fixed-effect blocks.}
#' \item{\code{graph_residual}}{Graph residual diagnostic for each column. It
#'   refers to the system actually solved, which changes when elimination is
#'   applied.}
#' \item{\code{timing}}{Named vector: \code{vertices}, \code{edges} and
#'   \code{levels} of the solved system, then \code{setup}, \code{projection} and
#'   \code{total} native seconds. R allocation and binding time are excluded.}
#' \item{\code{reduction}}{Named vector: \code{original_vertices}; \code{effect},
#'   the selected block (1 or 2 in the supplied order, 0 when disabled);
#'   \code{eliminated_levels} and \code{retained_levels} in that block;
#'   \code{max_degree}, the degree cutoff (5 when enabled, 0 otherwise).}
#' }
#' The per-column diagnostics are in X-then-y order.
#' @seealso \code{\link{CmgDemeaner}}
#' @examples
#' set.seed(1)
#' worker <- rep(1:40, each = 5)
#' firm <- rep_len(1:8, length(worker))
#' x <- cbind(x = rnorm(length(worker)))
#' y <- rnorm(length(worker))
#'
#' ans <- demean_fixest(y, x, list(worker, firm), c(40L, 8L))
#' ans$reduction
#'
#' full_graph <- demean_fixest(y, x, list(worker, firm), c(40L, 8L), schur = FALSE)
#' max(abs(ans$y_demean - full_graph$y_demean))
#' @export
demean_fixest <- function(y, X_raw, fe_id_list, r_nb_id_Q,
                         weights = NULL, tol = 1e-8, maxiter = 10000L,
                         nthreads = 1L, schur = TRUE) {
    control <- CmgDemeaner(tol, maxiter, schur)
    if (!is.list(fe_id_list) || length(fe_id_list) != 2L)
        stop("CMG requires exactly two categorical fixed effects.")
    n <- length(fe_id_list[[1L]])
    if (n < 2L || length(fe_id_list[[2L]]) != n)
        stop("CMG needs at least two observations and matching ID lengths.")
    if (!is.numeric(r_nb_id_Q) || length(r_nb_id_Q) != 2L ||
        anyNA(r_nb_id_Q) || any(!is.finite(r_nb_id_Q)) ||
        any(r_nb_id_Q < 1 | r_nb_id_Q > .Machine$integer.max |
            r_nb_id_Q != floor(r_nb_id_Q))) stop("Invalid fixed-effect sizes.")
    ids <- lapply(fe_id_list, function(v) {
        if (!is.integer(v)) {
            if (!is.numeric(v) || anyNA(v) || any(!is.finite(v)) ||
                any(v != floor(v)) || any(v > .Machine$integer.max))
                stop("Fixed-effect IDs must be finite integers.")
            v <- as.integer(v)
        }
        v
    })
    # The C boundary independently validates storage and dimensions. IDs,
    # finite values, positive weights and level coverage are checked in Rust.
    if (is.list(y)) y <- lapply(y, as.double) else y <- as.double(y)
    if (is.matrix(X_raw)) storage.mode(X_raw) <- "double" else X_raw <- as.double(X_raw)
    if (!is.null(weights)) weights <- as.double(weights)
    if (!is.numeric(nthreads) || length(nthreads) != 1L || !is.finite(nthreads) ||
        nthreads < 1 || nthreads != floor(nthreads) || nthreads > 1024L)
        stop("nthreads must be an integer from 1 to 1024.")
    ans <- .Call(C_cmgr_project, y, X_raw, ids, as.integer(r_nb_id_Q), weights,
                 control$fixef_tol, control$fixef_maxiter, as.integer(nthreads), control$schur)
    if (is.matrix(X_raw) && ncol(X_raw)) colnames(ans$X_demean) <- colnames(X_raw)
    names(ans$timing) <- c("vertices", "edges", "levels", "setup", "projection", "total")
    names(ans$reduction) <- c("original_vertices", "effect", "eliminated_levels", "retained_levels", "max_degree")
    ans
}
