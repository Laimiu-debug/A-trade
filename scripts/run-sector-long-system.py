"""Freeze current-industry long leaders plus anchored catchup before accounts."""
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


U=module('_sector_long_system_utils','scripts/run-liquid-trend-breakout.py')
read,write,ref,bound,sha,now,emit=(getattr(U,k) for k in ('read','write','ref','bound','sha','now','emit'))
BASE=ROOT/'artifacts/strategy-search';PARENT=BASE/'sector-lifecycle-system-v3/price-boundary-repair-v1/attempt-001'
STUDY=BASE/'sector-long-breakout-system-v4';OUT=STUDY/'attempt-001';PLAN=OUT/'plan.json';PROTOCOL=ROOT/'docs/sector-long-breakout-system-v4.json'
POLICIES=('completeSystem','withoutMarketAdmission','withoutSectorAdmission','withoutCatchupEntry','catchupOnly')


def verify_plan():
    plan=read(PLAN)
    for p,h in plan['sourcePins'].items():bound({'path':p,'sha256':h})
    for v in plan['bindings']:bound(v)
    return plan


def register():
    if OUT.exists():raise ValueError('Preserve existing attempt')
    parent,inputs,validation=(read(PARENT/name) for name in ('plan.json','input-validation.json','validation.json'))
    rules=read(PROTOCOL);registration=read(STUDY/'hypothesis-registration.json')
    if registration['protocol']!=ref(PROTOCOL) or not validation['passed'] or not validation['numericalContractConformancePassed']:raise ValueError('Fixed hypothesis and corrected independent account base required')
    for k in ('parentLifecycleProtocol','parentAccountValidation','parentPrecisionCorrection','additionalV2DevelopmentValidation','historicalLongBreakoutDefinition','historicalLongBreakoutReadout'):bound(rules[k])
    original_ref=next(v for v in parent['bindings'] if v['path']=='artifacts/strategy-search/sector-lifecycle-system-v3/attempt-001/plan.json')
    original=read(bound(original_ref))
    v2_validation=read(bound(rules['additionalV2DevelopmentValidation']));v2inputs=read(bound(v2_validation['inputs']))
    system_ref=next(g['systemInputs'] for g in inputs['groups'] if g['period']=='primary' and g['policy']=='v2AdmissionWithLifecycleExit')
    pins={**parent['sourcePins']}
    for path in ('docs/sector-long-breakout-system-v4.json','service/research_sector_long_breakout_system.py','scripts/run-sector-long-system.py',
        'scripts/verify-sector-long-inputs.py','tests/test_research_sector_long_system.py','scripts/audit-sector-lifecycle-system.py'):
        pins[path]=sha(ROOT/path)
    bindings=parent['bindings']+[ref(PROTOCOL),ref(STUDY/'hypothesis-registration.json'),registration['readableRules'],ref(PARENT/'plan.json'),ref(PARENT/'input-validation.json'),
        ref(PARENT/'validation.json'),validation['readout'],validation['fullAccountAudit'],system_ref,inputs['dataset'],inputs['lifecycleContext'],inputs['lifecycleValidation'],
        parent['originalIndustryContext'],v2_validation['inputs'],v2inputs['formation'],v2inputs['formationValidation'],original_ref,original['matrix'],original['joins']]
    for v in bindings:bound(v)
    OUT.mkdir()
    write(PLAN,{'schema':'sector-long-breakout-system-v4-plan','registeredAt':now(),'protocol':ref(PROTOCOL),'rules':parent['rules'],'longRules':rules,
        'sourcePins':pins,'bindings':bindings,'matrix':original['matrix'],'joins':original['joins'],'baseConfig':parent['baseConfig'],
        'workspaceJsonBefore':U.workspace_snapshot(),'dataset':inputs['dataset'],'baseHealthSystemInputs':system_ref,'originalAdmissionContext':parent['originalIndustryContext'],
        'lifecycleContext':inputs['lifecycleContext'],'lifecycleValidation':inputs['lifecycleValidation'],'anchorFormation':v2inputs['formation'],'anchorValidation':v2inputs['formationValidation'],
        'parentAccountValidation':ref(PARENT/'validation.json'),'plannedAccounts':rules['newAccountsPlanned'],'newStrategyTrials':1,'newAccountsPlanned':6,
        'implementationInterpretations':{'priceArithmetic':'Validated raw cent prices; integer cent sums compare strict MA60/MA120 trends and exact prior20-return cap without changing source quotes',
            'peerReturn':'Complete price-valid121-slot windows and no explicitly suspended bar; amount positivity is required separately for each candidate full141-slot window',
            'catchupAnchors':'Only previously frozen V2 catchup events under matching current-admission policies; new IDs/ranks retain original anchor ID/rank provenance',
            'holdingProjection':'Reuse the corrected V3 health-exit input with original V1 fresh-industry admission, independently compare all611 days',
            'rawPrices':'No source prices changed; explicit suspension truth values are extracted separately from the same raw account dataset'},
        'holdoutClaim':False,'targetAchieved':False})
    for path in pins:
        target=OUT/'frozen-code'/path;target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as f:f.write((ROOT/path).read_bytes())
    emit({'registered':True,'plan':ref(PLAN),'plannedAccounts':6})


