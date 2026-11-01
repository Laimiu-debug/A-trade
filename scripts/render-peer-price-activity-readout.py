"""Render a reviewed, compact Sources receipt from the completed account readout."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/peer-price-activity-alignment-v1'
METHOD = 'service/research_peer_activity_alignment.py'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(name):
    return json.loads((OUT / name).read_text('utf-8'))


def write(name, value):
    with (OUT / name).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


v, c, r, plan = read('validation.json'), read('comparison.json'), read('readout.json'), read('plan.json')
assert v['passed'] is True and r['verification']['passed'] is True and len(c['rows']) == 6
pins = {name: sha(OUT / name) for name in ('plan.json', 'input-completion.json', 'comparison.json', 'validation.json', 'readout.json')}
assert v['comparisonSha256'] == pins['comparison.json'] and v['planSha256'] == pins['plan.json']
assert r['verification']['sha256'] == pins['validation.json']
for name in ('plan.json', 'input-completion.json', 'comparison.json', 'validation.json'):
    assert r['inputPins'][(OUT / name).relative_to(ROOT).as_posix()] == pins[name]
assert r['accountRows'] == c['rows']
assert c['newCandidateVariants'] == 2 and c['newAccounts'] == 6
assert len(r['parentBaselineParity']) == 2 and all(row['fullResultExactlyEqual'] is True for row in r['parentBaselineParity'])
assert len(r['opportunityRows']) == 397 and len(r['comparisons']) == 4
assert sha(ROOT / METHOD) == plan['sourcePins'][METHOD]
labels = {'currentActivity': 'C｜当日活跃', 'priceProgress': 'P｜活跃且相对价格推进',
          'activityAligned': 'PC｜推进且量价同步'}
accounts = []
coverage = []
for a in r['accounts']:
    row = a['comparisonRow']
    closed = a['curveAttribution']['closedEpisodesByExitDate']
    accounts.append({'period': a['period'], 'policy': labels[a['policy']],
        **{k: row[k] for k in ('signals', 'closed', 'accountReturnPct', 'maxDrawdownPct',
            'winRatePct', 'meanWinPct', 'meanLossPct', 'closedMeanPct', 'profitFactor', 'payoffRatio',
            'openPositions', 'meanExposurePct', 'maxConsecutiveLosses', 'top5ShareOfGrossProfitPct')},
        'worstClosedPct': closed['returnPct']['min'], 'p05ClosedPct': closed['returnPct']['p05'],
        'medianHoldingSessions': row['holdingSessions']['median']})
    counts = a['curveAttribution']['opportunitiesBySignalDate']
    coverage.append({'period': a['period'], 'policy': labels[a['policy']], **counts})
years = [{**row, 'policy': labels[row['policy']]} for row in r['yearRows']]
halfyears = [{**row, 'policy': labels[row['policy']]} for row in r['halfYearRows']]
phases = [{**row, 'policy': labels[row['policy']]} for row in r['phaseRows']]
contrasts = [{'period': row['period'], 'priority': '主比较' if row['priority'] == 'primary' else '次比较',
    'contrast': labels[row['candidate']] + ' − ' + labels[row['control']], **row['candidateMinusControl']}
    for row in r['comparisons']]
assert (len(accounts), len(years), len(halfyears), len(phases), len(coverage)) == (6, 12, 24, 48, 6)
limits = [
    '两段历史各从50万元独立起步，收益是各自两年累计净收益，包含费用、每边10基点滑点和期末持仓估值；不能拼接成四年账户。',
    '两段历史均已用于研究，3141只存续主板股票样本、历史资格与权益资料及模拟成交的原限制继续保留，不能据此确认未来稳定收益。',
    '成交额份额描述交易活跃度，不代表主力净流入或控盘；相关性同伴也不是已认证的题材成员。',
    '群体在信号前10个交易日固定；成交额分母使用截至信号日完整覆盖30天的同一组股票U(T)，并非形成日F冻结的分母。三组共同要求完整金额及F至T的11日价格资料，缺失保留未知。',
    'P的推进是20个同伴等权日对数收益减去指数收益后的10日合计；相对推进为正时，群体绝对价格仍可能下跌，也不是可成交篮子收益。',
    'PC新增的是10日成交额份额与同日相对收益的中心化协方差严格大于0；金额含成交价格，且金额加总与价格等权的权重不同，不能解释为资金因果推动或主力控盘。',
    '主比较为PC对P，次比较为P对C；两项新增过滤政策都记入试验记录。过滤会改变现金、持仓和后续成交，不能认定为独立因子的因果效应。',
    '三组仍在同一个信号日T决策并尝试下一开盘买入；本轮没有测试更早入场。重复锚点、重叠群体和日期使这些观察互相依赖。',
    '市场阶段、情绪及代表股是事后历史描述，仅用于完整归因，从未用于生成本轮信号。',
]


def source(definitions):
    return {'label': '固定同伴价格与交易活跃同步研究',
        'files': [{'label': name} for name in ('plan.json', 'input-completion.json', 'comparison.json',
            'readout.json', 'validation.json', 'research_peer_activity_alignment.py', 'market-phase-atlas-v1/atlas.json')],
        'metricDefinitions': [{'label': label, 'definition': definition} for label, definition in definitions],
        'filters': ['两期全部397个原始temporal Top5机会', '固定20个同伴，不含买入的锚点股票',
            'C：当日份额高于截至形成日的20日均值', 'P：在C上增加10日群体相对对数收益合计>0',
            'PC：在P上增加份额与同日相对收益的10日中心化协方差>0',
            '三组使用相同完整性口径，原Top5后过滤且不递补', '买卖与资金规则相同；未叠加此前8/10持续条件'],
        'caveats': limits, 'executedAt': c['completedAt'],
        'evidenceFlow': [{'kind': 'validation', 'title': '完整输入与账户复核',
            'detail': '独立验证器复核全部397个原机会、固定同伴与金额证据绑定、11日价格窗口、10日相对推进和协方差、三组完整名单及六个账户的成交与统计。金额分母和原同伴形成继承已核验的上轮冻结来源；未重新拉取行情。两期C完整账户结果还须与上轮基线精确相等。'},
            {'kind': 'validation', 'title': '完整阶段对账',
             'detail': '16个已冻结阶段覆盖全部969个目标交易日，每日只归属一个阶段；48个策略阶段结果的盈亏额逐账户相加、收益率复利连接，均与各自完整账户一致。12条年度和24条半年度记录全部保留。'}]}


def query(key, rows, columns, src, method=None):
    value = {'id': key, 'reportingPeriod': '2022—2023与2024—2025，各自独立账户',
        'rows': [{field: row[field] for field, _ in columns} for row in rows],
        'columns': [{'field': field, 'label': label} for field, label in columns], 'source': src}
    if method:
        value['methods'] = [{'language': 'python', 'code': method}]
    return value


method = (ROOT / METHOD).read_text('utf-8')
account_source = source([('账户收益', '累计净收益包含现金、已实现盈亏和期末仍持有股票的估值，按完整账户路径计算。'),
    ('交易分布', '平均盈利和平均亏损分别取完整平仓交易中的正、负净回报，未平仓股票单独保留。'),
    ('主次比较', '主比较PC减P，次比较P减C；收益、回撤和平均仓位之差的单位为百分点。回撤差为负只说明观察到的回撤下降，不能单独证明信号质量改善。')])
phase_source = source([('阶段收益', '从阶段开始前一交易日账户净值计算到阶段末，包含此前持仓在本阶段的净值变化。'),
    ('机会统计', '全部原始Top5机会保留，记录筛选、数据未知、实际入场与最终未入场状态；成交终态以账户截止日为准。')])
identity = [('period', '时期'), ('policy', '规则')]
receipt = {'schemaVersion': 1, 'items': [
    {'id': 'account-paths', 'title': '六个完整账户与两层比较', 'queries': [
        query('policy-contrasts', contrasts, [('period', '时期'), ('priority', '比较层次'), ('contrast', '差值定义（左减右）')]
            + [('accountReturnPct', '累计净收益差（百分点）'), ('maxDrawdownPct', '最大回撤差（百分点）'),
               ('closedMeanPct', '平仓均值差（百分点）'), ('meanExposurePct', '平均仓位差（百分点）')], account_source),
        query('accounts', accounts, identity + [('accountReturnPct', '累计净收益（%）'), ('maxDrawdownPct', '最大回撤（%）'),
            ('closed', '已平仓'), ('openPositions', '期末持仓'), ('meanWinPct', '平均盈利（%）'), ('meanLossPct', '平均亏损（%）'),
            ('closedMeanPct', '平仓平均净收益（%）'), ('profitFactor', '利润因子'), ('meanExposurePct', '平均仓位（%）')], account_source),
        query('loss-tails', accounts, identity + [('winRatePct', '平仓胜率（%）'), ('payoffRatio', '平均盈亏比'),
            ('worstClosedPct', '最差平仓（%）'), ('p05ClosedPct', '平仓5分位（%）'), ('medianHoldingSessions', '持仓交易日中位数'),
            ('top5ShareOfGrossProfitPct', '前五笔占总盈利（%）')], account_source)]},
    {'id': 'phase-paths', 'title': '全部年份、市场阶段与机会覆盖', 'queries': [
        query('years', years, identity + [('id', '年份'), ('returnPct', '年度收益（%）'), ('maxDrawdownPct', '年度最大回撤（%）')], phase_source),
        query('half-years', halfyears, identity + [('id', '半年'), ('returnPct', '半年度收益（%）'), ('maxDrawdownPct', '半年度最大回撤（%）')], phase_source),
        query('phases', phases, identity + [('phaseTitle', '阶段'), ('requestedStart', '开始'), ('requestedEnd', '结束'),
            ('returnPct', '阶段收益（%）'), ('maxDrawdownPct', '阶段最大回撤（%）'), ('netPnlAmount', '阶段净盈亏（元）')], phase_source),
        query('coverage', coverage, identity + [('originalTemporalTop5', '原始机会'), ('parentAmountUnknown', '原金额口径未知'),
            ('priceAlignmentUnknown', '共同输入未知'), ('selectedSignals', '选中信号'), ('entered', '实际入场'),
            ('notEntered', '选中但未入场'), ('filteredOrUnknown', '过滤或未知')], phase_source, method)]},
]}
serialized = json.dumps(receipt, ensure_ascii=False)
assert 'G:/' not in serialized and 'C:/' not in serialized and len(serialized.encode()) < 2_000_000
write('sources.json', receipt)
(OUT / 'inline').mkdir(exist_ok=False)
node = 'C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
renderer = Path('C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts/render-inline-sources.mjs')
fragment = OUT / 'inline/peer-price-activity-sources.html'
result = subprocess.run([node, str(renderer), '--input', str(OUT / 'sources.json'), '--output', str(fragment)], text=True, capture_output=True, timeout=60)
write('sources-render-log.json', {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
assert result.returncode == 0, result.stdout + result.stderr
for name, expected in pins.items():
    assert sha(OUT / name) == expected
assert sha(ROOT / METHOD) == plan['sourcePins'][METHOD]
write('render-receipt.json', {'inputPins': pins, 'builderSha256': sha(Path(__file__)), 'payloadSha256': sha(OUT / 'sources.json'),
    'fragmentSha256': sha(fragment), 'rendererSha256': sha(renderer), 'methodModuleSha256': sha(ROOT / METHOD),
    'cards': 2, 'accounts': 6, 'years': 12, 'halfYears': 24, 'phases': 48, 'coverage': 6, 'browserChecked': False})
print(json.dumps({'fragmentSha256': sha(fragment), 'bytes': fragment.stat().st_size}))
