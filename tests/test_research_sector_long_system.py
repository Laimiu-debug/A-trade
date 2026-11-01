from copy import deepcopy
from datetime import date,timedelta
import importlib.util
from pathlib import Path
import unittest
import numpy as np

from service.research_sector_long_breakout_system import build_inputs,SectorLongAccount
from service.research_account_fees import LegacyFeePolicy
from tests.test_research_market_sector_account import setup
from tests.test_research_volatility_trail import configuration,set_bar

ROOT=Path(__file__).resolve().parents[1]


def fixture():
    d=date(2023,1,2);calendar=[]
    while len(calendar)<180:
        if d.weekday()<5:calendar.append(d.isoformat())
        d+=timedelta(days=1)
    close=np.array([[round(10+.01*(r%5+1)*t,2) for t in range(180)] for r in range(10)])
    m={'calendar':np.array(calendar),'symbols':np.array([f'sh{600001+r:06d}' for r in range(10)]),'close':close.copy(),'open':close.copy(),
        'high':np.round(close+.02,2),'low':np.round(close-.02,2),'volume':np.full(close.shape,1e7),'amount':np.full(close.shape,1e8),
        'priceValid':np.ones(close.shape,bool),'explicitSuspended':np.zeros(close.shape,bool)}
    for row in (4,9):m['high'][row,140]=18.55;m['close'][row,170]=18.6;m['high'][row,170]=18.62
    joins=[{'snapshotId':'one','sectors':[{'sectorCode':f'C{30+g}','cohortRowIndices':list(range(g*5,g*5+5))} for g in range(2)]}]
    sectors=[{'sectorCode':f'C{30+g}','featureValid':True,'score':1-g*.1} for g in range(2)]
    context={'daily':[{'date':calendar[i],'index':i,'snapshotId':'one','market':{'admission':True},'selectedSectors':['C30','C31'],'sectors':deepcopy(sectors)} for i in range(140,180)]}
    anchors={'schedules':{p:[] for p in ('completeSystem','withoutMarketAdmission','withoutSectorAdmission','withoutCatchupEntry')},'metadata':{}}
    for p in ('completeSystem','withoutMarketAdmission','withoutSectorAdmission'):
        identifier='anchor:'+p
        signal={'signalId':identifier,'strategyId':'marketSectorStockV1','symbol':str(m['symbols'][0]),'signalDate':calendar[170],'originalRank':1}
        meta={**signal,'pattern':'catchup','signalClose':float(m['close'][0,170]),'failurePrice':float(m['close'][0,170]*.97),'meanNtr20':.02,
            'sectorCode':'C30','snapshotId':'one','sectorScore':1.,'priorAmount20':1e8,'stockReturn60Midrank':.3,'priorLeaderRows':[4],
            'base':{'failurePrice':float(m['close'][0,170]*.97)}}
        meta.pop('strategyId');anchors['schedules'][p].append(signal);anchors['metadata'][identifier]=meta
    return m,joins,context,anchors


def current(result,m,policy='completeSystem'):return [s for s in result['schedules'][policy] if s['signalDate']==m['calendar'][170]]


def oracle():
    spec=importlib.util.spec_from_file_location('_test_long_source_oracle',ROOT/'scripts/verify-sector-long-inputs.py')
    v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v);return v


def account_case(pattern='longLeaderBreakout',ntr=.08,opening=10.,floor=9.7):
    data,signals,inputs,policy=setup()
    for m in inputs['metadata'].values():m.update(pattern=pattern,failurePrice=floor,meanNtr20=ntr,base={'failurePrice':floor},anchorSourceId='old:test',anchorOriginalRank=1)
    set_bar(data,26,opening=opening)
    a=SectorLongAccount(data,signals,configuration(data),system_inputs=inputs,policy=policy,policy_label=policy,fee_policy=LegacyFeePolicy());a.step(20000)
    return a,a.result()


