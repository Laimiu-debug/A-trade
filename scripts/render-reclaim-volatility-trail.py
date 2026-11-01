"""Presentation only: one volatility-trail contrast, with audited full evidence.

No signal, account or summary execution. Synthetic mode never reads outcomes.
"""
from argparse import ArgumentParser
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/reclaim-volatility-trail-v1'
HELPER = ROOT / 'scripts/render-strength-feedback.py'
HELPER_SHA = 'bed327c3a4ba9ffc8a4041190c9a913b6b0ca14000dcf0da06559af5c42539d4'
spec = importlib.util.spec_from_file_location('_exit_presentation_helpers', HELPER)
BASE = importlib.util.module_from_spec(spec); spec.loader.exec_module(BASE)
require, sha, read, write, finite = BASE.require, BASE.sha, BASE.read, BASE.write, BASE.finite
require(sha(HELPER) == HELPER_SHA, 'Presentation helper changed')
NODE, TOOLS = BASE.NODE, BASE.TOOLS
METHOD = 'service/research_volatility_trail.py'
RUNNER = 'scripts/run-reclaim-volatility-trail.py'
PERIODS, BOARDS, BOARD_LABELS = BASE.PERIODS, BASE.BOARDS, BASE.BOARD_LABELS
MODES = ('trail10', 'volatilityTrail')
MODE_LABELS = dict(zip(MODES, ('固定百分比跟踪', '波动比例跟踪')))
CONTRASTS = {'volatilityTrailVsTrail10': ('trail10','volatilityTrail')}
VALUE = '累计净收益差（百分点）'
TITLE = '波动比例跟踪减固定百分比跟踪：净收益差'
TABLE_ROWS = [[12, 6, 12, 12, 6, 12, 24, 48, 96, 12]]
BASE_ID = BASE.BASE_ID
POLICY_ID = BASE_ID + [('policyLabel', '退出政策')]
ACCOUNT_FIELDS, CONTRAST_FIELDS = BASE.ACCOUNT_FIELDS, BASE.CONTRAST_FIELDS
TAIL_FIELDS = BASE.TAIL_FIELDS + [('openUnrealizedPnlPctOfFinalEquity', '浮盈占期末权益（%）')]
INTERVAL_FIELDS = BASE.INTERVAL_FIELDS + [('structureUnknown', '额外退出未知观察')]
EXIT_FIELDS = [('configured','波动退出已配置'),('observationCount','持仓日观察'),
    ('knownWindows','窗口已知'),('unknownWindows','窗口未知'),('invalidDistanceWindows','距离无效'),
    ('triggered','已触发'),('notTriggered','未触发'),('unknown','决策未知'),('skippedExistingExit','已有退出优先'),('skippedStandardExit','标准退出优先'),
    ('carriedThresholdObservations','沿用旧阈值观察'),('carriedThresholdTriggers','沿旧阈值触发'),
    ('uniqueUnavailableWindowHoldingEpisodes','曾窗口不可用持仓段'),('uniqueUnknownDecisionHoldingEpisodes','曾决策未知持仓段'),
    ('knownWindowWidthCount','已知窗口距离观察数'),('medianKnownWidthPct','已知窗口距离中位数（%）'),
    ('knownThresholdCount','已知阈值观察数'),('medianKnownRatio','阈值/入场风险价中位数'),
    ('windowDecisionCounts','窗口×决策计数'),('windowIssues','窗口问题计数'),('decisionReasons','决策原因计数'),
    ('sellReasons','实际卖出成交原因计数'),('queuedReasons','排队卖出原因计数')]
FORMATION_FIELDS = [('includedCount', '固定纳入身份'), ('excludedCount', '期初排除'), ('unknownCount', '身份未知'),
    ('formationTop5', '原始形成机会'), ('sourceImmediateSignals', '共同立即入场信号'),
    ('sameScheduleForEveryPolicy', '两政策信号完全相同'), ('benchmarkUnavailableDays', '基准不可用日'),
    ('formationEvidence', '形成资格计数')]
