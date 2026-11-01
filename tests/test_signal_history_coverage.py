"""Behavior checks for immutable provenance, calendar gaps and neutral boundaries."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from service.signal_history_coverage import (BASIC, DAILY, DIVIDEND, FACTOR,
    coverage_summary, encoded, load_calendar, load_manifest, load_universe_audit,
    normalize_history_coverage, read_hashed_json, sha256)


START, END = "2022-01-03", "2022-01-07"
STAMP = "2026-10-15T00:00:00+00:00"
AUDIT_HASH = "a" * 64
FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST".split(",")


def row(day, code="sh.600001", **changes):
    raw = dict(zip(FIELDS, [day, code, "10", "11", "9", "10", "9.5", "1000", "10000", "3", "1", "0"]))
    raw.update(changes)
    return raw


def put(root, name, obj):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    data = encoded(obj)
    path.write_bytes(data)
    return {"path": name, "sha256": sha256(data)}


def make_bundle(root, *, daily=None, basic=None, factors=None, dividends=None, code="sh.600001",
                page_mutator=None, aggregate_mutator=None):
    if daily is None:
        daily = [row("2022-01-04", code), row("2022-01-06", code, tradestatus="0", volume="", amount="")]
    if basic is None:
        basic = [{"code": code, "code_name": "*ST CURRENT NAME", "ipoDate": "2022-01-04",
                  "outDate": "2022-01-06", "type": "1", "status": "0"}]
    specifications = [
        ("basic", BASIC, {"code": code}, ["code", "code_name", "ipoDate", "outDate", "type", "status"], basic),
        ("daily", DAILY, {"code": code, "fields": ",".join(FIELDS), "start_date": START,
            "end_date": END, "frequency": "d", "adjustflag": "3"}, FIELDS, daily),
        ("factors", FACTOR, {"code": code, "start_date": START, "end_date": END},
            ["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"], factors or []),
        ("dividend-2022", DIVIDEND, {"code": code, "year": "2022", "yearType": "operate"},
            ["code", "dividOperateDate", "dividPayDate", "dividCashPsBeforeTax"], dividends or []),
    ]
    plans, results = [], []
    for suffix, api, params, fields, raw_rows in specifications:
        name = code.replace(".", "") + "-" + suffix
        plan = {"id": name, "api": api, "parameters": params}
        plans.append(plan)
        rows = [[r.get(f, "") for f in fields] for r in raw_rows]
        envelope = {"provider": "baostock", "api": api, "parameters": params,
            "retrievedAt": STAMP, "errorCode": "0", "fields": fields, "rows": rows,
            "rowBoundReached": False}
        pages = []
        # Capacity two deliberately exercises full page plus empty terminal page.
        for offset in range(0, len(rows) + 1, 2):
            number = len(pages) + 1
            page = {**envelope, "rows": rows[offset:offset + 2], "sdkPageNumber": str(number),
                "sdkPerPageCount": "2", "receivedRows": len(rows[offset:offset + 2]), "pageRowsTruncated": False}
            if page_mutator:
                page_mutator(suffix, number, page)
            ref = put(root, f"pages/{name}-{number}.json", page)
            pages.append({**ref, "rowCount": len(page["rows"]), "sdkPageNumber": str(number)})
        aggregate = {**envelope, "complete": True, "terminalReason": "shortOrEmptyTerminalPage",
                     "exceptionType": None, "pages": pages}
        if aggregate_mutator:
            aggregate_mutator(suffix, aggregate)
        ref = put(root, f"raw/{name}.json", aggregate)
        results.append({**plan, **ref, "status": "completed", "pages": pages, "rowCount": len(rows),
            "errorCode": "0", "rowBoundReached": False, "complete": True,
            "terminalReason": "shortOrEmptyTerminalPage", "exceptionType": None})
    protocol = put(root, "protocol.json", {"requestCount": len(plans), "sourceAuditSha256": AUDIT_HASH})
    request_plan = put(root, "request-manifest.json", {"requests": plans, "sourceAuditSha256": AUDIT_HASH,
        "protocolSha256": protocol["sha256"], "selected": [{"symbol": code.replace(".", "")} ]})
    manifest = put(root, "manifest.json", {"status": "completed", "protocol": protocol,
        "requestManifest": request_plan, "requests": results, "selectedCodes": [code],
        "auth": {"login": {"errorCode": "0"}, "logout": {"errorCode": "0"}}})
    return root / "manifest.json", manifest["sha256"]


def pure_inputs(symbols=("sh600001",)):
    calendar = {"start": START, "end": END,
        "dates": [f"2022-01-{d:02}" for d in range(3, 8)], "source": {"sha256": "b" * 64}}
    audit = {"symbols": list(symbols), "source": {"sha256": AUDIT_HASH},
        "observations": {s: [{"date": START, "membershipOnOtherDates": "unknown"}] for s in symbols}}
    return calendar, audit


class HistoryCoverageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def bundle(self, **kwargs):
        return load_manifest(*make_bundle(self.root, **kwargs))

    def normalized(self, bundle=None, **kwargs):
        calendar, audit = pure_inputs()
        return normalize_history_coverage([bundle or self.bundle(**kwargs)], calendar, audit, start=START, end=END)

    def test_calendar_partition_and_outdate_halt_placeholder_remain_neutral(self):
        package = self.normalized()
        security = package["securities"][0]
        c = security["coverage"]
        self.assertEqual(c["missingBeforeReportedIPO"], ["2022-01-03"])
        self.assertEqual(c["missingBetweenReportedBounds"], ["2022-01-05"])
        self.assertEqual(c["missingAfterReportedOutDate"], ["2022-01-07"])
        self.assertEqual(c["missingAtReportedOutDate"], [])
        boundary = c["observedAtOutDate"][0]
        self.assertEqual(boundary["tradingEvidence"], "haltedPlaceholder")
        self.assertIsNone(boundary["volume"])
        self.assertFalse(boundary["tradeStatus"])
        self.assertEqual(boundary["rawFields"]["volume"], "")
        self.assertEqual(security["qualityIssues"], [])
        self.assertFalse(security["daily"][0]["isST"])
        self.assertFalse(security["reportedBasic"]["historicalEligibilityUse"])
        self.assertFalse(package["certified"])
        self.assertEqual(sum(len(c[k]) for k in c if k.startswith("missing") and k != "missingDates"), len(c["missingDates"]))

    def test_unknown_daily_flags_are_not_inferred_from_current_basic_or_membership(self):
        package = self.normalized(daily=[row("2022-01-04", isST="", tradestatus="", preclose="")])
        security = package["securities"][0]
        self.assertIsNone(security["daily"][0]["isST"])
        self.assertIsNone(security["daily"][0]["tradeStatus"])
        self.assertEqual(security["coverage"]["unknownPrecloseCount"], 1)
        self.assertEqual(len(security["membershipObservations"]), 1)
        self.assertEqual(security["membershipObservations"][0]["membershipOnOtherDates"], "unknown")

    def test_no_daily_is_not_after_delisting_or_a_zero_return(self):
        package = self.normalized(daily=[], basic=[])
        c = package["securities"][0]["coverage"]
        self.assertTrue(c["noDailyRows"])
        self.assertEqual(c["missingListingBoundaryUnknown"], pure_inputs()[0]["dates"])
        self.assertIsNone(c["lastObservedDate"])
        self.assertFalse(package["returnsCalculated"])

    def test_missing_outdate_separately_counted_and_positive_boundary_not_discarded(self):
        p = self.normalized(daily=[row("2022-01-04")])
        self.assertEqual(p["securities"][0]["coverage"]["missingAtReportedOutDate"], ["2022-01-06"])
        p = self.normalized(daily=[row("2022-01-06"), row("2022-01-07")])
        c = p["securities"][0]["coverage"]
        self.assertEqual(c["observedAtOutDate"][0]["tradingEvidence"], "observedPositiveVolume")
        self.assertEqual(c["observedAfterOutDate"], ["2022-01-07"])
        self.assertEqual(p["securities"][0]["qualityIssues"], [{"date": "2022-01-07", "reason": "dailyOutsideReportedListingBounds"}])

    def test_future_outdate_is_metadata_only_not_an_in_range_rejection(self):
        basic = [{"code": "sh.600001", "ipoDate": "2000-01-01", "outDate": "2024-01-01", "status": "0"}]
        p = self.normalized(basic=basic)
        self.assertEqual(p["securities"][0]["coverage"]["missingAfterReportedOutDate"], [])
        self.assertEqual(p["securities"][0]["reportedBasic"]["outDate"], "2024-01-01")

    def test_unknown_outdate_does_not_invent_a_proven_listing_span(self):
        basic = [{"code": "sh.600001", "ipoDate": "2022-01-04", "outDate": "", "status": "1"}]
        p = self.normalized(basic=basic, daily=[])
        c = p["securities"][0]["coverage"]
        self.assertEqual(c["missingBetweenReportedBounds"], [])
        self.assertEqual(c["missingAfterReportedIPOWithoutOutDate"], [f"2022-01-{d:02}" for d in range(4, 8)])

    def test_contradictory_ohlc_and_halt_volume_are_reported_without_discarding(self):
        p = self.normalized(daily=[row("2022-01-04", close="12", tradestatus="0")])
        self.assertEqual(p["totals"]["observedDailyRows"], 1)
        self.assertEqual(p["securities"][0]["daily"][0]["qualityIssues"],
                         ["inconsistentOHLC", "haltWithPositiveVolumeOrAmount"])

    def test_cross_security_adjusted_rows_and_conflicting_actions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Cross-symbol"):
            self.normalized(daily=[row("2022-01-04", "sz.000001")])
        with self.assertRaisesRegex(ValueError, "Adjusted daily"):
            self.normalized(daily=[row("2022-01-04", adjustflag="2")])
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate rights"):
            self.normalized(factors=[{"code": "sh.600001", "dividOperateDate": "2022-01-04", "adjustFactor": "1"},
                                     {"code": "sh.600001", "dividOperateDate": "2022-01-04", "adjustFactor": "2"}])

    def test_raw_pages_and_aggregate_consistency_is_independently_checked(self):
        def mutate(suffix, aggregate):
            if suffix == "daily":
                aggregate["rows"][0][2] = "90"
        with self.assertRaisesRegex(ValueError, "Page rows differ"):
            self.bundle(aggregate_mutator=mutate)

    def test_raw_page_hash_and_manifest_hash_fail_closed(self):
        path, digest = make_bundle(self.root)
        page = next((self.root / "pages").glob("*.json"))
        page.write_bytes(page.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            load_manifest(path, digest)
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            load_manifest(path, "c" * 64)

    def test_missing_terminal_page_and_truncated_page_are_rejected(self):
        def truncation(suffix, number, page):
            if suffix == "daily":
                page["pageRowsTruncated"] = True
        with self.assertRaisesRegex(ValueError, "Truncated raw page"):
            self.bundle(page_mutator=truncation)
        def full_last_page(suffix, number, page):
            if suffix == "basic":
                page["sdkPerPageCount"] = "1"
        with self.assertRaisesRegex(ValueError, "terminal page"):
            self.bundle(page_mutator=full_last_page)

    def test_incomplete_request_and_plan_drift_are_rejected_even_with_new_manifest_hash(self):
        path, digest = make_bundle(self.root)
        manifest = json.loads(path.read_text("utf-8"))
        manifest["requests"][0]["complete"] = False
        digest = put(self.root, "manifest.json", manifest)["sha256"]
        with self.assertRaisesRegex(ValueError, "Incomplete request"):
            load_manifest(path, digest)
        manifest["requests"][0]["complete"] = True
        manifest["requests"][0]["parameters"] = {"code": "sh.600002"}
        digest = put(self.root, "manifest.json", manifest)["sha256"]
        with self.assertRaisesRegex(ValueError, "frozen request plan"):
            load_manifest(path, digest)

    def test_source_relative_traversal_is_rejected(self):
        path, _ = make_bundle(self.root)
        manifest = json.loads(path.read_text("utf-8"))
        manifest["protocol"]["path"] = "../protocol.json"
        digest = put(self.root, "manifest.json", manifest)["sha256"]
        with self.assertRaisesRegex(ValueError, "Unsafe source"):
            load_manifest(path, digest)

    def test_completed_marker_does_not_hide_failed_logout(self):
        path, _ = make_bundle(self.root)
        manifest = json.loads(path.read_text("utf-8"))
        manifest["auth"]["logout"]["errorCode"] = "10001001"
        digest = put(self.root, "manifest.json", manifest)["sha256"]
        with self.assertRaisesRegex(ValueError, "authentication lifecycle incomplete"):
            load_manifest(path, digest)

    def test_duplicate_daily_conflict_rejected_exact_duplicate_deduped(self):
        p = self.normalized(daily=[row("2022-01-04"), row("2022-01-04")])
        self.assertEqual(p["totals"]["observedDailyRows"], 1)
        self.assertEqual(p["totals"]["exactDuplicateRowsCollapsed"], 1)
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate symbol/date"):
            self.normalized(daily=[row("2022-01-04"), row("2022-01-04", close="10.5")])

    def test_conflicting_duplicate_bundles_rejected_identical_ones_collapse(self):
        a = self.bundle()
        c, universe = pure_inputs()
        p = normalize_history_coverage([a, deepcopy(a)], c, universe, start=START, end=END)
        self.assertEqual(p["exactDuplicateResponsesCollapsed"], 4)
        b = self.bundle(daily=[row("2022-01-04", close="10.5")])
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate request"):
            normalize_history_coverage([a, b], c, universe, start=START, end=END)

    def test_future_daily_and_effective_actions_rejected_noncalendar_bar_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside cutoff"):
            self.normalized(daily=[row("2024-01-02")])
        with self.assertRaisesRegex(ValueError, "outside cutoff"):
            self.normalized(factors=[{"code": "sh.600001", "dividOperateDate": "2024-01-01"}])
        c, a = pure_inputs(); c["dates"].remove("2022-01-04")
        with self.assertRaisesRegex(ValueError, "verified exchange calendar"):
            normalize_history_coverage([self.bundle()], c, a, start=START, end=END)

    def test_empty_rights_never_no_events_future_reported_terms_not_pit(self):
        p = self.normalized()
        actions = p["securities"][0]["corporateActions"]
        self.assertEqual(actions["status"], "unknown")
        self.assertFalse(actions["noEventsProven"])
        self.assertFalse(actions["pointInTimeTermsVerified"])
        p = self.normalized(dividends=[{"code": "sh.600001", "dividOperateDate": "2022-01-06",
                                        "dividPayDate": "2022-01-10", "dividCashPsBeforeTax": "0.1"}])
        actions = p["securities"][0]["corporateActions"]
        self.assertEqual(actions["futureReportedTerms"][0]["reportedDate"], "2022-01-10")
        self.assertFalse(actions["futureReportedTerms"][0]["usedForHistoricalEligibility"])

    def test_annual_dividend_response_before_warmup_is_retained_but_excluded(self):
        bundle = self.bundle(dividends=[{"code": "sh.600001", "dividOperateDate": "2022-01-01",
                                        "dividPayDate": "2022-01-04", "dividCashPsBeforeTax": "0.1"}])
        c, a = pure_inputs()
        # Same source shape as a 2021 whole-year operate query with August warmup.
        # Source hashes below are fixture hashes, not real-source attestations.
        c = {**c, "start": "2021-08-02", "end": "2021-12-31", "dates": ["2021-08-02", "2021-08-03"]}
        for response in bundle["responses"]:
            p = response["payload"]
            if p["api"] in (DAILY, FACTOR):
                p["parameters"]["start_date"] = c["start"]
                p["parameters"]["end_date"] = c["end"]
            p["rows"] = [] if p["api"] != DIVIDEND else [["sh.600001", "2021-06-01", "2021-06-02", "0.1"]]
            if p["api"] == DIVIDEND:
                p["parameters"]["year"] = "2021"
            response["payloadHash"] = sha256(encoded(p))
        p = normalize_history_coverage([bundle], c, a, start=c["start"], end=c["end"])
        actions = p["securities"][0]["corporateActions"]
        self.assertEqual(actions["dividendRowCount"], 0)
        self.assertEqual(actions["outsideAnalysisWindowDividendRowCount"], 1)
        excluded = actions["outsideAnalysisWindowReportedRows"][0]
        self.assertEqual(excluded["effectiveDate"], "2021-06-01")
        self.assertFalse(excluded["usedForHistoricalEligibilityOrWindowActions"])
        self.assertEqual(actions["status"], "unknown")

    def test_complete_fixed_sample_required_partial_is_explicit(self):
        c, a = pure_inputs(("sh600001", "sz000001"))
        bundle = self.bundle()
        with self.assertRaisesRegex(ValueError, "Missing fixed-universe"):
            normalize_history_coverage([bundle], c, a, start=START, end=END)
        p = normalize_history_coverage([bundle], c, a, start=START, end=END, require_all=False)
        self.assertTrue(p["partialEvidenceOnly"])
        self.assertEqual(p["notYetSuppliedSymbols"], ["sz000001"])

    def test_no_input_mutation_deterministic_fingerprint_and_post_load_tamper_rejected(self):
        bundle = self.bundle()
        before = deepcopy(bundle)
        a, b = self.normalized(bundle), self.normalized(bundle)
        self.assertEqual(bundle, before)
        self.assertEqual(a["fingerprint"], b["fingerprint"])
        self.assertNotIn("daily", coverage_summary(a)["securities"][0])
        bundle["responses"][1]["payload"]["rows"][0][2] = "80"
        with self.assertRaisesRegex(ValueError, "Verified response was mutated"):
            self.normalized(bundle)

    def test_calendar_requires_every_civil_day_and_strict_boolean_source_flags(self):
        rows = [[f"2022-01-{d:02}", "1" if d < 7 else "0"] for d in range(3, 8)]
        payload = {"provider": "baostock", "api": "query_trade_dates", "parameters": {
            "start_date": START, "end_date": END}, "retrievedAt": STAMP, "errorCode": "0",
            "fields": ["calendar_date", "is_trading_day"], "rows": rows, "rowBoundReached": False}
        ref = put(self.root, "calendar.json", payload)
        c = load_calendar(self.root / "calendar.json", ref["sha256"], start=START, end=END)
        self.assertEqual(len(c["dates"]), 4)
        payload["rows"].pop(1)
        ref = put(self.root, "calendar.json", payload)
        with self.assertRaisesRegex(ValueError, "Calendar has gaps"):
            load_calendar(self.root / "calendar.json", ref["sha256"], start=START, end=END)

    def test_audit_missing_union_reproduced_only_from_exact_dated_observations(self):
        payload = {"missingUnionSymbols": ["sh600001"], "missingUnionCount": 1, "dailySnapshots": [
            {"date": START, "missingFromFrozenSampleCount": 1, "membershipResponseSha256": "c" * 64,
             "missing": [{"symbol": "sh600001", "reportedTradeStatus": "0"}]}]}
        ref = put(self.root, "audit.json", payload)
        a = load_universe_audit(self.root / "audit.json", ref["sha256"])
        self.assertEqual(a["observations"]["sh600001"][0]["date"], START)
        self.assertEqual(a["observations"]["sh600001"][0]["membershipOnOtherDates"], "unknown")
        payload["dailySnapshots"][0]["missing"] = []
        ref = put(self.root, "audit.json", payload)
        with self.assertRaisesRegex(ValueError, "Membership count mismatch"):
            load_universe_audit(self.root / "audit.json", ref["sha256"])

    def test_cli_exclusive_output_receipt_binds_code_inputs_and_file_hashes(self):
        script = Path(__file__).resolve().parents[1] / "scripts/audit-signal-history-coverage.py"
        spec = importlib.util.spec_from_file_location("history_coverage_cli", script)
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        path, digest = make_bundle(self.root / "source")
        args = cli.parser().parse_args(["--manifest", str(path), digest, "--output", str(self.root / "output")])
        c, a = pure_inputs()
        # Substitute only the registered universe/calendar fixture. Exercise the
        # real batch loader, pure normalization, compression and exclusive I/O.
        source_before = {str(p): p.read_bytes() for p in (self.root / "source").rglob("*.json")}
        with patch.object(cli, "load_universe_audit", return_value=a), \
             patch.object(cli, "load_calendar", return_value=c), \
             patch.object(cli, "REGISTERED_UNIVERSE_COUNT", 1), \
             patch.object(cli, "REGISTERED_CALENDAR_COUNT", 5), \
             patch.object(cli, "START", START), patch.object(cli, "END", END):
            cli.execute(args)
            receipt = json.loads((args.output / "receipt.json").read_text("utf-8"))
            self.assertFalse(receipt["userDatabaseOpened"])
            self.assertEqual(receipt["inputs"]["manifests"][0]["sha256"], digest)
            for name, info in receipt["files"].items():
                self.assertEqual(sha256((args.output / name).read_bytes()), info["sha256"])
            self.assertEqual(receipt["code"]["service/signal_history_coverage.py"], sha256((script.parents[1] / "service/signal_history_coverage.py").read_bytes()))
            with self.assertRaisesRegex(ValueError, "Output exists"):
                cli.execute(args)
        self.assertEqual(source_before, {str(p): p.read_bytes() for p in (self.root / "source").rglob("*.json")})


if __name__ == "__main__":
    unittest.main()