class SectorLongTests(unittest.TestCase):
    def test_long_role_early_first_breakout_and_independent_source_rebuild(self):
        args=fixture();result=build_inputs(*args)
        rows=current(result,args[0]);self.assertEqual(len(rows),2)
        self.assertTrue(all(result['metadata'][s['signalId']]['pattern']=='longLeaderBreakout' for s in rows))
        self.assertTrue(oracle().audit(*args,result)['passed'])
        wrong=deepcopy(result);wrong['metadata'][rows[0]['signalId']]['longEvidence']['ma120Lag20']+=.01
        with self.assertRaises(ValueError):oracle().audit(*args,wrong)

    def test_full141_window_and_explicit_suspend_are_not_compressed(self):
        for field in ('priceValid','explicitSuspended'):
            m,j,c,a=fixture();m[field][4,40]=field=='explicitSuspended'
            r=build_inputs(m,j,c,a)
            at=current(r,m)
            self.assertFalse(any(s['symbol']==m['symbols'][4] for s in at))
            self.assertTrue(any(s['symbol']==m['symbols'][0] for s in at))

    def test_exact_prior20_return_limit_and_first_only(self):
        m,j,c,a=fixture();m['close'][4,149]=16.;m['open'][4,149]=16.;m['high'][4,149]=16.02;m['low'][4,149]=15.98
        m['close'][4,169]=18.4;m['low'][4,169]=18.38
        self.assertTrue(any(s['symbol']==m['symbols'][4] for s in current(build_inputs(m,j,c,a),m)))
        m['close'][4,169]=18.41
        self.assertFalse(any(s['symbol']==m['symbols'][4] for s in current(build_inputs(m,j,c,a),m)))
        m,j,c,a=fixture();m['close'][4,169]=18.6;m['high'][4,169]=18.62;m['close'][4,170]=18.7;m['high'][4,170]=18.72
        self.assertFalse(any(s['symbol']==m['symbols'][4] for s in current(build_inputs(m,j,c,a),m)))

    def test_equal_peer_ranks_and_liquidity_ties_resolve_by_symbol(self):
        m,j,c,a=fixture()
        for field in ('open','high','low','close','amount','volume'):m[field][3]=m[field][4]
        result=build_inputs(m,j,c,a);candidate=next(s for s in current(result,m) if s['symbol'] in (m['symbols'][3],m['symbols'][4]))
        self.assertEqual(candidate['symbol'],m['symbols'][3]);self.assertAlmostEqual(result['metadata'][candidate['signalId']]['stockReturn120Midrank'],.8)
        self.assertTrue(oracle().audit(m,j,c,a,result)['passed'])

    def test_branch_priority_controls_and_market_industry_admission(self):
        m,j,c,a=fixture();result=build_inputs(m,j,c,a)
        self.assertTrue(any(s['symbol']==m['symbols'][0] for s in current(result,m,'catchupOnly')))
        self.assertFalse(any(s['symbol']==m['symbols'][0] for s in current(result,m,'withoutCatchupEntry')))
        day=next(d for d in c['daily'] if d['index']==170);day['selectedSectors']=['C30']
        self.assertEqual(len(current(build_inputs(m,j,c,a),m)),1)
        self.assertEqual(len(current(build_inputs(m,j,c,a),m,'withoutSectorAdmission')),2)
        day['market']['admission']=False
        self.assertFalse(current(build_inputs(m,j,c,a),m))
        self.assertEqual(len(current(build_inputs(m,j,c,a),m,'withoutMarketAdmission')),1)

    def test_future_candles_cannot_change_current_roles_or_composition(self):
        m,j,c,a=fixture();before=build_inputs(m,j,c,a)
        for k in ('open','high','low','close','volume','amount'):m[k][:,171:]*=2
        after=build_inputs(m,j,c,a)
        self.assertEqual(current(before,m),current(after,m))
        for s in current(before,m):self.assertEqual(before['metadata'][s['signalId']],after['metadata'][s['signalId']])

    def test_catchup_keeps_its_opening_risk_guard_and_long_has_no_volatility_cap(self):
        _,result=account_case();self.assertTrue(result['trades']);self.assertFalse(result['research']['system']['catchupOpeningGuards'])
        _,result=account_case('catchup',.02,10.1,9.5)
        self.assertFalse(result['trades']);self.assertEqual(result['research']['opportunities'][0]['reason'],'preparedBaseOpeningRiskTooWide')
        _,result=account_case('longLeaderBreakout',.08,10.1,9.5);self.assertTrue(result['trades'])

    def test_suspension_mask_bound_to_actual_source_flags(self):
        m,j,c,a=fixture();data={'securities':[{'symbol':str(symbol),'bars':[{'date':str(day),'suspended':r==4 and t==40} for t,day in enumerate(m['calendar'])]} for r,symbol in enumerate(m['symbols'])]}
        with self.assertRaises(ValueError):oracle().audit_suspensions(data,m)
        m['explicitSuspended'][4,40]=True
        self.assertEqual(oracle().audit_suspensions(data,m)['explicitSuspendedSlots'],1)


if __name__=='__main__':unittest.main()
