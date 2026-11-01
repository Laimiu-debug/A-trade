"""Read-only account/phase attribution for the frozen peer price/activity experiment.

Only the derived readout is created. No source loading, signals, trading,
parameter selection, or narrative-phase filtering is performed here.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "artifacts/strategy-search/peer-price-activity-alignment-v1"
PARENT = ROOT / "artifacts/strategy-search/peer-amount-persistence-v1"
ATLAS = ROOT / "artifacts/strategy-search/market-phase-atlas-v1/atlas.json"
ATLAS_SHA = "ddf55f2741dca3793923e5da782690aad500510848077f43492fff41af1a44d8"
PERIODS = ("2022-2023", "2024-2025")
POLICIES = ("currentActivity", "priceProgress", "activityAligned")
ORIGINAL_COUNTS = {"2022-2023": 148, "2024-2025": 249}
CONTRASTS = (("primary", "priceProgress", "activityAligned"),
             ("secondary", "currentActivity", "priceProgress"))
ALIGNMENT_FLAGS = {"unknown": (None, None, None), "notActive": (False, False, False),
                   "noPositiveProgress": (True, False, False),
                   "nonPositiveAlignment": (True, True, False), "aligned": (True, True, True)}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def close(actual, expected, label):
    if actual is None or expected is None:
        require(actual is expected, label + ": null mismatch")
    else:
        require(finite(actual) and finite(expected)
                and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-6), label)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def location(path):
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def read(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def total(values):
    values = list(values)
    return math.fsum(values) if all(finite(value) for value in values) else None


def labels(values):
    return dict(sorted(Counter("null" if value is None else str(value) for value in values).items()))


def distribution(values):
    """Unknowns never silently become a complete-case distribution."""
    values = list(values)
    known = sorted(value for value in values if finite(value))
    result = {"count": len(values), "knownCount": len(known),
              "unknownCount": len(values) - len(known),
              "status": "empty" if not values else "complete" if len(values) == len(known) else "numericUnknown"}
    keys = ("mean", "min", "p05", "p25", "median", "p75", "p95", "max")
    result.update({key: None for key in keys})
    if not values or len(known) != len(values):
        return result

    def quantile(q):
        position = (len(known) - 1) * q
        left, right = math.floor(position), math.ceil(position)
        return known[left] + (known[right] - known[left]) * (position - left)

    result.update(mean=math.fsum(known) / len(known), min=known[0], max=known[-1],
                  p05=quantile(.05), p25=quantile(.25), median=quantile(.5),
                  p75=quantile(.75), p95=quantile(.95))
    return result


def closed_distribution(rows):
    wins = [row for row in rows if finite(row.get("pnlAmount")) and row["pnlAmount"] > 0]
    losses = [row for row in rows if finite(row.get("pnlAmount")) and row["pnlAmount"] < 0]
    zero = sum(finite(row.get("pnlAmount")) and row["pnlAmount"] == 0 for row in rows)
    unknown = len(rows) - len(wins) - len(losses) - zero
    profit = total(row["pnlAmount"] for row in wins)
    loss = total(-row["pnlAmount"] for row in losses)
    win_returns = distribution(row.get("returnPct") for row in wins)
    loss_returns = distribution(row.get("returnPct") for row in losses)
    payoff = None
    if not unknown and finite(win_returns["mean"]) and finite(loss_returns["mean"]) and loss_returns["mean"] < 0:
        payoff = win_returns["mean"] / -loss_returns["mean"]
    return {"count": len(rows), "wins": len(wins), "losses": len(losses), "zeros": zero,
            "unknownPnlCount": unknown,
            "netPnlAmount": total(row.get("pnlAmount") for row in rows),
            "grossProfitAmount": profit if not unknown else None,
            "grossLossAmount": loss if not unknown else None,
            "winRatePct": len(wins) / len(rows) * 100 if rows and not unknown else None,
            "profitFactor": profit / loss if not unknown and loss > 0 else None,
            "payoffRatio": payoff,
            "returnPct": distribution(row.get("returnPct") for row in rows),
            "winReturnPct": win_returns, "lossReturnPct": loss_returns,
            "pnlAmount": distribution(row.get("pnlAmount") for row in rows),
            "holdingSessions": distribution(row.get("holdingDays") for row in rows),
            "assignment": "whole closed episode assigned by exitDate; not mark-to-market interval contribution"}


def opportunity_counts(original, schedule, account_opportunities, start=None, end=None):
    def inside(row):
        return start is None or start <= row["signalDate"] <= end

    all_rows = [row for row in original if inside(row)]
    selected = [row for row in schedule if inside(row)]
    executed = [row for row in account_opportunities if inside(row)]
    require({row["signalId"] for row in selected} == {row["signalId"] for row in executed},
            "Selected and account opportunity identities differ")
    amount_states = Counter(row["parentAmount"]["kind"] for row in all_rows)
    alignment_states = Counter(row["priceAlignment"]["kind"] for row in all_rows)
    selected_ids = {row["signalId"] for row in selected}
    entered = sum(finite(row.get("filledQuantity")) and row["filledQuantity"] > 0 for row in executed)
    return {"originalTemporalTop5": len(all_rows), "parentAmountKinds": dict(sorted(amount_states.items())),
            "parentAmountEvaluable": sum(type(row["parentAmount"].get("control")) is bool for row in all_rows),
            "parentAmountUnknown": sum(type(row["parentAmount"].get("control")) is not bool for row in all_rows),
            "priceAlignmentKinds": dict(sorted(alignment_states.items())),
            "priceAlignmentKnown": sum(row["priceAlignment"]["complete"] is True for row in all_rows),
            "priceAlignmentUnknown": sum(row["priceAlignment"]["kind"] == "unknown" for row in all_rows),
            "selectedByPolicy": {policy: sum(row["priceAlignment"][policy] is True for row in all_rows) for policy in POLICIES},
            "selectedSignals": len(selected), "filteredOrUnknown": len(all_rows) - len(selected_ids),
            "entered": entered, "notEntered": len(executed) - entered,
            "terminalStatusCounts": labels(row.get("status", "missing") for row in executed),
            "terminalReasonCounts": labels(row.get("reason", "missing") for row in executed),
            "meaning": "signalDate cohort; account dispositions observed at the full account cutoff, not known at phase end; partial initial fills count once as entered"}


def check_alignment_rows(rows):
    """Check common evidence availability and nested policies, without outcomes."""
    for row in rows:
        alignment = row["priceAlignment"]
        require(alignment["kind"] in ALIGNMENT_FLAGS, "Unknown alignment state")
        require(type(alignment["complete"]) is bool, "Alignment completeness must be an explicit boolean")
        flags = [alignment[policy] for policy in POLICIES]
        if alignment["kind"] == "unknown":
            require(alignment["complete"] is False, "Unknown alignment cannot be complete")
            require(all(flag is None for flag in flags), "Unknown common inputs must remain unknown for all policies")
        else:
            require(alignment["complete"] is True, "Known alignment must be complete")
            require(all(type(flag) is bool for flag in flags), "Observed policy flags must be explicit booleans")
            require(not flags[2] or flags[1], "Alignment gate is not nested inside price progress")
            require(not flags[1] or flags[0], "Price progress gate is not nested inside current activity")
        require(tuple(flags) == ALIGNMENT_FLAGS[alignment["kind"]], "Alignment kind and policy flags disagree")


def interval(result, start, end, initial, original=(), schedule=()):
    curve = result["curve"]
    days = [row["date"] for row in curve]
    require(days == sorted(set(days)), "Curve dates must be unique and ordered")
    require(start <= end, "Reversed attribution interval")
    selected = [row for row in curve if start <= row["date"] <= end]
    prior = [row for row in curve if row["date"] < start]
    entering = prior[-1].get("equity") if prior else initial
    ending = selected[-1].get("equity") if selected else None
    numeric = bool(selected) and finite(entering) and entering > 0 and all(finite(row.get("equity")) for row in selected)
    net = ending - entering if finite(entering) and finite(ending) else None
    peak, max_dd, max_dd_date = entering, 0., None
    if numeric:
        for row in selected:
            peak = max(peak, row["equity"])
            drawdown = (peak - row["equity"]) / peak * 100
            if drawdown > max_dd:
                max_dd, max_dd_date = drawdown, row["date"]
    daily_rows = result.get("daily", [])
    daily = {row["date"]: row for row in daily_rows}
    require(len(daily) == len(daily_rows), "Duplicate daily holdings rows")
    exposures, ages, position_counts, missing_holdings = [], [], [], []
    for row in selected:
        equity, market = row.get("equity"), row.get("marketValue")
        exposures.append(market / equity * 100 if finite(market) and finite(equity) and equity > 0 else None)
        holdings = daily.get(row["date"], {}).get("holdings")
        if isinstance(holdings, list):
            position_counts.append(len(holdings))
            ages.extend(holding.get("holdingDays") for holding in holdings)
        else:
            missing_holdings.append(row["date"])
            position_counts.append(None)
    fills = [row for row in result.get("trades", []) if start <= row["date"] <= end]
    closed = [row for row in result.get("closedTrades", []) if start <= row["exitDate"] <= end]
    decisions = [row for row in result.get("decisions", []) if start <= row["date"] <= end and row.get("action") == "exit"]
    reliability = [row["date"] for row in selected if row.get("valuationReliable") is not True]
    market_known = all(finite(row.get("marketValue")) for row in selected)
    invested = sum(finite(row.get("marketValue")) and row["marketValue"] > 0 for row in selected)
    final_daily = daily.get(selected[-1]["date"], {}) if selected else {}
    return {"requestedStart": start, "requestedEnd": end,
            "observedStart": selected[0]["date"] if selected else None,
            "observedEnd": selected[-1]["date"] if selected else None,
            "observedSessions": len(selected),
            "enteringEquity": entering, "enteringEquityDate": prior[-1]["date"] if prior else None,
            "enteringEquityBasis": "previousObservedClose" if prior else "initialCapital",
            "endingEquity": ending, "netPnlAmount": net,
            "returnPct": (ending / entering - 1) * 100 if numeric else None,
            "maxDrawdownPct": max_dd if numeric else None, "maxDrawdownDate": max_dd_date if numeric else None,
            "endingCash": selected[-1].get("cash") if selected else None,
            "endingMarketValue": selected[-1].get("marketValue") if selected else None,
            "endingPositionCount": len(final_daily["holdings"]) if isinstance(final_daily.get("holdings"), list) else None,
            "status": "empty" if not selected else "numericUnknown" if not numeric else "observedValues;valuationUnverified" if reliability else "observedValues",
            "valuationUnreliableDates": reliability,
            "unknownEquityDates": [row["date"] for row in selected if not finite(row.get("equity"))],
            "investedSessions": invested, "investedSessionPct": invested / len(selected) * 100 if selected and market_known else None,
            "exposurePct": distribution(exposures), "positionCountPerClose": distribution(position_counts),
            "heldSessionAge": distribution(ages), "missingDailyHoldingsDates": missing_holdings,
            "holdingAgeMeaning": "position-close observations, not completed trade duration; missing holding dates reported separately",
            "fillCount": len(fills), "buyFillCount": sum(row["side"] == "buy" for row in fills),
            "sellFillCount": sum(row["side"] == "sell" for row in fills),
            "feesPaid": total(row.get("fee") for row in fills),
            "slippageAlreadyInFillPrices": total(row.get("slippage") for row in fills),
            "sellFillReasons": labels(row.get("reason", "missing") for row in fills if row["side"] == "sell"),
            "queuedExitReasons": labels(row.get("reason", "missing") for row in decisions),
            "exitCountMeaning": "queued decisions and sell fills are separate; partial fills/retries need not equal completed episodes",
            "closedEpisodesByExitDate": closed_distribution(closed),
            "opportunitiesBySignalDate": opportunity_counts(original, schedule,
                result.get("research", {}).get("opportunities", []), start, end),
            "calendarMeaning": "supplied source account curve only; no missing-date compression, interpolation, or calendar reconstruction"}


def compact_period(row, period, policy):
    result = deepcopy(row)
    if "closedEpisodes" in result:
        result["closedEpisodes"].pop("episodes", None)
        result["closedEpisodes"].pop("secondaryClosedNet30", None)
    result.update(period=period, policy=policy, source="savedAccountSummary.periods")
    return result


def build(directory, validation_path=None):
    directory = Path(directory).resolve()
    validation_path = Path(validation_path).resolve() if validation_path else directory / "validation.json"
    required = [directory / name for name in ("comparison.json", "plan.json", "input-completion.json")]
    required += [validation_path, ATLAS, PARENT / "comparison.json"]
    require(all(path.is_file() for path in required), "Comparison, plan, completed inputs, validation and atlas must all exist")
    require(sha(ATLAS) == ATLAS_SHA, "Frozen atlas hash changed")
    pins = {}

    def pin(path):
        path = Path(path)
        pins[location(path)] = sha(path)
        return path

    for path in required:
        pin(path)
    pin(Path(__file__))
    comparison = read(directory / "comparison.json")
    plan = read(directory / "plan.json")
    completion = read(directory / "input-completion.json")
    validation = read(validation_path)
    parent_comparison = read(PARENT / "comparison.json")
    require(validation.get("passed") is True, "Independent verification has not passed")
    require(validation.get("failureCount", 0) == 0, "Independent verification reports failures")
    require(validation.get("comparisonSha256") == sha(directory / "comparison.json"), "Validation comparison binding differs")
    require(validation.get("planSha256") == sha(directory / "plan.json"), "Validation plan binding differs")
    require(comparison["planSha256"] == completion["planSha256"] == sha(directory / "plan.json"), "Plan binding differs")
    require(completion.get("allSchedulesFrozenBeforeAccounts") is True, "All six input schedules were not frozen before accounts")
    require(comparison.get("newAccounts") == 6 and len(comparison["rows"]) == 6, "Exactly six accounts required")
    require(comparison.get("newCandidateVariants") == 2, "Both new filter-policy variants must be recorded")
    require(comparison.get("controlReplayExactMatches") == {period: True for period in PERIODS}, "Both control replays must match the parent exactly")
    by_account = {(row["period"], row["id"]): row for row in comparison["rows"]}
    require(set(by_account) == {(period, policy) for period in PERIODS for policy in POLICIES}, "Account identities differ")
    phases = read(ATLAS)["phases"]
    require(len(phases) == 16 and len({phase["id"] for phase in phases}) == 16, "Expected all original sixteen phases")
    accounts, years, halfyears, phase_rows, reconciliations, coverage_rows = [], [], [], [], [], []
    opportunity_rows, parent_parity = [], []
    for period in PERIODS:
        folder = directory / period
        for name in ("input-completion.json", "input-counts.json", "opportunities.jsonl.gz", "parent-selection.json.gz", "source-binding.json", "source.json"):
            pin(folder / name)
        receipt = read(folder / "input-completion.json")
        require(sha(folder / "input-completion.json") == completion["outputs"][period]["sha256"], "Period completion changed")
        for name in ("input-counts.json", "opportunities.jsonl.gz", "parent-selection.json.gz", "source-binding.json", "source.json"):
            require(sha(folder / name) == receipt["files"][name]["sha256"], "Frozen opportunity input binding differs: " + name)
        source_binding = read(folder / "source-binding.json")
        require(source_binding["accountData"] == receipt["accountData"], "Frozen account-data reference differs")
        original = records(folder / "opportunities.jsonl.gz")
        ids = [row["signalId"] for row in original]
        require(len(ids) == len(set(ids)), "Original opportunity IDs duplicated")
        counts = read(folder / "input-counts.json")
        parents = read(folder / "parent-selection.json.gz")
        require(len(original) == counts["originalTop5"] == parents["top5Count"] == ORIGINAL_COUNTS[period], "Original Top5 opportunity coverage differs")
        check_alignment_rows(original)
        require(counts["outcomes"] == labels(row["priceAlignment"]["kind"] for row in original), "Alignment state coverage differs")
        require(counts["known"] == sum(row["priceAlignment"]["complete"] is True for row in original)
                and counts["unknown"] == sum(row["priceAlignment"]["complete"] is False for row in original),
                "Known/unknown opportunity coverage differs")
        require(counts["controlScheduleExactlyMatchesParent"] is True, "Control schedule changed from parent")
        for opportunity in original:
            opportunity_rows.append({"period": period,
                **{key: opportunity[key] for key in ("signalId", "strategyId", "symbol", "signalDate", "originalRank", "formationDate", "groupSha256")},
                "parentAmountKind": opportunity["parentAmount"]["kind"],
                "parentCurrentActivity": opportunity["parentAmount"]["control"],
                "priceAlignment": deepcopy(opportunity["priceAlignment"]),
                "alignmentDiagnostics": deepcopy(opportunity["alignmentDiagnostics"]),
                "sourceOpportunitySha256": opportunity["sourceOpportunitySha256"],
                "source": location(folder / "opportunities.jsonl.gz")})
        previous_days = None
        for policy in POLICIES:
            names = [policy + suffix for suffix in (".signals.json", ".summary.json", ".result.json.gz")]
            for name in names:
                pin(folder / name)
                expected = receipt["files"].get(name) or comparison["artifacts"][period].get(name)
                require(expected and sha(folder / name) == expected["sha256"], "Input artifact binding differs: " + name)
            schedule = read(folder / (policy + ".signals.json"))
            summary = read(folder / (policy + ".summary.json"))
            result = read(folder / (policy + ".result.json.gz"))
            require(result["status"] == "completed", "Account is not completed")
            require(summary["policyId"] == policy and not summary["quality"]["integrityIssues"], "Account summary identity/integrity issue")
            config = plan["periods"][period]["config"]
            initial = config["initialCapital"]
            require(initial == 500000, "Registered initial capital changed")
            curve_days = [row["date"] for row in result["curve"]]
            require(curve_days == parents["targetDates"], "Curve does not cover all supplied target sessions")
            if previous_days is not None:
                require(curve_days == previous_days, "Policies have different curve calendars")
            previous_days = curve_days
            expected_ids = [row["signalId"] for row in original if row["priceAlignment"][policy] is True]
            require([row["signalId"] for row in schedule] == expected_ids, "Selected schedule changed")
            require(len(schedule) == counts["schedules"][policy], "Recorded selected schedule count differs")
            if policy == "currentActivity":
                parent_path = pin(PARENT / period / "currentActivity.result.json.gz")
                require(sha(parent_path) == parent_comparison["artifacts"][period]["currentActivity.result.json.gz"]["sha256"], "Parent baseline result binding differs")
                require(result == read(parent_path), "Current activity full result differs from parent baseline")
                parent_parity.append({"period": period, "policy": policy, "fullResultExactlyEqual": True,
                    "parentResult": location(parent_path), "parentResultSha256": sha(parent_path),
                    "newResultSha256": sha(folder / (policy + ".result.json.gz")),
                    "comparisonBasis": "complete parsed JSON value equality, without deleting any fields"})
            executed = result["research"]["opportunities"]
            require(len(executed) == len({row["signalId"] for row in executed}), "Account opportunity IDs duplicated")
            overall = interval(result, config["startDate"], config["endDate"], initial, original, schedule)
            saved = summary["account"]
            for key, source in (("returnPct", "netReturnPct"), ("maxDrawdownPct", "maxDrawdownPct"),
                                ("netPnlAmount", "netPnlAmount"), ("endingEquity", "finalEquity"),
                                ("feesPaid", "feesPaid"), ("buyFillCount", "buyFillCount"), ("sellFillCount", "sellFillCount")):
                close(overall[key], saved[source], f"{period}/{policy}/{key}")
            closed = overall["closedEpisodesByExitDate"]
            close(closed["returnPct"]["mean"], summary["closedEpisodes"]["meanReturnPct"], "Closed return mean")
            close(closed["netPnlAmount"], summary["closedEpisodes"]["netPnlAmount"], "Closed PnL")
            row = by_account[(period, policy)]
            close(overall["returnPct"], row["accountReturnPct"], "Comparison return")
            close(overall["maxDrawdownPct"], row["maxDrawdownPct"], "Comparison drawdown")
            require(row["signals"] == len(schedule), "Comparison selected signal count differs")
            entered = overall["opportunitiesBySignalDate"]["entered"]
            require(entered == overall["buyFillCount"] == closed["count"] + summary["openPositions"]["count"], "Entered/closed/open counts differ")
            open_positions = deepcopy(summary["openPositions"])
            open_positions["unrealizedPnlAmount"] = total(p["unrealizedPnlAmount"] for p in open_positions["positions"])
            open_positions["realizedPnlWhileOpen"] = total(p["realizedPnlWhileOpen"] for p in open_positions["positions"])
            accounts.append({"period": period, "policy": policy, "label": summary["policyLabel"],
                "comparisonRow": deepcopy(row), "account": deepcopy(saved), "curveAttribution": overall,
                "openPositionsAtCutoff": open_positions, "unresolvedExits": deepcopy(summary["unresolvedExits"]),
                "concentration": deepcopy(summary["concentration"]), "reconciliation": deepcopy(summary["reconciliation"]),
                "quality": deepcopy(summary["quality"]),
                "sourceQualityScalarFlags": {k: v for k, v in summary["sourceQuality"].items() if not isinstance(v, (dict, list))},
                "sourceQualityFullEvidence": location(folder / (policy + ".summary.json")),
                "warningCount": len(summary.get("warnings", [])), "inputCounts": counts,
                "parentSelectionExclusions": parents["exclusions"]})
            coverage_rows.append({"period": period, "policy": policy,
                **deepcopy(overall["opportunitiesBySignalDate"]),
                "buyFillCount": overall["buyFillCount"], "sellFillCount": overall["sellFillCount"],
                "closedCount": closed["count"], "openCountAtCutoff": open_positions["count"],
                "unresolvedExitCountAtCutoff": summary["unresolvedExits"]["count"],
                "curveSessions": len(curve_days), "parentSelectionExclusions": parents["exclusions"]})
            for saved_period in summary["periods"]:
                if saved_period["kind"] not in ("calendarYear", "calendarHalfYear"):
                    continue
                measured = interval(result, saved_period["requestedStart"], saved_period["requestedEnd"], initial, original, schedule)
                for key in ("returnPct", "maxDrawdownPct", "netPnlAmount", "enteringEquity", "endingEquity"):
                    close(measured[key], saved_period[key], "Saved year/halfyear " + key)
                (years if saved_period["kind"] == "calendarYear" else halfyears).append(compact_period(saved_period, period, policy))
            relevant = [phase for phase in phases if phase["startDate"] <= config["endDate"] and phase["endDate"] >= config["startDate"]]
            assignments = {day: [phase["id"] for phase in relevant if phase["startDate"] <= day <= phase["endDate"]] for day in curve_days}
            require(all(len(matches) == 1 for matches in assignments.values()), "Phases omit or overlap curve sessions")
            this_account_phases = []
            for phase in relevant:
                measured = interval(result, phase["startDate"], phase["endDate"], initial, original, schedule)
                measured.update(period=period, policy=policy, phaseId=phase["id"], phaseTitle=phase["title"],
                    sentimentSummary=phase["sentimentSummary"], dominantThemes=deepcopy(phase["dominantThemes"]),
                    representatives=deepcopy(phase["representatives"]), labelAvailability=phase["labelAvailability"],
                    attributionMeaning="retrospective calendar-phase curve attribution; includes carried positions and mark-to-market changes; never a signal filter")
                this_account_phases.append(measured)
                phase_rows.append(measured)
            summed = total(p["netPnlAmount"] for p in this_account_phases)
            close(summed, saved["netPnlAmount"], "Phase PnL does not sum to full account PnL")
            compounded = (math.prod(1 + p["returnPct"] / 100 for p in this_account_phases) - 1) * 100 if all(finite(p["returnPct"]) for p in this_account_phases) else None
            close(compounded, saved["netReturnPct"], "Phase returns do not compound to full return")
            reconciliations.append({"period": period, "policy": policy, "phaseCount": len(this_account_phases),
                "curveSessions": len(curve_days), "allSessionsAssignedExactlyOnce": True,
                "phaseNetPnlSum": summed, "accountNetPnlAmount": saved["netPnlAmount"],
                "phaseCompoundedReturnPct": compounded, "accountReturnPct": saved["netReturnPct"]})
    require(len(accounts) == 6 and len(years) == 12 and len(halfyears) == 24 and len(phase_rows) == 48,
            "Incomplete six-account/twelve-year/twenty-four-halfyear/forty-eight-phase coverage")
    require(len(opportunity_rows) == 397 and len({(row['period'], row['signalId']) for row in opportunity_rows}) == 397,
            "The complete397 original opportunities must be retained exactly once")
    comparisons = []
    for period in PERIODS:
        keys = ("accountReturnPct", "maxDrawdownPct", "closedMeanPct", "meanWinPct", "meanLossPct",
                "winRatePct", "payoffRatio", "profitFactor", "meanExposurePct", "closed", "openPositions", "signals")
        for priority, control_id, candidate_id in CONTRASTS:
            control, candidate = by_account[(period, control_id)], by_account[(period, candidate_id)]
            comparisons.append({"period": period, "priority": priority, "control": control_id, "candidate": candidate_id,
                "candidateMinusControl": {key: candidate[key] - control[key] if finite(candidate.get(key)) and finite(control.get(key)) else None for key in keys},
                "meaning": "entire nested-filter policy comparison; cash, holdings and executed opportunities can diverge"})
    readout = {"schema": "peer-price-activity-alignment-readout-v1", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "inputPins": pins, "verification": {"passed": True, "path": location(validation_path), "sha256": sha(validation_path),
            "meaning": "upstream independent verification plus this script's arithmetic/coverage reconciliation; not strategy certification"},
        "method": {"accountComparison": "Six separately funded accounts; no cross-period capital chaining or annualization; all397 original opportunities retained once in opportunityRows and all three policies share complete input requirements.",
            "primaryContrast": "activityAligned minus priceProgress, comparing the additional covariance gate conditional on positive relative price progression; this is a whole-policy comparison, not a causal factor estimate.",
            "secondaryContrast": "priceProgress minus currentActivity, separately showing the additional positive relative price progression filter.",
            "parentBaselineParity": "currentActivity complete result must exactly equal its batch28 counterpart with the inherited strategyId/config; no normalization or field removal.",
            "alignment": "Centered covariance of fixed-peer market amount share and equal-weight peer daily log return minus benchmark daily log return over F+1..T. It measures contemporaneous association, not main-fund identity or causal impact.",
            "relativePriceMeaning": "Positive sum of relative log returns means peer outperformance of the benchmark; peers may still fall in absolute price. It is not an executable basket return.",
            "phaseStart": "Last curve equity strictly before phase.startDate; if absent use registered500000 initial capital.",
            "phaseDrawdown": "Running peak starts at entering equity and includes every supplied curve observation in the phase; not reset to first in-phase close.",
            "phasePnl": "Ending equity minus entering equity, including unrealized changes and positions entered earlier. Phase net amounts add; phase return percentages compound and must not be summed.",
            "phaseClosedTrades": "Entire closed episodes assigned by exitDate as a separate distribution; never used as phase mark-to-market contribution.",
            "phaseOpportunities": "Original and selected opportunities assigned by signalDate; terminal disposition evaluated at account cutoff, not at phase end. Fill counts use actual fillDate.",
            "yearsAndHalfyears": "Saved complete summary.periods with episodes removed; return, drawdown and equity boundaries independently recomputed here.",
            "quantiles": "Linear interpolation at(n-1)*q; full distribution null if any required numeric value unknown; empty distributions remain null.",
            "phaseDefinition": "All16 original frozen atlas intervals, no relabeling, merging, selection or additional threshold.",
            "parameterSearch": False, "profitHurdles": None, "newStrategyTrialsByThisScript": 0, "accountsRerunByThisScript": 0},
        "accounts": accounts, "accountRows": deepcopy(comparison["rows"]), "comparisons": comparisons,
        "yearRows": years, "halfYearRows": halfyears, "phaseRows": phase_rows,
        "coverageRows": coverage_rows, "opportunityRows": opportunity_rows,
        "parentBaselineParity": parent_parity, "phaseReconciliation": reconciliations,
        "trialAccounting": {"experimentBatches": 1, "newFilterPolicyVariants": 2,
            "primaryCovarianceCandidateVariants": 1, "accountExecutions": 6,
            "meaning": "Both priceProgress and activityAligned are recorded filter-policy trials; currentActivity is an exact baseline replay. This summarizer runs no accounts or new strategy trials."},
        "limitations": list(dict.fromkeys(plan.get("limitations", []) + [
            "All phase boundaries, themes and representatives are retrospective historical descriptions, never contemporaneously available signal labels or evidence of causal market influence.",
            "This compares whole filtering policies. It does not identify an independent causal amount factor, investor identity, net inflow or ownership concentration.",
            "Amount contains traded prices; a contemporaneous covariance can have mechanical price components. Relative covariance also subtracts the amount-share/benchmark-return covariance.",
            "Price progression weights the fixed20 peers equally, while the activity measure sums their amounts. These are different weighting schemes; one member can dominate amount.",
            "The common amount denominator U(T) is constructed from information available by T, not frozen at F. Ten within-window observations and a T selected for current activity do not establish lead-lag prediction.",
            "All three policies retain the same signal date T and next-opening attempt; this experiment does not test earlier entry or answer whether entry was late.",
            "Repeated anchors, overlapping peer groups and dates are dependent. Six accounts and twelve year-policy rows are not independent replications.",
            "Both periods have prior research exposure; no pristine holdout, return guarantee or stable-profit conclusion.",
            "Open positions remain valued at the account cutoff, without forced liquidation; their unrealized gains are not completed-trade profits.",
            "The supplied source calendar and inherited historical universe, raw-price, rights and opening-execution assumptions are retained. This summary does not certify missing historical market sessions or fix individual stocks.",
            "Daily close observations do not establish intraday price order, guaranteed stop prices or realizable excursion returns."])),
        "decision": {"selectedPolicy": None, "automaticNextStrategy": None,
                     "meaning": "Complete descriptive readout; no preset pass/fail profitability thresholds or automatic strategy selection."},
        "certified": False, "targetAchieved": False}
    for path, expected in pins.items():
        actual_path = Path(path) if Path(path).is_absolute() else ROOT / path
        require(sha(actual_path) == expected, "Input changed while summarizing: " + path)
    destination = directory / "readout.json"
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(readout, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"path": location(destination), "sha256": sha(destination), "accounts": len(accounts),
            "yearRows": len(years), "halfYearRows": len(halfyears), "phaseRows": len(phase_rows),
            "passed": True, "certified": False}


def self_test():
    result = {"curve": [{"date": day, "equity": equity, "cash": equity / 2, "marketValue": equity / 2, "valuationReliable": True}
        for day, equity in (("2024-01-02", 100.), ("2024-01-03", 80.), ("2024-01-04", 90.), ("2024-01-05", 99.))],
        "daily": [{"date": day, "holdings": [{"holdingDays": n}]} for n, day in enumerate(("2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"), 1)],
        "trades": [], "closedTrades": [], "decisions": [], "research": {"opportunities": []}}
    mid = interval(result, "2024-01-03", "2024-01-04", 125.)
    close(mid["enteringEquity"], 100., "Previous close included")
    close(mid["returnPct"], -10., "Full interval return")
    close(mid["maxDrawdownPct"], 20., "Initial boundary included in drawdown")
    first = interval(result, "2024-01-01", "2024-01-02", 125.)
    close(first["returnPct"], -20., "Initial capital fallback")
    close(first["maxDrawdownPct"], 20., "First close loss included")
    last = interval(result, "2024-01-05", "2024-01-05", 125.)
    close(last["returnPct"], 10., "Adjacent interval boundary")
    close(first["netPnlAmount"] + mid["netPnlAmount"] + last["netPnlAmount"], -26., "Phase amounts additive")
    close((1 + first["returnPct"] / 100) * (1 + mid["returnPct"] / 100) * (1 + last["returnPct"] / 100) - 1, 99 / 125 - 1, "Phase returns compound")
    empty = interval(result, "2024-02-01", "2024-02-05", 125.)
    require(empty["returnPct"] is None and empty["maxDrawdownPct"] is None and empty["netPnlAmount"] is None, "Empty interval is not zero return")
    damaged = deepcopy(result)
    damaged["curve"][1]["equity"] = None
    unknown = interval(damaged, "2024-01-03", "2024-01-04", 125.)
    require(unknown["returnPct"] is None and unknown["maxDrawdownPct"] is None, "Missing interior equity cannot be skipped")
    damaged = deepcopy(result)
    damaged["curve"][1]["valuationReliable"] = False
    require(interval(damaged, "2024-01-03", "2024-01-04", 125.)["valuationUnreliableDates"] == ["2024-01-03"], "Quality flag retained")
    close(distribution([-10., 0., 10.])["p05"], -9., "Linear quantiles")
    require(distribution([1., None])["mean"] is None and distribution([1., None])["unknownCount"] == 1, "No silent complete cases")
    c = closed_distribution([{"pnlAmount": -5., "returnPct": -5., "holdingDays": 2}, {"pnlAmount": 15., "returnPct": 15., "holdingDays": 6}])
    close(c["payoffRatio"], 3., "Payoff arithmetic")
    close(c["profitFactor"], 3., "Profit factor arithmetic")
    def opp(sid, current, progress, aligned):
        kind = "unknown" if current is None else "notActive" if not current else "noPositiveProgress" if not progress else "nonPositiveAlignment" if not aligned else "aligned"
        return {"signalId": sid, "signalDate": "2024-01-03",
            "parentAmount": {"kind": "known" if current is not None else "unknown", "control": current},
            "priceAlignment": {"kind": kind, "complete": current is not None,
                "currentActivity": current, "priceProgress": progress, "activityAligned": aligned}}
    original = [opp("a", True, True, True), opp("b", True, True, False), opp("c", True, False, False),
                opp("d", False, False, False), opp("e", None, None, None)]
    check_alignment_rows(original)
    schedule = [{"signalId": "a", "signalDate": "2024-01-03"}]
    executed = [{**schedule[0], "filledQuantity": 1, "status": "filled", "reason": "filled"}]
    counts = opportunity_counts(original, schedule, executed)
    require((counts["originalTemporalTop5"], counts["priceAlignmentUnknown"], counts["selectedSignals"], counts["entered"], counts["filteredOrUnknown"]) == (5, 1, 1, 1, 4), "Opportunity states retained")
    require(counts["selectedByPolicy"] == {"currentActivity": 3, "priceProgress": 2, "activityAligned": 1}, "All nested policy counts retained")
    for bad in (opp("bad", False, True, True), opp("bad", True, False, True), opp("bad", 1, True, True), opp("bad", None, False, False)):
        try:
            check_alignment_rows([bad])
        except ValueError:
            pass
        else:
            raise AssertionError("Malformed or nonnested alignment accepted")
    result["research"]["opportunities"] = executed
    result["trades"] = [{"date": "2024-01-04", "side": "buy", "fee": 1., "slippage": .5}]
    at_signal = interval(result, "2024-01-03", "2024-01-03", 125., original, schedule)
    at_fill = interval(result, "2024-01-04", "2024-01-04", 125., original, schedule)
    require(at_signal["opportunitiesBySignalDate"]["selectedSignals"] == 1 and at_signal["buyFillCount"] == 0
            and at_fill["opportunitiesBySignalDate"]["selectedSignals"] == 0 and at_fill["buyFillCount"] == 1,
            "Signal-date and fill-date attribution separated")
    return {"passed": True, "checks": ["previous-close boundary", "initial-capital fallback", "drawdown includes entering equity",
        "phase PnL addition and return compounding", "empty/unknown intervals", "valuation flags", "linear quantiles and nulls",
        "closed payoff arithmetic", "all opportunity states", "three nested policies and common unknown inputs",
        "signal versus execution dates"], "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT)
    parser.add_argument("--validation", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory, args.validation), ensure_ascii=False))
