"""Actual synthetic accounts checked by the separately implemented daily audit.

Only the production unit-test fixtures are used as market inputs. This command
does not import a runner, open a dataset/result artifact, or write evidence files.
Its JSON stdout is supplementary evidence, not additional production unit tests.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import traceback

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CODE_PINS = {
    "service/research_volatility_trail.py": "3c688517a9fa28db6ebce5073e9c68d452cf1d25449d003094185c5e433ebb67",
    "tests/test_research_volatility_trail.py": "a99cc8fbfffd0b2d79bbd7232e4ae58cc6a66f438b5f8d22bfd5d5b30c5ac6c2",
    "scripts/verify-reclaim-volatility-trail.py": "81bd4a8681f31d9a1dc5c5732ed8cb7d763e3c779b58492098bbc3957fd0bb08",
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scenario(name, fixtures, audit):
    data = fixtures.fixture()
    signals = fixtures.schedule(data)
    if name == "thresholdEqualityNextOpen":
        security = data["securities"][0]
        window = audit.volatility_reference(data["calendar"][26], data["calendar"][5:26],
            security["bars"][5:26], {})
        fixtures.set_bar(data, 26, close=10. * (1 - window["scaledDistancePct"]))
        fixtures.set_bar(data, 27, opening=9.6, close=9.6)
    elif name == "unknownWindowCarriedFloor":
        fixtures.set_bar(data, 27, volume=0)
        fixtures.set_bar(data, 28, close=9.69)
    elif name == "initialStopPriority":
        fixtures.set_bar(data, 27, close=9.4)
    elif name == "precloseReferenceConversion":
        data["securities"][0]["precloseByDate"] = {data["calendar"][27]: 5.}
        for index in range(27, len(data["calendar"])):
            fixtures.set_bar(data, index, close=5., opening=5., high=5.025, low=4.975)
    elif name == "blockedPartialRetry":
        fixtures.set_bar(data, 26, close=9.69)
        for index in range(26, 33):
            fixtures.set_bar(data, index, volume=3000)
        fixtures.set_bar(data, 27, volume=0)
    elif name == "reentryResetsFloor":
        fixtures.set_bar(data, 26, close=9.69)
        signals = fixtures.schedule(data, (25, 31))
    elif name == "unknownWithoutPriorFloor":
        fixtures.set_bar(data, 20, volume=0)
        fixtures.set_bar(data, 26, close=9.6)
    elif name == "wideningCannotLoosenFloor":
        fixtures.set_bar(data, 26, close=10.8)
        fixtures.set_bar(data, 27, close=10.7)
    elif name == "missingCurrentBarFreezesFloor":
        data["securities"][0]["bars"].pop(27)
    else:
        require(name == "ordinaryHold", "Unregistered synthetic scenario")
    return data, signals


def assert_scenario(name, result, risk, data):
    detail = result["research"]["volatilityTrail"]
    observations = detail["observations"]
    sells = [r for r in result["trades"] if r["side"] == "sell"]
    day = data["calendar"]
    by_day = {r["date"]: r for r in observations}
    if name == "ordinaryHold":
        require(not sells and risk["terminalRatchetStates"] == 1, "Ordinary fixture must hold through cutoff")
        require(len(observations) > 5 and risk["queuedExits"] == 0, "No fifth-day exit is allowed")
    elif name == "thresholdEqualityNextOpen":
        row = by_day[day[26]]
        require(row["observedClose"] == row["threshold"] and row["decisionStatus"] == "triggered", "Equality must trigger")
        require(len(sells) == 1 and sells[0]["date"] == day[27]
            and sells[0]["signalDate"] == day[26] and sells[0]["price"] == 9.6, "Exit must execute at next open")
    elif name == "unknownWindowCarriedFloor":
        row = by_day[day[28]]
        require(row["window"]["windowStatus"] == "unknown" and row["usedCarriedThreshold"]
            and row["decisionStatus"] == "triggered", "Unknown window must retain an enforceable prior floor")
        require(by_day[day[27]]["decisionStatus"] == "unknown", "Zero current volume must freeze the floor")
    elif name == "initialStopPriority":
        row = by_day[day[27]]
        require(row["decisionStatus"] == "skippedStandardExit" and row["exitReason"] == "stopLoss", "Initial stop must take priority")
        require(risk["exitReasons"] == {"stopLoss": 1}, "Only the first standard risk intent is allowed")
    elif name == "precloseReferenceConversion":
        old, new = by_day[day[26]], by_day[day[27]]
        require(risk["riskReferenceConversions"] == 1, "Fixture must contain one inherited reference conversion")
        require(new["riskEntryPrice"] == old["riskEntryPrice"] / 2
            and new["ratchetRatio"] == old["ratchetRatio"]
            and new["threshold"] == old["threshold"] / 2, "Reference conversion must scale the price floor only")
    elif name == "blockedPartialRetry":
        require(len(sells) > 1 and any(r["positionAction"] == "reduce" for r in sells), "Fixture must exercise partial execution")
        require(sells[0]["date"] > day[27] and risk["observationCounts"]["skippedExistingExit"] > 0, "Blocked first exit must remain pending")
        require(all(r["riskEvidence"] == sells[0]["riskEvidence"] and r["signalDate"] == day[26]
            for r in sells), "Retries must preserve the first immutable risk intent")
        require(risk["queuedExits"] == 1 and risk["terminalRatchetStates"] == 0, "One exit intent must fully close the episode")
    elif name == "reentryResetsFloor":
        first_new = by_day[day[32]]
        require(risk["actualEntries"] == 2 and first_new["signalId"] == "synthetic:31"
            and first_new["previousRatio"] is None, "Reentry must bind the new signal and reset the floor")
    elif name == "unknownWithoutPriorFloor":
        row = by_day[day[26]]
        require(row["decisionStatus"] == "unknown" and row["decisionReason"] == "noKnownThreshold"
            and not row["thresholdKnown"], "Unavailable first window must not manufacture a threshold")
    elif name == "wideningCannotLoosenFloor":
        old, new = by_day[day[26]], by_day[day[27]]
        require(new["window"]["meanTRPct20"] > old["window"]["meanTRPct20"]
            and new["candidateRatio"] < old["ratchetRatio"]
            and new["ratchetRatio"] == old["ratchetRatio"], "Wider volatility cannot loosen the established ratchet")
    elif name == "missingCurrentBarFreezesFloor":
        old, new = by_day[day[26]], by_day[day[27]]
        require(not new["currentPriceValid"] and new["decisionStatus"] == "unknown"
            and new["candidateRatio"] is None and new["ratchetRatio"] == old["ratchetRatio"], "Missing current bar must freeze update and trigger")


def main():
    report = {
        "schema": "reclaim-volatility-trail-synthetic-crosscheck-v1",
        "passed": False, "checks": [],
        "scope": {"inputOrigin": "tests/test_research_volatility_trail.py fixtures only",
            "realSourceRead": False, "realDataFilesRead": 0, "realAccountExecutions": 0,
            "syntheticAccountExecutions": 0, "productionUnitIncrement": 0,
            "runnerImported": False, "filesWritten": 0},
    }
    active = "codePins"
    capture = io.StringIO()
    try:
        with redirect_stdout(capture):
            pins = {path: sha(ROOT / path) for path in CODE_PINS}
            require(pins == CODE_PINS, "Frozen fixture/module/independent-auditor code pin mismatch")
            pins[Path(__file__).resolve().relative_to(ROOT).as_posix()] = sha(Path(__file__))
            report["pins"] = pins
            fixtures = load("tests/test_research_volatility_trail.py", "volatility_trail_crosscheck_fixtures")
            audit = load("scripts/verify-reclaim-volatility-trail.py", "volatility_trail_crosscheck_independent_audit")
            cases = ("ordinaryHold", "thresholdEqualityNextOpen", "unknownWindowCarriedFloor",
                "initialStopPriority", "precloseReferenceConversion", "blockedPartialRetry",
                "reentryResetsFloor", "unknownWithoutPriorFloor", "wideningCannotLoosenFloor",
                "missingCurrentBarFreezesFloor")
            for active in cases:
                data, signals = scenario(active, fixtures, audit)
                original_data, original_signals = deepcopy(data), deepcopy(signals)
                engine = fixtures.account(data, signals)
                report["scope"]["syntheticAccountExecutions"] += 1
                engine.step(20000)
                result, checkpoint = engine.result(), engine.checkpoint()
                require(result["status"] == "completed", "Synthetic account did not finish")
                require(data == original_data and signals == original_signals, "Synthetic input mutation")
                risk = audit.daily_risk_reference(result, checkpoint, data, "volatilityTrail")
                assert_scenario(active, result, risk, data)
                report["checks"].append({"name": active, "passed": True, "dailyRisk": risk,
                    "tradeCount": len(result["trades"]), "closedEpisodeCount": len(result["closedTrades"]),
                    "tradeSha256": audit.digest(result["trades"]),
                    "observationSha256": audit.digest(result["research"]["volatilityTrail"]["observations"])})
            require(not capture.getvalue(), "Unexpected nested stdout; the command must emit only its JSON proof")
            report["passed"] = True
    except Exception as exc:
        report["failure"] = {"scenario": active, "type": type(exc).__name__, "message": str(exc),
            "traceback": traceback.format_exc(), "capturedStdout": capture.getvalue()}
    report["checkCount"] = len(report["checks"])
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
