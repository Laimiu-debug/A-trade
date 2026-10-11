import unittest

from service.trading_calendar import trading_sessions


class TradingCalendarTests(unittest.TestCase):
    def test_national_day_and_makeup_weekend_are_not_trading_sessions(self):
        self.assertEqual(trading_sessions("2026-09-30", "2026-10-12"),
                         ["2026-09-30", "2026-10-08", "2026-10-09", "2026-10-12"])
        self.assertEqual(trading_sessions("2025-09-28", "2025-10-11"),
                         ["2025-09-29", "2025-09-30", "2025-10-09", "2025-10-10"])

    def test_spring_festival_and_cross_year_schedule(self):
        self.assertEqual(trading_sessions("2026-02-13", "2026-02-24"), ["2026-02-13", "2026-02-24"])
        self.assertEqual(trading_sessions("2025-12-31", "2026-01-05"), ["2025-12-31", "2026-01-05"])

    def test_unknown_future_calendar_does_not_invent_sessions(self):
        self.assertIsNone(trading_sessions("2026-12-31", "2027-01-08"))
        self.assertIsNone(trading_sessions("2024-12-31", "2025-01-08"))
        self.assertEqual(trading_sessions("2026-10-12", "2026-10-11"), [])
        self.assertEqual(trading_sessions("2026-10-10", "2026-10-11"), [])
        for value in ["20261009", "2026-2-01", "2026-02-30", False]:
            with self.assertRaises(ValueError):
                trading_sessions(value, "2026-10-11")


if __name__ == "__main__":
    unittest.main()
