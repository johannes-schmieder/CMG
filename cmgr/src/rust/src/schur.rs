//! Exact partial elimination. CMG still owns PCG and solves this graph itself.
use super::{add, checkpoint, zero};
use cmg::{BuildContext, Laplacian};
use std::sync::atomic::AtomicBool;

pub const MAX_DEGREE: usize = 5;
const ELIMINATED: usize = usize::MAX;

struct Cell {
    firm: usize,
    weight: f64,
}

pub struct Reduction {
    worker_is_a: bool,
    firms: usize,
    offsets: Vec<usize>,
    rows: Vec<usize>,
    cell_offsets: Vec<usize>,
    cells: Vec<Cell>,
    nodes: Vec<usize>,
    totals: Vec<f64>,
    pub eliminated: usize,
    pub vertices: usize,
}

fn filled(n: usize, value: usize) -> Result<Vec<usize>, String> {
    let mut result = Vec::new();
    result.try_reserve_exact(n).map_err(|e| e.to_string())?;
    result.resize(n, value);
    Ok(result)
}

impl Reduction {
    pub fn build(
        a: &[i32],
        b: &[i32],
        na: usize,
        nb: usize,
        weights: &[f64],
        context: BuildContext<'_>,
        cancel: &AtomicBool,
    ) -> Result<(Self, Laplacian), String> {
        let worker_is_a = na >= nb;
        let (workers, firms, nw, nf) = if worker_is_a {
            (a, b, na, nb)
        } else {
            (b, a, nb, na)
        };
        let mut offsets = filled(nw + 1, 0)?;
        for (i, &worker) in workers.iter().enumerate() {
            if i % 4096 == 0 {
                checkpoint(cancel)?;
            }
            offsets[worker as usize] += 1;
        }
        for i in 1..=nw {
            offsets[i] += offsets[i - 1];
        }
        let mut next = filled(nw, 0)?;
        next.copy_from_slice(&offsets[..nw]);
        let mut rows = filled(a.len(), 0)?;
        for (i, &worker) in workers.iter().enumerate() {
            if i % 4096 == 0 {
                checkpoint(cancel)?;
            }
            let slot = &mut next[worker as usize - 1];
            rows[*slot] = i;
            *slot += 1;
        }
        drop(next);
        let mut cell_offsets = filled(nw + 1, 0)?;
        let mut cells = Vec::new();
        let mut nodes = filled(nw, ELIMINATED)?;
        let mut totals = zero(nw)?;
        let (mut vertices, mut eliminated, mut edge_count) = (nf, 0, 0usize);
        for worker in 0..nw {
            checkpoint(cancel)?;
            let mut ids = [0usize; MAX_DEGREE];
            let (mut sums, mut corrections) = ([0.0; MAX_DEGREE], [0.0; MAX_DEGREE]);
            let (mut count, mut retained) = (0, false);
            for (ordinal, &row) in rows[offsets[worker]..offsets[worker + 1]]
                .iter()
                .enumerate()
            {
                if ordinal % 4096 == 0 {
                    checkpoint(cancel)?;
                }
                let firm = firms[row] as usize - 1;
                let index = match ids[..count].iter().position(|&id| id == firm) {
                    Some(index) => index,
                    None if count < MAX_DEGREE => {
                        ids[count] = firm;
                        count += 1;
                        count - 1
                    }
                    None => {
                        retained = true;
                        break;
                    }
                };
                add(&mut sums[index], &mut corrections[index], weights[row]);
            }
            if retained {
                nodes[worker] = vertices;
                vertices = vertices
                    .checked_add(1)
                    .ok_or("Schur vertex count overflow")?;
                edge_count = edge_count
                    .checked_add(offsets[worker + 1] - offsets[worker])
                    .ok_or("Schur edge count overflow")?;
            } else {
                cells.try_reserve(count).map_err(|e| e.to_string())?;
                let (mut total, mut correction) = (0.0, 0.0);
                for j in 0..count {
                    let weight = sums[j] + corrections[j];
                    add(&mut total, &mut correction, weight);
                    cells.push(Cell {
                        firm: ids[j],
                        weight,
                    });
                }
                totals[worker] = total + correction;
                eliminated += 1;
                edge_count = edge_count
                    .checked_add(count * (count - 1) / 2)
                    .ok_or("Schur edge count overflow")?;
            }
            cell_offsets[worker + 1] = cells.len();
        }
        let mut edges = Vec::new();
        edges
            .try_reserve_exact(edge_count)
            .map_err(|e| e.to_string())?;
        for worker in 0..nw {
            checkpoint(cancel)?;
            if nodes[worker] != ELIMINATED {
                for (ordinal, &row) in rows[offsets[worker]..offsets[worker + 1]]
                    .iter()
                    .enumerate()
                {
                    if ordinal % 4096 == 0 {
                        checkpoint(cancel)?;
                    }
                    edges.push((nodes[worker], firms[row] as usize - 1, weights[row]));
                }
            } else {
                let row = &cells[cell_offsets[worker]..cell_offsets[worker + 1]];
                for (j, a) in row.iter().enumerate() {
                    for b in &row[j + 1..] {
                        // Avoid overflowing/underflowing the intermediate product.
                        let weight =
                            a.weight.min(b.weight) * (a.weight.max(b.weight) / totals[worker]);
                        if !weight.is_finite() || weight <= 0.0 {
                            return Err("Schur edge weight underflowed or is nonfinite".into());
                        }
                        edges.push((a.firm, b.firm, weight));
                    }
                }
            }
        }
        let graph =
            Laplacian::from_edges_with(vertices, edges, context).map_err(|e| e.to_string())?;
        Ok((
            Self {
                worker_is_a,
                firms: nf,
                offsets,
                rows,
                cell_offsets,
                cells,
                nodes,
                totals,
                eliminated,
                vertices,
            },
            graph,
        ))
    }

