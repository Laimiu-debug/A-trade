#!/usr/bin/env python3
"""Independently replay the supplemental raw feature panel, without outcomes."""
from __future__ import annotations

import argparse
from collections import Counter, deque
from datetime import date, datetime, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
from statistics import mean
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "docs/signal-supplemental-leadership-panel-v1.json"
REGISTRATION_SHA = "8b0f07ecd4e9b49e95f9abdecbcbd9a33af14d51dccd06395ddebf5e7796a558"
OLD_VERIFIER_SHA = "3abb3913de24c0bca00538691a1691a8e96fb4ccb21a236300c1d29d11977ce1"
OUTPUT_FILES = {"manifest.json", "features.jsonl.gz", "daily-coverage.jsonl.gz", "warmup-coverage.jsonl.gz",
                "summary.json", "execution-sources.zip", "attempt.json"}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


VERIFIER_PATH = Path(__file__).resolve()
VERIFIER_SHA = sha(VERIFIER_PATH)


def pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError("Nonfinite JSON constant: " + value)


def loads(value):
    return json.loads(value, object_pairs_hook=pairs, parse_constant=invalid_constant)


def read(path):
    return loads(Path(path).read_bytes())


def records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield loads(line)


def rel(root, name):
    item = PurePosixPath(name)
    if item.is_absolute() or ".." in item.parts or "\\" in name or ":" in name:
        raise ValueError("Unsafe relative source path")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Source escapes root")
    return path


def private_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Audit:
    def __init__(self):
        self.checks, self.failure_count, self.issues = 0, 0, []

    def check(self, ok, label, **details):
        self.checks += 1
        if not ok:
            self.failure_count += 1
            if len(self.issues) < 100:
                self.issues.append({"check": label, **details})
        return bool(ok)

    def require(self, ok, label, **details):
        if not self.check(ok, label, **details):
            raise ValueError(label)


def source_materialization(root, document, audit):
    binding = document["sourceMaterialization"]
    source = rel(root, binding["directory"])
    files = binding["files"]
    actual = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()}
    audit.require(actual == set(files), "materialized-source-exact-file-tree")
    for path in source.rglob("*"):
        audit.require(not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()), "source-no-links")
    for name, expected in files.items():
        audit.require(sha(rel(source, name)) == expected, "materialized-source-file-hash", file=name)
    protocol, manifest, receipt, verification = [read(source / name) for name in (
        "protocol.json", "manifest.json", "completion.json", "independent-verification.json")]
    audit.require(hashlib.sha256(encoded({k: v for k, v in protocol.items() if k != "protocolHash"})).hexdigest() == protocol["protocolHash"] == binding["protocolHash"], "source-protocol-identity")
    audit.require(all(item.get("protocolHash") == binding["protocolHash"] for item in (manifest, receipt, verification)), "source-audit-receipt-identity")
    audit.require(protocol["registrationSha256"] == binding["registrationSha256"], "source-registration-binding")
    audit.require(verification.get("passed") is True and type(verification.get("failureCount")) is int and verification["failureCount"] == 0, "source-independent-audit-passed")
    audit.require(verification.get("verifierSha256") == binding["independentVerifierSha256"] == sha(root / "scripts/verify-signal-supplemental-source.py"), "source-independent-verifier-binding")
    audit.require(verification.get("completionSha256") == files["completion.json"] and verification.get("resultFileHashes") == receipt["files"], "source-independent-completion-file-chain")
    audit.require(verification.get("implementation") == protocol["implementation"], "source-audit-exact-implementation-chain")
    audit.require(set(files) == set(receipt["files"]) | {"completion.json", "independent-verification.json"}, "source-complete-file-closure")
    audit.require(all(files[name] == expected for name, expected in receipt["files"].items()), "source-receipt-hashes")
    for value in (protocol, manifest, receipt, verification):
        audit.require(all(value.get(flag) is False for flag in ("returnsCalculated", "featuresCalculated", "certified", "targetAchieved")), "source-transformation-only-flags")
    fixed = document["fixedPopulation"]
    audit.require(protocol["registration"]["fixedPopulation"] == fixed and manifest["fixedSymbols"] == fixed["symbols"], "source-retains-original-fixed-sample")
    selected = sorted(fixed["symbols"] + [manifest["benchmarkSymbol"]])
    audit.require([row["symbol"] for row in manifest["sourceFiles"]] == selected and len(selected) == len(set(selected)), "source-selected-symbol-closure")
    audit.require(sorted(row["symbol"] for row in manifest["excludedSources"]) == protocol["registration"]["excludedOtherPinnedSymbols"], "source-exclusions-retained")
    audit.require(manifest["cutoff"] == document["sample"]["signalEnd"], "source-cutoff")
    audit.require(verification.get("materializedFiles") == len(selected) and verification.get("fixedSymbols") == fixed["count"] and verification.get("excludedOtherPinnedSymbols") == len(manifest["excludedSources"]), "source-audit-population")
    expected_physical = {str(rel(source, row["path"])): row["prefixSha256"] for row in manifest["sourceFiles"]}
    expected_physical[str(source / "workspace/market.sqlite3")] = manifest["workspace"]["sha256"]
    return source / "workspace", expected_physical


