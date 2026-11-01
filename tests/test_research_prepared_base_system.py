from copy import deepcopy
from datetime import date,timedelta
import importlib.util
from pathlib import Path
import unittest
import numpy as np

from service.research_prepared_base_system import build_inputs,PreparedBaseAccount,coherent
from service.research_account_fees import LegacyFeePolicy
from tests.test_research_market_sector_account import setup
from tests.test_research_volatility_trail import configuration,set_bar

ROOT=Path(__file__).resolve().parents[1]


def fixture():
    day=date(2024,1,1);dates=[]
    while len(dates)<100:
        if day.weekday()<5:dates.append(day.isoformat())
        day+=timedelta(days=1)
    t=np.arange(100)
    c=np.array([10+(.02 if r%5==4 else .01)*t for r in range(10)])
    m={"calendar":np.array(dates),"symbols":np.array([f"sh{600001+r:06d}" for r in range(10)]),
        "open":c.copy(),"close":c.copy(),"high":c*1.005,"low":c*.995,"volume":np.full(c.shape,1e7),
        "amount":np.full(c.shape,1e8),"priceValid":np.ones(c.shape,bool)}
    joins=[{"snapshotId":"known","sectors":[{"sectorCode":f"C{30+g}","cohortRowIndices":list(range(g*5,g*5+5))} for g in range(2)]}]
    sectors=[{"sectorCode":f"C{30+g}","score":1-g*.1,"featureValid":True,"return20":.2,"leaders":[g*5+4]} for g in range(2)]
    context={"daily":[{"date":dates[i],"index":i,"snapshotId":"known","market":{"admission":True},"selectedSectors":["C30","C31"],"sectors":deepcopy(sectors)} for i in range(60,100)]}
    base(m,4);base(m,9)
    return m,joins,context


def base(m,row):
    m['close'][row,75:80]=11.55;m['open'][row,75:80]=11.55
    m['high'][row,75:80]=12.;m['low'][row,75:80]=11.05
    m['close'][row,80:85]=[11.65,11.58,11.60,11.63,11.67]
    m['open'][row,80:85]=m['close'][row,80:85]
    m['high'][row,80:85]=[11.75,11.70,11.70,11.72,11.75]
    m['low'][row,80:85]=[11.35,11.40,11.45,11.50,11.55]
    m['amount'][row,75:80]=1.5e8;m['amount'][row,80:85]=6e7
    m['open'][row,85]=11.7;m['close'][row,85]=11.8;m['high'][row,85]=11.85;m['low'][row,85]=11.65;m['amount'][row,85]=2e8


def at_day(result,m,policy='completeSystem'):
    return [s for s in result['schedules'][policy] if s['signalDate']==m['calendar'][85]]


def oracle():
    s=importlib.util.spec_from_file_location('_prepared_base_test_reference',ROOT/'scripts/verify-prepared-base-inputs.py')
    v=importlib.util.module_from_spec(s);s.loader.exec_module(v);return v


def account_case(opening=10.,floor=9.7):
    data,signals,inputs,policy=setup()
    for meta in inputs['metadata'].values():
        meta.update(pattern='leaderBase',failurePrice=floor,meanNtr20=.02,base={'failurePrice':floor})
    set_bar(data,26,opening=opening)
    engine=PreparedBaseAccount(data,signals,configuration(data),system_inputs=inputs,policy=policy,fee_policy=LegacyFeePolicy())
    engine.step(20000)
    return engine,engine.result()


class PreparedBaseTests(unittest.TestCase):
    def test_complete_sequence_and_separate_direct_oracle(self):
        m,j,c=fixture();r=build_inputs(m,j,c)
        self.assertEqual(len(at_day(r,m)),2)
        self.assertTrue(oracle().audit(m,j,c,r)['passed'])
        altered=deepcopy(r);altered['metadata'][at_day(r,m)[0]['signalId']]['base']['recentRange']+=.1
        with self.assertRaises(ValueError):oracle().audit(m,j,c,altered)

    def test_missing_slot_is_not_compressed_and_snapshot_history_does_not_carry(self):
        m,j,c=fixture();m['priceValid'][4,82]=False
        self.assertEqual(len(at_day(build_inputs(m,j,c),m)),1)
        m,j,c=fixture();c['daily'][20]['snapshotId']='different';j.append({**deepcopy(j[0]),'snapshotId':'different'})
        self.assertFalse(at_day(build_inputs(m,j,c),m))

    def test_real_support_must_be_coherent_and_contraction_is_required(self):
        m,j,c=fixture();m['low'][4,80]=10.
        self.assertEqual(len(at_day(build_inputs(m,j,c),m)),1)
        m,j,c=fixture();m['amount'][4,80:85]=3e8
        self.assertEqual(len(at_day(build_inputs(m,j,c),m)),1)
        self.assertTrue(coherent(10.,9.5,.02))
        self.assertFalse(coherent(10.,9.9,.02))

    def test_future_prices_cannot_rewrite_current_preparation(self):
        m,j,c=fixture();before=build_inputs(m,j,c)
        for k in ('open','high','low','close','amount'):m[k][:,86:]*=7
        after=build_inputs(m,j,c)
        self.assertEqual(at_day(before,m),at_day(after,m))
        for s in at_day(before,m):self.assertEqual(before['metadata'][s['signalId']],after['metadata'][s['signalId']])

    def test_catchup_requires_distinct_old_leader_and_disabled_branch(self):
        m,j,c=fixture()
        m['close'][0]=10+.02*np.arange(100);m['open'][0]=m['close'][0];m['high'][0]=m['close'][0]*1.005;m['low'][0]=m['close'][0]*.995
        base(m,0)
        for row in (2,3,4):
            m['close'][row,65:]=np.linspace(11.,15.+row,35)
            m['open'][row,65:]=m['close'][row,65:];m['high'][row,65:]=m['close'][row,65:]*1.005;m['low'][row,65:]=m['close'][row,65:]*.995
        r=build_inputs(m,j,c)
        full=at_day(r,m);candidate=next(s for s in full if s['symbol']==m['symbols'][0])
        self.assertEqual(r['metadata'][candidate['signalId']]['pattern'],'catchup')
        self.assertFalse(any(s['symbol']==m['symbols'][0] for s in at_day(r,m,'withoutCatchupEntry')))
        c['daily'][20]['sectors'][0]['leaders']=[]
        self.assertFalse(any(s['symbol']==m['symbols'][0] for s in at_day(build_inputs(m,j,c),m)))

    def test_opening_risk_guard_pass_and_two_sided_rejection_no_retry(self):
        _,r=account_case();self.assertEqual(len(r['trades']),1)
        for opening,floor,reason in ((10.1,9.5,'preparedBaseOpeningRiskTooWide'),(9.8,9.7,'preparedBaseOpeningRiskInsideOneNtr')):
            _,r=account_case(opening,floor)
            self.assertFalse(r['trades']);self.assertEqual(r['research']['opportunities'][0]['reason'],reason)
            self.assertEqual(len(r['research']['system']['preparedBaseOpeningGuards']),1)


if __name__=='__main__':unittest.main()
