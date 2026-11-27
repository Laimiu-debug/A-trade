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
from app.core.strategy_plugins import MasterAlphaStrategyPlugin
from app.core.backtest_engine import BacktestEngine
from app.models import BacktestRunRequest, CandlePoint, BacktestPlateauPoint, BacktestPlateauResponse, BacktestPlateauRunRequest
from app.store import InMemoryStore, store
from app.core.plateau_features import feature_readers


def wujiang_buy_indicator(trigger_date: str = '2024-02-05'):
    return dict(
        has_data=True,
        trigger_date=trigger_date,
        symbol='sh600000',
        board='main',
        limit_ratio=0.10,
        close=11.0,
        prev_close=10.0,
        day_gain=10.0,
        prev_gain=-2.0,
        ret5=8.0,
        ret20=12.0,
        ret40=15.0,
        ma5=10.5,
        ma10=10.2,
        ma20=10.0,
        ma20_rising=True,
        ma60=9.5,
        ma60_rising=True,
        above_ma5=True,
        above_ma10=True,
        above_ma20=True,
        above_ma60=True,
        ma_bullish=True,
        vol_ratio_5=1.8,
        vol_ratio_20=2.0,
        vol_ratio_prev=1.5,
        up_down_vol_ratio=1.6,
        consec_zt=1,
        is_zt=True,
        prev_is_zt=False,
        prev2_is_zt=True,
        zt_in_5d=2,
        zt_in_10d=2,
        is_one_word=False,
        close_strength=0.95,
        upper_wick_ratio=0.02,
        breakout_20=True,
        breakout_40=True,
        drawdown20=0.0,
        dist_ma10_pct=7.8,
        dist_ma20_pct=10.0,
        amt_yi=6.5,
        sector_rank=3,
    )


