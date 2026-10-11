import copy
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from service.analysis_service import AnalysisService


class WatchlistBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = sqlite3.connect(Path(self.temp.name) / "fixture.sqlite3")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.analysis = AnalysisService(SimpleNamespace(db=self.db))
        self.db.execute("CREATE TABLE untouched_cache (value TEXT)")
        self.db.execute("INSERT INTO untouched_cache VALUES ('keep market cache')")
        self.db.execute("INSERT INTO analysis_watchlist (symbol,code,name,status,note,tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", ("sh600900", "600900", "示例证券", "watching", "原备注", '["原标签"]', "2026-10-10T01:00:00+00:00", "2026-10-11T01:00:00+00:00"))
        self.db.commit()

    def tearDown(self):
        self.analysis.close()

    def call(self, action, **payload):
        return self.analysis.dispatch("market.watchlist.backup." + action, payload)

    def exported_items(self):
        return self.call("export")["items"]

    def incoming(self):
        backup = self.call("export")
        backup["items"][0].update(symbol="bj920001", code="920001", name="换机未建索引证券", status="archived", note="恢复备注", tags=["标签一", "标签二"], research={"page": "T04", "asOf": "2026-10-09", "parameters": {"minReturn20": 3}, "source": "fixture source"})
        return backup

    def test_export_preview_restore_roundtrip_without_market_index(self):
        original = self.call("export")
        incoming = self.incoming()
        summary = self.call("preview", backup=incoming)
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["archived"], 1)
        self.assertEqual(self.exported_items(), original["items"])
        result = self.call("restore", backup=incoming)
        self.assertEqual(self.exported_items(), incoming["items"])
        self.assertEqual(result["previousBackup"]["items"], original["items"])
        self.assertEqual(json.loads(self.db.execute("SELECT data FROM analysis_watchlist_previous").fetchone()[0])["items"], original["items"])
        self.assertEqual(self.db.execute("SELECT value FROM untouched_cache").fetchone()[0], "keep market cache")
        self.call("restore", backup=result["previousBackup"])
        self.assertEqual(self.exported_items(), original["items"])

    def test_all_invalid_rows_and_wrong_backup_types_preserve_original(self):
        original = self.exported_items()
        mutations = [lambda b: b.update(format="a-trade.accounts"), lambda b: b.update(schemaVersion=True),
                     lambda b: b["items"].append(copy.deepcopy(b["items"][0])),
                     lambda b: b["items"][0].update(code="000001"), lambda b: b["items"][0].update(status="invalid"),
                     lambda b: b["items"][0].update(tags=[{}]), lambda b: b["items"][0].update(note="x" * 2001),
                     lambda b: b["items"][0].update(research={"page": "T04", "parameters": {"bad": float("nan")}}),
                     lambda b: b["items"][0].update(createdAt="2027-01-01T00:00:00Z"),
                     lambda b: b["items"].append({"symbol": "sz000001"})]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                backup = self.incoming()
                mutate(backup)
                with self.assertRaises(ValueError):
                    self.call("restore", backup=backup)
                self.assertEqual(self.exported_items(), original)

    def test_sql_failure_rolls_back_deletion_and_previous_snapshot(self):
        self.db.execute("CREATE TRIGGER simulate_disk_error BEFORE INSERT ON analysis_watchlist BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
        self.db.commit()
        original = self.exported_items()
        with self.assertRaises(sqlite3.IntegrityError):
            self.call("restore", backup=self.incoming())
        self.assertEqual(self.exported_items(), original)
        self.assertIsNone(self.db.execute("SELECT name FROM sqlite_master WHERE name='analysis_watchlist_previous'").fetchone())

    def test_empty_backup_is_explicit_valid_replacement_and_previous_is_available(self):
        backup = self.call("export")
        original = backup["items"]
        backup["items"] = []
        self.assertEqual(self.call("preview", backup=backup)["total"], 0)
        result = self.call("restore", backup=backup)
        self.assertEqual(self.exported_items(), [])
        self.assertEqual(result["previousBackup"]["items"], original)


if __name__ == "__main__":
    unittest.main()
