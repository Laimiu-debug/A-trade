from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

from service.research_sector_lifecycle_system import transform_contexts,rename_entry_formation,SectorLifecycleAccount
from service.research_prepared_base_system import build_inputs
from service.research_account_fees import LegacyFeePolicy
from tests.test_research_prepared_base_system import fixture,at_day
from tests.test_research_market_sector_account import setup
from tests.test_research_volatility_trail import configuration

ROOT=Path(__file__).resolve().parents[1]


def simple(length=12):
    return {'daily':[{'date':f'2024-01-{i+1:02d}','snapshotId':'one','selectedSectors':['C39'] if i in (0,9) else [],
        'sectors':[{'sectorCode':'C39','featureValid':True,'relative60':.1,'breadth60':.7,'score':1.,'failCount':0 if i==0 else 3,'riskExit':i>0}]} for i in range(length)]}


def oracle():
    s=importlib.util.spec_from_file_location('_lifecycle_test_backward_reference',ROOT/'scripts/verify-sector-lifecycle-inputs.py')
    v=importlib.util.module_from_spec(s);s.loader.exec_module(v);return v


class SectorLifecycleTests(unittest.TestCase):
    def test_active_survives_turnover_retreat_but_health_failure_retires(self):
        source=simple()
        for i in (3,4,5):source['daily'][i]['sectors'][0]['breadth60']=.4
        actual=transform_contexts(source)
        self.assertEqual(actual['daily'][2]['selectedSectors'],['C39'])
        self.assertFalse(actual['daily'][2]['sectors'][0]['riskExit'])
        self.assertTrue(actual['daily'][5]['sectors'][0]['riskExit'])
        self.assertFalse(actual['daily'][6]['sectors'][0]['lifecycle']['active'])
        self.assertTrue(actual['daily'][9]['sectors'][0]['lifecycle']['activatedToday'])
        self.assertTrue(oracle().audit(source,actual)['passed'])
        corrupted=deepcopy(actual);corrupted['daily'][5]['sectors'][0]['lifecycle']['active']=True
        with self.assertRaises(ValueError):oracle().audit(source,corrupted)

    def test_unknown_resets_failure_streak_without_erasing_activation(self):
        source=simple()
        for i in (2,4,5):source['daily'][i]['sectors'][0]['breadth60']=.4
        source['daily'][3]['sectors'][0]['featureValid']=False
        actual=transform_contexts(source)
        self.assertFalse(actual['daily'][3]['selectedSectors'])
        self.assertTrue(actual['daily'][3]['sectors'][0]['lifecycle']['active'])
        self.assertFalse(actual['daily'][5]['sectors'][0]['riskExit'])
        self.assertTrue(oracle().audit(source,actual)['passed'])

    def test_new_snapshot_cannot_inherit_prior_active_industry(self):
        source=simple()
        for row in source['daily'][7:]:row['snapshotId']='new'
        actual=transform_contexts(source)
        self.assertFalse(actual['daily'][7]['selectedSectors'])
        self.assertTrue(actual['daily'][9]['selectedSectors'])
        self.assertTrue(oracle().audit(source,actual)['passed'])

    def test_future_industry_evidence_does_not_change_earlier_state(self):
        source=simple();before=transform_contexts(source)
        for row in source['daily'][7:]:row['sectors'][0]['relative60']=-1
        source['daily'][9]['selectedSectors']=[]
        after=transform_contexts(source)
        self.assertEqual(before['daily'][:7],after['daily'][:7])

    def test_same_stock_sequence_and_signal_rename_without_entry_mutation(self):
        m,joins,c=fixture()
        for d in c['daily']:
            for s in d['sectors']:s.update(relative60=.1,breadth60=.7,failCount=0,riskExit=False)
        contexts=transform_contexts(c)
        base=build_inputs(m,joins,contexts);renamed=rename_entry_formation(base)
        self.assertTrue(oracle().audit_rename(base,renamed)['passed'])
        self.assertEqual(len(at_day(renamed,m)),2)
        for old,new in zip(at_day(base,m),at_day(renamed,m)):
            self.assertEqual({k:v for k,v in old.items() if k!='signalId'},{k:v for k,v in new.items() if k!='signalId'})
            self.assertTrue(new['signalId'].startswith('lifecycle-v3:'))

    def test_common_sector_exit_on_third_health_failure_and_persists(self):
        data,signals,inputs,policy=setup()
        for meta in inputs['metadata'].values():meta.update(pattern='leaderBase',failurePrice=9.7,meanNtr20=.02,base={'failurePrice':9.7})
        for i,d in enumerate(inputs['daily']):
            for s in d['sectors']:s.update(featureValid=True,relative60=.1,breadth60=.4 if 26<=i<=28 else .7,score=1.)
            if 26<=i<=28:d['selectedSectors']=[]
        transformed=transform_contexts({'daily':inputs['daily']});inputs['daily']=transformed['daily']
        engine=SectorLifecycleAccount(data,signals,configuration(data),system_inputs=inputs,policy=policy,lifecycle_policy='completeSystem',fee_policy=LegacyFeePolicy())
        engine.step(20000);result=engine.result();sells=[t for t in result['trades'] if t['side']=='sell']
        self.assertEqual(len(sells),1)
        self.assertEqual((sells[0]['signalDate'],sells[0]['date'],sells[0]['reason']),(data['calendar'][28],data['calendar'][29],'systemSectorRisk'))


if __name__=='__main__':unittest.main()
