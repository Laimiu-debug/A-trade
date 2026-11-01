from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest

from service.research_sector_lifecycle_system import transform_contexts
from tests.test_research_sector_lifecycle_system import simple

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_test_lifecycle_projection_oracle',ROOT/'scripts/audit-sector-lifecycle-system.py')
V=importlib.util.module_from_spec(spec);sys.modules[spec.name]=V;spec.loader.exec_module(V)


def case(control=False):
    original=simple()
    for d in original['daily']:d['market']={'admission':True,'riskExit':False,'crisisCount':0}
    life=transform_contexts(original)
    system={'daily':[]}
    for old,d in zip(original['daily'],life['daily']):
        system['daily'].append({'date':d['date'],'snapshotId':d['snapshotId'],'market':d['market'],
            'selectedSectors':old['selectedSectors'] if control else d['selectedSectors'],
            'sectors':[{k:s[k] for k in ('sectorCode','failCount','riskExit','lifecycle')} for s in d['sectors']]})
    return system,life,original


class LifecycleProjectionOracleTests(unittest.TestCase):
    def test_every_executed_risk_counter_and_industry_admission_checked(self):
        system,life,original=case()
        self.assertEqual(V.audit_projection(system,life,original,False)['decisionDays'],12)
        bad=deepcopy(system);bad['daily'][3]['sectors'][0]['failCount']=3
        with self.assertRaises(ValueError):V.audit_projection(bad,life,original,False)
        bad=deepcopy(system);bad['daily'][3]['selectedSectors']=[]
        with self.assertRaises(ValueError):V.audit_projection(bad,life,original,False)

    def test_control_retains_original_admission_and_common_new_health_exit(self):
        system,life,original=case(True)
        self.assertTrue(V.audit_projection(system,life,original,True)['controlRetainsOriginalAdmission'])
        with self.assertRaises(ValueError):V.audit_projection(system,life,original,False)
        bad=deepcopy(system);bad['daily'][3]['sectors'][0]['riskExit']=True
        with self.assertRaises(ValueError):V.audit_projection(bad,life,original,True)


if __name__=='__main__':unittest.main()
