"""Freeze -> form all schedules -> costed accounts -> independent audit. No tuning."""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
import gc
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m


U=module('_guiyin_dual_utils','scripts/run-liquid-trend-breakout.py')
read,write,ref,bound,sha,now,emit=(getattr(U,k) for k in ('read','write','ref','bound','sha','now','emit'))
BASE=ROOT/'artifacts/strategy-search';STUDY=BASE/'guiyin-dual-path-system-v1';OUT=STUDY/'attempt-001'
PARENT=BASE/'sector-long-breakout-system-v4/attempt-001';REGISTRY=BASE/'goal-continuation-20261018.json'
CONTRACT=ROOT/'docs/guiyin-dual-path-system-v1.json'


def register():
    if OUT.exists():raise ValueError('Preserve previous trial')
    from service.research_fixed_signal_account import _source_pins
    from service.research_guiyin_dual_path import POLICIES
    parent=read(PARENT/'plan.json'); original=read(BASE/'market-sector-stock-accounts-v1/attempt-001/plan.json')
    refs={k:parent[k] for k in ('matrix','joins','dataset')}
    refs.update(originalFormation=ref(BASE/'market-sector-stock-accounts-v1/attempt-001/formation.candidate.json.gz'),
        suspensions=ref(PARENT/'explicit-suspensions.npy'),benchmark=original['indices']['sh000300'],
        allSourceCycles=ref(BASE/'public-trading-evidence-v1/guiyin-ledger-validation-001/canonical-closed-cycles.json'),
        sourceValidation=ref(BASE/'public-trading-evidence-v1/guiyin-ledger-validation-001/readout.json'))
    for v in refs.values():bound(v)
    paths=('docs/guiyin-dual-path-system-v1.json','service/research_guiyin_dual_path.py','scripts/run-guiyin-dual-path-system.py',
           'scripts/verify-guiyin-dual-path-system.py','tests/test_research_guiyin_dual_path.py')
    pins={**_source_pins(),**{p:sha(ROOT/p) for p in paths}}
    cfg=deepcopy(parent['baseConfig']);cfg.update(maxHoldingDays=10)
    accounts=[dict(period='primary',policy=p) for p in POLICIES]+[dict(period='weakMarketStress',policy='complete')]
    rules=read(CONTRACT)
    OUT.mkdir(parents=True)
    write(OUT/'previous-registry.json',read(REGISTRY))
    write(OUT/'plan.json',dict(schema='guiyin-dual-path-system-frozen-plan-v1',registeredAt=now(),contract=ref(CONTRACT),sources=refs,sourcePins=pins,
         baseConfig=cfg,plannedAccounts=accounts,periods={'primary':rules['evaluation']['primary'],'weakMarketStress':rules['evaluation']['weakMarketStress']},
         workspaceJsonBefore=U.workspace_snapshot(),newStrategyTrials=1,newAccountsPlanned=8,historyAlreadyExposed=True,holdoutClaim=False,
         original49Registry=ref(BASE/'program.json'),sourceHypothesis='Two entry families are researcher daily proxies, not the original trader exact system',targetAchieved=False))
    for p in pins:
        target=OUT/'frozen-code'/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/p).read_bytes())
    registry=read(REGISTRY)
    registry.update(updatedAt=now(),researchContinuationState='Active by explicit human request; Goal backend status is stale blocked and has no agent resume method',
        activeStudy=dict(directory=str(OUT.relative_to(ROOT)).replace('\\','/'),status='rules frozen; all outcomes pending',plan=ref(OUT/'plan.json'),newAccountsPlanned=8,newStrategyTrials=1,targetAchieved=False))
    # This is the mutable continuation index only. The original49 registry remains byte-identical.
    import json
    REGISTRY.write_text(json.dumps(registry,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    emit(dict(registered=True,plan=ref(OUT/'plan.json'),plannedAccounts=8))


def verify():
    p=read(OUT/'plan.json')
    for path,h in p['sourcePins'].items():bound(dict(path=path,sha256=h))
    for v in p['sources'].values():bound(v)
    bound(p['original49Registry'])
    return p


def load_matrix(p):
    with np.load(bound(p['sources']['matrix']),allow_pickle=False) as z:m={k:z[k] for k in z.files}
    m['explicitSuspended']=np.load(bound(p['sources']['suspensions']),allow_pickle=False)
    return m


def benchmark(p,calendar):
    prices={}
    for day,op,hi,lo,cl,amount,vol,reserved in struct.iter_unpack('<IIIIIfII',bound(p['sources']['benchmark']).read_bytes()):
        s=str(day);prices[s[:4]+'-'+s[4:6]+'-'+s[6:]]=cl/100.
    return [prices.get(d,float('nan')) for d in calendar]


def prepare():
    from service.research_guiyin_dual_path import build_inputs
    p=verify();write(OUT/'prepare-start.json',dict(startedAt=now(),plan=ref(OUT/'plan.json')))
    tests=subprocess.run([sys.executable,'-X','utf8','-B','-m','unittest','tests.test_research_guiyin_dual_path','tests.test_research_fixed_signal_account',
        'tests.test_research_account_fees','tests.test_research_account_outcomes'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    write(OUT/'tests.json',dict(passed=tests.returncode==0,stdout=tests.stdout,stderr=tests.stderr))
    if tests.returncode:raise ValueError('Pre-account tests failed')
    m=load_matrix(p);joins=read(bound(p['sources']['joins']));old=read(bound(p['sources']['originalFormation']))
    emit(dict(formationStarted=True,days=len(old['daily']),population=len(m['symbols'])))
    f=build_inputs(m,joins,old,benchmark(p,m['calendar'].tolist()))
    write(OUT/'formation.json.gz',f)
    oracle=module('_guiyin_independent_formation','scripts/verify-guiyin-dual-path-system.py')
    proof=oracle.formation_audit(m,joins,f,benchmark(p,m['calendar'].tolist()),old)
    write(OUT/'formation-validation.json',proof)
    source_context=oracle.source_cycle_coverage(m,joins,f,read(bound(p['sources']['allSourceCycles'])))
    write(OUT/'all-source-cycle-coverage.json',source_context)
    system={k:f[k] for k in ('daily','sectorMaps','metadata')};write(OUT/'system-inputs.json.gz',system)
    groups=[]
    for a in p['plannedAccounts']:
        lo,hi=p['periods'][a['period']]
        signals=[s for s in f['schedules'][a['policy']] if lo<=s['signalDate']<=hi]
        path=OUT/a['period']/a['policy']/'signals.json';write(path,signals)
        groups.append(dict(**a,schedule=ref(path),signals=len(signals)))
    counts=dict(allCandidateEvents=len(f['allCandidates']),signals={k:len(v) for k,v in f['schedules'].items()},patterns=dict(Counter(m['pattern'] for m in f['metadata'].values())))
    del m,f,system;gc.collect()
    verify()
    if U.workspace_snapshot()!=p['workspaceJsonBefore']:raise ValueError('Protected workspace drift')
    write(OUT/'input-validation.json',dict(passed=True,completedAt=now(),plan=ref(OUT/'plan.json'),allSchedulesFrozenBeforeAccounts=True,
         groups=groups,formation=ref(OUT/'formation.json.gz'),system=ref(OUT/'system-inputs.json.gz'),formationValidation=ref(OUT/'formation-validation.json'),
         sourceCycleCoverage=ref(OUT/'all-source-cycle-coverage.json'),tests=ref(OUT/'tests.json'),counts=counts,newAccountsRun=0))
    emit(dict(inputsReady=True,**counts))


def run_accounts():
    from service.research_guiyin_dual_path import DualPathAccount
    from service.research_account_fees import LegacyFeePolicy
    from service.research_account_outcomes import summarize_account
    p=verify();inputs=read(OUT/'input-validation.json')
    if not inputs['passed'] or inputs['plan']!=ref(OUT/'plan.json'):raise ValueError('Frozen complete inputs required')
    data=read(bound(p['sources']['dataset']));system=read(bound(inputs['system']))
    for g in inputs['groups']:
        folder=OUT/g['period']/g['policy']
        if (folder/'completion.json').exists():
            emit(dict(alreadyCompleted=[g['period'],g['policy']]));continue
        if (folder/'run-start.json').exists():raise ValueError('Unfinished run must be preserved and diagnosed')
        write(folder/'run-start.json',dict(startedAt=now(),plan=ref(OUT/'plan.json'),inputs=ref(OUT/'input-validation.json')))
        config=deepcopy(p['baseConfig']);config.update(startDate=p['periods'][g['period']][0],endDate=p['periods'][g['period']][1])
        if g['policy']=='extra25bpsEachSide':config['slippageBps']=35.
        signals=read(bound(g['schedule']));emit(dict(accountStart=[g['period'],g['policy']],signals=len(signals)))
        e=DualPathAccount(data,signals,config,system_inputs=system,policy=g['policy'],fee_policy=LegacyFeePolicy())
        e.step(20000);r=e.result();checkpoint=e.checkpoint();summary=summarize_account(r,checkpoint,policy_id=g['policy'],policy_label=g['policy'])
        if r['status']!='completed' or summary['quality']['integrityIssues']:raise ValueError('Engine/cash bridge failed')
        for name,value in [('result.json.gz',r),('checkpoint.json.gz',checkpoint),('summary.json',summary)]:write(folder/name,value)
        write(folder/'completion.json',dict(completedAt=now(),result=ref(folder/'result.json.gz'),checkpoint=ref(folder/'checkpoint.json.gz'),summary=ref(folder/'summary.json'),schedule=g['schedule'],independentAuditPending=True,targetAchieved=False))
        emit(dict(accountCompletedUnaudited=[g['period'],g['policy']],net=summary['account']['netReturnPct'],DD=summary['account']['maxDrawdownPct'],R=summary['closedEpisodes']['payoffRatio'],PF=summary['closedEpisodes']['profitFactor'],n=summary['closedEpisodes']['count']))
        del e,r,checkpoint,summary;gc.collect()
    verify()


def finalize():
    p=verify();inputs=read(OUT/'input-validation.json')
    oracle=module('_guiyin_independent_accounts','scripts/verify-guiyin-dual-path-system.py')
    data=read(bound(p['sources']['dataset']));system=read(bound(inputs['system']))
    rows,proofs,episodes=[],[],[]
    for g in inputs['groups']:
        folder=OUT/g['period']/g['policy'];completion=read(folder/'completion.json')
        result=read(bound(completion['result']));summary=read(bound(completion['summary']))
        proof=oracle.account_audit(result,data,system)
        proofs.append(dict(period=g['period'],policy=g['policy'],**proof))
        row=oracle.metrics(result)
        row.update(period=g['period'],policy=g['policy'],source=ref(folder/'summary.json'),opportunityCounts=result['research']['opportunityCounts'],
                   worstCycleReturnPct=min((t['returnPct'] for t in result['closedTrades']),default=None),openPositions=len(result['positions']))
        row['numericResearchScreensPassed']=bool(row['closed']>=30 and row['payoffRatio'] is not None and row['payoffRatio']>=3 and row['profitFactor'] is not None and row['profitFactor']>=1.5 and row['meanLossReturnPct'] is not None and row['meanLossReturnPct']>=-5 and row['maxDrawdownPct']<=20 and row['netReturnPct']>0 and row['excessReturnPct']>0)
        rows.append(row)
        for t in proof['closedCycles']:
            episodes.append(dict(period=g['period'],policy=g['policy'],**t))
        emit(dict(audited=[g['period'],g['policy']],fills=len(result['trades']),closed=row['closed']))
        del result,summary;gc.collect()
    verify()
    unchanged=U.workspace_snapshot()==p['workspaceJsonBefore']
    if not unchanged:raise ValueError('Protected workspace changed')
    write(OUT/'independent-account-validation.json',dict(passed=True,completedAt=now(),proofs=proofs))
    write(OUT/'all-closed-cycles.json',episodes)
    write(OUT/'readout.json',dict(schema='guiyin-dual-path-complete-system-readout-v1',completedAt=now(),plan=ref(OUT/'plan.json'),inputs=ref(OUT/'input-validation.json'),
          independentValidation=ref(OUT/'independent-account-validation.json'),allClosedCycles=ref(OUT/'all-closed-cycles.json'),rows=rows,
          sourceCycleCoverage=inputs['sourceCycleCoverage'],userJsonFilesUnchanged=unchanged,original49RegistryUnchanged=True,
          newStrategyTrials=1,newAccountsRun=8,targetAchieved=False,certified=False,numericTargetsHumanConfirmed=False,
          scope='Daily next-open industry-rotation cash system, not exact intraday/theme/margin account replication',
          history='Previously exposed development histories. 2024Q4 overlaps source inspiration;2025-2026 are not pristine holdout. Common cutoff2026-10-15;2026-10-16 missing',
          rules=read(CONTRACT),profitabilityConclusion='Pass/fail pertains to fixed research screens only; source and execution limits preclude live-profitability certification'))
    write(OUT/'pre-final-registry.json',read(REGISTRY))
    registry=read(REGISTRY)
    registry.update(updatedAt=now(),targetAchieved=False,completedFixedCandidateTrialsThisContinuation=registry['completedFixedCandidateTrialsThisContinuation']+1,
        completedFixedCandidateTrialsTotalIncludingPrior=registry['completedFixedCandidateTrialsTotalIncludingPrior']+1,newAccountsThisContinuation=registry['newAccountsThisContinuation']+8,
        latestStudy=dict(directory=str(OUT.relative_to(ROOT)).replace('\\','/'),readout=ref(OUT/'readout.json'),validation=ref(OUT/'independent-account-validation.json'),goalAchieved=False),
        activeStudy=dict(directory=str(OUT.relative_to(ROOT)).replace('\\','/'),status='Eight fixed accounts complete; full-source and independent account evidence retained',newAccountsRun=8,newStrategyTrials=1,targetAchieved=False),
        lastGoalTurnClassification='Progress: complete two-path daily system frozen and eight costed accounts independently checked; no target completion claim')
    import json
    REGISTRY.write_text(json.dumps(registry,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    emit(dict(readout=ref(OUT/'readout.json'),rows=rows,userJsonFilesUnchanged=True))


if __name__=='__main__':
    ap=ArgumentParser();ap.add_argument('command',choices=('register','prepare','run','finalize'));a=ap.parse_args()
    {'register':register,'prepare':prepare,'run':run_accounts,'finalize':finalize}[a.command]()
