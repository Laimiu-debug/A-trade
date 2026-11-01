"""Register human steering after audited batch55; preserve all prior trials."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "artifacts/strategy-search/reclaim-entry-volatility-cap-v1"
OUT = ROOT / "artifacts/strategy-search/market-sector-stock-system-v1"
TRACKER = ROOT / "artifacts/strategy-search/goal-continuation-20261018.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(path):
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}


def write(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main():
    tracker, proof, result, plan = [read(p) for p in (TRACKER, OLD / "validation.json", OLD / "readout.json", OLD / "plan.json")]
    if not proof["passed"] or proof["readout"] != ref(OLD / "readout.json") or result["newAccounts"] != 6 or result["referenceAccountsReused"] != 6:
        raise ValueError("Completed55 account proof binding required")
    if tracker["completedFixedCandidateTrialsTotalIncludingPrior"] != 54 or tracker["newAccountsThisContinuation"] != 51:
        raise ValueError("Unexpected prior tracker totals; refuse duplicate increment")
    spec = importlib.util.spec_from_file_location("_system_handoff_utils", ROOT / "scripts/run-liquid-trend-breakout.py")
    util = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(util)
    if util.workspace_snapshot() != plan["workspaceJsonBefore"]:
        raise ValueError("Protected user JSON changed")
    prior = ROOT / tracker["previous49BatchRegistry"]["path"]
    if sha(prior) != tracker["previous49BatchRegistry"]["sha256"]:
        raise ValueError("Historical49 trial registry changed")
    write(OUT / "continuation-before-system-direction.json", deepcopy(tracker))
    intake = read(OUT / "source-intake-v1/readout.json")
    history = read(OUT / "windows-trust-probe-v1/inspection.json")
    eligible = [r for r in intake["localSectorIndexHistory"] if re.fullmatch(r"sh(?:880[2-9][0-9]{2}|881[0-9]{3})", r["symbol"])]
    preflight = {"schema": "market-sector-stock-system-preflight-v1", "completedAt": datetime.now(timezone.utc).isoformat(),
        "humanSteering": "Build an integrated strong-market, strong-industry/theme, strong-stock and rotation method rather than optimizing isolated indicators.",
        "framework": ref(ROOT / "docs/market-sector-stock-system-v1.md"), "direction": ref(ROOT / "docs/market-sector-stock-system-v1.json"),
        "archivedComponent": {"readout": ref(OLD / "readout.json"), "validation": ref(OLD / "validation.json"), "presentation": ref(OLD / "presentation.json"),
            "newAccounts": 6, "reusedControls": 6, "proposedScreenPasses": sum(r["rowRole"] == "newAccount" and r["proposedNumericScreenPassed"] for r in result["rows"])},
        "sourceIntake": ref(OUT / "source-intake-v1/readout.json"), "verifiedWindowsTlsRecovery": ref(OUT / "windows-trust-probe-v1/receipt.json"),
        "industryHistoryInspection": ref(OUT / "windows-trust-probe-v1/inspection.json"),
        "sourceStatus": {"allLocal88IndexFiles": len(intake["localSectorIndexHistory"]), "withinExistingSectorIndexScope": len(eligible),
            "scopeIsNotACommonTaxonomyProof": True, "officialHistoryRows": history["rows"], "historyMembershipReplayPermitted": False,
            "dateSemanticsClassificationVersionAndIndexCrosswalkStillRequired": True},
        "numericSystemParametersFrozen": False, "completeSystemMarketAccounts": 0, "completeSystemValidated": False, "targetAchieved": False,
        "userJsonFilesUnchanged": True, "newMarketAccountsThisPreflight": 0, "newStrategyTrialsThisPreflight": 0}
    write(OUT / "preflight.json", preflight)
    tracker.update(updatedAt=datetime.now(timezone.utc).isoformat(), completedFixedCandidateTrialsThisContinuation=6,
        completedFixedCandidateTrialsTotalIncludingPrior=55, newAccountsThisContinuation=57,
        targetAchieved=False, goalStatus="active", userJsonFilesUnchanged=True)
    tracker["latestStudy"] = {"directory": OLD.relative_to(ROOT).as_posix(), "readout": ref(OLD / "readout.json"),
        "validation": ref(OLD / "validation.json"), "presentation": ref(OLD / "presentation.json"),
        "conclusion": "All6 new cap accounts fail the proposed multi-metric screen; some mean losses shrink but winners/opportunity and excess returns deteriorate. One component experiment, not an integrated sector-rotation system."}
    tracker["activeStudy"] = {"status": "component experiment completed; all board and audit sessions terminal exit0",
        "directory": OLD.relative_to(ROOT).as_posix(), "batch": 55, "newAccounts": 6, "reusedControls": 6, "newStrategyTrials": 1,
        "preAccountTestsPassed": 78, "independentInputRiskOutcomeIdentityChecksPassed": True, "goalAchieved": False,
        "terminatedSessions": {"prepareHandleClosedReceiptPassed": 25630, "main": 39335, "chinext": 40525, "star": 68863, "finalize": 1894}}
    tracker["archivedEntryComponentNextStage"] = deepcopy(tracker["nextStage"])
    tracker["nextStage"] = {"status": "integrated method framework saved; sector history intake complete; timestamp and taxonomy validation next",
        "contract": ref(ROOT / "docs/market-sector-stock-system-v1.json"), "framework": ref(ROOT / "docs/market-sector-stock-system-v1.md"),
        "preflight": ref(OUT / "preflight.json"),
        "priority": "Validate dated membership and a consistent industry/index taxonomy, then freeze and execute one complete market-sector-stock-rotation-risk system with layer-removal controls. No further isolated threshold sweep.",
        "completeSystemMarketAccounts": 0, "completeSystemValidated": False, "corporateActionEngineeringDeferredByHuman": True}
    tracker["latestHumanScope"]["researchUnit"] = "Whole market-sector-stock-rotation portfolio system"
    tracker["latestHumanScope"]["sectorMeaning"] = "Industries and themes, not exchange listing boards"
    tracker["latestHumanSteering"]["integratedSystemInstruction"] = preflight["humanSteering"]
    tracker["latestHumanSteering"]["singleIndicatorAsSuccessCriterionRejected"] = True
    tracker["lastGoalTurnClassification"] = "progress: closed6 pre-registered cap accounts with6 unchanged controls and independent audit; saved integrated-system framework under new human steering; archived official history and local sector-index inventory. Complete-system parameters/accounts not yet validated. Goal remains active."
    TRACKER.write_text(json.dumps(tracker, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"completedTrials": 55, "newAccountsContinuation": 57, "goalStatus": "active", "frameworkRegistered": True,
        "completeSystemAccounts": 0, "protectedJsonUnchanged": True, "preflight": ref(OUT / "preflight.json")}))


if __name__ == "__main__":
    main()
