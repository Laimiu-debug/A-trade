"""Independent post-account readout check; no summary/model implementation import."""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path
import traceback

ROOT=Path(__file__).resolve().parents[1]
MAIN='scripts/verify-online-feedback-entry.py'
MAIN_SHA='ec17b1a7114bd2ed6a9ba5757f26fccdbfeb3baa1cedcadf5993ec18a0a963d5'
DEFAULT=ROOT/'artifacts/strategy-search/online-feedback-entry-v1'


def load():
    spec=importlib.util.spec_from_file_location('_online_readout_main',ROOT/MAIN)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.require(m.sha(ROOT/MAIN)==MAIN_SHA,'Independent account auditor changed')
    return m


def distribution(values):
    values=list(values)
    known=sorted(x for x in values if type(x) in (int,float) and math.isfinite(x))
    result=dict(count=len(values),knownCount=len(known),unknownCount=len(values)-len(known),
                status='empty' if not values else 'complete' if len(known)==len(values) else 'numericUnknown')
    result.update({k:None for k in ('mean','min','p05','p25','median','p75','p95','max')})
    if not values or len(known)!=len(values):return result
    result.update(mean=math.fsum(known)/len(known),min=known[0],max=known[-1])
    for key,fraction in (('p05',.05),('p25',.25),('median',.5),('p75',.75),('p95',.95)):
        at=(len(known)-1)*fraction;i=int(at)
        result[key]=known[i]+(known[min(i+1,len(known)-1)]-known[i])*(at-i)
    return result


def labels(values):
    return dict(sorted(Counter('null' if x is None else str(x) for x in values).items()))


def metric_reference(summary,scheduled,original):
    a,c,r=summary['account'],summary['closedEpisodes'],summary['reconciliation']
    overall=summary['periods'][0]
    mapping={'accountReturnPct':'netReturnPct','maxDrawdownPct':'maxDrawdownPct','netPnlAmount':'netPnlAmount','finalEquity':'finalEquity',
             'buyFillCount':'buyFillCount','sellFillCount':'sellFillCount','feesPaid':'feesPaid','slippageAlreadyInFillPrices':'slippageAlreadyInFillPrices'}
    result={k:a[v] for k,v in mapping.items()}
    result.update(meanExposurePct=overall['exposurePct']['mean'],investedSessionPct=overall['investedSessionPct'],
                  signals=scheduled,originalOpportunities=original,notScheduled=original-scheduled,notEntered=scheduled-a['buyFillCount'])
    result.update({k:c[v] for k,v in {'closed':'count','closedMeanPct':'meanReturnPct','closedMedianPct':'medianReturnPct',
        'winRatePct':'winRatePct','meanWinPct':'meanWinReturnPct','meanLossPct':'meanLossReturnPct','payoffRatio':'payoffRatio',
        'profitFactor':'profitFactor','maxConsecutiveLosses':'maxConsecutiveLosses'}.items()})
    result.update(closedPnlAmount=r['closedEpisodePnl'],openRealizedPnlAmount=r['openPositionRealizedPnl'],
        openUnrealizedPnlAmount=r['openPositionUnrealizedPnl'],openPositions=summary['openPositions']['count'],
        openStaleMarks=sum(x['markStaleAtCutoff'] for x in summary['openPositions']['positions']),
        unreliableValuationDays=len(summary['quality']['valuationUnreliableDates']),
        top1ShareOfGrossProfitPct=summary['concentration']['top1ProfitSharePct'],top5ShareOfGrossProfitPct=summary['concentration']['top5ProfitSharePct'])
    return result


