//! A finite correctness transcript shared byte-for-byte with the parent revision.
//! Run `cargo test --test controlled_dispatch -- --nocapture` to retain its text.
use cmg::{
    BuildBoundary, BuildControl, BuildDecision, BuildOperation, BuildStopReason, CmgError,
    CmgOptions, CmgPreconditioner, Laplacian,
};

type Input = (usize, Vec<(usize, usize, f64)>);

fn fixtures() -> Vec<Input> {
    vec![
        (0, vec![]),
        (1, vec![]),
        // Reversed endpoints, duplicate compensated sums, a separate edge and
        // an isolate exercise every graph preparation phase on a small input.
        (
            7,
            vec![
                (2, 0, 1e16),
                (4, 3, 0.125),
                (0, 2, 1.0),
                (1, 2, 3.0),
                (0, 2, 2.0),
                (2, 1, 1e-16),
                (3, 4, 0.25),
                (0, 1, 7.0),
            ],
        ),
        (
            11,
            (1..11)
                .map(|i| (i, i - 1, (1 + i % 3) as f64 * 1e-60))
                .collect(),
        ),
        (
            9,
            (0..9)
                .flat_map(|i| (i + 1..9).map(move |j| (i, j, (1 + (i + j) % 5) as f64 * 1e60)))
                .collect(),
        ),
    ]
}

fn run(
    input: &Input,
    dynamic: bool,
    memory: usize,
    work: u64,
    stop: Option<(usize, BuildDecision)>,
) -> (Result<Laplacian, CmgError>, Vec<BuildBoundary>) {
    let mut trace = Vec::new();
    // This closure borrows stack-owned trace and stop state; no 'static bound
    // or allocation of the callback is permitted by either call form.
    let mut callback = |event| {
        trace.push(event);
        match stop {
            Some((ordinal, decision)) if trace.len() == ordinal + 1 => decision,
            _ => BuildDecision::Continue,
        }
    };
    let result = if dynamic {
        let erased: &mut dyn BuildControl = &mut callback;
        Laplacian::from_edges_controlled(input.0, &input.1, memory, work, erased)
    } else {
        Laplacian::from_edges_controlled(input.0, &input.1, memory, work, &mut callback)
    };
    (result, trace)
}

fn charged(trace: &[BuildBoundary]) -> u64 {
    trace
        .iter()
        .map(|event| match event.operation {
            BuildOperation::Work { units } => u64::from(units),
            BuildOperation::Allocation { .. } => 0,
        })
        .sum()
}

fn bits(values: &[f64]) -> Vec<u64> {
    values.iter().map(|value| value.to_bits()).collect()
}

fn graph_and_cycle(graph: &Laplacian) -> String {
    let edges: Vec<_> = graph
        .edges()
        .iter()
        .map(|edge| (edge.u(), edge.v(), edge.weight().to_bits()))
        .collect();
    let pre = CmgPreconditioner::build(
        graph,
        CmgOptions {
            direct_threshold: 3,
            ..Default::default()
        },
    )
    .unwrap();
    let probe: Vec<_> = (0..graph.vertex_count())
        .map(|i| (i % 5) as f64 - 2.0)
        .collect();
    let rhs = graph.matvec(&probe).unwrap();
    let output = pre.apply(&rhs).unwrap();
    format!(
        "edges={edges:?}; diagonal={:?}; rhs={:?}; cycle={:?}; repeats={:?}; stop={:?}",
        bits(graph.diagonal()),
        bits(&rhs),
        bits(&output),
        pre.repeat_counts(),
        pre.hierarchy().report().terminal_reason(),
    )
}

