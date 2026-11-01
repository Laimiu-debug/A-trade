"""Presentation only: two exit-policy contrasts, with audited full evidence.

No signal, account or summary execution. Synthetic mode never reads outcomes.
"""
from argparse import ArgumentParser
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/reclaim-exit-transfer-v1'
HELPER = ROOT / 'scripts/render-strength-feedback.py'
HELPER_SHA = 'bed327c3a4ba9ffc8a4041190c9a913b6b0ca14000dcf0da06559af5c42539d4'
spec = importlib.util.spec_from_file_location('_exit_presentation_helpers', HELPER)
BASE = importlib.util.module_from_spec(spec); spec.loader.exec_module(BASE)
require, sha, read, write, finite = BASE.require, BASE.sha, BASE.read, BASE.write, BASE.finite
require(sha(HELPER) == HELPER_SHA, 'Presentation helper changed')
NODE, TOOLS = BASE.NODE, BASE.TOOLS
METHOD = 'service/research_structural_exits.py'
RUNNER = 'scripts/run-reclaim-exit-transfer.py'
PERIODS, BOARDS, BOARD_LABELS = BASE.PERIODS, BASE.BOARDS, BASE.BOARD_LABELS
MODES = ('baseline', 'trail10', 'structure10')
MODE_LABELS = dict(zip(MODES, ('原第五日退出', '百分比跟踪', '前十日低点')))
CONTRASTS = {'trail10VsBaseline': ('baseline', 'trail10'),
    'structure10VsTrail10': ('trail10', 'structure10'), 'structure10VsBaseline': ('baseline', 'structure10')}
CHARTS = {'primary': ('trail10VsBaseline', '去掉第五日退出：净收益差'),
    'secondary': ('structure10VsTrail10', '前十日低点替代百分比跟踪：净收益差')}
VALUE = '累计净收益差（百分点）'
TABLE_ROWS = {'primary': [[18, 18, 18, 18, 6, 18, 36, 72, 144, 18]], 'secondary': [[6, 12, 12]]}
BASE_ID = BASE.BASE_ID
POLICY_ID = BASE_ID + [('policyLabel', '退出政策')]
ACCOUNT_FIELDS, CONTRAST_FIELDS = BASE.ACCOUNT_FIELDS, BASE.CONTRAST_FIELDS
TAIL_FIELDS = BASE.TAIL_FIELDS + [('openUnrealizedPnlPctOfFinalEquity', '浮盈占期末权益（%）')]
INTERVAL_FIELDS = BASE.INTERVAL_FIELDS + [('structureUnknown', '额外退出未知观察')]
EXIT_FIELDS = [('configured', '额外退出已配置'), ('rule', '额外退出规则'), ('observationCount', '持仓日观察'),
    ('triggered', '已触发'), ('notTriggered', '未触发'), ('unknown', '未知'), ('skippedExistingExit', '已有退出优先'),
    ('uniqueUnknownHoldingEpisodes', '曾未知持仓段'), ('unknownReasons', '未知原因计数'),
    ('sellReasons', '实际卖出成交原因计数'), ('queuedReasons', '收盘排队卖出原因计数')]
FORMATION_FIELDS = [('includedCount', '固定纳入身份'), ('excludedCount', '期初排除'), ('unknownCount', '身份未知'),
    ('formationTop5', '原始形成机会'), ('sourceImmediateSignals', '共同立即入场信号'),
    ('sameScheduleForEveryPolicy', '三政策信号完全相同'), ('benchmarkUnavailableDays', '基准不可用日'),
    ('formationEvidence', '形成资格计数')]
COVERAGE_FIELDS = [('formationTop5', '原始形成机会'), ('selectedSignals', '安排信号'),
    ('notScheduledFromFormation', '未安排'), ('entered', '成交入场'), ('notEntered', '安排但未成交'),
    ('closedCount', '完整平仓'), ('openCountAtCutoff', '期末持仓'), ('unresolvedExitCountAtCutoff', '未解决退出'),
    ('valuationUnreliableDays', '估值不可靠日'), ('qualityFlags', '源质量标记'), ('terminalReasons', '账户终态原因计数')]
