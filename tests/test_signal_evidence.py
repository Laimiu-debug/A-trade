"""Offline evidence invariants; no sockets, live prices or user databases."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from service.signal_evidence import action_evidence, load_snapshot, normalize_evidence, write_evidence


START, END = "2022-01-03", "2022-01-07"
DAYS = [f"2022-01-0{i}" for i in range(3, 8)]
CODE = "sh.600000"


def response(api, parameters, rows, fields=None):
    return {"provider": "baostock", "api": api, "parameters": parameters,
            "fields": fields or list(rows[0] if rows else {}), "rows": rows,
            "retrievedAt": "2026-10-15T12:00:00+00:00", "errorCode": "0",
            "rowBoundReached": False}


def fixture():
    daily = response("query_history_k_data_plus", {"code": CODE, "start_date": START, "end_date": END,
        "frequency": "d", "adjustflag": "3"}, [
        {"date": day, "code": CODE, "open": "10", "high": "11", "low": "9", "close": "10",
         "preclose": "10", "volume": "1000", "amount": "10000", "adjustflag": "3",
         "tradestatus": "1", "isST": "0"} for day in DAYS])
    basic = response("query_stock_basic", {"code": CODE}, [{"code": CODE, "code_name": "Current Name",
        "ipoDate": "2000-01-01", "outDate": "", "type": "1", "status": "0"}])
    calendar = response("query_trade_dates", {"start_date": START, "end_date": END}, [
        {"calendar_date": day, "is_trading_day": "1"} for day in DAYS])
    factors = response("query_adjust_factor", {"code": CODE, "start_date": START, "end_date": END}, [],
                       ["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"])
    dividends = response("query_dividend_data", {"code": CODE, "year": "2022", "yearType": "operate"}, [], [
        "code", "dividPreNoticeDate", "dividAgmPumDate", "dividPlanAnnounceDate", "dividPlanDate",
        "dividRegistDate", "dividOperateDate", "dividPayDate", "dividStockMarketDate",
        "dividCashPsBeforeTax", "dividCashPsAfterTax", "dividStocksPs", "dividCashStock", "dividReserveToStockPs"])
    members = [response("query_all_stock", {"day": day}, [
        {"code": CODE, "tradeStatus": "1", "code_name": "Name at query"}]) for day in (START, END)]
    return [daily, basic, calendar, factors, dividends, *members]


def add_distribution(snapshots, *, capitalization="0.3"):
    snapshots[4]["rows"] = [[CODE, "", "2022-01-02", "2022-01-01", "2022-01-03", "2022-01-04",
        "2022-01-05", "2022-01-06", "2022-01-07", "0.1", "0.09或0.10", "0.2", "provider text", capitalization]]
    snapshots[3]["rows"] = [[CODE, "2022-01-05", "0.5", "2.0", "2.0"]]


def normalized(snapshots=None):
    return normalize_evidence(snapshots or fixture(), start=START, end=END, selected_symbols=[CODE])


class SignalEvidenceTests(unittest.TestCase):
    def test_pure_lossless_repeatable_and_does_not_infer_current_status(self):
        snapshots = fixture()
        before = deepcopy(snapshots)
        result = normalized(snapshots)
        self.assertEqual(snapshots, before)
        self.assertEqual(result, normalized(snapshots))
        self.assertFalse(result["quality"]["certified"])
        sec = result["securities"][0]
        self.assertEqual(sec["tradeStatusByDate"][START], True)
        self.assertEqual(sec["basicObservations"][0]["currentStatus"], "0")
        self.assertFalse(sec["basicObservations"][0]["historicalStatusInferred"])
        self.assertEqual({s["raw"]["api"] for s in result["sources"]}, {x["api"] for x in snapshots})

    def test_snapshot_membership_is_not_interpolated_or_inferred_from_ipo(self):
        sec = normalized()["securities"][0]
        self.assertEqual(sec["memberByDate"], {START: True, END: True})
        self.assertNotIn("2022-01-04", sec["memberByDate"])
        self.assertEqual(sec["listingInterval"]["startInclusive"], "2000-01-01")
        self.assertFalse(sec["listingInterval"]["isMembershipProof"])
        self.assertIsNone(sec["listingInterval"]["endExclusive"])

    def test_delist_date_is_exclusive_and_conflicts_are_reported(self):
        snapshots = fixture()
        snapshots[1]["rows"][0]["outDate"] = END
        result = normalized(snapshots)
        self.assertEqual(result["securities"][0]["delistDate"], END)
        self.assertEqual({x["code"] for x in result["issues"]},
                         {"membershipOutsideListingInterval", "dailyBarOutsideListingInterval"})
        snapshots[1]["rows"][0]["outDate"] = "1999-01-01"
        with self.assertRaisesRegex(ValueError, "Delisting"):
            normalized(snapshots)

    def test_bad_raw_ohlc_numeric_or_adjustment_cannot_pass(self):
        for key, value in (("high", "9.5"), ("preclose", "NaN"), ("volume", "-1"),
                           ("open", "Infinity"), ("adjustflag", "1")):
            with self.subTest(key=key):
                snapshots = fixture()
                snapshots[0]["rows"][0][key] = value
                with self.assertRaises(ValueError):
                    normalized(snapshots)

    def test_missing_reference_or_stays_unknown_and_halt_not_fabricated(self):
        snapshots = fixture()
        snapshots[0]["rows"][1].update(preclose="", isST="", tradestatus="")
        result = normalized(snapshots)
        sec = result["securities"][0]
        self.assertIsNone(sec["precloseByDate"][DAYS[1]])
        self.assertIsNone(sec["stByDate"][DAYS[1]])
        self.assertIsNone(sec["tradeStatusByDate"][DAYS[1]])
        self.assertIn("unknownReferencePreclose", [x["code"] for x in result["issues"]])

    def test_reference_jump_is_observed_even_with_matching_factor_and_dividend(self):
        snapshots = fixture()
        add_distribution(snapshots)
        snapshots[0]["rows"][2]["preclose"] = "9.9"
        result = normalized(snapshots)
        jump = next(x for x in result["issues"] if x["code"] == "referencePriceDiscontinuity")
        self.assertEqual(jump["date"], "2022-01-05")
        self.assertTrue(jump["dividendObserved"])
        self.assertTrue(jump["factorObserved"])
        evidence = action_evidence(result, CODE, as_of=END, start=DAYS[1], end=END)
        self.assertFalse(evidence["rawReferenceContinuityObserved"])
        self.assertEqual(evidence["status"], "unknown")
        self.assertFalse(evidence["pointInTimeTermsVerified"])

    def test_empty_factors_and_dividends_never_prove_no_action(self):
        result = normalized()
        evidence = action_evidence(result, CODE, as_of=END, start=DAYS[1], end=END)
        self.assertTrue(evidence["rawReferenceContinuityObserved"])
        self.assertFalse(evidence["noEventsProven"])
        self.assertFalse(evidence["bridgeVerified"])
        self.assertEqual(evidence["status"], "unknown")
        self.assertTrue(evidence["sourceHashes"])
        self.assertTrue(all(value is None for value in result["securities"][0]["actionKnownByDate"].values()))

    def test_continuity_requires_complete_calendar_and_previous_session(self):
        result = normalized()
        self.assertIsNone(action_evidence(result, CODE, as_of=END, start=START, end=END)["rawReferenceContinuityObserved"])
        snapshots = fixture()
        snapshots[2]["rows"].pop(2)
        result = normalized(snapshots)
        self.assertFalse(result["quality"]["calendarCoverageComplete"])
        self.assertIsNone(action_evidence(result, CODE, as_of=END, start=DAYS[1], end=END)["rawReferenceContinuityObserved"])

    def test_bonus_capitalization_tax_and_reported_dates_remain_distinct(self):
        snapshots = fixture()
        add_distribution(snapshots)
        result = normalized(snapshots)
        action = result["securities"][0]["dividendEvents"][0]
        self.assertEqual(action["stockDividendPerShare"], .2)
        self.assertEqual(action["capitalizationPerShare"], .3)
        self.assertEqual(action["totalBonusSharesPerShare"], .5)
        self.assertEqual(action["cashAfterTaxText"], "0.09或0.10")
        self.assertIsNone(action["cashNetPerShare"])
        self.assertIsNone(action["knownByDate"])
        self.assertEqual(action["reportedPlanDate"], START)
        self.assertFalse(action["reportedDateSemanticsVerified"])
        evidence = action_evidence(result, CODE, as_of=END, start=START, end=END)
        self.assertEqual(evidence["observedEffectiveEvents"][0]["evidenceDates"]["effectiveDate"], DAYS[2])
        self.assertTrue(evidence["observedEffectiveEvents"][0]["evidenceDates"]["retrievedAt"][0].startswith("2026"))

    def test_missing_share_component_is_not_zero_and_extra_field_is_preserved(self):
        snapshots = fixture()
        add_distribution(snapshots, capitalization="")
        snapshots[4]["rows"][0].append("undocumented-provider-value")
        action = normalized(snapshots)["securities"][0]["dividendEvents"][0]
        self.assertIsNone(action["capitalizationPerShare"])
        self.assertIsNone(action["totalBonusSharesPerShare"])
        self.assertEqual(action["raw"]["_extraFields"], ["undocumented-provider-value"])

    def test_invalid_event_chronology_is_retained_and_flagged_not_executed(self):
        snapshots = fixture()
        add_distribution(snapshots)
        snapshots[4]["rows"][0][5] = END
        snapshots[4]["rows"][0][7] = START
        result = normalized(snapshots)
        action = result["securities"][0]["dividendEvents"][0]
        self.assertEqual(set(action["chronologyIssues"]), {"recordNotBeforeEx", "cashPayDateBeforeEx"})
        self.assertFalse(action["economicLedgerReady"])
        self.assertEqual(len(result["issues"]), 2)

    def test_failed_truncated_or_schema_mismatched_source_is_not_empty_success(self):
        for field, value in (("errorCode", "100"), ("rowBoundReached", True), ("complete", False)):
            snapshots = fixture()
            snapshots[0][field] = value
            with self.assertRaises(ValueError):
                normalized(snapshots)
        snapshots = fixture()
        snapshots[0]["fields"].append("unprovided")
        with self.assertRaisesRegex(ValueError, "missing"):
            normalized(snapshots)

    def test_duplicates_conflicts_and_wrong_symbol_are_rejected(self):
        snapshots = fixture()
        snapshots[0]["rows"].append(deepcopy(snapshots[0]["rows"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            normalized(snapshots)
        snapshots = fixture()
        copy = deepcopy(snapshots[0])
        copy["rows"][0]["close"] = "10.1"
        snapshots.append(copy)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            normalized(snapshots)
        snapshots = fixture()
        snapshots[0]["rows"][0]["code"] = "sz.000001"
        with self.assertRaisesRegex(ValueError, "security"):
            normalized(snapshots)

    def test_future_window_and_source_rows_outside_cutoff_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "future"):
            action_evidence(normalized(), CODE, as_of=START, start=START, end=END)
        snapshots = fixture()
        snapshots[0]["parameters"]["end_date"] = "2022-01-10"
        snapshots[0]["rows"][-1]["date"] = "2022-01-10"
        with self.assertRaisesRegex(ValueError, "cutoff"):
            normalized(snapshots)

    def test_action_query_cannot_claim_coverage_beyond_package(self):
        for start, end in ((DAYS[1], "2022-01-10"), ("2021-12-31", END)):
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(ValueError, "coverage"):
                    action_evidence(normalized(), CODE, as_of="2022-01-10", start=start, end=end)

    def test_continuity_lineage_includes_prior_session_and_calendar_sources(self):
        snapshots = fixture()
        first = deepcopy(snapshots[0])
        first["rows"] = first["rows"][:1]
        snapshots[0]["rows"] = snapshots[0]["rows"][1:]
        result = normalized([first, *snapshots])
        needed = {source["sha256"] for source in result["sources"]
                  if source["api"] in ("query_history_k_data_plus", "query_trade_dates")}
        evidence = action_evidence(result, CODE, as_of=END, start=DAYS[1], end=END)
        self.assertTrue(evidence["rawReferenceContinuityObserved"])
        self.assertTrue(needed.issubset(set(evidence["sourceHashes"])))

    def test_explicit_sample_keeps_missing_security_without_backfill(self):
        result = normalize_evidence(fixture(), start=START, end=END, selected_symbols=[CODE, "sz.000001"])
        self.assertEqual(result["selection"]["symbols"], ["sh600000", "sz000001"])
        absent = result["securities"][1]
        self.assertEqual(absent["bars"], [])
        self.assertIsNone(absent["listingDate"])

    def test_source_bytes_and_new_artifact_are_immutable_no_database_write(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path, output = Path(directory) / "source.json", Path(directory) / "evidence.json"
            fake_db = Path(directory) / "market.sqlite3"
            fake_db.write_bytes(b"untouched user database sentinel")
            before_db = fake_db.read_bytes()
            content = json.dumps(fixture()[0], ensure_ascii=False, indent=4).encode()
            source_path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            snapshot = load_snapshot(source_path, digest)
            result = normalized([snapshot, *fixture()[1:]])
            source = next(x for x in result["sources"] if x["sha256"] == digest)
            self.assertEqual(source["hashBasis"], "file-bytes")
            receipt = write_evidence(result, output)
            self.assertEqual(receipt["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            with self.assertRaises(FileExistsError):
                write_evidence(result, output)
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_snapshot(source_path, "0" * 64)
            snapshot["payload"]["rows"][0]["close"] = "10.1"
            with self.assertRaisesRegex(ValueError, "changed"):
                normalized([snapshot, *fixture()[1:]])
            self.assertEqual(source_path.read_bytes(), content)
            self.assertEqual(fake_db.read_bytes(), before_db)


if __name__ == "__main__":
    unittest.main()