def _prepare(output, root, audit, report):
    check = audit.check
    audit.require(REGISTRATION_SHA is not None and sha(REGISTRATION) == REGISTRATION_SHA, "registered-supplemental-feature-document")
    document = read(REGISTRATION)
    manifest, receipt, summary = [read(output / name) for name in ("manifest.json", "completion.json", "summary.json")]
    mh = sha(output / "manifest.json")
    audit.require(manifest["registration"] == document and manifest["registrationSha256"] == receipt["registrationSha256"] == REGISTRATION_SHA, "exact-feature-registration-binding")
    audit.require(mh == receipt["sampleManifestHash"] == summary["sampleManifestHash"], "panel-manifest-reference")
    audit.require(set(receipt["files"]) == OUTPUT_FILES, "exact-panel-completion-files")
    for name, value in receipt["files"].items():
        audit.require(value["path"] == name and sha(output / name) == value["sha256"], "panel-output-hash", file=name)
    files = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    audit.require(files - {"independent-verification.json"} == OUTPUT_FILES | {"completion.json"}, "panel-output-file-tree")
    audit.require(not (output / "failure.json").exists(), "no-failed-panel-attempt")
    for flag in ("certified", "targetAchieved", "futureLabelsAccessed"):
        audit.require(manifest.get(flag) is False and summary.get(flag) is False, "panel-diagnostic-flags", flag=flag)
    audit.require(sha(output / "execution-sources.zip") == manifest["executionArchiveSha256"], "panel-execution-archive-sha")
    audit.require(manifest["implementation"].get("scripts/verify-signal-supplemental-leadership-panel.py") == VERIFIER_SHA, "current-independent-verifier-frozen-in-panel-archive")
    with zipfile.ZipFile(output / "execution-sources.zip") as archive:
        names = archive.namelist()
        audit.require(len(names) == len(set(names)) and set(names) == set(manifest["implementation"]), "exact-execution-archive-members")
        for name, expected in manifest["implementation"].items():
            audit.require(sha(rel(root, name)) == expected == hashlib.sha256(archive.read(name)).hexdigest(), "execution-source-hash", file=name)
    for name, expected in document["frozenFeatureImplementation"].items():
        audit.require(manifest["implementation"].get(name) == expected, "frozen-feature-implementation-retained", file=name)
    for key in ("frozenBuilder", "frozenFeatureRegistration", "frozenIndependentVerifier"):
        item = document[key]
        audit.require(sha(rel(root, item["path"])) == item["sha256"], "frozen-reference-hash", reference=key)
    audit.require(document["frozenIndependentVerifier"]["sha256"] == OLD_VERIFIER_SHA, "original-independent-formulas-frozen")
    workspace, physical = source_materialization(root, document, audit)
    audit.require(manifest["physicalSourceHashes"] == physical, "panel-physical-inputs-exact-materialized-sources")
    attempt = read(output / "attempt.json")
    audit.require(attempt.get("registrationSha256") == REGISTRATION_SHA and attempt.get("workspace") == str(workspace), "attempt-registered-workspace")
    spec, sample, frozen = document["calendar"], document["sample"], manifest["inputs"]
    audit.require(sha(rel(root, spec["path"])) == spec["sha256"], "notice-derived-calendar-hash")
    calendar = read(rel(root, spec["path"]))
    audit.require(calendar.get("schema") == spec["schema"] and calendar["start"] == spec["start"] == sample["warmupStart"] and calendar["end"] == spec["end"] == sample["signalEnd"], "notice-derived-calendar-provenance")
    days = calendar["dates"]
    audit.require(days == sorted(set(days)) and all(type(day) is str and date.fromisoformat(day).isoformat() == day and spec["start"] <= day <= spec["end"] for day in days), "canonical-unique-calendar-dates")
    audit.require(len(days) == sample["expectedCalendarSessions"] and sum(day < sample["signalStart"] for day in days) == sample["expectedWarmupSessions"] and sum(day >= sample["signalStart"] for day in days) == sample["expectedScoredSessions"], "calendar-target-warmup-counts")
    audit.require(calendar["sessionsByYear"] == dict(Counter(day[:4] for day in days)), "calendar-year-counts")
    # The official-notice date list is used directly. It is never recast as a provider response.
    old = private_module(rel(root, document["frozenIndependentVerifier"]["path"]), "_supplemental_independent_formulas")
    digest = old._digest
    audit.require(days == frozen["calendar"]["dates"] and digest(days) == frozen["calendar"]["sha256"], "frozen-calendar-identity")
    source = private_module(root / "scripts/run-strategy-search.py", "_supplemental_independent_source_loader")
    dataset = source.bound_snapshot(old.snapshot_universe(workspace, sample["selection"], sample["signalEnd"], sample["signalStart"]), sample["signalStart"], sample["signalEnd"])
    audit.require(dataset["fingerprint"] == sample["expectedFullSnapshotFingerprint"] == frozen["snapshotFingerprint"], "raw-source-fingerprint")
    audit.require(digest(dataset) == frozen["fullDatasetContentSha256"], "full-dataset-content-digest")
    securities = {security["symbol"]: security for security in dataset["securities"]}
    symbols = sorted(securities)
    audit.require(symbols == document["fixedPopulation"]["symbols"] == frozen["symbols"] and len(symbols) == len(dataset["securities"]) == sample["expectedFullSecurities"] and digest(symbols) == frozen["symbolsSha256"] == document["fixedPopulation"]["sha256"], "exact-original-fixed-population")
    audit.require(sum(len(security["bars"]) for security in securities.values()) == sample["expectedFullBars"], "retained-raw-bar-count")
    audit.require([day for day in dataset["calendar"] if days[0] <= day <= days[-1]] == days, "observed-calendar-equals-registered-notice-calendar")
    for symbol, security in securities.items():
        audit.require(all(bar["date"] <= sample["signalEnd"] for bar in security["bars"]), "no-post-cutoff-bar", symbol=symbol)
        check(digest(security["bars"]) == frozen["securities"][symbol]["retainedRawBarsSha256"], "retained-raw-source-bars", symbol=symbol)
        evidence = {key: value for key, value in security.items() if key not in ("bars", "name", "sourceSha256", "forceSeed", "historyWindow")}
        check(digest(evidence) == frozen["securities"][symbol]["datedEvidenceSha256"], "dated-source-evidence", symbol=symbol)
    benchmark_bars = [bar for bar in dataset["benchmark"]["bars"] if days[0] <= bar["date"] <= days[-1]]
    audit.require(digest(benchmark_bars) == frozen["benchmark"]["rawBarsSha256"] and dataset["benchmark"]["symbol"] == sample["selection"]["benchmarkSymbol"], "benchmark-identity-and-raw-hash")
    report.update(sampleManifestHash=mh, registrationSha256=REGISTRATION_SHA,
                  sourceMaterializationProtocolHash=document["sourceMaterialization"]["protocolHash"],
                  reusedIndependentVerifierSha256=OLD_VERIFIER_SHA, implementation=manifest["implementation"])
    return old, document, manifest, receipt, summary, dataset, symbols, securities, days, mh


