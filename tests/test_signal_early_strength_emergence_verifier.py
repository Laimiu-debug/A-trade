"""Synthetic independent-reference and completed-run audit regressions."""
from copy import deepcopy
from contextlib import contextmanager, redirect_stdout
from datetime import date, timedelta
import gzip
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from statistics import mean

import test_signal_early_strength_emergence_runner as fixture_module

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'scripts/verify-signal-early-strength-emergence-result.py'


def module():
    spec = importlib.util.spec_from_file_location('independent_early_strength_test', SOURCE)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class EarlyStrengthEmergenceVerifierTests(unittest.TestCase):
    def setUp(self):
        self.verifier = module()

    def test_json_duplicate_nonfinite_and_boolean_numeric_are_rejected(self):
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.verifier.parse(text)
        self.assertFalse(self.verifier.number(True))
        self.assertFalse(self.verifier.number(float('inf')))

    def example(self, index=70, symbol='sh600001', rank=.95, rank60=.7):
        calendar = [(date(2022,1,1)+timedelta(days=i)).isoformat() for i in range(100)]
        days = calendar[max(0,index-60):index+1]
        history = [.4]*19+[rank]
        def quality(n, window):
            return {'complete':True, 'requiredSessions':n, 'availableCalendarSessions':n,
                'observedSessions':n, 'validSessions':n, 'missingDates':[], 'invalidDates':[],
                'haltDates':[], 'reasons':[], 'unknownTradeStatusDates':list(window)}
        row = {'symbol':symbol,'asOfDate':calendar[index],'featureVersion':'daily-leadership-raw-v1',
            'rawPriceBasis':'raw-unadjusted','baseEligibility':{'eligible':True,'reason':'eligible','unknown':[]},
            'featureReasons':{},'completeness':{},'return20':.1,'return60':.1,'relativeReturn20':.1,'relativeReturn60':.1,
            'relativeReturn20Percentile':rank,'relativeReturn60Percentile':rank60,
            'relativeReturn20CrossSectionCount':9,'relativeReturn60CrossSectionCount':9,
            'trailing20DailyStrengthPercentiles':history,'trailing20StrengthCrossSectionCounts':[9]*20,
            'strengthRankSessionDates20':days[-20:],'knownStrengthRankSessions20':20,'availableStrengthRankSessions20':20,
            'meanStrengthPercentile20':mean(history),'minimumStrengthPercentile20':min(history),
            'prior20MeanAmount':100_000_000.,'knownAmountSessions20':20}
        for h in (20,60):
            for prefix in ('security','benchmark'):
                row['completeness'][prefix+str(h)] = quality(h+1,days[-h-1:])
        row['completeness']['amountPrior20'] = quality(20,days[-21:-1])
        return calendar, days, row

    def raw(self, days, *, rising=True):
        return [{'date':d,'open':10.+i*.1 if rising else 10.,'close':10.+i*.1 if rising else 10.,
            'low':9.,'high':12.,'volume':1000.,'priceBasisId':'raw-unadjusted',
            'knownSuspended':False,'tradeStatusState':'unknown'} for i,d in enumerate(days)]

    def test_independent_projection_groups_history_alignment_and_prior_amount(self):
        _, days, row = self.example()
        p = self.verifier.reference_project(row['symbol'],row['asOfDate'],days,row)
        self.assertTrue(all(p[k]['known'] for k in ('rank20','rank60','history20','amountPrior20')))
        row['completeness']['benchmark60']['complete'] = False
        row['amountToday'] = None
        changed = self.verifier.reference_project(row['symbol'],row['asOfDate'],days,row)
        self.assertFalse(changed['rank60']['known'])
        self.assertTrue(changed['rank20']['known']); self.assertTrue(changed['amountPrior20']['known'])
        row['strengthRankSessionDates20'][-1] = days[-2]
        row['knownAmountSessions20'] = True
        changed = self.verifier.reference_project(row['symbol'],row['asOfDate'],days,row)
        self.assertFalse(changed['history20']['known']); self.assertFalse(changed['amountPrior20']['known'])
        with self.assertRaises(ValueError):
            self.verifier.reference_project(row['symbol'],row['asOfDate'],[*days[:-1],'2099-01-01'],row)

    def test_six_slot_unknown_status_excess_independence_and_derived_overflow(self):
        _, days, row = self.example()
        days = days[-6:]
        stock, benchmark = self.raw(days), self.raw(days,rising=False)
        advance = self.verifier.reference_advance(row['symbol'],days[-1],days,stock,benchmark)
        self.assertEqual(advance['state'],'true')
        self.assertEqual(advance['stockQuality']['unknownTradeStatusDates'],days)
        missing = deepcopy(benchmark); missing[2] = None
        advance = self.verifier.reference_advance(row['symbol'],days[-1],days,stock,missing)
        self.assertEqual(advance['predicates']['own5']['state'],'true')
        self.assertEqual(advance['predicates']['excess5']['state'],'unknown')
        for i,bar in enumerate(stock):
            price = 1e308 if i == 5 else 1e-308
            bar.update(open=price,close=price,low=price,high=price)
        advance = self.verifier.reference_advance(row['symbol'],days[-1],days,stock,benchmark)
        self.assertEqual(advance['state'],'unknown')
        self.assertIn('nonfiniteOrUnderflowDerivedRatio',advance['stockQuality']['reasons'])

    def test_prefix_oracle_first_day_locked_failed_cross_and_cutoff(self):
        calendar,_,_ = self.example()
        targets = calendar[70:76]; symbol='sh600001'
        state = self.verifier.ReferenceStudy(targets,[symbol],calendar)
        outputs=[]
        for n,rank in enumerate((.4,.95,.4,.95,.4,.95)):
            _,days,row=self.example(70+n,rank=rank)
            if n==1: row['baseEligibility']={'eligible':False,'reason':'delisted','unknown':[]}
            projection=self.verifier.reference_project(symbol,targets[n],days,row)
            outputs.append(state.step(targets[n],n,{symbol:projection},{symbol:self.raw(days[-6:])},self.raw(days[-6:],rising=False)))
        self.assertEqual(outputs[0]['evaluations'][0]['crossingReason'],'previousFullPanelUnavailable')
        self.assertEqual(outputs[1]['formations'][0]['episode']['state'],'formationRejected')
        self.assertEqual(outputs[3]['formations'][0]['kind'],'formationSuppressedByLock')
        self.assertEqual(len(outputs[5]['formations']),1)
        terminal=state.finish(targets[-1])
        self.assertEqual([e['state'] for e in terminal['episodes']],['formationRejected','censoredAtStudyCutoff'])
        with self.assertRaises(ValueError): state.finish(targets[-1])

    @contextmanager
    def completed_fixture(self, prepare=None):
        f = fixture_module.EarlyStrengthEmergenceRunnerTests()
        f.setUp()
        try:
            (f.root/'synthetic-contract.txt').write_bytes(b'Eligibility fixture only; no actual market audit.')
            if prepare is not None:
                prepare(f)
                f.rebind()
            self.assertEqual(f.run_complete(),0)
            with patch.object(self.verifier,'REGISTRATION',f.registration), \
                 patch.object(self.verifier,'REGISTRATION_SHA256',f.registration_hash):
                yield f
        finally:
            f.doCleanups()

    def verify_fixture(self,f):
        with redirect_stdout(io.StringIO()):
            return self.verifier.verify(f.output,root=f.root)

    def resign(self,f):
        receipt=f.runner.read_json(f.attempt()/'completion.json')
        for item in receipt['files'].values(): item['sha256']=f.runner.sha(f.output/item['path'])
        fixture_module.write(f.attempt()/'completion.json',receipt)
        path=f.output/'events.jsonl'
        events=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        events[-1]['receipt']=receipt
        path.write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events),encoding='utf-8')

    def test_nonempty_independent_complete_audit_and_saved_report_verify_only(self):
        with self.completed_fixture() as f:
            report=self.verify_fixture(f)
            self.assertTrue(report['passed'],report)
            self.assertEqual(report['evaluatedSecuritySessions'],360)
            self.assertGreater(report['slotAccounting']['globalTop5Slots'],0)
            self.assertGreater(report['slotAccounting']['earlyMaskCounts']['unknown'],0)
            self.assertGreater(report['eventsChecked']['filledEvents'],0)
            self.verifier.save_report(f.output,report)
            with patch.object(f.runner,'collect_signals',side_effect=AssertionError('no replay')):
                self.assertEqual(f.execute(),0)

    def test_empty_policy_result_still_audits_full_population_and_all_episodes(self):
        def reject(f):
            for row in f.rows:
                row['baseEligibility']={'eligible':False,'reason':'amount20BelowMinimum','unknown':[]}
        with self.completed_fixture(reject) as f:
            report=self.verify_fixture(f)
            self.assertTrue(report['passed'],report)
            self.assertEqual(report['evaluatedSecuritySessions'],360)
            self.assertEqual(report['eventsChecked']['events'],0)
            self.assertEqual(report['slotAccounting']['globalTop5Slots'],0)
            self.assertGreater(report['episodeAccounting']['episodes'],0)

    def test_missing_duplicate_and_extra_full_population_predicates_are_rejected(self):
        with self.completed_fixture() as f:
            path=f.attempt()/'predicates.jsonl.gz'
            original=fixture_module.lines(path)
            variants=(original[:-1],[original[0],original[0],*original[2:]],[*original,original[-1]])
            for rows in variants:
                with self.subTest(rows=len(rows)):
                    fixture_module.write_lines(path,rows); self.resign(f)
                    report=self.verify_fixture(f)
                    self.assertFalse(report['passed'])
                    self.assertTrue(any(i['check'] in ('independent-crossing-lock-evaluation','output-exact-EOF:predicates')
                                        for i in report['issues']),report)

    def test_rehashed_mask_slot_terminal_and_horizon_semantics_rejected(self):
        with self.completed_fixture() as f:
            path=f.attempt()/'selection.jsonl.gz'; original=fixture_module.lines(path)
            changed=deepcopy(original)
            next(r for r in changed if r['selection']['slots'])['selection']['slots'][0]['earlyMask']='false'
            fixture_module.write_lines(path,changed); self.resign(f)
            report=self.verify_fixture(f)
            self.assertIn('independent-ranking-mask',{i['check'] for i in report['issues']})
            fixture_module.write_lines(path,original)
            terminal=f.attempt()/'episode-terminals.jsonl.gz'; originals=fixture_module.lines(terminal)
            changed=deepcopy(originals); changed[0]['state']='rankLost'
            fixture_module.write_lines(terminal,changed); self.resign(f)
            report=self.verify_fixture(f)
            self.assertIn('independent-episode-terminal',{i['check'] for i in report['issues']})
            fixture_module.write_lines(terminal,originals)
            signalpath=f.attempt()/'signals.jsonl.gz'; signals=fixture_module.lines(signalpath)
            changed=deepcopy(signals); changed[0]['formationDate']=f.target_dates[0]
            fixture_module.write_lines(signalpath,changed); self.resign(f)
            report=self.verify_fixture(f)
            self.assertIn('independent-selected-signal',{i['check'] for i in report['issues']})
            fixture_module.write_lines(signalpath,signals)
            result=f.result(); result['groups'][0]['horizons'][0]['proxies']['nextOpenNet30']['hits']+=1
            with gzip.open(f.attempt()/'result.json.gz','wt',encoding='utf-8') as stream: json.dump(result,stream)
            summary=f.runner.read_json(f.attempt()/'summary.json'); summary['groups']=deepcopy(result['groups'])
            fixture_module.write(f.attempt()/'summary.json',summary); self.resign(f)
            report=self.verify_fixture(f)
            self.assertIn('target-fraction',{i['check'] for i in report['issues']})
            self.assertFalse(report['passed'])

    def test_changed_prerequisite_or_source_inventory_cannot_claim_completed_audit(self):
        with self.completed_fixture() as f:
            path=f.root/f.runner.PREREQUISITES['scopedTests']; original=path.read_bytes()
            receipt=f.runner.read_json(path); receipt['testsRun']+=1; fixture_module.write(path,receipt)
            report=self.verify_fixture(f)
            self.assertFalse(report['passed']); self.assertFalse(report['verificationCompleted'])
            path.write_bytes(original)
            (f.source/'vipdoc/sh/lday/sh600099.day').write_bytes((f.source/'vipdoc/sh/lday/sh600001.day').read_bytes())
            report=self.verify_fixture(f)
            self.assertFalse(report['passed']); self.assertFalse(report['verificationCompleted'])


if __name__ == '__main__':
    unittest.main()
