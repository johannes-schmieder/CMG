from pathlib import Path
import subprocess

SOURCE_COMMIT = "c3a4e891f6403060f0391c4632bbd0b022618709"
SOURCE_PATH = "scripts/profile_split_forest_subphases.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)

text = text.replace(
    "profile_split_forest_subphases.py",
    "profile_split_forest_subphases_v3.py",
)
text = text.replace(
    "profile-split-forest-subphases.yml",
    "profile-split-forest-subphases-v3.yml",
)
text = text.replace(
    "split-forest-subphase-profile-latest.json",
    "split-forest-subphase-profile-v3-latest.json",
)
text = text.replace(
    "split-forest-subphases-after-ownership",
    "split-forest-subphases-after-branchless-ancestor-recording",
)
text = text.replace(
    "### Forest-split subphase profile — 2026-08-24",
    "### Post-branchless forest-split subphase profile — 2026-08-24",
)
text = text.replace(
    "## Forest-split subphase profile",
    "## Post-branchless forest-split subphase profile",
)

bad_line = "                        visited.set(index, visited[index]);\n"
if text.count(bad_line) != 1:
    raise SystemExit("historical profiler no-op visit marker changed unexpectedly")
text = text.replace(bad_line, "", 1)

old_recording = '''                    if visited[current] {
                        new_ancestors.push(ancestors_in_path);
                    } else {
                        ancestors_in_path += 1;
                        new_ancestors.push(ancestors_in_path);
                    }
'''
new_recording = '''                    ancestors_in_path += i64::from(u8::from(!visited[current]));
                    new_ancestors.push(ancestors_in_path);
'''
if text.count(old_recording) != 1:
    raise SystemExit("historical ancestor-recording block changed unexpectedly")
text = text.replace(old_recording, new_recording, 1)

old_conductance = '''        let phase_start = std::time::Instant::now();
        let mut conductance_cuts = 0_usize;
        for start in 0..n {
            let mut current = start;
            let mut continue_walk = true;
            while continue_walk && indegree[current] == 0 {
                continue_walk = false;
                let mut previous = current;
                let mut cut_mode = false;
                let mut removed_ancestors = 0_i64;
                let mut new_front = current;

                loop {
                    let next = forest[current];
                    if next == current || next == previous {
                        break;
                    }
                    if !cut_mode
                        && ancestors[current] > 2
                        && ancestors[next] - ancestors[current] > 2
                    {
                        conductance_cuts += 1;
                        forest[current] = current;
                        indegree[next] = indegree[next]
                            .checked_sub(1)
                            .expect("forest indegree invariant");
                        removed_ancestors = ancestors[current];
                        new_front = next;
                        cut_mode = true;
                    }
                    previous = current;
                    current = next;
                    if cut_mode {
                        ancestors[current] -= removed_ancestors;
                    }
                }
                if cut_mode {
                    continue_walk = true;
                    current = new_front;
                }
            }
        }
        conductance_ns.push(phase_start.elapsed().as_nanos());
'''
new_conductance = '''        let phase_start = std::time::Instant::now();
        let mut conductance_cuts = 0_usize;
        for start in 0..n {
            let mut current = start;
            'new_front: while indegree[current] == 0 {
                let mut previous = current;
                loop {
                    let next = forest[current];
                    if next == current || next == previous {
                        break 'new_front;
                    }
                    if ancestors[current] > 2
                        && ancestors[next] - ancestors[current] > 2
                    {
                        conductance_cuts += 1;
                        forest[current] = current;
                        indegree[next] = indegree[next]
                            .checked_sub(1)
                            .expect("forest indegree invariant");
                        let removed_ancestors = ancestors[current];

                        let mut adjustment_previous = current;
                        let mut adjustment_current = next;
                        loop {
                            ancestors[adjustment_current] -= removed_ancestors;
                            let adjustment_next = forest[adjustment_current];
                            if adjustment_next == adjustment_current
                                || adjustment_next == adjustment_previous
                            {
                                break;
                            }
                            adjustment_previous = adjustment_current;
                            adjustment_current = adjustment_next;
                        }

                        current = next;
                        continue 'new_front;
                    }
                    previous = current;
                    current = next;
                }
            }
        }
        conductance_ns.push(phase_start.elapsed().as_nanos());
'''
if text.count(old_conductance) != 1:
    raise SystemExit("historical conductance profile block changed unexpectedly")
text = text.replace(old_conductance, new_conductance, 1)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
for stale in (
    ".github/workflows/profile-split-forest-subphases.yml",
    "scripts/profile_split_forest_subphases.py",
    ".github/workflows/profile-split-forest-subphases-v2.yml",
    "scripts/profile_split_forest_subphases_v2.py",
):
    Path(stale).unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("historical profiler cleanup block changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "profile_split_forest_subphases_v3.py",
    "profile-split-forest-subphases-v3.yml",
    "split-forest-subphase-profile-v3-latest.json",
    "ancestors_in_path += i64::from(u8::from(!visited[current]))",
    "'new_front: while indegree[current] == 0",
    "split subphase profiler diverged from production output",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"post-branchless split profiler missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(
    compile(text, str(Path(__file__)), "exec"),
    {"__name__": "__main__", "__file__": __file__},
)
