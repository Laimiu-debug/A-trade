"""Replay panel identities/ranks/persistence from raw history, without feature code.

Every own/relative return, rank and persistence history is checked. Path and
amount formula checks use 32 symbols chosen by a fixed hash before outcomes,
plus the first price-incomplete row each day. This does not validate a strategy.
"""
from collections import Counter, deque
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from statistics import mean
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/leadership-panel-v1"
FEATURE_FIELDS = ("return20", "return60", "relativeReturn20", "relativeReturn60", "relativeReturn20Percentile",
    "relativeReturn60Percentile", "meanStrengthPercentile20", "minimumStrengthPercentile20",
    "topQuantileSessionCount20", "positiveCloseReturnSessions20", "closePathMaxDrawdown20",
    "signedPathEfficiency20", "closeAboveMA20Sessions20", "amountToday", "prior20MeanAmount",
    "amountRelativeToOwnPrior20")
sys.path.insert(0, str(ROOT))
from service.portfolio_universe import snapshot_universe, _digest


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def near(a, b):
    if a is None or b is None:
        return a is b
    return type(a) in (int, float) and math.isfinite(a) and math.isclose(a, b, rel_tol=1e-11, abs_tol=1e-12)


def positive(x):
    try:
        return type(x) in (int, float) and math.isfinite(x) and x > 0
    except OverflowError:
        return False


def ratio(numerator, denominator):
    if not positive(numerator) or not positive(denominator):
        return None
    try:
        value = numerator / denominator
        return value if positive(value) else None
    except OverflowError:
        return None


def status(value):
    if value is False or type(value) is int and value == 0 or value == "0":
        return 0
    if value is True or type(value) is int and value == 1 or value == "1":
        return 1
    return None


def inputs(security, days):
    lookup = {bar["date"]: bar for bar in security.get("bars", [])}
    bars = [lookup.get(day) for day in days]
    flags = [status(security.get("tradeStatusByDate", {}).get(day)) for day in days]
    valid, amount = [], []
    for bar, flag in zip(bars, flags):
        volume = bar is not None and positive(bar.get("volume")) and flag != 0
        price = volume and all(positive(bar.get(k)) for k in ("open", "high", "low", "close"))
        price = price and bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]
        valid.append(bool(price))
        amount.append(bool(volume and positive(bar.get("amount"))))
    prefix = lambda values: [0] + list(__import__("itertools").accumulate(values))
    return {"bars": bars, "status": flags, "valid": valid, "amount": amount,
            "p": prefix(valid), "a": prefix(amount)}


def count(prefix, end, length):
    return prefix[end + 1] - prefix[max(0, end + 1 - length)] if end >= 0 else 0


def complete(item, end, length, key="p"):
    return end + 1 >= length and count(item[key], end, length) == length


def returns(item, bench, index, horizon):
    own = ratio(item["bars"][index]["close"], item["bars"][index - horizon]["close"]) if complete(item, index, horizon + 1) else None
    reference = ratio(bench["bars"][index]["close"], bench["bars"][index - horizon]["close"]) if complete(bench, index, horizon + 1) else None
    relative = ratio(own, reference)
    return (own - 1 if own is not None else None,
            relative - 1 if relative is not None else None)


def ranks(values):
    ordered = sorted((v, s) for s, v in values.items() if v is not None)
    result, n, start = {}, len(ordered), 0
    while start < n:
        end = start + 1
        while end < n and ordered[end][0] == ordered[start][0]:
            end += 1
        percentile = (start + end) / (2 * n)
        result.update((s, percentile) for _, s in ordered[start:end])
        start = end
    return result, n


def formulas(item, index):
    expected = {}
    names = ("positiveCloseReturnSessions20", "closePathMaxDrawdown20", "signedPathEfficiency20")
    if complete(item, index, 21):
        closes = [b["close"] for b in item["bars"][index-20:index+1]]
        differences = [b-a for a, b in zip(closes, closes[1:])]
        try:
            distance = math.fsum(abs(x) for x in differences)
        except OverflowError:
            distance = None
        peak, maximum = closes[0], 0.
        for close in closes:
            peak = max(peak, close)
            maximum = max(maximum, 1-close/peak)
        expected.update(zip(names, (sum(x > 0 for x in differences), maximum,
                                   (closes[-1]-closes[0])/distance if positive(distance) else None)))
    else:
        expected.update(dict.fromkeys(names))
    expected["closeAboveMA20Sessions20"] = sum(
        item["bars"][j]["close"] > mean(b["close"] for b in item["bars"][j-19:j+1])
        for j in range(index-19, index+1)) if complete(item, index, 39) else None
    expected["amountToday"] = item["bars"][index]["amount"] if item["amount"][index] else None
    expected["prior20MeanAmount"] = mean(b["amount"] for b in item["bars"][index-20:index]) if complete(item, index-1, 20, "a") else None
    expected["amountRelativeToOwnPrior20"] = ratio(expected["amountToday"], expected["prior20MeanAmount"])
    return expected


