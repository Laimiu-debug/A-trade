"""Render a reviewed, compact Sources receipt from the completed account readout."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/peer-amount-persistence-v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(name):
    return json.loads((OUT / name).read_text('utf-8'))


def write(name, value):
    with (OUT / name).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


v, c, r = read('validation.json'), read('comparison.json'), read('readout.json')
assert v['passed'] and r['verification']['passed'] and len(c['rows']) == 4
pins = {name: sha(OUT / name) for name in ('plan.json', 'input-completion.json', 'comparison.json', 'validation.json', 'readout.json')}
labels = {a['policy']: a['label'] for a in r['accounts']}
accounts = []
coverage = []
for a in r['accounts']:
    row = a['comparisonRow']
    closed = a['curveAttribution']['closedEpisodesByExitDate']
    accounts.append({'period': a['period'], 'policy': a['label'],
        **{k: row[k] for k in ('signals', 'closed', 'accountReturnPct', 'maxDrawdownPct',
            'winRatePct', 'meanWinPct', 'meanLossPct', 'closedMeanPct', 'profitFactor', 'payoffRatio',
            'openPositions', 'meanExposurePct', 'maxConsecutiveLosses', 'top5ShareOfGrossProfitPct')},
        'worstClosedPct': closed['returnPct']['min'], 'p05ClosedPct': closed['returnPct']['p05'],
        'medianHoldingSessions': row['holdingSessions']['median']})
    counts = a['curveAttribution']['opportunitiesBySignalDate']
    coverage.append({'period': a['period'], 'policy': a['label'], **counts})
years = [{**row, 'policy': labels[row['policy']]} for row in r['yearRows']]
phases = [{**row, 'policy': labels[row['policy']]} for row in r['phaseRows']]
assert (len(accounts), len(years), len(phases), len(coverage)) == (4, 8, 32, 4)
limits = [
    '两段历史各从50万元独立起步，收益是各自两年累计净收益，包含费用、每边10基点滑点和期末持仓估值；不能拼接成四年账户。',
    '两段历史均已用于研究，3141只存续主板股票样本、历史资格与权益资料及模拟成交的原限制继续保留，不能据此确认未来稳定收益。',
    '成交额份额描述交易活跃度，不代表主力净流入或控盘；相关性同伴也不是已认证的题材成员。',
    '群体在信号前10个交易日固定；分母使用截至信号日完整覆盖30天的同一组股票，组内任何成员缺失时两种规则均不产生信号。',
    '持续性筛选会改变现金、持仓和后续实际成交，所见差异属于整体策略路径，不能认定为独立成交额因子的因果效应。',
    '市场阶段、情绪及代表股是事后历史描述，仅用于完整归因，从未用于生成本轮信号。',
]


def source(definitions):
    return {'label': '固定同伴成交额持续性研究',
        'files': [{'label': name} for name in ('plan.json', 'input-completion.json', 'comparison.json',
            'readout.json', 'validation.json', 'research_peer_amount_share.py', 'market-phase-atlas-v1/atlas.json')],
        'metricDefinitions': [{'label': label, 'definition': definition} for label, definition in definitions],
        'filters': ['两期全部397个原始temporal Top5机会', '固定20个同伴，不含买入的锚点股票',
            '当日份额高于此前20日均值', '候选额外要求最近10日中至少8日严格高于同一均值',
            '两组使用相同完整性口径，过滤后不递补', '买卖与资金规则相同，仅新增持续性条件'],
        'caveats': limits, 'executedAt': c['completedAt'],
        'evidenceFlow': [{'kind': 'validation', 'title': '完整输入与账户复核',
            'detail': '独立验证器从保存的原始金额和账户缓存重算完整分母、份额、名单及新时期同伴相关性，并复核四个账户的成交、费用、现金、退出和统计。旧时期同伴精确复用原已核验档案；形成日原判定抽取及原始数据身份继承已绑定的来源证明，未重新拉取行情。'},
            {'kind': 'validation', 'title': '完整阶段对账',
             'detail': '16个已冻结阶段覆盖全部969个目标交易日，每日只归属一个阶段；32个策略阶段结果的盈亏额逐账户相加、收益率复利连接，均与各自完整账户一致。'}]}


def query(key, rows, columns, src, method=None):
    value = {'id': key, 'reportingPeriod': '2022—2023与2024—2025，各自独立账户',
        'rows': [{field: row[field] for field, _ in columns} for row in rows],
        'columns': [{'field': field, 'label': label} for field, label in columns], 'source': src}
    if method:
        value['methods'] = [{'language': 'python', 'code': method}]
    return value


code = (ROOT / 'service/research_peer_amount_share.py').read_text('utf-8')
method = '\n\n'.join(ast.get_source_segment(code, node) for node in ast.parse(code).body
    if isinstance(node, ast.FunctionDef) and node.name in ('build_balanced_market', 'evaluate_peer_amount_share'))
account_source = source([('账户收益', '累计净收益包含现金、已实现盈亏和期末仍持有股票的估值，按完整账户路径计算。'),
    ('交易分布', '平均盈利和平均亏损分别取完整平仓交易中的正、负净回报，未平仓股票单独保留。')])
phase_source = source([('阶段收益', '从阶段开始前一交易日账户净值计算到阶段末，包含此前持仓在本阶段的净值变化。'),
    ('机会统计', '全部原始Top5机会保留，记录筛选、数据未知、实际入场与最终未入场状态；成交终态以账户截止日为准。')])
identity = [('period', '时期'), ('policy', '规则')]
receipt = {'schemaVersion': 1, 'items': [
    {'id': 'account-paths', 'title': '完整账户与交易盈亏', 'queries': [
        query('accounts', accounts, identity + [('accountReturnPct', '累计净收益（%）'), ('maxDrawdownPct', '最大回撤（%）'),
            ('closed', '已平仓'), ('openPositions', '期末持仓'), ('meanWinPct', '平均盈利（%）'), ('meanLossPct', '平均亏损（%）'),
            ('closedMeanPct', '平仓平均净收益（%）'), ('profitFactor', '利润因子'), ('meanExposurePct', '平均仓位（%）')], account_source),
        query('loss-tails', accounts, identity + [('winRatePct', '平仓胜率（%）'), ('payoffRatio', '平均盈亏比'),
            ('worstClosedPct', '最差平仓（%）'), ('p05ClosedPct', '平仓5分位（%）'), ('medianHoldingSessions', '持仓交易日中位数'),
            ('top5ShareOfGrossProfitPct', '前五笔占总盈利（%）')], account_source)]},
    {'id': 'phase-paths', 'title': '全部年份、市场阶段与机会覆盖', 'queries': [
        query('years', years, identity + [('id', '年份'), ('returnPct', '年度收益（%）'), ('maxDrawdownPct', '年度最大回撤（%）')], phase_source),
        query('phases', phases, identity + [('phaseTitle', '阶段'), ('requestedStart', '开始'), ('requestedEnd', '结束'),
            ('returnPct', '阶段收益（%）'), ('maxDrawdownPct', '阶段最大回撤（%）'), ('netPnlAmount', '阶段净盈亏（元）')], phase_source),
        query('coverage', coverage, identity + [('originalTemporalTop5', '原始机会'), ('amountUnknown', '金额口径未知'),
            ('selectedSignals', '选中信号'), ('entered', '实际入场'), ('filteredOrUnknown', '过滤或未知')], phase_source, method)]},
]}
serialized = json.dumps(receipt, ensure_ascii=False)
assert 'G:/' not in serialized and 'C:/' not in serialized and len(serialized.encode()) < 2_000_000
write('sources.json', receipt)
(OUT / 'inline').mkdir(exist_ok=False)
node = 'C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
renderer = Path('C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts/render-inline-sources.mjs')
fragment = OUT / 'inline/peer-amount-sources.html'
result = subprocess.run([node, str(renderer), '--input', str(OUT / 'sources.json'), '--output', str(fragment)], text=True, capture_output=True, timeout=60)
write('sources-render-log.json', {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
assert result.returncode == 0, result.stdout + result.stderr
for name, expected in pins.items():
    assert sha(OUT / name) == expected
write('render-receipt.json', {'inputPins': pins, 'builderSha256': sha(Path(__file__)), 'payloadSha256': sha(OUT / 'sources.json'),
    'fragmentSha256': sha(fragment), 'rendererSha256': sha(renderer), 'cards': 2, 'browserChecked': False})
print(json.dumps({'fragmentSha256': sha(fragment), 'bytes': fragment.stat().st_size}))
