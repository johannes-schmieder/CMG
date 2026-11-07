library(cmgr)
fails <- function(expr) stopifnot(inherits(tryCatch(force(expr), error = identity), "error"))
canonical <- CmgDemeaner(fixef_tol=1e-9, fixef_maxiter=123L, schur=TRUE)
stopifnot(identical(CmgDemeaner(), CmgDemeaner(schur=TRUE)),
          identical(CmgDemeaner(schur=FALSE)$schur, FALSE),
          identical(canonical, CmgDemeaner(tol=1e-9, maxiter=123L)),
          identical(canonical, CmgDemeaner(1e-9, 123L, TRUE)),
          identical(names(canonical), c("fixef_tol", "fixef_maxiter", "schur")))
fails(CmgDemeaner(fixef_tol=1e-8, tol=1e-8))
fails(CmgDemeaner(fixef_maxiter=100L, maxiter=100L))
fails(CmgDemeaner(fixef_tol=NA_real_))
fails(CmgDemeaner(fixef_maxiter=2.5))
