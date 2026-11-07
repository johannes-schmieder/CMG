\name{CmgDemeaner}
\alias{CmgDemeaner}
\title{Configure the optional two-way CMG backend}
\usage{CmgDemeaner(fixef_tol = 1e-8, fixef_maxiter = 10000L, schur = TRUE,
  tol, maxiter)}
\arguments{
\item{schur}{Enable exact low-degree Schur elimination. Defaults to TRUE. Set \code{schur = FALSE} to retain the full CMG graph.}
\item{fixef_tol}{Positive relative weighted fixed-effect moment tolerance, less than one.}
\item{fixef_maxiter}{Maximum PCG iterations per column.}
\item{tol,maxiter}{Compatibility aliases. Do not supply an alias and its canonical argument together.}
}
\description{Creates a configuration for the experimental companion fixest branch.}
\details{Only OLS with two categorical intercept effects is supported. The tolerance
is a bound on the maximum weighted group moment divided by the square root of
its group weight and the weighted norm of the centered input column. It is not
equivalent to fixest's native iterate-change tolerance. Graph solves use a
stricter RHS-relative residual target and returned observations are certified.
Neither criterion is a coefficient-error guarantee on ill-conditioned graphs.

With \code{schur = TRUE}, eliminate levels of the fixed-effect block
with more levels (normally workers; the first block breaks ties) if they have
at most five distinct neighbors. Repeated worker--firm observations are combined
before counting degree. Higher-degree levels remain in the actual solved graph.
This is exact partial elimination, without sampling. CMG and its PCG settings
are unchanged. No observations are dropped.}
\value{A configuration of class \code{fixest_demeaner_cmg}.}
