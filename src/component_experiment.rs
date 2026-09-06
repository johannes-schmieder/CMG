//! Explicit construction policies for independent attribution.

/// Independent experimental changes, for attribution against the ordinary builder.
///
/// These options change setup only. Every resulting preconditioner is fixed
/// across applications; RHS values and convergence never determine its structure.
#[derive(Debug, Clone, Copy, Default)]
pub struct ComponentBuildOptions {
    /// Retire isolated coarse vertices after contraction, retaining parent smoothing.
    pub prune_coarse_isolates: bool,
    /// Keep retired representatives in vertex-based stopping decisions.
    ///
    /// With pruning enabled, this separates compact storage from early direct
    /// factorization. Retired representatives carry no edges, vectors or work.
    /// An empty compact child always terminates directly. Ignored without pruning.
    pub preserve_unpruned_stopping: bool,
    /// Use ordered sparse factors for direct terminals, one component at a time.
    pub factor_terminal_components: bool,
}
