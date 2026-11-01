"""Matched numerical repair: exact V3 signals and contexts, no new hypothesis."""
from argparse import ArgumentParser
from copy import deepcopy
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def module(name,path):
    s=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

U=module('_lifecycle_precision_run_utils','scripts/run-liquid-trend-breakout.py')
read,write,ref,bound,sha,now,emit=(getattr(U,k) for k in ('read','write','ref','bound','sha','now','emit'))
BASE=ROOT/'artifacts/strategy-search';PARENT=BASE/'sector-lifecycle-system-v3/attempt-001'
STUDY=BASE/'sector-lifecycle-system-v3/price-boundary-repair-v1';OUT=STUDY/'attempt-001';PLAN=OUT/'plan.json'
PROTOCOL=ROOT/'docs/sector-lifecycle-price-boundary-repair-v1.json'
POLICIES=('completeSystem','withoutMarketAdmission','withoutSectorAdmission','withoutCatchupEntry','v2AdmissionWithLifecycleExit')

def verify_plan():
    plan=read(PLAN)
    for p,h in plan['sourcePins'].items():bound({'path':p,'sha256':h})
    for value in plan['bindings']:bound(value)
    return plan

def register():
    if OUT.exists():raise ValueError('Preserve numerical repair attempt')
    parent,inputs,validation=(read(PARENT/name) for name in ('plan.json','input-validation.json','validation.json'))
    if not validation['passed'] or validation['numericalContractConformancePassed'] or validation['numericBoundaryExceptionCount']<1:raise ValueError('Source-bound numerical exception and completed frozen-arithmetic audit required')
    if not inputs['passed']:raise ValueError('Original six fixed inputs required')
    pins={**parent['sourcePins']}
    for path in ('docs/sector-lifecycle-price-boundary-repair-v1.json','service/research_sector_lifecycle_precision.py','scripts/run-lifecycle-price-repair.py',
            'tests/test_sector_lifecycle_numeric_reference.py','scripts/verify-sector-lifecycle-risk.py'):
        pins[path]=sha(ROOT/path)
    bindings=parent['bindings']+[ref(PROTOCOL),ref(PARENT/'plan.json'),ref(PARENT/'input-validation.json'),ref(PARENT/'validation.json'),
        validation['readout'],validation['fullAccountAudit'],validation['numericBoundaryDiagnostic']]
    for value in bindings:bound(value)
    OUT.mkdir(parents=True)
    write(PLAN,{'schema':'lifecycle-structural-price-repair-plan','registeredAt':now(),'protocol':ref(PROTOCOL),'rules':parent['rules'],
        'lifecycleRules':parent['lifecycleRules'],'sourcePins':pins,'bindings':bindings,'baseConfig':parent['baseConfig'],
        'workspaceJsonBefore':U.workspace_snapshot(),'parentDataset':inputs['dataset'],'parentSystemInputs':parent['parentSystemInputs'],
        'originalIndustryContext':parent['originalIndustryContext'],'originalContextValidation':parent['originalContextValidation'],
        'parentFrozenFullSchedule':parent['parentFrozenFullSchedule'],'parentAccountValidation':ref(PARENT/'validation.json'),
        'originalSixInputs':ref(PARENT/'input-validation.json'),'plannedAccounts':parent['plannedAccounts'],
        'repairOfCandidateTrial':58,'newStrategyTrials':0,'newAccountsPlanned':6,'signalRuleChanges':0,'priceSourceChanges':0,
        'numericalComparisonPolicy':'sixteenUlpsAtStructuralPrice','holdoutClaim':False,'targetAchieved':False})
    for path in pins:
        target=OUT/'frozen-code'/path;target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as f:f.write((ROOT/path).read_bytes())
    emit({'registered':True,'plan':ref(PLAN),'newStrategyTrials':0,'plannedMatchedAccounts':6})

