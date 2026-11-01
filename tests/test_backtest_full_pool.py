"""Full selected pool, explicit limits, cached pools and plateau propagation."""
import atexit
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))
from service.finaltrade_runtime import configure_environment, REFERENCE
TEMP = tempfile.TemporaryDirectory(prefix='atrade-full-pool-')
atexit.register(TEMP.cleanup)
configure_environment(TEMP.name)
sys.path.insert(0, str(REFERENCE / 'backend'))
from app.models import BacktestRunRequest, BacktestPlateauRunRequest, BacktestResponse, ScreenerParams
from app.store import InMemoryStore


class FullPoolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='atrade-full-pool-case-')
        self.addCleanup(self.temp.cleanup)
        configure_environment(self.temp.name)
        self.store = InMemoryStore(app_state_path=str(Path(self.temp.name) / 'app-state.json'),
                                   sim_state_path=str(Path(self.temp.name) / 'sim-state.json'))
        self.payload = BacktestRunRequest(mode='trend_pool', trend_step='step1',
            date_from='2026-04-01', date_to='2026-04-02', enable_advanced_analysis=False,
            strategy_id='score_only_rank_v1', execution_path_preference='legacy')
        self.rows = [SimpleNamespace(symbol=f'sh{600000 + i:06d}') for i in range(256)]
        self.params = ScreenerParams(markets=['sh'], mode='strict', return_window_days=40,
            top_n=500, turnover_threshold=.05, amount_threshold=5e8, amplitude_threshold=.03)
        self.stats = {'cache_hit_days': 0, 'cache_miss_days': 2, 'cache_write_days': 0}

    def rolling(self, payload=None, rows=None, full_market=False):
        payload = payload or self.payload
        rows = rows if rows is not None else self.rows
        inputs = {day: (rows, None) for day in ['2026-04-01', '2026-04-02']}
        with patch.object(self.store, '_load_backtest_input_rows_by_dates', return_value=(inputs, self.stats)), \
             patch.object(self.store, '_is_backtest_trend_filter_cache_enabled', return_value=False), \
             patch.object(self.store, '_resolve_screener_step_configs', return_value=None), \
             patch.object(self.store, '_run_screener_filters_for_backtest', return_value=(rows, [], [], rows[:7])):
            if full_market:
                return self.store._build_full_market_rolling_universe(payload=payload, board_filters=[])
            return self.store._build_trend_pool_rolling_universe(payload=payload, screener_params=self.params, board_filters=[])

    def test_default_request_selects_all_step1_members(self):
        self.assertEqual(self.payload.max_symbols, 0)
        symbols, allowed, notes, *_ = self.rolling()
        self.assertEqual(set(symbols), {row.symbol for row in self.rows})
        self.assertTrue(all(len(members) == 256 for members in allowed.values()))
        self.assertIn('全部', ' '.join(notes))

    def test_smaller_pool_stays_small_and_an_explicit_limit_still_works(self):
        self.assertEqual(len(self.rolling(rows=self.rows[:17])[0]), 17)
        limited = self.payload.model_copy(update={'max_symbols': 120})
        symbols, allowed, *_ = self.rolling(limited)
        self.assertEqual(symbols, sorted(row.symbol for row in self.rows[:120]))
        self.assertTrue(all(len(members) == 120 for members in allowed.values()))

    def test_selected_step_is_preserved(self):
        symbols, *_ = self.rolling(self.payload.model_copy(update={'trend_step': 'step4'}))
        self.assertEqual(len(symbols), 7)

    def test_full_market_does_not_replace_the_old_limit_with_a_hidden_6000_cap(self):
        rows = [SimpleNamespace(symbol=f'sh{600000 + i:06d}') for i in range(6101)]
        symbols, allowed, *_ = self.rolling(rows=rows, full_market=True)
        self.assertEqual(len(symbols), 6101)
        self.assertTrue(all(len(members) == 6101 for members in allowed.values()))
        self.assertEqual(len(self.rolling(self.payload.model_copy(update={'max_symbols': 120}), full_market=True)[0]), 120)

    def test_static_selected_pool_uses_all_members_by_default(self):
        pools = SimpleNamespace(step1=self.rows, step2=[], step3=[], step4=self.rows[:7])
        run = SimpleNamespace(step_pools=pools, run_id='fixture', as_of_date=self.payload.date_from)
        symbols, allowed, _ = self.store._build_trend_pool_static_universe_from_run(
            payload=self.payload, run_detail=run, board_filters=[])
        self.assertEqual(len(symbols), 256)
        self.assertTrue(all(len(members) == 256 for members in allowed.values()))

    def test_warm_trend_cache_can_return_the_entire_pool(self):
        with patch.object(self.store, '_load_backtest_trend_filter_cache', return_value=[r.symbol for r in self.rows]), \
             patch.object(self.store, '_is_backtest_trend_filter_cache_enabled', return_value=True), \
             patch.object(self.store, '_resolve_screener_step_configs', return_value=None):
            result = self.store._build_trend_pool_rolling_universe(payload=self.payload,
                screener_params=self.params, board_filters=[])
        self.assertEqual(len(result[0]), 256)

    def test_plateau_preserves_all_and_explicit_limits_with_correct_ranking(self):
        observed = []
        def universe(payload, *_args, **_kwargs):
            selected = self.rows if payload.max_symbols == 0 else self.rows[:payload.max_symbols]
            symbols = [r.symbol for r in selected]
            return symbols, {self.payload.date_from: set(symbols)}, []
        def run(payload, **kwargs):
            observed.append((payload.max_symbols, len(kwargs['prebuilt_universe'][0])))
            return BacktestResponse(stats=dict(win_rate=0, total_return=0, max_drawdown=0, avg_pnl_ratio=0, trade_count=0),
                trades=[], range=dict(date_from=payload.date_from, date_to=payload.date_to))
        payload = BacktestPlateauRunRequest(base_payload=self.payload, sampling_mode='grid',
            max_symbols_list=[0, 120], stop_loss_list=[.05], take_profit_list=[.15], sample_points=2, max_points=2)
        with patch.object(self.store, '_build_backtest_universe_for_plateau', side_effect=universe), \
             patch.object(self.store, 'run_backtest', side_effect=run):
            result = self.store.run_backtest_plateau(payload)
        self.assertTrue(all(p.error is None for p in result.points), [p.error for p in result.points])
        self.assertEqual(set(observed), {(0, 256), (120, 120)})
        self.assertEqual({p.params.max_symbols for p in result.points}, {0, 120})

    def test_plateau_without_count_axis_keeps_full_pool(self):
        observed = []
        def run(payload, **kwargs):
            observed.append(payload.max_symbols)
            return BacktestResponse(stats=dict(win_rate=0, total_return=0, max_drawdown=0, avg_pnl_ratio=0, trade_count=0),
                trades=[], range=dict(date_from=payload.date_from, date_to=payload.date_to))
        payload = BacktestPlateauRunRequest(base_payload=self.payload, sampling_mode='grid',
            stop_loss_list=[.04, .05], take_profit_list=[.15], sample_points=2, max_points=2)
        with patch.object(self.store, '_build_backtest_universe_for_plateau', return_value=([], None, [])), \
             patch.object(self.store, 'run_backtest', side_effect=run):
            result = self.store.run_backtest_plateau(payload)
        self.assertEqual({p.params.max_symbols for p in result.points}, {0})
        self.assertEqual(observed, [0, 0])


if __name__ == '__main__':
    unittest.main()
