"""Presentation of audited saved-account arithmetic; never run research."""
from argparse import ArgumentParser
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/reclaim-exit-mechanism-v1'
NODE = 'C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
TOOLS = Path('C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts')
METHOD = 'service/research_exit_mechanism.py'
RUNNER = 'scripts/diagnose-reclaim-exit-mechanism.py'
VERIFIER = 'scripts/verify-reclaim-exit-mechanism.py'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
BOARD_LABELS = dict(zip(BOARDS, ('主板', '创业板', '科创板')))
POLICIES = ('trail10', 'volatilityTrail')
POLICY_LABELS = dict(zip(POLICIES, ('固定跟踪', '波动跟踪')))
GROUPS = ('bothSameEntry', 'bothDifferentEntry', 'controlOnly', 'candidateOnly', 'neither')
GROUP_LABELS = dict(zip(GROUPS, ('双方同日同价入场', '双方入场但日价不同', '仅固定入场', '仅波动入场', '双方未入场')))
STATES = ('closed', 'openAtCutoff', 'notEntered')
STATE_LABELS = dict(zip(STATES, ('闭仓', '期末未平', '未入场')))
COMPONENTS = ('returnProfileComponent', 'committedCapitalComponent', 'candidateOnlyComponent', 'controlOnlyComponent')
COMPONENT_LABELS = dict(zip(COMPONENTS, ('双方入场：收益率差', '双方入场：投入差', '仅波动入场盈亏', '仅固定入场盈亏取反')))
EPOCHS = {'2022-2023': ('oldPeriod',), '2024-2025': ('before20240924', 'from20240924')}
EPOCH_LABELS = {'oldPeriod': '2022—2023', 'before20240924': '2024·9·24之前', 'from20240924': '2024·9·24日起'}
VALUE = '对净盈亏差的贡献（万元）'
TITLE = '波动跟踪减固定跟踪：四项净盈亏差额'
TABLES = ('accountDifferenceRows', 'groupRows', 'maturityRows', 'formationYearRows', 'formationEpochRows',
    'closedPairTimingRows', 'closedPairBaselineOutcomeRows', 'accountBridges', 'curveEpochRows')
TABLE_ROWS = [[6, 30, 54, 60, 45, 18, 18, 12, 12]]
LIMITS = [
    '图中四项为波动跟踪减固定跟踪的有符号金额分解，单位万元；四项合计再加原账户未分配残差之差才等于完整账户净盈亏差，残差与总差单列。',
    '双方入场包括实际买入日及成交价相同或不同的两类，来源表分别保留；同日同价仍可有不同投入金额。两项共同分解是代数恒等式，不是因果收益。',
    '收益率差项用两侧平均投入乘两侧回报率之差；投入差项用两侧平均回报率乘投入之差。投入是实际买入金额加买入费，成交价已含滑点，不重复扣除。',
    '仅波动入场项是该侧实际盈亏；仅固定入场项取原固定侧盈亏的相反数。未入场的零值仅用于会计加总，不是虚构的零收益交易。',
    '未平仓项包含截止日浮盈亏及未平仓中的已实现盈亏，不等同完整闭仓。九种闭仓、未平、未入场组合全部保留，陈旧估值和未解决退出限制继续存在。',
    '退出早晚和原固定侧盈亏分组仅限双方同日同价入场且均已闭仓的子集；按实际退出日期事后分类，不能作为当时可用的交易筛选条件。',
    '退出方式会改变持仓、资金和后来实际成交集合；相同原信号不代表独立随机配对试验，较长持仓或某个正差不能证明能持续留住赢家。',
    '完整交易按原形成年度及9月24日前后归属，盈亏可发生在以后；连续权益区间另列，继承进入资金与跨段持仓，不重启账户。',
    '全部8155原形成机会、两时期、三板块及12个原账户保留；每账户独立起资，不能把金额相加当共享资金组合收益，板块不等同科技行业。',
    '本轮没有新账户、信号、准入条件、退出规则或收益门槛；只使用已反复研究的2022—2025历史，2026未纳入，不是留出检验，原价格、历史状态和估值质量限制保留。',
]

def require(value, message):
    if not value: raise ValueError(message)

def sha(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()

def read(path): return json.loads(Path(path).read_text('utf-8'))

def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write('\n')

def finite(value): return type(value) in (int, float) and math.isfinite(value)

def near(a, b): return finite(a) and finite(b) and math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-7)

def encoded(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)

