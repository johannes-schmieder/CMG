from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_fused_task import task_roots  # noqa: E402
from tasks.generate_tasks import tasks as generate_tasks  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