#[test]
fn concrete_and_dyn_complete_transcript_matches_graph_and_application_bits() {
    let mut transcript = String::new();
    for (index, input) in fixtures().iter().enumerate() {
        let (concrete, trace) = run(input, false, usize::MAX, u64::MAX, None);
        let (dynamic, dynamic_trace) = run(input, true, usize::MAX, u64::MAX, None);
        assert_eq!(trace, dynamic_trace);
        let concrete = concrete.unwrap();
        let dynamic = dynamic.unwrap();
        let reference = Laplacian::from_edges(input.0, input.1.iter().copied()).unwrap();
        let result = graph_and_cycle(&concrete);
        assert_eq!(result, graph_and_cycle(&dynamic));
        assert_eq!(result, graph_and_cycle(&reference));
        transcript.push_str(&format!("fixture={index}; trace={trace:?}\n{result}\n"));
        let exact = charged(&trace);
        for work in [exact, exact - 1, 0] {
            let (concrete, seen) = run(input, false, usize::MAX, work, None);
            let (dynamic, dynamic_seen) = run(input, true, usize::MAX, work, None);
            assert_eq!(seen, dynamic_seen);
            assert_eq!(format!("{concrete:?}"), format!("{dynamic:?}"));
            if work == exact {
                assert_eq!(seen, trace);
                assert_eq!(graph_and_cycle(&concrete.unwrap()), result);
                transcript.push_str(&format!("work={work}; complete\n"));
            } else {
                let denied = trace[seen.len()];
                assert_eq!(seen, trace[..seen.len()]);
                let expected = CmgError::BuildStopped {
                    reason: BuildStopReason::WorkBudgetExhausted,
                    phase: denied.phase,
                    chunk_id: denied.chunk_id,
                    charged_work: denied.charged_work,
                };
                assert_eq!(concrete.unwrap_err(), expected);
                transcript.push_str(&format!("work={work}; {expected:?}\n"));
            }
        }
    }
    println!("DISPATCH_TRANSCRIPT_BEGIN\n{transcript}DISPATCH_TRANSCRIPT_END");
}

#[test]
fn both_dispatch_forms_preserve_every_stop_and_interrupt_prefix() {
    let input = &fixtures()[2];
    let (_, trace) = run(input, false, usize::MAX, u64::MAX, None);
    for (index, boundary) in trace.iter().enumerate() {
        for (decision, reason) in [
            (BuildDecision::StopUnused, BuildStopReason::StopUnused),
            (BuildDecision::Interrupted, BuildStopReason::Interrupted),
        ] {
            for dynamic in [false, true] {
                let (result, seen) = run(
                    input,
                    dynamic,
                    usize::MAX,
                    u64::MAX,
                    Some((index, decision)),
                );
                assert_eq!(seen, trace[..=index]);
                assert_eq!(
                    result.unwrap_err(),
                    CmgError::BuildStopped {
                        reason,
                        phase: boundary.phase,
                        chunk_id: boundary.chunk_id,
                        charged_work: boundary.charged_work,
                    }
                );
            }
        }
    }
}

#[test]
fn dispatch_preserves_budget_callback_and_validation_error_precedence() {
    for input in [
        (2, vec![(2, 1, 1.0)]),
        (2, vec![(0, 2, 1.0)]),
        (2, vec![(1, 1, 1.0)]),
        (2, vec![(0, 1, 0.0)]),
        (2, vec![(0, 1, f64::INFINITY)]),
        (2, vec![(0, 1, f64::NAN)]),
    ] {
        for (memory, work, stop) in [
            (usize::MAX, u64::MAX, None),
            (0, u64::MAX, None),
            (0, 0, Some((0, BuildDecision::Interrupted))),
            (0, 128, Some((0, BuildDecision::StopUnused))),
        ] {
            let (concrete, trace) = run(&input, false, memory, work, stop);
            let (dynamic, dynamic_trace) = run(&input, true, memory, work, stop);
            assert_eq!(trace, dynamic_trace);
            let error = concrete.unwrap_err();
            // NaN is intentionally preserved in the error payload.
            assert_eq!(format!("{error:?}"), format!("{:?}", dynamic.unwrap_err()));
            match (memory, work, stop) {
                (_, 0, _) => {
                    assert!(trace.is_empty());
                    assert!(matches!(
                        error,
                        CmgError::BuildStopped {
                            reason: BuildStopReason::WorkBudgetExhausted,
                            ..
                        }
                    ));
                }
                (_, _, Some(_)) => assert!(matches!(
                    error,
                    CmgError::BuildStopped {
                        reason: BuildStopReason::StopUnused,
                        ..
                    }
                )),
                (0, _, _) => assert!(matches!(error, CmgError::MemoryBudgetExceeded { .. })),
                _ => assert_eq!(
                    format!("{error:?}"),
                    format!(
                        "{:?}",
                        Laplacian::from_edges(input.0, input.1.iter().copied()).unwrap_err()
                    )
                ),
            }
        }
    }
}
