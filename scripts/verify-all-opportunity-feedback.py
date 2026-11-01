"""Independent frozen-decision mapping and executable-account audit.

This auditor does not import the new runner/model and never regenerates the
independent opportunity library.  Its parent receipts bind the already audited
compact decisions; this experiment changes only which frozen allow is used.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/all-opportunity-feedback-v1'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
EXITS = ('trail10', 'volatilityTrail')
POPULATIONS = ('originalEntered', 'allExecutable')
MODELS = ('intercept', 'ridge')
CANDIDATES = tuple(p + '__' + m for p in POPULATIONS for m in MODELS)
SID = 'strongUndercutReclaimV1'
DEPENDENCIES = {
    'scripts/verify-online-feedback-entry.py': 'ec17b1a7114bd2ed6a9ba5757f26fccdbfeb3baa1cedcadf5993ec18a0a963d5',
    'scripts/verify-reclaim-exit-mechanism.py': '96ef23022f1a8a515ecf94eabdab8696057bcb166a2cd309fe43f101a507cd89',
    'scripts/verify-reclaim-volatility-trail.py': '81bd4a8681f31d9a1dc5c5732ed8cb7d763e3c779b58492098bbc3957fd0bb08',
    'scripts/verify-reclaim-exit-transfer.py': '5437d5e9421f65f935d211c553e95c473cf40fddafaf09b12694b3bf3548913a',
    'scripts/verify-board-coverage-reclaim.py': '8d95ec533c901c7e47b6c23beb6567ae6760900d7be9e6038fff0d58853b6c29',
    'scripts/verify-peer-amount-persistence.py': 'cfaabb0262a33c7c6a5776f4fb54b7d0298df104abf8374ac87313ec6cccd026',
    'scripts/verify-relative-repair-cost-stress.py': 'df00a1573933b4624563b4db0f451a731449c55c180b8b6793f5967b147e762a',
}
FEE_BINDING = {'path': 'docs/account-exit-comparison-v1.json',
               'sha256': '1406190246a20ccb823b129f471bcd5c16ede59b78c56fe62947ff4c4f4cb21e'}


def require(value, message):
    if not value:
        raise AssertionError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def same(actual, expected, label='value'):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and set(actual) == set(expected), label + ': keys')
        for key in expected:
            same(actual[key], expected[key], label + '.' + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + ': length')
        for i, value in enumerate(expected):
            same(actual[i], value, f'{label}[{i}]')
    elif finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-11), label + ': number')
    else:
        require(type(actual) is type(expected) and actual == expected, label + ': value')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz')
          else Path(path).open(encoding='utf-8')) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def ref(path):
    return {'path': Path(path).resolve().relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def bound(item):
    path = (ROOT / item['path']).resolve()
    require(path.is_relative_to(ROOT) and sha(path) == item['sha256'], 'Changed bound file: ' + item['path'])
    return path


def independent(name):
    require(name in DEPENDENCIES and sha(ROOT / name) == DEPENDENCIES[name], 'Frozen independent helper changed: ' + name)
    spec = importlib.util.spec_from_file_location('_all_feedback_' + Path(name).stem.replace('-', '_'), ROOT / name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def candidate_dimensions(candidate):
    require(candidate in CANDIDATES, 'Unknown candidate identity')
    return tuple(candidate.split('__'))


def schedule_reference(schedule, daily, model, calendar, target_dates=None):
    """Check the saved decision clock and copy allowed original identities only."""
    require(model in MODELS, 'Unknown model')
    require(calendar == sorted(set(calendar)), 'Canonical source calendar')
    index = {d: i for i, d in enumerate(calendar)}
    dates = [d['formationDate'] for d in daily]
    require(dates == sorted(set(dates)), 'Unique canonical daily decisions')
    if target_dates is not None:
        require(dates == target_dates, 'Complete target-date decision evidence')
    by_day = {}
    for row in daily:
        day = row['formationDate']; i = index.get(day)
        require(i is not None and row['asOfDate'] == (calendar[i-1] if i else None), 'Exact T-1 decision clock')
        slots = calendar[max(0, i-126):i]
        expected_window = dict(requestedSlots=126, availableSlots=len(slots),
            startDate=slots[0] if slots else None, endDate=slots[-1] if slots else None,
            historyTruncated=len(slots) < 126)
        same(row['window'], expected_window, 'Inherited closed-label availability window')
        require(set(row['models']) == set(MODELS), 'Both frozen models')
        support = row['support']['eligibleFormationDayCount']
        require(type(support) is int and support >= 0, 'Support count')
        require(row['currentFeatureStatus'] in ('complete','unknown'), 'Current feature status')
        current_known = row['currentFeatureStatus'] == 'complete'
        for name, decision in row['models'].items():
            require(set(decision) == {'status', 'reason', 'prediction', 'allow'} and type(decision['allow']) is bool, 'Decision shape/Boolean allow')
            if support < 20 or not current_known:
                reason = 'insufficientTrainingFormationDays' if support < 20 else 'currentFeaturesUnknown'
                require(decision == dict(status='fallback', reason=reason, prediction=None, allow=True), 'Shared unconditional fallback')
            else:
                prediction = decision['prediction']
                require(decision['status'] == 'fitted' and finite(prediction), 'Supported finite fitted prediction')
                allow = prediction > 0
                require(decision['allow'] is allow and decision['reason'] == ('positivePrediction' if allow else 'nonPositivePrediction'), 'Strict zero economic threshold')
        by_day[day] = row
    seen_ids, seen_day_symbols, seen_day_ranks = set(), set(), set()
    previous = None; output = []; included = excluded = fallback = 0
    for signal in schedule:
        require(set(signal) == {'signalId','strategyId','symbol','signalDate','originalRank'}, 'Original five signal fields')
        day, symbol, rank = signal['signalDate'], signal['symbol'], signal['originalRank']
        require(signal['strategyId'] == SID and signal['signalId'] == f'{SID}:{day}:{symbol}', 'Original UR identity')
        require(type(rank) is int and 1 <= rank <= 5 and day in by_day, 'Original rank/date')
        require(signal['signalId'] not in seen_ids and (day,symbol) not in seen_day_symbols and (day,rank) not in seen_day_ranks, 'Duplicate original opportunity')
        order = (day,rank,symbol)
        require(previous is None or order > previous, 'Original canonical order cannot be silently reranked')
        previous = order; seen_ids.add(signal['signalId']); seen_day_symbols.add((day,symbol)); seen_day_ranks.add((day,rank))
        decision = by_day[day]['models'][model]
        if decision['allow']:
            output.append(deepcopy(signal)); included += 1
        else:
            excluded += 1
        fallback += decision['status'] == 'fallback'
    return output, dict(originalSignals=len(schedule), included=included, excluded=excluded, fallbackSignals=fallback)


def typed_fee_check(result, period):
    """Fee settings are provenance as well as arithmetic: int 5 is preserved."""
    if period != '2022-2023':
        return
    policy = read(bound(FEE_BINDING))['feePolicy']
    require(type(policy['minCommission']) is int and policy['minCommission'] == 5, 'Frozen historical minimum commission type')
    # Full fee identity and ledger are checked by the frozen ledger auditor;
    # additionally retain the exact old settings digest, rather than float-normalizing it.
    identity = result['research']['feeLedger']['identity']
    require(identity['settingsSha256'] == '21a860ff8b5510a920e4ba149ea6a30ee1dcb8327b1c8a20fb5f9e2b0ff607e9', 'Historical typed fee settings changed')


def write_proof(directory, name, proof):
    with (directory / name).open('x', encoding='utf-8') as stream:
        json.dump(proof, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write('\n')
    print(json.dumps({'passed': True, 'output': ref(directory/name), 'counts': proof['counts']}, ensure_ascii=False), flush=True)
    return proof


def self_test():
    import unittest
    name = 'tests/test_all_opportunity_feedback_verifier.py'
    spec = importlib.util.spec_from_file_location('_all_feedback_verifier_tests', ROOT/name)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    require(result.wasSuccessful(), 'Independent synthetic tests failed')
    return dict(passed=True, syntheticGroups=result.testsRun, realSourceReads=0, accountExecutions=0,
                tests=ref(ROOT/name))


def registered_plan(directory):
    plan = read(directory/'plan.json')
    require(plan['schema'] == 'all-opportunity-feedback-v1' and plan['boards'] == list(BOARDS)
            and plan['exits'] == list(EXITS) and plan['populations'] == list(POPULATIONS)
            and plan['models'] == list(MODELS) and plan['candidates'] == list(CANDIDATES), 'Fixed study dimensions')
    expected_trials = dict(experimentBatches=1,newEntryPolicies=4,newAccounts=48,referenceAccountsReused=12,
                           newExitDefinitions=0,newUniverseVariants=0)
    require(all(plan[k] == v for k,v in expected_trials.items()) and plan['parameterSearch'] is False
            and plan['holdoutClaim'] is False and plan['candidateTrialCountBefore'] == 39, 'Fixed trial scope')
    for field in ('sourcePins','inputPins'):
        for path,value in plan[field].items(): bound({'path':path,'sha256':value})
    require(plan['sourcePins'].get(Path(__file__).relative_to(ROOT).as_posix()) == sha(__file__), 'Registered verifier identity')
    same(read(bound(plan['protocol'])),plan['rules'],'Frozen protocol')
    same(plan['rules']['trialAccounting'],expected_trials,'Registered trial counts')
    require(plan['rules']['candidateIds'] == list(CANDIDATES), 'Protocol candidate identities')
    for path,value in DEPENDENCIES.items(): require(sha(ROOT/path) == value, 'Frozen independent dependency '+path)
    bound(FEE_BINDING)

    parent = read(bound(plan['parentPlan']))
    library = read(bound(plan['libraryPlan']))
    path_proof = read(bound(plan['libraryValidation']))
    report = read(bound(plan['libraryReadout']))
    report_proof = read(bound(plan['libraryReadoutValidation']))
    library_completion = read(bound(plan['libraryCompletion']))
    delivery = read(bound(plan['libraryDelivery']))
    require(plan['parentPlan']['sha256'] == 'c9abd252f3b017948c61332b500150ceaa30862d8291c097f16938678100d53c'
            and plan['libraryPlan']['sha256'] == 'dd3e9b6fcbe3b72c8c986c2100dfa80468feb29ce5cc41b7951db73e644f91f7', 'Fixed two prior registrations')
    require(path_proof['passed'] is True and path_proof['planSha256'] == plan['libraryPlan']['sha256']
            and path_proof['libraryCompletionSha256'] == plan['libraryCompletion']['sha256'], 'Independent path-library proof')
    require(report_proof['passed'] is True and report_proof['planSha256'] == plan['libraryPlan']['sha256']
            and report_proof['pathValidationSha256'] == plan['libraryValidation']['sha256']
            and report_proof['libraryCompletionSha256'] == plan['libraryCompletion']['sha256']
            and report_proof['readoutSha256'] == plan['libraryReadout']['sha256'], 'Independent frozen feedback-library proof')
    require(report['pathValidationSha256'] == plan['libraryValidation']['sha256']
            and report['libraryCompletionSha256'] == plan['libraryCompletion']['sha256']
            and report['planSha256'] == plan['libraryPlan']['sha256'], 'Feedback readout exact parent chain')
    delivered = {r['path']:r['sha256'] for r in delivery['files']}
    for name in ('libraryReadout','libraryReadoutValidation','libraryValidation','libraryCompletion'):
        require(delivered.get(plan[name]['path']) == plan[name]['sha256'], 'Finalized predecessor file '+name)
    require(delivery['newCandidateAccounts'] == 0 and delivery['candidateTrialCount'] == 39, 'Library was not a new candidate account trial')
    for name in ('periods','sourceData','baselineSchedules','accountArtifacts','metadata','opportunities','atlas'):
        same(plan[name],parent[name],'Inherited original execution '+name)
    for name in ('sourceData','baselineSchedules','metadata','periods'):
        same(plan[name],library[name],'Shared label/account source '+name)
    same(library['parentPlan'],plan['parentPlan'],'Common parent identity')
    for name in ('parentInputCompletion','parentInputValidation'):
        same(plan[name],library[name], 'Original input proof reference')
    old_input_proof = read(bound(plan['parentInputValidation']))
    require(old_input_proof['passed'] is True and old_input_proof['planSha256'] == plan['parentPlan']['sha256']
            and old_input_proof['inputCompletionSha256'] == plan['parentInputCompletion']['sha256'], 'Original account input binding')
    # Validate the original baseline account references against the original account
    # proof as well, rather than treating the new library as an account replay.
    original_comparison_path = ROOT/'artifacts/strategy-search/reclaim-volatility-trail-v1/comparison.json'
    original_proof_path = original_comparison_path.with_name('validation.json')
    require(sha(original_proof_path) == 'a4161122f3ea8c2b8efb04e2dfe6a67cd2ee5c6d8a6cafcdb5e20f560df5fabf', 'Original 12-account proof identity')
    original_proof=read(original_proof_path); original_comparison=read(original_comparison_path)
    require(original_proof['passed'] is True and original_proof['comparisonSha256'] == sha(original_comparison_path), 'Original account audit binding')
    same(plan['accountArtifacts'],original_comparison['artifacts'],'Original twelve accounts are unfiltered baselines')
    expected_keys=[(p,b,e) for p in PERIODS for b in BOARDS for e in EXITS]
    decisions={(r['period'],r['board'],r['exit']):r['samples'] for r in report['decisionArtifacts']}
    paths={(r['period'],r['board'],r['exit']):r['paths'] for r in library_completion['groups']}
    require(len(decisions)==len(paths)==12 and set(decisions)==set(paths)==set(expected_keys), 'Complete inherited label/decision matrices')
    for p,b,e in expected_keys:
        same(plan['decisionArtifacts'][p][b][e],decisions[p,b,e],'Exact audited compact libraries')
        same(plan['libraryPaths'][p][b][e],paths[p,b,e],'Exact audited outcome-label paths')
        for population in POPULATIONS:
            item=decisions[p,b,e][population]
            require(report_proof['inputPins'].get(item['path']) == item['sha256'], 'Decision bytes covered by independent arithmetic proof')
            require(delivered.get(item['path']) == item['sha256'], 'Decision bytes in finalized delivery')
    require(report['counts']['originalFormations']==8155 and report['counts']['decisionLibraries']==24
            and path_proof['counts']['paths']==16310, 'Inherited full opportunity denominator')
    return plan


def mask_counts_reference(schedule,daily,model):
    by_day={r['formationDate']:r['models'][model] for r in daily}
    selected=[by_day[s['signalDate']] for s in schedule]
    return dict(originalSignals=len(schedule),scheduledSignals=sum(x['allow'] for x in selected),
        rejectedSignals=sum(not x['allow'] for x in selected),decisionDays=len(daily),
        fittedDays=sum(x['status']=='fitted' for x in by_day.values()),
        fallbackDays=sum(x['status']=='fallback' for x in by_day.values()),
        fittedAdmittedSignals=sum(x['status']=='fitted' and x['allow'] for x in selected),
        fittedRejectedSignals=sum(x['status']=='fitted' and not x['allow'] for x in selected),
        fallbackAdmittedSignals=sum(x['status']=='fallback' and x['allow'] for x in selected))


def frozen_groups(directory,plan):
    completion=read(directory/'input-completion.json')
    require(completion['schema']=='all-opportunity-feedback-v1-inputs'
            and completion['planSha256']==sha(directory/'plan.json')
            and completion['preAccountTestsSha256']==sha(directory/'pre-account-tests.json')
            and completion['allSchedulesFrozenBeforeAccounts'] is True
            and completion['newAccountsExecuted']==completion['pathsExecuted']==completion['modelsRefitted']==0, 'All masks frozen without new paths/models/accounts')
    groups=completion['groups']
    require([(g['period'],g['board'],g['exit']) for g in groups] == [(p,b,e) for p in PERIODS for b in BOARDS for e in EXITS], 'All 12 groups in declared order')
    for g in groups:
        p,b,e=(g[k] for k in ('period','board','exit'))
        for target,source in [('baseline','accountArtifacts'),('decisions','decisionArtifacts'),('libraryPaths','libraryPaths')]:
            same(g[target],plan[source][p][b][e], 'Group '+target)
        same(g['baselineSchedule'],plan['baselineSchedules'][p][b],'Group original schedule')
        same(g['accountData'],plan['sourceData'][p][b]['accountData'],'Group original account cache')
        require(list(g['candidates'])==list(CANDIDATES),'All four candidates in declared order')
        for candidate,item in g['candidates'].items():
            require((item['population'],item['model'])==candidate_dimensions(candidate),'Candidate dimensions')
            require(bound(item['schedule'])==directory/p/b/e/(candidate+'.signals.json'),'Prepared candidate schedule path')
    return completion,groups


def check_pretests(directory,plan,completion):
    tests=read(directory/'pre-account-tests.json')
    require(tests['passed'] is True and tests['planSha256']==sha(directory/'plan.json')
            and tests['sourcePins']==plan['sourcePins'] and tests['allChecksBeforeFirstNewAccount'] is True
            and len(tests['commands'])>=4 and all(c['exitCode']==0 for c in tests['commands']), 'Actual successful pre-account checks')
    stamp=lambda x:datetime.fromisoformat(x)
    require(stamp(plan['registeredAt'])<=stamp(tests['startedAt'])<=stamp(tests['completedAt'])<=stamp(completion['completedAt']), 'Register/test/prepare chronology')
    return tests


def verify_inputs(directory):
    plan=registered_plan(directory);completion,groups=frozen_groups(directory,plan)
    check_pretests(directory,plan,completion)
    original=records(bound(plan['opportunities']))
    require(len(original)==len({(r['period'],r['board'],r['formationId']) for r in original})==8155,'All original formations unique')
    counts=dict(sourceGroups=0,originalSchedules=0,preparedSchedules=0,sourceImmediateOpportunities=0,
                sourceExitOpportunities=0,inputDecisionDays=0,newAccounts=0,modelsRefitted=0,pathsExecuted=0)
    seen_sources=set();audits=[]
    for g in groups:
        p,b,e=(g[k] for k in ('period','board','exit'))
        schedule=read(bound(g['baselineSchedule']))
        expected=[r['signals']['immediate'] for r in original if r['period']==p and r['board']==b]
        require(schedule==expected,'Original complete ordered opportunity schedule')
        if (p,b) not in seen_sources:
            counts['sourceImmediateOpportunities']+=len(schedule);counts['originalSchedules']+=1;seen_sources.add((p,b))
        counts['sourceExitOpportunities']+=len(schedule);counts['sourceGroups']+=1
        metadata=read(bound(plan['metadata'][p][b]));selected={}
        for population in POPULATIONS:
            daily=records(bound(g['decisions'][population]));counts['inputDecisionDays']+=len(daily)
            for d in daily:
                require((d['period'],d['board'],d['exit'],d['sample'])==(p,b,e,population),'Frozen decision group/population identity')
            for model in MODELS:
                candidate=population+'__'+model;item=g['candidates'][candidate]
                wanted,_=schedule_reference(schedule,daily,model,metadata['calendar'],metadata['targetDates'])
                require(read(bound(item['schedule']))==wanted,'Candidate exact original fields, ranks, order and allow mask')
                expected_counts=mask_counts_reference(schedule,daily,model)
                same(item['counts'],expected_counts,'All candidate input counts')
                selected[candidate]=expected_counts;counts['preparedSchedules']+=1
        audits.append(dict(period=p,board=b,exit=e,originalSignals=len(schedule),candidateCounts=selected))
        print(json.dumps({'checkedInputs':[p,b,e],'candidates':{k:v['scheduledSignals'] for k,v in selected.items()}}),flush=True)
    expected=dict(preparedGroups=counts['sourceGroups'],originalSchedules=counts['originalSchedules'],
        preparedSchedules=counts['preparedSchedules'],sourceImmediateOpportunities=counts['sourceImmediateOpportunities'],
        sourceExitOpportunities=counts['sourceExitOpportunities'])
    same(completion['counts'],expected,'Independently counted completion matrix')
    require(expected==dict(preparedGroups=12,originalSchedules=6,preparedSchedules=48,sourceImmediateOpportunities=8155,sourceExitOpportunities=16310)
            and counts['inputDecisionDays']==11628,'Complete inherited populations and daily libraries')
    registered_plan(directory)
    return write_proof(directory,'input-validation.json',dict(passed=True,verifiedAt=datetime.now(timezone.utc).isoformat(),
        planSha256=sha(directory/'plan.json'),inputCompletionSha256=sha(directory/'input-completion.json'),
        preAccountTestsSha256=sha(directory/'pre-account-tests.json'),verifierSha256=sha(__file__),
        libraryValidationSha256=plan['libraryValidation']['sha256'],libraryReadoutValidationSha256=plan['libraryReadoutValidation']['sha256'],
        sourcePins=DEPENDENCIES,inputPins=plan['inputPins'],preparedGroups=groups,counts=counts,groups=audits,
        checks=['All 24 previously audited compact decision libraries and label paths retain their finalized byte identities',
                'All 48 schedules independently mapped from exact original Top5 order and frozen Boolean allows',
                'Exact T-1 cutoff, inclusive 126 slots, common fallback and strict zero admission verified for all daily decisions',
                'All 8155 formations / 16310 exit opportunities retained in the source population; no rank backfill or date shift',
                'No models refitted, paths replayed, or candidate accounts executed by this audit'],
        holdoutClaim=False,certified=False,targetAchieved=False))


def verify_accounts(directory):
    plan=registered_plan(directory);completion,groups=frozen_groups(directory,plan);tests=check_pretests(directory,plan,completion)
    inputs=read(directory/'input-validation.json')
    require(inputs['passed'] is True and inputs['planSha256']==sha(directory/'plan.json')
            and inputs['inputCompletionSha256']==sha(directory/'input-completion.json')
            and inputs['preAccountTestsSha256']==sha(directory/'pre-account-tests.json')
            and inputs['verifierSha256']==sha(__file__),'Exact independent input proof')
    same(inputs['preparedGroups'],groups,'Audited 48 masks remain unchanged')
    start=read(directory/'run-start.json')
    same(start['inputValidation'],ref(directory/'input-validation.json'),'Run used input proof')
    same(start['inputCompletion'],ref(directory/'input-completion.json'),'Run used complete schedule freeze')
    same(start['tests'],ref(directory/'pre-account-tests.json'),'Run used actual pretests')
    require(start['planSha256']==sha(directory/'plan.json')
            and datetime.fromisoformat(completion['completedAt'])<=datetime.fromisoformat(inputs['verifiedAt'])
            <=datetime.fromisoformat(start['startedAt']),'Audit and schedule freeze precede all accounts')
    comparison=read(directory/'comparison.json')
    require(comparison['schema']=='all-opportunity-feedback-v1-comparison' and comparison['holdoutClaim'] is False,'Comparison identity')
    for k,filename in [('planSha256','plan.json'),('inputCompletionSha256','input-completion.json'),
            ('inputValidationSha256','input-validation.json'),('preAccountTestsSha256','pre-account-tests.json'),('runStartSha256','run-start.json')]:
        require(comparison[k]==sha(directory/filename),'Comparison binding '+k)
    for k,value in plan['rules']['trialAccounting'].items(): require(comparison[k]==value,'Comparison scope '+k)
    rows=comparison['rows']
    require([(r['period'],r['board'],r['exit'],r['candidate']) for r in rows]
            ==[(g['period'],g['board'],g['exit'],m) for g in groups for m in ('baseline',*CANDIDATES)],'Complete 60-account ordering')
    require(len(comparison['groupCompletions'])==12,'All terminal group receipts')
    for g,item in zip(groups,comparison['groupCompletions']):
        p,b,e=(g[k] for k in ('period','board','exit'));terminal=read(bound(item))
        require(bound(item)==directory/p/b/e/'group-completion.json' and terminal['completed'] is True
                and terminal['newAccounts']==4 and terminal['referenceAccountsReused']==1,'Terminal group scope')
        for k in ('planSha256','inputCompletionSha256','inputValidationSha256','preAccountTestsSha256','runStartSha256'):
            require(terminal[k]==comparison[k],'Terminal group binding '+k)
        same(terminal['rows'],[r for r in rows if (r['period'],r['board'],r['exit'])==(p,b,e)],'Complete group account artifacts')
        group_start=read(directory/p/b/e/'group-run-start.json')
        require(group_start['planSha256']==sha(directory/'plan.json') and group_start['runStartSha256']==sha(directory/'run-start.json')
                and datetime.fromisoformat(start['startedAt'])<=datetime.fromisoformat(group_start['startedAt'])
                <=datetime.fromisoformat(terminal['completedAt']),'Per-group run chronology')
    board=independent('scripts/verify-board-coverage-reclaim.py')
    accounting=independent('scripts/verify-peer-amount-persistence.py');cost=accounting.cost_reference()
    trail=independent('scripts/verify-reclaim-exit-transfer.py');volatility=independent('scripts/verify-reclaim-volatility-trail.py')
    prior=independent('scripts/verify-online-feedback-entry.py');atlas=read(bound(plan['atlas']))
    data=None;data_key=None;reports=[]
    counts=dict(accounts=0,newAccounts=0,reusedAccounts=0,sourceImmediateOpportunities=8155,sourceExitOpportunities=16310,
                preparedSchedules=48,newFills=0,heldCloseChecks=0,summaryIntervals=0)
    for r in rows:
        p,b,e,m=(r[k] for k in ('period','board','exit','candidate'))
        g=next(g for g in groups if (g['period'],g['board'],g['exit'])==(p,b,e));folder=directory/p/b/e
        population,model=(None,None) if m=='baseline' else candidate_dimensions(m)
        require((r['population'],r['model'])==(population,model) and r['reusedAccount'] is (m=='baseline'),'Reported candidate dimensions')
        expected_schedule=g['baselineSchedule'] if m=='baseline' else g['candidates'][m]['schedule']
        same(r['schedule'],expected_schedule,'Account exact frozen schedule')
        require(bound(r['summary'])==folder/(m+'.summary.json'),'Summary path identity')
        if m=='baseline':
            same(r['result'],g['baseline'][e+'.result.json.gz'],'Original baseline result bytes')
            same(r['checkpoint'],g['baseline'][e+'.checkpoint.json.gz'],'Original baseline checkpoint bytes')
        else:
            require(bound(r['result'])==folder/(m+'.result.json.gz') and bound(r['checkpoint'])==folder/(m+'.checkpoint.json.gz'),'New account artifact identity')
        result,checkpoint,summary,schedule=[read(bound(r[k])) for k in ('result','checkpoint','summary','schedule')]
        require(summary['policyId']==m and summary['policyLabel']==m,'Account summary policy label')
        _,market,account=cost.reference_modules(p)
        full_summary=account.verify_summary(result,summary,prior.interval_reference(p,atlas))
        counts['summaryIntervals']+=len(summary['periods']);counts['accounts']+=1
        typed_fee_check(result,p)
        if m=='baseline':
            require(result['research']['identity']['signalSchedule']==schedule,'Original baseline schedule identity')
            audit=dict(reusedAuditedAccount=True,summary=full_summary);counts['reusedAccounts']+=1
        else:
            if data_key!=(p,b):
                if data is not None: del data;gc.collect()
                data=read(bound(g['accountData']));data_key=(p,b)
                require(digest(data)==plan['sourceData'][p][b]['accountDatasetDigest'],'Original account dataset digest')
            compatible=deepcopy(summary);compatible['periods']=summary['periods'][:7]
            fn=trail.audit_account if e=='trail10' else volatility.audit_account
            audit=fn(result,checkpoint,compatible,schedule,plan['periods'][p]['policyConfigs'][e],data,p,e,board,accounting)
            audit['fullSummaryIntervals']=full_summary
            by_day={d['formationDate']:d for d in records(bound(g['decisions'][population]))}
            permitted={s['signalId']:s for s in schedule}
            for fill in result['trades']:
                if fill['side']=='buy':
                    require(fill['signalId'] in permitted and fill['signalDate']==permitted[fill['signalId']]['signalDate']
                            and by_day[fill['signalDate']]['models'][model]['allow'] is True,'New buy not admitted by frozen model decision')
            counts['newAccounts']+=1;counts['newFills']+=len(result['trades']);counts['heldCloseChecks']+=audit['dailyRisk']['heldDays']
            require(digest(data)==plan['sourceData'][p][b]['accountDatasetDigest'],'Audit mutated input data')
        reports.append(dict(period=p,board=b,exit=e,candidate=m,population=population,model=model,artifactRefs=r,checks=audit))
        print(json.dumps({'checkedAccount':[p,b,e,m],'reused':m=='baseline'}),flush=True)
        del result,checkpoint,summary,schedule;gc.collect()
    require((counts['accounts'],counts['newAccounts'],counts['reusedAccounts'])==(60,48,12),'Actual complete account audit counts')
    registered_plan(directory)
    return write_proof(directory,'validation.json',dict(passed=True,verifiedAt=datetime.now(timezone.utc).isoformat(),
        planSha256=sha(directory/'plan.json'),comparisonSha256=sha(directory/'comparison.json'),
        inputCompletionSha256=sha(directory/'input-completion.json'),inputValidationSha256=sha(directory/'input-validation.json'),
        preAccountTestsSha256=sha(directory/'pre-account-tests.json'),verifierSha256=sha(__file__),
        sourcePins={**DEPENDENCIES,**cost.REFERENCES},inputPins=plan['inputPins'],counts=counts,accounts=reports,
        accountArtifacts=rows,baselineArtifacts=plan['accountArtifacts'],libraryValidation=plan['libraryValidation'],
        libraryReadoutValidation=plan['libraryReadoutValidation'],runStart=ref(directory/'run-start.json'),
        checks=['12 baseline result/checkpoint pairs byte-reused; all 60 complete carried-equity summaries independently recomputed',
                '48 new account fee ledgers, cash/shares, full curves, terminal positions, fill budgets, lots and execution restrictions audited',
                'Every held-close exit decision independently replayed including unknown windows, carried floors and existing-exit priority',
                'Every actual buy belongs to its exact frozen population/model allow schedule, no model refit or new label generation',
                'Original int-5 historical fee settings identity preserved; pretests and 48-mask audit precede all new accounts'],
        holdoutClaim=False,certified=False,targetAchieved=False))


if __name__=='__main__':
    parser=ArgumentParser();parser.add_argument('mode',choices=('inputs','accounts'),nargs='?',default='accounts')
    parser.add_argument('--inputs','--inputs-only',dest='inputs',action='store_true')
    parser.add_argument('--self-test',action='store_true');parser.add_argument('--out',type=Path,default=DEFAULT)
    args=parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(),ensure_ascii=False,indent=2))
    else:
        directory=args.out.resolve();mode='inputs' if args.inputs or args.mode=='inputs' else 'accounts'
        try: (verify_inputs if mode=='inputs' else verify_accounts)(directory)
        except Exception as error:
            if directory.exists():
                folder=directory/'validation-attempts';folder.mkdir(exist_ok=True)
                with (folder/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')).open('x',encoding='utf-8') as stream:
                    json.dump(dict(passed=False,at=datetime.now(timezone.utc).isoformat(),mode=mode,verifierSha256=sha(__file__),
                        type=type(error).__name__,message=str(error),traceback=traceback.format_exc()),stream,ensure_ascii=False,indent=2)
            raise
