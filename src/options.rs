//! User-configurable validation and solver options.

use crate::CmgError;

/// Numerical tolerances used while validating sparse systems.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ValidationOptions {
    /// Relative/absolute tolerance used by dense symmetry validation.
    pub symmetry_tolerance: f64,
    /// Relative tolerance used for component-wise Laplacian compatibility.
    pub compatibility_tolerance: f64,
}

impl Default for ValidationOptions {
    fn default() -> Self {
        Self {
            symmetry_tolerance: 1.0e-12,
            compatibility_tolerance: 1.0e-12,
        }
    }
}

impl ValidationOptions {
    /// Validate all tolerance fields.
    pub fn validate(self) -> Result<Self, CmgError> {
        validate_nonnegative("symmetry_tolerance", self.symmetry_tolerance)?;
        validate_nonnegative("compatibility_tolerance", self.compatibility_tolerance)?;
        Ok(self)
    }
}

/// Options controlling deterministic hierarchy construction.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CmgOptions {
    /// Use a direct terminal when the current level has fewer vertices than
    /// this value. The upstream default is 700.
    pub direct_threshold: usize,
    /// Maximum number of stored hierarchy levels.
    pub max_levels: usize,
    /// Stop coarsening when cumulative matrix nonzeros exceed this multiple of
    /// the initial matrix nonzeros. The upstream default is 5.
    pub max_hierarchy_nnz_factor: f64,
    /// Threshold used by the low-effective-degree forest correction. The
    /// upstream default is `1/8`.
    pub low_effective_degree_threshold: f64,
}

impl Default for CmgOptions {
    fn default() -> Self {
        Self {
            direct_threshold: 700,
            max_levels: 128,
            max_hierarchy_nnz_factor: 5.0,
            low_effective_degree_threshold: 0.125,
        }
    }
}

impl CmgOptions {
    /// Validate hierarchy options.
    pub fn validate(self) -> Result<Self, CmgError> {
        if self.direct_threshold == 0 {
            return Err(CmgError::InvalidOption {
                name: "direct_threshold",
                value: 0.0,
            });
        }
        if self.max_levels == 0 {
            return Err(CmgError::InvalidOption {
                name: "max_levels",
                value: 0.0,
            });
        }
        validate_positive("max_hierarchy_nnz_factor", self.max_hierarchy_nnz_factor)?;
        if !self.low_effective_degree_threshold.is_finite()
            || !(0.0..=1.0).contains(&self.low_effective_degree_threshold)
        {
            return Err(CmgError::InvalidOption {
                name: "low_effective_degree_threshold",
                value: self.low_effective_degree_threshold,
            });
        }
        Ok(self)
    }
}

fn validate_nonnegative(name: &'static str, value: f64) -> Result<(), CmgError> {
    if value.is_finite() && value >= 0.0 {
        Ok(())
    } else {
        Err(CmgError::InvalidOption { name, value })
    }
}

fn validate_positive(name: &'static str, value: f64) -> Result<(), CmgError> {
    if value.is_finite() && value > 0.0 {
        Ok(())
    } else {
        Err(CmgError::InvalidOption { name, value })
    }
}
