//! Requested-allocation accounting for separate, serial instrumentation runs.
//! This counts System allocator layouts, not RSS or allocator arena overhead.
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicUsize, Ordering};

struct Tracker;
static LIVE: AtomicUsize = AtomicUsize::new(0);
static PEAK: AtomicUsize = AtomicUsize::new(0);
static CALLS: AtomicUsize = AtomicUsize::new(0);
#[global_allocator]
static GLOBAL: Tracker = Tracker;

fn add(bytes: usize) {
    let live = LIVE.fetch_add(bytes, Ordering::Relaxed) + bytes;
    PEAK.fetch_max(live, Ordering::Relaxed);
}
unsafe impl GlobalAlloc for Tracker {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let p = unsafe { System.alloc(layout) };
        if !p.is_null() {
            CALLS.fetch_add(1, Ordering::Relaxed);
            add(layout.size());
        }
        p
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        let p = unsafe { System.alloc_zeroed(layout) };
        if !p.is_null() {
            CALLS.fetch_add(1, Ordering::Relaxed);
            add(layout.size());
        }
        p
    }
    unsafe fn dealloc(&self, p: *mut u8, layout: Layout) {
        LIVE.fetch_sub(layout.size(), Ordering::Relaxed);
        unsafe { System.dealloc(p, layout) };
    }
    unsafe fn realloc(&self, p: *mut u8, layout: Layout, n: usize) -> *mut u8 {
        let q = unsafe { System.realloc(p, layout, n) };
        if !q.is_null() {
            CALLS.fetch_add(1, Ordering::Relaxed);
            if n >= layout.size() {
                add(n - layout.size());
            } else {
                LIVE.fetch_sub(layout.size() - n, Ordering::Relaxed);
            }
        }
        q
    }
}

pub(crate) struct Start {
    live: usize,
    calls: usize,
}
pub(crate) struct Sample {
    pub(crate) peak_additional: usize,
    pub(crate) live_additional: usize,
    pub(crate) allocations: usize,
}
pub(crate) fn begin() -> Start {
    let live = LIVE.load(Ordering::Relaxed);
    PEAK.store(live, Ordering::Relaxed);
    Start {
        live,
        calls: CALLS.load(Ordering::Relaxed),
    }
}
pub(crate) fn end(start: Start) -> Sample {
    Sample {
        peak_additional: PEAK.load(Ordering::Relaxed).saturating_sub(start.live),
        live_additional: LIVE.load(Ordering::Relaxed).saturating_sub(start.live),
        allocations: CALLS.load(Ordering::Relaxed) - start.calls,
    }
}
