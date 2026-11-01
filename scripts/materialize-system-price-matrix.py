"""One cross-listing-board raw source matrix; no trading or strategy scoring."""
from argparse import ArgumentParser
from bisect import bisect_left
from datetime import datetime, timezone
import gc
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("_system_matrix_utils", ROOT / "scripts/run-liquid-trend-breakout.py")
U = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(U)
read, write, bound, ref, sha, emit = (getattr(U, name) for name in ("read", "write", "bound", "ref", "sha", "emit"))
BASE = ROOT / "artifacts/strategy-search/market-sector-stock-system-v1"
FOUNDATION = BASE / "published-snapshot-foundation-v3"
OUT = BASE / "price-matrix-v1"
PLAN = OUT / "plan.json"
FIELDS = ("open", "high", "low", "close", "volume", "amount")


def verify_plan():
    plan = read(PLAN)
    for value in plan["bindings"]:
        bound(value)
    for path, digest in plan["sourcePins"].items():
        bound({"path": path, "sha256": digest})
    return plan


def register():
    if OUT.exists():
        raise ValueError("Matrix input already registered")
    parent_path = ROOT / "artifacts/strategy-search/reclaim-market-alignment-v1/plan.json"
    parent, proof = read(parent_path), read(FOUNDATION / "validation.json")
    if not proof["passed"]:
        raise ValueError("Verified dated membership foundation required")
    sources = parent["sources"]
    bindings = [ref(parent_path), ref(FOUNDATION / "validation.json"), proof["snapshots"], proof["calendar"], *sources.values(), *parent["indices"].values()]
    for value in bindings:
        bound(value)
    OUT.mkdir()
    write(PLAN, {"schema": "integrated-system-source-price-matrix-v1", "registeredAt": U.now(), "sourcePins": {
        Path(__file__).relative_to(ROOT).as_posix(): sha(Path(__file__))}, "bindings": bindings, "sources": sources, "indices": parent["indices"],
        "snapshots": proof["snapshots"], "calendar": proof["calendar"], "workspaceJsonBefore": U.workspace_snapshot(),
        "fields": FIELDS, "expectedPopulation": {"main": 3126, "chinext": 1312, "star": 561}, "rawPriceBasis": "raw-unadjusted",
        "newMarketAccounts": 0, "newStrategyTrials": 0, "completeSystemValidated": False,
        "meaning": "All previously fixed4999 source stocks in one matrix; current surviving-stock source limitations retained. Snapshot groups replace only after publication. No threshold or strategy optimization."})
    emit({"registered": True, "population": 4999, "plan": ref(PLAN)})


