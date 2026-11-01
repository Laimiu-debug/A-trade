"""Audited three-board environment presentation; no new signals or accounts."""
from argparse import ArgumentParser
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/board-environment-context-v1'
NODE = 'C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
TOOLS = Path('C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts')
BOARDS = ('main', 'chinext', 'star')
BOARD_LABELS = dict(zip(BOARDS, ('主板', '创业板', '科创板')))
PERIODS = ('2022-2023', '2024-2025')
POLICIES = ('trail10', 'volatilityTrail')
POLICY_LABELS = dict(zip(POLICIES, ('固定跟踪', '波动跟踪')))
PHASES = (
    ('2022-01-risk-off-and-policy-themes', '22.01 弱市'), ('2022-02-new-energy-rebound', '22.04 反弹'),
    ('2022-03-fragmented-rotation-to-xinchuang', '22.07 轮动'), ('2022-04-policy-reopening-rotation', '22.11 修复'),
    ('2023-01-recovery-ai-and-soe', '23.01 开年'), ('2023-02-ai-hardware-and-policy-rotation', '23.05 分化'),
    ('2023-03-huawei-chain-versus-weak-index', '23.08 华为'), ('2023-04-yearend-fragmented-themes', '23.11 年末'),
    ('2024-01-stress', '24.01 弱市'), ('2024-02-rebound', '24.02 修复'),
    ('2024-05-fragmented', '24.05 弱市'), ('2024-09-policy-rally', '24.09 政策'),
    ('2024-10-differentiation', '24.10 分化'), ('2025-01-spring-tech', '25.01 科技'),
    ('2025-04-repair', '25.04 修复'), ('2025-08-ai-trend', '25.08 趋势'),
)
PHASE_IDS = tuple(p[0] for p in PHASES)
PHASE_LABELS = dict(PHASES)
METRICS = ('breadth20', 'priceCoverage', 'amountRatio', 'amountCoverage', 'matchedBreadthChange5',
    'matchedCoverage', 'endpointLower', 'endpointUpper', 'continuousLower', 'continuousUpper', 'topPositiveLower', 'topPositiveUpper')
SERIES = ('上涨广度（日均）', '持续在榜（日均下界）')
VALUE = '阶段日均（%）'
TABLE_ROWS = [[16, 16, 16, 3, 32]] * 3
LIMITS = [
    '每个阶段按形成日T归属，但环境只看到T−1收盘；9月24日环境截至9月23日，政策日反应最早进入下一交易日。阶段天数不同，图中是各阶段已知日的等权平均，不是阶段累计涨幅。',
    '上涨广度为同板固定样本中，完整20交易日价格收益为正的证券占可评估证券比例；未知证券不填零，固定样本覆盖和未知日数另列。它不是当天上涨家数。',
    '持续在榜把T−6冻结的原RS20前十分位队列观察到T−1；五天每天均在当日强十分位才计留存。下界保留原人数作分母，未知计入上界，区间是缺失边界而非置信区间。端点在榜、五日价格上涨与持续在榜不是同一件事。',
    '两条图示指标在读到本轮结果后为解释选择；未修改已冻结统计、未搜索门槛，也未证明两指标同时较高就有交易优势。三图均从零起，跨板比较时须看各图轴刻度和具体数值。',
    '相对成交额是各股当日金额除以前20交易日均额后取横截面中位数，再作阶段日均；不是全市场总成交额、资金净流入或长期成交额水平。20日相对排名统一参照沪深300，板块身份不等于纯科技行业。',
    '历史阶段只作描述，不能把9月24日或题材名称直接写成交易开关。两期固定来源样本不同，全部2022—2025为已检视开发历史；2026已暴露但未使用，无独立留出声明。',
    '旧账户连续阶段收益继承进入权益和存量持仓；没有买卖也可能因持仓重估上涨，不是当期新信号收益。全部96条旧曲线及估值质量标记保留；未平仓重估不能称已实现利润。本轮无新账户、信号或稳定盈利结论。',
]


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def write(path, data):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def pct(value):
    return None if value is None else value * 100


def near(a, b):
    return finite(a) and finite(b) and math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-7)


