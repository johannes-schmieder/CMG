"""Vendor provenance must detect edits, extra files and a different source pin."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from vendor import ROOT_FILES


class VendorTest(unittest.TestCase):
    def test_snapshot_and_fail_closed_checks(self):
        script = Path(__file__).with_name("vendor.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target, record = root / "source", root / "vendor", root / "source.json"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            for name in ROOT_FILES:
                (source / name).write_text(name + "\n")
            (source / "src").mkdir()
            (source / "src/lib.rs").write_text("pub fn solve() {}\n")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(["git", "-C", str(source), "-c", "user.name=Vendor Test",
                            "-c", "user.email=vendor@example.invalid", "commit", "-qm", "fixture"], check=True)
            common = ["--destination", str(target), "--record", str(record)]

            def run(action, *extra):
                return subprocess.run(["python3", str(script), action, *common, *extra],
                                      capture_output=True, text=True)

            self.assertEqual(run("refresh", "--source", str(source), "--rev", "HEAD").returncode, 0)
            self.assertEqual(run("check").returncode, 0)
            original = (target / "src/lib.rs").read_bytes()
            (target / "src/lib.rs").write_text("private patch\n")
            self.assertNotEqual(run("check").returncode, 0)
            (target / "src/lib.rs").write_bytes(original)
            (target / "extra.rs").write_text("extra\n")
            self.assertNotEqual(run("check").returncode, 0)
            (target / "extra.rs").unlink()
            data = json.loads(record.read_text())
            data["revision"] = "0" * 40
            record.write_text(json.dumps(data))
            self.assertNotEqual(run("check", "--source", str(source), "--rev", "HEAD").returncode, 0)


if __name__ == "__main__":
    unittest.main()
