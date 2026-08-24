from pathlib import Path
import subprocess

SOURCE_COMMIT = "2870771df17286578e03f68e83206bd8c869d02b"
SOURCE_PATH = "scripts/reuse_csr_row_counts_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace("import os\n", "import os\nimport re\n", 1)
text = text.replace(
    'WORKFLOW = Path(".github/workflows/reuse-csr-row-counts.yml")',
    'WORKFLOW = Path(".github/workflows/compact-csr-row-offsets.yml")',
    1,
)
text = text.replace(
    'SCRIPT = Path("scripts/reuse_csr_row_counts_gate.py")',
    'SCRIPT = Path("scripts/compact_csr_row_offsets_gate.py")',
    1,
)
text = text.replace(
    'RECORD = Path(".ci/performance/reuse-csr-row-counts-latest.json")',
    'RECORD = Path(".ci/performance/compact-csr-row-offsets-latest.json")',
    1,
)
text = text.replace(
    '"experiment": "reuse-csr-row-counts"',
    '"experiment": "compact-csr-row-offsets"',
    1,
)

old_median = '''fn median(mut values: Vec<u128>) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}
'''
new_median = '''fn median<T: Ord + Copy>(mut values: Vec<T>) -> T {
    values.sort_unstable();
    values[values.len() / 2]
}
'''
if text.count(old_median) != 1:
    raise SystemExit("CSR benchmark median helper changed unexpectedly")
text = text.replace(old_median, new_median, 1)

old_clippy = '''            "--",
            "D",
            "warnings",
'''
new_clippy = '''            "--",
            "-D",
            "warnings",
'''
if text.count(old_clippy) != 1:
    raise SystemExit("CSR benchmark Clippy marker changed unexpectedly")
text = text.replace(old_clippy, new_clippy, 1)

old_stable = '''        "operators",
        "plan_bytes",
        "threads",
'''
new_stable = '''        "operators",
        "threads",
'''
if text.count(old_stable) != 1:
    raise SystemExit("CSR plan stable-metadata marker changed unexpectedly")
text = text.replace(old_stable, new_stable, 1)

old_result_tail = '''    for field in (
        "median_ns",
        "median_additional_peak_bytes",
        "median_retained_bytes",
    ):
        baseline_value = statistics.median(item[field] for item in baseline_samples)
        candidate_value = statistics.median(item[field] for item in candidate_samples)
        result[f"baseline_{field}"] = baseline_value
        result[f"candidate_{field}"] = candidate_value
        result[f"candidate_over_baseline_{field}"] = candidate_value / baseline_value
    return result
'''
new_result_tail = '''    for field in (
        "median_ns",
        "median_additional_peak_bytes",
        "median_retained_bytes",
        "plan_bytes",
    ):
        baseline_value = statistics.median(item[field] for item in baseline_samples)
        candidate_value = statistics.median(item[field] for item in candidate_samples)
        result[f"baseline_{field}"] = baseline_value
        result[f"candidate_{field}"] = candidate_value
        result[f"candidate_over_baseline_{field}"] = candidate_value / baseline_value
    return result
'''
if text.count(old_result_tail) != 1:
    raise SystemExit("CSR plan result block changed unexpectedly")
text = text.replace(old_result_tail, new_result_tail, 1)

start = text.index("OLD_PREFIX =")
end = text.index("\n\ndef geometric", start)
source_patch = r'''ROW_OFFSETS_INSERT_MARKER = '''#[derive(Debug, Clone, PartialEq)]
enum ColumnIndices {
'''
ROW_OFFSETS_INSERT = '''#[derive(Debug, Clone, PartialEq)]
enum RowOffsets {
    Compact(Vec<u32>),
    Native(Vec<usize>),
}

impl RowOffsets {
    fn byte_len(&self) -> usize {
        match self {
            Self::Compact(values) => values.len().saturating_mul(core::mem::size_of::<u32>()),
            Self::Native(values) => values.len().saturating_mul(core::mem::size_of::<usize>()),
        }
    }

    const fn is_compact(&self) -> bool {
        matches!(self, Self::Compact(_))
    }

    fn row_count(&self) -> usize {
        match self {
            Self::Compact(values) => values.len().saturating_sub(1),
            Self::Native(values) => values.len().saturating_sub(1),
        }
    }

