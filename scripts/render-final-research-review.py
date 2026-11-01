"""Render the selected experimental families from stored, reviewed outcomes."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/final-research-review-v1'
BASE = ROOT / 'artifacts/strategy-search'
NODE = Path('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
RENDER = Path('C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(name, value):
    with (OUT / name).open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    old_path = BASE / 'contraction-policy-screen-v1/readout.json'
    new_path = BASE / 'supplemental-force-context-screen-v1/baseline10.summary.json'
    leader_path = BASE / 'leader-persistence-admission-v1/readout.json'
    reclaim_path = BASE / 'reclaim-volatility-trail-v1/readout.json'
    old = next(r for r in read(old_path)['rows'] if r['id'] == 'noProgress5')
    new = read(new_path)
    labels = {'main': '主板', 'chinext': '创业板', 'star': '科创板'}
    rows = []

    def add(family, short, period, board, metrics):
        rows.append({'strategy': family, 'category': short + '·' + labels[board], 'period': period,
                     'board': labels[board], 'netReturnPct': metrics['accountReturnPct'],
                     'maxDrawdownPct': metrics['maxDrawdownPct'], 'closedTrades': metrics['closed'],
                     'profitFactor': metrics['profitFactor']})

    add('收缩突破·停滞退出', '收缩', '2022–2023', 'main', old)
    add('收缩突破·停滞退出', '收缩', '2024–2025', 'main', {
        'accountReturnPct': new['account']['netReturnPct'],
        'maxDrawdownPct': new['account']['maxDrawdownPct'],
        'closed': new['closedEpisodes']['count'], 'profitFactor': new['closedEpisodes']['profitFactor']})
    for row in read(leader_path)['accountRows']:
        if row['policy'] == 'persistent':
            add('持续强势·再次突破', '强势', row['period'].replace('-', '–'), row['board'], row['metrics'])
    for row in read(reclaim_path)['accountRows']:
        if row['policy'] == 'volatilityTrail':
            add('支撑收回·波动跟踪', '收回', row['period'].replace('-', '–'), row['board'], row)
    assert len(rows) == 14 and len({(r['category'], r['period']) for r in rows}) == 14
    caveats = [
        '全部数值来自已经反复查看的2022至2025年开发数据，不是未使用的样本外验证，也不表示未来收益。',
        '每个值为独立两年模拟账户的累计净收益，包含期末未平仓市值；不是年化收益、单笔收益或三策略组合收益。',
        '收缩策略仅有主板证据；其他两组覆盖主板、创业板、科创板。固定样本、费用沿革和持仓暴露不同，不能据图建立同股票池因果排名。',
        '原始价格、历史状态和模拟开盘成交的限制仍然存在；全部原结果及估值标记保留。',
        '持续强势策略部分盈利账户交易稀疏；支撑收回策略旧期三板均亏。入选只表示保留实验价值，没有策略获认证。',
    ]
    source = {'label': '三组实验的已保存账户结果',
              'files': [{'label': p.relative_to(ROOT).as_posix()} for p in [old_path, new_path, leader_path, reclaim_path]],
              'metricDefinitions': [{'definition': '累计净收益按各账户原始初始资金和期末权益计算，沿用原账户已计入的费用与滑点。'},
                                    {'definition': '最大回撤来自各账户完整逐日权益，闭仓笔数不包含期末仍持有的仓位。'}],
              'caveats': caveats}
    columns = [{'field': 'strategy', 'label': '实验策略'}, {'field': 'board', 'label': '板块'},
               {'field': 'period', 'label': '区间'}, {'field': 'netReturnPct', 'label': '累计净收益（%）'},
               {'field': 'maxDrawdownPct', 'label': '最大回撤（%）'}, {'field': 'closedTrades', 'label': '闭仓笔数'},
               {'field': 'profitFactor', 'label': '盈利因子'}]
    # Keep original evidence rows intact; only the category's display alias has
    # a word boundary so the native vertical axis can wrap strategy and board.
    chart_rows = [{**row, 'displayCategory': row['category'].replace('·', ' ').replace('创业板', '创业').replace('科创板', '科创')} for row in rows]
    chart = {'schemaVersion': 1, 'id': 'selected-experimental-strategies', 'queryId': 'selected-accounts',
             'title': '三组实验的历史累计收益',
             'description': '固定历史样本，结果依赖市场阶段；收缩策略仅测试主板。',
             'chart': {'type': 'bar', 'x': 'displayCategory', 'y': 'netReturnPct', 'series': 'period',
                       'showXAxisLabel': False, 'yLabel': '累计净收益（%）', 'showYAxisLabel': True,
                       'xTickLabelLayout': 'wrapped', 'showValues': False, 'stackable': False},
             'rows': chart_rows, 'columns': ['category', 'displayCategory', *(c['field'] for c in columns)], 'source': source, 'height': 440,
             'theme': 'codex-classic', 'generatedAt': datetime.now(timezone.utc).isoformat()}
    write('selected-chart.json', chart)
    write('selected-sources.json', {'schemaVersion': 1, 'items': [{
        'id': 'selected-experimental-evidence', 'title': '三组实验的完整账户对照',
        'queries': [{'id': 'selected-accounts', 'source': source, 'rows': rows,
                     'columns': columns, 'reportingPeriod': '2022–2023 与 2024–2025；各自独立两年账户',
                     'preview': {'kind': 'aggregate', 'note': '保留三组策略所有已研究的时期与板块，未只展示盈利分组。', 'totalRows': 14}}]}]})
    logs = []
    for kind, name in [('chart', 'selected-chart'), ('sources', 'selected-sources')]:
        command = [str(NODE), str(RENDER / ('render-inline-' + kind + '.mjs')),
                   '--input', str(OUT / (name + '.json')), '--output', str(OUT / (name + '.html'))]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', check=True)
        logs.append({'kind': kind, 'stdout': result.stdout, 'stderr': result.stderr})
    write('selected-render-receipt.json', {'passed': True, 'createdAt': datetime.now(timezone.utc).isoformat(),
        'sourceRows': len(rows), 'inputPins': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                             for p in [old_path, new_path, leader_path, reclaim_path]}, 'logs': logs})
    print(json.dumps({'passed': True, 'sourceRows': len(rows), 'output': str(OUT)}))


if __name__ == '__main__':
    main()
