#!/usr/bin/env python3
"""Remove the redundant dense graph matrix from terminal LDL construction."""

from pathlib import Path


path = Path("src/ldl.rs")
text = path.read_text()
old = '''        let dense = graph.to_dense();
        let active_vertices: Vec<usize> = (0..vertex_count)
            .filter(|vertex| !is_anchor[*vertex])
            .collect();
        let mut pattern_nonzeros = vec![0_usize; vertex_count];
        for &row in &active_vertices {
            pattern_nonzeros[row] = active_vertices
                .iter()
                .filter(|&&column| dense[row][column] != 0.0)
                .count();
        }

        let mut permutation = active_vertices;
        permutation.sort_by_key(|&vertex| (pattern_nonzeros[vertex], vertex));
        let dimension = permutation.len();

        let mut matrix = vec![vec![0.0; dimension]; dimension];
        for (row, &original_row) in permutation.iter().enumerate() {
            for (column, &original_column) in permutation.iter().enumerate() {
                matrix[row][column] = dense[original_row][original_column];
            }
        }
'''
new = '''        let active_vertices: Vec<usize> = (0..vertex_count)
            .filter(|vertex| !is_anchor[*vertex])
            .collect();
        let mut pattern_nonzeros = vec![0_usize; vertex_count];
        for &vertex in &active_vertices {
            // Every active grounded row retains its positive diagonal.
            pattern_nonzeros[vertex] = 1;
        }
        for edge in graph.edges() {
            if !is_anchor[edge.u()] && !is_anchor[edge.v()] {
                pattern_nonzeros[edge.u()] += 1;
                pattern_nonzeros[edge.v()] += 1;
            }
        }

        let mut permutation = active_vertices;
        permutation.sort_by_key(|&vertex| (pattern_nonzeros[vertex], vertex));
        let dimension = permutation.len();

        // Assemble the ordered grounded matrix directly. The previous path
        // first materialized the full graph matrix and then copied the active
        // permutation into this second dense buffer. Direct assembly removes
        // one vertex_count^2 allocation and its complete permutation scan.
        let mut matrix = vec![vec![0.0; dimension]; dimension];
        let mut factor_index = vec![usize::MAX; vertex_count];
        for (factor_vertex, &original_vertex) in permutation.iter().enumerate() {
            factor_index[original_vertex] = factor_vertex;
            matrix[factor_vertex][factor_vertex] = graph.diagonal()[original_vertex];
        }
        for edge in graph.edges() {
            let factor_u = factor_index[edge.u()];
            let factor_v = factor_index[edge.v()];
            if factor_u == usize::MAX || factor_v == usize::MAX {
                continue;
            }
            matrix[factor_u][factor_v] -= edge.weight();
            matrix[factor_v][factor_u] -= edge.weight();
        }
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"terminal construction anchor: expected one block, found {count}")
path.write_text(text.replace(old, new, 1))
