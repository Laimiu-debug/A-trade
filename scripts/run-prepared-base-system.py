"""Freeze one complete prepared-base hypothesis, then execute five accounts."""
from argparse import ArgumentParser
from copy import deepcopy
import gc
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value);return value


U=module('_prepared_base_frozen_utils','scripts/run-liquid-trend-breakout.py')
read,write,ref,bound,sha,now,emit=(getattr(U,k) for k in ('read','write','ref','bound','sha','now','emit'))
BASE=ROOT/'artifacts/strategy-search'
PARENT=BASE/'market-sector-stock-accounts-v1/attempt-001'
STUDY=BASE/'market-sector-stock-base-v2'
OUT=STUDY/'attempt-001'
PLAN=OUT/'plan.json'
PROTOCOL=ROOT/'docs/market-sector-stock-base-v2.json'
POLICIES=('completeSystem','withoutMarketAdmission','withoutSectorAdmission','withoutCatchupEntry')


def verify_plan():
    plan=read(PLAN)
    for p,h in plan['sourcePins'].items():bound({'path':p,'sha256':h})
    for value in plan['bindings']:bound(value)
    return plan


def register():
    if OUT.exists():raise ValueError('Preserve existing attempt; choose another explicit identity')
    parent=read(PARENT/'plan.json');inputs=read(PARENT/'input-validation.json');proof=read(PARENT/'validation.json')
    hypothesis=read(STUDY/'hypothesis-registration.json');rules=read(PROTOCOL)
    if hypothesis['protocol']!=ref(PROTOCOL) or not proof['passed'] or not inputs['passed']:raise ValueError('Frozen hypothesis and audited parent required')
    for k in ('parentProtocol','parentAccountValidation'):bound(rules[k])
    pins={**parent['sourcePins']}
    for path in ('docs/market-sector-stock-base-v2.json','service/research_prepared_base_system.py','scripts/run-prepared-base-system.py',
            'scripts/verify-prepared-base-inputs.py','tests/test_research_prepared_base_system.py'):
        pins[path]=sha(ROOT/path)
    bindings=parent['bindings']+[ref(PROTOCOL),ref(STUDY/'hypothesis-registration.json'),ref(PARENT/'plan.json'),ref(PARENT/'input-validation.json'),
        ref(PARENT/'validation.json'),ref(PARENT/'formation.candidate.json.gz'),ref(PARENT/'formation-validation.json'),
        inputs['dataset'],inputs['systemInputs'],proof['readout'],proof['fullAccountAudit']]
    for value in bindings:bound(value)
    OUT.mkdir()
    write(PLAN,{'schema':'prepared-base-system-v2-plan','registeredAt':now(),'protocol':ref(PROTOCOL),'rules':parent['rules'],
        'preparedBaseRules':rules,'sourcePins':pins,'bindings':bindings,'matrix':parent['matrix'],'joins':parent['joins'],
        'baseConfig':parent['baseConfig'],'workspaceJsonBefore':U.workspace_snapshot(),'parentDataset':inputs['dataset'],
        'parentSystemInputs':inputs['systemInputs'],'parentFormation':ref(PARENT/'formation.candidate.json.gz'),
        'parentFormationValidation':ref(PARENT/'formation-validation.json'),'parentAccountValidation':ref(PARENT/'validation.json'),
        'plannedAccounts':rules['newAccountsPlanned'],'reusedAccountReadout':proof['readout'],'newStrategyTrials':1,'newAccountsPlanned':5,
        'implementationInterpretations':{'boundary':'Risk coherence checked as .95*price<=fixedSupport<=price*(1-signalNTR20), avoiding ratio subtraction rounding at exact5percent',
            'contexts':'All611 previously independently verified macro/industry states are frozen dependencies; no market or industry recomputation/tuning',
            'openingPriority':'Common raw-opening floor/ceiling takes precedence; prepared-base two-sided risk checked only when that common raw guard passes',
            'strategyId':'Inherited marketSectorStockV1 engine adapter ID retained; preparedBaseIdentity and frozen signal IDs distinguish V2'},
        'holdoutClaim':False,'targetAchieved':False})
    for path in pins:
        target=OUT/'frozen-code'/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/path).read_bytes())
    emit({'registered':True,'plan':ref(PLAN),'newAccountsPlanned':5})


