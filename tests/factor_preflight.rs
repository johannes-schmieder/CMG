#![cfg(feature = "experimental-components")]
//! Fixed-action and control-boundary witnesses for opt-in terminal affordability.
use cmg::{
    BuildBoundary, BuildControl, BuildDecision, BuildOperation, BuildStopReason, CmgError,
    CmgOptions, CmgPreconditioner, ComponentBuildOptions, Laplacian, TerminalReason,
};

fn experiment() -> ComponentBuildOptions {
    ComponentBuildOptions {
        prune_coarse_isolates: true,
        preserve_unpruned_stopping: true,
        factor_terminal_components: true,
    }
}
fn build(
    graph: &Laplacian,
    options: CmgOptions,
    budget: u64,
    control: &mut dyn BuildControl,
) -> Result<CmgPreconditioner, CmgError> {
    CmgPreconditioner::build_component_experiment_serial_controlled_with_factor_preflight(
        graph,
        options,
        experiment(),
        usize::MAX,
        budget,
        control,
    )
}
fn charge(event: &BuildBoundary) -> u64 {
    match event.operation {
        BuildOperation::Work { units } => units as u64,
        _ => 0,
    }
}
fn bits(v: &[f64]) -> Vec<u64> {
    v.iter().map(|x| x.to_bits()).collect()
}
fn path(n: usize, scale: f64) -> Laplacian {
    Laplacian::from_edges(n, (1..n).map(|v| (v - 1, v, scale))).unwrap()
}

#[test]
fn generous_preflight_preserves_legacy_boundaries_hierarchy_factor_and_apply_bits() {
    for n in [0, 1, 2, 9, 33] {
        let graph = path(n, 1.0);
        for direct_threshold in [2, 700] {
            let options = CmgOptions {
                direct_threshold,
                ..Default::default()
            };
            for prune in [false, true] {
                let experiment = ComponentBuildOptions {
                    prune_coarse_isolates: prune,
                    ..experiment()
                };
                let mut legacy_trace = Vec::new();
                let old = CmgPreconditioner::build_component_experiment_serial_controlled(
                    &graph,
                    options,
                    experiment,
                    usize::MAX,
                    u64::MAX,
                    &mut |event| {
                        legacy_trace.push(event);
                        BuildDecision::Continue
                    },
                )
                .unwrap();
                assert!(
                    legacy_trace
                        .iter()
                        .all(|e| e.phase != "terminal factor preflight")
                );
                let mut enabled_trace = Vec::new();
                let new = CmgPreconditioner::build_component_experiment_serial_controlled_with_factor_preflight(
                    &graph, options, experiment, usize::MAX, u64::MAX, &mut |event| {
                        enabled_trace.push(event); BuildDecision::Continue
                    }).unwrap();
                assert_eq!(old, new);
                // Reconstruct the entire old trace, including work and ordinals,
                // after removing only opt-in preflight charges.
                let mut normalized = Vec::new();
                let mut charged = 0;
                for event in &enabled_trace {
                    if event.phase == "terminal factor preflight" {
                        continue;
                    }
                    normalized.push(BuildBoundary {
                        chunk_id: normalized.len() as u64,
                        charged_work: charged,
                        ..*event
                    });
                    charged += charge(event);
                }
                assert_eq!(normalized, legacy_trace);
                let rhs = graph
                    .matvec(&(0..n).map(|i| (i % 7) as f64).collect::<Vec<_>>())
                    .unwrap();
                assert_eq!(
                    bits(&old.apply(&rhs).unwrap()),
                    bits(&new.apply(&rhs).unwrap())
                );
            }
        }
    }
}

