/*
 * Standalone benchmark adaptation of the pinned stationary CMG recursive
 * preconditioner application.
 *
 * Upstream: ikoutis/cmg-solver
 * Commit: 19752fc102f8cae8e34f66457bfaccb1aaa60375
 * Original path: matlab/cmg/mex/preconditioner.c
 * License: GNU GPL version 3; see the repository LICENSE.
 *
 * MATLAB data structures and the direct-terminal callback are replaced by a
 * plain C ABI. The benchmark deliberately constructs iterative terminals, so
 * the recursive arithmetic path is the same as the pinned source.
 */

#include <stdint.h>

typedef struct cmg_reference_level {
    const uint32_t *ci;
    uint32_t nc;
    const double *a;
    const uint32_t *ia;
    const uint32_t *ja;
    uint32_t n;
    const double *inv_d;
    int32_t repeat;
    uint32_t is_last;
    uint32_t iterative;
    double *lws1;
    double *lws2;
    double *sws1;
    double *sws2;
} cmg_reference_level;

void cmg_reference_sspmv(uint32_t n,
                         const double *a,
                         const uint32_t *ia,
                         const uint32_t *ja,
                         const double *x,
                         double *y);

void cmg_reference_rmvecmul(const uint32_t *ci,
                            const double *x,
                            uint32_t n,
                            double *y,
                            uint32_t m);

void cmg_reference_trmvecmul(const uint32_t *ci,
                             const double *x,
                             uint32_t m,
                             double *y,
                             uint32_t n);

static int apply_level(cmg_reference_level *levels,
                       const double *b,
                       uint32_t level_index,
                       int32_t iter,
                       double *x) {
    cmg_reference_level *level = &levels[level_index];
    uint32_t n = level->n;
    uint32_t i;
    int32_t j;

    if (level->is_last != 0U) {
        if (level->iterative == 0U) {
            return -1;
        }
        for (i = 0; i < n; i++) {
            x[i] = level->inv_d[i] * b[i];
        }
        return 0;
    }

    for (i = 0; i < n; i++) {
        x[i] = 0.0;
        level->lws1[i] = level->inv_d[i] * b[i];
    }

    for (j = 1; j <= iter; j++) {
        if (j == 1) {
            for (i = 0; i < n; i++) {
                x[i] = level->lws1[i];
            }
        } else {
            cmg_reference_sspmv(
                n, level->a, level->ia, level->ja, x, level->lws2);
            for (i = 0; i < n; i++) {
                level->lws2[i] =
                    level->inv_d[i] * (b[i] - level->lws2[i]);
                x[i] = x[i] + level->lws2[i];
            }
        }

        cmg_reference_sspmv(
            n, level->a, level->ia, level->ja, x, level->lws2);
        for (i = 0; i < n; i++) {
            level->lws2[i] = b[i] - level->lws2[i];
        }
        cmg_reference_rmvecmul(
            level->ci, level->lws2, n, level->sws1, level->nc);
        if (apply_level(levels,
                        level->sws1,
                        level_index + 1U,
                        level->repeat,
                        level->sws2) != 0) {
            return -1;
        }
        cmg_reference_trmvecmul(
            level->ci, level->sws2, level->nc, level->lws2, n);
        for (i = 0; i < n; i++) {
            x[i] = level->lws2[i] + x[i];
        }

        cmg_reference_sspmv(
            n, level->a, level->ia, level->ja, x, level->lws2);
        for (i = 0; i < n; i++) {
            level->lws2[i] =
                level->inv_d[i] * (b[i] - level->lws2[i]);
            x[i] = x[i] + level->lws2[i];
        }
    }

    return 0;
}

int32_t cmg_reference_cycle(cmg_reference_level *levels,
                            const double *b,
                            int32_t iter,
                            double *x) {
    return apply_level(levels, b, 0U, iter, x);
}
