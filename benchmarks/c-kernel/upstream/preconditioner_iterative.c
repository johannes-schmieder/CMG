/*
 * Standalone benchmark adaptation of the pinned CMG recursive preconditioner.
 *
 * Upstream: ikoutis/cmg-solver
 * Commit: 19752fc102f8cae8e34f66457bfaccb1aaa60375
 * Original path: matlab/cmg/mex/preconditioner.c
 * License: GNU GPL version 3; see the repository LICENSE.
 *
 * This benchmark-only adapter removes MATLAB headers and the direct LDL branch.
 * It exercises the same stationary cycle with an iterative terminal, using a
 * hierarchy and workspaces supplied by Rust.
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
                         precision *y);
void cmg_reference_rmvecmul(const mIndex *ci,
                            const precision *x,
                            mSize n,
                            precision *y,
                            mSize m);
void cmg_reference_trmvecmul(const mIndex *ci,
                             const precision *x,
                             mSize m,
                             precision *y,
                             mSize n);

typedef struct cmg_reference_level {
    const mIndex *cluster_index;
    mSize coarse_dimension;
    const precision *matrix_values;
    const mIndex *row_offsets;
    const mIndex *column_indices;
    mSize dimension;
    const precision *inverse_diagonal;
    int repeat;
    uint32_t is_last;
    precision *large_workspace_one;
    precision *large_workspace_two;
    precision *small_workspace_one;
    precision *small_workspace_two;
} cmg_reference_level;

static void vector_subtract(const precision *x,
                            const precision *y,
                            precision *z,
                            mSize n) {
    mIndex i;
    for (i = 0; i < n; i++) {
        z[i] = x[i] - y[i];
    }
}

static void vector_add(const precision *x,
                       const precision *y,
                       precision *z,
                       mSize n) {
    mIndex i;
    for (i = 0; i < n; i++) {
        z[i] = x[i] + y[i];
    }
}

static void vector_multiply(const precision *x,
                            const precision *y,
                            precision *z,
                            mSize n) {
    mIndex i;
    for (i = 0; i < n; i++) {
        z[i] = x[i] * y[i];
    }
}

static void apply_level(cmg_reference_level *levels,
                        const precision *b,
                        mIndex level_index,
                        int iterations,
                        precision *x) {
    cmg_reference_level *level = &levels[level_index];
    mSize n = level->dimension;
    mIndex i;
    int iteration;

    if (level->is_last) {
        vector_multiply(level->inverse_diagonal, b, x, n);
        return;
    }

    precision *y = level->large_workspace_two;
    precision *r = level->large_workspace_two;
    precision *scaled_rhs = level->large_workspace_one;
    precision *coarse_rhs = level->small_workspace_one;
    precision *coarse_correction = level->small_workspace_two;

    for (i = 0; i < n; i++) {
        x[i] = (precision)0.0;
    }
    vector_multiply(level->inverse_diagonal, b, scaled_rhs, n);

    for (iteration = 1; iteration <= iterations; iteration++) {
        if (iteration == 1) {
            for (i = 0; i < n; i++) {
                x[i] = scaled_rhs[i];
            }
        } else {
            cmg_reference_sspmv(n,
                                level->matrix_values,
                                level->row_offsets,
                                level->column_indices,
                                x,
                                y);
            vector_subtract(b, y, r, n);
            vector_multiply(level->inverse_diagonal, r, y, n);
            vector_add(x, y, x, n);
        }

        cmg_reference_sspmv(n,
                            level->matrix_values,
                            level->row_offsets,
                            level->column_indices,
                            x,
                            y);
        vector_subtract(b, y, r, n);
        cmg_reference_rmvecmul(level->cluster_index,
                               r,
                               n,
                               coarse_rhs,
                               level->coarse_dimension);
        apply_level(levels,
                    coarse_rhs,
                    level_index + 1,
                    level->repeat,
                    coarse_correction);
        cmg_reference_trmvecmul(level->cluster_index,
                                coarse_correction,
                                level->coarse_dimension,
                                y,
                                n);
        vector_add(y, x, x, n);

        cmg_reference_sspmv(n,
                            level->matrix_values,
                            level->row_offsets,
                            level->column_indices,
                            x,
                            y);
        vector_subtract(b, y, r, n);
        vector_multiply(level->inverse_diagonal, r, y, n);
        vector_add(x, y, x, n);
    }
}

void cmg_reference_apply_iterative(cmg_reference_level *levels,
                                   mSize level_count,
                                   const precision *b,
                                   precision *x) {
    if (level_count == 0) {
        return;
    }
    apply_level(levels, b, 0, 1, x);
}
