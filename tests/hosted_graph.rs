#![cfg(feature = "parallel")]

use cmg::{
    BuildBoundary, BuildDecision, BuildOperation, BuildStopReason, CmgError, CmgOptions,
    CmgPreconditioner, HostExecutor, Laplacian,
};
use std::sync::{
    Barrier,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};

const THRESHOLD: usize = 65_536;

// Duplicate endpoints, reversed inputs, unequal/tied weights, awkward tails,
// two disconnected components and isolated vertices. Weight order matters.
fn input(len: usize) -> Vec<(usize, usize, f64)> {
    (0..len)
        .map(|i| {
            let component = (i % 2) * 256;
            let u = component + (i * 73 % 255);
            let v = u + 1;
            let w = [1.0, 1e-12, 2.0, 1e9, 0.25, 1.0][(i / 7) % 6];
            if i % 3 == 0 { (v, u, w) } else { (u, v, w) }
        })
        .collect()
}

fn hosted(
    pool: &rayon::ThreadPool,
    ceiling: usize,
    edges: &[(usize, usize, f64)],
    memory: usize,
    work: u64,
    control: &mut (dyn cmg::BuildControl + Send),
) -> Result<Laplacian, CmgError> {
    pool.install(|| {
        HostExecutor::new(pool, ceiling)
            .unwrap()
            .with_installed(|team| {
                Laplacian::from_edges_hosted_controlled(
                    514,
                    edges,
                    memory,
                    work,
                    &team,
                    || false,
                    control,
                )
            })
            .unwrap()
    })
}

fn charge(trace: &[BuildBoundary]) -> u64 {
    trace
        .iter()
        .map(|b| match b.operation {
            BuildOperation::Work { units } => u64::from(units),
            _ => 0,
        })
        .sum()
}

#[test]
fn threshold_and_large_uneven_graph_preserve_bits_hierarchy_cycle_and_canonical_charges() {
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(4)
        .build()
        .unwrap();
    for len in [THRESHOLD - 1, THRESHOLD, THRESHOLD + 17] {
        let edges = input(len);
        let serial =
            Laplacian::from_edges_controlled(514, &edges, usize::MAX, u64::MAX, &mut |_| {
                BuildDecision::Continue
            })
            .unwrap();
        let options = CmgOptions {
            direct_threshold: 8,
            ..Default::default()
        };
        let old = CmgPreconditioner::build(&serial, options).unwrap();
        assert!(old.hierarchy().levels().len() > 1);
        let probe: Vec<_> = (0..514).map(|i| (i as f64 * 0.37).sin()).collect();
        let rhs = serial.matvec(&probe).unwrap();
        let expected = old.apply(&rhs).unwrap();
        let mut reference = None;
        for ceiling in [1, 2, 4] {
            for _ in 0..2 {
                let mut trace = Vec::new();
                let graph = hosted(&pool, ceiling, &edges, usize::MAX, u64::MAX, &mut |b| {
                    trace.push(b);
                    BuildDecision::Continue
                })
                .unwrap();
                assert_eq!(graph, serial);
                assert_eq!(
                    graph
                        .diagonal()
                        .iter()
                        .map(|v| v.to_bits())
                        .collect::<Vec<_>>(),
                    serial
                        .diagonal()
                        .iter()
                        .map(|v| v.to_bits())
                        .collect::<Vec<_>>()
                );
                assert_eq!(
                    trace.iter().any(|b| b.phase == "hosted input edge mapping"),
                    len >= THRESHOLD
                );
                let mut used = 0;
                for (ordinal, b) in trace.iter().enumerate() {
                    assert_eq!(b.chunk_id, ordinal as u64);
                    assert_eq!(b.charged_work, used);
                    if let BuildOperation::Work { units } = b.operation {
                        assert!((1..=1024).contains(&units));
                        used += u64::from(units);
                    }
                }
                if let Some(expected_trace) = &reference {
                    assert_eq!(&trace, expected_trace);
                } else {
                    reference = Some(trace);
                }
                let new = CmgPreconditioner::build(&graph, options).unwrap();
                assert_eq!(new.hierarchy(), old.hierarchy());
                assert_eq!(new.terminal_factor(), old.terminal_factor());
                assert_eq!(new.repeat_counts(), old.repeat_counts());
                assert_eq!(new.apply(&rhs).unwrap(), expected);
            }
        }
    }
}

