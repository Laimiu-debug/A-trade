"""Compare actual matched accounts, never adjust old profit by hand."""
import importlib.util
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_lifecycle_precision_change_utils',ROOT/'scripts/run-liquid-trend-breakout.py')
U=importlib.util.module_from_spec(spec);sys.modules[spec.name]=U;spec.loader.exec_module(U)
STUDY=ROOT/'artifacts/strategy-search/sector-lifecycle-system-v3/price-boundary-repair-v1/attempt-001'
FIELDS=('date','signalDate','symbol','side','quantity','price','openingPrice','amount','fee','slippage','cashAfter','realizedPnl',
    'reason','targetWeight','quantityBefore','quantityAfter','positionAction','unfilledQuantity','signalId','originalRank')


def build():
    validation=U.read(STUDY/'validation.json')
    if not validation['passed'] or not validation['numericalContractConformancePassed'] or validation['numericBoundaryExceptionCount']:
        raise ValueError('Independently audited corrected execution required')
    result=U.read(U.bound(validation['readout']));parent=U.read(U.bound(result['reusedParentValidation']))
    old=U.read(U.bound(parent['readout']));exceptions=U.read(U.bound(parent['numericBoundaryDiagnostic']))
    expected_changed={(r['period'],r['policy']) for r in exceptions['rows']}
    changed=set();rows=[];events=[]
    for current in result['rows']:
        period,policy=current['period'],current['policy']
        previous=next(r for r in old['rows'] if (r['period'],r['policy'])==(period,policy))
        a,b=(U.read(U.bound(r['evidence']['result'])) for r in (previous,current))
        old_trades=[{k:t.get(k) for k in FIELDS} for t in a['trades']]
        new_trades=[{k:t.get(k) for k in FIELDS} for t in b['trades']]
        same_curve=a['curve']==b['curve'];same_trades=old_trades==new_trades
        if not (same_curve and same_trades):changed.add((period,policy))
        rows.append({'period':period,'policy':policy,'economicFillsIdentical':same_trades,'entireEquityCurveIdentical':same_curve,
            'oldClosed':previous['closed'],'newClosed':current['closed'],'oldReturnPct':previous['netReturnPct'],'newReturnPct':current['netReturnPct'],
            'returnDeltaPp':current['netReturnPct']-previous['netReturnPct'],'oldDDPct':previous['maxDrawdownPct'],'newDDPct':current['maxDrawdownPct'],
            'oldPayoff':previous['payoffRatio'],'newPayoff':current['payoffRatio'],'oldPF':previous['profitFactor'],'newPF':current['profitFactor'],
            'originalResult':previous['evidence']['result'],'correctedResult':current['evidence']['result']})
        for issue in exceptions['rows']:
            if (issue['period'],issue['policy'])!=(period,policy):continue
            binding=next(r for r in a['research']['system']['entryBindings'] if r['signalId']==issue['signalId'])
            key=(issue['symbol'],binding['entryDate'])
            before=next(r for r in a['closedTrades'] if (r['symbol'],r['entryDate'])==key)
            after=next(r for r in b['closedTrades'] if (r['symbol'],r['entryDate'])==key)
            events.append({'period':period,'policy':policy,'symbol':key[0],'entryDate':key[1],'sourceBoundaryDate':issue['date'],
                'sourceObservedClose':issue['observedClose'],'originalBinaryThreshold':issue['failureThreshold'],
                'oldExitDate':before['exitDate'],'newExitDate':after['exitDate'],'oldEpisodeReturnPct':before['returnPct'],'newEpisodeReturnPct':after['returnPct'],
                'oldEpisodeNetPnl':before['pnlAmount'],'newEpisodeNetPnl':after['pnlAmount'],
                'meaning':'Actual completely closed episode in two separately executed accounts; does not substitute this delta for the total portfolio change.'})
    if len(rows)!=6 or changed!=expected_changed:raise ValueError('Numerical repair changed accounts beyond the original independently identified boundary scope')
    U.write(STUDY/'matched-change-analysis.json',{'schema':'lifecycle-structural-numerical-repair-comparison','builder':U.ref(Path(__file__)),
        'newValidation':U.ref(STUDY/'validation.json'),'originalValidation':result['reusedParentValidation'],'originalBoundaryDiagnostic':parent['numericBoundaryDiagnostic'],
        'rows':rows,'boundaryEpisodes':events,'changedAccounts':len(changed),'unchangedAccounts':len(rows)-len(changed),
        'changedScopeMatchesOriginalBoundaryAccounts':True,'newStrategyTrials':0,'newAccounts':0,'targetAchieved':False,
        'scope':'Actual complete matched reruns, byte-identical frozen schedules/contexts; cash and opportunity changes after an earlier sell remain part of the rerun, not a hand-adjusted counterfactual.'})
    U.emit({'comparison':U.ref(STUDY/'matched-change-analysis.json'),'changedAccounts':len(changed),'unchangedAccounts':len(rows)-len(changed),'boundaryEpisodes':events})


if __name__=='__main__':build()
