\name{demean_fixest}
\alias{demean_fixest}
\title{Project prepared columns on two categorical fixed effects}
\usage{demean_fixest(y, X_raw, fe_id_list, r_nb_id_Q, weights = NULL,
  tol = 1e-8, maxiter = 10000L, nthreads = 1L, schur = TRUE)}
\arguments{
\item{y}{Numeric outcome vector, list of numeric outcome vectors, or scalar zero if absent.}
\item{X_raw}{Numeric regressor matrix, or scalar zero if absent.}
\item{fe_id_list}{List of two integer ID vectors of equal length (at least two observations), with compact one-based IDs.}
\item{r_nb_id_Q}{Two positive whole numbers giving the level counts for the ID vectors. Every declared level must occur.}
\item{weights}{Positive finite observation weights, or \code{NULL} for unit weights.}
\item{tol}{Numeric scalar, finite and strictly between zero and one. Relative weighted fixed-effect moment tolerance; see \code{\link{CmgDemeaner}}.}
\item{maxiter}{Positive whole number. Maximum PCG iterations per column.}
\item{nthreads}{Whole number from 1 to 1024. Maximum concurrent numerical workers.}
\item{schur}{\code{TRUE} (default) or \code{FALSE}. Whether to apply exact worker elimination before the CMG solve. \code{FALSE} makes CMG solve the full worker-firm graph for the same projection; see \code{\link{CmgDemeaner}}.}
}
\description{Residualizes numeric columns on two categorical intercept effects, using CMG-preconditioned conjugate gradients in native code.}
\details{Input rows must already match the estimator's selected sample; no rows are removed. Returned columns are in their original units and are not multiplied by square-root weights.

Each call builds one hierarchy, which serves all of its columns. Nothing is cached across calls. Named-effect recovery, GLM or IRLS reweighting, varying slopes and a total-process memory cap are not provided.

Invalid input, an exhausted iteration budget or a failed moment check raises an error rather than returning partial results. An interrupt requests native cancellation, and all native work is joined before an error is raised. That error is an ordinary R error, so \code{try()} and error handlers can catch it.}
\value{A list with elements:
\describe{
\item{\code{X_demean}}{Residualized regressor matrix with the column names of \code{X_raw}, or a 1-by-1 zero matrix if \code{X_raw} is absent.}
\item{\code{y_demean}}{Residualized outcome, with a vector or list shape matching \code{y}, or scalar zero if absent.}
\item{\code{iterations}}{PCG iterations for each column.}
\item{\code{means}}{Zero, because no warm-start state is exposed.}
\item{\code{fe_moment}}{Normalized weighted moment for each column, checked against both original fixed-effect blocks.}
\item{\code{graph_residual}}{Graph residual diagnostic for each column. It refers to the system actually solved, which changes when elimination is applied.}
\item{\code{timing}}{Named vector: \code{vertices}, \code{edges} and \code{levels} of the solved system, then \code{setup}, \code{projection} and \code{total} native seconds. R allocation and binding time are excluded.}
\item{\code{reduction}}{Named vector: \code{original_vertices}; \code{effect}, the selected block (1 or 2 in the supplied order, 0 when disabled); \code{eliminated_levels} and \code{retained_levels} in that block; \code{max_degree}, the degree cutoff (5 when enabled, 0 otherwise).}
}
The per-column diagnostics are in X-then-y order.}
\seealso{\code{\link{CmgDemeaner}}}
\examples{
set.seed(1)
worker <- rep(1:40, each = 5)
firm <- rep_len(1:8, length(worker))
x <- cbind(x = rnorm(length(worker)))
y <- rnorm(length(worker))

ans <- demean_fixest(y, x, list(worker, firm), c(40L, 8L))
ans$reduction

full_graph <- demean_fixest(y, x, list(worker, firm), c(40L, 8L), schur = FALSE)
max(abs(ans$y_demean - full_graph$y_demean))
}
