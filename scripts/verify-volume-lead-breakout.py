"""Independent exact-window price/volume queue and full cash-account audit.

No new production builder or runner is imported; no account is executed here.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "artifacts/strategy-search/volume-lead-breakout-v1"
FAMILY = "volume-lead-breakout-v1"
SID = "volumeLeadBreakoutV1"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
EXITS = ("trail10",)
POLICIES = ("priceBreakout", "volumeLead")
STATES = ("passed", "failed", "unknown")
COMMON_STATES = ("candidate", "notTriggered", "commonUnknown")
REASONS = ("insufficientSourceHistory", "missingAlignedBar", "nonpositiveOrUnknownOpen", "nonpositiveOrUnknownHigh",
           "nonpositiveOrUnknownLow", "nonpositiveOrUnknownClose", "nonpositiveOrUnknownVolume", "nonpositiveOrUnknownAmount",
           "invalidOHLCEnvelope", "nonfinitePriorMeanAmount", "nonfiniteObvLevel",
           "currentBreakoutFalse", "previousBreakoutTrue", "previousUpFalse")
BASIS = {
    "protocol": "docs/volume-lead-breakout-v1.json",
    "commonWindow": "Exactly23 source-calendar slots T-22..T; missing slots are never compressed",
    "quality": "Every slot needs finite strictly positive OHLC, volume and amount, and a valid OHLC envelope",
    "qualityLimits": "Valid numbers do not establish historical suspension, corporate-action or investor identity; no extra status, basis, preclose, ST or listing-age filter",
    "priceSetup": "C_T > max(H_T-20..T-1); C_T-1 > max(H_T-21..T-2) is false; C_T-1 > C_T-2",
    "commonOrder": ["mean(amount_T-20..T-1) descending", "symbol ascending"],
    "commonTop5": "Freeze price-setup Top5 before OBV admission; no backfill or account-state selection",
    "obv": "OBV_T-22=0; each subsequent level adds sign(close change)*current volume through T-1; flat close adds0",
    "obvComparison": "Strict OBV_T-1 > max(OBV_T-21..T-2); baseline and T are not in the20 comparison levels",
    "arithmetic": "Mean amount uses math.fsum(values)/20; OBV levels use math.fsum((previousLevel,signedVolume)); nonfinite derived values are commonUnknown before ranking",
    "reasonCounts": "Nonexclusive reasons, each counted at most once per symbol-day; known false setup predicates are distinct from unknown evidence",
    "signalClock": "Signal at T close, including the final target session; inherited account attempts at the next source-session open",
    "scope": "Fixed-cohort historical development comparison; no parameter search, date gate or independent holdout claim",
}
ACCOUNT_HELPER = "scripts/verify-full-gap-retention.py"
ACCOUNT_HELPER_SHA = "394729cbb46bd71d7920d0ba30c361e6153204e7c522856ed281d35d3bf3833e"


def load_account_reference():
    path = ROOT / ACCOUNT_HELPER
    with path.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != ACCOUNT_HELPER_SHA:
            raise AssertionError("Frozen account oracle changed")
    spec = importlib.util.spec_from_file_location("_volume_lead_independent_account_math", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


G = load_account_reference()
P = G.P
require, finite, same, sha, digest, read, records, ref, bound = (
    getattr(G, name) for name in ("require", "finite", "same", "sha", "digest", "read", "records", "ref", "bound"))
DEPENDENCIES = {ACCOUNT_HELPER: ACCOUNT_HELPER_SHA, **G.DEPENDENCIES,
    "scripts/verify-relative-repair-screen.py": "d332acb6874b5bf5a3954d0d47824fabfea9284d28b902d788b9fe0314cda690",
    "scripts/verify-market-context-screen.py": "f08d534351f55a6715dd1e43533f1ac914799d55ba4f1d502ca8c870fca235b1",
    "scripts/verify-supplemental-force-context.py": "d2390f1142238ae00fc3f2430eaa4b91bdd66a408a0657f496f0a6158fc05e9d"}

def account_reference(result, checkpoint, summary, schedule, config, dataset, period, exit_name, atlas):
    require(exit_name == "trail10", "Only the fixed single exit is registered")
    G.SID = SID
    return G.account_reference(result, checkpoint, summary, schedule, config, dataset, period, exit_name, atlas)


def positive_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def source_reference(dataset, start, end):
    require(isinstance(dataset, dict) and isinstance(start, str) and isinstance(end, str) and start <= end,
            "Ordered source range required")
    require(isinstance(dataset["calendar"], (list, tuple)), "Source calendar sequence required")
    G.calendar_reference(list(dataset["calendar"]))
    calendar = [day for day in dataset["calendar"] if day <= end]
    targets = [day for day in calendar if day >= start]
    require(bool(targets), "No target source session")
    securities = dataset["securities"]
    require(isinstance(securities, list) and bool(securities), "Full nonempty frozen cohort required")
    symbols = [s.get("symbol") for s in securities]
    require(all(isinstance(s, str) and s.strip() for s in symbols) and len(set(symbols)) == len(symbols),
            "Unique nonempty source identities required")
    permitted, by_symbol = set(calendar), {}
    for security in securities:
        bars = security.get("bars")
        require(isinstance(bars, (list, tuple)), "Raw bars must be a sequence")
        retained, previous = {}, None
        for bar in bars:
            require(isinstance(bar, dict), "Dated bar object required")
            day = bar.get("date")
            G.calendar_reference([day])
            if day > end: continue
            require(previous is None or previous < day, "Historical bars must be unique and increasing")
            previous, retained[day] = day, bar
        require(set(retained) <= permitted, "Historical bar outside exact source calendar")
        by_symbol[security["symbol"]] = retained
    return calendar, targets, sorted(symbols), by_symbol


def bar_issues_reference(bar):
    if bar is None: return ["missingAlignedBar"]
    fields = ("open", "high", "low", "close", "volume", "amount")
    issues = ["nonpositiveOrUnknown" + name.title() for name in fields if not positive_number(bar.get(name))]
    if all(positive_number(bar.get(name)) for name in fields[:4]) and not (
            bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]):
        issues.append("invalidOHLCEnvelope")
    return issues


def obv_reference(window_dates, bars):
    """Frozen T-22 baseline; include T-21..T-1, never current-T volume."""
    require(len(window_dates) == len(bars) == 23, "OBV expects common exact23-slot evidence")
    directions, signed, levels = [None], [0], [0]
    for offset in range(1, 22):
        direction = (bars[offset]["close"] > bars[offset - 1]["close"]) - (bars[offset]["close"] < bars[offset - 1]["close"])
        increment = direction * bars[offset]["volume"]
        try:
            level = math.fsum((levels[-1], increment))
        except (ValueError, OverflowError):
            return None
        if not finite(level): return None
        directions.append(direction); signed.append(increment); levels.append(level)
    maximum = max(levels[1:21])
    return {"baselineDate": window_dates[0], "levelDates": window_dates[:22], "closeDirections": directions,
            "signedVolumes": signed, "levels": levels, "comparisonDates": window_dates[1:21],
            "previousLevel": levels[21], "prior20Maximum": maximum, "passed": levels[21] > maximum}


def masks_reference(state):
    require(state in ("passed", "failed"), "Every selected candidate has complete OBV evidence")
    return {"priceBreakout": {"status": "included", "reason": "commonPriceBreakout"},
            "volumeLead": {"status": "included" if state == "passed" else "excluded",
                           "reason": "priorObvHigh" if state == "passed" else "priorObvNotHigh"}}


def evaluate_reference(calendar, at, bars, bar_reasons, invalid_prefix):
    first = max(0, at - 22)
    dates = calendar[first:at + 1]
    if at < 22 or invalid_prefix[at + 1] != invalid_prefix[first]:
        slots = [{"date": calendar[i], "reasons": bar_reasons[i]} for i in range(first, at + 1) if bar_reasons[i]]
        reasons = {r for slot in slots for r in slot["reasons"]}
        if at < 22: reasons.add("insufficientSourceHistory")
        return "commonUnknown", [r for r in REASONS if r in reasons], slots, None
    window = bars[first:at + 1]
    high, prior_high = max(b["high"] for b in window[2:22]), max(b["high"] for b in window[1:21])
    closes = [window[i]["close"] for i in (22, 21, 20)]
    predicates = {"currentBreakout": closes[0] > high, "previousNotBreakout": closes[1] <= prior_high,
                  "previousUp": closes[1] > closes[2]}
    failures = [r for k, r in zip(predicates, ("currentBreakoutFalse", "previousBreakoutTrue", "previousUpFalse")) if not predicates[k]]
    if failures: return "notTriggered", failures, [], None
    try:
        mean_amount = math.fsum(b["amount"] for b in window[2:22]) / 20
    except (ValueError, OverflowError):
        mean_amount = None
    reasons = [] if positive_number(mean_amount) else ["nonfinitePriorMeanAmount"]
    obv = obv_reference(dates, window)
    if obv is None: reasons.append("nonfiniteObvLevel")
    if reasons: return "commonUnknown", reasons, [], None
    evidence = {"windowDates": dates, "priorAmountDates": dates[2:22], "priorMeanAmount": mean_amount,
                "currentClose": closes[0], "previousClose": closes[1], "beforePreviousClose": closes[2],
                "currentPrior20High": high, "previousPrior20High": prior_high, "predicates": predicates, "obv": obv}
    return "candidate", [], [], evidence


def totals_reference(candidates, opportunities, statuses, reasons):
    selected = Counter(r["formationStatus"] for r in opportunities)
    policy = {p: Counter(r["policyAdmission"][p]["status"] for r in opportunities) for p in POLICIES}
    return {"currentCandidateCount": len(candidates), "top5OpportunityCount": len(opportunities),
            "rankExcludedCount": len(candidates) - len(opportunities),
            "selectedFormationCounts": {s: selected[s] for s in STATES},
            "policyCounts": {p: {s: policy[p][s] for s in ("included", "excluded", "unknown")} for p in POLICIES},
            "signals": {p: policy[p]["included"] for p in POLICIES},
            "notScheduled": {p: len(opportunities) - policy[p]["included"] for p in POLICIES},
            "commonStatusCounts": {s: statuses[s] for s in COMMON_STATES},
            "commonReasonCounts": {r: reasons[r] for r in REASONS}}


def reference_build(dataset, *, start, end, progress=None):
    G.calendar_reference([start]); G.calendar_reference([end])
    require(progress is None or callable(progress), "Progress must be callable")
    calendar, targets, symbols, sources = source_reference(dataset, start, end)
    prepared = {}
    for symbol in symbols:
        bars = [sources[symbol].get(day) for day in calendar]
        issues = [bar_issues_reference(bar) for bar in bars]
        prefix = [0]
        for issue in issues: prefix.append(prefix[-1] + bool(issue))
        prepared[symbol] = (bars, issues, prefix)
    indexes = {day: i for i, day in enumerate(calendar)}
    daily, opportunities, all_candidates = [], [], []
    all_statuses, all_reasons = Counter(), Counter()
    for day in targets:
        at = indexes[day]
        candidates, unknowns, today = [], [], []
        statuses, reasons = Counter(), Counter()
        for symbol in symbols:
            state, why, invalid, evidence = evaluate_reference(calendar, at, *prepared[symbol])
            statuses[state] += 1; reasons.update(set(why))
            if state == "commonUnknown": unknowns.append({"symbol": symbol, "reasons": why, "invalidSlots": invalid})
            if state == "candidate":
                candidates.append({"symbol": symbol, "formationStatus": "passed" if evidence["obv"]["passed"] else "failed", "evidence": evidence})
        candidates.sort(key=lambda r: (-r["evidence"]["priorMeanAmount"], r["symbol"]))
        for rank, candidate in enumerate(candidates, 1): candidate.update(originalRank=rank, selectedTop5=rank <= 5)
        for c in candidates[:5]:
            identity = f"{SID}:{day}:{c['symbol']}"
            signal = {"signalId": identity, "strategyId": SID, "symbol": c["symbol"], "signalDate": day, "originalRank": c["originalRank"]}
            admission = masks_reference(c["formationStatus"])
            today.append({**signal, "formationId": identity, "formationDate": day, "peerFormationDate": calendar[at - 1],
                          "formationStatus": c["formationStatus"], "evidence": deepcopy(c["evidence"]), "policyAdmission": admission,
                          "signals": {p: deepcopy(signal) if admission[p]["status"] == "included" else None for p in POLICIES}})
        counts = totals_reference(candidates, today, statuses, reasons)
        daily.append({"formationDate": day, "peerFormationDate": calendar[at - 1] if at else None,
                      "currentCandidates": candidates, "commonRanking": [{k: c[k] for k in ("symbol", "originalRank", "selectedTop5", "formationStatus")} for c in candidates],
                      "selectedSignalIds": [r["signalId"] for r in today], "commonUnknowns": unknowns, "counts": counts})
        opportunities.extend(today); all_candidates.extend(candidates)
        all_statuses.update(statuses); all_reasons.update(reasons)
        if progress and (len(daily) % 50 == 0 or day == targets[-1]): progress({"verifiedDate": day, "opportunities": len(opportunities)})
    counts = {"sampleCount": len(symbols), "targetDayCount": len(targets),
              **totals_reference(all_candidates, opportunities, all_statuses, all_reasons)}
    metadata = {"version": FAMILY, "strategyId": SID, "policies": list(POLICIES), "calendar": calendar,
                "targetDates": targets, "symbols": symbols, "start": start, "end": end, "counts": deepcopy(counts),
                "basis": deepcopy(BASIS), "certified": False, "targetAchieved": False}
    schedules = {p: [deepcopy(r["signals"][p]) for r in opportunities if r["signals"][p] is not None] for p in POLICIES}
    return {"metadata": metadata, "dailyRows": daily, "opportunities": opportunities, "schedules": schedules, "counts": counts}


def check_schedule_partition(expected):
    c, opportunities = expected["counts"], expected["opportunities"]
    require(sum(c["commonStatusCounts"].values()) == c["sampleCount"] * c["targetDayCount"], "All symbol-days must conserve")
    require(c["commonStatusCounts"]["candidate"] == c["currentCandidateCount"] == c["top5OpportunityCount"] + c["rankExcludedCount"], "All common candidates conserve")
    require(c["top5OpportunityCount"] == len(opportunities) == sum(c["selectedFormationCounts"].values()) and
            c["selectedFormationCounts"]["unknown"] == 0, "Selected candidates have complete known evidence")
    require(c["signals"]["priceBreakout"] == len(opportunities) and c["signals"]["volumeLead"] == c["selectedFormationCounts"]["passed"], "Ordered admission masks differ")
    for policy in POLICIES:
        schedule = expected["schedules"][policy]
        require(len(schedule) + c["notScheduled"][policy] == len(opportunities) and c["policyCounts"][policy]["unknown"] == 0,
                "Selected unknown or rejected slot lost")
        require(schedule == sorted(schedule, key=lambda s: (s["signalDate"], s["originalRank"], s["symbol"], s["signalId"])) and
                len({s["signalId"] for s in schedule}) == len(schedule), "Frozen schedule identity/order differs")
        require(all(set(s) == {"signalId", "strategyId", "symbol", "signalDate", "originalRank"} and
                    s["strategyId"] == SID and s["signalId"] == f"{SID}:{s['signalDate']}:{s['symbol']}" and
                    type(s["originalRank"]) is int and 1 <= s["originalRank"] <= 5 for s in schedule), "Canonical price signal identity differs")
        require(schedule == [r["signals"][policy] for r in opportunities if r["signals"][policy] is not None], "Excluded original slots must not refill")



def require_pin(plan, item):
    path = bound(item)
    require(plan["inputPins"].get(item["path"]) == item["sha256"], "Reference not frozen as input: " + item["path"])
    return path


def pretests_reference(directory, plan, completion):
    tests = read(directory / "pre-account-tests.json")
    require(tests["passed"] is True and tests["planSha256"] == sha(directory / "plan.json") and
            tests["sourcePins"] == plan["sourcePins"] and tests["allChecksBeforeFirstNewAccount"] is True,
            "Passing pre-account tests must bind all frozen sources")
    names = ("test_research_volume_lead_breakout.py", "test_volume_lead_breakout_verifier.py",
             "test_volume_lead_breakout_readout.py", "test_research_fixed_signal_account.py",
             "test_research_account_fees.py", "test_research_account_outcomes.py")
    require(len(tests["commands"]) == len(names) and all(c["exitCode"] == 0 for c in tests["commands"]), "Six actual successful test commands required")
    for command, name in zip(tests["commands"], names):
        require(command["argv"][1:] == ["-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", name, "-v"]
                and "\nOK\n" in command["stderr"], "Wrong or unsuccessful pretest command")
    stamp = datetime.fromisoformat
    require(stamp(plan["registeredAt"]) <= stamp(tests["startedAt"]) <= stamp(tests["completedAt"]) <=
            stamp(completion["completedAt"]), "Pretest/preparation chronology differs")
    return tests



def registered_plan(directory):
    plan = read(directory / "plan.json")
    require(plan["schema"] == FAMILY and plan["strategyId"] == SID and plan["boards"] == list(BOARDS) and
            plan["policies"] == list(POLICIES) and plan["exits"] == list(EXITS) and list(plan["periods"]) == list(PERIODS),
            "Frozen experiment matrix differs")
    trials = {"newSetupMechanisms": 1, "newEntryPolicies": 2, "newAccounts": 12, "referenceAccountsReused": 0,
              "newUniverseVariants": 0, "completedResearchBatchesAdded": 1}
    same(plan["rules"]["trialAccounting"], trials, "Preregistered scope")
    require(all(plan[k] == v for k, v in trials.items()), "Plan trial accounting differs")
    require(all(plan[k] is False for k in ("parameterSearch", "holdoutClaim", "certified", "targetAchieved")), "Unsupported claim")
    same(read(bound(plan["protocol"])), plan["rules"], "Frozen protocol content")
    require(plan["rules"]["candidateIds"] == list(POLICIES) and plan["rules"]["exits"] == list(EXITS) and
            plan["rules"]["profitHurdles"] is None, "Unregistered policy or outcome hurdle")
    for family in ("sourcePins", "inputPins"):
        for path, expected in plan[family].items(): bound({"path": path, "sha256": expected})
    expected_sources = {**DEPENDENCIES, Path(__file__).relative_to(ROOT).as_posix(): sha(__file__),
        "tests/test_volume_lead_breakout_verifier.py": sha(ROOT / "tests/test_volume_lead_breakout_verifier.py")}
    for path, expected in expected_sources.items():
        require(plan["sourcePins"].get(path) == expected and sha(ROOT / path) == expected, "Unfrozen independent dependency: " + path)
    require(plan["sourcePins"].get(plan["protocol"]["path"]) == plan["protocol"]["sha256"], "Protocol not source-pinned")
    require(sha(directory / "program-before.json") == plan["previousProgram"]["sha256"], "Pre-experiment registry snapshot differs")
    require(plan["previousPlan"]["sha256"] == "51826b8447f68f55600058a7356f7f35a9c94708b9e99dbd40cd502c026ea349", "Completed48 plan differs")
    require(plan["sourcePlan"]["sha256"] == "a911df3c26aa6e662ce8c62d6374e856427c311200b2dc99bcf409c2725ffefa", "Original fixed source plan differs")
    require_pin(plan, plan["sourcePlan"])
    require(plan["accountContract"] == P.FEE_BINDING, "Frozen dated fee contract differs")
    fee = read(require_pin(plan, plan["accountContract"]))["feePolicy"]
    same(plan["feePolicy"], fee, "Unchanged fee policy")
    require(type(plan["feePolicy"]["minCommission"]) is int and plan["feePolicy"]["minCommission"] == 5, "Integer5 fee identity must remain")
    parent = read(require_pin(plan, plan["previousPlan"]))
    for name in ("sourceData", "sourcePlan", "atlas", "accountContract", "feePolicy"):
        same(plan[name], parent[name], "Unchanged48 source/account contract: " + name)
    require_pin(plan, plan["atlas"])
    def rename(value):
        if isinstance(value, dict): return {k: rename(v) for k, v in value.items()}
        if isinstance(value, list): return [rename(v) for v in value]
        return SID if value == "leaderPersistenceAdmissionV1" else deepcopy(value)
    for period in PERIODS:
        expected = rename(parent["periods"][period])
        same(plan["periods"][period], expected, "Only account SID changes")
        config = expected["policyConfigs"]["trail10"]
        require(config["initialCapital"] == 500000 and config["maxPositions"] == 5 and config["stopLossPct"] == .05 and
                config["trailingStopPct"] == .1 and config["takeProfitPct"] == config["maxHoldingDays"] == 0 and
                config["slippageBps"] == 10 and config["allocation"]["strategyIds"] == [SID], "Fixed account settings differ")
    return plan


def source_bindings_reference(plan):
    answer = G.source_bindings_reference(plan)
    keys = ("previousPlan", "previousValidation", "previousReadoutValidation", "previousDelivery", "previousDecision")
    docs = {key: read(require_pin(plan, plan[key])) for key in keys}
    pins = {plan[key]["path"]: plan[key]["sha256"] for key in keys}
    parent, proof, readout_proof, delivery, decision = (docs[k] for k in keys)
    require(delivery["candidateTrialsAfter"] == 48 and proof["passed"] is True and readout_proof["passed"] is True and
            proof["planSha256"] == plan["previousPlan"]["sha256"] == readout_proof["planSha256"] and
            readout_proof["accountValidationSha256"] == plan["previousValidation"]["sha256"], "Completed48 proof lineage differs")
    require(proof["verifierSha256"] == parent["sourcePins"]["scripts/verify-leader-persistence-admission.py"], "48 original verifier identity differs")
    require(proof["counts"]["accounts"] == proof["counts"]["newAccounts"] == 18 and proof["counts"]["reusedAccounts"] == 0,
            "Previous fixed execution contract not fully audited")
    inventory = {r["path"]: r["sha256"] for r in delivery["files"]}
    require(len(inventory) == len(delivery["files"]), "Duplicate prior sealed member")
    for path, expected in inventory.items():
        bound({"path": path, "sha256": expected})
    for key in ("previousPlan", "previousValidation", "previousReadoutValidation", "previousDecision"):
        require(inventory.get(plan[key]["path"]) == plan[key]["sha256"], "48 receipt not inside final delivery")
    previous_dir = bound(plan["previousPlan"]).parent
    for name, expected in (("comparison.json", proof["comparisonSha256"]), ("readout.json", readout_proof["readoutSha256"]),
                           ("input-completion.json", proof["inputCompletionSha256"]), ("input-validation.json", proof["inputValidationSha256"])):
        path = previous_dir / name
        require(sha(path) == expected == inventory.get(path.relative_to(ROOT).as_posix()), "48 completed artifact not sealed")
        require(decision["inputPins"].get(name) == expected, "Previous decision does not bind audited artifact")
        pins[path.relative_to(ROOT).as_posix()] = expected
    require(decision["inputPins"]["validation.json"] == plan["previousValidation"]["sha256"] and
            decision["inputPins"]["readout-validation.json"] == plan["previousReadoutValidation"]["sha256"] and
            decision["newAccounts"] == 18 and decision["reusedAccounts"] == 0, "Previous decision scope/proof differs")
    for period in PERIODS:
        for board in BOARDS:
            source = plan["sourceData"][period][board]
            require(proof["datasetPins"].get(source["accountData"]["path"]) == source["accountData"]["sha256"], "48 source cache identity differs")
            for name in ("accountData", "sourceManifest", "sourceValidation", "sourceCompletion", "upstreamInputReceipt"):
                if name in source: require_pin(plan, source[name])
    answer.update(previousEvidencePins=pins, previousValidationSha256=plan["previousValidation"]["sha256"])
    return answer


def frozen_groups(directory, plan):
    completion = read(directory / "input-completion.json")
    require(completion["schema"] == FAMILY + "-inputs" and completion["planSha256"] == sha(directory / "plan.json") and
            completion["preAccountTestsSha256"] == sha(directory / "pre-account-tests.json") and
            completion["allSchedulesFrozenBeforeAccounts"] is True and completion["newAccountsExecuted"] == 0, "Complete input freeze required")
    groups = completion["groups"]
    require([(g["period"], g["board"]) for g in groups] == [(p, b) for p in PERIODS for b in BOARDS], "Six source input groups/order")
    for group in groups:
        period, board = group["period"], group["board"]
        folder, source = directory / period / board, plan["sourceData"][period][board]
        require(group["accountData"] == source["accountData"] and group["accountDatasetDigest"] == source["accountDatasetDigest"], "Original cache input changed")
        for key, name in (("metadata", "metadata.json"), ("daily", "daily-evidence.jsonl.gz"), ("opportunities", "opportunities.jsonl.gz"), ("inputReceipt", "input-completion.json")):
            require(bound(group[key]) == folder / name, "Prepared artifact path changed")
        require(list(group["schedules"]) == list(POLICIES), "Both schedule arms required")
        for policy in POLICIES:
            require(bound(group["schedules"][policy]) == folder / (policy + ".signals.json"), "Policy schedule path changed")
        receipt = read(bound(group["inputReceipt"]))
        require(receipt["planSha256"] == sha(directory / "plan.json"), "Per-source receipt plan changed")
        same({k: v for k, v in receipt.items() if k not in ("planSha256", "completedAt")},
             {k: v for k, v in group.items() if k != "inputReceipt"}, "Exact prepared per-source closure")
        require(datetime.fromisoformat(plan["registeredAt"]) <= datetime.fromisoformat(receipt["completedAt"]) <=
                datetime.fromisoformat(completion["completedAt"]), "Preparation chronology changed")
    expected = {"sourceGroups": 6, "preparedSchedules": 12, "accountScheduleBindings": 12,
                "opportunities": sum(g["counts"]["top5OpportunityCount"] for g in groups),
                "signals": {p: sum(g["counts"]["signals"][p] for g in groups) for p in POLICIES}}
    same(completion["counts"], expected, "Complete input counters")
    return completion, groups


def verify_inputs(directory):
    require(not (directory / "input-validation.json").exists(), "Never overwrite input proof")
    plan = registered_plan(directory)
    sources = source_bindings_reference(plan)
    completion, groups = frozen_groups(directory, plan)
    tests = pretests_reference(directory, plan, completion)
    prepare = read(directory / "prepare-start.json")
    require(prepare["planSha256"] == sha(directory / "plan.json") and prepare["preAccountTestsSha256"] == sha(directory / "pre-account-tests.json"), "Exclusive prepare-start binding")
    require(datetime.fromisoformat(tests["completedAt"]) <= datetime.fromisoformat(prepare["startedAt"]) <= datetime.fromisoformat(completion["completedAt"]), "Tests must precede preparation")
    totals = {"sourceGroups": 0, "sourceDays": 0, "currentCandidateCount": 0, "sourceOpportunityCount": 0,
              "rankExcludedCount": 0, "preparedSchedules": 0, "accountScheduleBindings": 0,
              "selectedFormationCounts": {s: 0 for s in STATES}, "commonStatusCounts": {s: 0 for s in COMMON_STATES},
              "signals": {p: 0 for p in POLICIES}, "accountsExecuted": 0}
    reports = []
    for group in groups:
        period, board = group["period"], group["board"]
        data = read(bound(group["accountData"]))
        require(digest(data) == group["accountDatasetDigest"] and data["period"] == period and data["board"] == board and
                len(data["securities"]) == plan["sourceData"][period][board]["members"], "Bound full raw source differs")
        spec = plan["periods"][period]
        expected = reference_build(data, start=spec["startDate"], end=spec["endDate"])
        same(read(bound(group["metadata"])), expected["metadata"], "Full input metadata")
        same(records(bound(group["daily"])), expected["dailyRows"], "All common status counts and ranked candidates")
        same(records(bound(group["opportunities"])), expected["opportunities"], "Every commonTop5 and two masks")
        same(group["counts"], expected["counts"], "Independent source counts")
        check_schedule_partition(expected)
        for policy in POLICIES:
            require(read(bound(group["schedules"][policy])) == expected["schedules"][policy], "Exact ordered policy schedule differs")
            totals["signals"][policy] += len(expected["schedules"][policy])
        c = expected["counts"]
        totals["sourceGroups"] += 1; totals["sourceDays"] += c["targetDayCount"]
        totals["sourceOpportunityCount"] += c["top5OpportunityCount"]
        for name in ("currentCandidateCount", "rankExcludedCount"): totals[name] += c[name]
        for state in STATES: totals["selectedFormationCounts"][state] += c["selectedFormationCounts"][state]
        for state in COMMON_STATES: totals["commonStatusCounts"][state] += c["commonStatusCounts"][state]
        totals["preparedSchedules"] += 2; totals["accountScheduleBindings"] += 2
        reports.append({"period": period, "board": board, "counts": c, "sourceBinding": group["accountData"],
                        "sourceOpportunityCount": c["top5OpportunityCount"]})
        require(digest(data) == group["accountDatasetDigest"], "Input audit mutated source")
        print(json.dumps({"checkedSource": [period, board], "counts": c, "passed": True}), flush=True)
        del data, expected
        gc.collect()
    require((totals["sourceGroups"], totals["preparedSchedules"], totals["accountScheduleBindings"]) == (6, 12, 12) and
            totals["sourceOpportunityCount"] == completion["counts"]["opportunities"], "Complete independently rebuilt input matrix")
    registered_plan(directory)
    return P.write_proof(directory, "input-validation.json", {"passed": True, "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "planSha256": sha(directory / "plan.json"), "inputCompletionSha256": sha(directory / "input-completion.json"),
        "preAccountTestsSha256": sha(directory / "pre-account-tests.json"), "verifierSha256": sha(__file__),
        "sourcePins": DEPENDENCIES, "inputPins": plan["inputPins"], "preparedGroups": groups, "counts": totals,
        "groups": reports, **sources, "checks": ["Original six raw source identities independently bound through48",
            "Every target symbol-day rebuilt from exact23-slot OHLCVA; unknowns retained in fixed cohort counts",
            "Common price-onset/previous-up candidates ranked before strict OBV mask; no backfill",
            "All12 schedules independently rebuilt before any account; no new account executed by auditor"],
        "holdoutClaim": False, "certified": False, "targetAchieved": False})


def verify_accounts(directory):
    require(not (directory / "validation.json").exists(), "Never overwrite account proof")
    plan = registered_plan(directory)
    sources = source_bindings_reference(plan)
    completion, groups = frozen_groups(directory, plan)
    pretests_reference(directory, plan, completion)
    inputs = read(directory / "input-validation.json")
    hashes = {key: sha(directory / name) for key, name in (("planSha256", "plan.json"),
        ("inputCompletionSha256", "input-completion.json"), ("inputValidationSha256", "input-validation.json"),
        ("preAccountTestsSha256", "pre-account-tests.json"), ("runStartSha256", "run-start.json"))}
    require(inputs["passed"] is True and inputs["verifierSha256"] == sha(__file__), "Same frozen independent input auditor required")
    for key in ("planSha256", "inputCompletionSha256", "preAccountTestsSha256"):
        require(inputs[key] == hashes[key], "Independent input proof binding differs")
    same(inputs["preparedGroups"], groups, "All independently rebuilt prepared inputs unchanged")
    for key, value in sources.items(): same(inputs[key], value, "Inherited proof changed: " + key)
    require(inputs["counts"]["sourceGroups"] == 6 and inputs["counts"]["preparedSchedules"] == 12 and
            inputs["counts"]["accountScheduleBindings"] == 12 and inputs["counts"]["sourceOpportunityCount"] ==
            completion["counts"]["opportunities"], "Independent source populations differ")
    start = read(directory / "run-start.json")
    for key, filename in (("inputValidation", "input-validation.json"), ("inputCompletion", "input-completion.json"), ("tests", "pre-account-tests.json")):
        same(start[key], ref(directory / filename), "Actual account start binding")
    stamp = datetime.fromisoformat
    require(start["planSha256"] == hashes["planSha256"] and stamp(completion["completedAt"]) <=
            stamp(inputs["verifiedAt"]) <= stamp(start["startedAt"]), "Input generation/audit must precede every account")
    comparison = read(directory / "comparison.json")
    require(comparison["schema"] == FAMILY + "-comparison" and all(comparison[k] is False for k in
            ("holdoutClaim", "certified", "targetAchieved")), "Comparison scope changed")
    for key, value in hashes.items(): require(comparison[key] == value, "Comparison dependency differs: " + key)
    for key, value in plan["rules"]["trialAccounting"].items(): require(comparison[key] == value, "Actual trial accounting differs")
    rows = comparison["rows"]
    require([tuple(r[k] for k in ("period", "board", "exit", "policy")) for r in rows] ==
            [(g["period"], g["board"], "trail10", p) for g in groups for p in POLICIES], "All12 account identities/order required")
    require(len(comparison["groupCompletions"]) == 6, "Six terminal source-group receipts required")
    for group, item in zip(groups, comparison["groupCompletions"]):
        period, board = group["period"], group["board"]
        folder = directory / period / board / "trail10"
        require(bound(item) == folder / "group-completion.json", "Terminal path differs")
        terminal = read(bound(item))
        require(tuple(terminal[k] for k in ("period", "board", "exit")) == (period, board, "trail10") and
                terminal["completed"] is True and terminal["newAccounts"] == 2 and terminal["referenceAccountsReused"] == 0,
                "Terminal matrix is not two newly executed arms")
        for key, value in hashes.items(): require(terminal[key] == value, "Terminal ancestry differs")
        same(terminal["rows"], [r for r in rows if (r["period"], r["board"]) == (period, board)], "Complete terminal artifact descriptors")
        group_start = read(folder / "group-run-start.json")
        require(group_start["planSha256"] == hashes["planSha256"] and group_start["runStartSha256"] == hashes["runStartSha256"] and
                stamp(start["startedAt"]) <= stamp(group_start["startedAt"]) <= stamp(terminal["completedAt"]) <=
                stamp(comparison["completedAt"]), "Exclusive group start/finish chronology differs")
    atlas = read(bound(plan["atlas"]))
    counts = {"accounts": 0, "newAccounts": 0, "reusedAccounts": 0, "sourceGroups": 6,
        "sourceOpportunityCount": inputs["counts"]["sourceOpportunityCount"],
        "currentCandidateCount": inputs["counts"]["currentCandidateCount"],
        "selectedFormationCounts": inputs["counts"]["selectedFormationCounts"],
        "commonStatusCounts": inputs["counts"]["commonStatusCounts"],
        "preparedSchedules": 12, "accountScheduleBindings": 12, "newFills": 0, "heldCloseChecks": 0, "summaryIntervals": 0}
    reports, dependency_pins, data, data_key = [], dict(DEPENDENCIES), None, None
    for row in rows:
        period, board, exit_name, policy = (row[k] for k in ("period", "board", "exit", "policy"))
        group = next(g for g in groups if (g["period"], g["board"]) == (period, board))
        folder = directory / period / board / exit_name
        require(set(row) == {"period", "board", "exit", "policy", "candidate", "schedule", "result", "checkpoint", "summary", "reusedAccount"}
                and row["reusedAccount"] is False and row["candidate"] == policy, "Every account is new with complete descriptor")
        same(row["schedule"], group["schedules"][policy], "Account consumes exact audited schedule")
        for field, suffix in (("result", ".result.json.gz"), ("checkpoint", ".checkpoint.json.gz"), ("summary", ".summary.json")):
            require(bound(row[field]) == folder / (policy + suffix), "Exact new account artifact path")
        if data_key != (period, board):
            if data is not None: del data; gc.collect()
            data = read(bound(group["accountData"])); data_key = (period, board)
            require(digest(data) == group["accountDatasetDigest"], "Account raw source changed")
        result, checkpoint, summary, schedule = [read(bound(row[k])) for k in ("result", "checkpoint", "summary", "schedule")]
        require(summary["policyId"] == policy and summary["policyLabel"] == policy, "Policy summary identity differs")
        report, more_pins = account_reference(result, checkpoint, summary, schedule,
            plan["periods"][period]["policyConfigs"][exit_name], data, period, exit_name, atlas)
        dependency_pins.update(more_pins)
        counts["accounts"] += 1; counts["newAccounts"] += 1
        counts["newFills"] += len(result["trades"])
        counts["heldCloseChecks"] += report["dailyRisk"]["heldDays"]
        counts["summaryIntervals"] += len(summary["periods"])
        reports.append({"period": period, "board": board, "exit": exit_name, "policy": policy, "artifactRefs": row, "checks": report})
        require(digest(data) == group["accountDatasetDigest"], "Account audit mutated source")
        print(json.dumps({"checkedAccount": [period, board, exit_name, policy], "passed": True}), flush=True)
        del result, checkpoint, summary, schedule
        gc.collect()
    require((counts["accounts"], counts["newAccounts"], counts["reusedAccounts"]) == (12, 12, 0), "Actual audited account totals differ")
    registered_plan(directory)
    return P.write_proof(directory, "validation.json", {"passed": True, "verifiedAt": datetime.now(timezone.utc).isoformat(),
        **{k: v for k, v in hashes.items() if k != "runStartSha256"}, "comparisonSha256": sha(directory / "comparison.json"),
        "verifierSha256": sha(__file__), "sourcePins": dependency_pins, "inputPins": plan["inputPins"],
        "runStart": ref(directory / "run-start.json"), "counts": counts, "accounts": reports, "accountArtifacts": rows, **sources,
        "checks": ["All12 schedules are independently verified before any account",
            "All12 complete fee ledgers, cash/share paths, closed/open episodes, valuation and summary intervals independently reconciled",
            "Every held close independently checks5percent initial and10percent trailing exits including missing/forced-exit precedence",
            "Next-source-session buys, board lots, price bands and prior-volume constraints audited without executing an account",
            "Historical typed integer5 fee identity preserved; no reused account or inferred additive policy return"],
        "holdoutClaim": False, "certified": False, "targetAchieved": False})



def self_test():
    path = ROOT / "tests/test_volume_lead_breakout_verifier.py"
    spec = importlib.util.spec_from_file_location("_volume_lead_synthetic", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stderr).run(unittest.defaultTestLoader.loadTestsFromModule(module))
    require(result.wasSuccessful(), "Independent synthetic checks failed")
    report = {"passed": True, "syntheticGroups": result.testsRun, "realSourceReads": 0, "accountExecutions": 0,
              "verifierSha256": sha(__file__), "testsSha256": sha(path)}
    print(json.dumps(report), flush=True)
    return report


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("inputs", "accounts"), default="accounts")
    parser.add_argument("--inputs", "--inputs-only", dest="inputs", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT)
    args = parser.parse_args()
    if args.self_test: return self_test()
    directory = args.out.resolve()
    mode = "inputs" if args.inputs or args.mode == "inputs" else "accounts"
    try:
        return (verify_inputs if mode == "inputs" else verify_accounts)(directory)
    except Exception as error:
        if directory.exists():
            attempts = directory / "validation-attempts"
            attempts.mkdir(exist_ok=True)
            with (attempts / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json")).open("x", encoding="utf-8") as handle:
                json.dump({"passed": False, "at": datetime.now(timezone.utc).isoformat(), "mode": mode,
                    "verifierSha256": sha(__file__), "errorType": type(error).__name__, "message": str(error),
                    "traceback": traceback.format_exc()}, handle, ensure_ascii=False, indent=2)
        raise


if __name__ == "__main__":
    main()
