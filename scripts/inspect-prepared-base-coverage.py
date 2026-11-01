"""Explain frozen entry-opportunity exclusions without loading any outcomes."""
from collections import Counter,defaultdict
import importlib.util
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_prepared_base_coverage_utils',ROOT/'scripts/run-liquid-trend-breakout.py')
U=importlib.util.module_from_spec(spec);sys.modules[spec.name]=U;spec.loader.exec_module(U)
STUDY=ROOT/'artifacts/strategy-search/market-sector-stock-base-v2/attempt-001'


def build():
    plan=U.read(STUDY/'plan.json');inputs=U.read(STUDY/'input-validation.json')
    if not inputs['passed'] or inputs['planSha256']!=U.sha(STUDY/'plan.json'):raise ValueError('Frozen validated schedules required')
    formation=U.read(U.bound(inputs['formation']));context=U.read(U.bound(plan['parentFormation']))
    days={d['date']:d for d in context['daily']}
    buckets={role:Counter() for role in ('leaderBase','catchup')}
    components={role:Counter() for role in buckets};events=[]
    for c in formation['candidateCounts']:
        if c['date']<'2024-09-24':continue
        d=days[c['date']];sector=next(s for s in d['sectors'] if s['sectorCode']==c['sectorCode'])
        reason='marketNotAdmitted' if d['market']['admission'] is not True else 'industryNotCurrentlyRawStrong' if sector['rawStrong'] is not True else 'industryNotThreeConsecutive' if not sector['confirmed'] else 'outsideIndustryTopThree' if c['sectorCode'] not in d['selectedSectors'] else 'marketAndIndustryAdmitted'
        for role in buckets:
            n=c[role]
            if not n:continue
            buckets[role]['allPreparedStockEvents']+=n;buckets[role][reason]+=n
            if d['market']['admission'] is True and sector['rawStrong'] is not True:
                for field,failed in (('relative20NonPositive',sector['relative20']<=0),('relative60NonPositive',sector['relative60']<=0),('breadthNotAboveHalf',sector['breadth60']<=.5),('relativeTurnoverBelowOne',sector['participation']<1)):
                    if failed:components[role][field]+=n
        events.append({**c,'marketAndIndustryExclusion':reason,'rawStrong':sector['rawStrong'],'confirmed':sector['confirmed'],
            'relative20':sector['relative20'],'relative60':sector['relative60'],'breadth60':sector['breadth60'],'participation':sector['participation']})
    summaries=[]
    for role in buckets:
        b=buckets[role]
        assert b['allPreparedStockEvents']==sum(n for k,n in b.items() if k!='allPreparedStockEvents')
        summaries.append({'role':role,'mutuallyExclusiveStageCounts':dict(b),'overlappingRawIndustryFailureCounts':dict(components[role])})
    folder=STUDY/'entry-coverage-001';folder.mkdir()
    U.write(folder/'events.json',events)
    U.write(folder/'readout.json',{'schema':'prepared-base-opportunity-coverage-v1','builder':U.ref(Path(__file__)),'formation':inputs['formation'],
        'inputs':U.ref(STUDY/'input-validation.json'),'context':plan['parentFormation'],'rows':summaries,
        'unit':'Prepared stock x close events before one-per-industry and global ordering; repeated stock/date occurrences are distinct frozen events, not independent trades',
        'period':'2024-09-24 through2026-10-15','accountResultsRead':False,'newAccounts':0,'newStrategyTrials':0,'parameterChanges':0,
        'scope':'Descriptive causal source-window admission exclusions only. Excluded opportunities are not assumed profitable; no alternative-rule account return inferred.',
        'targetAchieved':False})
    U.emit({'coverage':U.ref(folder/'readout.json'),'rows':summaries})


if __name__=='__main__':build()
