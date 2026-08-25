# Official MATLAB hierarchy flag 3

The source is the unmodified official CMG tree pinned at commit
`19752fc102f8cae8e34f66457bfaccb1aaa60375`.

In `matlab/cmg/cmg_precondition.m`, lines 151--158 label flag `3` as hierarchy
stagnation. The branch is taken when the aggregate count removes fewer than two
vertices (`nc >= n-1`) or cumulative hierarchy nonzeros exceed five times the
finest matrix nonzeros. The upstream warning says that convergence may be slow
because of matrix density. The campaign therefore records flag `3` as an
algorithmic warning, retains the point, and assesses its solve accuracy rather
than reclassifying it as a wrapper failure.

The dense first-study points reached this branch. The SCC2 result format records
the exact flag at every requested CPU count.
