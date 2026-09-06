//! Deterministic disconnected graph fixtures shared by timing and allocation builds.

use cmg::Laplacian;

pub(crate) struct Case {
    pub(crate) name: &'static str,
    pub(crate) graph: Laplacian,
}

fn paths(name: &'static str, lengths: &[usize], pairs: usize, isolates: usize) -> Case {
    let mut edges = Vec::new();
    let mut n = 0;
    for &size in lengths {
        edges.extend((0..size.saturating_sub(1)).map(|v| (n + v, n + v + 1, 1.0)));
        n += size;
    }
    edges.extend((0..pairs).map(|c| (n + 2 * c, n + 2 * c + 1, 1.0)));
    Case {
        name,
        graph: Laplacian::from_edges(n + 2 * pairs + isolates, edges).unwrap(),
    }
}

fn grid(name: &'static str, side: usize, pairs: usize) -> Case {
    let mut edges = Vec::new();
    let n = side * side;
    for v in 0..n {
        if v % side + 1 < side {
            edges.push((v, v + 1, 1.0));
        }
        if v + side < n {
            edges.push((v, v + side, 1.0));
        }
    }
    edges.extend((0..pairs).map(|c| (n + 2 * c, n + 2 * c + 1, 1.0)));
    Case {
        name,
        graph: Laplacian::from_edges(n + 2 * pairs, edges).unwrap(),
    }
}

fn triangles() -> Case {
    Case {
        name: "all-triangles",
        graph: Laplacian::from_edges(
            3000,
            (0..1000).flat_map(|c| {
                [
                    (3 * c, 3 * c + 1, 1.0),
                    (3 * c + 1, 3 * c + 2, 2.0),
                    (3 * c, 3 * c + 2, 0.5),
                ]
            }),
        )
        .unwrap(),
    }
}

pub(crate) fn sentinels() -> Vec<Case> {
    vec![
        paths("connected-path", &[4096], 0, 0),
        paths("path-plus-pairs", &[4096], 1000, 0),
        paths("path-plus-isolates", &[4096], 0, 1000),
        paths("two-paths-plus-pairs", &[2048, 2048], 1000, 0),
        paths("all-pairs", &[], 1000, 0),
        paths("pairs-below-threshold", &[], 349, 0),
        paths("pairs-at-threshold", &[], 350, 0),
        grid("connected-grid", 32, 0),
        grid("grid-plus-pairs", 32, 1000),
        triangles(),
    ]
}

fn next(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9e3779b97f4a7c15);
    let mut x = *state;
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d049bb133111eb);
    x ^ (x >> 31)
}

fn shuffled(name: &'static str, n: usize, edges: Vec<(usize, usize, f64)>, seed: u64) -> Case {
    let mut permutation: Vec<_> = (0..n).collect();
    let mut state = seed;
    for i in (1..n).rev() {
        let j = (next(&mut state) % (i + 1) as u64) as usize;
        permutation.swap(i, j);
    }
    Case {
        name,
        graph: Laplacian::from_edges(
            n,
            edges
                .into_iter()
                .map(|(u, v, w)| (permutation[u], permutation[v], w)),
        )
        .unwrap(),
    }
}

fn weighted_path(name: &'static str, n: usize, pairs: usize, seed: u64) -> Case {
    let mut state = seed;
    let mut edges: Vec<_> = (0..n - 1)
        .map(|v| (v, v + 1, 10.0_f64.powi((next(&mut state) % 7) as i32 - 3)))
        .collect();
    edges.extend((0..pairs).map(|c| {
        (
            n + 2 * c,
            n + 2 * c + 1,
            0.125 + ((c * 3) % 17) as f64 / 8.0,
        )
    }));
    shuffled(name, n + 2 * pairs, edges, seed)
}

fn bridged_cliques(name: &'static str, pairs: usize, seed: u64) -> Case {
    let clusters = 32;
    let size = 24;
    let n = clusters * size;
    let mut edges = Vec::new();
    let mut state = seed;
    for c in 0..clusters {
        for u in 0..size {
            for v in u + 1..size {
                edges.push((
                    c * size + u,
                    c * size + v,
                    0.5 + (next(&mut state) % 17) as f64 / 16.0,
                ));
            }
        }
        if c > 0 {
            edges.push((c * size - 1, c * size, 1e-6));
        }
    }
    edges.extend((0..pairs).map(|c| (n + 2 * c, n + 2 * c + 1, 1.0)));
    shuffled(name, n + 2 * pairs, edges, seed)
}

fn worker_firm(name: &'static str, degree: usize, pairs: usize, seed: u64) -> Case {
    worker_firm_sized(name, 521, degree, pairs, seed)
}

