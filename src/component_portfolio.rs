//! Explicit fixed-matrix experiment: direct isolates/pairs plus stationary PCG.

use std::sync::Arc;

use crate::components::ComponentWorkspace;
use crate::pcg::{allowed_residual, euclidean_norm, make_diagnostics};
use crate::{
    CmgError, CmgOptions, CmgPreconditioner, ComponentBuildOptions, Components, GroundedLdl,
    Laplacian, PcgDiagnostics, PcgOptions, PcgWorkspace, solve_pcg_into_with_workspace,
};

/// Prepared experimental partition of one fixed numerical Laplacian.
///
/// Whole components of size one or two are removed from the Krylov vectors and
/// factored together with grounded LDL. The remaining graph uses a stationary
/// component-aware CMG preconditioner with preserved stopping counts. This is an
/// explicit experiment, never an automatic route of ordinary PCG. Changing any
/// weight requires a new preparation. Clones share workspace identity.
#[derive(Debug, Clone)]
pub struct ComponentPcgExperiment {
    graph: Laplacian,
    identity: Arc<()>,
    route: PortfolioRoute,
}

#[derive(Debug, Clone)]
enum PortfolioRoute {
    Full(Box<CmgPreconditioner>),
    Split(Box<SplitPortfolio>),
}

#[derive(Debug, Clone)]
struct SplitPortfolio {
    components: Components,
    active_vertices: Box<[u32]>,
    tiny_vertices: Box<[u32]>,
    active: Option<CmgPreconditioner>,
    tiny: GroundedLdl,
}

/// Caller-reusable serial scratch for a [`ComponentPcgExperiment`].
///
/// A workspace belongs to the preparation that created it (or a clone), not to
/// another preparation with equal dimensions. Warm caller-buffer solves allocate
/// no numerical storage. Separate simultaneous solves require separate workspaces.
#[derive(Debug, Clone)]
pub struct ComponentPcgWorkspace {
    identity: Arc<()>,
    route: PortfolioWorkspace,
}

#[derive(Debug, Clone)]
enum PortfolioWorkspace {
    Full(Box<PcgWorkspace>),
    Split(Box<SplitWorkspace>),
}

#[derive(Debug, Clone)]
struct SplitWorkspace {
    projected_rhs: Vec<f64>,
    solution: Vec<f64>,
    residual: Vec<f64>,
    component: ComponentWorkspace,
    active_rhs: Vec<f64>,
    active_guess: Vec<f64>,
    active_solution: Vec<f64>,
    active_pcg: Option<PcgWorkspace>,
    tiny_rhs: Vec<f64>,
    tiny_solution: Vec<f64>,
    forward: Vec<f64>,
    backward: Vec<f64>,
}

/// Separate original-system certificate and active-PCG diagnostics.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ComponentPcgDiagnostics {
    original: PcgDiagnostics,
    active: Option<PcgDiagnostics>,
}

impl ComponentPcgDiagnostics {
    /// Full original-system residual, tolerance, backward error and RHS projection.
    ///
    /// Iterations and restarts count the active PCG work, which can differ from
    /// ordinary full-vector PCG. The initial residual refers to the full guess.
    #[must_use]
    pub const fn original(self) -> PcgDiagnostics {
        self.original
    }

    /// Diagnostics certified by the active PCG solve, if one was needed.
    ///
    /// These describe the active submitted RHS only. Use [`Self::original`] for
    /// the complete-system certificate. A preparation with no removable blocks
    /// delegates to ordinary PCG, so the two diagnostics are then identical.
    #[must_use]
    pub const fn active(self) -> Option<PcgDiagnostics> {
        self.active
    }
}

fn build_active(graph: &Laplacian, options: CmgOptions) -> Result<CmgPreconditioner, CmgError> {
    CmgPreconditioner::build_component_experiment(
        graph,
        options,
        ComponentBuildOptions {
            prune_coarse_isolates: true,
            preserve_unpruned_stopping: true,
            factor_terminal_components: true,
        },
    )
}

