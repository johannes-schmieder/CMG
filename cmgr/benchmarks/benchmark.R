#!/usr/bin/env Rscript
# Five-method fixed-plan comparison; see README.md for invocation and scope.
args <- commandArgs(TRUE)
usage <- paste("Usage: Rscript benchmark.R --library DIR --output DIR",
               "[--source NAME=DIR ...] [--build-manifest FILE ...] [--smoke]")
opt <- list(source=character(), build_manifest=character(), smoke=FALSE)
i <- 1L
while(i <= length(args)) {
  key <- args[i]
  if(key == "--smoke") { opt$smoke <- TRUE; i <- i+1L; next }
  if(!key %in% c("--library","--output","--source","--build-manifest") || i == length(args))
    stop(usage, call.=FALSE)
  value <- args[i+1L]
  name <- gsub("-","_",substring(key,3L),fixed=TRUE)
  if(name %in% c("source","build_manifest")) opt[[name]] <- c(opt[[name]],value)
  else if(!is.null(opt[[name]])) stop("Duplicate argument: ",key)
  else opt[[name]] <- value
  i <- i+2L
}
if(is.null(opt$library) || is.null(opt$output)) stop(usage,call.=FALSE)
opt$library <- normalizePath(opt$library,mustWork=TRUE)
if(file.exists(opt$output) && !dir.exists(opt$output)) stop("Output is an existing file")
if(dir.exists(opt$output) && length(list.files(opt$output,all.files=TRUE,no..=TRUE)))
  stop("Refusing to reuse a nonempty output directory: ",opt$output)

