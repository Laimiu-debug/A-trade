"""Render an audited formation-environment diagnostic; never run accounts.

Three board-specific shared charts retain every period, exit and state.  The
synthetic test exercises presentation only and never opens historical outcomes.
"""
from argparse import ArgumentParser
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/reclaim-relative-feedback-context-v1'
NODE = 'C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
TOOLS = Path('C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts')
PERIODS = ('2022-2023', '2024-2025')
YEARS = {'2022-2023': ('2022', '2023'), '2024-2025': ('2024', '2025')}
BOARDS = ('main', 'chinext', 'star')
BOARD_LABELS = dict(zip(BOARDS, ('主板', '创业板', '科创板')))
POLICIES = ('trail10', 'volatilityTrail')
POLICY_LABELS = dict(zip(POLICIES, ('固定跟踪', '波动跟踪')))
STATES = ('stronger', 'weaker', 'equal', 'unknown')
STATE_LABELS = dict(zip(STATES, ('强队列正反馈占比更高', '强队列正反馈占比更低',
    '强弱队列正反馈占比相同', '证据不足')))
STATE_SHORT = dict(zip(STATES, ('高', '低', '同', '未知')))
EPOCHS = {'2022-2023': ('oldPeriod',), '2024-2025': ('before20240924', 'from20240924')}
EPOCH_LABELS = {'oldPeriod': '2022—2023', 'before20240924': '2024·9·24之前', 'from20240924': '2024·9·24日起'}
METHOD = 'service/research_relative_feedback_context.py'
RUNNER = 'scripts/diagnose-reclaim-relative-feedback-context.py'
VERIFIER = 'scripts/verify-reclaim-relative-feedback-context.py'
VALUE = '已闭仓贡献（初始资金百分点）'
DELIVERY_ORDER = [key for board in BOARDS for key in (board + 'Chart', board + 'Sources')]
TABLE_ROWS = [[16, 8, 4, 4, 2, 32, 24, 4, 16]]
MATRIX_COUNTS = {'overallRows': 48, 'comparisonRows': 24, 'accountReconciliation': 12,
    'qualityRows': 12, 'contextDayCounts': 6, 'formationYearRows': 96,
    'epochStateRows': 72, 'curveEpochRows': 12}
COUNTS = ('formationCount', 'entered', 'closed', 'open', 'notEntered', 'uniqueFormationDays', 'uniqueSymbols')
MONEY_FIELDS = [('closedPnlAmount', '已闭仓净盈亏（元）'),
    ('openRealizedPnlAmount', '未平仓已实现盈亏（元）'),
    ('openUnrealizedPnlAmount', '未平仓未实现盈亏（元）'),
    ('totalPnlAmount', '形成归属合计（元）')]
