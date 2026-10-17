//! Parent-compatible transcript for the unchanged controlled scalar APIs.
#![cfg(feature = "experimental-components")]
use cmg::{
    BuildBoundary, BuildControl, BuildDecision, BuildOperation, BuildStopReason, CmgError,
    CmgOptions, CmgPreconditioner, ComponentBuildOptions, Laplacian,
};

fn capture<T: core::fmt::Debug>(
    name: &str,
    transcript: &mut String,
    mut build: impl FnMut(u64, &mut dyn BuildControl) -> Result<T, CmgError>,
) -> T {
    let mut trace = Vec::new();
    let value = build(u64::MAX, &mut |event| {
        trace.push(event);
        BuildDecision::Continue
    })
    .unwrap();
    let work: u64 = trace
        .iter()
        .map(|event: &BuildBoundary| match event.operation {
            BuildOperation::Work { units } => u64::from(units),
            BuildOperation::Allocation { .. } => 0,
        })
        .sum();
    let mut exact_trace = Vec::new();
    let exact = build(work, &mut |event| {
        exact_trace.push(event);
        BuildDecision::Continue
    })
    .unwrap();
    assert_eq!(trace, exact_trace);
    assert_eq!(format!("{value:?}"), format!("{exact:?}"));
    transcript.push_str(&format!(
        "stage={name}; work={work}; trace={trace:?}\n{value:?}\n"
    ));
    for budget in [work - 1, 0] {
        let mut short_trace = Vec::new();
        let error = build(budget, &mut |event| {
            short_trace.push(event);
            BuildDecision::Continue
        })
        .unwrap_err();
        assert_eq!(short_trace, trace[..short_trace.len()]);
        let denied = trace[short_trace.len()];
        assert_eq!(
            error,
            CmgError::BuildStopped {
                reason: BuildStopReason::WorkBudgetExhausted,
                phase: denied.phase,
                chunk_id: denied.chunk_id,
                charged_work: denied.charged_work,
            }
        );
        transcript.push_str(&format!("budget={budget}; {error:?}\n"));
    }
    value
}

#[test]
fn scalar_preparation_hierarchy_boundaries_and_application_transcript() {
    let mut transcript = String::new();
    for n in [0_usize, 1, 9, 33] {
        let edges: Vec<_> = (1..n)
            .filter(|i| i % 11 != 0)
            .map(|i| (i - 1, i, 0.5 + (i % 5) as f64 / 4.0))
            .collect();
        transcript.push_str(&format!("vertices={n}\n"));
        let graph = capture("graph", &mut transcript, |work, control| {
            Laplacian::from_edges_controlled(n, &edges, usize::MAX, work, control)
        });
        let pre = capture("hierarchy", &mut transcript, |work, control| {
            CmgPreconditioner::build_component_experiment_serial_controlled(
                &graph,
                CmgOptions {
                    direct_threshold: 4,
                    max_levels: 8,
                    ..Default::default()
                },
                ComponentBuildOptions {
                    prune_coarse_isolates: true,
                    preserve_unpruned_stopping: true,
                    factor_terminal_components: true,
                },
                usize::MAX,
                work,
                control,
            )
        });
        capture("workspace bytes", &mut transcript, |work, control| {
            pre.workspace_allocation_bytes_controlled(work, control)
        });
        capture("cycle bound", &mut transcript, |work, control| {
            pre.scalar_cycle_work_bound_controlled(work, control)
        });
        let mut workspace = capture("workspace", &mut transcript, |work, control| {
            pre.try_workspace_controlled(work, control)
        });
        let rhs = graph
            .matvec(&(0..n).map(|i| (i % 7) as f64 - 3.0).collect::<Vec<_>>())
            .unwrap();
        let mut result = vec![f64::NAN; n];
        pre.apply_compatible_into(&rhs, &mut result, &mut workspace)
            .unwrap();
        transcript.push_str(&format!(
            "rhs={:?}; result={:?}\n",
            rhs.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
            result.iter().map(|x| x.to_bits()).collect::<Vec<_>>()
        ));
    }
    println!("SCALAR_MIGRATION_BEGIN\n{transcript}SCALAR_MIGRATION_END");
}