# Set these before loading numerical libraries or creating the global Rayon pool.
Sys.setenv(OMP_NUM_THREADS="4", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
           BLIS_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1", RAYON_NUM_THREADS="4")
.libPaths(c(opt$library,.libPaths()))
library(fixest)
library(Matrix)
stopifnot(requireNamespace("cmgr",quietly=TRUE),requireNamespace("withinr",quietly=TRUE))
stopifnot(all(c("CmgDemeaner","LsmrDemeaner") %in% getNamespaceExports("fixest")))
options(warn=1)
setFixest_notes(FALSE)
setFixest_nthreads(4L)
dir.create(opt$output,recursive=TRUE,showWarnings=FALSE)
out <- normalizePath(opt$output,mustWork=TRUE)
script_arg <- grep("^--file=",commandArgs(),value=TRUE)
script <- normalizePath(sub("^--file=","",script_arg[1]),mustWork=TRUE)
script_dir <- dirname(script)
source(file.path(script_dir,"summarize.R"),local=TRUE)

sha256 <- function(path) {
  exe <- Sys.which("shasum")
  if(nzchar(exe)) return(sub(" .*","",system2(exe,c("-a","256",shQuote(path)),stdout=TRUE)[1]))
  exe <- Sys.which("sha256sum")
  if(nzchar(exe)) return(sub(" .*","",system2(exe,shQuote(path),stdout=TRUE)[1]))
  NA_character_
}
git_text <- function(path,args) {
  value <- suppressWarnings(system2("git",c("-C",shQuote(path),args),stdout=TRUE,stderr=TRUE))
  if(!is.null(attr(value,"status"))) stop("Cannot inspect source repository: ",path,"\n",paste(value,collapse="\n"))
  value
}
source_ids <- lapply(opt$source,function(spec) {
  equal <- regexpr("=",spec,fixed=TRUE)
  if(equal < 2L) stop("Sources must use NAME=DIR: ",spec)
  name <- substr(spec,1L,equal-1L)
  if(!grepl("^[A-Za-z0-9_-]+$",name)) stop("Unsafe source label: ",name)
  path <- normalizePath(substr(spec,equal+1L,nchar(spec)),mustWork=TRUE)
  patch <- git_text(path,c("diff","--binary","HEAD","--"))
  patch_file <- file.path(out,paste0("source-",name,".patch"))
  writeLines(patch,patch_file,useBytes=TRUE)
  list(name=name,path=path,commit=git_text(path,c("rev-parse","HEAD")),
       branch=git_text(path,c("branch","--show-current")),
       status=git_text(path,c("status","--short","--untracked-files=normal")),
       tracked_diff_sha256=sha256(patch_file))
})
if(anyDuplicated(vapply(source_ids,`[[`,character(1),"name"))) stop("Duplicate source labels")
build_manifests <- lapply(seq_along(opt$build_manifest),function(k) {
  path <- normalizePath(opt$build_manifest[k],mustWork=TRUE)
  dest <- file.path(out,paste0("build-manifest-",k,"-",basename(path)))
  stopifnot(file.copy(path,dest,overwrite=FALSE))
  list(original_path=path,retained_file=basename(dest),sha256=sha256(dest))
})
packages <- lapply(c("fixest","cmgr","withinr","Matrix"),function(pkg) {
  path <- find.package(pkg)
  binaries <- list.files(file.path(path,"libs"),pattern="\\.(so|dll|dylib)$",recursive=TRUE,full.names=TRUE)
  r_code <- c(file.path(path,c("DESCRIPTION","NAMESPACE")),
              list.files(file.path(path,"R"),full.names=TRUE))
  r_code <- r_code[file.exists(r_code) & !dir.exists(r_code)]
  list(package=pkg,version=as.character(packageVersion(pkg)),installed_path=path,
       description=packageDescription(pkg)[c("Package","Version","Built","RemoteSha","GithubSHA1")],
       binaries=setNames(vapply(binaries,sha256,character(1)),binaries),
       r_code_and_metadata=setNames(vapply(r_code,sha256,character(1)),r_code))
})
identity <- list(start_utc=format(Sys.time(),tz="UTC",usetz=TRUE),command=commandArgs(),
                 sys_info=Sys.info(),R=R.version.string,
                 threads=Sys.getenv(c("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS",
                                      "BLIS_NUM_THREADS","VECLIB_MAXIMUM_THREADS","RAYON_NUM_THREADS")),
                 fixest_threads=getFixest_nthreads(),sources=source_ids,packages=packages,
                 build_manifests=build_manifests,
                 scripts=setNames(vapply(c(script,file.path(script_dir,"summarize.R")),sha256,character(1)),
                                  c("benchmark.R","summarize.R")),
                 memory="Not measured. In-process RSS cannot fairly isolate method allocations/reusable runtime pools.")
if(Sys.info()[["sysname"]] == "Darwin")
  identity$machine <- setNames(lapply(c("machdep.cpu.brand_string","hw.memsize","hw.physicalcpu","hw.logicalcpu"),function(key) {
    value <- suppressWarnings(system2("sysctl",c("-n",key),stdout=TRUE,stderr=TRUE))
    list(value=unname(value),exit_status=if(is.null(attr(value,"status"))) 0L else attr(value,"status"))
  }),c("machdep.cpu.brand_string","hw.memsize","hw.physicalcpu","hw.logicalcpu"))
dput(identity,file.path(out,"identity.R"))
writeLines(capture.output(sessionInfo()),file.path(out,"session-info.txt"))
stopifnot(all(file.copy(c(script,file.path(script_dir,"summarize.R")),out,overwrite=FALSE)))

plan <- list(seed=20261107L,workers=if(opt$smoke) 120L else 20000L,
             years=6L,firms=if(opt$smoke) 30L else 1000L,controls=4L,threads=4L,
             repetitions=if(opt$smoke) 0L else 3L,
             probabilities=if(opt$smoke) .02 else c(.005,.01,.02,.05,.20),
             extra_case="5% annual moves, 99% neighboring-firm destinations",
             methods=c("native_default","native_tight","withinr_additive","cmg_off","cmg_schur"),
             native_default=list(tol=1e-6,iter=10000L),
             native_tight=list(tol=1e-10,iter=20000L),
             withinr=list(fixef_atol=1e-8,fixef_btol=1e-8,fixef_maxiter=10000L,preconditioner="additive"),
             cmg=list(fixef_tol=1e-8,fixef_maxiter=10000L),
             schur=list(max_degree=5L,selection="larger FE",cmg_settings="unchanged"),
             gates=c(coef=1e-7,residual=1e-5,projection=1e-5,normalized_fe_moment=1e-8),
             weights="unit", mode=if(opt$smoke) "untimed tiny smoke" else "timed full plan",
             timing="complete feols call; demeaned=TRUE; one warmup per method and case; cyclic timed method order; GC outside timer",
             acceptance="sample size unchanged, no reported nonconvergence, finite metrics, all four errors strictly below gates",
             oracle="independent sparse direct worker-Schur solve, one firm anchor per component",
             memory=identity$memory)
dput(plan,file.path(out,"plan.R"))

make_data <- function(p,local=FALSE,nw=plan$workers,nf=plan$firms) {
  set.seed(plan$seed)
  tt <- plan$years; n <- nw*tt
  home <- sample(rep(seq_len(nf),length.out=nw))
  u <- matrix(runif(nw*(tt-1L)),nw)
  jumps <- matrix(sample.int(nf-1L,nw*(tt-1L),TRUE),nw)
  nearby <- matrix(sample(c(-1L,1L),nw*(tt-1L),TRUE),nw)
  shortcut <- matrix(runif(nw*(tt-1L)) < .01,nw)
  firms <- matrix(0L,nw,tt); firms[,1] <- home
  for(t in 2:tt) {
    delta <- if(local) ifelse(shortcut[,t-1L],jumps[,t-1L],nearby[,t-1L]) else jumps[,t-1L]
    firms[,t] <- ifelse(u[,t-1L] < p,(firms[,t-1L]-1L+delta) %% nf+1L,firms[,t-1L])
  }
  worker <- rep(seq_len(nw),each=tt); firm <- as.integer(t(firms))
  X <- matrix(rnorm(n*plan$controls),n,plan$controls)
  colnames(X) <- paste0("x",seq_len(ncol(X)))
  y <- as.vector(X %*% (seq_len(ncol(X))/ncol(X)))+
       rnorm(nw)[worker]+rnorm(nf)[firm]+rnorm(n)
  list(data=data.frame(y,worker,firm,X),firms=firms,
       annual_mobility=mean(firms[,-1] != firms[,-tt]),
       ever_mover=mean(rowSums(firms[,-1] != firms[,-tt]) > 0))
}

direct_reference <- function(d) {
  n <- nrow(d); nw <- max(d$worker); nf <- max(d$firm)
  W <- sparseMatrix(i=seq_len(n),j=d$worker,x=1,dims=c(n,nw))
  F <- sparseMatrix(i=seq_len(n),j=d$firm,x=1,dims=c(n,nf))
  C <- crossprod(W,F)
  tw <- as.numeric(colSums(W)); tf <- as.numeric(colSums(F))
  S <- forceSymmetric(Diagonal(x=tf)-crossprod(C,Diagonal(x=1/tw) %*% C))
  parent <- seq_len(nf)
  root <- function(i) { while(parent[i] != i) i <- parent[i]; i }
  edge <- summary(S); edge <- edge[edge$i != edge$j & edge$x < 0,]
  for(k in seq_len(nrow(edge))) {
    a <- root(edge$i[k]); b <- root(edge$j[k]); if(a != b) parent[b] <- a
  }
  component <- vapply(seq_len(nf),root,integer(1))
  anchors <- match(unique(component),component); keep <- setdiff(seq_len(nf),anchors)
  V <- as.matrix(d[c("y",paste0("x",seq_len(plan$controls)))])
  worker_mean <- as.matrix(crossprod(W,V))/tw
  rhs <- as.matrix(crossprod(F,V)-crossprod(C,worker_mean))
  bf <- matrix(0,nf,ncol(V))
  if(length(keep)) bf[keep,] <- as.matrix(solve(
    Cholesky(S[keep,keep,drop=FALSE],LDL=FALSE,super=FALSE),rhs[keep,,drop=FALSE]))
  aw <- worker_mean-as.matrix(C %*% bf)/tw
  projected <- V-aw[d$worker,,drop=FALSE]-bf[d$firm,,drop=FALSE]
  X <- projected[,-1,drop=FALSE]; y <- projected[,1]
  beta <- as.vector(solve(crossprod(X),crossprod(X,y)))
  norm <- sqrt(colSums(sweep(V,2,colMeans(V))^2))
  moment <- function(Z) max(abs(sweep(as.matrix(crossprod(W,Z))/sqrt(tw),2,norm,"/")),
                            abs(sweep(as.matrix(crossprod(F,Z))/sqrt(tf),2,norm,"/")))
  stopifnot(moment(projected) < 1e-10)
  list(projected=projected,coef=beta,residual=as.vector(y-X%*%beta),moment=moment,
       components=length(anchors),
       largest_component_firms=max(tabulate(match(component,unique(component)))),
       vertices=nw+nf,distinct_worker_firm_edges=length(C@x))
}

# Independent oracle cross-check, outside every fit timer.
small <- make_data(.02,nw=120L,nf=30L)$data
smallref <- direct_reference(small)
Vsmall <- as.matrix(small[c("y",paste0("x",1:4))])
dense <- lm.fit(model.matrix(~factor(worker)+factor(firm),small),Vsmall)$residuals
stopifnot(max(abs(dense-smallref$projected)) < 1e-9)
writeLines(sprintf("DENSE_QR_ORACLE=PASS max_difference=%.17g",max(abs(dense-smallref$projected))),
           file.path(out,"oracle-check.txt"))

cases <- data.frame(case=c(paste0("random_",plan$probabilities),"local_0.05"),
                    p=c(plan$probabilities,.05),local=c(rep(FALSE,length(plan$probabilities)),TRUE))
methods <- plan$methods
times <- warmups <- topology <- list()
fml <- y ~ x1+x2+x3+x4 | worker+firm
for(j in seq_len(nrow(cases))) {
  case <- cases$case[j]
  generated <- make_data(cases$p[j],cases$local[j]); d <- generated$data
  ref <- direct_reference(d)
  topology[[j]] <- data.frame(case,n=nrow(d),workers=max(d$worker),firms=max(d$firm),
      annual_mobility=generated$annual_mobility,ever_mover=generated$ever_mover,
      components=ref$components,largest_component_firms=ref$largest_component_firms,
      vertices=ref$vertices,distinct_edges=ref$distinct_worker_firm_edges)
  write.csv(do.call(rbind,topology),file.path(out,"topology.csv"),row.names=FALSE)
  fit <- function(method) {
    if(method == "native_default") feols(fml,d,nthreads=4L,demeaned=TRUE,vcov="iid")
    else if(method == "native_tight") feols(fml,d,nthreads=4L,demeaned=TRUE,vcov="iid",
                                           fixef.tol=1e-10,fixef.iter=20000L)
    else if(method == "withinr_additive") feols(fml,d,nthreads=4L,demeaned=TRUE,vcov="iid",
        demeaner=LsmrDemeaner(fixef_atol=1e-8,fixef_btol=1e-8,fixef_maxiter=10000L,preconditioner="additive"))
    else feols(fml,d,nthreads=4L,demeaned=TRUE,vcov="iid",
        demeaner=CmgDemeaner(fixef_tol=1e-8,fixef_maxiter=10000L,schur=method == "cmg_schur"))
  }
  assess <- function(f) {
    Z <- cbind(f$y_demeaned,f$X_demeaned)
    err <- c(coef=max(abs(coef(f)-ref$coef)),residual=max(abs(resid(f)-ref$residual)),
             projection=max(abs(Z-ref$projected)),normalized_fe_moment=ref$moment(Z))
    converged <- if(is.null(f$convStatus)) NA else isTRUE(f$convStatus)
    sample_ok <- nobs(f) == nrow(d)
    reasons <- c(if(isFALSE(converged)) "nonconvergence",if(!sample_ok) "sample_size",
                 if(any(!is.finite(err))) "nonfinite",names(err)[!is.finite(err) | err >= plan$gates])
    list(error=err,pass=length(reasons)==0L,reasons=paste(unique(reasons),collapse=" | "),
         converged=converged,nobs=nobs(f),iterations=max(c(0,f$iterations),na.rm=TRUE))
  }
  for(r in 0:plan$repetitions) {
    order <- if(r == 0L) methods else methods[((seq_along(methods)+r-2L) %% length(methods))+1L]
    for(position in seq_along(order)) {
      method <- order[position]
      gc()
      warns <- character(); failure <- ""; assessment_error <- ""; f <- NULL
      run_fit <- function() tryCatch(withCallingHandlers(f <<- fit(method),
          warning=function(w) { warns <<- c(warns,conditionMessage(w)); invokeRestart("muffleWarning") }),
          error=function(e) { failure <<- conditionMessage(e) })
      if(opt$smoke) { run_fit(); tm <- NA_real_ } else tm <- unname(system.time(run_fit())["elapsed"])
      a <- if(is.null(f)) NULL else tryCatch(assess(f),error=function(e) {assessment_error <<- conditionMessage(e);NULL})
      if(is.null(a)) a <- list(error=setNames(rep(Inf,4),names(plan$gates)),pass=FALSE,
                               reasons=if(nzchar(failure)) "fit_error" else "assessment_error",
                               converged=NA,nobs=if(is.null(f)) NA_integer_ else nobs(f),iterations=NA_integer_)
      row <- data.frame(case,rep=r,position,method,elapsed=tm,valid=a$pass,converged=a$converged,nobs=a$nobs,
                        no_reported_nonconvergence=!is.null(f) && !isFALSE(a$converged),
                        max_coef=a$error[1],max_residual=a$error[2],max_projection=a$error[3],
                        max_fe_moment=a$error[4],iterations=a$iterations,failed_gates=a$reasons,
                        warning=paste(warns,collapse=" | "),error=failure,
                        assessment_error=assessment_error,row.names=NULL)
      if(r == 0L) warmups[[length(warmups)+1L]] <- row else times[[length(times)+1L]] <- row
      write.csv(do.call(rbind,warmups),file.path(out,if(opt$smoke) "smoke.csv" else "warmups.csv"),row.names=FALSE)
      if(length(times)) write.csv(do.call(rbind,times),file.path(out,"timings.csv"),row.names=FALSE)
      cat(sprintf("%s rep=%d %s %s valid=%s residual_error=%.3g %s\n",case,r,method,
                  if(opt$smoke) "untimed" else sprintf("%.3fs",tm),a$pass,a$error[2],a$reasons))
      flush.console()
    }
  }
}
if(opt$smoke) {
  raw <- do.call(rbind,warmups)
  stopifnot(nrow(raw) == nrow(cases)*length(methods),!anyDuplicated(raw[c("case","rep","method")]))
  cat("UNTIMED_SMOKE_COMPLETE\n")
} else {
  raw <- do.call(rbind,times)
  stopifnot(nrow(raw) == nrow(cases)*length(methods)*plan$repetitions,
            !anyDuplicated(raw[c("case","rep","method")]))
  summarize_benchmark(out)
  cat("FIVE_METHOD_BENCHMARK_COMPLETE\n")
}
writeLines(c(sprintf("finished_utc=%s",format(Sys.time(),tz="UTC",usetz=TRUE)),
             sprintf("mode=%s",plan$mode)),file.path(out,"COMPLETE.txt"))
