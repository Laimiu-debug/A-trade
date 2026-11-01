"""Independent feedback readout audit. Never imports the new summarizer."""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.util
import json
import math
from pathlib import Path
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/strength-feedback-v1'
MAIN = 'scripts/verify-strength-feedback.py'
MAIN_SHA = '9569d82ddacff78fed2e92dcc2cc45814cf9a8be9bfef6d627ef531fdc38cb91'
CONTEXT = 'scripts/verify-reclaim-context-diagnostic.py'
CONTEXT_SHA = '9b899e4c84ca90d2d26be4ef3a2ae2910029fa726441e23bed7ccbf6f48c67c9'
BOARD = 'scripts/verify-board-coverage-readout.py'
BOARD_SHA = '376ef50af09e1024e6ca578acc6dda5d4a5d39b9b67c3057eb67b330fe158650'
ATLAS = 'artifacts/strategy-search/market-phase-atlas-v1/atlas.json'
ATLAS_SHA = 'ddf55f2741dca3793923e5da782690aad500510848077f43492fff41af1a44d8'
POLICIES = ('baseline','known','positive')
CONTRASTS = (('knownVsBaseline','baseline','known'),('positiveVsKnown','known','positive'),('positiveVsBaseline','baseline','positive'))


def load():
    spec = importlib.util.spec_from_file_location('_feedback_readout_independent', ROOT/MAIN)
    audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)
    audit.require(audit.sha(ROOT/MAIN)==MAIN_SHA,'Frozen main auditor changed')
    return audit, audit.reference(CONTEXT,CONTEXT_SHA), audit.reference(BOARD,BOARD_SHA)


def policy_decision(row, policy):
    return {'status':'included','reason':'reusedOriginalControl'} if policy=='baseline' else row['decisions'][policy]


def schedule_reference(original, policy, audit):
    fields = ('signalId','strategyId','symbol','signalDate','originalRank')
    values = []
    for row in original:
        audit.require(row['formationId']==row['signalId'] and row['strategyId']==audit.SID and row['sourceStrategyId']==audit.BASELINE_SID,
                      'Source/new identity changed')
        if policy=='baseline':
            values.append({**{k:row[k] for k in fields},'strategyId':audit.BASELINE_SID})
        elif policy_decision(row,policy)['status']=='included':
            expected={k:row[k] for k in fields}
            audit.same(row['signals'][policy],expected,'Only strategy identity may differ from source')
            values.append(expected)
        else:
            audit.require(row['signals'][policy] is None,'Excluded signal unexpectedly scheduled')
    audit.require(values==sorted(values,key=lambda r:(r['signalDate'],r['originalRank'],r['symbol'],r['signalId'])), 'Original order changed')
    return values


def formation_reference(original,result,policy,start,end,audit,ctx):
    actual=result['research']['opportunities'];by_id={r['signalId']:r for r in actual}
    schedule=schedule_reference(original,policy,audit)
    audit.same(result['research']['identity']['signalSchedule'],schedule,'Whole schedule')
    audit.require(len(by_id)==len(actual) and set(by_id)=={r['signalId'] for r in schedule},'Complete account population')
    formed=[r for r in original if start<=r['formationDate']<=end]
    admitted=[by_id[r['formationId']] for r in formed if r['formationId'] in by_id]
    entered=sum(audit.finite(r.get('filledQuantity')) and r['filledQuantity']>0 for r in admitted)
    statuses=Counter('scheduled' if r['formationId'] in by_id else policy_decision(r,policy)['status'] for r in formed)
    return {'formationTop5':len(formed),'originalControlOpportunities':len(formed),'feedbackStates':ctx.labels(r['state'] for r in formed),
        'policyDispositionCounts':{k:statuses[k] for k in ('scheduled','excluded','unknown')},
        'gateReasonCounts':ctx.labels(policy_decision(r,policy)['reason'] for r in formed),
        'selectedSignalsFromFormation':len(admitted),'notScheduledFromFormation':len(formed)-len(admitted),
        'enteredFromFormation':entered,'notEnteredFromFormation':len(admitted)-entered,
        'uniqueFormationDays':len({r['formationDate'] for r in formed}),'uniqueSymbols':len({r['symbol'] for r in formed}),
        'terminalStatusCounts':ctx.labels(r.get('status','missing') for r in admitted),
        'terminalReasonCounts':ctx.labels(r.get('reason','missing') for r in admitted),
        'meaning':'Original parent control opportunity cohort at A; F=A-5 is feedback cohort cutoff. Terminal dispositions are measured at account cutoff, not interval end.'}


def interval_reference(result,original,policy,start,end,audit,ctx):
    measured=ctx.curve_interval(result,start,end)
    exits=[r for r in result['decisions'] if start<=r['date']<=end and r.get('action')=='sell' and r.get('status')=='queued']
    measured['queuedExitReasons']=ctx.labels(r.get('reason','missing') for r in exits)
    measured['queuedExitDecisionCount']=len(exits)
    measured['queuedExitDefinition']='action=sell AND status=queued; queued decisions, partial fills and closed episodes differ'
    measured['opportunitiesByFormationDate']=formation_reference(original,result,policy,start,end,audit,ctx)
    return measured


