"""Synthetic causal paths only; no frozen market source or account runs."""
from copy import deepcopy
from datetime import date, timedelta
import math
import unittest

from service.research_peer_activity_alignment import evaluate_peer_activity_alignment
from service.research_peer_amount_share import build_balanced_market, evaluate_peer_amount_share

ANCHOR = "anchor"
PEERS = [f"peer{i:02d}" for i in range(20)]
DAYS = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(30)]


def amount_evidence(shares=None, members=PEERS):
    shares = [.1] * 5 + [.3] * 5 if shares is None else shares
    full = [.05] * 20 + list(shares)
    amounts = {symbol: [v * 100 / 20 for v in full] for symbol in members}
    amounts[ANCHOR] = [(1 - v) * 100 for v in full]
    market = build_balanced_market(DAYS, amounts)
    return evaluate_peer_amount_share(ANCHOR, members, DAYS, amounts, market)


def bars(log_returns, scale=1.):
    closes = [100.]
    for value in log_returns:
        closes.append(closes[-1] * math.exp(value))
    return [{"date": day, "open": close * scale, "high": close * scale * 1.01,
             "low": close * scale * .99, "close": close * scale, "volume": 1000.,
             "priceBasisId": "raw-unadjusted", "knownSuspended": False}
            for day, close in zip(DAYS[19:], closes)]


def inputs(returns=None, shares=None):
    returns = [-.01] * 5 + [.03] * 5 if returns is None else returns
    return {symbol: bars(returns) for symbol in PEERS}, bars([0.] * 10), amount_evidence(shares)


def evaluate(raw, benchmark, amount, members=PEERS, days=None):
    return evaluate_peer_activity_alignment(ANCHOR, members, DAYS[19:] if days is None else days,
                                            raw, benchmark, amount)


