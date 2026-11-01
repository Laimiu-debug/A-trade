"""Independently replay an outcome-free market/role panel from frozen features.

Only standard-library imports are used. Neither the builder nor its pure core
is imported, and no strategy outcome, live market source, or user DB is read.
The previously audited feature panel is a trusted, hash-bound input; this is
not a second raw-price reconstruction of that panel or an execution audit.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from datetime import date, datetime, timezone
from functools import lru_cache
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_SOURCE_PATH = Path(__file__).resolve()
VERIFIER_SOURCE_SHA256 = hashlib.sha256(VERIFIER_SOURCE_PATH.read_bytes()).hexdigest()
DEFAULT_OUTPUT = ROOT / "artifacts/strategy-search/market-state-panel-v1"
DEFAULT_PANEL = ROOT / "artifacts/strategy-search/leadership-panel-v1"
REGISTRATION = "docs/signal-market-state-panel-v1.json"
REGISTRATION_SHA256 = "40a68c2a85cf4fd2b4e9d4968d80c027726187ea36f70487fa902662b07c2e20"
VERSION = "market-state-observations-v1"
SOURCE_VERSION = "daily-leadership-raw-v1"
INPUT_FILES = {"manifest.json", "features.jsonl.gz", "daily-coverage.jsonl.gz",
               "warmup-coverage.jsonl.gz", "summary.json", "execution-sources.zip",
               "attempt.json", "completion.json", "independent-verification.json"}
OUTPUT_FILES = {"protocol.json", "execution-sources.zip", "attempt.json",
                "daily.jsonl.gz", "roles.jsonl.gz", "summary.json"}
IMPLEMENTATION = {REGISTRATION, "scripts/build-signal-market-state-panel.py",
                  "service/signal_market_state.py"}
GATES = (("meanStrengthPercentile20", ">=", .90),
         ("minimumStrengthPercentile20", ">=", .80),
         ("relativeReturn60Percentile", ">=", .90),
         ("closePathMaxDrawdown20", "<=", .10),
         ("signedPathEfficiency20", ">=", .35))
GROUP_LENGTHS = {"security20": 21, "benchmark20": 21, "security60": 61,
                 "benchmark60": 61, "path20": 21, "ma20": 39,
                 "amountPrior20": 20, "amountToday": 1}


def read(path):
    return path.read_text(encoding="utf-8")


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key: " + key)
        result[key] = value
    return result


def parse_json(text):
    return json.loads(text, object_pairs_hook=unique_pairs)


def read_json(path):
    return parse_json(read(path))


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def bounded(value, low=0, high=1):
    return finite(value) and low <= value <= high


@lru_cache(maxsize=4096)
def valid_day_text(value):
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def valid_day(value):
    return isinstance(value, str) and valid_day_text(value)


def ratio(a, b):
    return a / b if b else None


def nullable_median(values):
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else mean(ordered[n // 2 - 1:n // 2 + 1])


def json_records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield parse_json(line)


def take(iterator, label):
    try:
        return next(iterator)
    except StopIteration:
        raise ValueError(label + " ended before the registered population") from None


def exhausted(iterator, label):
    try:
        next(iterator)
    except StopIteration:
        return
    raise ValueError(label + " contains trailing records")


class Audit:
    def __init__(self):
        self.checks = self.failures = 0
        self.issues = []

    def check(self, condition, label, **details):
        self.checks += 1
        if not condition:
            self.failures += 1
            if len(self.issues) < 100:
                self.issues.append({"check": label, **details})
        return bool(condition)

    def require(self, condition, label):
        if not self.check(condition, label):
            raise ValueError("Cannot safely replay: " + label)

    def equal(self, actual, expected, label, **details):
        # Counts, classes and serialized sidecars use exact equality. Numeric
        # aggregates get only arithmetic comparison tolerance, never Q gates.
        if type(expected) is float:
            ok = finite(actual) and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)
        elif expected is None or type(expected) is bool:
            ok = actual is expected
        elif type(expected) is int:
            ok = type(actual) is int and actual == expected
        else:
            ok = actual == expected
        self.check(ok, label, **details)


def contained(base, name):
    path = (base / name).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise ValueError("Artifact path is missing or escapes its directory: " + name)
    return path


def archive_matches(path, implementation, root, audit):
    with zipfile.ZipFile(path) as archive:
        audit.require(sorted(archive.namelist()) == sorted(implementation), "source-archive-exact-names")
        for name, expected_hash in implementation.items():
            audit.require(sha(contained(root, name)) == expected_hash, "live-source-hash:" + name)
            audit.require(hashlib.sha256(archive.read(name)).hexdigest() == expected_hash,
                          "archived-source-hash:" + name)


def binding(output, panel, root, audit):
    registration_path = contained(root, REGISTRATION)
    audit.require(sha(registration_path) == REGISTRATION_SHA256, "registered-rule-hash")
    document = read_json(registration_path)
    audit.require(document["status"] == "registered-before-first-real-extraction" and
        document["featureVersion"] == VERSION and document["sourceFeatureVersion"] == SOURCE_VERSION and
        type(document["newStrategyTrials"]) is int and document["newStrategyTrials"] == 0 and
        document["returnsCalculated"] is False and document["futureLabelsCreated"] is False and
        document["certified"] is False and document["targetAchieved"] is False, "observation-only-registration")
    audit.require(sha(contained(root, document["design"]["path"])) == document["design"]["sha256"],
                  "design-provenance")
    audit.require(panel == (root / document["input"]["directory"]).resolve(), "input-panel-directory")
    audit.require(not output.is_relative_to(panel), "output-outside-input-panel")
    expected_inputs = document["input"]["filesSha256"]
    audit.require(set(expected_inputs) == INPUT_FILES, "nine-registered-input-files")
    bound_files = {contained(root, document["design"]["path"]): document["design"]["sha256"]}
    for name, expected_hash in expected_inputs.items():
        path = contained(panel, name)
        audit.require(sha(path) == expected_hash, "input-file-hash:" + name)
        bound_files[path] = expected_hash
    manifest = read_json(panel / "manifest.json")
    prior_receipt = read_json(panel / "completion.json")
    prior_audit = read_json(panel / "independent-verification.json")
    sample, inputs = document["sample"], manifest["inputs"]
    symbols, calendar = inputs["symbols"], inputs["calendar"]["dates"]
    days = [d for d in calendar if sample["start"] <= d <= sample["end"]]
    audit.require(manifest["featureVersion"] == SOURCE_VERSION and symbols == sorted(set(symbols)) and
        len(symbols) == sample["symbols"] == inputs["symbolCount"] and set(inputs["securities"]) == set(symbols) and
        inputs["symbolsSha256"] == digest(symbols) and calendar == sorted(set(calendar)) and
        all(valid_day(d) for d in calendar) and inputs["calendar"]["sha256"] == digest(calendar) and
        inputs["snapshotFingerprint"] == sample["snapshotFingerprint"] and
        len(days) == sample["sessions"] and len(days) * len(symbols) == sample["rows"], "fixed-sample-calendar")
    audit.require(prior_receipt["sampleManifestHash"] == expected_inputs["manifest.json"] and
        prior_audit["sampleManifestHash"] == expected_inputs["manifest.json"] and
        prior_audit.get("passed") is True and type(prior_audit.get("failureCount")) is int and
        prior_audit["failureCount"] == 0 and prior_audit["featureRows"] == sample["rows"] and
        prior_receipt["featureRows"] == sample["rows"] and prior_receipt["targetSessions"] == sample["sessions"],
        "prior-completed-panel-and-independent-audit")
    old_files = INPUT_FILES - {"completion.json", "independent-verification.json"}
    audit.require(set(prior_receipt["files"]) == old_files and all(
        prior_receipt["files"][name] == {"path": name, "sha256": expected_inputs[name]} for name in old_files),
        "prior-panel-receipt")
    protocol = read_json(contained(output, "protocol.json"))
    body = {k: v for k, v in protocol.items() if k != "protocolHash"}
    audit.require(protocol["protocolHash"] == digest(body) and
        protocol["schema"] == "market-state-observation-protocol-v1" and
        protocol["registrationSha256"] == REGISTRATION_SHA256 and protocol["registration"] == document and
        protocol["inputFilesSha256"] == expected_inputs and protocol["symbols"] == symbols and
        protocol["dates"] == days and protocol["inputManifestSha256"] == expected_inputs["manifest.json"] and
        protocol["calendarHash"] == inputs["calendar"]["sha256"] and
        type(protocol["newStrategyTrials"]) is int and protocol["newStrategyTrials"] == 0 and
        protocol["returnsCalculated"] is False and protocol["certified"] is False and
        protocol["targetAchieved"] is False, "frozen-execution-protocol")
    audit.require(set(protocol["implementation"]) == IMPLEMENTATION, "complete-execution-dependencies")
    audit.require(protocol["executionArchiveSha256"] == sha(contained(output, "execution-sources.zip")),
                  "whole-execution-archive-hash")
    archive_matches(contained(output, "execution-sources.zip"), protocol["implementation"], root, audit)
    receipt = read_json(contained(output, "completion.json"))
    audit.require(receipt["schema"] == "market-state-observation-completion-v1" and
        receipt["protocolHash"] == protocol["protocolHash"] and receipt["roleRows"] == sample["rows"] and
        receipt["dailyRows"] == sample["sessions"] and receipt["returnsCalculated"] is False and
        type(receipt["newStrategyTrials"]) is int and receipt["newStrategyTrials"] == 0 and
        set(receipt["files"]) == OUTPUT_FILES, "complete-output-receipt")
    for name, entry in receipt["files"].items():
        path = contained(output, name)
        actual_hash = sha(path)
        audit.require(entry == {"path": name, "sha256": actual_hash}, "output-file-hash:" + name)
        bound_files[path] = actual_hash
    bound_files[output / "completion.json"] = sha(output / "completion.json")
    for name, expected_hash in protocol["implementation"].items():
        bound_files[root / name] = expected_hash
    attempt = read_json(output / "attempt.json")
    audit.require(attempt["protocolHash"] == protocol["protocolHash"] and
        attempt["rule"] == "one-complete-extraction;no-outcome-inputs" and not (output / "failure.json").exists(),
        "successful-attempt-state")
    lineage = {"inputManifestSha256": expected_inputs["manifest.json"],
        "inputFeatureFileSha256": expected_inputs["features.jsonl.gz"],
        "calendarHash": protocol["calendarHash"], "protocolHash": protocol["protocolHash"]}
    return document, manifest, protocol, symbols, days, lineage, bound_files


def ordered_dates(values, day, maximum=None):
    return (isinstance(values, list) and (maximum is None or len(values) <= maximum) and
        all(valid_day(d) and d <= day for d in values) and values == sorted(set(values)))


def window_ok(row, name):
    groups = row.get("completeness")
    value = groups.get(name) if isinstance(groups, dict) else None
    if not isinstance(value, dict) or value.get("complete") is not True:
        return False
    size = GROUP_LENGTHS[name]
    return (all(type(value.get(key)) is int and value[key] == size for key in
                ("requiredSessions", "availableCalendarSessions", "observedSessions", "validSessions")) and
            all(value.get(key) == [] for key in ("missingDates", "invalidDates", "haltDates", "reasons")) and
            ordered_dates(value.get("unknownTradeStatusDates"), row["asOfDate"], size))


def feature_issue(row, field):
    reasons = row.get("featureReasons")
    if not isinstance(reasons, dict):
        return ["featureReasonsUnavailable"]
    return ["featureReasons:" + field] if field in reasons and reasons[field] != [] else []


def common_issues(row):
    return (["sourceFeatureVersionMismatch"] if row.get("featureVersion") != SOURCE_VERSION else []) + (
        ["rawPriceBasisUnknown"] if row.get("rawPriceBasis") != "raw-unadjusted" else [])


def missing_windows(row, names):
    return ["requiredWindowUnavailable:" + name for name in names if not window_ok(row, name)]


def reference_row(row, days, day_index, population):
    """Evaluate registered evidence independently; no dependency on output Q."""
    common = common_issues(row)
    own = row.get("return20")
    price_reasons = common + missing_windows(row, ["security20"]) + feature_issue(row, "return20")
    if not finite(own):
        price_reasons.append("nonfiniteFeature:return20")
    price = {"evaluable": not price_reasons, "return20": own if not price_reasons else None,
             "reasons": sorted(set(price_reasons))}
    amount_reasons = common + missing_windows(row, ["amountToday", "amountPrior20"])
    if type(row.get("knownAmountSessions20")) is not int or row["knownAmountSessions20"] != 20:
        amount_reasons.append("knownAmountSessions20Incomplete")
    for field in ("amountToday", "prior20MeanAmount", "amountRelativeToOwnPrior20"):
        if not finite(row.get(field)) or row[field] <= 0:
            amount_reasons.append("nonpositiveOrNonfiniteFeature:" + field)
        amount_reasons.extend(feature_issue(row, field))
    if all(finite(row.get(field)) and row[field] > 0 for field in
           ("amountToday", "prior20MeanAmount", "amountRelativeToOwnPrior20")):
        if row["amountRelativeToOwnPrior20"] != row["amountToday"] / row["prior20MeanAmount"]:
            amount_reasons.append("amountRatioMismatch")
    amount = {"evaluable": not amount_reasons,
              "ratio": row.get("amountRelativeToOwnPrior20") if not amount_reasons else None,
              "reasons": sorted(set(amount_reasons))}

    history, sizes, history_days = (row.get(k) for k in ("trailing20DailyStrengthPercentiles",
        "trailing20StrengthCrossSectionCounts", "strengthRankSessionDates20"))
    history_valid = isinstance(history, list) and len(history) == 20 and all(bounded(v) for v in history)
    sizes_valid = isinstance(sizes, list) and len(sizes) == 20 and all(
        type(n) is int and 1 <= n <= population for n in sizes)
    historical_issues = []
    if not history_valid:
        historical_issues.append("archivedStrengthPercentilesUnavailable")
    if not sizes_valid:
        historical_issues.append("archivedStrengthCountsUnavailable")
    if (not ordered_dates(history_days, row["asOfDate"]) or len(history_days) != 20 or
            history_days[-1] != row["asOfDate"] or [d for d in history_days if d >= days[0]] !=
            days[max(0, day_index - 19):day_index + 1]):
        historical_issues.append("archivedStrengthDatesUnavailable")
    for field in ("knownStrengthRankSessions20", "availableStrengthRankSessions20"):
        if type(row.get(field)) is not int or row[field] != 20:
            historical_issues.append("invalidCount:" + field)
    current_size = row.get("relativeReturn20CrossSectionCount")
    if not bounded(row.get("relativeReturn20Percentile")) or not (
            type(current_size) is int and 1 <= current_size <= population):
        historical_issues.append("currentStrengthRankUnavailable")
    elif (isinstance(history, list) and len(history) == 20 and isinstance(sizes, list) and len(sizes) == 20 and
          (history[-1] != row["relativeReturn20Percentile"] or sizes[-1] != current_size)):
        historical_issues.append("currentStrengthRankArchiveMismatch")
    for field in ("return20", "relativeReturn20"):
        if not finite(row.get(field)):
            historical_issues.append("nonfiniteFeature:" + field)
        historical_issues.extend(feature_issue(row, field))
    required = {"meanStrengthPercentile20": ["security20", "benchmark20"],
        "minimumStrengthPercentile20": ["security20", "benchmark20"],
        "relativeReturn60Percentile": ["security60", "benchmark60"],
        "closePathMaxDrawdown20": ["path20"], "signedPathEfficiency20": ["path20"]}
    predicates = {}
    for field, operator, threshold in GATES:
        value = row.get(field)
        valid = bounded(value, -1 if field == "signedPathEfficiency20" else 0)
        issues = common + feature_issue(row, field) + missing_windows(row, required[field])
        if not valid:
            issues.append("invalidBoundedFeature:" + field)
        if field in ("meanStrengthPercentile20", "minimumStrengthPercentile20"):
            issues.extend(historical_issues)
            if history_valid and valid:
                expected_value = mean(history) if field == "meanStrengthPercentile20" else min(history)
                if value != expected_value:
                    issues.append("archivedStrengthStatisticMismatch:" + field)
        if field == "relativeReturn60Percentile":
            n = row.get("relativeReturn60CrossSectionCount")
            if type(n) is not int or not 1 <= n <= population:
                issues.append("currentRelative60RankCountUnavailable")
            for underlying in ("return60", "relativeReturn60"):
                if not finite(row.get(underlying)):
                    issues.append("nonfiniteFeature:" + underlying)
                issues.extend(feature_issue(row, underlying))
        issues = sorted(set(issues))
        passed = None if issues else value >= threshold if operator == ">=" else value <= threshold
        predicates[field] = {"value": value if valid else None, "operator": operator,
            "threshold": threshold, "passed": passed, "unknownReasons": issues}
    unknown = sorted({reason for p in predicates.values() for reason in p["unknownReasons"]})
    failed = [name for name, p in predicates.items() if p["passed"] is False]
    return {"qState": "unknown" if unknown else "false" if failed else "true",
        "criterionValues": {name: p["value"] for name, p in predicates.items()},
        "predicates": predicates, "failedPredicates": failed, "unknownReasons": unknown,
        "price20": price, "amount": amount}


def reference_transition(prior, current):
    table = {
        (None, "true"): "priorStateUnknownEntrant", (None, "false"): "initial:false", (None, "unknown"): "initial:unknown",
        ("true", "true"): "continued", ("true", "false"): "observedFailure", ("true", "unknown"): "unknown",
        ("false", "true"): "knownNewChallenger", ("false", "false"): "falseToFalse", ("false", "unknown"): "falseToUnknown",
        ("unknown", "true"): "priorStateUnknownEntrant", ("unknown", "false"): "unknownToFalse", ("unknown", "unknown"): "unknownToUnknown"}
    return table[prior, current]


def quality_from_input(rows):
    groups = {}
    for name, length in GROUP_LENGTHS.items():
        counts = {"requiredSessions": length, "completeCount": 0, "missingCount": 0,
            "invalidCount": 0, "haltCount": 0, "unknownTradeStatusCount": 0, "unavailableEvidenceCount": 0}
        reasons = Counter()
        for row in rows:
            mapping = row.get("completeness")
            g = mapping.get(name) if isinstance(mapping, dict) else None
            if not isinstance(g, dict):
                counts["unavailableEvidenceCount"] += 1
                reasons["groupEvidenceUnavailable"] += 1
                continue
            counts["completeCount"] += int(window_ok(row, name))
            for source, target in (("missingDates", "missingCount"), ("invalidDates", "invalidCount"),
                                   ("haltDates", "haltCount"), ("unknownTradeStatusDates", "unknownTradeStatusCount")):
                if ordered_dates(g.get(source), row["asOfDate"], length):
                    counts[target] += int(bool(g[source]))
                else:
                    reasons["invalidEvidence:" + source] += 1
            if isinstance(g.get("reasons"), list) and all(isinstance(r, str) for r in g["reasons"]):
                reasons.update(set(g["reasons"]))
            else:
                reasons["groupReasonsUnavailable"] += 1
        counts["reasonCounts"] = dict(reasons)
        groups[name] = counts
    return {"groups": groups, "countsMayOverlap": True, "historicalIdentityAndTradeStatusCertified": False}


def reference_day(rows, current, previous, old, days, index):
    size = len(rows)
    prices = [r["price20"]["return20"] for r in current if r["price20"]["evaluable"]]
    amounts = [r["amount"]["ratio"] for r in current if r["amount"]["evaluable"]]
    reasons = lambda key: dict(Counter(reason for record in current for reason in set(record[key]["reasons"])))
    distribution = Counter(str(r["knownAmountSessions20"]) if type(r.get("knownAmountSessions20")) is int and
        0 <= r["knownAmountSessions20"] <= 20 else "unknownOrInvalid" for r in rows)
    price = {"evaluableCount": len(prices), "coverage": len(prices)/size, "unavailableCount": size-len(prices),
        "medianAbsoluteReturn20": nullable_median(prices), "positiveCount": sum(p > 0 for p in prices),
        "zeroCount": sum(p == 0 for p in prices), "negativeCount": sum(p < 0 for p in prices),
        "positiveReturn20Breadth": ratio(sum(p > 0 for p in prices), len(prices)), "reasonCounts": reasons("price20")}
    amount = {"evaluableCount": len(amounts), "coverage": len(amounts)/size, "unavailableCount": size-len(amounts),
        "medianRelativeToOwnPrior20": nullable_median(amounts), "knownAmountSessions20Distribution": dict(distribution),
        "reasonCounts": reasons("amount")}
    matched = {key: None for key in ("earlierEvaluableCount", "pairedCount", "pairedCoverageToday", "pairedCoverageEarlier",
        "pairedCoverageFixed", "positiveBreadthToday", "positiveBreadthEarlier", "medianReturnToday", "medianReturnEarlier",
        "matchedBreadthChange5", "matchedMedianReturnChange5", "unpairedTodayCount", "unpairedEarlierCount")}
    matched.update(available=old is not None, earlierDate=days[index-5] if old is not None else None,
        todayEvaluableCount=len(prices), reason="earlierTargetSessionUnavailable" if old is None else None)
    if old is not None:
        pairs = [(now["price20"]["return20"], past["return20"]) for now, past in zip(current, old)
                 if now["price20"]["evaluable"] and past["evaluable"]]
        n, old_n = len(pairs), sum(r["evaluable"] for r in old)
        matched.update(earlierEvaluableCount=old_n, pairedCount=n, pairedCoverageToday=ratio(n, len(prices)),
            pairedCoverageEarlier=ratio(n, old_n), pairedCoverageFixed=n/size,
            unpairedTodayCount=len(prices)-n, unpairedEarlierCount=old_n-n)
        if pairs:
            today, prior = zip(*pairs)
            bt, bp = sum(v > 0 for v in today)/n, sum(v > 0 for v in prior)/n
            mt, mp = nullable_median(today), nullable_median(prior)
            delta = mt-mp
            matched.update(positiveBreadthToday=bt, positiveBreadthEarlier=bp, medianReturnToday=mt,
                medianReturnEarlier=mp, matchedBreadthChange5=100*(bt-bp),
                matchedMedianReturnChange5=delta if finite(delta) else None,
                reason=None if finite(delta) else "nonfiniteMedianDifference")
        else:
            matched["reason"] = "emptyMatchedPopulation"
    cohort = {key: None for key in ("eligibleCount", "continuedCount", "observedFailureCount", "unknownCount", "observedCount",
        "conditionalContinuationRate", "observedCoverage", "continuationLowerBound", "continuationUpperBound")}
    transitions = [reference_transition(previous[i]["qState"] if previous else None, r["qState"]) for i, r in enumerate(current)]
    cohort.update(previousCohortAvailable=previous is not None, previousDate=days[index-1] if previous is not None else None,
        members=None, reason="previousCohortUnavailable" if previous is None else None,
        boundsMeaning="observation-missingness bounds; not confidence intervals or net30 probabilities",
        currentTrueRoles={k: transitions.count(k) for k in ("continued", "knownNewChallenger", "priorStateUnknownEntrant")})
    if previous is not None:
        members = [i for i, record in enumerate(previous) if record["qState"] == "true"]
        counts = Counter(current[i]["qState"] for i in members)
        n, observed = len(members), counts["true"]+counts["false"]
        cohort.update(members=[rows[i]["symbol"] for i in members], eligibleCount=n,
            continuedCount=counts["true"], observedFailureCount=counts["false"], unknownCount=counts["unknown"], observedCount=observed,
            conditionalContinuationRate=ratio(counts["true"], observed), observedCoverage=ratio(observed, n),
            continuationLowerBound=ratio(counts["true"], n), continuationUpperBound=ratio(counts["true"]+counts["unknown"], n))
    return {"price20": price, "amount": amount, "matched5": matched, "yesterdayCohort": cohort,
        "qCounts": {s: sum(r["qState"] == s for r in current) for s in ("true", "false", "unknown")},
        "confirmedStrongEmpty": not any(r["qState"] == "true" for r in current),
        "qUnknownCount": sum(r["qState"] == "unknown" for r in current),
        "quality": quality_from_input(rows)}


def compare_tree(audit, actual, expected, label, **details):
    if isinstance(expected, dict):
        if not audit.check(isinstance(actual, dict) and set(actual) == set(expected), label+":keys", **details):
            return
        for key, value in expected.items():
            compare_tree(audit, actual[key], value, label+"."+key, **details)
    else:
        audit.equal(actual, expected, label, **details)


def exact_json_value(actual, expected):
    """Preserve JSON types in sidecars: true must never compare equal to 1."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(exact_json_value(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(exact_json_value(a, e) for a, e in zip(actual, expected))
    return actual == expected