def dispositions_reference(original,result,summary,policy,audit,board):
    rows=board.disposition_reference(result,audit)
    raw={r['signalId']:r for r in result['research']['opportunities']}
    opened={(r['record']['symbol'],r['record']['entryDate']):r for r in summary['openPositions']['positions']}
    audit.require(len(opened)==summary['openPositions']['count'],'Open identity collision')
    selected={r['signalId']:r for r in schedule_reference(original,policy,audit)}
    answer={}
    for formed in original:
        sid=formed['formationId'];signal=selected.get(sid)
        audit.require((sid in rows)==(signal is not None),'Schedule disposition mismatch')
        if signal is None:
            d={'signalId':sid,'signalDate':None,'entryDate':None,'exitDate':None,'positionState':'notScheduled','status':'notScheduled',
               'reason':policy_decision(formed,policy)['reason'],'admissionStatus':policy_decision(formed,policy)['status'],
               'filledQuantity':0,'attemptDate':None,'closedReturnPct':None,'closedPnlAmount':None,'accountOpportunity':None,'openPositionAtCutoff':None}
        else:
            d=deepcopy(rows[sid]);position=opened.get((formed['symbol'],d['entryDate']))
            audit.require((position is not None)==(d['positionState']=='openAtCutoff'),'Open binding differs')
            d.update(admissionStatus='included',accountOpportunity=raw[sid],openPositionAtCutoff=position)
        answer[sid]={'formationId':sid,'formationDate':formed['formationDate'],'cohortDate':formed['cohortDate'],
            'feedbackState':formed['state'],'strategyId':audit.BASELINE_SID if policy=='baseline' else audit.SID,
            'plannedNextEntryDate':formed.get('plannedNextEntryDate'),'nextEntryWithinPeriod':formed.get('nextEntryWithinPeriod'),
            'decisionDate':signal['signalDate'] if signal else None,**d}
    return answer


def add_counts(rows):
    result={}
    for row in rows:
        for key,value in row.items():
            if type(value) is not int or value<0: raise ValueError('Nonnegative integer counts required')
            result[key]=result.get(key,0)+value
    return result


def feedback_interval_reference(daily,start,end,ctx):
    selected=[r for r in daily if start<=r['formationDate']<=end]
    return {'days':len(selected),'dayStateCounts':ctx.labels(r['state'] for r in selected),
        'memberObservations':sum(r['cohortSize'] for r in selected),
        'memberFeedbackCounts':add_counts(r['feedbackCounts'] for r in selected),
        'knownDecisionWithUnknownMembersDays':sum(r['state']!='unknown' and r['feedbackCounts']['unknown']>0 for r in selected)}


def feedback_reference(folder,receipt,original,daily,metadata,audit,ctx):
    audit.require([r['formationDate'] for r in daily]==metadata['targetDates'],'Every target session retained')
    by_day={r['formationDate']:r for r in daily};seen=[];observations=0
    with gzip.open(folder/'member-evidence.jsonl.gz','rt',encoding='utf-8') as stream:
        for line in stream:
            group=json.loads(line);day=group['formationDate'];row=by_day[day];members=group['members']
            audit.require(day not in seen and group['memberDigest']==row['memberDigest']==audit.digest(members),'Frozen cohort digest')
            audit.require(group['cohortDate']==row['cohortDate'] and group['rankPopulation']==row['rankPopulation']
                          and group['cohortSize']==row['cohortSize']==len(members),'Full member coverage')
            audit.require([r['symbol'] for r in members]==sorted({r['symbol'] for r in members}),'Frozen member order/uniqueness')
            statuses=Counter()
            for member in members:
                value=member['returnSinceCohort']
                state='unknown' if value is None else 'positive' if value>0 else 'negative' if value<0 else 'flat'
                audit.require(member['status']==state and (value is None or audit.finite(value)),'Member feedback sign')
                statuses[state]+=1
            counts={k:statuses[k] for k in ('positive','flat','negative','unknown')}
            audit.same(row['feedbackCounts'],counts,'Full fixed denominator')
            size=len(members);low=counts['positive'];high=low+counts['unknown']
            verdict='unknown' if not size else 'pass' if low>size/2 else 'fail' if high<=size/2 else 'unknown'
            audit.require(row['state']==verdict,'Majority with unknown bounds')
            audit.same(row['positiveFractionLower'],low/size if size else None,'Lower bound')
            audit.same(row['positiveFractionUpper'],high/size if size else None,'Upper bound')
            seen.append(day);observations+=size
    audit.require(seen==metadata['targetDates'],'All member records retained')
    states=Counter(r['state'] for r in daily);source=receipt['counts']
    audit.require(source['targetDayCount']==len(daily) and source['inputSignalCount']==source['opportunityCount']==len(original),'Day/formation denominators')
    audit.same(source['dayStates'],{k:states[k] for k in ('pass','fail','unknown')},'All day states')
    for formed in original:
        audit.same({k:formed[k] for k in ('cohortDate','state','cohortSize','feedbackCounts','memberDigest')},
                   {k:by_day[formed['formationDate']][k] for k in ('cohortDate','state','cohortSize','feedbackCounts','memberDigest')},'Opportunity/day binding')
    return {'passed':True,'counts':source,'targetDayCount':len(daily),'fullMemberRecords':len(seen),'memberObservations':observations,
        'dayStateCounts':{k:states[k] for k in ('pass','fail','unknown')},'dayReasonCounts':ctx.labels(r['reason'] for r in daily),
        'memberFeedbackCounts':add_counts(r['feedbackCounts'] for r in daily),
        'knownDecisionWithUnknownMembersDays':sum(r['state']!='unknown' and r['feedbackCounts']['unknown']>0 for r in daily),
        'completeNonemptyCohortDays':sum(r['cohortSize']>0 and r['feedbackCounts']['unknown']==0 for r in daily),
        'emptyCohortDays':sum(r['cohortSize']==0 for r in daily),
        'rankPopulation':ctx.describe(r['rankPopulation'] for r in daily),'cohortSize':ctx.describe(r['cohortSize'] for r in daily),
        'rankUnknownReasonCounts':add_counts(r['rankReasonCounts'] for r in daily),
        'opportunityStateCounts':ctx.labels(r['state'] for r in original),'uniqueOpportunityDays':len({r['formationDate'] for r in original}),
        'opportunityDayStateCounts':ctx.labels(by_day[d]['state'] for d in sorted({r['formationDate'] for r in original})),
        'basis':metadata['basis'],
        'evidenceArtifacts':{name:{'path':ref['path'],'sha256':ref['sha256']} for name,ref in receipt['files'].items() if name.endswith('.jsonl.gz')},
        'meaning':'All target days and all F-frozen member observations, including unknown members. Repeated windows and multiple signals on a day are dependent; these are not independent outcome samples.'}


