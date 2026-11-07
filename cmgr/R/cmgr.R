#' Configure the opt-in two-way CMG demeaner
#' @param fixef_tol Relative weighted fixed-effect moment tolerance.
#' @param fixef_maxiter Maximum PCG iterations per column.
#' @param tol,maxiter Compatibility aliases for fixef_tol and fixef_maxiter.
#' @param schur Enable exact elimination of levels with at most five distinct
#'   neighbors from the larger fixed-effect block (normally workers).
#' @export
CmgDemeaner <- function(fixef_tol = 1e-8, fixef_maxiter = 10000L, schur = FALSE,
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

#' Residualize fixest's prepared columns on two categorical effects
#' @param y Numeric outcome vector, list of vectors, or scalar zero when absent.
#' @param X_raw Numeric matrix, or scalar zero when absent.
#' @param fe_id_list List of two positive integer ID vectors.
#' @param r_nb_id_Q Two level counts corresponding to the ID vectors.
#' @param weights Positive observation weights, or NULL for unit weights.
#' @param tol Relative weighted fixed-effect moment tolerance.
#' @param maxiter Maximum PCG iterations per column.
#' @param schur See CmgDemeaner().
#' @param nthreads Maximum numerical worker count.
#' @return A list with residualized columns, iterations and diagnostics.
#' @export
demean_fixest <- function(y, X_raw, fe_id_list, r_nb_id_Q,
                         weights = NULL, tol = 1e-8, maxiter = 10000L,
                         nthreads = 1L, schur = FALSE) {
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
