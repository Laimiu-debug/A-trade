"""Bind two issuer action notices to demonstrably affected frozen holdings.

This explains model contamination; it does NOT correct or rerun an account.
Fractional-share allocation and deferred tax are deliberately not invented.
"""
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"artifacts/strategy-search/reclaim-corporate-action-audit-v1"
spec=importlib.util.spec_from_file_location("_action_audit_util",ROOT/"scripts/run-liquid-trend-breakout.py")
U=importlib.util.module_from_spec(spec);spec.loader.exec_module(U)

CASES=(
 {"symbol":"sh688195","name":"腾景科技","publishDate":"2026-05-23","registrationDate":"2026-05-28","exDate":"2026-05-29",
  "newSharesAvailableDate":"2026-05-29","cashPayDate":"2026-05-29","actualCashGross":"0.164","actualShareIncrease":"0.4",
  "priceReferenceCash":"0.164","priceReferenceShareIncrease":"0.4","differential":False,
  "publicPdf":"https://dataclouds.cninfo.com.cn/shgonggao/hsomarket/2026/20260522/53022a39bc234add95ce19cfdb26d41d.PDF"},
 {"symbol":"sh688200","name":"华峰测控","publishDate":"2026-06-05","registrationDate":"2026-06-10","exDate":"2026-06-11",
  "newSharesAvailableDate":"2026-06-11","cashPayDate":"2026-06-11","actualCashGross":"1.19","actualShareIncrease":"0.48",
  "priceReferenceCash":"1.1890","priceReferenceShareIncrease":"0.4796","differential":True,
  "publicPdf":"https://static.cninfo.com.cn/finalpage/2026-06-05/1225351734.PDF"},
)