    pub fn effect(&self) -> usize {
        if self.worker_is_a { 1 } else { 2 }
    }
    pub fn retained(&self) -> usize {
        self.nodes.len() - self.eliminated
    }
    fn worker_index(&self, worker: usize, na: usize) -> usize {
        if self.worker_is_a {
            worker
        } else {
            na + worker
        }
    }
    fn firm_index(&self, firm: usize, na: usize) -> usize {
        if self.worker_is_a { na + firm } else { firm }
    }

    /// Center within each eliminated worker before accumulating the firm RHS.
    /// Balance only its largest-weight cell to remove summation roundoff; a
    /// stayer's contribution is identically zero, even in an isolated component.
    #[allow(clippy::too_many_arguments)]
    pub fn rhs(
        &self,
        a: &[i32],
        b: &[i32],
        na: usize,
        weights: &[f64],
        input: &[f64],
        scale: f64,
        mean: f64,
        full_rhs: &[f64],
        cancel: &AtomicBool,
    ) -> Result<Vec<f64>, String> {
        let firms = if self.worker_is_a { b } else { a };
        let sign = if self.worker_is_a { -1.0 } else { 1.0 };
        let mut rhs = zero(self.vertices)?;
        let mut correction = zero(self.vertices)?;
        for worker in 0..self.nodes.len() {
            checkpoint(cancel)?;
            let full = self.worker_index(worker, na);
            if self.nodes[worker] != ELIMINATED {
                rhs[self.nodes[worker]] = full_rhs[full];
                for (ordinal, &row) in self.rows[self.offsets[worker]..self.offsets[worker + 1]]
                    .iter()
                    .enumerate()
                {
                    if ordinal % 4096 == 0 {
                        checkpoint(cancel)?;
                    }
                    let firm = firms[row] as usize - 1;
                    add(
                        &mut rhs[firm],
                        &mut correction[firm],
                        sign * weights[row] * (input[row] / scale - mean),
                    );
                }
                continue;
            }
            let cells = &self.cells[self.cell_offsets[worker]..self.cell_offsets[worker + 1]];
            if cells.len() == 1 {
                continue;
            }
            let worker_mean = -sign * full_rhs[full] / self.totals[worker];
            let (mut sums, mut corrections) = ([0.0; MAX_DEGREE], [0.0; MAX_DEGREE]);
            for (ordinal, &row) in self.rows[self.offsets[worker]..self.offsets[worker + 1]]
                .iter()
                .enumerate()
            {
                if ordinal % 4096 == 0 {
                    checkpoint(cancel)?;
                }
                let firm = firms[row] as usize - 1;
                let j = cells
                    .iter()
                    .position(|c| c.firm == firm)
                    .expect("prepared worker cell");
                add(
                    &mut sums[j],
                    &mut corrections[j],
                    weights[row] * ((input[row] / scale - mean) - worker_mean),
                );
            }
            let anchor = cells
                .iter()
                .enumerate()
                .max_by(|(_, a), (_, b)| a.weight.total_cmp(&b.weight))
                .unwrap()
                .0;
            let (mut balance, mut balance_correction) = (0.0, 0.0);
            for (j, cell) in cells.iter().enumerate() {
                if j == anchor {
                    continue;
                }
                let value = sign * (sums[j] + corrections[j]);
                add(&mut rhs[cell.firm], &mut correction[cell.firm], value);
                add(&mut balance, &mut balance_correction, value);
            }
            let firm = cells[anchor].firm;
            add(
                &mut rhs[firm],
                &mut correction[firm],
                -(balance + balance_correction),
            );
        }
        for (i, (value, correction)) in rhs.iter_mut().zip(correction).enumerate() {
            if i % 4096 == 0 {
                checkpoint(cancel)?;
            }
            *value += correction;
        }
        Ok(rhs)
    }

    pub fn recover(
        &self,
        na: usize,
        rhs: &[f64],
        reduced: &[f64],
        cancel: &AtomicBool,
    ) -> Result<Vec<f64>, String> {
        let mut full = zero(rhs.len())?;
        for (firm, &value) in reduced[..self.firms].iter().enumerate() {
            if firm % 4096 == 0 {
                checkpoint(cancel)?;
            }
            full[self.firm_index(firm, na)] = value;
        }
        for worker in 0..self.nodes.len() {
            if worker % 4096 == 0 {
                checkpoint(cancel)?;
            }
            let index = self.worker_index(worker, na);
            full[index] = if self.nodes[worker] != ELIMINATED {
                reduced[self.nodes[worker]]
            } else {
                let cells = &self.cells[self.cell_offsets[worker]..self.cell_offsets[worker + 1]];
                let anchor = reduced[cells[0].firm];
                let (mut value, mut correction) = (rhs[index] / self.totals[worker], 0.0);
                for cell in cells {
                    add(
                        &mut value,
                        &mut correction,
                        (cell.weight / self.totals[worker]) * (reduced[cell.firm] - anchor),
                    );
                }
                anchor + (value + correction)
            };
        }
        Ok(full)
    }
}
