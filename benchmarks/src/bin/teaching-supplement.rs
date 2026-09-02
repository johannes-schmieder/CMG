//! Generate deterministic numerical exhibits for the CMG teaching supplement.
//!
//! This binary is intentionally benchmark-only: it exposes no new library API.

use cmg::{CmgOptions, CmgPreconditioner, Laplacian, PcgOptions, solve_pcg};
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::env;
use std::error::Error;
use std::fmt::Write as _;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};

const EXPECTED_VENETO_ROWS: usize = 71_614;

fn main() -> Result<(), Box<dyn Error>> {
    let (veneto_path, output_dir) = arguments()?;
    fs::create_dir_all(&output_dir)?;
    generate_toy(&output_dir)?;
    generate_veneto(&veneto_path, &output_dir)?;
    println!("generated teaching data in {}", output_dir.display());
    Ok(())
}

fn arguments() -> Result<(PathBuf, PathBuf), Box<dyn Error>> {
    let mut veneto = None;
    let mut output = None;
    let mut args = env::args().skip(1);
    while let Some(argument) = args.next() {
        match argument.as_str() {
            "--veneto" => veneto = args.next().map(PathBuf::from),
            "--output" => output = args.next().map(PathBuf::from),
            "--help" | "-h" => {
                println!("usage: teaching-supplement --veneto PATH --output DIRECTORY");
                std::process::exit(0);
            }
            _ => return Err(format!("unknown argument: {argument}").into()),
        }
    }
    Ok((
        veneto.ok_or("missing --veneto PATH")?,
        output.ok_or("missing --output DIRECTORY")?,
    ))
}

fn generate_toy(output: &Path) -> Result<(), Box<dyn Error>> {
    let edges = vec![
        (0, 1, 4.0),
        (0, 5, 1.2),
        (1, 2, 3.0),
        (1, 4, 0.8),
        (2, 3, 2.0),
        (2, 6, 0.4),
        (3, 4, 1.0),
        (4, 5, 2.5),
        (4, 8, 0.25),
        (6, 7, 4.0),
        (6, 11, 1.2),
        (7, 8, 3.0),
        (7, 10, 0.8),
        (8, 9, 2.0),
        (9, 10, 1.0),
        (10, 11, 2.5),
    ];
    let graph = Laplacian::from_edges(12, edges)?;
    let options = CmgOptions {
        direct_threshold: 3,
        ..CmgOptions::default()
    };
    let preconditioner = CmgPreconditioner::build(&graph, options)?;

    write_edges(output.join("toy_edges.csv"), &graph, "V")?;
    write_hierarchy(output.join("toy_hierarchy.csv"), &preconditioner)?;
    write_hierarchy_edges(output.join("toy_hierarchy_edges.csv"), &preconditioner)?;
    write_aggregations(output.join("toy_aggregations.csv"), &preconditioner)?;
    write_matrix(output.join("toy_laplacian.csv"), &dense_laplacian(&graph))?;
    write_matrix(
        output.join("toy_preconditioner.csv"),
        &materialize_preconditioner(&preconditioner, graph.vertex_count(), None)?,
    )?;

    let mut truth: Vec<f64> = (0..12)
        .map(|index| ((index as f64 + 1.0) * 0.71).sin() + 0.2 * index as f64)
        .collect();
    center(&mut truth);
    let rhs = matvec(&graph, &truth);
    let result = solve_pcg(&graph, &preconditioner, &rhs, PcgOptions::default())?;
    let maximum_error = result
        .solution()
        .iter()
        .zip(&truth)
        .map(|(actual, expected)| (actual - expected).abs())
        .fold(0.0_f64, f64::max);
    let mut file = csv_writer(output.join("toy_solution.csv"))?;
    writeln!(file, "vertex,x_true,rhs,x_pcg")?;
    for index in 0..12 {
        writeln!(
            file,
            "V{},{:.17e},{:.17e},{:.17e}",
            index + 1,
            truth[index],
            rhs[index],
            result.solution()[index]
        )?;
    }
    let terminal = format!(
        "{:?}",
        preconditioner.hierarchy().report().terminal_reason()
    );
    let macros = format!(
        "\\newcommand{{\\ToyVertices}}{{12}}\n\\newcommand{{\\ToyEdges}}{{{}}}\n\\newcommand{{\\ToyLevels}}{{{}}}\n\\newcommand{{\\ToyTerminal}}{{{}}}\n\\newcommand{{\\ToyIterations}}{{{}}}\n\\newcommand{{\\ToyRelativeResidual}}{{\\num{{{:.3e}}}}}\n\\newcommand{{\\ToyBackwardError}}{{\\num{{{:.3e}}}}}\n\\newcommand{{\\ToyMaximumError}}{{\\num{{{:.3e}}}}}\n",
        graph.edge_count(),
        preconditioner.hierarchy().levels().len(),
        terminal,
        result.iterations(),
        result.relative_residual(),
        result.backward_error(),
        maximum_error
    );
    fs::write(output.join("toy_macros.tex"), macros)?;
    Ok(())
}

