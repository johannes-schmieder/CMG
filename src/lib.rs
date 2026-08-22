/// Minimal smoke-test function for the initial CMG Rust scaffold.
pub fn add(left: i64, right: i64) -> i64 {
    left + right
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rust_toolchain_smoke_test() {
        assert_eq!(add(40, 2), 42);
    }
}
