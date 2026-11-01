"""Zero-account entry-risk description; never treats a subset as a new strategy.

Entry premium comes from the frozen T-close reserve and actual opening fill.
The first held-day volatility window ends on T, before the T+1 entry. Compare
closed winners/losers descriptively; no threshold scan or admission is promoted.
"""
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/reclaim-market-alignment-v1"


def read(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(path):
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}


def bound(item):
    path = (ROOT / item["path"]).resolve()
    if not path.is_relative_to(ROOT) or sha(path) != item["sha256"]:
        raise ValueError("Foreign or changed source")
    return path


def write(path, data):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def describe(rows):
    result = {"closed": len(rows)}
    for key in ("returnPct", "entryOpeningGapPct", "preEntryMeanTRPct20", "triggerCloseGainPct", "triggerToCompletedNetDeltaPercentagePoints"):
        values = [r[key] for r in rows if numeric(r.get(key))]
        result[key] = {"known": len(values), "unknown": len(rows) - len(values),
                       "mean": mean(values) if values else None, "median": median(values) if values else None}
    return result


def main():
    proof, readout = read(OUT / "validation.json"), read(OUT / "readout.json")
    if not proof["passed"] or proof["readout"] != ref(OUT / "readout.json"):
        raise ValueError("Bound independent complete-account proof required")
    write(OUT / "entry-risk-diagnostic-definition.json", {"registeredAt": datetime.now(timezone.utc).isoformat(),
        "builder": ref(Path(__file__)), "readout": ref(OUT / "readout.json"), "groups": ["allClosed", "closedWinners", "closedLosers", "closedInitialStopLosses"],
        "entryGap": "actual openingPrice / (frozen reservePrice / (1+slippageBps/10000))-1",
        "preEntryVolatility": "first held-day prior20 normalized true-range mean, require window.asOfDate==signalDate<entryDate",
        "triggerToCompletedDelta": "net complete-episode return minus first queued exit's observed close/risk-entry gain; includes fees, slippage, timing/partial-fill effects, not pure overnight-gap causal attribution",
        "limits": "Conditional descriptive groups are not filtered strategy returns or counterfactual PnL; no entry threshold is tested/promoted here. Open episodes excluded and retained separately in the parent account.",
        "newAccounts": 0, "newStrategyTrials": 0})
    rows = []
    for case in readout["rows"]:
        result = read(bound(case["evidence"]["result"]))
        opportunities = {r["signalId"]: r for r in result["research"]["opportunities"]}
        entries, active = {}, {}
        for fill in result["trades"]:
            symbol = fill["symbol"]
            if fill["side"] == "buy":
                if fill["positionAction"] != "open" or symbol in active:
                    raise ValueError("One initial fill per episode required")
                key = (symbol, fill["date"])
                if key in entries:
                    raise ValueError("Duplicate entry episode")
                entries[key] = {"buy": fill, "sells": []}
                active[symbol] = key
            else:
                entries[active[symbol]]["sells"].append(fill)
                if fill["positionAction"] == "exit":
                    del active[symbol]
        first_observations = {}
        for observation in result["research"]["volatilityTrail"]["observations"]:
            if observation["date"] == observation["entryDate"]:
                key = (observation["symbol"], observation["entryDate"])
                if key in first_observations:
                    raise ValueError("Duplicate first-day risk observation")
                first_observations[key] = observation
        episodes = []
        for closed in result["closedTrades"]:
            key = (closed["symbol"], closed["entryDate"])
            episode = entries[key]
            buy, sells = episode["buy"], episode["sells"]
            opportunity = opportunities[buy["signalId"]]
            if not sells or sells[-1]["date"] != closed["exitDate"] or sells[-1]["positionAction"] != "exit":
                raise ValueError("Complete-episode sell linkage differs")
            signal_close = opportunity["reservePrice"] / (1 + result["config"]["slippageBps"] / 10000)
            gap = (buy["openingPrice"] / signal_close - 1) * 100
            first = first_observations[key]
            if first["window"]["asOfDate"] != buy["signalDate"] or not buy["signalDate"] < buy["date"]:
                raise ValueError("Pre-entry volatility availability mismatch")
            vol = first["window"]["meanTRPct20"]
            risk = sells[0].get("riskEvidence", {})
            close, entry = risk.get("observedClose"), risk.get("riskEntryPrice")
            trigger = (close / entry - 1) * 100 if numeric(close) and numeric(entry) and entry > 0 else None
            value = closed["pnlAmount"] / closed["investedAmount"] * 100
            if not math.isclose(value, closed["returnPct"], rel_tol=1e-10, abs_tol=1e-9):
                raise ValueError("Closed episode return inconsistent")
            episodes.append({"symbol": closed["symbol"], "entryDate": closed["entryDate"], "exitDate": closed["exitDate"],
                "returnPct": value, "entryOpeningGapPct": gap, "preEntryMeanTRPct20": vol * 100 if numeric(vol) else None,
                "firstExitReason": sells[0]["reason"], "triggerCloseGainPct": trigger,
                "triggerToCompletedNetDeltaPercentagePoints": value - trigger if trigger is not None else None})
        winners = [r for r in episodes if r["returnPct"] > 0]
        losers = [r for r in episodes if r["returnPct"] < 0]
        stops = [r for r in losers if r["firstExitReason"] == "stopLoss"]
        if len(episodes) != case["closed"]:
            raise ValueError("Closed sample count mismatch")
        for subset, expected in ((winners, case["meanWinReturnPct"]), (losers, case["meanLossReturnPct"])):
            actual = mean(r["returnPct"] for r in subset) if subset else None
            if (actual is None) != (expected is None) or actual is not None and not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8):
                raise ValueError("Independent parent payoff mean mismatch")
        row = {"board": case["board"], "policy": case["policy"], "allClosed": describe(episodes),
            "closedWinners": describe(winners), "closedLosers": describe(losers), "closedInitialStopLosses": describe(stops),
            "source": case["evidence"]["result"], "parentOpenEpisodesExcluded": case["openCount"]}
        rows.append(row)
        print(json.dumps({"board": row["board"], "policy": row["policy"], "closed": len(episodes), "initialStopLosses": len(stops),
            "lossMeanPct": row["closedLosers"]["returnPct"]["mean"], "stopTriggerMeanPct": row["closedInitialStopLosses"]["triggerCloseGainPct"]["mean"],
            "winnerPreTRPct20": row["closedWinners"]["preEntryMeanTRPct20"]["mean"], "loserPreTRPct20": row["closedLosers"]["preEntryMeanTRPct20"]["mean"]}), flush=True)
    write(OUT / "entry-risk-diagnostic.json", {"schema": "reclaim-entry-risk-description-v1", "completedAt": datetime.now(timezone.utc).isoformat(),
        "definition": ref(OUT / "entry-risk-diagnostic-definition.json"), "rows": rows, "allEpisodeLinksAndParentMeansChecked": True,
        "allPreEntryVolatilityEndsOnSignalClose": True, "newMarketAccounts": 0, "newStrategyTrials": 0,
        "hypothesisPromoted": False, "filteredStrategyProfitClaim": False, "targetAchieved": False})


if __name__ == "__main__":
    main()
