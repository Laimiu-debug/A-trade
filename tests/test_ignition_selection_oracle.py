"""Adversarial tests for independent full-pool and trailing-feature verification."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest

import numpy as np

from service.research_guiyin_dual_path import build_inputs
from service.research_ignition_selection import build_ignition_inputs


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ignition_selection_oracle',
    ROOT / 'scripts/verify-ignition-selection-inputs.py')
ORACLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORACLE)


def fixture():
    days = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(32)]
    symbols = [f'sh{600001 + i}' for i in range(6)]
    matrix = {k: np.full((6, 32), v, dtype=float) for k, v in
              [('open', 100.), ('close', 100.), ('high', 101.), ('low', 99.),
               ('volume', 1e7), ('amount', 1e8)]}
    matrix.update(calendar=np.asarray(days), symbols=np.asarray(symbols),
                  priceValid=np.ones((6, 32), dtype=bool), explicitSuspended=np.zeros((6, 32), dtype=bool))
    matrix['high'][1, :25] = 103.
    matrix['amount'][0, :25] = 1.5e8
    for field, value in [('close', 108.), ('high', 108.), ('low', 100.)]:
        matrix[field][:3, 25] = value
    matrix['amount'][:3, 25] = [3e8, 2e8, 4e8]
    joins = [{'snapshotId': 'published', 'sectors': [{'sectorCode': 'S1', 'cohortRowIndices': list(range(5))}]}]
    old = {'daily': [{'date': days[25], 'index': 25, 'snapshotId': 'published',
                     'market': {'admission': True, 'turnover5ToPrior20': 1.},
                     'sectors': [{'sectorCode': 'S1', 'sectorName': 'test industry'}]}]}
    formation = build_inputs(matrix, joins, old, [100.] * 32)
    parent = deepcopy(formation['schedules']['complete'])
    assert len(parent) == 1 and parent[0]['symbol'] == symbols[0]
    # A separate inherited B slot is immutable; its formation is not changed by this experiment.
    b = {**parent[0], 'signalId': 'frozen-B', 'symbol': symbols[5], 'originalRank': 2}
    bmeta = {**deepcopy(formation['metadata'][parent[0]['signalId']]), **b,
             'pattern': 'B_corePullback', 'sectorCode': 'S2', 'sectorName': 'other industry'}
    formation['metadata'][b['signalId']] = bmeta
    formation['daily'][0]['selectedSectors'].append('S2')
    formation['sectorMaps']['published'][symbols[5]] = 'S2'
    parent.append(b)
    return formation, matrix, parent, days[25], days[-1]


def spec(name):
    return {'id': name, 'parentPolicy': 'complete', 'entryPolicy': 'original', 'parameters': {},
            'formationPolicy': {'baseline': 'original', 'selection': 'leastBreakoutExtension',
                                'participation': 'strongestAmountParticipation'}[name]}


class FuturePoison:
    """Permit reads only through D, including slices; no implicit array conversion."""
    def __init__(self, array, cutoff):
        self.array, self.cutoff = array, cutoff

    def __getitem__(self, key):
        _, column = key
        touched = range(*column.indices(self.array.shape[1])) if isinstance(column, slice) else [column]
        if any(i > self.cutoff for i in touched):
            raise AssertionError('Future OHLCV read in formation')
        return self.array[key]


class IgnitionOracleTests(unittest.TestCase):
    def setUp(self):
        self.formation, self.matrix, self.parents, self.start, self.end = fixture()
        self.case = spec('selection')
        self.output = self.build()

    def build(self):
        return build_ignition_inputs(self.formation, self.matrix, self.parents, self.case, self.start, self.end)

    def audit(self, dataset=None):
        return ORACLE.audit_ignition_inputs(self.formation, self.matrix, self.parents,
            self.case, *self.output, self.start, self.end, execution_dataset=dataset)

    def test_all_three_rules_and_unchanged_B_slot(self):
        expected = {'baseline': 'sh600001', 'selection': 'sh600002', 'participation': 'sh600003'}
        for name, symbol in expected.items():
            with self.subTest(case=name):
                self.case = spec(name)
                self.output = self.build()
                report = self.audit()
                self.assertEqual(self.output[1][0]['symbol'], symbol)
                self.assertEqual(report['eligibleFullSectorCandidatesChecked'], 3)
                self.assertEqual(self.output[1][1], self.parents[1])
                self.assertEqual(report['slotsChecked'], 2)

    def test_detects_coherent_candidate_omission_from_frozen_pool(self):
        self.formation['allCandidates'] = [x for x in self.formation['allCandidates'] if x['symbol'] != 'sh600002']
        self.output = self.build()
        with self.assertRaisesRegex(ValueError, 'full raw sector universe'):
            self.audit()

    def test_detects_raw_new_eligible_stock_absent_from_pool(self):
        for field, value in [('close', 108.), ('high', 108.), ('low', 100.), ('amount', 2e8)]:
            self.matrix[field][3, 25] = value
        with self.assertRaisesRegex(ValueError, 'full raw sector universe'):
            self.audit()

    def test_detects_stale_or_tampered_ntr_prior_amount_and_rank(self):
        original = deepcopy(self.formation)
        for field, change in [('meanNtr20', .002), ('priorAmount20', 1e6), ('priorIndustryRank20', .1)]:
            with self.subTest(field=field):
                self.formation = deepcopy(original)
                row = next(r for r in self.formation['allCandidates'] if r['symbol'] == 'sh600002')
                row[field] += change
                self.output = self.build()
                with self.assertRaisesRegex(ValueError, 'Raw candidate'):
                    self.audit()

    def test_detects_proof_feature_identity_rank_and_risk_changes(self):
        original = deepcopy(self.output)
        for change in ('feature', 'identity', 'rank', 'risk', 'B', 'slot'):
            with self.subTest(change=change):
                self.output = deepcopy(original)
                system, schedule, proof = self.output
                if change == 'feature':
                    proof['rows'][0]['candidateFeatures'][0]['breakoutExtensionNtr'] += .01
                elif change == 'identity':
                    schedule[0]['symbol'] = 'sh600003'
                elif change == 'rank':
                    schedule[0]['originalRank'] = 3
                elif change == 'risk':
                    system['metadata'][schedule[0]['signalId']]['failurePrice'] -= 1
                elif change == 'B':
                    system['metadata']['frozen-B']['signalClose'] += 1
                else:
                    proof['rows'].pop()
                with self.assertRaises(ValueError):
                    self.audit()

    def test_detects_slot_dropped_from_schedule_and_duplicate_parent(self):
        self.output[1].pop()
        with self.assertRaisesRegex(ValueError, 'Slot dropped'):
            self.audit()
        self.output = self.build()
        self.parents.append(deepcopy(self.parents[0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate parent'):
            self.audit()

    def test_future_values_are_never_read_by_builder_or_oracle(self):
        baseline = deepcopy(self.output)
        for field in ('open', 'high', 'low', 'close', 'volume', 'amount', 'priceValid', 'explicitSuspended'):
            self.matrix[field] = FuturePoison(self.matrix[field], 25)
        self.output = self.build()
        self.assertEqual(self.output, baseline)
        self.assertTrue(self.audit()['passed'])

    def test_requires_selected_stock_projection_and_prefix_prices(self):
        dataset = {'calendar': self.matrix['calendar'].tolist(),
                   'securities': [{'symbol': row['symbol'], 'bars': []} for row in self.output[1]]}
        self.assertTrue(self.audit(dataset)['passed'])
        dataset['securities'].pop()
        with self.assertRaisesRegex(ValueError, 'projection omitted'):
            self.audit(dataset)

    def test_rejects_context_after_period_end(self):
        self.output[0]['daily'].append({**deepcopy(self.formation['daily'][0]), 'date': '2025-01-01'})
        with self.assertRaisesRegex(ValueError, 'period prefix'):
            self.audit()

    def test_fill_clock_rejects_same_day_delay_duplicate_and_retry(self):
        system, schedule, _ = self.output
        row = schedule[0]
        trade = {'side': 'buy', 'signalId': row['signalId'], 'signalDate': self.start,
                 'symbol': row['symbol'], 'date': str(self.matrix['calendar'][26])}
        data = {'calendar': self.matrix['calendar'].tolist()}
        result = {'trades': [trade]}
        self.assertEqual(ORACLE.audit_fill_clock(result, data, system)['buyFillClocksChecked'], 1)
        for offset in (25, 27):
            bad = deepcopy(result)
            bad['trades'][0]['date'] = str(self.matrix['calendar'][offset])
            with self.assertRaises(ValueError):
                ORACLE.audit_fill_clock(bad, data, system)
        with self.assertRaises(ValueError):
            ORACLE.audit_fill_clock({'trades': [trade, trade]}, data, system)
        result['research'] = {'opportunities': [{'signalDate': self.start, 'attemptDate': str(self.matrix['calendar'][27])}]}
        with self.assertRaisesRegex(ValueError, 'retried'):
            ORACLE.audit_fill_clock(result, data, system)


if __name__ == '__main__':
    unittest.main()
