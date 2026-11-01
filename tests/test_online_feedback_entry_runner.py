"""Synthetic schedule/account integration; no historical inputs or file writes."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

from service.research_online_feedback import build_online_feedback
from service.research_fixed_signal_account import FixedSignalAccount
from service.research_volatility_trail import VolatilityTrailAccount
from service.research_account_fees import LegacyFeePolicy
from tests.test_research_volatility_trail import fixture, configuration, schedule

spec = importlib.util.spec_from_file_location('_online_runner_test', Path(__file__).resolve().parents[1]/'scripts/run-online-feedback-entry.py')
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)


def environments(calendar):
    return [{'formationDate': day, 'asOfDate': calendar[i-1],
        'price20': {'positiveReturn20Breadth': .5}, 'amount': {'medianRelativeToOwnPrior20': 1.},
        'retention': {'continuous': {'retentionLower': .5}}} for i,day in enumerate(calendar) if i]


def execute(data, signals, exit_name):
    cls = FixedSignalAccount if exit_name == 'trail10' else VolatilityTrailAccount
    cfg = configuration(data, trailingStopPct=.1 if exit_name == 'trail10' else 0.)
    engine = cls(data, signals, cfg, entry_weight=.2, fee_policy=LegacyFeePolicy())
    engine.step(20000)
    return engine.result()


class OnlineEntryIntegrationTests(unittest.TestCase):
    def test_insufficient_feedback_preserves_original_account_and_next_open(self):
        data = fixture(); cal=data['calendar']; original=schedule(data)
        feedback=build_online_feedback(cal, cal[25:], environments(cal), [])
        for exit_name in ('trail10','volatilityTrail'):
            baseline=execute(data, original, exit_name)
            for mode in ('intercept','ridge'):
                selected=runner.select_schedule(original,feedback['dailyRows'],mode)
                self.assertEqual(selected,original)
                got=execute(data,selected,exit_name)
                for key in ('trades','curve','closedTrades','positions'):
                    self.assertEqual(got[key],baseline[key])
                self.assertEqual(got['trades'][0]['date'],cal[26])
                self.assertGreater(got['trades'][0]['date'],original[0]['signalDate'])

    def test_negative_closed_feedback_yields_real_cash_only_account(self):
        data=fixture(); cal=data['calendar']; original=schedule(data)
        shadows=[{'formationId':str(i),'formationDate':cal[i],'entryDate':cal[i+1],
                  'exitDate':cal[i+2],'netReturn':-.05} for i in range(1,21)]
        feedback=build_online_feedback(cal,cal[25:],environments(cal),shadows)
        for mode in ('intercept','ridge'):
            selected=runner.select_schedule(original,feedback['dailyRows'],mode)
            self.assertEqual(selected,[])
            for exit_name in ('trail10','volatilityTrail'):
                result=execute(data,selected,exit_name)
                self.assertEqual(result['trades'],[])
                self.assertTrue(all(row['equity']==50000 and row['cash']==50000 for row in result['curve']))

    def test_filter_never_reranks_or_backfills_and_keeps_inputs(self):
        data=fixture(); original=schedule(data,indexes=(25,27)); original[1]['originalRank']=4
        before=deepcopy(original)
        decisions=[{'formationDate':s['signalDate'],'models':{'intercept':{'allow':i==1}}} for i,s in enumerate(original)]
        got=runner.select_schedule(original,decisions,'intercept')
        self.assertEqual(got,[before[1]]); self.assertEqual(got[0]['originalRank'],4)
        got[0]['originalRank']=1
        self.assertEqual(original,before)
        with self.assertRaises(ValueError):runner.select_schedule(original,decisions[:1],'intercept')
        with self.assertRaises(ValueError):runner.select_schedule(original,decisions+decisions,'intercept')

    def test_shadow_join_uses_net_cost_denominator_and_excludes_open_label(self):
        original=[{'formationId':'one','formationDate':'2022-01-03','symbol':'a','accountDispositions':{'trail10':{
            'filledQuantity':100,'entryDate':'2022-01-04','exitDate':'2022-01-05','positionState':'closed','closedReturnPct':-5.}}},
            {'formationId':'two','formationDate':'2022-01-05','symbol':'b','accountDispositions':{'trail10':{
            'filledQuantity':100,'entryDate':'2022-01-06','exitDate':None,'positionState':'openAtCutoff'}}}]
        summary={'closedEpisodes':{'count':1,'episodes':[{'record':{'symbol':'a','entryDate':'2022-01-04',
            'exitDate':'2022-01-05','pnlAmount':-50.,'investedAmount':1000.},'netReturnPct':-5.}]},
            'openPositions':{'positions':[{'record':{'symbol':'b','entryDate':'2022-01-06'}}]}}
        got=runner.shadow_rows(original,'trail10',summary)
        self.assertEqual(got[0]['netReturn'],-.05)
        self.assertIsNone(got[1]['netReturn']); self.assertIsNone(got[1]['exitDate'])
        bad=deepcopy(summary);bad['closedEpisodes']['episodes'][0]['record']['investedAmount']=900.
        with self.assertRaises(ValueError):runner.shadow_rows(original,'trail10',bad)


if __name__=='__main__': unittest.main()
