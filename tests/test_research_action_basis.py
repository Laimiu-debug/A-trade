"""Independent economic identities, timing and ownership traps; no market run."""
from copy import deepcopy
from decimal import Decimal as D
import unittest

from service.research_action_basis import (
    normalize_distribution, reference_change, causal_price_window,
    distribution_entitlements, grant_lots, rebase_risk_and_mark,
    consume_sellable_lots, DistributionCashBook,
)


def terms(**overrides):
    return {"id": "synthetic-2026-06-11", "symbol": "sh688200", "publicDate": "2026-06-05",
        "recordDate": "2026-06-10", "exDate": "2026-06-11", "shareListingDate": "2026-06-11",
        "cashPayDate": "2026-06-11", "cashGrossPerShare": "1.19", "additionalSharesPerShare": "0.48",
        "referenceCashPerShare": "1.1890", "referenceAdditionalSharesPerShare": "0.4796",
        "sourceSha256": "a" * 64, "sourceUrl": "https://example.test/synthetic.pdf", **overrides}


def lot(quantity=100, cost="1000", **overrides):
    return {"lotId": "fill-old", "episodeId": "episode-old", "acquiredDate": "2026-06-01",
            "quantity": quantity, "cost": cost, **overrides}


def entitlement(event=None, lots=None, policy="exact", tax="0.20"):
    event = event or terms()
    return distribution_entitlements(event, [lot()] if lots is None else lots,
        record_as_of=event["recordDate"], fraction_policy=policy, tax_reserve_rate=tax)


def bar(day, close):
    return {"date": day, "open": close, "high": close, "low": close, "close": close,
            "amount": "1000000", "volume": 100000}


def window(raw, events, as_of, **overrides):
    return causal_price_window(raw, events, as_of=as_of, symbol="sh688200", price_basis="raw-unadjusted", **overrides)