def episodes_reference(key,original,schedule,result,shadow_ids,m,episode_audit):
    episodes,_=episode_audit.reconstruct_episodes(result)
    opportunities={r['signalId']:r for r in result['research']['opportunities']}
    selected={r['signalId'] for r in schedule}
    m.require(set(opportunities)==selected,'Readout admission/opportunity identities')
    m.same(schedule,[r for r in original if r['signalId'] in selected],'Original-order selected schedule')
    details,entries=[],[]
    for signal in original:
        sid=signal['signalId']; ep=episodes.get(sid); opp=opportunities.get(sid)
        state='closed' if ep and ep['exitDate'] else 'openAtCutoff' if ep else 'notEntered'
        detail=None
        if opp:
            detail=dict(signalId=sid,signalDate=opp['signalDate'],entryDate=ep['entryDate'] if ep else None,
                exitDate=ep['exitDate'] if ep else None,positionState=state,status=opp['status'],reason=opp['reason'],
                filledQuantity=opp.get('filledQuantity'),attemptDate=opp.get('attemptDate'),
                closedReturnPct=ep['closedPnlAmount']/ep['investedAmount']*100 if state=='closed' else None,
                closedPnlAmount=ep['closedPnlAmount'] if state=='closed' else None)
            m.require((ep is not None)==(opp['filledQuantity']>0),'Entry identity and original opportunity')
        details.append({**key,'formationId':sid,'formationDate':signal['signalDate'],'symbol':signal['symbol'],
            'originalRank':signal['originalRank'],'scheduled':sid in selected,'shadowEverEntered':sid in shadow_ids,
            'accountDisposition':detail,'positionState':state if opp else 'notScheduled'})
        if ep:
            entries.append({**key,'formationId':sid,'formationDate':signal['signalDate'],'symbol':signal['symbol'],
                'entryDate':ep['entryDate'],'entryPrice':ep['entryPrice'],'entryQuantity':ep['entryQuantity'],
                'exitDate':ep['exitDate'],'positionState':state,'investedAmount':ep['investedAmount'],
                'shadowEverEntered':sid in shadow_ids,'candidateOnlyEntry':sid not in shadow_ids,
                'closedReturnPct':ep['closedPnlAmount']/ep['investedAmount']*100 if state=='closed' else None,
                'closedPnlAmount':ep['closedPnlAmount'],'openRealizedPnlAmount':ep['openRealizedPnlAmount'],
                'openUnrealizedPnlAmount':ep['openUnrealizedPnlAmount'],'holdingSessions':ep['holdingSessions'],
                'openMarkStale':ep['openMarkStale']})
    m.require(len(entries)==len(episodes),'All actual episodes assigned exactly once')
    return details,entries


def entry_group_reference(rows):
    closed=[r for r in rows if r['positionState']=='closed']; opened=[r for r in rows if r['positionState']=='openAtCutoff']
    amounts={k:math.fsum(r[k] for r in rows) for k in ('closedPnlAmount','openRealizedPnlAmount','openUnrealizedPnlAmount')}
    return dict(buyCount=len(rows),closedCount=len(closed),openCount=len(opened),**amounts,
        totalEpisodePnlAmount=math.fsum(amounts.values()),closedReturnPct=distribution(r['closedReturnPct'] for r in closed),
        investedAmountSum=math.fsum(r['investedAmount'] for r in rows),openStaleMarkCount=sum(r['openMarkStale'] for r in opened),
        meaning='Posthoc observed episode group; investment sums reuse capital and are not a portfolio denominator.')


def shift_reference(key,entries,summary,m):
    shared=[r for r in entries if r['shadowEverEntered']]; novel=[r for r in entries if not r['shadowEverEntered']]
    grouped={'allCandidateEntries':entry_group_reference(entries),'shadowAlsoEntered':entry_group_reference(shared),
             'candidateOnly':entry_group_reference(novel)}
    for field,saved in [('closedPnlAmount','closedEpisodePnl'),('openRealizedPnlAmount','openPositionRealizedPnl'),
                        ('openUnrealizedPnlAmount','openPositionUnrealizedPnl')]:
        m.near(grouped['allCandidateEntries'][field],summary['reconciliation'][saved],'All entries money bridge')
        m.near(grouped['shadowAlsoEntered'][field]+grouped['candidateOnly'][field],summary['reconciliation'][saved],'Candidate sample-shift money partition')
    return {**key,'candidateOnlyBuyCount':len(novel),'actualBuyCount':len(entries),'candidateOnlyBuyPct':len(novel)/len(entries)*100 if entries else None,
        **grouped,'episodePnlBridge':summary['reconciliation']['episodePnlBridge'],
        'meaning':'Retrospective sample shift, not model input, a causal effect, or a rerun deleting baseline trades.'}


