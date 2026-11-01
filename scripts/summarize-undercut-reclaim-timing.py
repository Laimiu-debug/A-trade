"""Read-only account/phase attribution for frozen undercut/reclaim entry clocks.

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
DEFAULT = ROOT / "artifacts/strategy-search/strong-undercut-reclaim-timing-v1"
ATLAS = ROOT / "artifacts/strategy-search/market-phase-atlas-v1/atlas.json"
ATLAS_SHA = "ddf55f2741dca3793923e5da782690aad500510848077f43492fff41af1a44d8"
PERIODS = ("2022-2023", "2024-2025")
POLICIES = ("immediate", "delay2", "confirm2")
CONTRASTS = (("primary", "delay2", "immediate"),
             ("secondary", "delay2", "confirm2"))


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


def inside(day, start=None, end=None):
    return day is not None and (start is None or start <= day <= end)


def decision_counts(schedule, account_opportunities, start=None, end=None):
    selected = [row for row in schedule if inside(row["signalDate"], start, end)]
    executed = [row for row in account_opportunities if inside(row["signalDate"], start, end)]
    require({row["signalId"] for row in selected} == {row["signalId"] for row in executed},
            "Selected and account decision identities differ")
    entered = sum(finite(row.get("filledQuantity")) and row["filledQuantity"] > 0 for row in executed)
    return {"selectedSignals": len(selected), "entered": entered, "notEntered": len(executed) - entered,
            "terminalStatusCounts": labels(row.get("status", "missing") for row in executed),
            "terminalReasonCounts": labels(row.get("reason", "missing") for row in executed),
            "meaning": "signalDate is actual policy decision date; terminal dispositions observed at full account cutoff, not phase end"}


def formation_counts(original, schedule, account_opportunities, start=None, end=None):
    by_id = {row["formationId"]: row for row in original}
    require(len(by_id) == len(original), "Duplicate formation IDs")
    selected_ids = {row["signalId"] for row in schedule}
    require(selected_ids <= set(by_id), "Schedule signal ID lacks original formation")
    executed = {row["signalId"]: row for row in account_opportunities}
    require(set(executed) == selected_ids, "Account opportunity identities differ from schedule")
    formations = [row for row in original if inside(row["formationDate"], start, end)]
    cohort_ids = {row["formationId"] for row in formations}
    selected = [executed[sid] for sid in cohort_ids & selected_ids]
    entered = sum(finite(row.get("filledQuantity")) and row["filledQuantity"] > 0 for row in selected)
    return {"formationTop5": len(formations),
            "confirmationStates": labels(confirmation_state(row) for row in formations),
            "selectedSignalsFromFormation": len(selected), "notScheduledFromFormation": len(formations) - len(selected),
            "enteredFromFormation": entered, "notEnteredFromFormation": len(selected) - entered,
            "terminalStatusCounts": labels(row.get("status", "missing") for row in selected),
            "terminalReasonCounts": labels(row.get("reason", "missing") for row in selected),
            "meaning": "formationDate cohort; later decisions, entries and exits remain assigned to their original formation here; terminal dispositions observed at full account cutoff"}


def confirmation_state(row):
    state = row["confirmation"]["status"]
    require(state in ("passed", "failed", "unknown", "censored"), "Invalid confirmation state")
    return state


def execution_dates(result):
    """Attach only observed account dates; never reinterpret open positions as exits."""
    buys = [row for row in result["trades"] if row["side"] == "buy"]
    by_signal = {row["signalId"]: row for row in buys}
    require(len(by_signal) == len(buys), "Multiple entry fills require explicit episode handling")
    closed = {(row["symbol"], row["entryDate"]): row for row in result["closedTrades"]}
    require(len(closed) == len(result["closedTrades"]), "Duplicate closed episode entry identity")
    entries = {(row["symbol"], row["date"]) for row in buys}
    require(set(closed) <= entries, "Closed episode has no corresponding entry fill")
    answer = {}
    for opportunity in result["research"]["opportunities"]:
        sid = opportunity["signalId"]
        require(sid not in answer, "Duplicate account opportunity ID")
        buy = by_signal.get(sid)
        entered = finite(opportunity.get("filledQuantity")) and opportunity["filledQuantity"] > 0
        require(entered == (buy is not None), "Filled opportunity and entry trade disagree")
        episode = closed.get((buy["symbol"], buy["date"])) if buy else None
        answer[sid] = {"signalId": sid, "signalDate": opportunity["signalDate"],
            "entryDate": buy["date"] if buy else None, "exitDate": episode["exitDate"] if episode else None,
            "positionState": "closed" if episode else "openAtCutoff" if buy else "notEntered",
            "status": opportunity["status"], "reason": opportunity["reason"],
            "filledQuantity": opportunity.get("filledQuantity"), "attemptDate": opportunity.get("attemptDate"),
            "closedReturnPct": episode.get("returnPct") if episode else None,
            "closedPnlAmount": episode.get("pnlAmount") if episode else None}
    require(set(by_signal) <= set(answer), "Entry fill has no account opportunity")
    return answer


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
            "opportunitiesByFormationDate": formation_counts(original, schedule,
                result.get("research", {}).get("opportunities", []), start, end),
            "opportunitiesByDecisionDate": decision_counts(schedule,
                result.get("research", {}).get("opportunities", []), start, end),
            "calendarMeaning": "supplied source account curve only; no missing-date compression, interpolation, or calendar reconstruction"}


def compact_period(row, period, policy):
    result = deepcopy(row)
    if "closedEpisodes" in result:
        result["closedEpisodes"].pop("episodes", None)
        result["closedEpisodes"].pop("secondaryClosedNet30", None)
    result.update(period=period, policy=policy, source="savedAccountSummary.periods")
    return result


def check_formations(original, metadata):
    ids = [row["formationId"] for row in original]
    require(len(ids) == len(set(ids)), "Original formation IDs duplicated")
    target_dates = metadata["targetDates"]
    require(target_dates == sorted(set(target_dates)), "Target calendar must be unique and ordered")
    target_index = {day: i for i, day in enumerate(target_dates)}
    for row in original:
        day, sid = row["formationDate"], row["formationId"]
        require(day in target_index, "Formation outside target calendar")
        require(set(row["signals"]) == set(POLICIES) and set(row["decisionDates"]) == set(POLICIES),
                "Missing clock signal/date evidence")
        state = confirmation_state(row)
        j = target_index[day] + 2
        later = target_dates[j] if j < len(target_dates) else None
        expected_dates = {"immediate": day, "delay2": later, "confirm2": later}
        require(row["decisionDates"] == expected_dates, "Decision dates do not follow source target slots")
        require((state == "censored") == (later is None), "Confirmation censoring differs from target cutoff")
        expected_flags = {"immediate": True, "delay2": later is not None, "confirm2": state == "passed"}
        for policy in POLICIES:
            signal = row["signals"][policy]
            require((signal is not None) == expected_flags[policy], "Signal eligibility differs from clock evidence")
            if signal is not None:
                require(set(signal) == {"signalId", "strategyId", "symbol", "signalDate", "originalRank"},
                        "Signal schema differs")
                require(signal["signalId"] == sid and signal["symbol"] == row["symbol"]
                        and signal["originalRank"] == row["originalRank"]
                        and signal["signalDate"] == expected_dates[policy], "Formation/signal identity differs")


def build(directory, validation_path=None):
    directory = Path(directory).resolve()
    validation_path = Path(validation_path).resolve() if validation_path else directory / "validation.json"
    required = [directory / name for name in ("comparison.json", "plan.json", "input-completion.json")]
    required += [validation_path, ATLAS]
    require(all(path.is_file() for path in required), "Comparison, plan, completed inputs, validation and atlas must exist")
    require(sha(ATLAS) == ATLAS_SHA, "Frozen atlas hash changed")
    pins = {}

    def pin(path):
        path = Path(path)
        pins[location(path)] = sha(path)
        return path

    for path in required:
        pin(path)
    pin(Path(__file__))
    comparison, plan = read(directory / "comparison.json"), read(directory / "plan.json")
    completion, validation = read(directory / "input-completion.json"), read(validation_path)
    require(validation.get("passed") is True and validation.get("failureCount", 0) == 0,
            "Independent verification has not passed")
    require(validation.get("comparisonSha256") == sha(directory / "comparison.json"), "Validation comparison binding differs")
    require(validation.get("planSha256") == sha(directory / "plan.json"), "Validation plan binding differs")
    require(comparison["planSha256"] == completion["planSha256"] == sha(directory / "plan.json"), "Plan binding differs")
    require(completion.get("allSchedulesFrozenBeforeAccounts") is True, "All six schedules must freeze before accounts")
    for key, value in (("newSetupMechanisms", 1), ("newPolicyVariants", 3), ("newAccounts", 6)):
        require(comparison.get(key) == plan.get(key) == value, "Trial accounting differs: " + key)
    by_account = {(row["period"], row["id"]): row for row in comparison["rows"]}
    require(len(comparison["rows"]) == 6 and set(by_account) == {(p, m) for p in PERIODS for m in POLICIES},
            "Exactly the six registered account identities required")
    phases = read(ATLAS)["phases"]
    require(len(phases) == 16 and len({phase["id"] for phase in phases}) == 16, "Expected all original sixteen phases")
    accounts, years, halfyears, phase_rows, reconciliations, coverage_rows = [], [], [], [], [], []
    opportunity_rows, period_counts = [], {}
    for period in PERIODS:
        folder = directory / period
        pin(folder / "input-completion.json")
        require(sha(folder / "input-completion.json") == completion["outputs"][period]["sha256"], "Period completion changed")
        receipt = read(folder / "input-completion.json")
        for name in ("input-counts.json", "opportunities.jsonl.gz", "signal-metadata.json", "source-binding.json", "source.json", "daily-evidence.jsonl.gz"):
            pin(folder / name)
            require(sha(folder / name) == receipt["files"][name]["sha256"], "Frozen input changed: " + name)
        source_binding = read(folder / "source-binding.json")
        require(source_binding["accountData"] == receipt["accountData"], "Account-data reference differs")
        original = records(folder / "opportunities.jsonl.gz")
        counts, metadata = read(folder / "input-counts.json"), read(folder / "signal-metadata.json")
        require(counts == receipt["counts"] == metadata["counts"], "Input counts differ across receipts")
        check_formations(original, metadata)
        require(counts["sampleCount"] == 3141 and counts["targetDayCount"] == len(metadata["targetDates"]),
                "Source population/calendar count differs")
        require(counts["opportunityCount"] == len(original), "Original Top5 formation coverage differs")
        require(counts["confirmation"] == {state: sum(confirmation_state(row) == state for row in original)
                for state in ("passed", "failed", "unknown", "censored")}, "Confirmation coverage differs")
        require(counts["signals"] == {policy: sum(row["signals"][policy] is not None for row in original)
                for policy in POLICIES}, "All clock schedule counts differ")
        period_counts[period] = deepcopy(counts)
        retained = {}
        for opportunity in original:
            retained[opportunity["formationId"]] = {"period": period, **deepcopy(opportunity),
                "accountDispositions": {}, "source": location(folder / "opportunities.jsonl.gz")}
            opportunity_rows.append(retained[opportunity["formationId"]])
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
            require(curve_days == metadata["targetDates"], "Curve does not cover all supplied target sessions")
            if previous_days is not None:
                require(curve_days == previous_days, "Policies have different curve calendars")
            previous_days = curve_days
            expected_schedule = [row["signals"][policy] for row in original if row["signals"][policy] is not None]
            require(schedule == expected_schedule, "Selected schedule changed")
            require(len(schedule) == counts["signals"][policy], "Recorded selected schedule count differs")
            executed = result["research"]["opportunities"]
            require(len(executed) == len({row["signalId"] for row in executed}), "Account opportunity IDs duplicated")
            observed_dates = execution_dates(result)
            for opportunity in original:
                sid = opportunity["formationId"]
                signal = opportunity["signals"][policy]
                detail = observed_dates.get(sid)
                require((detail is not None) == (signal is not None), "Formation schedule/account join differs")
                retained[sid]["accountDispositions"][policy] = {"formationId": sid,
                    "formationDate": opportunity["formationDate"],
                    "decisionDate": opportunity["decisionDates"][policy],
                    **(detail if detail is not None else {"signalId": sid, "signalDate": None,
                        "entryDate": None, "exitDate": None, "positionState": "notScheduled",
                        "status": "notScheduled", "reason": confirmation_state(opportunity),
                        "filledQuantity": 0, "attemptDate": None, "closedReturnPct": None, "closedPnlAmount": None})}
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
            entered = overall["opportunitiesByDecisionDate"]["entered"]
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
                "formationUniverse": "full frozen 3141 source population; no later leader selection"})
            coverage_rows.append({"period": period, "policy": policy,
                **deepcopy(overall["opportunitiesByFormationDate"]),
                **deepcopy(overall["opportunitiesByDecisionDate"]),
                "buyFillCount": overall["buyFillCount"], "sellFillCount": overall["sellFillCount"],
                "closedCount": closed["count"], "openCountAtCutoff": open_positions["count"],
                "unresolvedExitCountAtCutoff": summary["unresolvedExits"]["count"],
                "curveSessions": len(curve_days), "formationUniverse": "full frozen 3141 source population; no later leader selection"})
            for saved_period in summary["periods"]:
                if saved_period["kind"] not in ("calendarYear", "calendarHalfYear"):
                    continue
                measured = interval(result, saved_period["requestedStart"], saved_period["requestedEnd"], initial, original, schedule)
                for key in ("returnPct", "maxDrawdownPct", "netPnlAmount", "enteringEquity", "endingEquity"):
                    close(measured[key], saved_period[key], "Saved year/halfyear " + key)
                annual = compact_period(saved_period, period, policy)
                annual.update(opportunitiesByFormationDate=measured["opportunitiesByFormationDate"],
                    opportunitiesByDecisionDate=measured["opportunitiesByDecisionDate"])
                (years if saved_period["kind"] == "calendarYear" else halfyears).append(annual)
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
            for field, expected in (("formationTop5", len(original)),
                    ("selectedSignalsFromFormation", len(schedule)), ("enteredFromFormation", entered)):
                require(sum(p["opportunitiesByFormationDate"][field] for p in this_account_phases) == expected,
                        "Formation phase counts do not reconcile: " + field)
            for field, expected in (("selectedSignals", len(schedule)), ("entered", entered)):
                require(sum(p["opportunitiesByDecisionDate"][field] for p in this_account_phases) == expected,
                        "Decision phase counts do not reconcile: " + field)
            require(sum(p["closedEpisodesByExitDate"]["count"] for p in this_account_phases) == closed["count"],
                    "Closed phase episodes do not reconcile")
            reconciliations.append({"period": period, "policy": policy, "phaseCount": len(this_account_phases),
                "formationAndDecisionCountsReconciled": True, "closedEpisodeCountsReconciled": True,
                "curveSessions": len(curve_days), "allSessionsAssignedExactlyOnce": True,
                "phaseNetPnlSum": summed, "accountNetPnlAmount": saved["netPnlAmount"],
                "phaseCompoundedReturnPct": compounded, "accountReturnPct": saved["netReturnPct"]})
    require(len(accounts) == 6 and len(years) == 12 and len(halfyears) == 24 and len(phase_rows) == 48,
            "Incomplete six-account/twelve-year/twenty-four-halfyear/forty-eight-phase coverage")
    require(len({(row["period"], row["formationId"]) for row in opportunity_rows}) == len(opportunity_rows),
            "Every original formation must be retained exactly once")
    require(all(set(row["accountDispositions"]) == set(POLICIES) for row in opportunity_rows),
            "Missing per-clock terminal disposition")
    comparisons = []
    for period in PERIODS:
        keys = ("accountReturnPct", "maxDrawdownPct", "closedMeanPct", "meanWinPct", "meanLossPct",
                "winRatePct", "payoffRatio", "profitFactor", "meanExposurePct", "closed", "openPositions", "signals")
        for priority, control_id, candidate_id in CONTRASTS:
            control, candidate = by_account[(period, control_id)], by_account[(period, candidate_id)]
            comparisons.append({"period": period, "priority": priority, "control": control_id, "candidate": candidate_id,
                "candidateMinusControl": {key: candidate[key] - control[key] if finite(candidate.get(key)) and finite(control.get(key)) else None for key in keys},
                "meaning": "whole entry-clock policy comparison; budgets, holdings, execution and subsequent opportunities interact; not a causally isolated timing estimate"})
    readout = {"schema": "strong-undercut-reclaim-timing-readout-v1", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "inputPins": pins, "verification": {"passed": True, "path": location(validation_path), "sha256": sha(validation_path),
            "meaning": "upstream independent verification plus this script's arithmetic/coverage reconciliation; not strategy certification"},
        "method": {"accountComparison": "Six separately funded accounts; no cross-period capital chaining or annualization. All original formation Top5 opportunities retained once with all three policy dispositions.",
            "primaryContrast": "immediate minus delay2; same formation set with immediate versus unconditional two-session waiting policy.",
            "secondaryContrast": "confirm2 minus delay2; same delayed decision clock, with versus without two-close support confirmation.",
            "formation": "T-1-known strong stock undercuts its prior20-session low and closes strictly back above that frozen support at T; ranking and Top5 frozen before future confirmation.",
            "confirmation": "T+1 and T+2 closes must remain at or above frozen support with registered valid bars; failed, unknown and censored remain distinct. No missing-date compression.",
            "cutoff": "Late formations keep immediate opportunities even when T+2 is outside target; no complete-case account rerun or deletion to equalize opportunities. Decision on final target date is retained even without a later opening attempt.",
            "phaseStart": "Last curve equity strictly before phase.startDate; if absent use registered500000 initial capital.",
            "phaseDrawdown": "Running peak starts at entering equity and includes every supplied curve observation; not reset to first in-phase close.",
            "phasePnl": "Ending equity minus entering equity, including unrealized changes and carried positions. Phase net amounts add; return percentages compound and must not be summed.",
            "phaseClosedTrades": "Whole closed episodes assigned by exitDate separately; not mark-to-market interval contribution.",
            "phaseOpportunities": "Original and selected formation cohorts use formationDate. Actual policy decisions use signalDate in a separate table. Entries use actual buy-fill date; closed episodes use actual exitDate. Terminal dispositions are observed at account cutoff, not phase end.",
            "yearsAndHalfyears": "Saved complete summary.periods with episodes removed; boundaries, return and drawdown independently recomputed, plus both formation and decision counts.",
            "quantiles": "Linear interpolation at(n-1)*q; any unknown numeric value makes the full distribution null; empty distributions remain null.",
            "phaseDefinition": "All16 original frozen atlas intervals; retrospective interpretation only, without relabeling, merging or selection.",
            "parameterSearch": False, "profitHurdles": None, "newStrategyTrialsByThisScript": 0,
            "accountsRerunByThisScript": 0, "futurePathDiagnosticsAdded": False},
        "accounts": accounts, "accountRows": deepcopy(comparison["rows"]), "comparisons": comparisons,
        "yearRows": years, "halfYearRows": halfyears, "phaseRows": phase_rows,
        "coverageRows": coverage_rows, "opportunityRows": opportunity_rows,
        "inputCountsByPeriod": period_counts, "phaseReconciliation": reconciliations,
        "trialAccounting": {"experimentBatches": 1, "newSetupMechanisms": 1, "newPolicyVariants": 3,
            "accountExecutions": 6, "meaning": "All three entry policies are recorded trials; this summarizer runs no accounts or new strategies."},
        "limitations": list(dict.fromkeys(plan.get("limitations", []) + [
            "All phase boundaries, themes and representatives are retrospective descriptions, not contemporaneously available signal labels.",
            "The historical periods were already inspected in previous research; this is neither an untouched holdout nor evidence of prospective stability.",
            "A support undercut and recovery does not identify absorption, investor identity, main-force control or an inevitable future leader.",
            "Immediate versus delayed policy accounts can differ through prices, cash, holdings and fill eligibility. Confirmed-only entry also selects on two extra closes; it cannot be attributed entirely to waiting.",
            "Repeated formations and shared market dates are dependent. Phase returns and six account results are not independent replications.",
            "Phase opportunity cohorts may execute in later phases; the separate formation/decision/fill/exit dates must not be interchanged.",
            "Entry and exit simulation, raw source prices, historical eligibility, missing rights data and market-session coverage retain their original limitations.",
            "Open positions and unexecuted exits at the account cutoff remain distinct from completed trade returns."])),
        "decision": {"selectedPolicy": None, "automaticNextStrategy": None,
            "meaning": "Complete descriptive readout without profit thresholds or automatic strategy selection."},
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
        "formationOpportunities": len(opportunity_rows), "passed": True, "certified": False}


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
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    def opp(sid, day, status):
        j = dates.index(day) + 2
        delayed = dates[j] if j < len(dates) else None
        def signal(decision):
            return {"signalId": sid, "strategyId": "test", "symbol": sid,
                    "signalDate": decision, "originalRank": 1}
        return {"formationId": sid, "formationDate": day, "symbol": sid, "originalRank": 1,
            "decisionDates": {"immediate": day, "delay2": delayed, "confirm2": delayed},
            "confirmation": {"status": status}, "signals": {"immediate": signal(day),
                "delay2": signal(delayed) if delayed else None,
                "confirm2": signal(delayed) if status == "passed" else None}}
    original = [opp("a", dates[0], "passed"), opp("b", dates[1], "failed"),
                opp("c", dates[2], "censored"), opp("d", dates[3], "censored")]
    check_formations(original, {"targetDates": dates})
    delayed = original[0]["signals"]["delay2"]
    executed = [{**delayed, "filledQuantity": 1, "status": "filled", "reason": "filled"}]
    fc = formation_counts(original, [delayed], executed, dates[0], dates[0])
    dc = decision_counts([delayed], executed, dates[0], dates[0])
    require(fc["formationTop5"] == fc["enteredFromFormation"] == 1 and dc["selectedSignals"] == 0,
            "Formation remains assigned to original date despite later decision")
    require(decision_counts([delayed], executed, dates[2], dates[2])["selectedSignals"] == 1,
            "Decision assigned to actual delayed date")
    all_counts = formation_counts(original, [delayed], executed)
    require(all_counts["confirmationStates"] == {"censored": 2, "failed": 1, "passed": 1},
            "Unselected and censored formations retained")
    for mutation in ("signalId", "censor", "confirmedBackdate", "dropImmediate"):
        bad = deepcopy(original)
        if mutation == "signalId": bad[0]["signals"]["delay2"]["signalId"] = "different"
        elif mutation == "censor": bad[2]["confirmation"]["status"] = "failed"
        elif mutation == "confirmedBackdate": bad[0]["signals"]["confirm2"]["signalDate"] = dates[0]
        else: bad[3]["signals"]["immediate"] = None
        try: check_formations(bad, {"targetDates": dates})
        except ValueError: pass
        else: raise AssertionError("Malformed formation clocks accepted: " + mutation)
    result["research"]["opportunities"] = executed
    result["trades"] = [{"date": dates[3], "symbol": "a", "signalId": "a", "side": "buy", "fee": 1., "slippage": .5}]
    at_formation = interval(result, dates[0], dates[0], 125., original, [delayed])
    at_decision = interval(result, dates[2], dates[2], 125., original, [delayed])
    at_fill = interval(result, dates[3], dates[3], 125., original, [delayed])
    require(at_formation["opportunitiesByFormationDate"]["selectedSignalsFromFormation"] == 1
            and at_formation["opportunitiesByDecisionDate"]["selectedSignals"] == 0
            and at_decision["opportunitiesByDecisionDate"]["selectedSignals"] == 1
            and at_decision["buyFillCount"] == 0 and at_fill["buyFillCount"] == 1,
            "Formation, decision and fill dates remain separate")
    observed = execution_dates(result)["a"]
    require(observed["entryDate"] == dates[3] and observed["exitDate"] is None
            and observed["positionState"] == "openAtCutoff", "Open position not counted as closed")
    closed_result = deepcopy(result)
    closed_result["closedTrades"] = [{"symbol": "a", "entryDate": dates[3], "exitDate": "2024-01-08",
                                      "returnPct": 3., "pnlAmount": 3.}]
    require(execution_dates(closed_result)["a"]["exitDate"] == "2024-01-08", "Closed episode linked to original entry")
    return {"passed": True, "checks": ["previous-close boundary", "initial-capital fallback", "drawdown includes entering equity",
        "phase PnL addition and return compounding", "empty/unknown intervals", "valuation flags", "linear quantiles and nulls",
        "closed payoff arithmetic", "all formation states and end censoring", "three clock identities and delayed decisions",
        "formation versus decision versus execution dates", "observed entry/exit linkage and open positions"], "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT)
    parser.add_argument("--validation", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory, args.validation), ensure_ascii=False))