#[derive(Debug)]
struct VenetoGraph {
    graph: Laplacian,
    rows: usize,
    workers: usize,
    firms: usize,
    unique_matches: usize,
    component_count: usize,
    largest_workers: usize,
    largest_firms: usize,
}

fn generate_veneto(path: &Path, output: &Path) -> Result<(), Box<dyn Error>> {
    let veneto = read_veneto(path)?;
    if veneto.rows != EXPECTED_VENETO_ROWS {
        return Err(format!(
            "unexpected Veneto row count: expected {EXPECTED_VENETO_ROWS}, found {}",
            veneto.rows
        )
        .into());
    }
    let graph = &veneto.graph;
    let preconditioner = CmgPreconditioner::build(graph, CmgOptions::default())?;
    write_hierarchy(output.join("veneto_hierarchy.csv"), &preconditioner)?;
    write_degree_histogram(output.join("veneto_degree_histogram.csv"), graph)?;

    let selected = select_neighborhood(graph, veneto.largest_workers, 12);
    let labels = anonymized_labels(&selected, veneto.largest_workers);
    write_selected_edges(
        output.join("veneto_selected_edges.csv"),
        graph,
        &selected,
        &labels,
    )?;
    let block = selected_laplacian_block(graph, &selected);
    write_labeled_matrix(output.join("veneto_laplacian_block.csv"), &labels, &block)?;
    let response =
        materialize_preconditioner(&preconditioner, graph.vertex_count(), Some(&selected))?;
    write_labeled_matrix(
        output.join("veneto_preconditioner_block.csv"),
        &labels,
        &response,
    )?;

    let mut truth: Vec<f64> = (0..graph.vertex_count())
        .map(|index| {
            let value = index as f64 + 1.0;
            (0.013 * value).sin() + 0.25 * (0.007 * value).cos()
        })
        .collect();
    center(&mut truth);
    let rhs = matvec(graph, &truth);
    let result = solve_pcg(graph, &preconditioner, &rhs, PcgOptions::default())?;
    let maximum_error = result
        .solution()
        .iter()
        .zip(&truth)
        .map(|(actual, expected)| (actual - expected).abs())
        .fold(0.0_f64, f64::max);
    let report = preconditioner.hierarchy().report();
    let terminal = format!("{:?}", report.terminal_reason());
    let macros = format!(
        "\\newcommand{{\\VenetoRows}}{{{}}}\n\\newcommand{{\\VenetoWorkers}}{{{}}}\n\\newcommand{{\\VenetoFirms}}{{{}}}\n\\newcommand{{\\VenetoMatches}}{{{}}}\n\\newcommand{{\\VenetoComponents}}{{{}}}\n\\newcommand{{\\VenetoLargestVertices}}{{{}}}\n\\newcommand{{\\VenetoLargestWorkers}}{{{}}}\n\\newcommand{{\\VenetoLargestFirms}}{{{}}}\n\\newcommand{{\\VenetoLargestEdges}}{{{}}}\n\\newcommand{{\\VenetoLevels}}{{{}}}\n\\newcommand{{\\VenetoTerminal}}{{{}}}\n\\newcommand{{\\VenetoIterations}}{{{}}}\n\\newcommand{{\\VenetoRelativeResidual}}{{\\num{{{:.3e}}}}}\n\\newcommand{{\\VenetoBackwardError}}{{\\num{{{:.3e}}}}}\n\\newcommand{{\\VenetoMaximumError}}{{\\num{{{:.3e}}}}}\n\\newcommand{{\\VenetoRetainedMiB}}{{\\num{{{:.2}}}}}\n",
        veneto.rows,
        veneto.workers,
        veneto.firms,
        veneto.unique_matches,
        veneto.component_count,
        graph.vertex_count(),
        veneto.largest_workers,
        veneto.largest_firms,
        graph.edge_count(),
        preconditioner.hierarchy().levels().len(),
        terminal,
        result.iterations(),
        result.relative_residual(),
        result.backward_error(),
        maximum_error,
        preconditioner.retained_bytes() as f64 / (1024.0 * 1024.0)
    );
    fs::write(output.join("veneto_macros.tex"), macros)?;
    Ok(())
}