def metric_reference(account, audit):
    fields = dict(account['comparisonRow'])
    book, curve, bridge = account['account'], account['curveAttribution'], account['reconciliation']
    fields.update(netPnlAmount=book['netPnlAmount'], finalEquity=book['finalEquity'],
        closedPnlAmount=bridge['closedEpisodePnl'], openRealizedPnlAmount=bridge['openPositionRealizedPnl'],
        openUnrealizedPnlAmount=bridge['openPositionUnrealizedPnl'], investedSessionPct=curve['investedSessionPct'],
        feesPaid=curve['feesPaid'], slippageAlreadyInFillPrices=curve['slippageAlreadyInFillPrices'],
        buyFillCount=curve['buyFillCount'], sellFillCount=curve['sellFillCount'],
        notEntered=curve['opportunitiesByDecisionDate']['notEntered'],
        notScheduled=curve['opportunitiesByFormationDate']['notScheduledFromFormation'],
        top1ShareOfGrossProfitPct=account['concentration']['top1ProfitSharePct'],
        top5ShareOfGrossProfitPct=account['concentration']['top5ProfitSharePct'])
    return {key: value for key, value in fields.items() if value is None or audit.finite(value)}


def contrasts_reference(accounts,audit):
    metrics={(r['period'],r['board'],r['policy']):metric_reference(r,audit) for r in accounts};answer=[]
    for p in audit.PERIODS:
        for b in audit.BOARDS:
            for name,control,candidate in CONTRASTS:
                a,z=metrics[(p,b,control)],metrics[(p,b,candidate)]
                audit.require(a.keys()==z.keys(),'Full metric contrast coverage')
                answer.append({'period':p,'board':b,'id':name,'control':control,'candidate':candidate,'controlMetrics':a,'candidateMetrics':z,
                    'candidateMinusControl':{k:z[k]-a[k] if audit.finite(a[k]) and audit.finite(z[k]) else None for k in sorted(a)},
                    'meaning':'Whole-account policy difference includes feedback availability, state admission, exposure and capital paths; not a causal return estimate.'})
    return answer


