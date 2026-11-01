"""Independent inherited-table and new-account readout verification."""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import traceback

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'artifacts/strategy-search/common-support-switch-v1'
MAIN='scripts/verify-common-support-switch.py'
MAIN_SHA='bef26a73a5d92c661b7b7b40dc70e75b94de33fde1b9e2b01e717d055a5a569a'
OLD='scripts/verify-online-feedback-readout.py'
OLD_SHA='dd2f467f25d91ecbd5712b40dc8d7c02717ab4ac82a6d755dd51c306c9c233b2'
STATES=('bothFitted','originalOnlyFitted','allOnlyFitted','neitherFitted')
POPULATIONS=('originalEntered','allExecutable')
MODELS=('intercept','ridge')
KEYS=('period','board','exit','candidate')
CONTRASTS=tuple(('commonVs'+label+model.title(),population+'__'+model,'commonSupport__'+model,family)
    for model in MODELS for label,population,family in [('Original','originalEntered','commonSupportChange'),('All','allExecutable','extraCoverageRemoved')])


def load_file(name):
    spec=importlib.util.spec_from_file_location('_common_readout_'+Path(name).stem.replace('-','_'),ROOT/name)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def load():
    m=load_file(MAIN)
    m.require(m.sha(ROOT/MAIN)==MAIN_SHA and m.sha(ROOT/OLD)==OLD_SHA,'Frozen independent main/readout math changed')
    return m,load_file(OLD)


def identity(row):return tuple(row[k] for k in KEYS)


def reuse_reference(parent,reference_rows,m):
    expected={(p,b,e,c) for p in m.PERIODS for b in m.BOARDS for e in m.EXITS for c in m.INHERITED_CANDIDATES}
    refs={identity(r):r for r in reference_rows}
    m.require(len(refs)==len(reference_rows)==60 and set(refs)==expected,'Complete inherited reference descriptors')
    accounts={identity(r):r for r in parent['accounts']};flat={identity(r):r for r in parent['accountRows']}
    m.require(set(accounts)==set(flat)==expected and len(parent['accounts'])==len(parent['accountRows'])==60,'All parent account summaries')
    out=[];rows=[]
    for key,descriptor in refs.items():
        m.require(accounts[key]['artifacts']==descriptor,'Exact inherited artifact descriptor')
        additions=dict(executionOrigin='reused',reusedAccount=True,sourceReusedAccount=descriptor['reusedAccount'])
        out.append({**deepcopy(accounts[key]),**additions});rows.append({**deepcopy(flat[key]),**additions})
    tables={}
    for name,count in [('yearRows',120),('halfYearRows',240),('phaseRows',480),('curveEpochRows',60),('coverageRows',60),('partitionReconciliation',60)]:
        m.require(len(parent[name])==count and all(identity(r) in expected for r in parent[name]),'Parent table count/identity '+name)
        tables[name]=[{**deepcopy(r),'executionOrigin':'reused'} for r in parent[name]]
    return out,rows,tables


