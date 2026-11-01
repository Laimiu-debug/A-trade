"""Selection opportunity coverage, causal boundaries and account integration."""
from copy import deepcopy
import unittest
import numpy as np

from service.research_ignition_selection import build_ignition_inputs, make_account, validate_case
from service.research_account_fees import LegacyFeePolicy
from service.research_account_outcomes import summarize_account
from tests.test_research_market_sector_account import setup
from tests.test_research_volatility_trail import configuration


def rule(ident):
    return {'id': ident, 'parentPolicy': 'complete', 'formationPolicy': {
        'baseline': 'original', 'selection': 'leastBreakoutExtension',
        'participation': 'strongestAmountParticipation'}[ident],
        'entryPolicy': 'original', 'parameters': {}}


def fixture():
    data, signals, system, _ = setup(((25, 0), (25, 2)))
    signals[1]['originalRank'] = 2
    system['metadata'][signals[1]['signalId']]['originalRank'] = 2
    days, names = data['calendar'], [s['symbol'] for s in data['securities']]
    for security in data['securities']:
        security['stByDate'] = {day: False for day in days}
    for day in system['daily']:
        day['market']['turnover5ToPrior20'] = 1.1
    system['sectorMaps']['synthetic'][names[1]] = 'C30'
    system['version'] = 'synthetic'
    for signal in signals:
        meta = system['metadata'][signal['signalId']]
        meta.update(pattern='A_ignition' if signal['symbol'] == names[0] else 'B_corePullback',
                    contextCutoff=days[25], sourceIndex=25, priorIndustryRank20=.8,
                    priorAmount20=5e7, priorReturn20=.08, closePosition=.9, sectorScore=.8)
    original = system['metadata'][signals[0]['signalId']]
    alternative = {**deepcopy(original), 'symbol': names[1], 'priorIndustryRank20': .5}
    for candidate in [original, alternative]:
        candidate = deepcopy(candidate)
        candidate.pop('signalId', None)
        system.setdefault('allCandidates', []).append(candidate)
    matrix = {k: np.full((3, len(days)), v) for k, v in
              [('open', 10.), ('close', 10.), ('high', 9.9), ('low', 9.8),
               ('amount', 1e8), ('volume', 1e7)]}
    matrix['high'][1, :25] = 9.98
    matrix['high'][:, 25:] = 10.05
    matrix['amount'][0, 25] = 3e8
    matrix['amount'][1, 25] = 2e8
    matrix.update(calendar=np.array(days), symbols=np.array(names),
                  priceValid=np.ones((3, len(days)), dtype=bool))
    return data, signals, system, matrix


def build(f, ident):
    data, signals, system, matrix = f
    return build_ignition_inputs(system, matrix, signals, rule(ident),
                                 data['calendar'][25], data['calendar'][-1])


