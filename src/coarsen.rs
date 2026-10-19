//! Deterministic restriction, prolongation, and Galerkin graph contraction.

use crate::{CmgError, Edge, Laplacian};
#[cfg(feature = "parallel")]
use crate::{ParallelExecutor, execution::PARALLEL_SETUP_MIN_ITEMS};
#[cfg(feature = "parallel")]
use rayon::prelude::*;
use std::sync::OnceLock;

crate::storage::storage_fields!(Aggregation, labels, native_labels, sizes);
impl crate::storage::Storage for LabelStorage {
    fn visit(
        &self,
        visitor: &mut dyn FnMut(crate::storage::Allocation) -> Result<(), CmgError>,
    ) -> Result<(), CmgError> {
        match self {
            Self::Compact(v) => crate::storage::Storage::visit(v, visitor),
            Self::Native(v) => crate::storage::Storage::visit(v, visitor),
        }
    }
}

/// A zero-based partition of fine vertices into coarse aggregates.
#[derive(Debug, Clone)]
enum LabelStorage {
    Compact(Vec<u32>),
    Native(Vec<usize>),
}

/// A zero-based partition of fine vertices into coarse aggregates.
#[derive(Debug)]
pub struct Aggregation {
    labels: LabelStorage,
    native_labels: OnceLock<Vec<usize>>,
    aggregate_count: usize,
    sizes: OnceLock<Vec<usize>>,
}

impl Clone for Aggregation {
    fn clone(&self) -> Self {
        Self {
            labels: self.labels.clone(),
            native_labels: OnceLock::new(),
            aggregate_count: self.aggregate_count,
            sizes: OnceLock::new(),
        }
    }
}

impl PartialEq for Aggregation {
    fn eq(&self, other: &Self) -> bool {
        self.aggregate_count == other.aggregate_count
            && self.fine_dimension() == other.fine_dimension()
            && (0..self.fine_dimension()).all(|index| self.label_at(index) == other.label_at(index))
    }
}

impl Eq for Aggregation {}

impl Aggregation {
    pub(crate) fn retained_bytes(&self) -> usize {
        let labels = match &self.labels {
            LabelStorage::Compact(labels) => labels
                .capacity()
                .saturating_mul(core::mem::size_of::<u32>()),
            LabelStorage::Native(labels) => labels
                .capacity()
                .saturating_mul(core::mem::size_of::<usize>()),
        };
        labels
            .saturating_add(self.native_labels.get().map_or(0, |labels| {
                labels
                    .capacity()
                    .saturating_mul(core::mem::size_of::<usize>())
            }))
            .saturating_add(self.sizes.get().map_or(0, |sizes| {
                sizes
                    .capacity()
                    .saturating_mul(core::mem::size_of::<usize>())
            }))
    }

    /// Construct an aggregation from explicit labels and a coarse dimension.
    pub fn new(labels: Vec<usize>, aggregate_count: usize) -> Result<Self, CmgError> {
        for &label in &labels {
            if label >= aggregate_count {
                return Err(CmgError::VertexOutOfBounds {
                    vertex: label,
                    vertex_count: aggregate_count,
                });
            }
        }
        Ok(Self::from_validated_parts(labels, aggregate_count))
    }

    pub(crate) fn from_forest_labels(labels: Vec<usize>, aggregate_count: usize) -> Self {
        debug_assert!(labels.iter().all(|&label| label < aggregate_count));
        Self::from_validated_parts(labels, aggregate_count)
    }

    pub(crate) fn from_forest_labels_admitted(
        labels: Vec<usize>,
        aggregate_count: usize,
    ) -> Result<Self, CmgError> {
        let labels = if aggregate_count <= (u32::MAX as usize).saturating_add(1) {
            let mut compact =
                crate::storage::try_capacity(labels.len(), "aggregation compact labels")?;
            compact.extend(labels.iter().map(|&label| label as u32));
            LabelStorage::Compact(compact)
        } else {
            LabelStorage::Native(labels)
        };
        Ok(Self {
            labels,
            native_labels: OnceLock::new(),
            aggregate_count,
            sizes: OnceLock::new(),
        })
    }