#[test]
fn exact_post_preflight_boundary_decides_before_any_factor_work() {
    // Independently evaluated formula values F(0,0), F(1,0), F(2,1).
    for (n, factor_bound) in [(0, 536), (1, 1584), (2, 4488)] {
        let graph = path(n, 1.0);
        let options = CmgOptions::default();
        let mut trace = Vec::new();
        build(&graph, options, u64::MAX, &mut |event| {
            trace.push(event);
            BuildDecision::Continue
        })
        .unwrap();
        let preflight = *trace
            .iter()
            .find(|e| e.phase == "terminal factor preflight")
            .unwrap();
        assert_eq!(charge(&preflight), 1024);
        let at = preflight.charged_work + 1024 + factor_bound;
        let mut accepted_trace = Vec::new();
        let accepted = build(&graph, options, at, &mut |event| {
            accepted_trace.push(event);
            BuildDecision::Continue
        });
        assert_eq!(accepted_trace, trace[..accepted_trace.len()]);
        assert_eq!(
            accepted_trace[preflight.chunk_id as usize + 1].phase,
            "hierarchy inverse diagonal"
        );
        // F reserves no credit for intervening metadata/finalization. The direct
        // decision is admitted at equality, but completion can still exhaust.
        match accepted {
            Ok(candidate) => assert_eq!(
                candidate.hierarchy().report().terminal_reason(),
                TerminalReason::Direct
            ),
            Err(CmgError::BuildStopped {
                reason: BuildStopReason::WorkBudgetExhausted,
                phase,
                ..
            }) => assert_ne!(phase, "terminal factor preflight"),
            other => panic!("unexpected exact-bound result: {other:?}"),
        }
        let mut refused_trace = Vec::new();
        let refused = build(&graph, options, at - 1, &mut |event| {
            refused_trace.push(event);
            BuildDecision::Continue
        });
        assert!(
            refused_trace.iter().all(
                |e| !e.phase.starts_with("terminal ") || e.phase == "terminal factor preflight"
            ),
            "declined root must never enter factor preparation/allocation"
        );
        if n <= 1 {
            assert!(matches!(refused, Err(CmgError::BuildStopped {
                reason: BuildStopReason::WorkBudgetExhausted, phase: "terminal factor preflight",
                chunk_id, charged_work,
            }) if chunk_id == preflight.chunk_id + 1 && charged_work == preflight.charged_work + 1024));
            assert_eq!(refused_trace, trace[..=preflight.chunk_id as usize]);
        } else {
            let refused = refused.unwrap();
            assert_eq!(
                refused.hierarchy().report().terminal_reason(),
                TerminalReason::FullContraction
            );
            assert!(refused.terminal_factor().is_none());
        }
        let mut before = Vec::new();
        assert!(
            matches!(build(&graph, options, preflight.charged_work + 1023, &mut |event| {
            before.push(event); BuildDecision::Continue
        }), Err(CmgError::BuildStopped { reason: BuildStopReason::WorkBudgetExhausted,
            phase: "terminal factor preflight", charged_work, .. }) if charged_work == preflight.charged_work)
        );
        assert_eq!(
            before,
            trace[..preflight.chunk_id as usize],
            "budget gate must precede callback"
        );
    }
}

#[test]
fn opt_in_rejects_dense_factor_option_and_retains_every_stop_and_interrupt_boundary() {
    let graph = path(4, 1.0);
    assert!(matches!(
        CmgPreconditioner::build_component_experiment_serial_controlled_with_factor_preflight(
            &graph,
            CmgOptions::default(),
            ComponentBuildOptions::default(),
            usize::MAX,
            u64::MAX,
            &mut |_| panic!("invalid option reached callback")
        ),
        Err(CmgError::InvalidOption {
            name: "factor_terminal_components",
            ..
        })
    ));
    // A finite allowance declines root direct construction but completes an
    // existing non-direct stop. Every new-path boundary remains interruptible.
    let graph = path(24, 1.0);
    let options = CmgOptions {
        max_levels: 8,
        ..Default::default()
    };
    let mut trace = Vec::new();
    let reference = build(&graph, options, 200_000, &mut |event| {
        trace.push(event);
        BuildDecision::Continue
    })
    .unwrap();
    assert!(reference.hierarchy().levels().len() > 1);
    for (index, expected) in trace.iter().enumerate() {
        for (decision, reason) in [
            (BuildDecision::StopUnused, BuildStopReason::StopUnused),
            (BuildDecision::Interrupted, BuildStopReason::Interrupted),
        ] {
            let mut seen = 0;
            let result = build(&graph, options, 200_000, &mut |event| {
                assert_eq!(event, trace[seen]);
                seen += 1;
                if seen == index + 1 {
                    decision
                } else {
                    BuildDecision::Continue
                }
            });
            assert!(
                matches!(result, Err(CmgError::BuildStopped { reason: r, phase, chunk_id, charged_work })
                if r == reason && phase == expected.phase && chunk_id == expected.chunk_id && charged_work == expected.charged_work)
            );
            assert_eq!(seen, index + 1);
        }
    }
    let mut retry_trace = Vec::new();
    let retry = build(&graph, options, 200_000, &mut |event| {
        retry_trace.push(event);
        BuildDecision::Continue
    })
    .unwrap();
    assert_eq!(trace, retry_trace);
    assert_eq!(reference, retry);
    assert!(trace.iter().map(charge).sum::<u64>() <= 200_000);
}