fn worker_firm_sized(
    name: &'static str,
    side: usize,
    degree: usize,
    pairs: usize,
    seed: u64,
) -> Case {
    let n = 2 * side;
    let mut state = seed;
    let mut edges = Vec::new();
    for u in 0..side {
        // The two ring links guarantee connectivity; remaining links vary with seed.
        edges.push((u, side + u, 1.0));
        edges.push((u, side + (u + 1) % side, 0.5));
        for _ in 2..degree {
            let v = next(&mut state) as usize % side;
            edges.push((u, side + v, 0.25 + (next(&mut state) % 31) as f64 / 16.0));
        }
    }
    edges.extend((0..pairs).map(|c| (n + 2 * c, n + 2 * c + 1, 1.0)));
    shuffled(name, n + 2 * pairs, edges, seed)
}

fn heterogeneous(name: &'static str, pairs: usize, seed: u64) -> Case {
    let mut state = seed;
    let mut edges = Vec::new();
    let mut n = 0;
    for (c, size) in [3, 7, 13, 29, 61, 127, 251, 509].into_iter().enumerate() {
        for u in 0..size {
            if u + 1 < size {
                edges.push((
                    n + u,
                    n + u + 1,
                    0.25 + (next(&mut state) % 15) as f64 / 8.0,
                ));
            }
            for v in u + 2..size {
                if next(&mut state) % ((c + 2) * 5) as u64 == 0 {
                    edges.push((n + u, n + v, 0.5));
                }
            }
        }
        n += size;
    }
    edges.extend((0..pairs).map(|c| (n + 2 * c, n + 2 * c + 1, 1.0)));
    shuffled(name, n + 2 * pairs, edges, seed)
}

/// Freeze these dimensions and seed before measurement; use a new seed for a
/// later holdout, never silently replace a difficult or unsuccessful fixture.
pub(crate) fn stress(seed: u64) -> Vec<Case> {
    vec![
        weighted_path("weighted-connected-path", 4093, 0, seed),
        weighted_path("weighted-path-plus-pairs", 4093, 997, seed),
        bridged_cliques("bridged-connected-cliques", 0, seed + 1),
        bridged_cliques("bridged-cliques-plus-pairs", 997, seed + 1),
        worker_firm("sparse-connected-worker-firm", 4, 0, seed + 2),
        worker_firm("sparse-worker-firm-plus-pairs", 4, 997, seed + 2),
        worker_firm("dense-connected-worker-firm", 24, 0, seed + 3),
        worker_firm("dense-worker-firm-plus-pairs", 24, 997, seed + 3),
        heterogeneous("heterogeneous-material", 0, seed + 4),
        heterogeneous("heterogeneous-plus-pairs", 997, seed + 4),
        paths("material-below-threshold", &[696], 1, 0),
        paths("material-at-threshold", &[698], 1, 0),
    ]
}

/// Larger local cases, frozen before the kernel screen at seed 20260908.
pub(crate) fn large(seed: u64) -> Vec<Case> {
    vec![
        paths("large-connected-path", &[65536], 0, 0),
        paths("large-path-plus-pairs", &[65536], 16000, 0),
        grid("large-connected-grid", 128, 0),
        grid("large-grid-plus-pairs", 128, 16000),
        worker_firm_sized("large-sparse-connected-worker-firm", 8191, 4, 0, seed),
        worker_firm_sized("large-sparse-worker-firm-plus-pairs", 8191, 4, 16000, seed),
        worker_firm_sized("large-dense-connected-worker-firm", 8191, 24, 0, seed + 1),
        worker_firm_sized(
            "large-dense-worker-firm-plus-pairs",
            8191,
            24,
            16000,
            seed + 1,
        ),
        weighted_path("large-weighted-path-plus-pairs", 65533, 16000, seed + 2),
        paths("large-all-pairs", &[], 40000, 0),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    use cmg::Components;
    #[test]
    fn large_cases_have_exact_component_counts_and_finite_graphs() {
        let cases = large(20260908);
        assert_eq!(cases.len(), 10);
        for case in cases {
            let expected = if case.name.ends_with("plus-pairs") {
                16001
            } else if case.name == "large-all-pairs" {
                40000
            } else {
                1
            };
            assert_eq!(
                Components::from_laplacian(&case.graph).count(),
                expected,
                "{}",
                case.name
            );
            assert!(case.graph.vertex_count() <= 100000);
            assert!(case.graph.diagonal().iter().all(|x| x.is_finite()));
        }
    }
    #[test]
    fn stress_graphs_have_exact_component_counts_and_repeatable_weights() {
        let a = stress(20260906);
        let b = stress(20260906);
        let changed = stress(20260907);
        assert_eq!(a.len(), 12);
        for ((a, b), changed) in a.iter().zip(b).zip(changed) {
            assert_eq!(a.graph, b.graph);
            let expected = if a.name.contains("below-threshold") || a.name.contains("at-threshold")
            {
                2
            } else if a.name == "heterogeneous-material" {
                8
            } else if a.name == "heterogeneous-plus-pairs" {
                1005
            } else if a.name.ends_with("plus-pairs") {
                998
            } else {
                1
            };
            assert_eq!(
                Components::from_laplacian(&a.graph).count(),
                expected,
                "{}",
                a.name
            );
            if !a.name.contains("threshold") {
                assert_ne!(a.graph, changed.graph);
            }
        }
    }
}
