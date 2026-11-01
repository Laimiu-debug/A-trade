"""Dated prepared-base entries inside the frozen market/industry account."""
from copy import deepcopy
import hashlib
from pathlib import Path

import numpy as np

from .research_market_sector_stock import stock_features, midranks, SID
from .research_market_sector_account import MarketSectorStockAccount
from .research_volatility_trail import _positive

VERSION = "prepared-base-system-v2"
POLICIES = ("completeSystem", "withoutMarketAdmission", "withoutSectorAdmission", "withoutCatchupEntry")
ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = ("docs/market-sector-stock-base-v2.json", "service/research_prepared_base_system.py")


def source_pins():
    return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCE_FILES}


def coherent(price, floor, ntr):
    # Price-space comparisons include the exact five-percent boundary without
    # a subtraction/division rounding it to .050000000000000044.
    return bool(0 < ntr < 1 and 0 < floor < price and .95*price <= floor <= price*(1-ntr))


def build_inputs(matrix, joins, verified_formation):
    """Reuse audited macro/industry states; replace only entry preparation."""
    f = stock_features(matrix)
    dates, symbols = matrix["calendar"].tolist(), matrix["symbols"].tolist()
    contexts = verified_formation["daily"]
    by_index = {r["index"]:r for r in contexts}
    maps = {j["snapshotId"]:{g["sectorCode"]:g["cohortRowIndices"] for g in j["sectors"]} for j in joins}
    schedules, metadata, candidate_counts = {p:[] for p in POLICIES}, {}, []
    for day in contexts:
        i = day["index"]
        if i < 65 or i-5 not in by_index:
            continue
        old = by_index[i-5]
        old_sectors = {s["sectorCode"]:s for s in old["sectors"]} if old["snapshotId"] == day["snapshotId"] else {}
        ordered = sorted((s for s in day["sectors"] if s["featureValid"]),key=lambda s:(-s["score"],s["sectorCode"]))
        candidates = {}
        for sector in ordered:
            code = sector["sectorCode"]
            rows = np.asarray(maps[day["snapshotId"]][code],int)
            ranks20, ranks60 = (midranks(f[k][rows,i]) for k in ("return20","return60"))
            old_leaders = old_sectors.get(code,{}).get("leaders",[])
            healthy = [r for r in old_leaders if np.isfinite(f["ma20"][r,i]) and f["close"][r,i] > f["ma20"][r,i]]
            leaders = set(sector["leaders"])
            choices = []
            for j,r in enumerate(rows.tolist()):
                close, ntr, amount = f["close"][r,i],f["meanNtr20"][r,i],f["priorAmount20"][r,i]
                if not np.isfinite([close,ntr,amount,f["ma20"][r,i],f["ma60"][r,i],f["return60"][r,i],f["return20"][r,i]]).all() or amount <= 0:
                    continue
                if not (f["return60"][r,i]>0 and close>f["ma60"][r,i] and close<=f["ma20"][r,i]+2*ntr*close):
                    continue
                recent, earlier = slice(i-5,i),slice(i-10,i-5)
                if not matrix["priceValid"][r,i-10:i].all() or not np.isfinite(matrix["amount"][r,i-10:i]).all() or not (matrix["amount"][r,i-10:i]>0).all():
                    continue
                if not all(np.isfinite(matrix[k][r,i-10:i]).all() for k in ("open","high","low","close")):
                    continue
                if not np.isfinite(f["ma20"][r,recent]).all() or not np.isfinite(f["ma60"][r,recent]).all():
                    continue
                floor = float(matrix["low"][r,recent].min())
                if not coherent(close,floor,ntr):
                    continue
                recent_range = float(matrix["high"][r,recent].max()-floor)
                earlier_range = float(matrix["high"][r,earlier].max()-matrix["low"][r,earlier].min())
                recent_amount = float(matrix["amount"][r,recent].mean())
                touch = matrix["low"][r,recent] <= f["ma20"][r,recent]
                if not ((f["close"][r,recent]>f["ma60"][r,recent]).all() and touch.any() and recent_range<earlier_range and recent_amount<=amount):
                    continue
                if not (close>f["priorHigh5"][r,i] and f["close"][r,i-1]<=f["priorHigh5"][r,i-1] and f["amount"][r,i]>amount):
                    continue
                leader = r in leaders and r in healthy
                catchup = f["return20"][r,i]<=sector["return20"] and .2<=ranks20[j]<.8 and any(q!=r for q in healthy)
                pattern = "leaderBase" if leader else "catchup" if catchup else None
                if pattern is None:
                    continue
                info = {"pattern":pattern,"signalClose":float(close),"failurePrice":floor,"meanNtr20":float(ntr),
                    "sectorCode":code,"snapshotId":day["snapshotId"],"sectorScore":sector["score"],
                    "stockReturn60Midrank":float(ranks60[j]),"priorAmount20":float(amount),"priorLeaderRows":healthy,
                    "base":{"earlierFrom":dates[i-10],"earlierThrough":dates[i-6],"recentFrom":dates[i-5],"recentThrough":dates[i-1],
                        "earlierRange":earlier_range,"recentRange":recent_range,"recentMeanAmount":recent_amount,
                        "supportTouchDates":[dates[i-5+k] for k in range(5) if touch[k]],"failurePrice":floor}}
                key = (0 if leader else 1,-amount,-ranks60[j],symbols[r])
                choices.append((key,r,info))
            candidates[code] = choices
            if choices:
                candidate_counts.append({"date":day["date"],"sectorCode":code,"leaderBase":sum(q[2]["pattern"]=="leaderBase" for q in choices),"catchup":sum(q[2]["pattern"]=="catchup" for q in choices)})
        for policy in POLICIES:
            if policy!="withoutMarketAdmission" and day["market"]["admission"] is not True:
                continue
            chosen = []
            for sector in ordered:
                code = sector["sectorCode"]
                if policy!="withoutSectorAdmission" and code not in day["selectedSectors"]:
                    continue
                eligible = [q for q in candidates[code] if policy!="withoutCatchupEntry" or q[2]["pattern"]!="catchup"]
                if eligible:
                    chosen.append(min(eligible,key=lambda q:q[0]))
            for rank,(_,r,info) in enumerate(chosen[:5],1):
                identifier=f"prepared-base-v2:{policy}:{day['date']}:{symbols[r]}"
                signal={"signalId":identifier,"strategyId":SID,"symbol":symbols[r],"signalDate":day["date"],"originalRank":rank}
                schedules[policy].append(signal)
                metadata[identifier]={**info,**{k:v for k,v in signal.items() if k!="strategyId"}}
    return {"version":VERSION,"schedules":schedules,"metadata":metadata,"candidateCounts":candidate_counts,
        "counts":{p:len(v) for p,v in schedules.items()},"contextDecisionDays":len(contexts)}


