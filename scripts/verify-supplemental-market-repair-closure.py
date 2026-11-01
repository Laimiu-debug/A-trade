#!/usr/bin/env python3
"""External schema correction and full independent supplemental repair replay.

The original verifier and all completed inputs remain immutable. Only its exact
bind_market flags fragment is replaced in a private in-memory module. No
production detector or evaluator is called, and no corrected input is invented.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import types

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_PATH = "scripts/verify-signal-supplemental-market-repair-result.py"
ORIGINAL_SHA256 = "81c01d6ec8afcd8f89b1c6d70039ad5786fc00effb0c7232322a996ce2088755"
REGISTRATION = ROOT / "docs/signal-supplemental-market-repair-v1.json"
REGISTRATION_SHA256 = "436dccdc20811fcbb38d3eeeaf99beb966e71c114ffe10996933a38ccd4315b8"
DEFAULT_OUTPUT = ROOT / "artifacts/strategy-search/signal-supplemental-market-repair-v1"
DEFAULT_FAILED_REPORT = DEFAULT_OUTPUT / "independent-result-verification-failed-20261015T190334260443Z.json"
DEFAULT_REPORT = ROOT / "artifacts/strategy-search/supplemental-market-repair-closure-verification-v1.json"

ORIGINAL_FRAGMENT = '''    for item in (protocol, receipt, proof):
        audit.require(item.get("returnsCalculated") is False and type(item.get("newStrategyTrials")) is int and item["newStrategyTrials"] == 0 and
                      item.get("certified") is False and item.get("targetAchieved") is False, "market-observations-only-flags")
'''
CORRECTED_FRAGMENT = '''    summary = read(directory / "summary.json")
    audit.require(summary.get("schema") == "market-state-observation-summary-v1" and
                  summary.get("protocolHash") == protocol["protocolHash"], "market-summary-schema-and-identity")
    for item in (protocol, summary, proof):
        audit.require(item.get("returnsCalculated") is False and type(item.get("newStrategyTrials")) is int and item["newStrategyTrials"] == 0 and
                      item.get("certified") is False and item.get("targetAchieved") is False, "market-observations-only-flags")
    audit.require(receipt.get("schema") == "market-state-observation-completion-v1" and
                  receipt.get("returnsCalculated") is False and type(receipt.get("newStrategyTrials")) is int and receipt["newStrategyTrials"] == 0 and
                  all(key not in receipt or receipt[key] is False for key in ("certified", "targetAchieved")),
                  "market-completion-schema-observation-flags")
'''


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def text_sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


VERIFIER_SOURCE_PATH = Path(__file__).resolve()
VERIFIER_SHA256 = sha(VERIFIER_SOURCE_PATH)


def corrected_module(root):
    """Load exactly one reviewed textual correction, without changing disk."""
    original = Path(root) / ORIGINAL_PATH
    if sha(original) != ORIGINAL_SHA256:
        raise ValueError("Frozen original verifier hash mismatch")
    source = original.read_text(encoding="utf-8")
    if source.count(ORIGINAL_FRAGMENT) != 1:
        raise ValueError("Expected flags fragment must occur exactly once")
    before, after = source.split(ORIGINAL_FRAGMENT)
    if before.rfind("\ndef bind_market(") < before.rfind("\ndef ") or not after.startswith('    audit.require(protocol["executionArchiveSha256"]'):
        raise ValueError("Correction is outside the exact bind_market boundary")
    effective = before + CORRECTED_FRAGMENT + after
    module = types.ModuleType("_supplemental_market_repair_schema_closure")
    module.__file__ = str(original.resolve())
    exec(compile(effective, str(original), "exec"), module.__dict__)
    module.REGISTRATION = Path(REGISTRATION)
    module.REGISTRATION_SHA256 = REGISTRATION_SHA256
    return module, {
        "originalVerifierPath": ORIGINAL_PATH,
        "originalVerifierSha256": ORIGINAL_SHA256,
        "sourceTextEncoding": "UTF-8 with universal-newline normalization",
        "originalSourceTextSha256": text_sha(source),
        "originalFragment": ORIGINAL_FRAGMENT,
        "originalFragmentSha256": text_sha(ORIGINAL_FRAGMENT),
        "correctedFragment": CORRECTED_FRAGMENT,
        "correctedFragmentSha256": text_sha(CORRECTED_FRAGMENT),
        "effectiveSourceTextSha256": text_sha(effective),
        "replacementCount": 1,
    }


def verify(output: Path = DEFAULT_OUTPUT, *, root: Path = ROOT,
           failed_report: Path = DEFAULT_FAILED_REPORT) -> dict:
    """Return a report after full original replay; do not write any file."""
    started = time.perf_counter()
    root, output, failed_report = Path(root).resolve(), Path(output).resolve(), Path(failed_report).resolve()
    report = {"schema": "supplemental-market-repair-external-closure-verification-v1",
              "checkedAt": datetime.now(timezone.utc).isoformat(), "output": str(output),
              "verifierPath": str(VERIFIER_SOURCE_PATH), "verifierSha256": VERIFIER_SHA256,
              "originalVerifierSha256": ORIGINAL_SHA256, "registrationSha256": REGISTRATION_SHA256,
              "certified": False, "targetAchieved": False, "fullIndependentReplayCompleted": False}
    issues, checks, bound = [], 0, {}

    def check(ok, label):
        nonlocal checks
        checks += 1
        if not ok:
            issues.append({"check": label})
            raise ValueError(label)

    try:
        bound[VERIFIER_SOURCE_PATH] = VERIFIER_SHA256
        bound[root / ORIGINAL_PATH] = ORIGINAL_SHA256
        check(sha(VERIFIER_SOURCE_PATH) == VERIFIER_SHA256, "closure-verifier-start-hash")
        module, correction = corrected_module(root)
        report["schemaCorrection"] = correction
        failed_sha = sha(failed_report)
        bound[failed_report] = failed_sha
        failure = module.read(failed_report)
        check(failure.get("passed") is False and failure.get("verifierSha256") == ORIGINAL_SHA256 and
              failure.get("registrationSha256") == REGISTRATION_SHA256 and
              Path(failure["output"]).resolve() == output,
              "original-failure-identity")
        names = {item.get("check") for item in failure.get("issues", [])}
        check(names == {"market-observations-only-flags", "supplemental-repair-audit-exception"} and
              type(failure.get("failureCount")) is int and failure["failureCount"] == 2,
              "original-failure-is-only-known-schema-error")
        report["originalFailureReport"] = {"path": str(failed_report), "sha256": failed_sha,
            "checks": failure.get("checks"), "failureCount": failure["failureCount"], "issues": failure["issues"]}
        base = module.verify(output, root=root)
        # Identity remains the external closure, never the original unmodified
        # verifier. Retain original replay evidence without hiding its failures.
        for key, value in base.items():
            if key not in {"schema", "checkedAt", "verifierSha256", "checks", "failureCount", "issues", "passed", "elapsedSeconds"}:
                report[key] = value
        checks += base["checks"]
        issues.extend(base["issues"])
        base_failures = base["failureCount"]
        report["fullIndependentReplayCompleted"] = base.get("passed") is True
        report["scope"] = [
            "External schema-only correction: protocol/summary/audit require four observation-only flags; completion requires its two original flags and rejects non-false optional certification fields.",
            "The complete frozen independent verifier then runs, including source/ZIP/receipt binding, all market and role gates, parent lineage/rank mirroring, fresh active replay, event/path/cohort arithmetic and end-of-run source hashes.",
            *base.get("scope", [])]
        report["limitations"] = ["The original failed report is preserved and pinned; this external closure is a different verifier identity, not a retroactive pass by the original verifier.", *base.get("limitations", [])]
    except Exception as error:
        base_failures = locals().get("base_failures", 0)
        issues.append({"check": "external-closure-exception", "errorType": type(error).__name__, "message": str(error)})
    closure_issues = len(issues) - (len(base["issues"]) if "base" in locals() else 0)
    for path, expected in bound.items():
        checks += 1
        try:
            if sha(path) != expected:
                issues.append({"check": "closure-post-file-hash", "path": str(path)})
                closure_issues += 1
        except OSError as error:
            issues.append({"check": "closure-post-file-unavailable", "path": str(path), "message": str(error)})
            closure_issues += 1
    failures = base_failures + closure_issues
    report.update(passed=failures == 0, checks=checks, failureCount=failures, issues=issues,
                  elapsedSeconds=time.perf_counter()-started,
                  verifierSha256=VERIFIER_SHA256)
    report.setdefault("sourceHashes", {}).update({str(p): value for p, value in bound.items()})
    return report


def save_report(path: Path, report: dict) -> Path:
    path = Path(path)
    if not report["passed"]:
        path = path.with_name(path.stem + "-failed-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + path.suffix)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--failed-report", type=Path, default=DEFAULT_FAILED_REPORT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    report = verify(args.output, failed_report=args.failed_report)
    path = save_report(args.report, report)
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "failureCount": report["failureCount"], "report": str(path)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