def verify(directory,audit,ctx,board_ref):
    require,same,read=audit.require,audit.same,audit.read
    require(not (directory/'readout-validation.json').exists(),'Never overwrite independent readout proof')
    plan=audit.registered_plan(directory)
    audit.inherited_sources(plan)
    completion,receipts=audit.input_receipts(directory,plan)
    proof,report,comparison=(read(directory/name) for name in ('validation.json','readout.json','comparison.json'))
    require(proof['passed'] is True and proof['verifierSha256']==audit.sha(ROOT/MAIN)==plan['sourcePins'][MAIN]
        and proof['planSha256']==report['planSha256']==audit.sha(directory/'plan.json')
        and proof['comparisonSha256']==audit.sha(directory/'comparison.json')
        and proof['inputValidationSha256']==audit.sha(directory/'input-validation.json')
        and proof['inputCompletionSha256']==audit.sha(directory/'input-completion.json'), 'Independent account proof chain stale')
    require(report['schema']=='strength-feedback-readout-v1' and report['verification']['passed'] is True
        and audit.pinned(report['verification'])==(directory/'validation.json').resolve(), 'Readout proof binding differs')
    for name,expected in report['inputPins'].items():require(audit.sha(audit.local(name))==expected,'Readout input changed: '+name)
    require(proof['accountArtifacts']==comparison['artifacts'] and proof['baselineArtifacts']==plan['baselineArtifacts'], 'Account references differ')
    lengths={'accounts':18,'accountRows':18,'comparisons':18,'primaryComparisons':6,'availabilityComparisons':6,'overallComparisons':6,
             'yearRows':36,'halfYearRows':72,'phaseRows':144,'curveEpochRows':18,'coverageRows':18,'formationCoverageRows':6,'feedbackCoverageRows':6,'partitionReconciliation':18}
    require(all(len(report[k])==n for k,n in lengths.items()),'Complete18-account matrix required')
    matrix=[(p,b,m) for p in audit.PERIODS for b in audit.BOARDS for m in POLICIES]
    require([(r['period'],r['board'],r['policy']) for r in report['accounts']]==matrix,'Account order differs')
    same(report['accountRows'],comparison['rows'],'All original comparison rows including normalized baseline')
    require(audit.sha(ROOT/ATLAS)==ATLAS_SHA,'Atlas changed');phases=read(ROOT/ATLAS)['phases']
    require(len(phases)==16 and all(p['labelAvailability']=='retrospectiveResearchOnly' for p in phases),'Hindsight labels changed')
    side=report['opportunityRowsArtifact'];side_path=audit.pinned(side)
    require(side_path==(directory/'opportunity-dispositions.jsonl.gz').resolve() and side['format']=='jsonl+gzip'
        and side['identity']==['period','board','formationId'] and side['policiesPerRow']==list(POLICIES),'Original sidecar scope differs')
    observations=audit.records(side_path)
    require(len(observations)==side['count']==len({(r['period'],r['board'],r['formationId']) for r in observations})
        ==proof['counts']['sourceControlOpportunities'],'Complete original control population required')
    parent=audit.pinned(plan['baselinePlan']).parent
    old_plan,old_comparison,old_report,old_proof=(read(parent/name) for name in ('plan.json','comparison.json','readout.json','readout-validation.json'))
    require(old_proof['passed'] and old_proof['readoutSha256']==audit.sha(parent/'readout.json')
            and old_proof['comparisonSha256']==audit.sha(parent/'comparison.json'),'Inherited readout proof differs')
    by_account={(r['period'],r['board'],r['policy']):r for r in report['accounts']}
    by_coverage={(r['period'],r['board'],r['policy']):r for r in report['coverageRows']}
    by_partition={(r['period'],r['board'],r['policy']):r for r in report['partitionReconciliation']}
    require(len(by_account)==len(by_coverage)==len(by_partition)==18,'Duplicate policy identity')
    intervals=checked=0;order=[];all_daily=[]
    for period in audit.PERIODS:
        for board in audit.BOARDS:
            folder=directory/period/board;receipt=receipts[period][board]
            original=audit.records(folder/'opportunities.jsonl.gz');daily=audit.records(folder/'daily-evidence.jsonl.gz');metadata=read(folder/'signal-metadata.json')
            parent_input=read(audit.pinned(plan['baselineInputs'][period][board]))
            raw_parent=audit.records(audit.pinned(parent_input['files']['opportunities.jsonl.gz']));parent_by_id={r['formationId']:r for r in raw_parent}
            baseline=read(audit.pinned(plan['baselineSchedules'][period][board]))
            same(baseline,[r['signals']['control'] for r in raw_parent if r['signals']['control'] is not None],'Parent control queue')
            same(schedule_reference(original,'baseline',audit),baseline,'Exact original control identity')
            indices={d:i for i,d in enumerate(metadata['calendar'])}
            for r in original:
                at=indices[r['formationDate']];next_day=metadata['calendar'][at+1] if at+1<len(metadata['calendar']) else None
                require(r['cohortDate']==(metadata['calendar'][at-5] if at>=5 else None),'Exact5-session feedback clock')
                parent_row=parent_by_id[r['formationId']]
                r.update(period=period,board=board,plannedNextEntryDate=next_day,nextEntryWithinPeriod=next_day in metadata['targetDates'],
                         parentLineage={k:parent_row[k] for k in ('eventId','peerFormationDate','anchorSymbol','originalLeaderRank','frozenMemberRank','groupSha256')})
            saved_rows=[r for r in observations if (r['period'],r['board'])==(period,board)]
            require([r['formationId'] for r in saved_rows]==[r['formationId'] for r in original],'Original order/identity differs')
            for source,saved in zip(original,saved_rows):
                same({k:v for k,v in saved.items() if k!='accountDispositions'},source,'All original context and parent lineage')
                require(set(saved['accountDispositions'])==set(POLICIES),'Three policy dispositions required')
                order.append((period,board,source['formationId']))
            expected_feedback=feedback_reference(folder,receipt,original,daily,metadata,audit,ctx)
            saved_feedback=next(r for r in report['feedbackCoverageRows'] if (r['period'],r['board'])==(period,board))
            same(saved_feedback,{'period':period,'board':board,**expected_feedback},'Complete day/member/unknown coverage')
            all_daily.extend({'period':period,'board':board,**r} for r in daily)
            parent_cov=next(r for r in old_report['formationCoverageRows'] if (r['period'],r['board'])==(period,board))
            expected_formation={'period':period,'board':board,'sourceCohort':parent_cov['sourceCohort'],
                'parentOriginalTop5':len(raw_parent),'parentControlSignals':len(baseline),'formationTop5':len(original),
                'upstreamNotScheduled':len(raw_parent)-len(baseline),'parentLeaderEventCount':parent_cov['leaderEventCount'],
                'parentLayerCounts':parent_cov['layerCounts'],'feedbackOpportunityStates':ctx.labels(r['state'] for r in original),
                'feedbackDayCount':len(daily),'parentReadout':{'path':(parent/'readout.json').relative_to(ROOT).as_posix(),'sha256':audit.sha(parent/'readout.json')},
                'meaning':"New population is the parent's exact executable control queue; original upstream Top5 and four-layer lineage remain bound, never resurrected."}
            same(next(r for r in report['formationCoverageRows'] if (r['period'],r['board'])==(period,board)),expected_formation,'Parent/new population bridge')
            same(report['inputCountsByPeriodBoard'][period][board],receipt['counts'],'All input counts retained')
            for policy in POLICIES:
                key=period,board,policy;account=by_account[key];source_mode='control' if policy=='baseline' else policy
                require(account['id']==policy and account['label']==audit.LABELS[policy]
                    and account['sourcePolicyId']==source_mode and account['sourceStrategyId']==(audit.BASELINE_SID if policy=='baseline' else audit.SID)
                    and account['execution']==('reused-frozen-account' if policy=='baseline' else 'new-independent-account'),'Reused/new account identity conflated')
                refs=comparison['artifacts'][period][board][policy]
                if policy=='baseline':same(refs,plan['baselineArtifacts'][period][board],'Baseline exact bytes')
                files={name:audit.pinned(ref) for name,ref in refs.items()}
                result,summary=read(files[source_mode+'.result.json.gz']),read(files[source_mode+'.summary.json'])
                schedule=baseline if policy=='baseline' else read(folder/(policy+'.signals.json'))
                same(schedule,schedule_reference(original,policy,audit),'Actual ordered schedule')
                require(summary['policyId']==source_mode and account['sourcePolicyLabel']==summary['policyLabel'],'Original summary identity')
                config=(old_plan if policy=='baseline' else plan)['periods'][period]['config']
                same([r['date'] for r in result['curve']],metadata['targetDates'],'Complete target calendar')
                details=dispositions_reference(original,result,summary,policy,audit,board_ref)
                for saved in saved_rows:same(saved['accountDispositions'][policy],details[saved['formationId']],'Every formation/entry/closed/open disposition');checked+=1
                whole=interval_reference(result,original,policy,config['startDate'],config['endDate'],audit,ctx)
                same(account['curveAttribution'],whole,'Independent whole equity curve')
                same(account['closedDistribution'],whole['closedEpisodesByExitDate'],'Complete closed distribution')
                for field in ('account','closedEpisodes','unresolvedExits','concentration','reconciliation','quality','rights','sourceQuality'):
                    same(account[field],summary[field],'Original summary field '+field)
                same(account['configuredUniverseValues'],config['universe'],'Compatibility config values')
                same(account['opportunityStatusCounts'],summary['opportunityDispositions']['opportunityCounts'],'All opportunity outcomes')
                flags={k:v for k,v in summary['sourceQuality'].items() if not isinstance(v,(dict,list))}
                same(account['sourceQualityScalarFlags'],flags,'Scalar quality flags')
                require(account['warningCount']==len(summary.get('warnings',[])),'Warning count differs')
                row=next(r for r in comparison['rows'] if (r['period'],r['board'],r['id'])==key)
                same(account['comparisonRow'],row,'Comparison row')
                if policy=='baseline':
                    source_row=next(r for r in old_comparison['rows'] if (r['period'],r['board'],r['id'])==(period,board,'control'))
                    same(row,{**source_row,'id':'baseline','policy':'baseline','label':audit.LABELS[policy]},'Baseline display-only metadata change')
                same(account['artifactRefs'],refs,'Saved account references');same(account['sourceBinding'],plan['sourceData'][period][board],'Source binding')
                opened=deepcopy(summary['openPositions']);opened['unrealizedPnlAmount']=math.fsum(r['unrealizedPnlAmount'] for r in opened['positions']);opened['realizedPnlWhileOpen']=math.fsum(r['realizedPnlWhileOpen'] for r in opened['positions'])
                same(account['openPositionsAtCutoff'],opened,'All terminal open episodes')
                same(math.fsum([whole['closedEpisodesByExitDate']['netPnlAmount'],opened['unrealizedPnlAmount'],opened['realizedPnlWhileOpen'],summary['reconciliation']['rightsAttributionGapAmount']]),whole['netPnlAmount'],'Full closed/open/rights bridge')
                expected_cov={'period':period,'board':board,'id':policy,'policy':policy,**whole['opportunitiesByFormationDate'],**whole['opportunitiesByDecisionDate'],
                    'buyFillCount':whole['buyFillCount'],'sellFillCount':whole['sellFillCount'],'closedCount':len(result['closedTrades']),
                    'openCountAtCutoff':opened['count'],'unresolvedExitCountAtCutoff':summary['unresolvedExits']['count'],'curveSessions':len(result['curve']),
                    'valuationUnreliableDays':len(whole['valuationUnreliableDates']),'qualityScalarFlags':flags}
                same(by_coverage[key],expected_cov,'Complete policy coverage')
                parts_proof=[]
                for group,label in (('yearRows','years'),('halfYearRows','halfyears'),('phaseRows','phases'),('curveEpochRows','userEventEpochs')):
                    parts=[r for r in report[group] if (r['period'],r['board'],r['policy'])==key]
                    if group=='curveEpochRows' and period==audit.PERIODS[0]:require(not parts,'Unexpected old epochs');continue
                    windows=([(r['requestedStart'],r['requestedEnd']) for r in summary['periods'] if r['kind']==('calendarYear' if group=='yearRows' else 'calendarHalfYear')] if group in ('yearRows','halfYearRows') else
                        [(r['startDate'],r['endDate']) for r in phases if r['startDate']<=config['endDate'] and r['endDate']>=config['startDate']] if group=='phaseRows' else [('2024-01-02','2024-09-23'),('2024-09-24','2025-12-31')])
                    require([(r['requestedStart'],r['requestedEnd']) for r in parts]==windows,'Full original interval matrix '+group)
                    for part in parts:
                        start,end=part['requestedStart'],part['requestedEnd'];expected=interval_reference(result,original,policy,start,end,audit,ctx)
                        same({k:part[k] for k in expected},expected,'Independent interval arithmetic '+group)
                        same(part['feedbackByActivationDate'],feedback_interval_reference(daily,start,end,ctx),'Feedback day denominator within interval')
                        if group=='phaseRows':
                            phase=next(r for r in phases if r['id']==part['phaseId'])
                            same({k:part[k] for k in ('sentimentSummary','dominantThemes','representatives','labelAvailability')},{k:phase[k] for k in ('sentimentSummary','dominantThemes','representatives','labelAvailability')},'Original hindsight phase labels')
                        intervals+=1
                    pnl=math.fsum(r['netPnlAmount'] for r in parts);ret=(math.prod(1+r['returnPct']/100 for r in parts)-1)*100
                    same(pnl,whole['netPnlAmount'],'Partition PnL');same(ret,whole['returnPct'],'Partition compounding')
                    require(sum(r['observedSessions'] for r in parts)==whole['observedSessions'] and
                        sum(r['opportunitiesByFormationDate']['formationTop5'] for r in parts)==len(original) and
                        sum(r['opportunitiesByFormationDate']['selectedSignalsFromFormation'] for r in parts)==len(schedule) and
                        sum(r['closedEpisodesByExitDate']['count'] for r in parts)==len(result['closedTrades']),'Partition coverage conservation')
                    parts_proof.append({'kind':label,'passed':True,'intervals':len(parts),'netPnlAmount':pnl,'compoundedReturnPct':ret})
                same(by_partition[key],{'period':period,'board':board,'policy':policy,'passed':True,'checks':parts_proof},'Partition evidence')
            same(report['sourceBindings'][period][board],{**plan['sourceData'][period][board],'inputCompletion':completion['outputs'][period][board],
                'baselineInput':plan['baselineInputs'][period][board],'baselineSchedule':plan['baselineSchedules'][period][board],
                'baselineArtifacts':plan['baselineArtifacts'][period][board],'evidenceArtifacts':receipt['files']},'Complete source lineage refs')
    same(report['feedbackDailyRows'],all_daily,'All day-level evidence unchanged')
    require(order==[(r['period'],r['board'],r['formationId']) for r in observations] and checked==len(observations)*3 and intervals==270,'All three policies and intervals checked')
    comparisons=contrasts_reference(report['accounts'],audit);same(report['comparisons'],comparisons,'All18 signed contrasts')
    for field,name in (('primaryComparisons','positiveVsKnown'),('availabilityComparisons','knownVsBaseline'),('overallComparisons','positiveVsBaseline')):
        same(report[field],[r for r in comparisons if r['id']==name],'Prespecified contrast group')
    require(report['decision']=={'selectedBoard':None,'selectedPolicy':None,'automaticNextStrategy':None,'profitHurdle':None}
        and report['certified'] is False and report['targetAchieved'] is False,'Unsupported promotion')
    same(report['trialAccounting'],{'experimentBatches':1,'newSetupMechanisms':0,'newEntryPolicies':2,'newUniverseVariants':0,'newAccountExecutions':12,'referenceAccountsReused':6,'summarizerAccountExecutions':0},'Trial accounting')
    output={'schema':'strength-feedback-readout-independent-validation-v1','passed':True,'verifiedAt':datetime.now(timezone.utc).isoformat(),
        'verifierSha256':audit.sha(__file__),'planSha256':audit.sha(directory/'plan.json'),'comparisonSha256':audit.sha(directory/'comparison.json'),
        'accountValidationSha256':audit.sha(directory/'validation.json'),'readoutSha256':audit.sha(directory/'readout.json'),
        'opportunityRowsArtifactSha256':audit.sha(side_path),'independentReferences':{MAIN:audit.sha(ROOT/MAIN),CONTEXT:CONTEXT_SHA,BOARD:BOARD_SHA},
        'counts':{**lengths,'fullCurveIntervals':18,'subIntervals':intervals,'originalControlOpportunities':len(observations),'policyDispositions':checked,'feedbackDays':len(all_daily)},
        'checks':['Six exact baseline artifacts plus twelve new accounts, every policy and mixed strategy identity',
                  'All18 independent full curves and270 continuous subintervals, including entering equity and carried holdings',
                  'Every original control opportunity with three distinct schedule/execution/closed/open dispositions',
                  'Every target day, full frozen member stream, unknown denominator and parent upstream lineage',
                  'All18 full-metric contrasts with null preservation and no automatic policy promotion']}
    with (directory/'readout-validation.json').open('x',encoding='utf-8') as stream:json.dump(output,stream,ensure_ascii=False,allow_nan=False,indent=2);stream.write('\n')
    return output


