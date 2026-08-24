from pathlib import Path
import subprocess

SOURCE_COMMIT = "fe907c4d52a64bc87379dd2b21b044b05c54b893"
SOURCE_PATH = "scripts/row_bucket_endpoint_sort_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "row_bucket_endpoint_sort_gate.py",
    "lsd_radix_sort_gate.py",
)
text = text.replace(
    "row-bucket-endpoint-sort.yml",
    "lsd-radix-sort.yml",
)
text = text.replace(
    "row-bucket-endpoint-sort-latest.json",
    "lsd-radix-sort-latest.json",
)
text = text.replace(
    "in-place-row-bucket-endpoint-sort",
    "memory-routed-lsd-radix-sort",
)
text = text.replace(
    "Row-bucket endpoint-sort",
    "Memory-routed LSD radix-sort",
)
text = text.replace(
    "row-bucket endpoint-sort",
    "memory-routed LSD radix-sort",
)
text = text.replace(
    "In-place endpoint-row partitioning with per-row key sorting",
    "Memory-routed stable LSD radix sorting",
)
text = text.replace(
    "perf: retain row-bucket endpoint sorting",
    "perf: retain memory-routed LSD radix sorting",
)
text = text.replace(
    "perf: record row-bucket endpoint-sort experiment",
    "perf: record memory-routed LSD radix-sort experiment",
)

candidate_start = text.index("OLD_COMPACT =")
candidate_end = text.index("\n\ndef geometric", candidate_start)
candidate_block = r'''OLD_COMPACT = '''    pub(crate) fn from_compact_edges(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        sort_compact_edge_endpoints(&mut raw);
        Self::from_endpoint_sorted_raw_edges(vertex_count, raw)
    }
'''
NEW_COMPACT = '''    pub(crate) fn from_compact_edges(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        sort_compact_edge_endpoints_routed(vertex_count, &mut raw);
        Self::from_endpoint_sorted_raw_edges(vertex_count, raw)
    }
'''
OLD_FALLBACK = '''        } else {
            sort_compact_edge_endpoints(&mut raw);
            return Self::from_endpoint_sorted_raw_edges(vertex_count, raw);
        }
'''
NEW_FALLBACK = '''        } else {
            sort_compact_edge_endpoints_routed(vertex_count, &mut raw);
            return Self::from_endpoint_sorted_raw_edges(vertex_count, raw);
        }
'''
HELPER_MARKER = '''fn sort_compact_edge_endpoints(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
}
'''
HELPER_REPLACEMENT = '''fn sort_compact_edge_endpoints(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
}

const LSD_RADIX_SORT_MIN_EDGES: usize = 1 << 18;
const LSD_RADIX_SORT_MIN_EDGES_PER_VERTEX: usize = 8;
const LSD_RADIX_SORT_MAX_SCRATCH_BYTES: usize = 64 << 20;
const LSD_RADIX_BUCKETS: usize = 1 << 16;

#[inline]
fn should_use_lsd_radix_sort(vertex_count: usize, edge_count: usize) -> bool {
    let scratch_bytes = edge_count.saturating_mul(std::mem::size_of::<Edge>());
    vertex_count > 0
        && edge_count >= LSD_RADIX_SORT_MIN_EDGES
        && edge_count
            >= vertex_count.saturating_mul(LSD_RADIX_SORT_MIN_EDGES_PER_VERTEX)
        && scratch_bytes <= LSD_RADIX_SORT_MAX_SCRATCH_BYTES
}

fn sort_compact_edge_endpoints_routed(vertex_count: usize, raw: &mut [Edge]) {
    if should_use_lsd_radix_sort(vertex_count, raw.len()) {
        sort_compact_edge_endpoints_lsd(raw);
    } else {
        sort_compact_edge_endpoints(raw);
    }
}

fn radix_endpoint_pass(
    source: &[Edge],
    destination: &mut [Edge],
    shift: u32,
    positions: &mut [usize],
) {
    debug_assert_eq!(source.len(), destination.len());
    positions.fill(0);
    for edge in source {
        positions[((edge.key >> shift) & 0xffff) as usize] += 1;
    }
    let mut running = 0_usize;
    for position in positions.iter_mut() {
        let count = *position;
        *position = running;
        running += count;
    }
    for &edge in source {
        let bucket = ((edge.key >> shift) & 0xffff) as usize;
        destination[positions[bucket]] = edge;
        positions[bucket] += 1;
    }
}

fn sort_compact_edge_endpoints_lsd(raw: &mut [Edge]) {
    if raw.len() < 2 {
        return;
    }
    let reference = raw[0].key;
    let varying = raw
        .iter()
        .skip(1)
        .fold(0_u64, |mask, edge| mask | (reference ^ edge.key));
    if varying == 0 {
        return;
    }

    let mut scratch = vec![raw[0]; raw.len()];
    let mut positions = vec![0_usize; LSD_RADIX_BUCKETS];
    let mut source_is_raw = true;
    for shift in [0_u32, 16, 32, 48] {
        if ((varying >> shift) & 0xffff) == 0 {
            continue;
        }
        if source_is_raw {
            radix_endpoint_pass(raw, &mut scratch, shift, &mut positions);
        } else {
            radix_endpoint_pass(&scratch, raw, shift, &mut positions);
        }
        source_is_raw = !source_is_raw;
    }
    if !source_is_raw {
        raw.copy_from_slice(&scratch);
    }
}
'''
TEST_MODULE = '''