def _replay(output, prepared, audit, report):
    old, registration, manifest, receipt, summary, dataset, symbols, securities, days, mh = prepared
    sample, frozen = registration["sample"], manifest["inputs"]
    inputs, returns, ranks = old.inputs, old.returns, old.ranks
    complete, count, formulas, near = old.complete, old.count, old.formulas, old.near
    FEATURE_FIELDS, check = old.FEATURE_FIELDS, audit.check
    last_notice, sampled = time.perf_counter(), 0
    raw = {symbol: inputs(security, days) for symbol, security in securities.items()}
    bench = inputs(dataset.get("benchmark") or {}, days)
    chosen = set(sorted(symbols, key=lambda s: hashlib.sha256(("leader-panel-formula-audit-v1:"+s).encode()).hexdigest())[:32])
    histories = {s: deque(maxlen=20) for s in symbols}
    history_counts, history_dates = deque(maxlen=20), deque(maxlen=20)
    target_coverage, warmup_coverage = list(records(output / "daily-coverage.jsonl.gz")), list(records(output / "warmup-coverage.jsonl.gz"))
    check([c["asOfDate"] for c in warmup_coverage+target_coverage] == days, "all-registered-coverage-dates")
    daily_lookup = {c["asOfDate"]: c for c in warmup_coverage+target_coverage}
    stream = records(output / "features.jsonl.gz")
    total_rows = 0
    totals = {k: Counter() for k in ("featureNonNullCounts", "featureGroupCompleteness", "eligibilityReasons", "unknownHistoricalFields", "qualityCounts", "crossSectionDenominators")}
    for index, day in enumerate(days):
        expected_returns = {h: {s: returns(raw[s], bench, index, h) for s in symbols} for h in (20, 60)}
        ranking = {h: ranks({s: v[1] for s, v in expected_returns[h].items()}) for h in (20, 60)}
        history_counts.append(ranking[20][1])
        history_dates.append(day)
        for symbol in symbols:
            histories[symbol].append(ranking[20][0].get(symbol))
        cover = daily_lookup[day]
        check(set(cover["featureNonNullCounts"]) == set(FEATURE_FIELDS), "exact-feature-count-fields", day=day)
        check(cover["rows"] == len(symbols) and cover["sampleManifestHash"] == mh and cover["crossSectionDenominators"] == {str(h): ranking[h][1] for h in (20, 60)}, "daily-source-rank-denominators", day=day)
        if day < sample["signalStart"]:
            continue
        feature_counts, group_counts, reasons, unknown, quality = Counter(), Counter(), Counter(), Counter(), Counter()
        missing_sample_used = False
        for symbol in symbols:
            row = next(stream, None)
            if row is None:
                raise ValueError("Feature file ended before its fixed population")
            total_rows += 1
            check((row["asOfDate"], row["symbol"], row["sampleManifestHash"], row["featureVersion"]) == (day, symbol, mh, registration["featureVersion"]), "row-identity", day=day, symbol=symbol)
            check(row["sourceHashes"] == {**frozen["securities"][symbol], "benchmarkRawBarsSha256": frozen["benchmark"]["rawBarsSha256"]} and row["calendarHash"] == frozen["calendar"]["sha256"], "row-source-binding", day=day, symbol=symbol)
            for horizon in (20, 60):
                own, relative = expected_returns[horizon][symbol]
                check(near(row[f"return{horizon}"], own) and near(row[f"relativeReturn{horizon}"], relative)
                    and row[f"relativeReturn{horizon}Percentile"] == ranking[horizon][0].get(symbol)
                    and row[f"relativeReturn{horizon}CrossSectionCount"] == ranking[horizon][1], "raw-returns-and-rank", day=day, symbol=symbol, horizon=horizon)
            history = [None]*(20-len(histories[symbol])) + list(histories[symbol])
            known = [v for v in history if v is not None]
            check(row["trailing20DailyStrengthPercentiles"] == history
                and row["trailing20StrengthCrossSectionCounts"] == [None]*(20-len(history_counts))+list(history_counts)
                and row["strengthRankSessionDates20"] == [None]*(20-len(history_dates))+list(history_dates)
                and row["knownStrengthRankSessions20"] == len(known)
                and near(row["meanStrengthPercentile20"], mean(known) if len(known) == 20 else None)
                and row["minimumStrengthPercentile20"] == (min(known) if len(known) == 20 else None)
                and row["topQuantileSessionCount20"] is None, "archived-rank-persistence", day=day, symbol=symbol)
            expected_complete = {"security20": complete(raw[symbol], index, 21), "security60": complete(raw[symbol], index, 61),
                "benchmark20": complete(bench, index, 21), "benchmark60": complete(bench, index, 61),
                "path20": complete(raw[symbol], index, 21), "ma20": complete(raw[symbol], index, 39),
                "amountPrior20": complete(raw[symbol], index-1, 20, "a"), "amountToday": raw[symbol]["amount"][index]}
            check({k: c["complete"] for k, c in row["completeness"].items()} == expected_complete
                and row["knownAmountSessions20"] == count(raw[symbol]["a"], index-1, 20), "independent-group-availability", day=day, symbol=symbol)
            if symbol in chosen or not missing_sample_used and not expected_complete["security60"]:
                sampled += 1
                if not expected_complete["security60"]:
                    missing_sample_used = True
                expected = formulas(raw[symbol], index)
                check(all(near(row[k], v) for k, v in expected.items()), "raw-path-and-amount-formulas", day=day, symbol=symbol)
            check(row["certified"] is False and row["targetAchieved"] is False and row["historicalEligibilityVerified"] is False
                and row["baseEligibility"]["affectsFeaturesOrRanks"] is False, "diagnostic-only-flags", day=day, symbol=symbol)
            for key in FEATURE_FIELDS:
                feature_counts[key] += row[key] is not None
            for key, value in expected_complete.items():
                group_counts[key + (":complete" if value else ":incomplete")] += 1
            state = row["baseEligibility"]
            reasons[state["reason"]] += 1
            evidence_keys = {"historicalSt": "historicalSt", "historicalMembership": "historicalMembership",
                "listingDate": "listingDate", "tradeStatus": "historicalTradeStatus"}
            unknown.update(mapped for key, mapped in evidence_keys.items() if row["asOfEvidence"][key]["status"] == "unknown")
            quality["eligible" if state["eligible"] else "ineligible"] += 1
            for field in ("missingDates", "invalidDates", "haltDates", "unknownTradeStatusDates"):
                quality[field+":current"] += day in row[field]
                quality[field+":anyWindow"] += bool(row[field])
        calculated = {"featureNonNullCounts": dict(feature_counts), "featureGroupCompleteness": dict(group_counts),
            "eligibilityReasons": dict(reasons), "unknownHistoricalFields": dict(unknown), "qualityCounts": dict(quality),
            "crossSectionDenominators": {str(h): ranking[h][1] for h in (20, 60)}}
        check(all(cover[k] == v for k, v in calculated.items()), "daily-coverage-arithmetic", day=day)
        for key in totals:
            totals[key].update(calculated[key])
        if time.perf_counter()-last_notice > 30:
            print(f"Independent panel replay: {total_rows}/{sample['expectedFeatureRows']} rows", flush=True)
            last_notice = time.perf_counter()
    check(next(stream, None) is None and total_rows == sample["expectedFeatureRows"] == summary["featureRows"] == receipt["featureRows"], "complete-row-count-no-trailing-rows")
    check(len(target_coverage) == sample["expectedScoredSessions"] == summary["targetSessions"] and len(warmup_coverage) == sample["expectedWarmupSessions"] == summary["warmupSessions"], "target-warmup-counts")
    check(all(summary[key] == dict(value) for key, value in totals.items()), "summary-coverage-arithmetic")
    check(summary["strategyReturnsCalculated"] is False and summary["forwardReturnsCalculated"] is False
        and summary["backwardPriceReturnsCalculated"] is True and summary["tradePoliciesAdded"] == 0, "no-trading-or-forward-label-output")
    report.update(featureRows=total_rows, formulaRowsReplayed=sampled,
                  fixedFormulaSampleSymbols=sorted(chosen), targetSessions=len(target_coverage),
                  warmupSessions=len(warmup_coverage))


