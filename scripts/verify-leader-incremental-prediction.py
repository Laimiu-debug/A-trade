"""Independent raw/fit/forecast audit for the account-free leader diagnostic.

Only the old frozen raw-bar validity class is shared. Returns, peer aggregation,
normal equations, a pivoted stdlib solver, forecasting and all losses below are
independent of the new builder. Real execution is explicitly orchestrated.
"""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone, timedelta
import argparse
import gc
import gzip
import hashlib
import importlib.metadata
import importlib.util
import json
import math
from pathlib import Path
import sys
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/leader-incremental-prediction-v1"
FAMILY = "leader-incremental-prediction-v1"
DEPENDENCIES = {"service/research_leader_lag.py": "1c6168e7b56af0bff90dcda665ab88e9d4756f5da1b82d8b7b76b1e14bf1fc9e"}
FEATURES = ("own", "peerMean", "index", "anchor")
MODELS = ("baseline", "augmented")
DOMAINS = {
    "fitStatus": ("known", "fitUnknown"),
    "featureStatus": ("known", "featureUnknown", "forecastBeyondEnd"),
    "predictionStatus": ("known", "fitUnknown", "featureUnknown", "forecastBeyondEnd", "numericUnknown"),
    "targetStatus": ("known", "targetUnknown", "targetBeyondEnd"),
    "scoreStatus": ("forecastBeyondEnd", "fitUnknown", "featureUnknown", "targetBeyondEnd", "targetUnknown", "numericUnknown", "scored"),
}
BASIS = {
    "population": "All parent formations retained; only formed groups create all20 original members, without amount/Top5/breakout filters",
    "return": "Simple decimal close(d)/close(d-1)-1; frozen _Series exact raw OHLCV/envelope/status validity",
    "training": "60 feature slots F-60..F-1, next-slot member targets through F, requiring62 price slots F-61..F; all60 complete jointly",
    "peerMean": "math.fsum of all other19 fixed members divided by19; self and anchor excluded; no missing-peer deletion",
    "standardization": "Training-only arithmetic mean and population std /60; exact constant columns mapped to0 in training and prediction; y unscaled",
    "ridge": "sum squared errors + 1*sum squared slopes; unpenalized intercept=yMean; numpy.linalg.solve(Z.T@Z+I, Z.T@(y-yMean))",
    "prediction": "Frozen F models, features known at d=A..A+4, target d+1; common four-feature support; no refitting",
    "cutoff": "Dates beyond registered end are null planned slots; no numerical bars after end; known prediction survives unknown/censored target",
    "errors": "Only common scored rows; augmented-minus-baseline MSE/MAE deltas; fixed zero forecast descriptive benchmark",
    "scope": "Conditional selected-event development diagnostic, no account, trading-profit, causal leadership or holdout claim",
}
_SERIES = None


def require(value, message):
    if not value:
        raise AssertionError(message)


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def day(value):
    require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value, "Invalid ISO session")
    return value


def sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def relative(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def ref(path):
    return {"path": relative(path), "sha256": sha(path)}


def bound(reference):
    require(isinstance(reference, dict) and isinstance(reference.get("path"), str), "Missing artifact ref")
    path = (ROOT / reference["path"]).resolve()
    require(path.is_relative_to(ROOT) and path.is_file(), "Missing/outside artifact: " + reference["path"])
    require(sha(path) == reference.get("sha256"), "Artifact hash mismatch: " + reference["path"])
    if "bytes" in reference:
        require(path.stat().st_size == reference["bytes"], "Artifact size mismatch")
    return path


def read(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def records(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def same(actual, expected, context="root"):
    if type(expected) is float:
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-12),
                f"{context}: numeric mismatch {actual!r} != {expected!r}")
    elif isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), f"{context}: key mismatch")
        for key, value in expected.items():
            same(actual[key], value, context + "." + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), f"{context}: list length mismatch")
        for i, (left, right) in enumerate(zip(actual, expected)):
            same(left, right, f"{context}[{i}]")
    else:
        require(type(actual) is type(expected) and actual == expected, f"{context}: literal mismatch {actual!r} != {expected!r}")


