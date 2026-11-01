"""Presentation of an audited descriptive joint-cohort partition; no account runs."""
from argparse import ArgumentParser
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/joint-cohort-regime-v1'
NODE = 'C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
TOOLS = Path('C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts')
RUNNER = 'scripts/diagnose-joint-cohort-regime.py'
VERIFIER = 'scripts/verify-joint-cohort-regime.py'
PERIODS = ('2022-2023','2024-2025')
BOARDS = ('main','chinext','star')
BOARD_LABELS = dict(zip(BOARDS,('主板','创业板','科创板')))
BOARD_SHORT = dict(zip(BOARDS,('主板','创业','科创')))
POLICIES = ('trail10','volatilityTrail')
POLICY_LABELS = dict(zip(POLICIES,('固定跟踪','波动跟踪')))
STATES = ('bothPositiveMajority','topOnlyPositiveMajority','bottomOnlyPositiveMajority','neitherPositiveMajority','unknown')
STATE_LABELS = dict(zip(STATES,('强弱两组均多数上涨','仅原强组多数上涨','仅原弱组多数上涨','两组均未多数上涨','证据不足')))
RELATIVE = ('stronger','weaker','equal','unknown')
RELATIVE_LABELS = dict(zip(RELATIVE,('强组正反馈占比更高','强组正反馈占比更低','两组正反馈占比相同','证据不足')))
EPOCHS = {PERIODS[0]:('oldPeriod',),PERIODS[1]:('before20240924','from20240924')}
EPOCH_LABELS = {'oldPeriod':'2022—2023','before20240924':'2024-01-02至09-23','from20240924':'2024-09-24至2025-12-31'}
MATRIX = {'overallRows':60,'formationYearRows':120,'epochStateRows':90,'phaseRows':480,'phaseDayRows':240,
    'epochDayRows':45,'accountReconciliation':12,'curveEpochRows':12,'relativeJointRows':120,'qualityRows':12,'continuousPhaseRows':96}
TABLE_ROWS = [[45,240,120],[60,60,120,90,480,12,12,12,96]]
TITLE = '9·24前后：强弱队列的上涨反馈'
VALUE = '交易日占比（%）'
MONEY = [('closedPnlAmount','已闭仓净盈亏（元）'),('openRealizedPnlAmount','未平已实现（元）'),
    ('openUnrealizedPnlAmount','未平未实现（元）'),('totalPnlAmount','形成归属合计（元）')]
POPULATION = ('formationCount','entered','closed','open','notEntered')
ENV_LIMITS = [
    '五态描述同板历史样本中、此前固定的最强和最弱十分位队列，不代表全市场普涨、实际市场总龙头独涨或交易资金身份。',
    '仅原强组多数上涨表示原强十分位上涨成员超过一半、原弱十分位未超过一半；后者包含下跌和走平，不等于弱组全跌。',
    '形成日T只使用A=T−1已知信息，队列冻结于F=T−6，反馈是F至A五个交易日收盘变化；9月24日的环境截至9月23日，政策日反应最早进入下一交易日环境。',
    '未知成员保留在固定人数中，用上涨比例上下界判定是否严格超过一半；任一队列无法判定时归证据不足，没有覆盖率门槛。',
    '图中区间为2024-01-02至09-23和2024-09-24至2025-12-31，长度不同；占比分母包含全部区间交易日及证据不足，零天状态仍保留，不重新归一化已知状态。',
    '16个历史阶段只作描述连接，不是交易输入；全部2022—2025是已检视开发历史，2026此前已暴露但本次未使用，不能称留出检验。',
]
ACCOUNT_LIMITS = [
    '所有金额按原信号形成时环境归属，整笔交易可跨环境和9月24日；这不是仅交易该环境后重跑的账户收益。',
    '两个退出视图复用原12个账户与8155个机会的16310条终态，不是独立新增样本或可相加组合；本轮没有新账户、信号、筛选或收益门槛。',
    '闭仓平均、胜率和盈亏分布受实际成交、资金占用、退出与期末删失影响；未成交不填零收益，未平仓已实现和未实现盈亏单列。',
    '完整平仓数为零时平均收益、胜率等保持未知；空组零金额不表示零收益交易。未分配权益残差与原账户核对，未把残差分摊给环境。',
    '估值不可靠、陈旧持仓、数值未知和未解决退出保留；长期未知持仓不能解释为趋势成功。板块不是纯科技行业，日线不能识别主力或量化交易者。',
    '连续净值分段继承此前权益与仓位，不重启账户；历史有利分组不足以证明可执行环境准入或未来稳定小亏大赚。',
]