class PeerActivityAlignmentTests(unittest.TestCase):
    def test_same_return_sum_and_activity_marginals_different_pairing_flip_covariance(self):
        up = evaluate(*inputs())
        down = evaluate(*inputs([.03] * 5 + [-.01] * 5))
        self.assertEqual([r['activityShare'] for r in up['dailyRows']],
                         [r['activityShare'] for r in down['dailyRows']])
        self.assertAlmostEqual(up['netR'], down['netR'], places=13)
        self.assertAlmostEqual(up['netR'], .1, places=13)
        self.assertAlmostEqual(up['meanA'], .2, places=14)
        self.assertAlmostEqual(up['cov'], .002, places=14)
        self.assertAlmostEqual(down['cov'], -.002, places=14)
        self.assertTrue(up['currentActivity'] and up['priceProgress'] and up['activityAligned'])
        self.assertTrue(down['currentActivity'] and down['priceProgress'])
        self.assertFalse(down['activityAligned'])
        self.assertEqual(down['kind'], 'nonPositiveAlignment')
        self.assertAlmostEqual(math.fsum(up['centeredProducts']) / 10, up['cov'])

    def test_exact_zero_variance_is_known_zero_covariance(self):
        constant_activity = evaluate(*inputs(shares=[.2] * 10))
        self.assertTrue(constant_activity['complete'])
        self.assertEqual(constant_activity['cov'], 0.)
        self.assertEqual(constant_activity['centeredProducts'], [0.] * 10)
        self.assertTrue(constant_activity['priceProgress'])
        self.assertFalse(constant_activity['activityAligned'])
        no_return = evaluate(*inputs([0.] * 10))
        self.assertEqual(no_return['cov'], 0.)
        self.assertEqual(no_return['netR'], 0.)
        self.assertTrue(no_return['currentActivity'])
        self.assertFalse(no_return['priceProgress'])
        self.assertFalse(no_return['activityAligned'])

    def test_benchmark_subtraction_uses_each_matching_daily_interval(self):
        excess = [-.01] * 5 + [.03] * 5
        benchmark_returns = [.02, -.01] * 5
        peer_returns = [r + b for r, b in zip(excess, benchmark_returns)]
        raw = {symbol: bars(peer_returns) for symbol in PEERS}
        result = evaluate(raw, bars(benchmark_returns), amount_evidence())
        for row, expected, benchmark in zip(result['dailyRows'], excess, benchmark_returns):
            self.assertAlmostEqual(row['benchmarkLogReturn'], benchmark, places=13)
            self.assertAlmostEqual(row['relativeLogReturn'], expected, places=13)
            self.assertAlmostEqual(row['peerMeanLogReturn'] - row['benchmarkLogReturn'], expected, places=13)
        self.assertAlmostEqual(result['netR'], .1, places=13)
        self.assertAlmostEqual(result['cov'], .002, places=13)
        self.assertTrue(result['activityAligned'])

    def test_negative_progress_and_known_false_control_remain_nested(self):
        negative = evaluate(*inputs([-.03] * 5 + [.01] * 5))
        self.assertLess(negative['netR'], 0)
        self.assertGreater(negative['cov'], 0)
        self.assertFalse(negative['priceProgress'])
        self.assertFalse(negative['activityAligned'])
        self.assertEqual(negative['kind'], 'noPositiveProgress')
        inactive = evaluate(*inputs(shares=[.01] * 10))
        self.assertTrue(inactive['complete'])
        self.assertEqual(inactive['kind'], 'notActive')
        for key in ('currentActivity', 'priceProgress', 'activityAligned'):
            self.assertIs(inactive[key], False)

    def test_amount_unknown_missing_and_incomplete_group_share_unknown_eligibility(self):
        raw, benchmark, amount = inputs()
        unknown = deepcopy(amount)
        unknown.update(control=None, complete=False)
        for evidence in (None, unknown):
            result = evaluate(raw, benchmark, evidence)
            self.assertEqual(result['kind'], 'unknown')
            self.assertIsNone(result['netR'])
            for key in ('currentActivity', 'priceProgress', 'activityAligned'):
                self.assertIsNone(result[key])
        short = PEERS[:-1]
        result = evaluate(raw, benchmark, amount_evidence(members=short), members=short)
        self.assertIn('incompleteFrozenPeerGroup', result['unknownReasons'])
        self.assertIsNone(result['currentActivity'])

    def test_missing_price_slot_is_not_compressed_even_if_control_false(self):
        raw, benchmark, amount = inputs(shares=[.01] * 10)
        raw[PEERS[0]][4] = None
        result = evaluate(raw, benchmark, amount)
        self.assertIs(result['originalAmountControl'], False)
        self.assertEqual(len(result['dailyRows']), 10)
        self.assertEqual(result['observationDates'], DAYS[20:])
        self.assertEqual(result['priceQuality']['members'][0]['invalidSlot'], 4)
        for key in ('currentActivity', 'priceProgress', 'activityAligned'):
            self.assertIsNone(result[key])

    def test_unchanged_strict_price_quality_for_peers_and_benchmark(self):
        edits = [('priceBasisId', 'adjusted'), ('knownSuspended', True), ('knownSuspended', 0),
                 ('volume', 0), ('close', float('nan')), ('low', 1e6), ('date', DAYS[0])]
        for field, value in edits:
            with self.subTest(field=field, value=value):
                raw, benchmark, amount = inputs()
                raw[PEERS[0]][3][field] = value
                result = evaluate(raw, benchmark, amount)
                self.assertEqual(result['kind'], 'unknown')
                self.assertIsNone(result['currentActivity'])
        raw, benchmark, amount = inputs()
        benchmark[3] = None
        result = evaluate(raw, benchmark, amount)
        self.assertIn('incompleteBenchmarkPriceWindow', result['unknownReasons'])

    def test_exact_f_through_t_only_no_future_or_shifted_window(self):
        raw, benchmark, amount = inputs()
        observed = evaluate(raw, benchmark, amount)
        self.assertEqual(observed['formationDate'], DAYS[19])
        self.assertEqual(observed['signalDate'], DAYS[-1])
        for bad in (DAYS[18:29], DAYS[20:], DAYS[19:][::-1], DAYS[19:-1] + [DAYS[-2]]):
            with self.assertRaises(ValueError):
                evaluate(raw, benchmark, amount, days=bad)
        future = deepcopy(raw[PEERS[0]][-1])
        future['date'] = '2020-01-31'
        raw[PEERS[0]].append(future)
        result = evaluate(raw, benchmark, amount)
        self.assertEqual(result['kind'], 'unknown')
        self.assertEqual(result['priceQuality']['members'][0]['reason'], 'insufficientAlignedRawHistory')

    def test_individual_price_scaling_and_member_order_do_not_change_decisions(self):
        raw, benchmark, amount = inputs()
        original = evaluate(raw, benchmark, amount)
        scaled = deepcopy(raw)
        for i, symbol in enumerate(PEERS):
            for bar in scaled[symbol]:
                for field in ('open', 'high', 'low', 'close'):
                    bar[field] *= i + 2.5
        for bar in benchmark:
            for field in ('open', 'high', 'low', 'close'):
                bar[field] *= 7.25
        changed = evaluate(scaled, benchmark, amount, members=PEERS[::-1])
        self.assertEqual(changed['members'], PEERS[::-1])
        for key in ('meanA', 'meanR', 'netR', 'cov'):
            self.assertAlmostEqual(original[key], changed[key], places=13)
        for key in ('currentActivity', 'priceProgress', 'activityAligned', 'kind'):
            self.assertEqual(original[key], changed[key])
        reordered = evaluate(raw, bars([0.] * 10), amount, members=PEERS[::-1])
        self.assertEqual(original['dailyRows'], reordered['dailyRows'])

    def test_nonfinite_log_return_is_unknown_without_emitting_nan(self):
        raw, benchmark, amount = inputs()
        for i, price in enumerate((1e-300, 1e300)):
            raw[PEERS[0]][i].update(open=price, high=price, low=price, close=price)
        result = evaluate(raw, benchmark, amount)
        self.assertEqual(result['priceQuality']['members'][0]['reason'], 'nonfiniteLogReturn')
        self.assertIsNone(result['cov'])
        self.assertIsNone(result['activityAligned'])

    def test_identity_mismatch_is_rejected_and_inputs_are_not_mutated(self):
        raw, benchmark, amount = inputs()
        original = deepcopy((raw, benchmark, amount))
        evaluate(raw, benchmark, amount)
        self.assertEqual((raw, benchmark, amount), original)
        for changed in ('anchor', 'members', 'formationDate', 'signalDate'):
            bad = deepcopy(amount)
            bad[changed] = 'other' if changed != 'members' else PEERS[:-1]
            with self.assertRaises(ValueError):
                evaluate(raw, benchmark, bad)
        with self.assertRaises(ValueError):
            evaluate(raw, benchmark, amount, members=[ANCHOR] + PEERS[:-1])
        with self.assertRaises(ValueError):
            evaluate(raw, benchmark, amount, members=PEERS[:-1] + [PEERS[0]])


if __name__ == '__main__':
    unittest.main()
