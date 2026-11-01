"""Independent common-A queue, historical qualification and cash-account audit.

Only previously audited full daily A candidates are transformed.  No new
production builder or runner is imported and no account is executed here.
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
from pathlib import Path
import sys
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "artifacts/strategy-search/leader-persistence-admission-v1"
FAMILY = "leader-persistence-admission-v1"
SID = "leaderPersistenceAdmissionV1"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
EXITS = ("trail10",)
POLICIES = ("knownAll", "persistent", "nonpersistent")
STATES = ("passed", "failed", "unknown")
ACCOUNT_HELPER = "scripts/verify-full-gap-retention.py"
ACCOUNT_HELPER_SHA = "394729cbb46bd71d7920d0ba30c361e6153204e7c522856ed281d35d3bf3833e"


def load_account_reference():
    path = ROOT / ACCOUNT_HELPER
    with path.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != ACCOUNT_HELPER_SHA:
            raise AssertionError("Frozen account oracle changed")
    spec = importlib.util.spec_from_file_location("_persistence_independent_account_math", path)
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
FIELDS = ("meanStrengthPercentile20", "minimumStrengthPercentile20", "relativeReturn60Percentile",
          "closePathMaxDrawdown20", "signedPathEfficiency20")
BASIS = {
    "protocol": "docs/leader-persistence-admission-v1.json",
    "input": "All audited parent daily leaderCandidates, before the parent F filter; parent evidence is retained unchanged",
    "symbols": "Runner-provided sorted identities from the same bound account dataset; not the union of observed candidates",
    "commonOrder": ["meanStrengthPercentile20 descending", "relativeReturn60Percentile descending", "symbol ascending"],
    "commonTop5": "Take first5 A candidates before inspecting F status; unknown and excluded ranks are never backfilled",
    "historicalClock": "F equals A minus10 exact source-calendar slots; F does not require breakout",
    "knownAll": "Include commonTop5 with F passed or failed; exclude unknown from every policy",
    "persistent": "Include commonTop5 with F passed",
    "nonpersistent": "Include commonTop5 with F failed",
    "identity": "New opportunity ID identifies the A candidate, not a parent event or follower; all policies share it",
    "signalClock": "SignalDate=A close; inherited fixed-account next-session one-attempt execution applies externally",
    "scope": "Fixed board-cohort development comparison; no raw feature recomputation, peer requirement, market-date gate or account-state selection",
}


def account_reference(result, checkpoint, summary, schedule, config, dataset, period, exit_name, atlas):
    require(exit_name == "trail10", "Only the fixed single exit is registered")
    G.SID = SID
    return G.account_reference(result, checkpoint, summary, schedule, config, dataset, period, exit_name, atlas)


def masks_reference(state):
    require(state in STATES, "Unknown F state enum")
    if state == "unknown":
        return {p: {"status": "unknown", "reason": "formationEvidenceUnknown"} for p in POLICIES}
    persistence_reason = "formationPersistent" if state == "passed" else "formationNotPersistent"
    return {"knownAll": {"status": "included", "reason": "formationEvidenceKnown"},
            "persistent": {"status": "included" if state == "passed" else "excluded", "reason": persistence_reason},
            "nonpersistent": {"status": "included" if state == "failed" else "excluded", "reason": persistence_reason}}


def candidate_queue_reference(candidates):
    require(isinstance(candidates, list), "Full original A-candidate list required")
    require(len({c["symbol"] for c in candidates}) == len(candidates), "Duplicate A-candidate symbol")
    for candidate in candidates:
        state = candidate["currentEvidence"]
        require(state["status"] == "passed" and state["breakout"] is True, "A candidate must satisfy current conditions and breakout")
        values = state["values"]
        require(all(finite(values[k]) for k in ("meanStrengthPercentile20", "relativeReturn60Percentile")), "Known finite A ranking values required")
        require(candidate["formationStatus"] in STATES, "Original F status required")
    return sorted(candidates, key=lambda c: (-c["currentEvidence"]["values"]["meanStrengthPercentile20"],
                                          -c["currentEvidence"]["values"]["relativeReturn60Percentile"], c["symbol"]))


def totals_reference(candidates, opportunities):
    state_counts = Counter(r["formationStatus"] for r in opportunities)
    policy_counts = {p: Counter(r["policyAdmission"][p]["status"] for r in opportunities) for p in POLICIES}
    return {"currentCandidateCount": len(candidates), "top5OpportunityCount": len(opportunities),
            "rankExcludedCount": len(candidates) - len(opportunities),
            "selectedFormationCounts": {s: state_counts[s] for s in STATES},
            "policyCounts": {p: {s: policy_counts[p][s] for s in ("included", "excluded", "unknown")} for p in POLICIES},
            "signals": {p: policy_counts[p]["included"] for p in POLICIES},
            "notScheduled": {p: len(opportunities) - policy_counts[p]["included"] for p in POLICIES}}


def evidence_reference(evidence, day):
    require(isinstance(evidence, dict) and evidence.get("date") == day and evidence.get("status") in STATES, "Evidence date/state mismatch")
    values, predicates, reasons = (evidence.get(k) for k in ("values", "predicates", "reasons"))
    require(isinstance(values, dict) and set(values) == set(FIELDS) and isinstance(predicates, dict) and
            set(predicates) == set(FIELDS) and isinstance(reasons, list), "Evidence field/reason domain mismatch")
    for field, threshold, upper in zip(FIELDS, (.90, .80, .90, .10, .35), (False, False, False, True, False)):
        value = values[field]
        require(value is None or finite(value), "Nonfinite parent condition")
        expected = None if value is None else value <= threshold if upper else value >= threshold
        require(type(predicates[field]) is type(expected) and predicates[field] == expected, "Saved predicate arithmetic differs")
    if evidence["status"] == "unknown":
        require(bool(reasons), "Unknown evidence lacks reasons")
    else:
        require(not reasons and all(v is not None for v in values.values()) and evidence["status"] ==
                ("passed" if all(predicates.values()) else "failed"), "Known evidence status disagrees with predicates")


def parent_day_reference(row, calendar, index, symbols):
    day = row["formationDate"]
    f = calendar[index[day] - 10] if index[day] >= 10 else None
    require(row.get("peerFormationDate") == f, "F is not exact A-10")
    candidates = row.get("leaderCandidates")
    require(isinstance(candidates, list), "All original A candidates required")
    names = [r["symbol"] for r in candidates]
    require(names == sorted(set(names)) and set(names) <= symbols, "Candidate order/identity differs from fixed cohort")
    for field, domain in (("activationCounts", ("candidate", "notTriggered", "unknown")), ("formationCounts", STATES)):
        require(set(row[field]) == set(domain) and all(type(v) is int and v >= 0 for v in row[field].values()), "Counter domain invalid")
    require(sum(row["activationCounts"].values()) == len(symbols) and row["activationCounts"]["candidate"] == len(candidates), "All-symbol and A-candidate populations differ")
    ordered = candidate_queue_reference(candidates)
    for c in candidates:
        require(c["formationDate"] == day and c["peerFormationDate"] == f, "Candidate timing differs")
        current = c["currentEvidence"]
        evidence_reference(current, day)
        require(finite(current.get("close")) and finite(current.get("prior5High")) and current["close"] > current["prior5High"] > 0, "A breakout strict inequality differs")
        prior = c["formationEvidence"]
        if prior is None:
            status, reason = "unknown", "formationStateUnavailable"
        else:
            require(f is not None, "Evidence exists before calendar")
            evidence_reference(prior, f)
            status = prior["status"]
            reason = {"passed": "formationFiveConditionsPassed", "failed": "formationFiveConditionsFailed", "unknown": "formationEvidenceUnknown"}[status]
        require((c["formationStatus"], c["formationReason"]) == (status, reason), "Saved F classification differs")
    state_counts = Counter(c["formationStatus"] for c in candidates)
    same(row["formationCounts"], {s: state_counts[s] for s in STATES}, "Original conditional F counts")
    old_ranked = [c for c in ordered if c["formationStatus"] == "passed"]
    old_rank = {c["symbol"]: rank for rank, c in enumerate(old_ranked, 1)}
    for c in candidates:
        rank = old_rank.get(c["symbol"])
        require(c["originalLeaderRank"] == rank and type(c["selectedTop5"]) is bool and c["selectedTop5"] ==
                (rank is not None and rank <= 5), "Old rank provenance changed")
    same(row["leaderRanking"], [{"symbol": c["symbol"], "originalLeaderRank": rank, "selectedTop5": rank <= 5} for rank, c in enumerate(old_ranked, 1)], "Old F-first ranking")
    same(row["leaderEventIds"], [f"leaderLagFollowersV1:{day}:{c['symbol']}" for c in old_ranked[:5]], "Original parent events")
    for name, value in (("currentCandidateCount", len(candidates)), ("temporalCandidateCount", len(old_ranked)), ("leaderEventCount", min(5, len(old_ranked)))):
        require(type(row["counts"][name]) is int and row["counts"][name] == value, "Parent daily count mismatch")
    return ordered


def reference_build(parent_metadata, parent_daily_rows, *, start, end, progress=None):
    require(start <= end and parent_metadata.get("strategyId") == "leaderLagFollowersV1", "Wrong parent strategy/range")
    calendar, parent_targets = parent_metadata["calendar"], parent_metadata["targetDates"]
    G.calendar_reference(calendar); G.calendar_reference(parent_targets)
    require(parent_targets == [d for d in calendar if parent_targets[0] <= d <= parent_targets[-1]], "Parent target sessions incomplete")
    symbols = parent_metadata.get("symbols")
    require(isinstance(symbols, list) and bool(symbols) and all(isinstance(s, str) and s.strip() for s in symbols)
            and symbols == sorted(set(symbols)) and type(parent_metadata["counts"]["sampleCount"]) is int and
            parent_metadata["counts"]["sampleCount"] == len(symbols), "Full frozen cohort identities required")
    require(isinstance(parent_daily_rows, list) and [r.get("formationDate") for r in parent_daily_rows] == parent_targets,
            "All original daily dates, including no-event dates, required")
    retained_calendar = [d for d in calendar if d <= end]
    targets = [d for d in parent_targets if start <= d <= end]
    require(bool(targets) and targets == [d for d in retained_calendar if start <= d <= end], "Requested interval not full source sessions")
    index, symbol_set = {day: i for i, day in enumerate(retained_calendar)}, set(symbols)
    daily, opportunities, all_candidates = [], [], []
    for row in parent_daily_rows:
        day = row["formationDate"]
        if day not in index or not start <= day <= end: continue
        ordered = parent_day_reference(row, retained_calendar, index, symbol_set)
        ranked = [{"symbol": c["symbol"], "originalRank": i, "selectedTop5": i <= 5,
                   "formationStatus": c["formationStatus"], "parentCandidate": deepcopy(c)} for i, c in enumerate(ordered, 1)]
        original_digest = digest(row)
        today = []
        for rank, candidate in enumerate(ordered[:5], 1):
            identity = f"{SID}:{day}:{candidate['symbol']}"
            signal = {"signalId": identity, "strategyId": SID, "symbol": candidate["symbol"], "signalDate": day, "originalRank": rank}
            masks = masks_reference(candidate["formationStatus"])
            today.append({**signal, "formationId": identity, "formationDate": day, "peerFormationDate": row["peerFormationDate"],
                "formationStatus": candidate["formationStatus"], "parentDailySha256": original_digest,
                "parentCandidate": deepcopy(candidate), "policyAdmission": masks,
                "signals": {p: deepcopy(signal) if masks[p]["status"] == "included" else None for p in POLICIES}})
        daily.append({"formationDate": day, "peerFormationDate": row["peerFormationDate"], "parentDailySha256": original_digest,
            "parentActivationCounts": deepcopy(row["activationCounts"]), "parentFormationCounts": deepcopy(row["formationCounts"]),
            "currentCandidates": ranked, "commonRanking": [{k: r[k] for k in ("symbol", "originalRank", "selectedTop5", "formationStatus")} for r in ranked],
            "selectedSignalIds": [r["signalId"] for r in today], "counts": totals_reference(ordered, today)})
        opportunities.extend(today); all_candidates.extend(ordered)
        if progress: progress({"verifiedDate": day, "opportunities": len(opportunities)})
    counts = {"sampleCount": len(symbols), "targetDayCount": len(targets), **totals_reference(all_candidates, opportunities)}
    metadata = {"version": FAMILY, "strategyId": SID, "parentStrategyId": "leaderLagFollowersV1", "policies": list(POLICIES),
                "calendar": retained_calendar, "targetDates": targets, "symbols": list(symbols), "start": start, "end": end,
                "counts": deepcopy(counts), "basis": deepcopy(BASIS), "certified": False, "targetAchieved": False}
    schedules = {p: [deepcopy(r["signals"][p]) for r in opportunities if r["signals"][p] is not None] for p in POLICIES}
    return {"metadata": metadata, "dailyRows": daily, "opportunities": opportunities, "schedules": schedules, "counts": counts}


def require_pin(plan, item):
    path = bound(item)
    require(plan["inputPins"].get(item["path"]) == item["sha256"], "Reference not frozen as input: " + item["path"])
    return path


def pretests_reference(directory, plan, completion):
    tests = read(directory / "pre-account-tests.json")
    require(tests["passed"] is True and tests["planSha256"] == sha(directory / "plan.json") and
            tests["sourcePins"] == plan["sourcePins"] and tests["allChecksBeforeFirstNewAccount"] is True,
            "Passing pre-account tests must bind all frozen sources")
    names = ("test_research_leader_persistence_admission.py", "test_leader_persistence_admission_verifier.py",
             "test_leader_persistence_admission_readout.py", "test_research_fixed_signal_account.py",
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
            plan["policies"] == list(POLICIES) and plan["exits"] == list(EXITS) and list(plan["periods"]) == list(PERIODS), "Frozen experiment matrix differs")
    trials = {"newSetupMechanisms": 1, "newEntryPolicies": 3, "newAccounts": 18, "referenceAccountsReused": 0,
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
        "tests/test_leader_persistence_admission_verifier.py": sha(ROOT / "tests/test_leader_persistence_admission_verifier.py"),
        "service/research_leader_lag.py": "1c6168e7b56af0bff90dcda665ab88e9d4756f5da1b82d8b7b76b1e14bf1fc9e"}
    for path, expected in expected_sources.items():
        require(plan["sourcePins"].get(path) == expected and sha(ROOT / path) == expected, "Unfrozen independent/parent dependency: " + path)
    require(plan["sourcePins"].get(plan["protocol"]["path"]) == plan["protocol"]["sha256"], "Protocol not source-pinned")
    require(plan["previousProgram"]["sha256"] == "499157c26e4d641f121652a2a788b4508bff72555164ebd70828a0fc7d07029b" and
            sha(directory / "program-before.json") == plan["previousProgram"]["sha256"], "Pre-experiment registry snapshot differs")
    require(plan["previousDelivery"]["sha256"] == "523b10adaf72c04b29608d97a96a7a79c72ba639a86f4ac3ded59d3353a69dfc", "Predecessor differs")
    require(plan["sourcePlan"]["sha256"] == "a911df3c26aa6e662ce8c62d6374e856427c311200b2dc99bcf409c2725ffefa", "Original fixed source plan differs")
    require_pin(plan, plan["sourcePlan"])
    require(plan["accountContract"] == P.FEE_BINDING, "Frozen dated fee contract differs")
    fee = read(require_pin(plan, plan["accountContract"]))["feePolicy"]
    same(plan["feePolicy"], fee, "Unchanged fee policy")
    require(type(plan["feePolicy"]["minCommission"]) is int and plan["feePolicy"]["minCommission"] == 5, "Integer5 fee identity must remain")
    parent = read(require_pin(plan, plan["parentAccountPlan"]))
    same(plan["sourceData"], parent["sourceData"], "Account source identity")
    same(plan["atlas"], parent["atlas"], "Existing complete descriptive intervals")
    def rename(value):
        if isinstance(value, dict): return {k: rename(v) for k, v in value.items()}
        if isinstance(value, list): return [rename(v) for v in value]
        return SID if value == "leaderAmountHandoffV1" else deepcopy(value)
    for period in PERIODS:
        expected = {k: parent["periods"][period][k] for k in ("startDate", "endDate")}
        expected["policyConfigs"] = {"trail10": rename(parent["periods"][period]["policyConfigs"]["trail10"])}
        same(plan["periods"][period], expected, "Only SID and single-exit projection change")
        config = expected["policyConfigs"]["trail10"]
        require(config["initialCapital"] == 500000 and config["maxPositions"] == 5 and config["stopLossPct"] == .05 and
                config["trailingStopPct"] == .1 and config["takeProfitPct"] == config["maxHoldingDays"] == 0 and
                config["slippageBps"] == 10 and config["allocation"]["strategyIds"] == [SID], "Fixed account settings differ")
    return plan


def source_bindings_reference(plan):
    answer = G.source_bindings_reference(plan)
    keys = ("previousPlan", "previousValidation", "previousReadoutValidation", "previousDelivery",
            "lagPlan", "lagInputCompletion", "lagInputValidation", "lagValidation",
            "parentAccountPlan", "parentAccountValidation", "parentAccountDelivery", "parentAccountComparison")
    docs = {key: read(require_pin(plan, plan[key])) for key in keys}
    pins = {plan[key]["path"]: plan[key]["sha256"] for key in keys}
    previous, proof, readout_proof, delivery = (docs[k] for k in keys[:4])
    require(delivery["candidateTrialsAfter"] == 47 and proof["passed"] is True and readout_proof["passed"] is True and
            proof["planSha256"] == plan["previousPlan"]["sha256"] == readout_proof["planSha256"] and
            readout_proof["sourceValidationSha256"] == plan["previousValidation"]["sha256"], "Completed47 proof lineage differs")
    require(proof["verifierSha256"] == previous["sourcePins"]["scripts/verify-leader-stage-coverage.py"], "47 original verifier identity differs")
    inventory = {r["path"]: r["sha256"] for r in delivery["files"]}
    require(len(inventory) == len(delivery["files"]), "Duplicate prior sealed member")
    for key in ("previousPlan", "previousValidation", "previousReadoutValidation"):
        require(inventory.get(plan[key]["path"]) == plan[key]["sha256"], "47 receipt not inside final delivery")
    previous_dir = bound(plan["previousPlan"]).parent
    for name, expected in (("completion.json", proof["completionSha256"]), ("readout.json", readout_proof["readoutSha256"])):
        path = previous_dir / name
        require(sha(path) == expected == inventory.get(path.relative_to(ROOT).as_posix()), "47 completed artifact not sealed")
        pins[path.relative_to(ROOT).as_posix()] = expected
    lp, lc, lv, la = (docs[k] for k in ("lagPlan", "lagInputCompletion", "lagInputValidation", "lagValidation"))
    require(lv["passed"] is True and la["passed"] is True and lv["planSha256"] == la["planSha256"] == lc["planSha256"] == plan["lagPlan"]["sha256"] and
            lv["inputCompletionSha256"] == la["inputCompletionSha256"] == plan["lagInputCompletion"]["sha256"] and
            la["inputValidationSha256"] == plan["lagInputValidation"]["sha256"], "34 full daily source audit lineage differs")
    for key in ("lagPlan", "lagInputCompletion", "lagInputValidation"):
        require(previous["inputPins"].get(plan[key]["path"]) == plan[key]["sha256"], "47 did not bind the same34 source proof")
    account_parent, account_proof, account_delivery, comparison = (docs[k] for k in
        ("parentAccountPlan", "parentAccountValidation", "parentAccountDelivery", "parentAccountComparison"))
    require(account_delivery["candidateTrialsAfter"] == 43 and account_proof["passed"] is True and
            account_proof["planSha256"] == plan["parentAccountPlan"]["sha256"] and
            account_proof["comparisonSha256"] == plan["parentAccountComparison"]["sha256"], "43 execution contract not audited")
    same(account_proof["accountArtifacts"], comparison["rows"], "Original executable account artifacts")
    account_inventory = {r["path"]: r["sha256"] for r in account_delivery["files"]}
    for key in ("parentAccountPlan", "parentAccountValidation", "parentAccountComparison"):
        require(account_inventory.get(plan[key]["path"]) == plan[key]["sha256"], "43 execution proof not sealed")
    source_groups = {(g["period"], g["board"]): g for g in previous["groups"]}
    audited_groups = {(g["period"], g["board"]): g for g in proof["sourceGroups"]}
    require(len(source_groups) == len(previous["groups"]) == len(audited_groups) == len(proof["sourceGroups"]) == 6, "Six audited47 sources required")
    require(list(plan["parentData"]) == list(PERIODS), "Two parent periods required")
    for period in PERIODS:
        require(list(plan["parentData"][period]) == list(BOARDS), "Three original board sources required")
        for board in BOARDS:
            inherited, source = plan["parentData"][period][board], plan["sourceData"][period][board]
            old = source_groups[(period, board)]
            same(inherited, {"metadata": old["lagMetadata"], "daily": old["lagDaily"], "accountData": old["accountData"]}, "Exact47 source view")
            same(source["accountData"], inherited["accountData"], "Account and signal cohorts coincide")
            receipt = read(bound(lc["outputs"][period][board]))
            require(receipt["planSha256"] == plan["lagPlan"]["sha256"], "Original34 per-source receipt plan")
            same(receipt["accountData"], source["accountData"], "34 full raw source identity")
            same(lp["sourceData"][period][board]["accountData"], source["accountData"], "34 fixed source plan")
            for name, file in (("metadata", "signal-metadata.json"), ("daily", "daily-evidence.jsonl.gz")):
                item = inherited[name]
                same(item, receipt["files"][file], "Original34 complete artifact")
                same(item, lv["inputArtifacts"][period][board][file], "34 independently audited artifact")
                same(item, la["inputArtifacts"][period][board][file], "34 account-bound signal artifact")
                same(item, audited_groups[(period, board)]["sourceBindings"]["lagMetadata" if name == "metadata" else "lagDaily"], "47 source audit artifact")
                require_pin(plan, item); pins[item["path"]] = item["sha256"]
            require(lv["datasetPins"].get(source["accountData"]["path"]) == source["accountData"]["sha256"], "34 audited dataset pin differs")
    answer.update(parentEvidencePins=pins, parentInputValidationSha256=plan["lagInputValidation"]["sha256"],
                  previousValidationSha256=plan["previousValidation"]["sha256"])
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
        folder = directory / period / board
        source, parent = plan["sourceData"][period][board], plan["parentData"][period][board]
        require(group["accountData"] == source["accountData"] == parent["accountData"] and
                group["accountDatasetDigest"] == source["accountDatasetDigest"], "Original cache input changed")
        same(group["parentMetadata"], parent["metadata"], "Parent metadata ref")
        same(group["parentDaily"], parent["daily"], "Parent complete daily ref")
        for key, name in (("metadata", "metadata.json"), ("daily", "daily-evidence.jsonl.gz"), ("opportunities", "opportunities.jsonl.gz"), ("inputReceipt", "input-completion.json")):
            require(bound(group[key]) == folder / name, "Prepared artifact path changed")
        require(list(group["schedules"]) == list(POLICIES), "All three schedule arms required")
        for policy in POLICIES:
            require(bound(group["schedules"][policy]) == folder / (policy + ".signals.json"), "Policy schedule path changed")
        receipt = read(bound(group["inputReceipt"]))
        require(receipt["planSha256"] == sha(directory / "plan.json"), "Per-source receipt plan changed")
        same({k: v for k, v in receipt.items() if k not in ("planSha256", "completedAt")},
             {k: v for k, v in group.items() if k != "inputReceipt"}, "Exact prepared per-source closure")
        require(datetime.fromisoformat(plan["registeredAt"]) <= datetime.fromisoformat(receipt["completedAt"]) <=
                datetime.fromisoformat(completion["completedAt"]), "Preparation chronology changed")
    expected = {"sourceGroups": 6, "preparedSchedules": 18, "accountScheduleBindings": 18,
                "opportunities": sum(g["counts"]["top5OpportunityCount"] for g in groups),
                "signals": {p: sum(g["counts"]["signals"][p] for g in groups) for p in POLICIES}}
    same(completion["counts"], expected, "Complete input counters")
    return completion, groups


def verify_inputs(directory):
    require(not (directory / "input-validation.json").exists(), "Never overwrite input proof")
    plan = registered_plan(directory)
    sources = source_bindings_reference(plan)
    completion, groups = frozen_groups(directory, plan)
    pretests_reference(directory, plan, completion)
    prepare = read(directory / "prepare-start.json")
    require(prepare["planSha256"] == sha(directory / "plan.json") and prepare["preAccountTestsSha256"] == sha(directory / "pre-account-tests.json"), "Exclusive prepare-start binding")
    tests = read(directory / "pre-account-tests.json")
    require(datetime.fromisoformat(tests["completedAt"]) <= datetime.fromisoformat(prepare["startedAt"]) <= datetime.fromisoformat(completion["completedAt"]), "Tests must precede preparation")
    totals = {"sourceGroups": 0, "sourceDays": 0, "currentCandidateCount": 0, "sourceOpportunityCount": 0,
              "rankExcludedCount": 0, "preparedSchedules": 0, "accountScheduleBindings": 0,
              "selectedFormationCounts": {s: 0 for s in STATES}, "signals": {p: 0 for p in POLICIES}, "accountsExecuted": 0}
    reports = []
    for group in groups:
        period, board = group["period"], group["board"]
        data = read(bound(group["accountData"]))
        require(digest(data) == group["accountDatasetDigest"] and data["period"] == period and data["board"] == board and
                len(data["securities"]) == plan["sourceData"][period][board]["members"], "Bound full raw source differs")
        metadata = read(bound(group["parentMetadata"]))
        require("symbols" not in metadata, "Unexpected modification to original metadata schema")
        metadata["symbols"] = sorted(s["symbol"] for s in data["securities"])
        require(data["calendar"] == metadata["calendar"], "Signal and account calendars differ")
        daily = records(bound(group["parentDaily"]))
        spec = plan["periods"][period]
        expected = reference_build(metadata, daily, start=spec["startDate"], end=spec["endDate"])
        same(read(bound(group["metadata"])), expected["metadata"], "Full input metadata")
        same(records(bound(group["daily"])), expected["dailyRows"], "All A candidates and common ranking")
        same(records(bound(group["opportunities"])), expected["opportunities"], "Every original commonTop5 and three masks")
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
        totals["preparedSchedules"] += 3; totals["accountScheduleBindings"] += 3
        reports.append({"period": period, "board": board, "counts": c, "sourceBinding": group["accountData"],
                        "parentMetadata": group["parentMetadata"], "parentDaily": group["parentDaily"], "sourceOpportunityCount": c["top5OpportunityCount"]})
        require(digest(data) == group["accountDatasetDigest"], "Input audit mutated source")
        print(json.dumps({"checkedSource": [period, board], "counts": c, "passed": True}), flush=True)
        del data, metadata, daily, expected
        gc.collect()
    require((totals["sourceGroups"], totals["preparedSchedules"], totals["accountScheduleBindings"]) == (6, 18, 18) and
            totals["sourceOpportunityCount"] == completion["counts"]["opportunities"], "Complete independently rebuilt input matrix")
    registered_plan(directory)
    return P.write_proof(directory, "input-validation.json", {"passed": True, "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "planSha256": sha(directory / "plan.json"), "inputCompletionSha256": sha(directory / "input-completion.json"),
        "preAccountTestsSha256": sha(directory / "pre-account-tests.json"), "verifierSha256": sha(__file__),
        "sourcePins": DEPENDENCIES, "inputPins": plan["inputPins"], "preparedGroups": groups, "counts": totals,
        "groups": reports, **sources, "checks": ["Exact34 full daily candidate proof inherited through completed47",
            "All A candidates reranked before F masks, with unknown and excluded original slots retained",
            "All18 schedules independently rebuilt before any account; original next-session clock retained"],
        "holdoutClaim": False, "certified": False, "targetAchieved": False})


def check_schedule_partition(expected):
    counts = expected["counts"]
    opportunities = expected["opportunities"]
    require(counts["currentCandidateCount"] == counts["top5OpportunityCount"] + counts["rankExcludedCount"], "All current candidate ranks conserve")
    require(counts["top5OpportunityCount"] == len(opportunities) == sum(counts["selectedFormationCounts"].values()), "All selected F states conserve")
    require(counts["signals"]["knownAll"] == counts["signals"]["persistent"] + counts["signals"]["nonpersistent"], "Known schedule union does not conserve")
    require(counts["signals"]["persistent"] == counts["selectedFormationCounts"]["passed"] and
            counts["signals"]["nonpersistent"] == counts["selectedFormationCounts"]["failed"], "Known F masks differ")
    for policy in POLICIES:
        schedule = expected["schedules"][policy]
        require(len(schedule) + counts["notScheduled"][policy] == len(opportunities) and
                counts["policyCounts"][policy]["unknown"] == counts["selectedFormationCounts"]["unknown"], "Unknown or rejected original slots lost")
        require(schedule == sorted(schedule, key=lambda s: (s["signalDate"], s["originalRank"], s["symbol"], s["signalId"])) and
                len({s["signalId"] for s in schedule}) == len(schedule), "Frozen schedule identity/order differs")
        require(all(set(s) == {"signalId", "strategyId", "symbol", "signalDate", "originalRank"} and
                    s["strategyId"] == SID and s["signalId"] == f"{SID}:{s['signalDate']}:{s['symbol']}" and
                    type(s["originalRank"]) is int and 1 <= s["originalRank"] <= 5 for s in schedule), "Canonical new A-signal identity differs")


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
    require(inputs["counts"]["sourceGroups"] == 6 and inputs["counts"]["preparedSchedules"] == 18 and
            inputs["counts"]["accountScheduleBindings"] == 18 and inputs["counts"]["sourceOpportunityCount"] ==
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
            [(g["period"], g["board"], "trail10", p) for g in groups for p in POLICIES], "All18 account identities/order required")
    require(len(comparison["groupCompletions"]) == 6, "Six terminal source-group receipts required")
    for group, item in zip(groups, comparison["groupCompletions"]):
        period, board = group["period"], group["board"]
        folder = directory / period / board / "trail10"
        require(bound(item) == folder / "group-completion.json", "Terminal path differs")
        terminal = read(bound(item))
        require(tuple(terminal[k] for k in ("period", "board", "exit")) == (period, board, "trail10") and
                terminal["completed"] is True and terminal["newAccounts"] == 3 and terminal["referenceAccountsReused"] == 0,
                "Terminal matrix is not three newly executed arms")
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
        "preparedSchedules": 18, "accountScheduleBindings": 18, "newFills": 0, "heldCloseChecks": 0, "summaryIntervals": 0}
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
    require((counts["accounts"], counts["newAccounts"], counts["reusedAccounts"]) == (18, 18, 0), "Actual audited account totals differ")
    registered_plan(directory)
    return P.write_proof(directory, "validation.json", {"passed": True, "verifiedAt": datetime.now(timezone.utc).isoformat(),
        **{k: v for k, v in hashes.items() if k != "runStartSha256"}, "comparisonSha256": sha(directory / "comparison.json"),
        "verifierSha256": sha(__file__), "sourcePins": dependency_pins, "inputPins": plan["inputPins"],
        "runStart": ref(directory / "run-start.json"), "counts": counts, "accounts": reports, "accountArtifacts": rows, **sources,
        "checks": ["All18 schedules are independently verified before any account",
            "All18 complete fee ledgers, cash/share paths, closed/open episodes, valuation and summary intervals independently reconciled",
            "Every held close independently checks5percent initial and10percent trailing exits including missing/forced-exit precedence",
            "Next-source-session buys, board lots, price bands and prior-volume constraints audited without executing an account",
            "Historical typed integer5 fee identity preserved; no reused account or inferred additive policy return"],
        "holdoutClaim": False, "certified": False, "targetAchieved": False})


def self_test():
    path = ROOT / "tests/test_leader_persistence_admission_verifier.py"
    spec = importlib.util.spec_from_file_location("_persistence_admission_synthetic", path)
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
