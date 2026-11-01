"""Synthetic proposal checks only: no frozen market files or event outcomes."""
from copy import deepcopy
import hashlib
import unittest

from service.signal_leadership_replacement import (
    CANDIDATE_ID, CONTROL_SEED, FEATURE_VERSION, Q_DEFINITION,
    evaluate_replacement_day, rank_replacement_signals, select_replacement_controls,
)


DATES = ['2023-12-26', '2023-12-27', '2023-12-28', '2023-12-29',
         '2024-01-02', '2024-01-03', '2024-01-04', '2024-01-05', '2024-01-08']
SYMBOLS = [f'sh600{i:03d}' for i in range(1, 15)]


def fixture(index=5):
    days = DATES[max(0, index-2):index+1]
    rows = []
    for position, day in enumerate(days):
        offset = position-len(days)+3
        cross = []
        for i, symbol in enumerate(SYMBOLS):
            q = ('true' if offset == 0 or i >= 2 else 'false') if i < 4 else (
                'false' if offset == 0 else 'true') if i < 11 else 'false'
            secondary = [.90, .95, .95, .94, .93, .92, .91][i-4] if 4 <= i < 11 else .90
            cross.append({'date': day, 'symbol': symbol, 'featureVersion': FEATURE_VERSION,
                          'qDefinition': Q_DEFINITION, 'qState': q,
                          'criterionValues': {'meanStrengthPercentile20': .95, 'relativeReturn60Percentile': secondary},
                          'baseEligibility': {'eligible': True, 'reason': 'eligible', 'unknown': ['historicalSt']}})
        rows.append(cross)
    market = {'date': DATES[index], 'featureVersion': FEATURE_VERSION, 'qDefinition': Q_DEFINITION,
              'fixedPopulation': len(SYMBOLS), 'price20': {'coverage': .9},
              'matched5': {'available': index >= 5, 'earlierDate': DATES[index-5] if index >= 5 else None,
                           'pairedCoverageFixed': .9 if index >= 5 else None,
                           'matchedBreadthChange5': 0. if index >= 5 else None}}
    return rows, market


def run(rows=None, market=None, index=5, dates=DATES):
    default_rows, default_market = fixture(index)
    return evaluate_replacement_day(SYMBOLS, dates, index,
                                    default_rows if rows is None else rows,
                                    default_market if market is None else market)