def main():
    started = last_notice = time.perf_counter()
    checks = failures = sampled = 0
    issues = []

    def check(ok, label, **details):
        nonlocal checks, failures
        checks += 1
        if not ok:
            failures += 1
            if len(issues) < 100:
                issues.append({"check": label, **details})

    manifest, receipt, summary = [read(OUT / name) for name in ("manifest.json", "completion.json", "summary.json")]
    registration = manifest["registration"]
    sample, frozen = registration["sample"], manifest["inputs"]
    mh = sha(OUT / "manifest.json")
    check(mh == receipt["sampleManifestHash"] == summary["sampleManifestHash"], "manifest-reference")
    check(sha(ROOT / "docs/signal-leadership-panel-v1.json") == manifest["registrationSha256"] == receipt["registrationSha256"], "registration-file")
    check(registration == read(ROOT / "docs/signal-leadership-panel-v1.json"), "registration-bytes-content")
    required = {"manifest.json", "features.jsonl.gz", "daily-coverage.jsonl.gz", "warmup-coverage.jsonl.gz", "summary.json", "execution-sources.zip", "attempt.json"}
    check(set(receipt["files"]) == required, "exact-output-files")
    for name, value in receipt["files"].items():
        check(name in required and value["path"] == name and sha(OUT / name) == value["sha256"], "output-hash", file=name)
    check(sha(OUT / "execution-sources.zip") == manifest["executionArchiveSha256"], "source-archive-hash")
    with zipfile.ZipFile(OUT / "execution-sources.zip") as archive:
        check(set(archive.namelist()) == set(manifest["implementation"]), "source-archive-members")
        for name, expected in manifest["implementation"].items():
            check(sha(ROOT / name) == expected == hashlib.sha256(archive.read(name)).hexdigest(), "execution-source-bytes", file=name)
    for name, expected in manifest["physicalSourceHashes"].items():
        check(sha(Path(name)) == expected, "physical-source-hash", file=Path(name).name)
    cal_spec = registration["calendar"]
    check(sha(ROOT / cal_spec["path"]) == cal_spec["sha256"], "saved-calendar-source")
    saved = read(ROOT / cal_spec["path"])
    days = [d for d, flag in saved["rows"] if flag == "1"]
    check(days == frozen["calendar"]["dates"] and _digest(days) == frozen["calendar"]["sha256"], "calendar-identity")
    spec = importlib.util.spec_from_file_location("_leader_independent_source", ROOT / "scripts/run-strategy-search.py")
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    dataset = source.bound_snapshot(snapshot_universe(ROOT / "artifacts/strategy-search/source-workspace",
        sample["selection"], sample["signalEnd"], sample["signalStart"]), sample["signalStart"], sample["signalEnd"])
    check(dataset["fingerprint"] == sample["expectedFullSnapshotFingerprint"] == frozen["snapshotFingerprint"], "raw-source-fingerprint")
    check(_digest(dataset) == frozen["fullDatasetContentSha256"], "complete-dataset-digest")
    securities = {s["symbol"]: s for s in dataset["securities"]}
    symbols = sorted(securities)
    check(symbols == frozen["symbols"] and len(symbols) == sample["expectedFullSecurities"] and _digest(symbols) == frozen["symbolsSha256"], "fixed-symbol-population")
    check(sum(len(s["bars"]) for s in securities.values()) == sample["expectedFullBars"], "raw-bar-count")
    check([d for d in dataset["calendar"] if days[0] <= d <= days[-1]] == days, "source-calendar-exact-equality")
    for symbol, security in securities.items():
        check(_digest(security["bars"]) == frozen["securities"][symbol]["retainedRawBarsSha256"], "retained-source-bars", symbol=symbol)
        evidence = {k: v for k, v in security.items() if k not in ("bars", "name", "sourceSha256", "forceSeed", "historyWindow")}
        check(_digest(evidence) == frozen["securities"][symbol]["datedEvidenceSha256"], "dated-source-evidence", symbol=symbol)
    benchmark_bars = [b for b in dataset["benchmark"]["bars"] if days[0] <= b["date"] <= days[-1]]
    check(_digest(benchmark_bars) == frozen["benchmark"]["rawBarsSha256"], "raw-benchmark-hash")
    raw = {symbol: inputs(security, days) for symbol, security in securities.items()}
    bench = inputs(dataset.get("benchmark") or {}, days)
    chosen = set(sorted(symbols, key=lambda s: hashlib.sha256(("leader-panel-formula-audit-v1:"+s).encode()).hexdigest())[:32])
    histories = {s: deque(maxlen=20) for s in symbols}
    history_counts, history_dates = deque(maxlen=20), deque(maxlen=20)
    target_coverage, warmup_coverage = list(records(OUT / "daily-coverage.jsonl.gz")), list(records(OUT / "warmup-coverage.jsonl.gz"))
    check([c["asOfDate"] for c in warmup_coverage+target_coverage] == days, "all-587-coverage-dates")
    daily_lookup = {c["asOfDate"]: c for c in warmup_coverage+target_coverage}
    stream = records(OUT / "features.jsonl.gz")
    total_rows = 0
    totals = {k: Counter() for k in ("featureNonNullCounts", "featureGroupCompleteness", "eligibilityReasons", "unknownHistoricalFields", "qualityCounts", "crossSectionDenominators")}
    for index, day in enumerate(days):
        expected_returns = {h: {s: returns(raw[s], bench, index, h) for s in symbols} for h in (20, 60)}
        ranking = {h: ranks({s: v[1] for s, v in expected_returns[h].items()}) for h in (20, 60)}
        history_counts.append(ranking[20][1])
        history_dates.append(day)
        for symbol in symbols:
            histories[symbol].append(ranking[20][0].get(symbol))
        cover = daily_lookup[day]
        check(set(cover["featureNonNullCounts"]) == set(FEATURE_FIELDS), "exact-feature-count-fields", day=day)
        check(cover["rows"] == len(symbols) and cover["sampleManifestHash"] == mh and cover["crossSectionDenominators"] == {str(h): ranking[h][1] for h in (20, 60)}, "daily-source-rank-denominators", day=day)
        if day < sample["signalStart"]:
            continue
        feature_counts, group_counts, reasons, unknown, quality = Counter(), Counter(), Counter(), Counter(), Counter()
        missing_sample_used = False
        for symbol in symbols:
            row = next(stream, None)
            if row is None:
                raise ValueError("Feature file ended before its fixed population")
            total_rows += 1
            check((row["asOfDate"], row["symbol"], row["sampleManifestHash"], row["featureVersion"]) == (day, symbol, mh, registration["featureVersion"]), "row-identity", day=day, symbol=symbol)
            check(row["sourceHashes"] == {**frozen["securities"][symbol], "benchmarkRawBarsSha256": frozen["benchmark"]["rawBarsSha256"]} and row["calendarHash"] == frozen["calendar"]["sha256"], "row-source-binding", day=day, symbol=symbol)
            for horizon in (20, 60):
                own, relative = expected_returns[horizon][symbol]
                check(near(row[f"return{horizon}"], own) and near(row[f"relativeReturn{horizon}"], relative)
                    and row[f"relativeReturn{horizon}Percentile"] == ranking[horizon][0].get(symbol)
                    and row[f"relativeReturn{horizon}CrossSectionCount"] == ranking[horizon][1], "raw-returns-and-rank", day=day, symbol=symbol, horizon=horizon)
            history = [None]*(20-len(histories[symbol])) + list(histories[symbol])
            known = [v for v in history if v is not None]
            check(row["trailing20DailyStrengthPercentiles"] == history
                and row["trailing20StrengthCrossSectionCounts"] == [None]*(20-len(history_counts))+list(history_counts)
                and row["strengthRankSessionDates20"] == [None]*(20-len(history_dates))+list(history_dates)
                and row["knownStrengthRankSessions20"] == len(known)
                and near(row["meanStrengthPercentile20"], mean(known) if len(known) == 20 else None)
                and row["minimumStrengthPercentile20"] == (min(known) if len(known) == 20 else None)
                and row["topQuantileSessionCount20"] is None, "archived-rank-persistence", day=day, symbol=symbol)
            expected_complete = {"security20": complete(raw[symbol], index, 21), "security60": complete(raw[symbol], index, 61),
                "benchmark20": complete(bench, index, 21), "benchmark60": complete(bench, index, 61),
                "path20": complete(raw[symbol], index, 21), "ma20": complete(raw[symbol], index, 39),
                "amountPrior20": complete(raw[symbol], index-1, 20, "a"), "amountToday": raw[symbol]["amount"][index]}
            check({k: c["complete"] for k, c in row["completeness"].items()} == expected_complete
                and row["knownAmountSessions20"] == count(raw[symbol]["a"], index-1, 20), "independent-group-availability", day=day, symbol=symbol)
            if symbol in chosen or not missing_sample_used and not expected_complete["security60"]:
                sampled += 1
                if not expected_complete["security60"]:
                    missing_sample_used = True
                expected = formulas(raw[symbol], index)
                check(all(near(row[k], v) for k, v in expected.items()), "raw-path-and-amount-formulas", day=day, symbol=symbol)
            check(row["certified"] is False and row["targetAchieved"] is False and row["historicalEligibilityVerified"] is False
                and row["baseEligibility"]["affectsFeaturesOrRanks"] is False, "diagnostic-only-flags", day=day, symbol=symbol)
            for key in FEATURE_FIELDS:
                feature_counts[key] += row[key] is not None
            for key, value in expected_complete.items():
                group_counts[key + (":complete" if value else ":incomplete")] += 1
            state = row["baseEligibility"]
            reasons[state["reason"]] += 1
            evidence_keys = {"historicalSt": "historicalSt", "historicalMembership": "historicalMembership",
                "listingDate": "listingDate", "tradeStatus": "historicalTradeStatus"}
            unknown.update(mapped for key, mapped in evidence_keys.items() if row["asOfEvidence"][key]["status"] == "unknown")
            quality["eligible" if state["eligible"] else "ineligible"] += 1
            for field in ("missingDates", "invalidDates", "haltDates", "unknownTradeStatusDates"):
                quality[field+":current"] += day in row[field]
                quality[field+":anyWindow"] += bool(row[field])
        calculated = {"featureNonNullCounts": dict(feature_counts), "featureGroupCompleteness": dict(group_counts),
            "eligibilityReasons": dict(reasons), "unknownHistoricalFields": dict(unknown), "qualityCounts": dict(quality),
            "crossSectionDenominators": {str(h): ranking[h][1] for h in (20, 60)}}
        check(all(cover[k] == v for k, v in calculated.items()), "daily-coverage-arithmetic", day=day)
        for key in totals:
            totals[key].update(calculated[key])
        if time.perf_counter()-last_notice > 30:
            print(f"Independent panel replay: {total_rows}/{sample['expectedFeatureRows']} rows", flush=True)
            last_notice = time.perf_counter()
    check(next(stream, None) is None and total_rows == sample["expectedFeatureRows"] == summary["featureRows"] == receipt["featureRows"], "complete-row-count-no-trailing-rows")
    check(len(target_coverage) == sample["expectedScoredSessions"] == summary["targetSessions"] and len(warmup_coverage) == sample["expectedWarmupSessions"] == summary["warmupSessions"], "target-warmup-counts")
    check(all(summary[key] == dict(value) for key, value in totals.items()), "summary-coverage-arithmetic")
    check(summary["strategyReturnsCalculated"] is False and summary["forwardReturnsCalculated"] is False
        and summary["backwardPriceReturnsCalculated"] is True and summary["tradePoliciesAdded"] == 0, "no-trading-or-forward-label-output")
    report = {"schemaVersion": 1, "checkedAt": datetime.now(timezone.utc).isoformat(), "passed": failures == 0,
        "checks": checks, "failureCount": failures, "issues": issues, "featureRows": total_rows,
        "formulaRowsReplayed": sampled, "fixedFormulaSampleSymbols": sorted(chosen),
        "sampleManifestHash": mh, "verifierSha256": sha(Path(__file__)), "elapsedSeconds": time.perf_counter()-started,
        "scope": "Every target-row identity, own/relative return, rank, rank history and feature-group complete flag; target-day/total coverage arithmetic. Warmup: date, row count and raw-source rank denominators. Raw path/amount formulas: fixed hash sample plus first incomplete-price row each target day.",
        "limitations": ["Shares the original read-only source loader, not the feature implementation; this cannot independently certify source completeness.",
            "Eligibility arithmetic is reconciled from recorded as-of states, not independently reimplemented; focused synthetic tests cover future-invalid fallback.",
            "Detailed missing/invalid/status date arrays and per-group observed/valid counts are not independently replayed in full; their target-day coverage arithmetic is reconciled, while feature-group complete flags and required-window ranks are recomputed from raw history.",
            "No leader truth, causal influence, future return, corporate-action profit, execution or target-probability certification."],
        "certified": False, "targetAchieved": False}
    filename = "independent-verification.json" if failures == 0 else "independent-verification-failed-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+".json"
    with (OUT / filename).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"passed": failures == 0, "checks": checks, "failures": failures, "formulaRows": sampled}), flush=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