def matrix(rows, fields, expected):
    keys = [tuple(r[k] for k in fields) for r in rows]
    require(len(keys) == len(set(keys)) and set(keys) == expected, 'Incomplete/duplicate matrix: ' + '/'.join(fields))

def validate(data):
    for key, n in zip(TABLES, TABLE_ROWS[0]): require(len(data[key]) == n, 'Missing complete table ' + key)
    matrix(data['accountDifferenceRows'], ('period', 'board'), {(p, b) for p in PERIODS for b in BOARDS})
    specs = [('groupRows', 'group', GROUPS), ('maturityRows', 'maturity', tuple(a + '__' + b for a in STATES for b in STATES)),
        ('closedPairTimingRows', 'closedPairTiming', ('later', 'same', 'earlier')),
        ('closedPairBaselineOutcomeRows', 'baselineOutcome', ('win', 'loss', 'zero'))]
    for key, field, values in specs:
        matrix(data[key], ('period', 'board', field), {(p, b, v) for p in PERIODS for b in BOARDS for v in values})
    for key, field, values in (('formationYearRows', 'formationYear', {p: p.split('-') for p in PERIODS}),
        ('formationEpochRows', 'formationEpoch', EPOCHS)):
        matrix(data[key], ('period', 'board', field, 'group'), {(p, b, v, g) for p in PERIODS for b in BOARDS for v in values[p] for g in GROUPS})
    matrix(data['accountBridges'], ('period', 'board', 'policy'), {(p, b, m) for p in PERIODS for b in BOARDS for m in POLICIES})
    matrix(data['curveEpochRows'], ('period', 'board', 'policy', 'phaseId'), {(PERIODS[1], b, m, e) for b in BOARDS for m in POLICIES for e in EPOCHS[PERIODS[1]]})
    require(sum(r['count'] for r in data['accountDifferenceRows']) == data['pairCount'], 'All original pairs required')
    for key in TABLES[:7]:
        for row in data[key]:
            require(type(row['count']) is int and row['count'] >= 0, 'Invalid population count')
            require(near(math.fsum(row[k] for k in COMPONENTS), row['deltaPnl'])
                and near(row['candidatePnl'] - row['controlPnl'], row['deltaPnl']), 'Signed components do not reconcile')
    for row in data['accountDifferenceRows']:
        require(row['passed'] is True and near(row['deltaPnl'] + row['residualDelta'], row['accountNetPnlDelta']), 'Full account difference bridge differs')
    for row in data['accountBridges']:
        require(row['passed'] is True and near(row['assignedPnl'] + row['unallocatedResidual'], row['netPnlAmount']), 'Original account bridge differs')

def flat(row):
    r = deepcopy(row); r['boardLabel'] = BOARD_LABELS[r['board']]
    for field, mapping in (('policy', POLICY_LABELS), ('group', GROUP_LABELS), ('formationEpoch', EPOCH_LABELS), ('phaseId', EPOCH_LABELS),
        ('closedPairTiming', {'later': '波动退出更晚', 'same': '同日退出', 'earlier': '波动退出更早'}),
        ('baselineOutcome', {'win': '固定侧盈利', 'loss': '固定侧亏损', 'zero': '固定侧持平'})):
        if field in r: r[field + 'Label'] = mapping[r[field]]
    if 'maturity' in r: r['maturityLabel'] = '固定' + STATE_LABELS[r['maturity'].split('__')[0]] + ' / 波动' + STATE_LABELS[r['maturity'].split('__')[1]]
    if 'controlClosedCount' in r:
        r['maturitySummary'] = f"固定闭{r['controlClosedCount']}/未平{r['controlOpenCount']}；波动闭{r['candidateClosedCount']}/未平{r['candidateOpenCount']}"
        r['openPnlSummary'] = '固定已实现/未实现 ' + str(r['controlOpenRealizedPnlAmount']) + ' / ' + str(r['controlOpenUnrealizedPnlAmount']) + '；波动已实现/未实现 ' + str(r['candidateOpenRealizedPnlAmount']) + ' / ' + str(r['candidateOpenUnrealizedPnlAmount'])
    return r

