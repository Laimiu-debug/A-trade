"""Retrospective comparisons, paired block intervals and a reproducible figure.

Never treats previously inspected history as a pristine holdout, or stitches
independently capitalized periods into one equity curve.
"""
from pathlib import Path
import gzip
import hashlib
import json
import math
import os
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / 'artifacts/strategy-search/profitability-review-20261031'
LABELS = {'complete': '原双路径', 'Bonly': '原回调路径',
          'confirmComplete': '双路径延后确认', 'confirmBonly': '回调延后确认'}


def read(path):
    with (gzip.open if str(path).endswith('.gz') else open)(path, 'rt', encoding='utf-8') as stream:
        return json.load(stream)


def pin(path):
    return {'path': path.relative_to(ROOT).as_posix(),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def daily_returns(result):
    previous = result['config']['initialCapital']
    values = []
    for row in result['curve']:
        values.append(row['equity'] / previous - 1)
        previous = row['equity']
    return np.asarray(values, dtype=float)


def block_interval(a, b):
    delta = daily_returns(a) - daily_returns(b)
    n, block = len(delta), 20
    if n < block:
        return {'available': False, 'reason': 'Fewer than 20 paired sessions'}
    rng = np.random.default_rng(20261031)
    starts = rng.integers(0, n - block + 1, size=(2000, math.ceil(n / block)))
    indices = (starts[:, :, None] + np.arange(block)).reshape(2000, -1)[:, :n]
    means = delta[indices].mean(axis=1) * 10000
    return {'available': True, 'pairedSessions': n, 'blockSessions': block, 'draws': 2000,
            'seed': 20261031, 'meanDailyExcessBps': float(delta.mean() * 10000),
            'percentile95Bps': np.percentile(means, [2.5, 97.5]).tolist(),
            'meaning': 'Retrospective dependent-return resampling; not correction for prior adaptive selection or a forecast.'}


def derive(result, summary):
    curve, trades = result['curve'], result['closedTrades']
    capital = result['config']['initialCapital']
    profit = sum(t['pnlAmount'] for t in trades if t['pnlAmount'] > 0)
    loss = -sum(t['pnlAmount'] for t in trades if t['pnlAmount'] < 0)
    net = (curve[-1]['equity'] / capital - 1) * 100
    assert math.isclose(net, summary['account']['netReturnPct'], abs_tol=1e-8)
    assert math.isclose(sum(t['pnlAmount'] for t in trades), summary['closedEpisodes']['netPnlAmount'], abs_tol=1e-6)
    return {'netReturnPct': net, 'maxDrawdownPct': max(c['drawdownPct'] for c in curve),
            'closed': len(trades), 'profitFactor': profit / loss if loss else None,
            'winRatePct': sum(t['pnlAmount'] > 0 for t in trades) / len(trades) * 100 if trades else None,
            'feesPaid': sum(t['fee'] for t in result['trades']),
            'closedNetPnl': sum(t['pnlAmount'] for t in trades),
            'openMarketValue': curve[-1]['marketValue'],
            'meanExposurePct': float(np.mean([c['marketValue'] / c['equity'] * 100 for c in curve])),
            'benchmarkReturnPct': (curve[-1]['benchmarkEquity'] / capital - 1) * 100 if curve[-1].get('benchmarkEquity') is not None else None,
            'topFiveProfitShare': sum(sorted([t['pnlAmount'] for t in trades if t['pnlAmount'] > 0], reverse=True)[:5]) / profit if profit else None,
            'valuationUnreliableDates': summary['quality']['valuationUnreliableDates']}


def figure(rows, destination):
    os.environ.setdefault('MPLCONFIGDIR', 'H:/codex/cache/matplotlib')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'Microsoft YaHei', 'axes.unicode_minus': False,
                         'font.size': 11, 'axes.spines.top': False, 'axes.spines.right': False})
    ids = list(LABELS)
    colors = ['#64748b', '#2563eb', '#c66b11', '#0c8476']
    vals = [r['metrics']['netReturnPct'] for r in rows if r['cost'] == 'normal']
    spread = max(max(vals), 0) - min(min(vals), 0)
    pad = max(1, spread * .22)
    fig, axes = plt.subplots(1, 2, figsize=(12.8, 5.7), sharey=True)
    for ax, (period, title) in zip(axes, [('development', '2024-09-24 至 2025-12-31'),
                                       ('reusedTemporalCheck', '2026-01-05 至 2026-10-15')]):
        selected = {r['caseId']: r for r in rows if r['period'] == period and r['cost'] == 'normal'}
        net = [selected[i]['metrics']['netReturnPct'] for i in ids]
        bars = ax.bar(range(4), net, color=colors, width=.62)
        ax.axhline(0, color='#64748b', linewidth=.8)
        ax.set_xticks(range(4), [LABELS[i].replace('延后', '\n延后') for i in ids])
        ax.set_title(title, fontsize=12, pad=16)
        ax.set_ylim(min(min(vals), 0) - pad, max(max(vals), 0) + pad)
        ax.set_axisbelow(True)
        ax.grid(axis='y', alpha=.18)
        for bar, ident, value in zip(bars, ids, net):
            count = selected[ident]['metrics']['closed']
            ax.annotate(f'{value:+.2f}%\n{count} 笔闭仓', (bar.get_x() + bar.get_width()/2, value),
                        xytext=(0, 5 if value >= 0 else -5), textcoords='offset points',
                        ha='center', va='bottom' if value >= 0 else 'top', fontsize=10)
    axes[0].set_ylabel('费用后账户总收益（%）')
    fig.suptitle('延后确认是否改善亏损：两段已使用历史的复验', fontsize=16, y=.98)
    fig.text(.5, .025, '各期独立以 50 万元开始 · 包含费用与单边 10 bps 滑点\n两段历史均已暴露，不能视为干净样本外，也不拼接为连续收益', ha='center', fontsize=10, color='#475569')
    fig.subplots_adjust(left=.085, right=.98, top=.84, bottom=.22, wspace=.15)
    fig.savefig(destination, dpi=160)
    plt.close(fig)


