"""Independent artifact arithmetic/timing audit; not an execution certification."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/strategy-search/signal-sequence-price-v1"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def same_number(actual, expected):
    if actual is None or expected is None:
        return actual is expected
    return (type(actual) in (int, float) and type(expected) in (int, float)
            and math.isfinite(actual) and math.isfinite(expected)
            and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12))


def save_report(report):
    # Never replace either successful evidence or a failed verification run.
    name = "independent-result-verification.json"
    if not report.get("passed") or (OUTPUT / name).exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        name = f"independent-result-verification-{'passed' if report.get('passed') else 'failed'}-{stamp}.json"
    path = OUTPUT / name
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return path


def main():
    issues, checks = [], 0
    def check(ok, label, **details):
        nonlocal checks
        checks += 1
        if not ok:
            issues.append({"check": label, **details})
    protocol = read(OUTPUT / "protocol.json")
    attempt = OUTPUT / "attempts/attempt-0001"
    receipt = read(attempt / "completion.json")
    protocol_hash = hashlib.sha256(json.dumps(protocol, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    snapshot = read(OUTPUT / "snapshot.json")
    check(receipt["protocolHash"] == protocol_hash, "receipt-protocol-hash")
    check(receipt["snapshotFingerprint"] == snapshot["fingerprint"] ==
          protocol["registration"]["sample"]["expectedFullSnapshotFingerprint"], "receipt-snapshot-fingerprint")
    for key, info in receipt["files"].items():
        check(sha(OUTPUT / info["path"]) == info["sha256"], "artifact-hash", file=key)
    for name, expected in protocol["implementation"].items():
        check(sha(ROOT / name) == expected, "implementation-hash", file=name)
    check(set(receipt["files"]) == {"candidates", "coverage", "signals", "result", "summary", "transitions", "finalStates"}, "seven-required-artifacts")
    with gzip.open(attempt / "result.json.gz", "rt", encoding="utf-8") as stream:
        result = json.load(stream)
    coverage = list(lines(attempt / "coverage.jsonl.gz"))
    dates = [row["date"] for row in coverage]
    check(dates == sorted(set(dates)) and len(dates) == 484, "ordered-unique-484-sessions")
    check(Counter(day[:4] for day in dates) == {"2022": 242, "2023": 242} and
          dates[0] == "2022-01-04" and dates[-1] == "2023-12-29", "registered-calendar-boundaries")
    index = {day: i for i, day in enumerate(dates)}
    sid = protocol["strategyOrder"][0]
    candidates = list(lines(attempt / "candidates.jsonl.gz"))
    signals = list(lines(attempt / "signals.jsonl.gz"))
    by_day = defaultdict(list)
    for row in candidates:
        by_day[row["signalDate"]].append(row)
        b, r, t = (row["formationFacts"][key] for key in ("B", "R", "T"))
        bi, ri, ti = (index[row[key]] for key in ("anchorDate", "retestDate", "signalDate"))
        check(1 <= ri - bi <= 9 and ri < ti <= bi + 10, "formation-clock", symbol=row["symbol"])
        anchor = row["anchor"]
        check(b["close"] > anchor and b["close"] > b["ma80"] > b["previousMa80"], "breakout-prices")
        check(b["volume"] / b["prior40MeanVolume"] >= 1.2, "breakout-volume")
        check(anchor <= r["low"] <= 1.02 * anchor and r["close"] < b["close"] and r["volume"] <= .8 * b["volume"], "retest-conditions")
        check(t["close"] > r["high"] and t["close"] > t["priorSessionClose"] and t["volume"] >= b["volume"], "renewal-conditions")
        check(abs(row["score"] + (t["close"] / anchor - 1)) < 1e-12, "negative-distance-score")
        check(row.get("certified") is False and row.get("actionEvidence") == "unverified", "raw-candidate-not-certified")
    for day, rows in by_day.items():
        expected = sorted(rows, key=lambda row: (row["confirmedClose"] / row["anchor"] - 1, row["symbol"]))
        check(rows == expected, "whole-cross-section-rank", date=day)
        check([row["rank"] for row in rows] == list(range(1, len(rows) + 1)), "consecutive-ranks", date=day)
        check(all(row["selectedTop5"] == (i < 5) for i, row in enumerate(rows)), "top-five-no-backfill", date=day)
    identity = lambda row: (row["strategyId"], row["symbol"], row["signalDate"])
    check(Counter(identity(row) for row in signals if row["strategyId"] == sid) ==
          Counter(identity(row) for row in candidates if row["selectedTop5"]), "selected-raw-signals-exact-top-five")
    admitted = result["events"] + result["overlapSuppressed"]
    check(Counter(identity(row) for row in signals) == Counter(identity(row) for row in admitted), "all-selected-signal-dispositions")
    transition_counts = defaultdict(Counter)
    last = {}
    for row in lines(attempt / "transitions.jsonl.gz"):
        transition_counts[row["date"]][row["kind"]] += 1
        previous = last.get(row["symbol"])
        if previous and previous["after"]["name"].startswith("waitingFor"):
            check(index[row["date"]] == index[previous["date"]] + 1, "waiting-advanced-every-session", symbol=row["symbol"])
            check(row["before"] == previous["after"], "waiting-state-continuity", symbol=row["symbol"])
        last[row["symbol"]] = row
    for row in coverage:
        written = transition_counts[row["date"]]
        omitted = row["transitionsOmitted"]
        check(sum(written.values()) == row["transitionsWritten"], "written-transition-count", date=row["date"])
        check(sum(written.values()) + omitted == row["sequenceSteps"] == 3141, "all-security-session-steps", date=row["date"])
        check(sum(row["transitionCounts"].values()) == row["sequenceSteps"], "kind-count-reconciliation")
        check(sum(row["omittedTransitionReasons"].values()) == omitted, "omitted-reason-reconciliation")
        omitted_kinds = Counter()
        for key, count in row["omittedTransitionReasons"].items():
            kind = key.split(":", 1)[0]
            check(kind in {"notTriggered", "unevaluable"}, "only-idle-transitions-omitted", date=row["date"])
            omitted_kinds[kind] += count
        check(written + omitted_kinds == Counter(row["transitionCounts"]), "per-kind-transition-reconciliation", date=row["date"])
        match = row["controlMatching"][sid]
        top = {s["symbol"] for s in by_day[row["date"]] if s["selectedTop5"]}
        check(len(match["selectedSymbols"]) == match["matchedCount"] and not top.intersection(match["selectedSymbols"]), "control-count-and-top-exclusion")
    final = list(lines(attempt / "final-state.jsonl.gz"))
    check(len(final) == len({row["symbol"] for row in final}) == 3141, "all-final-states")
    for row in final:
        if row["symbol"] in last:
            check(row["state"] == last[row["symbol"]]["after"], "final-state-matches-last-transition")
    event_by_id = {row["eventId"]: row for row in result["events"]}
    check(len(event_by_id) == len(result["events"]), "unique-event-identities")
    for suppressed in result["overlapSuppressed"]:
        prior = event_by_id.get(suppressed.get("blockedByEventId"))
        check(prior is not None, "suppression-prior-event-exists", signal=identity(suppressed))
        if prior is None:
            continue
        check((prior["strategyId"], prior["symbol"]) == (suppressed["strategyId"], suppressed["symbol"])
              and prior["entry"]["status"] == "filled", "suppression-same-group-filled-symbol", signal=identity(suppressed))
        check(prior["signalDate"] < suppressed["signalDate"] and prior["entry"]["date"] <= suppressed["signalDate"],
              "suppression-prior-entry-available", signal=identity(suppressed))
        hit_day = prior["outcomes"]["nextOpenNet30"]["hitDate"]
        check(hit_day is None or suppressed["signalDate"] < hit_day,
              "suppression-still-active-at-close-no-same-day-exit-block", signal=identity(suppressed))
    previous_entry_by_group_symbol = {}
    for event in result["events"]:
        entry = event["entry"]
        key = (event["strategyId"], event["symbol"])
        prior = previous_entry_by_group_symbol.get(key)
        if prior is not None:
            hit_day = prior["outcomes"]["nextOpenNet30"]["hitDate"]
            check(hit_day is not None and hit_day <= event["signalDate"], "admitted-event-released-at-open", eventId=event["eventId"])
        if entry["date"]:
            check(index[entry["date"]] == index[event["signalDate"]] + 1, "one-next-session-entry")
        if entry["status"] != "filled":
            continue
        previous_entry_by_group_symbol[key] = event
        check(abs(entry["quantity"] * entry["price"] + entry["fee"] - entry["investedCapital"]) < 1e-7, "entry-cost-arithmetic")
        hit = event["outcomes"]["nextOpenNet30"]
        if hit["hitDate"]:
            check(hit["tradingDays"] == index[hit["hitDate"]] - index[entry["date"]] + 1 >= 2, "target-date-and-t-plus-one")
            net = event["terminal"]["proceeds"] / entry["investedCapital"] - 1
            check(net >= .3 - 1e-10 and abs(100 * net - event["terminal"]["netReturnPct"]) < 1e-8, "target-net-arithmetic")
    for group in result["groups"]:
        group_events = [e for e in result["events"] if e["strategyId"] == group["strategyId"]]
        entered = [e for e in group_events if e["entry"]["status"] == "filled"]
        check(Counter(e["entry"]["status"] for e in group_events) == Counter(group["entryStatusCounts"]), "entry-status-denominators")
        check(len(group_events) == group["signalCount"] and same_number(group["entryFillRate"],
              len(entered) / len(group_events) if group_events else None), "entry-fill-rate-arithmetic")
        check(sum(e["outcomes"]["nextOpenNet30"]["hitDate"] is None for e in entered) ==
              group["rightCensoredEventCount"], "unfinished-event-count")
        check(len(entered) == group["filledEntryCount"], "filled-count")
        for report in group["horizons"]:
            h = report["tradingDays"]
            mature = [e for e in entered if len(dates) - index[e["entry"]["date"]] >= h]
            valid = []
            for e in mature:
                stop = min(h, e["outcomes"]["nextOpenNet30"]["tradingDays"] or h)
                if e["entry"]["executionValid"] is True and not any(x["tradingDay"] <= stop for x in e["observation"]["missingDates"]) and not any(x.get("tradingDay", 1) <= stop for x in e["quality"]["executionCapacityIssues"]):
                    valid.append(e)
            check(len(mature) == report["completeCalendarCohort"] and len(valid) == report["eligibleEntryCohort"], "mature-valid-denominators")
            check(len(entered) - len(mature) == report["rightCensoredBeforeHorizon"], "right-censored-before-horizon")
            unresolved = len(mature) - len(valid)
            check(unresolved == report["missingOrInvalidDataExcluded"], "unresolved-outcome-count")
            for proxy, values in report["proxies"].items():
                times = [e["outcomes"][proxy]["tradingDays"] for e in valid if e["outcomes"][proxy]["tradingDays"] is not None and e["outcomes"][proxy]["tradingDays"] <= h]
                check(len(times) == values["hits"] and len(valid) == values["denominator"], "target-hit-denominator")
                check((median(times) if times else None) == values["medianHitTradingDays"], "successful-time-median")
                rate = len(times) / len(valid) if valid else None
                lower = len(times) / len(mature) if mature else None
                upper = (len(times) + unresolved) / len(mature) if mature else None
                bounds = values["missingOutcomeBounds"]
                check(same_number(values["hitRate"], rate), "hit-rate-arithmetic", strategy=group["strategyId"], horizon=h, proxy=proxy)
                check(bounds["denominator"] == len(mature) and bounds["unresolved"] == unresolved and
                      same_number(bounds["lower"], lower) and same_number(bounds["upper"], upper),
                      "missing-outcome-bounds-arithmetic", strategy=group["strategyId"], horizon=h, proxy=proxy)
    check(result.get("targetAchieved") is False and result.get("certified") is False, "no-goal-certification")
    report = {"schemaVersion": 1, "checkedAt": datetime.now(timezone.utc).isoformat(), "checks": checks,
        "issues": issues, "passed": not issues, "resultSha256": sha(attempt / "result.json.gz"),
        "verifierSha256": sha(Path(__file__)), "candidateCount": len(candidates), "eventCount": len(result["events"]),
        "scope": "Independent artifact arithmetic, formation chronology, full-pool log accounting and frozen source hashes",
        "limitations": ["No independent raw-market replay or entitlement ledger; formation arithmetic uses recorded causal facts.",
                        "Does not prove historical membership completeness, opening fills, control hash pool or independent future success probability."],
        "certified": False, "targetAchieved": False}
    report_path = save_report(report)
    print(json.dumps({"checks": checks, "issues": issues[:12], "passed": not issues, "reportPath": str(report_path)}))
    return int(bool(issues))


if __name__ == "__main__":
    try:
        result = main()
    except Exception as exc:
        report = {"schemaVersion": 1, "checkedAt": datetime.now(timezone.utc).isoformat(), "passed": False,
            "verificationCompleted": False, "errorType": type(exc).__name__, "error": str(exc),
            "verifierSha256": sha(Path(__file__)), "certified": False, "targetAchieved": False}
        report_path = save_report(report)
        print(json.dumps({"passed": False, "errorType": type(exc).__name__, "reportPath": str(report_path)}))
        raise
    raise SystemExit(result)