impl ComponentPcgExperiment {
    /// Partition and factor a fixed matrix, retaining its original graph for certification.
    ///
    /// Vertex order is preserved in each induced graph. The size threshold is
    /// fixed at two; neither RHS data nor observed convergence changes the partition.
    pub fn build(graph: &Laplacian, options: CmgOptions) -> Result<Self, CmgError> {
        let options = options.validate()?;
        let components = Components::from_laplacian(graph);
        let tiny_count = components
            .sizes()
            .iter()
            .filter(|&&n| n <= 2)
            .sum::<usize>();
        let route = if tiny_count == 0 {
            PortfolioRoute::Full(Box::new(build_active(graph, options)?))
        } else {
            let mut active_vertices = Vec::with_capacity(graph.vertex_count() - tiny_count);
            let mut tiny_vertices = Vec::with_capacity(tiny_count);
            let mut sub_index = vec![0usize; graph.vertex_count()];
            for (vertex, &component) in components.labels().iter().enumerate() {
                let vertices = if components.sizes()[component] <= 2 {
                    &mut tiny_vertices
                } else {
                    &mut active_vertices
                };
                sub_index[vertex] = vertices.len();
                vertices.push(vertex as u32);
            }
            let is_tiny = |v: usize| components.sizes()[components.labels()[v]] <= 2;
            let active = if active_vertices.is_empty() {
                None
            } else {
                let active_graph = Laplacian::from_edges(
                    active_vertices.len(),
                    graph
                        .edges()
                        .iter()
                        .filter(|e| !is_tiny(e.u()))
                        .map(|e| (sub_index[e.u()], sub_index[e.v()], e.weight())),
                )?;
                Some(build_active(&active_graph, options)?)
            };
            let tiny_graph = Laplacian::from_edges(
                tiny_vertices.len(),
                graph
                    .edges()
                    .iter()
                    .filter(|e| is_tiny(e.u()))
                    .map(|e| (sub_index[e.u()], sub_index[e.v()], e.weight())),
            )?;
            let tiny = GroundedLdl::factor_by_component(&tiny_graph)?;
            PortfolioRoute::Split(Box::new(SplitPortfolio {
                components,
                active_vertices: active_vertices.into_boxed_slice(),
                tiny_vertices: tiny_vertices.into_boxed_slice(),
                active,
                tiny,
            }))
        };
        Ok(Self {
            graph: graph.clone(),
            identity: Arc::new(()),
            route,
        })
    }

    /// Original fixed numerical graph used for all final certificates.
    #[must_use]
    pub const fn graph(&self) -> &Laplacian {
        &self.graph
    }

    /// Number of original vertices retained in the Krylov system.
    #[must_use]
    pub fn active_vertices(&self) -> usize {
        match &self.route {
            PortfolioRoute::Full(_) => self.graph.vertex_count(),
            PortfolioRoute::Split(split) => split.active_vertices.len(),
        }
    }

    /// Number of original vertices removed as whole isolates or pairs.
    #[must_use]
    pub fn direct_vertices(&self) -> usize {
        self.graph.vertex_count() - self.active_vertices()
    }

    /// Principal retained heap bytes, counting shared graph buffers once.
    ///
    /// Includes original certification graph, induced active hierarchy, maps,
    /// component metadata and tiny factors. Excludes allocator and `Arc` overhead.
    #[must_use]
    pub fn retained_bytes(&self) -> usize {
        match &self.route {
            PortfolioRoute::Full(pre) => pre
                .retained_bytes()
                .saturating_add(core::mem::size_of::<CmgPreconditioner>()),
            PortfolioRoute::Split(split) => self
                .graph
                .retained_bytes()
                .saturating_add(core::mem::size_of::<SplitPortfolio>())
                .saturating_add(split.components.byte_len())
                .saturating_add(self.graph.vertex_count().saturating_mul(4))
                .saturating_add(
                    split
                        .active
                        .as_ref()
                        .map_or(0, CmgPreconditioner::retained_bytes),
                )
                .saturating_add(split.tiny.byte_len()),
        }
    }