class DistributionBasisTests(unittest.TestCase):
    def test_source_terms_do_not_conflate_actual_and_virtual_ratios(self):
        event = normalize_distribution(terms())
        self.assertEqual(event["additionalSharesPerShare"], "0.48")
        self.assertEqual(event["referenceAdditionalSharesPerShare"], "0.4796")
        self.assertEqual(reference_change(event, "467.46")["exReference"], "315.13")
        e = entitlement(event, [lot(290, "135563.4")], "floor", "0")
        self.assertEqual(e["theoreticalGrant"], "139.20")
        self.assertEqual(e["allocatedGrant"], 139)
        self.assertEqual(D(e["lots"][0]["cashGross"]), D("345.10"))

    def test_verified_tengjing_reference_arithmetic(self):
        e = terms(symbol="sh688195", cashGrossPerShare="0.164", additionalSharesPerShare="0.4",
                  referenceCashPerShare="0.164", referenceAdditionalSharesPerShare="0.4")
        self.assertEqual(reference_change(e, "341.42")["exReference"], "243.75")
        state = entitlement(e, [lot(581)], "ceil", "0")
        self.assertEqual(state["allocatedGrant"], 233)
        self.assertEqual(D(state["lots"][0]["cashGross"]), D("95.284"))

    def test_unknown_fields_missing_terms_bad_numbers_and_dates_rejected(self):
        cases = [{"cashGrossPerShare": None}, {"additionalSharesPerShare": None},
                 {"cashGrossPerShare": 1.19}, {"referenceCashPerShare": "NaN"},
                 {"referenceAdditionalSharesPerShare": "-1"}, {"publicDate": "2026-06-12"},
                 {"shareListingDate": "2026-06-10"}, {"cashPayDate": None},
                 {"sourceSha256": None}, {"symbol": None}, {"unknown": "ignored?"}]
        for override in cases:
            with self.subTest(override=override), self.assertRaises(ValueError):
                normalize_distribution(terms(**override))
        with self.assertRaises(ValueError):
            reference_change(terms(referenceCashPerShare="11"), "10")

    def test_future_event_cannot_adjust_past_decisions(self):
        raw = [bar("2026-06-10", "467.46"), bar("2026-06-11", "315")]
        saved = deepcopy(raw)
        before = window(raw, [terms()], "2026-06-10")
        self.assertEqual(D(before["bars"][0]["close"]), D("467.46"))
        self.assertEqual(before["referenceChanges"], [])
        after = window(raw, [terms()], "2026-06-11")
        self.assertAlmostEqual(D(after["bars"][0]["close"]), D("315.13"), places=20)
        self.assertEqual(after["bars"][1]["close"], "315")
        self.assertEqual(after["bars"][0]["amount"], raw[0]["amount"])
        self.assertEqual(raw, saved)
        future_poison = bar("2026-06-12", "not-a-price")
        self.assertEqual(before, window(raw + [future_poison], [terms()], "2026-06-10"))

    def test_native_raw_floats_and_missing_reference_or_adjusted_input(self):
        raw = [bar("2026-06-10", 467.46), bar("2026-06-11", 315.)]
        result = window(raw, [terms()], "2026-06-11")
        self.assertAlmostEqual(D(result["bars"][0]["close"]), D("315.13"), places=20)
        self.assertEqual(result["bars"][0]["sourcePriceBasisId"], "raw-unadjusted")
        self.assertNotEqual(result["bars"][0]["priceBasisId"], "raw-unadjusted")
        for bad_raw in ([{**raw[0], "priceBasisId": "qfq"}, raw[1]],
                        [{**raw[0], "date": "2026-06-09"}, raw[1]]):
            with self.subTest(raw=bad_raw), self.assertRaises(ValueError):
                window(bad_raw, [terms()], "2026-06-11")
        with self.assertRaises(ValueError):
            causal_price_window(raw, [terms()], as_of="2026-06-11", symbol="sh688200", price_basis="qfq")

    def test_multiple_effective_actions_compound_and_future_does_not(self):
        event1 = terms(id="split1", cashGrossPerShare="0", referenceCashPerShare="0",
                       additionalSharesPerShare="1", referenceAdditionalSharesPerShare="1", cashPayDate=None)
        event2 = terms(id="split2", publicDate="2026-06-12", recordDate="2026-06-15", exDate="2026-06-16",
                       shareListingDate="2026-06-16", cashGrossPerShare="0", referenceCashPerShare="0",
                       additionalSharesPerShare="1", referenceAdditionalSharesPerShare="1", cashPayDate=None)
        raw = [bar("2026-06-10", "100"), bar("2026-06-11", "50"),
               bar("2026-06-15", "50"), bar("2026-06-16", "25")]
        result = window(raw, [event2, event1], "2026-06-16")
        self.assertEqual([D(row["close"]) for row in result["bars"]], [D(25)] * 4)
        for ambiguous in ([event1, event1], [event1, terms(id="other")], [terms(symbol="sz000001")]):
            with self.subTest(ambiguous=ambiguous), self.assertRaises(ValueError):
                window(raw, ambiguous, "2026-06-16")

    def test_pure_cash_conserves_equity_without_inventing_free_cash(self):
        event = terms(cashGrossPerShare="1", referenceCashPerShare="1", additionalSharesPerShare="0",
                      referenceAdditionalSharesPerShare="0", shareListingDate=None)
        book = DistributionCashBook()
        e = entitlement(event, tax="0")
        book.accrue(e, day="2026-06-11")
        # Independently: 100 old shares at 10 => 100 at 9 + a 100 receivable.
        self.assertEqual(100 * D("9") + D(book.summary()["netAssetAdjustment"]), D("1000"))
        self.assertEqual(book.advance_open("2026-06-11")["spendableCashDelta"], "0")
        self.assertEqual(D(book.advance_open("2026-06-12")["spendableCashDelta"]), D(100))
        self.assertEqual(book.summary()["netAssetAdjustment"], "0")

    def test_split_cost_and_missing_ex_bar_mark_conserve_wealth(self):
        event = terms(cashGrossPerShare="0", referenceCashPerShare="0", additionalSharesPerShare="1",
                      referenceAdditionalSharesPerShare="1", cashPayDate=None)
        original = [lot(100, "1001")]
        lots = grant_lots(entitlement(event, original), original, day="2026-06-11")
        self.assertEqual(sum(row["quantity"] for row in lots), 200)
        self.assertEqual(sum(D(row["cost"]) for row in lots), D(1001))
        state = {"riskEntryPrice": "10", "closePeak": "12", "lastPrice": "10", "lastPriceDate": "2026-06-10", "ratchetRatio": "1.1"}
        rebased = rebase_risk_and_mark(state, reference_change(event, "10"), ex_date="2026-06-11")
        self.assertEqual(D(rebased["riskEntryPrice"]), D(5))
        self.assertEqual(D(rebased["closePeak"]), D(6))
        self.assertEqual(rebased["ratchetRatio"], "1.1")
        self.assertEqual(200 * D(rebased["lastPrice"]), 100 * D(state["lastPrice"]))
        self.assertIn("Stale", rebased["markMeaning"])
        with self.assertRaises(ValueError):
            rebase_risk_and_mark(rebased, reference_change(event, "10"), ex_date="2026-06-11")
        with self.assertRaises(ValueError):
            grant_lots(entitlement(event, original), lots, day="2026-06-11")

    def test_real_market_drop_remains_after_reference_conversion(self):
        event = terms(cashGrossPerShare="0", referenceCashPerShare="0", additionalSharesPerShare="1",
                      referenceAdditionalSharesPerShare="1", cashPayDate=None)
        state = {"riskEntryPrice": "10", "closePeak": "10", "lastPrice": "4.5", "lastPriceDate": "2026-06-11", "ratchetRatio": "0.95"}
        new = rebase_risk_and_mark(state, reference_change(event, "10"), ex_date="2026-06-11")
        self.assertEqual(new["lastPrice"], "4.5")
        self.assertLess(D(new["lastPrice"]), D(new["riskEntryPrice"]) * D("0.95"))

    def test_fraction_scenarios_are_account_total_not_each_lot_rounding(self):
        event = terms(cashGrossPerShare="0", referenceCashPerShare="0", additionalSharesPerShare="0.4",
                      referenceAdditionalSharesPerShare="0.4", cashPayDate=None)
        lots = [lot(1, "10", lotId="b"), lot(1, "10", lotId="a"), lot(1, "10", lotId="c")]
        with self.assertRaises(ValueError):
            entitlement(event, lots, "exact")
        lo, hi = entitlement(event, lots, "floor"), entitlement(event, lots, "ceil")
        self.assertEqual((lo["allocatedGrant"], hi["allocatedGrant"]), (1, 2))
        self.assertEqual([row["lotId"] for row in lo["lots"] if row["allocatedGrant"]], ["a"])
        self.assertIn("notBroker", lo["shareAllocationMeaning"])

    def test_delayed_listing_and_partial_fills_keep_cost_and_fees(self):
        event = terms(cashGrossPerShare="0", referenceCashPerShare="0", additionalSharesPerShare="1",
                      referenceAdditionalSharesPerShare="1", cashPayDate=None, shareListingDate="2026-06-15")
        original = [lot(100, "1001")]
        lots = grant_lots(entitlement(event, original), original, day="2026-06-11")
        frozen = deepcopy(lots)
        with self.assertRaises(ValueError):
            consume_sellable_lots(lots, 101, day="2026-06-11", price="5", fee="1")
        self.assertEqual(lots, frozen)
        part = consume_sellable_lots(lots, 40, day="2026-06-11", price="5", fee="1")
        second = consume_sellable_lots(part["lots"], 60, day="2026-06-12", price="5", fee="1")
        last = consume_sellable_lots(second["lots"], 100, day="2026-06-15", price="5", fee="1")
        self.assertEqual(last["lots"], [])
        # Total sale proceeds 1000, opening cost1001 (includes buy fee), sell fees3.
        self.assertEqual(sum(D(row["realizedPnl"]) for row in (part, second, last)), D(-4))
        self.assertEqual(sum(D(row["costRemoved"]) for row in (part, second, last)), D(1001))

    def test_t_plus_one_not_bypassed_by_new_lot(self):
        rows = [lot(acquiredDate="2026-06-11")]
        with self.assertRaises(ValueError):
            consume_sellable_lots(rows, 1, day="2026-06-11", price="10", fee="0")

    def test_old_dividend_stays_with_old_episode_after_symbol_reentry(self):
        book = DistributionCashBook()
        e = entitlement(terms(cashPayDate="2026-06-15"))
        book.accrue(e, day="2026-06-11")
        # The account may now close old and open a new episode for this symbol.
        # No lookup of a current position exists in this cash book.
        self.assertEqual(D(book.summary()["incomeByOriginalEpisode"]["episode-old"]), D("95.20"))
        movement = book.advance_open("2026-06-16")
        self.assertEqual(D(movement["spendableCashDelta"]), D("95.20"))
        self.assertNotIn("episode-new", book.summary()["incomeByOriginalEpisode"])
        replacement = [lot(lotId="new-fill", episodeId="episode-new")]
        with self.assertRaises(ValueError):
            grant_lots(e, replacement, day="2026-06-11")

    def test_tax_reserve_liability_credit_and_explicit_settlement_bridge(self):
        book = DistributionCashBook()
        book.accrue(entitlement(), day="2026-06-11")
        s = book.summary()
        self.assertEqual((D(s["grossReceivable"]), D(s["taxReserveLiability"]), D(s["netAssetAdjustment"])),
                         (D(119), D("23.8"), D("95.2")))
        self.assertEqual(book.advance_open("2026-06-11")["spendableCashDelta"], "0")
        movement = book.advance_open("2026-06-12")
        s = book.summary()
        self.assertEqual((D(movement["grossCredit"]), D(movement["reservedCash"])), (D(119), D("23.8")))
        self.assertEqual(D(s["netAssetAdjustment"]), D(0))
        release = book.settle_tax("synthetic-2026-06-11:fill-old", "11.9", day="2026-06-12")
        self.assertEqual(D(release["spendableCashDelta"]), D("11.9"))
        self.assertEqual(D(book.summary()["incomeByOriginalEpisode"]["episode-old"]), D("107.1"))
        self.assertEqual(book.summary()["taxReserveLiability"], "0")
        with self.assertRaises(ValueError):
            book.settle_tax("synthetic-2026-06-11:fill-old", "11.9", day="2026-06-12")

    def test_empty_record_ownership_never_creates_dividend_or_shares(self):
        e, book = entitlement(lots=[]), DistributionCashBook()
        self.assertEqual(e["allocatedGrant"], 0)
        book.accrue(e, day="2026-06-11")
        self.assertEqual(book.summary()["incomeByOriginalEpisode"], {})
        self.assertEqual(book.advance_open("2026-06-12")["spendableCashDelta"], "0")

    def test_timing_order_and_atomic_failure_or_explicit_rollback(self):
        book, e = DistributionCashBook(), entitlement()
        book.advance_open("2026-06-10")
        snapshot = book.checkpoint()
        invalid = deepcopy(e)
        invalid["lots"].append({**e["lots"][0], "lotId": "broken", "taxReserve": "1000"})
        with self.assertRaises(ValueError):
            book.accrue(invalid, day="2026-06-11")
        self.assertEqual(book.checkpoint(), snapshot)
        book.accrue(e, day="2026-06-11")
        accrued = book.checkpoint()
        for operation in (lambda: book.accrue(e, day="2026-06-11"),
                          lambda: book.advance_open("2026-06-10"),
                          lambda: book.settle_tax("synthetic-2026-06-11:fill-old", "0", day="2026-06-11")):
            with self.assertRaises(ValueError):
                operation()
            self.assertEqual(book.checkpoint(), accrued)
        book.advance_open("2026-06-12")
        book.restore(accrued)
        self.assertEqual(book.checkpoint(), accrued)
        self.assertEqual(D(book.advance_open("2026-06-12")["spendableCashDelta"]), D("95.2"))


if __name__ == "__main__":
    unittest.main()