    fn last(&self) -> usize {
        match self {
            Self::Compact(values) => values.last().copied().unwrap_or(0) as usize,
            Self::Native(values) => values.last().copied().unwrap_or(0),
        }
    }

    #[inline]
    fn bounds(&self, row: usize) -> (usize, usize) {
        debug_assert!(row < self.row_count());
        match self {
            Self::Compact(values) => (values[row] as usize, values[row + 1] as usize),
            Self::Native(values) => (values[row], values[row + 1]),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
enum ColumnIndices {
'''
OLD_FIELD = '''    row_offsets: Vec<usize>,
'''
NEW_FIELD = '''    row_offsets: RowOffsets,
'''
OLD_BUILD = '''        let mut row_offsets = Vec::with_capacity(vertex_count + 1);
        row_offsets.push(0);
        for count in row_counts {
            let next = row_offsets
                .last()
                .copied()
                .unwrap_or(0_usize)
                .checked_add(count)
                .ok_or(CmgError::InvalidHierarchy {
                    context: "CSR row offsets overflowed usize",
                })?;
            row_offsets.push(next);
        }
        if row_offsets.last().copied().unwrap_or(0) != directed_entries {
            return Err(CmgError::InvalidHierarchy {
                context: "CSR row counts do not match directed-entry count",
            });
        }

        let mut next = row_offsets[..vertex_count].to_vec();
'''
NEW_BUILD = '''        let row_offsets = if directed_entries <= u32::MAX as usize {
            let mut offsets = Vec::with_capacity(vertex_count + 1);
            offsets.push(0_u32);
            let mut running = 0_usize;
            for count in row_counts {
                running = running
                    .checked_add(count)
                    .ok_or(CmgError::InvalidHierarchy {
                        context: "CSR row offsets overflowed usize",
                    })?;
                offsets.push(u32::try_from(running).map_err(|_| {
                    CmgError::InvalidHierarchy {
                        context: "CSR compact row offset exceeded u32::MAX",
                    }
                })?);
            }
            RowOffsets::Compact(offsets)
        } else {
            let mut offsets = Vec::with_capacity(vertex_count + 1);
            offsets.push(0_usize);
            for count in row_counts {
                let next = offsets
                    .last()
                    .copied()
                    .unwrap_or(0_usize)
                    .checked_add(count)
                    .ok_or(CmgError::InvalidHierarchy {
                        context: "CSR row offsets overflowed usize",
                    })?;
                offsets.push(next);
            }
            RowOffsets::Native(offsets)
        };
        if row_offsets.last() != directed_entries {
            return Err(CmgError::InvalidHierarchy {
                context: "CSR row counts do not match directed-entry count",
            });
        }

        let mut next = (0..vertex_count)
            .map(|row| row_offsets.bounds(row).0)
            .collect::<Vec<_>>();
'''
OLD_BYTE_LEN = '''        self.row_offsets
            .len()
            .saturating_mul(core::mem::size_of::<usize>())
            .saturating_add(self.columns.byte_len())
'''
NEW_BYTE_LEN = '''        self.row_offsets
            .byte_len()
            .saturating_add(self.columns.byte_len())
'''
OLD_COMPACT_METHOD = '''    pub const fn uses_compact_indices(&self) -> bool {
        self.columns.is_compact()
    }
'''
NEW_COMPACT_METHOD = '''    pub const fn uses_compact_indices(&self) -> bool {
        self.columns.is_compact()
    }

    /// Return whether row offsets use four-byte storage.
    #[must_use]
    pub const fn uses_compact_row_offsets(&self) -> bool {
        self.row_offsets.is_compact()
    }
'''
OLD_SORTED = '''fn rows_are_sorted(row_offsets: &[usize], columns: &ColumnIndices) -> bool {
    match columns {
        ColumnIndices::Compact(columns) => row_offsets.windows(2).all(|window| {
            columns[window[0]..window[1]]
                .windows(2)
                .all(|pair| pair[0] < pair[1])
        }),
        ColumnIndices::Native(columns) => row_offsets.windows(2).all(|window| {
            columns[window[0]..window[1]]
                .windows(2)
                .all(|pair| pair[0] < pair[1])
        }),
    }
}
'''
NEW_SORTED = '''fn rows_are_sorted(row_offsets: &RowOffsets, columns: &ColumnIndices) -> bool {
    (0..row_offsets.row_count()).all(|row| {
        let (start, end) = row_offsets.bounds(row);
        match columns {
            ColumnIndices::Compact(columns) => columns[start..end]
                .windows(2)
                .all(|pair| pair[0] < pair[1]),
            ColumnIndices::Native(columns) => columns[start..end]
                .windows(2)
                .all(|pair| pair[0] < pair[1]),
        }
    })
}
'''
OLD_TEST = '''        assert!(csr.uses_compact_indices());
        assert!(csr.byte_len() >= (graph.vertex_count() + 1) * core::mem::size_of::<usize>());
'''
NEW_TEST = '''        assert!(csr.uses_compact_indices());
        assert!(csr.uses_compact_row_offsets());
        assert!(csr.byte_len() >= (graph.vertex_count() + 1) * core::mem::size_of::<u32>());
'''
TEST_MODULE = '''

#[cfg(test)]
mod compact_row_offset_tests {
    use super::CsrLaplacian;
    use crate::Laplacian;

    #[test]
    fn compact_offsets_preserve_row_matvec() {
        let graph = Laplacian::from_edges(
            10,
            (0..9).map(|vertex| (vertex, vertex + 1, 1.0 + vertex as f64)),
        )
        .unwrap();
        let csr = CsrLaplacian::from_laplacian(&graph).unwrap();
        assert!(csr.uses_compact_row_offsets());
        let input: Vec<_> = (0..10).map(|index| index as f64 - 4.0).collect();
        assert_eq!(csr.matvec(&input).unwrap(), graph.matvec(&input).unwrap());
    }
}
'''


def apply_candidate(source):
    replacements = (
        (ROW_OFFSETS_INSERT_MARKER, ROW_OFFSETS_INSERT, "row-offset enum insertion"),
        (OLD_FIELD, NEW_FIELD, "row-offset field"),
        (OLD_BUILD, NEW_BUILD, "row-offset construction"),
        (OLD_BYTE_LEN, NEW_BYTE_LEN, "row-offset byte accounting"),
        (OLD_COMPACT_METHOD, NEW_COMPACT_METHOD, "compact-offset accessor"),
        (OLD_SORTED, NEW_SORTED, "row ordering validation"),
        (OLD_TEST, NEW_TEST, "compact storage test"),
    )
    candidate = source
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)

    loop_pattern = re.compile(
        r'(?m)^(?P<indent>\s*)for index in self\.row_offsets\[row\]\.\.self\.row_offsets\[row \+ 1\] \{$'
    )
    candidate, loop_count = loop_pattern.subn(
        lambda match: (
            f"{match.group('indent')}let (start, end) = self.row_offsets.bounds(row);\n"
            f"{match.group('indent')}for index in start..end {{"
        ),
        candidate,
    )
    if loop_count != 4:
        raise RuntimeError(f"expected four CSR row loops, found {loop_count}")

    bounds_pattern = '''                let start = self.row_offsets[row];
                let end = self.row_offsets[row + 1];
'''
    bounds_replacement = '''                let (start, end) = self.row_offsets.bounds(row);
'''
    if candidate.count(bounds_pattern) != 2:
        raise RuntimeError("expected two maximum-neighbor row bounds")
    candidate = candidate.replace(bounds_pattern, bounds_replacement)

    if "mod compact_row_offset_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate
'''
text = text[:start] + source_patch + text[end:]

text = text.replace("CSR row-count reuse", "Compact CSR row offsets")
text = text.replace("CSR row-count", "CSR row-offset")
text = text.replace("Reusing row counts as CSR insertion cursors", "Using checked four-byte CSR row offsets")
text = text.replace(
    'result["geometric_retained_ratio"] = geometric(retained_ratios)\n',
    'result["geometric_retained_ratio"] = geometric(retained_ratios)\n'
    '    plan_ratios = [\n'
    '        case["candidate_over_baseline_plan_bytes"]\n'
    '        for case in result["cases"].values()\n'
    '    ]\n'
    '    result["geometric_plan_bytes_ratio"] = geometric(plan_ratios)\n'
    '    result["worst_plan_bytes_ratio"] = max(plan_ratios)\n',
    1,
)

old_limits = '''        "geometric_time_ratio_max": 1.01,
        "worst_time_ratio_max": 1.04,
        "geometric_additional_peak_ratio_max": 0.95,
        "worst_additional_peak_ratio_max": 0.98,
        "geometric_retained_ratio_max": 1.001,
        "worst_retained_ratio_max": 1.001,
        "max_post_drop_delta_bytes": 0,
'''
new_limits = '''        "geometric_time_ratio_max": 1.02,
        "worst_time_ratio_max": 1.05,
        "geometric_additional_peak_ratio_max": 0.97,
        "worst_additional_peak_ratio_max": 0.99,
        "geometric_retained_ratio_max": 0.96,
        "worst_retained_ratio_max": 0.985,
        "geometric_plan_bytes_ratio_max": 0.96,
        "worst_plan_bytes_ratio_max": 0.985,
        "max_post_drop_delta_bytes": 0,
'''
if text.count(old_limits) != 1:
    raise SystemExit("CSR acceptance-limit block changed unexpectedly")
text = text.replace(old_limits, new_limits, 1)

old_acceptance = '''        result["geometric_time_ratio"] <= 1.01
        and result["worst_time_ratio"] <= 1.04
        and result["geometric_additional_peak_ratio"] <= 0.95
        and result["worst_additional_peak_ratio"] <= 0.98
        and result["geometric_retained_ratio"] <= 1.001
        and result["worst_retained_ratio"] <= 1.001
        and result["max_post_drop_delta_bytes"] == 0
'''
new_acceptance = '''        result["geometric_time_ratio"] <= 1.02
        and result["worst_time_ratio"] <= 1.05
        and result["geometric_additional_peak_ratio"] <= 0.97
        and result["worst_additional_peak_ratio"] <= 0.99
        and result["geometric_retained_ratio"] <= 0.96
        and result["worst_retained_ratio"] <= 0.985
        and result["geometric_plan_bytes_ratio"] <= 0.96
        and result["worst_plan_bytes_ratio"] <= 0.985
        and result["max_post_drop_delta_bytes"] == 0
'''
if text.count(old_acceptance) != 1:
    raise SystemExit("CSR acceptance expression changed unexpectedly")
text = text.replace(old_acceptance, new_acceptance, 1)

text = text.replace(
    "the CSR insertion cursor reuses the row-count allocation with lower plan-build peak memory",
    "checked compact row offsets reduced retained plan size and plan-build peak memory",
)
text = text.replace(
    "plan-build timing or exact-memory gates were not all met",
    "plan-build timing, compact-plan, or exact-memory gates were not all met",
)
text = text.replace(
    '"worst_retained_ratio",\n):',
    '"worst_retained_ratio",\n    "geometric_plan_bytes_ratio",\n    "worst_plan_bytes_ratio",\n):',
    1,
)
text = text.replace(
    '"perf: retain CSR row-offset reuse"',
    '"perf: retain compact CSR row offsets"',
)
text = text.replace(
    '"perf: record CSR row-offset reuse experiment"',
    '"perf: record compact CSR row-offset experiment"',
)

old_cleanup = "WORKFLOW.unlink(missing_ok=True)\nSCRIPT.unlink(missing_ok=True)\n"
new_cleanup = (
    "WORKFLOW.unlink(missing_ok=True)\n"
    "SCRIPT.unlink(missing_ok=True)\n"
    "for stale in (\n"
    "    \"scripts/reuse_csr_row_counts_gate.py\",\n"
    "    \"scripts/reuse_csr_row_counts_gate_v2.py\",\n"
    "    \"scripts/reuse_csr_row_counts_gate_v3.py\",\n"
    "):\n"
    "    Path(stale).unlink(missing_ok=True)\n"
)
if text.count(old_cleanup) != 1:
    raise SystemExit("compact CSR cleanup marker changed unexpectedly")
text = text.replace(old_cleanup, new_cleanup, 1)

required = (
    "compact_csr_row_offsets_gate.py",
    "compact-csr-row-offsets.yml",
    "enum RowOffsets",
    "uses_compact_row_offsets",
    "geometric_plan_bytes_ratio",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"compact CSR row-offset gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
