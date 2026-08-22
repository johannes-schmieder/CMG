from pathlib import Path
import re

path = Path("src/ldl.rs")
text = path.read_text()

allocation = "        let mut matrix = vec![0.0; grounded_n * grounded_n];\n"
if text.count(allocation) != 1:
    raise SystemExit(
        f"dense matrix allocation anchor count {text.count(allocation)}"
    )
text = text.replace(allocation, "", 1)

edge_assignment = re.compile(
    r"(?m)^\s*matrix\[[^\n]+\]\s*-=\s*edge\.weight\(\);\s*\n"
)
text, removed_edge_assignments = edge_assignment.subn("", text)
if removed_edge_assignments != 2:
    raise SystemExit(
        f"expected two dense off-diagonal assignments, removed {removed_edge_assignments}"
    )

lines = text.splitlines(keepends=True)
diagonal_line = None
for index, line in enumerate(lines):
    if "matrix[" in line and "graph.diagonal()" in line:
        diagonal_line = index
        break
if diagonal_line is None:
    raise SystemExit("dense diagonal-assignment anchor not found")

loop_start = None
for index in range(diagonal_line, -1, -1):
    stripped = lines[index].lstrip()
    if stripped.startswith("for ") and "{" in stripped:
        loop_start = index
        break
if loop_start is None:
    raise SystemExit("dense diagonal loop start not found")

brace_depth = 0
loop_end = None
for index in range(loop_start, len(lines)):
    brace_depth += lines[index].count("{")
    brace_depth -= lines[index].count("}")
    if brace_depth == 0:
        loop_end = index + 1
        break
if loop_end is None:
    raise SystemExit("dense diagonal loop end not found")

loop_text = "".join(lines[loop_start:loop_end])
if "grounded_to_original" not in loop_text or "matrix[" not in loop_text:
    raise SystemExit("unexpected dense diagonal loop")
del lines[loop_start:loop_end]
text = "".join(lines)

factor_start_anchor = "        let mut factor = vec![0.0; grounded_n * grounded_n];\n"
factor_start = text.find(factor_start_anchor)
if factor_start < 0:
    raise SystemExit("dense factor allocation anchor not found")

factorization_markers = [
    "        for k in 0..grounded_n {\n",
    "        for pivot in 0..grounded_n {\n",
]
factorization_start = -1
for marker in factorization_markers:
    candidate = text.find(marker, factor_start + len(factor_start_anchor))
    if candidate >= 0 and (factorization_start < 0 or candidate < factorization_start):
        factorization_start = candidate
if factorization_start < 0:
    raise SystemExit("dense LDL factorization loop anchor not found")

old_permutation_block = text[factor_start:factorization_start]
if "matrix[" not in old_permutation_block or "order[" not in old_permutation_block:
    raise SystemExit("unexpected dense permutation block")

replacement = '''        let mut factor = vec![0.0; grounded_n * grounded_n];

        // Assemble the permuted grounded matrix directly in the factor
        // workspace. The previous implementation first built an unpermuted
        // dense matrix, then allocated a second dense matrix and copied all
        // n^2 entries through the ordering. Direct assembly removes one n^2
        // buffer and the permutation copy without changing factor arithmetic.
        for (grounded_vertex, &original_vertex) in
            grounded_to_original.iter().enumerate()
        {
            let permuted_vertex = inverse_order[grounded_vertex];
            factor[permuted_vertex * grounded_n + permuted_vertex] =
                graph.diagonal()[original_vertex];
        }

        let mut factor_grounded_index = vec![usize::MAX; graph.vertex_count()];
        for (grounded_vertex, &original_vertex) in
            grounded_to_original.iter().enumerate()
        {
            factor_grounded_index[original_vertex] = grounded_vertex;
        }
        for edge in graph.edges() {
            let grounded_u = factor_grounded_index[edge.u()];
            let grounded_v = factor_grounded_index[edge.v()];
            if grounded_u == usize::MAX || grounded_v == usize::MAX {
                continue;
            }
            let permuted_u = inverse_order[grounded_u];
            let permuted_v = inverse_order[grounded_v];
            factor[permuted_u * grounded_n + permuted_v] -= edge.weight();
            factor[permuted_v * grounded_n + permuted_u] -= edge.weight();
        }

'''
text = text[:factor_start] + replacement + text[factorization_start:]

if "let mut matrix" in text or "matrix[" in text:
    raise SystemExit("dense staging matrix reference remains after patch")

path.write_text(text)
