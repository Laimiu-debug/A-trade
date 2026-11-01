"""Frozen signal-day location/relative-strength description, no outcomes read."""
from collections import Counter
import importlib.util
from pathlib import Path
from statistics import median
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_long_entry_context_utils',ROOT/'scripts/run-liquid-trend-breakout.py')
U=importlib.util.module_from_spec(spec);sys.modules[spec.name]=U;spec.loader.exec_module(U)
STUDY=ROOT/'artifacts/strategy-search/sector-long-breakout-system-v4/attempt-001'


def build():
    plan=U.read(STUDY/'plan.json');inputs=U.read(STUDY/'input-validation.json')
    if not inputs['passed'] or inputs['planSha256']!=U.sha(STUDY/'plan.json'):raise ValueError('Validated frozen inputs required')
    formation=U.read(U.bound(inputs['formation']));context=U.read(U.bound(plan['originalAdmissionContext']))
    with np.load(U.bound(plan['matrix']),allow_pickle=False) as data:
        dates,symbols,close=data['calendar'].tolist(),data['symbols'].tolist(),data['close']
    date_index={d:i for i,d in enumerate(dates)};stock_index={s:i for i,s in enumerate(symbols)};days={d['date']:d for d in context['daily']}
    rows=[]
    for signal in formation['schedules']['withoutCatchupEntry']:
        if signal['signalDate']<'2024-09-24':continue
        m=formation['metadata'][signal['signalId']];i,r=date_index[signal['signalDate']],stock_index[signal['symbol']]
        sector=next(s for s in days[signal['signalDate']]['sectors'] if s['sectorCode']==m['sectorCode'])
        current20=float(close[r,i]/close[r,i-20]-1)
        rows.append({'date':signal['signalDate'],'symbol':signal['symbol'],'sectorCode':m['sectorCode'],
            'triggerDayReturnPct':float((close[r,i]/close[r,i-1]-1)*100),
            'closeAbovePrior120HighPct':(m['signalClose']/m['longEvidence']['priorHigh120']-1)*100,
            'prior20ReturnPct':m['longEvidence']['prior20Return']*100,'current20ReturnPct':current20*100,
            'industry20ReturnPct':sector['return20']*100,'current20RelativePp':(current20-sector['return20'])*100,
            'signalNtr20Pct':m['meanNtr20']*100,'failureDistanceInNtr':(m['signalClose']-m['failurePrice'])/(m['signalClose']*m['meanNtr20'])})
    fields=('triggerDayReturnPct','closeAbovePrior120HighPct','prior20ReturnPct','current20RelativePp','signalNtr20Pct','failureDistanceInNtr')
    summary={k:{'min':min(r[k] for r in rows),'median':median(r[k] for r in rows),'max':max(r[k] for r in rows)} for k in fields}
    folder=STUDY/'entry-context-001';folder.mkdir()
    U.write(folder/'readout.json',{'schema':'frozen-long-entry-context-v1','builder':U.ref(Path(__file__)),'inputs':U.ref(STUDY/'input-validation.json'),
        'formation':inputs['formation'],'matrix':plan['matrix'],'originalAdmission':plan['originalAdmissionContext'],'rows':rows,'summary':summary,
        'signals':len(rows),'current20NotAboveIndustryMean':sum(r['current20RelativePp']<=0 for r in rows),'sectors':dict(Counter(r['sectorCode'] for r in rows)),
        'accountOutcomesRead':False,'parameterSearch':False,'newStrategyTrials':0,'newAccounts':0,'targetAchieved':False,
        'scope':'All primary frozen long-leader signals. Source-only descriptive location and relative strength; no conditional return or profitable replacement rule inferred.'})
    U.emit({'readout':U.ref(folder/'readout.json'),'signals':len(rows),'current20NotAboveIndustryMean':sum(r['current20RelativePp']<=0 for r in rows),'summary':summary})


if __name__=='__main__':build()