LIMITS = [
    '两图分别比较去掉第五日退出、将百分比跟踪替换为前十日低点退出；收益差必须结合仓位差、回撤、未实现盈亏与估值质量阅读，正差不等于稳定优势。',
    '这是既有退出定义向固定支撑收回信号的迁移对照；三政策使用逐字相同的原始信号，六个原账户复用，十二个新账户独立运行，没有新入场条件。',
    '三政策均保留5%初始收盘价格止损；百分比跟踪保留10%跟踪，前十日低点政策关闭百分比跟踪并比较此前十个精确交易槽的最低价，阈值可以下移。',
    '额外退出的未知、已有退出优先和未触发分别统计；未知或陈旧估值下长期持仓不能解释为趋势成功，未配置额外退出也不是完整非触发证据。',
    '收盘触发最早后续可成交开盘执行；原费用、每边10基点滑点、涨跌停与成交限制保留，名义5%止损不能保证实际损失不超过5%。',
    '退出变化会改变持仓期限、可用资金和后来实际买入集合；账户差不是逐笔配对的因果收益，未成交机会不能填成零收益交易。',
    '完整平仓分布与未平仓盈亏分开；浮盈占期末权益以终值为分母，不是总利润占比。已闭仓样本选择和期末删失仍存在。',
    '各账户独立50万元起资，两期不能拼成四年账户，三板块也不是共享资金组合；年份、半年、事后阶段和9月24日前后区间继承净值与跨期仓位，区间不等长。',
    '源股票是各期期初至少61个有效既往观测的固定本地身份；历史生存、原始价格、公司行为和状态信息仍有限，板块不等于科技行业。',
    '仅研究2022—2025年，2026未纳入，历史已用于研究而非留出样本；相对强度和超额收益如有列出均相对沪深300，非板块匹配或beta中性收益。',
]


def indexed(rows, policy=True):
    keys = ('period', 'board', 'policy') if policy else ('period', 'board')
    out = {tuple(r[k] for k in keys): r for r in rows}
    expected = {(p, b, m) for p in PERIODS for b in BOARDS for m in MODES} if policy else {(p, b) for p in PERIODS for b in BOARDS}
    require(len(out) == len(rows) and set(out) == expected, 'Incomplete/duplicate account or source matrix')
    return out


