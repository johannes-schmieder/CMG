from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_fused_task import is_fused_experiment, task_roots  # noqa: E402
from tasks.generate_tasks import FUSED_CPU_PROFILES, tasks as generate_tasks  # noqa: E402
from validate_fused_manifest import validate as validate_manifest  # noqa: E402


class FusedPathTests(unittest.TestCase):
    def test_smoke_and_full_task_ids_are_disjoint(self) -> None:
        run_root = Path("/projectnb/welfgr/cmg-benchmarks/runs/example")
        smoke = task_roots(run_root, "fused-smoke", 1)
        full = task_roots(run_root, "fused", 1)
        self.assertNotEqual(smoke, full)
        self.assertEqual(smoke[0], run_root / "output/fused-smoke/task-1")
        self.assertEqual(full[1], run_root / "receipts/fused/task-1")

    def test_fused_tasks_are_portable_and_target_28_core_hosts(self) -> None:
        smoke = generate_tasks("fused-smoke", {})
        full = generate_tasks("fused", {})
        self.assertEqual(len(smoke), 2)
        self.assertEqual(len(full), 12)
        for task in smoke + full:
            self.assertEqual(task["target_cpu"], "portable")
            self.assertEqual(task["slots"], 28)
            self.assertEqual(task["host_num_proc"], 28)
            self.assertEqual(task["host_cpu_type"], "E5-2680v4")
            self.assertEqual(task["cpu_model_contains"], "E5-2680 v4")

    def test_cpu_profiles_generate_isolated_smoke_and_screen_manifests(self) -> None:
        for profile, expected in FUSED_CPU_PROFILES.items():
            smoke = generate_tasks("fused-cpu-smoke", {}, profile)
            screen = generate_tasks("fused-cpu-screen", {}, profile)
            smoke_experiment = f"fused-cpu-smoke-{profile}"
            screen_experiment = f"fused-cpu-screen-{profile}"
            self.assertEqual(len(smoke), 1)
            self.assertEqual(len(screen), 4)
            self.assertTrue(is_fused_experiment(smoke_experiment))
            self.assertTrue(is_fused_experiment(screen_experiment))
            self.assertNotEqual(
                task_roots(Path("/run"), smoke_experiment, 1),
                task_roots(Path("/run"), screen_experiment, 1),
            )
            for task in smoke + screen:
                self.assertEqual(task["target_cpu"], "portable")
                for key, value in expected.items():
                    self.assertEqual(task[key], value)
            observed = {(task["family"], task["mode"], task["rhs_count"]) for task in screen}
            self.assertEqual(
                observed,
                {
                    ("worker-firm", "homogeneous", 16),
                    ("worker-firm", "mixed", 16),
                    ("dense-worker-firm", "homogeneous", 16),
                    ("dense-worker-firm", "mixed", 16),
                },
            )

    def test_cpu_profile_is_required_only_for_cpu_kinds(self) -> None:
        with self.assertRaises(ValueError):
            generate_tasks("fused-cpu-screen", {})
        with self.assertRaises(ValueError):
            generate_tasks("fused", {}, "gold-6242")

    def test_fused_experiment_names_reject_unsafe_namespaces(self) -> None:
        self.assertFalse(is_fused_experiment("fused-cpu-screen-../bad"))
        self.assertFalse(is_fused_experiment("fused-cpu-screen-"))

    def test_generated_cpu_manifests_validate_and_tampering_fails(self) -> None:
        profile = "gold-6242"
        rows = generate_tasks("fused-cpu-screen", {}, profile)
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / f"fused-cpu-screen-{profile}.jsonl"
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
            validate_manifest(manifest)
            rows[0]["slots"] = 28
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
            with self.assertRaises(SystemExit):
                validate_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
