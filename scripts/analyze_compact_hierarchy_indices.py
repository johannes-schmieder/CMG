import json
from pathlib import Path
import re
import subprocess

ROOT = Path.cwd()
PREREQUISITE = Path(".ci/performance/parallel-pcg-vector-updates-latest.json")


def classify_reference(line, field):
    escaped = re.escape(field)
    if re.search(rf"\b{escaped}\s*:\s*Vec\s*<\s*usize\s*>", line):
        return "declaration"
    if re.search(rf"\b{escaped}\s*\[[^\]]+\]", line):
        if re.search(r"\]\s*=", line):
            return "indexed_write"
        return "indexed_read"
    if re.search(rf"\b{escaped}\s*\.\s*(?:push|extend|resize|reserve)", line):
        return "construction"
    if re.search(rf"\b{escaped}\b[^\n]*\.(?:iter|chunks|windows|par_iter)", line):
        return "iteration"
    if re.search(rf"for\s+[^\n]+\b{escaped}\b", line):
        return "iteration"
    if re.search(rf"\b{escaped}\b[^\n]*\.(?:clone|to_vec)", line):
        return "clone"
    if "pub fn" in line or "pub(crate) fn" in line or "pub struct" in line:
        return "public_context"
    if re.fullmatch(rf"\s*{escaped}\s*,?\s*", line) or re.match(
        rf"\s*{escaped}\s*:", line
    ):
        return "initialization"
    return "other"


def score(candidate):
    name = candidate["field"].lower()
    owner = candidate["struct"].lower()
    value = 0
    if "aggregate" in name or "aggregation" in owner:
        value += 220
    if "label" in name:
        value += 180
    if "component" in name:
        value += 150
    if "parent" in name:
        value += 100
    if "vertex" in name or "vertices" in name:
        value += 35
    if "column" in name or "neighbor" in name:
        value -= 140
    if "offset" in name:
        value -= 100
    if "report" in owner or "diagnostic" in owner or "workspace" in owner:
        value -= 120
    if candidate["visibility"]:
        value -= 80
    return value


prerequisite = json.loads(PREREQUISITE.read_text()) if PREREQUISITE.exists() else {}
if prerequisite.get("validation") != "success":
    print("vector-update prerequisite is unresolved; leaving layout analysis armed")
    raise SystemExit(0)

struct_pattern = re.compile(r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+(\w+)")
field_pattern = re.compile(
    r"(?m)^\s*(?P<vis>pub(?:\([^)]*\))?\s+)?(?P<field>\w+)\s*:\s*Vec\s*<\s*usize\s*>\s*,?\s*$"
)

candidates = []
for path in sorted(Path("src").glob("*.rs")):
    text = path.read_text()
    struct_positions = [(match.start(), match.group(1)) for match in struct_pattern.finditer(text)]
    for match in field_pattern.finditer(text):
        owners = [name for position, name in struct_positions if position < match.start()]
        owner = owners[-1] if owners else "module_local"
        candidate = {
            "path": path.as_posix(),
            "struct": owner,
            "field": match.group("field"),
            "visibility": (match.group("vis") or "").strip(),
            "declaration_line": text.count("\n", 0, match.start()) + 1,
        }
        candidate["score"] = score(candidate)
        candidates.append(candidate)

for candidate in candidates:
    references = []
    field = candidate["field"]
    pattern = re.compile(rf"\b{re.escape(field)}\b")
    for root in (Path("src"), Path("tests"), Path("benchmarks/src")):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.rs")):
            for line_number, line in enumerate(path.read_text().splitlines(), 1):
                if pattern.search(line):
                    references.append(
                        {
                            "path": path.as_posix(),
                            "line": line_number,
                            "category": classify_reference(line, field),
                            "source": line.strip(),
                        }
                    )
    candidate["references"] = references
    counts = {}
    for reference in references:
        category = reference["category"]
        counts[category] = counts.get(category, 0) + 1
    candidate["reference_counts"] = counts
    candidate["mechanically_bounded"] = (
        counts.get("declaration", 0) == 1
        and counts.get("public_context", 0) == 0
        and counts.get("other", 0) <= 4
    )

candidates.sort(key=lambda item: (item["score"], -len(item["references"])), reverse=True)
selected = next(
    (
        candidate
        for candidate in candidates
        if candidate["score"] > 0 and candidate["mechanically_bounded"]
    ),
    None,
)
result = {
    "schema_version": 1,
    "analysis": "compact-hierarchy-index-layout",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "prerequisite_source_sha": prerequisite.get("baseline_sha"),
    "selected": selected,
    "candidate_count": len(candidates),
    "candidates": candidates[:20],
}
record = Path(".ci/performance/compact-hierarchy-index-analysis.json")
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

selected_text = (
    f'`{selected["struct"]}.{selected["field"]}` in `{selected["path"]}`'
    if selected
    else "none"
)
checkpoint = f'''### Compact hierarchy-index analysis — 2026-08-23

- Selected mechanically bounded candidate: {selected_text}.
- Private `Vec<usize>` candidates examined: `{len(candidates)}`.
- No production source was changed by this analysis.
- Machine-readable evidence: `.ci/performance/compact-hierarchy-index-analysis.json`.

'''
plan_path = Path("PERFORMANCE_PLAN.md")
plan = plan_path.read_text()
heading = "### Compact hierarchy-index analysis — 2026-08-23\n"
marker = "## Current next action\n"
if heading in plan:
    start = plan.index(heading)
    end = plan.index(marker, start)
    plan = plan[:start] + checkpoint + plan[end:]
else:
    plan = plan.replace(marker, checkpoint + marker, 1)
plan_path.write_text(plan)

status_path = Path("PERFORMANCE_STATUS.md")
status = status_path.read_text().rstrip()
heading = "## Compact hierarchy-index analysis\n"
block = (
    "## Compact hierarchy-index analysis\n\n"
    f"- Selected candidate: {selected_text}.\n"
    f"- Candidates examined: `{len(candidates)}`.\n"
    "- No production numerical source changed.\n"
    "- Evidence: `.ci/performance/compact-hierarchy-index-analysis.json`.\n"
)
if heading in status:
    start = status.index(heading)
    end = status.find("\n## ", start + len(heading))
    if end == -1:
        end = len(status)
    status = status[:start] + block + status[end:]
else:
    status += "\n\n" + block
status_path.write_text(status.rstrip() + "\n")

Path(".github/workflows/analyze-compact-hierarchy-indices.yml").unlink(missing_ok=True)
Path("scripts/analyze_compact_hierarchy_indices.py").unlink(missing_ok=True)