def switch_coverage_reference(key,model,original,decisions,schedule,calendar,target_dates,m,old):
    wanted,basic=m.switch_schedule_reference(original,decisions['originalEntered'],decisions['allExecutable'],model,calendar,target_dates)
    m.require(wanted==schedule,'Readout consumes exact audited switch schedule')
    counts=m.count_reference(original,decisions['originalEntered'],decisions['allExecutable'],model,basic)
    indexes={p:{r['formationDate']:r for r in decisions[p]} for p in POPULATIONS}
    selected=[]
    for row in basic:
        day=row['formationDate'];a=indexes['originalEntered'][day]['models'][model];z=indexes['allExecutable'][day]['models'][model]
        chosen=z if row['selectedPopulation']=='allExecutable' else a
        selected.append(dict(formationDate=day,asOfDate=row['asOfDate'],supportState=row['supportState'],selectedPopulation=row['selectedPopulation'],
            originalAllow=a['allow'],allAllow=z['allow'],selectedAllow=row['allow'],changedAllow=row['allow']!=a['allow'],models={model:deepcopy(chosen)}))
    by_day={r['formationDate']:r for r in selected};signal_rows=[by_day[s['signalDate']] for s in original]
    coverage={**key,'candidate':'commonSupport__'+model,'population':'commonSupport','model':model,
        'targetDayCount':len(selected),'originalOpportunities':len(original),'scheduledSignals':len(schedule),'notScheduledSignals':len(original)-len(schedule),
        **{s+'Days':counts['supportStateDays'][s] for s in STATES},**{s+'Signals':counts['supportStateSignals'][s] for s in STATES},
        'selectedAllDays':counts['selectedPopulationDays']['allExecutable'],'selectedOriginalDays':counts['selectedPopulationDays']['originalEntered'],
        'selectedAllSignals':counts['selectedPopulationSignals']['allExecutable'],'selectedOriginalSignals':counts['selectedPopulationSignals']['originalEntered'],
        **{k:counts[k] for k in ('changedAllowDays','changedAllowSignals','addedSignals','removedSignals','supportStateDays','supportStateSignals',
                               'admittedSignalsBySupportState','rejectedSignalsBySupportState')},
        'modelStatusCounts':old.labels(r['models'][model]['status'] for r in selected),
        'commonVsAllChangedAllowDays':sum(r['selectedAllow']!=r['allAllow'] for r in selected),
        'commonVsAllChangedAllowSignals':sum(r['selectedAllow']!=r['allAllow'] for r in signal_rows),
        'commonVsAllAddedSignals':sum(not r['allAllow'] and r['selectedAllow'] for r in signal_rows),
        'commonVsAllRemovedSignals':sum(r['allAllow'] and not r['selectedAllow'] for r in signal_rows),'preparedCounts':counts,
        'meaning':'All target days and original opportunities remain in separate denominators. Unprefixed changes are versus originalEntered and occur only on bothFitted days. commonVsAll changes are versus allExecutable and occur only outside bothFitted. The switch is not simply an all-only-date filter: originalOnlyFitted days can also differ from allExecutable fallback.'}
    return coverage,selected


def comparisons_reference(flat,m):
    index={identity(r):r['metrics'] for r in flat}
    expected={(p,b,e,c) for p in m.PERIODS for b in m.BOARDS for e in m.EXITS for c in (*m.INHERITED_CANDIDATES,*m.CANDIDATES)}
    m.require(len(index)==len(flat)==84 and set(index)==expected,'Complete 84-account comparison matrix')
    output=[]
    for p in m.PERIODS:
        for b in m.BOARDS:
            for e in m.EXITS:
                for cid,control,candidate,family in CONTRASTS:
                    a,z=index[p,b,e,control],index[p,b,e,candidate]
                    m.require(set(a)==set(z),'Comparison metric schema')
                    output.append(dict(period=p,board=b,exit=e,id=cid,comparisonFamily=family,control=control,candidate=candidate,
                        controlMetrics=a,candidateMetrics=z,candidateMinusControl={k:z[k]-a[k] if m.finite(a[k]) and m.finite(z[k]) else None for k in sorted(a)},
                        meaning='Complete shared-capital accounts; changed admissions can change later holdings, cash and capacity. Not a sum of paired independent paths or a per-trade causal effect.'))
    return output