    #[cfg(feature = "experimental-components")]
    pub(crate) fn from_forest_labels_controlled(
        labels: Vec<usize>,
        aggregate_count: usize,
        meter: &mut crate::cancel::CancelMeter<'_>,
    ) -> Result<Self, CmgError> {
        let labels = if aggregate_count <= (u32::MAX as usize).saturating_add(1) {
            meter.allocation(
                "aggregation compact labels",
                crate::storage::bytes::<u32>(labels.len())?,
            )?;
            let mut compact =
                crate::storage::try_capacity(labels.len(), "aggregation compact labels")?;
            for &label in &labels {
                meter.work("aggregation compact label copy", 16)?;
                debug_assert!(label < aggregate_count);
                compact.push(label as u32);
            }
            LabelStorage::Compact(compact)
        } else {
            LabelStorage::Native(labels)
        };
        Ok(Self {
            labels,
            native_labels: OnceLock::new(),
            aggregate_count,
            sizes: OnceLock::new(),
        })
    }

    fn from_validated_parts(labels: Vec<usize>, aggregate_count: usize) -> Self {
        let compact_limit = (u32::MAX as usize).saturating_add(1);
        let labels = if aggregate_count <= compact_limit {
            LabelStorage::Compact(labels.into_iter().map(|label| label as u32).collect())
        } else {
            LabelStorage::Native(labels)
        };
        Self {
            labels,
            native_labels: OnceLock::new(),
            aggregate_count,
            sizes: OnceLock::new(),
        }
    }

    /// Return the fine-to-coarse labels.
    ///
    /// Hierarchy-built aggregations retain compact labels internally. The
    /// native-width compatibility slice is materialized lazily only when this
    /// public accessor is called.
    #[must_use]
    pub fn labels(&self) -> &[usize] {
        match &self.labels {
            LabelStorage::Native(labels) => labels,
            LabelStorage::Compact(labels) => self
                .native_labels
                .get_or_init(|| labels.iter().map(|&label| label as usize).collect()),
        }
    }

    /// Return aggregate sizes.
    ///
    /// Production hierarchy kernels need only the aggregate count. The full
    /// native-width size vector is materialized lazily for API compatibility.
    #[must_use]
    pub fn sizes(&self) -> &[usize] {
        self.sizes.get_or_init(|| {
            let mut sizes = vec![0; self.aggregate_count];
            match &self.labels {
                LabelStorage::Compact(labels) => {
                    for &label in labels {
                        sizes[label as usize] += 1;
                    }
                }
                LabelStorage::Native(labels) => {
                    for &label in labels {
                        sizes[label] += 1;
                    }
                }
            }
            sizes
        })
    }

    /// Return the fine dimension.
    #[must_use]
    pub fn fine_dimension(&self) -> usize {
        match &self.labels {
            LabelStorage::Compact(labels) => labels.len(),
            LabelStorage::Native(labels) => labels.len(),
        }
    }

    /// Return the coarse dimension.
    #[must_use]
    pub const fn coarse_dimension(&self) -> usize {
        self.aggregate_count
    }

    #[inline]
    pub(crate) fn label_at(&self, index: usize) -> usize {
        match &self.labels {
            LabelStorage::Compact(labels) => labels[index] as usize,
            LabelStorage::Native(labels) => labels[index],
        }
    }

    /// Restrict by summing fine values within every aggregate.
    pub fn restrict(&self, fine: &[f64]) -> Result<Vec<f64>, CmgError> {
        let mut coarse = vec![0.0; self.coarse_dimension()];
        self.restrict_into(fine, &mut coarse)?;
        Ok(coarse)
    }

    /// Restrict into caller-owned storage.
    pub fn restrict_into(&self, fine: &[f64], coarse: &mut [f64]) -> Result<(), CmgError> {
        if fine.len() != self.fine_dimension() {
            return Err(CmgError::dimension(
                "Aggregation::restrict fine",
                self.fine_dimension(),
                fine.len(),
            ));
        }
        if coarse.len() != self.coarse_dimension() {
            return Err(CmgError::dimension(
                "Aggregation::restrict coarse",
                self.coarse_dimension(),
                coarse.len(),
            ));
        }
        coarse.fill(0.0);
        match &self.labels {
            LabelStorage::Compact(labels) => {
                for (&value, &label) in fine.iter().zip(labels) {
                    coarse[label as usize] += value;
                }
            }
            LabelStorage::Native(labels) => {
                for (&value, &label) in fine.iter().zip(labels) {
                    coarse[label] += value;
                }
            }
        }
        Ok(())
    }

