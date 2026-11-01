"""Real file changes must invalidate every layer of a new backtest request."""
import atexit
import datetime as dt
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

BOOT = tempfile.TemporaryDirectory(prefix='atrade-source-import-')
atexit.register(BOOT.cleanup)
configure_environment(BOOT.name)
sys.path.insert(0, str(REFERENCE / 'backend'))
from app.models import BacktestRunRequest
from app.store import InMemoryStore, BacktestValidationError
from app import tdx_loader


class BacktestSourceRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='atrade-source-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / 'vipdoc'
        self.path = self.data / 'sh/lday/sh600001.day'
        self.path.parent.mkdir(parents=True)
        self.days = []
        day = dt.date(2025, 1, 2)
        while len(self.days) < 450:
            if day.weekday() < 5:
                self.days.append(day.isoformat())
            day += dt.timedelta(days=1)
        self.write_prices()
        configure_environment(self.root / 'state')
        env = patch.dict(os.environ, {'TDX_TREND_BACKTEST_RESULT_CACHE': '1',
                                     'TDX_TREND_INPUT_POOL_RUNTIME_CACHE': '1',
                                     'TDX_TREND_BACKTEST_MATRIX_DIFF_GUARD': '0'})
        env.start()
        self.addCleanup(env.stop)
        self.store = InMemoryStore(app_state_path=str(self.root / 'state/app-state.json'),
                                   sim_state_path=str(self.root / 'state/sim-state.json'))
        config = self.store.get_config()
        config.tdx_data_path = str(self.data)
        config.akshare_cache_dir = str(self.root / 'csv')
        config.market_data_source = 'tdx_only'
        config.markets = ['sh']
        self.store.set_config(config)
        self.payload = BacktestRunRequest(date_from=self.days[-5], date_to=self.days[-1],
            strategy_id='ths_main_force_golden_cross_v1', mode='full_market',
            pool_roll_mode='daily', enable_advanced_analysis=False,
            execution_path_preference='legacy')

    def write_prices(self, correction=0):
        raw = bytearray()
        for i, day in enumerate(self.days):
            price = 1000 + i + (correction if i >= len(self.days) - 3 else 0)
            raw.extend(struct.pack('<IIIIIfII', int(day.replace('-', '')), price,
                                   price + 10, price - 10, price, 300000000, 20000000, 0))
        previous = self.path.stat().st_mtime_ns if self.path.exists() else 0
        self.path.write_bytes(raw)
        # Make timestamp advancement deterministic even on coarse filesystems.
        os.utime(self.path, ns=(max(previous + 1000000, self.path.stat().st_mtime_ns),) * 2)

    def result_key(self):
        return self.store._build_backtest_result_cache_key(self.payload, strategy_version='v',
            strategy_params_hash='p', event_judgment_profile_hash='e')

    def test_same_date_same_length_correction_changes_source_and_precheck(self):
        before = self.result_key()
        precheck = self.store._build_backtest_precheck_cache_key(self.payload)
        size = self.path.stat().st_size
        self.write_prices(20)
        self.assertEqual(size, self.path.stat().st_size)
        self.assertNotEqual(before, self.result_key())
        self.assertNotEqual(precheck, self.store._build_backtest_precheck_cache_key(self.payload))

    def test_add_remove_csv_and_metadata_change_revision(self):
        for relative in ('vipdoc/sh/lday/sh600002.day', 'T0002/hq_cache/shs.tnf',
                         'T0002/hq_cache/base.dbf', 'base.dbf', 'csv/sh600001.csv'):
            with self.subTest(relative=relative):
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                before = self.result_key()
                path.write_bytes(b'one')
                added = self.result_key()
                self.assertNotEqual(before, added)
                path.write_bytes(b'changed length')
                self.assertNotEqual(added, self.result_key())
                path.unlink()
                self.assertEqual(before, self.result_key())

    def test_input_pool_reloads_corrected_price_without_new_trade_date(self):
        args = dict(markets=['sh'], return_window_days=40,
                    as_of_date=self.days[-1])
        first, _, _ = self.store._load_input_pool_rows(**args)
        self.write_prices(20)
        second, _, _ = self.store._load_input_pool_rows(**args)
        self.assertNotEqual(first[0].latest_price, second[0].latest_price)

    def test_loader_metadata_caches_use_revision(self):
        args = dict(tdx_root=str(self.data), markets=['sh'], return_window_days=40,
                    as_of_date=self.days[-1])
        with patch.object(tdx_loader, '_load_symbol_name_map_from_tnf',
                          return_value={'sh600001': '旧名称'}) as names, \
             patch.object(tdx_loader, '_load_float_shares_from_base_dbf',
                          return_value=({'sh600001': 100000000}, None)) as shares:
            first, _ = tdx_loader.load_input_pool_from_tdx(**args)
            names.return_value = {'sh600001': 'ST新名称'}
            shares.return_value = ({'sh600001': 200000000}, None)
            meta = self.root / 'T0002/hq_cache/base.dbf'
            meta.parent.mkdir(parents=True)
            meta.write_bytes(b'updated')
            second, _ = tdx_loader.load_input_pool_from_tdx(**args)
            self.assertEqual(names.call_count, 2)
            self.assertEqual(shares.call_count, 2)
            self.assertEqual(first[0].name, '旧名称')
            self.assertEqual(second[0].name, 'ST新名称')
            self.assertNotEqual(first[0].turnover20, second[0].turnover20)

    def test_source_changes_during_run_reject_result_and_keep_original_key(self):
        before = self.result_key()
        with self.assertRaises(BacktestValidationError) as caught:
            with self.store._backtest_candle_history(self.payload):
                self.assertEqual(before, self.result_key())
                self.write_prices(20)
                self.assertEqual(before, self.result_key())
        self.assertEqual(caught.exception.code, 'BACKTEST_MARKET_DATA_CHANGED')
        self.assertNotEqual(before, self.result_key())

    def test_failed_run_caches_stay_retired_after_original_files_are_restored(self):
        original_key = self.result_key()
        extra = self.path.with_name('sh600002.day')
        save = self.store._save_backtest_result_cache

        def change_after_cache_write(*args, **kwargs):
            saved = save(*args, **kwargs)
            extra.write_bytes(self.path.read_bytes())
            return saved

        with patch.object(self.store, '_save_backtest_result_cache', side_effect=change_after_cache_write):
            with self.assertRaises(BacktestValidationError):
                self.store.run_backtest(self.payload)
        extra.unlink()
        # Same original filenames, sizes and timestamps; persistent generation
        # retirement still makes caches from the failed execution unreachable.
        self.assertNotEqual(original_key, self.result_key())
        rebuilt = self.store.run_backtest(self.payload)
        self.assertFalse(any('回测结果缓存命中' in note for note in rebuilt.notes))
        restarted = InMemoryStore(app_state_path=str(self.root / 'state/app-state.json'),
                                  sim_state_path=str(self.root / 'state/sim-state.json'))
        self.assertEqual(self.store._market_data_revision(), restarted._market_data_revision())

    def test_plateau_worker_uses_parent_revision_before_prebuilt_inputs(self):
        with self.assertRaises(BacktestValidationError):
            with self.store._backtest_candle_history(self.payload):
                self.result_key()
                revisions = dict(self.store._backtest_runtime_context.market_revisions)
                history = self.store._backtest_runtime_context.candle_history
                self.write_prices(20)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    worker = executor.submit(self.store._run_with_candle_history,
                        history, lambda: self.fail('Changed source must be rejected before prebuilt inputs'),
                        market_revisions=revisions)
                    worker.result()

    def test_legacy_recomputes_after_correction_and_rejects_deleted_source(self):
        self.check_run_revision('legacy', 'ths_main_force_golden_cross_v1')

    def test_matrix_rebuilds_after_correction_and_rejects_deleted_source(self):
        self.check_run_revision('matrix', 'wyckoff_trend_v1')

    def check_run_revision(self, execution, strategy):
        payload = self.payload.model_copy(update={'execution_path_preference': execution,
                                                  'strategy_id': strategy})
        first = self.store.run_backtest(payload)
        self.assertEqual(first.execution_path, execution)
        cached = self.store.run_backtest(payload)
        self.assertTrue(any('回测结果缓存命中' in note for note in cached.notes))
        self.write_prices(20)
        with patch.object(self.store, '_ensure_candles', wraps=self.store._ensure_candles) as candles:
            corrected = self.store.run_backtest(payload)
        self.assertFalse(any('回测结果缓存命中' in note for note in corrected.notes))
        self.assertGreater(candles.call_count, 0, 'Lower matrix/event caches must also reload prices')
        self.path.unlink()
        with self.assertRaises(ValueError):
            self.store.run_backtest(payload)


if __name__ == '__main__':
    unittest.main()