def audit_rows(output, panel, data, audit):
    document, manifest, protocol, symbols, days, lineage, _ = data
    inputs = manifest["inputs"]
    source = json_records(panel / "features.jsonl.gz")
    roles = json_records(output / "roles.jsonl.gz")
    daily = json_records(output / "daily.jsonl.gz")
    totals, previous, earlier = Counter(), None, deque(maxlen=5)
    role_count, last_notice = 0, time.perf_counter()
    evidence_fields = ("rawPriceBasis", "knownStrengthRankSessions20", "availableStrengthRankSessions20",
        "relativeReturn20CrossSectionCount", "relativeReturn60CrossSectionCount", "knownAmountSessions20")
    for index, day in enumerate(days):
        rows, current = [], []
        old = earlier[0] if len(earlier) == 5 else None
        for ordinal, symbol in enumerate(symbols):
            row = take(source, "input features")
            expected_sources = {**inputs["securities"][symbol], "benchmarkRawBarsSha256": inputs["benchmark"]["rawBarsSha256"]}
            audit.require(row.get("symbol") == symbol and row.get("asOfDate") == day and
                row.get("featureVersion") == SOURCE_VERSION and row.get("sampleManifestHash") == lineage["inputManifestSha256"] and
                row.get("calendarHash") == lineage["calendarHash"] and row.get("sourceHashes") == expected_sources,
                "input-row-identity-order-provenance")
            rows.append(row)
            expected = reference_row(row, days, index, len(symbols))
            current.append(expected)
            role = take(roles, "roles")
            prior = previous[ordinal] if previous is not None else None
            groups = row.get("completeness")
            groups = groups if isinstance(groups, dict) else {}
            evidence = {k: row.get(k) for k in evidence_fields}
            evidence.update(sourceFeatureVersion=row.get("featureVersion"),
                windowComplete={k: window_ok(row, k) for k in GROUP_LENGTHS},
                windowValidSessions={k: groups[k].get("validSessions") if isinstance(groups.get(k), dict) else None
                                     for k in GROUP_LENGTHS},
                fullEvidenceReference={"date": day, "symbol": symbol, "sampleManifestHash": row.get("sampleManifestHash")})
            role_expected = {"date": day, "symbol": symbol, "featureVersion": VERSION,
                "qDefinition": "persistent-price-strength-five-gates-v1", **expected,
                "priorQState": prior["qState"] if prior else None, "previousRecordAvailable": prior is not None,
                "priorCriterionValues": prior["criterionValues"] if prior else None,
                "transition": reference_transition(prior["qState"] if prior else None, expected["qState"]),
                "matched5": {"available": old is not None, "earlierDate": days[index-5] if old is not None else None,
                    "paired": bool(expected["price20"]["evaluable"] and old[ordinal]["evaluable"]) if old is not None else None,
                    "earlier": old[ordinal] if old is not None else None},
                "evidence": evidence, "baseEligibility": row.get("baseEligibility"), "featureReasons": row.get("featureReasons"),
                "asOfEvidence": row.get("asOfEvidence"), "sourceHashes": expected_sources,
                "sampleManifestHash": row["sampleManifestHash"], "certified": False,
                "targetAchieved": False, "actionEvidence": "unverified", **lineage}
            # Exact serialized lineage/sidecars; independently recomputed Q and
            # predicates have the same direct floating comparisons as registered.
            equal_role = exact_json_value(role, role_expected)
            audit.check(equal_role, "full-role-replay", date=day, symbol=symbol,
                        differingKeys=[] if equal_role else [k for k in set(role) | set(role_expected)
                            if not exact_json_value(role.get(k), role_expected.get(k))])
            totals[expected["qState"]] += 1
            role_count += 1
        expected_daily = {"date": day, "featureVersion": VERSION, "fixedPopulation": len(symbols),
            "qDefinition": "persistent-price-strength-five-gates-v1", **reference_day(rows, current, previous, old, days, index),
            "newStrategyTrials": 0, "marketRegime": None, "forwardReturnsCalculated": False,
            "strategyReturnsCalculated": False, "backwardPriceReturnsFromPanel": True, "certified": False,
            "targetAchieved": False, **lineage}
        compare_tree(audit, take(daily, "daily"), expected_daily, "daily-replay", date=day)
        previous = [{"qState": r["qState"], "criterionValues": r["criterionValues"]} for r in current]
        earlier.append([r["price20"] for r in current])
        if time.perf_counter()-last_notice >= 25:
            print(f"Independent market-state audit: {index+1}/{len(days)} days; {role_count} rows", flush=True)
            last_notice = time.perf_counter()
    for iterator, label in ((source, "input features"), (roles, "roles"), (daily, "daily")):
        exhausted(iterator, label)
        audit.check(True, label+":complete-eof")
    summary = read_json(output / "summary.json")
    for name, expected in {"schema": "market-state-observation-summary-v1", "protocolHash": protocol["protocolHash"],
        "roleRows": role_count, "dailyRows": len(days), "qCounts": dict(totals), "returnsCalculated": False,
        "newStrategyTrials": 0, "futureLabelsCreated": False, "certified": False, "targetAchieved": False}.items():
        audit.equal(summary.get(name), expected, "summary:"+name)
    audit.check(role_count == document["sample"]["rows"], "all-fixed-role-rows")
    return {"roleRows": role_count, "dailyRows": len(days), "qCounts": dict(totals)}


