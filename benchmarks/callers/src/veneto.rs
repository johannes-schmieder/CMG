//! Public Veneto observation-weighted graph, both complete and largest connected block.
use super::fixtures::Case;
use cmg::{Components, Laplacian};
use std::collections::{BTreeMap, HashMap};
use std::io::{BufRead, BufReader};

pub(super) fn load(path: &str) -> Result<Vec<Case>, Box<dyn std::error::Error>> {
    from_reader(BufReader::new(std::fs::File::open(path)?))
}

fn from_reader(reader: impl BufRead) -> Result<Vec<Case>, Box<dyn std::error::Error>> {
    let mut workers = HashMap::new();
    let mut firms = HashMap::new();
    let mut pairs = BTreeMap::new();
    for line in reader.lines() {
        let line = line?;
        let mut fields = line.split(',');
        let w: u64 = fields.next().ok_or("worker")?.parse()?;
        let f: u64 = fields.next().ok_or("firm")?.parse()?;
        let next = workers.len();
        let w = *workers.entry(w).or_insert(next);
        let next = firms.len();
        let f = *firms.entry(f).or_insert(next);
        *pairs.entry((w, f)).or_insert(0usize) += 1;
    }
    let n = workers.len() + firms.len();
    let edges: Vec<_> = pairs
        .into_iter()
        .map(|((w, f), weight)| (w, workers.len() + f, weight as f64))
        .collect();
    let graph = Laplacian::from_edges(n, edges.iter().copied())?;
    let components = Components::from_laplacian(&graph);
    let mut sizes = vec![0usize; components.count()];
    for &label in components.labels() {
        sizes[label] += 1;
    }
    let largest = (0..sizes.len())
        .max_by_key(|&c| (sizes[c], std::cmp::Reverse(c)))
        .ok_or("empty input")?;
    let mut map = vec![usize::MAX; n];
    let mut count = 0;
    for (v, &label) in components.labels().iter().enumerate() {
        if label == largest {
            map[v] = count;
            count += 1;
        }
    }
    let connected = Laplacian::from_edges(
        count,
        edges
            .into_iter()
            .filter(|&(u, _, _)| map[u] != usize::MAX)
            .map(|(u, v, w)| (map[u], map[v], w)),
    )?;
    Ok(vec![
        Case {
            name: "veneto-complete",
            graph,
        },
        Case {
            name: "veneto-largest-connected",
            graph: connected,
        },
    ])
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn observation_weights_and_connected_extraction() {
        let cases = from_reader(std::io::Cursor::new("1,10\n1,10\n2,10\n2,11\n3,12\n")).unwrap();
        assert_eq!(cases[0].graph.vertex_count(), 6);
        assert_eq!(cases[0].graph.edge_count(), 4);
        assert_eq!(Components::from_laplacian(&cases[0].graph).count(), 2);
        assert_eq!(
            cases[0]
                .graph
                .edges()
                .iter()
                .map(|e| e.weight())
                .sum::<f64>(),
            5.0
        );
        assert_eq!(cases[1].graph.vertex_count(), 4);
        assert_eq!(cases[1].graph.edge_count(), 3);
        assert_eq!(Components::from_laplacian(&cases[1].graph).count(), 1);
    }
    #[test]
    fn malformed_and_empty_inputs_fail() {
        for input in ["", "worker,firm\n", "1\n"] {
            assert!(from_reader(std::io::Cursor::new(input)).is_err());
        }
    }
}
