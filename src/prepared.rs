//! Reusable canonical topology for changing-weight Laplacians.

use crate::graph::compensated_sum;
use crate::{CmgError, Components, Edge, Laplacian};
use std::sync::Arc;

crate::storage::storage_fields!(
    PreparedLaplacianTopology,
    canonical_keys,
    group_offsets,
    group_input_indices,
    input_to_canonical,
    components,
    lineage
);
crate::storage::storage_fields!(PreparedLaplacianWorkspace, duplicate_weights);

/// Canonical, validated graph structure reusable across numeric weight frames.
#[derive(Debug, Clone)]
pub struct PreparedLaplacianTopology {
    vertex_count: usize,
    input_edge_count: usize,
    canonical_keys: Vec<u64>,
    group_offsets: Vec<usize>,
    group_input_indices: Vec<usize>,
    input_to_canonical: Vec<usize>,
    components: Arc<Components>,
    lineage: Arc<()>,
    maximum_duplicate_count: usize,
}

impl PreparedLaplacianTopology {
    /// Requested-layout upper bound for `prepare_with_budget`, including the
    /// indexed input, all maps, union/find scratch, and Arc blocks. Caller
    /// endpoint storage and allocator overhead are excluded. No executor is used.
    pub fn preparation_allocation_bound(
        vertices: usize,
        input_edges: usize,
    ) -> Result<usize, CmgError> {
        use crate::storage::{add, arc_bytes, bytes};
        let offsets = input_edges
            .checked_add(1)
            .ok_or(CmgError::AllocationFailed {
                context: "prepared offset size overflows",
            })?;
        let mut total = bytes::<(u64, usize)>(input_edges)?;
        total = add(total, bytes::<u64>(input_edges)?)?;
        total = add(total, bytes::<usize>(offsets)?)?;
        total = add(total, bytes::<[usize; 2]>(input_edges)?)?;
        total = add(total, bytes::<[usize; 4]>(vertices)?)?;
        add(add(total, arc_bytes::<Components>()?)?, arc_bytes::<()>()?)
    }

    /// Admit before reading/allocating topology data. The slice fixes the input
    /// length; arbitrary iterator size hints cannot bypass this bound.
    pub fn prepare_with_budget(
        vertices: usize,
        endpoints: &[(usize, usize)],
        budget_bytes: usize,
    ) -> Result<Self, CmgError> {
        crate::storage::admit(
            Self::preparation_allocation_bound(vertices, endpoints.len())?,
            budget_bytes,
        )?;
        Self::prepare(vertices, endpoints.iter().copied())
    }

    /// New numeric graph layouts; existing topology and assembly scratch are
    /// borrowed and must be charged separately, as must an older live frame.
    pub fn numeric_allocation_bytes(&self) -> Result<usize, CmgError> {
        use crate::storage::{add, arc_bytes, bytes};
        let payload = add(
            bytes::<Edge>(self.canonical_keys.len())?,
            bytes::<f64>(self.vertex_count)?,
        )?;
        add(
            add(
                add(payload, arc_bytes::<Vec<Edge>>()?)?,
                arc_bytes::<Vec<f64>>()?,
            )?,
            arc_bytes::<()>()?,
        )
    }