def feedback_reference(key,decisions,original,schedules,m):
    by_day={r['formationDate']:r for r in decisions}; output=[]
    for mode in m.MODES:
        model=[r['models'][mode] for r in decisions]
        selected=[r for r in original if by_day[r['signalDate']]['models'][mode]['allow']]
        m.same(schedules[mode],selected,'Admitted schedule for coverage')
        output.append({**key,'candidate':mode,'targetDayCount':len(decisions),
            'statusCounts':labels(r['status'] for r in model),'reasonCounts':labels(r['reason'] for r in model),
            'allowDayCount':sum(r['allow'] for r in model),'rejectDayCount':sum(not r['allow'] for r in model),
            'originalOpportunities':len(original),'scheduledSignals':len(selected),'notScheduledSignals':len(original)-len(selected),
            'fittedOriginalOpportunities':sum(by_day[r['signalDate']]['models'][mode]['status']=='fitted' for r in original),
            'fallbackOriginalOpportunities':sum(by_day[r['signalDate']]['models'][mode]['status']=='fallback' for r in original),
            'predictionWhenFitted':distribution(r['prediction'] for r in model if r['status']=='fitted'),
            'eligibleEpisodesPerDay':distribution(r['support']['eligibleEpisodeCount'] for r in decisions),
            'eligibleFormationDaysPerDay':distribution(r['support']['eligibleFormationDayCount'] for r in decisions),
            'labelXUnknownEpisodeDayObservations':sum(r['support']['labelXUnknownCount'] for r in decisions),
            'uniqueLabelXUnknownFormationIds':len({x['formationId'] for r in decisions for x in r['labelXUnknown']}),
            'immatureEpisodeDayObservations':sum(r['support']['immatureCount'] for r in decisions),
            'immatureAgeSlots':distribution(x['ageSlots'] for r in decisions for x in r['immature']),
            'currentFeatureStatusCounts':labels(r['currentFeatureStatus'] for r in decisions),
            'meaning':'Day counts use all source target sessions; repeated label/maturity views are episode-day observations, not independent trades.'})
    return output


def intervals_reference(key,result,summary,original,schedule,decisions,m,phases):
    measured=[]
    for saved in summary['periods'][1:]:
        first,last=saved['requestedStart'],saved['requestedEnd']
        fills=[r for r in result['trades'] if first<=r['date']<=last]
        formed=[r for r in original if first<=r['signalDate']<=last]
        admitted=[r for r in schedule if first<=r['signalDate']<=last]
        selected=[r for r in decisions if first<=r['formationDate']<=last]
        curve=[r for r in result['curve'] if first<=r['date']<=last]
        before=[r for r in result['curve'] if r['date']<first]
        entering=before[-1]['equity'] if before else result['config']['initialCapital']
        peak=entering; dd=0.
        for r in curve:
            peak=max(peak,r['equity']);dd=max(dd,(peak-r['equity'])/peak*100)
        m.near(saved['enteringEquity'],entering,'Interval previous actual close')
        m.near(saved['netPnlAmount'],curve[-1]['equity']-entering,'Interval carried-equity PnL')
        m.near(saved['returnPct'],(curve[-1]['equity']/entering-1)*100,'Interval actual-base return')
        m.near(saved['maxDrawdownPct'],dd,'Interval path drawdown')
        item={**key,**deepcopy(saved),'closedEpisodes':{k:v for k,v in saved['closedEpisodes'].items() if k!='episodes'},
            'feesPaid':math.fsum(r['fee'] for r in fills),'slippageAlreadyInFillPrices':math.fsum(r['slippage'] for r in fills),
            'buyFillCount':sum(r['side']=='buy' for r in fills),'sellFillCount':sum(r['side']=='sell' for r in fills),
            'sellFillReasons':labels(r.get('reason') for r in fills if r['side']=='sell'),
            'originalFormations':len(formed),'scheduledFromFormations':len(admitted),'notScheduledFromFormations':len(formed)-len(admitted),
            'attributionMeaning':'Continuous actual equity and positions; closed episodes assigned by final exitDate, entry permission counts by original formationDate.'}
        item['modelStatusCounts']=labels(r['models'][key['candidate']]['status'] for r in selected) if key['candidate']!='baseline' else {}
        item['modelReasonCounts']=labels(r['models'][key['candidate']]['reason'] for r in selected) if key['candidate']!='baseline' else {}
        if item['kind']=='retrospectiveNarrativePhase':item['phaseTitle']=phases[item['id']]['title']
        measured.append(item)
    partitions=[]
    for kind in ('calendarYear','calendarHalfYear','retrospectiveNarrativePhase','descriptiveEpoch'):
        part=sorted([r for r in measured if r['kind']==kind],key=lambda r:r['requestedStart'])
        if not part:continue
        covered=Counter(x['date'] for r in part for x in result['curve'] if r['requestedStart']<=x['date']<=r['requestedEnd'])
        m.same(dict(covered),dict(Counter(r['date'] for r in result['curve'])),'Exact full interval calendar partition')
        for field,target in [('netPnlAmount',summary['account']['netPnlAmount']),('feesPaid',summary['account']['feesPaid'])]:
            m.near(math.fsum(r[field] for r in part),target,'Full account partition '+field)
        m.require(sum(r['closedEpisodes']['count'] for r in part)==summary['closedEpisodes']['count'],'Closed episodes final-exit partition')
        for previous,current in zip(part,part[1:]):m.near(previous['endingEquity'],current['enteringEquity'],'Continuous carried capital')
        m.near((math.prod(1+r['returnPct']/100 for r in part)-1)*100,summary['account']['netReturnPct'],'Compounded continuous account return')
        partitions.append(dict(kind=kind,intervalCount=len(part),passed=True))
    return measured,{**key,'passed':True,'checks':partitions}


