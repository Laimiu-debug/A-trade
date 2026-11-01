"""Describe separately replayed online-feedback accounts after their full audit.

Actual candidate-only entries are a posthoc sample-shift diagnostic. Their
outcomes are never used to change the frozen shadow labels or model decisions.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT = ROOT / "artifacts/strategy-search/online-feedback-entry-v1"
HELPER = ROOT / "scripts/summarize-undercut-reclaim-timing.py"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
EXITS = ("trail10", "volatilityTrail")
CANDIDATES = ("baseline", "intercept", "ridge")
CONTRASTS = (("interceptVsBaseline", "baseline", "intercept"),
             ("ridgeVsIntercept", "intercept", "ridge"),
             ("ridgeVsBaseline", "baseline", "ridge"))

spec = importlib.util.spec_from_file_location("online_feedback_summary_math", HELPER)
MATH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MATH)
require, finite, close, total = MATH.require, MATH.finite, MATH.close, MATH.total


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def read(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def identity(row):
    return {key: row[key] for key in ("period", "board", "exit", "candidate")}


def compact_closed(closed):
    return {key: deepcopy(value) for key, value in closed.items() if key != "episodes"}


def metrics(summary, scheduled, original_count):
    account, closed, bridge = (summary[key] for key in ("account", "closedEpisodes", "reconciliation"))
    overall = summary["periods"][0]
    require(overall["id"] == "overall", "First interval is not overall")
    buys = account["buyFillCount"]
    return {"accountReturnPct": account["netReturnPct"], "maxDrawdownPct": account["maxDrawdownPct"],
        "netPnlAmount": account["netPnlAmount"], "finalEquity": account["finalEquity"],
        "meanExposurePct": overall["exposurePct"]["mean"], "investedSessionPct": overall["investedSessionPct"],
        "signals": scheduled, "originalOpportunities": original_count,
        "notScheduled": original_count - scheduled, "notEntered": scheduled - buys,
        "buyFillCount": buys, "sellFillCount": account["sellFillCount"],
        "feesPaid": account["feesPaid"], "slippageAlreadyInFillPrices": account["slippageAlreadyInFillPrices"],
        "closed": closed["count"], "closedMeanPct": closed["meanReturnPct"],
        "closedMedianPct": closed["medianReturnPct"], "winRatePct": closed["winRatePct"],
        "meanWinPct": closed["meanWinReturnPct"], "meanLossPct": closed["meanLossReturnPct"],
        "payoffRatio": closed["payoffRatio"], "profitFactor": closed["profitFactor"],
        "maxConsecutiveLosses": closed["maxConsecutiveLosses"],
        "closedPnlAmount": bridge["closedEpisodePnl"],
        "openRealizedPnlAmount": bridge["openPositionRealizedPnl"],
        "openUnrealizedPnlAmount": bridge["openPositionUnrealizedPnl"],
        "openPositions": summary["openPositions"]["count"],
        "openStaleMarks": sum(row["markStaleAtCutoff"] for row in summary["openPositions"]["positions"]),
        "unreliableValuationDays": len(summary["quality"]["valuationUnreliableDates"]),
        "top1ShareOfGrossProfitPct": summary["concentration"]["top1ProfitSharePct"],
        "top5ShareOfGrossProfitPct": summary["concentration"]["top5ProfitSharePct"]}


def attach_episodes(key, original, schedule, result, summary, shadow_entered_ids):
    """Join buys to episodes by entry identity, never by sell.signalId."""
    original_ids = [signal["signalId"] for signal in original]
    schedule_ids = [signal["signalId"] for signal in schedule]
    require(len(original_ids) == len(set(original_ids)) and len(schedule_ids) == len(set(schedule_ids)),
            "Duplicate original or scheduled signal")
    selected = set(schedule_ids)
    require(schedule == [signal for signal in original if signal["signalId"] in selected],
            "Schedule changed original fields, order, or membership")
    require(set(shadow_entered_ids) <= set(original_ids), "Shadow entry outside original opportunities")
    observed = MATH.execution_dates(result)
    require(set(observed) == selected, "Account opportunity set differs from schedule")
    buys = {row["signalId"]: row for row in result["trades"] if row["side"] == "buy"}
    closed = {(row["record"]["symbol"], row["record"]["entryDate"]): row
              for row in summary["closedEpisodes"]["episodes"]}
    opened = {(row["record"]["symbol"], row["record"]["entryDate"]): row
              for row in summary["openPositions"]["positions"]}
    require(len(closed) == summary["closedEpisodes"]["count"]
            and len(opened) == summary["openPositions"]["count"], "Episode identities are not unique")
    used_closed, used_open, dispositions, entries = set(), set(), [], []
    for signal in original:
        sid, day, symbol = signal["signalId"], signal["signalDate"], signal["symbol"]
        detail = observed.get(sid)
        disposition = {**key, "formationId": sid, "formationDate": day, "symbol": symbol,
            "originalRank": signal["originalRank"], "scheduled": sid in selected,
            "shadowEverEntered": sid in shadow_entered_ids,
            "accountDisposition": deepcopy(detail) if detail else None,
            "positionState": detail["positionState"] if detail else "notScheduled"}
        dispositions.append(disposition)
        if sid not in buys:
            continue
        buy = buys[sid]
        require(buy["symbol"] == symbol and buy["date"] == detail["entryDate"], "Buy identity mismatch")
        episode_key = (symbol, buy["date"])
        if detail["positionState"] == "closed":
            episode = closed[episode_key]
            require(episode["record"]["exitDate"] == detail["exitDate"], "Final exit differs")
            used_closed.add(episode_key)
            values = {"closedReturnPct": episode["netReturnPct"], "closedPnlAmount": episode["pnlAmount"],
                "openRealizedPnlAmount": 0., "openUnrealizedPnlAmount": 0.,
                "holdingSessions": episode["holdingSessions"], "openMarkStale": False}
        else:
            require(detail["positionState"] == "openAtCutoff", "Unexpected entered maturity")
            episode = opened[episode_key]
            used_open.add(episode_key)
            values = {"closedReturnPct": None, "closedPnlAmount": 0.,
                "openRealizedPnlAmount": episode["realizedPnlWhileOpen"],
                "openUnrealizedPnlAmount": episode["unrealizedPnlAmount"],
                "holdingSessions": episode["observedHoldingSessions"], "openMarkStale": episode["markStaleAtCutoff"]}
        entries.append({**key, "formationId": sid, "formationDate": day, "symbol": symbol,
            "entryDate": buy["date"], "entryPrice": buy["price"], "entryQuantity": buy["quantity"],
            "exitDate": detail["exitDate"], "positionState": detail["positionState"],
            "investedAmount": episode["record"]["investedAmount"],
            "shadowEverEntered": sid in shadow_entered_ids,
            "candidateOnlyEntry": sid not in shadow_entered_ids, **values})
    require(used_closed == set(closed) and used_open == set(opened)
            and len(entries) == len(buys) == summary["account"]["buyFillCount"], "Entered episode coverage")
    return dispositions, entries


def entry_group(rows):
    closed = [row for row in rows if row["positionState"] == "closed"]
    opened = [row for row in rows if row["positionState"] == "openAtCutoff"]
    require(len(closed) + len(opened) == len(rows), "Entry diagnostic contains nonentries")
    closed_amount = total(row["closedPnlAmount"] for row in rows)
    realized = total(row["openRealizedPnlAmount"] for row in rows)
    floating = total(row["openUnrealizedPnlAmount"] for row in rows)
    return {"buyCount": len(rows), "closedCount": len(closed), "openCount": len(opened),
        "closedPnlAmount": closed_amount, "openRealizedPnlAmount": realized,
        "openUnrealizedPnlAmount": floating, "totalEpisodePnlAmount": total([closed_amount, realized, floating]),
        "closedReturnPct": MATH.distribution(row["closedReturnPct"] for row in closed),
        "investedAmountSum": total(row["investedAmount"] for row in rows),
        "openStaleMarkCount": sum(row["openMarkStale"] for row in opened),
        "meaning": "Posthoc observed episode group; investment sums reuse capital and are not a portfolio denominator."}


def sample_shift(key, entries, summary):
    known = [row for row in entries if row["shadowEverEntered"]]
    novel = [row for row in entries if not row["shadowEverEntered"]]
    groups = {"allCandidateEntries": entry_group(entries), "shadowAlsoEntered": entry_group(known),
              "candidateOnly": entry_group(novel)}
    bridge = summary["reconciliation"]
    for field, saved in (("closedPnlAmount", "closedEpisodePnl"),
                         ("openRealizedPnlAmount", "openPositionRealizedPnl"),
                         ("openUnrealizedPnlAmount", "openPositionUnrealizedPnl")):
        close(groups["allCandidateEntries"][field], bridge[saved], "Candidate entry money bridge")
        close(total([groups["shadowAlsoEntered"][field], groups["candidateOnly"][field]]), bridge[saved], "Sample shift partition")
    return {**key, "candidateOnlyBuyCount": len(novel), "actualBuyCount": len(entries),
        "candidateOnlyBuyPct": len(novel) / len(entries) * 100 if entries else None,
        **groups, "episodePnlBridge": deepcopy(bridge["episodePnlBridge"]),
        "meaning": "Retrospective sample shift, not model input, a causal effect, or a rerun deleting baseline trades."}


def compare_accounts(rows):
    keys = [(row["period"], row["board"], row["exit"], row["candidate"]) for row in rows]
    indexed = dict(zip(keys, rows))
    expected = {(p, b, e, c) for p in PERIODS for b in BOARDS for e in EXITS for c in CANDIDATES}
    require(len(indexed) == len(rows) == 36 and set(indexed) == expected, "Complete 36-account matrix required")
    answer = []
    for period in PERIODS:
        for board in BOARDS:
            for exit_policy in EXITS:
                for contrast_id, control, candidate in CONTRASTS:
                    left, right = (indexed[(period, board, exit_policy, mode)]["metrics"] for mode in (control, candidate))
                    require(set(left) == set(right), "Comparison fields differ")
                    answer.append({"period": period, "board": board, "exit": exit_policy, "id": contrast_id,
                        "control": control, "candidate": candidate, "controlMetrics": deepcopy(left),
                        "candidateMetrics": deepcopy(right), "candidateMinusControl": {
                            name: right[name] - left[name] if finite(left[name]) and finite(right[name]) else None
                            for name in sorted(left)},
                        "meaning": "Separately replayed complete accounts; includes admission, capital and exposure paths, not a filtered baseline bill."})
    return answer


def interval_rows(key, summary, result, original, schedule, decisions):
    """Keep service summary interval returns and entry equity without resets."""
    output = []
    for saved in summary["periods"]:
        if saved["id"] == "overall":
            continue
        start, end = saved["requestedStart"], saved["requestedEnd"]
        fills = [row for row in result["trades"] if start <= row["date"] <= end]
        cohort = [row for row in original if start <= row["signalDate"] <= end]
        admitted = [row for row in schedule if start <= row["signalDate"] <= end]
        selected_decisions = [row for row in decisions if start <= row["formationDate"] <= end]
        item = {**key, **deepcopy(saved), "closedEpisodes": compact_closed(saved["closedEpisodes"]),
            "feesPaid": total(row["fee"] for row in fills),
            "slippageAlreadyInFillPrices": total(row["slippage"] for row in fills),
            "buyFillCount": sum(row["side"] == "buy" for row in fills),
            "sellFillCount": sum(row["side"] == "sell" for row in fills),
            "sellFillReasons": MATH.labels(row.get("reason") for row in fills if row["side"] == "sell"),
            "originalFormations": len(cohort), "scheduledFromFormations": len(admitted),
            "notScheduledFromFormations": len(cohort) - len(admitted),
            "attributionMeaning": "Continuous actual equity and positions; closed episodes assigned by final exitDate, entry permission counts by original formationDate."}
        if key["candidate"] != "baseline":
            item["modelStatusCounts"] = MATH.labels(row["models"][key["candidate"]]["status"] for row in selected_decisions)
            item["modelReasonCounts"] = MATH.labels(row["models"][key["candidate"]]["reason"] for row in selected_decisions)
        else:
            item["modelStatusCounts"], item["modelReasonCounts"] = {}, {}
        output.append(item)
    return output


def partition_intervals(rows, summary, curve):
    checks = []
    for kind in ("calendarYear", "calendarHalfYear", "retrospectiveNarrativePhase", "descriptiveEpoch"):
        part = sorted((row for row in rows if row["kind"] == kind), key=lambda row: row["requestedStart"])
        if not part:
            continue
        coverage = Counter(day["date"] for row in part for day in curve
                           if row["requestedStart"] <= day["date"] <= row["requestedEnd"])
        require(coverage == Counter(day["date"] for day in curve), "Interval date partition: " + kind)
        close(total(row["netPnlAmount"] for row in part), summary["account"]["netPnlAmount"], "Interval PnL partition")
        close(total(row["feesPaid"] for row in part), summary["account"]["feesPaid"], "Interval fee partition")
        require(sum(row["closedEpisodes"]["count"] for row in part) == summary["closedEpisodes"]["count"],
                "Interval closed episode partition")
        for earlier, later in zip(part, part[1:]):
            close(later["enteringEquity"], earlier["endingEquity"], "Continuous interval entry equity")
        if all(finite(row["returnPct"]) for row in part):
            compounded = (math.prod(1 + row["returnPct"] / 100 for row in part) - 1) * 100
            close(compounded, summary["account"]["netReturnPct"], "Compounded interval return")
        checks.append({"kind": kind, "intervalCount": len(part), "passed": True})
    return checks


def feedback_coverage(key, decisions, original, schedules, target_dates):
    require([row["formationDate"] for row in decisions] == target_dates, "All target-day decisions required")
    by_day = {row["formationDate"]: row for row in decisions}
    require(len(by_day) == len(decisions), "Duplicate decision date")
    rows = []
    for mode in ("intercept", "ridge"):
        expected = [signal for signal in original if by_day[signal["signalDate"]]["models"][mode]["allow"]]
        require(expected == schedules[mode], "Permission mask does not equal frozen candidate schedule")
        models = [row["models"][mode] for row in decisions]
        require(all(model["status"] in ("fitted", "fallback") and type(model["allow"]) is bool for model in models),
                "Unknown model status/permission")
        require(all(model["allow"] is True and model["prediction"] is None for model in models if model["status"] == "fallback"),
                "Fallback must preserve original schedule without a fabricated prediction")
        rows.append({**key, "candidate": mode, "targetDayCount": len(decisions),
            "statusCounts": MATH.labels(model["status"] for model in models),
            "reasonCounts": MATH.labels(model["reason"] for model in models),
            "allowDayCount": sum(model["allow"] for model in models),
            "rejectDayCount": sum(not model["allow"] for model in models),
            "originalOpportunities": len(original), "scheduledSignals": len(expected),
            "notScheduledSignals": len(original) - len(expected),
            "fittedOriginalOpportunities": sum(by_day[signal["signalDate"]]["models"][mode]["status"] == "fitted" for signal in original),
            "fallbackOriginalOpportunities": sum(by_day[signal["signalDate"]]["models"][mode]["status"] == "fallback" for signal in original),
            "predictionWhenFitted": MATH.distribution(model["prediction"] for model in models if model["status"] == "fitted"),
            "eligibleEpisodesPerDay": MATH.distribution(row["support"]["eligibleEpisodeCount"] for row in decisions),
            "eligibleFormationDaysPerDay": MATH.distribution(row["support"]["eligibleFormationDayCount"] for row in decisions),
            "labelXUnknownEpisodeDayObservations": sum(row["support"]["labelXUnknownCount"] for row in decisions),
            "uniqueLabelXUnknownFormationIds": len({x["formationId"] for row in decisions for x in row["labelXUnknown"]}),
            "immatureEpisodeDayObservations": sum(row["support"]["immatureCount"] for row in decisions),
            "immatureAgeSlots": MATH.distribution(x["ageSlots"] for row in decisions for x in row["immature"]),
            "currentFeatureStatusCounts": MATH.labels(row["currentFeatureStatus"] for row in decisions),
            "meaning": "Day counts use all source target sessions; repeated label/maturity views are episode-day observations, not independent trades."})
    return rows


def build(directory=DEFAULT):
    directory = Path(directory).resolve()
    names = ("plan.json", "comparison.json", "input-completion.json", "input-validation.json", "validation.json")
    destination = directory / "readout.json"
    sidecar = directory / "opportunity-dispositions.jsonl.gz"
    entry_sidecar = directory / "candidate-entry-episodes.jsonl.gz"
    require(not any(path.exists() for path in (destination, sidecar, entry_sidecar)), "Readout artifacts already exist")
    pins = {}

    def pin(path):
        path = Path(path).resolve()
        require(path.is_relative_to(ROOT), "Readout input outside repository")
        name, value = path.relative_to(ROOT).as_posix(), sha(path)
        require(pins.setdefault(name, value) == value, "Readout input changed")
        return path

    def bound(item):
        path = pin(ROOT / item["path"])
        require(pins[path.relative_to(ROOT).as_posix()] == item["sha256"], "Artifact digest mismatch")
        return path

    def reference(path):
        path = Path(path).resolve()
        return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}

    documents = {name: read(pin(directory / name)) for name in names}
    plan, comparison, completion, input_validation, validation = (documents[name] for name in names)
    plan_sha = sha(directory / "plan.json")
    require(plan["schema"] == "online-feedback-entry-v1" and comparison["planSha256"] == completion["planSha256"] == plan_sha,
            "Wrong plan/comparison/completion")
    require(validation["passed"] is True and input_validation["passed"] is True
        and validation["planSha256"] == input_validation["planSha256"] == plan_sha
        and validation["comparisonSha256"] == sha(directory / "comparison.json")
        and validation["inputCompletionSha256"] == input_validation["inputCompletionSha256"] == sha(directory / "input-completion.json")
        and validation["inputValidationSha256"] == sha(directory / "input-validation.json"), "Full audit chain required")
    verifier = "scripts/verify-online-feedback-entry.py"
    require(validation["verifierSha256"] == plan["sourcePins"][verifier] == sha(ROOT / verifier), "Frozen independent verifier changed")
    require(validation["counts"]["accounts"] == 36 and validation["counts"]["newAccounts"] == 24
        and validation["counts"]["reusedAccounts"] == 12
        and comparison["newAccounts"] == 24 and comparison["referenceAccountsReused"] == 12, "Account trial matrix")
    require(completion["allSchedulesFrozenBeforeAccounts"] is True
        and plan["holdoutClaim"] is False and plan["parameterSearch"] is False, "Scope cannot promote or retune")
    for name, value in plan["sourcePins"].items():
        bound({"path": name, "sha256": value})
    for path in (Path(__file__), HELPER):
        pin(path)
    atlas = read(bound(plan["atlas"]))
    phases = {row["id"]: row for row in atlas["phases"]}
    require(len(phases) == len(atlas["phases"]) == 16, "Complete original phase atlas")
    source_groups = {(row["period"], row["board"], row["exit"]): row for row in completion["groups"]}
    require(len(source_groups) == len(completion["groups"]) == 12, "Twelve independent shadow groups required")
    indexed = {(row["period"], row["board"], row["exit"], row["candidate"]): row for row in comparison["rows"]}
    require(len(indexed) == len(comparison["rows"]) == 36, "Duplicate/incomplete account matrix")
    accounts, flat, intervals, coverages, feedback, compact_daily, shifts, partitions = ([] for _ in range(8))
    dispositions, candidate_entries = [], []
    original_sources = {}
    for period in PERIODS:
        for board in BOARDS:
            original = read(bound(plan["baselineSchedules"][period][board]))
            metadata = read(bound(plan["metadata"][period][board]))
            original_sources[(period, board)] = len(original)
            for exit_policy in EXITS:
                group_key = (period, board, exit_policy)
                group = source_groups[group_key]
                key = {"period": period, "board": board, "exit": exit_policy}
                shadow = read(bound(group["shadow"]))
                shadow_ids = {row["formationId"] for row in shadow}
                require(len(shadow_ids) == len(shadow), "Duplicate shadow episode ID")
                decision_path = bound(group["decisions"])
                with gzip.open(decision_path, "rt", encoding="utf-8") as stream:
                    decisions = [json.loads(line) for line in stream]
                schedules = {mode: read(bound(group["candidates"][mode])) for mode in ("intercept", "ridge")}
                feedback.extend(feedback_coverage(key, decisions, original, schedules, metadata["targetDates"]))
                compact_daily.extend({**key, **{name: deepcopy(row[name]) for name in
                    ("formationDate", "asOfDate", "currentFeatures", "currentFeatureStatus", "currentFeatureReasons", "support", "models")}}
                    for row in decisions)
                for mode in CANDIDATES:
                    account_key = {**key, "candidate": mode}
                    descriptor = indexed[(period, board, exit_policy, mode)]
                    schedule = original if mode == "baseline" else schedules[mode]
                    require(read(bound(descriptor["schedule"])) == schedule, "Comparison schedule binding")
                    if mode == "baseline":
                        require(descriptor["result"] == plan["accountArtifacts"][period][board][exit_policy][exit_policy + ".result.json.gz"]
                            and descriptor["checkpoint"] == plan["accountArtifacts"][period][board][exit_policy][exit_policy + ".checkpoint.json.gz"], "Reference account was not reused")
                    result = read(bound(descriptor["result"]))
                    bound(descriptor["checkpoint"])
                    summary = read(bound(descriptor["summary"]))
                    require(summary["inputResultSha256"] == digest(result) and summary["policyId"] == mode
                        and result["status"] == "completed" and not summary["quality"]["integrityIssues"], "Summary/result/account mismatch")
                    require([row["date"] for row in result["curve"]] == metadata["targetDates"], "Complete source account date coverage")
                    detail, entries = attach_episodes(account_key, original, schedule, result, summary, shadow_ids)
                    dispositions.extend(detail)
                    if mode == "baseline":
                        require({row["formationId"] for row in entries} == shadow_ids, "Shadow differs from fixed account admissions")
                    else:
                        candidate_entries.extend(entries)
                        shifts.append(sample_shift(account_key, entries, summary))
                    scalar = metrics(summary, len(schedule), len(original))
                    flat.append({**account_key, "reusedAccount": mode == "baseline", "metrics": scalar})
                    accounts.append({**account_key, "artifacts": deepcopy(descriptor), "metrics": scalar,
                        **{name: deepcopy(summary[name]) for name in ("account", "closedEpisodes", "concentration", "openPositions", "rights", "unresolvedExits", "reconciliation", "quality", "warnings")},
                        "sourceQualityScalarFlags": {name: value for name, value in summary["sourceQuality"].items() if not isinstance(value, (dict, list))},
                        "fullSourceQuality": {"summaryRef": deepcopy(descriptor["summary"]), "field": "sourceQuality"}})
                    states = Counter(row["positionState"] for row in detail)
                    coverages.append({**account_key, "originalOpportunities": len(original), "scheduledSignals": len(schedule),
                        "notScheduled": states["notScheduled"], "entered": len(entries),
                        "closed": states["closed"], "openAtCutoff": states["openAtCutoff"], "notEntered": states["notEntered"],
                        "accountOpportunityReasons": MATH.labels(row["accountDisposition"]["reason"] for row in detail if row["scheduled"]),
                        "valuationUnreliableDays": scalar["unreliableValuationDays"]})
                    measured = interval_rows(account_key, summary, result, original, schedule, decisions)
                    for row in measured:
                        if row["kind"] == "retrospectiveNarrativePhase":
                            phase = phases[row["id"]]
                            require((row["requestedStart"], row["requestedEnd"]) == (phase["startDate"], phase["endDate"]), "Phase dates changed")
                            row["phaseTitle"] = phase["title"]
                    intervals.extend(measured)
                    partitions.append({**account_key, "passed": True, "checks": partition_intervals(measured, summary, result["curve"])})
    require(sum(original_sources.values()) == 8155 and len(dispositions) == 8155 * 6, "All original opportunity-policy dispositions required")
    comparisons = compare_accounts(flat)
    tables = {name: [row for row in intervals if row["kind"] == kind] for name, kind in
        (("yearRows", "calendarYear"), ("halfYearRows", "calendarHalfYear"),
         ("phaseRows", "retrospectiveNarrativePhase"), ("curveEpochRows", "descriptiveEpoch"))}
    require([len(tables[name]) for name in tables] == [72, 144, 288, 36], "Complete year/half/phase/epoch matrices")
    require(len(shifts) == len(feedback) == 24 and len(coverages) == len(partitions) == 36, "Complete diagnostic matrices")
    for name, value in pins.items():
        require(sha(ROOT / name) == value, "Readout input changed during build")
    for path, values in ((sidecar, dispositions), (entry_sidecar, candidate_entries)):
        with gzip.open(path, "xt", encoding="utf-8", compresslevel=1) as stream:
            for row in values:
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
    report = {"schema": "online-feedback-entry-readout-v1", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "planSha256": plan_sha, "comparisonSha256": sha(directory / "comparison.json"),
        "verification": {"passed": True, **reference(directory / "validation.json")}, "inputPins": pins,
        "accounts": accounts, "accountRows": flat, "comparisons": comparisons, **tables,
        "coverageRows": coverages, "feedbackCoverageRows": feedback, "feedbackDailyRows": compact_daily,
        "sampleShiftRows": shifts, "partitionReconciliation": partitions,
        "counts": {"accounts": 36, "comparisons": 36, "yearRows": 72, "halfYearRows": 144,
            "phaseRows": 288, "curveEpochRows": 36, "coverageRows": 36, "feedbackCoverageRows": 24,
            "sampleShiftRows": 24, "originalOpportunities": 8155, "sourceExitOpportunities": 16310,
            "policyDispositions": len(dispositions), "actualCandidateEntryEpisodes": len(candidate_entries),
            "feedbackDecisionDays": len(compact_daily)},
        "opportunityRowsArtifact": {**reference(sidecar), "count": len(dispositions), "format": "jsonl+gzip",
            "identity": ["period", "board", "exit", "candidate", "formationId"]},
        "candidateEntryRowsArtifact": {**reference(entry_sidecar), "count": len(candidate_entries), "format": "jsonl+gzip",
            "identity": ["period", "board", "exit", "candidate", "formationId"]},
        "method": {"accounts": "36 full cash/position accounts: 12 byte-reused baselines and 24 newly replayed candidates; summaries use the same explicit intervals.",
            "primary": "ridge minus intercept isolates the added predictive environment specification within the same support/fallback design; it remains a descriptive account comparison.",
            "fallback": "Insufficient training formation dates or unknown current X preserves every original signal; it is not a no-trade policy.",
            "shadow": "Fixed baseline actually entered episodes only; separate period/board/exit populations; no candidate feedback into training.",
            "sampleShift": "shadowEverEntered and candidate-only outcomes are terminal posthoc descriptions, never model inputs or an all-opportunity label claim.",
            "intervals": "Original curve equity and positions carry across years/phases/9.24; closed episodes assigned by final exit, admissions by formation.",
            "quality": "Full source quality remains in bound summaries; arithmetic validation does not certify raw prices, historical status or realizable returns."},
        "newAccounts": 24, "referenceAccountsReused": 12, "newEntryPolicies": 2,
        "holdoutClaim": False, "certified": False, "targetAchieved": False}
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"passed": True, "readout": reference(destination), "accounts": len(accounts), "comparisons": len(comparisons),
            "tableCounts": {name: len(rows) for name, rows in tables.items()}, "opportunityRows": len(dispositions)}


def synthetic_fixture():
    """A closed winner, a closed loss, and same-symbol reentry still open."""
    from service.research_account_outcomes import summarize_account
    dates = ["2024-01-" + str(day).zfill(2) for day in range(2, 9)]
    signals = [{"signalId": sid, "strategyId": "strongUndercutReclaimV1", "symbol": symbol,
                "signalDate": dates[index], "originalRank": 1}
               for sid, symbol, index in (("a", "sz000001", 0), ("b", "sz000002", 1),
                                          ("c", "sz000001", 3), ("d", "sz000003", 4))]
    schedule = signals[:3]
    trades = []
    for sid, symbol, day, side, price in (("a", "sz000001", dates[1], "buy", 10.),
            ("b", "sz000002", dates[2], "buy", 10.), (None, "sz000001", dates[3], "sell", 11.),
            ("c", "sz000001", dates[4], "buy", 20.), (None, "sz000002", dates[5], "sell", 9.)):
        trades.append({"signalId": sid, "symbol": symbol, "date": day, "side": side,
                       "quantity": 100, "price": price, "amount": price * 100,
                       "fee": 0., "slippage": 0., "reason": "signal" if side == "buy" else "trailingStop"})
    closed = [{"symbol": symbol, "entryDate": dates[entry], "exitDate": dates[exit_day],
               "investedAmount": 1000., "pnlAmount": pnl, "returnPct": pnl / 10,
               "holdingDays": exit_day - entry + 1}
              for symbol, entry, exit_day, pnl in (("sz000001", 1, 3, 100.), ("sz000002", 2, 5, -100.))]
    position = {"symbol": "sz000001", "entryDate": dates[4], "quantity": 100,
                "marketValue": 2100., "costBasis": 2000., "investedAmount": 2000.,
                "realizedPnl": 0., "priceDate": dates[-1]}
    cash = [500000., 499000., 498000., 499100., 497100., 498000., 498000.]
    market = [0., 1000., 2000., 1000., 3000., 2000., 2100.]
    curve = [{"date": day, "cash": c, "marketValue": m, "equity": c + m, "valuationReliable": True}
             for day, c, m in zip(dates, cash, market)]
    holding_indices = [[], [1], [1, 2], [2], [2, 4], [4], [4]]
    daily = [{"date": day, "holdings": [{"symbol": str(index), "holdingDays": at - index + 1}
              for index in holding_indices[at]]} for at, day in enumerate(dates)]
    opportunities = [{**signal, "status": "filled", "reason": "filled", "filledQuantity": 100,
                      "attemptDate": next(trade["date"] for trade in trades if trade["signalId"] == signal["signalId"])}
                     for signal in schedule]
    result = {"status": "completed", "config": {"initialCapital": 500000., "startDate": dates[0], "endDate": dates[-1]},
        "curve": curve, "daily": daily, "trades": trades, "closedTrades": closed, "positions": [position],
        "actionLedger": [], "cashReceivables": [], "actionPayables": [], "forcedExits": {}, "preExRights": {},
        "reservedActionCash": 0., "pendingTarget": None, "quality": {}, "warnings": [],
        "research": {"opportunities": opportunities, "opportunityCounts": {"filled": 3}}}
    specs = [{"id": "2024", "kind": "calendarYear", "start": "2024-01-01", "end": "2024-12-31"},
             {"id": "part1", "kind": "retrospectiveNarrativePhase", "start": dates[0], "end": dates[3]},
             {"id": "part2", "kind": "retrospectiveNarrativePhase", "start": dates[4], "end": dates[-1]}]
    summary = summarize_account(result, policy_id="ridge", policy_label="synthetic", intervals=specs)
    require(not summary["quality"]["integrityIssues"], "Synthetic accounting integrity")
    return signals, schedule, result, summary


def self_test():
    checks = []
    original, schedule, result, summary = synthetic_fixture()
    frozen = deepcopy((original, schedule, result, summary))
    key = {"period": "2024-2025", "board": "main", "exit": "trail10", "candidate": "ridge"}
    dispositions, entries = attach_episodes(key, original, schedule, result, summary, {"a", "c"})
    require([row["formationId"] for row in entries] == ["a", "b", "c"]
            and entries[0]["positionState"] == "closed" and entries[2]["positionState"] == "openAtCutoff",
            "Same-symbol reentry or null sell IDs confused")
    checks.append("Buy identity joins null-ID exits and same-symbol closed/open reentry")
    require(len(dispositions) == 4 and dispositions[-1]["positionState"] == "notScheduled"
            and dispositions[-1]["accountDisposition"] is None, "Original opportunity denominator")
    checks.append("Unscheduled original opportunities remain explicit")
    shift = sample_shift(key, entries, summary)
    require(shift["candidateOnlyBuyCount"] == 1 and shift["candidateOnly"]["closedPnlAmount"] == -100.
            and shift["shadowAlsoEntered"]["closedPnlAmount"] == 100.
            and shift["shadowAlsoEntered"]["openUnrealizedPnlAmount"] == 100., "Posthoc sample shift partition")
    close(shift["candidateOnlyBuyPct"], 100 / 3, "Actual buy denominator")
    checks.append("Candidate-only losing entry retained and open/closed money partitions reconcile")
    empty = entry_group([])
    require(empty["buyCount"] == 0 and empty["closedReturnPct"]["mean"] is None
            and empty["totalEpisodePnlAmount"] == 0, "Empty entry group semantics")
    unknown = deepcopy(entries)
    unknown[0]["closedReturnPct"] = None
    require(entry_group(unknown)["closedReturnPct"]["mean"] is None, "Unknown return was dropped")
    checks.append("Empty and partially unknown closed distributions are not fabricated zeros or complete cases")
    try:
        attach_episodes(key, original, list(reversed(schedule)), result, summary, {"a"})
    except ValueError:
        pass
    else:
        raise AssertionError("Reordered original schedule accepted")
    checks.append("Original signal order and fields cannot be repaired or reranked")
    require((original, schedule, result, summary) == frozen, "Input mutation")
    checks.append("All input account and signal objects remain immutable")
    measured = interval_rows(key, summary, result, original, schedule, [])
    parts = partition_intervals(measured, summary, result["curve"])
    require(len(parts) == 2 and all(row["passed"] for row in parts), "Synthetic interval partition")
    phases = [row for row in measured if row["kind"] == "retrospectiveNarrativePhase"]
    require(phases[1]["enteringEquity"] == 500100. and phases[1]["closedEpisodes"]["count"] == 1,
            "Carried equity or exit-date assignment")
    checks.append("Interval capital carries across cuts; whole episode PnL uses final-exit date")
    duplicates = measured + [deepcopy(phases[0])]
    try:
        partition_intervals(duplicates, summary, result["curve"])
    except ValueError:
        pass
    else:
        raise AssertionError("Overlapping interval accepted")
    checks.append("Overlapping date intervals fail partition reconciliation")
    scalar = metrics(summary, len(schedule), len(original))
    require(scalar["closed"] == 2 and scalar["openPositions"] == 1
            and scalar["notScheduled"] == 1 and scalar["openUnrealizedPnlAmount"] == 100., "Saved metric mapping")
    matrix = [{"period": p, "board": b, "exit": e, "candidate": c,
               "metrics": {"accountReturnPct": float(at), "profitFactor": None}}
              for p in PERIODS for b in BOARDS for e in EXITS for at, c in enumerate(CANDIDATES)]
    contrasts = compare_accounts(matrix)
    require(len(contrasts) == 36 and contrasts[0]["candidateMinusControl"] == {"accountReturnPct": 1., "profitFactor": None},
            "Contrast direction/null handling")
    checks.append("All 36 contrasts preserve candidate-minus-control direction and null metrics")
    decisions = []
    dates = [row["date"] for row in result["curve"]]
    for at, day in enumerate(dates):
        model = {"status": "fallback" if at == 0 else "fitted", "reason": "insufficientTrainingFormationDays" if at == 0 else "positivePrediction",
                 "allow": True, "prediction": None if at == 0 else .01}
        decisions.append({"formationDate": day, "models": {mode: deepcopy(model) for mode in ("intercept", "ridge")},
            "support": {"eligibleEpisodeCount": at, "eligibleFormationDayCount": at,
                        "labelXUnknownCount": 0, "immatureCount": 1},
            "labelXUnknown": [], "immature": [{"formationId": "x", "ageSlots": at + 1}],
            "currentFeatureStatus": "complete"})
    coverage = feedback_coverage({name: key[name] for name in ("period", "board", "exit")}, decisions,
                                 original, {mode: original for mode in ("intercept", "ridge")}, dates)
    require(coverage[0]["fallbackOriginalOpportunities"] == 1 and coverage[0]["scheduledSignals"] == 4
            and coverage[0]["immatureEpisodeDayObservations"] == 7, "Fallback or repeated maturity observations")
    checks.append("Fallback admits original signals and repeated immature views keep episode-day denominator")
    return {"passed": True, "syntheticGroups": len(checks), "checks": checks,
            "realAccountReads": 0, "accountRuns": 0, "filesWritten": 0, "productionUnitIncrement": 0}


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--directory", type=Path, default=DEFAULT)
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False, allow_nan=False))
