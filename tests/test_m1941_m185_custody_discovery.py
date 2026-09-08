from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest


TOOL = Path(__file__).resolve().parents[1] / "tools" / "discover_m185_custody_evidence.py"
SPEC = importlib.util.spec_from_file_location("discover_m185_custody_evidence", TOOL)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class M1941M185CustodyDiscoveryTests(unittest.TestCase):
    def test_json_identity_is_discovered_without_modification(self) -> None:
        with TemporaryDirectory() as root:
            path = Path(root) / "m174.json"
            payload = {
                "protocol": "dusty-m174-test-evidence",
                "strategy_fingerprint": "a" * 64,
                "robustness_fingerprint": "b" * 64,
                "status": "serious_challenger",
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            before = path.read_bytes()
            report = MODULE.discover((Path(root),))
            self.assertEqual(report["candidate_count"], 1)
            self.assertEqual(report["candidates"][0]["kind"], "json")
            self.assertEqual(
                report["candidates"][0]["identities"]["robustness_fingerprint"],
                "b" * 64,
            )
            self.assertEqual(path.read_bytes(), before)

    def test_irrelevant_json_is_ignored(self) -> None:
        with TemporaryDirectory() as root:
            (Path(root) / "other.json").write_text('{"hello":"world"}', encoding="utf-8")
            report = MODULE.discover((Path(root),))
            self.assertEqual(report["candidate_count"], 0)

    def test_sqlite_is_opened_read_only_and_matching_tables_are_reported(self) -> None:
        with TemporaryDirectory() as root:
            path = Path(root) / "evidence.db"
            db = sqlite3.connect(path)
            try:
                db.execute("CREATE TABLE robustness_certifications(id INTEGER PRIMARY KEY, payload TEXT)")
                db.commit()
            finally:
                db.close()
            before = path.read_bytes()
            report = MODULE.discover((Path(root),))
            self.assertEqual(report["candidate_count"], 1)
            self.assertEqual(report["candidates"][0]["kind"], "sqlite")
            self.assertIn(
                "robustness_certifications",
                report["candidates"][0]["matching_tables"],
            )
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