#[test]
fn finite_work_and_memory_refusals_are_not_retried() {
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(4)
        .build()
        .unwrap();
    let edges = input(THRESHOLD + 17);
    let mut trace = Vec::new();
    hosted(&pool, 4, &edges, usize::MAX, u64::MAX, &mut |b| {
        trace.push(b);
        BuildDecision::Continue
    })
    .unwrap();
    let total = charge(&trace);
    let required = match hosted(&pool, 4, &edges, 0, u64::MAX, &mut |_| {
        BuildDecision::Continue
    }) {
        Err(CmgError::MemoryBudgetExceeded { required_bytes, .. }) => required_bytes,
        other => panic!("expected scratch refusal: {other:?}"),
    };
    // The existing serial bound is exactly the hosted bound.
    let serial_required =
        match Laplacian::from_edges_controlled(514, &edges, 0, u64::MAX, &mut |_| {
            BuildDecision::Continue
        }) {
            Err(CmgError::MemoryBudgetExceeded { required_bytes, .. }) => required_bytes,
            _ => panic!("expected serial bound"),
        };
    assert_eq!(required, serial_required);
    for ceiling in [1, 2, 4] {
        hosted(&pool, ceiling, &edges, required, total, &mut |_| {
            BuildDecision::Continue
        })
        .unwrap();
        assert!(matches!(
            hosted(&pool, ceiling, &edges, required - 1, total, &mut |_| {
                BuildDecision::Continue
            }),
            Err(CmgError::MemoryBudgetExceeded { .. })
        ));
        for budget in [0, 63, 191, total - 1] {
            assert!(matches!(
                hosted(&pool, ceiling, &edges, required, budget, &mut |_| {
                    BuildDecision::Continue
                }),
                Err(CmgError::BuildStopped {
                    reason: BuildStopReason::WorkBudgetExhausted,
                    ..
                })
            ));
        }
        // Stop at each phase's first boundary plus the final mapping admission.
        let mut stops = Vec::new();
        let mut phases = Vec::new();
        for (index, b) in trace.iter().enumerate() {
            if !phases.contains(&b.phase) {
                phases.push(b.phase);
                stops.push(index);
            }
        }
        stops.push(
            trace
                .iter()
                .rposition(|b| b.phase == "hosted input edge mapping")
                .unwrap(),
        );
        for stop in stops {
            let mut seen = Vec::new();
            let failure = hosted(
                &pool,
                ceiling,
                &edges,
                required,
                total,
                &mut |b: BuildBoundary| {
                    seen.push(b);
                    if b.chunk_id == stop as u64 {
                        BuildDecision::StopUnused
                    } else {
                        BuildDecision::Continue
                    }
                },
            )
            .unwrap_err();
            assert!(
                matches!(failure, CmgError::BuildStopped { reason: BuildStopReason::StopUnused,
                chunk_id, charged_work, .. } if chunk_id == stop as u64 && charged_work == trace[stop].charged_work)
            );
            assert_eq!(seen, trace[..=stop]);
        }
    }
    eprintln!(
        "hosted graph fixed witness: input={}, vertices=514, bound={}, work={}, boundaries={}",
        edges.len(),
        required,
        total,
        trace.len()
    );
}

#[test]
fn invalid_edge_errors_match_serial_order_across_workers() {
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(4)
        .build()
        .unwrap();
    for bad in [
        (514, 0, 1.0),
        (1, 1, 1.0),
        (0, 1, -2.0),
        (0, 1, f64::INFINITY),
    ] {
        let mut edges = input(THRESHOLD + 17);
        edges[97] = bad;
        edges[60_000] = (2, 2, 0.0);
        let serial =
            Laplacian::from_edges_controlled(514, &edges, usize::MAX, u64::MAX, &mut |_| {
                BuildDecision::Continue
            })
            .unwrap_err();
        for ceiling in [1, 2, 4] {
            assert_eq!(
                hosted(&pool, ceiling, &edges, usize::MAX, u64::MAX, &mut |_| {
                    BuildDecision::Continue
                })
                .unwrap_err(),
                serial
            );
        }
    }
}

#[test]
fn mapping_reaches_admitted_lanes_and_cancellation_or_panic_joins_them() {
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(4)
        .build()
        .unwrap();
    let edges = input(THRESHOLD);
    // The last mapping gate arms the test-only query, after serial admission.
    // Rendezvous verifies actual independent graph mapping lanes, not only an
    // empty framework stage. It deliberately strengthens the normal cheap query.
    for ceiling in [1, 2, 4] {
        for panic_first in [false, true] {
            let armed = AtomicBool::new(false);
            let calls = AtomicUsize::new(0);
            let arrived = AtomicUsize::new(0);
            let barrier = Barrier::new(ceiling);
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                pool.install(|| {
                    HostExecutor::new(&pool, ceiling)
                        .unwrap()
                        .with_installed(|team| {
                            let mut mapping_gates = 0;
                            Laplacian::from_edges_hosted_controlled(
                                514,
                                &edges,
                                usize::MAX,
                                u64::MAX,
                                &team,
                                || {
                                    if !armed.load(Ordering::Acquire) {
                                        return false;
                                    }
                                    let call = calls.fetch_add(1, Ordering::SeqCst);
                                    if call < ceiling {
                                        arrived.fetch_add(1, Ordering::SeqCst);
                                        barrier.wait();
                                        if panic_first && call == 0 {
                                            panic!("hosted mapping query panic");
                                        }
                                    }
                                    !panic_first && call >= ceiling
                                },
                                &mut |b: BuildBoundary| {
                                    if b.phase == "hosted input edge mapping" {
                                        mapping_gates += 1;
                                        if mapping_gates == 1 + 2 * THRESHOLD.div_ceil(8) {
                                            armed.store(true, Ordering::Release);
                                        }
                                    }
                                    BuildDecision::Continue
                                },
                            )
                        })
                        .unwrap()
                })
            }));
            assert_eq!(arrived.load(Ordering::SeqCst), ceiling);
            if panic_first {
                assert!(result.is_err());
            } else {
                assert!(matches!(
                    result.unwrap(),
                    Err(CmgError::BuildStopped {
                        reason: BuildStopReason::Interrupted,
                        ..
                    })
                ));
            }
            // Joined pool and all borrowed input remain usable after either exit.
            assert_eq!(pool.install(|| 7 + 9), 16);
        }
    }
}
