fn main() {
    println!("cargo:rerun-if-env-changed=CMG_BENCH_COMMIT");
}