def main():
    plan = read(STUDY / 'hypothesis-plan.json')
    accounts = STUDY / 'experiment/accounts'
    rows, results = [], {}
    for period in plan['periods']:
        for ident in LABELS:
            for cost in ('normal', 'stress'):
                folder = accounts / period / ident / cost
                if cost == 'stress' and not (period == 'reusedTemporalCheck' and ident.startswith('confirm')):
                    continue
                assert (folder / 'completion.json').is_file(), str(folder)
                completion = read(folder / 'completion.json')
                assert completion['independentCashAuditPassed'] is True
                for filename in ('result.json.gz', 'summary.json'):
                    assert pin(folder / filename) == completion['artifacts'][filename]
                result = read(folder / 'result.json.gz')
                summary = read(folder / 'summary.json')
                assert result['status'] == 'completed'
                assert not summary['quality']['integrityIssues']
                assert result['config']['endDate'] <= '2026-10-15'
                row = {'period': period, 'caseId': ident, 'label': LABELS[ident], 'cost': cost,
                       'metrics': derive(result, summary), 'source': pin(folder / 'result.json.gz')}
                rows.append(row)
                results[period, ident, cost] = result
    index = {(r['period'], r['caseId'], r['cost']): r for r in rows}
    verdicts, comparisons = [], []
    for ident, parent in [('confirmComplete', 'complete'), ('confirmBonly', 'Bonly')]:
        checks = []
        for period in plan['periods']:
            candidate = index[period, ident, 'normal']['metrics']
            baseline = index[period, parent, 'normal']['metrics']
            conditions = {'positiveNet': candidate['netReturnPct'] > 0,
                          'beatsMatchedParent': candidate['netReturnPct'] > baseline['netReturnPct'],
                          'profitFactorAboveOne': candidate['profitFactor'] is not None and candidate['profitFactor'] > 1,
                          'closedAtLeast30': candidate['closed'] >= 30,
                          'drawdownAtMost20': candidate['maxDrawdownPct'] <= 20}
            checks.append({'period': period, **conditions})
            a, b = results[period, ident, 'normal'], results[period, parent, 'normal']
            assert [r['date'] for r in a['curve']] == [r['date'] for r in b['curve']]
            comparisons.append({'period': period, 'caseId': ident, 'parent': parent,
                                'netImprovementPp': candidate['netReturnPct'] - baseline['netReturnPct'],
                                'blockBootstrap': block_interval(a, b)})
        stress = index['reusedTemporalCheck', ident, 'stress']['metrics']['netReturnPct'] > 0
        passed = all(all(v for k, v in row.items() if k != 'period') for row in checks) and stress
        verdicts.append({'caseId': ident, 'checks': checks, 'positiveStressNet': stress,
                         'historicalScreenPassed': passed, 'promotedForLiveUse': False,
                         'pristineOosValidated': False})
    output = {'schema': 'dual-confirmation-reviewed-readout-v1', 'plan': pin(STUDY / 'hypothesis-plan.json'),
              'analysisCode': pin(Path(__file__).resolve()),
              'rows': rows, 'comparisons': comparisons, 'decisions': verdicts,
              'historicalScreenPassCount': sum(v['historicalScreenPassed'] for v in verdicts),
              'profitabilityProven': False, 'pristineOosPerformed': False,
              'oosStatus': plan['oos'], 'periodsAreIndependentAccounts': True,
              'limitations': plan['limits']}
    (STUDY / 'readout.json').write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    figure(rows, STUDY / 'historical-comparison.png')
    print(json.dumps({'accounts': len(rows), 'historicalScreenPassCount': output['historicalScreenPassCount'],
                      'rows': [{'period': r['period'], 'caseId': r['caseId'], 'cost': r['cost'], **r['metrics']} for r in rows]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
