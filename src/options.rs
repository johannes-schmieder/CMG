//! User-configurable validation and solver options.

use crate::CmgError;

/// Numerical tolerances used while validating sparse systems.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ValidationOptions {
    /// Relative/absolute tolerance used by dense symmetry validation.
    pub symmetry_tolerance: f64,
    /// Relative tolerance allowed in diagonal-dominance comparisons.
    pub diagonal_dominance_tolerance: f64,
    /// Relative tolerance used for component-wise Laplacian compatibility.
    pub compatibility_tolerance: f64,
    /// Relative threshold below which positive row-sum excess is treated as
    /// numerical zero when deciding whether SDDM augmentation is required.
    pub strict_dominance_tolerance: f64,
}

impl Default for ValidationOptions {
    fn default() -> Self {
        Self {
            symmetry_tolerance: 1.0e-12,
            diagonal_dominance_tolerance: 1.0e-13,
            compatibility_tolerance: 1.0e-12,
            strict_dominance_tolerance: 1.0e-13,
        }
    }
}

impl ValidationOptions {
    /// Validate all tolerance fields.
    pub fn validate(self) -> Result<Self, CmgError> {
        validate_nonnegative("symmetry_tolerance", self.symmetry_tolerance)?;
        validate_nonnegative(
            "diagonal_dominance_tolerance",
            self.diagonal_dominance_tolerance,
        )?;
        validate_nonnegative(
            "compatibility_tolerance",
            self.compatibility_tolerance,
        )?;
        validate_nonnegative(
            "strict_dominance_tolerance",
            self.strict_dominance_tolerance,
        )?;
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