fn dot(a: &[f64], b: &[f64]) -> f64 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}
fn close(a: &[f64], b: &[f64]) {
    let scale = a.iter().chain(b).fold(0.0_f64, |s, x| s.max(x.abs()));
    for (a, b) in a.iter().zip(b) {
        assert!(
            (a - b).abs() <= 1e-11 * scale,
            "{a} != {b} at scale {scale}"
        );
    }
}

#[test]
fn declined_weighted_cycles_remain_fixed_linear_symmetric_and_positive_on_the_range() {
    for disconnected in [false, true] {
        for scale in [1e-90, 1.0, 1e90] {
            let n = if disconnected { 50 } else { 24 };
            let edges = (0..if disconnected { 2 } else { 1 }).flat_map(|c| {
                (1..24).map(move |v| {
                    (
                        c * 24 + v - 1,
                        c * 24 + v,
                        scale
                            * if v == 12 {
                                1.0 / 65536.0
                            } else {
                                1.0 + (v % 5) as f64 / 4.0
                            },
                    )
                })
            });
            let graph = Laplacian::from_edges(n, edges).unwrap();
            let options = CmgOptions {
                max_levels: 8,
                ..Default::default()
            };
            let pre = build(&graph, options, 200_000, &mut |_| BuildDecision::Continue).unwrap();
            assert!(pre.hierarchy().levels().len() > 1);
            let retry = build(&graph, options, 200_000, &mut |_| BuildDecision::Continue).unwrap();
            assert_eq!(pre, retry);
            // Exact within-component dipoles are independently in the original
            // Laplacian range. They avoid contaminating the linearity witness
            // with a second floating-point matvec's cancellation error.
            // Check both nearby dipoles and dipoles crossing the weak edge.
            for (u_end, v_end) in [(1, 3), (23, 21)] {
                let mut u = vec![0.0; n];
                let mut v = vec![0.0; n];
                for c in 0..if disconnected { 2 } else { 1 } {
                    u[c * 24] = scale;
                    u[c * 24 + u_end] = -scale;
                    v[c * 24 + 2] = scale;
                    v[c * 24 + v_end] = -scale;
                }
                let mu = pre.apply(&u).unwrap();
                let mv = pre.apply(&v).unwrap();
                assert_eq!(bits(&mu), bits(&retry.apply(&u).unwrap()));
                assert_eq!(bits(&mu), bits(&pre.apply(&u).unwrap()));
                let input: Vec<_> = u.iter().zip(&v).map(|(a, b)| 1.25 * a - 0.75 * b).collect();
                let expected: Vec<_> = mu
                    .iter()
                    .zip(&mv)
                    .map(|(a, b)| 1.25 * a - 0.75 * b)
                    .collect();
                close(&pre.apply(&input).unwrap(), &expected);
                let left = dot(&u, &mv);
                let right = dot(&mu, &v);
                assert!((left - right).abs() <= 1e-11 * left.abs().max(right.abs()));
                assert!(dot(&u, &mu) > 0.0 && dot(&v, &mv) > 0.0);
                assert!(mu.iter().chain(&mv).all(|x| x.is_finite()));
                if disconnected {
                    assert_eq!(&mu[48..], &[0.0, 0.0]);
                }
            }
        }
    }
}