    #[cfg(feature = "experimental-components")]
    pub(crate) fn restrict_residual_into(
        &self,
        rhs: &[f64],
        matrix_value: &[f64],
        coarse: &mut [f64],
    ) -> Result<(), CmgError> {
        for (context, actual) in [
            ("Aggregation residual rhs", rhs.len()),
            ("Aggregation residual matvec", matrix_value.len()),
        ] {
            if actual != self.fine_dimension() {
                return Err(CmgError::dimension(context, self.fine_dimension(), actual));
            }
        }
        if coarse.len() != self.coarse_dimension() {
            return Err(CmgError::dimension(
                "Aggregation residual coarse",
                self.coarse_dimension(),
                coarse.len(),
            ));
        }
        coarse.fill(0.0);
        // Keep each subtraction and the ascending fine-vertex addition order.
        // The completed matvec is read-only; smoothing still has its own barrier.
        match &self.labels {
            LabelStorage::Compact(labels) => {
                for ((&b, &ax), &label) in rhs.iter().zip(matrix_value).zip(labels) {
                    coarse[label as usize] += b - ax;
                }
            }
            LabelStorage::Native(labels) => {
                for ((&b, &ax), &label) in rhs.iter().zip(matrix_value).zip(labels) {
                    coarse[label] += b - ax;
                }
            }
        }
        Ok(())
    }

    /// Prolong by copying each coarse value to its fine aggregate members.
    pub fn prolong(&self, coarse: &[f64]) -> Result<Vec<f64>, CmgError> {
        let mut fine = vec![0.0; self.fine_dimension()];
        self.prolong_into(coarse, &mut fine)?;
        Ok(fine)
    }

    /// Prolong into caller-owned storage.
    pub fn prolong_into(&self, coarse: &[f64], fine: &mut [f64]) -> Result<(), CmgError> {
        validate_prolong_dimensions(self, coarse, fine)?;
        match &self.labels {
            LabelStorage::Compact(labels) => {
                for (value, &label) in fine.iter_mut().zip(labels) {
                    *value = coarse[label as usize];
                }
            }
            LabelStorage::Native(labels) => {
                for (value, &label) in fine.iter_mut().zip(labels) {
                    *value = coarse[label];
                }
            }
        }
        Ok(())
    }

    /// Add a prolonged coarse vector to a fine vector in place.
    pub fn prolong_add_into(&self, coarse: &[f64], fine: &mut [f64]) -> Result<(), CmgError> {
        validate_prolong_dimensions(self, coarse, fine)?;
        match &self.labels {
            LabelStorage::Compact(labels) => {
                for (value, &label) in fine.iter_mut().zip(labels) {
                    *value += coarse[label as usize];
                }
            }
            LabelStorage::Native(labels) => {
                for (value, &label) in fine.iter_mut().zip(labels) {
                    *value += coarse[label];
                }
            }
        }
        Ok(())
    }

    #[cfg(feature = "parallel")]
    pub(crate) fn prolong_add_into_with_executor(
        &self,
        coarse: &[f64],
        fine: &mut [f64],
        executor: &ParallelExecutor,
    ) -> Result<(), CmgError> {
        validate_prolong_dimensions(self, coarse, fine)?;
        if !executor.should_parallel(fine.len()) {
            return self.prolong_add_into(coarse, fine);
        }
        executor.install(|| match &self.labels {
            LabelStorage::Compact(labels) => fine
                .par_iter_mut()
                .zip(labels.par_iter())
                .for_each(|(value, &label)| *value += coarse[label as usize]),
            LabelStorage::Native(labels) => fine
                .par_iter_mut()
                .zip(labels.par_iter())
                .for_each(|(value, &label)| *value += coarse[label]),
        });
        Ok(())
    }

