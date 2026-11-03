/*
 * Standalone benchmark adaptation of the pinned CMG sspmv kernel.
 *
 * Upstream: ikoutis/cmg-solver
 * Commit: 19752fc102f8cae8e34f66457bfaccb1aaa60375
 * Original path: matlab/cmg/mex/sspmv.c
 * Copyright (c) 2008-2020 Ioannis Koutis and Gary Miller
 * SPDX-License-Identifier: MIT
 * MIT permission confirmed 2026-11-03; see docs/UPSTREAM.md and LICENSE.
 *
 * The arithmetic loop is unchanged. MATLAB-specific headers and type aliases
 * are replaced by fixed-width standalone declarations.
 */

#include <stdint.h>

typedef uint32_t mIndex;
typedef uint32_t mSize;
typedef double precision;

void cmg_reference_sspmv(mSize n,
                         const precision *a,
                         const mIndex *ia,
                         const mIndex *ja,
                         const precision *x,
                         precision *y) {
    mIndex i, j, k;
    precision sum;

    for (i = 0; i < n; i++) {
        y[i] = 0;
    }

    for (i = 0; i < n; i++) {
        sum = a[ia[i]] * x[i];
        for (j = ia[i] + 1; j < ia[i + 1]; j++) {
            k = ja[j];
            sum += a[j] * x[k];
            y[k] += a[j] * x[i];
        }
        y[i] += sum;
    }
}
