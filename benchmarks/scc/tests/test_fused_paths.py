from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_fused_task import task_roots  # noqa: E402


class FusedPathTests(unittest.TestCase):
    def test_smoke_and_full_task_ids_are_disjoint(self) -> None:
        run_root = Path("/projectnb/welfgr/cmg-benchmarks/runs/example")
        smoke = task_roots(run_root, "fused-smoke", 1)
        full = task_roots(run_root, "fused", 1)
        self.assertNotEqual(smoke, full)
        self.assertEqual(smoke[0], run_root / "output/fused-smoke/task-1")
        self.assertEqual(full[1], run_root / "receipts/fused/task-1")


if __name__ == "__main__":
    unittest.main()