class IgnitionSelectionTests(unittest.TestCase):
    def test_baseline_identity_and_b_metadata_unchanged(self):
        f = fixture()
        result, signals, proof = build(f, 'baseline')
        self.assertEqual(signals, f[1])
        self.assertEqual(result['metadata'], f[2]['metadata'])
        self.assertEqual(proof['droppedSlots'], 0)
        changed, selected, _ = build(f, 'selection')
        self.assertEqual(selected[1], f[1][1])
        self.assertEqual(changed['metadata'][selected[1]['signalId']], f[2]['metadata'][f[1][1]['signalId']])

    def test_reranks_full_pool_without_dropping_slots_or_changing_rank(self):
        f = fixture()
        result, signals, proof = build(f, 'selection')
        self.assertEqual(len(signals), len(f[1]))
        self.assertEqual(signals[0]['symbol'], f[0]['securities'][1]['symbol'])
        self.assertEqual(signals[0]['originalRank'], f[1][0]['originalRank'])
        self.assertEqual(proof['changedSlots'], 1)
        self.assertEqual(proof['rows'][0]['candidateCount'], 2)
        self.assertEqual(result['metadata'][signals[0]['signalId']]['originSignalId'], f[1][0]['signalId'])

    def test_participation_is_distinct_rule_not_amount_size_or_breakout_filter(self):
        f = fixture()
        f[2]['allCandidates'][1]['priorAmount20'] = 2e7
        _, signals, proof = build(f, 'participation')
        self.assertEqual(signals[0]['symbol'], f[0]['securities'][1]['symbol'])
        # Absolute D amount is smaller, but relative participation is larger.
        self.assertEqual(proof['rows'][0]['candidateFeatures'][1]['amountParticipation'], 10)
        self.assertEqual(proof['slots'], 2)

    def test_future_prices_amount_and_volume_cannot_change_selection(self):
        for ident in ('baseline', 'selection', 'participation'):
            f = fixture()
            expected = build(f, ident)
            for key in ('open', 'high', 'low', 'close', 'amount', 'volume'):
                f[3][key][:, 26:] = float('nan')
            self.assertEqual(build(f, ident), expected)

    def test_full_and_truncated_price_prefix_select_identically(self):
        f = fixture()
        expected = build(f, 'participation')
        cutoff = 30
        shorter = {k: v[:, :cutoff] if getattr(v, 'ndim', 0) == 2 else v[:cutoff] if k == 'calendar' else v
                   for k, v in f[3].items()}
        actual = build_ignition_inputs(f[2], shorter, f[1], rule('participation'), f[0]['calendar'][25], f[0]['calendar'][cutoff-1])
        self.assertEqual(actual[1:], expected[1:])
        self.assertTrue(all(d['date'] <= f[0]['calendar'][cutoff-1] for d in actual[0]['daily']))

    def test_missing_original_or_duplicate_candidates_rejected(self):
        f = fixture()
        f[2]['allCandidates'].pop(0)
        with self.assertRaises(ValueError):
            build(f, 'selection')
        f = fixture()
        f[2]['allCandidates'].append(deepcopy(f[2]['allCandidates'][0]))
        with self.assertRaises(ValueError):
            build(f, 'selection')

    def test_membership_and_decision_cutoff_cannot_be_injected(self):
        for field, value in [('snapshotId', 'future'), ('contextCutoff', '2026-10-16'), ('sourceIndex', 26)]:
            f = fixture()
            f[2]['allCandidates'][1][field] = value
            with self.assertRaises(ValueError):
                build(f, 'selection')

    def test_deterministic_ties_follow_original_priority(self):
        f = fixture()
        f[3]['high'][1, :25] = 9.9
        _, first, _ = build(f, 'selection')
        f[2]['allCandidates'].reverse()
        _, second, _ = build(f, 'selection')
        self.assertEqual(first, second)
        self.assertEqual(first[0]['symbol'], f[1][0]['symbol'])

    def test_nonregistered_parameter_or_entry_filter_rejected(self):
        for change in ({'parameters': {'threshold': 3}}, {'entryPolicy': 'signalCloseCap'}, {'parentPolicy': 'Aonly'}):
            with self.assertRaises(ValueError):
                validate_case({**rule('selection'), **change})

    def test_execution_is_next_open_and_preserves_t1_and_cash(self):
        f = fixture()
        system, signals, _ = build(f, 'selection')
        engine = make_account(f[0], system, signals, configuration(f[0], maxHoldingDays=10),
                              rule('selection'), fee_policy=LegacyFeePolicy())
        engine.step(20000)
        result = engine.result()
        buys = [t for t in result['trades'] if t['side'] == 'buy']
        self.assertEqual(len(buys), 2)
        self.assertTrue(all(t['date'] == f[0]['calendar'][26] for t in buys))
        self.assertTrue(all(t['date'] > f[0]['calendar'][26] for t in result['trades'] if t['side'] == 'sell'))
        self.assertFalse(summarize_account(result, engine.checkpoint())['quality']['integrityIssues'])

    def test_rule_mutation_fails_before_continuing_account(self):
        f = fixture()
        system, signals, _ = build(f, 'participation')
        engine = make_account(f[0], system, signals, configuration(f[0]),
                              rule('participation'), fee_policy=LegacyFeePolicy())
        engine.selection_case['formationPolicy'] = 'original'
        with self.assertRaises(ValueError):
            engine.step(1)


if __name__ == '__main__':
    unittest.main()
