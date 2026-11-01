"""Descriptive all-cycle diagnostics after a fixed run; never searches thresholds."""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean
import sys

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/strategy-search/guiyin-dual-path-system-v1'/('attempt-'+(sys.argv[1] if len(sys.argv)>1 else '001'))


def read(path):
    with (gzip.open(path,'rt',encoding='utf-8') if str(path).endswith('.gz') else path.open('r',encoding='utf-8')) as f:return json.load(f)


def stats(rows):
    wins=[r for r in rows if r['pnlAmount']>0];losses=[r for r in rows if r['pnlAmount']<0]
    a=mean(r['returnPct'] for r in wins) if wins else None;b=mean(r['returnPct'] for r in losses) if losses else None
    profit=sum(r['pnlAmount'] for r in wins);loss=-sum(r['pnlAmount'] for r in losses)
    return dict(closed=len(rows),wins=len(wins),losses=len(losses),meanWinReturnPct=a,meanLossReturnPct=b,
                payoffRatio=a/-b if a is not None and b is not None else None,profitFactor=profit/loss if loss else None,
                netClosedPnl=sum(r['pnlAmount'] for r in rows),worstReturnPct=min((r['returnPct'] for r in rows),default=None))


def main():
    result=read(OUT/'readout.json');assert read(OUT/'independent-account-validation.json')['passed']
    rows=read(OUT/'all-closed-cycles.json');maincycles=[r for r in rows if r['period']=='primary' and r['policy']=='complete']
    inputs=read(OUT/'input-validation.json')
    formation=read(ROOT/inputs['formation']['path']);source=read(ROOT/inputs['sourceCycleCoverage']['path'])
    candidate={(e['signalDate'],e['symbol'][2:]):e['pattern'] for e in formation['allCandidates']}
    timing=[]
    for row in source['rows']:
        same_day=candidate.get((row['entryDate'],row['code']))
        timing.append(dict(**row,entrySessionCloseDailyPath=same_day,entrySessionSignalKnownBeforeOriginalFill=False,
                          note='Same-session close is unavailable for reconstructing original intraday execution; this is a timing diagnostic only'))
    coverage=[]
    for basis,key in [('previous-close','previousCloseDailyPath'),('entry-session-close-after-original-entry','entrySessionCloseDailyPath')]:
        for path in ['A_ignition','B_corePullback',None]:
            group=[r for r in timing if r[key]==path]
            coverage.append(dict(basis=basis,path=path or 'notPresent',cycles=len(group),wins=sum(r['sourceOutcome']=='win' for r in group),losses=sum(r['sourceOutcome']=='loss' for r in group)))
    source_dates=[]
    for day in sorted(set(r['entryDate'] for r in timing)):
        group=[r for r in timing if r['entryDate']==day]
        source_dates.append(dict(entryDate=day,cycles=len(group),wins=sum(r['sourceOutcome']=='win' for r in group),
                                 losses=sum(r['sourceOutcome']=='loss' for r in group),meanCycleReturnPct=mean(r['sourceCycleReturnPct'] for r in group),
                                 previousCloseMarketAdmission=group[0]['marketAdmittedAtPreviousClose']))
    by_path=[dict(path=p,**stats([r for r in maincycles if r['pattern']==p])) for p in ['A_ignition','B_corePullback']]
    periods=[('sourceOverlap2024Q4','2024-09-24','2024-12-31'),('extension2025','2025-01-01','2025-12-31'),('extension2026','2026-01-01','2026-10-15')]
    by_period=[dict(period=label,grouping='entry date; later exits retained in their entry cohort',**stats([r for r in maincycles if lo<=r['entryDate']<=hi])) for label,lo,hi in periods]
    exits=[dict(exitReason=reason,**stats([r for r in maincycles if r['exitReason']==reason])) for reason in sorted(set(r['exitReason'] for r in maincycles))]
    extremes=dict(bestFive=sorted(maincycles,key=lambda r:r['returnPct'],reverse=True)[:5],worstFive=sorted(maincycles,key=lambda r:r['returnPct'])[:5])
    diagnostic=dict(schema='guiyin-dual-path-all-outcome-diagnostics-v1',newStrategyTrials=0,newAccounts=0,parametersChanged=False,
                    wholeSystemByEntryPath=by_path,wholeSystemByEntryPeriod=by_period,wholeSystemByExitReason=exits,
                    sourceCoverageByInformationTime=coverage,allSourceTimingRows=timing,extremes=extremes,
                    allSourceEntryDateCohorts=source_dates,
                    interpretationLimits=['These are selected-account and executed-strategy associations, not causal feature effects.',
                                          'An entry-session close signal cannot be used to claim the original earlier intraday fill.',
                                          '2025-2026 were already exposed in prior development; they are not clean out-of-sample validation.'])
    path=OUT/'all-outcome-diagnostics.json'
    with path.open('x',encoding='utf-8') as f:json.dump(diagnostic,f,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({k:diagnostic[k] for k in ['wholeSystemByEntryPath','wholeSystemByEntryPeriod','sourceCoverageByInformationTime']},ensure_ascii=False))


if __name__=='__main__':main()
