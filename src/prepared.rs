//! Reusable canonical topology for changing-weight Laplacians.

use crate::graph::compensated_sum;
use crate::{CmgError, Components, Edge, Laplacian};
use std::sync::Arc;

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