def prepare():
    from service.research_sector_long_breakout_system import build_inputs
    plan=verify_plan();write(OUT/'prepare-start.json',{'startedAt':now(),'plan':ref(PLAN)})
    tests=subprocess.run([sys.executable,'-X','utf8','-B','-m','unittest','tests.test_research_sector_long_system','tests.test_research_sector_lifecycle_system',
        'tests.test_research_prepared_base_system','tests.test_research_market_sector_stock','tests.test_research_market_sector_account','tests.test_research_volatility_trail',
        'tests.test_research_fixed_signal_account','tests.test_research_account_fees','tests.test_research_account_outcomes','tests.test_market_sector_risk_oracle',
        'tests.test_market_sector_ledger_oracle','tests.test_prepared_base_system_oracle','tests.test_sector_lifecycle_system_oracle','tests.test_sector_lifecycle_numeric_reference'],
        cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    write(OUT/'pre-account-tests.json',{'passed':tests.returncode==0,'exitCode':tests.returncode,'stdout':tests.stdout,'stderr':tests.stderr})
    if tests.returncode:raise ValueError('Long-system pre-account checks failed')
    if not read(bound(plan['anchorValidation']))['passed'] or not read(bound(plan['lifecycleValidation']))['passed']:raise ValueError('Independent inherited anchor/industry proof required')
    with np.load(bound(plan['matrix']),allow_pickle=False) as stored:matrix={k:stored[k] for k in stored.files}
    dates={d:i for i,d in enumerate(matrix['calendar'].tolist())};symbols={s:i for i,s in enumerate(matrix['symbols'].tolist())}
    matrix['explicitSuspended']=np.zeros(matrix['close'].shape,bool)
    data=read(bound(plan['dataset']))
    for security in data['securities']:
        for bar in security['bars']:
            if bar.get('suspended'):matrix['explicitSuspended'][symbols[security['symbol']],dates[bar['date']]]=True
    oracle=module('_sector_long_direct_source_oracle','scripts/verify-sector-long-inputs.py')
    sourceproof=oracle.audit_suspensions(data,matrix);del data;gc.collect()
    with (OUT/'explicit-suspensions.npy').open('xb') as f:np.save(f,matrix['explicitSuspended'],allow_pickle=False)
    write(OUT/'suspension-source-validation.json',{**sourceproof,'dataset':plan['dataset'],'mask':ref(OUT/'explicit-suspensions.npy')})
    original,joins,anchors=(read(bound(plan[k])) for k in ('originalAdmissionContext','joins','anchorFormation'))
    emit({'formationBuildStart':True,'stocks':len(symbols),'decisionDays':len(original['daily']),'explicitSuspendedSlots':sourceproof['explicitSuspendedSlots']})
    formation=build_inputs(matrix,joins,original,anchors);write(OUT/'formation.candidate.json.gz',formation)
    emit({'formationBuilt':formation['counts'],'accountOutcomesObserved':False})
    proof=oracle.audit(matrix,joins,original,anchors,formation);write(OUT/'formation-validation.json',proof)
    emit({'allSourceWindowsAndCompositionPassed':True,**proof})
    system=read(bound(plan['baseHealthSystemInputs']))
    system.update(metadata=formation['metadata'],formationSource=ref(OUT/'formation.candidate.json.gz'),independentProof=ref(OUT/'formation-validation.json'))
    projection=module('_sector_long_context_projection','scripts/audit-sector-lifecycle-system.py')
    contextproof=projection.audit_projection(system,read(bound(plan['lifecycleContext'])),original,True)
    write(OUT/'system-context-validation.json',{'passed':True,**contextproof,'originalAdmission':plan['originalAdmissionContext'],'lifecycleContext':plan['lifecycleContext']})
    write(OUT/'system-inputs.json.gz',system)
    groups=[]
    for account in plan['plannedAccounts']:
        period,policy=account['period'],account['policy'];datespec=plan['rules']['periods'][period]
        signals=[s for s in formation['schedules'][policy] if datespec['startDate']<=s['signalDate']<=datespec['endDate']]
        path=OUT/period/policy/'signals.json';write(path,signals)
        groups.append({'period':period,'policy':policy,'enginePolicy':account.get('enginePolicy',policy),'schedule':ref(path),'signals':len(signals),'systemInputs':ref(OUT/'system-inputs.json.gz')})
    del matrix,original,anchors,formation,system;gc.collect()
    if U.workspace_snapshot()!=plan['workspaceJsonBefore']:raise ValueError('Protected workspace changed')
    verify_plan()
    write(OUT/'input-validation.json',{'passed':True,'completedAt':now(),'planSha256':sha(PLAN),'allSixSchedulesFrozenBeforeAccounts':True,
        'dataset':plan['dataset'],'groups':groups,'formation':ref(OUT/'formation.candidate.json.gz'),'formationValidation':ref(OUT/'formation-validation.json'),
        'systemInputs':ref(OUT/'system-inputs.json.gz'),'contextValidation':ref(OUT/'system-context-validation.json'),'lifecycleContext':plan['lifecycleContext'],
        'suspensionMask':ref(OUT/'explicit-suspensions.npy'),'suspensionValidation':ref(OUT/'suspension-source-validation.json'),
        'tests':ref(OUT/'pre-account-tests.json'),'newAccountsRun':0,'userJsonFilesUnchanged':True,'sourcePriceChanges':0})
    emit({'inputsReady':True,'groups':[{k:g[k] for k in ('period','policy','signals')} for g in groups]})


def run_account(period,policy):
    from service.research_sector_long_breakout_system import SectorLongAccount
    from service.research_account_fees import LegacyFeePolicy
    from service.research_account_outcomes import summarize_account
    plan,inputs=verify_plan(),read(OUT/'input-validation.json')
    if not inputs['passed'] or inputs['planSha256']!=sha(PLAN) or not inputs['allSixSchedulesFrozenBeforeAccounts']:raise ValueError('All six fixed schedules required')
    group=next(g for g in inputs['groups'] if (g['period'],g['policy'])==(period,policy));folder=OUT/period/policy
    write(folder/'run-start.json',{'startedAt':now(),'plan':ref(PLAN),'inputs':ref(OUT/'input-validation.json')})
    data,system=read(bound(inputs['dataset'])),read(bound(group['systemInputs']))
    config=deepcopy(plan['baseConfig']);dates=plan['rules']['periods'][period]
    config.update(startDate=dates['startDate'],endDate=dates['endDate'],initialCapital=dates['initialCapital'])
    schedule=read(bound(group['schedule']));emit({'accountStart':[period,policy],'signals':len(schedule)})
    engine=SectorLongAccount(data,schedule,config,system_inputs=system,policy=group['enginePolicy'],policy_label=policy,fee_policy=LegacyFeePolicy())
    engine.step(20000);result,checkpoint=engine.result(),engine.checkpoint();summary=summarize_account(result,checkpoint,policy_id=policy,policy_label=policy)
    if result['status']!='completed' or summary['quality']['integrityIssues']:raise ValueError('Account or cash-fee bridge failed')
    for suffix,value in (('result.json.gz',result),('checkpoint.json.gz',checkpoint),('summary.json',summary)):write(folder/suffix,value)
    verify_plan()
    write(folder/'completion.json',{'completedAt':now(),'plan':ref(PLAN),'inputs':ref(OUT/'input-validation.json'),'systemInputs':group['systemInputs'],
        'period':period,'policy':policy,'enginePolicy':group['enginePolicy'],'result':ref(folder/'result.json.gz'),'checkpoint':ref(folder/'checkpoint.json.gz'),
        'summary':ref(folder/'summary.json'),'schedule':group['schedule'],'independentAccountAuditPending':True,'targetAchieved':False})
    c=summary['closedEpisodes'];emit({'accountCompletedUnaudited':[period,policy],'returnPct':summary['account']['netReturnPct'],'DD':summary['account']['maxDrawdownPct'],
        'R':c['payoffRatio'],'PF':c['profitFactor'],'closed':c['count'],'meanLossPct':c['meanLossReturnPct']})


if __name__=='__main__':
    p=ArgumentParser();p.add_argument('command',choices=('register','prepare','run'));p.add_argument('--attempt',default='001');p.add_argument('--period',choices=('primary','weakMarketStress'));p.add_argument('--policy',choices=POLICIES);a=p.parse_args()
    if not re.fullmatch(r'[0-9]{3}',a.attempt):raise ValueError('Explicit three-digit attempt required')
    OUT=STUDY/('attempt-'+a.attempt);PLAN=OUT/'plan.json'
    register() if a.command=='register' else prepare() if a.command=='prepare' else run_account(a.period,a.policy)
