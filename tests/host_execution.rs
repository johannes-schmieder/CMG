#![cfg(feature = "parallel")]

use cmg::{CmgError, ControlledStage, HostExecutor, ParallelExecutor, ParallelOptions};
use std::sync::{
    Barrier,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};

fn stage(len: usize, chunk_len: usize) -> cmg::AdmittedStage {
    ControlledStage::admit(len, chunk_len).unwrap()
}

fn interrupted(error: CmgError) {
    assert!(matches!(error, CmgError::Cancelled { .. }));
}

#[test]
fn planning_rejects_invalid_chunks_and_never_overflows() {
    assert!(ControlledStage::admit(8, 0).is_err());
    // Chunk counts are derived by division; even the largest slice length
    // with one-element chunks is a valid geometry and scans nothing.
    assert!(ControlledStage::admit(usize::MAX, 1).is_ok());
}

#[test]
fn empty_stage_and_precancelled_stage_never_call_kernel() {
    let pool = ParallelExecutor::new(ParallelOptions {
        threads: 1,
        ..Default::default()
    })
    .unwrap();
    pool.with_installed(|team| {
        let empty = stage(0, 1);
        empty
            .run(
                &team,
                &mut [] as &mut [u32],
                || false,
                |_, _| panic!("empty"),
            )
            .unwrap();
        let mut values = [7; 2];
        interrupted(
            stage(2, 1)
                .run(&team, &mut values, || true, |_, _| panic!("cancelled"))
                .unwrap_err(),
        );
        assert_eq!(values, [7; 2]);
    })
    .unwrap();
}

#[test]
fn host_requires_its_exact_pool_and_positive_bounded_ceiling() {
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(4)
        .build()
        .unwrap();
    let foreign = rayon::ThreadPoolBuilder::new()
        .num_threads(2)
        .build()
        .unwrap();
    assert!(HostExecutor::new(&pool, 0).is_err());
    assert!(HostExecutor::new(&pool, 5).is_err());
    let host = HostExecutor::new(&pool, 2).unwrap();
    assert!(
        host.with_installed(|_| panic!("must reject first"))
            .is_err()
    );
    foreign.install(|| {
        assert!(host.with_installed(|_| panic!("wrong pool")).is_err());
    });
    pool.install(|| {
        // This controller is intentionally !Send: host callbacks do not migrate.
        let local = std::rc::Rc::new(std::cell::Cell::new(0));
        host.with_installed(|team| {
            assert_eq!(team.worker_ceiling(), 2);
            local.set(local.get() + 1);
        })
        .unwrap();
        assert_eq!(local.get(), 1);
    });
}

#[test]
fn stage_uses_host_workers_and_caps_terminal_lanes() {
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(4)
        .build()
        .unwrap();
    for ceiling in [1, 2, 4] {
        let active = AtomicUsize::new(0);
        let maximum = AtomicUsize::new(0);
        let rendezvous = Barrier::new(ceiling);
        let visits = AtomicUsize::new(0);
        let mut output = vec![usize::MAX; 4 * ceiling];
        pool.install(|| {
            HostExecutor::new(&pool, ceiling)
                .unwrap()
                .with_installed(|team| {
                    stage(output.len(), 1).run(
                        &team,
                        &mut output,
                        || false,
                        |chunk, values| {
                            assert!(pool.current_thread_index().is_some());
                            let now = active.fetch_add(1, Ordering::SeqCst) + 1;
                            maximum.fetch_max(now, Ordering::SeqCst);
                            // Each terminal lane owns four chunks; synchronize
                            // its first one to demonstrate the admitted ceiling.
                            if chunk % 4 == 0 {
                                rendezvous.wait();
                            }
                            values[0] = chunk;
                            visits.fetch_add(1, Ordering::SeqCst);
                            active.fetch_sub(1, Ordering::SeqCst);
                            Ok(())
                        },
                    )
                })
                .unwrap()
                .unwrap();
        });
        assert_eq!(maximum.load(Ordering::SeqCst), ceiling);
        assert_eq!(visits.load(Ordering::SeqCst), output.len());
        assert_eq!(output, (0..output.len()).collect::<Vec<_>>());
    }
}

#[test]
fn admitted_stage_rejects_mismatched_storage_before_writes() {
    let admitted = ControlledStage::admit(10, 3).unwrap();
    let pool = ParallelExecutor::new(ParallelOptions {
        threads: 4,
        ..Default::default()
    })
    .unwrap();
    let mut values = [7; 3];
    assert!(
        pool.with_installed(|team| admitted.run(
            &team,
            &mut values,
            || false,
            |_, _| { panic!("mismatched storage must reject before writes") }
        ))
        .unwrap()
        .is_err()
    );
    assert_eq!(values, [7; 3]);
}

