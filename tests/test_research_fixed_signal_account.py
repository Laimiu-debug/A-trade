"""Synthetic account behavior; no frozen strategy or market sample is run."""
import ast
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import inspect
import json
from pathlib import Path
import textwrap
import unittest
from unittest.mock import patch

from service.portfolio_engine import PortfolioEngine, _digest
from service.research_fixed_signal_account import FixedSignalAccount, LegacyFeePolicy, DatedFeePolicy

SYMBOLS = ("sh600001", "sz000001", "sh600002")


def fixture(start="2023-08-14", length=16, symbols=SYMBOLS):
    cursor, days = date.fromisoformat(start), []
    while len(days) < length:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    securities = []
    for symbol in symbols:
        rows = [{"date": day, "open": 10., "high": 10., "low": 10., "close": 10.,
                 "volume": 1000000, "amount": 10000000.} for day in days]
        securities.append({"symbol": symbol, "bars": rows, "listingDate": "2010-01-01",
                           "stByDate": {day: False for day in days},
                           "tradeStatusByDate": {day: True for day in days},
                           "precloseByDate": {day: 10. for day in days}})
    return {"calendar": days, "calendarSource": "synthetic-independent", "securities": securities,
            "fingerprint": "synthetic-fixed-account", "adjustment": "raw"}


def settings(data, **overrides):
    return {"startDate": data["calendar"][5], "endDate": data["calendar"][-1],
            "initialCapital": 10000, "topN": 5, "maxPositions": 1, "maxStockWeight": 1,
            "rebalanceMode": "risk", "rebalanceEvery": 1, "switchThreshold": 0,
            "weighting": "equal", "commissionRate": 0, "minCommission": 0, "transferFeeRate": 0,
            "stampDutyRate": 0, "slippageBps": 0, "maxParticipationRate": .1,
            "stopLossPct": 0, "trailingStopPct": 0, "takeProfitPct": 0, "maxHoldingDays": 0,
            "strategies": [{"id": "fixed", "kind": "momentum", "pool": "base"}],
            "allocation": {"mode": "single", "strategyIds": ["fixed"]},
            "universe": {"strictHistory": False, "excludeSt": True, "minListingDays": 0, "minAmount20": 0},
            **overrides}


def signal(data, index=5, symbol=SYMBOLS[0], rank=1, ident=None):
    return {"signalId": ident or f"{index}:{symbol}", "strategyId": "fixed", "symbol": symbol,
            "signalDate": data["calendar"][index], "originalRank": rank}


def set_prices(data, symbol, closes, opens=None):
    security = next(row for row in data["securities"] if row["symbol"] == symbol)
    for index, bar in enumerate(security["bars"]):
        close = closes[index]
        opening = opens[index] if opens else close
        bar.update(open=opening, close=close, high=max(close, opening), low=min(close, opening), amount=bar["volume"] * close)
        security["precloseByDate"][bar["date"]] = closes[max(0, index - 1)]


def account(data, signals=None, config=None, weight=1., policy=None, state=None):
    return FixedSignalAccount(data, [signal(data)] if signals is None else signals,
                              settings(data) if config is None else config, entry_weight=weight,
                              fee_policy=LegacyFeePolicy() if policy is None else policy, state=state)


def complete(engine):
    engine.step(20000)
    return engine.result()


