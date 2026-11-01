"""Frozen signal-set changes and industry-slot competition; no outcomes read."""
from collections import Counter
import importlib.util
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_lifecycle_opportunity_utils',ROOT/'scripts/run-liquid-trend-breakout.py')
U=importlib.util.module_from_spec(spec);sys.modules[spec.name]=U;spec.loader.exec_module(U)
STUDY=ROOT/'artifacts/strategy-search/sector-lifecycle-system-v3/attempt-001'


def build():
    plan=U.read(STUDY/'plan.json');inputs=U.read(STUDY/'input-validation.json')
    if not inputs['passed'] or inputs['planSha256']!=U.sha(STUDY/'plan.json'):raise ValueError('Frozen inputs required')
    current=U.read(U.bound(inputs['formation']))
    old_ref=next(v for v in plan['bindings'] if v['path']=='artifacts/strategy-search/market-sector-stock-base-v2/attempt-001/formation.candidate.json.gz')
    old=U.read(U.bound(old_ref));original=U.read(U.bound(plan['originalIndustryContext']));life=U.read(U.bound(inputs['lifecycleContext']))
    if current['candidateCounts']!=old['candidateCounts']:raise ValueError('Underlying stock preparation pool changed beyond industry timing hypothesis')
    days={d['date']:d for d in life['daily']};rows=[];changes=[]
    for policy in current['schedules']:
        before={ (s['signalDate'],s['symbol']):s for s in old['schedules'][policy] if s['signalDate']>='2024-09-24'}
        after={ (s['signalDate'],s['symbol']):s for s in current['schedules'][policy] if s['signalDate']>='2024-09-24'}
        lost,added=set(before)-set(after),set(after)-set(before)
        reasons=Counter()
        for key in sorted(lost):
            info=old['metadata'][before[key]['signalId']];d=days[key[0]];sector=next(s for s in d['sectors'] if s['sectorCode']==info['sectorCode'])
            reason='healthyActiveIndustryCrowdedOutOfTopThree' if sector['lifecycle']['active'] and sector['lifecycle']['health'] is True and info['sectorCode'] not in d['selectedSectors'] else 'otherSourceQualificationChange'
            reasons[reason]+=1;changes.append({'policy':policy,'date':key[0],'symbol':key[1],'sectorCode':info['sectorCode'],'change':'lost','reason':reason})
        for key in sorted(added):
            info=current['metadata'][after[key]['signalId']];changes.append({'policy':policy,'date':key[0],'symbol':key[1],'sectorCode':info['sectorCode'],'change':'added'})
        rows.append({'policy':policy,'oldSignals':len(before),'newSignals':len(after),'retainedStockCloseEvents':len(set(before)&set(after)),
            'addedStockCloseEvents':len(added),'lostStockCloseEvents':len(lost),'lostReasons':dict(reasons),
            'newRoles':dict(Counter(current['metadata'][s['signalId']]['pattern'] for s in after.values()))})
    paired=[(o,n) for o,n in zip(original['daily'],life['daily']) if o['date']>='2024-09-24']
    assert all(o['date']==n['date'] for o,n in paired)
    folder=STUDY/'opportunity-diagnostic-001';folder.mkdir()
    U.write(folder/'changes.json',changes)
    U.write(folder/'readout.json',{'schema':'lifecycle-opportunity-changes-v1','builder':U.ref(Path(__file__)),'inputs':U.ref(STUDY/'input-validation.json'),
        'originalFormation':old_ref,'newFormation':inputs['formation'],'context':inputs['lifecycleContext'],'rows':rows,
        'primaryDecisionDays':len(paired),'changedIndustryShortlistDays':sum(o['selectedSectors']!=n['selectedSectors'] for o,n in paired),
        'unchangedUnderlyingStockPreparationEvents':True,'accountOutcomesRead':False,'newAccounts':0,'newStrategyTrials':0,'parameterChanges':0,'targetAchieved':False,
        'scope':'Source-only selection changes; added/excluded candidates are not assumed profitable. Repeated stock-close events are not independent trades.'})
    U.emit({'diagnostic':U.ref(folder/'readout.json'),'rows':rows})


if __name__=='__main__':build()