fn read_veneto(path: &Path) -> Result<VenetoGraph, Box<dyn Error>> {
    let file = BufReader::new(File::open(path)?);
    let mut workers: HashMap<u64, usize> = HashMap::new();
    let mut firms: HashMap<u64, usize> = HashMap::new();
    let mut pairs: BTreeMap<(usize, usize), usize> = BTreeMap::new();
    let mut rows = 0;
    for line in file.lines() {
        let line = line?;
        let mut fields = line.split(',');
        let worker_id: u64 = fields.next().ok_or("missing worker field")?.parse()?;
        let firm_id: u64 = fields.next().ok_or("missing firm field")?.parse()?;
        let next_worker = workers.len();
        let worker = *workers.entry(worker_id).or_insert(next_worker);
        let next_firm = firms.len();
        let firm = *firms.entry(firm_id).or_insert(next_firm);
        *pairs.entry((worker, firm)).or_insert(0) += 1;
        rows += 1;
    }
    let worker_count = workers.len();
    let firm_count = firms.len();
    let vertex_count = worker_count + firm_count;
    let mut dsu = DisjointSet::new(vertex_count);
    for &(worker, firm) in pairs.keys() {
        dsu.union(worker, worker_count + firm);
    }
    let mut component_sizes: BTreeMap<usize, usize> = BTreeMap::new();
    for vertex in 0..vertex_count {
        *component_sizes.entry(dsu.find(vertex)).or_insert(0) += 1;
    }
    let (&largest_root, _) = component_sizes
        .iter()
        .max_by_key(|(root, size)| (**size, std::cmp::Reverse(**root)))
        .ok_or("Veneto graph contains no vertices")?;
    let members: Vec<usize> = (0..vertex_count)
        .filter(|&vertex| dsu.find(vertex) == largest_root)
        .collect();
    let mut new_index = vec![usize::MAX; vertex_count];
    let mut largest_workers = 0;
    for &old in &members {
        if old < worker_count {
            new_index[old] = largest_workers;
            largest_workers += 1;
        }
    }
    let mut largest_firms = 0;
    for &old in &members {
        if old >= worker_count {
            new_index[old] = largest_workers + largest_firms;
            largest_firms += 1;
        }
    }
    let edges = pairs.iter().filter_map(|(&(worker, firm), &weight)| {
        let old_firm = worker_count + firm;
        if new_index[worker] == usize::MAX || new_index[old_firm] == usize::MAX {
            None
        } else {
            Some((new_index[worker], new_index[old_firm], weight as f64))
        }
    });
    let graph = Laplacian::from_edges(members.len(), edges)?;
    Ok(VenetoGraph {
        graph,
        rows,
        workers: worker_count,
        firms: firm_count,
        unique_matches: pairs.len(),
        component_count: component_sizes.len(),
        largest_workers,
        largest_firms,
    })
}