class PreparedBaseAccount(MarketSectorStockAccount):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.base_identity={"version":VERSION,"sourcePins":source_pins()}
        self._base_opening=[]
        for signal in self._schedule:
            m=self._system_meta[signal["signalId"]]
            if m["pattern"] not in ("leaderBase","catchup") or not coherent(m["signalClose"],m["failurePrice"],m["meanNtr20"]):
                raise ValueError("Prepared-base signal role/risk invalid")
            if m["base"]["failurePrice"]!=m["failurePrice"]:
                raise ValueError("Prepared support differs from fixed failure")

    def _identity_stable(self):
        super()._identity_stable()
        if hasattr(self,"base_identity") and self.base_identity["sourcePins"]!=source_pins():
            raise ValueError("Prepared-base execution source changed")

    def _transaction_snapshot(self):
        return super()._transaction_snapshot(),len(self._base_opening)

    def _transaction_restore(self,snapshot):
        parent,n=snapshot
        super()._transaction_restore(parent)
        del self._base_opening[n:]

    def _execute_order(self,day,symbol,side,requested,pending):
        if side=="buy":
            m=self._system_meta[pending["signalId"]]
            bar,_=self._bar(symbol,day)
            if bar is not None and _positive(bar.get("open")) and m["failurePrice"]<bar["open"]<=m["signalClose"]*(1+m["meanNtr20"]):
                price=self._fill_price(symbol,day,side)
                if price is not None:
                    reason="preparedBaseOpeningRiskTooWide" if m["failurePrice"]<.95*price else "preparedBaseOpeningRiskInsideOneNtr" if m["failurePrice"]>price*(1-m["meanNtr20"]) else None
                    self._base_opening.append({"date":day,"symbol":symbol,"signalId":pending["signalId"],"modeledPrice":price,
                        "failurePrice":m["failurePrice"],"signalNtr20":m["meanNtr20"],"status":"rejected" if reason else "passed","reason":reason})
                    if reason:
                        self._decision(day,symbol,"buy","deferred",reason,signalDate=pending["signalDate"])
                        return
        super()._execute_order(day,symbol,side,requested,pending)

    def _system_result(self):
        return {**super()._system_result(),"preparedBaseIdentity":deepcopy(self.base_identity),"preparedBaseOpeningGuards":deepcopy(self._base_opening)}
