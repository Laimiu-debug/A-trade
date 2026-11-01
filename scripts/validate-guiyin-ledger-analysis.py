"""Independent source replay and canonical statistics for the first ledger."""
from collections import defaultdict, Counter
from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1'
ANALYSIS = BASE / 'guiyin-ledger-audit-001'
OUT = BASE / 'guiyin-ledger-validation-001'


def ref(p):
    return {'path': p.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}


def write(p, x):
    p.write_text(json.dumps(x, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def stats(es, per_side_bps=D(0)):
    values=[]
    for e in es:
        rate=per_side_bps/D(10000)
        buy=D(e['buyOutlay'])+D(e['buyGross'])*rate
        # Original sell gross = net proceeds + charges paid on those sale rows.
        sell=D(e['sellNet'])-D(e['sellGross'])*rate
        pnl=sell-buy
        values.append((e,pnl,pnl/buy))
    w=[x for x in values if x[1]>0];l=[x for x in values if x[1]<0]
    avg=lambda xs,k:sum(x[k] for x in xs)/len(xs) if xs else None
    wr,lr=avg(w,2),avg(l,2);wp=sum(x[1] for x in w);lp=-sum(x[1] for x in l)
    order=sorted(values,key=lambda x:x[1],reverse=True)
    return {'episodes':len(es),'wins':len(w),'losses':len(l),'breakeven':len(values)-len(w)-len(l),
            'winRate':len(w)/len(es) if es else None,'meanWinReturn':float(wr) if wr is not None else None,
            'meanLossReturn':float(lr) if lr is not None else None,'returnPayoffRatio':float(wr/-lr) if wr is not None and lr else None,
            'monetaryPayoffRatio':float((wp/len(w))/(lp/len(l))) if w and l else None,
            'profitFactor':float(wp/lp) if lp else None,'netClosedPnlYuan':str(wp-lp),
            'medianCycleReturn':float(statistics.median(x[2] for x in values)) if values else None,
            'worstCycleReturn':float(min(x[2] for x in values)) if values else None,
            'topFiveShareOfGrossProfits':float(sum(x[1] for x in order[:5])/wp) if wp and len(w)>=5 else None,
            'meanWinCalendarDays':statistics.mean(x[0]['holdingCalendarDays'] for x in w) if w else None,
            'meanLossCalendarDays':statistics.mean(x[0]['holdingCalendarDays'] for x in l) if l else None,
            'medianWinCalendarDays':statistics.median(x[0]['holdingCalendarDays'] for x in w) if w else None,
            'medianLossCalendarDays':statistics.median(x[0]['holdingCalendarDays'] for x in l) if l else None,
            'extraCostPerSideBps':str(per_side_bps),'scenarioIsActualFinancingInterest':False}


def main():
    if OUT.exists():raise ValueError('Preserve completed validation')
    raw_path=BASE/'guiyin-original-001/归因.xls'
    wheel=BASE/'analysis-runtime-xlrd-2.0.2/xlrd-2.0.2-py2.py3-none-any.whl'
    sys.path.insert(0,str(wheel));import xlrd
    sheet=xlrd.open_workbook(raw_path).sheet_by_index(0)
    trades=json.loads((ANALYSIS/'trades.json').read_text(encoding='utf-8'))
    episodes=json.loads((ANALYSIS/'episodes.json').read_text(encoding='utf-8'))
    old=json.loads((ANALYSIS/'readout.json').read_text(encoding='utf-8'))
    by_row={t['sourceRow']:t for t in trades}
    raw_checks=0
    for t in trades:
        a=sheet.row_values(t['sourceRow']-1)
        assert a[1]==t['code'] and a[2]==t['name'] and a[3]==t['business']
        assert D(str(a[5]))==t['quantity'] and D(str(a[7]))==D(t['grossYuan']) and D(str(a[8]))==D(t['tradeCashDeltaYuan'])
        assert D(str(a[11]))==t['afterQuantity'];raw_checks+=1
    canonical=[];covered=[]
    for e in episodes:
        rows=[sheet.row_values(i-1) for i in e['rows']]
        assert sum(int(a[5]) for a in rows)==e.get('endingQuantity',0)-e['openingQuantity']
        if e['status']!='closed':continue
        buy=sum(-D(str(a[8])) for a in rows if a[5]>0)
        sell=sum(D(str(a[8])) for a in rows if a[5]<0)
        assert buy==D(e['buyOutlay']) and sell==D(e['sellNet'])
        assert sell-buy==D(e['netPnlAfterVisibleTransactionChargesYuan'])
        c=dict(e);c['securityType']='ETF' if e['code'].startswith(('1','5')) else 'equity'
        if c['securityType']=='ETF':c['board']='ETF'
        c['sellGross']=str(sum(D(str(a[7])) for a in rows if a[5]<0))
        canonical.append(c);covered.extend(e['rows'])
    assert len(covered)==len(set(covered))
    # T+1 compatibility: new purchases do not add to a stock's available shares.
    available={};prev_date={};end={};t1_violations=[]
    for t in trades:
        if t['code'].startswith(('1','5')):continue
        code=t['code']
        if prev_date.get(code)!=t['date']:
            available[code]=t['beforeQuantity'];prev_date[code]=t['date']
        if t['quantity']<0:
            available[code]+=t['quantity']
            if available[code]<0:t1_violations.append(t['sourceRow'])
        end[code]=t['afterQuantity']
    assert not t1_violations
    equity=[e for e in canonical if e['securityType']=='equity']
    post=[e for e in equity if e['entryDate']>='2024-09-24']
    pre=[e for e in equity if e['lastExitDate']<'2024-09-24']
    carry=[e for e in equity if e['entryDate']<'2024-09-24'<=e['lastExitDate']]
    summary={k:stats(v) for k,v in [('allCompleteEquityCycles',equity),('primaryPostSplitEntryCycles',post),('preSplitFullyClosedStress',pre),('carryInClosedAfterSplit',carry)]}
    assert len(canonical)==219 and len(equity)==216 and len(post)==134
    assert abs(summary['primaryPostSplitEntryCycles']['returnPayoffRatio']-old['cohorts']['primaryEntryOnOrAfterSplit']['realizedReturnPayoffRatioAfterVisibleCharges'])<1e-12
    # Actual finance-relevant regression cases: partial-sale/rebuy grouping,
    # opening inventory, unfinished positions and an invalid balance sequence.
    module_path=ROOT/'scripts/analyze-guiyin-original-ledger.py'
    spec=importlib.util.spec_from_file_location('guiyin_reconstruction_under_test',module_path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    def t(row,qty,before,after,net,gross):
        return {'sourceRow':row,'code':'000001','name':'fixture','date':'2024-10-08' if row<4 else '2024-10-09',
                'quantity':qty,'beforeQuantity':before,'afterQuantity':after,'grossYuan':str(gross),
                'tradeCashDeltaYuan':str(net),'visibleChargesYuan':'1','business':'证券买入' if qty>0 else '证券卖出'}
    v=[t(1,100,0,100,-1001,1000),t(2,-40,100,60,479,480),t(3,40,60,100,-441,440),t(4,-100,100,0,1299,1300)]
    reconstructed,_=mod.reconstruct(v)
    assert len(reconstructed)==1 and D(reconstructed[0]['netPnlAfterVisibleTransactionChargesYuan'])==336
    v=[t(1,-50,50,0,499,500),t(2,10,0,10,-101,100),t(4,-10,10,0,111,112)]
    reconstructed,_=mod.reconstruct(v)
    assert Counter(e['status'] for e in reconstructed)=={'opening-inventory-cost-missing':1,'closed':1}
    reconstructed,_=mod.reconstruct([t(1,100,0,100,-1001,1000),t(2,-40,100,60,479,480)])
    assert reconstructed[0]['status']=='open' and reconstructed[0]['netPnlAfterVisibleTransactionChargesYuan'] is None
    try:mod.reconstruct([t(1,100,0,100,-1001,1000),t(2,-40,120,80,479,480)])
    except ValueError:pass
    else:raise AssertionError('Discontinuous inventory was not rejected')
    primary=summary['primaryPostSplitEntryCycles']
    rank=sorted(post,key=lambda e:D(e['netPnlAfterVisibleTransactionChargesYuan']),reverse=True)
    removed={f'withoutTop{k}Winners':stats([e for e in post if e['episodeId'] not in {x['episodeId'] for x in rank[:k]}]) for k in [1,5]}
    # A source cross-check, not an independently certified broker statement.
    snapshot={'sourceUrl':'https://m.tgb.cn/a/2eqTaiSffPs','image':ref(BASE/'guiyin-first-person-001/2024-12-31-1.png'),
              'totalAssetsYuan':'26645647.64','liabilitiesYuan':'3174874.28','netAssetsYuan':'23470773.36',
              'holdingsValueYuan':'4173882.00','availableFundsYuan':'22471765.64','positionPct':'17.8',
              'holdings':[{'code':'300548','name':'博创科技','valueYuan':'3480750.00','price':'46.410','impliedQuantity':75000},
                          {'code':'603286','name':'日盈电子','valueYuan':'693132.00','price':'19.580','impliedQuantity':35400}],
              'quantityIsDerivedFromDisplayedValueAndPrice':True,'screenshotIndependentlyAuthenticated':False}
    assert D(snapshot['totalAssetsYuan'])-D(snapshot['liabilitiesYuan'])==D(snapshot['netAssetsYuan'])
    assert D(snapshot['availableFundsYuan'])+D(snapshot['holdingsValueYuan'])==D(snapshot['totalAssetsYuan'])
    for h in snapshot['holdings']:
        assert D(h['valueYuan'])/D(h['price'])==h['impliedQuantity']==old['endingInventory'][h['code']]
    snapshot['worksheetRemainingAmountMinusDisplayedAvailableFundsYuan']=str(D(trades[-1]['sourceRemainingAmountYuan'])-D(snapshot['availableFundsYuan']))
    result={'schema':'guiyin-canonical-equity-cycle-validation-v1','completedAt':datetime.now(timezone.utc).isoformat(),
            'builder':ref(Path(__file__)),'originalReadout':ref(ANALYSIS/'readout.json'),'source':ref(raw_path),
            'sourceTradeRowsReplayed':raw_checks,'completeSecurityCyclesVerified':len(canonical),'completeEquityCycles':len(equity),
            'nonEquityCyclesKeptSeparate':[e for e in canonical if e['securityType']!='equity'],
            'correctionsToFirstReadout':['Three 沙特ETF cycles were included in the original all-securities count and labeled main board. Canonical equity count216 excludes them; primary post-split count134 and its payoff are unchanged.',
                                         'For even sample sizes, the first readout selected the upper middle return. Canonical medians average the two middle values; payoff, PF and net-PnL statistics are unchanged.'],
            'summary':summary,'primaryPerSideAdditionalCostStress':[stats(post,D(bps)) for bps in [0,10,25,50]],
            'profitConcentrationRemovalStress':removed,
            'primaryBoardCohorts':{b:stats([e for e in post if e['board']==b]) for b in sorted(set(e['board'] for e in post))},
            'primaryFinancingMarkerCohorts':{'withFinancedBuyRows':stats([e for e in post if e['financedBuyRows']]),
                                             'withoutFinancedBuyRows':stats([e for e in post if not e['financedBuyRows']])},
            'financingMarkerCohortsDoNotIdentifyUnleveragedAccountReturns':True,
            'sourceTPlusOneCompatibilityViolations':t1_violations,'financialRegressionCasesPassed':4,
            'yearEndFirstPersonSnapshot':snapshot,'primaryStatisticsMatchIndependentRawCashReplay':True,
            'accountNetReturnAudited':False,'completeFinancingCostAudit':False,'causalEntrySignalValidated':False,
            'newStrategyTrials':0,'newStrategyAccounts':0,'targetAchieved':False}
    OUT.mkdir();write(OUT/'canonical-closed-cycles.json',canonical);result['canonicalCycles']=ref(OUT/'canonical-closed-cycles.json');write(OUT/'readout.json',result)
    print(json.dumps({'validation':ref(OUT/'readout.json'),'sourceRowsReplayed':raw_checks,'equityCycles':len(equity),'primary':primary,
                      'costStress':result['primaryPerSideAdditionalCostStress'],'removalStress':removed,'yearEndNetAssets':snapshot['netAssetsYuan']},ensure_ascii=True))


if __name__=='__main__':main()