    /// Form the exact graph Laplacian `R L R^T`.
    pub fn contract(&self, graph: &Laplacian) -> Result<Laplacian, CmgError> {
        self.contract_impl::<false>(graph)
    }

    pub(crate) fn contract_admitted(&self, graph: &Laplacian) -> Result<Laplacian, CmgError> {
        self.contract_impl::<true>(graph)
    }

    #[cfg(feature = "experimental-components")]
    pub(crate) fn contract_controlled(
        &self,
        graph: &Laplacian,
        meter: &mut crate::cancel::CancelMeter<'_>,
    ) -> Result<Laplacian, CmgError> {
        self.validate_contract_graph(graph)?;
        meter.allocation(
            "contracted edges",
            crate::storage::bytes::<Edge>(graph.edge_count())?,
        )?;
        let mut coarse_edges =
            crate::storage::try_capacity(graph.edge_count(), "contracted edges")?;
        for edge in graph.edges() {
            meter.work("contracted edge mapping", 64)?;
            let left = self.label_at(edge.u());
            let right = self.label_at(edge.v());
            if left != right {
                coarse_edges.push(Edge::from_internal_parts(left, right, edge.weight())?);
            }
        }
        Laplacian::from_compact_edges_controlled(self.coarse_dimension(), coarse_edges, meter)
    }

    fn contract_impl<const ADMITTED: bool>(
        &self,
        graph: &Laplacian,
    ) -> Result<Laplacian, CmgError> {
        self.validate_contract_graph(graph)?;
        let mut coarse_edges = if ADMITTED {
            crate::storage::try_capacity(graph.edge_count(), "contracted edges")?
        } else {
            Vec::with_capacity(graph.edge_count())
        };
        match &self.labels {
            LabelStorage::Compact(labels) => {
                for edge in graph.edges() {
                    let left = labels[edge.u()] as usize;
                    let right = labels[edge.v()] as usize;
                    if left != right {
                        coarse_edges.push(Edge::from_internal_parts(left, right, edge.weight())?);
                    }
                }
            }
            LabelStorage::Native(labels) => {
                for edge in graph.edges() {
                    let left = labels[edge.u()];
                    let right = labels[edge.v()];
                    if left != right {
                        coarse_edges.push(Edge::from_internal_parts(left, right, edge.weight())?);
                    }
                }
            }
        }
        if ADMITTED {
            Laplacian::from_compact_edges_admitted(self.coarse_dimension(), coarse_edges)
        } else {
            Laplacian::from_compact_edges(self.coarse_dimension(), coarse_edges)
        }
    }

    /// Form `R L R^T` using deterministic parallel edge mapping and sorting.
    ///
    /// The resulting graph is bit-for-bit identical to [`Self::contract`].
    /// Small edge sets follow the serial path selected by the executor.
    #[cfg(feature = "parallel")]
    pub fn contract_with_executor(
        &self,
        graph: &Laplacian,
        executor: &ParallelExecutor,
    ) -> Result<Laplacian, CmgError> {
        self.validate_contract_graph(graph)?;
        if graph.edge_count() < PARALLEL_SETUP_MIN_ITEMS
            || !executor.should_parallel(graph.edge_count())
        {
            return self.contract(graph);
        }
        let coarse_edges: Result<Vec<Edge>, CmgError> = executor.install(|| match &self.labels {
            LabelStorage::Compact(labels) => graph
                .edges()
                .par_iter()
                .filter_map(|edge| {
                    let left = labels[edge.u()] as usize;
                    let right = labels[edge.v()] as usize;
                    (left != right).then(|| Edge::from_internal_parts(left, right, edge.weight()))
                })
                .collect(),
            LabelStorage::Native(labels) => graph
                .edges()
                .par_iter()
                .filter_map(|edge| {
                    let left = labels[edge.u()];
                    let right = labels[edge.v()];
                    (left != right).then(|| Edge::from_internal_parts(left, right, edge.weight()))
                })
                .collect(),
        });
        Laplacian::from_compact_edges_with_executor(
            self.coarse_dimension(),
            coarse_edges?,
            executor,
        )
    }