fn select_neighborhood(graph: &Laplacian, worker_count: usize, target: usize) -> Vec<usize> {
    let mut adjacency = vec![Vec::new(); graph.vertex_count()];
    for edge in graph.edges() {
        adjacency[edge.u()].push(edge.v());
        adjacency[edge.v()].push(edge.u());
    }
    let unweighted_degrees: Vec<usize> = adjacency.iter().map(Vec::len).collect();
    for neighbors in &mut adjacency {
        neighbors.sort_by(|left, right| {
            unweighted_degrees[*right]
                .cmp(&unweighted_degrees[*left])
                .then_with(|| graph.diagonal()[*right].total_cmp(&graph.diagonal()[*left]))
                .then_with(|| left.cmp(right))
        });
    }
    let start = (worker_count..graph.vertex_count())
        .max_by(|left, right| {
            let left_mobile = adjacency[*left]
                .iter()
                .filter(|&&worker| unweighted_degrees[worker] > 1)
                .count();
            let right_mobile = adjacency[*right]
                .iter()
                .filter(|&&worker| unweighted_degrees[worker] > 1)
                .count();
            left_mobile
                .cmp(&right_mobile)
                .then_with(|| adjacency[*left].len().cmp(&adjacency[*right].len()))
                .then_with(|| graph.diagonal()[*left].total_cmp(&graph.diagonal()[*right]))
                .then_with(|| right.cmp(left))
        })
        .expect("largest component has a firm");
    let mut selected = vec![start];
    let mut seen = vec![false; graph.vertex_count()];
    seen[start] = true;
    let mut queue = VecDeque::from([start]);
    while selected.len() < target {
        let vertex = queue.pop_front().expect("connected component exhausted");
        let mut added = 0;
        for &neighbor in &adjacency[vertex] {
            if !seen[neighbor] && added < 3 {
                seen[neighbor] = true;
                selected.push(neighbor);
                queue.push_back(neighbor);
                added += 1;
                if selected.len() == target {
                    break;
                }
            }
        }
        if selected.len() < target && adjacency[vertex].iter().any(|&neighbor| !seen[neighbor]) {
            queue.push_back(vertex);
        }
    }
    selected
}

fn anonymized_labels(selected: &[usize], worker_count: usize) -> Vec<String> {
    let mut workers = 0;
    let mut firms = 0;
    selected
        .iter()
        .map(|&vertex| {
            if vertex < worker_count {
                workers += 1;
                format!("W{workers}")
            } else {
                firms += 1;
                format!("F{firms}")
            }
        })
        .collect()
}

fn write_selected_edges(
    path: PathBuf,
    graph: &Laplacian,
    selected: &[usize],
    labels: &[String],
) -> Result<(), Box<dyn Error>> {
    let positions: HashMap<usize, usize> = selected
        .iter()
        .enumerate()
        .map(|(position, &vertex)| (vertex, position))
        .collect();
    let mut file = csv_writer(path)?;
    writeln!(file, "u,v,weight")?;
    for edge in graph.edges() {
        if let (Some(&u), Some(&v)) = (positions.get(&edge.u()), positions.get(&edge.v())) {
            writeln!(file, "{},{},{:.17e}", labels[u], labels[v], edge.weight())?;
        }
    }
    Ok(())
}

fn write_hierarchy(
    path: PathBuf,
    preconditioner: &CmgPreconditioner,
) -> Result<(), Box<dyn Error>> {
    let mut file = csv_writer(path)?;
    writeln!(file, "level,vertices,edges,matrix_nnz,repeat,terminal")?;
    for (level_index, level) in preconditioner.hierarchy().levels().iter().enumerate() {
        writeln!(
            file,
            "{},{},{},{},{},{}",
            level_index,
            level.graph().vertex_count(),
            level.graph().edge_count(),
            level.graph().matrix_nnz(),
            preconditioner.repeat_counts()[level_index],
            level
                .terminal_reason()
                .map(|reason| format!("{reason:?}"))
                .unwrap_or_default()
        )?;
    }
    Ok(())
}

