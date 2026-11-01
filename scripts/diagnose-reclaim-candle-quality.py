"""Zero-account admission diagnostic, not a counterfactual strategy backtest.

Two simple causal interpretations are declared here before cohort summaries:
positive reversal close (C>O and C>previous close), and previous C>priorMA60.
Observed original-account episode subsets cannot prove a filtered account edge.
"""
import gc
import hashlib
import json
import math
from pathlib import Path
import importlib.util

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"artifacts/strategy-search/reclaim-entry-quality-diagnostic-v1"
spec=importlib.util.spec_from_file_location("_entry_diag_util",ROOT/"scripts/run-liquid-trend-breakout.py")
U=importlib.util.module_from_spec(spec);spec.loader.exec_module(U)


def features(bars,calendar,at):
    def positive(v):return type(v) in (int,float) and math.isfinite(v) and v>0
    current=bars.get(calendar[at]);previous=bars.get(calendar[at-1]) if at else None
    if current is None or previous is None or not all(positive(v) for v in (current.get("close"),current.get("open"),previous.get("close"))):
        return {"reversal":"unknown","trend":"unknown","priorMA60":None}
    reversal=current["close"]>current["open"] and current["close"]>previous["close"]
    rows=[bars.get(d) for d in calendar[max(0,at-60):at]]
    known=len(rows)==60 and all(r is not None and positive(r.get("close")) and positive(r.get("volume")) for r in rows)
    ma=math.fsum(r["close"] for r in rows)/60 if known else None
    return {"reversal":"passed" if reversal else "failed","trend":"unknown" if ma is None else "passed" if previous["close"]>ma else "failed",
            "priorMA60":ma,"formationClose":current["close"],"formationOpen":current["open"],"previousClose":previous["close"]}


def stats(episodes):
    wins=[r["netReturnPct"] for r in episodes if r["pnlAmount"]>0];losses=[r["netReturnPct"] for r in episodes if r["pnlAmount"]<0]
    gp=math.fsum(r["pnlAmount"] for r in episodes if r["pnlAmount"]>0);gl=-math.fsum(r["pnlAmount"] for r in episodes if r["pnlAmount"]<0)
    mw=math.fsum(wins)/len(wins) if wins else None;ml=math.fsum(losses)/len(losses) if losses else None
    return {"closed":len(episodes),"wins":len(wins),"losses":len(losses),"R":mw/-ml if mw is not None and ml is not None else None,
            "PF":gp/gl if gl else None,"meanWinPct":mw,"meanLossPct":ml,"netPnl":math.fsum(r["pnlAmount"] for r in episodes)}


def main():
    if OUT.exists():raise ValueError("Diagnostic already exists; do not refit silently")
    OUT.mkdir()
    U.write(OUT/"definition.json",{"registeredAt":U.now(),"builder":U.ref(Path(__file__)),"tests":0,"newAccounts":0,"newStrategyTrials":0,
        "features":{"reversal":"At original signal T: close_T>open_T and close_T>close_T-1, exact calendar previous session.",
                    "trend":"At original signal T: close_T-1>mean(close_T-60..T-1), all60 exact positive-close/volume observations required."},
        "interpretation":"Descriptive original-account closed-episode subsets only; not a new cash account, not a pure holdout, no causal effect or promotion."})
    results=[]
    for period,study in (("bull924","bull-reclaim-liquidity-v1"),("2026","reclaim-2026-replication-v1")):
        folder=ROOT/"artifacts/strategy-search"/study;plan=U.read(folder/"plan.json")
        validation=U.read(folder/"validation.json")
        if not validation["passed"] or validation["readout"]!=U.ref(folder/"readout.json"):raise ValueError("Audited results required")
        for board in ("main","chinext","star"):
            if period=="bull924":source=plan["sourceData"][board]["accountData"]
            else:source=next(g["source"] for g in U.read(folder/"input-validation.json")["groups"] if g["board"]==board)
            data=U.read(U.bound(source));calendar=data["calendar"];indices={d:i for i,d in enumerate(calendar)}
            maps={s["symbol"]:{r["date"]:r for r in s["bars"]} for s in data["securities"]}
            schedule=U.read(folder/board/"original.signals.json")
            details=[{**s,**features(maps[s["symbol"]],calendar,indices[s["signalDate"]])} for s in schedule]
            by_id={s["signalId"]:s for s in details}
            result=U.read(folder/board/"original.result.json.gz");summary=U.read(folder/board/"original.summary.json")
            fills={(r["symbol"],r["date"]):r["signalId"] for r in result["trades"] if r["side"]=="buy"}
            episodes=[]
            for e in summary["closedEpisodes"]["episodes"]:
                record=e["record"];signal_id=fills[(record["symbol"],record["entryDate"])];f=by_id[signal_id]
                episodes.append({"signalId":signal_id,"netReturnPct":e["netReturnPct"],"pnlAmount":e["pnlAmount"],
                                 "reversal":f["reversal"],"trend":f["trend"]})
            partitions={key:{state:stats([e for e in episodes if e[key]==state]) for state in ("passed","failed","unknown")} for key in ("reversal","trend")}
            counts={key:{state:sum(r[key]==state for r in details) for state in ("passed","failed","unknown")} for key in ("reversal","trend")}
            row={"period":period,"board":board,"originalSchedule":U.ref(folder/board/"original.signals.json"),"source":source,
                 "allOriginalClosed":stats(episodes),"signalCounts":counts,"actualOriginalEpisodePartitions":partitions}
            results.append(row);U.write(OUT/(period+"-"+board+".signals.json"),details)
            print(json.dumps({"period":period,"board":board,"all":row["allOriginalClosed"],"signals":counts,"closedPartitions":partitions},ensure_ascii=False),flush=True)
            del data,maps,result,summary,details,episodes;gc.collect()
    U.write(OUT/"readout.json",{"completedAt":U.now(),"definition":U.ref(OUT/"definition.json"),"rows":results,
         "newAccounts":0,"newStrategyTrials":0,"targetAchieved":False,"holdoutClaim":False,
         "nextBoundary":"Only a preregistered complete cash-account comparison can evaluate either admission. This diagnostic does not imply its skipped trades or PnL would transfer unchanged."})


if __name__=="__main__":main()
