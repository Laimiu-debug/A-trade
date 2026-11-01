"""Independent report and paired-error audit, with no new summary/model import."""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.util
import json
import math
from pathlib import Path
import traceback

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'artifacts/strategy-search/all-opportunity-feedback-v1'
MAIN='scripts/verify-all-opportunity-feedback.py'
MAIN_SHA='d5f1d1f611e38c0460a6ce1c03a8229b44f34fd310a6c59d9fb974335bc26857'
OLD='scripts/verify-online-feedback-readout.py'
OLD_SHA='dd2f467f25d91ecbd5712b40dc8d7c02717ab4ac82a6d755dd51c306c9c233b2'
POPULATIONS=('originalEntered','allExecutable')
MODELS=('intercept','ridge')
CANDIDATES=('baseline',*(p+'__'+m for p in POPULATIONS for m in MODELS))
PAIRS=(('allVsOriginalIntercept','originalEntered__intercept','allExecutable__intercept','population'),
       ('allVsOriginalRidge','originalEntered__ridge','allExecutable__ridge','population'),
       ('ridgeVsInterceptOriginal','originalEntered__intercept','originalEntered__ridge','model'),
       ('ridgeVsInterceptAll','allExecutable__intercept','allExecutable__ridge','model'))
ACCOUNT_PAIRS=(*PAIRS,*((c+'VsBaseline','baseline',c,'baseline') for c in CANDIDATES[1:]))
FIT_CELLS=('bothFitted','controlOnlyFitted','candidateOnlyFitted','neitherFitted')
PATH_STATES=('closed','open','unfilled','unresolved')


def require(ok,message):
    if not ok: raise AssertionError(message)


def finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def load_file(name):
    spec=importlib.util.spec_from_file_location('_all_feedback_readout_'+Path(name).stem.replace('-','_'),ROOT/name)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def load():
    m=load_file(MAIN)
    require(m.sha(ROOT/MAIN)==MAIN_SHA and m.sha(ROOT/OLD)==OLD_SHA,'Frozen independent main/readout helper changed')
    return m,load_file(OLD)


def candidate_identity(candidate):
    require(candidate in CANDIDATES,'Unknown candidate')
    population,model=(None,None) if candidate=='baseline' else candidate.split('__')
    return dict(candidate=candidate,population=population,model=model)