def validate(data):
    require(data['newStrategyTrials'] == data['newAccounts'] == 0 and not data['holdoutClaim'] and
        not data['certified'] and not data['targetAchieved'], 'Descriptive scope differs')
    require(len(data['phaseRows']) == 48 and len(data['epochRows']) == 9 and len(data['continuousAccountRows']) == 96, 'Complete matrix required')
    phases = {(r['board'], r['phaseId']) for r in data['phaseRows']}
    require(len(phases) == 48 and phases == {(b, p) for b in BOARDS for p in PHASE_IDS}, 'Phase identities differ')
    accounts = {(r['board'], r['policy'], r['phaseId']) for r in data['continuousAccountRows']}
    require(len(accounts) == 96 and accounts == {(b, m, p) for b in BOARDS for m in POLICIES for p in PHASE_IDS}, 'Account identities differ')
    epochs = {(r['board'], r['epoch']) for r in data['epochRows']}
    require(len(epochs) == 9 and epochs == {(b, e) for b in BOARDS for e in ('2022-2023', 'before20240924', 'from20240924')}, 'Epoch identities differ')
    for r in data['phaseRows'] + data['epochRows']:
        require(r['period'] in PERIODS and type(r['sessions']) is int and r['sessions'] > 0 and type(r['fixedPopulation']) is int and r['fixedPopulation'] > 0, 'Population/session count invalid')
        require(r['asOfStart'] < r['observedStart'] and r['asOfEnd'] < r['observedEnd'], 'T-1 clock differs')
        for metric in METRICS:
            s = r[metric]
            require(type(s['knownDays']) is int and type(s['unknownDays']) is int and s['knownDays'] >= 0 and s['unknownDays'] >= 0 and
                s['knownDays'] + s['unknownDays'] == r['sessions'], 'Known/unknown days differ: ' + metric)
            require(all(v is None or finite(v) for v in (s['mean'], s['median'], s['minimum'], s['maximum'])), 'Nonfinite statistic')
            if s['knownDays'] == 0:
                require(all(s[k] is None for k in ('mean', 'median', 'minimum', 'maximum')), 'Missing statistics not null')
        for prefix in ('endpoint', 'continuous', 'topPositive'):
            lo, hi = r[prefix + 'Lower']['mean'], r[prefix + 'Upper']['mean']
            require((lo is None and hi is None) or (finite(lo) and finite(hi) and 0 <= lo <= hi <= 1), 'Retention/feedback bounds differ')
    for row in data['continuousAccountRows']:
        reference = next(r for r in data['phaseRows'] if r['board'] == row['board'] and r['phaseId'] == row['phaseId'])
        require(row['period'] == reference['period'] and row['observedStart'] == reference['observedStart'] and
            row['observedEnd'] == reference['observedEnd'] and row['observedSessions'] == reference['sessions'], 'Environment/account interval binding differs')
        require(near(row['endingEquity'] - row['enteringEquity'], row['netPnlAmount']) and
            near((row['endingEquity'] / row['enteringEquity'] - 1) * 100, row['returnPct']), 'Original continuous account bridge differs')
    require(sum(r['sessions'] for r in data['phaseRows']) == data['dailyCount'], 'All daily rows not represented')