def labels(row):
    return {'period': row['period'], 'boardLabel': BOARD_LABELS[row['board']],
        **({'policyLabel': MODE_LABELS[row['policy']]} if 'policy' in row else {})}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def tables(data):
    accounts, coverage, exits, formation, contrasts = [], [], [], [], []
    by_account, by_coverage = indexed(data['accounts']), indexed(data['coverageRows'])
    for key, a in by_account.items():
        row, curve, bridge, c = a['comparisonRow'], a['curveAttribution'], a['reconciliation'], by_coverage[key]
        require((row['period'], row['board'], row['id']) == key, 'Account identity mismatch')
        closed = curve['closedEpisodesByExitDate']
        flat = {**labels(a), **{k: row[k] for k, _ in ACCOUNT_FIELDS},
            **{k: row[k] for k in ('winRatePct', 'payoffRatio', 'profitFactor', 'maxConsecutiveLosses', 'top5ShareOfGrossProfitPct')},
            'worstClosedPct': closed['returnPct']['min'], 'p05ClosedPct': closed['returnPct']['p05'],
            'medianHoldingSessions': row['holdingSessions']['median'], 'maxHoldingSessions': row['holdingSessions']['maximum'],
            'closedPnlAmount': bridge['closedEpisodePnl'], 'openUnrealizedPnlAmount': bridge['openPositionUnrealizedPnl'],
            'openRealizedPnlAmount': bridge['openPositionRealizedPnl'], 'feesPaid': curve['feesPaid'],
            'slippageAlreadyInFillPrices': curve['slippageAlreadyInFillPrices'],
            'openUnrealizedPnlPctOfFinalEquity': a['openUnrealizedPnlPctOfFinalEquity']}
        require(all(v is None or finite(v) for k, v in flat.items() if k not in dict(POLICY_ID)), 'Invalid numeric account field')
        require(finite(flat['accountReturnPct']) and finite(flat['maxDrawdownPct']), 'Unavailable account curve')
        require(c['formationTop5'] == c['selectedSignals'] and c['notScheduledFromFormation'] == 0
            and c['selectedSignals'] == c['entered'] + c['notEntered']
            and c['entered'] == c['closedCount'] + c['openCountAtCutoff'], 'Common opportunity counts do not conserve')
        accounts.append(flat)
        coverage.append({**c, **labels(c), 'qualityFlags': encoded(c['qualityScalarFlags']),
            'terminalReasons': encoded(c['terminalReasonCounts'])})
        e = a['exitEvidence']; expected = {'baseline': 'noProgress5', 'trail10': None, 'structure10': 'structure10'}[a['policy']]
        require(e['rule'] == expected and e['configured'] == (expected is not None)
            and sum(e['observationCounts'].values()) == e['observationCount'], 'Exit evidence identity/count mismatch')
        exits.append({**labels(a), **e, **e['observationCounts'], 'unknownReasons': encoded(e['unknownReasonCounts']),
            'sellReasons': encoded(curve['sellFillReasons']), 'queuedReasons': encoded(curve['queuedExitReasons'])})
    for c in indexed(data['formationCoverageRows'], False).values():
        require(c['formationTop5'] == c['sourceImmediateSignals'] and c['sameScheduleForEveryPolicy'] is True, 'Common signal population differs')
        formation.append({**c, **labels(c), **{k: c['sourceCohort'][k] for k in ('includedCount', 'excludedCount', 'unknownCount')},
            'formationEvidence': encoded(c['formationEvidenceTotals'])})
    require(sum(c['formationTop5'] for c in formation) == data['opportunityRowsArtifact']['count'], 'Original opportunity count mismatch')
    seen = set()
    for r in data['comparisons']:
        key = (r['period'], r['board'], r['id'])
        require(key not in seen and r['id'] in CONTRASTS, 'Duplicate/unknown contrast'); seen.add(key)
        control, candidate = CONTRASTS[r['id']]
        require((r['control'], r['candidate']) == (control, candidate), 'Flipped contrast')
        delta, c, k = r['candidateMinusControl'], r['candidateMetrics'], r['controlMetrics']
        contrasts.append({**labels(r), 'contrast': r['id'], 'contrastLabel': MODE_LABELS[candidate] + '−' + MODE_LABELS[control],
            'returnDifferencePp': delta['accountReturnPct'], 'drawdownDifferencePp': delta['maxDrawdownPct'],
            'exposureDifferencePp': delta['meanExposurePct'], 'candidateReturnPct': c['accountReturnPct'], 'controlReturnPct': k['accountReturnPct'],
            'candidateValuationDays': c['unreliableValuationDays'], 'controlValuationDays': k['unreliableValuationDays'],
            'closedPnlDifference': delta['closedPnlAmount'], 'openPnlDifference': delta['openUnrealizedPnlAmount']})
    require(seen == {(p, b, x) for p in PERIODS for b in BOARDS for x in CONTRASTS}, 'All eighteen contrasts required')
    intervals = []
    for key, count in (('yearRows', 36), ('halfYearRows', 72), ('phaseRows', 144), ('curveEpochRows', 18)):
        require(len(data[key]) == count, 'Incomplete ' + key)
        values, seen = [], set()
        for r in data[key]:
            identity = (r['period'], r['board'], r['policy'], r['requestedStart'], r['requestedEnd'])
            require(identity[:3] in by_account and identity not in seen, 'Duplicate interval'); seen.add(identity)
            label = r.get('phaseTitle') or r.get('phaseId') or r.get('id')
            require(label is not None, 'Missing interval label')
            values.append({**r, **labels(r), 'intervalLabel': label, 'meanExposurePct': r['exposurePct']['mean'],
                'valuationDays': len(r['valuationUnreliableDates']), 'structureUnknown': r['exitEvidence']['observationCounts']['unknown']})
        intervals.append(values)
    return contrasts, accounts, exits, formation, coverage, intervals


