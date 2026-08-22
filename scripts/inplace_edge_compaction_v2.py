#!/usr/bin/env python3
"""Patch canonical edge merging to compact the sorted raw buffer in place."""

from pathlib import Path


path = Path("src/graph.rs")
text = path.read_text()
old = '''    fn from_sorted_raw_edges(
        vertex_count: usize,
        raw: Vec<(usize, usize, f64)>,
    ) -> Result<Self, CmgError> {
        let mut canonical = Vec::with_capacity(raw.len());
        let mut cursor = 0;
        while cursor < raw.len() {
            let u = raw[cursor].0;
            let v = raw[cursor].1;
            let start = cursor;
            while cursor < raw.len() && raw[cursor].0 == u && raw[cursor].1 == v {
                cursor += 1;
            }
            let weight = compensated_sum(raw[start..cursor].iter().map(|edge| edge.2));
            if !weight.is_finite() || weight <= 0.0 {
                return Err(CmgError::InvalidEdgeWeight { u, v, weight });
            }
            canonical.push(Edge { u, v, weight });
        }

        let mut diagonal = vec![0.0; vertex_count];
        for edge in &canonical {
            diagonal[edge.u] += edge.weight;
            diagonal[edge.v] += edge.weight;
        }

        let diagonal_nnz = diagonal.iter().filter(|degree| **degree != 0.0).count();
        let matrix_nnz = diagonal_nnz + 2 * canonical.len();
        let operator_norm_bound = 2.0 * diagonal.iter().copied().fold(0.0, f64::max);

        Ok(Self {
            vertex_count,
            edges: canonical,
            diagonal,
            matrix_nnz,
            operator_norm_bound,
            lineage: Arc::new(()),
        })
    }
'''
new = '''    fn from_sorted_raw_edges(
        vertex_count: usize,
        mut raw: Vec<(usize, usize, f64)>,
    ) -> Result<Self, CmgError> {
        // Merge equal endpoints into the front of the already sorted input.
        // This delays allocating the final `Edge` vector until after duplicate
        // contraction and avoids retaining a full-capacity second edge buffer
        // throughout the merge.
        let mut read_index = 0;
        let mut write_index = 0;
        while read_index < raw.len() {
            let u = raw[read_index].0;
            let v = raw[read_index].1;
            let group_start = read_index;
            while read_index < raw.len()
                && raw[read_index].0 == u
                && raw[read_index].1 == v
            {
                read_index += 1;
            }
            let weight = compensated_sum(
                raw[group_start..read_index]
                    .iter()
                    .map(|edge| edge.2),
            );
            if !weight.is_finite() || weight <= 0.0 {
                return Err(CmgError::InvalidEdgeWeight { u, v, weight });
            }
            raw[write_index] = (u, v, weight);
            write_index += 1;
        }
        raw.truncate(write_index);

        let mut diagonal = vec![0.0; vertex_count];
        for &(u, v, weight) in &raw {
            diagonal[u] += weight;
            diagonal[v] += weight;
        }

        let diagonal_nnz = diagonal.iter().filter(|degree| **degree != 0.0).count();
        let matrix_nnz = diagonal_nnz + 2 * raw.len();
        let operator_norm_bound = 2.0 * diagonal.iter().copied().fold(0.0, f64::max);
        let canonical = raw
            .into_iter()
            .map(|(u, v, weight)| Edge { u, v, weight })
            .collect();

        Ok(Self {
            vertex_count,
            edges: canonical,
            diagonal,
            matrix_nnz,
            operator_norm_bound,
            lineage: Arc::new(()),
        })
    }
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"sorted-edge helper anchor: expected one block, found {count}")
path.write_text(text.replace(old, new, 1))
