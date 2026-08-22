//! Deterministic Rust port of the Combinatorial Multigrid (CMG)
//! preconditioner.
//!
//! The implementation is developed against the pinned upstream source recorded
//! in `UPSTREAM.md`. Numerical modules are added in recoverable checkpoints; the
//! live status is maintained in `PLAN.md`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

mod coarsen;
mod components;
mod error;
mod forest;
mod graph;
mod hierarchy;
mod ldl;
mod options;
mod sddm;

pub use coarsen::Aggregation;
pub use components::Components;
pub use error::CmgError;
pub use forest::{
    ForestGrouping, build_forest_grouping, forest_components, maximum_weight_forest, split_forest,
};
pub use graph::{Edge, Laplacian};
pub use hierarchy::{CmgHierarchy, HierarchyBuildReport, HierarchyLevel, TerminalReason};
pub use ldl::GroundedLdl;
pub use options::{CmgOptions, ValidationOptions};
pub use sddm::{SddmAugmentation, SddmMatrix};

/// Return the pinned upstream CMG commit used as the behavioral reference.
#[must_use]
pub const fn upstream_commit() -> &'static str {
    "19752fc102f8cae8e34f66457bfaccb1aaa60375"
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn upstream_pin_is_stable() {
        assert_eq!(
            upstream_commit(),
            "19752fc102f8cae8e34f66457bfaccb1aaa60375"
        );
    }
}