def prepare():
    from service.research_prepared_base_system import build_inputs
    plan=verify_plan();write(OUT/'prepare-start.json',{'startedAt':now(),'plan':ref(PLAN)})
    tests=subprocess.run([sys.executable,'-X','utf8','-B','-m','unittest','tests.test_research_prepared_base_system',
        'tests.test_research_market_sector_stock','tests.test_research_market_sector_account','tests.test_research_volatility_trail',
        'tests.test_research_fixed_signal_account','tests.test_research_account_fees','tests.test_research_account_outcomes',
        'tests.test_market_sector_risk_oracle','tests.test_market_sector_ledger_oracle'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    write(OUT/'pre-account-tests.json',{'passed':tests.returncode==0,'exitCode':tests.returncode,'stdout':tests.stdout,'stderr':tests.stderr})
    if tests.returncode:raise ValueError('Pre-account checks failed')
    parent_proof=read(bound(plan['parentFormationValidation']))
    if not parent_proof['passed']:raise ValueError('Independent parent macro/industry proof required')
    context=read(bound(plan['parentFormation']));joins=read(bound(plan['joins']))
    with np.load(bound(plan['matrix']),allow_pickle=False) as stored:matrix={k:stored[k] for k in stored.files}
    emit({'buildStart':True,'stocks':len(matrix['symbols']),'decisionDays':len(context['daily'])})
    formation=build_inputs(matrix,joins,context);write(OUT/'formation.candidate.json.gz',formation)
    emit({'preparedBaseFormationBuilt':formation['counts'],'accountOutcomesObserved':False})
    oracle=module('_prepared_base_direct_window_check','scripts/verify-prepared-base-inputs.py')
    proof=oracle.audit(matrix,joins,context,formation);write(OUT/'formation-validation.json',proof)
    emit({'independentPreparedBaseFormationPassed':True,**proof})
    system=read(bound(plan['parentSystemInputs']))
    system.update(metadata=formation['metadata'],formationSource=ref(OUT/'formation.candidate.json.gz'),independentProof=ref(OUT/'formation-validation.json'))
    write(OUT/'system-inputs.json.gz',system)
    groups=[]
    for item in plan['plannedAccounts']:
        period,policy=item['period'],item['policy'];dates=plan['rules']['periods'][period]
        schedule=[s for s in formation['schedules'][policy] if dates['startDate']<=s['signalDate']<=dates['endDate']]
        path=OUT/period/policy/'signals.json';write(path,schedule)
        groups.append({'period':period,'policy':policy,'schedule':ref(path),'signals':len(schedule)})
    del matrix,context,formation,system;gc.collect()
    if U.workspace_snapshot()!=plan['workspaceJsonBefore']:raise ValueError('Protected workspace changed')
    verify_plan()
    write(OUT/'input-validation.json',{'passed':True,'completedAt':now(),'planSha256':sha(PLAN),'allFiveSchedulesFrozenBeforeAccounts':True,
        'dataset':plan['parentDataset'],'systemInputs':ref(OUT/'system-inputs.json.gz'),'formation':ref(OUT/'formation.candidate.json.gz'),
        'formationValidation':ref(OUT/'formation-validation.json'),'parentFormationValidation':plan['parentFormationValidation'],
        'tests':ref(OUT/'pre-account-tests.json'),'groups':groups,'population':4999,'newAccountsRun':0,'userJsonFilesUnchanged':True})
    emit({'inputsReady':True,'groups':[{k:g[k] for k in ('period','policy','signals')} for g in groups]})


def run_account(period,policy):
    from service.research_account_fees import LegacyFeePolicy
    from service.research_account_outcomes import summarize_account
    from service.research_prepared_base_system import PreparedBaseAccount
    plan,inputs=verify_plan(),read(OUT/'input-validation.json')
    if not inputs['passed'] or inputs['planSha256']!=sha(PLAN) or not inputs['allFiveSchedulesFrozenBeforeAccounts']:raise ValueError('Frozen five schedules required')
    group=next(g for g in inputs['groups'] if (g['period'],g['policy'])==(period,policy))
    folder=OUT/period/policy
    write(folder/'run-start.json',{'startedAt':now(),'plan':ref(PLAN),'inputs':ref(OUT/'input-validation.json')})
    data,system=read(bound(inputs['dataset'])),read(bound(inputs['systemInputs']))
    config=deepcopy(plan['baseConfig']);dates=plan['rules']['periods'][period]
    config.update(startDate=dates['startDate'],endDate=dates['endDate'],initialCapital=dates['initialCapital'])
    schedule=read(bound(group['schedule']));emit({'accountStart':[period,policy],'signals':len(schedule)})
    account=PreparedBaseAccount(data,schedule,config,system_inputs=system,policy=policy,fee_policy=LegacyFeePolicy())
    account.step(20000);result,checkpoint=account.result(),account.checkpoint()
    summary=summarize_account(result,checkpoint,policy_id=policy,policy_label=policy)
    if result['status']!='completed' or summary['quality']['integrityIssues'] or result['research']['identity']['signalSchedule']!=schedule:raise ValueError('Account or cash/fee reconciliation failed')
    for suffix,value in (('result.json.gz',result),('checkpoint.json.gz',checkpoint),('summary.json',summary)):write(folder/suffix,value)
    verify_plan()
    write(folder/'completion.json',{'completedAt':now(),'plan':ref(PLAN),'inputs':ref(OUT/'input-validation.json'),'period':period,'policy':policy,
        'result':ref(folder/'result.json.gz'),'checkpoint':ref(folder/'checkpoint.json.gz'),'summary':ref(folder/'summary.json'),'schedule':group['schedule'],
        'independentAccountAuditPending':True,'targetAchieved':False})
    c=summary['closedEpisodes'];emit({'accountCompletedUnaudited':[period,policy],'returnPct':summary['account']['netReturnPct'],
        'DD':summary['account']['maxDrawdownPct'],'R':c['payoffRatio'],'PF':c['profitFactor'],'closed':c['count'],'meanLossPct':c['meanLossReturnPct']})


if __name__=='__main__':
    p=ArgumentParser();p.add_argument('command',choices=('register','prepare','run'));p.add_argument('--attempt',default='001')
    p.add_argument('--period',choices=('primary','weakMarketStress'));p.add_argument('--policy',choices=POLICIES);a=p.parse_args()
    if not re.fullmatch(r'[0-9]{3}',a.attempt):raise ValueError('Explicit three-digit attempt required')
    OUT=STUDY/('attempt-'+a.attempt);PLAN=OUT/'plan.json'
    register() if a.command=='register' else prepare() if a.command=='prepare' else run_account(a.period,a.policy)
