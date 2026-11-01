"""Freeze lifecycle states and all six schedules before any V3 account."""
from argparse import ArgumentParser
from copy import deepcopy
import gc
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def module(name,path):
    s=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m


U=module('_sector_lifecycle_utils','scripts/run-liquid-trend-breakout.py')
read,write,ref,bound,sha,now,emit=(getattr(U,k) for k in ('read','write','ref','bound','sha','now','emit'))
BASE=ROOT/'artifacts/strategy-search';PARENT=BASE/'market-sector-stock-base-v2/attempt-001'
STUDY=BASE/'sector-lifecycle-system-v3';OUT=STUDY/'attempt-001';PLAN=OUT/'plan.json'
PROTOCOL=ROOT/'docs/sector-lifecycle-system-v3.json'
POLICIES=('completeSystem','withoutMarketAdmission','withoutSectorAdmission','withoutCatchupEntry','v2AdmissionWithLifecycleExit')


def verify_plan():
    p=read(PLAN)
    for path,digest in p['sourcePins'].items():bound({'path':path,'sha256':digest})
    for value in p['bindings']:bound(value)
    return p


def register():
    if OUT.exists():raise ValueError('Preserve existing execution attempt')
    parent,inputs,validation=(read(PARENT/name) for name in ('plan.json','input-validation.json','validation.json'))
    hypothesis=read(STUDY/'hypothesis-registration.json');rules=read(PROTOCOL)
    if hypothesis['protocol']!=ref(PROTOCOL) or not validation['passed'] or not inputs['passed']:raise ValueError('Pinned hypothesis/audited parent required')
    for k in ('parentPreparedBaseProtocol','parentAccountValidation','sourceCoverageDiagnostic'):bound(rules[k])
    pins={**parent['sourcePins']}
    for path in ('docs/sector-lifecycle-system-v3.json','service/research_sector_lifecycle_system.py','scripts/run-sector-lifecycle-system.py','scripts/verify-sector-lifecycle-inputs.py','tests/test_research_sector_lifecycle_system.py'):
        pins[path]=sha(ROOT/path)
    full=next(g['schedule'] for g in inputs['groups'] if g['period']=='primary' and g['policy']=='completeSystem')
    bindings=parent['bindings']+[ref(PROTOCOL),ref(STUDY/'hypothesis-registration.json'),ref(PARENT/'plan.json'),ref(PARENT/'input-validation.json'),ref(PARENT/'validation.json'),
        inputs['formation'],inputs['formationValidation'],inputs['systemInputs'],inputs['dataset'],validation['readout'],validation['fullAccountAudit'],full]
    for value in bindings:bound(value)
    OUT.mkdir()
    write(PLAN,{'schema':'sector-lifecycle-system-v3-plan','registeredAt':now(),'protocol':ref(PROTOCOL),'rules':parent['rules'],'lifecycleRules':rules,
        'sourcePins':pins,'bindings':bindings,'matrix':parent['matrix'],'joins':parent['joins'],'baseConfig':parent['baseConfig'],
        'workspaceJsonBefore':U.workspace_snapshot(),'parentDataset':inputs['dataset'],'parentSystemInputs':inputs['systemInputs'],
        'originalIndustryContext':parent['parentFormation'],'originalContextValidation':parent['parentFormationValidation'],
        'parentAccountValidation':ref(PARENT/'validation.json'),'parentFrozenFullSchedule':full,'plannedAccounts':rules['newAccountsPlanned'],
        'newStrategyTrials':1,'newAccountsPlanned':6,'holdoutClaim':False,'targetAchieved':False,
        'implementationInterpretations':{'healthFailure':'Every feature-valid industry is counted, including unestablished industries in the no-sector admission control',
            'activationDate':'First selection since most recent retirement or snapshot reset; repeated confirmation does not restart it',
            'industryExitControl':'Exact V2 primary full schedule bytes; original V1 selected-industry list only for this control entry validation, common lifecycle health/risk counters for exits',
            'identity':'Inherited engine strategy ID retained; lifecycleIdentity, changed system input digest and new signal namespace identify V3'}})
    for path in pins:
        target=OUT/'frozen-code'/path;target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as f:f.write((ROOT/path).read_bytes())
    emit({'registered':True,'plan':ref(PLAN),'plannedAccounts':6})


