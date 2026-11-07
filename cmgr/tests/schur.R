library(cmgr)
fails <- function(expr) stopifnot(inherits(try(expr, silent=TRUE), "try-error"))
for (bad in list(NA, 1, "TRUE", logical(), c(TRUE,FALSE))) fails(CmgDemeaner(schur=bad))

# Includes repeated spells, degree 1/2/5/6 workers, two mover components and a
# third stayer-only component. Degree means distinct firms, not observations.
paths <- list(c(1,1,1,1,1,1), c(1,2,1,2,2,1), c(1,2,3,4,5,1),
              1:6, c(2,4,2,4,4,2))
a <- rep(1:65, each=6)
b <- unlist(lapply(1:65, function(i) {
    if (i > 60) return(rep(13L,6))
    as.integer(paths[[(i-1L) %% 5L + 1L]] + 6L*(i>30))
}))
set.seed(20261107)
v <- cbind(rnorm(length(a)), rnorm(length(a)), 0, 3,
           1e5 + rnorm(length(a)), 1e-8*rnorm(length(a)))
for (weights in list(rep(1,length(a)), exp(rnorm(length(a))), 10^runif(length(a),-4,4))) {
    D <- model.matrix(~factor(a)+factor(b))
    ref <- lm.wfit(D,v,w=weights)$residuals
    # Check each column on its own residual scale. The roundoff floor follows
    # the input scale and allows weighted-QR roundoff, so a small column cannot
    # pass merely by returning zeros.
    bound <- 2e-7 * apply(abs(ref), 2L, max) +
             1e-12 * apply(abs(v), 2L, max)
    for (threads in c(1L,4L)) for (swap in c(FALSE,TRUE)) {
        ids <- if (swap) list(b,a) else list(a,b)
        sizes <- if (swap) c(13,65) else c(65,13)
        ans <- demean_fixest(0,v,ids,sizes,weights=weights,nthreads=threads,schur=TRUE,tol=1e-10)
        again <- demean_fixest(0,v,ids,sizes,weights=weights,nthreads=threads,tol=1e-10)
        off <- demean_fixest(0,v,ids,sizes,weights=weights,nthreads=threads,schur=FALSE,tol=1e-10)
        stopifnot(all(apply(abs(ans$X_demean-ref), 2L, max) <= bound),
                  all(apply(abs(ans$X_demean-off$X_demean), 2L, max) <= bound),
                  all(ans$fe_moment <= 1e-10),
                  identical(ans$X_demean,again$X_demean),
                  ans$reduction['effect'] == if(swap) 2 else 1,
                  ans$reduction['eliminated_levels'] == 53,
                  ans$reduction['retained_levels'] == 12,
                  ans$timing['vertices'] == 25,
                  off$timing['vertices'] == 78,
                  ans$reduction['max_degree'] == 5)
    }
}

# All workers eliminated, no graph edges and no PCG iteration, including a
# component containing only one worker. Observations are retained and demeaned.
a <- rep(1:20,each=5); b <- rep(c(rep(1L,10),rep(2L,9),3L),each=5)
w <- exp(rnorm(length(a))); v <- cbind(rnorm(length(a)),rnorm(length(a)))
ref <- lm.wfit(model.matrix(~factor(a)+factor(b)),v,w=w)$residuals
ans <- demean_fixest(v[,1],v[,2,drop=FALSE],list(a,b),c(20,3),weights=w,schur=TRUE)
stopifnot(max(abs(cbind(ans$y_demean,ans$X_demean)-ref))<1e-12,
          ans$timing['vertices']==3,ans$timing['edges']==0,all(ans$iterations==0))

# No eligible workers, and then an equal-size tie (the first FE is selected).
a <- rep(1:12,each=6); b <- rep(1:6,12); v <- matrix(rnorm(length(a)),ncol=1)
ans <- demean_fixest(0,v,list(a,b),c(12,6),schur=TRUE)
ref <- lm.fit(model.matrix(~factor(a)+factor(b)),v)$residuals
stopifnot(ans$reduction['eliminated_levels']==0,ans$timing['vertices']==18,
          max(abs(ans$X_demean-ref))<1e-10)
a <- rep(1:8,each=3); b <- as.integer((a-1L+rep(0:2,8)) %% 8L+1L)
v <- matrix(rnorm(length(a)),ncol=1)
ans <- demean_fixest(0,v,list(a,b),c(8,8),schur=TRUE)
ref <- lm.fit(model.matrix(~factor(a)+factor(b)),v)$residuals
stopifnot(ans$reduction['effect']==1,ans$timing['vertices']==8,
          max(abs(ans$X_demean-ref))<1e-10)

# A weak, nonterminal firm graph must fail explicitly at a one-step budget.
a <- rep(1:4000,each=4)
b <- as.integer((a-1L) %% 1000L + 1L)
mover <- which(seq_along(a) %% 4L >= 2L)
b[mover] <- sample(1:1000,length(mover),TRUE)
fails(demean_fixest(rnorm(length(a)),0,list(a,b),c(4000,1000),schur=TRUE,tol=1e-12,maxiter=1L))

# Positive original weights can still imply an unrepresentable clique edge.
a <- rep(1:4,each=3); b <- rep(1:3,4); w <- rep(c(1,1e-300,1e-300),4)
fails(demean_fixest(rnorm(length(a)),0,list(a,b),c(4,3),weights=w,schur=TRUE))
cat("Schur projection, orientation, degree boundary and failure tests passed\n")
