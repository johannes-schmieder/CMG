\name{demean_fixest}
\alias{demean_fixest}
\title{Project prepared columns on two categorical fixed effects}
\usage{demean_fixest(y, X_raw, fe_id_list, r_nb_id_Q, weights = NULL,
  tol = 1e-8, maxiter = 10000L, nthreads = 1L, schur = TRUE)}
\arguments{
\item{y}{Numeric outcome vector, list of outcome vectors, or scalar zero if absent.}
\item{X_raw}{Numeric regressor matrix, or scalar zero if absent.}
\item{fe_id_list}{List of two positive integer ID vectors.}
\item{r_nb_id_Q}{Two level counts. Every declared level must occur.}
\item{weights}{Positive finite observation weights, or NULL for unit weights.}
\item{tol,maxiter,schur}{See \code{\link{CmgDemeaner}}.}
\item{nthreads}{Maximum concurrent numerical workers, between 1 and 1024.}
}
\description{A direct native weighted fixed-effect projection using CMG-PCG.}
\details{Input rows must already match the estimator's selected sample. No rows
are removed. Returned residualized variables have their original units; they
are not multiplied by square-root weights. One hierarchy serves all columns.
An independent hierarchy is built for each call. Named-effect recovery, GLM,
varying slopes, reweighting caches and a total-process memory cap are outside
this first interface. Interrupts request native cancellation; all native work
is joined before an R error is raised.}
\value{A list with \code{X_demean}, \code{y_demean}, \code{iterations},
\code{means} (zero, because no warm-start state is exposed), \code{fe_moment},
\code{graph_residual}, \code{timing} and \code{reduction}. Diagnostics are in X-then-y order.
Timing entries are vertex count, edge count, hierarchy levels, setup seconds,
projection seconds and total native seconds. R allocation and binding time are
excluded from native timing. Reduction entries are original vertex count,
selected effect (1 or 2, in the supplied ID order; 0 when disabled), number of
eliminated levels, number of retained levels in that selected block, and degree
cutoff (5 when enabled, 0 otherwise). Graph residuals refer to the actual system
solved, which changes when Schur elimination is enabled. The FE moment always
checks both original fixed-effect blocks. Failed solves return an error rather than partial results.}
