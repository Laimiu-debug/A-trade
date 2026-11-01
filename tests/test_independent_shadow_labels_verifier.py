"""Small independent-auditor boundaries; no historical source/account runs."""
from copy import deepcopy
from datetime import date,timedelta
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('shadow_reference',ROOT/'scripts/verify-independent-shadow-labels.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)


def fixture():
    days=[(date(2022,1,3)+timedelta(days=i)).isoformat() for i in range(18)]
    bars=[dict(date=d,open=10.,high=10.2,low=9.8,close=10.,volume=1000000.,amount=10000000.) for d in days]
    security=dict(symbol='sh600001',bars=bars,stByDate={d:False for d in days})
    data=dict(calendar=days,calendarSource='independent-test',securities=[security],benchmark={'bars':[]},adjustment='raw')
    signal=dict(signalId='sid',symbol=security['symbol'],signalDate=days[10],originalRank=1,strategyId='synthetic')
    config=dict(startDate=days[0],endDate=days[-1],initialCapital=500000.,slippageBps=10,maxParticipationRate=.05,
                universe=dict(excludeSt=True,strictHistory=False))
    return days,data,signal,config


def fill(day,side,qty,cash):
    price=10.01 if side=='buy' else 9.99
    fee=v.fees(qty*price,side,day,'2022-2023')
    cash+=(qty*price-fee) if side=='sell' else -(qty*price+fee)
    return dict(date=day,symbol='sh600001',side=side,quantity=qty,openingPrice=10.,price=price,fee=fee,cashAfter=cash),cash


class IndependentShadowVerifierTests(unittest.TestCase):
    def test_projection_preserves_calendar_status_and_universe_evidence(self):
        days,data,signal,config=fixture()
        other=deepcopy(data['securities'][0]);other['symbol']='sh600002';data['securities'].append(other)
        data['membershipByDate']={days[0]:['sh600001','sh600002'],days[-1]:[]}
        before=v.digest(data);projected=v.projection_reference(data,signal['symbol'])
        self.assertEqual(before,v.digest(data));self.assertEqual(projected['membershipByDate'],data['membershipByDate'])
        self.assertIs(projected['securities'][0],data['securities'][0]);self.assertIs(projected['calendar'],data['calendar'])

    def test_full_prefix_quarantine_blocks_later_buy_without_zero_label(self):
        days,data,signal,config=fixture()
        data['securities'][0]['bars'][7].update(high=14.,close=14.)
        result=v.execution_reference(signal,data,config,'2022-2023',[],[],days[:12])
        self.assertTrue(result['quarantined']);self.assertEqual(result['opportunity']['status'],'notPlanned')
        self.assertEqual(result['opportunity']['reason'],'soldTodayOrQuarantined');self.assertEqual(result['terminalQuantity'],0)
        self.assertIsNone(result['opportunity']['attemptDate']);self.assertIsNone(result['opportunity']['plannedQuantity'])
        with self.assertRaisesRegex(ValueError,'Original complete period prefix'):
            v.execution_reference(signal,data,config,'2022-2023',[],[],days[10:12])

    def test_partial_buy_remainder_expires_and_partial_sells_retry(self):
        days,data,signal,config=fixture();rows=data['securities'][0]['bars']
        rows[10]['volume']=20000.;rows[12]['volume']=4000.
        buy,cash=fill(days[11],'buy',1000,500000.)
        sell1,cash=fill(days[13],'sell',200,cash);sell2,cash=fill(days[14],'sell',800,cash)
        result=v.execution_reference(signal,data,config,'2022-2023',[buy,sell1,sell2],[{'date':days[12]}],days[:15])
        self.assertEqual(result['opportunity']['status'],'partial');self.assertEqual(result['opportunity']['filledQuantity'],1000)
        self.assertEqual(result['opportunity']['unfilledQuantity'],8900);self.assertEqual(result['fillCount'],3)
        self.assertEqual(result['terminalQuantity'],0);self.assertAlmostEqual(result['terminalCash'],cash)

    def test_missing_next_session_no_delayed_buy(self):
        days,data,signal,config=fixture();del data['securities'][0]['bars'][11]
        result=v.execution_reference(signal,data,config,'2022-2023',[],[],days[:14])
        self.assertEqual(result['opportunity']['status'],'expired');self.assertEqual(result['opportunity']['reason'],'missingBar')
        self.assertEqual(result['opportunity']['attemptDate'],days[11])

    def test_cutoff_signal_remains_censored(self):
        days,data,signal,config=fixture();signal['signalDate']=days[-1]
        result=v.execution_reference(signal,data,config,'2022-2023',[],[],days)
        self.assertEqual(result['opportunity']['status'],'censored');self.assertIsNone(result['opportunity']['attemptDate'])

    def test_engineering_sample_is_bounded_and_never_profit_ranked(self):
        rows=[dict(positionState=state,trades=[],opportunity={'status':'filled'},profit=x)
              for state,x in [('notEntered',1000),('closed',-1000),('closed',9000),('openAtCutoff',0),('closed',1)]]
        rows[2]['trades']=[{'positionAction':'reduce'}]
        self.assertEqual(v.deterministic_samples(rows),[0,4,1,3,2])
        for row in rows:row['profit']=-row['profit']
        self.assertEqual(v.deterministic_samples(rows),[0,4,1,3,2])

    def test_registered_fee_number_type_preserves_identity_not_only_economics(self):
        fee=v.read(v.bound(v.FEE_BINDING))['feePolicy']
        cfg={'commissionRate':.0003,'minCommission':5.,'stampDutyRate':None,'transferFeeRate':.00001}
        actual=v.dated_policy_reference(fee).settings(cfg)
        floating=deepcopy(fee);floating['minCommission']=5.
        other=v.dated_policy_reference(floating).settings(cfg)
        self.assertIs(type(actual['minCommission']),int)
        self.assertIs(type(other['minCommission']),float)
        self.assertEqual(actual,other)
        self.assertNotEqual(v.digest(actual),v.digest(other))
        self.assertEqual(v.digest(actual),v.digest(v.dated_policy_reference(fee).settings(cfg)))


if __name__=='__main__':unittest.main()