def payloads(data, method):
    contrasts, accounts, exits, formation, coverage, intervals = tables(data)
    artifact = data['opportunityRowsArtifact']
    src = {'label': '固定支撑收回信号的退出迁移（2022—2025）',
        'files': [{'label': n} for n in ('plan.json', 'comparison.json', 'validation.json', 'readout.json', 'readout-validation.json',
            'research_structural_exits.py', 'board-coverage-reclaim-v1/readout.json', 'market-phase-atlas-v1/atlas.json')]
            + [{'label': f"opportunity-dispositions.jsonl.gz：{artifact['count']}原机会×3政策；SHA256 {artifact['sha256']}"}],
        'metricDefinitions': [{'label': '退出迁移', 'definition': '完整账户净收益差逐个比较取消第五日退出和替换跟踪方式；三种账户的入场信号相同，资金和后续成交路径可能改变。'},
            {'label': '资金与质量', 'definition': '净收益包括费用、滑点与期末持仓；未平仓盈亏、平均仓位、估值不可靠日及额外退出未知观察单列。'},
            {'label': '区间归因', 'definition': '分期从前一收盘净值起算，继承跨期仓位；原机会以形成日归属，退出原因以实际观察、排队或成交时点各自统计。'}],
        'filters': ['两个历史时期、三个固定板块、三种完整退出政策', '全部原始立即入场信号逐字不变，无筛选或补位',
            '六个原账户复用，十二个新账户独立运行', '2024年9月24日只作连续资金归因分界'],
        'caveats': LIMITS,
        'evidenceFlow': [{'kind': 'validation', 'title': '账户与汇总核验', 'detail': '独立复核18账户、270个子区间、18个有方向差值和所有原机会的三政策终态；四种额外退出观察状态与实际卖出、排队原因分别保留。'}]}
    def query(key, rows, columns, source=src, include_method=False):
        require(all(all(k in r for k, _ in columns) for r in rows), 'Missing column in ' + key)
        out = {'id': key, 'rows': [{k: r[k] for k, _ in columns} for r in rows],
            'columns': [{'field': k, 'label': v} for k, v in columns], 'source': source,
            'reportingPeriod': '2022—2023与2024—2025独立账户；2026未纳入'}
        if include_method: out['methods'] = [{'language': 'python', 'code': method}]
        return out
    full = [query('contrasts', contrasts, BASE_ID + CONTRAST_FIELDS), query('accounts', accounts, POLICY_ID + ACCOUNT_FIELDS),
        query('tails-and-capital', accounts, POLICY_ID + TAIL_FIELDS), query('exit-evidence', exits, POLICY_ID + EXIT_FIELDS, include_method=True),
        query('formation-coverage', formation, BASE_ID + FORMATION_FIELDS), query('policy-coverage', coverage, POLICY_ID + COVERAGE_FIELDS)]
    full.extend(query(k, rows, POLICY_ID + INTERVAL_FIELDS) for k, rows in zip(('years', 'half-years', 'phases', 'event-epochs'), intervals))
    outputs = {}
    for kind, (contrast_id, title) in CHARTS.items():
        control, candidate = CONTRASTS[contrast_id]
        selected = [r for r in contrasts if r['contrast'] == contrast_id]
        scoped_src = deepcopy(src)
        scoped_src['metricDefinitions'][0]['definition'] = title + '，方向为' + MODE_LABELS[candidate] + '减' + MODE_LABELS[control] + '，同时保留仓位差与估值不可靠日。'
        if kind == 'secondary':
            scoped_src['files'].append({'label': 'reclaim-exit-transfer-primary-sources.html：前一图来源含全18账户、18对照及全部分期；完整数值见 readout.json'})
            queries = [query('secondary-contrasts', selected, BASE_ID + CONTRAST_FIELDS, scoped_src),
                query('secondary-accounts', [r for r in accounts if r['policyLabel'] in (MODE_LABELS[control], MODE_LABELS[candidate])], POLICY_ID + ACCOUNT_FIELDS + TAIL_FIELDS, scoped_src),
                query('secondary-exits', [r for r in exits if r['policyLabel'] in (MODE_LABELS[control], MODE_LABELS[candidate])], POLICY_ID + EXIT_FIELDS, scoped_src)]
        else:
            queries = full
        sources = {'schemaVersion': 1, 'items': [{'id': kind + '-exit-transfer', 'title': title + '：完整账户与风险证据', 'queries': queries}]}
        chart = {'schemaVersion': 1, 'id': 'reclaim-exit-transfer-' + kind, 'queryId': contrast_id,
            'title': title, 'height': 360, 'theme': 'codex-classic',
            'chart': {'type': 'horizontalBar', 'x': 'boardLabel', 'y': VALUE, 'series': 'period', 'stackable': False,
                'startAtZero': True, 'sortOrder': 'original', 'valueDecimals': 2, 'xLabel': VALUE,
                'showXAxisLabel': True, 'showYAxisLabel': False, 'showValues': True,
                'categoryOrder': list(BOARD_LABELS.values()), 'seriesOrder': list(PERIODS),
                'colors': {p: f'var(--chart-{i+1})' for i, p in enumerate(PERIODS)}},
            'rows': [{'period': r['period'], 'boardLabel': r['boardLabel'], VALUE: r['returnDifferencePp'],
                '平均仓位差（百分点）': r['exposureDifferencePp'], '候选估值不可靠日': r['candidateValuationDays'],
                '对照估值不可靠日': r['controlValuationDays']} for r in selected],
            'columns': ['period', 'boardLabel', VALUE, '平均仓位差（百分点）', '候选估值不可靠日', '对照估值不可靠日'],
            'source': scoped_src}
        require(len(chart['rows']) == 6 and all(finite(r[VALUE]) for r in chart['rows']), 'Six finite differences required')
        require([[len(q['rows']) for q in item['queries']] for item in sources['items']] == TABLE_ROWS[kind], 'Incomplete receipt')
        for value in (chart, sources):
            text = encoded(value)
            require(len(text.encode()) < 1_000_000 and 'G:/' not in text and 'C:/' not in text, 'Oversized/private payload')
        outputs[kind] = {'chart': chart, 'sources': sources}
    return outputs