    /// Allocate reusable serial scratch for this preparation.
    pub fn workspace(&self) -> Result<ComponentPcgWorkspace, CmgError> {
        let route = match &self.route {
            PortfolioRoute::Full(pre) => {
                PortfolioWorkspace::Full(Box::new(PcgWorkspace::try_new(pre)?))
            }
            PortfolioRoute::Split(split) => {
                let n = self.graph.vertex_count();
                let a = split.active_vertices.len();
                let t = split.tiny_vertices.len();
                let d = split.tiny.active_dimension();
                PortfolioWorkspace::Split(Box::new(SplitWorkspace {
                    projected_rhs: zeros(n)?,
                    solution: zeros(n)?,
                    residual: zeros(n)?,
                    component: split.components.try_workspace()?,
                    active_rhs: zeros(a)?,
                    active_guess: zeros(a)?,
                    active_solution: zeros(a)?,
                    active_pcg: split
                        .active
                        .as_ref()
                        .map(PcgWorkspace::try_new)
                        .transpose()?,
                    tiny_rhs: zeros(t)?,
                    tiny_solution: zeros(t)?,
                    forward: zeros(d)?,
                    backward: zeros(d)?,
                }))
            }
        };
        Ok(ComponentPcgWorkspace {
            identity: Arc::clone(&self.identity),
            route,
        })
    }

    /// Solve into a caller buffer and certify against the full submitted system.
    ///
    /// All tolerances, compatibility checks, iteration budgets and residual
    /// replacement rules are unchanged. Active-PCG failure or failure of the
    /// final full-system certificate returns an error without changing `solution`.
    /// There is no fallback, retry or tolerance relaxation. Initial guesses are
    /// validated and centered on all original components, including removed ones.
    pub fn solve_into(
        &self,
        rhs: &[f64],
        initial_guess: Option<&[f64]>,
        solution: &mut [f64],
        options: PcgOptions,
        workspace: &mut ComponentPcgWorkspace,
    ) -> Result<ComponentPcgDiagnostics, CmgError> {
        let n = self.graph.vertex_count();
        for (context, actual) in [
            ("component portfolio RHS", rhs.len()),
            ("component portfolio solution", solution.len()),
        ] {
            if actual != n {
                return Err(CmgError::dimension(context, n, actual));
            }
        }
        if let Some(guess) = initial_guess {
            if guess.len() != n {
                return Err(CmgError::dimension(
                    "component portfolio initial guess",
                    n,
                    guess.len(),
                ));
            }
        }
        let options = options.validate()?;
        if !Arc::ptr_eq(&self.identity, &workspace.identity) {
            return Err(CmgError::InvalidHierarchy {
                context: "component portfolio workspace belongs to another preparation",
            });
        }
        match (&self.route, &mut workspace.route) {
            (PortfolioRoute::Full(pre), PortfolioWorkspace::Full(ws)) => {
                let original = solve_pcg_into_with_workspace(
                    &self.graph,
                    pre,
                    rhs,
                    initial_guess,
                    solution,
                    options,
                    ws,
                )?;
                Ok(ComponentPcgDiagnostics {
                    original,
                    active: Some(original),
                })
            }
            (PortfolioRoute::Split(split), PortfolioWorkspace::Split(ws)) => {
                ws.projected_rhs.copy_from_slice(rhs);
                let projection = split.components.project_rhs_in_place_with_workspace(
                    &mut ws.projected_rhs,
                    options.validation,
                    &mut ws.component,
                )?;
                let rhs_norm = euclidean_norm(rhs);
                let bound = self.graph.operator_norm_bound();
                let initial_residual = if let Some(guess) = initial_guess {
                    ws.solution.copy_from_slice(guess);
                    split
                        .components
                        .center_in_place_with_workspace(&mut ws.solution, &mut ws.component)?;
                    residual_norm(&self.graph, rhs, &ws.solution, &mut ws.residual)?
                } else {
                    ws.solution.fill(0.0);
                    rhs_norm
                };
                let initial_tolerance =
                    allowed_residual(options, rhs_norm, bound, euclidean_norm(&ws.solution));
                if initial_residual.is_finite()
                    && initial_tolerance.is_finite()
                    && initial_residual <= initial_tolerance
                {
                    let original = make_diagnostics(
                        &ws.solution,
                        0,
                        initial_residual,
                        initial_residual,
                        rhs_norm,
                        initial_tolerance,
                        bound,
                        0,
                        projection,
                    );
                    solution.copy_from_slice(&ws.solution);
                    return Ok(ComponentPcgDiagnostics {
                        original,
                        active: None,
                    });
                }
                gather(&ws.projected_rhs, &split.tiny_vertices, &mut ws.tiny_rhs);
                split.tiny.solve_into_compatible(
                    &ws.tiny_rhs,
                    &mut ws.tiny_solution,
                    &mut ws.forward,
                    &mut ws.backward,
                )?;
                let active = if let (Some(pre), Some(pcg)) = (&split.active, &mut ws.active_pcg) {
                    gather(
                        &ws.projected_rhs,
                        &split.active_vertices,
                        &mut ws.active_rhs,
                    );
                    if initial_guess.is_some() {
                        gather(&ws.solution, &split.active_vertices, &mut ws.active_guess);
                    }
                    let graph = pre.hierarchy().levels()[0].graph();
                    Some(solve_pcg_into_with_workspace(
                        graph,
                        pre,
                        &ws.active_rhs,
                        initial_guess.map(|_| ws.active_guess.as_slice()),
                        &mut ws.active_solution,
                        options,
                        pcg,
                    )?)
                } else {
                    None
                };
                scatter(&ws.tiny_solution, &split.tiny_vertices, &mut ws.solution);
                scatter(
                    &ws.active_solution,
                    &split.active_vertices,
                    &mut ws.solution,
                );
                split
                    .components
                    .center_in_place_with_workspace(&mut ws.solution, &mut ws.component)?;
                let residual = residual_norm(&self.graph, rhs, &ws.solution, &mut ws.residual)?;
                let tolerance =
                    allowed_residual(options, rhs_norm, bound, euclidean_norm(&ws.solution));
                let iterations = active.map_or(0, PcgDiagnostics::iterations);
                if !residual.is_finite() || !tolerance.is_finite() || residual > tolerance {
                    return Err(CmgError::ResidualVerificationFailed {
                        iteration: iterations,
                        residual_norm: residual,
                        tolerance,
                    });
                }
                let original = make_diagnostics(
                    &ws.solution,
                    iterations,
                    initial_residual,
                    residual,
                    rhs_norm,
                    tolerance,
                    bound,
                    active.map_or(0, PcgDiagnostics::restarts),
                    projection,
                );
                solution.copy_from_slice(&ws.solution);
                Ok(ComponentPcgDiagnostics { original, active })
            }
            _ => Err(CmgError::InvalidHierarchy {
                context: "component portfolio workspace route mismatch",
            }),
        }
    }
}