    /// Fixed CMG-owned banks; the caller has already admitted the whole build.
    /// Both banks are provisioned before dispatch. Workers only write disjoint
    /// slots; no worker-side growing collection or CMG allocation is needed.
    #[cfg(feature = "parallel")]
    pub(crate) fn contract_with_executor_admitted(
        &self,
        graph: &Laplacian,
        executor: &ParallelExecutor,
    ) -> Result<Laplacian, CmgError> {
        self.validate_contract_graph(graph)?;
        if graph.edge_count() < PARALLEL_SETUP_MIN_ITEMS
            || !executor.should_parallel(graph.edge_count())
        {
            return self.contract_admitted(graph);
        }
        let mut mapped =
            crate::storage::try_filled(graph.edge_count(), None, "parallel contracted edge slots")?;
        let mut coarse_edges =
            crate::storage::try_capacity(graph.edge_count(), "parallel contracted edges")?;
        executor.install(|| {
            mapped
                .par_iter_mut()
                .zip(graph.edges().par_iter())
                .try_for_each(|(slot, edge)| {
                    let (left, right) = match &self.labels {
                        LabelStorage::Compact(labels) => {
                            (labels[edge.u()] as usize, labels[edge.v()] as usize)
                        }
                        LabelStorage::Native(labels) => (labels[edge.u()], labels[edge.v()]),
                    };
                    if left != right {
                        *slot = Some(Edge::from_internal_parts(left, right, edge.weight())?);
                    }
                    Ok::<_, CmgError>(())
                })
        })?;
        // Preserve input order before the unchanged endpoint/weight sorts.
        // At most m items are appended to capacity m; this cannot grow.
        for edge in mapped.into_iter().flatten() {
            coarse_edges.push(edge);
        }
        Laplacian::from_compact_edges_with_executor_admitted(
            self.coarse_dimension(),
            coarse_edges,
            executor,
        )
    }

    fn validate_contract_graph(&self, graph: &Laplacian) -> Result<(), CmgError> {
        if graph.vertex_count() != self.fine_dimension() {
            return Err(CmgError::dimension(
                "Aggregation::contract",
                self.fine_dimension(),
                graph.vertex_count(),
            ));
        }
        Ok(())
    }
}

fn validate_prolong_dimensions(
    aggregation: &Aggregation,
    coarse: &[f64],
    fine: &[f64],
) -> Result<(), CmgError> {
    if coarse.len() != aggregation.coarse_dimension() {
        return Err(CmgError::dimension(
            "Aggregation::prolong coarse",
            aggregation.coarse_dimension(),
            coarse.len(),
        ));
    }
    if fine.len() != aggregation.fine_dimension() {
        return Err(CmgError::dimension(
            "Aggregation::prolong fine",
            aggregation.fine_dimension(),
            fine.len(),
        ));
    }
    Ok(())
}

#[cfg(test)]
mod compact_aggregation_label_tests {
    use super::{Aggregation, LabelStorage};

    #[test]
    fn compact_storage_preserves_public_labels_and_algebra() {
        let aggregation = Aggregation::new(vec![0, 0, 1, 2, 2], 3).unwrap();
        assert!(matches!(&aggregation.labels, LabelStorage::Compact(_)));
        assert!(aggregation.native_labels.get().is_none());
        assert!(aggregation.sizes.get().is_none());

        let fine = [1.0, 2.0, 3.0, 4.0, 5.0];
        assert_eq!(aggregation.restrict(&fine).unwrap(), vec![3.0, 3.0, 9.0]);
        assert_eq!(
            aggregation.prolong(&[10.0, 20.0, 30.0]).unwrap(),
            vec![10.0, 10.0, 20.0, 30.0, 30.0]
        );
        assert_eq!(aggregation.labels(), &[0, 0, 1, 2, 2]);
        assert!(aggregation.native_labels.get().is_some());
        assert!(aggregation.sizes.get().is_none());
        assert_eq!(aggregation.sizes(), &[2, 1, 2]);
        assert!(aggregation.sizes.get().is_some());
    }

