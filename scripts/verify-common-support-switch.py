"""Independent common-support admission and full-account audit.

Consumes the immutable two-population decisions without fitting any model or
rerunning any label path. The switch rule is reproduced here, not imported from
the execution runner. Frozen prior independent ledger/risk mathematics is reused.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gc
import importlib.util
import json
from pathlib import Path
import sys
import traceback

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'artifacts/strategy-search/common-support-switch-v1'
PARENT_AUDITOR='scripts/verify-all-opportunity-feedback.py'
PARENT_AUDITOR_SHA='d5f1d1f611e38c0460a6ce1c03a8229b44f34fd310a6c59d9fb974335bc26857'
PARENT_DELIVERY_SHA='fd8abe5db443e566189148bde07d3ad02d9af333751b765133fe3c8873003324'
PERIODS=('2022-2023','2024-2025')
BOARDS=('main','chinext','star')
EXITS=('trail10','volatilityTrail')
MODELS=('intercept','ridge')
POPULATIONS=('originalEntered','allExecutable')
CANDIDATES=tuple('commonSupport__'+m for m in MODELS)
INHERITED_CANDIDATES=('baseline',*(p+'__'+m for p in POPULATIONS for m in MODELS))
SUPPORT_STATES=('bothFitted','originalOnlyFitted','allOnlyFitted','neitherFitted')


def load_parent():
    spec=importlib.util.spec_from_file_location('_common_switch_independent_parent',ROOT/PARENT_AUDITOR)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.require(module.sha(ROOT/PARENT_AUDITOR)==PARENT_AUDITOR_SHA,'Frozen parent independent auditor changed')
    return module


P=load_parent()
require,finite,same,sha,digest,read,records,ref,bound=(getattr(P,k) for k in ('require','finite','same','sha','digest','read','records','ref','bound'))
DEPENDENCIES={PARENT_AUDITOR:PARENT_AUDITOR_SHA,**P.DEPENDENCIES}


def switch_decision_reference(original,all_executable):
    """Truth table first; never let all-only fitted override original fallback."""
    for item in (original,all_executable):
        require(set(item)=={'status','reason','prediction','allow'} and type(item['allow']) is bool,'Exact frozen decision shape')
        require(item['status'] in ('fitted','fallback'),'Switch requires known frozen support status')
        if item['status']=='fitted':
            require(finite(item['prediction']) and item['allow'] is (item['prediction']>0)
                    and item['reason']==('positivePrediction' if item['prediction']>0 else 'nonPositivePrediction'),'Fitted decision consistency')
        else:
            require(item['prediction'] is None and item['allow'] is True
                    and item['reason'] in ('insufficientTrainingFormationDays','currentFeaturesUnknown'),'Fallback retains original signals')
    left,right=(original['status']=='fitted',all_executable['status']=='fitted')
    state={(True,True):'bothFitted',(True,False):'originalOnlyFitted',(False,True):'allOnlyFitted',(False,False):'neitherFitted'}[left,right]
    selected='allExecutable' if left and right else 'originalEntered'
    chosen=all_executable if selected=='allExecutable' else original
    return dict(supportState=state,selectedPopulation=selected,allow=chosen['allow'])


def switch_schedule_reference(schedule,original_daily,all_daily,model,calendar,target_dates):
    # The frozen independent parent validates the full original five-field signal,
    # T-1/126-slot clock, strict threshold, and common fallback from support/X.
    P.schedule_reference(schedule,original_daily,model,calendar,target_dates)
    P.schedule_reference(schedule,all_daily,model,calendar,target_dates)
    require([d['formationDate'] for d in original_daily]==[d['formationDate'] for d in all_daily],'Population decision calendars differ')
    decisions=[]
    for left,right in zip(original_daily,all_daily,strict=True):
        require(left['formationDate']==right['formationDate'] and left['asOfDate']==right['asOfDate'],'Switch information-clock alignment')
        state=switch_decision_reference(left['models'][model],right['models'][model])
        decisions.append(dict(formationDate=left['formationDate'],asOfDate=left['asOfDate'],**state))
    by_day={d['formationDate']:d for d in decisions}
    selected=[deepcopy(s) for s in schedule if by_day[s['signalDate']]['allow']]
    return selected,decisions


def count_reference(schedule,left,right,model,decisions):
    a={d['formationDate']:d['models'][model] for d in left}
    z={d['formationDate']:d['models'][model] for d in right}
    selected={d['formationDate']:d for d in decisions}
    dates=[d['formationDate'] for d in decisions];signal_dates=[s['signalDate'] for s in schedule]
    for day in dates:
        chosen=selected[day]
        require(chosen['allow']==a[day]['allow'] or chosen['supportState']=='bothFitted','Original admission can differ only in common support')
        require(chosen['allow']==z[day]['allow'] or chosen['supportState']!='bothFitted','All admission can differ only outside common support')
    return dict(originalSignals=len(schedule),scheduledSignals=sum(selected[d]['allow'] for d in signal_dates),
        rejectedSignals=sum(not selected[d]['allow'] for d in signal_dates),decisionDays=len(dates),
        supportStateDays={s:sum(selected[d]['supportState']==s for d in dates) for s in SUPPORT_STATES},
        supportStateSignals={s:sum(selected[d]['supportState']==s for d in signal_dates) for s in SUPPORT_STATES},
        admittedSignalsBySupportState={s:sum(selected[d]['supportState']==s and selected[d]['allow'] for d in signal_dates) for s in SUPPORT_STATES},
        rejectedSignalsBySupportState={s:sum(selected[d]['supportState']==s and not selected[d]['allow'] for d in signal_dates) for s in SUPPORT_STATES},
        selectedPopulationDays={p:sum(selected[d]['selectedPopulation']==p for d in dates) for p in POPULATIONS},
        selectedPopulationSignals={p:sum(selected[d]['selectedPopulation']==p for d in signal_dates) for p in POPULATIONS},
        originalAllowedDays=sum(a[d]['allow'] for d in dates),allAllowedDays=sum(z[d]['allow'] for d in dates),
        selectedAllowedDays=sum(selected[d]['allow'] for d in dates),originalAllowedSignals=sum(a[d]['allow'] for d in signal_dates),
        allAllowedSignals=sum(z[d]['allow'] for d in signal_dates),
        changedAllowDays=sum(selected[d]['allow']!=a[d]['allow'] for d in dates),
        changedAllowSignals=sum(selected[d]['allow']!=a[d]['allow'] for d in signal_dates),
        addedSignals=sum(selected[d]['allow'] and not a[d]['allow'] for d in signal_dates),
        removedSignals=sum(not selected[d]['allow'] and a[d]['allow'] for d in signal_dates))


def registered_plan(directory):
    plan=read(directory/'plan.json')
    require(plan['schema']=='common-support-switch-v1' and plan['boards']==list(BOARDS) and plan['exits']==list(EXITS)
            and plan['models']==list(MODELS) and plan['candidates']==list(CANDIDATES),'Fixed switch study identity')
    expected=dict(experimentBatches=1,newEntryPolicies=2,newAccounts=24,referenceAccountsReused=60,comparisonAccounts=84,
                  newExitDefinitions=0,newUniverseVariants=0)
    require(all(plan[k]==v for k,v in expected.items()) and plan['parameterSearch'] is False and plan['holdoutClaim'] is False,'Fixed single experiment scope')
    for field in ('sourcePins','inputPins'):
        for name,value in plan[field].items():bound(dict(path=name,sha256=value))
    require(plan['sourcePins'].get(Path(__file__).relative_to(ROOT).as_posix())==sha(__file__),'Registered independent auditor identity')
    same(read(bound(plan['protocol'])),plan['rules'],'Frozen protocol bytes')
    same(plan['rules']['trialAccounting'],expected,'Trial accounting protocol')
    require(plan['rules']['supportStates']==list(SUPPORT_STATES) and plan['rules']['candidateIds']==list(CANDIDATES),'Four support states and two fixed candidates')
    parent_path=bound(plan['parentPlan']);parent=P.registered_plan(parent_path.parent)
    comparison=read(bound(plan['parentComparison']));proof=read(bound(plan['parentValidation']))
    delivery=read(bound(plan['parentDelivery']))
    require(plan['parentDelivery']['sha256']==PARENT_DELIVERY_SHA,'Completed predecessor delivery identity')
    require(proof['passed'] is True and proof['planSha256']==plan['parentPlan']['sha256']
            and proof['comparisonSha256']==plan['parentComparison']['sha256'] and proof['verifierSha256']==PARENT_AUDITOR_SHA
            and proof['inputCompletionSha256']==plan['parentInputCompletion']['sha256']
            and proof['inputValidationSha256']==plan['parentInputValidation']['sha256']
            and (proof['counts']['accounts'],proof['counts']['newAccounts'],proof['counts']['reusedAccounts'])==(60,48,12),'Exact predecessor independent account proof')
    bound(plan['parentInputCompletion']);bound(plan['parentInputValidation'])
    require(delivery['candidateTrialCount']==40 and delivery['newAccounts']==48 and delivery['referenceAccountsReused']==12,'Predecessor final scope')
    files={r['path']:r['sha256'] for r in delivery['files']}
    for name in ('parentPlan','parentComparison','parentValidation','parentReadout','parentReadoutValidation'):
        require(files.get(plan[name]['path'])==plan[name]['sha256'],'Parent finalized file '+name);bound(plan[name])
    parent_readout=read(bound(plan['parentReadout']));parent_readout_proof=read(bound(plan['parentReadoutValidation']))
    require(parent_readout_proof['passed'] is True and parent_readout_proof['planSha256']==plan['parentPlan']['sha256']
            and parent_readout_proof['comparisonSha256']==plan['parentComparison']['sha256']
            and parent_readout_proof['validationSha256']==plan['parentValidation']['sha256']
            and parent_readout_proof['readoutSha256']==plan['parentReadout']['sha256'],'Exact inherited readout proof')
    require(parent_readout['planSha256']==plan['parentPlan']['sha256'],'Parent readout registration')
    for name in ('periods','sourceData','baselineSchedules','accountArtifacts','metadata','decisionArtifacts','libraryPaths','opportunities','atlas',
                 'libraryPlan','libraryDelivery','libraryReadout','libraryReadoutValidation','libraryValidation','libraryCompletion'):
        same(plan[name],parent[name],'Unchanged original source/decision '+name)
    same([r['artifactRefs'] for r in proof['accounts']],comparison['rows'],'All sixty predecessor accounts independently covered')
    same(plan['referenceRows'],comparison['rows'],'Sixty inherited plan descriptors')
    require([(r['period'],r['board'],r['exit'],r['candidate']) for r in comparison['rows']]
            ==[(p,b,e,c) for p in PERIODS for b in BOARDS for e in EXITS for c in INHERITED_CANDIDATES],'Original sixty-account order')
    for row in comparison['rows']:
        for kind in ('result','checkpoint','summary','schedule'):
            # The finalized comparison/proof bind inherited upstream bytes too;
            # a delivery archive need not duplicate the original baseline files.
            bound(row[kind])
    return plan


def frozen_groups(directory,plan):
    completion=read(directory/'input-completion.json')
    require(completion['planSha256']==sha(directory/'plan.json')
            and completion['preAccountTestsSha256']==sha(directory/'pre-account-tests.json')
            and completion['allSchedulesFrozenBeforeAccounts'] is True
            and completion['newAccountsExecuted']==completion['pathsExecuted']==completion['modelsRefitted']==0,'All switch schedules frozen before account execution')
    groups=completion['groups'];parent=read(bound(plan['parentComparison']))
    require([(g['period'],g['board'],g['exit']) for g in groups]==[(p,b,e) for p in PERIODS for b in BOARDS for e in EXITS],'Exact twelve source/exit groups')
    for g in groups:
        p,b,e=(g[k] for k in ('period','board','exit'))
        require(g['inheritedRows']==[r for r in parent['rows'] if (r['period'],r['board'],r['exit'])==(p,b,e)],'Five inherited account descriptors unchanged')
        same(g['baselineSchedule'],plan['baselineSchedules'][p][b],'Original six schedules')
        same(g['accountData'],plan['sourceData'][p][b]['accountData'],'Frozen source cache')
        same(g['decisions'],plan['decisionArtifacts'][p][b][e],'Both immutable prediction libraries')
        require(list(g['candidates'])==list(CANDIDATES),'Two candidate order')
        for candidate,item in g['candidates'].items():
            require(item['population']=='commonSupport' and item['model']==candidate.split('__')[1],'Candidate switch dimensions')
            require(bound(item['schedule'])==directory/p/b/e/(candidate+'.signals.json'),'Switch schedule artifact location')
    return completion,groups


def pretests_reference(directory,plan,completion):
    tests=read(directory/'pre-account-tests.json')
    require(tests['passed'] is True and tests['planSha256']==sha(directory/'plan.json')
            and tests['sourcePins']==plan['sourcePins'] and tests['allChecksBeforeFirstNewAccount'] is True
            and len(tests['commands'])>=4 and all(c['exitCode']==0 for c in tests['commands']),'Exact successful pre-account tests')
    stamp=datetime.fromisoformat
    require(stamp(plan['registeredAt'])<=stamp(tests['startedAt'])<=stamp(tests['completedAt'])<=stamp(completion['completedAt']),'Register/test/prepare clock')
    return tests


def verify_inputs(directory):
    plan=registered_plan(directory);completion,groups=frozen_groups(directory,plan)
    pretests_reference(directory,plan,completion)
    original=records(bound(plan['opportunities']))
    require(len(original)==len({(r['period'],r['board'],r['formationId']) for r in original})==8155,'All original opportunities unique')
    counts=dict(sourceGroups=0,originalSchedules=0,preparedSchedules=0,sourceImmediateOpportunities=0,
                sourceExitOpportunities=0,inputDecisionDays=0,newAccounts=0,modelsRefitted=0,pathsExecuted=0,referenceAccounts=60)
    seen=set();audits=[]
    for group in groups:
        p,b,e=(group[k] for k in ('period','board','exit'))
        schedule=read(bound(group['baselineSchedule']))
        require(schedule==[r['signals']['immediate'] for r in original if (r['period'],r['board'])==(p,b)],'All original signal identity/order/ranks')
        if (p,b) not in seen:
            seen.add((p,b));counts['originalSchedules']+=1;counts['sourceImmediateOpportunities']+=len(schedule)
        counts['sourceGroups']+=1;counts['sourceExitOpportunities']+=len(schedule)
        metadata=read(bound(plan['metadata'][p][b]))
        daily={population:records(bound(group['decisions'][population])) for population in POPULATIONS}
        for population,rows in daily.items():
            counts['inputDecisionDays']+=len(rows)
            require(all((r['period'],r['board'],r['exit'],r['sample'])==(p,b,e,population) for r in rows),'Frozen prediction population identity')
        observed={}
        for model in MODELS:
            candidate='commonSupport__'+model;item=group['candidates'][candidate]
            wanted,decisions=switch_schedule_reference(schedule,daily['originalEntered'],daily['allExecutable'],model,
                                                       metadata['calendar'],metadata['targetDates'])
            require(read(bound(item['schedule']))==wanted,'Exact independent common-support mask')
            expected=count_reference(schedule,daily['originalEntered'],daily['allExecutable'],model,decisions)
            same(item['counts'],expected,'Full switch state and admission counts')
            observed[candidate]=expected;counts['preparedSchedules']+=1
        audits.append(dict(period=p,board=b,exit=e,originalSignals=len(schedule),candidateCounts=observed))
        print(json.dumps({'checkedInputs':[p,b,e],'selected':{k:v['scheduledSignals'] for k,v in observed.items()}}),flush=True)
    expected=dict(preparedGroups=counts['sourceGroups'],originalSchedules=counts['originalSchedules'],preparedSchedules=counts['preparedSchedules'],
                  sourceImmediateOpportunities=counts['sourceImmediateOpportunities'],sourceExitOpportunities=counts['sourceExitOpportunities'],
                  referenceAccounts=sum(len(g['inheritedRows']) for g in groups))
    same(completion['counts'],expected,'Actual complete input denominator')
    require(expected==dict(preparedGroups=12,originalSchedules=6,preparedSchedules=24,sourceImmediateOpportunities=8155,sourceExitOpportunities=16310,referenceAccounts=60)
            and counts['inputDecisionDays']==11628,'Complete source/decision/schedule population')
    registered_plan(directory)
    return P.write_proof(directory,'input-validation.json',dict(passed=True,verifiedAt=datetime.now(timezone.utc).isoformat(),
        planSha256=sha(directory/'plan.json'),inputCompletionSha256=sha(directory/'input-completion.json'),
        preAccountTestsSha256=sha(directory/'pre-account-tests.json'),verifierSha256=sha(__file__),
        parentValidationSha256=plan['parentValidation']['sha256'],parentReadoutValidationSha256=plan['parentReadoutValidation']['sha256'],
        sourcePins=DEPENDENCIES,inputPins=plan['inputPins'],preparedGroups=groups,counts=counts,groups=audits,
        checks=['Both immutable decision libraries inherit their complete independent label/model proof, with no refit or path replay',
            'All 24 switch masks independently computed across all four support states and original signal order',
            'Original admission differs only in bothFitted; all admission differs only outside bothFitted',
            'All 60 predecessor account descriptors preserve original result/checkpoint/summary/schedule references and old metadata',
            'No candidate account executes in this audit; all 8155 original formations remain in source denominators'],
        holdoutClaim=False,certified=False,targetAchieved=False))


def verify_accounts(directory):
    plan=registered_plan(directory);completion,groups=frozen_groups(directory,plan);pretests_reference(directory,plan,completion)
    inputs=read(directory/'input-validation.json');start=read(directory/'run-start.json');comparison=read(directory/'comparison.json')
    require(inputs['passed'] is True and inputs['planSha256']==sha(directory/'plan.json')
            and inputs['inputCompletionSha256']==sha(directory/'input-completion.json') and inputs['verifierSha256']==sha(__file__)
            and inputs['preAccountTestsSha256']==sha(directory/'pre-account-tests.json'),'Exact input audit')
    same(inputs['preparedGroups'],groups,'Independently audited frozen masks')
    same(start['inputValidation'],ref(directory/'input-validation.json'),'Run used exact input audit')
    same(start['inputCompletion'],ref(directory/'input-completion.json'),'Run used full input freeze')
    same(start['tests'],ref(directory/'pre-account-tests.json'),'Run used actual tests')
    require(start['planSha256']==sha(directory/'plan.json') and datetime.fromisoformat(completion['completedAt'])
            <=datetime.fromisoformat(inputs['verifiedAt'])<=datetime.fromisoformat(start['startedAt']),'Input proof precedes account run')
    require(comparison['schema']=='common-support-switch-v1-comparison' and comparison['holdoutClaim'] is False,'Comparison identity')
    for k,filename in [('planSha256','plan.json'),('inputCompletionSha256','input-completion.json'),('inputValidationSha256','input-validation.json'),
                       ('preAccountTestsSha256','pre-account-tests.json'),('runStartSha256','run-start.json')]:
        require(comparison[k]==sha(directory/filename),'Comparison exact binding '+k)
    for k,value in plan['rules']['trialAccounting'].items():require(comparison[k]==value,'Comparison experiment count '+k)
    rows=comparison['rows'];order=(*INHERITED_CANDIDATES,*CANDIDATES)
    require([(r['period'],r['board'],r['exit'],r['candidate']) for r in rows]==[(p,b,e,c) for p in PERIODS for b in BOARDS for e in EXITS for c in order],
            'All 84 accounts in exact declared order')
    require(len(comparison['groupCompletions'])==12,'All groups terminal')
    for group,item in zip(groups,comparison['groupCompletions'],strict=True):
        p,b,e=(group[k] for k in ('period','board','exit'));terminal=read(bound(item))
        require(bound(item)==directory/p/b/e/'group-completion.json' and terminal['completed'] is True
                and terminal['newAccounts']==2 and terminal['referenceAccountsReused']==5,'Group completion scope')
        for k in ('planSha256','inputCompletionSha256','inputValidationSha256','preAccountTestsSha256','runStartSha256'):
            require(terminal[k]==comparison[k],'Group exact dependency '+k)
        require(terminal['rows']==[r for r in rows if (r['period'],r['board'],r['exit'])==(p,b,e)],'Terminal group descriptors')
        group_start=read(directory/p/b/e/'group-run-start.json')
        require(group_start['planSha256']==sha(directory/'plan.json') and group_start['runStartSha256']==sha(directory/'run-start.json')
                and datetime.fromisoformat(start['startedAt'])<=datetime.fromisoformat(group_start['startedAt'])
                <=datetime.fromisoformat(terminal['completedAt']),'Group start/finish chronology')
    parent_comparison=read(bound(plan['parentComparison']))
    require([r for r in rows if r['candidate'] in INHERITED_CANDIDATES]==parent_comparison['rows'],'All sixty inherited descriptors byte-value exact')
    same(comparison['referenceRows'],parent_comparison['rows'],'Explicit comparison reference rows')
    same(comparison['newRows'],[r for r in rows if r['candidate'] in CANDIDATES],'Explicit new-account rows')
    board=P.independent('scripts/verify-board-coverage-reclaim.py')
    accounting=P.independent('scripts/verify-peer-amount-persistence.py');cost=accounting.cost_reference()
    trail=P.independent('scripts/verify-reclaim-exit-transfer.py');volatility=P.independent('scripts/verify-reclaim-volatility-trail.py')
    prior=P.independent('scripts/verify-online-feedback-entry.py');atlas=read(bound(plan['atlas']))
    reports=[];data=None;data_key=None
    counts=dict(accounts=0,newAccounts=0,reusedAccounts=0,sourceImmediateOpportunities=8155,sourceExitOpportunities=16310,
                preparedSchedules=24,newFills=0,heldCloseChecks=0,newSummaryIntervals=0)
    for row in rows:
        p,b,e,c=(row[k] for k in ('period','board','exit','candidate'))
        group=next(g for g in groups if (g['period'],g['board'],g['exit'])==(p,b,e));counts['accounts']+=1
        if c in INHERITED_CANDIDATES:
            require(row in group['inheritedRows'],'Inherited group identity')
            for kind in ('summary','result','checkpoint','schedule'):bound(row[kind])
            reports.append(dict(period=p,board=b,exit=e,candidate=c,artifactRefs=row,executionOrigin='reused',
                checks=dict(inheritedUnderExactParentProof=True,parentValidation=plan['parentValidation'])))
            counts['reusedAccounts']+=1
            continue
        model=c.split('__')[1];folder=directory/p/b/e
        require(row['population']=='commonSupport' and row['model']==model and row['reusedAccount'] is False,'New account identity')
        same(row['schedule'],group['candidates'][c]['schedule'],'New account exact independently audited schedule')
        for kind,suffix in [('result','.result.json.gz'),('checkpoint','.checkpoint.json.gz'),('summary','.summary.json')]:
            require(bound(row[kind])==folder/(c+suffix),'New output path identity')
        result,checkpoint,summary,schedule=[read(bound(row[k])) for k in ('result','checkpoint','summary','schedule')]
        require(summary['policyId']==summary['policyLabel']==c,'New summary identity')
        _,market,account=cost.reference_modules(p)
        full_summary=account.verify_summary(result,summary,prior.interval_reference(p,atlas));P.typed_fee_check(result,p)
        if data_key!=(p,b):
            if data is not None:del data;gc.collect()
            data=read(bound(group['accountData']));data_key=(p,b)
            require(digest(data)==plan['sourceData'][p][b]['accountDatasetDigest'],'Frozen source dataset digest')
        compatible=deepcopy(summary);compatible['periods']=summary['periods'][:7]
        fn=trail.audit_account if e=='trail10' else volatility.audit_account
        audit=fn(result,checkpoint,compatible,schedule,plan['periods'][p]['policyConfigs'][e],data,p,e,board,accounting)
        audit['fullSummaryIntervals']=full_summary
        daily={pop:{r['formationDate']:r for r in records(bound(group['decisions'][pop]))} for pop in POPULATIONS}
        permitted={s['signalId']:s for s in schedule}
        for fill in result['trades']:
            if fill['side']=='buy':
                day=fill['signalDate']
                require(fill['signalId'] in permitted and permitted[fill['signalId']]['signalDate']==day,'Actual buy outside original admitted identity')
                require(switch_decision_reference(daily['originalEntered'][day]['models'][model],daily['allExecutable'][day]['models'][model])['allow'] is True,'Actual buy not allowed by point-in-time switch')
        require(digest(data)==plan['sourceData'][p][b]['accountDatasetDigest'],'Audit mutated frozen source')
        counts['newAccounts']+=1;counts['newFills']+=len(result['trades']);counts['heldCloseChecks']+=audit['dailyRisk']['heldDays'];counts['newSummaryIntervals']+=len(summary['periods'])
        reports.append(dict(period=p,board=b,exit=e,candidate=c,artifactRefs=row,executionOrigin='new',checks=audit))
        print(json.dumps({'checkedNewAccount':[p,b,e,c],'heldCloses':audit['dailyRisk']['heldDays']}),flush=True)
        del result,checkpoint,summary,schedule;gc.collect()
    require((counts['accounts'],counts['newAccounts'],counts['reusedAccounts'])==(84,24,60),'Actual account audit dimensions')
    registered_plan(directory)
    return P.write_proof(directory,'validation.json',dict(passed=True,verifiedAt=datetime.now(timezone.utc).isoformat(),
        planSha256=sha(directory/'plan.json'),comparisonSha256=sha(directory/'comparison.json'),
        inputCompletionSha256=sha(directory/'input-completion.json'),inputValidationSha256=sha(directory/'input-validation.json'),
        preAccountTestsSha256=sha(directory/'pre-account-tests.json'),verifierSha256=sha(__file__),
        parentValidationSha256=plan['parentValidation']['sha256'],parentReadoutValidationSha256=plan['parentReadoutValidation']['sha256'],
        sourcePins={**DEPENDENCIES,**cost.REFERENCES},inputPins=plan['inputPins'],counts=counts,accounts=reports,accountArtifacts=rows,
        runStart=ref(directory/'run-start.json'),checks=[
            '60 exact inherited result/checkpoint/summary/schedule descriptors bound to prior complete independent proof; historical reuse flags remain unchanged',
            '24 new full cash/share/fee/order/curve/position accounts independently replay-audited without executing accounts',
            'Every held-close fixed or volatility exit independently checked, including no-trigger and existing-exit priority',
            'Every new buy belongs to exact original identity and independently selected common-support decision',
            'Typed historical minimum commission and original transaction settings preserved; all schedule proofs precede first run'],
        holdoutClaim=False,certified=False,targetAchieved=False))


def self_test():
    import unittest
    path=ROOT/'tests/test_common_support_switch_verifier.py'
    spec=importlib.util.spec_from_file_location('_common_switch_verifier_tests',path)
    tests=importlib.util.module_from_spec(spec);spec.loader.exec_module(tests)
    result=unittest.TextTestRunner(stream=sys.stderr,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(tests))
    require(result.wasSuccessful(),'Independent switch synthetic checks failed')
    return dict(passed=True,syntheticGroups=result.testsRun,tests=ref(path),realSourceReads=0,modelFits=0,accountExecutions=0)


if __name__=='__main__':
    parser=ArgumentParser();parser.add_argument('mode',choices=('inputs','accounts'),nargs='?',default='accounts')
    parser.add_argument('--inputs','--inputs-only',dest='inputs',action='store_true');parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--out',type=Path,default=DEFAULT);args=parser.parse_args()
    if args.self_test:print(json.dumps(self_test(),ensure_ascii=False,indent=2))
    else:
        directory=args.out.resolve();mode='inputs' if args.inputs or args.mode=='inputs' else 'accounts'
        try:(verify_inputs if mode=='inputs' else verify_accounts)(directory)
        except Exception as error:
            if directory.exists():
                folder=directory/'validation-attempts';folder.mkdir(exist_ok=True)
                with (folder/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')).open('x',encoding='utf-8') as stream:
                    json.dump(dict(passed=False,at=datetime.now(timezone.utc).isoformat(),mode=mode,verifierSha256=sha(__file__),
                        type=type(error).__name__,message=str(error),traceback=traceback.format_exc()),stream,ensure_ascii=False,indent=2)
            raise