def synthetic_feedback(audit):
    daily=[];groups=[];original=[]
    for i,statuses in enumerate((('positive','positive','unknown'),('positive','flat','negative','unknown'),('positive','positive','negative','unknown')),1):
        day=f'2024-07-0{i}';members=[]
        for j,status in enumerate(statuses):
            members.append({'symbol':f's{j}','relativeReturn20':.2,'percentile':.95,'returnSinceCohort':
                None if status=='unknown' else .1 if status=='positive' else -.1 if status=='negative' else 0.,'status':status,'reason':'missing' if status=='unknown' else None})
        counts={k:statuses.count(k) for k in ('positive','flat','negative','unknown')};n=len(members);p=counts['positive'];u=counts['unknown']
        state=('pass','fail','unknown')[i-1]
        row={'formationDate':day,'cohortDate':'2024-06-24','rankPopulation':30,'rankReasonCounts':{},'cohortSize':n,
            'feedbackCounts':counts,'positiveFractionLower':p/n,'positiveFractionUpper':(p+u)/n,'state':state,'reason':state,'memberDigest':audit.digest(members)}
        daily.append(row);groups.append({**{k:row[k] for k in ('formationDate','cohortDate','rankPopulation','cohortSize','memberDigest')},'members':members})
        signal={'signalId':f'old:{i}','strategyId':audit.SID,'symbol':'s0','signalDate':day,'originalRank':1}
        decisions={mode:{'status':'unknown' if state=='unknown' else 'included' if mode=='known' or state=='pass' else 'excluded','reason':state} for mode in ('known','positive')}
        original.append({**signal,**{k:row[k] for k in ('formationDate','cohortDate','state','cohortSize','feedbackCounts','memberDigest')},
            'formationId':signal['signalId'],'sourceStrategyId':audit.BASELINE_SID,'decisions':decisions,
            'signals':{mode:deepcopy(signal) if decisions[mode]['status']=='included' else None for mode in ('known','positive')}})
    counts={'targetDayCount':3,'inputSignalCount':3,'opportunityCount':3,'dayStates':{'pass':1,'fail':1,'unknown':1},
        'signals':{'known':2,'positive':1},'notScheduled':{'known':1,'positive':2},
        'policyCounts':{'known':{'included':2,'excluded':0,'unknown':1},'positive':{'included':1,'excluded':1,'unknown':1}}}
    metadata={'targetDates':[r['formationDate'] for r in daily],'basis':{}}
    return original,daily,groups,counts,metadata