    #[test]
    fn clone_does_not_duplicate_materialized_native_cache() {
        let aggregation = Aggregation::new(vec![0, 1, 1, 2], 3).unwrap();
        let _ = aggregation.labels();
        let cloned = aggregation.clone();
        assert_eq!(aggregation, cloned);
        let _ = aggregation.sizes();
        assert!(cloned.native_labels.get().is_none());
        assert!(cloned.sizes.get().is_none());
        assert_eq!(cloned.labels(), &[0, 1, 1, 2]);
        assert_eq!(cloned.sizes(), &[1, 2, 1]);
    }
}

#[cfg(all(test, feature = "experimental-components"))]
mod residual_restriction_tests {
    use super::*;

    #[test]
    fn fused_restriction_keeps_cancellation_order_and_checks_dimensions() {
        let labels = vec![2, 0, 1, 2, 2, 1, 0, 2];
        let rhs = [1e100, 5.0, -0.0, 1.0, -1e100, 2.0, -5.0, 3.0];
        let ax = [0.0, 1.0, 0.0, 0.5, 0.0, 1e-100, -1.0, -0.0];
        let residual: Vec<_> = rhs.iter().zip(ax).map(|(b, a)| b - a).collect();
        for native in [false, true] {
            let mut aggregation = Aggregation::new(labels.clone(), 3).unwrap();
            if native {
                aggregation.labels = LabelStorage::Native(labels.clone());
            }
            let expected = aggregation.restrict(&residual).unwrap();
            let mut actual = vec![f64::NAN; 3];
            aggregation
                .restrict_residual_into(&rhs, &ax, &mut actual)
                .unwrap();
            assert_eq!(
                actual.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
                expected.iter().map(|x| x.to_bits()).collect::<Vec<_>>()
            );
            for (b, a) in [(&rhs[..7], &ax[..]), (&rhs[..], &ax[..7])] {
                assert!(
                    aggregation
                        .restrict_residual_into(b, a, &mut actual)
                        .is_err()
                );
                assert_eq!(actual, expected);
            }
            assert!(
                aggregation
                    .restrict_residual_into(&rhs, &ax, &mut [99.0; 2])
                    .is_err()
            );
        }
        let empty = Aggregation::new(Vec::new(), 0).unwrap();
        empty.restrict_residual_into(&[], &[], &mut []).unwrap();
    }
}

#[cfg(all(test, feature = "experimental-components"))]
mod controlled_aggregation_tests {
    use super::*;
    use crate::cancel::{CancelMeter, StopAt};

    #[test]
    fn controlled_labels_and_contraction_preserve_values_and_cancel() {
        let labels = vec![0, 0, 1, 1, 2, 2, 3, 3, 4];
        let graph = Laplacian::from_edges(
            9,
            [
                (0, 1, 1.0),
                (0, 2, 1e16),
                (1, 3, 1.0),
                (1, 2, 2.0),
                (3, 4, 0.5),
                (5, 7, 3.0),
                (6, 8, 1.0),
            ],
        )
        .unwrap();
        let original = graph.clone();
        let expected = Aggregation::from_forest_labels_admitted(labels.clone(), 5).unwrap();
        let never = || false;
        let actual = Aggregation::from_forest_labels_controlled(
            labels.clone(),
            5,
            &mut CancelMeter::every_operation(&never),
        )
        .unwrap();
        assert_eq!(actual, expected);
        let count = StopAt::never();
        let query = || count.query();
        let coarse = actual
            .contract_controlled(&graph, &mut CancelMeter::every_operation(&query))
            .unwrap();
        assert_eq!(coarse, expected.contract_admitted(&graph).unwrap());
        for stop in 0..count.polls() {
            let cancel = StopAt::at(Some(stop));
            let query = || cancel.query();
            assert!(matches!(
                actual.contract_controlled(&graph, &mut CancelMeter::every_operation(&query)),
                Err(CmgError::Cancelled { .. })
            ));
            assert_eq!(cancel.polls(), stop + 1);
        }
        let always = || true;
        assert!(matches!(
            Aggregation::from_forest_labels_controlled(
                labels,
                5,
                &mut CancelMeter::every_operation(&always)
            ),
            Err(CmgError::Cancelled { .. })
        ));
        assert_eq!(graph, original);
    }
}
