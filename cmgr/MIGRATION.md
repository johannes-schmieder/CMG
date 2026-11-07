# Source migration

The R package was imported into the CMG repository from local cmgr commit
`12b15942b69ef3d840375ed62a7233180dc4da91` on 2026-11-07.
The prior standalone Git history and historical experiment records are retained
outside this repository under `~/research/fereg/fixest-cmg-current`.

The root Rust solver is unchanged. Its source is no longer maintained as a
second tracked copy inside the R package. Build-time snapshots are recorded
in `inst/CMG-SOURCE.json` and bundled only for standalone R installation.
Version 0.0.2 adds canonical `fixef_tol`/`fixef_maxiter` configuration names;
`tol`/`maxiter` remain supported aliases. The exact Schur option remains opt-in.