def verify(output: Path, panel: Path, root: Path = ROOT):
    """Read and replay one completed output; never writes or starts extraction."""
    started = time.perf_counter()
    output, panel, root = output.resolve(), panel.resolve(), root.resolve()
    audit = Audit()
    report = {"schemaVersion": 1, "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "output": str(output), "inputPanel": str(panel), "roleRows": 0, "dailyRows": 0,
        "verifierSha256": VERIFIER_SOURCE_SHA256,
        "newStrategyTrials": 0, "returnsCalculated": False, "certified": False, "targetAchieved": False,
        "scope": ["Independent market aggregates, full Q population, adjacent role transitions and unchanged sidecars.",
                  "Input/output/source hashes and archive identity, exact source and output row/EOF population.",
                  "Uses the previously audited frozen feature panel; does not rebuild its raw-price features, infer market influence or evaluate trading returns."],
        "limitations": ["A successful small synthetic fixture is not a real-market panel verification.",
                        "Unknown PIT identity, rights and trading-status evidence is retained; no economic-return or execution certification."]}
    try:
        audit.require(sha(VERIFIER_SOURCE_PATH) == VERIFIER_SOURCE_SHA256, "verifier-source-hash-at-start")
        data = binding(output, panel, root, audit)
        report["protocolHash"] = data[2]["protocolHash"]
        report["inputManifestSha256"] = data[5]["inputManifestSha256"]
        report["implementation"] = data[2]["implementation"]
        report["resultFileHashes"] = {name: sha(output / name) for name in OUTPUT_FILES}
        report.update(audit_rows(output, panel, data, audit))
        for path, expected_hash in data[-1].items():
            audit.require(sha(path) == expected_hash, "post-replay-file-hash:" + path.name)
    except Exception as exc:
        audit.check(False, "replay-exception", exception=type(exc).__name__, message=str(exc))
    try:
        audit.check(sha(VERIFIER_SOURCE_PATH) == VERIFIER_SOURCE_SHA256, "verifier-source-hash-at-end")
    except OSError as exc:
        audit.check(False, "verifier-source-hash-at-end", exception=type(exc).__name__, message=str(exc))
    report.update(passed=audit.failures == 0, checks=audit.checks, failureCount=audit.failures,
                  issues=audit.issues, elapsedSeconds=time.perf_counter() - started)
    return report


def save_report(output, report):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = output / "independent-verification.json"
    if not report["passed"] or path.exists():
        status = "success" if report["passed"] else "failed"
        path = output / f"independent-verification-{status}-{stamp}.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = verify(args.output, DEFAULT_PANEL)
    saved = save_report(args.output, result)
    print(json.dumps({"passed": result["passed"], "checks": result["checks"],
        "failureCount": result["failureCount"], "report": str(saved)}, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