def verify(output: Path, root: Path = ROOT) -> dict:
    """Read-only report. Never call the old main or rewrite a provider payload."""
    started = time.perf_counter()
    output, root = Path(output).resolve(), Path(root).resolve()
    audit = Audit()
    report = {"schema": "supplemental-leadership-panel-independent-verification-v1",
        "verifiedAt": datetime.now(timezone.utc).isoformat(), "verifierSha256": VERIFIER_SHA,
        "certified": False, "targetAchieved": False, "strategyReturnsCalculated": False,
        "forwardReturnsCalculated": False, "backwardPriceReturnsCalculated": True,
        "scope": "Every target-row identity, own/relative backward return, rank, 20-session rank history and feature-group complete flag; target-day/total coverage arithmetic. Warmup: date, row count and independently reconstructed rank denominators. Raw path/amount formulas: the unchanged fixed 32-symbol hash sample plus the first incomplete-price row each target day. Source materialization, exact source/output inventories, archived implementation and the genuine notice-derived calendar are separately hash-bound.",
        "limitations": ["Reuses the frozen original independent verifier's pure numerical functions and the original read-only source loader, never the feature implementation or the old verifier main.",
            "Base eligibility is reconciled from the panel's recorded as-of metadata; its complete decision arithmetic is not independently reimplemented.",
            "Detailed missing/invalid/status date arrays and per-group observed/valid counts are not independently reconstructed in full. Their reported target-day arithmetic is reconciled.",
            "The source transformation audit supplies prefix-byte/SQLite provenance; pre-2023 numeric quality, historical full-market identity and corporate-action completeness remain uncertified.",
            "Known prior default-strategy exposure means this interval is qualified supplemental validation, not pristine holdout. No leader truth, causal influence, future return, economic net30, execution or target-probability certification."]}
    try:
        audit.require(sha(VERIFIER_PATH) == VERIFIER_SHA, "verifier-imported-source-identity")
        output_pins = {name: sha(output / name) for name in OUTPUT_FILES | {"completion.json"}}
        prepared = _prepare(output, root, audit, report)
        document, manifest = prepared[1], prepared[2]
        # Record both current adapter and every bound reused component.
        source_pins = {Path(REGISTRATION).resolve().relative_to(root).as_posix(): REGISTRATION_SHA,
                       document["calendar"]["path"]: document["calendar"]["sha256"],
                       **document["frozenFeatureImplementation"]}
        for key in ("frozenBuilder", "frozenFeatureRegistration", "frozenIndependentVerifier"):
            source_pins[document[key]["path"]] = document[key]["sha256"]
        for name, expected in document["sourceMaterialization"]["files"].items():
            source_pins[document["sourceMaterialization"]["directory"] + "/" + name] = expected
        _replay(output, prepared, audit, report)
        for name, expected in output_pins.items():
            audit.check(sha(output / name) == expected, "panel-output-unchanged-during-replay", file=name)
        for name, expected in {**source_pins, **manifest["implementation"]}.items():
            audit.check(sha(rel(root, name)) == expected, "source-unchanged-during-replay", file=name)
        expected_output_files = OUTPUT_FILES | {"completion.json"}
        actual = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
        audit.check(actual - {"independent-verification.json"} == expected_output_files, "panel-file-inventory-unchanged")
        source_directory = rel(root, document["sourceMaterialization"]["directory"])
        actual_sources = {path.relative_to(source_directory).as_posix() for path in source_directory.rglob("*") if path.is_file()}
        audit.check(actual_sources == set(document["sourceMaterialization"]["files"]), "source-file-inventory-unchanged")
        report.update(resultFileHashes=output_pins, sourceHashes=source_pins)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, EOFError, zipfile.BadZipFile) as error:
        audit.check(False, "verification-exception", errorType=type(error).__name__, error=str(error))
    try:
        audit.check(sha(VERIFIER_PATH) == VERIFIER_SHA, "verifier-source-unchanged-after-replay")
    except OSError as error:
        audit.check(False, "verifier-source-unavailable", error=str(error))
    report.update(passed=audit.failure_count == 0, checks=audit.checks, failureCount=audit.failure_count,
                  issues=audit.issues, elapsedSeconds=time.perf_counter() - started)
    return report


def save_report(output, report):
    name = "independent-verification.json" if report["passed"] else "independent-verification-failed-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    path = Path(output) / name
    with path.open("xb") as stream:
        stream.write(encoded(report) + b"\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/strategy-search/supplemental-leadership-panel-v1")
    args = parser.parse_args(argv)
    report = verify(args.output)
    report_path = save_report(args.output, report)
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "failureCount": report["failureCount"], "report": str(report_path)}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
