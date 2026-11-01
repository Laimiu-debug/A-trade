"""Readout of six frozen board-specific reclaim accounts, without re-running them.

The board sample and cross-sectional rank populations are deliberately changed.
This report is a source/coverage replication, not a new alpha or a pure addition
to the old 3141-stock account. Opportunity details are saved separately as gzip.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.util
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "artifacts/strategy-search/board-coverage-reclaim-v1"
HELPER = ROOT / "scripts/summarize-undercut-reclaim-timing.py"
ATLAS = ROOT / "artifacts/strategy-search/market-phase-atlas-v1/atlas.json"
ATLAS_SHA = "ddf55f2741dca3793923e5da782690aad500510848077f43492fff41af1a44d8"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
POLICY = "immediate"
EPOCHS = (("before20240924", "2024-01-02", "2024-09-23"),
          ("from20240924", "2024-09-24", "2025-12-31"))


def helper_module():
    spec = importlib.util.spec_from_file_location("board_coverage_readout_base", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = helper_module()
require, finite, close = BASE.require, BASE.finite, BASE.close
sha, location, read, records = BASE.sha, BASE.location, BASE.read, BASE.records
total, labels, distribution = BASE.total, BASE.labels, BASE.distribution
closed_distribution, execution_dates = BASE.closed_distribution, BASE.execution_dates


def inside(day, start=None, end=None):
    return day is not None and (start is None or start <= day <= end)


def formation_counts(original, schedule, account_opportunities, start=None, end=None):
    """Immediate opportunities remain distinct from later execution/exit status."""
    original_ids = [r["formationId"] for r in original]
    require(len(original_ids) == len(set(original_ids)), "Duplicate original formation identity")
    require([r["signalId"] for r in schedule] == original_ids, "Immediate schedule is not the original ordered Top5 stream")
    executed = {r["signalId"]: r for r in account_opportunities}
    require(len(executed) == len(account_opportunities) and set(executed) == set(original_ids),
            "Account opportunities do not cover the complete original stream")
    for original_row, signal in zip(original, schedule):
        require(signal["signalDate"] == original_row["formationDate"]
                and signal["symbol"] == original_row["symbol"]
                and signal["originalRank"] == original_row["originalRank"], "Formation and immediate signal differ")
    cohort = [r for r in original if inside(r["formationDate"], start, end)]
    decisions = [executed[r["formationId"]] for r in cohort]
    entered = sum(finite(r.get("filledQuantity")) and r["filledQuantity"] > 0 for r in decisions)
    return {"formationTop5": len(cohort), "selectedSignalsFromFormation": len(cohort), "notScheduledFromFormation": 0,
        "enteredFromFormation": entered, "notEnteredFromFormation": len(cohort) - entered,
        "uniqueFormationDays": len({r["formationDate"] for r in cohort}), "uniqueSymbols": len({r["symbol"] for r in cohort}),
        "terminalStatusCounts": labels(r.get("status", "missing") for r in decisions),
        "terminalReasonCounts": labels(r.get("reason", "missing") for r in decisions),
        "meaning": "formationDate cohort; terminal opportunity status is observed at account cutoff, not interval end"}


def interval(result, start, end, initial, original=(), schedule=()):
    # The helper's accounting remains frozen. Its historical queued-exit selector
    # used action=exit; correct that presentation field in this new wrapper only.
    view = dict(result)
    view["research"] = {"opportunities": []}
    measured = BASE.interval(view, start, end, initial)
    queued = [r for r in result.get("decisions", []) if start <= r["date"] <= end
              and r.get("action") == "sell" and r.get("status") == "queued"]
    measured["queuedExitReasons"] = labels(r.get("reason", "missing") for r in queued)
    measured["queuedExitDecisionCount"] = len(queued)
    measured["queuedExitDefinition"] = "action=sell AND status=queued; queued decisions, partial fills and closed episodes differ"
    account_opps = result.get("research", {}).get("opportunities", [])
    measured["opportunitiesByFormationDate"] = formation_counts(original, schedule, account_opps, start, end)
    measured["opportunitiesByDecisionDate"] = BASE.decision_counts(schedule, account_opps, start, end)
    return measured


def partition_check(parts, overall, original_count, label):
    close(total(r["netPnlAmount"] for r in parts), overall["netPnlAmount"], label + " PnL addition")
    compounded = (math.prod(1 + r["returnPct"] / 100 for r in parts) - 1) * 100 \
        if all(finite(r["returnPct"]) for r in parts) else None
    close(compounded, overall["returnPct"], label + " return compounding")
    require(sum(r["observedSessions"] for r in parts) == overall["observedSessions"], label + " curve coverage")
    for field, expected in (("formationTop5", original_count), ("selectedSignalsFromFormation", original_count),
                            ("enteredFromFormation", overall["buyFillCount"])):
        require(sum(r["opportunitiesByFormationDate"][field] for r in parts) == expected,
                label + " formation coverage: " + field)
    for key in ("selectedSignals", "entered", "notEntered"):
        require(sum(r["opportunitiesByDecisionDate"][key] for r in parts)
                == overall["opportunitiesByDecisionDate"][key], label + " decision coverage: " + key)
    require(sum(r["closedEpisodesByExitDate"]["count"] for r in parts)
            == overall["closedEpisodesByExitDate"]["count"], label + " closed episode coverage")
    return {"kind": label, "passed": True, "intervals": len(parts),
            "netPnlAmount": total(r["netPnlAmount"] for r in parts), "compoundedReturnPct": compounded}


def opportunity_records(period, board, original, schedule, result, summary):
    """A separate gzip sidecar will retain every selected formation and its fate."""
    observed = execution_dates(result)
    require(set(observed) == {r["formationId"] for r in original}, "Missing original account disposition")
    raw_opps = {r["signalId"]: r for r in result["research"]["opportunities"]}
    open_rows = {(r["record"]["symbol"], r["record"]["entryDate"]): r
                 for r in summary["openPositions"]["positions"]}
    require(len(open_rows) == summary["openPositions"]["count"], "Duplicate open episode identity")
    rows = []
    for formation, signal in zip(original, schedule):
        sid = formation["formationId"]
        detail = deepcopy(observed[sid])
        opened = open_rows.get((formation["symbol"], detail["entryDate"]))
        require((opened is not None) == (detail["positionState"] == "openAtCutoff"), "Open position and entry linkage differ")
        rows.append({"period": period, "board": board, "policy": POLICY,
            "formationId": sid, "formationDate": formation["formationDate"], "symbol": formation["symbol"],
            "originalRank": formation["originalRank"], "formationEvidence": deepcopy(formation),
            "signal": deepcopy(signal), "accountOpportunity": deepcopy(raw_opps[sid]),
            "accountDisposition": detail,
            "openPositionAtCutoff": deepcopy(opened) if opened is not None else None})
    require(len(rows) == len(original) == len(schedule), "Complete original opportunity coverage required")
    return rows


def validation_binding(validation, plan_sha, comparison_sha):
    require(validation.get("passed") is True and validation.get("failureCount", 0) == 0,
            "Independent account verification has not passed")
    require(validation.get("planSha256") == plan_sha, "Validation plan hash differs")
    require(validation.get("comparisonSha256") == comparison_sha, "Validation comparison hash differs")


def summarize_loaded_account(period, board, config, original, schedule, metadata, result, summary, row, phases):
    """Summarize one independently verified account; do not load any price source."""
    require(period in PERIODS and board in BOARDS, "Unexpected board/period")
    require(row["period"] == period and row["board"] == board and row["id"] == POLICY,
            "Comparison account identity differs")
    require(result["status"] == "completed" and summary["policyId"] == POLICY, "Wrong or incomplete account summary")
    require(not summary["quality"]["integrityIssues"], "Account has unresolved arithmetic integrity issues")
    require(schedule == [r["signals"][POLICY] for r in original], "Original immediate schedule changed")
    initial, dates = config["initialCapital"], metadata["targetDates"]
    require(initial == 500000 and dates == sorted(set(dates)), "Capital/calendar differs")
    require([r["date"] for r in result["curve"]] == dates, "Complete target curve required")
    all_opportunities = opportunity_records(period, board, original, schedule, result, summary)
    overall = interval(result, config["startDate"], config["endDate"], initial, original, schedule)
    saved = summary["account"]
    for key, source in (("returnPct", "netReturnPct"), ("maxDrawdownPct", "maxDrawdownPct"),
                        ("netPnlAmount", "netPnlAmount"), ("endingEquity", "finalEquity"),
                        ("feesPaid", "feesPaid"), ("buyFillCount", "buyFillCount"), ("sellFillCount", "sellFillCount")):
        close(overall[key], saved[source], f"{period}/{board}/{key}")
    closed = overall["closedEpisodesByExitDate"]
    close(closed["netPnlAmount"], summary["closedEpisodes"]["netPnlAmount"], "Closed net PnL")
    close(closed["returnPct"]["mean"], summary["closedEpisodes"]["meanReturnPct"], "Closed return mean")
    for key, actual in (("accountReturnPct", overall["returnPct"]), ("maxDrawdownPct", overall["maxDrawdownPct"]),
                        ("meanExposurePct", overall["exposurePct"]["mean"]), ("closed", closed["count"]),
                        ("signals", len(schedule)), ("openPositions", summary["openPositions"]["count"])):
        close(row[key], actual, "Comparison " + key)
    entered = overall["opportunitiesByDecisionDate"]["entered"]
    require(entered == overall["buyFillCount"] == closed["count"] + summary["openPositions"]["count"],
            "Entered/closed/open counts differ")
    opened = deepcopy(summary["openPositions"])
    opened["unrealizedPnlAmount"] = total(r["unrealizedPnlAmount"] for r in opened["positions"])
    opened["realizedPnlWhileOpen"] = total(r["realizedPnlWhileOpen"] for r in opened["positions"])
    bridge = summary["reconciliation"]
    close(opened["unrealizedPnlAmount"], bridge["openPositionUnrealizedPnl"], "Open unrealized PnL bridge")
    close(opened["realizedPnlWhileOpen"], bridge["openPositionRealizedPnl"], "Open realized PnL bridge")
    close(total([closed["netPnlAmount"], opened["unrealizedPnlAmount"], opened["realizedPnlWhileOpen"],
                 bridge["rightsAttributionGapAmount"]]), saved["netPnlAmount"], "Closed/open/unallocated PnL bridge")
    quality = summary["sourceQuality"]
    account = {"period": period, "board": board, "id": POLICY, "policy": POLICY,
        "label": summary["policyLabel"], "comparisonRow": deepcopy(row), "account": deepcopy(saved),
        "curveAttribution": overall, "closedEpisodes": deepcopy(summary["closedEpisodes"]),
        "closedDistribution": closed, "openPositionsAtCutoff": opened,
        "unresolvedExits": deepcopy(summary["unresolvedExits"]), "concentration": deepcopy(summary["concentration"]),
        "reconciliation": deepcopy(bridge), "quality": deepcopy(summary["quality"]), "rights": deepcopy(summary["rights"]),
        "sourceQuality": deepcopy(quality),
        "sourceQualityScalarFlags": {k: v for k, v in quality.items() if not isinstance(v, (list, dict))},
        "sourceQualityCoverage": deepcopy(quality.get("coverage")), "warningCount": len(summary.get("warnings", [])),
        "opportunityStatusCounts": deepcopy(summary["opportunityDispositions"]["opportunityCounts"]),
        "configuredUniverseValues": deepcopy(config["universe"]),
        "configuredUniverseMeaning": "Compatibility configuration only: fixed-signal execution does not enforce these minListingDays/minAmount values. Do not infer an effective 120-day or amount entry gate.",
        "formationSampleMeaning": "Period-start fixed identity requires at least61 valid observed OHLCV bars; every formation separately requires61 complete aligned prior slots, then within-board RS rank and Top5."}
    coverage = {"period": period, "board": board, "id": POLICY, "policy": POLICY,
        **deepcopy(overall["opportunitiesByFormationDate"]), **deepcopy(overall["opportunitiesByDecisionDate"]),
        "buyFillCount": overall["buyFillCount"], "sellFillCount": overall["sellFillCount"],
        "closedCount": closed["count"], "openCountAtCutoff": opened["count"],
        "unresolvedExitCountAtCutoff": summary["unresolvedExits"]["count"], "curveSessions": len(dates),
        "valuationUnreliableDays": len(overall["valuationUnreliableDates"]),
        "qualityScalarFlags": deepcopy(account["sourceQualityScalarFlags"]),
        "qualifiedMeaning": "Observed execution states/reasons and inherited historical uncertainty, not certified exchange eligibility."}
    years, halves, phase_rows, epochs = [], [], [], []
    for saved_period in summary["periods"]:
        if saved_period["kind"] not in ("calendarYear", "calendarHalfYear"):
            continue
        measured = interval(result, saved_period["requestedStart"], saved_period["requestedEnd"], initial, original, schedule)
        for key in ("returnPct", "maxDrawdownPct", "netPnlAmount", "enteringEquity", "endingEquity"):
            close(measured[key], saved_period[key], "Saved year/halfyear " + key)
        item = BASE.compact_period(saved_period, period, POLICY)
        item.update(measured)
        item["board"] = board
        (years if saved_period["kind"] == "calendarYear" else halves).append(item)
    relevant = [p for p in phases if p["startDate"] <= config["endDate"] and p["endDate"] >= config["startDate"]]
    require(all(sum(p["startDate"] <= d <= p["endDate"] for p in relevant) == 1 for d in dates),
            "Atlas intervals overlap or omit source target sessions")
    for phase in relevant:
        measured = interval(result, phase["startDate"], phase["endDate"], initial, original, schedule)
        measured.update(period=period, board=board, policy=POLICY, phaseId=phase["id"], phaseTitle=phase["title"],
            sentimentSummary=phase["sentimentSummary"], dominantThemes=deepcopy(phase["dominantThemes"]),
            representatives=deepcopy(phase["representatives"]), labelAvailability=phase["labelAvailability"],
            attributionMeaning="hindsight calendar description only; account mark-to-market changes include carried positions")
        phase_rows.append(measured)
    checks = [partition_check(part, overall, len(original), label)
              for part, label in ((years, "years"), (halves, "halfyears"), (phase_rows, "phases"))]
    if period == "2024-2025":
        for phase_id, start, end in EPOCHS:
            measured = interval(result, start, end, initial, original, schedule)
            measured.update(period=period, board=board, policy=POLICY, phaseId=phase_id,
                attributionMeaning="user-specified2024-09-24 event boundary; actual prior equity and holdings continue without restarting capital")
            epochs.append(measured)
        checks.append(partition_check(epochs, overall, len(original), "userEventEpochs"))
    return {"account": account, "coverage": coverage, "yearRows": years, "halfYearRows": halves,
        "phaseRows": phase_rows, "curveEpochRows": epochs, "opportunities": all_opportunities,
        "reconciliation": {"period": period, "board": board, "passed": True, "checks": checks}}


def build(directory, validation_path=None):
    directory = Path(directory).resolve()
    validation_path = Path(validation_path).resolve() if validation_path else directory / "validation.json"
    names = ("plan.json", "comparison.json", "input-completion.json", "source-completion.json",
             "source-validation.json", "input-validation.json")
    paths = {name: directory / name for name in names}
    destination, sidecar = directory / "readout.json", directory / "opportunity-dispositions.jsonl.gz"
    require(not destination.exists() and not sidecar.exists(), "Readout/sidecar already exists; no overwrite")
    require(all(p.is_file() for p in (*paths.values(), validation_path, ATLAS)), "Validated frozen artifacts required")
    require(sha(ATLAS) == ATLAS_SHA, "Original atlas changed")
    pins = {}

    def pin(path):
        path = Path(path).resolve()
        digest = sha(path)
        require(pins.setdefault(location(path), digest) == digest, "Input changed: " + location(path))
        return path

    def bound(reference):
        require(isinstance(reference, dict) and set(reference) >= {"path", "sha256"}, "Invalid artifact reference")
        path = Path(reference["path"])
        path = pin(path if path.is_absolute() else ROOT / path)
        require(pins[location(path)] == reference["sha256"], "Artifact binding differs: " + reference["path"])
        return path

    for path in (*paths.values(), validation_path, ATLAS, Path(__file__), HELPER):
        pin(path)
    plan, comparison = read(paths["plan.json"]), read(paths["comparison.json"])
    completion, validation = read(paths["input-completion.json"]), read(validation_path)
    plan_sha, comparison_sha = pins[location(paths["plan.json"])], pins[location(paths["comparison.json"])]
    validation_binding(validation, plan_sha, comparison_sha)
    require(plan["schema"] == "board-coverage-reclaim-v1" and plan["boards"] == list(BOARDS)
            and set(plan["periods"]) == set(PERIODS) and plan["mode"] == POLICY, "Registered account set differs")
    require(comparison["planSha256"] == completion["planSha256"] == plan_sha, "Plan receipt differs")
    require(completion["allSchedulesFrozenBeforeAccounts"] is True, "Schedules not frozen before accounts")
    for key, expected in (("newAccounts", 6), ("newUniverseVariants", 3), ("newSetupMechanisms", 0),
                          ("referenceAccountsReused", 0)):
        require(plan[key] == comparison[key] == expected, "Trial accounting differs: " + key)
    require(plan["newEntryPolicies"] == 0 and plan["parameterSearch"] is False
            and plan["profitHurdles"] is None, "This readout cannot select a new entry policy")
    source_completion = read(paths["source-completion.json"])
    source_validation, input_validation = read(paths["source-validation.json"]), read(paths["input-validation.json"])
    require(source_completion["passed"] is True and source_completion["planSha256"] == plan_sha,
            "Source completion not passed or belongs to another plan")
    for receipt, hash_key, target in ((source_validation, "sourceCompletionSha256", paths["source-completion.json"]),
                                      (input_validation, "inputCompletionSha256", paths["input-completion.json"])):
        require(receipt["passed"] is True and receipt["planSha256"] == plan_sha
                and receipt[hash_key] == pins[location(target)], "Source/input verification binding differs")
    require(bound(completion["sourceCompletion"]) == paths["source-completion.json"], "Source completion reference differs")
    for key in ("sourcePins", "inputPins"):
        for name, expected in plan[key].items():
            bound({"path": name, "sha256": expected})
    rows = deepcopy(comparison["rows"])
    by_account = {(r["period"], r["board"]): r for r in rows}
    require(len(rows) == len(by_account) == 6 and set(by_account) == {(p, b) for p in PERIODS for b in BOARDS}
            and all(r["id"] == POLICY for r in rows), "Exactly six unique new board accounts required")
    phases = read(ATLAS)["phases"]
    require(len(phases) == len({p["id"] for p in phases}) == 16
            and all(p["labelAvailability"] == "retrospectiveResearchOnly" for p in phases), "Original hindsight atlas required")
    accounts, years, halves, phase_rows, epochs, coverage, opportunities, partitions = ([] for _ in range(8))
    source_bindings, input_counts = {}, {}
    for period in PERIODS:
        source_bindings[period], input_counts[period] = {}, {}
        manifest_path = pin(directory / period / "source-manifest.json")
        manifest = read(manifest_path)
        require(manifest["passed"] is True and manifest["period"] == period and manifest["planSha256"] == plan_sha,
                "Source manifest identity differs")
        for board in BOARDS:
            folder = directory / period / board
            receipt_path = bound(completion["outputs"][period][board])
            require(receipt_path == folder / "input-completion.json", "Wrong board input receipt path")
            receipt = read(receipt_path)
            require(receipt["planSha256"] == plan_sha and receipt["period"] == period and receipt["board"] == board,
                    "Board input receipt identity differs")
            require(bound(receipt["sourceManifest"]) == manifest_path
                    and bound(receipt["sourceValidation"]) == paths["source-validation.json"], "Board source references differ")
            cache = bound(receipt["accountData"])
            require(cache == directory / period / (board + ".account-data.json.gz")
                    and source_validation["datasetPins"][location(cache)] == receipt["accountData"]["sha256"],
                    "Dataset reference differs from independently verified source")
            inputs = {name: bound(reference) for name, reference in receipt["files"].items()}
            require(all(path.parent == folder for path in inputs.values()), "Input outside registered board folder")
            original, schedule = records(inputs["opportunities.jsonl.gz"]), read(inputs["immediate.signals.json"])
            metadata, counts = read(inputs["signal-metadata.json"]), read(inputs["input-counts.json"])
            require(counts == receipt["counts"] == metadata["counts"], "Input counts differ")
            require(counts["opportunityCount"] == counts["signals"][POLICY] == len(original) == len(schedule),
                    "Complete immediate opportunity stream required")
            require(counts["targetDayCount"] == len(metadata["targetDates"])
                    and counts["sampleCount"] == manifest["boards"][board]["includedCount"], "Cohort/calendar denominator differs")
            daily = records(inputs["daily-evidence.jsonl.gz"])
            require([r["formationDate"] for r in daily] == metadata["targetDates"], "Daily input coverage differs")
            require([sid for r in daily for sid in r["selectedFormationIds"]] == [r["formationId"] for r in original],
                    "Daily selected Top5 differs from opportunities")
            formation_totals = {key: sum(r["counts"][key] for r in daily) for key in daily[0]["counts"]}
            require(formation_totals["sampleCount"] == counts["sampleCount"] * counts["targetDayCount"]
                    and formation_totals["setupCount"] == counts["setupCount"]
                    and formation_totals["selectedCount"] == len(original), "Formation evidence totals differ")
            board_records = [r for r in manifest["records"] if r["board"] == board]
            cohort = deepcopy(manifest["boards"][board])
            # Materializer refs are relative to OUT; account/input refs are
            # repository-relative. Publish only the latter unambiguous form.
            manifest_cache = cohort["accountData"]
            require((directory / manifest_cache["path"]).resolve() == cache
                    and manifest_cache["sha256"] == receipt["accountData"]["sha256"]
                    and manifest_cache["datasetDigest"] == receipt["accountDatasetDigest"],
                    "Manifest/account dataset identity differs")
            cohort["accountData"] = {**manifest_cache, **deepcopy(receipt["accountData"])}
            cohort["selectionStatusCounts"] = labels(r["selectionStatus"] for r in board_records)
            cohort["selectionReasonCounts"] = labels(r["reason"] for r in board_records)
            cohort["discoveredIdentityCount"] = len(board_records)
            for state in ("included", "excluded", "unknown"):
                require(cohort[state + "Count"] == cohort["selectionStatusCounts"].get(state, 0), "Manifest cohort counts differ")
            artifacts = {name: bound(reference) for name, reference in comparison["artifacts"][period][board].items()}
            require(all(path.parent == folder for path in artifacts.values()), "Account artifact outside board folder")
            result, summary = read(artifacts["immediate.result.json.gz"]), read(artifacts["immediate.summary.json"])
            item = summarize_loaded_account(period, board, plan["periods"][period]["config"], original, schedule,
                                            metadata, result, summary, by_account[(period, board)], phases)
            item["account"]["execution"] = "new-independent-account"
            item["account"]["artifactRefs"] = deepcopy(comparison["artifacts"][period][board])
            item["account"]["sourceCohort"] = cohort
            item["account"]["formationEvidenceTotals"] = formation_totals
            item["coverage"].update(sourceCohort=deepcopy(cohort), formationEvidenceTotals=formation_totals,
                benchmarkUnavailableDays=sum(not r["benchmarkPrior60"]["complete"] for r in daily),
                benchmarkReasonCounts=labels(r["benchmarkPrior60"]["reason"] for r in daily),
                formationEvidenceMeaning="Counts are symbol-day observations at each sequential formation screen; different fields have different denominators and must not be added as unique stocks.")
            accounts.append(item["account"])
            coverage.append(item["coverage"])
            years.extend(item["yearRows"]); halves.extend(item["halfYearRows"])
            phase_rows.extend(item["phaseRows"]); epochs.extend(item["curveEpochRows"])
            opportunities.extend(item["opportunities"]); partitions.append(item["reconciliation"])
            input_counts[period][board] = deepcopy(counts)
            source_bindings[period][board] = {"inputCompletion": deepcopy(completion["outputs"][period][board]),
                **{key: deepcopy(receipt[key]) for key in ("accountData", "accountDatasetDigest", "sourceManifest", "sourceValidation")},
                "dailyEvidence": deepcopy(receipt["files"]["daily-evidence.jsonl.gz"]),
                "originalOpportunities": deepcopy(receipt["files"]["opportunities.jsonl.gz"])}
            del result, summary, original, schedule, daily, item
    require((len(accounts), len(years), len(halves), len(phase_rows), len(epochs), len(coverage)) == (6, 12, 24, 48, 6, 6),
            "Incomplete account/year/halfyear/phase/epoch/coverage matrix")
    require(len(opportunities) == sum(c["formationTop5"] for c in coverage)
            == len({(r["period"], r["board"], r["formationId"]) for r in opportunities}), "Original opportunity identities lost")
    readout = {"schema": "board-coverage-reclaim-readout-v1", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "planSha256": plan_sha, "inputPins": pins, "sourceBindings": source_bindings,
        "verification": {"passed": True, "path": location(validation_path), "sha256": sha(validation_path),
            "meaning": "independent source/signal/account verification plus this script's arithmetic and coverage reconciliation; not strategy certification"},
        "method": {
            "population": "Three period-start fixed local-file cohorts under the common at least61 valid strictly pre-start observed OHLCV bars rule; every signal still uses61 consecutive prior calendar slots. Target-period new listings excluded.",
            "ranking": "Original UR prior relative60 midrank and liquidity Top5 within each board; no merged-market ranking or shared cross-board capital.",
            "commonBenchmark": "sh000300 (CSI300 / 沪深300) is the common benchmark for every board. Any saved excessReturn is relative to CSI300, not a board-matched benchmark or beta-neutral return.",
            "boardMeaning": "main/chinext/star describe listing boards, not pure technology-industry membership; this experiment does not isolate a technology sector effect.",
            "compatibilityConfig": "FrozenSignalScorer bypasses DailyScorer. Retained config minListingDays120/minAmount20 must not be described as effective additional eligibility screens.",
            "account": "Six fresh independent500000 two-year immediate accounts. No old3141 baseline reused, four-year chaining, annualization or account run by this script.",
            "comparison": "Source/cohort construction and board transfer replication. Differences also reflect ranks, execution regimes and capital paths; no causal board effect or new alpha claim.",
            "phaseStart": "Last supplied curve equity strictly before interval start, otherwise initial500000; drawdown includes entering equity.",
            "phasePnl": "Ending minus entering equity includes carried holdings and unrealized changes; net amounts add and return percentages compound.",
            "closedEpisodes": "Completed episodes by exitDate are conditional on execution and closure; report open holdings separately. Closed mean is not a regime-specific strategy return.",
            "opportunities": "All selected formation opportunities retained in a separately hashed gzip, including account rejection/nonentry and terminal open holdings; formationDate, signalDate, entryDate and exitDate remain distinct.",
            "formationEvidence": "Daily missingness totals are symbol-day observations with sequential denominators; source identity exclusions and historical eligibility unknowns remain separate.",
            "queuedExitCorrection": "Presentation counts action=sell AND status=queued; frozen helper and account artifacts unchanged.",
            "phaseDefinition": "All16 original hindsight atlas intervals and2024-09-24 boundary, with continuous holdings and equity; absent from entry rules.",
            "unusedBuilderFields": "Original opportunity confirmation/delayed-clock evidence is retained only as source evidence; only immediate is executed in this batch.",
            "unknowns": "Unknown numerical evidence remains null; no outcome deletion, fill interpolation or missing-date compression.",
            "priceSources": "Account datasets are referenced and hash-checked, never parsed or recomputed here; huge sourceBindings remain in original referenced artifacts.",
            "parameterSearch": False, "profitHurdles": None, "accountsRerunByThisScript": 0},
        "accounts": accounts, "accountRows": [deepcopy(by_account[(p, b)]) for p in PERIODS for b in BOARDS],
        "yearRows": years, "halfYearRows": halves, "phaseRows": phase_rows, "curveEpochRows": epochs,
        "coverageRows": coverage, "inputCountsByPeriodBoard": input_counts, "partitionReconciliation": partitions,
        "trialAccounting": {"experimentBatches": 1, "newSetupMechanisms": 0, "newEntryPolicies": 0,
            "newUniverseVariants": 3, "newAccountExecutions": 6, "referenceAccountsReused": 0, "summarizerAccountExecutions": 0},
        "limitations": list(dict.fromkeys(plan["limitations"] + [
            "Rank populations and source construction differ from the old3141 sample; this is not a pure incremental universe expansion.",
            "All boards use the common CSI300 benchmark. Relative/excess returns are not board-index-matched or beta-neutral performance, and listing board is not an industry classification.",
            "Closed-trade averages condition on execution and maturity; terminal open gains/losses can carry across multiple market regimes.",
            "Lower exposure or drawdown alone does not establish an entry advantage; no exposure-normalized return is a counterfactual account.",
            "Holding-session ages on daily positions and completed episode durations have different denominators.",
            "Historical membership, listing dates, ST status and corporate actions remain unknown; present-day files can omit delisted stocks.",
            "No minimum-profit threshold, best-board promotion or automatic next strategy is selected by this report."])),
        "decision": {"selectedBoard": None, "selectedPolicy": None, "automaticNextStrategy": None, "profitHurdle": None},
        "certified": False, "targetAchieved": False}
    for name, expected in pins.items():
        path = Path(name)
        require(sha(path if path.is_absolute() else ROOT / path) == expected, "Input changed while summarizing: " + name)
    # No output is written until the whole matrix and arithmetic have passed.
    with gzip.open(sidecar, "xt", encoding="utf-8", compresslevel=1) as stream:
        for record in opportunities:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    readout["opportunityRowsArtifact"] = {"path": location(sidecar), "sha256": sha(sidecar),
        "count": len(opportunities), "format": "jsonl+gzip", "identity": ["period", "board", "formationId"]}
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(readout, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"passed": True, "path": location(destination), "sha256": sha(destination),
        "opportunityRowsArtifact": readout["opportunityRowsArtifact"],
        "accounts": len(accounts), "yearRows": len(years), "halfYearRows": len(halves), "phaseRows": len(phase_rows),
        "curveEpochRows": len(epochs), "coverageRows": len(coverage), "certified": False}


def self_test():
    dates = ("2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05")
    result = {"curve": [{"date": d, "equity": v, "cash": v / 2, "marketValue": v / 2,
                          "valuationReliable": True} for d, v in zip(dates, (100., 80., 90., 99.))],
        "daily": [{"date": d, "holdings": [{"holdingDays": i}]} for i, d in enumerate(dates, 1)],
        "trades": [], "closedTrades": [], "research": {"opportunities": []},
        "decisions": [{"date": dates[1], "action": "sell", "status": "queued", "reason": "stopLoss"},
                      {"date": dates[1], "action": "sell", "status": "rejected", "reason": "bad"},
                      {"date": dates[1], "action": "buy", "status": "queued", "reason": "bad"},
                      {"date": dates[1], "action": "exit", "status": "queued", "reason": "bad"},
                      {"date": dates[3], "action": "sell", "status": "queued", "reason": "later"}]}
    mid = interval(result, dates[1], dates[2], 125.)
    close(mid["enteringEquity"], 100., "Previous close boundary")
    close(mid["returnPct"], -10., "Interval return")
    close(mid["maxDrawdownPct"], 20., "Entering equity drawdown")
    require(mid["queuedExitReasons"] == {"stopLoss": 1} and mid["queuedExitDecisionCount"] == 1,
            "Only queued sell decisions count")
    require(result["research"] == {"opportunities": []} and result["decisions"][3]["action"] == "exit",
            "Input result changed")
    parts = [interval(result, dates[0], dates[0], 125.), mid, interval(result, dates[3], dates[3], 125.)]
    partition_check(parts, interval(result, dates[0], dates[3], 125.), 0, "synthetic")
    missing = deepcopy(result)
    missing["curve"][1]["equity"] = None
    require(interval(missing, dates[1], dates[2], 125.)["returnPct"] is None, "Missing interior equity skipped")
    require(interval(result, "2024-02-01", "2024-02-02", 125.)["returnPct"] is None, "Empty interval is zero return")
    missing["curve"][1]["valuationReliable"] = False
    require(interval(missing, dates[1], dates[2], 125.)["valuationUnreliableDates"] == [dates[1]], "Quality flag lost")
    require(distribution([1., None])["mean"] is None, "Unknown distribution value dropped")
    originals = [{"formationId": sid, "formationDate": dates[0], "symbol": sid, "originalRank": rank}
                 for rank, sid in enumerate(("a", "b"), 1)]
    signals = [{"signalId": r["formationId"], "symbol": r["symbol"], "signalDate": r["formationDate"],
                "originalRank": r["originalRank"], "strategyId": "test"} for r in originals]
    opps = [{**signals[0], "filledQuantity": 100, "status": "filled", "reason": "filled"},
            {**signals[1], "filledQuantity": 0, "status": "notPlanned", "reason": "unknownEligibility"}]
    counts = formation_counts(originals, signals, opps)
    require(counts["formationTop5"] == 2 and counts["enteredFromFormation"] == 1 and counts["notEnteredFromFormation"] == 1,
            "Original/entered/unentered denominators differ")
    require(counts["terminalReasonCounts"]["unknownEligibility"] == 1, "Unknown execution reason lost")
    try: formation_counts(originals, signals[::-1], opps)
    except ValueError: pass
    else: raise AssertionError("Reordered Top5 accepted")
    joined = deepcopy(result)
    joined["research"]["opportunities"] = opps
    joined["trades"] = [{"date": dates[1], "symbol": "a", "signalId": "a", "side": "buy", "fee": 1., "slippage": .1}]
    open_row = {"record": {"symbol": "a", "entryDate": dates[1]}, "unrealizedPnlAmount": 2., "realizedPnlWhileOpen": 0.}
    rows = opportunity_records("2024-2025", "star", originals, signals, joined,
                               {"openPositions": {"count": 1, "positions": [open_row]}})
    require(rows[0]["accountDisposition"]["entryDate"] == dates[1]
            and rows[0]["accountDisposition"]["exitDate"] is None
            and rows[0]["openPositionAtCutoff"]["unrealizedPnlAmount"] == 2.
            and rows[1]["accountDisposition"]["positionState"] == "notEntered", "Open/unentered sidecar linkage differs")
    joined["closedTrades"] = [{"symbol": "a", "entryDate": dates[1], "exitDate": dates[3], "returnPct": 3., "pnlAmount": 3.}]
    closed_rows = opportunity_records("2024-2025", "star", originals, signals, joined,
                                      {"openPositions": {"count": 0, "positions": []}})
    require(closed_rows[0]["accountDisposition"]["exitDate"] == dates[3]
            and closed_rows[0]["openPositionAtCutoff"] is None, "Closed episode linked as open")
    valid = {"passed": True, "planSha256": "plan", "comparisonSha256": "comparison"}
    validation_binding(valid, "plan", "comparison")
    for mutation in ({"passed": False}, {"failureCount": 1}, {"planSha256": "wrong"}, {"comparisonSha256": "wrong"}):
        try: validation_binding(valid | mutation, "plan", "comparison")
        except ValueError: pass
        else: raise AssertionError("Unvalidated evidence accepted")
    # Exercise the complete pure account adapter for every board and both periods,
    # with no executions and independent500000 accounts, including carry partitions.
    fixture_accounts = []
    for period in PERIODS:
        first, last = (int(value) for value in period.split("-"))
        cal = [f"{year}-{month:02d}-03" for year in (first, last) for month in (1, 7)]
        fixture = {"status": "completed", "curve": [{"date": d, "equity": 500000., "cash": 500000.,
            "marketValue": 0., "valuationReliable": True} for d in cal],
            "daily": [{"date": d, "holdings": []} for d in cal], "trades": [], "closedTrades": [],
            "decisions": [], "research": {"opportunities": []}}
        config = {"initialCapital": 500000, "startDate": f"{first}-01-01", "endDate": f"{last}-12-31",
                  "universe": {"minListingDays": 120, "minAmount20": 100000000}}
        saved_periods, fixture_phases = [], []
        for year in (first, last):
            saved_periods.append({**interval(fixture, f"{year}-01-01", f"{year}-12-31", 500000),
                "kind": "calendarYear", "label": str(year)})
            for number, (start, end) in enumerate(((f"{year}-01-01", f"{year}-06-30"),
                                                  (f"{year}-07-01", f"{year}-12-31")), 1):
                saved_periods.append({**interval(fixture, start, end, 500000), "kind": "calendarHalfYear",
                    "label": f"{year}-H{number}"})
                fixture_phases.append({"id": f"{year}-{number}", "title": "synthetic", "startDate": start,
                    "endDate": end, "sentimentSummary": "synthetic", "dominantThemes": [], "representatives": [],
                    "labelAvailability": "retrospectiveResearchOnly"})
        summary = {"policyId": POLICY, "policyLabel": "synthetic", "quality": {"integrityIssues": []},
            "account": {"netReturnPct": 0., "maxDrawdownPct": 0., "netPnlAmount": 0., "finalEquity": 500000.,
                        "feesPaid": 0., "buyFillCount": 0, "sellFillCount": 0},
            "closedEpisodes": {"netPnlAmount": 0., "meanReturnPct": None},
            "openPositions": {"count": 0, "positions": []}, "reconciliation": {"openPositionUnrealizedPnl": 0.,
                "openPositionRealizedPnl": 0., "rightsAttributionGapAmount": 0.},
            "sourceQuality": {"pointInTimeComplete": False, "coverage": {"localOnly": True}},
            "unresolvedExits": {"count": 0}, "concentration": {}, "rights": {},
            "opportunityDispositions": {"opportunityCounts": {}}, "periods": saved_periods}
        for board in BOARDS:
            comparison = {"period": period, "board": board, "id": POLICY, "accountReturnPct": 0.,
                          "maxDrawdownPct": 0., "meanExposurePct": 0., "closed": 0, "signals": 0, "openPositions": 0}
            item = summarize_loaded_account(period, board, config, [], [], {"targetDates": cal}, fixture,
                                            summary, comparison, fixture_phases)
            require(item["coverage"]["notEntered"] == 0 and item["account"]["sourceQuality"]["pointInTimeComplete"] is False
                    and len(item["yearRows"]) == 2 and len(item["halfYearRows"]) == 4,
                    "Empty-account denominator/quality/matrix differs")
            require(all(check["passed"] for check in item["reconciliation"]["checks"]), "Full account partition failed")
            fixture_accounts.append(item)
    require(len(fixture_accounts) == 6 and sum(len(r["curveEpochRows"]) for r in fixture_accounts) == 6,
            "Six independent board accounts or new-period event rows lost")
    return {"passed": True, "checks": ["previous-close and initial-capital boundaries", "entering equity drawdown",
        "queued sell/status correction with immutable inputs", "interval addition and compounding", "empty and missing numeric evidence",
        "valuation quality and unknown distributions", "complete original/entered/unentered counts", "Top5 order binding",
        "open and closed episode linkage in opportunity sidecar", "exact passed validation bindings",
        "six-board-period full adapter with empty-account and quality preservation",
        "full adapter yearly/halfyear/phase/event partition reconciliation"], "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT)
    parser.add_argument("--validation", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory, args.validation), ensure_ascii=False))