def prepare():
    plan=verify_plan();parent=read(bound(plan['originalSixInputs']))
    write(OUT/'prepare-start.json',{'startedAt':now(),'plan':ref(PLAN)})
    tests=subprocess.run([sys.executable,'-X','utf8','-B','-m','unittest','tests.test_research_sector_lifecycle_system','tests.test_research_prepared_base_system',
        'tests.test_research_market_sector_stock','tests.test_research_market_sector_account','tests.test_research_volatility_trail','tests.test_research_fixed_signal_account',
        'tests.test_research_account_fees','tests.test_research_account_outcomes','tests.test_market_sector_risk_oracle','tests.test_market_sector_ledger_oracle',
        'tests.test_prepared_base_system_oracle','tests.test_sector_lifecycle_system_oracle','tests.test_sector_lifecycle_numeric_reference'],
        cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    write(OUT/'pre-account-tests.json',{'passed':tests.returncode==0,'exitCode':tests.returncode,'stdout':tests.stdout,'stderr':tests.stderr})
    if tests.returncode:raise ValueError('Matched numerical-repair tests failed')
    groups=[]
    for source in parent['groups']:
        period,policy=source['period'],source['policy'];path=OUT/period/policy/'signals.json';path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as f:f.write(bound(source['schedule']).read_bytes())
        if sha(path)!=source['schedule']['sha256']:raise ValueError('Changed frozen entry bytes')
        context=OUT/('control-system-inputs.json.gz' if policy=='v2AdmissionWithLifecycleExit' else 'system-inputs.json.gz')
        if not context.exists():
            with context.open('xb') as f:f.write(bound(source['systemInputs']).read_bytes())
        if sha(context)!=source['systemInputs']['sha256']:raise ValueError('Changed frozen context bytes')
        groups.append({**source,'schedule':ref(path),'systemInputs':ref(context),'originalSchedule':source['schedule'],'originalSystemInputs':source['systemInputs']})
    if len(groups)!=6 or U.workspace_snapshot()!=plan['workspaceJsonBefore']:raise ValueError('Complete six matched accounts/protected workspace required')
    verify_plan()
    write(OUT/'input-validation.json',{'passed':True,'completedAt':now(),'planSha256':sha(PLAN),'allSixSchedulesFrozenBeforeAccounts':True,
        'copiedScheduleAndContextBytesUnchanged':True,'dataset':parent['dataset'],'groups':groups,
        **{k:parent[k] for k in ('formation','formationValidation','lifecycleContext','lifecycleValidation')},
        'tests':ref(OUT/'pre-account-tests.json'),'newStrategyTrials':0,'newAccountsRun':0,'userJsonFilesUnchanged':True})
    emit({'inputsReady':True,'allSixSchedulesAndContextsByteIdentical':True,'newStrategyTrials':0,
        'groups':[{k:g[k] for k in ('period','policy','signals')} for g in groups]})

def run_account(period,policy):
    from service.research_sector_lifecycle_precision import PrecisionSafeLifecycleAccount
    from service.research_account_fees import LegacyFeePolicy
    from service.research_account_outcomes import summarize_account
    plan,inputs=verify_plan(),read(OUT/'input-validation.json')
    if not inputs['passed'] or inputs['planSha256']!=sha(PLAN) or not inputs['allSixSchedulesFrozenBeforeAccounts']:raise ValueError('All six frozen inputs required')
    group=next(g for g in inputs['groups'] if (g['period'],g['policy'])==(period,policy));folder=OUT/period/policy
    write(folder/'run-start.json',{'startedAt':now(),'plan':ref(PLAN),'inputs':ref(OUT/'input-validation.json'),'systemInputs':group['systemInputs']})
    data,system=read(bound(inputs['dataset'])),read(bound(group['systemInputs']))
    config=deepcopy(plan['baseConfig']);dates=plan['rules']['periods'][period]
    config.update(startDate=dates['startDate'],endDate=dates['endDate'],initialCapital=dates['initialCapital'])
    schedule=read(bound(group['schedule']));emit({'accountStart':[period,policy],'signals':len(schedule)})
    engine=PrecisionSafeLifecycleAccount(data,schedule,config,system_inputs=system,policy=group['enginePolicy'],lifecycle_policy=policy,fee_policy=LegacyFeePolicy())
    engine.step(20000);result,checkpoint=engine.result(),engine.checkpoint();summary=summarize_account(result,checkpoint,policy_id=policy,policy_label=policy)
    if result['status']!='completed' or summary['quality']['integrityIssues'] or result['research']['identity']['signalSchedule']!=schedule:raise ValueError('Account or fee bridge failed')
    for suffix,value in (('result.json.gz',result),('checkpoint.json.gz',checkpoint),('summary.json',summary)):write(folder/suffix,value)
    verify_plan()
    write(folder/'completion.json',{'completedAt':now(),'plan':ref(PLAN),'inputs':ref(OUT/'input-validation.json'),'systemInputs':group['systemInputs'],
        'period':period,'policy':policy,'enginePolicy':group['enginePolicy'],'result':ref(folder/'result.json.gz'),'checkpoint':ref(folder/'checkpoint.json.gz'),
        'summary':ref(folder/'summary.json'),'schedule':group['schedule'],'independentAccountAuditPending':True,'targetAchieved':False})
    c=summary['closedEpisodes'];emit({'accountCompletedUnaudited':[period,policy],'returnPct':summary['account']['netReturnPct'],
        'DD':summary['account']['maxDrawdownPct'],'R':c['payoffRatio'],'PF':c['profitFactor'],'closed':c['count'],'meanLossPct':c['meanLossReturnPct']})


if __name__=='__main__':
    p=ArgumentParser();p.add_argument('command',choices=('register','prepare','run'));p.add_argument('--attempt',default='001');p.add_argument('--period',choices=('primary','weakMarketStress'));p.add_argument('--policy',choices=POLICIES);a=p.parse_args()
    if not re.fullmatch(r'[0-9]{3}',a.attempt):raise ValueError('Explicit three-digit attempt required')
    OUT=STUDY/('attempt-'+a.attempt);PLAN=OUT/'plan.json'
    register() if a.command=='register' else prepare() if a.command=='prepare' else run_account(a.period,a.policy)