def main():
    evidence=ROOT/"artifacts/strategy-search/reclaim-2026-replication-v1/star"
    result=U.read(evidence/"expanding.result.json.gz")
    summary=U.read(evidence/"expanding.summary.json")
    proof=U.read(evidence.parent/"validation.json")
    if not proof["passed"] or proof["readout"]!=U.ref(evidence.parent/"readout.json"):raise ValueError("Bound model result required")
    plan=U.read(evidence.parent/"plan.json");inputs=U.read(evidence.parent/"input-validation.json")
    source=next(g["source"] for g in inputs["groups"] if g["board"]=="star")
    data=U.read(U.bound(source));securities={r["symbol"]:r for r in data["securities"]}
    daily={r["date"]:r for r in result["daily"]};cases=[]
    for case in CASES:
        symbol=case["symbol"];pdf=OUT/"sources"/(symbol[2:]+"-distribution-2026.pdf")
        if not pdf.read_bytes().startswith(b"%PDF-"):raise ValueError("PDF magic missing")
        text="".join(pdf.with_suffix('.txt').read_text('utf-8').split())
        if symbol[2:] not in text or "每股现金红利"+case['actualCashGross'] not in text or "每股转增"+case['actualShareIncrease']+"股" not in text:
            raise ValueError("Manually reviewed terms do not match archived issuer text")
        bars={r["date"]:r for r in securities[symbol]["bars"]};calendar=data["calendar"];at=calendar.index(case["exDate"])
        prior=bars[calendar[at-1]];current=bars[case["exDate"]]
        held=next(h for h in daily[case['registrationDate']]['holdings'] if h['symbol']==symbol)
        after=next(h for h in daily[case['exDate']]['holdings'] if h['symbol']==symbol)
        if held['quantity']!=after['quantity']:raise ValueError("Expected unprocessed share count differs")
        quarantine=[{k:r.get(k) for k in ("date","symbol","action","status","reason")} for r in result['decisions'] if r.get('symbol')==symbol and r.get('reason')=='rawCloseDiscontinuity']
        if not any(q['date']==case['exDate'] for q in quarantine):raise ValueError("Expected affected-day quarantine absent")
        pending=result['forcedExits'].get(symbol)
        if pending is None or pending['signalDate']!=case['exDate']:raise ValueError("Expected persistent original exit absent")
        qty=Decimal(str(held['quantity']));previous=Decimal(str(prior['close']));price=Decimal(str(current['close']))
        cash=Decimal(case['actualCashGross']);increase=Decimal(case['actualShareIncrease'])
        reference=((previous-Decimal(case['priceReferenceCash']))/(1+Decimal(case['priceReferenceShareIncrease']))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        theoretical_qty=qty*(1+increase);gross_cash=qty*cash
        old_mark=qty*price;illustration=theoretical_qty*price+gross_cash
        record={**case,"issuerPdf":U.ref(pdf),"issuerBulletinQuery":U.ref(OUT/'sources'/(symbol[2:]+'-sse-bulletins.json')),
            "sourceData":source,"modelResult":U.ref(evidence/'expanding.result.json.gz'),
            "registeredSharesInModel":int(qty),"exDayModelShares":after['quantity'],"priorClose":float(previous),"rawExDayClose":float(price),
            "rawCloseChangePct":float((price/previous-1)*100),"announcementExReferenceRounded":float(reference),
            "closeVersusAnnouncementExReferencePct":float((price/reference-1)*100),
            "theoreticalProportionalSharesBeforeFractionAllocation":float(theoretical_qty),"cashGrossEntitlementBeforeDeferredTax":float(gross_cash),
            "uncorrectedExDayPositionMark":float(old_mark),"illustrativeSharesPlusGrossCashMark":float(illustration),
            "illustrativeMissingAssetsBeforeTaxAndFractionAllocation":float(illustration-old_mark),
            "quarantine":quarantine,"stillPendingAtCutoff":{k:pending[k] for k in ('signalDate','reason')},
            "actualNewSharesFractionAllocationKnown":False,"investorTaxAndSaleTimeReconciled":False,
            "notACorrectedAccount":True,"interpretation":"Company notice explains the apparent raw price discontinuity. The passive mark illustration is not executable account return; fractional-share allocation, deferred tax, price-reference changes, signal history, exits, cash and capacity require a new complete replay."}
        cases.append(record)
        print(json.dumps({k:record[k] for k in ('symbol','exDate','rawCloseChangePct','announcementExReferenceRounded','closeVersusAnnouncementExReferencePct','illustrativeMissingAssetsBeforeTaxAndFractionAllocation')},ensure_ascii=False),flush=True)
    rows=[]
    for study in ('bull-reclaim-liquidity-v1','reclaim-2026-replication-v1'):
        readout=U.read(ROOT/'artifacts/strategy-search'/study/'readout.json')
        for r in readout['rows']:
            rows.append({'study':study,'board':r['board'],'policy':r['policy'],'valuationUnreliableDays':r['valuationUnreliableDays'],
                         'scope':'Original raw-price model outcome only; no economic-certification claim','originalEvidence':r['evidence']})
    report={'schema':'reclaim-action-impact-audit-v1','completedAt':U.now(),'builder':U.ref(Path(__file__)),'cases':cases,'modelQualityRows':rows,
        'priceDiscontinuityExplainedForTwoHoldings':True,'technicalArithmeticAuditStillValid':True,'economicFitnessForGoalProven':False,
        'fullCorporateActionLedgerComplete':False,'newAccounts':0,'newStrategyTrials':0,'targetAchieved':False,
        'effectScope':['Historical comparability of support/ranks','Shares and gross/deferred cash','Risk references and volatility windows','Quarantine and exit execution','Cash and future slot allocation','Net payoff distribution and account drawdown'],
        'nextWork':'Build and validate a dated action-aware source/feature/account contract before another profitability claim. Do not remove quarantine, drop affected stocks after observing their outcomes, hand-adjust only ending equity, or use later-finalized terms at an earlier decision date.'}
    U.write(OUT/'readout.json',report)


if __name__=='__main__':main()
