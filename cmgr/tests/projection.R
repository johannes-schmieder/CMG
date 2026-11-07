library(cmgr)
set.seed(97011)
n <- 2400L
a <- rep(1:120,each=20)
b <- sample(1:15,n,TRUE)+15L*(a>60)
w <- exp(rnorm(n,sd=.7))
v <- cbind(rnorm(n),rnorm(n),rep(0,n),rep(3,n))
D <- model.matrix(~factor(a)+factor(b))
ref <- lm.wfit(D,v,w=w)$residuals
for (threads in c(1L,4L)) {
    ans <- demean_fixest(0,v,list(a,b),c(120,30),weights=w,nthreads=threads)
    stopifnot(max(abs(ans$X_demean-ref))<1e-9,all(ans$fe_moment<=1e-8))
    again <- demean_fixest(0,v,list(a,b),c(120,30),weights=w,nthreads=threads)
    stopifnot(identical(ans$X_demean,again$X_demean))
}
fails <- function(expr) stopifnot(inherits(try(expr,silent=TRUE),"try-error"))
fails(demean_fixest(v[,1],0,list(a,b),c(120,30),weights=replace(w,1,0)))
fails(demean_fixest(v[,1],0,list(a,b),c(121,30)))
fails(demean_fixest(v[,1],0,list(replace(a,1,0L),b),c(120,30)))
fails(demean_fixest(replace(v[,1],1,Inf),0,list(a,b),c(120,30)))
fails(CmgDemeaner(maxiter=NA))
# A nonterminal graph ensures the one-iteration failure exercises real PCG.
a2 <- rep(1:800,each=6); b2 <- sample(1:200,length(a2),TRUE)
fails(demean_fixest(rnorm(length(a2)),0,list(a2,b2),c(800,200),tol=1e-12,maxiter=1L))
ans <- demean_fixest(v[,1],0,list(a,b),c(120,30),maxiter=1L)
stopifnot(all(is.finite(ans$y_demean)))
stopifnot(inherits(try(demean_fixest(0,matrix(1:4,4),
  list(c(1L,1L,2L,2L),c(1L,2L,1L,2L)),c(.Machine$integer.max,2L)),silent=TRUE),"try-error"))
cat("projection and validation tests passed\n")
