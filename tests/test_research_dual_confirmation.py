"""Synthetic confirmation timing and original-account integration only."""
from copy import deepcopy
import unittest

from service.research_dual_confirmation import build_confirmation_inputs
from service.research_guiyin_dual_path import DualPathAccount
from service.research_account_fees import LegacyFeePolicy
from tests.test_research_market_sector_account import setup
from tests.test_research_volatility_trail import configuration


def fixture(signals_at=((25, 0),), count=3):
    data, signals, system, _ = setup(signals_at, count=count)
    for security in data['securities']:
        security['stByDate'] = {day: False for day in data['calendar']}
        for bar in security['bars']:
            bar.update(high=10.2, close=10.1)
    for i, day in enumerate(system['daily']):
        day['market']['turnover5ToPrior20'] = 1.1
        day['index'] = i
    for signal in signals:
        meta = system['metadata'][signal['signalId']]
        meta.update(pattern='A_ignition', contextCutoff=signal['signalDate'], sourceIndex=25)
    return data, signals, system


def build(case, policy='dualConfirm', start=None, end=None):
    data, signals, system = case
    return build_confirmation_inputs(data, system, signals, policy,
                                     start or data['calendar'][25], end or data['calendar'][-1])


class DualConfirmationTests(unittest.TestCase):
    def test_shifts_only_entry_date_and_preserves_original_anchors_without_mutation(self):
        case = fixture()
        before = deepcopy(case)
        system, signals, proof = build(case)
        parent = case[1][0]
        new = signals[0]
        self.assertEqual(new['signalDate'], case[0]['calendar'][26])
        self.assertEqual(proof['rows'][0]['earliestEntryDate'], case[0]['calendar'][27])
        self.assertEqual(new['signalId'], 'dualConfirm:' + parent['signalId'])
        meta = system['metadata'][new['signalId']]
        for key in ('signalClose', 'failurePrice', 'meanNtr20', 'originalRank'):
            self.assertEqual(meta[key], case[2]['metadata'][parent['signalId']][key])
        self.assertEqual(meta['originSignalId'], parent['signalId'])
        self.assertEqual(meta['originSignalDate'], parent['signalDate'])
        self.assertEqual(meta['contextCutoff'], new['signalDate'])
        self.assertEqual(meta['sourceIndex'], 26)
        self.assertEqual(meta['originSourceIndex'], 25)
        self.assertEqual(case, before)

    def test_future_price_perturbation_does_not_change_any_formation_output(self):
        case = fixture()
        expected = build(case)
        for security in case[0]['securities']:
            for bar in security['bars'][27:]:
                bar.update(open=-100, high=float('inf'), low=0, close=float('nan'), volume=0, amount=0)
        self.assertEqual(build(case), expected)

    def test_future_ohlcv_fields_are_never_read(self):
        class UnreadableFuture(dict):
            def get(self, key, default=None):
                if key != 'date':
                    raise AssertionError('Future price field was read')
                return super().get(key, default)
            def __getitem__(self, key):
                if key != 'date':
                    raise AssertionError('Future price field was read')
                return super().__getitem__(key)
        case = fixture()
        for security in case[0]['securities']:
            security['bars'][27:] = [UnreadableFuture(bar) for bar in security['bars'][27:]]
        self.assertEqual(build(case)[2]['counts']['passed'], 1)

    def test_missing_confirmation_session_is_unknown_never_compressed(self):
        case = fixture()
        case[0]['securities'][0]['bars'].pop(26)
        _, schedule, proof = build(case)
        self.assertFalse(schedule)
        self.assertEqual(proof['rows'][0]['confirmationDate'], case[0]['calendar'][26])
        self.assertEqual(proof['rows'][0]['reason'], 'missingConfirmationBar')
        self.assertEqual(proof['counts']['unknown'], 1)

    def test_invalid_confirmation_evidence_remains_unknown(self):
        for field, value in (('volume', 0), ('amount', None), ('close', True),
                             ('high', float('inf')), ('low', 12), ('knownSuspended', True)):
            with self.subTest(field=field):
                case = fixture()
                case[0]['securities'][0]['bars'][26][field] = value
                _, schedule, proof = build(case)
                self.assertFalse(schedule)
                self.assertEqual(proof['counts']['unknown'], 1)
        case = fixture()
        case[0]['securities'][0]['tradeStatusByDate'] = {case[0]['calendar'][26]: False}
        self.assertEqual(build(case)[2]['rows'][0]['reason'], 'knownSuspension')

    def test_equality_to_original_close_is_allowed_but_flat_confirmation_is_not(self):
        case = fixture()
        bar = case[0]['securities'][0]['bars'][26]
        bar.update(open=9.99, close=10.)
        self.assertEqual(build(case)[2]['counts']['passed'], 1)
        bar.update(open=10.)
        self.assertEqual(build(case)[2]['rows'][0]['reason'], 'confirmationNotPositive')
        bar.update(open=9.97, close=9.99)
        self.assertEqual(build(case)[2]['rows'][0]['reason'], 'confirmationBelowSignalClose')

    def test_market_sector_and_membership_changes_reject(self):
        for reason in ('marketNotAdmitted', 'originalSectorNotSelected', 'sectorMembershipChanged'):
            with self.subTest(reason=reason):
                case = fixture()
                context = case[2]['daily'][26]
                if reason == 'marketNotAdmitted':
                    context['market']['admission'] = False
                elif reason == 'originalSectorNotSelected':
                    context['selectedSectors'] = []
                else:
                    context['snapshotId'] = 'changed'
                    case[2]['sectorMaps']['changed'] = {'sh600001': 'C99'}
                _, signals, proof = build(case)
                self.assertFalse(signals)
                self.assertEqual(proof['rows'][0]['reason'], reason)

    def test_new_snapshot_with_unchanged_membership_updates_provenance(self):
        case = fixture()
        case[2]['daily'][26]['snapshotId'] = 'nextPublication'
        case[2]['sectorMaps']['nextPublication'] = deepcopy(case[2]['sectorMaps']['synthetic'])
        system, signals, _ = build(case)
        meta = system['metadata'][signals[0]['signalId']]
        self.assertEqual(meta['snapshotId'], 'nextPublication')
        self.assertEqual(meta['originSnapshotId'], 'synthetic')
        DualPathAccount(case[0], signals, configuration(case[0]), system_inputs=system,
                        policy='complete', fee_policy=LegacyFeePolicy())

    def test_original_ranks_and_parent_membership_are_not_backfilled(self):
        case = fixture(((25, 0), (25, 1), (25, 2)))
        for signal, rank in zip(case[1], (1, 2, 4)):
            signal['originalRank'] = rank
            case[2]['metadata'][signal['signalId']]['originalRank'] = rank
        case[0]['securities'][0]['bars'][26]['close'] = 10.
        _, signals, proof = build(case)
        self.assertEqual([s['originalRank'] for s in signals], [2, 4])
        self.assertEqual(proof['parentScheduleCount'], 3)
        self.assertEqual(proof['counts']['rejected'], 1)

    def test_both_origin_and_confirmation_must_be_in_interval(self):
        case = fixture()
        days = case[0]['calendar']
        self.assertEqual(build(case, start=days[26])[2]['counts']['outsideInterval'], 1)
        self.assertEqual(build(case, end=days[25])[2]['counts']['censored'], 1)
        # D+2 outside interval does not retroactively suppress a valid D+1
        # decision; the unchanged account records the unavailable execution.
        system, signals, proof = build(case, end=days[26])
        self.assertEqual(len(signals), 1)
        engine = DualPathAccount(case[0], signals, configuration(case[0], endDate=days[26]),
            system_inputs=system, policy='complete', fee_policy=LegacyFeePolicy())
        engine.step(20000)
        self.assertFalse(engine.result()['trades'])

    def test_complete_and_bonly_parent_streams_integrate_with_complete_account(self):
        for family, policy_id in (('A_ignition', 'dualConfirm'), ('B_corePullback', 'bConfirm')):
            with self.subTest(family=family):
                case = fixture()
                case[2]['metadata'][case[1][0]['signalId']]['pattern'] = family
                system, signals, _ = build(case, policy=policy_id)
                engine = DualPathAccount(case[0], signals, configuration(case[0], maxHoldingDays=10),
                    system_inputs=system, policy='complete', fee_policy=LegacyFeePolicy())
                engine.step(20000)
                buys = [x for x in engine.result()['trades'] if x['side'] == 'buy']
                self.assertEqual(len(buys), 1)
                self.assertEqual(buys[0]['date'], case[0]['calendar'][27])
                self.assertGreater(buys[0]['date'], signals[0]['signalDate'])

    def test_original_opening_cap_remains_and_future_open_only_affects_execution(self):
        case = fixture()
        expected = build(case)
        case[0]['securities'][0]['bars'][27].update(open=10.3, high=10.3)
        self.assertEqual(build(case), expected)
        system, signals, _ = build(case)
        engine = DualPathAccount(case[0], signals, configuration(case[0]), system_inputs=system,
                                policy='complete', fee_policy=LegacyFeePolicy())
        engine.step(20000)
        result = engine.result()
        self.assertFalse(result['trades'])
        self.assertEqual(result['research']['opportunities'][0]['reason'], 'openingTooExtended')


if __name__ == '__main__':
    unittest.main()