    /// Assemble with a pre-allocation incremental requested-layout budget.
    /// Includes the new graph's owned Arc blocks, but not shared topology blocks.
    pub fn assemble_with_workspace_budgeted(
        &self,
        weights: &[f64],
        workspace: &mut PreparedLaplacianWorkspace,
        budget_bytes: usize,
    ) -> Result<Laplacian, CmgError> {
        crate::storage::admit(self.numeric_allocation_bytes()?, budget_bytes)?;
        self.assemble_with_workspace(weights, workspace)
    }
    /// Validate and canonicalize undirected endpoints once.
    pub fn prepare<I>(vertex_count: usize, endpoints: I) -> Result<Self, CmgError>
    where
        I: IntoIterator<Item = (usize, usize)>,
    {
        let iterator = endpoints.into_iter();
        let mut indexed = Vec::new();
        indexed
            .try_reserve_exact(iterator.size_hint().0)
            .map_err(|_| CmgError::AllocationFailed {
                context: "prepared topology endpoints",
            })?;
        for (input_index, (left, right)) in iterator.enumerate() {
            validate_endpoint(vertex_count, left)?;
            validate_endpoint(vertex_count, right)?;
            if left == right {
                return Err(CmgError::SelfLoop { vertex: left });
            }
            let (u, v) = if left < right {
                (left as u32, right as u32)
            } else {
                (right as u32, left as u32)
            };
            if indexed.len() == indexed.capacity() {
                indexed
                    .try_reserve(1)
                    .map_err(|_| CmgError::AllocationFailed {
                        context: "prepared topology endpoints",
                    })?;
            }
            indexed.push((pack_key(u, v), input_index));
        }
        indexed.sort_unstable();

        let input_edge_count = indexed.len();
        let mut canonical_keys = Vec::new();
        let mut group_offsets = Vec::new();
        let mut group_input_indices = Vec::new();
        let mut input_to_canonical = Vec::new();
        canonical_keys
            .try_reserve_exact(input_edge_count)
            .map_err(|_| CmgError::AllocationFailed {
                context: "prepared canonical endpoints",
            })?;
        group_offsets
            .try_reserve_exact(input_edge_count.saturating_add(1))
            .map_err(|_| CmgError::AllocationFailed {
                context: "prepared duplicate offsets",
            })?;
        group_input_indices
            .try_reserve_exact(input_edge_count)
            .map_err(|_| CmgError::AllocationFailed {
                context: "prepared duplicate order",
            })?;
        input_to_canonical
            .try_reserve_exact(input_edge_count)
            .map_err(|_| CmgError::AllocationFailed {
                context: "prepared input edge map",
            })?;
        input_to_canonical.resize(input_edge_count, 0);
        group_offsets.push(0);

        let mut maximum_duplicate_count = 0;
        let mut cursor = 0;
        while cursor < indexed.len() {
            let key = indexed[cursor].0;
            let canonical_index = canonical_keys.len();
            canonical_keys.push(key);
            let start = cursor;
            while cursor < indexed.len() && indexed[cursor].0 == key {
                let input_index = indexed[cursor].1;
                group_input_indices.push(input_index);
                input_to_canonical[input_index] = canonical_index;
                cursor += 1;
            }
            maximum_duplicate_count = maximum_duplicate_count.max(cursor - start);
            group_offsets.push(cursor);
        }

        let components = Arc::new(Components::try_from_endpoints(
            vertex_count,
            canonical_keys.iter().map(|&key| (key_u(key), key_v(key))),
        )?);

        Ok(Self {
            vertex_count,
            input_edge_count,
            canonical_keys,
            group_offsets,
            group_input_indices,
            input_to_canonical,
            components,
            lineage: Arc::new(()),
            maximum_duplicate_count,
        })
    }

    /// Return the number of vertices, including isolates.
    #[must_use]
    pub const fn vertex_count(&self) -> usize {
        self.vertex_count
    }

    /// Return the number of original input edges.
    #[must_use]
    pub const fn input_edge_count(&self) -> usize {
        self.input_edge_count
    }

    /// Return the number of canonical undirected edges.
    #[must_use]
    pub fn canonical_edge_count(&self) -> usize {
        self.canonical_keys.len()
    }

    /// Return deterministic connected-component metadata.
    #[must_use]
    pub fn components(&self) -> &Components {
        &self.components
    }

    /// Map each original input edge to its canonical edge index.
    #[must_use]
    pub fn input_edge_to_canonical_edge(&self) -> &[usize] {
        &self.input_to_canonical
    }

    /// Return principal retained bytes for topology, maps, and components.
    #[must_use]
    pub fn retained_bytes(&self) -> usize {
        self.canonical_keys
            .capacity()
            .saturating_mul(core::mem::size_of::<u64>())
            .saturating_add(
                self.group_offsets
                    .capacity()
                    .saturating_mul(core::mem::size_of::<usize>()),
            )
            .saturating_add(
                self.group_input_indices
                    .capacity()
                    .saturating_mul(core::mem::size_of::<usize>()),
            )
            .saturating_add(
                self.input_to_canonical
                    .capacity()
                    .saturating_mul(core::mem::size_of::<usize>()),
            )
            .saturating_add(self.components.byte_len())
    }

    /// Allocate numeric-assembly scratch sized for this topology.
    pub fn workspace(&self) -> Result<PreparedLaplacianWorkspace, CmgError> {
        PreparedLaplacianWorkspace::new(self)
    }

    /// Assemble one changing-weight numeric frame.
    pub fn assemble(&self, weights: &[f64]) -> Result<Laplacian, CmgError> {
        let mut workspace = self.workspace()?;
        self.assemble_with_workspace(weights, &mut workspace)
    }

