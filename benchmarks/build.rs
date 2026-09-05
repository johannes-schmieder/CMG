fn main() {
    println!("cargo:rerun-if-env-changed=CMG_BENCH_COMMIT");
    println!("cargo:rerun-if-env-changed=CMG_BENCH_ARCHIVE_SHA256");
}
