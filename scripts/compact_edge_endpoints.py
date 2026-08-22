from pathlib import Path

GRAPH_PATH = Path("src/graph.rs")
ERROR_PATH = Path("src/error.rs")
graph = GRAPH_PATH.read_text()
error = ERROR_PATH.read_text()
original_graph = graph
original_error = error


def replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one {description}, found {count}")
    return text.replace(old, new, 1)


graph = replace_once(
    graph,
    """pub struct Edge {
    u: usize,
    v: usize,
    weight: f64,
}
""",
    """pub struct Edge {
    u: u32,
    v: u32,
    weight: f64,
}
""",
    "Edge endpoint field block",
)
graph = replace_once(
    graph,
    """    pub const fn u(self) -> usize {
        self.u
    }
""",
    """    pub const fn u(self) -> usize {
        self.u as usize
    }
""",
    "Edge::u accessor",
)
graph = replace_once(
    graph,
    """    pub const fn v(self) -> usize {
        self.v
    }
""",
    """    pub const fn v(self) -> usize {
        self.v as usize
    }
""",
    "Edge::v accessor",
)
graph = replace_once(
    graph,
    """            canonical.push(Edge { u, v, weight });
""",
    """            canonical.push(Edge {
                u: u as u32,
                v: v as u32,
                weight,
            });
""",
    "canonical Edge construction",
)

# All retained-edge reads in graph.rs use checked public-style accessors. These
# exact replacements deliberately avoid broad source rewriting.
for old, new in (
    ("diagonal[edge.u]", "diagonal[edge.u()]"),
    ("diagonal[edge.v]", "diagonal[edge.v()]"),
    ("input[edge.u]", "input[edge.u()]"),
    ("input[edge.v]", "input[edge.v()]"),
    ("output[edge.u]", "output[edge.u()]"),
    ("output[edge.v]", "output[edge.v()]"),
    ("dense[edge.u]", "dense[edge.u()]"),
    ("dense[edge.v]", "dense[edge.v()]"),
):
    graph = graph.replace(old, new)

width_guard_anchor = """        if right >= vertex_count {
            return Err(CmgError::VertexOutOfBounds {
                vertex: right,
                vertex_count,
            });
        }
        if left == right {
"""
width_guard_replacement = """        if right >= vertex_count {
            return Err(CmgError::VertexOutOfBounds {
                vertex: right,
                vertex_count,
            });
        }
        if left > u32::MAX as usize {
            return Err(CmgError::VertexIndexTooWide {
                vertex: left,
                maximum: u32::MAX as usize,
            });
        }
        if right > u32::MAX as usize {
            return Err(CmgError::VertexIndexTooWide {
                vertex: right,
                maximum: u32::MAX as usize,
            });
        }
        if left == right {
"""
graph = replace_once(
    graph,
    width_guard_anchor,
    width_guard_replacement,
    "compact endpoint width guard anchor",
)

error_variant_anchor = """    VertexOutOfBounds {
        /// Invalid vertex index.
        vertex: usize,
        /// Number of vertices in the graph.
        vertex_count: usize,
    },
"""
error_variant_replacement = error_variant_anchor + """    /// A vertex index cannot be represented by compact retained edge storage.
    VertexIndexTooWide {
        /// Vertex index that exceeded the compact representation.
        vertex: usize,
        /// Largest endpoint representable by the retained edge format.
        maximum: usize,
    },
"""
error = replace_once(
    error,
    error_variant_anchor,
    error_variant_replacement,
    "VertexOutOfBounds error variant",
)

error_display_anchor = """            Self::VertexOutOfBounds {
                vertex,
                vertex_count,
            } => write!(formatter, "vertex {vertex} is outside 0..{vertex_count}"),
"""
error_display_replacement = error_display_anchor + """            Self::VertexIndexTooWide { vertex, maximum } => write!(
                formatter,
                "vertex {vertex} exceeds the retained edge endpoint limit {maximum}"
            ),
"""
error = replace_once(
    error,
    error_display_anchor,
    error_display_replacement,
    "VertexOutOfBounds display arm",
)

layout_tests = """

#[cfg(test)]
mod compact_edge_layout_tests {
    use super::{Edge, Laplacian};
    use crate::CmgError;

    #[test]
    fn edge_uses_compact_endpoints() {
        assert_eq!(std::mem::size_of::<Edge>(), 16);
        assert_eq!(std::mem::align_of::<Edge>(), 8);
    }

    #[cfg(target_pointer_width = "64")]
    #[test]
    fn endpoint_above_u32_is_rejected_before_graph_allocation() {
        let vertex = u32::MAX as usize + 1;
        let error = Laplacian::from_edges(vertex + 1, [(0, vertex, 1.0)]).unwrap_err();
        assert_eq!(
            error,
            CmgError::VertexIndexTooWide {
                vertex,
                maximum: u32::MAX as usize,
            }
        );
    }
}
"""
if "mod compact_edge_layout_tests" not in graph:
    graph += layout_tests

if graph == original_graph:
    raise SystemExit("compact edge patch made no graph changes")
if error == original_error:
    raise SystemExit("compact edge patch made no error changes")
if "u: u32" not in graph or "v: u32" not in graph:
    raise SystemExit("compact endpoint fields are missing")
if "VertexIndexTooWide" not in graph or "VertexIndexTooWide" not in error:
    raise SystemExit("typed endpoint-width guard is missing")

GRAPH_PATH.write_text(graph)
ERROR_PATH.write_text(error)