def json_lines(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        for line in stream:
            if line.strip(): yield json.loads(line)


def normalize_paths(rows,original,key,cutoff):
    output=[];seen=set()
    for row,signal in zip(rows,original,strict=True):
        path=row['path'];out=path['outcome'];label=path['label'];state=path['status']
        fid=row['formationId']
        require(fid==signal['signalId'] and fid not in seen and path['signal']==signal
                and path['exitPolicy']==key['exit'] and row['formationDate']==signal['signalDate']
                and row['symbol']==signal['symbol'] and all(row[k]==key[k] for k in ('period','board','exit')),'Exact all-opportunity path identity/order')
        seen.add(fid)
        require(state in PATH_STATES and type(row['originalEntered']) is bool and type(out['entered']) is bool,'Path state/membership')
        require(out['labelEligible'] is (state=='closed') and out['fullyClosed'] is (state=='closed'),'Mature label status')
        if out['entered']:
            require(isinstance(label,dict) and label['formationId']==fid and label['formationDate']==row['formationDate']
                    and label['entryDate']==out['entryDate']>row['formationDate'],'Actual path entry label')
        else:
            require(label is None and out['entryDate'] is None,'Nonentry must not have a label')
        require(state!='unfilled' or not out['entered'],'Unfilled entry conflict')
        require(state not in ('closed','open') or out['entered'],'Closed/open missing entry')
        if state=='closed':
            require(finite(label['netReturn']) and label['entryDate']<label['exitDate']<=cutoff
                    and label['exitDate']==out['finalExitDate']==out['labelAvailableDate']
                    and label['netReturn']==out['netReturn'] and finite(out['investedAmount']) and out['investedAmount']>0,'Settled final-cutoff label')
            require(math.isclose(label['netReturn'],out['netPnlAmount']/out['investedAmount'],rel_tol=1e-11,abs_tol=1e-11),'Cash-normalized final outcome')
        else:
            require(out['netReturn'] is None and (label is None or label['exitDate'] is label['netReturn'] is None),'Nonclosed not zero-filled')
        output.append(dict(formationId=fid,formationDate=row['formationDate'],symbol=row['symbol'],
            originalEntered=row['originalEntered'],pathStatus=state,pathEntered=out['entered'],entryDate=out['entryDate'],
            finalExitDate=label['exitDate'] if label else None,labelNetReturn=label['netReturn'] if label else None,sourceCutoff=cutoff))
    return output


def observation(day,model):
    if day is None or model not in day.get('models',{}):
        return dict(status='missing',reason='missingFrozenPrediction',prediction=None,allow=None)
    value=day['models'][model]
    require(type(value['allow']) is bool and value['status'] in ('fitted','fallback'),'Frozen model observation')
    if value['status']=='fitted':
        require(finite(value['prediction']) and value['allow'] is (value['prediction']>0),'Fitted prediction/allow')
    else:
        require(value['prediction'] is None and value['allow'] is True,'Fallback has no fitted forecast')
    return {k:deepcopy(value[k]) for k in ('status','reason','prediction','allow')}


def paired_reference(key,paths,decisions):
    indexes={p:{d['formationDate']:d for d in decisions[p]} for p in POPULATIONS}
    require(all(len(indexes[p])==len(decisions[p]) for p in POPULATIONS),'Duplicate frozen decision date')
    require(len({p['formationId'] for p in paths})==len(paths),'Duplicate path for evaluation')
    output=[]
    for cid,left,right,family in PAIRS:
        lp,lm=left.split('__');rp,rm=right.split('__')
        for path in paths:
            ld=indexes[lp].get(path['formationDate']);rd=indexes[rp].get(path['formationDate'])
            if ld is not None and rd is not None: require(ld['asOfDate']==rd['asOfDate'],'Common information cutoff')
            a,b=observation(ld,lm),observation(rd,rm)
            fitted=(a['status']=='fitted',b['status']=='fitted')
            cell={(True,True):'bothFitted',(True,False):'controlOnlyFitted',(False,True):'candidateOnlyFitted',(False,False):'neitherFitted'}[fitted]
            output.append({**key,'contrastId':cid,'comparisonFamily':family,'controlCandidate':left,'candidateCandidate':right,
                **deepcopy(path),'controlAsOfDate':ld['asOfDate'] if ld else None,'candidateAsOfDate':rd['asOfDate'] if rd else None,
                'control':a,'candidate':b,'fitCell':cell})
    return output


def score_reference(rows):
    """Compute each daily mean first, then equally average the represented days."""
    days=defaultdict(list)
    for r in rows:
        if r['pathStatus']=='closed':
            require(r['pathEntered'] and finite(r['labelNetReturn']),'Known closed label required')
            days[r['formationDate']].append(r)
    scored=[r for group in days.values() for r in group];n=len(scored);day_n=len(days)
    def avg(fn):
        return math.fsum(math.fsum(fn(r) for r in group)/len(group) for group in days.values())/day_n if day_n else None
    result=dict(episodeCount=n,formationDayCount=day_n,weightSum=float(day_n),
        minimumEpisodesPerFormationDay=min((len(x) for x in days.values()),default=None),
        maximumEpisodesPerFormationDay=max((len(x) for x in days.values()),default=None),
        meanNetReturnPct=avg(lambda r:r['labelNetReturn']*100),positiveLabelSharePct=avg(lambda r:100. if r['labelNetReturn']>0 else 0.))
    for side in ('control','candidate'):
        available=sum(r[side]['status']=='fitted' and finite(r[side]['prediction']) for r in scored)
        complete=n>0 and available==n
        result[side]=dict(predictionCount=available,completeOnScoredLabels=complete,
            meanPredictionPct=avg(lambda r:r[side]['prediction']*100) if complete else None,
            maePp=avg(lambda r:abs(r[side]['prediction']-r['labelNetReturn'])*100) if complete else None,
            msePpSquared=avg(lambda r:((r[side]['prediction']-r['labelNetReturn'])*100)**2) if complete else None)
    result['candidateMinusControl']={k:result['candidate'][k]-result['control'][k]
        if finite(result['candidate'][k]) and finite(result['control'][k]) else None
        for k in ('meanPredictionPct','maePp','msePpSquared')}
    return result


def cell_reference(rows,scored=True):
    states=Counter(r['pathStatus'] for r in rows)
    require(set(states)<=set(PATH_STATES),'Evaluation state enum')
    result=dict(opportunityCount=len(rows),formationDayCount=len({r['formationDate'] for r in rows}),
        symbolCount=len({r['symbol'] for r in rows}),enteredCount=sum(r['pathEntered'] for r in rows),
        closedCount=states['closed'],unfilledCount=states['unfilled'],openCount=states['open'],unresolvedCount=states['unresolved'],
        immatureEnteredCount=sum(r['pathEntered'] and r['pathStatus']!='closed' for r in rows),
        pathStatusCounts={s:states[s] for s in PATH_STATES},
        controlStatusCounts={s:sum(r['control']['status']==s for r in rows) for s in ('fitted','fallback','missing')},
        candidateStatusCounts={s:sum(r['candidate']['status']==s for r in rows) for s in ('fitted','fallback','missing')})
    if scored:result['score']=score_reference(rows)
    return result


def spans_reference(dates):
    require(dates==sorted(set(dates)) and dates,'Exact target dates for formation attribution')
    output=[dict(id='overall',kind='overall',startDate=dates[0],endDate=dates[-1])]
    for year in sorted({d[:4] for d in dates}):
        selected=[d for d in dates if d.startswith(year)]
        output.append(dict(id=year,kind='calendarYear',startDate=selected[0],endDate=selected[-1]))
    if dates[0]<'2024-09-24'<=dates[-1]:
        for label,after in [('before20240924',False),('from20240924',True)]:
            selected=[d for d in dates if (d>='2024-09-24') is after]
            output.append(dict(id=label,kind='descriptiveEpoch',startDate=selected[0],endDate=selected[-1]))
    return output


def evaluation_reference(key,paired,dates):
    output=[]
    for cid,control,candidate,family in PAIRS:
        source=[r for r in paired if r['contrastId']==cid]
        require(len(source)==len({r['formationId'] for r in source}) and all(r['formationDate'] in dates for r in source),'Complete paired attribution identity')
        for span in spans_reference(dates):
            selected=[r for r in source if span['startDate']<=r['formationDate']<=span['endDate']]
            fit={cell:cell_reference([r for r in selected if r['fitCell']==cell]) for cell in FIT_CELLS}
            common=[r for r in selected if r['fitCell']=='bothFitted']
            allowed={a+b:cell_reference([r for r in common if r['control']['allow'] is bool(int(a)) and r['candidate']['allow'] is bool(int(b))])
                     for a in ('0','1') for b in ('0','1')}
            margins={side:{name:cell_reference([r for r in common if r[side]['allow'] is value])
                for name,value in [('reject',False),('allow',True)]} for side in ('control','candidate')}
            require(sum(c['opportunityCount'] for c in fit.values())==len(selected)
                    and sum(c['opportunityCount'] for c in allowed.values())==len(common),'Complete fitted/allow partitions')
            output.append({**key,**span,'contrastId':cid,'comparisonFamily':family,'controlCandidate':control,'candidateCandidate':candidate,
                'targetDayCount':sum(span['startDate']<=d<=span['endDate'] for d in dates),
                'coverage':cell_reference(selected,False),'fitCells':fit,'commonFitted':fit['bothFitted'],
                'commonFittedAllowCells':allowed,'commonFittedAllowMargins':margins,
                'meaning':'Final-cutoff independent-path outcomes, attributed to formation T; each slice/cell reweights its own closed labels by formation day. Not portfolio returns or independent observations.'})
    return output


def account_comparisons_reference(flat,m):
    index={(r['period'],r['board'],r['exit'],r['candidate']):r['metrics'] for r in flat}
    require(len(index)==len(flat)==60,'Complete unique account comparison population')
    output=[]
    for p in m.PERIODS:
        for b in m.BOARDS:
            for e in m.EXITS:
                for cid,control,candidate,family in ACCOUNT_PAIRS:
                    a,z=index[p,b,e,control],index[p,b,e,candidate]
                    require(set(a)==set(z),'Account metric schema')
                    output.append(dict(period=p,board=b,exit=e,id=cid,comparisonFamily=family,control=control,candidate=candidate,
                        controlMetrics=a,candidateMetrics=z,candidateMinusControl={k:z[k]-a[k] if finite(a[k]) and finite(z[k]) else None for k in sorted(a)},
                        meaning='Separate complete accounts including changed funding and exposure; not a paired-trade causal effect.'))
    return output


def feedback_reference(key,population,daily,original,schedules,old):
    index={d['formationDate']:d for d in daily};output=[]
    for model in MODELS:
        candidate=population+'__'+model;seen=[observation(d,model) for d in daily]
        wanted=[s for s in original if index[s['signalDate']]['models'][model]['allow']]
        require(schedules[candidate]==wanted,'Coverage exact mask')
        output.append({**key,**candidate_identity(candidate),'targetDayCount':len(daily),
            'statusCounts':old.labels(r['status'] for r in seen),'reasonCounts':old.labels(r['reason'] for r in seen),
            'allowDayCount':sum(r['allow'] for r in seen),'rejectDayCount':sum(not r['allow'] for r in seen),
            'originalOpportunities':len(original),'scheduledSignals':len(wanted),'notScheduledSignals':len(original)-len(wanted),
            'fittedOriginalOpportunities':sum(index[s['signalDate']]['models'][model]['status']=='fitted' for s in original),
            'fallbackOriginalOpportunities':sum(index[s['signalDate']]['models'][model]['status']=='fallback' for s in original),
            'predictionWhenFitted':old.distribution(r['prediction'] for r in seen if r['status']=='fitted'),
            'eligibleEpisodesPerDay':old.distribution(r['support']['eligibleEpisodeCount'] for r in daily),
            'eligibleFormationDaysPerDay':old.distribution(r['support']['eligibleFormationDayCount'] for r in daily),
            'labelXUnknownEpisodeDayObservations':sum(r['support']['labelXUnknownCount'] for r in daily),
            'immatureEpisodeDayObservations':sum(r['support']['immatureCount'] for r in daily),
            'immatureDailyMeanAgeSlots':old.distribution(r['support']['immatureAgeSlots']['mean'] for r in daily),
            'currentFeatureStatusCounts':old.labels(r['currentFeatureStatus'] for r in daily),
            'meaning':'Full target-day coverage; immature counts repeat labels across days. Mean ages describe daily populations, not independent episodes.'})
    return output


def compare_stream(path,expected,m,label):
    count=0
    for saved,wanted in zip(json_lines(path),expected,strict=True):
        m.same(saved,wanted,f'{label}[{count}]');count+=1
    return count


def verify(directory):
    m,old=load();plan=m.registered_plan(directory);completion,groups=m.frozen_groups(directory,plan)
    proof=m.read(directory/'validation.json');report=m.read(directory/'readout.json');comparison=m.read(directory/'comparison.json')
    require(proof['passed'] is True and proof['verifierSha256']==MAIN_SHA and proof['planSha256']==m.sha(directory/'plan.json')
            and proof['comparisonSha256']==m.sha(directory/'comparison.json')
            and proof['inputCompletionSha256']==m.sha(directory/'input-completion.json')
            and proof['inputValidationSha256']==m.sha(directory/'input-validation.json'),'Exact account/input independent proofs')
    require(report['verification']['passed'] is True and m.bound(report['verification'])==directory/'validation.json'
            and report['planSha256']==proof['planSha256'] and report['comparisonSha256']==proof['comparisonSha256'],'Readout exact proof bindings')
    m.same([a['artifactRefs'] for a in proof['accounts']],comparison['rows'],'Readout uses exactly independently audited account artifacts')
    require((proof['counts']['accounts'],proof['counts']['newAccounts'],proof['counts']['reusedAccounts'])==(60,48,12),'Complete account audit prerequisite')
    for path,value in report['inputPins'].items():m.bound(dict(path=path,sha256=value))
    mathref=m.independent('scripts/verify-online-feedback-entry.py')
    episode_audit=m.independent('scripts/verify-reclaim-exit-mechanism.py')
    phases={r['id']:r for r in m.read(m.bound(plan['atlas']))['phases']}
    require(len(phases)==16,'Complete inherited narrative phases')
    descriptor={(r['period'],r['board'],r['exit'],r['candidate']):r for r in comparison['rows']}
    accounts=[];flat=[];intervals=[];coverage=[];feedback=[];shifts=[];partitions=[];evaluations=[]
    details=[];entries=[];pair_records=[];decision_refs=[];original_counts={}
    for g in groups:
        p,b,e=(g[k] for k in ('period','board','exit'));key=dict(period=p,board=b,exit=e)
        original=m.read(m.bound(g['baselineSchedule']));md=m.read(m.bound(plan['metadata'][p][b]));dates=md['targetDates']
        original_counts[p,b]=len(original)
        paths=normalize_paths(json_lines(m.bound(g['libraryPaths'])),original,key,dates[-1])
        original_entered={r['formationId'] for r in paths if r['originalEntered']}
        decisions={population:m.records(m.bound(g['decisions'][population])) for population in POPULATIONS}
        schedules={c:m.read(m.bound(g['candidates'][c]['schedule'])) for c in m.CANDIDATES}
        for population in POPULATIONS:
            require([r['formationDate'] for r in decisions[population]]==dates,'All target daily decisions')
            feedback.extend(feedback_reference(key,population,decisions[population],original,schedules,old))
            decision_refs.append({**key,'population':population,'artifact':g['decisions'][population],
                'targetDayCount':len(decisions[population]),'scope':'Byte-reused frozen prediction evidence; no refit by summarizer.'})
        paired=paired_reference(key,paths,decisions);pair_records.extend(paired)
        evaluations.extend(evaluation_reference(key,paired,dates))
        for candidate in CANDIDATES:
            account_key={**key,**candidate_identity(candidate)};item=descriptor[p,b,e,candidate]
            require((item['population'],item['model'])==(account_key['population'],account_key['model']),'Descriptor candidate dimensions')
            result=m.read(m.bound(item['result']));summary=m.read(m.bound(item['summary']))
            schedule=original if candidate=='baseline' else schedules[candidate]
            require(m.read(m.bound(item['schedule']))==schedule and summary['inputResultSha256']==m.digest(result)
                    and summary['policyId']==candidate and result['status']=='completed'
                    and [r['date'] for r in result['curve']]==dates,'Account source/complete calendar identity')
            disposition,actual=old.episodes_reference(account_key,original,schedule,result,original_entered,mathref,episode_audit)
            details.extend(disposition)
            if candidate=='baseline':require({r['formationId'] for r in actual}==original_entered,'Original entered-mask membership')
            else:
                entries.extend(actual);shifts.append(old.shift_reference(account_key,actual,summary,mathref))
            metrics=old.metric_reference(summary,len(schedule),len(original))
            flat.append({**account_key,'reusedAccount':candidate=='baseline','metrics':metrics})
            accounts.append({**account_key,'artifacts':item,'metrics':metrics,
                **{k:summary[k] for k in ('account','closedEpisodes','concentration','openPositions','rights','unresolvedExits','reconciliation','quality','warnings')},
                'sourceQualityScalarFlags':{k:v for k,v in summary['sourceQuality'].items() if not isinstance(v,(dict,list))},
                'fullSourceQuality':{'summaryRef':item['summary'],'field':'sourceQuality'}})
            states=Counter(r['positionState'] for r in disposition)
            coverage.append({**account_key,'originalOpportunities':len(original),'scheduledSignals':len(schedule),'notScheduled':states['notScheduled'],
                'entered':len(actual),'closed':states['closed'],'openAtCutoff':states['openAtCutoff'],'notEntered':states['notEntered'],
                'accountOpportunityReasons':old.labels(r['accountDisposition']['reason'] for r in disposition if r['scheduled']),
                'valuationUnreliableDays':metrics['unreliableValuationDays']})
            # The old independent interval arithmetic accepts model-only names;
            # map metadata back after computing every number, not the production summary.
            legacy_key={**account_key,'candidate':account_key['model'] or 'baseline'}
            measured,partition=old.intervals_reference(legacy_key,result,summary,original,schedule,
                decisions[account_key['population']] if account_key['population'] else [],mathref,phases)
            for row in measured:row.update(account_key)
            partition.update(account_key);intervals.extend(measured);partitions.append(partition)
        print(json.dumps({'checkedReadout':[p,b,e],'pairedRows':len(paired),'originalFormations':len(original)}),flush=True)
    contrasts=account_comparisons_reference(flat,m)
    expected=dict(accounts=accounts,accountRows=flat,comparisons=contrasts,
        primaryComparisons=[r for r in contrasts if r['comparisonFamily']=='population'],
        modelComparisons=[r for r in contrasts if r['comparisonFamily']=='model'],
        baselineComparisons=[r for r in contrasts if r['comparisonFamily']=='baseline'],
        coverageRows=coverage,feedbackCoverageRows=feedback,feedbackDecisionArtifacts=decision_refs,
        sampleShiftRows=shifts,partitionReconciliation=partitions,predictionEvaluationRows=evaluations,
        predictionPrimaryRows=[r for r in evaluations if r['comparisonFamily']=='population'])
    for name,kind in [('yearRows','calendarYear'),('halfYearRows','calendarHalfYear'),('phaseRows','retrospectiveNarrativePhase'),('curveEpochRows','descriptiveEpoch')]:
        expected[name]=[r for r in intervals if r['kind']==kind]
    for name,value in expected.items():m.same(report[name],value,'Independent entire readout table '+name)
    for field,values,identity in [('opportunityRowsArtifact',details,['period','board','exit','candidate','formationId']),
            ('candidateEntryRowsArtifact',entries,['period','board','exit','candidate','formationId']),
            ('pairedPredictionRowsArtifact',pair_records,['period','board','exit','contrastId','formationId'])]:
        item=report[field];require(item['count']==len(values) and item['format']=='jsonl+gzip' and item['identity']==identity,'Complete sidecar descriptor '+field)
        require(len({tuple(r[k] for k in identity) for r in values})==len(values),'Unique sidecar identity '+field)
        compare_stream(m.bound(item),values,m,field)
    counts=dict(accounts=len(accounts),newAccounts=sum(not r['reusedAccount'] for r in flat),reusedAccounts=sum(r['reusedAccount'] for r in flat),
        comparisons=len(contrasts),primaryComparisons=len(expected['primaryComparisons']),modelComparisons=len(expected['modelComparisons']),baselineComparisons=len(expected['baselineComparisons']),
        **{n:len(expected[n]) for n in ('yearRows','halfYearRows','phaseRows','curveEpochRows')},
        coverageRows=len(coverage),feedbackCoverageRows=len(feedback),sampleShiftRows=len(shifts),originalOpportunities=sum(original_counts.values()),
        sourceExitOpportunities=sum(original_counts.values())*2,policyDispositions=len(details),actualCandidateEntryEpisodes=len(entries),
        predictionEvaluationRows=len(evaluations),pairedPredictionOpportunities=len(pair_records),frozenDecisionLibraries=len(decision_refs),
        frozenDecisionDays=sum(r['targetDayCount'] for r in decision_refs))
    m.same(report['counts'],counts,'Actual complete report matrix counts')
    require((counts['accounts'],counts['newAccounts'],counts['reusedAccounts'],counts['policyDispositions'],counts['pairedPredictionOpportunities'],counts['predictionEvaluationRows'])
            ==(60,48,12,81550,65240,192),'No original opportunity or policy omitted')
    require([len(expected[n]) for n in ('yearRows','halfYearRows','phaseRows','curveEpochRows')]==[120,240,480,60]
            and len(intervals)==900 and counts['frozenDecisionDays']==11628,'All account intervals and prediction days')
    require(report['newAccounts']==48 and report['referenceAccountsReused']==12 and report['signalsGenerated'] is False
            and report['summarizerAccountExecutions']==report['summarizerModelFits']==0 and report['holdoutClaim'] is False
            and report['certified'] is False and report['targetAchieved'] is False,'Fixed development diagnostic scope')
    for path,value in report['inputPins'].items():m.bound(dict(path=path,sha256=value))
    receipt=dict(passed=True,verifiedAt=datetime.now(timezone.utc).isoformat(),planSha256=m.sha(directory/'plan.json'),
        comparisonSha256=m.sha(directory/'comparison.json'),validationSha256=m.sha(directory/'validation.json'),
        accountValidationSha256=m.sha(directory/'validation.json'),readoutSha256=m.sha(directory/'readout.json'),
        verifierSha256=m.sha(__file__),mainVerifierSha256=MAIN_SHA,inputCompletionSha256=m.sha(directory/'input-completion.json'),
        inputValidationSha256=m.sha(directory/'input-validation.json'),sourcePins={MAIN:MAIN_SHA,OLD:OLD_SHA,
            **m.DEPENDENCIES,Path(__file__).relative_to(ROOT).as_posix():m.sha(__file__)},inputPins=report['inputPins'],
        counts={**counts,'sourceImmediateOpportunities':counts['originalOpportunities']},
        **{field:report[field] for field in ('opportunityRowsArtifact','candidateEntryRowsArtifact','pairedPredictionRowsArtifact')},
        **{field+'Sha256':report[field]['sha256'] for field in ('opportunityRowsArtifact','candidateEntryRowsArtifact','pairedPredictionRowsArtifact')},
        checks=['60 independently audited account records, 96 signed contrasts and all 900 carried-equity intervals rechecked',
            '81550 original opportunity-policy dispositions and every actual candidate-entry episode reconstructed from original buy/sell sequences',
            '65240 paired prediction records use exact frozen dates/models and the same independently executed final-cutoff path label',
            '192 evaluations independently compute daily means then equal-day errors; all fitted/fallback/missing and path-maturity cells retained',
            'Closed-only evaluation never zero-fills unfilled/open/unresolved paths; final labels may mature after formation slice end',
            'No production model/summary import, no model fit, no source-bar replay and no account execution'],holdoutClaim=False,certified=False,targetAchieved=False)
    return m.write_proof(directory,'readout-validation.json',receipt)


def self_test():
    checks=[]
    def forecast(value,status='fitted'):
        return dict(status=status,prediction=value,allow=value>0 if value is not None else True,
                    reason='positivePrediction' if value is not None and value>0 else 'nonPositivePrediction' if value is not None else 'insufficientTrainingFormationDays')
    def row(day,y,left,right,ident):
        return dict(formationDate=day,formationId=ident,symbol=ident,pathStatus='closed',pathEntered=True,labelNetReturn=y,
                    control=forecast(left),candidate=forecast(right),fitCell='bothFitted')
    rows=[row('2024-09-23',.1,0.,.15,'a'),row('2024-09-23',.3,0.,.15,'b'),row('2024-09-24',-.2,0.,-.1,'c')]
    out=score_reference(rows)
    for value,expected in [(out['meanNetReturnPct'],0.),(out['control']['maePp'],20.),(out['control']['msePpSquared'],450.),
                           (out['candidate']['maePp'],10.),(out['candidate']['msePpSquared'],112.5),(out['candidate']['meanPredictionPct'],2.5)]:
        require(math.isclose(value,expected,abs_tol=1e-10),'Hand-calculated unequal-day forecast error')
    require(out['formationDayCount']==2 and out['weightSum']==2,'Date denominator')
    checks.append('Hand-computed 3-label / 2-day pp and squared-pp scores')
    for side in ('control','candidate'):
        doubled=score_reference(rows[:2]*2+rows[2:])
        require(math.isclose(doubled[side]['maePp'],out[side]['maePp'],abs_tol=1e-10),'Identical daily population duplication changed date weight')
    checks.append('Replicated same-day labels do not multiply the date weight')
    mixed=deepcopy(rows);mixed[1]['candidate']=forecast(None,'fallback')
    score=score_reference(mixed)
    require(score['control']['maePp']==out['control']['maePp'] and score['candidate']['maePp'] is None
            and score['candidate']['predictionCount']==2,'Missing forecasts incorrectly treated as zero')
    checks.append('Incomplete prediction population cannot fabricate a common score')
    extra=deepcopy(rows)
    for state in ('open','unfilled','unresolved'):
        r=deepcopy(rows[0]);r.update(pathStatus=state,labelNetReturn=None,pathEntered=state!='unfilled');extra.append(r)
    cell=cell_reference(extra)
    require(cell['opportunityCount']==6 and cell['score']==out and cell['immatureEnteredCount']==2,'Unknown outcomes became zero labels')
    checks.append('Open, unresolved and unfilled paths stay counted but unscored')
    require(score_reference([])['control']['maePp'] is None and score_reference([])['weightSum']==0,'Empty cells must be null not zero error')
    checks.append('Empty cells retain null errors and explicit zero denominator')
    dates=['2024-09-23','2024-09-24'];key=dict(period='2024-2025',board='main',exit='trail10')
    paths=[dict(formationId=r['formationId'],formationDate=r['formationDate'],symbol=r['symbol'],pathStatus='closed',pathEntered=True,
        labelNetReturn=r['labelNetReturn'],finalExitDate='2025-12-30',originalEntered=False,entryDate='2024-09-25',sourceCutoff='2025-12-31') for r in rows]
    daily={p:[dict(formationDate=d,asOfDate='2024-09-22' if d.endswith('23') else '2024-09-23',
        models={model:forecast(.05 if p=='allExecutable' else -.05) for model in MODELS}) for d in dates] for p in POPULATIONS}
    paired=paired_reference(key,paths,daily);evaluated=evaluation_reference(key,paired,dates)
    require(len(paired)==12 and len(evaluated)==16,'Four comparisons and complete cohort slices')
    pre=next(r for r in evaluated if r['contrastId']==PAIRS[0][0] and r['id']=='before20240924')
    require(pre['commonFitted']['score']['episodeCount']==2 and pre['commonFittedAllowCells']['01']['opportunityCount']==2,'Final labels after slice end lost or allow-cell mismatch')
    checks.append('Formation slicing permits later final maturity and retains discordant 01 cell')
    changed=deepcopy(daily);changed['allExecutable'][0]['models']['ridge']=forecast(None,'fallback')
    changed['originalEntered']=changed['originalEntered'][1:]
    paired2=paired_reference(key,paths,changed)
    one=[r for r in paired2 if r['contrastId']=='allVsOriginalRidge' and r['formationDate']==dates[0]]
    require(all(r['fitCell']=='neitherFitted' and r['control']['status']=='missing' and r['candidate']['status']=='fallback' for r in one),'Missing/fallback conflated with fitted')
    checks.append('Missing and fallback remain explicit without entering common fitted scores')
    return dict(passed=True,syntheticGroups=len(checks),checks=checks,realSourceReads=0,modelFits=0,accountExecutions=0)


if __name__=='__main__':
    parser=ArgumentParser();parser.add_argument('--self-test',action='store_true');parser.add_argument('--out',type=Path,default=DEFAULT);args=parser.parse_args()
    if args.self_test:print(json.dumps(self_test(),ensure_ascii=False,indent=2))
    else:
        directory=args.out.resolve()
        try:verify(directory)
        except Exception as error:
            m,_=load();folder=directory/'validation-attempts';folder.mkdir(exist_ok=True)
            with (folder/('readout-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')).open('x',encoding='utf-8') as stream:
                json.dump(dict(passed=False,at=datetime.now(timezone.utc).isoformat(),verifierSha256=m.sha(__file__),
                    type=type(error).__name__,message=str(error),traceback=traceback.format_exc()),stream,ensure_ascii=False,indent=2)
            raise
