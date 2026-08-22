//! Deterministic connected components and Laplacian null-space operations.

use crate::{CmgError, Laplacian, ValidationOptions};

/// Connected-component metadata for a weighted graph.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Components {
    labels: Vec<usize>,
    sizes: Vec<usize>,
}

impl Components {
    /// Compute deterministic component labels.
    ///
    /// Labels are assigned in first-vertex order, so relabeling does not depend
    /// on edge input order.
    #[must_use]
    pub fn from_laplacian(graph: &Laplacian) -> Self {
        let vertex_count = graph.vertex_count();
        let mut parent: Vec<usize> = (0..vertex_count).collect();
        for edge in graph.edges() {
            union_min_root(&mut parent, edge.u(), edge.v());
        }
        for vertex in 0..vertex_count {
            let root = find_root(&mut parent, vertex);
            parent[vertex] = root;
        }

        let mut root_to_label = vec![usize::MAX; vertex_count];
        let mut labels = vec![0; vertex_count];
        let mut sizes = Vec::new();
        for vertex in 0..vertex_count {
            let root = parent[vertex];
            let label = if root_to_label[root] == usize::MAX {
                let next = sizes.len();
                root_to_label[root] = next;
                sizes.push(0);
                next
            } else {
                root_to_label[root]
            };
            labels[vertex] = label;
            sizes[label] += 1;
        }
        Self { labels, sizes }
    }

    /// Return the number of connected components.
    #[must_use]
    pub fn count(&self) -> usize {
        self.sizes.len()
    }

    /// Return the component label of every vertex.
    #[must_use]
    pub fn labels(&self) -> &[usize] {
        &self.labels
    }

    /// Return component sizes in label order.
    #[must_use]
    pub fn sizes(&self) -> &[usize] {
        &self.sizes
    }

    /// Return component-wise compensated sums of a vector.
    pub fn sums(&self, values: &[f64]) -> Result<Vec<f64>, CmgError> {
        self.compensated_sums(values, "Components::sums")
    }

    /// Verify that a right-hand side sums to numerical zero on every component.
    pub fn validate_rhs(&self, rhs: &[f64], options: ValidationOptions) -> Result<(), CmgError> {
        let options = options.validate()?;
        if rhs.len() != self.labels.len() {
            return Err(CmgError::dimension(
                "Components::validate_rhs",
                self.labels.len(),
                rhs.len(),
            ));
        }
        let sums = self.compensated_sums(rhs, "Components::validate_rhs")?;
        let mut scale_sums = vec![0.0; self.count()];
        let mut scale_corrections = vec![0.0; self.count()];
        for (value, label) in rhs.iter().zip(&self.labels) {
            neumaier_add(
                &mut scale_sums[*label],
                &mut scale_corrections[*label],
                value.abs(),
            );
        }
        for component in 0..self.count() {
            let scale = scale_sums[component] + scale_corrections[component];
            let tolerance = options.compatibility_tolerance * scale.max(1.0);
            if sums[component].abs() > tolerance {
                return Err(CmgError::IncompatibleLaplacianRhs {
                    component,
                    sum: sums[component],
                    tolerance,
                });
            }
        }
        Ok(())
    }

    /// Subtract the mean within every component in place.
    pub fn center_in_place(&self, values: &mut [f64]) -> Result<(), CmgError> {
        if values.len() != self.labels.len() {
            return Err(CmgError::dimension(
                "Components::center_in_place",
                self.labels.len(),
                values.len(),
            ));
        }
        let sums = self.sums(values)?;
        let means: Vec<f64> = sums
            .iter()
            .zip(&self.sizes)
            .map(|(sum, size)| *sum / *size as f64)
            .collect();
        for (value, label) in values.iter_mut().zip(&self.labels) {
            *value -= means[*label];
        }
        Ok(())
    }

    fn compensated_sums(
        &self,
        values: &[f64],
        context: &'static str,
    ) -> Result<Vec<f64>, CmgError> {
        if values.len() != self.labels.len() {
            return Err(CmgError::dimension(
                context,
                self.labels.len(),
                values.len(),
            ));
        }
        let mut sums = vec![0.0; self.count()];
        let mut corrections = vec![0.0; self.count()];
        for (vertex, (value, label)) in values.iter().zip(&self.labels).enumerate() {
            if !value.is_finite() {
                return Err(CmgError::NonFiniteMatrixValue {
                    row: vertex,
                    column: 0,
                    value: *value,
                });
            }
            neumaier_add(&mut sums[*label], &mut corrections[*label], *value);
        }
        for (sum, correction) in sums.iter_mut().zip(corrections) {
            *sum += correction;
        }
        Ok(sums)
    }
}

fn neumaier_add(sum: &mut f64, correction: &mut f64, value: f64) {
    let next = *sum + value;
    *correction += if sum.abs() >= value.abs() {
        (*sum - next) + value
    } else {
        (value - next) + *sum
    };
    *sum = next;
}

fn find_root(parent: &mut [usize], vertex: usize) -> usize {
    let mut root = vertex;
    while parent[root] != root {
        root = parent[root];
    }
    let mut current = vertex;
    while parent[current] != current {
        let next = parent[current];
        parent[current] = root;
        current = next;
    }
    root
}

fn union_min_root(parent: &mut [usize], left: usize, right: usize) {
    let left_root = find_root(parent, left);
    let right_root = find_root(parent, right);
    if left_root == right_root {
        return;
    }
    let (root, child) = if left_root < right_root {
        (left_root, right_root)
    } else {
        (right_root, left_root)
    };
    parent[child] = root;
}
