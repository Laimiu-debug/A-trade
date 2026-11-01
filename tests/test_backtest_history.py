"""Real-format history regression: date selection must not depend on a live chart tail."""
import atexit
import csv
import datetime as dt
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.finaltrade_runtime import configure_environment, REFERENCE

BOOT = tempfile.TemporaryDirectory(prefix='atrade-history-import-')
atexit.register(BOOT.cleanup)
configure_environment(BOOT.name)
sys.path.insert(0, str(REFERENCE / 'backend'))
from app.models import BacktestRunRequest, BacktestPlateauRunRequest
from app.store import InMemoryStore, BacktestValidationError
from app.tdx_loader import load_candles_for_symbol, _parse_day_file


def fixture(root):
    folder = root / 'vipdoc/sh/lday'
    folder.mkdir(parents=True)
    days = []
    day = dt.date(1999, 1, 4)
    while day <= dt.date(2026, 10, 16):
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += dt.timedelta(days=1)
    for stock in range(3):
        raw = bytearray()
        for i, day in enumerate(days):
            close = 30 + stock + .01 * i + math.sin(i / 7)
            raw.extend(struct.pack('<IIIIIfII', int(day.replace('-', '')), round(close * 100),
                                   round((close + .5) * 100), round((close - .5) * 100),
                                   round(close * 100), close * 12000000, 12000000, 0))
        (folder / f'sh60000{stock + 1}.day').write_bytes(raw)
    return days


class BacktestHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='atrade-date-history-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.days = fixture(self.root)
        self.env = patch.dict(os.environ, {
            'TDX_TREND_BACKTEST_RESULT_CACHE_ENABLED': '0',
            'TDX_TREND_BACKTEST_MATRIX_BUILD_WORKERS': '2',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        configure_environment(self.root / 'state')
        self.store = InMemoryStore(app_state_path=str(self.root / 'state/app-state.json'),
                                   sim_state_path=str(self.root / 'state/sim-state.json'))
        config = self.store.get_config()
        config.tdx_data_path = str(self.root / 'vipdoc')
        config.market_data_source = 'tdx_only'
        config.markets = ['sh']
        config.candles_window_bars = 120
        self.store.set_config(config)
        self.payload = BacktestRunRequest(date_from='2026-04-01', date_to='2026-06-01',
            strategy_id='ths_main_force_golden_cross_v1', mode='full_market', max_symbols=120,
            pool_roll_mode='weekly', enable_advanced_analysis=False, execution_path_preference='legacy')
        self.symbols = ['sh600001', 'sh600002', 'sh600003']

    def test_reported_interval_reads_history_and_keeps_chart_setting(self):
        latest = self.store._ensure_candles(self.symbols[0])
        self.assertEqual(len(latest), 120)
        self.assertGreater(latest[0].time, self.payload.date_from)
        with self.store._backtest_candle_history(self.payload):
            historical = self.store._ensure_candles(self.symbols[0])
            self.assertLess(historical[0].time, self.payload.date_from)
            self.assertEqual(historical[-1].time, self.payload.date_to)
            self.assertGreaterEqual(sum(c.time < self.payload.date_from for c in historical), 400)
        self.assertEqual(self.store.get_config().candles_window_bars, 120)
        self.assertEqual(self.store._ensure_candles(self.symbols[0]), latest)
        self.store._precheck_backtest_data_coverage_before_task(self.payload)

    def test_long_interval_no_longer_has_a_fixed_window_gate(self):
        payload = self.payload.model_copy(update={'date_from': '2010-01-04'})
        with patch.object(self.store, '_validate_backtest_data_coverage',
                          wraps=self.store._validate_backtest_data_coverage) as validate:
            self.store._precheck_backtest_data_coverage_before_task(payload)
        self.assertTrue(validate.called, 'An old-date empty candidate pool must not make the precheck pass')
        with self.store._backtest_candle_history(payload):
            self.assertGreater(len(self.store._ensure_candles(self.symbols[0])), 1200)

    def test_parallel_matrix_workers_keep_the_historical_interval(self):
        with self.store._backtest_candle_history(self.payload):
            loader = self.store._capture_candle_loader()
        with ThreadPoolExecutor(max_workers=3) as executor:
            points = list(executor.map(loader, self.symbols))
        self.assertTrue(all(p[-1].time == self.payload.date_to for p in points))
        self.assertTrue(all(p[0].time < self.payload.date_from for p in points))
        self.assertEqual(self.store._ensure_candles(self.symbols[0])[-1].time, self.days[-1])

    def test_two_concurrent_date_ranges_are_independent(self):
        def read(start):
            payload = self.payload.model_copy(update={'date_from': start, 'date_to': start[:4] + '-06-01'})
            with self.store._backtest_candle_history(payload):
                return self.store._ensure_candles(self.symbols[0])[-1].time
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(read, ['2024-04-01', '2026-04-01']))
        self.assertEqual(results, ['2024-05-31', '2026-06-01'])

    def test_weekend_endpoints_do_not_require_weekend_bars(self):
        payload = self.payload.model_copy(update={'date_from': '2026-04-04', 'date_to': '2026-05-31'})
        self.store._validate_backtest_data_coverage(payload, self.symbols, scope_label='test')

    def test_real_missing_history_is_reported_without_window_advice(self):
        for updates in ({'date_from': '1998-01-02'}, {'date_to': '2027-01-04'}):
            with self.subTest(updates=updates), self.assertRaises(BacktestValidationError) as caught:
                self.store._validate_backtest_data_coverage(
                    self.payload.model_copy(update=updates), self.symbols, scope_label='test')
            self.assertEqual(caught.exception.code, 'BACKTEST_DATA_COVERAGE_INSUFFICIENT')
            self.assertNotIn('增大K线窗口', str(caught.exception))

    def test_csv_history_uses_same_requested_interval(self):
        cache = self.root / 'csv'
        cache.mkdir()
        with (cache / 'sh600001.csv').open('w', newline='', encoding='utf-8') as fp:
            writer = csv.writer(fp)
            writer.writerow(['date', 'open', 'high', 'low', 'close', 'volume'])
            writer.writerows([day, 10, 11, 9, 10, 1000000] for day in self.days)
        points = load_candles_for_symbol('', 'sh600001', 120, market_data_source='akshare_only',
            akshare_cache_dir=str(cache), date_from=self.payload.date_from, date_to=self.payload.date_to, warmup_bars=400)
        self.assertEqual(points[-1].time, self.payload.date_to)
        self.assertLess(points[0].time, self.payload.date_from)

    def test_legacy_and_matrix_complete_reported_range(self):
        for engine in ('legacy', 'matrix'):
            with self.subTest(engine=engine):
                result = self.store.run_backtest(self.payload.model_copy(update={
                    'execution_path_preference': engine,
                    'strategy_id': 'wyckoff_trend_v1' if engine == 'matrix' else self.payload.strategy_id}))
                self.assertEqual(result.execution_path, engine)
                self.assertEqual(result.effective_run_request.date_from, self.payload.date_from)
                self.assertEqual(result.effective_run_request.date_to, self.payload.date_to)
                self.assertTrue(result.equity_curve)
                self.assertTrue(all(self.payload.date_from <= p.date <= self.payload.date_to for p in result.equity_curve))

    def test_plateau_parallel_points_complete_historical_range(self):
        payload = BacktestPlateauRunRequest(base_payload=self.payload, sampling_mode='grid',
            stop_loss_list=[.04, .05], take_profit_list=[.15], max_points=2, sample_points=2)
        result = self.store.run_backtest_plateau(payload)
        self.assertEqual(result.evaluated_combinations, 2)
        self.assertTrue(all(p.error is None for p in result.points), [p.error for p in result.points])

    def test_exception_restores_live_chart_context(self):
        with self.assertRaises(RuntimeError):
            with self.store._backtest_candle_history(self.payload):
                raise RuntimeError('cancelled')
        self.assertEqual(len(self.store._ensure_candles(self.symbols[0])), 120)

    def test_historical_listing_age_does_not_include_future_records(self):
        path = self.root / 'vipdoc/sh/lday/sh600001.day'
        parsed = _parse_day_file(path, 'sh600001', date_from=self.days[90], date_to=self.days[90])
        self.assertEqual(parsed['total_bars'], 91)
        self.assertEqual(parsed['dates'][-1], self.days[90])


if __name__ == '__main__':
    unittest.main()
