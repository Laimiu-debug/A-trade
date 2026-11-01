"""Synthetic causal/ranking/clock tests for the independent gap auditor."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_full_gap_independent_test_target', ROOT / 'scripts/verify-full-gap-retention.py')
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)


def fixture(symbol_count=36, sessions=135):
    days = [(date(2022, 1, 1) + timedelta(days=i)).isoformat() for i in range(sessions)]
    securities = []
    for n in range(symbol_count):
        bars = []
        for i, day in enumerate(days):
            close = 10 + (n + 1) * .001 * i
            if 125 <= i <= 129:
                close = bars[-1]['high'] + 1
            bars.append(dict(date=day, open=close, high=close + .1, low=close - .1, close=close,
                             volume=4e6 if 125 <= i <= 129 else 2e6,
                             amount=100e6 + n * 1e6))
        securities.append(dict(symbol=f'sh{600000+n:06d}', bars=bars))
    benchmark = dict(symbol='sh000300', bars=[dict(date=d, open=100, high=101, low=99, close=100,
        volume=1e8, amount=1e10) for d in days])
    return dict(calendar=days, securities=securities, benchmark=benchmark,
                priceBasisId='raw-unadjusted', volumeUnit='shares', amountUnit='CNY')


class PureReferenceTests(unittest.TestCase):
    def test_midrank_ties_and_all_population(self):
        self.assertEqual(V.midrank_reference({'a': 0, 'b': 1, 'c': 1, 'd': 3}),
                         {'a': .125, 'b': .5, 'c': .5, 'd': .875})
        self.assertEqual(V.midrank_reference({'a': 1}), {'a': .5})

    def test_quality_halts_missing_and_envelope(self):
        data = fixture(1, 2); bar = data['securities'][0]['bars'][0]; day = data['calendar'][0]
        for value in (False, 0, 0., '0'):
            self.assertEqual(V.bar_reason(bar, day, value), 'knownSuspension')
        self.assertEqual(V.bar_reason(None, day), 'missingAlignedRawBar')
        broken = {**bar, 'low': bar['high'] + 1}
        self.assertEqual(V.bar_reason(broken, day), 'invalidRawOHLCEnvelope')

    def test_exact_slots_and_prior_only_rank(self):
        data = fixture(); before = deepcopy(data); days = data['calendar']
        idx = V.source_reference(data, days[125], days[-1])
        s = idx['sources'][data['securities'][-1]['symbol']]
        own, relative, reason = V.relative_reference(s, idx['sources']['__benchmark__'], 124)
        self.assertIsNone(reason); self.assertEqual(own, relative)
        data['securities'][-1]['bars'][125]['close'] *= 100
        current = V.source_reference(data, days[125], days[-1])
        self.assertEqual(V.relative_reference(current['sources'][data['securities'][-1]['symbol']],
            current['sources']['__benchmark__'], 124), (own, relative, reason))
        missing = deepcopy(before); del missing['securities'][-1]['bars'][100]
        m = V.source_reference(missing, days[125], days[-1])
        self.assertEqual(V.relative_reference(m['sources'][data['securities'][-1]['symbol']],
            m['sources']['__benchmark__'], 124)[2], 'stock:missingAlignedRawBar')

    def test_basis_explicit_threshold_and_invalid(self):
        data = fixture(1, 125); days = data['calendar']; sec = data['securities'][0]
        idx = V.source_reference(data, days[120], days[-1]); source = idx['sources'][sec['symbol']]
        check = lambda: V.confirmation_reference(source, days, 121, 1, set(days))['referenceChecks'][0]
        self.assertEqual(check()['status'], 'notDeclared')
        sec['precloseByDate'] = {days[121]: sec['bars'][120]['close'] * .5}
        idx = V.source_reference(data, days[120], days[-1]); source = idx['sources'][sec['symbol']]
        self.assertEqual(check()['status'], 'changedReference')
        sec['precloseByDate'][days[121]] = 0
        self.assertEqual(check()['status'], 'invalidReference')

    def test_top5_lock_covers_nonselected_and_never_extends(self):
        data = fixture(); days = data['calendar']; original = deepcopy(data)
        result = V.reference_build(data, start=days[125], end=days[132])
        first = [f for f in result['formations'] if f['formationDate'] == days[125]]
        self.assertGreater(len(first), 5)
        excluded = next(f for f in first if f['selectionStatus'] == 'rankExcluded')
        lock = [f for f in result['formations'] if f['symbol'] == excluded['symbol'] and f['formationDate'] in days[126:128]]
        self.assertEqual(len(lock), 2)
        self.assertTrue(all(f['selectionStatus'] == 'locked' and f['lockOwner'] == excluded['formationId']
                            and f['lockUntilIndex'] == 127 for f in lock))
        renewal = next(f for f in result['formations'] if f['symbol'] == excluded['symbol'] and f['formationDate'] == days[128])
        self.assertTrue(renewal['lockApplied']); self.assertEqual(renewal['lockUntilIndex'], 130)
        self.assertEqual(data, original)

    def test_common_unknown_and_strict_low_equality_without_backfill(self):
        data = fixture(); days = data['calendar']
        first = V.reference_build(data, start=days[125], end=days[129])
        opportunity = first['opportunities'][0]
        sec = next(s for s in data['securities'] if s['symbol'] == opportunity['symbol'])
        sec['bars'][126]['low'] = opportunity['gapBoundary']
        failed = V.reference_build(data, start=days[125], end=days[129])
        observed = next(r for r in failed['opportunities'] if r['formationId'] == opportunity['formationId'])
        self.assertIs(observed['control'], True); self.assertIs(observed['retained'], False)
        self.assertIsNotNone(observed['signals']['control']); self.assertIsNone(observed['signals']['retained'])
        self.assertEqual([f for f in first['formations'] if f['formationDate'] == days[125]],
                         [f for f in failed['formations'] if f['formationDate'] == days[125]])
        sec['bars'][127]['volume'] = 0
        unknown = V.reference_build(data, start=days[125], end=days[129])
        observed = next(r for r in unknown['opportunities'] if r['formationId'] == opportunity['formationId'])
        self.assertEqual(observed['confirmation']['status'], 'unknown')
        self.assertEqual(observed['signals'], {'control': None, 'retained': None})
        self.assertEqual(observed['confirmation']['lows'][0], opportunity['gapBoundary'])

    def test_history_st_and_liquidity_do_not_shrink_rank_population(self):
        data = fixture(); days = data['calendar']; original = V.source_reference(data, days[125], days[-1])
        rows, population, _ = V.qualification_reference(original, 125)
        top = data['securities'][-1]
        top['stByDate'] = {days[124]: True}
        for b in top['bars'][105:125]: b['amount'] = 49_999_999
        altered = V.source_reference(data, days[125], days[-1])
        changed, changed_population, _ = V.qualification_reference(altered, 125)
        self.assertEqual(population, changed_population)
        bad = next(r for r in changed if r['symbol'] == top['symbol'])
        self.assertIn('knownPriorST', bad['failureReasons']); self.assertIn('priorMeanAmountBelowMinimum', bad['failureReasons'])
        top['stByDate'][days[124]] = None
        for b in top['bars'][105:125]: b['amount'] = 50_000_000
        altered = V.source_reference(data, days[125], days[-1])
        exact = next(r for r in V.qualification_reference(altered, 125)[0] if r['symbol'] == top['symbol'])
        self.assertTrue(exact['eligible']); self.assertIsNone(exact['knownStPrior'])
        top['bars'] = top['bars'][6:]  # older-than-rank-window omissions
        sparse = V.source_reference(data, days[125], days[-1])
        sparse_rows, sparse_population, _ = V.qualification_reference(sparse, 125)
        self.assertEqual(population, sparse_population)
        sparse_top = next(r for r in sparse_rows if r['symbol'] == top['symbol'])
        self.assertEqual(sparse_top['priorValidObservations'], 119)
        self.assertIn('insufficientObservedHistory120', sparse_top['failureReasons'])

    def test_gap_and_activity_strict_boundaries(self):
        data = fixture(); days = data['calendar']; top = data['securities'][-1]
        top['bars'][125]['low'] = top['bars'][124]['high']
        idx = V.source_reference(data, days[125], days[-1])
        row = V.qualification_reference(idx, 125)[0][-1]
        self.assertTrue(row['eligible']); self.assertFalse(row['rawSetup'])
        self.assertIn('notFullUpwardGap', row['failureReasons'])
        top['bars'][125]['low'] += .01
        top['bars'][125]['volume'] = 2e6
        idx = V.source_reference(data, days[125], days[-1])
        row = V.qualification_reference(idx, 125)[0][-1]
        self.assertFalse(row['rawSetup']); self.assertIn('formationVolumeNotAbovePriorMean', row['failureReasons'])

    def test_censor_and_future_extension_preserve_formations(self):
        data = fixture(); days = data['calendar']
        prefix = V.reference_build(data, start=days[125], end=days[126])
        full = V.reference_build(data, start=days[125], end=days[132])
        self.assertTrue(all(r['confirmation']['status'] == 'censored' for r in prefix['opportunities']))
        self.assertEqual(prefix['schedules'], {'control': [], 'retained': []})
        # lockUntilDate is a visible-calendar descriptor; absolute lockUntilIndex
        # and formation identity/rank/ownership are fixed before confirmation.
        project = lambda rows: [{k:v for k,v in r.items() if k != 'lockUntilDate'} for r in rows]
        self.assertEqual(project(prefix['formations']), project([r for r in full['formations'] if r['formationDate'] <= days[126]]))
        self.assertTrue(all(s['signalDate'] == days[127] for s in full['schedules']['control'][:5]))

    def test_explicit_rebasing_common_unknown_not_retroactive_rerank(self):
        data = fixture(); days = data['calendar']
        known = V.reference_build(data, start=days[125], end=days[127])
        sec = data['securities'][-1]; sec['precloseByDate'] = {days[126]: sec['bars'][125]['close'] / 2}
        altered = V.reference_build(data, start=days[125], end=days[127])
        self.assertEqual(known['formations'], altered['formations'])
        top = next(r for r in altered['opportunities'] if r['symbol'] == sec['symbol'])
        self.assertEqual(top['confirmation']['status'], 'unknown')
        self.assertIn(days[126] + ':changedReference', top['confirmation']['reasons'])


if __name__ == '__main__':
    unittest.main()