def require(value,message):
    if not value: raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()


def read(path): return json.loads(Path(path).read_text('utf-8'))


def write(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,allow_nan=False,indent=2);stream.write('\n')


def finite(value): return type(value) in (int,float) and math.isfinite(value)


def near(a,b): return finite(a) and finite(b) and math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-7)


def matrix(rows,fields,expected,name):
    keys=[tuple(row[k] for k in fields) for row in rows]
    require(len(keys)==len(set(keys)) and set(keys)==expected,'Incomplete/duplicate '+name)


def validate(data):
    require(data['states']==list(STATES) and data['labels']==STATE_LABELS,'Joint-state labels changed')
    for name,n in MATRIX.items(): require(len(data[name])==n,'Missing full matrix '+name)
    phase_by_id={p['id']:p for p in data['phases']}
    require(len(phase_by_id)==len(data['phases'])==16,'All sixteen phases required')
    phases={p:[x['id'] for x in data['phases'] if p[:4]<=x['startDate'][:4]<=p[-4:]] for p in PERIODS}
    base={(p,b,m,s) for p in PERIODS for b in BOARDS for m in POLICIES for s in STATES}
    matrix(data['overallRows'],('period','board','policy','jointState'),base,'overall')
    matrix(data['formationYearRows'],('period','board','policy','jointState','formationYear'),{(*k,y) for k in base for y in k[0].split('-')},'years')
    matrix(data['epochStateRows'],('period','board','policy','jointState','formationEpoch'),{(*k,e) for k in base for e in EPOCHS[k[0]]},'epochs')
    matrix(data['phaseRows'],('period','board','policy','jointState','phaseId'),{(*k,f) for k in base for f in phases[k[0]]},'phases')
    matrix(data['epochDayRows'],('period','board','formationEpoch','jointState'),{(p,b,e,s) for p in PERIODS for b in BOARDS for e in EPOCHS[p] for s in STATES},'epoch days')
    matrix(data['phaseDayRows'],('period','board','phaseId','jointState'),{(p,b,f,s) for p in PERIODS for b in BOARDS for f in phases[p] for s in STATES},'phase days')
    matrix(data['relativeJointRows'],('period','board','relativeState','jointState'),{(p,b,r,s) for p in PERIODS for b in BOARDS for r in RELATIVE for s in STATES},'relative/joint')
    for name in ('accountReconciliation','qualityRows'):
        matrix(data[name],('period','board','policy'),{(p,b,m) for p in PERIODS for b in BOARDS for m in POLICIES},name)
    matrix(data['continuousPhaseRows'],('period','board','policy','phaseId'),{(p,b,m,f) for p in PERIODS for b in BOARDS for m in POLICIES for f in phases[p]},'continuous phases')
    for row in data['continuousPhaseRows']:
        require(all(type(row[k]) is int and row[k]>=0 for k in ('observedSessions','buyFillCount','sellFillCount','endingPositionCount','valuationFlagDays')),'Invalid continuous phase counts')
        require(near(row['endingEquity']-row['enteringEquity'],row['netPnlAmount']),'Continuous phase monetary bridge')
        require(near((row['endingEquity']/row['enteringEquity']-1)*100,row['returnPct']),'Continuous phase return differs')
    matrix(data['curveEpochRows'],('period','board','policy','phaseId'),{(PERIODS[1],b,m,e) for b in BOARDS for m in POLICIES for e in EPOCHS[PERIODS[1]]},'continuous epochs')
    for row in data['epochDayRows']:
        require(type(row['dayCount']) is int and type(row['totalDays']) is int and 0<=row['dayCount']<=row['totalDays'],'Invalid day denominator')
        require(near(row['daySharePct'],row['dayCount']/row['totalDays']*100) if row['totalDays'] else row['daySharePct'] is None,'Day share differs')
    for p in PERIODS:
        for b in BOARDS:
            days=[r for r in data['epochDayRows'] if (r['period'],r['board'])==(p,b)]
            for e in EPOCHS[p]:
                part=[r for r in days if r['formationEpoch']==e]
                require(len({r['totalDays'] for r in part})==1 and sum(r['dayCount'] for r in part)==part[0]['totalDays'],'Five-state day partition')
            total=sum(r['dayCount'] for r in days)
            require(total==sum(r['days'] for r in data['relativeJointRows'] if (r['period'],r['board'])==(p,b))
                ==sum(r['days'] for r in data['phaseDayRows'] if (r['period'],r['board'])==(p,b)),'Daily partition sums differ')
            for f in phases[p]:
                part=[r for r in data['phaseDayRows'] if (r['period'],r['board'],r['phaseId'])==(p,b,f)]
                require(all(type(r['days']) is int and 0<=r['days']<=r['phaseDays'] for r in part)
                    and len({r['phaseDays'] for r in part})==1 and sum(r['days'] for r in part)==part[0]['phaseDays'],'Phase-day partition')
    for name in ('overallRows','formationYearRows','epochStateRows','phaseRows'):
        for row in data[name]:
            require(all(type(row[k]) is int and row[k]>=0 for k in POPULATION),'Invalid population')
            require(row['formationCount']==row['entered']+row['notEntered'] and row['entered']==row['closed']+row['open'],'Outcome denominator differs')
            require(all(finite(row[k]) for k,_ in MONEY) and near(row['totalPnlAmount'],math.fsum(row[k] for k,_ in MONEY[:3])),'Group monetary bridge')
            require(row['closed']==row['wins']+row['losses']+row['zeros'],'Closed win/loss count')
    require(sum(r['formationCount'] for r in data['overallRows'])==data['recordCount']==2*data['formationCount'],'Original outcome population differs')
    for row in data['accountReconciliation']:
        require(row['passed'] and near(row['assignedPnlAmount']+row['unallocatedResidual'],row['accountNetPnlAmount']),'Original account bridge')
    return phase_by_id