def build(directory=OUT):
    directory = Path(directory).resolve()
    names = ('plan.json', 'comparison.json', 'validation.json', 'readout.json', 'readout-validation.json')
    values, pins = {n: read(directory / n) for n in names}, {n: sha(directory / n) for n in names}
    plan, comparison, proof, data, audit = (values[n] for n in names)
    require(proof['passed'] and data['verification']['passed'] and audit['passed'], 'Independent audits required')
    require(proof['planSha256'] == data['planSha256'] == audit['planSha256'] == pins['plan.json'], 'Plan audit binding differs')
    require(proof['comparisonSha256'] == audit['comparisonSha256'] == pins['comparison.json'], 'Comparison binding differs')
    require(data['verification']['sha256'] == audit['accountValidationSha256'] == pins['validation.json']
        and audit['readoutSha256'] == pins['readout.json'], 'Readout binding differs')
    require(indexed(data['accountRows']) == indexed(comparison['rows']), 'Account display rows differ')
    for key, n in (('newAccounts', 12), ('referenceAccountsReused', 6), ('newSetupMechanisms', 0), ('newEntryPolicies', 0),
                   ('newExitDefinitions', 0), ('transferredExitPolicies', 2), ('newUniverseVariants', 0)):
        require(plan[key] == comparison[key] == n, 'Trial scope differs: ' + key)
    for name in ('plan.json', 'comparison.json', 'validation.json'):
        require(data['inputPins'][(directory / name).relative_to(ROOT).as_posix()] == pins[name], 'Input changed')
    for name in (METHOD, RUNNER): require(plan['sourcePins'][name] == sha(ROOT / name), 'Frozen source changed')
    artifact = data['opportunityRowsArtifact']; path = ROOT / artifact['path']
    require(path.resolve().parent == directory and artifact['format'] == 'jsonl+gzip', 'Sidecar outside family')
    pins[path.name] = sha(path)
    require(pins[path.name] == artifact['sha256'] == audit['opportunityRowsArtifactSha256'], 'Sidecar binding differs')
    outputs = payloads(data, (ROOT / METHOD).read_text('utf-8'))
    output_names = ['inline', 'render-receipt.json'] + [f'{k}-{t}.json' for k in CHARTS for t in ('chart', 'sources')]
    require(not any((directory / n).exists() for n in output_names), 'Do not overwrite presentation evidence')
    (directory / 'inline').mkdir()
    fragments, payload_pins = {}, {}
    for kind, items in outputs.items():
        for typ, payload in items.items():
            key = kind + '-' + typ
            source, target = directory / (key + '.json'), directory / 'inline' / ('reclaim-exit-transfer-' + key + '.html')
            write(source, payload); payload_pins[key] = {'path': source.name, 'sha256': sha(source)}
            renderer = TOOLS / ('render-inline-' + typ + '.mjs')
            result = subprocess.run([NODE, str(renderer), '--input', str(source), '--output', str(target)], text=True, capture_output=True, timeout=60)
            write(directory / (key + '-render-log.json'), {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
            require(result.returncode == 0, result.stdout + result.stderr)
            require(target.stat().st_size < 1_000_000, 'Oversized fragment')
            fragments[key] = {'path': target.relative_to(directory).as_posix(), 'sha256': sha(target), 'rendererSha256': sha(renderer)}
    require(all(sha(directory / n) == digest for n, digest in pins.items()), 'Analysis inputs modified')
    receipt = {'schema': 'reclaim-exit-transfer-render-v1', 'inputPins': pins, 'builderSha256': sha(__file__),
        'presentationHelpers': {HELPER.relative_to(ROOT).as_posix(): HELPER_SHA}, 'payloads': payload_pins, 'fragments': fragments,
        'tableRows': TABLE_ROWS, 'chartRows': {'primary': 6, 'secondary': 6}, 'cards': {'primary': 1, 'secondary': 1},
        'deliveryOrder': ['primary-chart', 'primary-sources', 'secondary-chart', 'secondary-sources'],
        'opportunityRowsArtifact': artifact, 'actualMarketRuns': 0, 'analysisArtifactsModified': False,
        'dataPeriod': '2022-2025', 'includes2026': False, 'holdout': False, 'causalClaim': False,
        'boardIsIndustry': False, 'browserChecked': False}
    write(directory / 'render-receipt.json', receipt)
    return {'fragments': fragments, 'tableRows': TABLE_ROWS, 'renderReceiptSha256': sha(directory / 'render-receipt.json')}


def self_test():
    data = {k: [] for k in ('accounts', 'coverageRows', 'formationCoverageRows', 'comparisons', 'yearRows', 'halfYearRows', 'phaseRows', 'curveEpochRows')}
    data['opportunityRowsArtifact'] = {'count': 48, 'sha256': 'a' * 64}
    for p in PERIODS:
        for b in BOARDS:
            data['formationCoverageRows'].append({'period': p, 'board': b, 'formationTop5': 8, 'sourceImmediateSignals': 8,
                'sameScheduleForEveryPolicy': True, 'sourceCohort': {'includedCount': 8, 'excludedCount': 1, 'unknownCount': 1},
                'formationEvidenceTotals': {'unknown': 4}, 'benchmarkUnavailableDays': 2})
            for m in MODES:
                identity = {'period': p, 'board': b, 'policy': m}
                e = {'configured': m != 'trail10', 'rule': {'baseline': 'noProgress5', 'trail10': None, 'structure10': 'structure10'}[m],
                    'observationCount': 0 if m == 'trail10' else 4, 'observationCounts': {k: int(m != 'trail10') for k in ('triggered', 'notTriggered', 'unknown', 'skippedExistingExit')},
                    'uniqueUnknownHoldingEpisodes': int(m != 'trail10'), 'unknownReasonCounts': {'missingBar': 1} if m != 'trail10' else {}}
                row = {k: None for k, _ in ACCOUNT_FIELDS + TAIL_FIELDS}
                row.update(**identity, id=m, accountReturnPct=1., maxDrawdownPct=2., holdingSessions={'median': None, 'maximum': None})
                data['accounts'].append({**identity, 'comparisonRow': row, 'openUnrealizedPnlPctOfFinalEquity': None, 'exitEvidence': e,
                    'curveAttribution': {'closedEpisodesByExitDate': {'returnPct': {'min': None, 'p05': None}}, 'feesPaid': 0., 'slippageAlreadyInFillPrices': 0.,
                        'sellFillReasons': {'initialStop': 1}, 'queuedExitReasons': {'structure10': 1}},
                    'reconciliation': {'closedEpisodePnl': 0., 'openPositionUnrealizedPnl': 0., 'openPositionRealizedPnl': 0.}})
                data['coverageRows'].append({**identity, 'formationTop5': 8, 'selectedSignals': 8, 'notScheduledFromFormation': 0,
                    'entered': 2, 'notEntered': 6, 'closedCount': 1, 'openCountAtCutoff': 1, 'unresolvedExitCountAtCutoff': 1,
                    'valuationUnreliableDays': 1, 'qualityScalarFlags': {'stale': True}, 'terminalReasonCounts': {'noCash': 6}})
                for key, n in (('yearRows', 2), ('halfYearRows', 4), ('phaseRows', 8), ('curveEpochRows', 2 if p == PERIODS[1] else 0)):
                    for i in range(n):
                        data[key].append({**identity, 'id': str(i), 'requestedStart': f'{p[:4]}-{i+1:02d}-01', 'requestedEnd': f'{p[:4]}-{i+1:02d}-28',
                            'returnPct': 1., 'maxDrawdownPct': 2., 'netPnlAmount': 3., 'feesPaid': 0., 'endingPositionCount': 1,
                            'valuationUnreliableDates': [], 'exposurePct': {'mean': 40.}, 'exitEvidence': e})
            for kind, (control, candidate) in CONTRASTS.items():
                data['comparisons'].append({'period': p, 'board': b, 'id': kind, 'candidate': candidate, 'control': control,
                    'candidateMinusControl': {'accountReturnPct': float(BOARDS.index(b)-1), 'maxDrawdownPct': None, 'meanExposurePct': -10.,
                        'closedPnlAmount': 0., 'openUnrealizedPnlAmount': None},
                    'candidateMetrics': {'accountReturnPct': 1., 'unreliableValuationDays': 2},
                    'controlMetrics': {'accountReturnPct': 0., 'unreliableValuationDays': 3}})
    before = deepcopy(data); output = payloads(data, '# Synthetic example only.')
    require(data == before, 'Input mutated')
    for kind, items in output.items():
        require([r[VALUE] for r in items['chart']['rows'][:3]] == [-1., 0., 1.], 'Signed/null data altered')
        require(items['chart']['chart']['xLabel'] == VALUE, 'Percentage-point axis label required')
    require(output['primary']['sources']['items'][0]['queries'][1]['rows'][0]['closedMeanPct'] is None, 'Unknown filled')
    for kind in ('account', 'phase', 'population', 'exitCount', 'flipped', 'schedule'):
        bad = deepcopy(data)
        if kind == 'account': bad['accounts'][-1] = deepcopy(bad['accounts'][0])
        elif kind == 'phase': bad['phaseRows'].pop()
        elif kind == 'population': bad['opportunityRowsArtifact']['count'] = 1
        elif kind == 'exitCount': bad['accounts'][0]['exitEvidence']['observationCount'] = 0
        elif kind == 'flipped': bad['comparisons'][0]['candidate'] = 'baseline'
        else: bad['formationCoverageRows'][0]['sameScheduleForEveryPolicy'] = False
        try: payloads(bad, '# synthetic')
        except ValueError: pass
        else: raise AssertionError('Invalid fixture accepted: ' + kind)
    code = """import fs from 'node:fs';
import {normalizeInlineChartInput} from %s;
import {normalizeInlineSourcesInput} from %s;
for(const item of Object.values(JSON.parse(fs.readFileSync(0,'utf8')))){
 normalizeInlineChartInput(item.chart);normalizeInlineSourcesInput(item.sources);
} console.log('passed');""" % (json.dumps((TOOLS / 'inline-chart-input.mjs').as_uri()), json.dumps((TOOLS / 'inline-sources-input.mjs').as_uri()))
    result = subprocess.run([NODE, '--input-type=module', '-e', code], input=json.dumps(output, ensure_ascii=False), text=True, capture_output=True, timeout=30)
    require(result.returncode == 0, result.stderr)
    return {'passed': True, 'checks': ['two six-difference views', 'all18 accounts and contrasts', 'all270 intervals',
        '13 complete evidence tables', 'four exit observation states', 'nulls and zero retained', 'no mutation',
        'six invalid cases rejected', 'exposure and valuation fields', 'shared renderer validators'], 'filesWritten': 0}


if __name__ == '__main__':
    parser = ArgumentParser(); parser.add_argument('--directory', type=Path, default=OUT); parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False))