fn write_aggregations(
    path: PathBuf,
    preconditioner: &CmgPreconditioner,
) -> Result<(), Box<dyn Error>> {
    let mut file = csv_writer(path)?;
    writeln!(file, "level,fine_vertex,coarse_vertex")?;
    for (level_index, level) in preconditioner.hierarchy().levels().iter().enumerate() {
        if let Some(aggregation) = level.aggregation() {
            for (vertex, &label) in aggregation.labels().iter().enumerate() {
                writeln!(file, "{level_index},{vertex},{label}")?;
            }
        }
    }
    Ok(())
}

fn write_hierarchy_edges(
    path: PathBuf,
    preconditioner: &CmgPreconditioner,
) -> Result<(), Box<dyn Error>> {
    let mut file = csv_writer(path)?;
    writeln!(file, "level,u,v,weight")?;
    for (level_index, level) in preconditioner.hierarchy().levels().iter().enumerate() {
        for edge in level.graph().edges() {
            writeln!(
                file,
                "{level_index},{},{},{:.17e}",
                edge.u(),
                edge.v(),
                edge.weight()
            )?;
        }
    }
    Ok(())
}

fn write_edges(path: PathBuf, graph: &Laplacian, prefix: &str) -> Result<(), Box<dyn Error>> {
    let mut file = csv_writer(path)?;
    writeln!(file, "u,v,weight")?;
    for edge in graph.edges() {
        writeln!(
            file,
            "{}{},{},{:.17e}",
            prefix,
            edge.u() + 1,
            format_args!("{prefix}{}", edge.v() + 1),
            edge.weight()
        )?;
    }
    Ok(())
}

fn write_degree_histogram(path: PathBuf, graph: &Laplacian) -> Result<(), Box<dyn Error>> {
    let bins = [
        ("1", 1usize, 1usize),
        ("2", 2, 2),
        ("3--4", 3, 4),
        ("5--8", 5, 8),
        ("9--16", 9, 16),
        ("17--32", 17, 32),
        ("33+", 33, usize::MAX),
    ];
    let mut counts = vec![0usize; bins.len()];
    let mut adjacency_count = vec![0usize; graph.vertex_count()];
    for edge in graph.edges() {
        adjacency_count[edge.u()] += 1;
        adjacency_count[edge.v()] += 1;
    }
    for degree in adjacency_count {
        if let Some((index, _)) = bins
            .iter()
            .enumerate()
            .find(|(_, (_, low, high))| degree >= *low && degree <= *high)
        {
            counts[index] += 1;
        }
    }
    let mut file = csv_writer(path)?;
    writeln!(file, "bin,count")?;
    for ((label, _, _), count) in bins.iter().zip(counts) {
        writeln!(file, "{label},{count}")?;
    }
    Ok(())
}

fn materialize_preconditioner(
    preconditioner: &CmgPreconditioner,
    dimension: usize,
    selected: Option<&[usize]>,
) -> Result<Vec<Vec<f64>>, Box<dyn Error>> {
    let columns: Vec<usize> = selected.map_or_else(|| (0..dimension).collect(), <[usize]>::to_vec);
    let rows = columns.clone();
    let mut matrix = vec![vec![0.0; columns.len()]; rows.len()];
    let mean = 1.0 / dimension as f64;
    for (column_position, &column) in columns.iter().enumerate() {
        let mut rhs = vec![-mean; dimension];
        rhs[column] += 1.0;
        let mut response = preconditioner.apply(&rhs)?;
        // A Laplacian solution is defined only up to an additive component
        // constant.  Center each response before displaying B so the teaching
        // matrix represents the quotient-space operator rather than a choice
        // of grounding convention.
        center(&mut response);
        for (row_position, &row) in rows.iter().enumerate() {
            matrix[row_position][column_position] = response[row];
        }
    }
    Ok(matrix)
}

