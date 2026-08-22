from pathlib import Path

path = Path("src/graph.rs")
text = path.read_text()

replacements = [
    (
        "    diagonal: Vec<f64>,\n    lineage: Arc<()>,\n",
        "    diagonal: Vec<f64>,\n    matrix_nnz: usize,\n    operator_norm_bound: f64,\n    lineage: Arc<()>,\n",
    ),
    (
        "            && self.diagonal == other.diagonal\n",
        "            && self.diagonal == other.diagonal\n            && self.matrix_nnz == other.matrix_nnz\n            && self.operator_norm_bound == other.operator_norm_bound\n",
    ),
    (
        """        Ok(Self {
            vertex_count,
            edges: canonical,
            diagonal,
            lineage: Arc::new(()),
        })
""",
        """        let diagonal_nnz = diagonal.iter().filter(|degree| **degree != 0.0).count();
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
""",
    ),
    (
        """    pub fn matrix_nnz(&self) -> usize {
        let diagonal_nnz = self
            .diagonal
            .iter()
            .filter(|degree| **degree != 0.0)
            .count();
        diagonal_nnz + 2 * self.edges.len()
    }
""",
        """    pub const fn matrix_nnz(&self) -> usize {
        self.matrix_nnz
    }
""",
    ),
    (
        """    pub fn operator_norm_bound(&self) -> f64 {
        2.0 * self.diagonal.iter().copied().fold(0.0, f64::max)
    }
""",
        """    pub const fn operator_norm_bound(&self) -> f64 {
        self.operator_norm_bound
    }
""",
    ),
]

for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"graph invariant anchor count {count}: {old[:80]!r}")
    text = text.replace(old, new, 1)

old_test = """        assert!(graph.shares_lineage(&clone));
        assert!(!graph.shares_lineage(&rebuilt));
        assert_eq!(graph, rebuilt);
    }
}
"""
new_test = """        assert!(graph.shares_lineage(&clone));
        assert!(!graph.shares_lineage(&rebuilt));
        assert_eq!(graph, rebuilt);
        assert_eq!(graph.matrix_nnz(), 7);
        assert_eq!(graph.operator_norm_bound(), 6.0);
    }

    #[test]
    fn cached_invariants_include_isolated_vertices_correctly() {
        let graph = Laplacian::from_edges(4, [(0, 1, 2.5)]).unwrap();
        assert_eq!(graph.matrix_nnz(), 4);
        assert_eq!(graph.operator_norm_bound(), 5.0);
    }
}
"""
if text.count(old_test) != 1:
    raise SystemExit("graph invariant test anchor changed")
path.write_text(text.replace(old_test, new_test, 1))
