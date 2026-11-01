"""Read-only bindings for two existing contraction account baselines.

Only small market sidecars and the daily stream are physically verified here.
Large feature/role identities are inherited from pinned successful audits; this
is not another mathematical audit or a scan of those files. Account raw loading
is explicit and occurs once per load_account_source call, never on import.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = Path("artifacts/strategy-search")
SID = "rawThreeStageContractionBreakoutDiagnostic"
PERIODS = {
    "2022-2023": {
        "baseline": "contraction-policy-screen-v1", "name": "noProgress5",
        "market": "market-state-panel-v1", "start": "2022-01-04", "end": "2023-12-29",
        "sessions": 484, "signals": 131,
        "signalPath": "signal-contraction-price-v1/attempts/attempt-0001/signals.jsonl.gz",
        "signalSha256": "536dc9e102a083cdb68bcd8b2b61920f35105049391457036ccfffe90777d80d",
        "baselinePins": {
            "noProgress5.result.json.gz": "bc376c65e8ed2089f472028412bd53fad5881989417341e1d5576e682a2b9e6f",
            "noProgress5.summary.json": "ec282489e3b4ffbbf7a99bc54c7d0350621a585cd392f91b38eb6a63404097de",
            "source.json": "bbabb266063c14c36198fa4b40868cc48f46551b62e96251394f17bf1b2dee29"},
        "marketPins": {
            "completion.json": "45526b0da92d57342033c3a3624798cfc9a2142105bc757c23396dd5a922a1cf",
            "protocol.json": "3d9b0b1627056a25733ffa6d912923d701945dd628cf7f6f03d31349f6c554f9",
            "summary.json": "bb3bec2f60e579cc1f6bb776d84055f6b91ffaf0fc732e3368f7800d9e8155ff",
            "independent-verification.json": "21f73511855e03faade8758129454cb7049f818c7d6a2165955b67df63a97a90",
            "attempt.json": "010d03d6439db813f6b8b9d0bde461a2175818a24c6b28fe3dd6b5369adb402f",
            "execution-sources.zip": "db52a4e3614b49b0da1c13b7cf825bc85db6ce4bb1fdc93f1ab7f3a7a372a778",
            "daily.jsonl.gz": "a006ca570b55954d9b6b3db0a7048ad07c843890a2f2e473afb7ab98574b51e5"}},
    "2024-2025": {
        "baseline": "supplemental-force-context-screen-v1", "name": "baseline10",
        "market": "supplemental-market-state-panel-v1", "start": "2024-01-02", "end": "2025-12-31",
        "sessions": 485, "signals": 213,
        "signalPath": "supplemental-force-context-screen-v1/baseline.signals.json",
        "signalSha256": "bc59e466c84ddae417b2f30be3d1d6b904673220d449c2743906ef04a0f8d359",
        "baselinePins": {
            "baseline10.result.json.gz": "6269c502e3ca532ed6fbba7c3065faca735f3b6a87c8e940ea55265e433dc685",
            "baseline10.summary.json": "bf8de434f8159273d68be73295bffd00f759ed50ba08a5ff671781a32f6e2057",
            "source.json": "218441d1c03a6685a658baabccd86c459b88bb74bf67fb5b73d0c6a6b5f01782"},
        "marketPins": {
            "completion.json": "0db5b039f3197b1919139b94a72a89dc0b417ac3b913f6fba8592707b16b4cdd",
            "protocol.json": "0a3055773144e19ee5e52efa8e508cf149d8fc59236f30bfc3d541703be84fa4",
            "summary.json": "e18b06b764f85d984943f24b9b360acfc04ffbcef41e784d25c619d290335831",
            "independent-verification.json": "1e7788ebd34ecc5e78ce765f6d61dd1770b1e0e7a9d1516d93fa394372721874",
            "attempt.json": "d5e17df15e066a9c5e7d86dd84ea913a59d983379c5b51c6943bd6f79d8c52cb",
            "execution-sources.zip": "63b5ba041c918e8d8cd955ebff6eac0963744a249d9208079a9acec32732ff11",
            "daily.jsonl.gz": "fc7ac704bd48925ca89d4a61888d81639a76977881494fa6d59720e46594991f"}},
}


def _require(value, message):
    if not value:
        raise ValueError(message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _local(name):
    path = (ROOT / name).resolve()
    _require(path.is_relative_to(ROOT.resolve()), "Input path leaves repository")
    return path


def _check(path, expected, files):
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    _require(actual == expected, f"Pinned input changed: {path.name}")
    files.append({"path": path.relative_to(ROOT.resolve()).as_posix(), "sha256": actual})


def _read(path):
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def _spec(period):
    _require(type(period) is str and period in PERIODS, "Unknown study period")
    return PERIODS[period]


def _baseline(period):
    spec, files = _spec(period), []
    directory = _local(BASE / spec["baseline"])
    for name, expected in spec["baselinePins"].items():
        _check(directory / name, expected, files)
    result = _read(directory / (spec["name"] + ".result.json.gz"))
    summary = _read(directory / (spec["name"] + ".summary.json"))
    identity = _read(directory / "source.json")
    _require(result["status"] == "completed" and summary["inputResultSha256"] == _digest(result),
             "Baseline result/summary mismatch")
    _require(result["dataDigest"] == identity["accountDatasetDigest"] and
             result["fingerprint"] == identity["accountSnapshotFingerprint"], "Baseline data identity differs")
    _require(result["research"]["structuralExits"]["rule"] == "noProgress5" and
             result["research"]["identity"]["entryWeight"] == .2, "Baseline account policy differs")
    config = result["config"]
    required = {"initialCapital": 500000., "maxPositions": 5, "maxStockWeight": .2,
        "topN": 5, "stopLossPct": .05, "trailingStopPct": .1, "takeProfitPct": 0.,
        "maxHoldingDays": 0, "slippageBps": 10., "startDate": spec["start"], "endDate": spec["end"]}
    _require(all(config.get(k) == v for k, v in required.items()), "Unexpected baseline account settings")
    days = [r["date"] for r in result["curve"]]
    _require(len(days) == spec["sessions"] and days == sorted(set(days)) and
             (days[0], days[-1]) == (spec["start"], spec["end"]), "Baseline session range differs")
    for day in days:
        _require(date.fromisoformat(day).isoformat() == day, "Invalid baseline date")
    signals = result["research"]["identity"]["signalSchedule"]
    path = _local(BASE / spec["signalPath"])
    _check(path, spec["signalSha256"], files)
    if period == "2022-2023":
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            source = [json.loads(line) for line in stream]
        source = [{"signalId": f"{SID}:{r['signalDate']}:{r['symbol']}", "strategyId": SID,
            "signalDate": r["signalDate"], "symbol": r["symbol"], "originalRank": r["rank"]}
            for r in source if r["strategyId"] == SID]
        source.sort(key=lambda r: (r["signalDate"], r["originalRank"], r["symbol"]))
    else:
        source = _read(path)
    _require(source == signals and len(signals) == spec["signals"] and
             _digest(signals) == result["research"]["identity"]["signalScheduleSha256"],
             "Complete pre-active opportunity schedule differs")
    _require(len({r["signalId"] for r in signals}) == len(signals) and
             len({(r["signalDate"], r["originalRank"]) for r in signals}) == len(signals) and
             all(r["signalDate"] in days and r["strategyId"] == SID and
                 type(r["originalRank"]) is int and 1 <= r["originalRank"] <= 5 for r in signals),
             "Invalid or duplicate opportunity identity/rank/date")
    return {"result": result, "summary": summary, "signals": deepcopy(signals),
            "sourceIdentity": identity, "files": files}


def load_baseline(period):
    """Return the complete stored baseline plus causally date-aligned daily rows.

    No price data is loaded. Role/feature large-file byte verification and their
    formulas are inherited, explicitly reported as not repeated by this call.
    The caller filters these pre-active signals, preserving IDs/ranks without
    backfill, then starts a fresh account; never filter existing baseline fills.
    """
    value, spec = _baseline(period), _spec(period)
    files = value["files"]
    directory = _local(BASE / spec["market"])
    for name, expected in spec["marketPins"].items():
        _check(directory / name, expected, files)
    protocol, completion, summary, audit = [_read(directory / name) for name in
        ("protocol.json", "completion.json", "summary.json", "independent-verification.json")]
    body = {k: v for k, v in protocol.items() if k != "protocolHash"}
    _require(_digest(body) == protocol["protocolHash"], "Market protocol content hash differs")
    _require(audit.get("passed") is True and audit.get("failureCount") == 0 and
             type(audit.get("checks")) is int and audit["checks"] > 0,
             "Market independent audit did not pass")
    expected_receipts = {"attempt.json", "daily.jsonl.gz", "execution-sources.zip",
                         "protocol.json", "roles.jsonl.gz", "summary.json"}
    _require(set(completion["files"]) == expected_receipts, "Market receipt inventory differs")
    for name, record in completion["files"].items():
        _require(record["path"] == name and record["sha256"] == audit["resultFileHashes"][name],
                 "Market receipt/audit digest mismatch")
        if name != "roles.jsonl.gz":
            _require(record["sha256"] == spec["marketPins"][name], "Market small sidecar pin differs")
    if "completion.json" in audit["resultFileHashes"]:
        _require(audit["resultFileHashes"]["completion.json"] == spec["marketPins"]["completion.json"],
                 "Market audit completion pin differs")
    _require(protocol["executionArchiveSha256"] == spec["marketPins"]["execution-sources.zip"] and
             protocol["implementation"] == audit["implementation"], "Market execution evidence differs")
    _require(_read(directory / "attempt.json")["protocolHash"] == protocol["protocolHash"],
             "Market attempt belongs to another protocol")
    dates = [r["date"] for r in value["result"]["curve"]]
    symbols = protocol["symbols"]
    _require(len(symbols) == 3141 and symbols == sorted(set(symbols)) and protocol["dates"] == dates,
             "Market/account population or target dates differ")
    for record in (completion, summary, audit):
        _require(record["protocolHash"] == protocol["protocolHash"] and record["dailyRows"] == len(dates)
                 and record["roleRows"] == len(dates) * len(symbols), "Market sidecar counts/identity differ")
    _require(summary["qCounts"] == audit["qCounts"], "Market Q totals differ")
    registration = protocol["registration"]
    _require(protocol["inputFilesSha256"] == registration["input"]["filesSha256"],
             "Market upstream file identities differ")
    manifest_path = _local(Path(registration["input"]["directory"]) / "manifest.json")
    _check(manifest_path, protocol["inputManifestSha256"], files)
    inputs = _read(manifest_path)["inputs"]
    source = value["sourceIdentity"]
    source_fp = source.get("sourceSnapshotFingerprint", source.get("sourceDatasetFingerprint"))
    _require(inputs["symbols"] == symbols and inputs["snapshotFingerprint"] == source_fp and
             inputs["fullDatasetContentSha256"] == source["sourceDatasetDigest"] and
             inputs["calendar"]["sha256"] == protocol["calendarHash"] == source["calendarEvidence"]["calendarHash"],
             "Market daily observations and account raw source differ")
    _require(audit["inputManifestSha256"] == protocol["inputManifestSha256"] ==
             protocol["inputFilesSha256"]["manifest.json"], "Market upstream manifest differs")
    rows, q_counts = [], {"true": 0, "false": 0, "unknown": 0}
    with gzip.open(directory / "daily.jsonl.gz", "rt", encoding="utf-8") as stream:
        for index, line in enumerate(stream):
            row = json.loads(line)
            _require(index < len(dates) and row["date"] == dates[index] and
                row["featureVersion"] == registration["featureVersion"] and row["fixedPopulation"] == len(symbols)
                and row["qDefinition"] == registration["qDefinition"]["id"] and
                row["protocolHash"] == protocol["protocolHash"] and
                row["calendarHash"] == protocol["calendarHash"] and
                row["inputManifestSha256"] == protocol["inputManifestSha256"] and
                row["inputFeatureFileSha256"] == protocol["inputFilesSha256"]["features.jsonl.gz"],
                "Market daily row identity/order differs")
            _require(set(row["qCounts"]) == set(q_counts) and
                all(type(n) is int and n >= 0 for n in row["qCounts"].values()) and
                sum(row["qCounts"].values()) == len(symbols), "Invalid daily Q population counts")
            for key in q_counts:
                q_counts[key] += row["qCounts"][key]
            expected_earlier = dates[index - 5] if index >= 5 else None
            _require(row["matched5"]["earlierDate"] == expected_earlier and
                (index >= 5 or row["matched5"]["available"] is False), "Market T-5 date differs")
            rows.append(row)
    _require(len(rows) == len(dates) and q_counts == summary["qCounts"], "Incomplete daily stream or Q totals")
    value.update(marketRows=rows, marketBinding={
        "directory": (BASE / spec["market"]).as_posix(), "protocolHash": protocol["protocolHash"],
        "completionSha256": spec["marketPins"]["completion.json"],
        "auditSha256": spec["marketPins"]["independent-verification.json"],
        "inputManifestSha256": protocol["inputManifestSha256"],
        "inputFeatureFileSha256": protocol["inputFilesSha256"]["features.jsonl.gz"],
        "rolesSha256": completion["files"]["roles.jsonl.gz"]["sha256"],
        "calendarHash": protocol["calendarHash"], "symbolsSha256": _digest(symbols),
        "dailyRows": len(rows), "fixedPopulation": len(symbols), "independentAuditPassed": True,
        "marketMathematicsRecomputed": False, "featureBytesRehashed": False, "roleBytesRehashed": False,
        "meaning": "Pinned audited daily aggregates; source feature and role proofs inherited, not rerun."})
    return value


def _module(filename):
    spec = importlib.util.spec_from_file_location("_market_context_" + filename.replace("-", "_"),
                                                 ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_account_source(period, progress=None):
    """Load one period once and require exact stored-baseline source identity.

    Each period is a separate account. Caller must reuse returned data for its
    schedule variants; invoking this function again intentionally loads again.
    The dated fee policy supports only 2022-23; 2024-25 explicitly retains the
    baseline Legacy fee assumption, not independently certified broker charges.
    """
    _require(progress is None or callable(progress), "progress must be callable")
    baseline = _baseline(period)
    if period == "2022-2023":
        helper = _module("run-direction-account-screen.py").load_helper()
        if progress:
            progress({"phase": "source-binding", "period": period})
        binding = helper.load_binding()
        if progress:
            progress({"phase": "source-loading", "period": period})
        raw = helper.load_dataset(binding)
        data, identity = helper.account_view(raw, binding)
        policy = helper.fee_policy(binding["document"])
    else:
        helper = _module("load-supplemental-account-source.py")
        raw, binding, _ = helper.load_source(progress=progress)
        data, identity = helper.account_view(raw, binding)
        from service.research_account_fees import LegacyFeePolicy
        policy = LegacyFeePolicy()
    _require(identity == baseline["sourceIdentity"], "Reloaded account source differs from stored baseline")
    config = baseline["result"]["config"]
    settings = baseline["result"]["research"]["feeLedger"]["identity"]["settings"]
    _require(all(value == settings.get("inheritedEventReservePolicyUnused" if key == "reservePolicy" else key)
                 for key, value in policy.settings(config).items()), "Reloaded fee policy differs from baseline")
    target_dates = [day for day in data["calendar"] if config["startDate"] <= day <= config["endDate"]]
    _require(target_dates == [r["date"] for r in baseline["result"]["curve"]], "Account target sessions differ")
    if progress:
        progress({"phase": "account-source-ready", "period": period, "sessions": len(target_dates)})
    return raw, data, identity, policy