class FixedSignalAccountTests(unittest.TestCase):
    def test_no_real_scorer_exact_day_rank_gaps_and_no_final_liquidation(self):
        data = fixture()
        with patch("service.portfolio_engine.DailyScorer", side_effect=AssertionError("real scorer called")):
            engine = account(data, [signal(data, rank=4)])
            result = complete(engine)
        self.assertEqual([(t["side"], t["date"], t["quantity"]) for t in result["trades"]],
                         [("buy", data["calendar"][6], 1000)])
        self.assertEqual(result["daily"][0]["topNByStrategy"]["fixed"][0]["rank"], 4)
        self.assertEqual(result["daily"][1]["topNByStrategy"]["fixed"], [])
        self.assertEqual(result["positions"][0]["quantity"], 1000)
        self.assertFalse(result["certified"])
        self.assertFalse(result["quality"]["certified"])
        self.assertFalse(result["quality"]["riskKnown"])

    def test_signal_validation_rejects_ambiguous_or_outside_schedule(self):
        data = fixture()
        base = signal(data)
        bad = [[base, base], [{**base, "originalRank": True}], [{**base, "originalRank": 6}],
               [{**base, "symbol": "sh600999"}], [{**base, "strategyId": "other"}],
               [{**base, "signalDate": data["calendar"][4]}], [{**base, "extra": 1}],
               [base, {**signal(data, symbol=SYMBOLS[1]), "signalId": "other"}],
               [base, {**base, "signalId": "other", "originalRank": 2}]]
        for rows in bad:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                account(data, rows)

    def test_explicit_account_and_exit_rules_and_no_rotation(self):
        data = fixture()
        for key in ("startDate", "endDate", "takeProfitPct", "stopLossPct", "trailingStopPct",
                    "maxHoldingDays", "maxStockWeight", "initialCapital", "maxPositions"):
            config = settings(data)
            config.pop(key)
            with self.subTest(key=key), self.assertRaises(ValueError):
                account(data, config=config)
        for override in ({"rebalanceMode": "rank"}, {"rebalanceEvery": 2},
                         {"allocation": {"mode": "equal", "strategyIds": ["fixed"]}}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                account(data, config=settings(data, **override))
        for calendar in (None, [], "bad"):
            altered = deepcopy(data)
            altered["calendar"] = calendar
            with self.assertRaises(ValueError):
                account(altered, [signal(data)], settings(data))

    def test_future_price_and_volume_do_not_change_close_plan(self):
        data, changed = fixture(), fixture()
        row = changed["securities"][0]["bars"][6]
        row.update(open=10.4, high=10.8, low=9.9, close=10.2, volume=100, amount=1000)
        a, b = account(data), account(changed)
        a.step(1); b.step(1)
        self.assertEqual(a.research_state, b.research_state)
        self.assertEqual(a.state["pendingTarget"], b.state["pendingTarget"])

    def test_close_slippage_sizing_not_upper_limit_and_open_can_only_reduce(self):
        data = fixture()
        engine = account(data, config=settings(data, slippageBps=10))
        engine.step(1)
        op = engine.research_state["opportunities"][0]
        self.assertAlmostEqual(op["reservePrice"], 10.01)
        self.assertEqual(op["plannedQuantity"], 900)
        for opening, expected in ((9.5, 1000), (10.5, 900)):
            data = fixture()
            data["securities"][0]["bars"][6].update(open=opening, low=min(10, opening), high=max(10, opening))
            result = complete(account(data))
            self.assertEqual(result["trades"][0]["quantity"], expected)
            self.assertLessEqual(result["trades"][0]["quantity"], result["research"]["opportunities"][0]["plannedQuantity"])

    def test_original_rank_cash_priority_slot_limit_no_backfill(self):
        data = fixture()
        rows = [signal(data, symbol=SYMBOLS[0], rank=5), signal(data, symbol=SYMBOLS[1], rank=2),
                signal(data, symbol=SYMBOLS[2], rank=4)]
        result = complete(account(data, rows, settings(data, maxPositions=2), weight=.7))
        self.assertEqual([(t["symbol"], t["quantity"]) for t in result["trades"]], [(SYMBOLS[1], 700), (SYMBOLS[2], 300)])
        self.assertEqual([row["status"] for row in result["research"]["opportunities"]], ["filled", "partial", "notPlanned"])
        self.assertEqual(result["daily"][1]["buyAttemptOrder"], [rows[1]["signalId"], rows[2]["signalId"]])

    def test_missing_buy_once_and_no_later_retry(self):
        data = fixture()
        del data["securities"][0]["bars"][6]
        result = complete(account(data))
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["research"]["opportunities"][0]["status"], "expired")
        self.assertEqual(result["research"]["opportunities"][0]["attemptDate"], data["calendar"][6])
        self.assertEqual(result["research"]["opportunities"][0]["reason"], "missingBar")

    def test_partial_buy_remainder_expires(self):
        data = fixture()
        data["securities"][0]["bars"][5]["volume"] = 1500
        result = complete(account(data))
        self.assertEqual([t["quantity"] for t in result["trades"]], [100])
        op = result["research"]["opportunities"][0]
        self.assertEqual((op["status"], op["plannedQuantity"], op["unfilledQuantity"]), ("partial", 1000, 900))
        self.assertEqual(len(result["research"]["feeLedger"]["paidLedger"]), 1)
        self.assertGreater(len(result["research"]["feeLedger"]["quotes"]), 1)

    def test_untradable_selected_open_does_not_backfill(self):
        data = fixture()
        data["securities"][0]["bars"][6].update(open=11, high=11, low=10)
        rows = [signal(data), signal(data, symbol=SYMBOLS[1], rank=2)]
        result = complete(account(data, rows))
        self.assertEqual(result["trades"], [])
        self.assertEqual([row["status"] for row in result["research"]["opportunities"]], ["expired", "notPlanned"])

    def test_retained_quantity_no_add_no_trim_after_appreciation(self):
        data = fixture()
        prices = [10.] * 7 + [10.3] * 9
        set_prices(data, SYMBOLS[0], prices)
        rows = [signal(data), signal(data, index=7)]
        result = complete(account(data, rows, settings(data, maxStockWeight=.5), weight=.9))
        self.assertEqual([t["quantity"] for t in result["trades"]], [500])
        self.assertEqual(result["positions"][0]["quantity"], 500)
        self.assertEqual(result["research"]["opportunities"][1]["reason"], "alreadyHeldNoAdd")
        self.assertTrue(all(row["action"] == "hold" for row in result["daily"][-1]["nextSessionPlan"]["rows"]))

    def test_failed_and_partial_exit_persists_through_rebound(self):
        data = fixture()
        closes = [10.] * 7 + [9.4, 9.6, 9.7] + [9.8] * 6
        opens = [10.] * 8 + [9.4, 9.6] + [9.8] * 6
        set_prices(data, SYMBOLS[0], closes, opens)
        security = data["securities"][0]
        security["tradeStatusByDate"][data["calendar"][8]] = False
        security["bars"][8]["volume"] = 3000
        result = complete(account(data, config=settings(data, stopLossPct=.05)))
        self.assertEqual([(t["side"], t["date"], t["quantity"]) for t in result["trades"]],
                         [("buy", data["calendar"][6], 1000), ("sell", data["calendar"][9], 300), ("sell", data["calendar"][10], 700)])
        self.assertTrue(all(t["signalDate"] == data["calendar"][7] for t in result["trades"][1:]))
        self.assertEqual(result["forcedExits"], {})
        self.assertEqual(len(result["closedTrades"]), 1)

    def test_pending_exit_provisional_slot_needs_actual_sale_and_cash(self):
        for halted in (False, True):
            data = fixture()
            set_prices(data, SYMBOLS[0], [10.] * 7 + [9.4] * 9, [10.] * 8 + [9.4] * 8)
            if halted:
                data["securities"][0]["tradeStatusByDate"][data["calendar"][8]] = False
            rows = [signal(data), signal(data, index=7, symbol=SYMBOLS[1])]
            result = complete(account(data, rows, settings(data, stopLossPct=.05)))
            replacement = result["research"]["opportunities"][1]
            if halted:
                self.assertEqual(replacement["reason"], "positionSlotsOccupied")
                self.assertEqual(replacement["status"], "expired")
                self.assertFalse(any(t["symbol"] == SYMBOLS[1] for t in result["trades"]))
            else:
                trades = [t for t in result["trades"] if t["date"] == data["calendar"][8]]
                self.assertEqual([t["side"] for t in trades], ["sell", "buy"])
                self.assertEqual(trades[1]["quantity"], 900)
                self.assertEqual(trades[1]["cashAfter"], 400)

    def test_same_session_exit_cannot_reenter_at_its_close(self):
        data = fixture()
        rows = [signal(data), signal(data, index=7)]
        result = complete(account(data, rows, settings(data, maxHoldingDays=1)))
        self.assertEqual([t["side"] for t in result["trades"]], ["buy", "sell"])
        self.assertEqual(result["trades"][1]["date"], data["calendar"][7])
        self.assertEqual(result["research"]["opportunities"][1]["reason"], "soldTodayOrQuarantined")

    def test_holding_clock_calendar_including_entry_and_missing_defers_trigger(self):
        data = fixture()
        for index in (7, 8):
            data["securities"][0]["bars"][index]["volume"] = 0
        result = complete(account(data, config=settings(data, maxHoldingDays=2)))
        sells = [t for t in result["trades"] if t["side"] == "sell"]
        self.assertEqual(sells[0]["signalDate"], data["calendar"][9])
        self.assertEqual(sells[0]["riskEvidence"]["holdingDays"], 4)
        self.assertGreater(sells[0]["date"], result["trades"][0]["date"])

    def test_cutoff_censored_opportunity_and_stale_open_position(self):
        data = fixture()
        last = len(data["calendar"]) - 1
        del data["securities"][0]["bars"][-1]
        rows = [signal(data), signal(data, index=last, symbol=SYMBOLS[1])]
        result = complete(account(data, rows, settings(data, maxPositions=2), weight=.5))
        self.assertEqual(result["research"]["opportunities"][1]["status"], "censored")
        self.assertEqual(result["curve"][-1]["staleSymbols"], [SYMBOLS[0]])
        self.assertEqual(len(result["positions"]), 1)
        self.assertEqual(result["positions"][0]["priceDate"], data["calendar"][-2])

    def test_dated_transfer_signal_reserve_before_april_boundary_never_increases(self):
        data = fixture(start="2022-04-21", length=9)
        self.assertEqual(data["calendar"][5:7], ["2022-04-28", "2022-04-29"])
        policy = DatedFeePolicy(commission_rate=0, min_commission=0, commission_includes_transfer=False, stamp_duty_override=0)
        config = settings(data, initialCapital=10000.15, transferFeeRate=.00001)
        result = complete(account(data, config=config, policy=policy))
        self.assertEqual(result["research"]["opportunities"][0]["plannedQuantity"], 900)
        self.assertEqual(result["trades"][0]["quantity"], 900)
        self.assertAlmostEqual(result["trades"][0]["fee"], .09)
        reserve = next(q for q in result["research"]["feeLedger"]["quotes"] if q["context"]["purpose"] == "signalReserve")
        self.assertEqual(reserve["rates"]["transfer"], .00002)
        self.assertEqual(result["research"]["feeLedger"]["paidLedger"][0]["quote"]["rates"]["transfer"], .00001)

    def test_dated_profile_rejects_unsupported_empty_interval(self):
        data = fixture(start="2024-01-02")
        policy = DatedFeePolicy(commission_rate=0, min_commission=0, commission_includes_transfer=False, stamp_duty_override=0)
        with self.assertRaises(ValueError):
            account(data, [], settings(data, transferFeeRate=.00001), policy=policy)

    def test_stamp_duty_actual_partial_sell_dates_and_zero_override(self):
        data = fixture(start="2023-08-16", length=10)
        self.assertEqual(data["calendar"][5:9], ["2023-08-23", "2023-08-24", "2023-08-25", "2023-08-28"])
        data["securities"][0]["bars"][6]["volume"] = 3500
        for override in (None, 0):
            config = settings(data, maxHoldingDays=1, stampDutyRate=override, minCommission=5)
            result = complete(account(data, config=config))
            sales = [t for t in result["trades"] if t["side"] == "sell"]
            self.assertEqual([t["quantity"] for t in sales], [300, 600])
            self.assertEqual([t["fee"] for t in sales], [8., 8.] if override is None else [5., 5.])
            self.assertEqual(len(result["research"]["feeLedger"]["paidLedger"]), 3)
            self.assertEqual(result["research"]["feeLedger"]["paidFeeTotal"], result["metrics"]["totalFees"])

    def test_hand_cash_cost_pnl_and_slippage_not_charged_twice(self):
        data = fixture()
        set_prices(data, SYMBOLS[0], [10.] * 7 + [10.5] * 9)
        result = complete(account(data, config=settings(data, maxHoldingDays=1, minCommission=5)))
        buy, sell = result["trades"]
        self.assertEqual((buy["quantity"], buy["amount"], buy["fee"], buy["cashAfter"]), (900, 9000, 5, 995))
        self.assertEqual((sell["amount"], sell["fee"], sell["cashAfter"], sell["realizedPnl"]), (9450, 5, 10440, 440))
        self.assertEqual(result["closedTrades"][0]["pnlAmount"], 440)
        self.assertEqual(result["metrics"]["finalEquity"], 10440)
        slipped = complete(account(fixture(), config=settings(fixture(), slippageBps=10)))
        self.assertAlmostEqual(slipped["curve"][1]["cash"] + slipped["curve"][1]["marketValue"], 9991.)
        self.assertAlmostEqual(slipped["metrics"]["totalSlippage"], 9.)

    def test_checkpoint_resume_matches_all_account_and_sidecar_evidence(self):
        data = fixture()
        rows = [signal(data), signal(data, index=9, symbol=SYMBOLS[1], rank=3)]
        config = settings(data, maxHoldingDays=2, minCommission=5)
        full = account(data, rows, config)
        expected = complete(full)
        split = account(data, rows, config)
        split.step(3)
        state = json.loads(json.dumps(split.checkpoint(), allow_nan=False))
        resumed = account(data, rows, config, state=state)
        self.assertEqual(complete(resumed), expected)
        self.assertEqual(resumed.checkpoint(), full.checkpoint())

    def test_injected_post_fill_failure_rolls_back_parent_fees_and_opportunity(self):
        data = fixture()
        engine = account(data)
        engine.step(1)
        before = engine.checkpoint()
        with patch("service.portfolio_engine.capture_rights", side_effect=RuntimeError("after fill and valuation")):
            with self.assertRaises(RuntimeError):
                engine.step(1)
        self.assertEqual(engine.checkpoint(), before)
        expected = complete(account(data))
        self.assertEqual(complete(engine), expected)

    def test_mutated_live_source_indexes_schedule_entry_policy_rejected(self):
        mutations = [lambda a: a.dataset["securities"][0]["bars"][6].update(open=9.9, low=9.9),
                     lambda a: a.by_date[SYMBOLS[0]].update({a.dates[1]: 7}),
                     lambda a: setattr(a, "entry_weight", .2),
                     lambda a: a._schedule[0].update(originalRank=2),
                     lambda a: a.scorer.by_day[a.dates[0]][0].update(originalRank=2),
                     lambda a: a.research_state["opportunities"][0].update(originalRank=2)]
        for mutate in mutations:
            engine = account(fixture())
            engine.step(1)
            mutate(engine)
            for action in (lambda: engine.step(1), engine.checkpoint, engine.result):
                with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                    action()

    def test_restore_rejects_changed_identity_and_rehashed_bad_sidecar(self):
        data = fixture()
        engine = account(data)
        engine.step(2)
        snapshot = engine.checkpoint()
        changes = [lambda s: s["researchState"]["opportunities"][0].update(status="bogus"),
                   lambda s: s["researchState"]["opportunities"][0].update(unfilledQuantity=1),
                   lambda s: s["researchState"]["opportunities"][0].update(fillId="foreign"),
                   lambda s: s["researchState"]["opportunities"][0].update(status="notObserved"),
                   lambda s: s["researchState"]["opportunities"][0].update(plannedQuantity=True)]
        for change in changes:
            changed = deepcopy(snapshot)
            change(changed)
            changed["digest"] = _digest({k: v for k, v in changed.items() if k != "digest"})
            with self.subTest(change=change), self.assertRaises(ValueError):
                account(data, state=changed)
        with self.assertRaises(ValueError):
            account(data, config=settings(data, stopLossPct=.1), state=snapshot)
        with self.assertRaises(ValueError):
            account(data, [signal(data, rank=2)], state=snapshot)

    def test_controlled_order_copy_only_explicit_fee_seams_differ(self):
        inherited = inspect.getsource(PortfolioEngine._execute_order)
        expected = inherited.replace('fee = self._fees(quantity * price, side, day)',
            'quote = self.fee_ledger.quote(quantity * price, side, self._context(symbol, pending["signalDate"], day, "fillQuote"))\n            fee = quote["breakdown"]["total"]')
        expected = expected.replace('self._affordable_quantity(ceiling, minimum, increment, price, min(max(0., s["cash"] - s["reservedActionCash"]), target_budget), day)',
            'self.fee_ledger.affordable_quantity(ceiling, minimum, increment, price, min(max(0., s["cash"] - s["reservedActionCash"]), target_budget), self._context(symbol, pending["signalDate"], day, "executionSizing"))')
        expected = expected.replace('        s["trades"].append(fill)\n', '''        s["trades"].append(fill)
        fill_id = f"fill-{len(s['trades']):08d}"
        fill.update(fillId=fill_id, feeQuoteId=quote["quoteId"],
                    signalId=pending.get("signalId"), originalRank=pending.get("originalRank"))
        self.fee_ledger.commit_fill(quote, fill, fill_id)
''')
        normalize = lambda text: ast.dump(ast.parse(textwrap.dedent(text)), include_attributes=False)
        self.assertEqual(normalize(inspect.getsource(FixedSignalAccount._execute_order)), normalize(expected))
        root = Path(__file__).resolve().parents[1]
        contract = json.loads((root / "docs/fixed-signal-account-contract-v1.json").read_text(encoding="utf-8"))
        for path, key in (("portfolio_engine.py", "existingEngineSha256"), ("portfolio_config.py", "existingConfigSha256"),
                          ("research_fee_profile.py", "existingResearchFeeSha256")):
            self.assertEqual(hashlib.sha256((root / "service" / path).read_bytes()).hexdigest(), contract["isolation"][key])


if __name__ == "__main__":
    unittest.main()