def payloads(data, method):
    validate(data)
    outputs = {}
    for board in BOARDS:
        phase = sorted((r for r in data['phaseRows'] if r['board'] == board), key=lambda r: PHASE_IDS.index(r['phaseId']))
        epochs = [r for r in data['epochRows'] if r['board'] == board]
        account = sorted((r for r in data['continuousAccountRows'] if r['board'] == board), key=lambda r: (PHASE_IDS.index(r['phaseId']), POLICIES.index(r['policy'])))
        def identity(r):
            return {'phase': PHASE_LABELS[r['phaseId']] + ' · ' + r['phaseTitle'], 'interval': r['observedStart'] + ' 至 ' + r['observedEnd'], 'sessions': r['sessions']}
        core = [{**identity(r), 'breadthMean': pct(r['breadth20']['mean']), 'breadthMedian': pct(r['breadth20']['median']),
            **{k: pct(r[k]['mean']) for k in ('continuousLower', 'continuousUpper', 'endpointLower', 'endpointUpper')}} for r in phase]
        activity = [{'phase': identity(r)['phase'], 'matchedChange': r['matchedBreadthChange5']['mean'], 'amountRatio': r['amountRatio']['mean'],
            'topPositiveLower': pct(r['topPositiveLower']['mean']), 'topPositiveUpper': pct(r['topPositiveUpper']['mean']),
            'matchedDays': str(r['matchedBreadthChange5']['knownDays']) + ' / ' + str(r['matchedBreadthChange5']['unknownDays']),
            'priceCoverage': pct(r['priceCoverage']['minimum']), 'amountCoverage': pct(r['amountCoverage']['minimum']),
            'matchedCoverage': pct(r['matchedCoverage']['minimum'])} for r in phase]
        coverage = [{'phase': identity(r)['phase'], 'fixedPopulation': r['fixedPopulation'], 'sessions': r['sessions'],
            'informationWindow': r['asOfStart'] + ' 至 ' + r['asOfEnd'],
            **{k: str(r[k]['knownDays']) + ' / ' + str(r[k]['unknownDays']) for k in ('breadth20', 'amountRatio', 'continuousLower')},
            'emptyCohortDays': r['emptyFrozenCohortDays']} for r in phase]
        epoch = [{'epoch': r['observedStart'] + ' 至 ' + r['observedEnd'], 'sessions': r['sessions'],
            'breadthMean': pct(r['breadth20']['mean']), 'continuousLower': pct(r['continuousLower']['mean']), 'continuousUpper': pct(r['continuousUpper']['mean']),
            'amountRatio': r['amountRatio']['mean'], 'matchedChange': r['matchedBreadthChange5']['mean'],
            'priceCoverage': pct(r['priceCoverage']['minimum']), 'matchedDays': str(r['matchedBreadthChange5']['knownDays']) + ' / ' + str(r['matchedBreadthChange5']['unknownDays'])} for r in epochs]
        ledger = [{'phase': PHASE_LABELS[r['phaseId']], 'policy': POLICY_LABELS[r['policy']], 'sessions': r['observedSessions'],
            'enteringEquity': r['enteringEquity'], 'endingEquity': r['endingEquity'], 'netPnlAmount': r['netPnlAmount'], 'returnPct': r['returnPct'],
            'fills': str(r['buyFillCount']) + ' / ' + str(r['sellFillCount']), 'endingPositionCount': r['endingPositionCount'], 'valuationFlagDays': r['valuationFlagDays']} for r in account]
        source = {'label': BOARD_LABELS[board] + '固定样本环境及旧账户',
            'files': [{'label': 'artifacts/strategy-search/board-environment-context-v1/' + f} for f in ('plan.json', 'readout.json', 'validation.json')] +
                [{'label': 'docs/board-environment-context-v1.json'}, {'label': 'scripts/build-board-environment-context.py'},
                 {'label': 'artifacts/strategy-search/joint-cohort-regime-v1/continuous-phase-context.json'},
                 {'label': 'artifacts/strategy-search/market-phase-atlas-v1/atlas.json'}],
            'metricDefinitions': [{'label': '上涨广度', 'definition': '固定同板可评估股票过去20交易日收益为正的比例，图取各阶段已知日等权平均。'},
                {'label': '持续在榜下界', 'definition': '原强十分位队列连续五天均保持在榜的人数占原队列人数；图取日均下界，上界和缺失信息完整保留。'}],
            'filters': ['同一板块两个时期全部16历史阶段', '形成日T使用前收盘T−1信息', '原96条连续账户曲线按板块分列'],
            'caveats': LIMITS, 'evidenceFlow': [{'kind': 'validation', 'title': '原始日表及阶段衔接核对',
                'detail': '独立复核六源日表、完整48阶段和9区间，核对原96条连续账户来源；固定样本、未知分母和上下界保留。'}]}
        def query(name, rows, columns, include_method=False):
            require(all(all(k in r for k, _ in columns) for r in rows), 'Missing preview field')
            result = {'id': name, 'rows': [{k: r[k] for k, _ in columns} for r in rows],
                'columns': [{'field': k, 'label': label} for k, label in columns], 'source': source,
                'reportingPeriod': '2022—2025全部描述阶段，信息截至形成日前收盘；区间长度不同'}
            if include_method:
                result['methods'] = [{'language': 'python', 'code': method}]
            return result
        queries = [query('phase-environment', core, [('phase', '阶段'), ('interval', '形成日期'), ('sessions', '日数'), ('breadthMean', '广度日均（%）'),
            ('breadthMedian', '广度日中位（%）'), ('continuousLower', '持续下界日均（%）'), ('continuousUpper', '持续上界日均（%）'),
            ('endpointLower', '端点下界日均（%）'), ('endpointUpper', '端点上界日均（%）')], True),
            query('activity-and-feedback', activity, [('phase', '阶段'), ('matchedChange', '匹配广度变化日均（百分点）'), ('amountRatio', '相对成交额日均（倍）'),
                ('topPositiveLower', '强组上涨下界日均（%）'), ('topPositiveUpper', '强组上涨上界日均（%）'), ('matchedDays', '匹配已知 / 未知日'),
                ('priceCoverage', '最低价格覆盖（%）'), ('amountCoverage', '最低金额覆盖（%）'), ('matchedCoverage', '最低匹配覆盖（%）')]),
            query('population-and-clock', coverage, [('phase', '阶段'), ('fixedPopulation', '固定证券数'), ('sessions', '全部日数'), ('informationWindow', '实际信息窗口'),
                ('breadth20', '广度已知 / 未知日'), ('amountRatio', '金额已知 / 未知日'), ('continuousLower', '持续已知 / 未知日'), ('emptyCohortDays', '空队列日')]),
            query('epochs', epoch, [('epoch', '完整区间'), ('sessions', '日数'), ('breadthMean', '广度日均（%）'), ('continuousLower', '持续下界日均（%）'),
                ('continuousUpper', '持续上界日均（%）'), ('amountRatio', '相对成交额日均（倍）'), ('matchedChange', '匹配广度变化日均（百分点）'),
                ('priceCoverage', '最低价格覆盖（%）'), ('matchedDays', '匹配已知 / 未知日')]),
            query('continuous-account-phases', ledger, [('phase', '阶段'), ('policy', '原退出'), ('sessions', '日数'), ('enteringEquity', '进入权益（元）'),
                ('endingEquity', '结束权益（元）'), ('netPnlAmount', '净值变化（元）'), ('returnPct', '连续阶段收益（%）'), ('fills', '买入 / 卖出成交笔数'),
                ('endingPositionCount', '末期持仓数'), ('valuationFlagDays', '估值不可靠日')])]
        chart_rows = []
        for r in phase:
            for metric, series in zip(('breadth20', 'continuousLower'), SERIES):
                chart_rows.append({'阶段': PHASE_LABELS[r['phaseId']], '指标': series, VALUE: pct(r[metric]['mean']),
                    '阶段全名': r['phaseTitle'], '形成日期': r['observedStart'] + ' 至 ' + r['observedEnd'], '阶段日数': r['sessions'],
                    '已知日数': r[metric]['knownDays'], '未知日数': r[metric]['unknownDays'],
                    '留存日均上界（%）': pct(r['continuousUpper']['mean']) if metric == 'continuousLower' else None})
        outputs[board + 'Chart'] = {'schemaVersion': 1, 'id': board + '-environment-phases', 'queryId': 'phase-environment',
            'title': BOARD_LABELS[board] + '：上涨广度与强势持续', 'height': 640, 'theme': 'codex-classic',
            'chart': {'type': 'horizontalBar', 'x': '阶段', 'y': VALUE, 'series': '指标', 'startAtZero': True, 'sortOrder': 'original',
                'xLabel': VALUE, 'showXAxisLabel': True, 'showYAxisLabel': False, 'showValues': False,
                'categoryOrder': list(PHASE_LABELS.values()), 'seriesOrder': list(SERIES)},
            'rows': chart_rows, 'columns': list(chart_rows[0]), 'source': source}
        outputs[board + 'Sources'] = {'schemaVersion': 1, 'items': [{'id': board + '-environment-evidence',
            'title': BOARD_LABELS[board] + '16阶段：环境、覆盖与原账户', 'queries': queries}]}
        require([len(q['rows']) for q in queries] == [16, 16, 16, 3, 32], 'Complete Sources matrix differs')
    for obj in outputs.values():
        text = json.dumps(obj, ensure_ascii=False, allow_nan=False)
        require(len(text.encode()) < 1_000_000 and 'G:/' not in text and 'C:/' not in text, 'Private or oversized payload')
    return outputs