impl ComponentPcgWorkspace {
    /// Principal retained scratch bytes, including full-system certification vectors.
    #[must_use]
    pub fn byte_len(&self) -> usize {
        match &self.route {
            PortfolioWorkspace::Full(ws) => ws
                .byte_len()
                .saturating_add(core::mem::size_of::<PcgWorkspace>()),
            PortfolioWorkspace::Split(ws) => {
                let vectors = [
                    &ws.projected_rhs,
                    &ws.solution,
                    &ws.residual,
                    &ws.active_rhs,
                    &ws.active_guess,
                    &ws.active_solution,
                    &ws.tiny_rhs,
                    &ws.tiny_solution,
                    &ws.forward,
                    &ws.backward,
                ];
                vectors
                    .iter()
                    .fold(core::mem::size_of::<SplitWorkspace>(), |sum, v| {
                        sum.saturating_add(v.capacity().saturating_mul(8))
                    })
                    .saturating_add(ws.component.byte_len())
                    .saturating_add(ws.active_pcg.as_ref().map_or(0, PcgWorkspace::byte_len))
            }
        }
    }
}

fn zeros(n: usize) -> Result<Vec<f64>, CmgError> {
    let mut values = Vec::new();
    values
        .try_reserve_exact(n)
        .map_err(|_| CmgError::AllocationFailed {
            context: "component portfolio scratch",
        })?;
    values.resize(n, 0.0);
    Ok(values)
}

fn gather(full: &[f64], vertices: &[u32], sub: &mut [f64]) {
    for (value, &vertex) in sub.iter_mut().zip(vertices) {
        *value = full[vertex as usize];
    }
}

fn scatter(sub: &[f64], vertices: &[u32], full: &mut [f64]) {
    for (&value, &vertex) in sub.iter().zip(vertices) {
        full[vertex as usize] = value;
    }
}

fn residual_norm(
    graph: &Laplacian,
    rhs: &[f64],
    solution: &[f64],
    scratch: &mut [f64],
) -> Result<f64, CmgError> {
    graph.matvec_into(solution, scratch)?;
    for (value, &b) in scratch.iter_mut().zip(rhs) {
        *value = b - *value;
    }
    Ok(euclidean_norm(scratch))
}
