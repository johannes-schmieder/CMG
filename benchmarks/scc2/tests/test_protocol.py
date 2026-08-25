from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tasks"))

from matrix import expand  # noqa: E402
from protocol import canonical_json, read_jsonl, validate_run_id, write_jsonl  # noqa: E402
from generate_tasks import tasks  # noqa: E402


class ProtocolTests(unittest.TestCase):
    def test_run_identity_is_strict(self) -> None:
        value = "20260824T120000Z-0123456789ab-b2v1-smoke"
        self.assertEqual(validate_run_id(value), value)
        with self.assertRaises(ValueError):
            validate_run_id("latest")

    def test_jsonl_round_trip_is_canonical(self) -> None:
        rows = [{"z": 1, "a": [3, 2, 1]}, {"value": 1.0e-8}]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rows.jsonl"
            write_jsonl(path, rows)
            self.assertEqual(read_jsonl(path), rows)
            self.assertEqual(path.read_text().splitlines()[0], canonical_json(rows[0]))

    def test_every_frozen_matrix_is_schema_valid_and_unique(self) -> None:
        schema = json.loads((ROOT / "schemas/task.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        optimal = {"path": 1, "worker-firm": 16, "dense-worker-firm": 32}
        for kind in (
            "smoke", "baseline", "routing", "reuse", "numa", "memory",
            "accuracy", "batch", "matched-edge",
        ):
            rows = tasks(kind, optimal)
            self.assertEqual([row["task_id"] for row in rows], list(range(1, len(rows) + 1)))
            for row in rows:
                validator.validate(row)
                identifiers = [config["configuration_id"] for config in expand(row)]
                self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_result_schema_accepts_normalized_fractional_timings(self) -> None:
        schema = json.loads((ROOT / "schemas/result.schema.json").read_text())
        sample_schema = schema["$defs"]["sample"]
        jsonschema.Draft202012Validator(sample_schema).validate(
            {
                "repetition": 1,
                "order_position": 5,
                "started_at_utc": "2026-08-25T20:00:00Z",
                "stage": "preconditioner_apply",
                "wall_ns": 2_371_740.234375,
                "process_cpu_ns": 2_400_000.5,
            }
        )


if __name__ == "__main__":
    unittest.main()