def comparisons_reference(accounts,m):
    indexed={(r['period'],r['board'],r['exit'],r['candidate']):r['metrics'] for r in accounts}; output=[]
    m.require(len(indexed)==len(accounts)==36,'Complete distinct account matrix')
    for p in m.PERIODS:
        for b in m.BOARDS:
            for e in m.EXITS:
                for name,left,right in [('interceptVsBaseline','baseline','intercept'),('ridgeVsIntercept','intercept','ridge'),('ridgeVsBaseline','baseline','ridge')]:
                    a,c=indexed[p,b,e,left],indexed[p,b,e,right]
                    output.append(dict(period=p,board=b,exit=e,id=name,control=left,candidate=right,controlMetrics=a,candidateMetrics=c,
                        candidateMinusControl={k:c[k]-a[k] if m.finite(c[k]) and m.finite(a[k]) else None for k in sorted(a)},
                        meaning='Separately replayed complete accounts; includes admission, capital and exposure paths, not a filtered baseline bill.'))
    return output


def verify(directory):
    m=load(); plan=m.registered_plan(directory); completion,groups=m.frozen_groups(directory,plan)
    proof=m.read(directory/'validation.json'); report=m.read(directory/'readout.json'); comparison=m.read(directory/'comparison.json')
    m.require(proof['passed'] is True and proof['verifierSha256']==MAIN_SHA and proof['planSha256']==m.sha(directory/'plan.json')
              and proof['comparisonSha256']==m.sha(directory/'comparison.json')
              and proof['inputCompletionSha256']==m.sha(directory/'input-completion.json')
              and proof['inputValidationSha256']==m.sha(directory/'input-validation.json'),'Exact original account/input proof')
    m.require(report['verification']['passed'] is True and m.bound(report['verification'])==directory/'validation.json'
              and report['planSha256']==proof['planSha256'] and report['comparisonSha256']==proof['comparisonSha256'],'Readout validation/plan bindings')
    m.same([r['artifactRefs'] for r in proof['accounts']],comparison['rows'],'Readout consumes exactly audited account files')
    for path,h in report['inputPins'].items():m.bound(dict(path=path,sha256=h))
    episode_audit=m.independent('scripts/verify-reclaim-exit-mechanism.py')
    phases={r['id']:r for r in m.read(m.bound(plan['atlas']))['phases']}
    accounts=[]; flat=[]; interval_rows=[];coverage=[];feedback=[];daily=[];shifts=[];partitions=[];details=[];entries=[]
    descriptors={(r['period'],r['board'],r['exit'],r['candidate']):r for r in comparison['rows']}
    for g in groups:
        p,b,e=g['period'],g['board'],g['exit'];key=dict(period=p,board=b,exit=e)
        original=m.read(m.bound(g['baselineSchedule'])); decisions=m.records(m.bound(g['decisions']))
        shadow={r['formationId'] for r in m.read(m.bound(g['shadow']))}
        schedules={mode:m.read(m.bound(g['candidates'][mode])) for mode in m.MODES}
        feedback.extend(feedback_reference(key,decisions,original,schedules,m))
        daily.extend({**key,**{k:r[k] for k in ('formationDate','asOfDate','currentFeatures','currentFeatureStatus','currentFeatureReasons','support','models')}} for r in decisions)
        for mode in ('baseline',*m.MODES):
            account_key={**key,'candidate':mode};descriptor=descriptors[p,b,e,mode]
            result=m.read(m.bound(descriptor['result']));summary=m.read(m.bound(descriptor['summary']))
            schedule=original if mode=='baseline' else schedules[mode]
            m.same(m.read(m.bound(descriptor['schedule'])),schedule,'Readout schedule source')
            m.require(summary['inputResultSha256']==m.digest(result),'Readout summary/result object binding')
            disposition,actual_entries=episodes_reference(account_key,original,schedule,result,shadow,m,episode_audit)
            details.extend(disposition)
            if mode=='baseline':m.require({r['formationId'] for r in actual_entries}==shadow,'Fixed shadow entry population')
            else:
                entries.extend(actual_entries);shifts.append(shift_reference(account_key,actual_entries,summary,m))
            metric=metric_reference(summary,len(schedule),len(original))
            flat.append({**account_key,'reusedAccount':mode=='baseline','metrics':metric})
            accounts.append({**account_key,'artifacts':descriptor,'metrics':metric,
                **{k:summary[k] for k in ('account','closedEpisodes','concentration','openPositions','rights','unresolvedExits','reconciliation','quality','warnings')},
                'sourceQualityScalarFlags':{k:v for k,v in summary['sourceQuality'].items() if not isinstance(v,(dict,list))},
                'fullSourceQuality':{'summaryRef':descriptor['summary'],'field':'sourceQuality'}})
            states=Counter(r['positionState'] for r in disposition)
            coverage.append({**account_key,'originalOpportunities':len(original),'scheduledSignals':len(schedule),'notScheduled':states['notScheduled'],
                'entered':len(actual_entries),'closed':states['closed'],'openAtCutoff':states['openAtCutoff'],'notEntered':states['notEntered'],
                'accountOpportunityReasons':labels(r['accountDisposition']['reason'] for r in disposition if r['scheduled']),
                'valuationUnreliableDays':metric['unreliableValuationDays']})
            measured,partition=intervals_reference(account_key,result,summary,original,schedule,decisions,m,phases)
            interval_rows.extend(measured);partitions.append(partition)
    expected={'accounts':accounts,'accountRows':flat,'comparisons':comparisons_reference(flat,m),'coverageRows':coverage,
        'feedbackCoverageRows':feedback,'feedbackDailyRows':daily,'sampleShiftRows':shifts,'partitionReconciliation':partitions}
    for name,kind in [('yearRows','calendarYear'),('halfYearRows','calendarHalfYear'),('phaseRows','retrospectiveNarrativePhase'),('curveEpochRows','descriptiveEpoch')]:
        expected[name]=[r for r in interval_rows if r['kind']==kind]
    for name,value in expected.items():m.same(report[name],value,'Entire readout table '+name)
    m.same(m.records(m.bound(report['opportunityRowsArtifact'])),details,'All original opportunity-policy dispositions')
    m.same(m.records(m.bound(report['candidateEntryRowsArtifact'])),entries,'All actual candidate-entry episodes')
    m.require(len(details)==48930 and len({tuple(r[k] for k in ('period','board','exit','candidate','formationId')) for r in details})==48930,'Unique complete original opportunity matrix')
    counts=dict(accounts=36,comparisons=36,yearRows=72,halfYearRows=144,phaseRows=288,curveEpochRows=36,coverageRows=36,
        feedbackCoverageRows=24,sampleShiftRows=24,originalOpportunities=8155,sourceExitOpportunities=16310,
        policyDispositions=len(details),actualCandidateEntryEpisodes=len(entries),feedbackDecisionDays=len(daily))
    m.same(report['counts'],counts,'Complete report counts')
    m.require(len(daily)==5814 and len(interval_rows)==540 and report['newAccounts']==24 and report['referenceAccountsReused']==12
              and report['holdoutClaim'] is False and report['certified'] is False,'Readout scope and limits')
    for name in ('opportunityRowsArtifact','candidateEntryRowsArtifact'):
        expected_count=len(details) if name=='opportunityRowsArtifact' else len(entries)
        m.require(report[name]['count']==expected_count and report[name]['format']=='jsonl+gzip'
                  and report[name]['identity']==['period','board','exit','candidate','formationId'],'Sidecar identity/count descriptor')
    for path,h in report['inputPins'].items():m.bound(dict(path=path,sha256=h))
    proof=dict(passed=True,verifiedAt=datetime.now(timezone.utc).isoformat(),planSha256=m.sha(directory/'plan.json'),
        comparisonSha256=m.sha(directory/'comparison.json'),accountValidationSha256=m.sha(directory/'validation.json'),
        readoutSha256=m.sha(directory/'readout.json'),verifierSha256=m.sha(__file__),mainVerifierSha256=MAIN_SHA,
        inputCompletionSha256=m.sha(directory/'input-completion.json'),inputValidationSha256=m.sha(directory/'input-validation.json'),
        opportunityRowsArtifact=report['opportunityRowsArtifact'],candidateEntryRowsArtifact=report['candidateEntryRowsArtifact'],
        opportunityRowsArtifactSha256=report['opportunityRowsArtifact']['sha256'],
        candidateEntryRowsArtifactSha256=report['candidateEntryRowsArtifact']['sha256'],
        sourcePins={MAIN:MAIN_SHA,'scripts/verify-reclaim-exit-mechanism.py':m.DEPENDENCIES['scripts/verify-reclaim-exit-mechanism.py'],
                    Path(__file__).relative_to(ROOT).as_posix():m.sha(__file__)},inputPins=report['inputPins'],
        counts={**counts,'newAccounts':24,'reusedAccounts':12,'sourceImmediateOpportunities':8155},
        checks=['All 36 account tables and 36 signed comparisons match exact independently audited summaries',
            'All 540 continuous intervals and full-calendar/PnL/fees/closed-episode partitions independently reconciled',
            'All 48930 original opportunities retained; full raw-fill episode reconstruction assigns each actual buy exactly once',
            'Every candidate-only entry retained with separate closed/open money; all shift groups reconcile to original account amounts',
            'All 5814 day decisions and 24 feedback coverage/fallback/maturity distributions independently summarized'],
        scope='No production summary/model imports, no source bars or account execution; prior full account arithmetic is inherited under exact hashes.',
        holdoutClaim=False,certified=False,targetAchieved=False)
    return m.write_proof(directory,'readout-validation.json',proof)