COVERAGE_FIELDS = [('formationTop5', '原始形成机会'), ('selectedSignals', '安排信号'),
    ('notScheduledFromFormation', '未安排'), ('entered', '成交入场'), ('notEntered', '安排但未成交'),
    ('closedCount', '完整平仓'), ('openCountAtCutoff', '期末持仓'), ('unresolvedExitCountAtCutoff', '未解决退出'),
    ('valuationUnreliableDays', '估值不可靠日'), ('qualityFlags', '源质量标记'), ('terminalReasons', '账户终态原因计数')]
LIMITS = [
    '主图比较波动比例跟踪减固定百分比跟踪的完整账户净收益差，同时保留仓位差、回撤和两账户估值不可靠日；正差不能证明稳定或因果优势。',
    '六个固定百分比跟踪账户从上轮直接复用，六个波动比例跟踪账户新运行；全部原支撑收回五字段信号、排名和日期不变，无新增入场筛选或板块选择。',
    '两政策均保留5%初始收盘止损且无第五日退出；候选只替换10%固定跟踪，用截至持仓日前一日的21个精确源交易槽计算20个归一化真实波幅的简单均值，再乘3。',
    '波动窗口已知才可产生新候选阈值；阈值在入场风险价单位中只上移，未知或距离无效时保留旧阈值，当前收盘有效仍可能触发，所以窗口未知不等于退出决策未知。',
    '已有强制退出与新标准退出分别优先，额外规则不覆盖原订单；当前价格无效时不更新或触发新规则。长期未知或陈旧估值下持仓不能解释为趋势成功。',
    '距离分布只含窗口已知的持仓日观察，阈值比例分布只含已知阈值观察；两者分母单列且不是独立交易数。原始距离0.1显示为10%，阈值比例以入场风险价为1。',
    '收盘触发最早后续可成交开盘执行；原费用、每边10基点滑点、涨跌停与成交限制保留，名义5%止损不是实际损失上限。',
    '退出改变持仓、资金和后来实际成交集合；账户差不是配对交易因果估计。已平仓分布、未平仓浮盈及部分已实现盈亏分开，未成交不算零收益交易。',
    '每账户独立50万元，两期与三板不能拼成共享资金组合；所有年份、半年、事后阶段和9月24日前后区间均继承净值和仓位，区间不等长且不是年化。',
    '仅研究已被反复检视的2022—2025历史，2026未纳入，非留出样本；固定本地身份、原始价格、公司行为和历史状态覆盖限制保留，板块不等于科技行业，若列超额收益均相对沪深300。',
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
        e = a['exitEvidence']; expected = 'meanTRPct20x3Ratchet' if a['policy']=='volatilityTrail' else None
        require(e['rule'] == expected and e['configured'] == (expected is not None)
            and sum(e['decisionCounts'].values()) == sum(e['windowCounts'].values()) == e['observationCount'], 'Exit evidence identity/count mismatch')
        width,ratio=e['knownWindowScaledDistancePct'],e['knownThresholdRatchetRatio']
        exits.append({**labels(a),**e,**e['decisionCounts'],
            'knownWindows':e['windowCounts']['known'],'unknownWindows':e['windowCounts']['unknown'],'invalidDistanceWindows':e['windowCounts']['invalidDistance'],
            'knownWindowWidthCount':width['count'],'medianKnownWidthPct':100*width['median'] if finite(width['median']) else None,
            'knownThresholdCount':ratio['count'],'medianKnownRatio':ratio['median'],
            'windowDecisionCounts':encoded(e['windowDecisionCounts']),'windowIssues':encoded(e['windowIssueCounts']),'decisionReasons':encoded(e['decisionReasonCounts']),
            'sellReasons':encoded(curve['sellFillReasons']),'queuedReasons':encoded(curve['queuedExitReasons'])})
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
    require(seen == {(p, b, x) for p in PERIODS for b in BOARDS for x in CONTRASTS}, 'All six contrasts required')
    intervals = []
    for key, count in (('yearRows', 24), ('halfYearRows', 48), ('phaseRows', 96), ('curveEpochRows', 12)):
        require(len(data[key]) == count, 'Incomplete ' + key)
        values, seen = [], set()
        for r in data[key]:
            identity = (r['period'], r['board'], r['policy'], r['requestedStart'], r['requestedEnd'])
            require(identity[:3] in by_account and identity not in seen, 'Duplicate interval'); seen.add(identity)
            label = r.get('phaseTitle') or r.get('phaseId') or r.get('id')
            require(label is not None, 'Missing interval label')
            values.append({**r, **labels(r), 'intervalLabel': label, 'meanExposurePct': r['exposurePct']['mean'],
                'valuationDays': len(r['valuationUnreliableDates']), 'structureUnknown': r['exitEvidence']['decisionCounts']['unknown']})
        intervals.append(values)
    return contrasts, accounts, exits, formation, coverage, intervals


def payloads(data, method):
    contrasts, accounts, exits, formation, coverage, intervals = tables(data)
    artifact=data['opportunityRowsArtifact']
    src={'label':'同一支撑收回信号的波动跟踪对照（2022—2025）',
        'files':[{'label':n} for n in ('plan.json','comparison.json','validation.json','readout.json','readout-validation.json',
            'research_volatility_trail.py','reclaim-exit-transfer-v1/readout.json','board-coverage-reclaim-v1/readout.json','market-phase-atlas-v1/atlas.json')]
            +[{'label':f"opportunity-dispositions.jsonl.gz：{artifact['count']}原机会×2政策；SHA256 {artifact['sha256']}"}],
        'metricDefinitions':[{'label':'主比较','definition':'六个板块和时期组合分别比较波动比例跟踪减固定百分比跟踪的完整账户累计净收益差，保留所有对照结果与风险。'},
            {'label':'退出观察','definition':'窗口状态与退出决策分别统计；沿旧阈值触发、标准和已有退出优先、未知观察与条件分布分母各自保留。'},
            {'label':'资金与分期','definition':'净收益包括费用、滑点及期末持仓；分期继承此前收盘净值与仓位，未平仓贡献和估值质量单列。'}],
        'filters':['两个历史时期、三个固定板块、两种退出政策，共12账户','原始立即入场信号逐字不变，无新增筛选或补位',
            '20个归一化真实波幅简单均值乘3，仅一组预先固定参数','2024年9月24日只作连续资金描述分界'],
        'caveats':LIMITS,'evidenceFlow':[{'kind':'validation','title':'账户与汇总核验',
            'detail':'独立复核12账户、180个子区间、6个有方向差值，以及全部原机会的两政策终态；窗口和决策状态、旧阈值触发及实际退出分别核对。'}]}
    def query(key,rows,columns,method_included=False):
        require(all(all(k in r for k,_ in columns) for r in rows),'Missing preview column '+key)
        item={'id':key,'rows':[{k:r[k] for k,_ in columns} for r in rows],
            'columns':[{'field':k,'label':label} for k,label in columns],'source':src,
            'reportingPeriod':'2022—2023与2024—2025独立账户；2026未纳入'}
        if method_included:item['methods']=[{'language':'python','code':method}]
        return item
    queries=[query('accounts',accounts,POLICY_ID+ACCOUNT_FIELDS),query('contrasts',contrasts,BASE_ID+CONTRAST_FIELDS),
        query('tails-and-capital',accounts,POLICY_ID+TAIL_FIELDS),query('exit-evidence',exits,POLICY_ID+EXIT_FIELDS,True),
        query('formation-coverage',formation,BASE_ID+FORMATION_FIELDS),query('policy-coverage',coverage,POLICY_ID+COVERAGE_FIELDS)]
    queries.extend(query(key,rows,POLICY_ID+INTERVAL_FIELDS) for key,rows in zip(('years','half-years','phases','event-epochs'),intervals))
    sources={'schemaVersion':1,'items':[{'id':'volatility-trail-vs-trail10','title':'波动跟踪相对固定跟踪：收益、暴露与退出质量','queries':queries}]}
    chart={'schemaVersion':1,'id':'reclaim-volatility-trail-difference','queryId':'volatilityTrailVsTrail10','title':TITLE,'height':360,'theme':'codex-classic',
        'chart':{'type':'horizontalBar','x':'boardLabel','y':VALUE,'series':'period','stackable':False,
            'startAtZero':True,'sortOrder':'original','valueDecimals':2,'xLabel':VALUE,'showXAxisLabel':True,'showYAxisLabel':False,
            'showValues':True,'categoryOrder':list(BOARD_LABELS.values()),'seriesOrder':list(PERIODS),
            'colors':{p:f'var(--chart-{i+1})' for i,p in enumerate(PERIODS)}},
        'rows':[{'period':r['period'],'boardLabel':r['boardLabel'],VALUE:r['returnDifferencePp'],
            '平均仓位差（百分点）':r['exposureDifferencePp'],'候选估值不可靠日':r['candidateValuationDays'],'对照估值不可靠日':r['controlValuationDays']} for r in contrasts],
        'columns':['period','boardLabel',VALUE,'平均仓位差（百分点）','候选估值不可靠日','对照估值不可靠日'],'source':src}
    require(len(chart['rows'])==6 and all(finite(r[VALUE]) for r in chart['rows']),'Six finite signed differences required')
    require([[len(q['rows']) for q in item['queries']] for item in sources['items']]==TABLE_ROWS,'Incomplete source matrix')
    for value in (chart,sources):
        text=encoded(value);require(len(text.encode())<1_000_000 and 'G:/' not in text and 'C:/' not in text,'Oversized/private payload')
    return {'chart':chart,'sources':sources}


def build(directory=OUT):
    directory=Path(directory).resolve();names=('plan.json','comparison.json','validation.json','readout.json','readout-validation.json')
    values,pins={n:read(directory/n) for n in names},{n:sha(directory/n) for n in names}
    plan,comparison,proof,data,audit=(values[n] for n in names)
    require(proof['passed'] and data['verification']['passed'] and audit['passed'],'Independent audits required')
    require(proof['planSha256']==data['planSha256']==audit['planSha256']==pins['plan.json'],'Plan binding differs')
    require(proof['comparisonSha256']==audit['comparisonSha256']==pins['comparison.json'],'Comparison binding differs')
    require(data['verification']['sha256']==audit['accountValidationSha256']==pins['validation.json'] and audit['readoutSha256']==pins['readout.json'],'Readout binding differs')
    require(indexed(data['accountRows'])==indexed(comparison['rows']),'Account rows differ')
    for key,n in (('newAccounts',6),('referenceAccountsReused',6),('newSetupMechanisms',0),('newEntryPolicies',0),('newExitDefinitions',1),('transferredExitPolicies',0),('newUniverseVariants',0)):
        require(plan[key]==comparison[key]==n,'Trial scope differs: '+key)
    for name in ('plan.json','comparison.json','validation.json'):
        require(data['inputPins'][(directory/name).relative_to(ROOT).as_posix()]==pins[name],'Changed input')
    for name in (METHOD,RUNNER): require(plan['sourcePins'][name]==sha(ROOT/name),'Changed frozen source')
    artifact=data['opportunityRowsArtifact'];path=ROOT/artifact['path']
    require(path.resolve().parent==directory and artifact['format']=='jsonl+gzip','Sidecar scope differs')
    pins[path.name]=sha(path);require(pins[path.name]==artifact['sha256']==audit['opportunityRowsArtifactSha256'],'Sidecar hash differs')
    outputs=payloads(data,(ROOT/METHOD).read_text('utf-8'))
    require(not any((directory/n).exists() for n in ('inline','chart.json','sources.json','render-receipt.json')),'Do not overwrite presentation')
    (directory/'inline').mkdir();fragments={};payload_pins={}
    for typ,payload in outputs.items():
        source=directory/(typ+'.json');target=directory/'inline'/('reclaim-volatility-trail-'+typ+'.html');write(source,payload)
        payload_pins[typ]={'path':source.name,'sha256':sha(source)};renderer=TOOLS/('render-inline-'+typ+'.mjs')
        result=subprocess.run([NODE,str(renderer),'--input',str(source),'--output',str(target)],text=True,capture_output=True,timeout=60)
        write(directory/(typ+'-render-log.json'),{'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
        require(result.returncode==0,result.stdout+result.stderr);require(target.stat().st_size<1_000_000,'Oversized fragment')
        fragments[typ]={'path':target.relative_to(directory).as_posix(),'sha256':sha(target),'rendererSha256':sha(renderer)}
    require(all(sha(directory/n)==digest for n,digest in pins.items()),'Analysis inputs changed')
    write(directory/'render-receipt.json',{'schema':'reclaim-volatility-trail-render-v1','inputPins':pins,'builderSha256':sha(__file__),
        'presentationHelpers':{HELPER.relative_to(ROOT).as_posix():HELPER_SHA},'payloads':payload_pins,'fragments':fragments,
        'tableRows':TABLE_ROWS,'chartRows':6,'cards':1,'deliveryOrder':['chart','sources'],'opportunityRowsArtifact':artifact,
        'actualMarketRuns':0,'analysisArtifactsModified':False,'dataPeriod':'2022-2025','includes2026':False,'holdout':False,'causalClaim':False,
        'boardIsIndustry':False,'browserChecked':False})
    return {'fragments':fragments,'tableRows':TABLE_ROWS,'renderReceiptSha256':sha(directory/'render-receipt.json')}


def self_test():
    data={k:[] for k in ('accounts','coverageRows','formationCoverageRows','comparisons','yearRows','halfYearRows','phaseRows','curveEpochRows')}
    data['opportunityRowsArtifact']={'count':48,'sha256':'a'*64}
    for period in PERIODS:
        for board in BOARDS:
            data['formationCoverageRows'].append({'period':period,'board':board,'formationTop5':8,'sourceImmediateSignals':8,
                'sameScheduleForEveryPolicy':True,'sourceCohort':{'includedCount':8,'excludedCount':1,'unknownCount':1},
                'formationEvidenceTotals':{'unknown':4},'benchmarkUnavailableDays':2})
            for mode in MODES:
                identity={'period':period,'board':board,'policy':mode};candidate=mode=='volatilityTrail'
                e={'configured':candidate,'rule':'meanTRPct20x3Ratchet' if candidate else None,'observationCount':5 if candidate else 0,
                    'decisionCounts':{s:int(candidate) for s in ('triggered','notTriggered','unknown','skippedExistingExit','skippedStandardExit')},
                    'windowCounts':{'known':3 if candidate else 0,'unknown':int(candidate),'invalidDistance':int(candidate)},
                    'windowDecisionCounts':{},'windowIssueCounts':{'missingBar':1} if candidate else {},'decisionReasonCounts':{},
                    'carriedThresholdObservations':int(candidate),'carriedThresholdTriggers':int(candidate),
                    'uniqueUnavailableWindowHoldingEpisodes':int(candidate),'uniqueUnknownDecisionHoldingEpisodes':int(candidate),
                    'knownWindowScaledDistancePct':{'count':3 if candidate else 0,'median':.06 if candidate else None},
                    'knownThresholdRatchetRatio':{'count':4 if candidate else 0,'median':1.05 if candidate else None}}
                row={k:None for k,_ in ACCOUNT_FIELDS+TAIL_FIELDS}
                row.update(**identity,id=mode,accountReturnPct=1.,maxDrawdownPct=2.,holdingSessions={'median':None,'maximum':None})
                data['accounts'].append({**identity,'comparisonRow':row,'openUnrealizedPnlPctOfFinalEquity':None,'exitEvidence':e,
                    'curveAttribution':{'closedEpisodesByExitDate':{'returnPct':{'min':None,'p05':None}},'feesPaid':0.,'slippageAlreadyInFillPrices':0.,
                        'sellFillReasons':{'initialStop':1},'queuedExitReasons':{'volatilityTrail':1}},
                    'reconciliation':{'closedEpisodePnl':0.,'openPositionUnrealizedPnl':0.,'openPositionRealizedPnl':0.}})
                data['coverageRows'].append({**identity,'formationTop5':8,'selectedSignals':8,'notScheduledFromFormation':0,
                    'entered':2,'notEntered':6,'closedCount':1,'openCountAtCutoff':1,'unresolvedExitCountAtCutoff':1,
                    'valuationUnreliableDays':1,'qualityScalarFlags':{'stale':True},'terminalReasonCounts':{'noCash':6}})
                for key,n in (('yearRows',2),('halfYearRows',4),('phaseRows',8),('curveEpochRows',2 if period==PERIODS[1] else 0)):
                    for i in range(n):
                        data[key].append({**identity,'id':str(i),'requestedStart':f'{period[:4]}-{i+1:02d}-01','requestedEnd':f'{period[:4]}-{i+1:02d}-28',
                            'returnPct':1.,'maxDrawdownPct':2.,'netPnlAmount':3.,'feesPaid':0.,'endingPositionCount':1,
                            'valuationUnreliableDates':[],'exposurePct':{'mean':40.},'exitEvidence':e})
            data['comparisons'].append({'period':period,'board':board,'id':'volatilityTrailVsTrail10','candidate':'volatilityTrail','control':'trail10',
                'candidateMinusControl':{'accountReturnPct':float(BOARDS.index(board)-1),'maxDrawdownPct':None,'meanExposurePct':-10.,
                    'closedPnlAmount':0.,'openUnrealizedPnlAmount':None},
                'candidateMetrics':{'accountReturnPct':1.,'unreliableValuationDays':2},'controlMetrics':{'accountReturnPct':0.,'unreliableValuationDays':3}})
    before=deepcopy(data);outputs=payloads(data,'# Synthetic only; no market outcomes.')
    require(data==before,'Input changed')
    require([r[VALUE] for r in outputs['chart']['rows'][:3]]==[-1.,0.,1.],'Signed differences changed')
    preview=outputs['sources']['items'][0]['queries']
    require(preview[0]['rows'][0]['closedMeanPct'] is None and preview[3]['rows'][1]['medianKnownWidthPct']==6.,'Unknown/width units changed')
    require(outputs['chart']['chart']['xLabel']==VALUE,'Percentage-point axis title required')
    for kind in ('account','phase','population','decision','window','flipped','schedule'):
        changed=deepcopy(data)
        if kind=='account': changed['accounts'][-1]=deepcopy(changed['accounts'][0])
        elif kind=='phase': changed['phaseRows'].pop()
        elif kind=='population': changed['opportunityRowsArtifact']['count']=1
        elif kind=='decision': changed['accounts'][1]['exitEvidence']['decisionCounts']['unknown']=0
        elif kind=='window': changed['accounts'][1]['exitEvidence']['windowCounts']['unknown']=0
        elif kind=='flipped': changed['comparisons'][0]['candidate']='trail10'
        else: changed['formationCoverageRows'][0]['sameScheduleForEveryPolicy']=False
        try:payloads(changed,'# synthetic')
        except ValueError:pass
        else:raise AssertionError('Invalid synthetic accepted: '+kind)
    code="""import fs from 'node:fs';
import {normalizeInlineChartInput} from %s;
import {normalizeInlineSourcesInput} from %s;
const item=JSON.parse(fs.readFileSync(0,'utf8'));
normalizeInlineChartInput(item.chart);normalizeInlineSourcesInput(item.sources);console.log('passed');""" % (json.dumps((TOOLS/'inline-chart-input.mjs').as_uri()),json.dumps((TOOLS/'inline-sources-input.mjs').as_uri()))
    result=subprocess.run([NODE,'--input-type=module','-e',code],input=json.dumps(outputs,ensure_ascii=False),text=True,capture_output=True,timeout=30)
    require(result.returncode==0,result.stderr)
    return {'passed':True,'checks':['six signed differences','12 unique accounts and6 contrasts','all180 intervals','ten complete tables',
        'separate window and decision states','known distance converted to percentage units','null and zero preserved','seven invalid cases rejected',
        'no mutation','shared renderer validators'],'filesWritten':0}


if __name__ == '__main__':
    parser = ArgumentParser(); parser.add_argument('--directory', type=Path, default=OUT); parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False))