class LeadershipReplacementTests(unittest.TestCase):
    def test_fixed_old_cohort_allows_different_failed_subsets_and_cross_year_history(self):
        rows, market = fixture()
        rows[1][0]['qState'], rows[1][1]['qState'] = 'false', 'false'
        for i in range(4):
            rows[2][i]['qState'] = 'false' if i >= 2 else 'true'
        result = run(rows, market)
        cohort = result['daily']['oldCohort']
        self.assertEqual(cohort['members'], SYMBOLS[:4])
        self.assertEqual((cohort['denominator'], cohort['threshold'], cohort['state']), (4, 2, 'true'))
        self.assertEqual([r['counts'] for r in cohort['endpoints']], [{'true': 2, 'false': 2, 'unknown': 0}]*2)
        self.assertEqual(cohort['anchorDate'], '2023-12-29')
        self.assertEqual(len(result['rankedCandidates']), 7)
        self.assertFalse(result['returnsCalculated'])
        self.assertFalse(result['targetAchieved'])

    def test_unknown_failure_bounds_keep_denominator_and_ceil_for_odd_size(self):
        cases = [(4, ['false', 'false', 'unknown', 'unknown'], 'true'),
                 (4, ['true', 'true', 'true', 'unknown'], 'false'),
                 (4, ['false', 'true', 'true', 'unknown'], 'unknown'),
                 (3, ['false', 'unknown', 'true'], 'unknown'),
                 (3, ['false', 'false', 'unknown'], 'true'),
                 (3, ['true', 'true', 'unknown'], 'false')]
        for n, states, expected in cases:
            with self.subTest(n=n, states=states):
                rows, market = fixture()
                for i in range(n, 4):
                    rows[0][i]['qState'] = 'unknown'
                for cross in rows[1:]:
                    for i, state in enumerate(states):
                        cross[i]['qState'] = state
                cohort = run(rows, market)['daily']['oldCohort']
                self.assertEqual((cohort['denominator'], cohort['threshold'], cohort['state']), (n, 2, expected))
                self.assertEqual(cohort['anchorQCounts']['unknown'], 4-n)
                for end in cohort['endpoints']:
                    self.assertEqual(sum(end['counts'].values()), n)
                    self.assertEqual(end['failureFractionLower'], states.count('false')/n)
                    self.assertEqual(end['failureFractionUpper'], (states.count('false')+states.count('unknown'))/n)

    def test_empty_old_cohort_is_false_never_zero_threshold_success(self):
        rows, market = fixture()
        for row in rows[0]:
            row['qState'] = 'false'
        result = run(rows, market)
        self.assertEqual(result['daily']['oldCohort']['state'], 'false')
        self.assertEqual(result['daily']['oldCohort']['members'], [])
        self.assertIn('emptyObservedOldCohort', result['evaluations'][5]['failedPredicates'])
        self.assertEqual(result['signals'], [])

    def test_unknown_precedes_known_failure_without_discarding_predicates(self):
        rows, market = fixture()
        for row in rows[1][:4]:
            row['qState'] = 'true'
        rows[2][0]['qState'], rows[2][1]['qState'] = 'false', 'unknown'
        result = run(rows, market)
        cohort = result['daily']['oldCohort']
        self.assertEqual([r['state'] for r in cohort['endpoints']], ['false', 'unknown'])
        self.assertEqual(cohort['state'], 'unknown')
        self.assertEqual(result['evaluations'][5]['candidateGate'], 'unknown')
        self.assertTrue(result['evaluations'][5]['failedPredicates'])
        self.assertTrue(result['evaluations'][5]['unknownReasons'])

    def test_challenger_requires_exact_known_false_true_true(self):
        for sequence, expected in [(('false', 'true', 'true'), 'true'),
                                   (('unknown', 'true', 'true'), 'unknown'),
                                   (('true', 'true', 'true'), 'false'),
                                   (('false', 'false', 'true'), 'false'),
                                   (('false', 'true', 'unknown'), 'unknown')]:
            with self.subTest(sequence=sequence):
                rows, market = fixture()
                for cross, state in zip(rows, sequence):
                    cross[5]['qState'] = state
                evaluation = run(rows, market)['evaluations'][5]
                self.assertEqual(evaluation['challengerGate'], expected)
                self.assertEqual([r['qState'] for r in evaluation['challengerHistory']], list(sequence))

    def test_market_boundaries_finite_domains_and_unknown_priority(self):
        self.assertEqual(run()['daily']['market']['state'], 'true')
        cases = [('price20', 'coverage', .899999, 'false'),
                 ('matched5', 'pairedCoverageFixed', .899999, 'false'),
                 ('matched5', 'matchedBreadthChange5', -.000001, 'false'),
                 ('matched5', 'matchedBreadthChange5', 100., 'true'),
                 ('matched5', 'matchedBreadthChange5', 100.01, 'unknown'),
                 ('price20', 'coverage', True, 'unknown'),
                 ('matched5', 'matchedBreadthChange5', float('nan'), 'unknown'),
                 ('matched5', 'pairedCoverageFixed', float('inf'), 'unknown'),
                 ('matched5', 'available', 1, 'unknown'),
                 ('matched5', 'available', False, 'unknown')]
        for group, key, value, expected in cases:
            with self.subTest(group=group, key=key, value=value):
                rows, market = fixture()
                market[group][key] = value
                self.assertEqual(run(rows, market)['daily']['market']['state'], expected)
        rows, market = fixture()
        market['price20']['coverage'] = .2
        market['matched5']['matchedBreadthChange5'] = None
        result = run(rows, market)
        self.assertEqual(result['daily']['market']['state'], 'unknown')
        self.assertIn('price20.coverage', result['daily']['market']['failedPredicates'])

    def test_first_five_target_dates_unknown_and_future_append_is_causal(self):
        for index in range(5):
            with self.subTest(index=index):
                result = run(index=index)
                self.assertEqual(result['signals'], [])
                self.assertEqual(result['daily']['candidateGateCounts']['unknown'], len(SYMBOLS))
        original = run()
        extended = run(dates=DATES+['2024-01-09', '2024-01-10'])
        self.assertEqual(original, extended)

    def test_calendar_population_role_identity_and_exact_fifth_slot_are_integrity_errors(self):
        cases = []
        for mutation in ('missing', 'duplicate', 'order', 'future', 'version', 'qbool', 'old-date', 'wrong-pair'):
            rows, market = fixture()
            if mutation == 'missing': rows[1].pop()
            elif mutation == 'duplicate': rows[1][1] = deepcopy(rows[1][0])
            elif mutation == 'order': rows[1][0], rows[1][1] = rows[1][1], rows[1][0]
            elif mutation == 'future': rows[2][0]['date'] = DATES[6]
            elif mutation == 'version': rows[1][0]['featureVersion'] = 'other'
            elif mutation == 'qbool': rows[1][0]['qState'] = True
            elif mutation == 'old-date': rows[0][0]['date'] = DATES[1]
            else: market['matched5']['earlierDate'] = DATES[1]
            cases.append((mutation, rows, market))
        for mutation, rows, market in cases:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                run(rows, market)
        for dates in (list(reversed(DATES)), DATES[:2]+[DATES[1]]+DATES[3:]):
            with self.assertRaises(ValueError): run(dates=dates)
        for index in (True, -1, len(DATES)):
            rows, market = fixture()
            with self.assertRaises(ValueError): evaluate_replacement_day(SYMBOLS, DATES, index, rows, market)

    def test_entry_qualification_is_separate_from_cohort_and_ranks(self):
        rows, market = fixture()
        for cross in rows:
            for row in cross[:4]:
                row['baseEligibility'] = {'eligible': False, 'reason': 'historicalSt', 'unknown': []}
        rows[-1][5]['baseEligibility'] = {'eligible': False, 'reason': 'historicalSt', 'unknown': []}
        rows[-1][6]['baseEligibility'] = {'eligible': False, 'reason': 'missingDailyBar', 'unknown': []}
        rows[-1][7]['baseEligibility'] = None
        result = run(rows, market)
        self.assertEqual(result['daily']['oldCohort']['denominator'], 4)
        self.assertEqual(result['daily']['oldCohort']['state'], 'true')
        self.assertEqual([result['evaluations'][i]['candidateGate'] for i in (5, 6, 7)], ['false', 'unknown', 'unknown'])
        self.assertTrue(any(row['candidateGate'] == 'true' for row in result['evaluations']))
        self.assertEqual(result['evaluations'][8]['baseEligibility']['unknown'], ['historicalSt'])

    def test_lex_top_five_precedes_external_active_exclusion_without_backfill(self):
        result = run()
        ranked = result['rankedCandidates']
        self.assertEqual([r['symbol'] for r in ranked], [SYMBOLS[i] for i in (5, 6, 7, 8, 9, 10, 4)])
        selected = [r for r in ranked if r['selectedTop5']]
        active = {selected[0]['symbol'], selected[2]['symbol']}
        remaining = [r for r in selected if r['symbol'] not in active]
        self.assertEqual([r['rank'] for r in remaining], [2, 4, 5])
        self.assertFalse(any(r['rank'] > 5 for r in remaining))
        self.assertEqual(run()['selection'], result['selection'])
        self.assertFalse(result['selection']['activeExclusionApplied'])
        bad = deepcopy(ranked)
        bad[1]['signalDate'] = DATES[6]
        with self.assertRaises(ValueError): rank_replacement_signals(bad)
        with self.assertRaises(ValueError): rank_replacement_signals(ranked+[ranked[0]])

    def test_controls_use_fixed_hash_pool_allow_failed_or_unknown_own_q_and_shortfall(self):
        rows, market = fixture()
        result = run(rows, market)
        top = [r for r in result['rankedCandidates'] if r['selectedTop5']]
        selected = {r['symbol'] for r in top}
        expected = sorted(set(SYMBOLS)-selected, key=lambda s: (hashlib.sha256(
            f'{CONTROL_SEED}:{DATES[5]}:{CANDIDATE_ID}:{s}'.encode()).hexdigest(), s))[:5]
        controls = select_replacement_controls(DATES[5], SYMBOLS, top, rows[-1])
        self.assertEqual(controls['matching']['selectedSymbols'], expected)
        changed = deepcopy(rows[-1])
        for i, row in enumerate(changed):
            if row['symbol'] not in selected:
                row['qState'] = 'unknown' if i % 2 else 'false'
        self.assertEqual(select_replacement_controls(DATES[5], SYMBOLS, top, changed), controls)
        self.assertEqual([r['pairedStrategySymbol'] for r in controls['controls']], [r['symbol'] for r in top])
        self.assertTrue(all(r['selectedTop5'] is False for r in controls['controls']))
        pool = [s for s in SYMBOLS if s not in selected]
        for row in changed:
            if row['symbol'] in pool[2:]:
                row['baseEligibility'] = {'eligible': False, 'reason': 'lowAmount20', 'unknown': []}
        short = select_replacement_controls(DATES[5], SYMBOLS, top, changed)
        self.assertEqual((short['matching']['requestedCount'], short['matching']['matchedCount'], short['matching']['shortfall']), (5, 2, 3))
        with self.assertRaises(ValueError): select_replacement_controls(DATES[5], SYMBOLS, top[1:], changed)

    def test_outputs_and_inputs_are_copy_isolated_and_ranking_unknown_is_not_passed(self):
        rows, market = fixture()
        before = deepcopy((rows, market))
        result = run(rows, market)
        self.assertEqual((rows, market), before)
        result['evaluations'][5]['baseEligibility']['unknown'].append('mutated')
        result['daily']['oldCohort']['members'].append('mutated')
        result['signals'][0]['symbol'] = 'mutated'
        self.assertEqual((rows, market), before)
        self.assertEqual(run(rows, market), run())
        rows[-1][5]['criterionValues']['meanStrengthPercentile20'] = float('nan')
        evaluation = run(rows, market)['evaluations'][5]
        self.assertEqual(evaluation['candidateGate'], 'unknown')
        self.assertIn('rankingEvidenceUnavailable:meanStrengthPercentile20', evaluation['unknownReasons'])


if __name__ == '__main__':
    unittest.main()
