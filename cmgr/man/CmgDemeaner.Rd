\name{CmgDemeaner}
\alias{CmgDemeaner}
\title{Configure the optional two-way CMG backend}
\usage{CmgDemeaner(fixef_tol = 1e-8, fixef_maxiter = 10000L, schur = TRUE,
  tol, maxiter)}
\arguments{
\item{fixef_tol}{Numeric scalar, finite and strictly between zero and one. Relative weighted fixed-effect moment tolerance; see Details.}
\item{fixef_maxiter}{Positive whole number. Maximum PCG iterations per column.}
\item{schur}{\code{TRUE} (default) or \code{FALSE}. Whether to apply exact worker elimination before the CMG solve; see Details.}
\item{tol,maxiter}{Compatibility aliases for \code{fixef_tol} and \code{fixef_maxiter}. Supplying an alias together with its canonical argument is an error.}
}
\description{Creates a validated configuration for two-way fixed-effect projection with CMG-preconditioned conjugate gradients. It is used by the experimental fixest backend, which supports OLS with two categorical intercept effects.}
\details{
\strong{Exact worker elimination.} A worker with few employers does not need to be part of the graph that CMG solves. Given the firm effects, that worker's effect is the worker's weighted mean of the outcome net of those firm effects. Substituting this into the system removes the worker exactly and connects the worker's firms directly. After the smaller system is solved, the worker's effect is recovered.

This is applied by default, as preprocessing inside cmgr. It works on the fixed-effect block with more levels (normally workers; the first block on a tie) and eliminates only levels with at most five distinct neighbors. Repeated worker-firm observations are combined before counting. Higher-degree levels stay in the solved graph. Nothing is sampled or approximated, no observations are dropped, and CMG's options and PCG implementation are unchanged.

\code{schur = FALSE} is an optional comparison and troubleshooting switch. It disables this algebraic reduction, so CMG solves the full worker-firm graph for the same model and observations. It does not select another solver or estimator.

\strong{Tolerance.} \code{fixef_tol} bounds the largest weighted group moment, divided by the square root of the group weight and the weighted norm of the centered input column. This is recomputed from the returned observations, and failure raises an error. It is not equivalent to fixest's native stopping tolerance, and it is not a guarantee of coefficient accuracy on ill-conditioned graphs.
}
\value{A list of class \code{c("fixest_demeaner_cmg", "fixest_demeaner")} with elements \code{fixef_tol}, \code{fixef_maxiter} (an integer) and \code{schur}.}
\seealso{\code{\link{demean_fixest}}}
\examples{
CmgDemeaner()
CmgDemeaner(fixef_tol = 1e-10)
CmgDemeaner(schur = FALSE)
}