#[test]
fn errors_are_canonical_and_all_chunks_join_before_return() {
    for threads in [1, 4] {
        let pool = ParallelExecutor::new(ParallelOptions {
            threads,
            ..Default::default()
        })
        .unwrap();
        let completed = AtomicUsize::new(0);
        let mut values = [0; 16];
        let result = pool
            .with_installed(|team| {
                stage(16, 1).run(
                    &team,
                    &mut values,
                    || false,
                    |chunk, values| {
                        values[0] = 1;
                        completed.fetch_add(1, Ordering::SeqCst);
                        match chunk {
                            2 => Err(CmgError::InvalidHierarchy {
                                context: "earliest",
                            }),
                            11 => Err(CmgError::InvalidHierarchy { context: "later" }),
                            _ => Ok(()),
                        }
                    },
                )
            })
            .unwrap();
        assert!(matches!(
            result,
            Err(CmgError::InvalidHierarchy {
                context: "earliest"
            })
        ));
        assert_eq!(completed.load(Ordering::SeqCst), 16);
        assert_eq!(values, [1; 16]);
    }
}

#[test]
fn cancellation_during_work_dominates_errors_and_joins() {
    let pool = ParallelExecutor::new(ParallelOptions {
        threads: 4,
        ..Default::default()
    })
    .unwrap();
    let cancel = AtomicBool::new(false);
    let active = AtomicUsize::new(0);
    let mut values = [0; 64];
    let result = pool
        .with_installed(|team| {
            stage(64, 1).run(
                &team,
                &mut values,
                || cancel.load(Ordering::Acquire),
                |chunk, values| {
                    active.fetch_add(1, Ordering::SeqCst);
                    values[0] = 1;
                    if chunk == 0 {
                        cancel.store(true, Ordering::Release);
                    }
                    active.fetch_sub(1, Ordering::SeqCst);
                    Err(CmgError::InvalidHierarchy {
                        context: "must lose to cancellation",
                    })
                },
            )
        })
        .unwrap();
    interrupted(result.unwrap_err());
    assert_eq!(active.load(Ordering::SeqCst), 0);
    assert_eq!(values[0], 1);
    assert!(values.contains(&0));
}

#[test]
fn panic_joins_sibling_lanes_before_unwinding() {
    let pool = ParallelExecutor::new(ParallelOptions {
        threads: 4,
        ..Default::default()
    })
    .unwrap();
    let barrier = Barrier::new(4);
    let completed = AtomicUsize::new(0);
    let mut values = [0; 4];
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        pool.with_installed(|team| {
            stage(4, 1).run(
                &team,
                &mut values,
                || false,
                |chunk, values| {
                    barrier.wait();
                    if chunk == 0 {
                        panic!("injected kernel panic");
                    }
                    values[0] = 1;
                    completed.fetch_add(1, Ordering::SeqCst);
                    Ok(())
                },
            )
        })
        .unwrap()
        .unwrap();
    }));
    assert!(result.is_err());
    assert_eq!(completed.load(Ordering::SeqCst), 3);
    assert_eq!(values, [0, 1, 1, 1]);
}

#[test]
fn owned_and_host_dispatch_same_kernel_without_retaining_output_storage() {
    fn kernel(chunk: usize, values: &mut [usize]) -> Result<(), CmgError> {
        for (offset, value) in values.iter_mut().enumerate() {
            *value = chunk * 3 + offset;
        }
        Ok(())
    }
    let host_pool = rayon::ThreadPoolBuilder::new()
        .num_threads(4)
        .build()
        .unwrap();
    let owned_pool = ParallelExecutor::new(ParallelOptions {
        threads: 4,
        ..Default::default()
    })
    .unwrap();
    let mut a = vec![0; 29];
    let mut b = vec![0; 29];
    let pointer = a.as_ptr();
    let capacity = a.capacity();
    host_pool
        .install(|| {
            HostExecutor::new(&host_pool, 4)
                .unwrap()
                .with_installed(|team| stage(29, 3).run(&team, &mut a, || false, kernel))
        })
        .unwrap()
        .unwrap();
    owned_pool
        .with_installed(|team| stage(29, 3).run(&team, &mut b, || false, kernel))
        .unwrap()
        .unwrap();
    assert_eq!(a, b);
    assert_eq!(a, (0..29).collect::<Vec<_>>());
    assert_eq!(pointer, a.as_ptr());
    assert_eq!(capacity, a.capacity());
    drop(host_pool);
    drop(owned_pool);
    a[0] = 99;
    assert_eq!(a[0], 99);
}