    /// Assemble one numeric frame while reusing duplicate-summation scratch.
    pub fn assemble_with_workspace(
        &self,
        weights: &[f64],
        workspace: &mut PreparedLaplacianWorkspace,
    ) -> Result<Laplacian, CmgError> {
        if weights.len() != self.input_edge_count {
            return Err(CmgError::dimension(
                "PreparedLaplacianTopology weights",
                self.input_edge_count,
                weights.len(),
            ));
        }
        for (&weight, &canonical_index) in weights.iter().zip(&self.input_to_canonical) {
            if !weight.is_finite() || weight <= 0.0 {
                let key = self.canonical_keys[canonical_index];
                return Err(CmgError::InvalidEdgeWeight {
                    u: key_u(key),
                    v: key_v(key),
                    weight,
                });
            }
        }
        workspace.validate(self.maximum_duplicate_count)?;

        let mut edges = Vec::new();
        edges
            .try_reserve_exact(self.canonical_keys.len())
            .map_err(|_| CmgError::AllocationFailed {
                context: "prepared numeric canonical edges",
            })?;
        let mut diagonal = Vec::new();
        diagonal
            .try_reserve_exact(self.vertex_count)
            .map_err(|_| CmgError::AllocationFailed {
                context: "prepared numeric diagonal",
            })?;
        diagonal.resize(self.vertex_count, 0.0_f64);

        for canonical_index in 0..self.canonical_keys.len() {
            let start = self.group_offsets[canonical_index];
            let end = self.group_offsets[canonical_index + 1];
            workspace.duplicate_weights.clear();
            workspace.duplicate_weights.extend(
                self.group_input_indices[start..end]
                    .iter()
                    .map(|&input_index| weights[input_index]),
            );
            workspace.duplicate_weights.sort_unstable_by(f64::total_cmp);
            let weight = compensated_sum(workspace.duplicate_weights.iter().copied());
            let key = self.canonical_keys[canonical_index];
            let u = key_u(key);
            let v = key_v(key);
            if !weight.is_finite() || weight <= 0.0 {
                return Err(CmgError::InvalidEdgeWeight { u, v, weight });
            }
            edges.push(Edge::from_internal_parts(u, v, weight)?);
            diagonal[u] += weight;
            diagonal[v] += weight;
        }
        Ok(Laplacian::from_prepared_parts(
            self.vertex_count,
            edges,
            diagonal,
            Arc::clone(&self.lineage),
            Arc::clone(&self.components),
        ))
    }

    pub(crate) fn matches_graph(&self, graph: &Laplacian) -> bool {
        graph.belongs_to_prepared_topology(&self.lineage, &self.components)
    }
}

/// Caller-owned scratch for repeated prepared numeric assembly.
#[derive(Debug, Clone)]
pub struct PreparedLaplacianWorkspace {
    duplicate_weights: Vec<f64>,
}

impl PreparedLaplacianWorkspace {
    /// Requested duplicate-weight scratch layout for the supplied topology.
    pub fn allocation_bytes(topology: &PreparedLaplacianTopology) -> Result<usize, CmgError> {
        crate::storage::bytes::<f64>(topology.maximum_duplicate_count)
    }

    /// Admit duplicate-weight scratch before its fallible allocation.
    pub fn new_with_budget(
        topology: &PreparedLaplacianTopology,
        budget_bytes: usize,
    ) -> Result<Self, CmgError> {
        crate::storage::admit(Self::allocation_bytes(topology)?, budget_bytes)?;
        Self::new(topology)
    }
    /// Allocate scratch compatible with a prepared topology.
    pub fn new(topology: &PreparedLaplacianTopology) -> Result<Self, CmgError> {
        let mut duplicate_weights = Vec::new();
        duplicate_weights
            .try_reserve_exact(topology.maximum_duplicate_count)
            .map_err(|_| CmgError::AllocationFailed {
                context: "prepared duplicate weights",
            })?;
        Ok(Self { duplicate_weights })
    }

    /// Return retained scratch bytes.
    #[must_use]
    pub fn byte_len(&self) -> usize {
        self.duplicate_weights
            .capacity()
            .saturating_mul(core::mem::size_of::<f64>())
    }

    fn validate(&self, required_capacity: usize) -> Result<(), CmgError> {
        if self.duplicate_weights.capacity() < required_capacity {
            return Err(CmgError::InvalidHierarchy {
                context: "prepared Laplacian workspace is too small",
            });
        }
        Ok(())
    }
}

fn validate_endpoint(vertex_count: usize, vertex: usize) -> Result<(), CmgError> {
    if vertex >= vertex_count {
        return Err(CmgError::VertexOutOfBounds {
            vertex,
            vertex_count,
        });
    }
    if vertex > u32::MAX as usize {
        return Err(CmgError::VertexIndexTooWide {
            vertex,
            maximum: u32::MAX as usize,
        });
    }
    Ok(())
}

const fn pack_key(u: u32, v: u32) -> u64 {
    ((u as u64) << 32) | v as u64
}

const fn key_u(key: u64) -> usize {
    (key >> 32) as usize
}

const fn key_v(key: u64) -> usize {
    key as u32 as usize
}
