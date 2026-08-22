fn main() {
    println!("cargo:rerun-if-changed=upstream/sspmv.c");
    cc::Build::new()
        .file("upstream/sspmv.c")
        .flag_if_supported("-O3")
        .flag_if_supported("-std=c99")
        .compile("cmg_reference_sspmv");
}
