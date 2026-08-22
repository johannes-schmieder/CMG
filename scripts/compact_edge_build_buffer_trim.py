from pathlib import Path

PATH = Path("src/graph.rs")
text = PATH.read_text()
original = text

old_builder = '''    fn from_sorted_raw_edges(
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
            canonical.push(Edge {
                u: u as u32,
                v: v as u32,
                weight,
            });
        }

        let mut diagonal = vec![0.0; vertex_count];
        for edge in &canonical {
            diagonal[edge.u()] += edge.weight;
            diagonal[edge.v()] += edge.weight;
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
new_builder = '''    fn from_sorted_raw_edges(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        // Equal endpoint pairs are contiguous after sorting. Merge them into
        // the front of the compact input buffer so graph construction does not
        // allocate a separate full-capacity canonical vector.
        let mut read_index = 0;
        let mut write_index = 0;
        while read_index < raw.len() {
            let u = raw[read_index].u;
            let v = raw[read_index].v;
            let group_start = read_index;
            while read_index < raw.len()
                && raw[read_index].u == u
                && raw[read_index].v == v
            {
                read_index += 1;
            }
            let weight = compensated_sum(
                raw[group_start..read_index]
                    .iter()
                    .map(|edge| edge.weight),
            );
            if !weight.is_finite() || weight <= 0.0 {
                return Err(CmgError::InvalidEdgeWeight {
                    u: u as usize,
                    v: v as usize,
                    weight,
                });
            }
            raw[write_index] = Edge { u, v, weight };
            write_index += 1;
        }
        raw.truncate(write_index);
        // Filtered coarse-edge iterators have a zero lower size hint, so their
        // vectors may grow beyond the final length. Do not retain that spare
        // capacity in every hierarchy level.
        if raw.capacity() != raw.len() {
            raw.shrink_to_fit();
        }

        let mut diagonal = vec![0.0; vertex_count];
        for edge in &raw {
            diagonal[edge.u()] += edge.weight;
            diagonal[edge.v()] += edge.weight;
        }

        let diagonal_nnz = diagonal.iter().filter(|degree| **degree != 0.0).count();
        let matrix_nnz = diagonal_nnz + 2 * raw.len();
        let operator_norm_bound = 2.0 * diagonal.iter().copied().fold(0.0, f64::max);

        Ok(Self {
            vertex_count,
            edges: raw,
            diagonal,
            matrix_nnz,
            operator_norm_bound,
            lineage: Arc::new(()),
        })
    }
'''
if text.count(old_builder) != 1:
    raise SystemExit("expected one sorted raw-edge builder block")
text = text.replace(old_builder, new_builder, 1)

old_signature = ''') -> Result<Vec<(usize, usize, f64)>, CmgError>
where
    I: IntoIterator<Item = (usize, usize, f64)>,
'''
new_signature = ''') -> Result<Vec<Edge>, CmgError>
where
    I: IntoIterator<Item = (usize, usize, f64)>,
'''
if text.count(old_signature) != 1:
    raise SystemExit("expected one validated edge collector signature")
text = text.replace(old_signature, new_signature, 1)

old_push = '''        raw.push((u, v, weight));
'''
new_push = '''        raw.push(Edge {
            u: u as u32,
            v: v as u32,
            weight,
        });
'''
if text.count(old_push) != 1:
    raise SystemExit("expected one raw edge push")
text = text.replace(old_push, new_push, 1)

old_compare = '''fn compare_raw_edges(
    left: &(usize, usize, f64),
    right: &(usize, usize, f64),
) -> core::cmp::Ordering {
    left.0
        .cmp(&right.0)
        .then(left.1.cmp(&right.1))
        .then_with(|| left.2.total_cmp(&right.2))
}
'''
new_compare = '''fn compare_raw_edges(left: &Edge, right: &Edge) -> core::cmp::Ordering {
    left.u
        .cmp(&right.u)
        .then(left.v.cmp(&right.v))
        .then_with(|| left.weight.total_cmp(&right.weight))
}
'''
if text.count(old_compare) != 1:
    raise SystemExit("expected one raw edge comparator")
text = text.replace(old_compare, new_compare, 1)

if text == original:
    raise SystemExit("trimmed compact graph buffer patch made no changes")
if "raw.shrink_to_fit();" not in text:
    raise SystemExit("capacity trim is missing")
if "Result<Vec<Edge>, CmgError>" not in text:
    raise SystemExit("compact validation buffer is missing")
if "let mut canonical" in text:
    raise SystemExit("separate canonical vector remains")

PATH.write_text(text)