def display(data):
    phases=validate(data)
    def label(row):
        value=deepcopy(row);value['boardLabel']=BOARD_LABELS[row['board']]
        period=row.get('formationYear',row['period'])
        if 'formationEpoch' in row: period=EPOCH_LABELS[row['formationEpoch']]
        if 'phaseId' in row: period=phases[row['phaseId']]['title'] if row['phaseId'] in phases else EPOCH_LABELS[row['phaseId']]
        value['scopeLabel']=period+' · '+BOARD_LABELS[row['board']]
        if 'policy' in row:value['policyLabel']=POLICY_LABELS[row['policy']]
        if 'jointState' in row:value['stateLabel']=STATE_LABELS[row['jointState']]
        if 'relativeState' in row:value['relativeLabel']=RELATIVE_LABELS[row['relativeState']]
        if 'formationCount' in row:value['population']=' / '.join(str(row[k]) for k in POPULATION)
        return value
    return {name:[label(r) for r in data[name]] for name in MATRIX}


def payloads(data,method):
    rows=display(data)
    files=[{'label':'artifacts/strategy-search/joint-cohort-regime-v1/'+name} for name in ('plan.json','readout.json','validation.json','daily.jsonl.gz','records.jsonl.gz')]
    files += [{'label':RUNNER},{'label':'docs/joint-cohort-regime-v1.json'}, {'label':'artifacts/strategy-search/reclaim-relative-feedback-context-v1/readout.json'}]
    env={'label':'固定强弱队列的联合环境','files':files,'metricDefinitions':[{'label':'多数上涨','definition':'固定队列中上涨成员占比严格超过一半；未知成员保留在分母，用整数上下界判定。'},
        {'label':'日数占比','definition':'各联合状态交易日数除以同板同区间全部交易日数，包含证据不足和真实零天状态。'}],
        'filters':['形成日使用前一源交易日信息','固定同板RS20顶/底十分位','完整保留五种联合状态'],
        'caveats':ENV_LIMITS,'evidenceFlow':[{'kind':'validation','title':'联合状态与分母核验','detail':'独立验证固定成员人数与整数多数边界、全部日期分组和记录身份；完整保留未知状态及原相对反馈交叉表。'}]}
    account={'label':'既有12账户的形成环境归因','files':files,'metricDefinitions':[{'label':'形成归属','definition':'原闭仓净盈亏与期末未平仓的已实现、未实现盈亏，按原信号形成环境归属。'},
        {'label':'资金桥','definition':'全部归属盈亏加原未分配残差对齐原账户；连续区间保留进入权益和跨段持仓。'}],
        'filters':['原8155机会的两种退出视图','两时期及三个板块全部保留','不执行新账户或环境准入规则'],
        'caveats':ACCOUNT_LIMITS,'evidenceFlow':[{'kind':'validation','title':'完整旧账本归因核验','detail':'独立复核原16310记录的五态、各年、9月24日分界和全部16阶段矩阵；核对12账户资金桥及12原连续曲线区间。'}]}
    def query(key,values,cols,source,include_method=False):
        require(len(cols)<=10 and all(all(k in r for k,_ in cols) for r in values),'Missing preview column '+key)
        result={'id':key,'rows':[{k:r[k] for k,_ in cols} for r in values],'columns':[{'field':k,'label':v} for k,v in cols],
            'source':source,'reportingPeriod':'2022—2025完整开发历史；9·24前后另列，2026未使用'}
        if include_method:result['methods']=[{'language':'python','code':method}]
        return result
    env_queries=[query('epoch-days',rows['epochDayRows'],[('scopeLabel','时期与板块'),('stateLabel','联合状态'),('dayCount','状态日数'),('totalDays','全部交易日'),('daySharePct','交易日占比（%）')],env,True),
        query('phase-days',rows['phaseDayRows'],[('scopeLabel','历史阶段与板块'),('stateLabel','联合状态'),('days','状态日数'),('phaseDays','阶段全部交易日')],env),
        query('relative-joint',rows['relativeJointRows'],[('scopeLabel','时期与板块'),('relativeLabel','原相对反馈'),('stateLabel','联合状态'),('days','共同日数')],env)]
    identity=[('scopeLabel','形成区间与板块'),('policyLabel','退出'),('stateLabel','联合状态')]
    financial=identity+[('population','形成 / 入场 / 闭仓 / 未平 / 未入场')]+MONEY
    payoff=[{**r,'outcomeCounts':' / '.join(str(r[k]) for k in ('closed','wins','losses','zeros')),
        'meanMedian':' / '.join('—' if r[k] is None else str(round(r[k],4)) for k in ('closedMeanPct','closedMedianPct')),
        'winLossMeans':' / '.join('—' if r[k] is None else str(round(r[k],4)) for k in ('meanWinPct','meanLossPct'))} for r in rows['overallRows']]
    qualities=[{**r,'qualityFlags':json.dumps(r['sourceQualityScalarFlags'],ensure_ascii=False,sort_keys=True)} for r in rows['qualityRows']]
    curves=[{**r,'meanExposurePct':r['exposurePct']['mean'],'valuationDays':len(r['valuationUnreliableDates'])} for r in rows['curveEpochRows']]
    continuous=[{**r,'scopeLabel':r['period']+' · '+BOARD_LABELS[r['board']],
        'intervalLabel':str(r['observedStart'])+' 至 '+str(r['observedEnd'])} for r in rows['continuousPhaseRows']]
    continuous_source={**account,'files':account['files']+[{'label':'artifacts/strategy-search/joint-cohort-regime-v1/continuous-phase-context.json'},
        {'label':'artifacts/strategy-search/joint-cohort-regime-v1/continuous-phase-validation.json'},
        {'label':'artifacts/strategy-search/reclaim-volatility-trail-v1/readout.json'}],
        'caveats':account['caveats']+['96条原连续阶段曲线是联合环境结果生成后追加的描述复核，未作为原联合分组的预注册证据，也未修改账户或规则。',
            '连续阶段收益继承进入权益和此前持仓；阶段无买卖成交时仍可因存量持仓涨跌产生盈亏，不能将阶段盈利当成该阶段新信号收益。']}
    account_queries=[query('overall',rows['overallRows'],financial,account),
        query('payoff',payoff,identity+[('outcomeCounts','闭仓 / 盈 / 亏 / 平'),('winRatePct','闭仓胜率（%）'),('meanMedian','平均 / 中位回报（%）'),('winLossMeans','平均盈 / 亏（%）'),('profitFactor','现金盈利因子'),('top5ShareOfGrossProfitPct','前五笔占总盈利（%）')],account),
        query('formation-years',rows['formationYearRows'],financial,account),query('formation-epochs',rows['epochStateRows'],financial,account),
        query('formation-phases',rows['phaseRows'],financial,account),
        query('account-bridges',rows['accountReconciliation'],[('scopeLabel','时期与板块'),('policyLabel','退出'),('population','形成 / 入场 / 闭仓 / 未平 / 未入场'),('assignedPnlAmount','已归属（元）'),('unallocatedResidual','未分配残差（元）'),('accountNetPnlAmount','账户净盈亏（元）'),('accountReturnPct','账户净收益（%）'),('passed','已核对')],account),
        query('quality',qualities,[('scopeLabel','时期与板块'),('policyLabel','退出'),('unreliableValuationDays','估值不可靠日'),('unresolvedExitCount','未解决退出'),('openMarkStaleCount','期末陈旧持仓'),('numericUnknownCount','数值未知'),('integrityIssueCount','完整性问题'),('qualityFlags','源质量标记')],account),
        query('continuous-epochs',curves,[('scopeLabel','连续区间与板块'),('policyLabel','退出'),('enteringEquity','进入权益（元）'),('endingEquity','结束权益（元）'),('returnPct','区间收益（%）'),('maxDrawdownPct','区间回撤（%）'),('meanExposurePct','平均仓位（%）'),('valuationDays','估值不可靠日')],account),
        query('continuous-phases',continuous,[('scopeLabel','时期与板块'),('policyLabel','退出'),('phaseId','阶段'),('intervalLabel','实际起止日期'),('observedSessions','交易日'),('returnPct','连续区间收益（%）'),('buyFillCount','买入成交笔数'),('sellFillCount','卖出成交笔数'),('valuationFlagDays','估值不可靠日')],continuous_source)]
    sources={'schemaVersion':1,'items':[{'id':'joint-environment','title':'9·24前后及完整历史阶段的强弱队列反馈','queries':env_queries},
        {'id':'existing-account-attribution','title':'形成环境中的既有账本：完整分母、盈亏与质量','queries':account_queries}]}
    categories=[BOARD_SHORT[b]+('前' if e=='before20240924' else '后') for b in BOARDS for e in EPOCHS[PERIODS[1]]]
    lookup={(r['board'],r['formationEpoch'],r['jointState']):r for r in rows['epochDayRows'] if r['period']==PERIODS[1]}
    chart_rows=[]
    for board in BOARDS:
        for epoch in EPOCHS[PERIODS[1]]:
            for state in STATES:
                row=lookup[board,epoch,state]
                chart_rows.append({'category':BOARD_SHORT[board]+('前' if epoch=='before20240924' else '后'),
                    'boardLabel':BOARD_LABELS[board],'epochLabel':EPOCH_LABELS[epoch],'stateLabel':STATE_LABELS[state],
                    VALUE:row['daySharePct'],'状态日数':row['dayCount'],'全部交易日':row['totalDays']})
    chart={'schemaVersion':1,'id':'joint-cohort-regime-days','queryId':'epoch-days','title':TITLE,'height':400,'theme':'codex-classic',
        'chart':{'type':'horizontalStackedBar','x':'category','y':VALUE,'series':'stateLabel','startAtZero':True,'sortOrder':'original',
            'xLabel':VALUE,'showXAxisLabel':True,'showYAxisLabel':False,'showValues':False,'valueDecimals':2,
            'categoryOrder':categories,'seriesOrder':list(STATE_LABELS.values()),'colors':{s:f'var(--chart-{i+1})' for i,s in enumerate(STATE_LABELS.values())}},
        'rows':chart_rows,'columns':list(chart_rows[0]),'source':env}
    require([[len(q['rows']) for q in card['queries']] for card in sources['items']]==TABLE_ROWS,'Complete preview matrix differs')
    for obj in (chart,sources):
        text=json.dumps(obj,ensure_ascii=False,allow_nan=False)
        require(len(text.encode())<1_000_000 and 'G:/' not in text and 'C:/' not in text,'Oversized/private projection')
    return {'chart':chart,'sources':sources}


