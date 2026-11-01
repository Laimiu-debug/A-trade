"""Known industry establishment, continuing health and retirement states."""
from copy import deepcopy
import hashlib
from pathlib import Path

from .research_prepared_base_system import PreparedBaseAccount

ROOT=Path(__file__).resolve().parents[1]
VERSION='sector-lifecycle-system-v3'
SOURCE_FILES=('docs/sector-lifecycle-system-v3.json','service/research_sector_lifecycle_system.py')


def source_pins():
    return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCE_FILES}


def transform_contexts(verified_context):
    result=deepcopy(verified_context)
    states={};snapshot=None
    for day in result['daily']:
        if day['snapshotId']!=snapshot:
            states={};snapshot=day['snapshotId']
        established=set(day['selectedSectors'])
        day['establishmentSectors']=list(day['selectedSectors'])
        for sector in day['sectors']:
            code=sector['sectorCode']
            active,fail,activation=states.get(code,(False,0,None))
            before=active
            health=None if not sector['featureValid'] else bool(sector['relative60']>0 and sector['breadth60']>.5)
            fail=fail+1 if health is False else 0
            retired=bool(active and fail>=3)
            if fail>=3:
                active,activation=False,None
            activated=bool(code in established and not active)
            if code in established:
                if health is not True:raise ValueError('Established industry lacks known positive health')
                if not active:activation=day['date']
                active=True
            states[code]=active,fail,activation
            sector.update(rawStrengthFailCount=sector['failCount'],rawStrengthRiskExit=sector['riskExit'],failCount=fail,riskExit=fail>=3,
                lifecycle={'active':active,'health':health,'failureCount':fail,'activationDate':activation,
                    'activatedToday':activated,'retiredToday':retired,'activeBeforeToday':before})
        candidates=[s for s in day['sectors'] if s['featureValid'] and s['lifecycle']['active'] and s['lifecycle']['health'] is True]
        day['selectedSectors']=[s['sectorCode'] for s in sorted(candidates,key=lambda s:(-s['score'],s['sectorCode']))[:3]]
    return result


def rename_entry_formation(base):
    result=deepcopy(base);result['version']=VERSION
    mapping={old:old.replace('prepared-base-v2:','lifecycle-v3:',1) for old in base['metadata']}
    result['metadata']={mapping[k]:{**m,'signalId':mapping[k]} for k,m in result['metadata'].items()}
    for signals in result['schedules'].values():
        for signal in signals:signal['signalId']=mapping[signal['signalId']]
    return result


class SectorLifecycleAccount(PreparedBaseAccount):
    """All execution/risk arithmetic inherited; only frozen context differs."""
    def __init__(self,*args,lifecycle_policy,**kwargs):
        self.lifecycle_identity={'version':VERSION,'policy':lifecycle_policy,'sourcePins':source_pins()}
        super().__init__(*args,**kwargs)

    def _identity_stable(self):
        super()._identity_stable()
        if self.lifecycle_identity['sourcePins']!=source_pins():raise ValueError('Industry lifecycle source changed')

    def _system_result(self):
        return {**super()._system_result(),'lifecycleIdentity':deepcopy(self.lifecycle_identity)}