def self_test():
    m,old=load();tests=load_file('tests/test_common_support_switch_verifier.py')
    cal,dates,left,right,signals=tests.fixture();decisions=dict(originalEntered=left,allExecutable=right)
    schedule,_=m.switch_schedule_reference(signals,left,right,'ridge',cal,dates)
    key=dict(period='2024-2025',board='main',exit='trail10')
    cov,selected=switch_coverage_reference(key,'ridge',signals,decisions,schedule,cal,dates,m,old)
    m.require(cov['changedAllowSignals']==3 and cov['commonVsAllChangedAllowSignals']==6
              and cov['commonVsAllAddedSignals']==3 and cov['commonVsAllRemovedSignals']==3,'Both sides of support change preserve different denominators')
    m.require([r['supportState'] for r in selected]==list(STATES) and cov['modelStatusCounts']=={'fallback':2,'fitted':2},'Four regimes and selected model support')
    checks=['Original-only and all-only support differences are both retained; switch is not merely an all-only exclusion',
            'Selected prediction support/fallback distribution follows the actual chosen population']
    refs=[dict(period=p,board=b,exit=e,candidate=c,reusedAccount=c=='baseline')
          for p in m.PERIODS for b in m.BOARDS for e in m.EXITS for c in m.INHERITED_CANDIDATES]
    parent=dict(accounts=[{**r,'artifacts':deepcopy(r),'metrics':{'x':i}} for i,r in enumerate(refs)],
                accountRows=[{**r,'metrics':{'x':i}} for i,r in enumerate(refs)])
    for name,multiple in [('yearRows',2),('halfYearRows',4),('phaseRows',8),('curveEpochRows',1),('coverageRows',1),('partitionReconciliation',1)]:
        parent[name]=[{**r,'value':j} for r in refs for j in range(multiple)]
    before=deepcopy(parent);accounts,flat,tables=reuse_reference(parent,refs,m)
    m.require(parent==before and len(accounts)==60 and all(r['reusedAccount'] and r['executionOrigin']=='reused' for r in flat)
              and sum(r['sourceReusedAccount'] for r in flat)==12,'Inherited flags are historical metadata, not current account counts')
    m.require(all(r['artifacts']==refs[i] for i,r in enumerate(accounts)),'Inherited artifact descriptors exact')
    checks.append('All sixty inherited descriptors/numbers remain unchanged; current and source reuse semantics are separated')
    for p in m.PERIODS:
        for b in m.BOARDS:
            for e in m.EXITS:
                for c in m.CANDIDATES:flat.append(dict(period=p,board=b,exit=e,candidate=c,metrics={'x':100}))
    contrasts=comparisons_reference(flat,m)
    m.require(len(contrasts)==48 and sum(r['comparisonFamily']=='commonSupportChange' for r in contrasts)==24,'Complete fixed contrast matrix')
    for row in contrasts:m.require(row['candidateMinusControl']['x']==100-row['controlMetrics']['x'],'Signed contrast')
    checks.append('All 84 accounts and 48 signed comparisons retained without selecting outcomes')
    altered=deepcopy(refs);altered[1]['reusedAccount']=True
    try:reuse_reference(parent,altered,m)
    except AssertionError:pass
    else:raise AssertionError('Modified inherited descriptor not rejected')
    checks.append('Changed source descriptor is rejected even when normalized current reuse would look identical')
    return dict(passed=True,syntheticGroups=len(checks),checks=checks,realSourceReads=0,modelFits=0,accountExecutions=0)