def build(directory=OUT):
    directory=Path(directory).resolve();names=('plan.json','readout.json','validation.json')
    plan,data,proof=(read(directory/name) for name in names);pins={name:sha(directory/name) for name in names}
    require(proof['passed'] and proof['planSha256']==pins['plan.json']==data['plan']['sha256']
        and proof['readoutSha256']==pins['readout.json'],'Independent diagnostic proof required')
    require(proof['verifierSha256']==sha(ROOT/VERIFIER),'Independent verifier changed')
    for item in plan['sourcePins']+plan['inputPins']:
        path=(ROOT/item['path']).resolve();require(path.is_relative_to(ROOT) and sha(path)==item['sha256'],'Frozen input changed')
    for key,proof_key in (('records','recordsSha256'),('daily','dailySha256')):
        path=(ROOT/data[key]['path']).resolve();require(path==directory/(key+'.jsonl.gz') and sha(path)==data[key]['sha256']==proof[proof_key],'Bound artifact differs')
        pins[path.name]=data[key]['sha256']
    require(data['formationCount']==8155 and data['recordCount']==16310 and data['sourceReadout']==plan['parentReadout'],'Original data scope differs')
    require(data['trialAccounting']=={'newStrategyTrials':0,'newAccounts':0,'existingAccountsRead':12,'newJointContextDefinitions':1}
        and data['selectedState'] is None and data['certified'] is False and data['targetAchieved'] is False,'Descriptive scope differs')
    context_name='continuous-phase-context.json';context_proof_name='continuous-phase-validation.json'
    context=read(directory/context_name);context_proof=read(directory/context_proof_name)
    pins[context_name]=sha(directory/context_name);pins[context_proof_name]=sha(directory/context_proof_name)
    require(context_proof['passed'] and context_proof['posthocFollowup'] is True and context_proof['contextSha256']==pins[context_name],
        'Independent post-hoc continuous-phase proof required')
    require(context_proof['sourceReadoutSha256']==context['sourceReadout']['sha256'] and
        context_proof['sourceValidationSha256']==context['sourceValidation']['sha256'],'Continuous-phase source proof differs')
    for item in [context[k] for k in ('sourceReadout','sourceValidation','extractor')]+context_proof['sourcePins']:
        path=(ROOT/item['path']).resolve();require(path.is_relative_to(ROOT) and sha(path)==item['sha256'],'Continuous-phase source changed')
    require(any(item['sha256']==context_proof['verifierSha256'] for item in context_proof['sourcePins']), 'Continuous-phase verifier not bound')
    require(context['newAccounts']==context['newSignals']==0 and not context['certified'] and not context['targetAchieved'], 'Continuous-phase descriptive scope differs')
    data={**data,'continuousPhaseRows':context['rows']}
    outputs=payloads(data,(ROOT/RUNNER).read_text('utf-8'))
    require(not any((directory/name).exists() for name in ('inline','chart.json','sources.json','render-receipt.json')),'Never overwrite presentation')
    (directory/'inline').mkdir();fragments={};payload_refs={}
    for kind,payload in outputs.items():
        source=directory/(kind+'.json');target=directory/'inline'/('joint-cohort-regime-'+kind+'.html');write(source,payload)
        renderer=TOOLS/('render-inline-'+kind+'.mjs')
        result=subprocess.run([NODE,str(renderer),'--input',str(source),'--output',str(target)],capture_output=True,text=True,timeout=60)
        write(directory/(kind+'-render-log.json'),{'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
        require(result.returncode==0,result.stderr);require(target.stat().st_size<1_000_000,'Oversized fragment')
        fragments[kind]={'path':target.relative_to(directory).as_posix(),'sha256':sha(target),'rendererSha256':sha(renderer)}
        payload_refs[kind]={'path':source.name,'sha256':sha(source)}
    require(all(sha(directory/name)==digest for name,digest in pins.items()),'Analysis mutated during rendering')
    write(directory/'render-receipt.json',{'schema':'joint-cohort-regime-render-v1','inputPins':pins,'builderSha256':sha(__file__),
        'fragments':fragments,'payloads':payload_refs,'deliveryOrder':['chart','sources'],'chartRows':30,'tableRows':TABLE_ROWS,
        'cards':2,'states':list(STATES),'accounts':12,'formationCount':8155,'recordCount':16310,'actualMarketRuns':0,
        'posthocContinuousPhaseRows':96,'continuousPhaseFollowupPreregistered':False,
        'analysisArtifactsModified':False,'browserChecked':False,'includes2026':False,'holdout':False,'causalClaim':False})
    return {'fragments':fragments,'tableRows':TABLE_ROWS,'renderReceiptSha256':sha(directory/'render-receipt.json')}


def synthetic():
    data={k:[] for k in MATRIX};data.update(states=list(STATES),labels=deepcopy(STATE_LABELS),phases=[])
    for p in PERIODS:
        for i in range(8):data['phases'].append({'id':p+'-'+str(i),'title':'合成阶段'+str(i),'startDate':p[:4]+'-01-01','endDate':p[-4:]+'-12-31'})
        for b in BOARDS:
            for e in EPOCHS[p]:
                for i,s in enumerate(STATES):data['epochDayRows'].append({'period':p,'board':b,'formationEpoch':e,'jointState':s,'dayCount':[2,1,0,0,1][i],'totalDays':4,'daySharePct':[50.,25.,0.,0.,25.][i]})
            for i in range(8):
                for j,s in enumerate(STATES):data['phaseDayRows'].append({'period':p,'board':b,'phaseId':p+'-'+str(i),'jointState':s,'days':int(j==0 and i<(4 if p==PERIODS[0] else 8)),'phaseDays':int(i<(4 if p==PERIODS[0] else 8))})
            for r in RELATIVE:
                for s in STATES:data['relativeJointRows'].append({'period':p,'board':b,'relativeState':r,'jointState':s,'days':(4 if p==PERIODS[0] else 8) if r=='stronger' and s==STATES[0] else 0})
            for m in POLICIES:
                ident={'period':p,'board':b,'policy':m}
                for i,s in enumerate(STATES):
                    row={**ident,'jointState':s,'formationCount':int(i==0),'entered':int(i==0),'closed':int(i==0),'open':0,'notEntered':0,
                        'wins':int(i==0),'losses':0,'zeros':0,'closedPnlAmount':10. if i==0 else 0.,'openRealizedPnlAmount':0.,'openUnrealizedPnlAmount':0.,'totalPnlAmount':10. if i==0 else 0.,
                        'closedMeanPct':1. if i==0 else None,'closedMedianPct':1. if i==0 else None,'meanWinPct':1. if i==0 else None,'meanLossPct':None,'winRatePct':100. if i==0 else None,'profitFactor':None,'top5ShareOfGrossProfitPct':100. if i==0 else None}
                    data['overallRows'].append(row)
                    for y in p.split('-'):data['formationYearRows'].append({**row,'formationYear':y})
                    for e in EPOCHS[p]:data['epochStateRows'].append({**row,'formationEpoch':e})
                    for f in range(8):data['phaseRows'].append({**row,'phaseId':p+'-'+str(f)})
                for f in range(8):data['continuousPhaseRows'].append({**ident,'phaseId':p+'-'+str(f),'observedStart':p[:4]+'-01-01','observedEnd':p[-4:]+'-12-31',
                    'observedSessions':10,'enteringEquity':500000.,'endingEquity':500011.,'netPnlAmount':11.,'returnPct':.0022,'buyFillCount':0,'sellFillCount':0,'endingPositionCount':1,'valuationFlagDays':0})
                data['accountReconciliation'].append({**ident,'formationCount':1,'entered':1,'closed':1,'open':0,'notEntered':0,'assignedPnlAmount':10.,'unallocatedResidual':1.,'accountNetPnlAmount':11.,'accountReturnPct':.0022,'passed':True})
                data['qualityRows'].append({**ident,'unreliableValuationDays':1,'unresolvedExitCount':0,'openMarkStaleCount':0,'numericUnknownCount':0,'integrityIssueCount':0,'sourceQualityScalarFlags':{'survivor':True}})
                if p==PERIODS[1]:
                    for e in EPOCHS[p]:data['curveEpochRows'].append({**ident,'phaseId':e,'enteringEquity':500000.,'endingEquity':500011.,'returnPct':.0022,'maxDrawdownPct':0.,'exposurePct':{'mean':20.},'valuationUnreliableDates':[]})
    data.update(recordCount=12,formationCount=6,dailyCount=36)
    return data


def self_test():
    data=synthetic();before=deepcopy(data);outputs=payloads(data,'# Synthetic only; no historical outcomes.')
    require(data==before and len(outputs['chart']['rows'])==30,'Mutation or dropped states')
    require(sum(r[VALUE]==0 for r in outputs['chart']['rows'])==12,'Zero states dropped')
    require(len(outputs['chart']['chart']['seriesOrder'])==5 and outputs['chart']['chart']['type']=='horizontalStackedBar','All states/raw percentage stack')
    require(outputs['sources']['items'][1]['queries'][1]['rows'][1]['profitFactor'] is None,'Unknown statistic filled')
    for kind in ('dayShare','dayPopulation','missingPhase','duplicate','unknownState','money','population','relative'):
        changed=deepcopy(data)
        if kind=='dayShare':changed['epochDayRows'][0]['daySharePct']=49.
        elif kind=='dayPopulation':changed['epochDayRows'][0]['totalDays']=3
        elif kind=='missingPhase':changed['phaseRows'].pop()
        elif kind=='duplicate':changed['overallRows'][-1]=deepcopy(changed['overallRows'][0])
        elif kind=='unknownState':changed['states'].pop()
        elif kind=='money':changed['overallRows'][0]['totalPnlAmount']=11.
        elif kind=='population':changed['overallRows'][0]['entered']=0
        else:changed['relativeJointRows'][0]['days']+=1
        try:payloads(changed,'# synthetic')
        except ValueError:pass
        else:raise AssertionError('Invalid fixture accepted '+kind)
    code="""import fs from 'node:fs';
import {normalizeInlineChartInput} from %s;
import {normalizeInlineSourcesInput} from %s;
const p=JSON.parse(fs.readFileSync(0,'utf8'));normalizeInlineChartInput(p.chart);normalizeInlineSourcesInput(p.sources);console.log('passed');""" % (json.dumps((TOOLS/'inline-chart-input.mjs').as_uri()),json.dumps((TOOLS/'inline-sources-input.mjs').as_uri()))
    result=subprocess.run([NODE,'--input-type=module','-e',code],input=json.dumps(outputs,ensure_ascii=False),capture_output=True,text=True,timeout=30)
    require(result.returncode==0,result.stderr)
    return {'passed':True,'checks':['all five states and zeros','complete1347 preview rows','all16 phases and12 accounts','population and money checks',
        'day denominator and joint/relative coverage','eight invalid fixtures rejected','unknown payoff preserved','no mutation','shared validators'],'filesWritten':0}


if __name__=='__main__':
    parser=ArgumentParser();parser.add_argument('--directory',type=Path,default=OUT);parser.add_argument('--self-test',action='store_true')
    args=parser.parse_args();print(json.dumps(self_test() if args.self_test else build(args.directory),ensure_ascii=False))
