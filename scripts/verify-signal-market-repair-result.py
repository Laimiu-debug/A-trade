"""Independent market-repair/resilience replay and hash-bound outcome audit.

No production market gate, trial runner or event calculator is imported. The
previously audited parent signals and market aggregates are frozen inputs. A
small AST-selected function set from the old independent verifier is reused for
active, price/fee, adverse-path and cohort arithmetic, not full fill eligibility.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict, deque
from copy import deepcopy
from datetime import date, datetime, timezone
import gzip
import hashlib
import importlib
import json
import math
from pathlib import Path
from statistics import median
import sys
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_SOURCE_PATH = Path(__file__).resolve()
VERIFIER_SOURCE_SHA256 = hashlib.sha256(VERIFIER_SOURCE_PATH.read_bytes()).hexdigest()
REGISTRATION = "docs/signal-market-repair-resilience-price-proxy-v1.json"
REGISTRATION_SHA256 = "13ef6bc30ad4d7007cad62c37babadc4e2a24f5f96a35cf7fcbb40cfdedc5a05"
DEFAULT_OUTPUT = ROOT / "artifacts/strategy-search/signal-market-repair-v1"
OLD_AUDITOR = "scripts/verify-signal-persistent-leader-result.py"
OLD_AUDITOR_SHA256 = "3e9a02ba8f48a34cd81af0f30108d94a0a1b30b60396905f185e37f491785222"
SOURCE_HELPER = "scripts/run-strategy-search.py"
SOURCE_HELPER_SHA256 = "6f83a920c7086ba69a98b7fe43e2da5ded52f1b529479edf6347afc8be629750"
PARENT_ID = "rawPersistentPriceLeaderBreakoutDiagnostic"
CANDIDATE_ID = "rawMarketRepairResilientLeaderBreakoutDiagnostic"
GROUPS = (CANDIDATE_ID, "control_" + CANDIDATE_ID)
PROXIES = {"nextOpenNet30", "rawHigh30", "netHigh30"}
HORIZONS = (5, 10, 20, 40, 60, 120, 250)
CONDITIONS = (("price20.coverage", ">=", .9, 0, 1),
    ("matched5.available", "is", True, None, None),
    ("matched5.pairedCoverageFixed", ">=", .9, 0, 1),
    ("matched5.matchedBreadthChange5", "finiteInDomain", None, -100, 100))
SOURCE_VERSION = "market-state-observations-v1"
Q_DEFINITION = "persistent-price-strength-five-gates-v1"
GATE_VERSION = "market-repair-resilience-gate-v1"
ATTEMPT_FILES = {"candidates": "candidates.jsonl.gz", "coverage": "coverage.jsonl.gz", "signals": "signals.jsonl.gz",
    "result": "result.json.gz", "summary": "summary.json", "selections": "selection.jsonl.gz",
    "marketGates": "market-gates.jsonl.gz", "filterDecisions": "filter-decisions.jsonl.gz", "attempt": "attempt.json"}


def unique_pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("Duplicate JSON key: " + key)
        out[key] = value
    return out


def parse(text):
    return json.loads(text, object_pairs_hook=unique_pairs)


def read(path):
    return parse(path.read_text(encoding="utf-8"))


def gz_json(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return parse(stream.read())


def records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield parse(line)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value):
    result = hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False,
                                  separators=(",", ":")).iterencode(value):
        result.update(chunk.encode("utf-8"))
    return result.hexdigest()


def identity(row):
    return row["strategyId"], row["symbol"], row["signalDate"]


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def exact(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(b, dict):
        return a.keys() == b.keys() and all(exact(a[k], v) for k, v in b.items())
    if isinstance(b, list):
        return len(a) == len(b) and all(exact(x, y) for x, y in zip(a, b))
    return a == b


def same(a, b):
    return a is b if a is None or b is None else finite(a) and finite(b) and math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)


class Audit:
    def __init__(self):
        self.checks = self.failure_count = 0
        self.issues, self.bound = [], {}

    def check(self, condition, label, **details):
        self.checks += 1
        if not condition:
            self.failure_count += 1
            if len(self.issues) < 100:
                self.issues.append({"check": label, **details})
        return bool(condition)

    def require(self, condition, label):
        if not self.check(condition, label):
            raise ValueError(label)

    def pin(self, path, expected=None):
        path = path.resolve()
        value = self.bound.get(path)
        if value is None:
            value = sha(path)
            self.bound[path] = value
        self.require(expected is None or value == expected, "bound-file-hash:" + path.name)
        return value


def within(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Missing or escaping path: " + str(name))
    return path


def refs(value):
    if isinstance(value, dict):
        if set(value) == {"path", "sha256"}:
            yield value
        else:
            for item in value.values():
                yield from refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from refs(item)


def archive(audit, path, implementation, root):
    with zipfile.ZipFile(path) as stream:
        audit.require(sorted(stream.namelist()) == sorted(implementation), "archive-exact-member-set")
        for name, expected in implementation.items():
            audit.pin(within(root, name), expected)
            audit.require(hashlib.sha256(stream.read(name)).hexdigest() == expected, "archived-source:" + name)


def ast_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    if {n.name for n in functions} != set(names):
        raise ValueError("Audited helper function set changed")
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def load_dataset(document, parent_protocol, root, audit):
    """Only source loading and the frozen causal cutoff; no event evaluation."""
    helper = within(root, SOURCE_HELPER)
    audit.pin(helper, SOURCE_HELPER_SHA256)
    sys.path.insert(0, str(root))
    universe = importlib.import_module("service.portfolio_universe")
    expected = document["parent"]["sourceImplementation"]["service/portfolio_universe.py"]
    audit.pin(Path(universe.__file__), expected)
    namespace = ast_functions(helper, {"bound_snapshot"}, {"board_of": universe.board_of,
        "_retain_causal_window": universe._retain_causal_window, "_digest": digest})
    selection = read(within(root, document["parent"]["completedInputs"]["selection.json"]["path"]))
    sample = document["sample"]
    loaded = universe.snapshot_universe(Path(parent_protocol["workspace"]), selection,
                                       sample["signalEnd"], sample["signalStart"])
    return namespace["bound_snapshot"](loaded, sample["signalStart"], sample["signalEnd"])


def reference_market(rows, dates, ordinal):
    """Independent six-day domains/signs, with explicit unknown evidence."""
    facts, history, diff = {}, [], {}
    for index, row in zip(range(max(0, ordinal-5), ordinal+1), rows):
        day = dates[index]
        one = {}
        for field, op, threshold, lo, hi in CONDITIONS:
            group, leaf = field.split(".")
            value = row.get(group, {}).get(leaf) if isinstance(row.get(group), dict) else None
            label, reasons, passed = day+":"+field, [], None
            if op == "is":
                if type(value) is bool:
                    passed = value
                    if not value:
                        reasons.append("matchedComparisonUnavailable:"+label)
                else:
                    reasons.append("requiredBooleanUnavailable:"+label)
            elif not finite(value):
                reasons.append("requiredFiniteNumberUnavailable:"+label)
            elif not lo <= value <= hi:
                reasons.append("requiredNumberOutOfRange:"+label)
            else:
                passed = value >= threshold if op == ">=" else True
            fact = {"value": value if type(value) is bool or finite(value) else None, "operator": op,
                "threshold": threshold, "domain": [lo, hi] if lo is not None else None,
                "passed": passed, "unknownReasons": reasons}
            facts[label] = one[field] = fact
        observed = one["matched5.matchedBreadthChange5"]
        diff[index] = observed["value"] if observed["passed"] is True else None
        history.append({"date": day, "targetIndex": index, "featureVersion": SOURCE_VERSION,
            "earlierDate": dates[index-5] if index >= 5 else None, "predicates": one})
    earlier = [diff.get(i) for i in range(ordinal-5, ordinal)]
    negative = sum(x < 0 for x in earlier) if None not in earlier else None
    values = {"priorFiveNegativeCount": (negative, ">=", 3),
              "previousNonPositive": (diff.get(ordinal-1), "<=", 0), "currentPositive": (diff[ordinal], ">", 0)}
    for key, (value, operator, threshold) in values.items():
        passed = None if value is None else (value >= threshold if operator == ">=" else
                    value <= threshold if operator == "<=" else value > threshold)
        facts[key] = {"value": value, "operator": operator, "threshold": threshold, "passed": passed,
            "unknownReasons": ["requiredDifferenceWindowUnavailable:"+key] if value is None else []}
    unknown = {r for f in facts.values() for r in f["unknownReasons"]}
    if ordinal < 10:
        unknown.add("firstTenTargetSessionsUnavailable")
    failed = [key for key, fact in facts.items() if fact["passed"] is False]
    state = "unknown" if unknown else "false" if failed else "true"
    return {"date": dates[ordinal], "targetIndex": ordinal, "version": GATE_VERSION, "candidateId": CANDIDATE_ID,
        "marketGate": state, "newEntryAllowed": state == "true", "predicates": facts,
        "failedPredicates": failed, "unknownReasons": sorted(unknown), "history": history,
        "certified": False, "targetAchieved": False}


def reference_compound(symbol, dates, ordinal, roles, market):
    history, failed, unknown = [], list(market["failedPredicates"]), set(market["unknownReasons"])
    for row in roles:
        state = row["qState"]
        passed = None if state == "unknown" else state == "true"
        reasons = ["requiredQStateUnavailable:"+row["date"]] if passed is None else []
        if passed is False:
            failed.append(row["date"]+":qState")
        unknown.update(reasons)
        history.append({"date": row["date"], "symbol": symbol, "featureVersion": SOURCE_VERSION,
            "qDefinition": Q_DEFINITION, "qState": state, "passed": passed, "unknownReasons": reasons})
    if ordinal < 5:
        unknown.add("firstFiveRoleSessionsUnavailable")
    state = "unknown" if unknown else "false" if failed else "true"
    return {"date": dates[ordinal], "symbol": symbol, "targetIndex": ordinal, "version": GATE_VERSION,
        "candidateId": CANDIDATE_ID, "signalGate": state, "newEntryAllowed": state == "true",
        "marketGate": market["marketGate"], "roleHistory": history, "failedPredicates": failed,
        "unknownReasons": sorted(unknown), "certified": False, "targetAchieved": False}


def bind_sources(root, output, audit):
    document_path = within(root, REGISTRATION)
    audit.pin(document_path, REGISTRATION_SHA256)
    document = read(document_path)
    audit.require(document["candidateId"] == CANDIDATE_ID and document["status"] ==
        "registered-rule-awaiting-reviewed-runner", "registered-one-candidate")
    for ref in refs(document):
        audit.pin(within(root, ref["path"]), ref["sha256"])
    audit.pin(within(root, OLD_AUDITOR), OLD_AUDITOR_SHA256)
    audit.pin(within(root, SOURCE_HELPER), SOURCE_HELPER_SHA256)
    parent = document["parent"]
    for name, expected in parent["sourceImplementation"].items():
        audit.pin(within(root, name), expected)
    parent_protocol = read(within(root, parent["completedInputs"]["protocol.json"]["path"]))
    parent_receipt = read(within(root, parent["completedInputs"]["completion.json"]["path"]))
    parent_audit = read(within(root, parent["completedInputs"]["independent-result-verification.json"]["path"]))
    audit.require(digest(parent_protocol) == parent_receipt["protocolHash"] == parent["protocolHash"] and
        parent_protocol["config"] == document["frozenExecutionConfig"] and
        digest(document["frozenExecutionConfig"]) == document["executionConfigHash"], "parent-config-protocol-binding")
    audit.require(parent_audit["passed"] is True and type(parent_audit["failureCount"]) is int and
        parent_audit["failureCount"] == 0 and parent_audit["evaluatedRows"] == document["sample"]["expectedFeatureRows"] and
        parent_audit["resultSha256"] == parent["completedOutputs"]["result"]["sha256"] and
        parent_audit["verifierSha256"] == OLD_AUDITOR_SHA256, "parent-actual-independent-audit")
    archive(audit, within(root, parent["completedOutputs"]["executionSources"]["path"]), parent["sourceImplementation"], root)
    market = document["marketInput"]
    market_audit = read(within(root, market["actualAudit"]["path"]))
    market_protocol = read(within(root, market["outputs"]["protocol.json"]["path"]))
    audit.require(market_audit["passed"] is True and type(market_audit["failureCount"]) is int and
        market_audit["failureCount"] == 0 and market_audit["dailyRows"] == document["sample"]["expectedScoredSessions"] and
        market_audit["roleRows"] == document["sample"]["expectedFeatureRows"] and
        market_audit["protocolHash"] == market["protocolHash"] == market_protocol["protocolHash"] and
        market_audit["verifierSha256"] == market["verifier"]["sha256"], "market-actual-independent-audit")
    audit.require(market_protocol["protocolHash"] == digest({k: v for k, v in market_protocol.items() if k != "protocolHash"}) and
        market_protocol["executionArchiveSha256"] == market["outputs"]["execution-sources.zip"]["sha256"],
        "market-protocol-and-whole-archive")
    archive(audit, within(root, market["outputs"]["execution-sources.zip"]["path"]), market["implementation"], root)
    return document, parent_protocol, market_protocol


def bind_execution(output, root, document, manifest, audit):
    for path, expected in manifest.get("physicalSourceHashes", {}).items():
        audit.pin(Path(path), expected)
    expected_binding = {"registrationSha256": REGISTRATION_SHA256,
        "inputFilesSha256": {str(p): h for p, h in audit.bound.items()},
        "parentProtocolHash": document["parent"]["protocolHash"], "marketProtocolHash": document["marketInput"]["protocolHash"],
        "snapshotFingerprint": document["sample"]["expectedFullSnapshotFingerprint"],
        "calendarHash": document["originalRawSourcePanelBinding"]["calendarHash"]}
    protocol = read(within(output, "protocol.json"))
    audit.require(protocol["schema"] == "market-repair-development-v1" and protocol["protocolHash"] ==
        digest({k: v for k, v in protocol.items() if k != "protocolHash"}), "execution-protocol-canonical-hash")
    audit.require(exact(protocol["registration"], document) and protocol["registrationSha256"] == REGISTRATION_SHA256 and
        exact(protocol["config"], document["frozenExecutionConfig"]) and exact(protocol["binding"], expected_binding) and
        protocol["strategyOrder"] == [CANDIDATE_ID] and protocol["groups"] == list(GROUPS) and
        protocol["certified"] is False and protocol["targetAchieved"] is False and protocol["pilot"] is False,
        "execution-fixed-inputs-config-and-groups")
    required_names = set(document["parent"]["sourceImplementation"]) | {REGISTRATION,
        "scripts/run-signal-market-repair.py", "service/signal_market_repair.py"}
    audit.require(set(protocol["implementation"]) == required_names, "complete-new-execution-source-set")
    audit.pin(output / "execution-sources.zip", protocol["executionArchiveSha256"])
    archive(audit, output / "execution-sources.zip", protocol["implementation"], root)
    complete = list((output / "attempts").glob("attempt-*/completion.json"))
    audit.require(len(complete) == 1, "one-successful-attempt")
    receipt_path, attempt = complete[0], complete[0].parent
    receipt = read(receipt_path)
    audit.require(attempt.name == "attempt-0001" and sorted(p.name for p in (output / "attempts").iterdir()) == [attempt.name] and
        not (attempt / "failure.json").exists(), "one-immutable-success-no-failure")
    expected_files = {k: "attempts/attempt-0001/" + n for k, n in ATTEMPT_FILES.items()}
    expected_files.update(executionSources="execution-sources.zip", protocol="protocol.json")
    audit.require(receipt["protocolHash"] == protocol["protocolHash"] and receipt["snapshotFingerprint"] ==
        expected_binding["snapshotFingerprint"] and receipt["certified"] is False and receipt["targetAchieved"] is False and
        set(receipt["files"]) == set(expected_files), "complete-new-output-receipt")
    for key, relative in expected_files.items():
        entry = receipt["files"][key]
        audit.require(entry["path"] == relative, "receipt-path:" + key)
        audit.pin(within(output, relative), entry["sha256"])
    audit.pin(receipt_path)
    attempt_meta = read(attempt / "attempt.json")
    audit.require(attempt_meta["protocolHash"] == protocol["protocolHash"] and attempt_meta["candidateId"] == CANDIDATE_ID and
        type(attempt_meta["attempt"]) is int and attempt_meta["attempt"] == 1, "attempt-identity")
    return protocol, receipt, attempt


def replay_filters(document, root, attempt, audit):
    parent, market = document["parent"], document["marketInput"]
    dates = document["originalRawSourcePanelBinding"]["targetDates"]
    population = document["sample"]["expectedFullSecurities"]
    parent_rows = {key: list(records(within(root, parent["completedOutputs"][key]["path"]))) for key in
                   ("candidates", "signals", "selections", "coverage")}
    sources = list(records(within(root, market["outputs"]["daily.jsonl.gz"]["path"])))
    outputs = {key: list(records(attempt / ATTEMPT_FILES[key])) for key in
               ("candidates", "signals", "selections", "coverage", "marketGates", "filterDecisions")}
    audit.require([r["date"] for r in sources] == dates and [r["date"] for r in parent_rows["selections"]] == dates and
        [r["date"] for r in parent_rows["coverage"]] == dates, "complete-parent-market-calendar-and-EOF")
    audit.require(len(parent_rows["signals"]) == len({identity(s) for s in parent_rows["signals"]}) and
        [s["signalDate"] for s in parent_rows["signals"]] == sorted(s["signalDate"] for s in parent_rows["signals"]) and
        set(s["signalDate"] for s in parent_rows["signals"]).issubset(dates), "unique-ordered-parent-signals")
    audit.require(len(parent_rows["candidates"]) == len({identity(s) for s in parent_rows["candidates"]}) and
        [s["signalDate"] for s in parent_rows["candidates"]] == sorted(s["signalDate"] for s in parent_rows["candidates"]) and
        set(s["signalDate"] for s in parent_rows["candidates"]).issubset(dates), "unique-ordered-parent-candidates")
    candidates, parent_signals = defaultdict(list), defaultdict(list)
    for row in parent_rows["candidates"]:
        candidates[row["signalDate"]].append(row)
    for row in parent_rows["signals"]:
        parent_signals[row["signalDate"]].append(row)
    expected = {k: [] for k in outputs}
    totals = {g: Counter() for g in GROUPS}
    gate_counts, candidate_counts = Counter(), Counter()
    mapping = {PARENT_ID: GROUPS[0], "control_" + PARENT_ID: GROUPS[1]}
    raw_binding = document["originalRawSourcePanelBinding"]
    manifest = read(within(root, raw_binding["files"]["manifest.json"]["path"]))
    symbols = manifest["inputs"]["symbols"]
    role_stream = records(within(root, market["outputs"]["roles.jsonl.gz"]["path"]))
    role_history, role_count, unmatched_total = deque(maxlen=5), 0, 0
    for ordinal, day in enumerate(dates):
        row, selection, coverage = sources[ordinal], parent_rows["selections"][ordinal], parent_rows["coverage"][ordinal]
        audit.require(row["featureVersion"] == market["featureVersion"] and type(row["fixedPopulation"]) is int and
            row["fixedPopulation"] == population and row["protocolHash"] == market["protocolHash"] and
            row["calendarHash"] == raw_binding["calendarHash"] and row["inputManifestSha256"] == raw_binding["files"]["manifest.json"]["sha256"] and
            row["inputFeatureFileSha256"] == raw_binding["files"]["features.jsonl.gz"]["sha256"] and
            row["matched5"]["earlierDate"] == (dates[ordinal-5] if ordinal >= 5 else None), "market-row-identity-exact-clock")
        audit.require(ordinal >= 5 or row["matched5"].get("available") is not True,
            "pre-target-market-comparison-cannot-be-available")
        current_roles = {}
        for symbol in symbols:
            role = next(role_stream, None)
            audit.require(isinstance(role, dict) and role.get("date") == day and role.get("symbol") == symbol and
                role.get("featureVersion") == SOURCE_VERSION and role.get("qDefinition") == Q_DEFINITION and
                role.get("qState") in ("true", "false", "unknown") and role.get("protocolHash") == market["protocolHash"] and
                role.get("calendarHash") == raw_binding["calendarHash"] and
                role.get("inputManifestSha256") == role.get("sampleManifestHash") == raw_binding["files"]["manifest.json"]["sha256"] and
                role.get("inputFeatureFileSha256") == raw_binding["files"]["features.jsonl.gz"]["sha256"] and
                exact(role.get("sourceHashes"), {**manifest["inputs"]["securities"][symbol],
                    "benchmarkRawBarsSha256": manifest["inputs"]["benchmark"]["rawBarsSha256"]}),
                "full-role-stream-date-symbol-version-lineage")
            current_roles[symbol] = role
            role_count += 1
        market_window = sources[max(0, ordinal-5):ordinal+1]
        gate = reference_market(market_window, dates, ordinal)
        state = gate["marketGate"]
        gate_counts[state] += 1
        gate_record = {**gate, "sourceMarketRowSha256": digest(row),
            "sourceMarketRowHashes": [{"date": v["date"], "sha256": digest(v)} for v in market_window],
            "marketDailySha256": market["outputs"]["daily.jsonl.gz"]["sha256"]}
        expected["marketGates"].append(gate_record)
        raw, chosen = candidates[day], parent_signals[day]
        audit.require(raw == sorted(raw, key=lambda r: (-r["meanStrengthPercentile20"], -r["relativeReturn60Percentile"], r["symbol"])) and
            all(r["strategyId"] == PARENT_ID and type(r["rank"]) is int and r["rank"] == i+1 and
                r["selectedTop5"] is (i < 5) for i, r in enumerate(raw)), "parent-entire-ranking-before-active")
        top, controls = raw[:5], chosen[len(raw[:5]):]
        rank = [{k: r[k] for k in ("symbol", "rank", "meanStrengthPercentile20", "relativeReturn60Percentile", "selectedTop5")} for r in raw]
        matching = selection["controlMatching"]
        audit.require(exact(chosen[:len(top)], top) and exact(selection["fullRanking"], rank) and
            selection["selectedTop5Symbols"] == [r["symbol"] for r in top] and selection["rawCandidateCount"] == len(raw) and
            selection["activeExclusionApplied"] is False and matching["selectedSymbols"] == [r["symbol"] for r in controls] and
            matching["requestedCount"] == len(top) and matching["matchedCount"] == len(controls) == min(len(top), matching["eligibleControlPoolSize"]) and
            matching["shortfall"] == len(top)-len(controls), "parent-top-five-controls-complete-lineage")
        audit.require(all(r["strategyId"] == "control_"+PARENT_ID and r["pairedStrategyId"] == PARENT_ID and
            type(r["rank"]) is int and r["rank"] == i+1 and r["selectedTop5"] is False for i, r in enumerate(controls)) and
            not {r["symbol"] for r in controls}.intersection(r["symbol"] for r in top) and
            [r["symbol"] for r in controls] == sorted([r["symbol"] for r in controls], key=lambda s:
                (hashlib.sha256(f"a-trade-signal-same-day-base-pool-control-v1:{day}:{PARENT_ID}:{s}".encode()).hexdigest(), s)),
            "parent-control-identity-old-salt-order")
        audit.require(coverage["controlMatching"] == {PARENT_ID: matching} and coverage["candidateCounts"] == {PARENT_ID: len(raw)} and
            coverage["evaluatedSecurities"] == sum(coverage["evaluatedKinds"].values()) == sum(coverage["evaluatedReasons"].values()) == population,
            "parent-coverage-accounting")
        compounds, daily_candidate_counts = {}, Counter()
        for candidate in raw:
            symbol = candidate["symbol"]
            history = [previous[symbol] for previous in role_history]
            compound = reference_compound(symbol, dates, ordinal, history, gate)
            compound.update(sourceRoleRowHashes=[{"date": v["date"], "symbol": symbol, "sha256": digest(v)} for v in history],
                sourceRolesFileSha256=market["outputs"]["roles.jsonl.gz"]["sha256"], sourceMarketGateSha256=digest(gate_record))
            compounds[candidate["rank"]] = compound
            candidate_counts[compound["signalGate"]] += 1
            daily_candidate_counts[compound["signalGate"]] += 1
            expected["candidates"].append({**candidate, "parentStrategyId": candidate["strategyId"], "marketGate": state,
                "signalGate": compound["signalGate"], "compoundGate": compound, "compoundGateSha256": digest(compound),
                "parentCandidateSha256": digest(candidate)})
        paired_by_rank = {s["rank"]: s for s in top}
        available_control_ranks = {s["rank"] for s in controls}
        unmatched = sum(compounds[k]["signalGate"] == "true" and k not in available_control_ranks for k in paired_by_rank)
        unmatched_total += unmatched
        admitted_today = 0
        for source in chosen:
            old, new = source["strategyId"], mapping[source["strategyId"]]
            paired, compound = paired_by_rank[source["rank"]], compounds[source["rank"]]
            signal_state = compound["signalGate"]
            lineage = {"parentStrategyId": old, "sourceSignalKey": list(identity(source)),
                "parentSignalSha256": digest(source), "parentSignalsFileSha256": parent["completedOutputs"]["signals"]["sha256"],
                "pairedParentSignalKey": [PARENT_ID, paired["symbol"], day], "pairedParentRank": paired["rank"],
                "strategyGate": compound, "strategyGateSha256": digest(compound)}
            expected["filterDecisions"].append({"parentSignal": source, **lineage, "newStrategyId": new, "marketGate": state,
                "signalGate": signal_state, "gateReasons": compound["unknownReasons"], "failedPredicates": compound["failedPredicates"],
                "failedMarketPredicates": gate["failedPredicates"], "admitted": signal_state == "true"})
            totals[new]["parentSignals"] += 1
            totals[new][{"true": "gateTrueAdmitted", "false": "gateFalseFiltered", "unknown": "gateUnknownFiltered"}[signal_state]] += 1
            if signal_state == "true":
                admitted_today += 1
                signal = {**source, **lineage, "strategyId": new, "marketGate": state, "signalGate": signal_state}
                if "pairedStrategyId" in signal:
                    signal["pairedStrategyId"] = CANDIDATE_ID
                expected["signals"].append(signal)
        expected["selections"].append({**selection, "marketGate": state, "gateReasons": gate["unknownReasons"],
            "failedMarketPredicates": gate["failedPredicates"], "parentSelectionSha256": digest(selection),
            "strategyGates": [{"rank": s["rank"], "symbol": s["symbol"], "signalGate": compounds[s["rank"]]["signalGate"],
                "strategyGateSha256": digest(compounds[s["rank"]])} for s in top], "unmatchedTrueStrategySlots": unmatched})
        expected["coverage"].append({**coverage, "marketGate": state, "admittedSignals": admitted_today,
            "parentCoverageSha256": digest(coverage), "candidateGateCounts": {s: daily_candidate_counts[s] for s in ("true", "false", "unknown")},
            "sourceRoleRows": len(current_roles), "unmatchedTrueStrategySlots": unmatched})
        role_history.append(current_roles)
    audit.require(next(role_stream, None) is None and role_count == len(dates)*len(symbols), "full-role-stream-exact-population-EOF")
    for key in outputs:
        audit.require(len(outputs[key]) == len(expected[key]), "complete-output-rows-and-EOF:" + key)
        for i, (actual, exp) in enumerate(zip(outputs[key], expected[key])):
            audit.check(exact(actual, exp), "independent-" + key + "-row", rowIndex=i)
    accounting = {g: {k: totals[g][k] for k in ("parentSignals", "gateTrueAdmitted", "gateFalseFiltered", "gateUnknownFiltered")}
                  for g in GROUPS}
    return expected["signals"], accounting, {k: gate_counts[k] for k in ("true", "false", "unknown")}, len(parent_rows["signals"]), {
        "candidateGateCounts": {s: candidate_counts[s] for s in ("true", "false", "unknown")},
        "sourceRoleRows": role_count, "unmatchedTrueStrategySlots": unmatched_total}


def event_audit(audit, result, signals, dataset, document, root):
    dates = document["originalRawSourcePanelBinding"]["targetDates"]
    index = {d: i for i, d in enumerate(dates)}
    calendar = dataset["calendar"]
    ci = {d: i for i, d in enumerate(calendar)}
    securities = {s["symbol"]: s for s in dataset["securities"]}
    raw = {s: {b["date"]: b for b in security["bars"]} for s, security in securities.items()}
    config = document["frozenExecutionConfig"]
    audit.require(len(signals) == len({identity(s) for s in signals}), "unique-admitted-signals")
    outcomes = result["events"] + result["overlapSuppressed"]
    audit.require(Counter(map(identity, outcomes)) == Counter(map(identity, signals)), "exact-all-event-dispositions")
    expected = {identity(s): s for s in signals}
    for event in result["events"]:
        audit.check(exact(event["signal"], expected[identity(event)]) and
            event["eventId"] == digest(list(identity(event)))[:24], "event-source-signal-and-new-id")
        audit.require(set(event["outcomes"]) == PROXIES, "complete-event-proxy-set")
    for row in result["overlapSuppressed"]:
        audit.check(exact({k: v for k, v in row.items() if k not in
            ("reason", "blockedByEventId", "activeUntil")}, expected[identity(row)]) and
            row["reason"] == "same-symbol-same-group-active-event", "suppressed-original-signal")
    for group in result["groups"]:
        selected = [s for s in signals if s["strategyId"] == group["strategyId"]]
        events = [e for e in result["events"] if e["strategyId"] == group["strategyId"]]
        filled = [e for e in events if e["entry"]["status"] == "filled"]
        audit.check(group["rawSelectedSignals"] == len(selected) and group["signalCount"] == len(events) and
            group["overlapSuppressedCount"] == len(selected)-len(events) and
            group["distinctSignalSymbols"] == len({s["symbol"] for s in selected}) and
            group["distinctSignalDates"] == len({s["signalDate"] for s in selected}), "group-all-signal-counts")
        audit.check(group["entryUnfilledReasons"] == dict(Counter(e["entry"].get("reason", "unknown") for e in events
            if e["entry"]["status"] != "filled")) and same(group["entryFillRate"], len(filled)/len(events) if events else None),
            "group-unfilled-reasons-and-rate")
        audit.check(group["rightCensoredEventCount"] == sum(e["outcomes"]["nextOpenNet30"]["hitDate"] is None for e in filled),
                    "group-right-censored-count")
        for h in group["horizons"]:
            audit.require(set(h["proxies"]) == PROXIES, "complete-horizon-proxy-set")
    # Only these three function definitions are executed, with explicit stdlib
    # globals. The old module's imports/main/source loader are never executed.
    namespace = ast_functions(within(root, OLD_AUDITOR), {"audit_outcomes", "same", "identity"},
        {"Counter": Counter, "median": median, "math": math, "CANDIDATE_ID": CANDIDATE_ID, "HORIZONS": HORIZONS})
    namespace["audit_outcomes"](audit.check, result, signals, dates, index, calendar, ci, raw, securities, config)
    # Rebuild raw/net high observations without treating them as executable
    # profits. This uses the recorded main opening exit as the censoring bound;
    # it does not establish that the recorded opening exit was the first fill.
    for event in result["events"]:
        entry = event["entry"]
        if entry["status"] != "filled":
            continue
        start = ci[entry["date"]]
        end = event["outcomes"]["nextOpenNet30"]["hitDate"]
        gap_dates, halted_dates, first = [], [], {"rawHigh30": None, "netHigh30": None}
        for offset, day in enumerate(calendar[start:], 1):
            halted = securities[event["symbol"]].get("tradeStatusByDate", {}).get(day) in (False, 0, "0")
            bar = raw[event["symbol"]].get(day)
            if halted:
                halted_dates.append(day)
            elif bar is None:
                gap_dates.append({"date": day, "tradingDay": offset})
            if end and day > end or bar is None or halted or bar["volume"] <= 0:
                continue
            price = bar["open"] if day == end else bar["high"]
            if first["rawHigh30"] is None and price / entry["price"] - 1 >= .3 - 1e-12:
                first["rawHigh30"] = (day, offset)
            if offset > 1:
                amount = entry["quantity"] * (price * .999)
                tax = .001 if day < "2023-08-28" else .0005
                fee = max(5, amount*.0003) + amount*.00001 + amount*tax
                if first["netHigh30"] is None and ((amount-fee)/entry["investedCapital"]-1)*100 >= 30-1e-10:
                    first["netHigh30"] = (day, offset)
        observation = event["observation"]
        audit.check(observation["availableTradingDays"] == len(calendar)-start and
            observation["firstDate"] == entry["date"] and observation["lastDate"] == calendar[-1] and
            observation["missingDates"] == gap_dates and observation["knownSuspensionDates"] == halted_dates and
            observation["knownSuspensionDays"] == len(halted_dates), "independent-full-potential-calendar-observation")
        for proxy, target in first.items():
            value = event["outcomes"][proxy]
            audit.check((value["hitDate"], value["tradingDays"]) == (target if target else (None, None)),
                        "independent-high-proxy-first-observation", eventId=event["eventId"], proxy=proxy)
        for proxy, value in event["outcomes"].items():
            target = value["hitDate"]
            audit.check(value["status"] == ("observedProxyHit" if target else "rightCensored") and
                value["calendarDays"] == ((date.fromisoformat(target)-date.fromisoformat(entry["date"])).days+1 if target else None),
                "event-target-status-calendar-days", proxy=proxy)
    audit.check(result["eventCount"] == len(result["events"]) and
        result["overlapSuppressedCount"] == len(result["overlapSuppressed"]), "result-event-counts")
    return {"events": len(result["events"]), "filledEvents": sum(e["entry"]["status"] == "filled" for e in result["events"])}


def verify(output: Path, root: Path = ROOT, *, dataset=None):
    """Read-only audit; dataset injection is for already hash-bound synthetic fixtures."""
    started, audit = time.perf_counter(), Audit()
    output, root = output.resolve(), root.resolve()
    report = {"schemaVersion": 1, "checkedAt": datetime.now(timezone.utc).isoformat(), "output": str(output),
        "verifierSha256": VERIFIER_SOURCE_SHA256, "dailyRows": 0, "parentSignals": 0, "admittedSignals": 0,
        "certified": False, "targetAchieved": False,
        "scope": ["Independent six-session market repair and five-session role resilience on the full registered market/role streams; exact parent pre-active population/ranks, original-rank mirrored controls, group mapping and all filtered/admitted logs.",
            "Complete signal/event/suppression sets, fresh active replay, event lineage, next-session entry, filled price/fee/net-target arithmetic, raw/net-high observation timing, adverse paths and fixed-horizon cohort arithmetic.",
            "AST-selected audit_outcomes/same/identity from the hash-bound earlier independent verifier; its production imports/main are not executed. Hash-bound source loader and causal bound_snapshot only.",
            "Registered files, complete output receipt/ZIP, full raw dataset content, source files and this verifier checked before/after; read-only and no strategy reevaluation."],
        "limitations": ["Trusts previously independently audited parent candidate/control selection and market aggregates; does not rebuild all historical ranks or the original eligible control pool.",
            "Does not independently reconstruct every buy/sell eligibility or opening-capacity gate, maximal order sizing, the first executable net opening target, or detect every omitted opening target. High/path audits use the recorded main exit boundary.",
            "Full-calendar quality and cohort arithmetic are checked, but recorded execution-valid/capacity evidence is not an auction execution certificate. Historical identity, rights, survivor coverage and dependent development events remain unverified.",
            "Synthetic audit success is not actual-market verification, independent holdout evidence or a future net +30 probability."]}
    try:
        audit.require(sha(VERIFIER_SOURCE_PATH) == VERIFIER_SOURCE_SHA256, "verifier-source-at-start")
        document, parent_protocol, _ = bind_sources(root, output, audit)
        manifest = read(within(root, document["originalRawSourcePanelBinding"]["files"]["manifest.json"]["path"]))
        protocol, receipt, attempt = bind_execution(output, root, document, manifest, audit)
        report.update(protocolHash=protocol["protocolHash"], implementation=protocol["implementation"],
            resultSha256=receipt["files"]["result"]["sha256"],
            reusedAudit={"path": OLD_AUDITOR, "sha256": OLD_AUDITOR_SHA256,
                         "functions": ["audit_outcomes", "same", "identity"]})
        if dataset is None:
            dataset = load_dataset(document, protocol, root, audit)
        sample, inputs = document["sample"], manifest["inputs"]
        dates = document["originalRawSourcePanelBinding"]["targetDates"]
        audit.require(dataset["fingerprint"] == sample["expectedFullSnapshotFingerprint"] and
            digest(dataset) == inputs["fullDatasetContentSha256"] and
            sorted(s["symbol"] for s in dataset["securities"]) == inputs["symbols"] and
            len(dataset["securities"]) == sample["expectedFullSecurities"] and
            sum(len(s["bars"]) for s in dataset["securities"]) == sample["expectedFullBars"] and
            dataset["calendar"] == sorted(set(dataset["calendar"])) and
            all(d <= sample["signalEnd"] for d in dataset["calendar"]) and
            [d for d in dataset["calendar"] if sample["warmupStart"] <= d <= sample["signalEnd"]] == inputs["calendar"]["dates"] and
            [d for d in dataset["calendar"] if sample["signalStart"] <= d <= sample["signalEnd"]] == dates,
            "full-raw-dataset-content-calendar-and-population")
        signals, accounting, gates, parent_count, extras = replay_filters(document, root, attempt, audit)
        result, summary = gz_json(attempt / "result.json.gz"), read(attempt / "summary.json")
        audit.require(exact({k: v for k, v in summary.items() if k not in ("performance", "ranking", "selectionEligible")},
            {k: v for k, v in result.items() if k not in ("events", "overlapSuppressed", "pathRiskEvents")}), "exact-summary-result-match")
        audit.require(result["protocolHash"] == protocol["protocolHash"] and result["snapshotFingerprint"] == dataset["fingerprint"] and
            result["parentProtocolHash"] == document["parent"]["protocolHash"] and result["strategyOrder"] == [CANDIDATE_ID] and
            result["certified"] is False and result["targetAchieved"] is False and result["pilot"] is False and
            summary["ranking"] is None and summary["selectionEligible"] is False, "result-identity-and-no-certification")
        for group in GROUPS:
            accounting[group].update(newAttemptedEvents=sum(e["strategyId"] == group for e in result["events"]),
                newActiveSuppressed=sum(e["strategyId"] == group for e in result["overlapSuppressed"]))
            counts = accounting[group]
            audit.check(counts["parentSignals"] == sum(counts[k] for k in
                ("gateTrueAdmitted", "gateFalseFiltered", "gateUnknownFiltered")) and
                counts["gateTrueAdmitted"] == counts["newAttemptedEvents"] + counts["newActiveSuppressed"], "full-filter-disposition-accounting")
        audit.check(exact(accounting, result["filterAccounting"]) and exact(gates, result["marketGateCounts"]) and
            exact(summary["performance"]["filterAccounting"], accounting) and
            exact(summary["performance"]["marketGateCounts"], gates) and
            summary["performance"]["scoredSessions"] == len(dates), "all-market-counts-and-signal-accounting")
        for key, value in extras.items():
            audit.check(exact(result[key], value) and exact(summary["performance"][key], value), "full-role-and-composite-accounting:"+key)
        report.update(event_audit(audit, result, signals, dataset, document, root))
        report.update(dailyRows=len(dates), parentSignals=parent_count, admittedSignals=len(signals), marketGateCounts=gates,
            sourceFingerprint=dataset["fingerprint"], resultFileHashes={k: v["sha256"] for k, v in receipt["files"].items()}, **extras)
        for path, expected_hash in audit.bound.items():
            audit.require(sha(path) == expected_hash, "post-audit-hash:" + path.name)
    except Exception as exc:
        audit.check(False, "audit-exception", exception=type(exc).__name__, message=str(exc))
    try:
        audit.check(sha(VERIFIER_SOURCE_PATH) == VERIFIER_SOURCE_SHA256, "verifier-source-at-end")
    except OSError as exc:
        audit.check(False, "verifier-source-at-end", exception=type(exc).__name__, message=str(exc))
    report.update(passed=audit.failure_count == 0, checks=audit.checks, failureCount=audit.failure_count,
        issues=audit.issues, elapsedSeconds=time.perf_counter()-started)
    return report


def save_report(output, report):
    path = output / "independent-result-verification.json"
    if not report["passed"] or path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = output / ("independent-result-verification-" + ("success-" if report["passed"] else "failed-") + stamp + ".json")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = verify(args.output)
    path = save_report(args.output, report)
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "failureCount": report["failureCount"],
                      "report": str(path)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
