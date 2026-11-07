#define R_NO_REMAP
#include <R.h>
#include <Rinternals.h>
#include <R_ext/Rdynload.h>
#include <R_ext/Utils.h>
#include <stdint.h>
#include <limits.h>
#include <time.h>

typedef struct { const double *input; double *output; } CmgrColumn;
typedef struct {
    size_t n, p, na, nb;
    const int *a, *b;
    const double *weights;
    const CmgrColumn *columns;
    double tol;
    size_t maxiter, threads, schur;
    int *iterations;
    double *moments, *relative_residuals, *stats, *reduction_stats;
} CmgrInput;
extern void *cmgr_start(const CmgrInput *, char *, size_t);
extern int cmgr_done(const void *);
extern void cmgr_cancel(const void *);
extern int cmgr_finish(void *, char *, size_t);

static void interrupt_check(void *unused) { (void) unused; R_CheckUserInterrupt(); }

static SEXP project(SEXP y, SEXP x, SEXP ids, SEXP sizes, SEXP weights,
                    SEXP tol, SEXP maxiter, SEXP threads, SEXP schur) {
    if (TYPEOF(ids) != VECSXP || XLENGTH(ids) != 2 || TYPEOF(sizes) != INTSXP || XLENGTH(sizes) != 2)
        Rf_error("Invalid CMG ID list or sizes.");
    SEXP a = VECTOR_ELT(ids, 0), b = VECTOR_ELT(ids, 1);
    if (TYPEOF(a) != INTSXP || TYPEOF(b) != INTSXP || XLENGTH(a) < 2 || XLENGTH(a) != XLENGTH(b))
        Rf_error("Invalid CMG ID vectors.");
    R_xlen_t n = XLENGTH(a);
    if (n > INT_MAX || INTEGER(sizes)[0] < 1 || INTEGER(sizes)[1] < 1 ||
        INTEGER(sizes)[0] > n || INTEGER(sizes)[1] > n)
        Rf_error("CMG currently requires at most INT_MAX rows and positive level counts.");
    if (TYPEOF(tol) != REALSXP || XLENGTH(tol) != 1 || !R_FINITE(REAL(tol)[0]) ||
        REAL(tol)[0] <= 0 || REAL(tol)[0] >= 1 || TYPEOF(maxiter) != INTSXP ||
        XLENGTH(maxiter) != 1 || INTEGER(maxiter)[0] < 1 || TYPEOF(threads) != INTSXP ||
        XLENGTH(threads) != 1 || INTEGER(threads)[0] < 1 || INTEGER(threads)[0] > 1024)
        Rf_error("Invalid CMG numerical controls.");
    if (TYPEOF(schur) != LGLSXP || XLENGTH(schur) != 1 || LOGICAL(schur)[0] == NA_LOGICAL)
        Rf_error("schur must be TRUE or FALSE.");
    if (weights != R_NilValue && (TYPEOF(weights) != REALSXP || XLENGTH(weights) != n))
        Rf_error("CMG weights must have one entry per observation.");
    int nx = 0, ny = 0, ylist = TYPEOF(y) == VECSXP;
    if (TYPEOF(x) != REALSXP) Rf_error("CMG X must use double storage.");
    if (Rf_isMatrix(x)) {
        SEXP dim = Rf_getAttrib(x, R_DimSymbol);
        if (INTEGER(dim)[0] != n) Rf_error("CMG X row count does not match IDs.");
        nx = INTEGER(dim)[1];
    } else if (XLENGTH(x) != 1 || REAL(x)[0] != 0) Rf_error("CMG absent X must be scalar zero.");
    if (ylist) {
        if (XLENGTH(y) > INT_MAX) Rf_error("Too many CMG outcome columns.");
        ny = (int) XLENGTH(y);
        for (int j = 0; j < ny; ++j)
            if (TYPEOF(VECTOR_ELT(y, j)) != REALSXP || XLENGTH(VECTOR_ELT(y, j)) != n)
                Rf_error("CMG outcome columns must match the ID length.");
    } else if (TYPEOF(y) != REALSXP) Rf_error("CMG y must use double storage.");
    else if (XLENGTH(y) == n) ny = 1;
    else if (XLENGTH(y) != 1 || REAL(y)[0] != 0) Rf_error("Invalid CMG outcome length.");
    if (nx > INT_MAX - ny || nx + ny == 0) Rf_error("Invalid CMG column count.");
    int p = nx + ny;
    SEXP xd = PROTECT(Rf_allocMatrix(REALSXP, nx ? (int)n : 1, nx ? nx : 1));
    if (!nx) REAL(xd)[0] = 0;
    SEXP yd = PROTECT(ylist ? Rf_allocVector(VECSXP, ny) : Rf_allocVector(REALSXP, ny ? n : 1));
    CmgrColumn *cols = (CmgrColumn *) R_alloc((size_t)p, sizeof(CmgrColumn));
    for (int j = 0; j < nx; ++j) {
        cols[j].input = REAL(x) + (R_xlen_t)j*n;
        cols[j].output = REAL(xd) + (R_xlen_t)j*n;
    }
    for (int j = 0; j < ny; ++j) {
        if (ylist) SET_VECTOR_ELT(yd, j, Rf_allocVector(REALSXP, n));
        cols[nx+j].input = REAL(ylist ? VECTOR_ELT(y, j) : y);
        cols[nx+j].output = REAL(ylist ? VECTOR_ELT(yd, j) : yd);
    }
    if (!ny && !ylist) REAL(yd)[0] = 0;
    SEXP it = PROTECT(Rf_allocVector(INTSXP, p));
    SEXP moments = PROTECT(Rf_allocVector(REALSXP, p));
    SEXP rr = PROTECT(Rf_allocVector(REALSXP, p));
    SEXP stats = PROTECT(Rf_allocVector(REALSXP, 6));
    SEXP reduction = PROTECT(Rf_allocVector(REALSXP, 5));
    CmgrInput args = {(size_t)n,(size_t)p,(size_t)INTEGER(sizes)[0],(size_t)INTEGER(sizes)[1],
        INTEGER(a),INTEGER(b),weights == R_NilValue ? NULL : REAL(weights),cols,
        REAL(tol)[0],(size_t)INTEGER(maxiter)[0],(size_t)INTEGER(threads)[0],(size_t)LOGICAL(schur)[0],
        INTEGER(it),REAL(moments),REAL(rr),REAL(stats),REAL(reduction)};
    char error[1024] = {0};
    R_CheckUserInterrupt();
    void *job = cmgr_start(&args, error, sizeof(error));
    if (!job) Rf_error("CMG: %s", error);
    int interrupted = 0;
    const struct timespec delay = {0, 1000000};
    while (!cmgr_done(job)) {
        if (!interrupted && !R_ToplevelExec(interrupt_check, NULL)) {
            interrupted = 1;
            cmgr_cancel(job);
        }
        nanosleep(&delay, NULL);
    }
    int status = cmgr_finish(job, error, sizeof(error));
    if (interrupted) Rf_error("CMG interrupted; native workers have finished.");
    if (status) Rf_error("CMG: %s", error);
    R_CheckUserInterrupt();
    SEXP ans = PROTECT(Rf_allocVector(VECSXP, 8));
    SEXP names = PROTECT(Rf_allocVector(STRSXP, 8));
    const char *labels[] = {"X_demean","y_demean","iterations","means","fe_moment","graph_residual","timing","reduction"};
    for (int j=0; j<8; ++j) SET_STRING_ELT(names,j,Rf_mkChar(labels[j]));
    SET_VECTOR_ELT(ans,0,xd); SET_VECTOR_ELT(ans,1,yd); SET_VECTOR_ELT(ans,2,it);
    SET_VECTOR_ELT(ans,3,Rf_ScalarReal(0)); SET_VECTOR_ELT(ans,4,moments);
    SET_VECTOR_ELT(ans,5,rr); SET_VECTOR_ELT(ans,6,stats); SET_VECTOR_ELT(ans,7,reduction);
    Rf_setAttrib(ans,R_NamesSymbol,names);
    UNPROTECT(9);
    return ans;
}
static const R_CallMethodDef calls[] = {{"C_cmgr_project", (DL_FUNC)&project, 9}, {NULL,NULL,0}};
void R_init_cmgr(DllInfo *dll) { R_registerRoutines(dll,NULL,calls,NULL,NULL); R_useDynamicSymbols(dll,FALSE); R_forceSymbols(dll,TRUE); }
