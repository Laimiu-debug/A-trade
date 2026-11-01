"""Adversarial tests for frozen decision-to-schedule reuse, no real inputs."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_all_feedback_audit_tested', ROOT/'scripts/verify-all-opportunity-feedback.py')
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)


def fixture():
    calendar = [(date(2020,1,1)+timedelta(days=i)).isoformat() for i in range(130)]
    targets = calendar[127:]
    daily = []
    for day in targets:
        i = calendar.index(day); slots = calendar[i-126:i]
        daily.append(dict(formationDate=day,asOfDate=calendar[i-1],
            window=dict(requestedSlots=126,availableSlots=126,startDate=slots[0],endDate=slots[-1],historyTruncated=False),
            support={'eligibleFormationDayCount':20},currentFeatureStatus='complete',
            models={m:dict(status='fitted',reason='positivePrediction',prediction=.01,allow=True) for m in A.MODELS}))
    signals = [dict(signalId=f'{A.SID}:{d}:s{rank}',strategyId=A.SID,symbol=f's{rank}',signalDate=d,originalRank=rank)
               for d in targets for rank in (1,3,5)]
    return calendar,targets,daily,signals


class FrozenMappingTests(unittest.TestCase):
    def test_different_population_does_not_change_original_identity(self):
        cal,targets,daily,signals = fixture(); before = deepcopy((daily,signals))
        daily[1]['models']['ridge']=dict(status='fitted',reason='nonPositivePrediction',prediction=-.01,allow=False)
        expected = signals[:3]+signals[6:]
        got,counts=A.schedule_reference(signals,daily,'ridge',cal,targets)
        self.assertEqual(got,expected); self.assertEqual([s['originalRank'] for s in got],[1,3,5]*2)
        self.assertEqual(counts,dict(originalSignals=9,included=6,excluded=3,fallbackSignals=0))
        self.assertEqual(signals,before[1]); self.assertEqual(A.candidate_dimensions('allExecutable__ridge'),('allExecutable','ridge'))

    def test_zero_and_negative_predictions_rejected_without_rounding(self):
        cal,targets,daily,signals = fixture()
        for i,value in enumerate((0.,-1e-20,1e-20)):
            daily[i]['models']['intercept']=dict(status='fitted',reason='positivePrediction' if value>0 else 'nonPositivePrediction',prediction=value,allow=value>0)
        got,_=A.schedule_reference(signals,daily,'intercept',cal,targets)
        self.assertEqual(got,signals[6:])
        daily[0]['models']['intercept']['allow']=True
        with self.assertRaises(AssertionError): A.schedule_reference(signals,daily,'intercept',cal,targets)

    def test_common_fallback_preserves_signals(self):
        cal,targets,daily,signals = fixture()
        daily[0]['support']['eligibleFormationDayCount']=19
        for m in A.MODELS:
            daily[0]['models'][m]=dict(status='fallback',reason='insufficientTrainingFormationDays',prediction=None,allow=True)
        daily[1]['currentFeatureStatus']='unknown'
        for m in A.MODELS:
            daily[1]['models'][m]=dict(status='fallback',reason='currentFeaturesUnknown',prediction=None,allow=True)
        got,counts=A.schedule_reference(signals,daily,'ridge',cal,targets)
        self.assertEqual(got,signals); self.assertEqual(counts['fallbackSignals'],6)
        daily[1]['models']['ridge']['allow']=False
        with self.assertRaises(AssertionError): A.schedule_reference(signals,daily,'ridge',cal,targets)

    def test_future_clock_and_window_tamper_rejected(self):
        cal,targets,daily,signals=fixture()
        for key, value in [('asOfDate', targets[0]),('window',dict(daily[0]['window'],startDate=cal[0]))]:
            altered=deepcopy(daily);altered[0][key]=value
            with self.assertRaises(AssertionError): A.schedule_reference(signals,altered,'ridge',cal,targets)

    def test_no_missing_duplicate_or_reordered_originals(self):
        cal,targets,daily,signals=fixture()
        for bad in (daily[:-1],daily+[daily[-1]],list(reversed(daily))):
            with self.assertRaises(AssertionError): A.schedule_reference(signals,bad,'ridge',cal,targets)
        for bad in (signals+[signals[-1]],list(reversed(signals))):
            with self.assertRaises(AssertionError): A.schedule_reference(bad,daily,'ridge',cal,targets)

    def test_same_numeric_fee_not_same_provenance(self):
        self.assertNotEqual(A.digest({'minCommission':5}),A.digest({'minCommission':5.0}))
        self.assertEqual(5*.01,5.0*.01)

    def test_invalid_bool_and_nonfinite_predictions_rejected(self):
        cal,targets,daily,signals=fixture()
        for changed in ({'allow':1},{'prediction':float('nan')},{'prediction':float('inf')},{'prediction':True}):
            bad=deepcopy(daily);bad[0]['models']['ridge'].update(changed)
            with self.assertRaises(AssertionError): A.schedule_reference(signals,bad,'ridge',cal,targets)


if __name__=='__main__': unittest.main()
