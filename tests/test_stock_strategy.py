import unittest
from datetime import date, timedelta
from contextlib import nullcontext
from types import SimpleNamespace

from service.force_statistics import prepare_force_statistics
from service.stock_strategy import stock_research


class Record(SimpleNamespace):
    def model_dump(self, **kwargs):
        return vars(self).copy()


def trade(**changes):
    return Record(**dict(dict(symbol='sh600001', signal_date='2025-01-02', entry_date='2025-01-03',
                             exit_date='2025-01-06', entry_price=10, exit_price=11, quantity=100,
                             exit_reason='take_profit', entry_signal='金叉'), **changes))


def candles(count=1):
    return [Record(time=(date(2025, 1, 6) + timedelta(days=i)).isoformat(),
                   open=10 + i * .05, high=11 + i * .05, low=9 + i * .05,
                   close=10 + i * .05 + (.3 if i % 3 else -.2),
                   volume=100000 + i * 1000, amount=(100000 + i * 1000) * (10 + i * .05))
            for i in range(count)]


class StockStrategyTest(unittest.TestCase):
    def fixture(self, result, bars=None):
        store = SimpleNamespace(
            _ensure_candles=lambda symbol: candles() if bars is None else bars,
            _build_stock_signal_snapshot=lambda symbol: None,
            list_strategies=lambda: SimpleNamespace(items=[Record(strategy_id='score_only_rank_v1', name='ScoreOnlyRank V1')]),
            _backtest_task_lock=nullcontext(), _lock=nullcontext(),
            _backtest_tasks={'task': Record(task_id='task', status='succeeded', result=result)},
            list_backtest_reports=lambda: SimpleNamespace(items=[]), _annotation_store={})
        return SimpleNamespace(store=store, source='market-root', names={'task':'测试回测'})

    def result(self, **changes):
        return SimpleNamespace(**dict(dict(strategy_id='score_only_rank_v1', strategy_version='1.0.0',
            trades=[], open_signals=[], plan_signals=[], range=Record(date_from='2025-01-02',date_to='2025-01-06'),
            strategy_params={'min_score':0}, execution_path='legacy'), **changes))

    def test_plan_is_not_a_fill_and_no_demonstration_analysis_is_returned(self):
        data = stock_research(self.fixture(self.result(plan_signals=[trade()])), 'SH600001')
        self.assertEqual(data['trades'], [])
        self.assertEqual(len(data['signals']), 1)
        self.assertIsNone(data['signal'])
        self.assertNotIn('analysis', data)
        self.assertEqual(data['runs'][0]['strategy_name'], '纯评分排名V1')

    def test_period_end_position_is_not_a_sell_and_is_not_counted_twice(self):
        row = trade(exit_reason='open')
        data = stock_research(self.fixture(self.result(trades=[row], open_signals=[row])), 'sh600001')
        self.assertEqual(len(data['trades']), 1)
        self.assertEqual(data['trades'][0]['position_state'], 'open')

    def test_last_day_unexecuted_opportunity_is_a_signal_not_a_position(self):
        row = trade(quantity=0, exit_reason='open')
        data = stock_research(self.fixture(self.result(plan_signals=[row], open_signals=[row])), 'sh600001')
        self.assertEqual(data['trades'], [])
        self.assertEqual(len(data['signals']), 1)

    def test_other_symbols_and_unfinished_runs_do_not_leak_into_stock_history(self):
        integration = self.fixture(self.result(trades=[trade(), trade(symbol='sz000001')]))
        integration.store._backtest_tasks['running'] = Record(task_id='running',status='running',result=self.result(trades=[trade()]))
        data = stock_research(integration, 'sh600001')
        self.assertEqual(len(data['trades']), 1)
        self.assertEqual(data['trades'][0]['source_id'], 'task')
        self.assertEqual(data['trades'][0]['run_name'], '测试回测')

    def test_missing_real_data_is_an_error_instead_of_an_empty_success(self):
        integration = self.fixture(self.result())
        def missing(symbol):
            raise ValueError('没有真实日线')
        integration.store._ensure_candles = missing
        with self.assertRaisesRegex(ValueError, '真实日线'):
            stock_research(integration, 'sh600001')
        with self.assertRaisesRegex(ValueError, '证券代码'):
            stock_research(integration, '../secret')

    def test_latest_force_statistics_match_shared_calculation_without_full_history_payload(self):
        bars = candles(80)
        snapshot = Record(health_score=64, force_rhythm_signal={'main_force': 3, 'signal_score': 80})
        integration = self.fixture(self.result(trades=[trade()]), bars)
        integration.store._build_stock_signal_snapshot = lambda symbol: snapshot
        data = stock_research(integration, 'sh600001')
        expected = prepare_force_statistics([
            {'date': row.time, 'open': row.open, 'high': row.high, 'low': row.low,
             'close': row.close, 'volume': row.volume, 'amount': row.amount} for row in bars
        ], volume_unit='shares')
        self.assertEqual(data['force_statistics'], {key: expected[key] for key in ('version', 'metadata', 'current')})
        self.assertNotIn('rows', data['force_statistics'])
        self.assertEqual(data['force_statistics']['current']['date'], data['as_of_date'])
        self.assertEqual(data['force_statistics']['metadata']['calendarBasis'], 'observedBars')
        self.assertEqual(data['signal'], snapshot.model_dump(), 'existing strategy snapshot is not replaced or rewritten')
        self.assertEqual(len(data['trades']), 1)

    def test_missing_candle_values_remain_unknown_and_do_not_block_existing_evidence(self):
        bars = [Record(time='2025-01-06')]
        data = stock_research(self.fixture(self.result(plan_signals=[trade()]), bars), 'sh600001')
        current = data['force_statistics']['current']
        self.assertEqual(current['status'], 'unknown')
        self.assertEqual(current['barStatus'], 'invalid')
        self.assertTrue(current['unknownFields'])
        self.assertEqual(len(data['signals']), 1)

    def test_empty_candles_have_no_invented_force_values(self):
        data = stock_research(self.fixture(self.result(), []), 'sh600001')
        self.assertIsNone(data['force_statistics']['current'])
        self.assertIsNone(data['as_of_date'])
        self.assertEqual(data['lookback_bars'], 0)


if __name__ == '__main__':
    unittest.main()
