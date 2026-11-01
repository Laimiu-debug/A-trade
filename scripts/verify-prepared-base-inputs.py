"""Separate direct-window prepared-base oracle; production builder not imported."""
import importlib.util
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("_base_v2_prior_direct_windows",ROOT/"scripts/verify-market-sector-stock-inputs.py")
V=importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)
POLICIES=("completeSystem","withoutMarketAdmission","withoutSectorAdmission","withoutCatchupEntry")


def audit(matrix,joins,context,actual):
    source=V.DirectWindowReference(matrix,[],{})
    dates,symbols=matrix["calendar"].tolist(),matrix["symbols"].tolist()
    by_i={d["index"]:d for d in context["daily"]}
    maps={j["snapshotId"]:{g["sectorCode"]:g["cohortRowIndices"] for g in j["sectors"]} for j in joins}
    signals,metadata,counts={p:[] for p in POLICIES},{},[]
    for day in context["daily"]:
        i=day["index"]
        if i<65 or i-5 not in by_i:
            continue
        now,previous=source.stock(i),source.stock(i-1)
        old=by_i[i-5]
        earlier={s["sectorCode"]:s for s in old["sectors"]} if old["snapshotId"]==day["snapshotId"] else {}
        sectors=sorted((s for s in day["sectors"] if s["featureValid"]),key=lambda s:(-s["score"],s["sectorCode"]))
        pool={}
        for sector in sectors:
            code=sector["sectorCode"];rows=maps[day["snapshotId"]][code]
            ranks20,ranks60=(V.ranks(now[k][rows]) for k in ("r20","r60"))
            old_leaders=earlier.get(code,{}).get("leaders",[])
            healthy=[r for r in old_leaders if np.isfinite(now["ma20"][r]) and now["close"][r]>now["ma20"][r]]
            choices=[]
            for j,r in enumerate(rows):
                c,n,a=now["close"][r],now["ntr"][r],now["priorA20"][r]
                if not np.isfinite([c,n,a,now["ma20"][r],now["ma60"][r],now["r60"][r],now["r20"][r]]).all() or a<=0:
                    continue
                if not (now["r60"][r]>0 and c>now["ma60"][r] and c<=now["ma20"][r]+2*n*c):
                    continue
                valid=True
                for t in range(i-10,i):
                    if not matrix["priceValid"][r,t] or not all(np.isfinite(matrix[k][r,t]) for k in ("open","high","low","close","amount")) or matrix["amount"][r,t]<=0:
                        valid=False;break
                if not valid:continue
                floor=min(matrix["low"][r,t] for t in range(i-5,i))
                if not (0<n<1 and 0<floor<c and floor>=.95*c and floor<=c*(1-n)):
                    continue
                touch=[]
                for t in range(i-5,i):
                    local=source.stock(t)
                    if not np.isfinite([local["ma20"][r],local["ma60"][r]]).all() or local["close"][r]<=local["ma60"][r]:
                        valid=False;break
                    if matrix["low"][r,t]<=local["ma20"][r]:touch.append(dates[t])
                if not valid or not touch:continue
                recent_range=max(matrix["high"][r,t] for t in range(i-5,i))-floor
                earlier_range=max(matrix["high"][r,t] for t in range(i-10,i-5))-min(matrix["low"][r,t] for t in range(i-10,i-5))
                recent_amount=sum(matrix["amount"][r,t] for t in range(i-5,i))/5
                if not (recent_range<earlier_range and recent_amount<=a and c>now["high5"][r] and previous["close"][r]<=previous["high5"][r] and now["amount"][r]>a):continue
                leader=r in sector["leaders"] and r in healthy
                lag=now["r20"][r]<=sector["return20"] and .2<=ranks20[j]<.8 and any(q!=r for q in healthy)
                pattern="leaderBase" if leader else "catchup" if lag else None
                if pattern is None:continue
                info={"pattern":pattern,"signalClose":float(c),"failurePrice":float(floor),"meanNtr20":float(n),
                    "sectorCode":code,"snapshotId":day["snapshotId"],"sectorScore":sector["score"],"stockReturn60Midrank":float(ranks60[j]),
                    "priorAmount20":float(a),"priorLeaderRows":healthy,
                    "base":{"earlierFrom":dates[i-10],"earlierThrough":dates[i-6],"recentFrom":dates[i-5],"recentThrough":dates[i-1],
                        "earlierRange":float(earlier_range),"recentRange":float(recent_range),"recentMeanAmount":float(recent_amount),"supportTouchDates":touch,"failurePrice":float(floor)}}
                choices.append(((0 if leader else 1,-a,-ranks60[j],symbols[r]),r,info))
            pool[code]=choices
            if choices:counts.append({"date":day["date"],"sectorCode":code,"leaderBase":sum(c[2]["pattern"]=="leaderBase" for c in choices),"catchup":sum(c[2]["pattern"]=="catchup" for c in choices)})
        for policy in POLICIES:
            if policy!="withoutMarketAdmission" and day["market"]["admission"] is not True:continue
            chosen=[]
            for sector in sectors:
                code=sector["sectorCode"]
                if policy!="withoutSectorAdmission" and code not in day["selectedSectors"]:continue
                eligible=[r for r in pool[code] if policy!="withoutCatchupEntry" or r[2]["pattern"]!="catchup"]
                if eligible:chosen.append(sorted(eligible,key=lambda r:r[0])[0])
            for rank,(_,r,info) in enumerate(chosen[:5],1):
                identifier=f"prepared-base-v2:{policy}:{day['date']}:{symbols[r]}"
                s={"signalId":identifier,"strategyId":"marketSectorStockV1","symbol":symbols[r],"signalDate":day["date"],"originalRank":rank}
                signals[policy].append(s);metadata[identifier]={**info,**{k:v for k,v in s.items() if k!="strategyId"}}
    V.same(actual["schedules"],signals,"all policies schedules")
    V.same(actual["metadata"],metadata,"all source-window evidence")
    V.same(actual["candidateCounts"],counts,"all candidate counts")
    return {"passed":True,"decisionDays":len(context["daily"]),"signals":sum(len(v) for v in signals.values()),
        "allPreparationRoleSupportRankAndScheduleFieldsChecked":True,"macroIndustryContext":"Unchanged previously independently audited V1 causal contexts; bound separately by preparation plan"}