def prepare():
    from service.research_sector_lifecycle_system import transform_contexts,rename_entry_formation
    from service.research_prepared_base_system import build_inputs
    plan=verify_plan();write(OUT/'prepare-start.json',{'startedAt':now(),'plan':ref(PLAN)})
    tests=subprocess.run([sys.executable,'-X','utf8','-B','-m','unittest','tests.test_research_sector_lifecycle_system','tests.test_research_prepared_base_system',
        'tests.test_research_market_sector_stock','tests.test_research_market_sector_account','tests.test_research_volatility_trail','tests.test_research_fixed_signal_account',
        'tests.test_research_account_fees','tests.test_research_account_outcomes','tests.test_market_sector_risk_oracle','tests.test_market_sector_ledger_oracle','tests.test_prepared_base_system_oracle'],
        cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    write(OUT/'pre-account-tests.json',{'passed':tests.returncode==0,'exitCode':tests.returncode,'stdout':tests.stdout,'stderr':tests.stderr})
    if tests.returncode:raise ValueError('Pre-account checks failed')
    if not read(bound(plan['originalContextValidation']))['passed']:raise ValueError('Independent original context proof missing')
    original=read(bound(plan['originalIndustryContext']));life=transform_contexts(original)
    write(OUT/'lifecycle-contexts.json.gz',life)
    oracle=module('_lifecycle_backward_source_check','scripts/verify-sector-lifecycle-inputs.py')
    proof=oracle.audit(original,life);write(OUT/'lifecycle-validation.json',proof);emit({'lifecycleIndependentAudit':proof})
    with np.load(bound(plan['matrix']),allow_pickle=False) as stored:matrix={k:stored[k] for k in stored.files}
    joins=read(bound(plan['joins']));base=build_inputs(matrix,joins,life);write(OUT/'entry-preparation.candidate.json.gz',base)
    entry_oracle=module('_lifecycle_entry_window_check','scripts/verify-prepared-base-inputs.py')
    entryproof=entry_oracle.audit(matrix,joins,life,base);write(OUT/'entry-preparation-validation.json',entryproof)
    formation=rename_entry_formation(base);renameproof=oracle.audit_rename(base,formation);write(OUT/'formation.candidate.json.gz',formation)
    emit({'formationIndependentlyChecked':True,'counts':formation['counts'],'entryProof':entryproof,'renameProof':renameproof,'accountOutcomesObserved':False})
    write(OUT/'formation-validation.json',{'passed':True,'lifecycle':ref(OUT/'lifecycle-validation.json'),'entries':ref(OUT/'entry-preparation-validation.json'),
        'rename':renameproof,'context':ref(OUT/'lifecycle-contexts.json.gz'),'formation':ref(OUT/'formation.candidate.json.gz')})
    system=read(bound(plan['parentSystemInputs']));fullschedule=read(bound(plan['parentFrozenFullSchedule']))
    metadata={**formation['metadata'],**{s['signalId']:system['metadata'][s['signalId']] for s in fullschedule}}
    contexts=[{'date':d['date'],'snapshotId':d['snapshotId'],'market':{k:d['market'][k] for k in ('admission','riskExit','crisisCount')},
        'selectedSectors':d['selectedSectors'],'sectors':[{k:s[k] for k in ('sectorCode','failCount','riskExit','lifecycle')} for s in d['sectors']]} for d in life['daily']]
    system.update(daily=contexts,metadata=metadata,formationSource=ref(OUT/'formation.candidate.json.gz'),independentProof=ref(OUT/'formation-validation.json'))
    write(OUT/'system-inputs.json.gz',system)
    control=deepcopy(system)
    original_by_date={d['date']:d for d in original['daily']}
    for day in control['daily']:day['selectedSectors']=original_by_date[day['date']]['selectedSectors']
    write(OUT/'control-system-inputs.json.gz',control)
    groups=[]
    for item in plan['plannedAccounts']:
        period,policy=item['period'],item['policy'];dates=plan['rules']['periods'][period];path=OUT/period/policy/'signals.json'
        is_control=policy=='v2AdmissionWithLifecycleExit'
        if is_control:
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as f:f.write(bound(plan['parentFrozenFullSchedule']).read_bytes())
            if sha(path)!=plan['parentFrozenFullSchedule']['sha256']:raise ValueError('Control changed V2 frozen schedule bytes')
            schedule=fullschedule
        else:
            schedule=[s for s in formation['schedules'][policy] if dates['startDate']<=s['signalDate']<=dates['endDate']];write(path,schedule)
        groups.append({'period':period,'policy':policy,'enginePolicy':item.get('enginePolicy',policy),'signals':len(schedule),'schedule':ref(path),
            'systemInputs':ref(OUT/('control-system-inputs.json.gz' if is_control else 'system-inputs.json.gz'))})
    del matrix,original,life,base,formation,system,control;gc.collect()
    if U.workspace_snapshot()!=plan['workspaceJsonBefore']:raise ValueError('Protected workspace changed')
    verify_plan()
    write(OUT/'input-validation.json',{'passed':True,'completedAt':now(),'planSha256':sha(PLAN),'allSixSchedulesFrozenBeforeAccounts':True,'dataset':plan['parentDataset'],
        'groups':groups,'formation':ref(OUT/'formation.candidate.json.gz'),'formationValidation':ref(OUT/'formation-validation.json'),
        'lifecycleContext':ref(OUT/'lifecycle-contexts.json.gz'),'lifecycleValidation':ref(OUT/'lifecycle-validation.json'),
        'tests':ref(OUT/'pre-account-tests.json'),'controlFrozenScheduleBytesUnchanged':True,'population':4999,'newAccountsRun':0,'userJsonFilesUnchanged':True})
    emit({'inputsReady':True,'groups':[{k:g[k] for k in ('period','policy','signals')} for g in groups]})


def run_account(period,policy):
    from service.research_sector_lifecycle_system import SectorLifecycleAccount
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
    engine=SectorLifecycleAccount(data,schedule,config,system_inputs=system,policy=group['enginePolicy'],lifecycle_policy=policy,fee_policy=LegacyFeePolicy())
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