def payloads(data, method):
    validate(data)
    src = {'label': '既有支撑收回账户的退出差额分解',
        'files': [{'label': n} for n in ('plan.json', 'readout.json', 'validation.json', 'pairs.jsonl.gz', METHOD, RUNNER, 'reclaim-volatility-trail-v1/readout.json')],
        'metricDefinitions': [{'label': '四项分解', 'definition': '四项按波动减固定的同一方向保留正负号；未分配残差和完整账户总差另列，金额单位由表头说明。'},
            {'label': '成熟度边界', 'definition': '闭仓净盈亏与未平仓已实现、未实现盈亏保持原账本口径；退出时点和原固定侧盈亏分析只纳入双方同日同价且都闭仓的实际子集。'}],
        'filters': ['全部两时期、三板块、12既有账户及8155原机会', '形成日期决定年度和事件分段', '不增加或重跑交易策略'],
        'caveats': LIMITS, 'evidenceFlow': [{'kind': 'validation', 'title': '机会与资金差额复核',
            'detail': '独立审计核对所有原机会的两侧实际入场、闭仓与期末持仓，完整群体和成熟度矩阵，以及四项差额、未分配残差和12个原账户资金桥。'}]}
    columns = [('period', '时期'), ('boardLabel', '板块')]
    money = [('controlPnl', '固定侧盈亏（元）'), ('candidatePnl', '波动侧盈亏（元）'), ('deltaPnl', '波动减固定（元）')]
    values = {key: [flat(row) for row in data[key]] for key in TABLES}
    for row in values['accountBridges']:
        row['population'] = f"形成{row['formationCount']} / 入场{row['entered']} / 闭{row['closed']} / 未平{row['open']}"
        row['quality'] = f"估值不可靠{row['valuationUnreliableDays']}日；陈旧持仓{row['openMarkStaleCount']}；数值未知{row['numericUnknownCount']}；完整性问题{row['integrityIssueCount']}"
    for row in values['curveEpochRows']:
        row['meanExposurePct'] = row['exposurePct']['mean']; row['valuationDays'] = len(row['valuationUnreliableDates'])
    def query(key, fields, include_method=False):
        require(all(all(k in r for k, _ in fields) for r in values[key]), 'Missing projection: ' + key)
        q = {'id': re.sub(r'(?<!^)(?=[A-Z])', '-', key).lower(), 'rows': [{k: r[k] for k, _ in fields} for r in values[key]], 'columns': [{'field': k, 'label': label} for k, label in fields],
            'source': src, 'reportingPeriod': '2022—2023与2024—2025独立账户；9月24日只作描述分界'}
        if include_method: q['methods'] = [{'language': 'python', 'code': method}]
        return q
    queries = [query('accountDifferenceRows', columns + [(k, COMPONENT_LABELS[k] + '（元）') for k in COMPONENTS] + [('residualDelta', '未分配残差差（元）'), ('accountNetPnlDelta', '完整账户净差（元）')], True),
        query('groupRows', columns + [('groupLabel', '实际入场组'), ('count', '原机会数')] + money + [('maturitySummary', '闭仓 / 期末未平')]),
        query('maturityRows', columns + [('maturityLabel', '两侧成熟度'), ('count', '原机会数'), ('controlClosedPnlAmount', '固定闭仓盈亏（元）'), ('candidateClosedPnlAmount', '波动闭仓盈亏（元）'), ('openPnlSummary', '未平盈亏（元）'), ('deltaPnl', '净差（元）')]),
        query('formationYearRows', [('formationYear', '形成年度'), ('boardLabel', '板块'), ('groupLabel', '实际入场组'), ('count', '机会数')] + money + [('maturitySummary', '闭仓 / 未平')]),
        query('formationEpochRows', [('formationEpochLabel', '形成日期区间'), ('boardLabel', '板块'), ('groupLabel', '实际入场组'), ('count', '机会数')] + money + [('maturitySummary', '闭仓 / 未平')]),
        query('closedPairTimingRows', columns + [('closedPairTimingLabel', '同日同价双闭仓：退出时点'), ('count', '实际配对数')] + money + [('meanReturnDeltaPct', '配对回报率差均值（百分点）')]),
        query('closedPairBaselineOutcomeRows', columns + [('baselineOutcomeLabel', '同日同价双闭仓：固定侧结果'), ('count', '实际配对数')] + money + [('meanReturnDeltaPct', '配对回报率差均值（百分点）')]),
        query('accountBridges', columns + [('policyLabel', '退出'), ('population', '完整分母'), ('assignedPnl', '归属盈亏（元）'), ('unallocatedResidual', '未分配残差（元）'), ('netPnlAmount', '账户净盈亏（元）'), ('quality', '原质量限制')]),
        query('curveEpochRows', [('boardLabel', '板块'), ('policyLabel', '退出'), ('phaseIdLabel', '原连续区间'), ('returnPct', '区间收益（%）'), ('maxDrawdownPct', '区间回撤（%）'), ('meanExposurePct', '平均仓位（%）'), ('endingPositionCount', '末日持仓'), ('valuationDays', '估值不可靠日')])]
    sources = {'schemaVersion': 1, 'items': [{'id': 'saved-exit-mechanism', 'title': '四项差额、入场路径与闭仓边界', 'queries': queries}]}
    category = lambda p, b: ('22–23' if p == PERIODS[0] else '24–25') + {'main': '主', 'chinext': '创', 'star': '科'}[b]
    chart_rows = [{'period': r['period'], 'boardLabel': BOARD_LABELS[r['board']], 'category': category(r['period'], r['board']),
        'component': k, 'componentLabel': COMPONENT_LABELS[k], VALUE: r[k] / 10000} for r in data['accountDifferenceRows'] for k in COMPONENTS]
    chart = {'schemaVersion': 1, 'id': 'reclaim-exit-mechanism', 'queryId': 'account-difference-rows', 'title': TITLE, 'height': 620,
        'chart': {'type': 'horizontalBar', 'x': 'category', 'y': VALUE, 'series': 'componentLabel', 'stackable': False,
            'startAtZero': True, 'sortOrder': 'original', 'showValues': True, 'valueDecimals': 2, 'xLabel': VALUE,
            'showXAxisLabel': True, 'showYAxisLabel': False, 'categoryOrder': [category(p, b) for p in PERIODS for b in BOARDS],
            'seriesOrder': list(COMPONENT_LABELS.values()), 'colors': {v: f'var(--chart-{i+1})' for i, v in enumerate(COMPONENT_LABELS.values())}},
        'rows': chart_rows, 'columns': list(chart_rows[0]), 'source': src, 'theme': 'codex-classic'}
    require([[len(q['rows']) for q in item['queries']] for item in sources['items']] == TABLE_ROWS, 'Source rows incomplete')
    for payload in (chart, sources):
        text = encoded(payload); require(len(text.encode()) < 1_000_000 and 'G:/' not in text and 'C:/' not in text, 'Oversized/private projection')
    return {'chart': chart, 'sources': sources}

