from copy import deepcopy
import unittest
import numpy as np
from service.research_guiyin_dual_path import DualPathAccount, market_state, build_inputs
from service.research_account_fees import LegacyFeePolicy
from service.research_account_outcomes import summarize_account
from tests.test_research_market_sector_account import setup
from tests.test_research_volatility_trail import configuration, set_bar


def case():
    data, signals, inputs, _ = setup()
    for s in data['securities']:
        s['stByDate'] = {d:False for d in data['calendar']}
    for d in inputs['daily']:
        d['market']['turnover5ToPrior20'] = 1.1
    for m in inputs['metadata'].values():
        m['pattern'] = 'A_ignition'
    return data, signals, inputs


def run(c, policy='complete'):
    data, signals, inputs = c
    e = DualPathAccount(data,signals,configuration(data,maxHoldingDays=10),system_inputs=inputs,policy=policy,fee_policy=LegacyFeePolicy())
    e.step(20000)
    r = e.result()
    assert not summarize_account(r,e.checkpoint(),policy_id=policy,policy_label=policy)['quality']['integrityIssues']
    return e,r


class DualPathTests(unittest.TestCase):
    def test_formation_ignition_and_future_price_mutation(self):
        data=case()[0]; days=data['calendar']; n=len(days)
        m={k:np.full((5,n),v) for k,v in [('open',10.),('close',10.),('high',10.05),('low',9.95),('volume',1e7),('amount',1e8)]}
        m.update(priceValid=np.ones((5,n),bool),explicitSuspended=np.zeros((5,n),bool),calendar=np.asarray(days),symbols=np.asarray([f'sh{600001+j}' for j in range(5)]))
        for k,v in [('close',11.),('high',11.),('amount',2e8)]:m[k][:,25]=v
        joins=[dict(snapshotId='test',sectors=[dict(sectorCode='C39',cohortRowIndices=list(range(5)))])]
        old={'daily':[dict(date=days[i],index=i,snapshotId='test',market={'admission':True,'turnover5ToPrior20':1.},sectors=[{'sectorCode':'C39','sectorName':'synthetic industry'}]) for i in range(24,28)]}
        a=build_inputs(m,joins,old,[100.]*n)
        self.assertEqual(len(a['schedules']['complete']),1)
        self.assertEqual(a['metadata'][a['schedules']['complete'][0]['signalId']]['pattern'],'A_ignition')
        m['close'][:,28:]=100.;m['high'][:,28:]=100.
        self.assertEqual(a,build_inputs(m,joins,old,[100.]*n))

    def test_flat_entry_exits_next_day_and_not_same_day(self):
        c=case(); _,r=run(c)
        buy,sell=r['trades']
        self.assertEqual(sell['reason'],'entryNoFollowThrough')
        self.assertEqual(sell['date'],c[0]['calendar'][27])
        self.assertEqual(buy['date'],c[0]['calendar'][26])
        self.assertLess(sell['date'],c[0]['calendar'][30])

    def test_opening_guard_expires_and_gap_loss_exceeds_stop(self):
        c=case(); set_bar(c[0],26,opening=10.5)
        _,r=run(c); self.assertFalse(r['trades'])
        self.assertEqual(r['research']['opportunities'][0]['reason'],'openingTooExtended')
        c=case(); set_bar(c[0],27,opening=9.2,close=9.2)
        _,r=run(c)
        self.assertLess(r['closedTrades'][0]['returnPct'],-5)

    def test_market_exit_survives_halt_and_recovery(self):
        c=case(); set_bar(c[0],26,close=10.3)
        c[2]['daily'][26]['market']['admission']=False
        set_bar(c[0],27,volume=0)
        _,r=run(c)
        sell=next(t for t in r['trades'] if t['side']=='sell')
        self.assertEqual(sell['reason'],'marketExposureReduction')
        self.assertEqual(sell['signalDate'],c[0]['calendar'][26])
        self.assertEqual(sell['date'],c[0]['calendar'][29])

    def test_profitable_continuation_and_actual_costs(self):
        c=case()
        for i in range(26,36): set_bar(c[0],i,close=10.4,opening=10.3)
        set_bar(c[0],26,opening=10.1)
        set_bar(c[0],28,close=10.8,opening=10.5)
        _,r=run(c)
        self.assertGreater(r['closedTrades'][0]['returnPct'],0)
        self.assertTrue(all(t['fee']>0 for t in r['trades']))
        self.assertNotEqual(r['trades'][-1]['reason'],'entryNoFollowThrough')

    def test_current_volume_does_not_enlarge_opening_fill(self):
        a,b=case(),case()
        set_bar(a[0],26,volume=2_000_000)
        set_bar(b[0],26,volume=200_000_000)
        _,ra=run(a); _,rb=run(b)
        self.assertEqual(ra['trades'][0]['quantity'],rb['trades'][0]['quantity'])

    def test_only_future_edits_leave_earlier_fills_identical(self):
        a,b=case(),case()
        for i in range(35,len(b[0]['calendar'])): set_bar(b[0],i,close=20,opening=20)
        _,ra=run(a); _,rb=run(b)
        self.assertEqual(ra['trades'],rb['trades'])

    def test_yellow_reduces_risk_and_mutation_rejected(self):
        a,b=case(),case()
        for d in b[2]['daily']: d['market']['turnover5ToPrior20']=.9
        _,ra=run(a); _,rb=run(b)
        self.assertLess(rb['trades'][0]['quantity'],ra['trades'][0]['quantity'])
        c=case()
        e=DualPathAccount(c[0],c[1],configuration(c[0]),system_inputs=c[2],policy='complete',fee_policy=LegacyFeePolicy())
        e.system_inputs['metadata']['synthetic:0']['failurePrice']=9.9
        with self.assertRaises(ValueError):e.step(1)

    def test_market_boundaries_and_basic_exit_control(self):
        self.assertEqual(market_state({'admission':False},'complete')[0],'off')
        self.assertEqual(market_state({'admission':True,'turnover5ToPrior20':1},'complete')[0],'green')
        _,r=run(case(),'basicExits')
        self.assertEqual(r['trades'][-1]['reason'],'maxHoldingDays')


if __name__=='__main__':unittest.main()
