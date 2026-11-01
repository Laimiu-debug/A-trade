"""Attribute completed-account episodes to their entry signal's event.

Reads immutable inputs and creates one exclusive event-outcomes.json only after
all six accounts complete. These are entry-event cohorts, not calendar-phase
portfolios. No account replay, rule change, or event-return compounding occurs.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import median


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/relative-repair-screen-v1"
PERIODS = ("2022-2023", "2024-2025")
GROUPS = ("allRepaired", "repaired", "matchedControl")
SID = "relativeRepairEventV1"


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read(path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz"
          else path.open(encoding="utf-8")) as stream:
        return json.load(stream)


def number(value):
    require(value is None or (type(value) in (int, float) and math.isfinite(value)),
            "Invalid number; unknown must remain null")
    return value


def total(values):
    values = [number(x) for x in values]
    return math.fsum(values) if all(x is not None for x in values) else None


def average(values):
    amount = total(values)
    return amount / len(values) if values and amount is not None else None


def near(actual, expected, label):
    number(actual)
    number(expected)
    require(actual is None if expected is None else actual is not None
            and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7), label)


def stats(values):
    for value in values:
        require(number(value) is not None and value >= 0, "Invalid matching distance")
    return {"count": len(values), "minimum": min(values) if values else None,
            "median": median(values) if values else None, "maximum": max(values) if values else None}


def signal(group, event, day, symbol, rank):
    return {"signalId": f"{group}:{event}:{day}:{symbol}", "strategyId": SID,
            "symbol": symbol, "signalDate": day, "originalRank": rank}


def event_structure(evidence):
    events, schedules, daily = evidence["events"], evidence["schedules"], evidence["dailyEvidence"]
    require(set(schedules) == set(GROUPS), "Expected all three groups")
    event_map = {e["eventId"]: e for e in events}
    require(len(event_map) == len(events), "Duplicate event ID")
    require(all(e["eventId"] == "relativeRepair:" + e["triggerDate"] for e in events), "Event ID mismatch")
    require([r["date"] for r in daily] == sorted({r["date"] for r in daily}), "Duplicate or unsorted daily evidence")
    rows = {key: [] for key in event_map}
    expected = {group: [] for group in GROUPS}
    membership = {}

    def append(group, event, day, symbol, rank):
        item = signal(group, event, day, symbol, rank)
        require(item["signalId"] not in membership, "Duplicate signal ID")
        expected[group].append(item)
        membership[item["signalId"]] = event

    for row in daily:
        key = row.get("eventId")
        if key is None:
            require(not row["selectedTop5"] and not row["pairs"], "Signals outside event")
            continue
        require(key in event_map, "Foreign daily event")
        rows[key].append(row)
        require(event_map[key]["triggerDate"] <= row["date"] <= event_map[key]["endDate"], "Daily event dates differ")
        selected = row["selectedTop5"]
        require(len(selected) <= 5 and [r["rank"] for r in selected] == list(range(1, len(selected) + 1)),
                "Top5 ranks differ")
        require(selected == [r for r in row["firstCrosses"] if r["selected"]], "Top5 differs from first-cross evidence")
        for item in selected:
            require(item["date"] == row["date"] and item["status"] == "eligibleFirstCross", "Unqualified Top5")
            append("allRepaired", key, row["date"], item["symbol"], item["rank"])
        paired_ranks, controls = set(), set()
        for pair in row["pairs"]:
            rank = pair["originalRank"]
            require(pair["eventId"] == key and pair["date"] == row["date"], "Pair event/date mismatch")
            require(rank not in paired_ranks and 1 <= rank <= len(selected)
                    and selected[rank - 1]["symbol"] == pair["repairedSymbol"], "Pair changed original rank")
            require(pair["controlSymbol"] not in controls, "Reused daily control")
            paired_ranks.add(rank)
            controls.add(pair["controlSymbol"])
            for group, field in (("repaired", "repairedSymbol"), ("matchedControl", "controlSymbol")):
                append(group, key, row["date"], pair[field], rank)
        require(len(row["unmatched"]) == len(selected) - len(paired_ranks)
                and {r["originalRank"] for r in row["unmatched"]} == set(range(1, len(selected) + 1)) - paired_ranks,
                "Unmatched Top5 gap differs")
    diagnostics = []
    for event in events:
        key = event["eventId"]
        days = rows[key]
        crosses = [r for d in days for r in d["firstCrosses"]]
        pairs = [r for d in days for r in d["pairs"]]
        require(crosses == event["firstCrosses"] and pairs == event["pairs"], "Event aggregate evidence differs")
        n_top = sum(len(d["selectedTop5"]) for d in days)
        unmatched = sum(len(d["unmatched"]) for d in days)
        require(n_top == len(pairs) + unmatched, "Pairing shortfall arithmetic differs")
        diagnostics.append({"eventId": key, "triggerDate": event["triggerDate"],
            "peakDate": event["peakDate"], "peakClose": event["peakClose"],
            "endDate": event["endDate"], "endReason": event["endReason"], "censored": event["censored"],
            "observedSessions": event["observedSessions"],
            "validFrozenPopulationCount": event["population"]["validPopulationCount"],
            "frozenCandidateCount": len(event["candidates"]), "frozenCandidates": deepcopy(event["candidates"]),
            "unknownStockCount": len(event["unknownSymbols"]), "unknownStocks": deepcopy(event["unknownSymbols"]),
            "unknownObservationCounts": dict(sum((Counter(d["unknownCounts"]) for d in days), Counter())),
            "firstPriceCrossCount": len(crosses), "crossStatuses": dict(Counter(r["status"] for r in crosses)),
            "eligibleTop5Count": n_top, "matchedPairCount": len(pairs), "unmatchedTop5Count": unmatched,
            "pairDistances": stats([p["distance"] for p in pairs]), "groups": {}})
    for group in GROUPS:
        require(schedules[group] == expected[group], "Schedule differs from daily Top5/pair evidence: " + group)
    return diagnostics, membership


def cohort_metrics(opportunities, closed, opened):
    returns, pnls = [r["netReturnPct"] for r in closed], [r["pnlAmount"] for r in closed]
    counts = Counter(r["pnlState"] for r in closed)
    return {"opportunities": len(opportunities),
        "opportunityStatuses": dict(Counter(r["status"] for r in opportunities)),
        "actualEntries": sum(r.get("filledQuantity", 0) > 0 for r in opportunities),
        "closedCount": len(closed), "wins": counts["win"], "losses": counts["loss"],
        "zeros": counts["zero"], "unknownPnlCount": counts["unknown"],
        "unknownReturnCount": sum(r is None for r in returns),
        "meanClosedNetReturnPct": average(returns), "closedNetPnlAmount": total(pnls),
        "openCountAtCutoff": len(opened), "openMarketValue": total([r["marketValue"] for r in opened]),
        "openUnrealizedPnlAmount": total([r["unrealizedPnlAmount"] for r in opened]),
        "realizedPnlWhileStillOpen": total([r["realizedPnlWhileOpen"] for r in opened]),
        "staleMarksAtCutoff": sum(r["markStaleAtCutoff"] for r in opened)}


def attribute_account(group, schedule, membership, event_ids, result, summary):
    require(result["status"] == summary["account"]["status"] == "completed", "Account incomplete")
    require(summary["policyId"] == group and summary["inputResultSha256"] == digest(result), "Summary/result binding differs")
    require(not summary["quality"]["integrityIssues"], "Account summary has integrity issues")
    require(summary["opportunityDispositions"]["opportunities"] == result["research"]["opportunities"], "Opportunity summary differs")
    by_signal = {r["signalId"]: r for r in schedule}
    opportunities = result["research"]["opportunities"]
    require(len(by_signal) == len(schedule) == len(opportunities), "Schedule/opportunity cardinality differs")
    buckets = {key: {"opportunities": [], "closed": [], "open": []} for key in event_ids}
    entries, found = {}, set()
    buy_rows = [r for r in result["trades"] if r["side"] == "buy"]
    buys = {r["fillId"]: r for r in buy_rows}
    require(len(buys) == len(buy_rows), "Duplicate buy fill ID")
    used_buys = set()
    for opportunity in opportunities:
        sid = opportunity["signalId"]
        require(sid in by_signal and sid not in found, "Foreign or duplicate opportunity")
        found.add(sid)
        require(all(opportunity.get(k) == v for k, v in by_signal[sid].items()), "Opportunity signal fields changed")
        event = membership[sid]
        buckets[event]["opportunities"].append(deepcopy(opportunity))
        quantity = opportunity.get("filledQuantity", 0)
        require(number(quantity) is not None and quantity >= 0, "Invalid filled quantity")
        if quantity > 0:
            key = (opportunity["symbol"], opportunity["attemptDate"])
            require(key not in entries, "Non-unique symbol/attemptDate entry")
            entries[key] = {"signalId": sid, "eventId": event, "originalRank": opportunity["originalRank"]}
            fill_id = opportunity["fillId"]
            require(fill_id in buys and fill_id not in used_buys, "Missing/duplicate entry fill")
            fill = buys[fill_id]
            require((fill["symbol"], fill["date"]) == key and fill["quantity"] == quantity
                    and fill["signalId"] == sid, "Entry fill differs from opportunity")
            used_buys.add(fill_id)
    require(used_buys == set(buys), "Unattributed buy fills")
    require([r["record"] for r in summary["closedEpisodes"]["episodes"]] == sorted(result["closedTrades"],
        key=lambda r: (r["exitDate"], r["entryDate"], r["symbol"])), "Closed summary records differ")
    require(sorted([r["record"] for r in summary["openPositions"]["positions"]], key=lambda r: r["symbol"])
            == sorted(result["positions"], key=lambda r: r["symbol"]), "Open summary records differ")
    consumed = set()
    for kind, records in (("closed", summary["closedEpisodes"]["episodes"]),
                          ("open", summary["openPositions"]["positions"])):
        for item in records:
            record = item["record"]
            key = (record["symbol"], record["entryDate"])
            require(key in entries and key not in consumed, "Missing or duplicate episode entry mapping")
            consumed.add(key)
            mapped = deepcopy(item)
            mapped.update(entries[key])
            if kind == "closed":
                pnl, invested = number(record["pnlAmount"]), number(record["investedAmount"])
                expected_return = 100 * pnl / invested if pnl is not None and invested is not None and invested > 0 else None
                near(item["pnlAmount"], pnl, "Closed PnL differs")
                near(item["netReturnPct"], expected_return, "Closed return differs")
                require(item["pnlState"] == ("unknown" if pnl is None else "win" if pnl > 0 else "loss" if pnl < 0 else "zero"), "Closed state differs")
            else:
                market, cost = number(record["marketValue"]), number(record["costBasis"])
                near(item["marketValue"], market, "Open market value differs")
                near(item["unrealizedPnlAmount"], market - cost if market is not None and cost is not None else None, "Open unrealized differs")
                near(item["realizedPnlWhileOpen"], record["realizedPnl"], "Open realized differs")
            buckets[entries[key]["eventId"]][kind].append(mapped)
    require(consumed == set(entries), "Filled opportunity lacks closed episode or terminal open position")
    attributed = {}
    for event, bucket in buckets.items():
        attributed[event] = {**cohort_metrics(bucket["opportunities"], bucket["closed"], bucket["open"]),
            "entryAttributions": [{**r, "symbol": key[0], "entryDate": key[1]}
                                  for key, r in entries.items() if r["eventId"] == event],
            "closedEpisodes": bucket["closed"], "openPositions": bucket["open"]}
    combined = cohort_metrics(opportunities, [r for b in buckets.values() for r in b["closed"]],
                              [r for b in buckets.values() for r in b["open"]])
    expected_closed = summary["closedEpisodes"]
    for field, original in (("closedCount", "count"), ("wins", "wins"), ("losses", "losses"),
                            ("zeros", "zeros"), ("unknownPnlCount", "unknownPnlCount"),
                            ("unknownReturnCount", "unknownReturnCount")):
        require(combined[field] == expected_closed[original], "Closed total differs: " + field)
    near(combined["meanClosedNetReturnPct"], expected_closed["meanReturnPct"], "Weighted episode mean differs")
    near(combined["closedNetPnlAmount"], expected_closed["netPnlAmount"], "Closed PnL total differs")
    require(combined["actualEntries"] == len(entries) == combined["closedCount"] + combined["openCountAtCutoff"], "Entry count bridge differs")
    require(combined["openCountAtCutoff"] == summary["openPositions"]["count"], "Open count differs")
    require(combined["actualEntries"] == summary["account"]["buyFillCount"], "Buy count differs")
    near(combined["openMarketValue"], summary["account"]["finalMarketValue"], "Market total differs")
    near(combined["openUnrealizedPnlAmount"], summary["reconciliation"]["openPositionUnrealizedPnl"], "Unrealized total differs")
    near(combined["realizedPnlWhileStillOpen"], summary["reconciliation"]["openPositionRealizedPnl"], "Open realized total differs")
    for field in ("opportunities", "actualEntries", "closedCount", "wins", "losses", "zeros", "openCountAtCutoff"):
        require(sum(a[field] for a in attributed.values()) == combined[field], "Event counts do not sum: " + field)
    for field in ("closedNetPnlAmount", "openMarketValue", "openUnrealizedPnlAmount", "realizedPnlWhileStillOpen"):
        near(total([a[field] for a in attributed.values()]), combined[field], "Event money does not sum: " + field)
    components = total([combined["closedNetPnlAmount"], combined["openUnrealizedPnlAmount"], combined["realizedPnlWhileStillOpen"]])
    account_pnl = summary["account"]["netPnlAmount"]
    residual = account_pnl - components if account_pnl is not None and components is not None else None
    near(residual, summary["reconciliation"]["episodePnlBridge"]["residual"], "Account bridge residual differs")
    combined.update(group=group, reconciliation="passed", accountNetPnlAmount=account_pnl,
        accountReturnPct=summary["account"]["netReturnPct"], unallocatedPnlBridgeResidual=residual,
        valuationUnreliableDates=deepcopy(summary["quality"]["valuationUnreliableDates"]),
        meanReturnBasis="all closed episodes equally weighted; never average event means",
        attributionBasis="entry signal event; open PnL separate from closed net PnL")
    return attributed, combined


def summarize(directory):
    plan_path, comparison_path = directory / "plan.json", directory / "comparison.json"
    require(comparison_path.is_file(), "All-six-account completion comparison is required")
    require(not (directory / "failure.json").exists(), "Failed run cannot be summarized")
    plan, comparison = read(plan_path), read(comparison_path)
    require(comparison["planSha256"] == sha(plan_path), "Frozen plan changed")
    require(len(comparison["rows"]) == 6 and {(r["period"], r["id"]) for r in comparison["rows"]}
            == {(p, g) for p in PERIODS for g in GROUPS}, "Expected six completed account rows")
    require(plan["strategyId"] == SID and plan["configurationsNewlyExecuted"] == 6, "Wrong experiment")
    paths = [plan_path, comparison_path]
    for period in PERIODS:
        names = ["signals-evidence.json.gz"] + [g + suffix for g in GROUPS
                for suffix in (".signals.json", ".result.json.gz", ".summary.json")]
        for name in names:
            path = directory / period / name
            require(sha(path) == comparison["artifacts"][period][name], "Completed artifact changed: " + str(path))
            paths.append(path)
    hashes = {p.relative_to(directory).as_posix(): sha(p) for p in paths}
    output = {"schema": "relative-repair-event-outcomes-v1", "createdAt": datetime.now(timezone.utc).isoformat(),
        "scriptSha256": sha(Path(__file__)), "inputArtifacts": hashes, "periods": {},
        "interpretation": "Entry-event cohorts from six already-completed accounts; no new account execution.",
        "limitations": ["Event closed means are trade averages, not event portfolio returns or compound returns.",
            "Closed PnL excludes terminal open PnL; any reconciliation residual stays unallocated.",
            "Matched signals need not both fill; account episodes are not forced into matched trades.",
            "Existing raw-price, sample, valuation and already-inspected-history limitations remain.",
            "Attribution is not causal proof, strategy certification or new selection evidence."],
        "certified": False, "targetAchieved": False}
    for period in PERIODS:
        base = directory / period
        evidence = read(base / "signals-evidence.json.gz")
        events, membership = event_structure(evidence)
        account_totals = {}
        for group in GROUPS:
            schedule = read(base / (group + ".signals.json"))
            require(schedule == evidence["schedules"][group], "Saved schedule differs from evidence")
            attributed, totals = attribute_account(group, schedule, membership, [e["eventId"] for e in events],
                read(base / (group + ".result.json.gz")), read(base / (group + ".summary.json")))
            for event in events:
                event["groups"][group] = attributed[event["eventId"]]
            account_totals[group] = totals
        output["periods"][period] = {"eventCount": len(events),
            "endReasonCounts": dict(Counter(e["endReason"] for e in events)),
            "censoredEventCount": sum(e["censored"] for e in events),
            "eligibleTop5Count": sum(e["eligibleTop5Count"] for e in events),
            "matchedPairCount": sum(e["matchedPairCount"] for e in events),
            "unmatchedTop5Count": sum(e["unmatchedTop5Count"] for e in events),
            "pairDistances": stats([p["distance"] for e in evidence["events"] for p in e["pairs"]]),
            "events": events, "accounts": account_totals}
    require(all(sha(p) == hashes[p.relative_to(directory).as_posix()] for p in paths), "Input changed during summary")
    return output


def self_test():
    require(average([]) is None and average([None]) is None and total([]) == 0, "Empty/unknown handling")
    opportunities = [{"status": "filled", "filledQuantity": 100}, {"status": "notPlanned"}]
    closed = [{"netReturnPct": 5, "pnlAmount": 10, "pnlState": "win"},
              {"netReturnPct": -1, "pnlAmount": -2, "pnlState": "loss"}]
    result = cohort_metrics(opportunities, closed, [])
    require(result["meanClosedNetReturnPct"] == 2 and result["closedNetPnlAmount"] == 8, "Episode arithmetic")
    require(result["actualEntries"] == 1 and result["opportunities"] == 2, "Opportunity accounting")
    require(stats([]) == {"count": 0, "minimum": None, "median": None, "maximum": None}, "Empty distances")
    require(stats([0, .5, .2])["median"] == .2, "Distance median")
    bad = deepcopy(closed)
    bad[1]["netReturnPct"] = None
    require(cohort_metrics([], bad, [])["meanClosedNetReturnPct"] is None, "Unknown return coerced")
    rejected = 0
    for actual, expected in ((0, None), (None, 0), (1, 2), (float("nan"), 0)):
        try:
            near(actual, expected, "intentional mismatch")
        except ValueError:
            rejected += 1
    require(rejected == 4, "Mismatch rejection")
    return {"status": "passed", "arithmeticAndNullChecks": 7, "intentionalRejections": rejected,
            "realArtifactsRead": False}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test()))
        return
    destination = args.directory / "event-outcomes.json"
    require(not destination.exists(), "Refusing to overwrite existing event outcomes")
    output = summarize(args.directory)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": "passed", "output": str(destination),
                      "accounts": sum(len(p["accounts"]) for p in output["periods"].values())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
