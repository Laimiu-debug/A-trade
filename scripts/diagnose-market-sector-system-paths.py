"""Read-only held-path diagnosis of the six frozen system accounts.

No forward holding simulation, parameter search, new signal or new account.
Intraday extremes describe an observed price path, not executable proceeds.
"""
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "artifacts/strategy-search/market-sector-stock-accounts-v1/attempt-001"
OUT = STUDY / "mechanism-diagnostic-001"


def read(path):
    path = Path(path)
    with (gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8")) as stream:
        return json.load(stream)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ref(path):
    return {"path": Path(path).relative_to(ROOT).as_posix(), "sha256": sha(path)}


def bound(value):
    path = (ROOT / value["path"]).resolve()
    if not path.is_relative_to(ROOT) or sha(path) != value["sha256"]:
        raise ValueError("Changed diagnostic input: " + value["path"])
    return path


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def stats(rows):
    losses = [r for r in rows if r["returnPct"] < 0]
    wins = [r for r in rows if r["returnPct"] > 0]
    return {"closed": len(rows), "netPnl": sum(r["pnlAmount"] for r in rows),
        "meanWinReturnPct": mean(r["returnPct"] for r in wins) if wins else None,
        "meanLossReturnPct": mean(r["returnPct"] for r in losses) if losses else None,
        "medianHoldingSessions": median(r["holdingDays"] for r in rows) if rows else None,
        "closedWithin5Sessions": sum(r["holdingDays"] <= 5 for r in rows),
        "signalNtr20MedianPct": median(r["signalNtr20Pct"] for r in rows) if rows else None,
        "initialFailureDistanceMedianNtr": median(r["initialFailureDistanceNtr"] for r in rows) if rows else None,
        "queueCloseReturnObserved": sum(r["queueCloseReturnPct"] is not None for r in rows),
        "queueCloseReturnMeanPct": mean(r["queueCloseReturnPct"] for r in rows if r["queueCloseReturnPct"] is not None) if any(r["queueCloseReturnPct"] is not None for r in rows) else None,
        "weightedSellPriceReturnMeanPct": mean(r["weightedSellPriceReturnPct"] for r in rows) if rows else None,
        "firstQueueToFinalFillMedianSessions": median(r["queueToFinalFillSessions"] for r in rows) if rows else None,
        "firstQueueToFinalFillMaxSessions": max((r["queueToFinalFillSessions"] for r in rows), default=None),
        "preFirstSellMfeMedianPct": median(r["preFirstSellMfePct"] for r in rows if r["preFirstSellMfePct"] is not None) if any(r["preFirstSellMfePct"] is not None for r in rows) else None}


def build():
    validation = read(STUDY / "validation.json")
    if not validation["passed"] or validation["targetAchieved"]:
        raise ValueError("Audited failed first system required")
    for key in ("plan", "inputs", "readout", "fullAccountAudit", "diagnostics"):
        bound(validation[key])
    plan, inputs = read(bound(validation["plan"])), read(bound(validation["inputs"]))
    for path, expected in plan["sourcePins"].items():
        if sha(ROOT / path) != expected:
            raise ValueError("Frozen execution source changed: " + path)
    formation = read(STUDY / "formation.candidate.json.gz")
    system = read(bound(inputs["systemInputs"]))
    daily = {r["date"]: r for r in formation["daily"]}
    with np.load(bound(plan["matrix"]), allow_pickle=False) as data:
        matrix = {k: data[k] for k in data.files}
    calendar, symbols = matrix["calendar"].tolist(), matrix["symbols"].tolist()
    date_index, row_index = {d:i for i,d in enumerate(calendar)}, {s:i for i,s in enumerate(symbols)}
    all_rows, account_rows, account_refs = [], [], []
    for expected in plan["plannedAccounts"]:
        period, policy = expected["period"], expected["policy"]
        path = STUDY / period / policy / "completion.json"
        completion = read(path)
        result = read(bound(completion["result"]))
        account_refs.append({"period":period, "policy":policy, "completion":ref(path), "result":completion["result"]})
        bindings = {b["signalId"]:b for b in result["research"]["system"]["entryBindings"]}
        fills, groups = result["trades"], defaultdict(list)
        for fill in fills:
            if fill["side"] == "buy":
                groups[fill["symbol"], fill["date"]] = [fill]
            else:
                key = next(k for k,v in reversed(list(groups.items())) if k[0] == fill["symbol"] and v[-1]["quantityAfter"] > 0)
                groups[key].append(fill)
        rows = []
        for closed in result["closedTrades"]:
            chain = groups[closed["symbol"], closed["entryDate"]]
            entry, sells = chain[0], chain[1:]
            if not sells or sells[-1]["quantityAfter"] != 0 or sells[-1]["date"] != closed["exitDate"]:
                raise ValueError("Closed episode/fill binding differs")
            meta = system["metadata"][entry["signalId"]]
            binding = bindings[entry["signalId"]]
            source = daily[entry["signalDate"]]
            sector = next(s for s in source["sectors"] if s["sectorCode"] == meta["sectorCode"])
            symbol_row, signal_i, entry_i = row_index[entry["symbol"]], date_index[entry["signalDate"]], date_index[entry["date"]]
            first_sell_i, final_i = date_index[sells[0]["date"]], date_index[sells[-1]["date"]]
            if entry_i != signal_i + 1:
                raise ValueError("Entry clock differs")
            queues = [d for d in result["decisions"] if d.get("symbol") == entry["symbol"] and d.get("action") == "sell" and d.get("status") == "queued" and entry["date"] <= d["date"] < sells[0]["date"]]
            if len(queues) != 1:
                raise ValueError("Persistent first exit decision is not unique")
            queue = queues[0]
            observed = queue.get("observedClose", queue.get("riskEvidence", {}).get("observedClose"))
            # A market/industry exit may be queued while this particular
            # security has no valid current price. Keep that observation null.
            price = entry["price"]
            weighted_sell = sum(s["price"]*s["quantity"] for s in sells) / entry["quantity"]
            valid = matrix["priceValid"][symbol_row, entry_i:first_sell_i]
            highs, lows = matrix["high"][symbol_row, entry_i:first_sell_i][valid], matrix["low"][symbol_row, entry_i:first_sell_i][valid]
            t_close = matrix["close"][symbol_row, signal_i]
            historical_close = matrix["close"][symbol_row, signal_i-19:signal_i+1]
            if not np.isfinite(historical_close).all() or not np.isclose(t_close, meta["signalClose"]):
                raise ValueError("Signal source path differs")
            item = {"period":period, "policy":policy, **closed, "signalId":entry["signalId"], "signalDate":entry["signalDate"],
                "pattern":meta["pattern"], "sectorCode":meta["sectorCode"], "exitReason":sells[-1]["reason"],
                "queueDate":queue["date"], "entryPrice":price, "signalNtr20Pct":meta["meanNtr20"]*100,
                "signalMa20DistanceNtr":(t_close-float(historical_close.mean()))/(meta["meanNtr20"]*t_close),
                "openingGapPct":(entry["openingPrice"]/t_close-1)*100,
                "initialFailureDistanceNtr":(price-binding["failurePriceAtSignal"])/(price*meta["meanNtr20"]),
                "nominalStopDistanceNtr":.05/meta["meanNtr20"],
                "queueCloseReturnPct":None if observed is None else ((observed/queue["riskEvidence"]["riskEntryPrice"]-1)*100 if "riskEntryPrice" in queue.get("riskEvidence",{}) else (observed/price-1)*100),
                "weightedSellPriceReturnPct":(weighted_sell/price-1)*100,
                "queueToFinalFillSessions":final_i-date_index[queue["date"]],
                "preFirstSellMfePct":(float(highs.max())/price-1)*100 if len(highs) else None,
                "preFirstSellMaePct":(float(lows.min())/price-1)*100 if len(lows) else None,
                "marketTurnoverCny":source["market"]["turnover"], "sectorParticipation":sector["participation"],
                "sectorConfirmationSessions":sector["confirmCount"]}
            rows.append(item)
        if len(rows) != len(result["closedTrades"]):
            raise ValueError("Closed population differs")
        summary = {"period":period,"policy":policy,"overall":stats(rows)}
        for key in ("exitReason", "pattern"):
            summary[key] = [{"label":label, **stats([r for r in rows if r[key] == label])} for label in sorted({r[key] for r in rows})]
        # One pre-existing stop/volatility unit comparison. No scanned bins.
        summary["nominalStopVsObservedOneNtr"] = [{"label":label, **stats([r for r in rows if (r["nominalStopDistanceNtr"] < 1) == inside])} for label,inside in (("stopInsideOneNtr",True),("stopAtLeastOneNtr",False))]
        account_rows.append(summary)
        all_rows.extend(rows)
    OUT.mkdir()
    write(OUT / "episodes.json", {"rows":all_rows,"intradayExtremesExecutable":False,"newAccounts":0,"newSignals":0})
    write(OUT / "readout.json", {"schema":"whole-system-held-path-diagnostic-v1","rows":account_rows,"accountEvidence":account_refs,
        "fullAccountValidation":ref(STUDY/"validation.json"),"formation":ref(STUDY/"formation.candidate.json.gz"),"matrix":plan["matrix"],
        "method":"Actual closed episodes; first persistent exit queue, realized fills, and only observed pre-first-sell holding paths. No post-exit return substituted.",
        "newAccounts":0,"newSignals":0,"newStrategyTrials":0,"parameterSearch":False,"certified":False,"targetAchieved":False})
    write(OUT / "validation.json", {"passed":True,"source":ref(Path(__file__)),"episodes":ref(OUT/"episodes.json"),"readout":ref(OUT/"readout.json"),
        "accounts":6,"closedEpisodes":len(all_rows),"executionSourcesUnchanged":True,"fullAccountValidation":ref(STUDY/"validation.json"),
        "scope":"Descriptive source-bound analysis; independent account/risk arithmetic is provided by the earlier full-account audit, not rerun here."})
    print(json.dumps({"done":str(OUT),"closedEpisodes":len(all_rows),"primaryComplete":account_rows[0]},ensure_ascii=False))


if __name__ == "__main__":
    build()