def prepare():
    plan = verify_plan()
    write(OUT / "start.json", {"startedAt": U.now(), "plan": ref(PLAN)})
    calendar = read(bound(plan["calendar"]))
    if calendar != sorted(set(calendar)):
        raise ValueError("Canonical independent source calendar required")
    date_index = {day: i for i, day in enumerate(calendar)}
    source_symbols, metadata = [], []
    population = sum(plan["expectedPopulation"].values())
    matrices = {field: np.full((population, len(calendar)), np.nan, dtype=np.float64) for field in FIELDS}
    cursor, source_counts = 0, {}
    for board, source in plan["sources"].items():
        emit({"sourceStart": board})
        data = read(bound(source))
        securities = data["securities"]
        if data["calendar"] != calendar or data["priceBasisId"] != plan["rawPriceBasis"] or data["adjustment"] != "none" or len(securities) != plan["expectedPopulation"][board]:
            raise ValueError("Existing stock source calendar, raw basis or cohort changed")
        count = 0
        for security in securities:
            symbol = security["symbol"]
            if symbol in source_symbols:
                raise ValueError("Duplicate symbol in common source pool")
            previous = ""
            for bar in security["bars"]:
                day = bar["date"]
                if day not in date_index or day <= previous:
                    raise ValueError("Source raw dates must be exact calendar slots in increasing order")
                for field in FIELDS:
                    value = bar.get(field)
                    if type(value) not in (int, float) or not np.isfinite(value):
                        raise ValueError("Unexpected nonnumeric source raw cell")
                    matrices[field][cursor, date_index[day]] = value
                previous = day
                count += 1
            source_symbols.append(symbol)
            metadata.append({"symbol": symbol, "sourceBoard": board, "amountUnit": security.get("amountUnit"), "volumeUnit": security.get("volumeUnit"),
                "corporateActionsComplete": security.get("corporateActionsComplete"), "historicalNameKnown": security.get("historicalNameKnown"),
                "localCoverageOnly": security.get("localCoverageOnly"), "rawSource": source})
            cursor += 1
        source_counts[board] = {"stocks": len(securities), "sourceBars": count}
        emit({"sourcePrepared": board, **source_counts[board]})
        del data, securities
        gc.collect()
    if cursor != population or len(set(symbol[2:] for symbol in source_symbols)) != population:
        raise ValueError("Unique publisher six-digit codes required for unambiguous industry join")
    order = np.argsort(source_symbols)
    symbols = np.array(source_symbols, dtype="U8")[order]
    for field in FIELDS:
        matrices[field] = matrices[field][order]
    metadata = [metadata[int(i)] for i in order]
    valid = np.logical_and.reduce([np.isfinite(matrices[f]) & (matrices[f] > 0) for f in ("open", "high", "low", "close", "volume")])
    valid &= matrices["low"] <= np.minimum(matrices["open"], matrices["close"])
    valid &= np.maximum(matrices["open"], matrices["close"]) <= matrices["high"]
    with (OUT / "prices.npz").open("xb") as handle:
        np.savez_compressed(handle, symbols=symbols, calendar=np.array(calendar, dtype="U10"), priceValid=valid, **matrices)
    write(OUT / "securities.json", metadata)
    del matrices, valid
    gc.collect()
    with np.load(OUT / "prices.npz", allow_pickle=False) as stored:
        observed = {key: stored[key] for key in stored.files}
    if observed["symbols"].tolist() != sorted(source_symbols) or observed["calendar"].tolist() != calendar:
        raise ValueError("Stored matrix identity axes differ")
    checked = 0
    for board, source in plan["sources"].items():
        data = read(bound(source))
        for security in data["securities"]:
            # Independent binary lookup rather than construction's running cursor
            # and date dictionary; every observed source field is checked.
            row = int(np.searchsorted(observed["symbols"], security["symbol"]))
            bars = security["bars"]
            columns = np.searchsorted(observed["calendar"], [bar["date"] for bar in bars])
            want = np.array([[bar[field] for field in FIELDS] for bar in bars], dtype=np.float64)
            for j, field in enumerate(FIELDS):
                if not np.array_equal(observed[field][row, columns], want[:, j], equal_nan=True):
                    raise ValueError("Full independent source-to-stored price cell mismatch")
            if np.isfinite(observed["close"][row]).sum() != len(bars):
                raise ValueError("A missing source calendar slot was filled")
            checked += len(bars) * len(FIELDS)
        del data
        gc.collect()
        emit({"fullSourceCellAuditPassed": board})
    snapshots = read(bound(plan["snapshots"]))
    symbol_rows = {symbol[2:]: i for i, symbol in enumerate(observed["symbols"].tolist())}
    joins = []
    for snapshot in snapshots:
        groups, known = {}, set()
        for member in snapshot["members"]:
            sector = groups.setdefault(member["sectorCode"], {"sectorCode": member["sectorCode"], "sectorName": member["sectorName"], "publisherMembers": 0, "cohortRowIndices": []})
            sector["publisherMembers"] += 1
            if member["code"] in symbol_rows:
                sector["cohortRowIndices"].append(symbol_rows[member["code"]])
                known.add(member["code"])
        joins.append({"snapshotId": snapshot["snapshotId"], "publicationDate": snapshot["publicationDate"],
            "availableFrom": next((day for day in calendar if day > snapshot["publicationDate"]), None),
            "classificationId": snapshot["classificationId"], "cohortKnown": len(known), "cohortUnknown": population - len(known),
            "unknownCohortCodes": sorted(symbol_rows.keys() - known), "sectors": list(groups.values())})
    if U.workspace_snapshot() != plan["workspaceJsonBefore"]:
        raise ValueError("Protected user workspace changed")
    write(OUT / "snapshot-cohort-joins.json", joins)
    write(OUT / "validation.json", {"passed": True, "plan": ref(PLAN), "prices": ref(OUT / "prices.npz"),
        "securities": ref(OUT / "securities.json"), "snapshotCohortJoins": ref(OUT / "snapshot-cohort-joins.json"), "population": population,
        "calendarSessions": len(calendar), "allSourceCellsCompared": checked, "allSourceCellsExact": True, "missingSlotsNotFilled": True,
        "sourceCounts": source_counts, "priceValidCells": int(observed["priceValid"].sum()), "rawPriceModelUncertified": True,
        "userJsonFilesUnchanged": True, "newMarketAccounts": 0, "newStrategyTrials": 0, "completeSystemValidated": False, "targetAchieved": False})
    emit({"matrixPassed": True, "population": population, "calendarSessions": len(calendar), "sourceCellsChecked": checked, "validation": ref(OUT / "validation.json")})


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("command", choices=("register", "prepare"))
    args = parser.parse_args()
    register() if args.command == "register" else prepare()