def raw_series():
    global _SERIES
    if _SERIES is None:
        path = ROOT / "service/research_leader_lag.py"
        require(sha(path) == DEPENDENCIES[relative(path)], "Frozen raw-validity dependency changed")
        spec = importlib.util.spec_from_file_location("independent_incremental_raw_validity", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _SERIES = module._Series
    return _SERIES


def solve_reference(matrix, rhs):
    """Partial-pivot elimination, not numpy and not the production solver."""
    size = len(rhs)
    require(size and len(matrix) == size and all(len(row) == size for row in matrix), "Square system required")
    work = [list(row) + [value] for row, value in zip(matrix, rhs)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(work[row][col]))
        work[col], work[pivot] = work[pivot], work[col]
        if not finite(work[col][col]) or work[col][col] == 0:
            raise ArithmeticError("Singular/nonfinite system")
        for row in range(col + 1, size):
            factor = work[row][col] / work[col][col]
            work[row][col] = 0.0
            for j in range(col + 1, size + 1):
                work[row][j] -= factor * work[col][j]
    solution = [0.0] * size
    for row in range(size - 1, -1, -1):
        solution[row] = (work[row][-1] - math.fsum(work[row][j] * solution[j]
                                                for j in range(row + 1, size))) / work[row][row]
    if not all(finite(value) for value in solution):
        raise ArithmeticError("Nonfinite solution")
    return solution


def ridge_reference(x, y, names):
    require(len(x) == len(y) == 60 and len(names) in (3, 4), "Exactly 60 fixed observations required")
    require(all(len(row) == len(names) and all(finite(v) for v in row) for row in x)
            and all(finite(v) for v in y), "Finite training values required")
    means, scales, constants = [], [], []
    for j, name in enumerate(names):
        column = [row[j] for row in x]
        constant = min(column) == max(column)
        mean = column[0] if constant else math.fsum(column) / 60
        scale = 0.0 if constant else math.sqrt(math.fsum((v - mean) ** 2 for v in column) / 60)
        if not finite(mean) or not finite(scale) or (not constant and scale == 0):
            raise ArithmeticError("Invalid moments")
        means.append(mean); scales.append(scale)
        if constant:
            constants.append(name)
    z = [[0.0 if scales[j] == 0 else (row[j] - means[j]) / scales[j]
          for j in range(len(names))] for row in x]
    intercept = y[0] if min(y) == max(y) else math.fsum(y) / 60
    columns = list(zip(*z))
    matrix = [[math.fsum(a * b for a, b in zip(left, right)) + float(i == j)
               for j, right in enumerate(columns)] for i, left in enumerate(columns)]
    rhs = [math.fsum(a * (b - intercept) for a, b in zip(column, y)) for column in columns]
    if not all(finite(v) for v in [intercept, *rhs, *(v for row in matrix for v in row)]):
        raise ArithmeticError("Invalid equations")
    return {"featureNames": list(names), "means": means, "scales": scales,
            "constantColumns": constants, "beta": solve_reference(matrix, rhs), "intercept": intercept,
            "normalMatrix": matrix, "rhs": rhs}


def prediction_reference(model, values):
    terms = [b * (0.0 if scale == 0 else (values[name] - center) / scale)
             for name, center, scale, b in zip(model["featureNames"], model["means"], model["scales"], model["beta"])]
    result = model["intercept"] + math.fsum(terms)
    if not finite(result):
        raise ArithmeticError("Nonfinite prediction")
    return result


def return_reference(series, calendar, at):
    if at <= 0:
        return None, ["insufficientCalendarHistory"]
    if at >= len(calendar):
        return None, ["returnBeyondEnd"]
    reasons = [f"{calendar[i]}:{series.reasons[i]}" for i in range(at - 1, at + 1) if series.reasons[i]]
    if reasons:
        return None, reasons
    ratio = series.bars[at]["close"] / series.bars[at - 1]["close"]
    value = ratio - 1
    return (value, []) if finite(ratio) and ratio > 0 and finite(value) else (None, ["nonfiniteOrUnderflowReturn"])


def source_reference(dataset, formations, start, end):
    day(start); day(end)
    require(start <= end and dataset.get("priceBasisId") == "raw-unadjusted", "Raw ordered source required")
    supplied = dataset.get("calendar")
    require(isinstance(supplied, list) and supplied and supplied == sorted(set(supplied)), "Invalid calendar")
    for value in supplied:
        day(value)
    calendar = [d for d in supplied if d <= end]
    targets = [d for d in calendar if start <= d <= end]
    require(targets, "Empty target calendar")
    require(isinstance(dataset.get("securities"), list), "Missing securities")
    securities = {}
    for security in dataset["securities"]:
        require(isinstance(security, dict), "Invalid security")
        symbol = security.get("symbol")
        require(isinstance(symbol, str) and symbol and symbol not in securities, "Duplicate/invalid symbol")
        securities[symbol] = security
    benchmark = dataset.get("benchmark")
    require(isinstance(benchmark, dict) and benchmark.get("symbol") == "sh000300", "Incorrect benchmark")
    require(isinstance(formations, list), "Ordered parent formations required")
    indexes = {d: i for i, d in enumerate(calendar)}
    saved, ids, slots, last = [], set(), set(), None
    for row in formations:
        require(isinstance(row, dict), "Invalid parent record")
        a = day(row.get("formationDate"))
        if a > end:
            continue
        anchor, rank, identity = row.get("anchorSymbol"), row.get("originalLeaderRank"), row.get("parentEventId")
        require(start <= a <= end and a in indexes and anchor in securities and type(rank) is int and 1 <= rank <= 5,
                "Invalid parent identity")
        key = (a, rank, anchor)
        require(identity == f"leaderLagFollowersV1:{a}:{anchor}" and identity not in ids and (a, rank) not in slots
                and (last is None or key > last), "Duplicate or unordered parent")
        require(indexes[a] >= 10 and row.get("peerFormationDate") == calendar[indexes[a] - 10], "F != A-10")
        members, status = row.get("members"), row.get("status")
        require(status in ("formed", "locked", "notFormed") and isinstance(members, list) and len(members) <= 20,
                "Invalid formation state")
        require(status != "formed" or (row.get("groupKind") == "formed" and len(members) == 20), "Formed requires 20 peers")
        seen = set()
        for i, member in enumerate(members, 1):
            symbol = member.get("symbol")
            require(symbol in securities and symbol != anchor and symbol not in seen
                    and type(member.get("frozenMemberRank")) is int and member["frozenMemberRank"] == i, "Invalid fixed peer order")
            seen.add(symbol)
        saved.append(deepcopy(row)); ids.add(identity); slots.add((a, rank)); last = key
    needed = {r["anchorSymbol"] for r in saved if r["status"] == "formed"}
    needed.update(m["symbol"] for r in saved if r["status"] == "formed" for m in r["members"])
    cls = raw_series()
    series = {symbol: cls(securities[symbol], calendar, end) for symbol in sorted(needed)}
    series[None] = cls(benchmark, calendar, end)
    return calendar, targets, saved, series


def empty_errors():
    return {**{m: {"squared": None, "absolute": None} for m in (*MODELS, "zero")}, "mseDelta": None, "maeDelta": None}


def loss_reference(predictions, actual):
    errors = {name: {"squared": (value - actual) ** 2, "absolute": abs(value - actual)}
              for name, value in [*predictions.items(), ("zero", 0.0)]}
    errors.update(mseDelta=errors["augmented"]["squared"] - errors["baseline"]["squared"],
                  maeDelta=errors["augmented"]["absolute"] - errors["baseline"]["absolute"])
    if not all(finite(value) for name in (*MODELS, "zero") for value in errors[name].values()) or not all(
            finite(errors[name]) for name in ("mseDelta", "maeDelta")):
        raise ArithmeticError("Nonfinite score")
    return errors


def reference_build(dataset, formations, *, start, end):
    calendar, targets, parents, series = source_reference(dataset, formations, start, end)
    indexes = {d: i for i, d in enumerate(calendar)}
    date_at = lambda i: calendar[i] if 0 <= i < len(calendar) else None
    returns = {}

    def ret(symbol, i):
        key = symbol, i
        if key not in returns:
            returns[key] = return_reference(series[symbol], calendar, i)
        return returns[key]

    def feature(member, peers, anchor, i):
        values, reasons = {}, []
        for name, symbol in (("own", member), ("index", None), ("anchor", anchor)):
            values[name], errors = ret(symbol, i)
            reasons += [name + ":" + error for error in errors]
        other = [symbol for symbol in peers if symbol != member]
        require(len(other) == 19 and anchor not in other, "Fixed19 support lost")
        peer_values, peer_errors = [], []
        for symbol in other:
            value, errors = ret(symbol, i)
            peer_values.append(value)
            peer_errors += ["peer:" + symbol + ":" + error for error in errors]
        mean = None
        if not peer_errors:
            try:
                mean = math.fsum(peer_values) / 19
                if not finite(mean):
                    raise ArithmeticError("Nonfinite peer mean")
            except (ArithmeticError, ValueError):
                mean = None
                peer_errors.append("nonfinitePeerMean")
        values["peerMean"] = mean
        return {name: values[name] for name in FEATURES}, reasons + peer_errors

    fits, rows = [], []
    for event in parents:
        if event["status"] != "formed":
            continue
        a, f = indexes[event["formationDate"]], indexes[event["peerFormationDate"]]
        peers = [m["symbol"] for m in event["members"]]
        for member in event["members"]:
            symbol = member["symbol"]
            identity = {name: event[name] for name in ("parentEventId", "formationDate", "peerFormationDate", "anchorSymbol", "originalLeaderRank")}
            identity.update(memberSymbol=symbol, frozenMemberRank=member["frozenMemberRank"], fitId=event["parentEventId"] + ":" + symbol)
            training = []
            for i in range(f - 60, f):
                features, issues = feature(symbol, peers, event["anchorSymbol"], i)
                target, errors = ret(symbol, i + 1)
                reasons = ["feature:" + x for x in issues] + ["target:" + x for x in errors]
                training.append({"featureDate": date_at(i), "targetDate": date_at(i + 1), "features": features,
                                 "actualReturn": target, "status": "unknown" if reasons else "known", "reasons": reasons})
            known = sum(p["status"] == "known" for p in training)
            fit = {**identity, "trainingFeatureDates": [date_at(i) for i in range(f - 60, f)],
                   "trainingTargetDates": [date_at(i) for i in range(f - 59, f + 1)],
                   "trainingPriceDates": [date_at(i) for i in range(f - 61, f + 1)],
                   "trainingPairs": training, "knownPairs": known, "fitStatus": "fitUnknown",
                   "reasons": [], "models": {mode: None for mode in MODELS}}
            if known < 60:
                fit["reasons"] = ["incompleteTrainingPairs"]
            else:
                try:
                    models = {}
                    for mode, names in (("baseline", FEATURES[:3]), ("augmented", FEATURES)):
                        models[mode] = ridge_reference([[p["features"][name] for name in names] for p in training],
                                                      [p["actualReturn"] for p in training], names)
                    fit.update(fitStatus="known", models=models)
                except (ArithmeticError, ValueError):
                    fit["reasons"] = ["numericFitFailure"]
            fits.append(fit)
            for offset in range(5):
                i = a + offset
                forecast_date, target_date = date_at(i), date_at(i + 1)
                if forecast_date is None:
                    values, reasons = {name: None for name in FEATURES}, ["forecastBeyondEnd"]
                else:
                    values, reasons = feature(symbol, peers, event["anchorSymbol"], i)
                target, target_reasons = (None, ["targetBeyondEnd"]) if target_date is None else ret(symbol, i + 1)
                feature_status = "forecastBeyondEnd" if forecast_date is None else "featureUnknown" if reasons else "known"
                target_status = "targetBeyondEnd" if target_date is None else "targetUnknown" if target_reasons else "known"
                prediction_status = next((s for ok, s in ((forecast_date is None, "forecastBeyondEnd"),
                    (fit["fitStatus"] != "known", "fitUnknown"), (bool(reasons), "featureUnknown")) if ok), "known")
                predictions, prediction_reasons = dict.fromkeys(MODELS), []
                if prediction_status == "known":
                    try:
                        predictions = {mode: prediction_reference(fit["models"][mode], values) for mode in MODELS}
                    except (ArithmeticError, ValueError):
                        predictions = dict.fromkeys(MODELS)
                        prediction_status, prediction_reasons = "numericUnknown", ["nonfinitePrediction"]
                checks = ((forecast_date is None, "forecastBeyondEnd"), (fit["fitStatus"] != "known", "fitUnknown"),
                          (bool(reasons), "featureUnknown"), (target_date is None, "targetBeyondEnd"),
                          (bool(target_reasons), "targetUnknown"), (prediction_status != "known", "numericUnknown"))
                score = next((state for ok, state in checks if ok), "scored")
                errors = empty_errors()
                if score == "scored":
                    try:
                        errors = loss_reference(predictions, target)
                    except (ArithmeticError, ValueError):
                        score = "numericUnknown"
                        prediction_reasons.append("nonfiniteScore")
                rows.append({**identity, "rowId": identity["fitId"] + ":" + str(offset), "offset": offset,
                             "forecastDate": forecast_date, "targetDate": target_date, "fitStatus": fit["fitStatus"],
                             "featureStatus": feature_status, "predictionStatus": prediction_status, "targetStatus": target_status,
                             "scoreStatus": score, "features": values, "featureReasons": reasons,
                             "predictionReasons": prediction_reasons, "targetReasons": list(target_reasons),
                             "actualReturn": target, "predictions": predictions, "errors": errors,
                             "crosses20240924": forecast_date is not None and target_date is not None
                                and forecast_date < "2024-09-24" <= target_date})
    states = Counter(row["status"] for row in parents)
    counts = {"formations": len(parents), "formationStatusCounts": {s: states[s] for s in ("formed", "locked", "notFormed")},
              "fits": len(fits), "plannedRows": len(rows),
              "fitStatusCounts": {s: sum(r["fitStatus"] == s for r in fits) for s in DOMAINS["fitStatus"]},
              "rowStatusCounts": {field: {s: sum(r[field] == s for r in rows) for s in states} for field, states in DOMAINS.items()},
              "unassignedForecastDates": sum(r["forecastDate"] is None for r in rows),
              "crosses20240924": sum(r["crosses20240924"] for r in rows)}
    return {"metadata": {"version": FAMILY, "calendar": calendar, "targetDates": targets, "basis": deepcopy(BASIS),
                         "numpyVersion": importlib.metadata.version("numpy")},
            "formations": parents, "fits": fits, "rows": rows, "counts": counts}


def verify_group(dataset, formations, actual, *, start, end):
    expected = reference_build(dataset, formations, start=start, end=end)
    same(actual, expected)
    require(expected["counts"]["fits"] == 20 * expected["counts"]["formationStatusCounts"]["formed"], "Fit population mismatch")
    require(expected["counts"]["plannedRows"] == 5 * expected["counts"]["fits"], "Planned-row population mismatch")
    return deepcopy(expected["counts"])


def synthetic_fixture(sessions=90, formation_index=75):
    calendar = [(date(2024, 7, 10) + timedelta(days=i)).isoformat() for i in range(sessions)]
    symbols = ["sh600000"] + [f"sh600{i:03d}" for i in range(1, 22)]

    def security(symbol, j):
        price, bars = 10.0 + j, []
        for i, d in enumerate(calendar):
            price *= 1 + .003 * math.sin(i * .29 + j * .71) + .001 * math.cos(i * .13 - j * .19)
            bars.append({"date": d, "open": price, "high": price * 1.01, "low": price * .99,
                         "close": price, "volume": 100000 + j, "amount": price * (100000 + j)})
        return {"symbol": symbol, "bars": bars}

    data = {"priceBasisId": "raw-unadjusted", "calendar": calendar,
            "securities": [security(symbol, j) for j, symbol in enumerate(symbols)], "benchmark": security("sh000300", 30)}
    a = calendar[formation_index]
    formation = {"parentEventId": f"leaderLagFollowersV1:{a}:{symbols[0]}", "formationDate": a,
                 "peerFormationDate": calendar[formation_index - 10], "anchorSymbol": symbols[0], "originalLeaderRank": 1,
                 "status": "formed", "groupKind": "formed",
                 "members": [{"symbol": symbol, "frozenMemberRank": i} for i, symbol in enumerate(symbols[1:21], 1)]}
    return data, [formation], a, calendar[-1]


def self_test():
    path = ROOT / "tests/test_leader_incremental_prediction_verifier.py"
    spec = importlib.util.spec_from_file_location("independent_leader_prediction_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    require(result.wasSuccessful(), "Independent synthetic tests failed")
    return {"passed": True, "syntheticTests": result.testsRun, "realSourceReads": 0, "accountExecutions": 0,
            "verifierSha256": sha(__file__), "testsSha256": sha(path)}


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "Timezone-aware provenance required")
    return result


def lifecycle_reference(plan, tests, run, completion, hashes):
    """Receipts must form one execution and actual tests must predate new fits."""
    require(tests.get("passed") is True and completion.get("passed") is True, "Failed execution receipts")
    for obj in (tests, run, completion):
        require(obj.get("planSha256") == hashes["plan"], "Receipt plan mismatch")
    require(run.get("preExecutionTestsSha256") == completion.get("preExecutionTestsSha256") == hashes["tests"],
            "Pre-execution test binding mismatch")
    require(completion.get("runStartSha256") == hashes["run"], "Run-start binding mismatch")
    require(plan.get("newAccounts") == plan.get("reusedAccounts") == completion.get("newAccounts") ==
            completion.get("reusedAccounts") == run.get("newAccounts") == 0, "Unexpected account execution")
    require(tests.get("realDataReads") == tests.get("accountExecutions") == 0, "Pretests must be synthetic")
    checks = tests.get("checks")
    require(isinstance(checks, list) and len(checks) == 3 and all(c.get("exitCode") == 0 for c in checks),
            "Three actual successful test commands required")
    filenames = ["test_research_leader_incremental_prediction.py", "test_leader_incremental_prediction_verifier.py",
                 "test_leader_incremental_prediction_readout.py"]
    for check, filename in zip(checks, filenames):
        argv = check.get("argv")
        require(isinstance(argv, list) and argv[1:] == ["-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", filename, "-v"],
                "Unexpected pretest command")
        require(isinstance(check.get("stderr"), str) and "\nOK\n" in check["stderr"], "Missing successful unittest output")
    registered, tested, started, ended = map(timestamp, (plan["registeredAt"], tests["completedAt"], run["startedAt"], completion["completedAt"]))
    require(registered <= tested <= started <= ended, "Pretest/run chronology mismatch")
    if "startedAt" in tests:
        require(registered <= timestamp(tests["startedAt"]) <= tested, "Pretest start chronology mismatch")


def registered_plan():
    plan_path = OUT / "plan.json"
    plan = read(plan_path)
    require(plan.get("schema") == FAMILY and plan.get("models") == list(MODELS)
            and plan.get("holdoutClaim") is False and plan.get("targetAchieved") is False, "Wrong diagnostic identity")
    for field in ("sourcePins", "inputPins"):
        require(isinstance(plan.get(field), dict), "Missing frozen pin table")
        for path, expected in plan[field].items():
            bound({"path": path, "sha256": expected})
    for path, expected in {**DEPENDENCIES, relative(__file__): sha(__file__),
                           "tests/test_leader_incremental_prediction_verifier.py": sha(ROOT / "tests/test_leader_incremental_prediction_verifier.py")}.items():
        require(plan["sourcePins"].get(path) == expected, "Independent source not preregistered: " + path)
    protocol_path = bound(plan["protocol"])
    require(plan["sourcePins"].get(relative(protocol_path)) == sha(protocol_path), "Protocol source pin missing")
    protocol = read(protocol_path)
    require(protocol.get("family") == FAMILY and protocol["training"]["baseline"] == list(FEATURES[:3])
            and protocol["training"]["augmented"] == list(FEATURES)
            and protocol["trialAccounting"]["newAccounts"] == protocol["trialAccounting"]["reusedAccounts"] == 0,
            "Protocol configuration mismatch")
    bound(plan["previousProgram"])
    require(sha(OUT / "program-before.json") == plan["previousProgram"]["sha256"], "Registry snapshot mismatch")
    bound(plan["atlas"])
    expected_keys = {(p, b) for p in ("2022-2023", "2024-2025") for b in ("main", "chinext", "star")}
    require(isinstance(plan.get("groups"), list) and len(plan["groups"]) == 6
            and {(g["period"], g["board"]) for g in plan["groups"]} == expected_keys, "Incomplete or duplicate source groups")
    for group in plan["groups"]:
        first, last = group["period"].split("-")
        require(group["start"] == first + "-01-01" and group["end"] == last + "-12-31", "Period boundaries changed")
        for name in ("accountData", "parentFormations"):
            require(plan["inputPins"].get(group[name]["path"]) == group[name]["sha256"], "Source input not frozen")
    return plan


def inherited_reference(plan):
    """Re-use the original proof of peer formation; do not recreate future membership."""
    references = {}
    root_by_role = {"parent": bound(plan["parentDelivery"]).parent, "previous": bound(plan["previousDelivery"]).parent}
    parent_groups, previous_groups = None, None
    for role, directory in root_by_role.items():
        delivery_path = directory / "delivery.json"
        delivery = read(delivery_path)
        require(delivery["candidateTrialsAfter"] == (43 if role == "parent" else 45), "Wrong inherited batch")
        inventory = {r["path"]: r["sha256"] for r in delivery["files"]}
        require(len(inventory) == len(delivery["files"]), "Duplicate delivery artifacts")
        # The current experiment binds the old proofs and raw inputs, not a new re-audit of old accounts.
        paths = {name: directory / name for name in ("plan.json", "input-completion.json", "validation.json")}
        if role == "parent":
            paths["input-validation.json"] = directory / "input-validation.json"
        for name, path in paths.items():
            require(plan["inputPins"].get(relative(path)) == sha(path) and inventory.get(relative(path)) == sha(path),
                    "Inherited proof not sealed: " + name)
            references[relative(path)] = sha(path)
        old_plan, old_inputs, old_proof = (read(paths[name]) for name in ("plan.json", "input-completion.json", "validation.json"))
        require(old_proof["passed"] is True and old_proof["planSha256"] == sha(paths["plan.json"])
                and old_proof["inputCompletionSha256"] == sha(paths["input-completion.json"])
                and old_inputs["planSha256"] == sha(paths["plan.json"]), "Inherited proof chain mismatch")
        groups = {(g["period"], g["board"]): g for g in old_inputs["groups"]}
        require(len(groups) == len(old_inputs["groups"]) == 6, "Inherited source duplicates")
        if role == "parent":
            parent_groups = groups
            proof = read(paths["input-validation.json"])
            require(proof["passed"] is True and proof["planSha256"] == sha(paths["plan.json"])
                    and proof["inputCompletionSha256"] == sha(paths["input-completion.json"])
                    and old_proof["inputValidationSha256"] == sha(paths["input-validation.json"]), "Original formation input proof mismatch")
            for source in plan["groups"]:
                group = groups[(source["period"], source["board"])]
                same(source["accountData"], group["accountData"], "parent source cache")
                same(source["parentFormations"], group["formations"], "parent formations")
                require(proof["datasetPins"].get(source["accountData"]["path"]) == source["accountData"]["sha256"],
                        "Original audited source-cache pin mismatch")
                require(inventory.get(group["formations"]["path"]) == group["formations"]["sha256"], "Original formations not sealed")
        else:
            previous_groups = groups
    for source in plan["groups"]:
        group = previous_groups[(source["period"], source["board"])]
        same(source["parentFormations"], group["parentFormations"], "previous immutable parent identity")
        same(source["accountData"], group["accountData"], "previous immutable source")
    return references


def audit():
    require(not (OUT / "validation.json").exists(), "Do not overwrite an existing validation receipt")
    plan = registered_plan()
    inherited = inherited_reference(plan)
    completion_path, tests_path, run_path = (OUT / name for name in ("completion.json", "pre-execution-tests.json", "run-start.json"))
    completion, tests, run = (read(path) for path in (completion_path, tests_path, run_path))
    hashes = {"plan": sha(OUT / "plan.json"), "tests": sha(tests_path), "run": sha(run_path), "completion": sha(completion_path)}
    lifecycle_reference(plan, tests, run, completion, hashes)
    require(len(completion["groups"]) == len(plan["groups"]), "Completed source count mismatch")
    proofs = []
    totals = {"sourceGroups": 0, "formations": 0, "formedEvents": 0, "fits": 0, "trainingPairs": 0,
              "plannedRows": 0, "newAccounts": 0, "reusedAccounts": 0}
    for source, completed in zip(plan["groups"], completion["groups"]):
        identity = {name: source[name] for name in ("period", "board", "start", "end")}
        same({name: completed[name] for name in identity}, identity, "Completed group order")
        directory = OUT / source["period"] / source["board"]
        same(read(directory / "completion.json"), completed, "Group completion")
        files = {name: bound(completed[name]) for name in ("metadata", "formations", "fits", "rows")}
        expected_filenames = {"metadata": "metadata.json", "formations": "formations.jsonl.gz", "fits": "fits.jsonl.gz", "rows": "predictions.jsonl.gz"}
        for name, path in files.items():
            require(path == directory / expected_filenames[name], "Unexpected completed artifact path")
        dataset = read(bound(source["accountData"]))
        parents = records(bound(source["parentFormations"]))
        actual = {"metadata": read(files["metadata"]), "counts": completed["counts"],
                  **{name: records(files[name]) for name in ("formations", "fits", "rows")}}
        same(actual["formations"], parents, "All immutable parent formation records")
        counts = verify_group(dataset, parents, actual, start=source["start"], end=source["end"])
        for name, path in files.items():
            require(sha(path) == completed[name]["sha256"], "Output artifact changed during audit")
        require(actual["metadata"]["numpyVersion"] == completion["numpyVersion"], "Execution version metadata mismatch")
        totals["sourceGroups"] += 1
        for name in ("formations", "fits", "plannedRows"):
            totals[name] += counts[name]
        totals["formedEvents"] += counts["formationStatusCounts"]["formed"]
        totals["trainingPairs"] += counts["fits"] * 60
        proofs.append({**identity, **{name: completed[name] for name in files}, "counts": counts,
                       "accountData": source["accountData"], "parentFormations": source["parentFormations"],
                       "rawReturnsAndTrainingRebuilt": True, "independentSolver": "stdlib partial-pivot Gaussian elimination"})
        print(json.dumps({"verifiedGroup": [source["period"], source["board"]], "counts": counts}, ensure_ascii=False), flush=True)
        del dataset, parents, actual
        gc.collect()
    require(totals["formations"] == 575 and totals["formedEvents"] == 316
            and totals["fits"] == 6320 and totals["plannedRows"] == 31600, "Frozen parent population changed")
    registered_plan()  # Recheck all input/code byte identities after computation.
    require(sha(completion_path) == hashes["completion"] and sha(OUT / "plan.json") == hashes["plan"]
            and sha(tests_path) == hashes["tests"] and sha(run_path) == hashes["run"], "Execution receipts changed during audit")
    proof = {"passed": True, "verifiedAt": datetime.now(timezone.utc).isoformat(), "planSha256": hashes["plan"],
             "completionSha256": sha(completion_path), "preExecutionTestsSha256": hashes["tests"], "runStartSha256": hashes["run"],
             "verifierSha256": sha(__file__), "sourcePins": plan["sourcePins"], "inputPins": plan["inputPins"],
             "inheritedProofPins": inherited, "counts": totals, "groups": proofs,
             "checks": {"rawReturnAndFixed19": True, "all60PairsThroughF": True, "sumLossIndependentSolve": True,
                        "jointFitAndPredictionSupport": True, "predictionTargetMissingnessSeparate": True,
                        "allPlannedSlotsAndStatuses": True, "allErrorsRecomputed": True, "noAccountExecution": True},
             "numericTolerance": {"relative": 1e-8, "absolute": 1e-12}, "holdoutClaim": False, "targetAchieved": False}
    with (OUT / "validation.json").open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(proof, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    return {"passed": True, "validation": ref(OUT / "validation.json"), "counts": totals}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False))
        return
    try:
        print(json.dumps(audit(), ensure_ascii=False), flush=True)
    except Exception as error:
        if OUT.is_dir():
            attempts = OUT / "validation-attempts"
            attempts.mkdir(exist_ok=True)
            failure = {"passed": False, "at": datetime.now(timezone.utc).isoformat(), "verifierSha256": sha(__file__),
                       "errorType": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
            path = attempts / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json")
            with path.open("x", encoding="utf-8") as handle:
                json.dump(failure, handle, ensure_ascii=False, indent=2)
        raise


if __name__ == "__main__":
    main()
