"""Independent switch truth table and identity preservation regressions."""
from copy import deepcopy
from datetime import date,timedelta
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_common_switch_audit_tested',ROOT/'scripts/verify-common-support-switch.py')
A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)


def decision(value=None):
    return dict(status='fallback',reason='insufficientTrainingFormationDays',prediction=None,allow=True) if value is None else dict(
        status='fitted',reason='positivePrediction' if value>0 else 'nonPositivePrediction',prediction=value,allow=value>0)


def fixture():
    calendar=[(date(2020,1,1)+timedelta(days=i)).isoformat() for i in range(134)]
    targets=calendar[130:];original=[];all_rows=[]
    for offset,day in enumerate(targets):
        i=calendar.index(day);slots=calendar[i-126:i]
        base=dict(formationDate=day,asOfDate=calendar[i-1],window=dict(requestedSlots=126,availableSlots=126,
            startDate=slots[0],endDate=slots[-1],historyTruncated=False),currentFeatureStatus='complete')
        left=[-.1,-.1,None,None][offset];right=[.1,None,-.1,None][offset]
        for dest,value in [(original,left),(all_rows,right)]:
            dest.append({**deepcopy(base),'support':{'eligibleFormationDayCount':19 if value is None else 20},
                         'models':{m:decision(value) for m in A.MODELS}})
    signals=[dict(signalId=f'{A.P.SID}:{day}:s{rank}',strategyId=A.P.SID,symbol=f's{rank}',signalDate=day,originalRank=rank)
             for day in targets for rank in (1,3,5)]
    return calendar,targets,original,all_rows,signals


class CommonSupportTests(unittest.TestCase):
    def test_four_support_states_and_actual_choice(self):
        cases=[(-.1,.1,'bothFitted','allExecutable',True),(-.1,None,'originalOnlyFitted','originalEntered',False),
               (None,-.1,'allOnlyFitted','originalEntered',True),(None,None,'neitherFitted','originalEntered',True)]
        for a,b,state,selected,allow in cases:
            self.assertEqual(A.switch_decision_reference(decision(a),decision(b)),dict(supportState=state,selectedPopulation=selected,allow=allow))

    def test_both_fitted_four_allow_combinations(self):
        for a in (-.1,.1):
            for b in (-.2,.2):
                got=A.switch_decision_reference(decision(a),decision(b))
                self.assertEqual(got['allow'],b>0);self.assertEqual(got['supportState'],'bothFitted')

    def test_all_only_fitted_never_overrides_original_fallback(self):
        for right in (-.5,0.,.5):
            got=A.switch_decision_reference(decision(),decision(right))
            self.assertTrue(got['allow']);self.assertEqual(got['selectedPopulation'],'originalEntered')

    def test_schedules_preserve_sparse_rank_original_ids_and_inputs(self):
        cal,dates,left,right,signals=fixture();saved=deepcopy((left,right,signals))
        selected,daily=A.switch_schedule_reference(signals,left,right,'ridge',cal,dates)
        self.assertEqual(selected,signals[:3]+signals[6:]);self.assertEqual([d['supportState'] for d in daily],list(A.SUPPORT_STATES))
        self.assertEqual((left,right,signals),saved)

    def test_zero_cells_and_switch_denominators(self):
        cal,dates,left,right,signals=fixture()
        selected,daily=A.switch_schedule_reference(signals,left,right,'intercept',cal,dates)
        counts=A.count_reference(signals,left,right,'intercept',daily)
        self.assertEqual(counts['supportStateDays'],dict.fromkeys(A.SUPPORT_STATES,1))
        self.assertEqual(counts['supportStateSignals'],dict.fromkeys(A.SUPPORT_STATES,3))
        self.assertEqual((counts['changedAllowDays'],counts['changedAllowSignals'],counts['addedSignals'],counts['removedSignals']),(1,3,3,0))
        self.assertEqual(counts['selectedPopulationDays'],{'originalEntered':3,'allExecutable':1})
        self.assertEqual(counts['scheduledSignals'],len(selected))
        self.assertEqual(sum(counts['admittedSignalsBySupportState'].values())+sum(counts['rejectedSignalsBySupportState'].values()),len(signals))

    def test_population_clock_mismatch_and_missing_day_rejected(self):
        cal,dates,left,right,signals=fixture()
        for bad in (right[:-1],deepcopy(right)):
            if len(bad)==len(right):bad[0]['asOfDate']=dates[0]
            with self.assertRaises(AssertionError):A.switch_schedule_reference(signals,left,bad,'ridge',cal,dates)

    def test_zero_rejection_and_bad_allow_type(self):
        self.assertFalse(A.switch_decision_reference(decision(.1),decision(0.))['allow'])
        bad=decision(.1);bad['allow']=1
        with self.assertRaises(AssertionError):A.switch_decision_reference(decision(.1),bad)

    def test_new_batch_reuse_is_not_old_reused_flag(self):
        old=[{'candidate':'baseline','reusedAccount':True},{'candidate':'originalEntered__ridge','reusedAccount':False}]
        new={'candidate':'commonSupport__ridge','reusedAccount':False}
        self.assertEqual(sum(r['candidate'] in A.INHERITED_CANDIDATES for r in [*old,new]),2)
        self.assertEqual(sum(r['reusedAccount'] for r in [*old,new]),1)


if __name__=='__main__':unittest.main()