class StrategyRegistrationTests(unittest.TestCase):
    def test_corrected_runs_cannot_reuse_the_legacy_result_cache(self):
        payload = BacktestRunRequest(strategy_id='wujiang_mainline_v1', date_from='2024-01-01', date_to='2024-02-01')
        kwargs = dict(strategy_version='2.0.0', strategy_params_hash='same-params', event_judgment_profile_hash='same-profile')
        current = store._build_backtest_result_cache_key(payload, **kwargs)
        with patch.object(store, '_BACKTEST_RESULT_CACHE_VERSION', 'backtest-result-v6'):
            old = store._build_backtest_result_cache_key(payload, **kwargs)
        self.assertNotEqual(current, old)

    def test_every_visible_strategy_has_its_own_plugin(self):
        registry = StrategyRegistry()
        self.assertEqual(len(registry.list()), 9)
        for descriptor in registry.list():
            plugin = registry._get_plugin(descriptor.strategy_id)
            self.assertEqual(plugin.strategy_id, descriptor.strategy_id)
        self.assertIsInstance(registry._get_plugin('wujiang_mainline_v1'), MasterAlphaStrategyPlugin)
        self.assertEqual(registry.get('wujiang_mainline_v1').version, '2.1.0')
        self.assertTrue(registry.get('wujiang_mainline_v1').is_default)
        # Ensure pruned low-value legacy strategies are no longer exposed.
        for removed in ('wyckoff_trend_v1', 'score_only_rank_v1', 'ths_force_rhythm_v1', 'trend_king_v1', 'limit_up_arb_v1'):
            self.assertIsNone(registry.get(removed))

    def test_all_six_master_modes_share_unified_mapping_and_regime_caps(self):
        from app.core.master_alpha_strategies import resolve_master_regime_slot_and_scale
        from service.master_advisor import STRATEGY_ID_TO_STYLE_KEY

        registry = StrategyRegistry()
        expected_ids = (
            'wujiang_mainline_v1',
            'mainline_halfway_v1',
            'first_to_second_v1',
            'highboard_sniper_v1',
            'ruhan_halfway_v1',
            'strong_trend_dip_v1',
        )
        for sid in expected_ids:
            desc = registry.get(sid)
            self.assertIsNotNone(desc, sid)
            self.assertEqual(desc.version, '2.1.0')
            self.assertIn(sid, STRATEGY_ID_TO_STYLE_KEY)
            for key in ('use_regime_switch', 'use_fast_weak_cut', 'use_add_on_win', 'use_partial_trim'):
                self.assertIn(key, desc.params_schema, f'{sid} missing {key}')

        adv_slots, adv_pos, adv_cap = resolve_master_regime_slot_and_scale('wujiang_mainline_v1', 'advance', 4, 0.25, {'use_regime_switch': True})
        dec_slots, dec_pos, dec_cap = resolve_master_regime_slot_and_scale('wujiang_mainline_v1', 'decline', 4, 0.25, {'use_regime_switch': True})
        off_slots, off_pos, off_cap = resolve_master_regime_slot_and_scale('wujiang_mainline_v1', 'decline', 4, 0.25, {'use_regime_switch': False})
        self.assertGreater(adv_slots, dec_slots)
        self.assertGreater(adv_cap, dec_cap)
        self.assertEqual((off_slots, off_pos, off_cap), (4, 0.25, 0.95))

    def test_missing_implementation_cannot_fall_back_to_permissive_plugin(self):
        registry = StrategyRegistry()
        registry._plugins.pop('wujiang_mainline_v1')
        with self.assertRaises(ValueError):
            registry.generate_signals(strategy_id='wujiang_mainline_v1', row=None, snapshot={}, params={})

    def test_master_alpha_accepts_real_buy_conditions_and_rejects_missing_or_ineligible_context(self):
        plugin = StrategyRegistry()._get_plugin('wujiang_mainline_v1')
        self.assertFalse(plugin.generate_signals(row=None, snapshot={}, params={}))
        snapshot = {'master_alpha_signal': wujiang_buy_indicator()}
        self.assertTrue(plugin.generate_signals(row=None, snapshot=snapshot, params={}))
        self.assertFalse(plugin.generate_signals(row=None, snapshot=snapshot, params={'track_mode': 'growth_20cm'}))
        self.assertFalse(plugin.generate_signals(row=None, snapshot=snapshot, params={'min_amount_yi': 10.0}))

    def test_master_alpha_sell_points_are_checked_after_buy_shape_disappears_and_after_leaving_pool(self):
        days = [(dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(40)]
        candles = [CandlePoint(time=day, open=100, high=101, low=99, close=100, volume=100000, amount=1e7) for day in days]
        registry = StrategyRegistry()
        def snapshot(_row, _window, date):
            indicator = wujiang_buy_indicator(date) if date == days[35] else {'has_data': True, 'trigger_date': date, 'above_ma20': True, 'above_ma10': True, 'day_gain': 0.0, 'upper_wick_ratio': 0.1}
            if date == days[38]:
                indicator = dict(has_data=True, trigger_date=date, above_ma20=False, above_ma10=False, day_gain=-4.2, upper_wick_ratio=0.1, vol_ratio_20=1.8, close_strength=0.1, dist_ma20_pct=-3.0)
            return {'master_alpha_signal': indicator, 'entry_quality_score': 80, 'risk_events': []}
        engine = BacktestEngine(get_candles=lambda _: candles,
                                build_row=lambda *_: SimpleNamespace(symbol='sh600000'),
                                calc_snapshot=snapshot, resolve_symbol_name=lambda _: 'fixture',
                                strategy_signal_filter=lambda sid, row, snap, params: registry.generate_signals(strategy_id=sid, row=row, snapshot=snap, params=params))
        payload = BacktestRunRequest(strategy_id='wujiang_mainline_v1', date_from=days[35], date_to=days[-1],
                                     entry_events=[], exit_events=[], min_score=0, min_event_count=0,
                                     stop_loss=.5, take_profit=1.5, delay_invalidation_enabled=False,
                                     enable_advanced_analysis=False)
        for allowed in (None, {day: ({'sh600000'} if day == days[35] else set()) for day in days}):
            with self.subTest(leaves_pool=allowed is not None):
                trades, *_ = engine._build_candidates_for_symbol('sh600000', payload, days[35], days[-1], allowed)
                self.assertEqual(len(trades), 1)
                self.assertEqual(trades[0].exit_date, days[39])
                self.assertIn('MA20', trades[0].exit_reason)


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