def build(directory=OUT):
    directory = Path(directory).resolve(); names = ('plan.json', 'readout.json', 'validation.json')
    values, pins = {n: read(directory/n) for n in names}, {n: sha(directory/n) for n in names}
    plan, data, proof = (values[n] for n in names)
    require(proof['passed'] is True and proof['planSha256'] == data['planSha256'] == pins['plan.json'] and proof['readoutSha256'] == pins['readout.json'], 'Independent diagnostic proof required')
    require(proof['verifierSha256'] == plan['sourcePins'][VERIFIER] == sha(ROOT/VERIFIER), 'Independent verifier changed')
    pair_path = ROOT/data['pairs']['path']; require(pair_path.resolve() == directory/'pairs.jsonl.gz' and sha(pair_path) == data['pairs']['sha256'] == proof['pairsSha256'], 'Paired evidence changed')
    require(data['pairCount'] == 8155 and data['trialAccounting']['newAccounts'] == 0 and data['profitHurdles'] is None and data['selectedRule'] is None, 'Diagnostic scope changed')
    for name in (METHOD, RUNNER): require(plan['sourcePins'][name] == sha(ROOT/name), 'Frozen source changed')
    outputs = payloads(data, (ROOT/METHOD).read_text('utf-8'))
    require(not any((directory/n).exists() for n in ('inline','chart.json','sources.json','render-receipt.json')), 'Do not overwrite presentation')
    (directory/'inline').mkdir(); fragments, payload_refs = {}, {}
    for kind, value in outputs.items():
        source, target = directory/(kind+'.json'), directory/'inline'/('reclaim-exit-mechanism-'+kind+'.html'); write(source,value)
        run = subprocess.run([NODE,str(TOOLS/('render-inline-'+kind+'.mjs')),'--input',str(source),'--output',str(target)],text=True,capture_output=True,timeout=60)
        write(directory/(kind+'-render-log.json'),{'returncode':run.returncode,'stdout':run.stdout,'stderr':run.stderr});require(run.returncode==0,run.stderr)
        fragments[kind]={'path':target.relative_to(directory).as_posix(),'sha256':sha(target)};payload_refs[kind]={'path':source.name,'sha256':sha(source)}
    require(all(sha(directory/n)==v for n,v in pins.items()),'Analysis files changed')
    write(directory/'render-receipt.json',{'schema':'reclaim-exit-mechanism-render-v1','inputPins':pins,'pairs':data['pairs'],
        'builderSha256':sha(__file__),'deliveryOrder':['chart','sources'],'fragments':fragments,'payloads':payload_refs,
        'chartRows':6,'chartRecords':24,'tableRows':TABLE_ROWS,'actualMarketRuns':0,'analysisArtifactsModified':False,'browserChecked':False})
    return {'renderReceiptSha256':sha(directory/'render-receipt.json'),'fragments':fragments,'tableRows':TABLE_ROWS}