fn dense_laplacian(graph: &Laplacian) -> Vec<Vec<f64>> {
    let mut matrix = vec![vec![0.0; graph.vertex_count()]; graph.vertex_count()];
    for (index, &diagonal) in graph.diagonal().iter().enumerate() {
        matrix[index][index] = diagonal;
    }
    for edge in graph.edges() {
        matrix[edge.u()][edge.v()] -= edge.weight();
        matrix[edge.v()][edge.u()] -= edge.weight();
    }
    matrix
}

fn selected_laplacian_block(graph: &Laplacian, selected: &[usize]) -> Vec<Vec<f64>> {
    let positions: HashMap<usize, usize> = selected
        .iter()
        .enumerate()
        .map(|(position, &vertex)| (vertex, position))
        .collect();
    let mut matrix = vec![vec![0.0; selected.len()]; selected.len()];
    for (position, &vertex) in selected.iter().enumerate() {
        matrix[position][position] = graph.diagonal()[vertex];
    }
    for edge in graph.edges() {
        if let (Some(&u), Some(&v)) = (positions.get(&edge.u()), positions.get(&edge.v())) {
            matrix[u][v] -= edge.weight();
            matrix[v][u] -= edge.weight();
        }
    }
    matrix
}

fn write_labeled_matrix(
    path: PathBuf,
    labels: &[String],
    matrix: &[Vec<f64>],
) -> Result<(), Box<dyn Error>> {
    let mut file = csv_writer(path)?;
    writeln!(file, ",{}", labels.join(","))?;
    for (label, row) in labels.iter().zip(matrix) {
        write!(file, "{label}")?;
        for value in row {
            write!(file, ",{value:.17e}")?;
        }
        writeln!(file)?;
    }
    Ok(())
}

fn write_matrix(path: PathBuf, matrix: &[Vec<f64>]) -> Result<(), Box<dyn Error>> {
    let mut file = csv_writer(path)?;
    for row in matrix {
        let mut line = String::new();
        for (column, value) in row.iter().enumerate() {
            if column > 0 {
                line.push(',');
            }
            write!(&mut line, "{value:.17e}")?;
        }
        writeln!(file, "{line}")?;
    }
    Ok(())
}

fn csv_writer(path: PathBuf) -> Result<BufWriter<File>, Box<dyn Error>> {
    Ok(BufWriter::new(File::create(path)?))
}

fn matvec(graph: &Laplacian, vector: &[f64]) -> Vec<f64> {
    let mut output: Vec<f64> = graph
        .diagonal()
        .iter()
        .zip(vector)
        .map(|(diagonal, value)| diagonal * value)
        .collect();
    for edge in graph.edges() {
        output[edge.u()] -= edge.weight() * vector[edge.v()];
        output[edge.v()] -= edge.weight() * vector[edge.u()];
    }
    output
}

fn center(vector: &mut [f64]) {
    let mean = vector.iter().sum::<f64>() / vector.len() as f64;
    for value in vector {
        *value -= mean;
    }
}

#[derive(Debug)]
struct DisjointSet {
    parent: Vec<usize>,
    size: Vec<usize>,
}

impl DisjointSet {
    fn new(count: usize) -> Self {
        Self {
            parent: (0..count).collect(),
            size: vec![1; count],
        }
    }

    fn find(&mut self, vertex: usize) -> usize {
        let mut root = vertex;
        while self.parent[root] != root {
            root = self.parent[root];
        }
        let mut current = vertex;
        while self.parent[current] != current {
            let next = self.parent[current];
            self.parent[current] = root;
            current = next;
        }
        root
    }

    fn union(&mut self, left: usize, right: usize) {
        let mut left_root = self.find(left);
        let mut right_root = self.find(right);
        if left_root == right_root {
            return;
        }
        if self.size[left_root] < self.size[right_root]
            || (self.size[left_root] == self.size[right_root] && left_root > right_root)
        {
            std::mem::swap(&mut left_root, &mut right_root);
        }
        self.parent[right_root] = left_root;
        self.size[left_root] += self.size[right_root];
    }
}
