/*
 * Standalone benchmark adaptation of the pinned CMG restriction and
 * prolongation kernels.
 *
 * Upstream: ikoutis/cmg-solver
 * Commit: 19752fc102f8cae8e34f66457bfaccb1aaa60375
 * Original path: matlab/cmg/mex/rmvec.c
 * License: GNU GPL version 3; see the repository LICENSE.
 *
 * MATLAB-specific declarations are replaced by fixed-width standalone types;
 * the arithmetic loops are unchanged.
 */

#include <stdint.h>

typedef uint32_t mIndex;
typedef uint32_t mSize;
typedef double precision;

void cmg_reference_rmvecmul(const mIndex *ci,
                            const precision *x,
                            mSize n,
                            precision *y,
                            mSize m) {
    mIndex i;

    for (i = 0; i < m; i++) {
        y[i] = (precision)0.0;
    }

    for (i = 0; i < n; i++) {
        y[ci[i]] = y[ci[i]] + x[i];
    }
}

void cmg_reference_trmvecmul(const mIndex *ci,
                             const precision *x,
                             mSize m,
                             precision *y,
                             mSize n) {
    mIndex i;
    (void)m;

    for (i = 0; i < n; i++) {
        y[i] = x[ci[i]];
    }
}
