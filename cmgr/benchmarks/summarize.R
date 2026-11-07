# Source this file to expose summarize_benchmark(); also runs standalone.
summarize_benchmark <- function(out) {
  plan <- dget(file.path(out,"plan.R"))
  # Empty all-string CSV columns otherwise become logical NA, and nzchar(NA)
  # would incorrectly count an absent warning/error as present.
  text_columns <- c(case="character",method="character",failed_gates="character",
                    warning="character",error="character",assessment_error="character")
  raw <- read.csv(file.path(out,"timings.csv"),stringsAsFactors=FALSE,colClasses=text_columns)
  warm <- read.csv(file.path(out,"warmups.csv"),stringsAsFactors=FALSE,colClasses=text_columns)
  summary <- do.call(rbind,lapply(split(raw,list(raw$case,raw$method),drop=TRUE),function(x) {
    w <- warm[warm$case == x$case[1] & warm$method == x$method[1],,drop=FALSE]
    complete <- nrow(x) == plan$repetitions && setequal(x$rep,seq_len(plan$repetitions)) && nrow(w) == 1L
    data.frame(case=x$case[1],method=x$method[1],repetitions=nrow(x),
               median_seconds=median(x$elapsed),min_seconds=min(x$elapsed),max_seconds=max(x$elapsed),
               valid=complete && all(x$valid) && all(w$valid),timed_valid=all(x$valid),warmup_valid=all(w$valid),
               max_coef=max(c(x$max_coef,w$max_coef)),max_residual=max(c(x$max_residual,w$max_residual)),
               max_projection=max(c(x$max_projection,w$max_projection)),
               max_fe_moment=max(c(x$max_fe_moment,w$max_fe_moment)),
               max_iterations=if(all(is.na(c(x$iterations,w$iterations)))) NA else max(c(x$iterations,w$iterations),na.rm=TRUE),
               warning_calls=sum(nzchar(c(x$warning,w$warning))),error_calls=sum(nzchar(c(x$error,w$error))),
               failed_gates=paste(unique(c(x$failed_gates[nzchar(x$failed_gates)],w$failed_gates[nzchar(w$failed_gates)])),collapse=" | "),
               row.names=NULL)
  }))
  case_order <- unique(raw$case)
  summary <- summary[order(match(summary$case,case_order),match(summary$method,plan$methods)),]
  rownames(summary) <- NULL
  write.csv(summary,file.path(out,"summary.csv"),row.names=FALSE)
  ratios <- list()
  for(case in case_order) {
    x <- summary[summary$case == case,,drop=FALSE]
    for(numerator in plan$methods) for(denominator in plan$methods) {
      if(numerator == denominator) next
      a <- x[x$method == numerator,,drop=FALSE]; b <- x[x$method == denominator,,drop=FALSE]
      valid <- nrow(a)==1L && nrow(b)==1L && a$valid && b$valid && a$median_seconds > 0 && b$median_seconds > 0
      ratios[[length(ratios)+1L]] <- data.frame(case,numerator,denominator,qualified=valid,
        elapsed_ratio=if(valid) a$median_seconds/b$median_seconds else NA_real_,
        interpretation="numerator median seconds / denominator median seconds; >1 favors denominator")
    }
  }
  write.csv(do.call(rbind,ratios),file.path(out,"ratios.csv"),row.names=FALSE)
  lines <- c("# Five-method fixest comparison", "",paste0("Mode: ",plan$mode,"."),"",
             "Times are median elapsed seconds for complete feols calls, including backend setup; three timed repetitions after one warmup per method/case.","",
             "| Case | Native default | Native tight | Withinr additive | CMG without elimination | CMG with elimination |",
             "|---|---:|---:|---:|---:|---:|")
  for(case in case_order) {
    x <- summary[summary$case==case,,drop=FALSE]
    cells <- vapply(plan$methods,function(method) {
      row <- x[x$method==method,,drop=FALSE]
      if(nrow(row)!=1L) return("missing")
      paste0(sprintf("%.3f",row$median_seconds),if(row$valid) "" else " †")
    },character(1))
    lines <- c(lines,paste0("| ",paste(c(case,cells),collapse=" | ")," |"))
  }
  lines <- c(lines,"","† At least one timed run/warmup failed an accuracy/convergence/sample check, or the expected repetitions were incomplete. Timings remain visible but no speedup ratio is reported against configurations that did not meet the shared checks.","",
             "All methods use the same observations and regression; tolerances are solver-specific and are not mathematically interchangeable. Native tight: fixef.tol=1e-10, fixef.iter=20000. Withinr additive: atol=btol=1e-8, maxiter=10000. CMG without and with elimination both use tol=1e-8, maxiter=10000. With elimination, cmgr first removes workers with at most five distinct firms using exact algebra, solves the smaller system with CMG, and then recovers those workers' effects. The model and observations are the same in both CMG columns.","",
             "Accuracy gates against independent sparse direct worker-Schur solve: coefficient <1e-7; original residual <1e-5; demeaned-column projection <1e-5; normalized original-FE moment <1e-8. The direct oracle is checked against dense dummy-variable QR on a small disconnected fixture.","",
             "Thread budget: fixest nthreads=4, Rayon=4, BLAS thread environment=1. Each backend chooses its own use of that budget. Inputs: synthetic unit-weight panel; 20,000 workers × 6 years, 1,000 firms, four controls. These are not large administrative-data or memory benchmarks.","",
             plan$memory,"","See identity.R for source revisions/status, installed binary hashes, package versions and host/thread settings; topology.csv for the realized panel graph; timings.csv and warmups.csv for every fit, warning, error, convergence flag and failed gate; ratios.csv for accuracy-qualified same-run comparisons.")
  writeLines(lines,file.path(out,"comparison.md"))
  print(summary,row.names=FALSE)
  invisible(summary)
}

if(sys.nframe() == 0L) {
  args <- commandArgs(TRUE)
  if(length(args) != 1L) stop("Usage: Rscript summarize.R OUTPUT_DIRECTORY")
  summarize_benchmark(normalizePath(args[1],mustWork=TRUE))
}