def self_test():
    m=load();checks=[]
    x=distribution([1.,2.,3.,4.]);m.near(x['median'],2.5,'Median');m.near(x['p05'],1.15,'Interpolated tail')
    m.require(distribution([1.,None])['mean'] is None and distribution([])['status']=='empty','Unknown distribution')
    checks.append('Independent sorted-interpolation distribution and unknown/empty semantics')
    rows=[dict(positionState='closed',closedPnlAmount=10.,openRealizedPnlAmount=0.,openUnrealizedPnlAmount=0.,closedReturnPct=1.,investedAmount=1000.,openMarkStale=False),
          dict(positionState='openAtCutoff',closedPnlAmount=0.,openRealizedPnlAmount=-2.,openUnrealizedPnlAmount=5.,closedReturnPct=None,investedAmount=200.,openMarkStale=True)]
    group=entry_group_reference(rows)
    m.require(group['totalEpisodePnlAmount']==13. and group['closedReturnPct']['count']==1 and group['openStaleMarkCount']==1,'Closed/open cash and labels')
    checks.append('Open realized/unrealized remains separate and never creates a closed zero return')
    episode_audit=m.independent('scripts/verify-reclaim-exit-mechanism.py')
    raw=episode_audit.synthetic_fixture()
    original=[dict(signalId=sid,signalDate=day,symbol='s',originalRank=1)
              for sid,day in [('first','2024-01-02'),('skipped','2024-01-04'),('second','2024-01-05')]]
    schedule=[original[0],original[2]]
    raw['research']={'opportunities':[dict(signalId=r['signalId'],signalDate=r['signalDate'],symbol='s',
        status='filled',reason='filled',filledQuantity=quantity,attemptDate=day)
        for r,quantity,day in [(original[0],100,'2024-01-03'),(original[2],50,'2024-01-06')]]}
    frozen=m.digest(raw)
    detail,entry=episodes_reference(dict(period='synthetic',board='synthetic',exit='synthetic',candidate='ridge'),
                                   original,schedule,raw,{'first'},m,episode_audit)
    m.require(m.digest(raw)==frozen and len(detail)==3 and len(entry)==2 and detail[1]['positionState']=='notScheduled',
              'Source preserved and rejected opportunity retained')
    m.near(entry[0]['closedPnlAmount'],16.,'Partial sales belong to first buy despite null sell signal IDs')
    m.near(entry[1]['openUnrealizedPnlAmount'],48.,'Reentry terminal mark')
    m.require(entry[1]['candidateOnlyEntry'] and entry[1]['openMarkStale'] and entry[1]['closedReturnPct'] is None,
              'New entry and stale unclosed episode retain correct denominators')
    checks.append('Raw partial sells without signal IDs, same-symbol reentry and rejected original signals map once to original formations')
    empty=entry_group_reference([]);m.require(empty['buyCount']==0 and empty['totalEpisodePnlAmount']==0 and empty['closedReturnPct']['mean'] is None,'Empty entry group')
    checks.append('Empty groups preserve null distribution and zero amounts')
    calendar,targets,environment,shadow=m.synthetic_fixture(); built=m.reference_build(calendar,targets,environment,shadow)
    original=[dict(signalId=f's:{i}',signalDate=d) for i,d in enumerate(targets)]
    by_day={r['formationDate']:r for r in built['dailyRows']}
    schedules={mode:[s for s in original if by_day[s['signalDate']]['models'][mode]['allow']] for mode in m.MODES}
    coverage=feedback_reference(dict(period='synthetic',board='synthetic',exit='synthetic'),built['dailyRows'],original,schedules,m)
    m.require(len(coverage)==2 and all(r['fittedOriginalOpportunities']+r['fallbackOriginalOpportunities']==7 for r in coverage),'Coverage partition')
    m.require(all(r['immatureEpisodeDayObservations']>r['uniqueLabelXUnknownFormationIds'] for r in coverage),'Maturity repeated-view denominator')
    checks.append('Feedback coverage distinguishes opportunities, calendar days and repeated immature episode-days')
    flat=[dict(period=p,board=b,exit=e,candidate=c,metrics={'return':float(i),'unknown':None})
          for p in m.PERIODS for b in m.BOARDS for e in m.EXITS for i,c in enumerate(('baseline','intercept','ridge'))]
    comparisons=comparisons_reference(flat,m)
    m.require(len(comparisons)==36 and comparisons[0]['candidateMinusControl']['return']==1.
              and comparisons[1]['candidateMinusControl']['return']==1. and comparisons[2]['candidateMinusControl']['return']==2.
              and all(r['candidateMinusControl']['unknown'] is None for r in comparisons),'Signed contrasts')
    checks.append('Three declared signed contrasts for each of twelve groups; null metrics are not zero')
    return dict(passed=True,syntheticGroups=len(checks),checks=checks,realSourceReads=0,accountRuns=0)


if __name__=='__main__':
    parser=ArgumentParser();parser.add_argument('--self-test',action='store_true');parser.add_argument('--out',type=Path,default=DEFAULT)
    args=parser.parse_args()
    if args.self_test:print(json.dumps(self_test(),ensure_ascii=False,indent=2))
    else:
        try:verify(args.out.resolve())
        except Exception as error:
            folder=args.out/'readout-validation-attempts';folder.mkdir(exist_ok=True)
            with (folder/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')).open('x',encoding='utf-8') as stream:
                json.dump(dict(passed=False,message=str(error),type=type(error).__name__,traceback=traceback.format_exc()),stream,ensure_ascii=False,indent=2)
            raise
