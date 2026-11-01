"""Boundary checks for the independent matched-anchor reference."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_matched_reference_tests', ROOT / 'scripts/verify-leader-matched-anchor.py')
V = importlib.util.module_from_spec(spec); spec.loader.exec_module(V)


def evidence():
    return {'initial': {'member': 5., 'real': 10., 'pseudo': 10.},
            'previous': {'member': 5., 'real': 10., 'pseudo': 10.},
            'current': {'member': 15., 'real': 10., 'pseudo': 10.}}


def fixture(symbols=24, sessions=40):
    dates = [(date(2022, 1, 1) + timedelta(days=i)).isoformat() for i in range(sessions)]
    def bars(amount):
        return [dict(date=d, open=10., high=10.1, low=9.9, close=10., volume=100., amount=amount) for d in dates]
    return dict(calendar=dates, priceBasisId='raw-unadjusted', volumeUnit='shares', amountUnit='CNY',
        securities=[dict(symbol=f'sh{600000+i:06d}', bars=bars(100. if i == 0 else 50.)) for i in range(symbols)],
        benchmark=dict(symbol='sh000300', bars=bars(100.)))


def parent_fixture():
    data = fixture()
    for i, amount in [(21, 100.), (22, 101.), (23, 110.)]:
        for bar in data['securities'][i]['bars']: bar['amount'] = amount
    data['securities'][1]['bars'][32].update(open=11., high=11.1, low=10.9, close=11., amount=300.)
    calendar = data['calendar']; events = []
    for at in (30, 37):
        day, frozen = calendar[at], calendar[at - 10]
        group = dict(anchorSymbol='sh600000', peerFormationDate=frozen, kind='formed', selectedCount=20, requiredCount=20,
            members=[dict(symbol=f'sh{600000+i:06d}', frozenMemberRank=i) for i in range(1, 21)])
        events.append(dict(eventId=f'leaderLagFollowersV1:{day}:sh600000', formationDate=day, peerFormationDate=frozen,
            anchorSymbol='sh600000', originalLeaderRank=1, groupSummary=group, groupSha256=V.digest(group)))
    parent = V.H.reference_build(data, events, start=calendar[30], end=calendar[-1])
    return data, parent['formations'], parent['opportunities']


def build(data, formations, opportunities, on_match_evidence=None, end=None):
    return V.reference_build(data, formations, opportunities, start=data['calendar'][30],
        end=end or data['calendar'][-1], on_match_evidence=on_match_evidence)


class MatchingReferenceTests(unittest.TestCase):
    def test_group_exclusion_and_exact_tie(self):
        scales = {'real': 100., 'peer': 100., 'b': 100., 'a': 100.}
        state, chosen, rows = V.select_match(scales, 'real', ['peer'])
        self.assertEqual((state, chosen), ('matched', 'a'))
        self.assertEqual([r[0] for r in rows], ['a', 'b'])

    def test_unknown_anchor_and_no_candidate(self):
        self.assertEqual(V.select_match({'real': None, 'a': 100.}, 'real', [])[0], 'anchorScaleUnknown')
        self.assertEqual(V.select_match({'real': 100., 'a': None}, 'real', [])[0], 'noEligibleCandidate')

    def test_prior20_excludes_a_and_exact_missing(self):
        data = fixture(); calendar = data['calendar']; at = 30
        index = V.H.G.source_reference(data, calendar[30], calendar[-1])
        value, detail = V.historical_scale(index['sources']['sh600000'], calendar, at)
        self.assertEqual(value, 100.)
        self.assertEqual(detail['dates'], calendar[10:30])
        data['securities'][0]['bars'][30]['amount'] = None
        index = V.H.G.source_reference(data, calendar[30], calendar[-1])
        self.assertEqual(V.historical_scale(index['sources']['sh600000'], calendar, at)[0], 100.)
        data['securities'][0]['bars'][10]['amount'] = None
        index = V.H.G.source_reference(data, calendar[30], calendar[-1])
        self.assertIsNone(V.historical_scale(index['sources']['sh600000'], calendar, at)[0])

    def test_four_overlap_cells(self):
        for real, pseudo, expected in [(10., 10., (True, True)), (10., 20., (True, False)),
                                      (20., 10., (False, True)), (20., 20., (False, False))]:
            e = evidence(); e['current'].update(real=real, pseudo=pseudo)
            r = V.common_reference(e, True)
            self.assertEqual((r['allowed']['realCross'], r['allowed']['pseudoCross']), expected)
            self.assertTrue(r['allowed']['control'])

    def test_status_priority_preserves_independent_flags(self):
        e = evidence(); e['initial']['pseudo'] = 4.; e['current']['real'] = None
        r = V.common_reference(e, True)
        self.assertEqual(r['state'], 'initialAbove')
        self.assertEqual(r['initialAbove'], ['pseudo'])
        self.assertIn(('current', 'real'), r['decisionUnknown'])
        self.assertFalse(any(r['allowed'].values()))
        e['initial']['real'] = None
        self.assertEqual(V.common_reference(e, True)['state'], 'initialUnknown')
        self.assertEqual(V.common_reference(e, False)['state'], 'matchUnavailable')

    def test_equality_and_no_later_crossover_substitution(self):
        e = evidence(); e['initial']['member'] = 10.; e['previous']['member'] = 10.
        e['current']['member'] = 10.
        self.assertEqual(V.common_reference(e, True)['state'], 'commonKnown')
        self.assertFalse(V.common_reference(e, True)['allowed']['realCross'])
        e['previous']['member'] = 11.; e['current']['member'] = 15.
        self.assertFalse(V.common_reference(e, True)['allowed']['realCross'])

    def test_all_formed_events_match_even_without_opportunity_and_reuse(self):
        data, forms, opps = parent_fixture(); stream = []
        result = build(data, forms, opps, stream.append)
        self.assertEqual(len(stream), 2)
        self.assertEqual(result['counts']['matched'], 2)
        self.assertEqual(result['counts']['distinctPseudoAnchors'], 1)
        self.assertEqual(result['counts']['reusedPseudoAssignments'], 1)
        self.assertEqual(result['counts']['originalTop5'], 1)
        for r in stream: self.assertEqual(r['candidatesSha256'], V.digest(r['candidates']))

    def test_a_unknown_does_not_select_second_nearest(self):
        data, forms, opps = parent_fixture()
        data['securities'][21]['bars'][30]['amount'] = None
        result = build(data, forms, opps)
        self.assertEqual(result['matches'][0]['pseudoSymbol'], 'sh600021')
        self.assertEqual(result['opportunities'][0]['commonStatus'], 'initialUnknown')
        self.assertTrue(all(not rows for rows in result['schedules'].values()))

    def test_initial_above_precedes_later_unknown_without_hiding_it(self):
        data, forms, opps = parent_fixture()
        for at in (28, 29, 30): data['securities'][21]['bars'][at]['amount'] = 1.
        # Keep pre-A scale at 100, so this identity stays the closest match.
        data['securities'][21]['bars'][10]['amount'] += 198.
        data['securities'][21]['bars'][32]['amount'] = None
        result = build(data, forms, opps)
        row = result['opportunities'][0]
        self.assertEqual(row['pseudoSymbol'], 'sh600021')
        self.assertEqual(row['commonStatus'], 'initialAbove')
        self.assertTrue(row['flags']['decisionUnknownReasons'])
        self.assertEqual([row[p] for p in V.POLICIES], [False] * 3)

    def test_inherited_evidence_contradiction_is_rejected(self):
        data, forms, opps = parent_fixture()
        opps[0]['initialAmountEvidence']['member']['total'] += 1.
        with self.assertRaises(AssertionError): build(data, forms, opps)

    def test_no_match_preserves_known_real_crossing_and_original_slot(self):
        data, forms, opps = parent_fixture()
        for i in (21, 22, 23): data['securities'][i]['bars'][10]['amount'] = None
        result = build(data, forms, opps)
        row = result['opportunities'][0]
        self.assertEqual(result['matches'][0]['matchStatus'], 'noEligibleCandidate')
        self.assertEqual(row['commonStatus'], 'matchUnavailable')
        self.assertTrue(row['crossing']['real'])
        self.assertIsNone(row['crossing']['pseudo'])
        self.assertTrue(row['flags']['initialUnknownReasons'])
        self.assertEqual(row['originalRank'], opps[0]['originalRank'])
        self.assertTrue(all(row[p] is None for p in V.POLICIES))

    def test_anchor_scale_unknown_not_replaced_and_other_formations_retained(self):
        data, forms, opps = parent_fixture()
        data['securities'][0]['bars'][10]['amount'] = None
        forms[1]['status'] = 'locked'
        result = build(data, forms, opps)
        self.assertEqual([r['matchStatus'] for r in result['matches']], ['anchorScaleUnknown', 'inheritedOther'])
        self.assertEqual(result['matches'][0]['eligibleCandidateCount'], 3)
        self.assertIsNone(result['matches'][0]['pseudoSymbol'])

    def test_original_identity_order_prefix_and_immutability(self):
        data, forms, opps = parent_fixture(); snapshot = deepcopy((data, forms, opps))
        full = build(data, forms, opps)
        short = build(data, forms, opps, end=data['calendar'][33])
        self.assertEqual(short['matches'], full['matches'][:1])
        self.assertEqual(short['opportunities'], full['opportunities'])
        self.assertEqual(short['schedules'], full['schedules'])
        self.assertEqual((data, forms, opps), snapshot)
        for row, old in zip(full['opportunities'], opps):
            self.assertEqual((row['parentOpportunityId'], row['originalRank'], row['signalDate']),
                             (old['signalId'], old['originalRank'], old['signalDate']))


if __name__ == '__main__': unittest.main()
