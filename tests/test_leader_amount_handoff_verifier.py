"""Synthetic tests for the independent handoff oracle, no production imports."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('_handoff_audit_under_test', ROOT / 'scripts/verify-leader-amount-handoff.py')
V = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(V)


def fixture(symbol_count=22, sessions=40):
    calendar = [(date(2022, 1, 1) + timedelta(days=i)).isoformat() for i in range(sessions)]
    symbols = ['sh' + str(600000 + i) for i in range(symbol_count)]
    def bars(amount):
        return [dict(date=day, open=10., high=10.1, low=9.9, close=10., volume=100., amount=amount) for day in calendar]
    securities = [dict(symbol=symbol, bars=bars(100. if i == 0 else 50.)) for i, symbol in enumerate(symbols)]
    data = dict(priceBasisId='raw-unadjusted', volumeUnit='shares', amountUnit='CNY', calendar=calendar,
                securities=securities, benchmark=dict(symbol='sh000300', bars=bars(100.)))
    return data


def indexed(data, end=None):
    return V.G.source_reference(data, data['calendar'][30], end or data['calendar'][-1])


def breakout(data, symbol_index, at, close=11.):
    data['securities'][symbol_index]['bars'][at].update(open=close, high=close + .1, low=close - .1, close=close)


def event(data, at=30, anchor_index=0, rank=1, kind='formed'):
    calendar = data['calendar']; symbol = data['securities'][anchor_index]['symbol']
    peers = [s['symbol'] for s in data['securities'] if s['symbol'] != symbol][:20]
    if kind != 'formed': peers = peers[:3]
    group = dict(anchorSymbol=symbol, peerFormationDate=calendar[at - 10], kind=kind,
        requiredCount=20, selectedCount=len(peers), members=[dict(symbol=s, frozenMemberRank=i + 1) for i, s in enumerate(peers)])
    return dict(eventId=f'leaderLagFollowersV1:{calendar[at]}:{symbol}', formationDate=calendar[at],
        peerFormationDate=calendar[at - 10], anchorSymbol=symbol, originalLeaderRank=rank,
        groupSha256=V.digest(group), groupSummary=group)


def build(data, events, end=None, on_evidence=None):
    return V.reference_build(data, events, start=data['calendar'][30], end=end or data['calendar'][-1], on_evidence=on_evidence)


class IndependentHandoffTests(unittest.TestCase):
    def test_exact_m3_crossing_equal_then_strict(self):
        data = fixture(); data['securities'][1]['bars'][32]['amount'] = 200.
        source = indexed(data); member, anchor = (source['sources'][s] for s in ('sh600001', 'sh600000'))
        equal = V.crossing_reference(member, anchor, source['calendar'], 32)
        self.assertFalse(equal['crossing'])
        data['securities'][1]['bars'][32]['amount'] = 201.; source = indexed(data)
        cross = V.crossing_reference(source['sources']['sh600001'], source['sources']['sh600000'], source['calendar'], 32)
        self.assertTrue(cross['crossing'])
        self.assertEqual(cross['current']['member']['total'], 301.)

    def test_first_breakout_consumed_before_later_cross(self):
        data = fixture(); breakout(data, 1, 31); breakout(data, 1, 33, 12.)
        data['securities'][1]['bars'][33]['amount'] = 300.
        source = indexed(data); saved = deepcopy(data)
        result = V.replay_member(source['sources']['sh600001'], source['sources']['sh600000'], source['calendar'], 30)
        self.assertEqual((result['terminal'], result['endedAt']), ('firstBreakout', 31))
        self.assertFalse(result['observations'][-1]['amount']['crossing'])
        self.assertEqual(data, saved)

    def test_unknown_price_permanently_ends_earlier(self):
        data = fixture(); del data['securities'][1]['bars'][31]; breakout(data, 1, 32, 12.)
        source = indexed(data)
        result = V.replay_member(source['sources']['sh600001'], source['sources']['sh600000'], source['calendar'], 30)
        self.assertEqual((result['terminal'], result['endedAt']), ('priceUnknown', 31))

    def test_earlier_unknown_amount_does_not_cancel_price(self):
        data = fixture(); data['securities'][1]['bars'][31]['amount'] = None; breakout(data, 1, 35)
        data['securities'][1]['bars'][35]['amount'] = 300.
        source = indexed(data)
        result = V.replay_member(source['sources']['sh600001'], source['sources']['sh600000'], source['calendar'], 30)
        self.assertEqual((result['terminal'], result['endedAt']), ('firstBreakout', 35))
        self.assertTrue(result['observations'][-1]['amount']['crossing'])

    def test_trigger_amount_unknown_keeps_consumed_breakout(self):
        data = fixture(); data['securities'][0]['bars'][32]['amount'] = None; breakout(data, 1, 32)
        source = indexed(data)
        result = V.replay_member(source['sources']['sh600001'], source['sources']['sh600000'], source['calendar'], 30)
        self.assertEqual(result['terminal'], 'firstBreakout')
        self.assertIsNone(result['observations'][-1]['amount']['crossing'])

    def test_initial_unknown_and_above_remain_excluded(self):
        data = fixture(); data['securities'][1]['bars'][30]['amount'] = 300.; breakout(data, 1, 32)
        source = indexed(data)
        self.assertEqual(V.replay_member(source['sources']['sh600001'], source['sources']['sh600000'], source['calendar'], 30)['terminal'], 'initialAbove')
        data['securities'][1]['bars'][30]['amount'] = None; source = indexed(data)
        self.assertEqual(V.replay_member(source['sources']['sh600001'], source['sources']['sh600000'], source['calendar'], 30)['terminal'], 'initialUnknown')

    def test_cutoff_and_last_slot_assessment(self):
        data = fixture(); source = indexed(data, data['calendar'][33])
        censored = V.replay_member(source['sources']['sh600001'], source['sources']['sh600000'], source['calendar'], 30)
        self.assertEqual((censored['terminal'], censored['endedAt']), ('censored', 33))
        source = indexed(data)
        expired = V.replay_member(source['sources']['sh600001'], source['sources']['sh600000'], source['calendar'], 30)
        self.assertEqual((expired['terminal'], expired['endedAt']), ('expired', 35))
        breakout(data, 1, 35); source = indexed(data)
        self.assertEqual(V.replay_member(source['sources']['sh600001'], source['sources']['sh600000'], source['calendar'], 30)['terminal'], 'firstBreakout')

    def test_prior_cross_is_not_current_cross(self):
        data = fixture(); data['securities'][1]['bars'][31]['amount'] = 300.; breakout(data, 1, 32)
        source = indexed(data)
        record = V.crossing_reference(source['sources']['sh600001'], source['sources']['sh600000'], source['calendar'], 32)
        self.assertGreater(record['previous']['member']['total'], record['previous']['anchor']['total'])
        self.assertGreater(record['current']['member']['total'], record['current']['anchor']['total'])
        self.assertFalse(record['crossing'])

    def test_incomplete_anchor_locks_without_extension(self):
        data = fixture(sessions=45)
        result = build(data, [event(data, 30, kind='incomplete'), event(data, 32), event(data, 35), event(data, 36)])
        self.assertEqual([f['status'] for f in result['formations']], ['notFormed', 'locked', 'locked', 'formed'])
        self.assertEqual([f['lockThroughIndex'] for f in result['formations']], [35, 35, 35, 41])
        self.assertEqual(result['counts']['memberEpisodes'], 20)

    def test_top5_precedes_amount_unknown_without_refill(self):
        data = fixture()
        for i in range(1, 9):
            breakout(data, i, 32); data['securities'][i]['bars'][32]['amount'] = 300.
        data['securities'][1]['bars'][32]['amount'] = None
        result = build(data, [event(data)])
        self.assertEqual(result['counts']['firstBreakout'], 8)
        self.assertEqual(result['counts']['selectedTop5'], 5)
        self.assertEqual(result['counts']['rankExcluded'], 3)
        self.assertEqual([s['originalRank'] for s in result['schedules']['control']], [2, 3, 4, 5])
        self.assertIsNone(result['opportunities'][0]['control'])

    def test_duplicate_owner_not_chosen_for_known_crossing(self):
        data = fixture(); breakout(data, 1, 32)
        data['securities'][1]['bars'][32]['amount'] = 300.
        data['securities'][0]['bars'][32]['amount'] = None
        result = build(data, [event(data, anchor_index=0, rank=1), event(data, anchor_index=21, rank=2)])
        pair = [r for r in result['rawConfirmations'] if r['symbol'] == 'sh600001']
        self.assertEqual(len(pair), 2)
        self.assertEqual([r['ownershipStatus'] for r in pair], ['owner', 'duplicateSuppressed'])
        self.assertIsNone(pair[0]['amountEvidence']['crossing'])
        self.assertTrue(pair[1]['amountEvidence']['crossing'])
        self.assertEqual(result['schedules']['control'], [])

    def test_future_prefix_and_all_layers_reconcile(self):
        data = fixture(); breakout(data, 1, 32); data['securities'][1]['bars'][32]['amount'] = 300.
        events = [event(data), event(data, 37)]
        snapshot = deepcopy((data, events)); evidence = []
        short = build(data, events, data['calendar'][33], evidence.append)
        long = build(data, events)
        self.assertEqual(short['schedules'], long['schedules'])
        # The short run's final evidence day legitimately adds censoring only.
        self.assertEqual(short['dailyRows'][:-1], long['dailyRows'][:len(short['dailyRows']) - 1])
        self.assertEqual(short['rawConfirmations'], [r for r in long['rawConfirmations'] if r['decisionDate'] <= data['calendar'][33]])
        self.assertEqual((data, events), snapshot)
        for row, detail in zip(short['dailyRows'], evidence): self.assertEqual(row['evidenceSha256'], V.digest(detail))
        c = long['counts']
        self.assertEqual(c['memberEpisodes'], 20 * c['unlockedFormed'])
        self.assertEqual(c['memberEpisodes'], c['initialUnknown'] + c['initialAbove'] + c['observedMembers'])
        self.assertEqual(c['firstBreakout'], c['duplicateSuppressed'] + c['uniqueOwners'])
        self.assertEqual(c['uniqueOwners'], c['selectedTop5'] + c['rankExcluded'])

    def test_unknown_diagnostic_is_not_an_admission_gate(self):
        data = fixture(); breakout(data, 1, 32)
        data['securities'][1]['bars'][32]['amount'] = 300.
        data['securities'][1]['bars'][20]['amount'] = None
        result = build(data, [event(data)])
        self.assertEqual(len(result['schedules']['handoff']), 1)
        self.assertIsNone(result['opportunities'][0]['diagnostics']['member']['ownRelativeAmount'])

    def test_parent_contradiction_is_fatal(self):
        data = fixture(); inherited = event(data)
        inherited['peerFormationDate'] = data['calendar'][21]
        with self.assertRaises(AssertionError): build(data, [inherited])
        inherited = event(data)
        with self.assertRaises(AssertionError): build(data, [inherited, deepcopy(inherited)])


if __name__ == '__main__': unittest.main()
