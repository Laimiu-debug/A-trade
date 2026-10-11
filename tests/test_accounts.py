import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from service.account_service import AccountService


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = AccountService(self.directory.name)
        self.workspace = self.service.dispatch("account.save", {
            "name": "测试账户", "kind": "paper", "startDate": "2026-01-01", "openingCash": 10000,
        })
        self.account_id = self.workspace["accountId"]

    def tearDown(self):
        self.service.close()
        self.directory.cleanup()

    def save(self, kind, **fields):
        return self.service.dispatch("ledger.save", {
            "accountId": self.account_id, "type": kind, "date": "2026-01-02", **fields,
        })

    def current(self):
        return self.service.dispatch("workspace", {"accountId": self.account_id})

    def test_new_account_has_real_empty_state_and_no_demo(self):
        state = self.current()
        self.assertEqual(state["ledger"], [])
        self.assertEqual(state["positions"], [])
        self.assertEqual(state["summary"]["cash"], 10000)
        self.assertEqual(state["summary"]["equity"], 10000)
        self.assertIsNone(state["summary"]["maxDrawdown"])

    def test_import_conflicts_require_resolution_and_keep_sources_in_backup(self):
        request = {"accountId": self.account_id, "fileName": "conflicts.csv",
                   "text": "date,type,code,quantity,price,amount,commission,fees\n2026-01-02,buy,600000,300,10.123,3037,5,20\n"}
        preview = self.service.dispatch("import.preview", request)
        entry = preview["rows"][0]["entry"]
        with self.assertRaisesRegex(ValueError, "差异"):
            self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": [entry]})
        self.assertEqual(self.current()["ledger"], [])
        entry["reconciliation"] = {"amount": {"resolution": "source"}, "fees": {"resolution": "source"}}
        state = self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": [entry]})
        trade = state["ledger"][0]
        self.assertEqual(trade["amount"], 3037)
        self.assertEqual(trade["fees"], 20)
        self.assertEqual(state["positions"][0]["cost"], 3057)
        self.assertEqual(trade["sourceValues"]["amount"], "3037.00")
        self.assertEqual(trade["sourceValues"]["feeComponents"]["commission"], "5.00")
        backup = self.service.dispatch("backup.export")
        self.service.dispatch("backup.restore", {"backup": backup})
        self.assertEqual(self.current()["ledger"][0]["sourceValues"], trade["sourceValues"])
        updated = self.service.dispatch("ledger.save", {"accountId": self.account_id, "id": trade["id"], "price": 11, "otherFees": 7})
        edited = updated["ledger"][0]
        self.assertEqual(edited["amount"], 3300)
        self.assertEqual(edited["fees"], 7)
        self.assertEqual(edited["sourceValues"], trade["sourceValues"])
        self.assertEqual(edited["reconciliation"]["fees"]["resolution"], "corrected")

    def test_readonly_asof_never_uses_future_trades_or_future_prices(self):
        self.save("buy", code="600000", quantity=100, price=10)
        self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-02", "prices": {"600000": 11}})
        self.save("sell", code="600000", quantity=100, price=12, date="2026-01-03")
        self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-04", "prices": {"600000": 20}})
        before = self.service.dispatch("backup.export")
        backup_before = self.service.previous_path.read_bytes()
        state = self.service.dispatch("account.asof", {"accountId": self.account_id, "date": "2026-01-02"})
        self.assertEqual(state["date"], "2026-01-02")
        self.assertEqual(len(state["ledger"]), 1)
        self.assertEqual(state["positions"][0]["quantity"], 100)
        self.assertEqual(state["positions"][0]["marketPrice"], 11)
        self.assertEqual(state["summary"]["cash"], 9000)
        self.assertEqual(state["summary"]["equity"], 10100)
        self.assertEqual(state["rounds"][0]["holdingDays"], 0)
        self.assertEqual(len(state["snapshots"]), 1)
        self.assertNotIn("accounts", state)
        self.assertEqual(self.service.previous_path.read_bytes(), backup_before)
        after = self.service.dispatch("backup.export")
        before.pop("exportedAt")
        after.pop("exportedAt")
        self.assertEqual(before, after)
        with self.assertRaisesRegex(ValueError, "起算"):
            self.service.dispatch("account.asof", {"accountId": self.account_id, "date": "2025-01-01"})

    def test_asof_missing_prices_remain_missing(self):
        self.save("buy", code="600000", quantity=100, price=10)
        self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-03", "prices": {"600000": 11}})
        state = self.service.dispatch("account.asof", {"accountId": self.account_id, "date": "2026-01-02"})
        self.assertIsNone(state["summary"]["equity"])
        self.assertEqual(state["summary"]["missingPrices"], ["600000"])

    def test_plan_execution_uses_only_effective_matching_fills_and_recomputes(self):
        state = self.save("buy", code="600000", quantity=100, price=10, commission=5)
        buy_id = state["ledger"][-1]["id"]
        state = self.save("buy", code="600001", quantity=100, price=10)
        wrong_code = state["ledger"][-1]["id"]
        state = self.save("sell", code="600000", quantity=50, price=12)
        wrong_side = state["ledger"][-1]["id"]
        state = self.save("buy", code="600000", quantity=50, price=11, date="2026-01-03")
        later_id = state["ledger"][-1]["id"]
        state = self.service.dispatch("plan.save", {"accountId": self.account_id, "date": "2026-01-02", "code": "600000",
                "quantity": 150, "action": "add", "status": "watching", "linkedTradeIds": [buy_id, wrong_code, wrong_side, later_id]})
        plan = state["plans"][0]
        execution = plan["execution"]
        self.assertEqual(plan["status"], "watching")
        self.assertEqual(execution["status"], "completed")
        self.assertEqual(execution["quantity"], 150)
        self.assertEqual(execution["averagePrice"], 10.3333)
        self.assertEqual(execution["fees"], 5)
        self.assertEqual(len(execution["warnings"]), 2)
        historical = self.service.dispatch("account.asof", {"accountId": self.account_id, "date": "2026-01-02"})["plans"][0]["execution"]
        self.assertEqual(historical["status"], "partial")
        self.assertEqual(historical["remainingQuantity"], 50)
        self.assertAlmostEqual(historical["completionPct"], 66.6667)
        state = self.service.dispatch("ledger.void", {"accountId": self.account_id, "id": later_id})
        self.assertEqual(state["plans"][0]["execution"]["quantity"], 100)
        self.assertTrue(any("作废" in warning for warning in state["plans"][0]["execution"]["warnings"]))
        state = self.service.dispatch("plan.save", {"accountId": self.account_id, "id": plan["id"], "quantity": 0})
        self.assertIsNone(state["plans"][0]["execution"]["completionPct"])
        self.assertEqual(state["plans"][0]["execution"]["status"], "unquantified")

    def test_round_cashflow_attribution_reconciles_profit_without_double_count(self):
        self.save("buy", code="600000", quantity=100, price=10)
        self.save("dividend", code="600000", amount=20, date="2026-01-03")
        self.save("fee", code="600000", amount=2, date="2026-01-03")
        state = self.save("sell", code="600000", quantity=100, price=9.9, date="2026-01-04")
        round_ = state["rounds"][0]
        self.assertEqual(round_["pricePnl"], -10)
        self.assertEqual(round_["dividendIncome"], 20)
        self.assertEqual(round_["standaloneFees"], 2)
        self.assertEqual(round_["totalPnl"], 8)
        self.assertEqual(round_["returnPct"], .8)
        self.assertEqual(len(round_["attributedCashIds"]), 2)
        self.assertEqual(state["summary"]["realizedPnl"], 8)
        self.assertEqual(state["summary"]["totalFees"], 2)
        self.assertEqual(state["roundsSummary"]["winRate"], 100)
        self.assertIsNone(state["roundsSummary"]["profitLossRatio"])
        self.assertEqual(state["roundsSummary"]["bySecurity"][0]["totalPnl"], 8)
        state = self.save("dividend", code="600000", amount=5, date="2026-01-05")
        self.assertEqual(state["summary"]["realizedPnl"], 13)
        self.assertEqual(state["rounds"][0]["totalPnl"], 8)
        self.assertEqual(len(state["attributionWarnings"]), 1)
        self.save("buy", code="600001", quantity=100, price=10, date="2026-01-06")
        state = self.save("sell", code="600001", quantity=100, price=9.96, date="2026-01-07")
        self.assertEqual(state["roundsSummary"]["profitLossRatio"], 2)
        self.assertEqual(state["roundsSummary"]["winRate"], 50)
        self.assertEqual(state["roundsSummary"]["averageHoldingDays"], 1.5)

    def test_empty_round_statistics_are_not_fake_observations(self):
        stats = self.current()["roundsSummary"]
        self.assertEqual(stats["completedCount"], 0)
        for key in ("winRate", "profitLossRatio", "averageHoldingDays", "totalPnl"):
            self.assertIsNone(stats[key])
        self.assertEqual(stats["bySecurity"], [])

    def test_snapshot_source_dates_survive_refresh_and_restore(self):
        state = self.save("buy", code="600000", quantity=100, price=10)
        buy_id = state["ledger"][0]["id"]
        payload = {"accountId": self.account_id, "date": "2026-01-04", "prices": {"600000": 11},
                   "priceDetails": {"600000": {"source": "local-daily", "priceDate": "2026-01-02", "requestedDate": "2026-01-04", "adjustment": "none", "stale": False}}}
        state = self.service.dispatch("snapshot.save", payload)
        details = state["positions"][0]["priceDetails"]
        self.assertTrue(details["stale"])
        self.assertEqual(details["priceDate"], "2026-01-02")
        self.assertEqual(state["snapshots"][0]["source"], "sourced-prices")
        state = self.service.dispatch("ledger.save", {"accountId": self.account_id, "id": buy_id, "price": 9})
        self.assertEqual(state["snapshots"][0]["priceDetails"]["600000"], details)
        exported = self.service.dispatch("backup.export")
        self.service.dispatch("backup.restore", {"backup": exported})
        self.assertEqual(self.current()["snapshots"][0]["priceDetails"]["600000"], details)
        for invalid in ({"priceDate": "2026-01-05"}, {"adjustment": "qfq"}, {"requestedDate": "2026-01-03"}):
            bad = copy.deepcopy(payload)
            bad["priceDetails"]["600000"].update(invalid)
            with self.assertRaises(ValueError):
                self.service.dispatch("snapshot.save", bad)

    def test_review_delete_releases_import_receipt_and_legacy_orphan_restore(self):
        payload = {"accountId": self.account_id, "kind": "reviews", "fileName": "2026-01-02-note.md", "text": "# 今日复盘\n按计划执行。"}
        preview = self.service.dispatch("import.preview", payload)
        state = self.service.dispatch("import.commit", {"accountId": self.account_id, "kind": "reviews", "rows": preview["rows"]})
        exported = self.service.dispatch("backup.export")
        self.service.dispatch("review.delete", {"accountId": self.account_id, "id": state["reviews"][0]["id"]})
        self.assertFalse(self.service.dispatch("import.preview", payload)["rows"][0]["duplicate"])
        exported["reviews"] = []
        self.service.dispatch("backup.restore", {"backup": exported})
        preview = self.service.dispatch("import.preview", payload)
        self.assertFalse(preview["rows"][0]["duplicate"])
        state = self.service.dispatch("import.commit", {"accountId": self.account_id, "kind": "reviews", "rows": preview["rows"]})
        self.assertEqual(len(state["reviews"]), 1)

    def test_review_score_readonly_and_unknown_rubric_rejected_at_save(self):
        before_count = self.service.db.execute("SELECT count(*) FROM audit").fetchone()[0]
        item_id = self.current()["reviewSchema"]["dimensions"][0]["items"][0]["id"]
        score = self.service.dispatch("review.score", {"review": {"scores": {item_id: {"score": 4, "evidence": "按计划执行"}}}})
        self.assertEqual(score["ratedCount"], 1)
        self.assertEqual(score["total"], score["score"])
        self.assertEqual(self.service.db.execute("SELECT count(*) FROM audit").fetchone()[0], before_count)
        with self.assertRaisesRegex(ValueError, "版本"):
            self.service.dispatch("review.save", {"accountId": self.account_id, "date": "2026-01-02", "rubricVersion": "future-v2"})
        with self.assertRaisesRegex(ValueError, "版本"):
            self.service.dispatch("review.score", {"rubricVersion": "future-v2", "scores": {}})

    def test_security_code_identity_is_shared_by_manual_import_plan_and_snapshot(self):
        state = self.save("buy", code="1", name="平安银行", quantity=100, price=10)
        self.assertEqual(state["ledger"][0]["code"], "000001")
        state = self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-02", "prices": {"SZ.1": 11}})
        self.assertEqual(state["summary"]["equity"], 10100)
        self.assertEqual(state["snapshots"][0]["prices"], {"000001": 11})
        state = self.service.dispatch("plan.save", {"accountId": self.account_id, "date": "2026-01-03", "code": "000001.SZ", "action": "sell"})
        self.assertEqual(state["plans"][0]["code"], "000001")
        preview = self.service.dispatch("import.preview", {"accountId": self.account_id, "fileName": "sell.csv",
            "text": "成交日期,证券代码,买卖方向,成交数量,成交价格,佣金\n20260103,000001,卖出,100,12,0\n"})
        state = self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": preview["rows"]})
        self.assertEqual(state["positions"], [])
        self.assertEqual(state["summary"]["realizedPnl"], 200)
        with self.assertRaisesRegex(ValueError, "证券代码"):
            self.save("buy", code="ABC", quantity=100, price=10)
        with self.assertRaisesRegex(ValueError, "证券代码"):
            self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-03", "prices": {"ABC": 10}})
        with self.assertRaisesRegex(ValueError, "重复填写"):
            self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-03", "prices": {"1": 10, "000001": 11}})

    def test_buy_partial_sell_fees_then_close_exactly(self):
        state = self.save("buy", code="600000", name="浦发银行", quantity=300, price="10.1234", commission="5", transferFee="0.03")
        self.assertEqual(state["summary"]["cash"], 6957.95)
        self.assertEqual(state["positions"][0]["cost"], 3042.05)
        self.assertIsNone(state["positions"][0]["marketValue"])
        self.assertIsNone(state["summary"]["equity"])
        state = self.save("sell", code="600000", quantity=100, price=12, commission=5, stampTax=.6)
        self.assertEqual(state["summary"]["cash"], 8152.35)
        self.assertEqual(state["summary"]["realizedPnl"], 180.38)
        self.assertEqual(state["positions"][0]["cost"], 2028.03)
        state = self.save("sell", code="600000", quantity=200, price=13, commission=5, stampTax=1.3)
        self.assertEqual(state["summary"]["cash"], 10746.05)
        self.assertEqual(state["summary"]["realizedPnl"], 746.05)
        self.assertEqual(state["summary"]["totalFees"], 16.93)
        self.assertEqual(state["positions"], [])
        self.assertEqual(len(state["rounds"]), 1)
        self.assertEqual(state["rounds"][0]["status"], "closed")
        self.assertEqual(state["rounds"][0]["realizedPnl"], 746.05)

    def test_new_buy_after_close_starts_separate_round(self):
        self.save("buy", code="600000", quantity=100, price=10)
        self.save("sell", code="600000", quantity=100, price=11)
        state = self.save("buy", code="600000", quantity=100, price=12)
        self.assertEqual(len(state["rounds"]), 2)
        self.assertEqual(state["rounds"][1]["status"], "open")
        self.assertEqual(state["positions"][0]["cost"], 1200)

    def test_oversell_and_insufficient_cash_are_atomic(self):
        before = self.current()
        with self.assertRaisesRegex(ValueError, "超过持仓"):
            self.save("sell", code="600000", quantity=1, price=10)
        with self.assertRaisesRegex(ValueError, "现金不足"):
            self.save("buy", code="600000", quantity=2000, price=10)
        self.assertEqual(self.current(), before)

    def test_historical_edit_or_void_replays_later_inventory(self):
        state = self.save("buy", code="600000", quantity=200, price=10)
        purchase = state["ledger"][0]
        self.save("sell", code="600000", quantity=150, price=12, date="2026-01-03")
        before = self.current()
        with self.assertRaisesRegex(ValueError, "超过持仓"):
            self.service.dispatch("ledger.void", {"accountId": self.account_id, "id": purchase["id"]})
        with self.assertRaisesRegex(ValueError, "超过持仓"):
            self.service.dispatch("ledger.save", {"accountId": self.account_id, "id": purchase["id"], "quantity": 100})
        self.assertEqual(self.current(), before)
        edited = self.service.dispatch("ledger.save", {"accountId": self.account_id, "id": purchase["id"], "price": 11})
        self.assertEqual(edited["summary"]["realizedPnl"], 150)
        self.assertEqual(edited["ledger"][0]["revision"], 2)

    def test_edit_last_fee_component_to_zero_replays_cost_cash_and_profit(self):
        state = self.save("buy", code="600000", quantity=100, price=10, commission=5)
        buy_id = state["ledger"][0]["id"]
        state = self.service.dispatch("ledger.save", {"accountId": self.account_id, "id": buy_id, "commission": 0})
        self.assertEqual(state["ledger"][0]["fees"], 0)
        self.assertEqual(state["ledger"][0]["otherFees"], 0)
        self.assertEqual(state["summary"]["cash"], 9000)
        self.assertEqual(state["positions"][0]["cost"], 1000)
        state = self.save("sell", code="600000", quantity=100, price=11, otherFees=5, date="2026-01-03")
        sell_id = state["ledger"][1]["id"]
        state = self.service.dispatch("ledger.save", {"accountId": self.account_id, "id": sell_id, "otherFees": 0})
        self.assertEqual(state["summary"]["cash"], 10100)
        self.assertEqual(state["summary"]["realizedPnl"], 100)
        self.assertEqual(state["summary"]["totalFees"], 0)

    def test_cash_flows_dividend_fee_separate_contributions_from_profit(self):
        self.save("deposit", amount=1000)
        self.save("withdrawal", amount=200)
        self.save("dividend", amount=50, code="600000")
        state = self.save("fee", amount=2)
        self.assertEqual(state["summary"]["cash"], 10848)
        self.assertEqual(state["summary"]["netContributions"], 10800)
        self.assertEqual(state["summary"]["realizedPnl"], 48)
        self.assertEqual(state["summary"]["totalFees"], 2)

    def test_opening_holdings_split_and_full_sale_preserve_cost(self):
        self.save("opening_position", code="600000", quantity=100, price=8)
        state = self.save("split", code="600000", quantity=100)
        self.assertEqual(state["summary"]["cash"], 10000)
        self.assertEqual(state["summary"]["netContributions"], 10800)
        self.assertEqual(state["positions"][0]["averageCost"], 4)
        state = self.save("sell", code="600000", quantity=200, price=5)
        self.assertEqual(state["summary"]["realizedPnl"], 200)
        self.assertEqual(state["summary"]["equity"], 11000)

    def test_accounts_are_isolated_and_archive_blocks_writes(self):
        self.save("buy", code="600000", quantity=100, price=10)
        other = self.service.dispatch("account.save", {"name": "第二账户", "kind": "real", "startDate": "2026-01-01", "openingCash": 500})
        self.assertNotEqual(other["accountId"], self.account_id)
        self.assertEqual(other["ledger"], [])
        first_entry = self.current()["ledger"][0]
        with self.assertRaisesRegex(ValueError, "不属于"):
            self.service.dispatch("ledger.void", {"accountId": other["accountId"], "id": first_entry["id"]})
        self.service.dispatch("account.archive", {"accountId": self.account_id})
        with self.assertRaisesRegex(ValueError, "归档"):
            self.save("deposit", amount=1)
        self.service.dispatch("account.archive", {"accountId": self.account_id, "archived": False})
        self.assertEqual(self.save("deposit", amount=1)["summary"]["cash"], 9001)

    def test_account_start_and_opening_cash_cannot_invalidate_ledger(self):
        self.save("buy", code="600000", quantity=100, price=10)
        with self.assertRaisesRegex(ValueError, "现金不足"):
            self.service.dispatch("account.save", {"id": self.account_id, "openingCash": 10})
        with self.assertRaisesRegex(ValueError, "起算日期"):
            self.service.dispatch("account.save", {"id": self.account_id, "startDate": "2026-02-01"})
        self.assertEqual(self.current()["accounts"][0]["openingCash"], 10000)

    def test_snapshot_missing_prices_historical_correction_and_stale_date(self):
        self.save("buy", code="600000", quantity=100, price=10)
        self.save("buy", code="000001", quantity=100, price=12)
        state = self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-02", "prices": {"600000": 11}})
        self.assertIsNone(state["summary"]["equity"])
        self.assertEqual(state["summary"]["missingPrices"], ["000001"])
        state = self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-02", "prices": {"600000": 11, "000001": 13}})
        self.assertEqual(state["summary"]["equity"], 10200)
        self.assertEqual(state["summary"]["returnPct"], 2)
        state = self.save("deposit", amount=100, date="2026-01-03")
        self.assertTrue(state["summary"]["valuationStale"])
        self.assertIsNone(state["summary"]["equity"])
        self.assertEqual(state["snapshots"][0]["summary"]["equity"], 10200)
        purchase = state["ledger"][0]
        state = self.service.dispatch("ledger.save", {"accountId": self.account_id, "id": purchase["id"], "price": 9})
        self.assertEqual(state["snapshots"][0]["summary"]["equity"], 10300)

    def test_drawdown_requires_comparable_snapshots(self):
        self.save("buy", code="600000", quantity=100, price=10)
        self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-02", "prices": {"600000": 12}})
        state = self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-03", "prices": {"600000": 10}})
        self.assertAlmostEqual(state["summary"]["maxDrawdown"], 1.9608, places=4)
        self.save("deposit", amount=100, date="2026-01-04")
        state = self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-04", "prices": {"600000": 11}})
        # A new capital segment must not discard the earlier comparable pair.
        self.assertAlmostEqual(state["summary"]["maxDrawdown"], 1.9608, places=4)
        self.assertEqual(state["summary"]["drawdownMethod"], "recorded-snapshots-flow-segmented")

    def test_drawdown_splits_equal_offsetting_cashflows(self):
        self.save("buy", code="600000", quantity=100, price=10)
        self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-02", "prices": {"600000": 10}})
        self.save("deposit", amount=500, date="2026-01-03")
        self.save("withdrawal", amount=500, date="2026-01-03")
        state = self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-04", "prices": {"600000": 9}})
        self.assertEqual(state["snapshots"][0]["summary"]["netContributions"], state["snapshots"][1]["summary"]["netContributions"])
        self.assertIsNone(state["summary"]["maxDrawdown"])
        state = self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-05", "prices": {"600000": 8}})
        self.assertAlmostEqual(state["summary"]["maxDrawdown"], 1.0101, places=4)

    def test_drawdown_missing_price_snapshot_breaks_continuity(self):
        self.save("buy", code="600000", quantity=100, price=10)
        self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-02", "prices": {"600000": 20}})
        self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-03", "prices": {}})
        state = self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-04", "prices": {"600000": 10}})
        self.assertIsNone(state["summary"]["maxDrawdown"])
        state = self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-05", "prices": {"600000": 9}})
        self.assertEqual(state["summary"]["maxDrawdown"], 1)

    def test_drawdown_ignores_voided_cashflows(self):
        self.save("buy", code="600000", quantity=100, price=10)
        self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-02", "prices": {"600000": 10}})
        state = self.save("deposit", amount=500, date="2026-01-03")
        self.service.dispatch("ledger.void", {"accountId": self.account_id, "id": state["ledger"][-1]["id"]})
        state = self.service.dispatch("snapshot.save", {"accountId": self.account_id, "date": "2026-01-04", "prices": {"600000": 9}})
        self.assertEqual(state["summary"]["maxDrawdown"], 1)

    def test_identical_partial_fills_not_deduped_but_same_file_row_is(self):
        rows = [{"rowNumber": n, "selected": True, "entry": {"type": "buy", "date": "2026-01-02", "code": "600000",
                "quantity": 100, "price": 10, "importKey": f"filehash:{n}", "source": "broker.csv"}} for n in (2, 3)]
        state = self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": rows})
        self.assertEqual(state["importResult"], {"imported": 2, "duplicates": 0})
        self.assertEqual(state["positions"][0]["quantity"], 200)
        state = self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": rows})
        self.assertEqual(state["importResult"], {"imported": 0, "duplicates": 2})
        self.assertEqual(len(state["ledger"]), 2)

    def test_external_execution_id_dedupes_across_files(self):
        entry = {"type": "buy", "date": "2026-01-02", "code": "600000", "quantity": 100, "price": 10, "externalId": "成交-1"}
        self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": [{"entry": dict(entry, importKey="file1:2")}]})
        state = self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": [{"entry": dict(entry, importKey="file2:2")}]})
        self.assertEqual(state["importResult"]["duplicates"], 1)

    def test_same_execution_id_on_different_trade_days_is_not_a_duplicate(self):
        self.save("buy", code="600000", quantity=100, price=10, externalId="当天成交001")
        state = self.save("buy", code="600000", quantity=100, price=11, externalId="当天成交001", date="2026-01-03")
        self.assertEqual(len(state["ledger"]), 2)
        self.assertEqual(state["positions"][0]["quantity"], 200)
        with self.assertRaisesRegex(ValueError, "记录重复"):
            self.save("buy", code="600000", quantity=100, price=11, externalId="当天成交001", date="2026-01-03")
        self.service.close()
        self.service = AccountService(self.directory.name)
        self.assertEqual(len(self.current()["ledger"]), 2)

    def test_import_invalid_later_row_rolls_back_whole_batch(self):
        rows = [{"entry": {"type": "buy", "date": "2026-01-02", "code": "600000", "quantity": 100, "price": 10}},
                {"entry": {"type": "sell", "date": "2026-01-03", "code": "600000", "quantity": 200, "price": 11}}]
        with self.assertRaisesRegex(ValueError, "超过持仓"):
            self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": rows})
        self.assertEqual(self.current()["ledger"], [])

    def test_missing_import_fees_need_explicit_confirmation(self):
        entry = {"type": "buy", "date": "2026-01-02", "code": "600000", "quantity": 100, "price": 10, "feeMissing": True}
        with self.assertRaisesRegex(ValueError, "缺少费用"):
            self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": [{"entry": entry}]})
        entry["feesConfirmed"] = True
        self.assertEqual(self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": [{"entry": entry}]})["importResult"]["imported"], 1)

    def test_ocr_cannot_commit_without_human_review(self):
        entry = {"type": "buy", "date": "2026-01-02", "code": "600000", "quantity": 100, "price": 10, "source": "ocr"}
        with self.assertRaisesRegex(ValueError, "人工核对"):
            self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": [{"entry": entry}]})
        entry["reviewed"] = True
        self.assertEqual(self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": [{"entry": entry}]})["importResult"]["imported"], 1)

    def test_same_ocr_image_dedupes_when_recognition_prose_changes(self):
        image_hash = "a" * 64
        entry = {"type": "buy", "date": "2026-01-02", "code": "600000", "name": "浦发银行", "quantity": 100, "price": 10,
            "commission": 5, "stampTax": 0, "transferFee": 0, "otherFees": 0,
            "source": "ocr", "importKey": f"ocr:{image_hash}:1", "sourceDetails": {"imageHash": image_hash}}
        first = self.service.dispatch("import.preview", {"accountId": self.account_id, "fileName": "recognition.json", "text": json.dumps({"source": "ocr", "rows": [entry]})})
        first["rows"][0]["entry"].update({"reviewed": True, "feesConfirmed": True})
        state = self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": first["rows"]})
        self.assertEqual(state["ledger"][0]["sourceDetails"]["imageHash"], image_hash)
        entry["name"] = "浦发银行 第二次识别的不同文字"
        second = self.service.dispatch("import.preview", {"accountId": self.account_id, "fileName": "recognition.json", "text": json.dumps({"source": "ocr", "rows": [entry]})})
        self.assertNotEqual(first["fileHash"], second["fileHash"])
        self.assertTrue(second["rows"][0]["duplicate"])
        self.assertFalse(second["rows"][0]["selected"])
        self.assertEqual(len(self.current()["ledger"]), 1)

    def test_partial_ocr_batch_reorder_or_missing_imported_row_blocks_whole_batch(self):
        image_hash = "b" * 64
        rows = [{"type": "buy", "date": "2026-01-02", "time": "10:00:00", "code": code, "quantity": 100, "price": 10,
            "commission": 0, "stampTax": 0, "transferFee": 0, "otherFees": 0, "source": "ocr",
            "importKey": f"ocr:{image_hash}:{index}", "sourceDetails": {"imageHash": image_hash}} for index, code in ((1, "000001"), (2, "600000"))]
        def preview(items):
            return self.service.dispatch("import.preview", {"accountId": self.account_id, "fileName": "ocr.json", "text": json.dumps({"source": "ocr", "rows": items})})
        first = preview(rows)
        first["rows"][0]["selected"] = False
        first["rows"][1]["entry"].update({"reviewed": True, "feesConfirmed": True})
        self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": first["rows"]})
        reordered = copy.deepcopy(rows)
        reordered[0]["code"], reordered[1]["code"] = reordered[1]["code"], reordered[0]["code"]
        conflicts = preview(reordered)
        self.assertTrue(all(row.get("conflict") for row in conflicts["rows"]))
        self.assertTrue(all(not row["selected"] for row in conflicts["rows"]))
        missing = preview(rows[:1])
        self.assertTrue(missing["rows"][0]["conflict"])
        self.assertIn("单独截图", missing["rows"][0]["errors"][-1])
        correct = preview(rows)
        self.assertFalse(correct["rows"][0].get("conflict", False))
        self.assertTrue(correct["rows"][0]["selected"])
        self.assertTrue(correct["rows"][1]["duplicate"])

    def test_partial_ocr_identical_legitimate_fills_keep_distinct_occurrences(self):
        image_hash = "c" * 64
        rows = [{"type": "buy", "date": "2026-01-02", "code": "600000", "quantity": 100, "price": 10,
            "commission": 0, "stampTax": 0, "transferFee": 0, "otherFees": 0, "source": "ocr",
            "importKey": f"ocr:{image_hash}:{index}", "sourceDetails": {"imageHash": image_hash}} for index in (1, 2)]
        payload = {"accountId": self.account_id, "fileName": "ocr.json", "text": json.dumps({"source": "ocr", "rows": rows})}
        first = self.service.dispatch("import.preview", payload)
        first["rows"][1]["selected"] = False
        first["rows"][0]["entry"].update({"reviewed": True, "feesConfirmed": True})
        self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": first["rows"]})
        second = self.service.dispatch("import.preview", payload)
        self.assertTrue(second["rows"][0]["duplicate"])
        self.assertTrue(second["rows"][1]["selected"])
        self.assertFalse(second["rows"][1].get("conflict", False))
        second["rows"][1]["entry"].update({"reviewed": True, "feesConfirmed": True})
        state = self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": second["rows"]})
        self.assertEqual(len(state["ledger"]), 2)
        self.assertEqual(state["positions"][0]["quantity"], 200)

    def test_voided_export_row_cannot_be_reactivated_by_import(self):
        for marker in (True, "已作废", "true", 1):
            entry = {"type": "buy", "date": "2026-01-02", "code": "600000", "quantity": 100, "price": 10, "voided": marker}
            with self.assertRaisesRegex(ValueError, "已作废"):
                self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": [{"entry": entry}]})
        self.assertEqual(self.current()["ledger"], [])

    def test_csv_preview_commit_correction_and_repeat_detection(self):
        preview = self.service.dispatch("import.preview", {"accountId": self.account_id, "fileName": "broker.csv",
            "text": "成交日期,证券代码,买卖方向,成交数量,成交价格,佣金,印花税,过户费,其他费用,成交编号\n20260102,600000,买入,100,10,5,0,0,0,1\n20260103,600000,卖出,200,12,5,0.6,0,0,2\n"})
        self.assertEqual(preview["rows"][0]["entry"]["code"], "600000")
        self.assertFalse(preview["rows"][0]["duplicate"])
        self.assertEqual(self.current()["ledger"], [])
        with self.assertRaisesRegex(ValueError, "超过持仓"):
            self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": preview["rows"]})
        preview["rows"][1]["entry"]["quantity"] = 100
        preview["rows"][1]["entry"]["amount"] = 1200
        state = self.service.dispatch("import.commit", {"accountId": self.account_id, "rows": preview["rows"]})
        self.assertEqual(state["summary"]["realizedPnl"], 189.4)
        self.assertEqual(state["ledger"][0]["sourceDetails"]["fileName"], "broker.csv")
        preview2 = self.service.dispatch("import.preview", {"accountId": self.account_id, "fileName": "broker.csv",
            "text": "成交日期,证券代码,买卖方向,成交数量,成交价格,佣金,印花税,过户费,其他费用,成交编号\n20260102,600000,买入,100,10,5,0,0,0,1\n"})
        self.assertTrue(preview2["rows"][0]["duplicate"])
        self.assertFalse(preview2["rows"][0]["selected"])

    def test_restart_preserves_accounts_ledger_and_audit(self):
        before = self.save("deposit", amount=333.33)
        self.service.close()
        self.service = AccountService(self.directory.name)
        self.assertEqual(self.current(), before)
        self.assertGreaterEqual(len(self.service.dispatch("backup.export")["audit"]), 2)

    def test_backup_roundtrip_and_invalid_restore_preserves_old_data(self):
        self.save("buy", code="600000", quantity=100, price=10)
        exported = self.service.dispatch("backup.export")
        self.save("sell", code="600000", quantity=100, price=11)
        state = self.service.dispatch("backup.restore", {"backup": json.dumps(exported), "accountId": self.account_id})
        self.assertEqual(len(state["ledger"]), 1)
        self.assertEqual(state["positions"][0]["quantity"], 100)
        broken = copy.deepcopy(exported)
        broken["ledger"][0]["type"] = "sell"
        before = self.current()
        with self.assertRaisesRegex(ValueError, "超过持仓"):
            self.service.dispatch("backup.restore", {"backup": broken})
        self.assertEqual(self.current(), before)
        duplicate = copy.deepcopy(exported)
        duplicate["ledger"].append(copy.deepcopy(duplicate["ledger"][0]))
        with self.assertRaisesRegex(ValueError, "重复流水"):
            self.service.dispatch("backup.restore", {"backup": duplicate})
        self.assertEqual(self.current(), before)

    def test_automatic_backup_recovery_keeps_damaged_file(self):
        self.save("deposit", amount=100)
        self.save("deposit", amount=200)
        self.service.close()
        (Path(self.directory.name) / "accounts.sqlite3").write_bytes(b"broken sqlite database")
        self.service = AccountService(self.directory.name)
        state = self.current()
        self.assertEqual(state["summary"]["cash"], 10100)
        self.assertIn("恢复", state["warning"])
        self.assertEqual(len(list(Path(self.directory.name).glob("accounts.damaged-*.sqlite3"))), 1)

    def test_future_schema_version_rejected_without_modification(self):
        self.service.close()
        db = sqlite3.connect(Path(self.directory.name) / "accounts.sqlite3")
        db.execute("PRAGMA user_version=99")
        db.close()
        with self.assertRaisesRegex(ValueError, "更新版本"):
            AccountService(self.directory.name)
        db = sqlite3.connect(Path(self.directory.name) / "accounts.sqlite3")
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 99)
        db.execute("PRAGMA user_version=1")
        db.close()
        self.service = AccountService(self.directory.name)

    def test_plan_crud_and_foreign_trade_link_rejected(self):
        state = self.service.dispatch("plan.save", {"accountId": self.account_id, "date": "2026-01-02", "code": "600000",
            "action": "buy", "quantity": 100, "trigger": "突破十元", "stopLoss": "跌破九元", "takeProfit": "达到十二元", "status": "ready"})
        plan = state["plans"][0]
        self.assertEqual(plan["stopLoss"], "跌破九元")
        state = self.service.dispatch("plan.save", {"accountId": self.account_id, "id": plan["id"], "status": "cancelled", "executionNotes": "条件失效"})
        self.assertEqual(state["plans"][0]["revision"], 2)
        self.assertEqual(self.service.dispatch("plan.delete", {"accountId": self.account_id, "id": plan["id"]})["plans"], [])
        with self.assertRaisesRegex(ValueError, "不属于"):
            self.service.dispatch("plan.save", {"accountId": self.account_id, "date": "2026-01-02", "code": "600000", "linkedTradeIds": ["wrong-account-trade"]})

    def test_detailed_review_score_is_recomputed_and_persisted(self):
        state = self.save("buy", code="600000", quantity=100, price=10)
        trade_id = state["ledger"][0]["id"]
        item_id = state["reviewSchema"]["dimensions"][0]["items"][0]["id"]
        state = self.service.dispatch("review.save", {"accountId": self.account_id, "kind": "trade", "date": "2026-01-02",
            "title": "首笔交易复盘", "fields": {"tradeRationale": "回看成交截图和计划"},
            "scores": {item_id: {"score": 4, "evidence": "交易前已写入计划并记录风险预算"}},
            "relatedTradeIds": [trade_id], "score": {"total": 100}})
        review = state["reviews"][0]
        self.assertEqual(review["score"]["ratedCount"], 1)
        self.assertLess(review["score"]["total"], 100)
        self.assertLess(review["score"]["coverage"], 100)
        self.assertEqual(review["relatedTradeIds"], [trade_id])
        self.service.close()
        self.service = AccountService(self.directory.name)
        self.assertEqual(self.current()["reviews"][0], review)
        self.assertEqual(self.service.dispatch("review.delete", {"accountId": self.account_id, "id": review["id"]})["reviews"], [])

    def test_review_markdown_preview_commit_and_backup_keep_deduplication(self):
        payload = {"accountId": self.account_id, "kind": "reviews", "fileName": "2026-01-02-复盘.md", "text": "# 今日复盘\n今日没有交易，等待条件明确。"}
        preview = self.service.dispatch("import.preview", payload)
        self.assertEqual(preview["kind"], "reviews")
        self.assertFalse(preview["rows"][0]["errors"])
        state = self.service.dispatch("import.commit", {"accountId": self.account_id, "kind": "reviews", "rows": preview["rows"]})
        self.assertEqual(len(state["reviews"]), 1)
        self.assertIn("等待条件", state["reviews"][0]["fields"]["importedText"])
        self.assertIsNone(state["reviews"][0]["score"]["total"])
        exported = self.service.dispatch("backup.export")
        self.service.dispatch("backup.restore", {"backup": exported})
        repeated = self.service.dispatch("import.preview", payload)
        self.assertTrue(repeated["rows"][0]["duplicate"])
        self.assertFalse(repeated["rows"][0]["selected"])

    def test_backup_cross_account_links_and_duplicate_entity_ids_are_rejected(self):
        state = self.save("buy", code="600000", quantity=100, price=10)
        trade_id = state["ledger"][0]["id"]
        other_id = self.service.dispatch("account.save", {"name": "隔离账户", "kind": "real", "startDate": "2026-01-01", "openingCash": 1000})["accountId"]
        self.service.dispatch("review.save", {"accountId": other_id, "date": "2026-01-02", "kind": "daily", "fields": {}, "scores": {}})
        exported = self.service.dispatch("backup.export")
        before = self.current()
        tampered = copy.deepcopy(exported)
        tampered["reviews"][0]["relatedTradeIds"] = [trade_id]
        with self.assertRaisesRegex(ValueError, "不属于"):
            self.service.dispatch("backup.restore", {"backup": tampered})
        self.assertEqual(self.current(), before)
        tampered = copy.deepcopy(exported)
        tampered["reviews"].append(dict(tampered["reviews"][0], accountId=self.account_id))
        with self.assertRaisesRegex(ValueError, "记录重复"):
            self.service.dispatch("backup.restore", {"backup": tampered})
        self.assertEqual(self.current(), before)
        tampered = copy.deepcopy(exported)
        tampered["ledger"].append(dict(tampered["ledger"][0], accountId=other_id))
        with self.assertRaisesRegex(ValueError, "重复流水"):
            self.service.dispatch("backup.restore", {"backup": tampered})
        self.assertEqual(self.current(), before)


if __name__ == "__main__":
    unittest.main()
