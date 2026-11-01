"""Seal the completed synthetic test receipt and source/feature checks.

This does not run market accounts or rerun previously completed strategy trials.
The test receipt is the actual terminal result from this goal turn; an optional
future repetition belongs to a new receipt, not an overwrite of this proof.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal as D
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.research_action_basis import normalize_distribution, reference_change, causal_price_window

OUT = ROOT / "artifacts/strategy-search/action-aware-engineering-v1"


def read(path):
    return json.loads(path.read_text("utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(path):
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}


def write(path, data):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main():
    tracker_path = ROOT / "artifacts/strategy-search/goal-continuation-20261018.json"
    tracker = read(tracker_path)
    registry = tracker["previous49BatchRegistry"]
    if sha(ROOT / registry["path"]) != registry["sha256"]:
        raise ValueError("Historical registry changed")
    old_plan_path = ROOT / "artifacts/strategy-search/reclaim-2026-replication-v1/plan.json"
    old_plan = read(old_plan_path)
    preserved = []
    for name, digest in old_plan["sourcePins"].items():
        if sha(ROOT / name) != digest:
            raise ValueError("Historical pinned source changed: " + name)
        preserved.append({"path": name, "sha256": digest})
    workspace = Path(os.environ["APPDATA"]) / "A-trade/workspace"
    before = old_plan["workspaceJsonBefore"]
    current = {p.relative_to(workspace).as_posix(): sha(p) for p in workspace.rglob("*.json")}
    unchanged = current == before
    if not unchanged:
        raise ValueError("Protected workspace changed since frozen study; investigate before claiming unchanged")
    names = ("tests.test_research_action_account", "tests.test_research_action_basis",
             "tests.test_research_fixed_signal_account", "tests.test_research_volatility_trail")
    count = sum(unittest.defaultTestLoader.loadTestsFromName(name).countTestCases() for name in names)
    receipt = read(OUT / "test-run.json")
    if count != receipt["testsRun"] or receipt["exitCode"] != 0 or not receipt["passed"]:
        raise ValueError("Completed test receipt does not match frozen suite")
    audit_path = ROOT / "artifacts/strategy-search/reclaim-corporate-action-audit-v1/readout.json"
    audit = read(audit_path)
    source_path = ROOT / "artifacts/strategy-search/reclaim-2026-replication-v1/star/account-data.json.gz"
    dataset = json.loads(gzip.decompress(source_path.read_bytes()))
    securities = {r["symbol"]: r for r in dataset["securities"]}
    examples, terms = [], []
    for case in audit["cases"]:
        pdf = case["issuerPdf"]
        if sha(ROOT / pdf["path"]) != pdf["sha256"]:
            raise ValueError("Issuer PDF changed")
        event = normalize_distribution({"id": case["symbol"] + ":" + case["exDate"], "symbol": case["symbol"],
            "publicDate": case["publishDate"], "recordDate": case["registrationDate"], "exDate": case["exDate"],
            "shareListingDate": case["newSharesAvailableDate"], "cashPayDate": case["cashPayDate"],
            "cashGrossPerShare": case["actualCashGross"], "additionalSharesPerShare": case["actualShareIncrease"],
            "referenceCashPerShare": case["priceReferenceCash"], "referenceAdditionalSharesPerShare": case["priceReferenceShareIncrease"],
            "sourceSha256": pdf["sha256"], "sourceUrl": case["publicPdf"]})
        bars = [b for b in securities[event["symbol"]]["bars"] if b["date"] in (event["recordDate"], event["exDate"])]
        if len(bars) != 2:
            raise ValueError("Exact record/ex bars missing")
        immutable = deepcopy(bars)
        before_view = causal_price_window(bars, [event], as_of=event["recordDate"], symbol=event["symbol"], price_basis="raw-unadjusted")
        after_view = causal_price_window(bars, [event], as_of=event["exDate"], symbol=event["symbol"], price_basis="raw-unadjusted")
        computed = reference_change(event, str(bars[0]["close"]))
        # Independent expected references were read/calculated from the issuer
        # notices in the preceding evidence review, before this module existed.
        if D(computed["exReference"]) != D(str(case["announcementExReferenceRounded"])):
            raise ValueError("Reference disagrees with sealed primary-source audit")
        if D(before_view["bars"][0]["close"]) != D(str(bars[0]["close"])) or bars != immutable:
            raise ValueError("Raw mutation or premature adjustment")
        if abs(D(after_view["bars"][0]["close"]) - D(computed["exReference"])) > D("1e-20"):
            raise ValueError("Comparable record price differs from expected ex-reference")
        terms.append(event)
        examples.append({"symbol": event["symbol"], "exDate": event["exDate"],
            "rawRecordClose": bars[0]["close"], "rawExClose": bars[1]["close"],
            "beforeExDecisionPrice": before_view["bars"][0]["close"],
            "afterExComparableRecordPrice": after_view["bars"][0]["close"],
            "announcedExReference": computed["exReference"],
            "actualGrantRatio": event["additionalSharesPerShare"],
            "virtualReferenceRatio": event["referenceAdditionalSharesPerShare"],
            "rawAmountVolumeUnchanged": all(a["amount"] == b["amount"] and a["volume"] == b["volume"] for a, b in zip(bars, after_view["bars"])),
            "source": pdf, "notAccountPerformance": True})
    terms_path = OUT / "verified-case-ledger.json"
    write(terms_path, {"schema": "verified-two-case-distribution-ledger-v1", "events": terms,
        "sourceAudit": ref(audit_path), "coverageComplete": False,
        "selection": "two outcome-identified diagnostic cases; not a frozen-cohort event census",
        "brokerAllocationOrCreditVerified": False, "investorTaxModelCertified": False})
    files = [ROOT / "docs/action-aware-synthetic-account-v1.json", Path(__file__),
        *[ROOT / ("service/" + name) for name in ("research_action_basis.py", "research_action_account.py",
            "research_action_account_core.py", "research_action_account_day.py")],
        ROOT / "tests/test_research_action_basis.py", ROOT / "tests/test_research_action_account.py"]
    result = {"schema": "action-aware-engineering-proof-v1", "completedAt": datetime.now(timezone.utc).isoformat(),
        "status": "synthetic-engineering-passed; market-source-feature-acceptance-pending", "passed": True,
        "testsRun": count, "testReceipt": ref(OUT / "test-run.json"), "sourcePins": [ref(p) for p in files],
        "historicalSourcePinsPreserved": len(preserved), "historicalSourcePins": preserved,
        "historicalRegistryPreserved": registry, "protectedJsonCount": len(current), "protectedJsonUnchanged": unchanged,
        "preservationBaseline": ref(old_plan_path), "verifiedCaseLedger": ref(terms_path), "featureExamples": examples,
        "staticDayFlowChanges": 4, "unchangedVolatilityRuleMethodsChecked": 5,
        "newMarketAccounts": 0, "newStrategyTrials": 0, "marketUsePermitted": False,
        "fullCorporateActionCoverageComplete": False, "economicFitnessForGoalProven": False,
        "targetAchieved": False, "goalStatus": "active",
        "nextWork": "Complete dated event-source coverage for frozen cohorts and warmup intervals, then implement independently checked causal signal builder and a frozen real-source account adapter before post9.24 replay."}
    write(OUT / "readout.json", result)
    print(json.dumps({k: result[k] for k in ("passed", "testsRun", "historicalSourcePinsPreserved", "protectedJsonCount", "protectedJsonUnchanged", "newMarketAccounts", "targetAchieved")}))


if __name__ == "__main__":
    main()
