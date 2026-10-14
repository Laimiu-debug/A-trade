"""Regression cases found by running the packaged strategy workflow."""
import atexit
import datetime as dt
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.finaltrade_runtime import configure_environment

TEMP = tempfile.TemporaryDirectory(prefix='atrade-strategy-fixes-')
atexit.register(TEMP.cleanup)
configure_environment(TEMP.name)
sys.path.insert(0, str(ROOT / 'vendor/finaltrade/backend'))
from app.core.strategy_registry import StrategyRegistry
from app.core.strategy_plugins import ForceRhythmPlugin
from app.core.backtest_engine import BacktestEngine
from app.models import BacktestRunRequest, CandlePoint, BacktestPlateauPoint, BacktestPlateauResponse, BacktestPlateauRunRequest
from app.store import InMemoryStore, store
from app.core.plateau_features import feature_readers


def buy_indicator():
    return dict(has_data=True, cycle_count=3, cycle_cv=.1, amplitude_cv=.1, autocorr=.8,
                wave_swing_ratio=.6, main_force=10, retail_force=4, prev_retail_force=5,
                main_force_state='rising', prev_main_force_state='falling', retail_force_state='falling',
                trough_percentile=.45, retail_percentile=.2, signal_score=80)


class StrategyRegistrationTests(unittest.TestCase):
    def test_corrected_runs_cannot_reuse_the_legacy_result_cache(self):
        payload = BacktestRunRequest(strategy_id='ths_force_rhythm_v1', date_from='2024-01-01', date_to='2024-02-01')
        kwargs = dict(strategy_version='1.0.1', strategy_params_hash='same-params', event_judgment_profile_hash='same-profile')
        current = store._build_backtest_result_cache_key(payload, **kwargs)
        with patch.object(store, '_BACKTEST_RESULT_CACHE_VERSION', 'backtest-result-v6'):
            old = store._build_backtest_result_cache_key(payload, **kwargs)
        self.assertNotEqual(current, old)

    def test_every_visible_strategy_has_its_own_plugin(self):
        registry = StrategyRegistry()
        self.assertEqual(len(registry.list()), 16)
        for descriptor in registry.list():
            plugin = registry._get_plugin(descriptor.strategy_id)
            self.assertEqual(plugin.strategy_id, descriptor.strategy_id)
        self.assertIsInstance(registry._get_plugin('ths_force_rhythm_v1'), ForceRhythmPlugin)
        self.assertEqual(registry.get('ths_force_rhythm_v1').version, '1.0.1')

    def test_missing_implementation_cannot_fall_back_to_permissive_plugin(self):
        registry = StrategyRegistry()
        registry._plugins.pop('ths_force_rhythm_v1')
        with self.assertRaises(ValueError):
            registry.generate_signals(strategy_id='ths_force_rhythm_v1', row=None, snapshot={}, params={})

    def test_rhythm_accepts_real_buy_conditions_and_rejects_missing_or_ineligible_context(self):
        plugin = StrategyRegistry()._get_plugin('ths_force_rhythm_v1')
        self.assertFalse(plugin.generate_signals(row=None, snapshot={}, params={}))
        snapshot = {'force_rhythm_signal': buy_indicator()}
        self.assertTrue(plugin.generate_signals(row=None, snapshot=snapshot, params={}))
        self.assertFalse(plugin.generate_signals(row=None, snapshot=snapshot, params={'min_cycle_count': 4}))

    def test_rhythm_sell_points_are_checked_after_buy_shape_disappears_and_after_leaving_pool(self):
        days = [(dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(40)]
        candles = [CandlePoint(time=day, open=100, high=101, low=99, close=100, volume=100000, amount=1e7) for day in days]
        registry = StrategyRegistry()
        def snapshot(_row, _window, date):
            indicator = buy_indicator() if date == days[35] else {}
            if date == days[38]:
                indicator = dict(has_data=True, main_force=10, retail_force=3, trough_percentile=.9, main_force_state='falling')
            return {'force_rhythm_signal': indicator, 'entry_quality_score': 80, 'risk_events': []}
        engine = BacktestEngine(get_candles=lambda _: candles,
                                build_row=lambda *_: SimpleNamespace(symbol='sh600000'),
                                calc_snapshot=snapshot, resolve_symbol_name=lambda _: 'fixture',
                                strategy_signal_filter=lambda sid, row, snap, params: registry.generate_signals(strategy_id=sid, row=row, snapshot=snap, params=params))
        payload = BacktestRunRequest(strategy_id='ths_force_rhythm_v1', date_from=days[35], date_to=days[-1],
                                     entry_events=[], exit_events=[], min_score=0, min_event_count=0,
                                     stop_loss=.5, take_profit=1.5, delay_invalidation_enabled=False,
                                     enable_advanced_analysis=False)
        for allowed in (None, {day: ({'sh600000'} if day == days[35] else set()) for day in days}):
            with self.subTest(leaves_pool=allowed is not None):
                trades, *_ = engine._build_candidates_for_symbol('sh600000', payload, days[35], days[-1], allowed)
                self.assertEqual(len(trades), 1)
                self.assertEqual(trades[0].exit_date, days[39])
                self.assertIn('波峰', trades[0].exit_reason)


def point(profit=.1, passes=True):
    return BacktestPlateauPoint(params=dict(window_days=60, min_score=0, stop_loss=.05, take_profit=.15,
        trailing_stop_pct=0, max_positions=5, position_pct=.2, max_symbols=120, priority_topk_per_day=0),
        stats=dict(win_rate=.6, total_return=profit, max_drawdown=.1, avg_pnl_ratio=.02, trade_count=30),
        passes_hard_filters=passes, local_score=80, point_score=80, plateau_score=80)


class PlateauRecommendationTests(unittest.TestCase):
    def test_parallel_grid_points_do_not_recalculate_the_same_daily_feature(self):
        from concurrent.futures import ThreadPoolExecutor
        import time
        calls=[]
        def calculate(row, window_days, *, as_of_date=None):
            calls.append(as_of_date);time.sleep(.02);return {'score':50}
        read_row,read_snapshot=feature_readers(lambda symbol,date:SimpleNamespace(symbol=symbol),calculate,lambda:'same-rule')
        row=read_row('sh600001','2024-01-01')
        with ThreadPoolExecutor(max_workers=4) as executor:
            results=list(executor.map(lambda _:read_snapshot(row,60,'2024-01-01'),range(16)))
        self.assertEqual(len(calls),1)
        self.assertEqual(len(results),16)

    def test_scan_reuses_only_identical_causal_features_and_owns_its_mutable_values(self):
        calls=[]
        profile=['rule-a']
        def calculate(row, window_days, *, as_of_date=None):
            calls.append((row.symbol,window_days,as_of_date,profile[0]))
            return {'score':50,'events':['entry']}
        read_row, read_snapshot=feature_readers(lambda symbol,date:SimpleNamespace(symbol=symbol,date=date),calculate,lambda:profile[0])
        row=read_row('sh600001','2024-01-01')
        snapshot=read_snapshot(row,60,'2024-01-01'); snapshot['events'].clear()
        self.assertEqual(read_snapshot(row,60,'2024-01-01')['events'],['entry'])
        self.assertEqual(len(calls),1)
        read_snapshot(row,60,'2024-01-02')
        read_snapshot(row,40,'2024-01-01')
        profile[0]='rule-b';read_snapshot(row,60,'2024-01-01')
        self.assertEqual(len(calls),4)
        _, new_scan=feature_readers(lambda symbol,date:SimpleNamespace(symbol=symbol),calculate,lambda:profile[0])
        new_scan(row,60,'2024-01-01')
        self.assertEqual(len(calls),5)

    def test_grid_estimate_is_complete_even_when_old_budget_was_four_points(self):
        payload = BacktestPlateauRunRequest(base_payload=BacktestRunRequest(date_from='2024-01-01',date_to='2024-02-01'),
            sampling_mode='grid',stop_loss_list=[.03+i*.005 for i in range(21)],take_profit_list=[.05+i*.015 for i in range(21)],sample_points=4,max_points=4)
        self.assertEqual(store._estimate_backtest_plateau_total_points(payload),441)
        payload.position_pct_list=[.1,.2,.3,.4,.5]
        with self.assertRaisesRegex(ValueError,'2000'):
            store._estimate_backtest_plateau_total_points(payload)
        with self.assertRaisesRegex(ValueError,'2000'):
            store._run_backtest_plateau_bound(payload)

    def test_ranked_point_alone_is_not_a_stable_region(self):
        self.assertIsNone(InMemoryStore._select_backtest_plateau_recommendation([]))

    def test_negative_or_failed_region_center_cannot_be_recommended(self):
        for candidate in (point(-.2), point(.2, False), point().model_copy(update={'error': 'failed'})):
            self.assertIsNone(InMemoryStore._select_backtest_plateau_recommendation([SimpleNamespace(center_point=candidate)]))

    def test_eligible_region_center_can_be_recommended(self):
        candidate = point()
        self.assertIs(InMemoryStore._select_backtest_plateau_recommendation([SimpleNamespace(center_point=candidate)]), candidate)

    def test_old_report_with_no_region_loses_its_fallback_recommendation(self):
        candidate = point(-.2, False)
        report = BacktestPlateauResponse(base_payload=BacktestRunRequest(date_from='2024-01-01', date_to='2024-02-01'),
                 total_combinations=1, evaluated_combinations=1, points=[candidate],
                 best_point=candidate, recommended_point=candidate, generated_at='2024-02-02')
        fixed = InMemoryStore._rehydrate_backtest_plateau_result(object.__new__(InMemoryStore), report)
        self.assertIsNone(fixed.recommended_point)
        self.assertIsNone(fixed.best_point)
        self.assertEqual(len(fixed.points), 1)

    def test_legacy_rhythm_report_is_preserved_but_cannot_recommend(self):
        candidate = point()
        report = BacktestPlateauResponse(base_payload=BacktestRunRequest(strategy_id='ths_force_rhythm_v1', date_from='2024-01-01', date_to='2024-02-01'),
                 total_combinations=1, evaluated_combinations=1, points=[candidate],
                 best_point=candidate, recommended_point=candidate, generated_at='2024-02-02')
        fixed = InMemoryStore._rehydrate_backtest_plateau_result(object.__new__(InMemoryStore), report)
        self.assertIsNone(fixed.recommended_point)
        self.assertEqual(fixed.points, report.points)
        self.assertTrue(any('旧版节奏波' in note for note in fixed.notes))


if __name__ == '__main__':
    unittest.main()
