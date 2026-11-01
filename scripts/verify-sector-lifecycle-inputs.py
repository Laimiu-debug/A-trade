"""Independent backward-history lifecycle oracle; no production import."""
from copy import deepcopy
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_lifecycle_source_comparator',ROOT/'scripts/verify-market-sector-stock-inputs.py')
V=importlib.util.module_from_spec(spec);spec.loader.exec_module(V)


def audit(original,actual):
    history=original['daily'];cache={}
    source_rows=[{s['sectorCode']:s for s in d['sectors']} for d in history]
    def state(index,code):
        if (index,code) in cache:return cache[index,code]
        day=history[index];start=index
        while start>0 and history[start-1]['snapshotId']==day['snapshotId']:start-=1
        def at(k):return source_rows[k].get(code)
        def health(k):
            row=at(k)
            return None if row is None or not row['featureValid'] else bool(row['relative60']>0 and row['breadth60']>.5)
        def count(k):
            length=0
            while k>=start and health(k) is False:length+=1;k-=1
            return length
        most_recent_retirement=start-1
        for k in range(index,start-1,-1):
            if count(k)>=3:most_recent_retirement=k;break
        first_activation=next((k for k in range(most_recent_retirement+1,index+1) if code in history[k]['selectedSectors']),None)
        active=first_activation is not None
        previous=state(index-1,code)['active'] if index>start else False
        current={'active':active,'health':health(index),'failureCount':count(index),
            'activationDate':history[first_activation]['date'] if active else None,
            'activatedToday':bool(not previous and code in day['selectedSectors']),
            'retiredToday':bool(previous and count(index)>=3),'activeBeforeToday':previous}
        cache[index,code]=current;return current
    expected=deepcopy(original)
    for i,day in enumerate(expected['daily']):
        day['establishmentSectors']=list(history[i]['selectedSectors'])
        for s in day['sectors']:
            life=state(i,s['sectorCode'])
            s.update(rawStrengthFailCount=s['failCount'],rawStrengthRiskExit=s['riskExit'],failCount=life['failureCount'],riskExit=life['failureCount']>=3,lifecycle=life)
        admitted=[s for s in day['sectors'] if s['featureValid'] and s['lifecycle']['active'] and s['lifecycle']['health'] is True]
        day['selectedSectors']=[s['sectorCode'] for s in sorted(admitted,key=lambda s:(-s['score'],s['sectorCode']))[:3]]
    V.same(actual,expected,'Every original field, lifecycle state and shortlist')
    return {'passed':True,'decisionDays':len(history),'industryStates':len(cache),'activationAndFailureRebuiltFromBackwardSourceHistory':True,
        'activationEvents':sum(s['lifecycle']['activatedToday'] for d in expected['daily'] for s in d['sectors']),
        'retirementEvents':sum(s['lifecycle']['retiredToday'] for d in expected['daily'] for s in d['sectors'])}


def audit_rename(base,actual):
    expected=deepcopy(base);expected['version']='sector-lifecycle-system-v3'
    renamed={}
    for old,info in base['metadata'].items():
        if not old.startswith('prepared-base-v2:'):raise ValueError('Unexpected base identity')
        new='lifecycle-v3:'+old[len('prepared-base-v2:'):]
        renamed[new]={**info,'signalId':new}
    expected['metadata']=renamed
    for values in expected['schedules'].values():
        for signal in values:signal['signalId']='lifecycle-v3:'+signal['signalId'][len('prepared-base-v2:'):]
    V.same(actual,expected,'Only causal formation namespace changes')
    return {'passed':True,'signals':len(renamed),'allFieldsPreservedExceptRegisteredVersionAndSignalNamespace':True}
