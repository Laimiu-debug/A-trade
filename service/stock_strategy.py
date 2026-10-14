"""Source-backed stock details shared by market and strategy entry points."""
import re

from service.strategy_integration import legacy_strategy_warning


def stock_symbol(value):
    symbol = str(value).strip().lower()
    if not re.fullmatch(r'(sh|sz|bj)\d{6}', symbol):
        raise ValueError('证券代码需为 sh、sz 或 bj 加六位数字。')
    return symbol


def stock_research(integration, symbol):
    store = integration.store
    symbol = stock_symbol(symbol)
    # This loader refuses missing real data. Never use get_analysis(), whose
    # generic fallback contains a demonstration conclusion and confidence.
    candles = store._ensure_candles(symbol)
    signal = store._build_stock_signal_snapshot(symbol)
    descriptors = {item.strategy_id: item for item in store.list_strategies().items}
    sources = []
    with store._backtest_task_lock:
        for task in store._backtest_tasks.values():
            if task.status == 'succeeded' and task.result is not None:
                sources.append((task.task_id, 'task', integration.names.get(task.task_id), task.result))
    warnings = []
    try:
        summaries = store.list_backtest_reports().items
    except (ValueError, OSError) as error:
        summaries = []
        warnings.append('报告目录读取失败：' + str(error))
    for summary in summaries:
        try:
            report = store.get_backtest_report(summary.report_id)
            if report and report.run_result is not None:
                sources.append((summary.report_id, 'report', getattr(summary, 'name', None), report.run_result))
        except (ValueError, OSError) as error:
            warnings.append('报告读取失败：' + str(error))

    trades, signals, runs = [], [], []
    for source_id, source_type, name, result in sources:
        sid = result.strategy_id
        strategy_name = getattr(descriptors.get(sid), 'name', sid)
        if sid == 'score_only_rank_v1':
            strategy_name = '纯评分排名V1'
        meta = dict(source_id=source_id, source_type=source_type, strategy_id=sid,
                    strategy_name=strategy_name, strategy_version=result.strategy_version,
                    run_name=name or strategy_name, warning=legacy_strategy_warning(sid, result.strategy_version))
        relevant = False
        for row in result.trades:
            if row.symbol.lower() == symbol and row.quantity > 0:
                state = 'open' if row.exit_reason == 'open' else 'closed'
                trades.append({**row.model_dump(mode='json'), **meta, 'position_state': state})
                relevant = True
        seen_signals = set()
        # open_signals means recent unexecuted opportunities in this engine,
        # including quantity=0 at period end. It is not a position collection.
        for row in [*result.plan_signals, *result.open_signals]:
            if row.symbol.lower() == symbol:
                key = (row.signal_date, row.entry_signal)
                if key in seen_signals:
                    continue
                seen_signals.add(key)
                signals.append({**row.model_dump(mode='json'), **meta})
                relevant = True
        if relevant:
            runs.append({**meta, 'range': result.range.model_dump(mode='json'),
                         'strategy_params': result.strategy_params, 'execution_path': result.execution_path})
    with store._lock:
        annotation = store._annotation_store.get(symbol)
    return {'symbol': symbol, 'source': integration.source,
            'as_of_date': candles[-1].time if candles else None,
            'lookback_bars': len(candles),
            'strategies': [{'strategy_id': sid, 'strategy_name': '纯评分排名V1' if sid == 'score_only_rank_v1' else item.name}
                           for sid, item in descriptors.items() if getattr(item, 'enabled', True)],
            'signal': signal.model_dump(mode='json') if signal else None,
            'annotation': annotation.model_dump(mode='json') if annotation else None,
            'trades': sorted(trades, key=lambda row: (row['entry_date'], row['source_id']), reverse=True),
            'signals': sorted(signals, key=lambda row: (row['signal_date'], row['source_id']), reverse=True),
            'runs': runs, 'warnings': warnings}