def self_test(audit,ctx,board):
    import io
    from unittest.mock import patch
    original,daily,groups,counts,metadata=synthetic_feedback(audit)
    for policy,n in zip(POLICIES,(3,2,1)):
        schedule=schedule_reference(original,policy,audit)
        audit.require(len(schedule)==n and all(r['strategyId']==(audit.BASELINE_SID if policy=='baseline' else audit.SID) for r in schedule),'Mixed strategy schedule')
        result={'research':{'identity':{'signalSchedule':schedule},'opportunities':[{**r,'filledQuantity':0,'status':'notPlanned','reason':'capacity'} for r in schedule]}}
        measured=formation_reference(original,result,policy,'2024-07-01','2024-07-03',audit,ctx)
        audit.require(measured['formationTop5']==3 and measured['selectedSignalsFromFormation']==n and measured['notEnteredFromFormation']==n,'Three-policy denominators')
    bad=deepcopy(original);bad[0]['signals']['positive']['signalId']='changed'
    try:schedule_reference(bad,'positive',audit)
    except ValueError:pass
    else:raise AssertionError('Changed signal ID accepted')
    result={'config':{'initialCapital':500000.},'curve':[{'date':d,'equity':e,'cash':e,'marketValue':0.,'valuationReliable':True}
            for d,e in [('2024-06-28',500000.),('2024-07-01',450000.),('2024-07-02',495000.)]],
        'daily':[{'date':d,'holdings':[]} for d in ('2024-06-28','2024-07-01','2024-07-02')],
        'trades':[],'closedTrades':[],'decisions':[{'date':'2024-07-01','action':'sell','status':'queued','reason':'stop'},
            {'date':'2024-07-01','action':'exit','status':'queued','reason':'old'}],
        'research':{'identity':{'signalSchedule':[]},'opportunities':[]}}
    measured=interval_reference(result,[],'baseline','2024-07-01','2024-07-02',audit,ctx)
    audit.same(measured['returnPct'],-1.,'Prior-close return');audit.same(measured['maxDrawdownPct'],10.,'Entering equity drawdown')
    audit.require(measured['queuedExitReasons']=={'stop':1},'Queued sell correction')
    schedule=schedule_reference(original,'positive',audit)
    result['research']={'identity':{'signalSchedule':schedule},'opportunities':[{**r,'filledQuantity':0,'status':'notPlanned','reason':'noNextSession'} for r in schedule]}
    dispositions=dispositions_reference(original,result,{'openPositions':{'count':0,'positions':[]}},'positive',audit,board)
    audit.require(dispositions['old:1']['positionState']=='notEntered' and dispositions['old:2']['positionState']=='notScheduled'
        and dispositions['old:3']['admissionStatus']=='unknown','Cutoff/unknown/excluded distinction')
    payload=''.join(json.dumps(g)+'\n' for g in groups)
    with patch('gzip.open',return_value=io.StringIO(payload)):
        feedback=feedback_reference(Path('synthetic'),{'counts':counts,'files':{}},original,daily,metadata,audit,ctx)
    audit.require(feedback['memberObservations']==11 and feedback['knownDecisionWithUnknownMembersDays']==2
        and feedback['dayStateCounts']=={'pass':1,'fail':1,'unknown':1} and feedback['completeNonemptyCohortDays']==0,'Fixed denominator/known-with-unknown separation')
    audit.require(feedback_interval_reference(daily,'2024-07-02','2024-07-02',ctx)['memberObservations']==4,'A-day cohort scope')
    accounts=[]
    for p in audit.PERIODS:
        for b in audit.BOARDS:
            for i,m in enumerate(POLICIES):
                accounts.append({'period':p,'board':b,'policy':m,'comparisonRow':{'accountReturnPct':float(i)},
                    'account':{'netPnlAmount':i,'finalEquity':500000+i},'curveAttribution':{'investedSessionPct':0.,'feesPaid':0.,'slippageAlreadyInFillPrices':0.,
                    'buyFillCount':0,'sellFillCount':0,'opportunitiesByDecisionDate':{'notEntered':0},'opportunitiesByFormationDate':{'notScheduledFromFormation':0}},
                    'reconciliation':{'closedEpisodePnl':0.,'openPositionRealizedPnl':0.,'openPositionUnrealizedPnl':0.},
                    'concentration':{'top1ProfitSharePct':None,'top5ProfitSharePct':None}})
    contrasts=contrasts_reference(accounts,audit)
    audit.require(len(contrasts)==18 and all(r['candidateMinusControl']['accountReturnPct']==(2. if r['id']=='positiveVsBaseline' else 1.)
        and r['candidateMinusControl']['top1ShareOfGrossProfitPct'] is None for r in contrasts),'All signed contrasts/null propagation')
    return {'passed':True,'checks':['mixed source/new strategy identities','three policy denominators','changed signal rejected',
        'independent prior-close return and drawdown','queued sell correction','unknown/excluded/unexecuted outcomes',
        'full member stream and robust majority with unknowns','18 signed contrasts and undefined tails'],
        'filesWritten':0,'actualResultsRead':False}


if __name__=='__main__':
    parser=ArgumentParser();parser.add_argument('--directory',type=Path,default=DEFAULT);parser.add_argument('--self-test',action='store_true');args=parser.parse_args()
    audit,ctx,board=load()
    try:
        result=self_test(audit,ctx,board) if args.self_test else verify(args.directory.resolve(),audit,ctx,board)
        print(json.dumps(result if args.self_test else {'passed':True,'counts':result['counts'],'sha256':audit.sha(args.directory/'readout-validation.json')},ensure_ascii=False))
    except Exception:
        traceback.print_exc();raise

