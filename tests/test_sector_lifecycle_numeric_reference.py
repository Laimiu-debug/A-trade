from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

from service.research_sector_lifecycle_system import SectorLifecycleAccount
from service.research_sector_lifecycle_precision import PrecisionSafeLifecycleAccount,price_tolerance
from service.research_account_fees import LegacyFeePolicy
from tests.test_research_market_sector_account import setup
from tests.test_research_volatility_trail import configuration

ROOT=Path(__file__).resolve().parents[1]


def reference():
    spec=importlib.util.spec_from_file_location('_lifecycle_numeric_reference',ROOT/'scripts/verify-sector-lifecycle-risk.py')
    v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v);return v


def run_boundary(safe=False,early_exit=False):
    data,signals,inputs,policy=setup()
    for s in data['securities']:
        for b in s['bars']:b.update(open=13.8,close=13.8,high=13.938,low=13.662)
    rows=data['securities'][0]['bars'];rows[25]['volume']=630000;rows[26]['open']=13.79
    rows[27].update(close=13.29,low=13.29);rows[28].update(open=13.25,low=13.25)
    for m in inputs['metadata'].values():m.update(signalClose=13.8,pattern='leaderBase',failurePrice=13.29,meanNtr20=.02,base={'failurePrice':13.29})
    inputs['daily'][30]['market'].update(admission=False,riskExit=True,crisisCount=3)
    if early_exit:inputs['daily'][26]['market'].update(admission=False,riskExit=True,crisisCount=3)
    config=configuration(data);config.update(initialCapital=500000.,maxParticipationRate=.01,slippageBps=10.)
    cls=PrecisionSafeLifecycleAccount if safe else SectorLifecycleAccount
    account=cls(data,signals,config,system_inputs=inputs,policy=policy,lifecycle_policy=policy,fee_policy=LegacyFeePolicy());account.step(20000)
    return account,account.result(),data


class NumericRiskReferenceTests(unittest.TestCase):
    def test_original_weighted_cost_behavior_and_ambiguity_reproducible(self):
        account,result,data=run_boundary()
        entry=next(t for t in result['trades'] if t['side']=='buy');self.assertEqual(entry['quantity'],6300)
        self.assertNotEqual(entry['price'],entry['price']*6300/6300)
        proof=reference().daily_system_risk_reference(result,account.checkpoint(),data,account.system_inputs)
        self.assertEqual(len(proof['structuralPriceBoundaryAmbiguities']),1)
        self.assertEqual([d['date'] for d in result['decisions'] if d['action']=='sell' and d['status']=='queued'],[data['calendar'][30]])

    def test_numerical_repair_triggers_equal_floor_and_sells_next_open(self):
        account,result,data=run_boundary(True)
        sells=[t for t in result['trades'] if t['side']=='sell'];self.assertEqual(len(sells),1)
        self.assertEqual((sells[0]['signalDate'],sells[0]['date'],sells[0]['reason']),(data['calendar'][27],data['calendar'][28],'systemStructuralFailure'))
        proof=reference().daily_system_risk_reference(result,account.checkpoint(),data,account.system_inputs,price_boundary_policy='sixteenUlpsAtStructuralPrice')
        self.assertEqual(proof['actualEntries'],1)
        bad=deepcopy(result);bad['decisions']=[d for d in bad['decisions'] if not (d['action']=='sell' and d['status']=='queued')]
        with self.assertRaises(ValueError):reference().daily_system_risk_reference(bad,account.checkpoint(),data,account.system_inputs,price_boundary_policy='sixteenUlpsAtStructuralPrice')

    def test_one_cent_is_not_tolerance_and_earlier_intent_is_retained(self):
        self.assertLess(price_tolerance(13.29),.000001)
        self.assertFalse(13.30<=13.29+price_tolerance(13.29))
        account,result,data=run_boundary(True,True)
        sells=[t for t in result['trades'] if t['side']=='sell']
        self.assertEqual((sells[0]['signalDate'],sells[0]['reason']),(data['calendar'][26],'systemMarketRisk'))
        self.assertTrue(reference().daily_system_risk_reference(result,account.checkpoint(),data,account.system_inputs,price_boundary_policy='sixteenUlpsAtStructuralPrice')['unregisteredExitsAbsent'])


if __name__=='__main__':unittest.main()
