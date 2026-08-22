fn main() {
    println!("cargo:rerun-if-changed=upstream/sspmv.c");
    println!("cargo:rerun-if-changed=upstream/rmvec.c");
    println!("cargo:rerun-if-changed=upstream/preconditioner_iterative.c");
    cc::Build::new()
        .files([
            "upstream/sspmv.c",
            "upstream/rmvec.c",
            "upstream/preconditioner_iterative.c",
        ])
        .flag_if_supported("-O3")
        .flag_if_supported("-std=c99")
        .compile("cmg_reference_kernels");
}
