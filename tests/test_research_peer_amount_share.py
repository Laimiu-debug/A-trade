"""Small synthetic amount paths, with no market source or account execution."""
from copy import deepcopy
from datetime import date, timedelta
import unittest

from service.research_peer_amount_share import build_balanced_market, evaluate_peer_amount_share

ANCHOR = "anchor"
PEERS = [f"peer{i:02d}" for i in range(20)]


def dates(count=30):
    return [(date(2020, 1, 1) + timedelta(days=index)).isoformat() for index in range(count)]


def amounts(high_days=8):
    value = {symbol: [1.] * 30 for symbol in PEERS}
    value[ANCHOR] = [20.] * 30
    for symbol in PEERS:
        for index in range(30 - high_days, 30):
            value[symbol][index] = 2.
    return value


def evaluate(value, members=PEERS):
    days = dates()
    market = build_balanced_market(days, value)
    return market, evaluate_peer_amount_share(ANCHOR, members, days, value, market)


class PeerAmountShareTests(unittest.TestCase):
    def test_strict_equal_and_seven_versus_eight(self):
        for count in (0, 7, 8, 10):
            with self.subTest(count=count):
                market, result = evaluate(amounts(count))
                self.assertEqual(result["formationDate"], dates()[19])
                self.assertEqual(result["signalDate"], dates()[29])
                self.assertEqual(result["baseline"], .5)
                self.assertEqual(result["shareAboveBaselineCount"], count)
                self.assertEqual(result["control"], count > 0)
                self.assertEqual(result["candidate"], count >= 8)
                self.assertEqual(market["counts"]["includedSymbols"], 21)
        value = amounts(10)
        for symbol in PEERS:
            value[symbol][-1] = 1.
        _, result = evaluate(value)
        self.assertEqual(result["shareAboveBaselineCount"], 9)
        self.assertEqual(result["current"], result["baseline"])
        self.assertFalse(result["control"])
        self.assertFalse(result["candidate"])

    def test_explicit_zero_missing_and_invalid_are_distinct(self):
        value = amounts()
        value[PEERS[0]][0] = 0.
        market, result = evaluate(value)
        self.assertTrue(result["complete"])
        self.assertIn(PEERS[0], market["universe"])
        self.assertEqual(market["counts"]["validZero"], 1)
        self.assertEqual(market["counts"]["missing"], 0)
        self.assertEqual(result["dailyRows"][0]["peerAmount"], 19.)
        value[PEERS[0]][0] = None
        market, result = evaluate(value)
        self.assertNotIn(PEERS[0], market["universe"])
        self.assertEqual(market["counts"]["missing"], 1)
        self.assertEqual(market["counts"]["validZero"], 0)
        self.assertEqual(result["kind"], "unknown")
        self.assertIsNone(result["control"])
        self.assertIsNone(result["candidate"])
        for invalid in (-1, True, float("nan"), float("inf"), "0"):
            value[PEERS[0]][0] = invalid
            market, result = evaluate(value)
            self.assertEqual(market["counts"]["invalid"], 1)
            self.assertEqual(market["counts"]["missing"], 0)
            self.assertFalse(result["complete"])

    def test_peer_missing_cannot_be_replaced_or_dropped_from_numerator(self):
        value = amounts()
        value[PEERS[-1]][20] = None
        value["replacement"] = [1.] * 30
        market, result = evaluate(value)
        self.assertIn("replacement", market["universe"])
        self.assertEqual(result["members"], PEERS)
        self.assertEqual(result["peersOutsideUniverse"], [PEERS[-1]])
        self.assertTrue(all(row["share"] is None for row in result["dailyRows"]))
        self.assertIsNone(result["control"])
        self.assertIsNone(result["candidate"])
        _, result = evaluate(amounts(), PEERS[:-1])
        self.assertIn("incompleteFrozenPeerGroup", result["unknownReasons"])

    def test_zero_denominator_is_unknown_not_zero_share(self):
        value = amounts()
        for path in value.values():
            path[23] = 0.
        market, result = evaluate(value)
        self.assertEqual(market["counts"]["includedSymbols"], 21)
        self.assertEqual(market["counts"]["zeroDenominatorDays"], 1)
        self.assertEqual(result["dailyRows"][23]["peerAmount"], 0.)
        self.assertIsNone(result["dailyRows"][23]["share"])
        self.assertEqual(result["baseline"], .5)
        self.assertIsNotNone(result["current"])
        self.assertIsNone(result["control"])
        self.assertIsNone(result["candidate"])

    def test_anchor_is_only_in_denominator(self):
        value = amounts(0)
        value[ANCHOR][-1] = 40.
        _, result = evaluate(value)
        self.assertEqual(result["dailyRows"][-1]["peerAmount"], 20.)
        self.assertEqual(result["dailyRows"][-1]["marketAmount"], 60.)
        self.assertAlmostEqual(result["current"], 1 / 3)
        self.assertFalse(result["control"])
        with self.assertRaises(ValueError):
            evaluate(value, [ANCHOR] + PEERS[:-1])

    def test_overlapping_date_has_window_specific_universe_and_denominator(self):
        all_days = dates(31)
        global_amounts = {symbol: [1.] * 31 for symbol in PEERS}
        global_amounts[ANCHOR] = [20.] * 31
        global_amounts["extra"] = [None] + [10.] * 30
        first = {symbol: row[:30] for symbol, row in global_amounts.items()}
        later = {symbol: row[1:] for symbol, row in global_amounts.items()}
        left = build_balanced_market(all_days[:30], first)
        right = build_balanced_market(all_days[1:], later)
        self.assertNotIn("extra", left["universe"])
        self.assertIn("extra", right["universe"])
        self.assertEqual(left["windowDates"][10], right["windowDates"][9])
        self.assertEqual(left["denominators"][10], 40.)
        self.assertEqual(right["denominators"][9], 50.)
        self.assertNotEqual(left["digest"], right["digest"])
        with self.assertRaisesRegex(ValueError, "exact window"):
            evaluate_peer_amount_share(ANCHOR, PEERS, all_days[1:], later, left)

    def test_uniform_currency_scaling_preserves_shares_and_decisions(self):
        value = amounts(8)
        _, original = evaluate(value)
        scaled = {symbol: [number * 10000. for number in row] for symbol, row in value.items()}
        _, result = evaluate(scaled)
        for a, b in zip(original["dailyRows"], result["dailyRows"]):
            self.assertAlmostEqual(a["share"], b["share"], places=14)
        for key in ("baseline", "current", "shareAboveBaselineCount", "control", "candidate", "kind"):
            self.assertEqual(result[key], original[key])

    def test_window_identity_and_market_mutation_are_rejected(self):
        value = amounts()
        for bad_days in (dates(31), dates(29), dates()[::-1], dates()[:-1] + [dates()[-2]]):
            with self.assertRaises(ValueError):
                build_balanced_market(bad_days, value)
        incomplete = deepcopy(value)
        incomplete[ANCHOR].pop()
        with self.assertRaises(ValueError):
            build_balanced_market(dates(), incomplete)
        market = build_balanced_market(dates(), value)
        market["denominators"][0] += 1
        with self.assertRaisesRegex(ValueError, "exact window"):
            evaluate_peer_amount_share(ANCHOR, PEERS, dates(), value, market)


if __name__ == "__main__":
    unittest.main()