#[cfg(test)]
mod lsd_radix_endpoint_sort_tests {
    use super::{
        Edge, Laplacian, endpoint_key, should_use_lsd_radix_sort,
        sort_compact_edge_endpoints_lsd,
    };

    #[test]
    fn lsd_sort_matches_endpoint_key_order_and_graph() {
        let vertex_count = 2_048;
        let mut candidate = Vec::new();
        for index in 0..50_000_usize {
            let left = (37 * index + 11) % (vertex_count - 1);
            let right = left + 1 + ((97 * index + 3) % (vertex_count - left - 1));
            let weight = 0.25 + ((index * 13) % 101) as f64 / 32.0;
            candidate.push(
                Edge::from_internal_parts(left, right, weight).unwrap(),
            );
            if index % 7 == 0 {
                candidate.push(
                    Edge::from_internal_parts(left, right, weight + 0.125).unwrap(),
                );
            }
        }
        candidate.reverse();
        let original = candidate.clone();
        let mut reference = candidate.clone();
        reference.sort_unstable_by_key(endpoint_key);
        sort_compact_edge_endpoints_lsd(&mut candidate);
        assert_eq!(
            candidate.iter().map(endpoint_key).collect::<Vec<_>>(),
            reference.iter().map(endpoint_key).collect::<Vec<_>>(),
        );
        let public = Laplacian::from_edges(
            vertex_count,
            original
                .iter()
                .map(|edge| (edge.u(), edge.v(), edge.weight())),
        )
        .unwrap();
        let routed = Laplacian::from_endpoint_sorted_raw_edges(
            vertex_count,
            candidate,
        )
        .unwrap();
        assert_eq!(routed, public);
    }

    #[test]
    fn router_requires_dense_bounded_scratch() {
        assert!(!should_use_lsd_radix_sort(1_000_000, 999_999));
        assert!(!should_use_lsd_radix_sort(40_000, 250_000));
        assert!(should_use_lsd_radix_sort(40_000, 400_000));
        assert!(!should_use_lsd_radix_sort(100_000, 5_000_000));
    }
}
'''


def apply_candidate(source):
    replacements = (
        (OLD_COMPACT, NEW_COMPACT, "compact constructor"),
        (OLD_FALLBACK, NEW_FALLBACK, "serial executor fallback"),
        (HELPER_MARKER, HELPER_REPLACEMENT, "endpoint sort helper"),
    )
    candidate = source
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if "mod lsd_radix_endpoint_sort_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate
'''
text = text[:candidate_start] + candidate_block + text[candidate_end:]

replacements = (
    ('"active_contraction_geometric_ratio_max": 0.90', '"active_contraction_geometric_ratio_max": 0.85'),
    ('"contraction_geometric_ratio_max": 0.93', '"contraction_geometric_ratio_max": 0.90'),
    ('"hierarchy_geometric_ratio_max": 0.98', '"hierarchy_geometric_ratio_max": 0.95'),
    ('"allocation_geometric_peak_ratio_max": 1.08', '"allocation_geometric_peak_ratio_max": 1.20'),
    ('"worst_allocation_peak_ratio_max": 1.15', '"worst_allocation_peak_ratio_max": 1.35'),
    ('"worst_peak_rss_ratio_max": 1.10', '"worst_peak_rss_ratio_max": 1.20'),
    ('result["active_contraction_geometric_ratio"] <= 0.90', 'result["active_contraction_geometric_ratio"] <= 0.85'),
    ('result["contraction_geometric_ratio"] <= 0.93', 'result["contraction_geometric_ratio"] <= 0.90'),
    ('result["hierarchy_geometric_ratio"] <= 0.98', 'result["hierarchy_geometric_ratio"] <= 0.95'),
    ('result["allocation_geometric_peak_ratio"] <= 1.08', 'result["allocation_geometric_peak_ratio"] <= 1.20'),
    ('result["worst_allocation_peak_ratio"] <= 1.15', 'result["worst_allocation_peak_ratio"] <= 1.35'),
    ('result["worst_peak_rss_ratio"] <= 1.10', 'result["worst_peak_rss_ratio"] <= 1.20'),
)
for old, new in replacements:
    if text.count(old) != 1:
        raise SystemExit(f"LSD gate threshold marker changed: {old}")
    text = text.replace(old, new, 1)

text = text.replace(
    "full qualification passed; dense coarse levels use in-place row partitioning with material contraction and hierarchy gains inside a bounded temporary-memory budget",
    "full qualification passed; dense coarse levels use a scratch-capped streaming radix sort with material contraction and hierarchy gains",
)
text = text.replace(
    "correctness passed, but contraction, hierarchy, solve, or temporary-memory gates were not all met",
    "correctness passed, but contraction, hierarchy, solve, or scratch-memory gates were not all met",
)

cleanup_marker = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
try:
'''
cleanup_replacement = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path("scripts/row_bucket_endpoint_sort_gate.py").unlink(missing_ok=True)
try:
'''
if text.count(cleanup_marker) != 1:
    raise SystemExit("LSD gate cleanup marker changed unexpectedly")
text = text.replace(cleanup_marker, cleanup_replacement, 1)

required = (
    "lsd_radix_sort_gate.py",
    "lsd-radix-sort.yml",
    "LSD_RADIX_SORT_MAX_SCRATCH_BYTES",
    "sort_compact_edge_endpoints_lsd",
    '"active_contraction_geometric_ratio_max": 0.85',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"memory-routed LSD gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