def build(directory=OUT):
    directory = Path(directory).resolve()
    plan, data, proof = (read(directory / name) for name in ('plan.json', 'readout.json', 'validation.json'))
    pins = {name: sha(directory / name) for name in ('plan.json', 'readout.json', 'validation.json')}
    require(proof['passed'] and proof['planSha256'] == pins['plan.json'] == data['plan']['sha256'] and proof['readoutSha256'] == pins['readout.json'], 'Independent proof required')
    require(proof['verifierSha256'] == sha(ROOT / 'scripts/verify-board-environment-context.py'), 'Independent verifier changed')
    require(proof['artifacts'] == data['artifacts'] and data['continuousAccountSource'] == plan['continuousAccounts'], 'Artifact provenance differs')
    for refs in (plan['sourcePins'], plan['inputPins']):
        for name, expected in refs.items():
            path = (ROOT / name).resolve()
            require(path.is_relative_to(ROOT) and sha(path) == expected, 'Frozen source changed: ' + name)
    outputs = payloads(data, (ROOT / 'scripts/build-board-environment-context.py').read_text('utf-8'))
    require(not (directory / 'inline').exists() and not (directory / 'render-receipt.json').exists(), 'Never overwrite presentation')
    (directory / 'inline').mkdir()
    fragments, payload_refs = {}, {}
    for key, payload in outputs.items():
        board = next(b for b in BOARDS if key.startswith(b))
        kind = 'chart' if key.endswith('Chart') else 'sources'
        source, target = directory / (board + '-' + kind + '.json'), directory / 'inline' / ('board-environment-' + board + '-' + kind + '.html')
        write(source, payload)
        renderer = TOOLS / ('render-inline-' + kind + '.mjs')
        result = subprocess.run([NODE, str(renderer), '--input', str(source), '--output', str(target)], capture_output=True, text=True, timeout=60)
        write(directory / (board + '-' + kind + '-render-log.json'), {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        require(result.returncode == 0, result.stderr)
        require(target.stat().st_size < 1_000_000, 'Oversized fragment')
        fragments[key] = {'path': target.relative_to(directory).as_posix(), 'sha256': sha(target), 'rendererSha256': sha(renderer)}
        payload_refs[key] = {'path': source.name, 'sha256': sha(source)}
    require(all(sha(directory / name) == value for name, value in pins.items()), 'Analysis changed during presentation')
    repair = directory / 'browser-qa/attempt-0001/presentation-before-label-repair/archive.json'
    write(directory / 'render-receipt.json', {'schema': 'board-environment-context-render-v1', 'inputPins': pins, 'builderSha256': sha(__file__),
        'fragments': fragments, 'payloads': payload_refs, 'deliveryOrder': list(outputs), 'chartRows': 96, 'chartRowsByBoard': {b: 32 for b in BOARDS},
        'tableRows': TABLE_ROWS, 'posthocDisplayMetricSelection': True, 'fixedAxisMaximumSupported': False,
        'actualMarketRuns': 0, 'analysisArtifactsModified': False, 'browserChecked': False, 'includes2026': False, 'holdout': False,
        **({'presentationRepair': {'reason': 'Date-prefixed phase labels prevent native Chinese-label truncation.',
            'priorAttemptPreserved': 'browser-qa/attempt-0001', 'archive': {'path': repair.relative_to(directory).as_posix(), 'sha256': sha(repair)}}} if repair.exists() else {})})
    return {'renderReceiptSha256': sha(directory / 'render-receipt.json'), 'fragments': fragments, 'tableRows': TABLE_ROWS}


def synthetic():
    result = {'phaseRows': [], 'epochRows': [], 'continuousAccountRows': [], 'newStrategyTrials': 0, 'newAccounts': 0,
        'holdoutClaim': False, 'certified': False, 'targetAchieved': False, 'dailyCount': 480}
    for board in BOARDS:
        for i, (phase_id, _) in enumerate(PHASES):
            p = PERIODS[int(i >= 8)]
            row = {'period': p, 'board': board, 'phaseId': phase_id, 'phaseTitle': '合成阶段' + str(i),
                'observedStart': p[:4] + '-02-02', 'observedEnd': p[:4] + '-02-12', 'asOfStart': p[:4] + '-02-01',
                'asOfEnd': p[:4] + '-02-11', 'sessions': 10, 'fixedPopulation': 100, 'emptyFrozenCohortDays': 0}
            for metric in METRICS:
                row[metric] = {'knownDays': 10, 'unknownDays': 0, 'mean': .5, 'median': .5, 'minimum': .4, 'maximum': .6}
            row['continuousUpper'] = {**row['continuousUpper'], 'mean': .6}
            result['phaseRows'].append(row)
            for mode in POLICIES:
                result['continuousAccountRows'].append({'period': p, 'board': board, 'policy': mode, 'phaseId': phase_id,
                    'observedStart': row['observedStart'], 'observedEnd': row['observedEnd'], 'observedSessions': 10,
                    'enteringEquity': 500000., 'endingEquity': 505000., 'netPnlAmount': 5000., 'returnPct': 1.,
                    'buyFillCount': 0, 'sellFillCount': 0, 'endingPositionCount': 1, 'valuationFlagDays': 2})
        for epoch in ('2022-2023', 'before20240924', 'from20240924'):
            result['epochRows'].append({**deepcopy(row), 'epoch': epoch})
    return result


def self_test():
    data = synthetic(); before = deepcopy(data); out = payloads(data, '# Synthetic fixture, no historical result.')
    require(data == before and len(out) == 6 and [len(out[b+'Chart']['rows']) for b in BOARDS] == [32]*3, 'Mutation or chart omissions')
    require([len(out[b+'Sources']['items'][0]['queries']) for b in BOARDS] == [5]*3, 'Sources incomplete')
    changed = deepcopy(data); changed['phaseRows'][0]['continuousLower'].update(knownDays=0, unknownDays=10, mean=None, median=None, minimum=None, maximum=None)
    changed['phaseRows'][0]['continuousUpper'].update(knownDays=0, unknownDays=10, mean=None, median=None, minimum=None, maximum=None)
    require(payloads(changed, '# sample')['mainChart']['rows'][1][VALUE] is None, 'Unknown painted as zero')
    for kind in ('identity', 'unknown-days', 'money', 'clock', 'bound'):
        bad = deepcopy(data)
        if kind == 'identity': bad['phaseRows'][-1] = deepcopy(bad['phaseRows'][0])
        elif kind == 'unknown-days': bad['phaseRows'][0]['priceCoverage']['unknownDays'] = 1
        elif kind == 'money': bad['continuousAccountRows'][0]['netPnlAmount'] = 1
        elif kind == 'clock': bad['phaseRows'][0]['asOfEnd'] = bad['phaseRows'][0]['observedEnd']
        else: bad['phaseRows'][0]['continuousLower']['mean'] = .7
        try: payloads(bad, '# sample')
        except ValueError: pass
        else: raise AssertionError('Bad fixture accepted: '+kind)
    code = "import fs from 'node:fs';import {normalizeInlineChartInput} from %s;import {normalizeInlineSourcesInput} from %s;const p=JSON.parse(fs.readFileSync(0,'utf8'));for(const [k,v] of Object.entries(p)){k.endsWith('Chart')?normalizeInlineChartInput(v):normalizeInlineSourcesInput(v)}" % (
        json.dumps((TOOLS/'inline-chart-input.mjs').as_uri()), json.dumps((TOOLS/'inline-sources-input.mjs').as_uri()))
    checked = subprocess.run([NODE, '--input-type=module', '-e', code], input=json.dumps(out, ensure_ascii=False), capture_output=True, text=True, timeout=30)
    require(checked.returncode == 0, checked.stderr)
    return {'passed': True, 'checks': ['six shared fragments', 'same16 phases across boards', '48 environments and96 old account intervals',
        'complete249 source rows', 'null not zero', 'five invalid fixtures rejected', 'T-1 and count/bound checks', 'unmodified input', 'shared validators'], 'filesWritten': 0}


if __name__ == '__main__':
    parser = ArgumentParser(); parser.add_argument('--directory', type=Path, default=OUT); parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args(); print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False))