STATE_FIELDS = [('population', '形成 / 入场 / 闭仓 / 未平 / 未入场')] + MONEY_FIELDS
LIMITS = [
    '图中为实际已闭仓净盈亏除以原账户初始资金的贡献，按形成时环境归属；不是只交易该环境后重跑的收益，也不是单笔信号的平均回报。',
    '强队列正反馈占比更高只表示上涨成员占比高于弱队列，不等于强队列多数上涨或强队列价格必然上涨；相同状态与证据不足分别保留。',
    '原形成日为T，反馈截至A=T−1；在F=T−6按同板当时有效人口的20日相对强度固定顶、底十分位队列，反馈是F至A五个交易日绝对收盘价格变化。',
    '未知成员仍留在固定队列人数中；按上涨比例上下界严格判定两队列之差，无法确定正、负或精确为零时保留证据不足，不追加覆盖率门槛。',
    '图中零贡献包含没有闭仓的空组，不是零收益交易；完整形成、安排、入场、闭仓、未平与未入场分母均保留，未平仓已实现及未实现盈亏单列。',
    '已闭仓均值和贡献受实际成交集合、资金占用、退出时点及期末删失影响；两种退出共享原始信号，但实际交易路径可不同，差额不作因果解释。',
    '整笔交易和期末持仓按原形成日归属，可跨越环境及9月24日分界；连续净值区间另列，保持进入权益和携带持仓，不重启账户。',
    '估值不可靠、陈旧标记、退出窗口未知及未分配权益差额延续原账本口径；长期无法估值或无法触发退出的持仓不能解释为趋势成功。',
    '全部12个原账户、两时期、三板块、四状态及全部年份均保留；每账户独立起资，两时期和三板块不拼接，板块不等于科技行业。',
    '三张图各自按本板块取值设置横轴范围，跨板块比较应读取百分点数值，不能直接比较不同图中的柱长。',
    '本轮没有新账户、准入筛选、退出规则或收益门槛；只分析已用于多轮研究的2022—2025历史，2026未纳入，不是留出检验，日线反馈不能识别主力或量化交易者身份。',
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def write(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def labels(row):
    result = deepcopy(row)
    result['boardLabel'] = BOARD_LABELS[row['board']]
    if 'policy' in row:
        result['policyLabel'] = POLICY_LABELS[row['policy']]
    if 'state' in row:
        result['stateLabel'] = STATE_LABELS[row['state']]
    if 'formationEpoch' in row:
        result['epochLabel'] = EPOCH_LABELS[row['formationEpoch']]
    if 'phaseId' in row:
        result['epochLabel'] = EPOCH_LABELS[row['phaseId']]
    if all(k in row for k in ('formationCount', 'entered', 'closed', 'open', 'notEntered')):
        result['population'] = ' / '.join(str(row[k]) for k in ('formationCount', 'entered', 'closed', 'open', 'notEntered'))
    return result


def exact_matrix(rows, fields, expected, name):
    keys = [tuple(row[key] for key in fields) for row in rows]
    require(len(set(keys)) == len(keys) and set(keys) == expected,
            'Incomplete/duplicate ' + name + ' identities')


def validate_tables(data):
    for name, count in MATRIX_COUNTS.items():
        require(isinstance(data.get(name), list) and len(data[name]) == count,
                'Complete matrix required: ' + name)
    exact_matrix(data['overallRows'], ('period', 'board', 'policy', 'state'),
        {(p, b, m, s) for p in PERIODS for b in BOARDS for m in POLICIES for s in STATES}, 'overall')
    exact_matrix(data['formationYearRows'], ('period', 'board', 'policy', 'formationYear', 'state'),
        {(p, b, m, y, s) for p in PERIODS for b in BOARDS for m in POLICIES for y in YEARS[p] for s in STATES}, 'year')
    exact_matrix(data['epochStateRows'], ('period', 'board', 'policy', 'formationEpoch', 'state'),
        {(p, b, m, e, s) for p in PERIODS for b in BOARDS for m in POLICIES for e in EPOCHS[p] for s in STATES}, 'formation epochs')
    exact_matrix(data['comparisonRows'], ('period', 'board', 'state'),
        {(p, b, s) for p in PERIODS for b in BOARDS for s in STATES}, 'policy differences')
    for table in ('accountReconciliation', 'qualityRows'):
        exact_matrix(data[table], ('period', 'board', 'policy'),
            {(p, b, m) for p in PERIODS for b in BOARDS for m in POLICIES}, table)
    exact_matrix(data['contextDayCounts'], ('period', 'board'),
        {(p, b) for p in PERIODS for b in BOARDS}, 'daily coverage')
    exact_matrix(data['curveEpochRows'], ('period', 'board', 'policy', 'phaseId'),
        {(PERIODS[1], b, m, e) for b in BOARDS for m in POLICIES for e in ('before20240924', 'from20240924')}, 'continuous curve epochs')
    for table in ('overallRows', 'formationYearRows', 'epochStateRows'):
        for row in data[table]:
            require(row['state'] in STATES, 'Unknown state identity')
            require(all(type(row[k]) is int and row[k] >= 0 for k in COUNTS), 'Invalid population count')
            require(row['formationCount'] == row['entered'] + row['notEntered']
                and row['entered'] == row['closed'] + row['open'], 'Opportunity populations do not conserve')
            for field in (*[k for k, _ in MONEY_FIELDS], 'closedContributionPpOfInitialCapital', 'totalContributionPpOfInitialCapital'):
                require(finite(row[field]), 'Nonfinite money contribution: ' + field)
            require(math.isclose(row['closedContributionPpOfInitialCapital'], row['closedPnlAmount'] / 500000 * 100,
                    rel_tol=1e-10, abs_tol=1e-9), 'Closed contribution denominator differs')
            require(math.isclose(row['totalPnlAmount'], math.fsum(row[k] for k in
                    ('closedPnlAmount', 'openRealizedPnlAmount', 'openUnrealizedPnlAmount')),
                    rel_tol=1e-10, abs_tol=1e-8), 'State money does not reconcile')
    for row in data['comparisonRows']:
        require((row['control'], row['candidate']) == POLICIES, 'Policy difference orientation changed')
    for row in data['contextDayCounts']:
        require(set(row['dayCountByState']) == set(row['opportunityCountByState']) == set(STATES)
            and sum(row['dayCountByState'].values()) == row['targetDays']
            and sum(row['opportunityCountByState'].values()) == row['formationCount'], 'Daily state counts incomplete')
    require(sum(row['formationCount'] for row in data['contextDayCounts']) == data['formationCount']
        and sum(row['formationCount'] for row in data['overallRows']) == data['recordCount']
        and data['recordCount'] == 2 * data['formationCount'], 'Original opportunity population changed')
    for row in data['accountReconciliation']:
        require(row['passed'] is True and math.isclose(row['assignedPnlAmount'] + row['unallocatedResidual'],
            row['accountNetPnlAmount'], rel_tol=1e-10, abs_tol=1e-7), 'Account residual bridge differs')
    return MATRIX_COUNTS


def payloads(data, method_text):
    validate_tables(data)
    result = {}
    for board in BOARDS:
        selected = {name: [labels(row) for row in data[name] if row['board'] == board] for name in MATRIX_COUNTS}
        quality = {(row['period'], row['policy']): row for row in selected['qualityRows']}
        src = {'label': BOARD_LABELS[board] + '强弱队列反馈与既有退出账户归因',
            'files': [{'label': name} for name in ('plan.json', 'input-completion.json', 'readout.json', 'validation.json',
                'records.jsonl.gz', METHOD, RUNNER, 'reclaim-volatility-trail-v1/readout.json',
                'reclaim-volatility-trail-v1/opportunity-dispositions.jsonl.gz')],
            'metricDefinitions': [
                {'label': '已闭仓贡献', 'definition': '已闭仓净盈亏按原形成时环境归属，除以原初始50万元乘100，单位为百分点；图中包含无闭仓的零贡献组。'},
                {'label': '反馈差', 'definition': '比较此前固定强、弱队列在已过去五个交易日中收盘上涨的成员比例；高、低、同、未知依次表示严格更高、更低、精确相同和证据不足。'},
                {'label': '完整机会', 'definition': '形成机会全部沿用原立即入场信号；数量列依次为形成、入场、闭仓、期末未平和未入场，未入场不是零收益交易。'},
                {'label': '资金桥', 'definition': '已闭仓与未平仓的两类盈亏分开归属，未分配权益残差单列；连续曲线另按实际日期区间继承原权益和持仓。'}],
            'filters': ['板块：' + BOARD_LABELS[board], '保留两历史时期、两种退出和全部四种环境状态',
                '所有形成日使用前一源交易日已知的反馈，未加入新的交易筛选', '全年与9月24日前后分组全部保留'],
            'caveats': LIMITS,
            'evidenceFlow': [{'kind': 'validation', 'title': '原机会、环境和账本归因核验',
                'detail': '独立审计逐日重算固定队列和整数差值边界，复核全部原机会的两种终态、闭仓及期末持仓归属、四状态完整矩阵、权益残差和原连续曲线。'}]}
        def query(key, rows, columns, include_method=False):
            require(len(columns) <= 8 and all(all(k in row for k, _ in columns) for row in rows), 'Preview projection differs: ' + key)
            item = {'id': board + '-' + key, 'rows': [{k: row[k] for k, _ in columns} for row in rows],
                'columns': [{'field': k, 'label': label} for k, label in columns], 'source': src,
                'reportingPeriod': '2022—2023与2024—2025；形成时环境归因，非筛选账户'}
            if include_method:
                item['methods'] = [{'language': 'python', 'code': method_text}]
            return item

        comparisons = []
        for row in selected['comparisonRows']:
            comparisons.append({**row, 'populationDelta': ' / '.join(str(row[k + 'Delta']) for k in ('entered', 'closed', 'open', 'notEntered'))})
        qualities = [{**row, 'qualityFlags': encoded(row['sourceQualityScalarFlags'])} for row in selected['qualityRows']]
        days = [{**row, 'dayStates': encoded(row['dayCountByState']), 'opportunityStates': encoded(row['opportunityCountByState'])}
            for row in selected['contextDayCounts']]
        curves = [{**row, 'meanExposurePct': row['exposurePct']['mean'], 'valuationDays': len(row['valuationUnreliableDates'])}
            for row in selected['curveEpochRows']]
        queries = [
            query('overall', selected['overallRows'], [('period', '时期'), ('policyLabel', '退出'), ('stateLabel', '形成时反馈差')] + STATE_FIELDS, True),
            query('policy-differences', comparisons, [('period', '时期'), ('stateLabel', '形成时反馈差'),
                ('populationDelta', '波动减固定：入场 / 闭仓 / 未平 / 未入场'),
                ('closedPnlAmountDelta', '闭仓净盈亏差（元）'), ('openRealizedPnlAmountDelta', '未平已实现差（元）'),
                ('openUnrealizedPnlAmountDelta', '未平未实现差（元）'), ('totalPnlAmountDelta', '合计差（元）'),
                ('closedContributionPpOfInitialCapitalDelta', '闭仓贡献差（百分点）')]),
            query('account-bridges', selected['accountReconciliation'], [('period', '时期'), ('policyLabel', '退出'),
                ('population', '形成 / 入场 / 闭仓 / 未平 / 未入场'), ('assignedPnlAmount', '已归属盈亏（元）'),
                ('unallocatedResidual', '未分配权益残差（元）'), ('accountNetPnlAmount', '原账户净盈亏（元）'),
                ('accountReturnPct', '原账户净收益（%）'), ('passed', '资金桥已核对')]),
            query('quality', qualities, [('period', '时期'), ('policyLabel', '退出'), ('unreliableValuationDays', '估值不可靠日'),
                ('unresolvedExitCount', '未解决退出'), ('openMarkStaleCount', '期末陈旧持仓'),
                ('numericUnknownCount', '数值未知路径'), ('integrityIssueCount', '完整性问题'), ('qualityFlags', '原源质量标记')]),
            query('context-days', days, [('period', '时期'), ('targetDays', '全部交易日'), ('formationCount', '原形成机会'),
                ('dayStates', '四状态交易日数'), ('opportunityStates', '四状态机会数'), ('completeMemberDays', '成员完整观察日'),
                ('boundedKnownWithUnknownMembersDays', '有未知成员仍可判定日'), ('emptyCohortDays', '队列为空日')]),
            query('formation-years', selected['formationYearRows'], [('formationYear', '形成年度'), ('policyLabel', '退出'), ('stateLabel', '形成时反馈差')] + STATE_FIELDS),
            query('formation-epochs', selected['epochStateRows'], [('epochLabel', '形成日期区间'), ('policyLabel', '退出'), ('stateLabel', '形成时反馈差')] + STATE_FIELDS),
            query('continuous-epochs', curves, [('policyLabel', '退出'), ('epochLabel', '原净值区间'), ('enteringEquity', '进入权益（元）'),
                ('endingEquity', '结束权益（元）'), ('returnPct', '区间收益（%）'), ('maxDrawdownPct', '区间回撤（%）'),
                ('meanExposurePct', '平均仓位（%）'), ('valuationDays', '估值不可靠日')]),
            query('payoff-distribution', selected['overallRows'], [('period', '时期'), ('policyLabel', '退出'), ('stateLabel', '形成时反馈差'),
                ('closed', '完整闭仓'), ('winRatePct', '闭仓胜率（%）'), ('meanWinPct', '平均盈利（%）'),
                ('meanLossPct', '平均亏损（%）'), ('top5ShareOfGrossProfitPct', '前五笔占闭仓总盈利（%）')]),
        ]
        sources = {'schemaVersion': 1, 'items': [{'id': board + '-formation-feedback',
            'title': BOARD_LABELS[board] + '：形成时强弱反馈与已闭仓贡献', 'queries': queries}]}
        category = lambda p, s: ('22–23' if p == PERIODS[0] else '24–25') + ' ' + STATE_SHORT[s]
        categories = [category(p, s) for p in PERIODS for s in STATES]
        by_key = {(r['period'], r['policy'], r['state']): r for r in selected['overallRows']}
        chart_rows = []
        for period in PERIODS:
            for state in STATES:
                for policy in POLICIES:
                    row = by_key[period, policy, state]
                    chart_rows.append({'period': period, 'state': state, 'category': category(period, state),
                        'policyLabel': POLICY_LABELS[policy], VALUE: row['closedContributionPpOfInitialCapital'],
                        **{k: row[k] for k in ('formationCount', 'entered', 'closed', 'open', 'notEntered',
                            'openRealizedPnlAmount', 'openUnrealizedPnlAmount', 'closedMeanPct', 'openMarkStaleCount')},
                        'unreliableValuationDays': quality[period, policy]['unreliableValuationDays']})
        chart = {'schemaVersion': 1, 'id': 'relative-feedback-' + board, 'queryId': board + '-overall',
            'title': BOARD_LABELS[board] + '：形成环境与已闭仓贡献', 'height': 620, 'theme': 'codex-classic',
            'chart': {'type': 'horizontalBar', 'x': 'category', 'y': VALUE, 'series': 'policyLabel', 'stackable': False,
                'startAtZero': True, 'sortOrder': 'original', 'valueDecimals': 2, 'xLabel': VALUE,
                'showXAxisLabel': True, 'showYAxisLabel': False, 'showValues': True,
                'categoryOrder': categories, 'seriesOrder': list(POLICY_LABELS.values()),
                'colors': {label: f'var(--chart-{i + 1})' for i, label in enumerate(POLICY_LABELS.values())}},
            'rows': chart_rows, 'columns': list(chart_rows[0]), 'source': src}
        require([[len(q['rows']) for q in item['queries']] for item in sources['items']] == TABLE_ROWS, 'Incomplete board Sources')
        for payload in (chart, sources):
            text = encoded(payload)
            require(len(text.encode()) < 1_000_000 and 'G:/' not in text and 'C:/' not in text, 'Oversized/private payload')
        result[board + 'Chart'], result[board + 'Sources'] = chart, sources
    return result


def build(directory=OUT):
    directory = Path(directory).resolve()
    names = ('plan.json', 'input-completion.json', 'readout.json', 'validation.json')
    values, pins = {n: read(directory / n) for n in names}, {n: sha(directory / n) for n in names}
    plan, completion, data, proof = (values[n] for n in names)
    require(proof['passed'] is True and proof['planSha256'] == data['planSha256'] == completion['planSha256'] == pins['plan.json'], 'Independent plan proof required')
    require(proof['readoutSha256'] == pins['readout.json'] and proof['inputCompletionSha256'] == pins['input-completion.json'], 'Independent diagnostic proof changed')
    require(proof['verifierSha256'] == plan['sourcePins'][VERIFIER] == sha(ROOT / VERIFIER), 'Independent verifier binding differs')
    require(data['inputCompletion']['sha256'] == pins['input-completion.json'], 'Context input completion differs')
    record_path = ROOT / data['records']['path']
    require(record_path.resolve() == directory / 'records.jsonl.gz' and sha(record_path) == data['records']['sha256'] == proof['recordsSha256'], 'Complete record artifact differs')
    pins['records.jsonl.gz'] = data['records']['sha256']
    require(data['formationCount'] == 8155 and data['recordCount'] == 16310, 'All original formations and both policies required')
    require(data['trialAccounting']['newAccounts'] == 0 and data['trialAccounting']['newStrategyTrials'] == 0
        and data['selectedState'] is None and data['automaticNextStrategy'] is None and data['profitHurdles'] is None, 'Diagnostic scope changed')
    require(data['sourceAccountReadout'] == plan['accountReadout'], 'Existing account readout reference differs')
    parent_path = ROOT / data['sourceAccountReadout']['path']
    require(sha(parent_path) == data['sourceAccountReadout']['sha256'], 'Existing account evidence changed')
    for name in (METHOD, RUNNER):
        require(plan['sourcePins'][name] == data['inputPins'][name] == sha(ROOT / name), 'Frozen analysis source changed')
    outputs = payloads(data, (ROOT / METHOD).read_text('utf-8'))
    require(not any((directory / n).exists() for n in ('inline', 'render-receipt.json', *[k + '.json' for k in DELIVERY_ORDER])), 'Never overwrite prior presentation')
    (directory / 'inline').mkdir()
    fragments, payload_refs = {}, {}
    for key, payload in outputs.items():
        board = next(b for b in BOARDS if key.startswith(b))
        kind = 'chart' if key.endswith('Chart') else 'sources'
        source, target = directory / (key + '.json'), directory / 'inline' / ('relative-feedback-' + board + '-' + kind + '.html')
        write(source, payload)
        command = [NODE, str(TOOLS / ('render-inline-' + kind + '.mjs')), '--input', str(source), '--output', str(target)]
        result = subprocess.run(command, text=True, capture_output=True, timeout=60)
        write(directory / (key + '-render-log.json'), {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        require(result.returncode == 0, key + ' shared rendering failed: ' + result.stderr)
        require(target.exists() and target.stat().st_size < 1_000_000, 'Missing/oversized rendered fragment')
        fragments[key] = {'path': target.relative_to(directory).as_posix(), 'sha256': sha(target)}
        payload_refs[key] = {'path': source.relative_to(directory).as_posix(), 'sha256': sha(source)}
    for name, expected in pins.items():
        require(sha(directory / name) == expected, 'Analysis artifact changed during presentation')
    write(directory / 'render-receipt.json', {'schema': 'reclaim-relative-feedback-context-render-v1',
        'inputPins': pins, 'sourceAccountReadout': data['sourceAccountReadout'], 'builderSha256': sha(__file__),
        'fragments': fragments, 'payloads': payload_refs, 'deliveryOrder': DELIVERY_ORDER,
        'chartRows': {board: 16 for board in BOARDS}, 'tableRows': {board: TABLE_ROWS for board in BOARDS},
        'states': list(STATES), 'accounts': 12, 'formationCount': 8155, 'recordCount': 16310,
        'actualMarketRuns': 0, 'analysisArtifactsModified': False, 'browserChecked': False,
        'measure': 'closedPnlAmount / original500000 * 100; formation attribution, not a filtered account return',
        'includes2026': False, 'holdout': False, 'causalClaim': False})
    return {'fragments': fragments, 'tableRows': {b: TABLE_ROWS for b in BOARDS}, 'renderReceiptSha256': sha(directory / 'render-receipt.json')}


def self_test():
    data = {key: [] for key in MATRIX_COUNTS}

    def aggregate(state, policy, empty=False):
        i = STATES.index(state)
        closed, opened, missing = ((0, 0, 0) if empty else ((1, 1, 1), (1, 0, 2), (0, 0, 0), (0, 0, 1))[i])
        pnl = ((100. if i == 0 else -50.) * (1 if policy == 'trail10' else 2)) if closed else 0.
        realized, unrealized = (3., -20.) if opened else (0., 0.)
        total = pnl + realized + unrealized
        return {'formationCount': closed + opened + missing, 'entered': closed + opened, 'closed': closed, 'open': opened,
            'notEntered': missing, 'uniqueFormationDays': int(closed + opened + missing > 0), 'uniqueSymbols': int(closed + opened + missing > 0),
            'closedPnlAmount': pnl, 'openRealizedPnlAmount': realized, 'openUnrealizedPnlAmount': unrealized, 'totalPnlAmount': total,
            'closedContributionPpOfInitialCapital': pnl / 500000 * 100, 'totalContributionPpOfInitialCapital': total / 500000 * 100,
            'closedMeanPct': (2. if pnl > 0 else -2.) if closed else None, 'openMarkStaleCount': opened,
            'winRatePct': (100. if pnl > 0 else 0.) if closed else None,
            'meanWinPct': 2. if pnl > 0 else None, 'meanLossPct': -2. if pnl < 0 else None,
            'top5ShareOfGrossProfitPct': 100. if pnl > 0 else None}

    for period in PERIODS:
        for board in BOARDS:
            data['contextDayCounts'].append({'period': period, 'board': board, 'targetDays': 7, 'formationCount': 7,
                'dayCountByState': dict(zip(STATES, (3, 3, 0, 1))), 'opportunityCountByState': dict(zip(STATES, (3, 3, 0, 1))),
                'completeMemberDays': 4, 'boundedKnownWithUnknownMembersDays': 2, 'emptyCohortDays': 1})
            for policy in POLICIES:
                identity = {'period': period, 'board': board, 'policy': policy}
                groups = []
                for state in STATES:
                    row = {**identity, 'state': state, **aggregate(state, policy)}
                    data['overallRows'].append(row); groups.append(row)
                    for index, year in enumerate(YEARS[period]):
                        data['formationYearRows'].append({**identity, 'state': state, 'formationYear': year,
                            **aggregate(state, policy, bool(index))})
                    for index, epoch in enumerate(EPOCHS[period]):
                        data['epochStateRows'].append({**identity, 'state': state, 'formationEpoch': epoch,
                            **aggregate(state, policy, bool(index))})
                money = math.fsum(row['totalPnlAmount'] for row in groups)
                data['accountReconciliation'].append({**identity, 'formationCount': 7, 'entered': 3, 'closed': 2, 'open': 1, 'notEntered': 4,
                    'assignedPnlAmount': money, 'unallocatedResidual': 5., 'accountNetPnlAmount': money + 5.,
                    'accountReturnPct': (money + 5.) / 500000 * 100, 'passed': True})
                data['qualityRows'].append({**identity, 'unreliableValuationDays': 2, 'unresolvedExitCount': 1, 'openMarkStaleCount': 1,
                    'numericUnknownCount': 0, 'integrityIssueCount': 0, 'sourceQualityScalarFlags': {'rawPrice': True}})
                if period == PERIODS[1]:
                    for epoch in EPOCHS[period]:
                        data['curveEpochRows'].append({**identity, 'phaseId': epoch, 'enteringEquity': 500000., 'endingEquity': 500010.,
                            'returnPct': .002, 'maxDrawdownPct': 0., 'exposurePct': {'mean': 10.}, 'valuationUnreliableDates': []})
            for state in STATES:
                a, b = aggregate(state, POLICIES[0]), aggregate(state, POLICIES[1])
                keys = ('entered', 'closed', 'open', 'notEntered', 'closedPnlAmount', 'openRealizedPnlAmount',
                    'openUnrealizedPnlAmount', 'totalPnlAmount', 'closedContributionPpOfInitialCapital', 'totalContributionPpOfInitialCapital')
                data['comparisonRows'].append({'period': period, 'board': board, 'state': state, 'control': POLICIES[0], 'candidate': POLICIES[1],
                    **{k + 'Delta': b[k] - a[k] for k in keys}})
    data.update(formationCount=42, recordCount=84)
    before = deepcopy(data)
    outputs = payloads(data, '# Synthetic only; no historical outcome loaded.')
    require(data == before and list(outputs) == DELIVERY_ORDER, 'Input mutated or delivery order changed')
    require(sum(MATRIX_COUNTS.values()) == 282 and sum(TABLE_ROWS[0]) * 3 == 330, 'Complete preview counts changed')
    for board in BOARDS:
        chart, sources = outputs[board + 'Chart'], outputs[board + 'Sources']
        require(len(chart['rows']) == 16 and len(chart['chart']['categoryOrder']) == 8, 'Complete chart categories required')
        require(len([row for row in chart['rows'] if row[VALUE] == 0]) == 8, 'Empty/unknown totals silently dropped')
        require(all(row['closedMeanPct'] is None for row in chart['rows'] if row['closed'] == 0), 'Null conditional means became zero')
        require(all(len(q['columns']) <= 8 for q in sources['items'][0]['queries']), 'Sources are too wide')
        require(all(BOARD_LABELS[board] in q['source']['label'] for q in sources['items'][0]['queries']), 'Board source identity missing')
        require(any(row[VALUE] < 0 for row in chart['rows']), 'Negative contribution lost')
        payoff = sources['items'][0]['queries'][8]
        require(len(payoff['rows']) == 16 and len(payoff['columns']) == 8
            and all(row['winRatePct'] is None for row in payoff['rows'] if row['closed'] == 0),
            'Complete payoff distribution or null conditional rates changed')
    for kind in ('missingState', 'duplicateYear', 'missingEpoch', 'reversedContrast', 'population', 'denominator', 'residual', 'dailyStates'):
        broken = deepcopy(data)
        if kind == 'missingState': broken['overallRows'].pop()
        elif kind == 'duplicateYear': broken['formationYearRows'][-1] = deepcopy(broken['formationYearRows'][0])
        elif kind == 'missingEpoch': broken['epochStateRows'].pop()
        elif kind == 'reversedContrast': broken['comparisonRows'][0]['candidate'] = POLICIES[0]
        elif kind == 'population': broken['overallRows'][0]['notEntered'] += 1
        elif kind == 'denominator': broken['overallRows'][0]['closedContributionPpOfInitialCapital'] = 123.
        elif kind == 'residual': broken['accountReconciliation'][0]['unallocatedResidual'] = 0.
        else: broken['contextDayCounts'][0]['dayCountByState']['unknown'] = 0
        try:
            payloads(broken, '# synthetic')
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid presentation accepted: ' + kind)
    code = """import fs from 'node:fs';
import {normalizeInlineChartInput} from %s;
import {normalizeInlineSourcesInput} from %s;
const inputs=JSON.parse(fs.readFileSync(0,'utf8'));
for(const [key,value] of Object.entries(inputs)) {
  if(key.endsWith('Chart')) normalizeInlineChartInput(value);
  else normalizeInlineSourcesInput(value);
}
console.log('passed');""" % (json.dumps((TOOLS / 'inline-chart-input.mjs').as_uri()), json.dumps((TOOLS / 'inline-sources-input.mjs').as_uri()))
    result = subprocess.run([NODE, '--input-type=module', '-e', code], input=json.dumps(outputs, ensure_ascii=False),
        text=True, capture_output=True, timeout=30)
    require(result.returncode == 0, result.stderr)
    return {'passed': True, 'checks': ['complete four-state matrix', 'all 48 plotted records', 'all 330 preview rows',
        'eight category labels per board', 'negative and zero totals', 'null means retained', 'separate three money components',
        'unallocated residual preserved', 'complete payoff distribution and conditional-rate nulls',
        'eight invalid cases rejected', 'no mutation', 'shared renderer validators'], 'filesWritten': 0}


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--directory', type=Path, default=OUT)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False))
