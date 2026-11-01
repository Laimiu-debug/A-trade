"""Causal-prefix and shared-support checks for the independent price oracle."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_pullback_reference_tests', ROOT / 'scripts/verify-leader-pullback-retention.py')
V = importlib.util.module_from_spec(spec); spec.loader.exec_module(V)


def fixture(symbols=24, sessions=40):
    calendar = [(date(2022, 1, 1) + timedelta(days=i)).isoformat() for i in range(sessions)]
    def bars(amount):
        return [dict(date=d, open=10., high=10.1, low=9.9, close=10., volume=100., amount=amount) for d in calendar]
    return dict(calendar=calendar, priceBasisId='raw-unadjusted', volumeUnit='shares', amountUnit='CNY',
        securities=[dict(symbol=f'sh{600000+i:06d}', bars=bars(100. if i == 0 else 50.)) for i in range(symbols)],
        benchmark=dict(symbol='sh000300', bars=bars(100.)))


def price(data, member, at, value):
    data['securities'][member]['bars'][at].update(open=value, high=value + .1, low=value - .1, close=value)


def parents(data):
    calendar = data['calendar']; events = []
    for at in (30, 37):
        if at >= len(calendar): continue
        day, frozen = calendar[at], calendar[at - 10]
        group = dict(anchorSymbol='sh600000', peerFormationDate=frozen, kind='formed', selectedCount=20, requiredCount=20,
            members=[dict(symbol=f'sh{600000+i:06d}', frozenMemberRank=i) for i in range(1, 21)])
        events.append(dict(eventId=f'leaderLagFollowersV1:{day}:sh600000', formationDate=day, peerFormationDate=frozen,
            anchorSymbol='sh600000', originalLeaderRank=1, groupSummary=group, groupSha256=V.digest(group)))
    old = V.H.reference_build(data, events, start=calendar[30], end=calendar[-1])
    return old['formations'], old['opportunities']


def parent_fixture():
    data = fixture()
    price(data, 0, 31, 9.8)
    price(data, 1, 33, 11.)
    price(data, 2, 31, 9.5)
    price(data, 2, 34, 11.)
    price(data, 3, 35, 11.)
    forms, opportunities = parents(data)
    return data, forms, opportunities


def build(data, forms=None, opportunities=None, end=None):
    if forms is None: forms, opportunities = parents(data)
    return V.reference_build(data, forms, opportunities, start=data['calendar'][30], end=end or data['calendar'][-1])


def indexed(data):
    return V.H.G.source_reference(data, data['calendar'][30], data['calendar'][-1])['sources']


class PullbackReferenceTests(unittest.TestCase):
    def test_first_strict_decline_stops_before_later_missing(self):
        data = fixture(); calendar = data['calendar']
        price(data, 0, 31, 9.999999999)
        data['securities'][0]['bars'][32]['volume'] = None
        result = V.first_down_reference(indexed(data)['sh600000'], calendar, 30, 34)
        self.assertEqual(result['status'], 'priorDown')
        self.assertEqual(result['firstDownDate'], calendar[31])
        self.assertEqual(result['windowDates'], calendar[30:32])
        self.assertEqual(len(result['evaluatedPairs']), 1)

    def test_unknown_prefix_is_terminal_not_later_decline(self):
        data = fixture(); calendar = data['calendar']
        data['securities'][0]['bars'][31]['volume'] = 0.
        price(data, 0, 32, 9.)
        result = V.first_down_reference(indexed(data)['sh600000'], calendar, 30, 35)
        self.assertEqual(result['status'], 'firstDownUnknown')
        self.assertIsNone(result['firstDownDate'])
        self.assertEqual(result['unknownDate'], calendar[31])
        self.assertEqual(result['windowDates'], calendar[30:32])

    def test_flat_and_same_day_are_known_nonadmissions(self):
        data = fixture(); calendar = data['calendar']
        for at, expected in [(31, 'noDownThroughT'), (34, 'noDownThroughT')]:
            self.assertEqual(V.first_down_reference(indexed(data)['sh600000'], calendar, 30, at)['status'], expected)
        price(data, 0, 34, 9.)
        down = V.first_down_reference(indexed(data)['sh600000'], calendar, 30, 34)
        self.assertEqual(down['status'], 'sameDayDown')
        retained = V.retention_reference(indexed(data)['sh600001'], calendar, down, 34)
        self.assertEqual(retained['status'], 'notApplicable')
        self.assertEqual(V.classify_reference(down, retained), 'sameDayDown')

    def test_retention_equality_and_one_observation(self):
        data = fixture(); calendar = data['calendar']; price(data, 0, 31, 9.)
        sources = indexed(data)
        down = V.first_down_reference(sources['sh600000'], calendar, 30, 32)
        result = V.retention_reference(sources['sh600001'], calendar, down, 32)
        self.assertTrue(result['retained'])
        self.assertEqual(result['referenceDate'], calendar[30])
        self.assertEqual(result['windowDates'], calendar[30:32])
        self.assertEqual(len(result['observations']), 1)

    def test_breach_then_recovery_and_trigger_cannot_repair(self):
        data, forms, opportunities = parent_fixture()
        row = next(r for r in build(data, forms, opportunities)['opportunities'] if r['symbol'] == 'sh600002')
        self.assertTrue(row['control']); self.assertFalse(row['retained'])
        self.assertEqual(row['retention']['firstBreachDate'], data['calendar'][31])
        self.assertNotIn(row['decisionDate'], row['retention']['windowDates'])
        self.assertGreater(row['priceEvidence']['close'], row['retention']['referenceClose'])

    def test_unknown_and_known_breach_both_preserved_in_pure_path(self):
        data = fixture(); calendar = data['calendar']; price(data, 0, 31, 9.)
        price(data, 1, 31, 9.5); data['securities'][1]['bars'][32]['volume'] = None
        sources = indexed(data); down = V.first_down_reference(sources['sh600000'], calendar, 30, 34)
        path = V.retention_reference(sources['sh600001'], calendar, down, 34)
        self.assertEqual(path['status'], 'unknown'); self.assertIsNone(path['retained'])
        self.assertEqual(path['breachDates'], [calendar[31]])
        self.assertIn(calendar[32], path['unknownReasons'][0])
        self.assertEqual(V.classify_reference(down, path), 'memberPathUnknown')

    def test_unknown_reference_never_fabricates_breach(self):
        data = fixture(); calendar = data['calendar']; price(data, 0, 31, 9.)
        data['securities'][1]['bars'][30]['volume'] = None; price(data, 1, 31, 5.)
        sources = indexed(data); down = V.first_down_reference(sources['sh600000'], calendar, 30, 33)
        path = V.retention_reference(sources['sh600001'], calendar, down, 33)
        self.assertEqual(path['referenceClose'], 10.)
        self.assertEqual(path['breachDates'], [])
        self.assertTrue(all(r['breached'] is None for r in path['observations']))

    def test_different_decision_prefixes_do_not_share_final_p(self):
        data = fixture(); calendar = data['calendar']; price(data, 0, 33, 9.)
        sources = indexed(data)
        statuses = [V.first_down_reference(sources['sh600000'], calendar, 30, t)['status'] for t in (32, 33, 34)]
        self.assertEqual(statuses, ['noDownThroughT', 'sameDayDown', 'priorDown'])

    def test_parent_amount_unknown_never_blocks_price_population(self):
        data, _, _ = parent_fixture()
        data['securities'][1]['bars'][33]['amount'] = None
        forms, opportunities = parents(data)
        row = next(r for r in build(data, forms, opportunities)['opportunities'] if r['symbol'] == 'sh600001')
        self.assertEqual(row['parentAmountStatus'], 'unknown')
        self.assertTrue(row['control']); self.assertTrue(row['retained'])

    def test_original_rank_gaps_identity_and_endday_signal_preserved(self):
        data, forms, opportunities = parent_fixture()
        opportunities[0]['originalRank'] = 4
        result = build(data, forms, opportunities, end=data['calendar'][33])
        self.assertEqual(result['schedules']['control'][0]['originalRank'], 4)
        self.assertEqual(result['schedules']['control'][0]['signalDate'], data['calendar'][33])
        self.assertEqual(result['calendar'][-1], data['calendar'][33])
        self.assertEqual(result['opportunities'][0]['parentOpportunityId'], opportunities[0]['signalId'])

    def test_price_contradiction_and_duplicate_parent_rejected(self):
        data, forms, opportunities = parent_fixture()
        bad = deepcopy(opportunities); bad[0]['priceEvidence']['prior5High'] += .01
        with self.assertRaises(AssertionError): build(data, forms, bad)
        with self.assertRaises(AssertionError): build(data, forms, [opportunities[0], opportunities[0]])

    def test_future_data_and_amount_changes_do_not_change_earlier_prefix(self):
        data, forms, opportunities = parent_fixture()
        before = build(data, forms, opportunities, end=data['calendar'][33])
        later = deepcopy(data)
        for security in later['securities']:
            for bar in security['bars'][34:]: bar.update(close=None, volume=None, amount=None)
        after = build(later, forms, opportunities, end=data['calendar'][33])
        self.assertEqual(before, after)

    def test_inherited_other_formations_and_zero_opportunity_population(self):
        data, forms, _ = parent_fixture(); forms[-1]['status'] = 'locked'
        result = build(data, forms, [])
        self.assertEqual(result['formations'], forms)
        self.assertEqual(result['counts']['parentFormations'], 2)
        self.assertEqual(result['counts']['inheritedOther'], 1)
        self.assertEqual(result['counts']['opportunities'], 0)
        self.assertEqual(result['counts']['commonStatusCounts'], {s: 0 for s in V.COMMON_STATES})

    def test_no_mutation_and_full_known_conservation(self):
        data, forms, opportunities = parent_fixture(); saved = deepcopy((data, forms, opportunities))
        result = build(data, forms, opportunities)
        self.assertEqual((data, forms, opportunities), saved)
        c = result['counts']
        self.assertEqual(c['originalTop5'], 3)
        self.assertEqual(c['commonStatusCounts']['commonKnown'], c['retainedKnown'] + c['lostKnown'])
        self.assertEqual(c['signals'], {'control': 3, 'retained': 2})
        for p in V.POLICIES: self.assertEqual(c['signals'][p] + c['notScheduled'][p], c['originalTop5'])


if __name__ == '__main__': unittest.main()