def verify(directory):
    m,old=load();plan=m.registered_plan(directory);completion,groups=m.frozen_groups(directory,plan)
    proof=m.read(directory/'validation.json');report=m.read(directory/'readout.json');comparison=m.read(directory/'comparison.json')
    m.require(proof['passed'] is True and proof['verifierSha256']==MAIN_SHA and proof['planSha256']==m.sha(directory/'plan.json')
        and proof['comparisonSha256']==m.sha(directory/'comparison.json')
        and proof['inputCompletionSha256']==m.sha(directory/'input-completion.json')
        and proof['inputValidationSha256']==m.sha(directory/'input-validation.json'),'Exact independent input/account proof')
    m.require(report['schema']=='common-support-switch-readout-v1' and report['verification']['passed'] is True
        and m.bound(report['verification'])==directory/'validation.json' and report['planSha256']==proof['planSha256']
        and report['comparisonSha256']==proof['comparisonSha256'],'Readout proof identity')
    m.same([r['artifactRefs'] for r in proof['accounts']],comparison['rows'],'All independently audited account descriptors')
    m.require((proof['counts']['accounts'],proof['counts']['newAccounts'],proof['counts']['reusedAccounts'])==(84,24,60),'Account proof scope')
    for name,value in report['inputPins'].items():m.bound(dict(path=name,sha256=value))
    parent=m.read(m.bound(plan['parentReadout']));parent_proof=m.read(m.bound(plan['parentReadoutValidation']))
    accounts,flat,tables=reuse_reference(parent,comparison['referenceRows'],m)
    parent_ref_fields=('opportunityRowsArtifact','candidateEntryRowsArtifact','pairedPredictionRowsArtifact')
    for field in parent_ref_fields:
        m.bound(parent[field]);m.require(parent_proof[field+'Sha256']==parent[field]['sha256'],'Inherited sidecar independently bound '+field)
    m.require(parent['opportunityRowsArtifact']['count']==parent_proof['counts']['policyDispositions']==81550
        and parent['pairedPredictionRowsArtifact']['count']==parent_proof['counts']['pairedPredictionOpportunities']==65240
        and len(parent['comparisons'])==96 and len(parent['predictionEvaluationRows'])==192,'Complete inherited diagnostics')
    expected_inherited=dict(readout=plan['parentReadout'],validation=plan['parentReadoutValidation'],comparisons=dict(field='comparisons',count=96),
        predictionEvaluationRows=dict(field='predictionEvaluationRows',count=192),pairedPredictionRowsArtifact=parent['pairedPredictionRowsArtifact'],
        sampleShiftRows=dict(field='sampleShiftRows',count=48),feedbackCoverageRows=dict(field='feedbackCoverageRows',count=48))
    m.same(report['inheritedEvidence'],expected_inherited,'Prior diagnostics referenced without alteration or recomputation')
    m.same(report['inheritedOpportunityRowsArtifact'],parent['opportunityRowsArtifact'],'Inherited full disposition sidecar')
    m.same(report['inheritedCandidateEntryRowsArtifact'],parent['candidateEntryRowsArtifact'],'Inherited actual entries sidecar')
    descriptors={identity(r):r for r in comparison['rows']};order={k:i for i,k in enumerate(descriptors)}
    mathref=m.P.independent('scripts/verify-online-feedback-entry.py')
    episode_audit=m.P.independent('scripts/verify-reclaim-exit-mechanism.py')
    phases={r['id']:r for r in m.read(m.bound(plan['atlas']))['phases']}
    details=[];entries=[];shifts=[];switch_rows=[];decision_refs=[];intervals=[];original_counts={}
    for g in groups:
        p,b,e=(g[k] for k in ('period','board','exit'));key=dict(period=p,board=b,exit=e)
        original=m.read(m.bound(g['baselineSchedule']));metadata=m.read(m.bound(plan['metadata'][p][b]));dates=metadata['targetDates']
        original_counts[p,b]=len(original)
        # Derive original membership directly from already-audited baseline buys,
        # independently of the new summary's inherited-disposition parsing.
        baseline=descriptors[p,b,e,'baseline'];baseline_result=m.read(m.bound(baseline['result']))
        entered={r['signalId'] for r in baseline_result['trades'] if r['side']=='buy'}
        m.require(entered<={s['signalId'] for s in original},'Original baseline entry identity')
        decisions={pop:m.records(m.bound(g['decisions'][pop])) for pop in POPULATIONS}
        for pop in POPULATIONS:
            decision_refs.append({**key,'population':pop,'artifact':g['decisions'][pop],
                'targetDayCount':len(decisions[pop]),'scope':'Byte-reused frozen decisions; no new forecasts or fitting.'})
        for model in MODELS:
            candidate='commonSupport__'+model;account_key={**key,'candidate':candidate,'population':'commonSupport','model':model}
            item=descriptors[p,b,e,candidate];schedule=m.read(m.bound(item['schedule']))
            switch,selected=switch_coverage_reference(key,model,original,decisions,schedule,metadata['calendar'],dates,m,old)
            m.same(switch['preparedCounts'],g['candidates'][candidate]['counts'],'Switch counts versus independently audited prepared evidence')
            switch_rows.append(switch)
            result=m.read(m.bound(item['result']));summary=m.read(m.bound(item['summary']))
            m.require(summary['inputResultSha256']==m.digest(result) and summary['policyId']==candidate and result['status']=='completed'
                and [r['date'] for r in result['curve']]==dates,'New account source and full calendar')
            disposition,actual=old.episodes_reference(account_key,original,schedule,result,entered,mathref,episode_audit)
            details.extend(disposition);entries.extend(actual);shifts.append(old.shift_reference(account_key,actual,summary,mathref))
            metrics=old.metric_reference(summary,len(schedule),len(original))
            provenance=dict(executionOrigin='new',reusedAccount=False,sourceReusedAccount=False)
            flat.append({**account_key,**provenance,'metrics':metrics})
            accounts.append({**account_key,**provenance,'artifacts':item,'metrics':metrics,
                **{k:summary[k] for k in ('account','closedEpisodes','concentration','openPositions','rights','unresolvedExits','reconciliation','quality','warnings')},
                'sourceQualityScalarFlags':{k:v for k,v in summary['sourceQuality'].items() if not isinstance(v,(dict,list))},
                'fullSourceQuality':{'summaryRef':item['summary'],'field':'sourceQuality'}})
            state=Counter(r['positionState'] for r in disposition)
            tables['coverageRows'].append({**account_key,'executionOrigin':'new','originalOpportunities':len(original),'scheduledSignals':len(schedule),
                'notScheduled':state['notScheduled'],'entered':len(actual),'closed':state['closed'],'openAtCutoff':state['openAtCutoff'],'notEntered':state['notEntered'],
                'accountOpportunityReasons':old.labels(r['accountDisposition']['reason'] for r in disposition if r['scheduled']),
                'valuationUnreliableDays':metrics['unreliableValuationDays']})
            measured,partition=old.intervals_reference({**account_key,'candidate':model},result,summary,original,schedule,selected,mathref,phases)
            for row in measured:
                subset=[r for r in selected if row['requestedStart']<=r['formationDate']<=row['requestedEnd']]
                row.update(account_key);row['executionOrigin']='new'
                row['switchSupportStateCounts']={s:sum(r['supportState']==s for r in subset) for s in STATES}
                row['switchSelectedPopulationCounts']={pop:sum(r['selectedPopulation']==pop for r in subset) for pop in POPULATIONS}
            partition.update(account_key);partition['executionOrigin']='new'
            intervals.extend(measured);tables['partitionReconciliation'].append(partition)
        print(json.dumps({'checkedReadout':[p,b,e],'originalFormations':len(original),'newAccounts':2}),flush=True)
    for name,kind in [('yearRows','calendarYear'),('halfYearRows','calendarHalfYear'),('phaseRows','retrospectiveNarrativePhase'),('curveEpochRows','descriptiveEpoch')]:
        tables[name].extend(r for r in intervals if r['kind']==kind)
    accounts.sort(key=lambda r:order[identity(r)]);flat.sort(key=lambda r:order[identity(r)])
    for rows in tables.values():rows.sort(key=lambda r:(order[identity(r)],r.get('requestedStart',''),r.get('id','')))
    comparisons=comparisons_reference(flat,m)
    expected=dict(accounts=accounts,accountRows=flat,comparisons=comparisons,primaryComparisons=comparisons,
        populationComparisons=[r for r in comparisons if r['comparisonFamily']=='commonSupportChange'],
        coverageComparisons=[r for r in comparisons if r['comparisonFamily']=='extraCoverageRemoved'],**tables,
        switchCoverageRows=switch_rows,sampleShiftRows=shifts,feedbackDecisionArtifacts=decision_refs)
    for name,value in expected.items():m.same(report[name],value,'Independent report table '+name)
    for field,values in [('opportunityRowsArtifact',details),('candidateEntryRowsArtifact',entries)]:
        item=report[field];keys=['period','board','exit','candidate','formationId']
        m.require(item['count']==len(values) and item['format']=='jsonl+gzip' and item['identity']==keys
            and len({tuple(r[k] for k in keys) for r in values})==len(values),'New sidecar complete unique identities')
        m.same(m.records(m.bound(item)),values,'All independently reconstructed new account episodes/dispositions')
    expected_sizes=dict(yearRows=168,halfYearRows=336,phaseRows=672,curveEpochRows=84,coverageRows=84,partitionReconciliation=84)
    m.require(all(len(tables[k])==v for k,v in expected_sizes.items()) and len(intervals)==360,'Complete inherited/new interval matrices')
    counts=dict(accounts=len(accounts),newAccounts=sum(r['executionOrigin']=='new' for r in accounts),reusedAccounts=sum(r['executionOrigin']=='reused' for r in accounts),
        comparisons=len(comparisons),primaryComparisons=len(comparisons),populationComparisons=len(expected['populationComparisons']),
        coverageComparisons=len(expected['coverageComparisons']),**{k:len(v) for k,v in tables.items()},switchCoverageRows=len(switch_rows),sampleShiftRows=len(shifts),
        originalOpportunities=sum(original_counts.values()),sourceExitOpportunities=2*sum(original_counts.values()),policyDispositions=81550+len(details),
        newPolicyDispositions=len(details),inheritedPolicyDispositions=parent['opportunityRowsArtifact']['count'],actualCandidateEntryEpisodes=len(entries),
        inheritedCandidateEntryEpisodes=parent['candidateEntryRowsArtifact']['count'],inheritedComparisons=96,inheritedPredictionEvaluationRows=192,
        inheritedPairedPredictionOpportunities=65240,newPredictionEvaluationRows=0,frozenDecisionLibraries=len(decision_refs),
        frozenDecisionDays=sum(r['targetDayCount'] for r in decision_refs))
    m.same(report['counts'],counts,'Complete observed readout counts')
    m.require((counts['accounts'],counts['newAccounts'],counts['reusedAccounts'],len(details),counts['originalOpportunities'],counts['frozenDecisionDays'])
        ==(84,24,60,32620,8155,11628),'Complete original and candidate denominators')
    m.require(report['newAccounts']==24 and report['referenceAccountsReused']==60 and report['signalsGenerated'] is False
        and report['summarizerAccountExecutions']==report['summarizerModelFits']==report['summarizerPathExecutions']==0
        and report['holdoutClaim'] is False and report['certified'] is False and report['targetAchieved'] is False,'Readout fixed diagnostic limits')
    for name,value in report['inputPins'].items():m.bound(dict(path=name,sha256=value))
    receipt=dict(passed=True,verifiedAt=datetime.now(timezone.utc).isoformat(),planSha256=m.sha(directory/'plan.json'),
        comparisonSha256=m.sha(directory/'comparison.json'),validationSha256=m.sha(directory/'validation.json'),
        accountValidationSha256=m.sha(directory/'validation.json'),readoutSha256=m.sha(directory/'readout.json'),
        verifierSha256=m.sha(__file__),mainVerifierSha256=MAIN_SHA,inputCompletionSha256=m.sha(directory/'input-completion.json'),
        inputValidationSha256=m.sha(directory/'input-validation.json'),parentReadoutSha256=plan['parentReadout']['sha256'],
        parentReadoutValidationSha256=plan['parentReadoutValidation']['sha256'],
        sourcePins={**m.DEPENDENCIES,MAIN:MAIN_SHA,OLD:OLD_SHA,Path(__file__).relative_to(ROOT).as_posix():m.sha(__file__)},inputPins=report['inputPins'],
        counts={**counts,'sourceImmediateOpportunities':counts['originalOpportunities']},
        opportunityRowsArtifact=report['opportunityRowsArtifact'],candidateEntryRowsArtifact=report['candidateEntryRowsArtifact'],
        opportunityRowsArtifactSha256=report['opportunityRowsArtifact']['sha256'],candidateEntryRowsArtifactSha256=report['candidateEntryRowsArtifact']['sha256'],
        checks=['60 parent account tables and 900 intervals retained exactly except explicit current-experiment provenance fields',
            '24 new account records and 360 full-calendar carried-equity intervals independently reconciled from audited results',
            '48 signed contrasts include all sources/models/exits; old 96 contrasts and 192 prediction diagnostics remain exact references',
            'Four support states and both sides of admission changes independently summarized from immutable T-1 predictions',
            '32620 new original-opportunity dispositions and every actual entry reconstructed; nonentry/open are never zero closed returns',
            'No new model fitting, label paths, forecast-error study or account execution in this readout audit'],holdoutClaim=False,certified=False,targetAchieved=False)
    return m.P.write_proof(directory,'readout-validation.json',receipt)


if __name__=='__main__':
    parser=ArgumentParser();parser.add_argument('--self-test',action='store_true');parser.add_argument('--out',type=Path,default=DEFAULT);args=parser.parse_args()
    if args.self_test:print(json.dumps(self_test(),ensure_ascii=False,indent=2))
    else:
        directory=args.out.resolve()
        try:verify(directory)
        except Exception as error:
            m,_=load();folder=directory/'validation-attempts';folder.mkdir(exist_ok=True)
            with (folder/('readout-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')).open('x',encoding='utf-8') as stream:
                json.dump(dict(passed=False,at=datetime.now(timezone.utc).isoformat(),verifierSha256=m.sha(__file__),type=type(error).__name__,
                    message=str(error),traceback=traceback.format_exc()),stream,ensure_ascii=False,indent=2)
            raise