def self_test():
    data = {key: [] for key in TABLES}
    def metric(i=1):
        parts = dict(zip(COMPONENTS,(100.*i,-20.*i,0.,-30.*i))); delta=sum(parts.values())
        return {'count':i,'controlPnl':-10.*i,'candidatePnl':-10.*i+delta,'deltaPnl':delta,**parts,'meanReturnDeltaPct':1. if i else None,
            **{side+k:v for side in ('control','candidate') for k,v in {'ClosedCount':i,'OpenCount':0,'ClosedPnlAmount':0.,'OpenRealizedPnlAmount':0.,'OpenUnrealizedPnlAmount':0.}.items()}}
    for p in PERIODS:
        for b in BOARDS:
            ident={'period':p,'board':b};data['accountDifferenceRows'].append({**ident,**metric(),'residualDelta':5.,'accountNetPnlDelta':55.,'passed':True})
            for table,field,vals in [('groupRows','group',GROUPS),('maturityRows','maturity',[a+'__'+b for a in STATES for b in STATES]),
                ('closedPairTimingRows','closedPairTiming',('later','same','earlier')),('closedPairBaselineOutcomeRows','baselineOutcome',('win','loss','zero'))]:
                data[table].extend({**ident,field:v,**metric(int(i==0))} for i,v in enumerate(vals))
            for table,field,vals in [('formationYearRows','formationYear',p.split('-')),('formationEpochRows','formationEpoch',EPOCHS[p])]:
                data[table].extend({**ident,field:v,'group':g,**metric(int(i==0))} for v in vals for i,g in enumerate(GROUPS))
            for m in POLICIES:
                data['accountBridges'].append({**ident,'policy':m,'formationCount':1,'entered':1,'closed':1,'open':0,'assignedPnl':100.,'unallocatedResidual':5.,'netPnlAmount':105.,'passed':True,'valuationUnreliableDays':2,'openMarkStaleCount':0,'numericUnknownCount':0,'integrityIssueCount':0})
                if p==PERIODS[1]:data['curveEpochRows'].extend({**ident,'policy':m,'phaseId':e,'returnPct':1.,'maxDrawdownPct':2.,'exposurePct':{'mean':10.},'endingPositionCount':0,'valuationUnreliableDates':[]} for e in EPOCHS[p])
    data['pairCount']=6;before=deepcopy(data);out=payloads(data,'# Synthetic only')
    require(before==data and len(out['chart']['rows'])==24 and sum(TABLE_ROWS[0])==255,'Complete unchanged projection')
    require(out['chart']['rows'][0][VALUE]==.01 and out['chart']['rows'][1][VALUE]==-.002,'Money units or sign changed')
    require(out['sources']['items'][0]['queries'][5]['rows'][1]['meanReturnDeltaPct'] is None,'Empty mean became zero')
    for key in TABLES:
        bad=deepcopy(data);bad[key].pop()
        try:payloads(bad,'# synthetic')
        except ValueError:pass
        else:raise AssertionError('Missing table row accepted '+key)
    code="""import fs from 'node:fs';import{normalizeInlineChartInput}from %s;import{normalizeInlineSourcesInput}from %s;const p=JSON.parse(fs.readFileSync(0,'utf8'));normalizeInlineChartInput(p.chart);normalizeInlineSourcesInput(p.sources);"""%(json.dumps((TOOLS/'inline-chart-input.mjs').as_uri()),json.dumps((TOOLS/'inline-sources-input.mjs').as_uri()))
    run=subprocess.run([NODE,'--input-type=module','-e',code],input=json.dumps(out,ensure_ascii=False),text=True,capture_output=True,timeout=30);require(run.returncode==0,run.stderr)
    return {'passed':True,'checks':['all nine matrices','six categories and four components','255 preview rows','signed ten-thousand-yuan units','open/closed boundary','null empty means','nine missing-row cases rejected','no mutation','shared renderer validators'],'filesWritten':0}

if __name__=='__main__':
    parser=ArgumentParser();parser.add_argument('--directory',type=Path,default=OUT);parser.add_argument('--self-test',action='store_true');a=parser.parse_args()
    print(json.dumps(self_test() if a.self_test else build(a.directory),ensure_ascii=False))
