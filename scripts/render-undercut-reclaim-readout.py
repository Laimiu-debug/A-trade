"""Render a reviewed, compact Sources receipt from the completed account readout."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/strong-undercut-reclaim-timing-v1'
METHOD = 'service/research_undercut_reclaim.py'


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
assert c['newSetupMechanisms'] == 1 and c['newPolicyVariants'] == 3 and c['newAccounts'] == 6
assert len(r['opportunityRows']) == sum(v['opportunityCount'] for v in r['inputCountsByPeriod'].values())
assert len(r['comparisons']) == 4
assert sha(ROOT / METHOD) == plan['sourcePins'][METHOD]
labels = {'immediate': 'I｜支撑收回后立即决策', 'delay2': 'D｜无条件等待两日',
          'confirm2': 'C｜等待两日且守住支撑'}
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

for row in r['coverageRows']:
    coverage.append({**row, 'policy': labels[row['policy']],
        **{'confirmation' + state.title(): row['confirmationStates'].get(state, 0)
           for state in ('passed', 'failed', 'unknown', 'censored')}})
years = [{**row, 'policy': labels[row['policy']]} for row in r['yearRows']]
halfyears = [{**row, 'policy': labels[row['policy']]} for row in r['halfYearRows']]
phases = [{**row, 'policy': labels[row['policy']],
    'formationTop5': row['opportunitiesByFormationDate']['formationTop5'],
    'formationSignals': row['opportunitiesByFormationDate']['selectedSignalsFromFormation'],
    'decisionSignals': row['opportunitiesByDecisionDate']['selectedSignals']} for row in r['phaseRows']]
contrasts = [{'period': row['period'], 'priority': '主比较' if row['priority'] == 'primary' else '次比较',
    'contrast': labels[row['candidate']] + ' − ' + labels[row['control']], **row['candidateMinusControl']}
    for row in r['comparisons']]
assert (len(accounts), len(years), len(halfyears), len(phases), len(coverage)) == (6, 12, 24, 48, 6)
limits = [
    '两段历史各从50万元独立起步，收益是各自两年累计净收益，包含费用、每边10基点滑点及期末持仓估值；不能拼接成四年账户。',
    '两段历史均已用于研究，3141只存续主板股票样本、原始价格、历史资格与权益资料及模拟成交限制继续保留，不能据此确认未来稳定收益。',
    'T−1时使用完整61日价格构造60日相对强度排名，要求排名至少0.80且自身60日收益为正；支撑是此前20日最低价。T日最低价严格跌破支撑且收盘严格收回。',
    '该形态不证明吸筹、洗盘、主力控盘或未来总龙头身份，也没有假设可在当天最低价成交。所有买入都在后续模拟开盘尝试。',
    '各日候选按T−1已知的20日平均成交额排序，冻结前五名和排名。三组复用同一形成集合，之后没有重新排名或补位。',
    'I在T收盘决策；D无条件等到T+2收盘；C也在T+2收盘，但要求T+1、T+2均有完整有效资料且收盘不低于冻结支撑。未知优先于另一日已知失败。',
    '主比较I减D，次比较C减D；前者比较等待政策，后者比较相同等待时间下的额外确认。资金、持仓、容量和成交资格会联动变化，不能解释成独立的因果时点收益。',
    '最后两日形成的机会保留I，D和C标为截尾；不删除这些机会凑成共同成熟账户。最后一天决策即使没有下一开盘仍保留账户机会。',
    '形成日、实际信号决策日、买入日和卖出日分别保留；形成阶段的机会可能在下一阶段成交。阶段盈亏来自净值曲线，包含此前持仓，不能用当阶段闭仓交易相加替代。',
    '重复形态、同日市场与重叠持仓使观察互相依赖。一种新形成机制、三种新账户政策全部计入研究次数，未设盈利门槛或选择最优时点。',
    '市场阶段、情绪及代表股是事后描述，只用于完整归因，从未进入信号筛选。期末持仓估值不等于已实现盈利。',
]


def source(definitions):
    count = len(r['opportunityRows'])
    return {'label': '先前强势股票支撑收回与三种入场时点研究',
        'files': [{'label': name} for name in ('plan.json', 'input-completion.json', 'comparison.json',
            'readout.json', 'validation.json', 'research_undercut_reclaim.py', 'market-phase-atlas-v1/atlas.json')],
        'metricDefinitions': [{'label': label, 'definition': definition} for label, definition in definitions],
        'filters': [f'两期全部{count}个已冻结原始Top5形成机会', '使用完整3141只股票来源，不从后来龙头名单倒推更早买点',
            '形成、排名及支撑均按信号时可见数据；不根据以后涨幅挑选事件',
            'I：T收盘决策，尝试T+1开盘', 'D：T+2收盘决策，尝试T+3开盘',
            'C：T+2收盘决策且前两日收盘均守住支撑，尝试T+3开盘',
            '各账户使用相同退出和成本规则；退出时钟从实际买入开始'],
        'caveats': limits, 'executedAt': c['completedAt'],
        'evidenceFlow': [{'kind': 'validation', 'title': '完整形成与账户复核',
            'detail': '独立验证器复核原始数据绑定、事前强度排名、支撑形成、全部日候选与Top5、三种决策时钟、未知和截尾、六个完整账户。汇总再核对形成ID到信号、买入与闭仓记录的身份连接。'},
            {'kind': 'validation', 'title': '完整阶段对账',
             'detail': '16个冻结阶段覆盖两期全部目标交易日，每日只归属一个阶段。48个策略阶段的盈亏额逐账户相加、收益率复利连接，与完整账户一致；形成日和决策日机会分别对账。12条年度与24条半年度记录全部保留。'}]}


def query(key, rows, columns, src, method=None):
    value = {'id': key, 'reportingPeriod': '2022—2023与2024—2025，各自独立账户',
        'rows': [{field: row[field] for field, _ in columns} for row in rows],
        'columns': [{'field': field, 'label': label} for field, label in columns], 'source': src}
    if method:
        value['methods'] = [{'language': 'python', 'code': method}]
    return value


method = (ROOT / METHOD).read_text('utf-8')
account_source = source([('账户收益', '累计净收益包含现金、已实现盈亏和期末仍持有股票估值，按完整账户路径计算。'),
    ('交易分布', '平均盈利和平均亏损分别取完整平仓交易中的正、负净回报，未平仓股票单独保留。'),
    ('主次比较', '主比较I减D，次比较C减D；收益、回撤与平均仓位之差的单位为百分点。回撤下降不单独证明信号质量改善。')])
phase_source = source([('阶段收益', '从阶段开始前一交易日账户净值计算到阶段末，包含此前持仓在本阶段的净值变化；没有前一日则使用初始50万元。'),
    ('原始形成机会', '按formationDate分组，是尚未读取未来确认、持仓状态之前冻结的Top5。'),
    ('两日确认状态', '三组展示同一原始形成集合的确认通过、失败、未知与观察截尾数；I和D不会因此过滤，C只使用确认通过的信号。'),
    ('实际决策机会', '按各政策signalDate分组；确认未通过、未知或截尾时可能没有信号，但原始形成仍保留。'),
    ('成交终态', '实际入场和未入场状态以完整账户截止日为准，不表示这些结果在形成阶段末已经可知。')])
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
            ('returnPct', '阶段收益（%）'), ('maxDrawdownPct', '阶段最大回撤（%）'), ('netPnlAmount', '阶段净盈亏（元）'), ('formationTop5', '本阶段形成数'),
            ('formationSignals', '由本阶段形成的信号'), ('decisionSignals', '本阶段实际决策数')], phase_source),
        query('coverage', coverage, identity + [('formationTop5', '原始形成机会'),
            ('confirmationPassed', '两日确认通过'), ('confirmationFailed', '两日确认失败'),
            ('confirmationUnknown', '两日确认未知'), ('confirmationCensored', '两日观察截尾'),
            ('selectedSignals', '实际决策信号'), ('entered', '实际入场'),
            ('notEntered', '决策后未入场'), ('notScheduledFromFormation', '形成后未发信号')], phase_source, method)]},
]}
serialized = json.dumps(receipt, ensure_ascii=False)
assert 'G:/' not in serialized and 'C:/' not in serialized and len(serialized.encode()) < 2_000_000
write('sources.json', receipt)
(OUT / 'inline').mkdir(exist_ok=False)
node = 'C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
renderer = Path('C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts/render-inline-sources.mjs')
fragment = OUT / 'inline/undercut-reclaim-sources.html'
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
