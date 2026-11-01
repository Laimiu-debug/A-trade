"""Independent saved-ledger readout audit; never imports the new summarizer."""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from contextlib import redirect_stdout
import importlib.util
import io
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/reclaim-exit-transfer-v1'
MAIN = 'scripts/verify-reclaim-exit-transfer.py'
MAIN_SHA = '5437d5e9421f65f935d211c553e95c473cf40fddafaf09b12694b3bf3548913a'
CONTEXT = 'scripts/verify-reclaim-context-diagnostic.py'
CONTEXT_SHA = '9b899e4c84ca90d2d26be4ef3a2ae2910029fa726441e23bed7ccbf6f48c67c9'
BOARD = 'scripts/verify-board-coverage-readout.py'
BOARD_SHA = '376ef50af09e1024e6ca578acc6dda5d4a5d39b9b67c3057eb67b330fe158650'
ATLAS = 'artifacts/strategy-search/market-phase-atlas-v1/atlas.json'
ATLAS_SHA = 'ddf55f2741dca3793923e5da782690aad500510848077f43492fff41af1a44d8'
MODES = ('baseline', 'trail10', 'structure10')
CONTRASTS = (('trail10VsBaseline', 'baseline', 'trail10'), ('structure10VsTrail10', 'trail10', 'structure10'),
             ('structure10VsBaseline', 'baseline', 'structure10'))
STATES = ('triggered', 'notTriggered', 'unknown', 'skippedExistingExit')


def load():
    spec = importlib.util.spec_from_file_location('_exit_transfer_readout_main', ROOT / MAIN)
    audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)
    if MAIN_SHA is not None: audit.require(audit.sha(ROOT / MAIN) == MAIN_SHA, 'Frozen main auditor changed')
    return audit, audit.reference(CONTEXT, CONTEXT_SHA), audit.reference(BOARD, BOARD_SHA)


def exit_reference(result, policy, audit, start=None, end=None):
    rule = {'baseline': 'noProgress5', 'trail10': None, 'structure10': 'structure10'}[policy]
    raw = result['research'].get('structuralExits')
    if rule is None:
        audit.require(raw is None, 'Unregistered extra exit on trail10'); selected = []
    else:
        audit.require(raw is not None and raw['rule'] == rule, 'Saved extra rule identity differs')
        recorded = raw['observations']; observed = {state: 0 for state in STATES}
        for row in recorded:
            audit.require(row['status'] in observed, 'Unknown exit observation state')
            observed[row['status']] += 1
        audit.same(raw['observationCounts'], observed, 'All extra exit observation states')
        selected = [r for r in recorded if (start is None or r['date'] >= start) and (end is None or r['date'] <= end)]
    counts = {state: 0 for state in STATES}; reasons = Counter(); episodes = set(); unknown_episodes = set()
    for row in selected:
        counts[row['status']] += 1; key = row['symbol'], row['entryDate']; episodes.add(key)
        if row['status'] == 'unknown': unknown_episodes.add(key)
        for reason in row.get('unknown', []): reasons[reason['reason']] += 1
    return {'configured': rule is not None, 'rule': rule, 'observationCount': len(selected), 'observationCounts': counts,
        'unknownReasonCounts': dict(sorted(reasons.items())), 'uniqueObservedHoldingEpisodes': len(episodes),
        'uniqueUnknownHoldingEpisodes': len(unknown_episodes),
        'meaning': 'Saved close-time extra-rule observations; absent adapter is not complete evidence. Unknown or skipped is not trend survival or a successful nontrigger. Standard exits keep priority.'}


def disposition_reference(original, result, summary, calendar, dates, audit, board):
    source = board.disposition_reference(result, audit)
    schedule = [r['signals']['immediate'] for r in original]
    audit.same(result['research']['identity']['signalSchedule'], schedule, 'Original five-field schedule')
    audit.require(set(source) == {r['formationId'] for r in original}, 'All formation identities retained')
    by_id = {r['signalId']: r for r in result['research']['opportunities']}
    opened = {(r['record']['symbol'], r['record']['entryDate']): r for r in summary['openPositions']['positions']}
    audit.require(len(opened) == summary['openPositions']['count'], 'Duplicate open identity')
    indexes = {d: i for i, d in enumerate(calendar)}; out = {}
    for formed in original:
        sid = formed['formationId']; detail = deepcopy(source[sid]); index = indexes[formed['formationDate']]
        nxt = calendar[index + 1] if index + 1 < len(calendar) else None
        position = opened.get((formed['symbol'], detail['entryDate']))
        audit.require((position is not None) == (detail['positionState'] == 'openAtCutoff'), 'Open/closed linkage differs')
        out[sid] = {**detail, 'formationId': sid, 'formationDate': formed['formationDate'], 'strategyId': audit.SID,
            'decisionDate': formed['signals']['immediate']['signalDate'], 'plannedNextEntryDate': nxt, 'nextEntryWithinPeriod': nxt in dates,
            'accountOpportunity': by_id[sid], 'openPositionAtCutoff': position}
    return out


def metric_reference(account, audit):
    values = dict(account['comparisonRow']); book = account['account']; curve = account['curveAttribution']; bridge = account['reconciliation']
    values.update(netPnlAmount=book['netPnlAmount'], finalEquity=book['finalEquity'], closedPnlAmount=bridge['closedEpisodePnl'],
        openRealizedPnlAmount=bridge['openPositionRealizedPnl'], openUnrealizedPnlAmount=bridge['openPositionUnrealizedPnl'],
        investedSessionPct=curve['investedSessionPct'], feesPaid=curve['feesPaid'], slippageAlreadyInFillPrices=curve['slippageAlreadyInFillPrices'],
        buyFillCount=curve['buyFillCount'], sellFillCount=curve['sellFillCount'], notEntered=curve['opportunitiesByDecisionDate']['notEntered'],
        notScheduled=curve['opportunitiesByFormationDate']['notScheduledFromFormation'],
        top1ShareOfGrossProfitPct=account['concentration']['top1ProfitSharePct'], top5ShareOfGrossProfitPct=account['concentration']['top5ProfitSharePct'],
        openUnrealizedPnlPctOfFinalEquity=account['openUnrealizedPnlPctOfFinalEquity'])
    return {k: v for k, v in values.items() if v is None or audit.finite(v)}


def contrasts_reference(accounts, audit):
    indexed = {(r['period'], r['board'], r['policy']): metric_reference(r, audit) for r in accounts}
    audit.require(len(indexed) == len(accounts) == 18 and set(indexed) == {(p,b,m) for p in audit.PERIODS for b in audit.BOARDS for m in MODES}, 'Complete unique account matrix')
    rows = []
    for p in audit.PERIODS:
        for b in audit.BOARDS:
            for name, control, candidate in CONTRASTS:
                left, right = indexed[(p,b,control)], indexed[(p,b,candidate)]
                audit.require(left.keys() == right.keys(), 'Metric set differs')
                rows.append({'period': p, 'board': b, 'id': name, 'control': control, 'candidate': candidate,
                    'controlMetrics': left, 'candidateMetrics': right,
                    'candidateMinusControl': {k: right[k] - left[k] if audit.finite(right[k]) and audit.finite(left[k]) else None for k in sorted(left)},
                    'meaning': 'Same original entry schedules, different complete exit-policy accounts; positions, subsequent admissions and capital paths differ. Not paired-trade causal attribution.'})
    return rows


def verify(directory, audit, ctx, board_ref):
    require, same, read = audit.require, audit.same, audit.read
    require(not (directory / 'readout-validation.json').exists(), 'Never overwrite readout audit')
    plan = audit.registered_plan(directory); audit.inherited_sources(plan)
    completion, receipts = audit.input_receipts(directory, plan)
    proof, report, comparison = (read(directory / n) for n in ('validation.json', 'readout.json', 'comparison.json'))
    require(proof['passed'] and proof['verifierSha256'] == audit.sha(ROOT / MAIN) == plan['sourcePins'][MAIN]
        and proof['planSha256'] == report['planSha256'] == audit.sha(directory / 'plan.json')
        and proof['comparisonSha256'] == audit.sha(directory / 'comparison.json')
        and proof['inputValidationSha256'] == audit.sha(directory / 'input-validation.json')
        and proof['inputCompletionSha256'] == audit.sha(directory / 'input-completion.json'), 'Frozen account proof chain differs')
    require(report['schema'] == 'reclaim-exit-transfer-readout-v1' and report['verification']['passed'] is True
        and audit.pinned(report['verification']) == (directory / 'validation.json').resolve(), 'Readout proof differs')
    for name, digest in report['inputPins'].items(): require(audit.sha(audit.local(name)) == digest, 'Input changed: ' + name)
    same(proof['accountArtifacts'], comparison['artifacts'], 'Complete account refs')
    lengths = {'accounts':18,'accountRows':18,'comparisons':18,'primaryComparisons':6,'secondaryComparisons':6,'overallComparisons':6,
        'yearRows':36,'halfYearRows':72,'phaseRows':144,'curveEpochRows':18,'coverageRows':18,'formationCoverageRows':6,'partitionReconciliation':18}
    require(all(len(report[k]) == n for k,n in lengths.items()), 'Complete eighteen-account matrix')
    matrix = [(p,b,m) for p in audit.PERIODS for b in audit.BOARDS for m in MODES]
    require([(r['period'],r['board'],r['policy']) for r in report['accounts']] == matrix, 'Account order changed')
    same(report['accountRows'], comparison['rows'], 'All comparison rows retained')
    require(audit.sha(ROOT / ATLAS) == ATLAS_SHA, 'Atlas changed'); phases = read(ROOT / ATLAS)['phases']
    parent = audit.pinned(plan['baselinePlan']).parent
    old_report, old_proof, old_comparison = (read(parent / n) for n in ('readout.json','readout-validation.json','comparison.json'))
    require(old_proof['passed'] and old_proof['readoutSha256'] == audit.sha(parent/'readout.json')
        and old_proof['comparisonSha256'] == audit.sha(parent/'comparison.json'), 'Original readout binding differs')
    side = report['opportunityRowsArtifact']; side_path = audit.pinned(side)
    require(side_path == (directory/'opportunity-dispositions.jsonl.gz').resolve() and side['format'] == 'jsonl+gzip'
        and side['identity'] == ['period','board','formationId'] and side['policiesPerRow'] == list(MODES), 'Sidecar scope differs')
    all_rows = audit.records(side_path)
    require(len(all_rows) == side['count'] == len({(r['period'],r['board'],r['formationId']) for r in all_rows})
        == proof['counts']['sourceImmediateOpportunities'], 'All original immediate opportunities required')
    indexed = {(r['period'],r['board'],r['policy']):r for r in report['accounts']}
    coverage = {(r['period'],r['board'],r['policy']):r for r in report['coverageRows']}
    partitions = {(r['period'],r['board'],r['policy']):r for r in report['partitionReconciliation']}
    require(len(indexed) == len(coverage) == len(partitions) == 18, 'Duplicate policies')
    checked = intervals = 0; ordering = []
    for p in audit.PERIODS:
        for b in audit.BOARDS:
            receipt = receipts[p][b]; source_input = read(audit.pinned(plan['baselineInputs'][p][b]))
            same(receipt['files'], source_input['files'], 'Original input refs preserved')
            files = {k:audit.pinned(v) for k,v in receipt['files'].items()}
            original = audit.records(files['opportunities.jsonl.gz']); schedule = read(audit.pinned(plan['baselineSchedules'][p][b]))
            metadata = read(files['signal-metadata.json']); counts = read(files['input-counts.json'])
            same(schedule, [r['signals']['immediate'] for r in original], 'Identical original immediate signals')
            same(report['inputCountsByPeriodBoard'][p][b], counts, 'Original counts')
            saved_rows = [r for r in all_rows if (r['period'],r['board']) == (p,b)]
            require([r['formationId'] for r in saved_rows] == [r['formationId'] for r in original], 'Original ordering')
            indexes = {d:i for i,d in enumerate(metadata['calendar'])}
            for formed,saved in zip(original,saved_rows):
                at = indexes[formed['formationDate']]; nxt = metadata['calendar'][at+1] if at+1<len(metadata['calendar']) else None
                same({k:v for k,v in saved.items() if k!='accountDispositions'}, {**formed,'period':p,'board':b,
                    'plannedNextEntryDate':nxt,'nextEntryWithinPeriod':nxt in metadata['targetDates']}, 'Unchanged original formation and timing')
                require(set(saved['accountDispositions']) == set(MODES), 'Three complete dispositions required'); ordering.append((p,b,formed['formationId']))
            parent_cov = next(r for r in old_report['coverageRows'] if (r['period'],r['board']) == (p,b))
            expected_formation = {'period':p,'board':b,'formationTop5':len(original),'sourceImmediateSignals':len(schedule),
                'sameScheduleForEveryPolicy':True,'sourceCohort':parent_cov['sourceCohort'],
                'formationEvidenceTotals':parent_cov['formationEvidenceTotals'],'benchmarkUnavailableDays':parent_cov['benchmarkUnavailableDays'],
                'benchmarkReasonCounts':parent_cov['benchmarkReasonCounts'],
                'parentReadout':{'path':(parent/'readout.json').relative_to(ROOT).as_posix(),'sha256':audit.sha(parent/'readout.json')}}
            same(next(r for r in report['formationCoverageRows'] if (r['period'],r['board']) == (p,b)), expected_formation, 'Unchanged source formation coverage')
            for mode in MODES:
                key = p,b,mode; account = indexed[key]; source_mode = 'immediate' if mode=='baseline' else mode
                refs = comparison['artifacts'][p][b][mode]
                if mode=='baseline': same(refs,plan['baselineArtifacts'][p][b],'Original baseline byte refs')
                result,summary = (read(audit.pinned(refs[source_mode+suffix])) for suffix in ('.result.json.gz','.summary.json'))
                config = plan['periods'][p]['policyConfigs'][mode]
                require(summary['policyId'] == source_mode and account['sourcePolicyId'] == source_mode
                    and account['sourcePolicyLabel'] == summary['policyLabel'] and account['sourceStrategyId'] == audit.SID
                    and account['id'] == mode and account['label'] == plan['labels'][mode]
                    and account['execution'] == ('reused-frozen-account' if mode=='baseline' else 'new-independent-account'), 'Source/display policy identity')
                same([r['date'] for r in result['curve']], metadata['targetDates'], 'Complete curve calendar')
                same(account['effectiveConfig'], config, 'Explicit effective exit configuration')
                details = disposition_reference(original,result,summary,metadata['calendar'],metadata['targetDates'],audit,board_ref)
                for saved in saved_rows: same(saved['accountDispositions'][mode],details[saved['formationId']],'Every original disposition'); checked += 1
                whole = board_ref.interval_reference(result,original,config['startDate'],config['endDate'],audit,ctx)
                same(account['curveAttribution'],whole,'Independent complete curve arithmetic')
                same(account['closedDistribution'],whole['closedEpisodesByExitDate'],'All closed tails')
                for field in ('account','closedEpisodes','unresolvedExits','concentration','reconciliation','quality','rights','sourceQuality'):
                    same(account[field],summary[field],'Saved field '+field)
                flags = {k:v for k,v in summary['sourceQuality'].items() if not isinstance(v,(dict,list))}
                same(account['sourceQualityScalarFlags'],flags,'All scalar quality flags'); same(account['sourceQualityCoverage'],summary['sourceQuality'].get('coverage'),'Quality coverage')
                same(account['configuredUniverseValues'],config['universe'],'Compatibility config'); same(account['opportunityStatusCounts'],summary['opportunityDispositions']['opportunityCounts'],'Opportunity status counts')
                require(account['warningCount']==len(summary.get('warnings',[])),'Warning count')
                row = next(r for r in comparison['rows'] if (r['period'],r['board'],r['id'])==key)
                same(account['comparisonRow'],row,'Comparison row unchanged')
                if mode=='baseline':
                    old_row = next(r for r in old_comparison['rows'] if (r['period'],r['board'])==(p,b))
                    same(row,{**old_row,'id':'baseline','policy':'baseline','label':plan['labels'][mode]},'Baseline display-only identity')
                same(account['artifactRefs'],refs,'Full account artifacts'); same(account['sourceBinding'],plan['sourceData'][p][b],'Full source binding')
                opened = deepcopy(summary['openPositions'])
                opened['unrealizedPnlAmount'] = math.fsum(r['unrealizedPnlAmount'] for r in opened['positions'])
                opened['realizedPnlWhileOpen'] = math.fsum(r['realizedPnlWhileOpen'] for r in opened['positions'])
                same(account['openPositionsAtCutoff'],opened,'Every open holding')
                equity = summary['account']['finalEquity']; share = opened['unrealizedPnlAmount']/equity*100 if audit.finite(equity) and equity>0 else None
                same(account['openUnrealizedPnlPctOfFinalEquity'],share,'Unrealized terminal equity contribution')
                same(math.fsum((whole['closedEpisodesByExitDate']['netPnlAmount'],opened['unrealizedPnlAmount'],opened['realizedPnlWhileOpen'],summary['reconciliation']['rightsAttributionGapAmount'])),whole['netPnlAmount'],'Closed/open/rights bridge')
                evidence = exit_reference(result,mode,audit); same(account['exitEvidence'],evidence,'Complete extra exit status evidence')
                expected_cov = {'period':p,'board':b,'id':mode,'policy':mode,**whole['opportunitiesByFormationDate'],**whole['opportunitiesByDecisionDate'],
                    'buyFillCount':whole['buyFillCount'],'sellFillCount':whole['sellFillCount'],'closedCount':len(result['closedTrades']),
                    'openCountAtCutoff':opened['count'],'unresolvedExitCountAtCutoff':summary['unresolvedExits']['count'],
                    'curveSessions':len(result['curve']),'valuationUnreliableDays':len(whole['valuationUnreliableDates']),'qualityScalarFlags':flags,
                    'qualifiedMeaning':'Observed execution states/reasons and inherited historical uncertainty, not certified exchange eligibility.',
                    'exitEvidence':evidence,'sellFillReasons':whole['sellFillReasons'],'queuedExitReasons':whole['queuedExitReasons'],
                    'openUnrealizedPnlAmount':opened['unrealizedPnlAmount'],'openUnrealizedPnlPctOfFinalEquity':share}
                same(coverage[key],expected_cov,'Every policy denominator/exit/quality/open field')
                part_checks = []
                for group,label in (('yearRows','years'),('halfYearRows','halfyears'),('phaseRows','phases'),('curveEpochRows','userEventEpochs')):
                    parts = [r for r in report[group] if (r['period'],r['board'],r['policy'])==key]
                    if group=='curveEpochRows' and p==audit.PERIODS[0]: require(not parts,'Unexpected old-period9/24 intervals'); continue
                    windows = ([(r['requestedStart'],r['requestedEnd']) for r in summary['periods'] if r['kind']==('calendarYear' if group=='yearRows' else 'calendarHalfYear')] if group in ('yearRows','halfYearRows') else
                        [(r['startDate'],r['endDate']) for r in phases if r['startDate']<=config['endDate'] and r['endDate']>=config['startDate']] if group=='phaseRows' else [('2024-01-02','2024-09-23'),('2024-09-24','2025-12-31')])
                    require([(r['requestedStart'],r['requestedEnd']) for r in parts]==windows,'Complete chronological intervals')
                    for part in parts:
                        start,end = part['requestedStart'],part['requestedEnd']; expected = board_ref.interval_reference(result,original,start,end,audit,ctx)
                        same({k:part[k] for k in expected},expected,'Independent subinterval arithmetic')
                        same(part['exitEvidence'],exit_reference(result,mode,audit,start,end),'Exit observations by actual close date')
                        if group=='phaseRows':
                            phase = next(r for r in phases if r['id']==part['phaseId'])
                            same({k:part[k] for k in ('sentimentSummary','dominantThemes','representatives','labelAvailability')},{k:phase[k] for k in ('sentimentSummary','dominantThemes','representatives','labelAvailability')},'Original hindsight labels')
                        intervals += 1
                    pnl = math.fsum(r['netPnlAmount'] for r in parts); ret = (math.prod(1+r['returnPct']/100 for r in parts)-1)*100
                    same(pnl,whole['netPnlAmount'],'Partition monetary sum'); same(ret,whole['returnPct'],'Partition compounding')
                    require(sum(r['observedSessions'] for r in parts)==whole['observedSessions']
                        and sum(r['opportunitiesByFormationDate']['formationTop5'] for r in parts)==len(original)
                        and sum(r['closedEpisodesByExitDate']['count'] for r in parts)==len(result['closedTrades']),'Partition coverage')
                    part_checks.append({'kind':label,'passed':True,'intervals':len(parts),'netPnlAmount':pnl,'compoundedReturnPct':ret})
                same(partitions[key],{'period':p,'board':b,'policy':mode,'passed':True,'checks':part_checks},'Partition reconciliation')
            same(report['sourceBindings'][p][b],{**plan['sourceData'][p][b],'inputCompletion':completion['outputs'][p][b],
                'baselineInput':plan['baselineInputs'][p][b],'baselineSchedule':plan['baselineSchedules'][p][b],
                'baselineArtifacts':plan['baselineArtifacts'][p][b],'evidenceArtifacts':receipt['files']},'Complete source lineage')
    require(intervals==270 and checked==3*len(all_rows) and ordering==[(r['period'],r['board'],r['formationId']) for r in all_rows], 'Whole original population and interval accounting')
    comparisons = contrasts_reference(report['accounts'],audit); same(report['comparisons'],comparisons,'All18 signed comparisons')
    for field,name in zip(('primaryComparisons','secondaryComparisons','overallComparisons'),(r[0] for r in CONTRASTS)):
        same(report[field],[r for r in comparisons if r['id']==name],'Named contrast subset')
    same(report['trialAccounting'],{'experimentBatches':1,'newSetupMechanisms':0,'newEntryPolicies':0,'newExitDefinitions':0,
        'transferredExitPolicies':2,'newUniverseVariants':0,'referenceAccountsReused':6,'newAccountExecutions':12,'summarizerAccountExecutions':0},'Transfer trial counting')
    require(report['decision']=={'selectedBoard':None,'selectedPolicy':None,'automaticNextStrategy':None,'profitHurdle':None}
        and report['certified'] is False and report['targetAchieved'] is False,'No automatic promotion')
    result = {'schema':'reclaim-exit-transfer-readout-independent-validation-v1','passed':True,'verifiedAt':datetime.now(timezone.utc).isoformat(),
        'verifierSha256':audit.sha(__file__),'planSha256':audit.sha(directory/'plan.json'),'comparisonSha256':audit.sha(directory/'comparison.json'),
        'accountValidationSha256':audit.sha(directory/'validation.json'),'readoutSha256':audit.sha(directory/'readout.json'),
        'opportunityRowsArtifactSha256':audit.sha(side_path),'independentReferences':{MAIN:audit.sha(ROOT/MAIN),CONTEXT:CONTEXT_SHA,BOARD:BOARD_SHA},
        'counts':{**lengths,'fullCurveIntervals':18,'subIntervals':intervals,'sourceImmediateOpportunities':len(all_rows),'policyDispositions':checked},
        'checks':['18 full accounts and270 continuous intervals independently recalculated','All original formations retain three unchanged-signal account dispositions',
            'Every exit reason and extra-rule status including unknown/skipped/absent adapter','All closed/open PnL, holding distributions, quality and18 signed contrasts','Six unchanged baseline references and twelve new exit-policy accounts; no new signal or automatic winner']}
    with (directory/'readout-validation.json').open('x',encoding='utf-8') as stream: json.dump(result,stream,ensure_ascii=False,allow_nan=False,indent=2);stream.write('\n')
    return result


def self_test(audit, ctx, board):
    capture = io.StringIO()
    with redirect_stdout(capture): board.self_test(audit,ctx)
    audit.require(json.loads(capture.getvalue())['passed'],'Independent base interval synthetic failed')
    r = {'research':{'structuralExits':{'rule':'structure10','observations':[
        {'date':'2024-01-03','symbol':'a','entryDate':'2024-01-02','status':state,'unknown':[{'reason':'missingDailyBar'}] if state=='unknown' else []} for state in STATES],
        'observationCounts':{state:1 for state in STATES}}}}
    old=deepcopy(r); full=exit_reference(r,'structure10',audit)
    audit.require(full['observationCount']==4 and full['unknownReasonCounts']=={'missingDailyBar':1} and full['uniqueUnknownHoldingEpisodes']==1 and r==old,'Unknown/status counts')
    audit.require(exit_reference(r,'structure10',audit,'2024-01-04','2024-01-05')['observationCount']==0,'Close-date attribution')
    audit.require(exit_reference({'research':{}},'trail10',audit)['configured'] is False,'Absent adapter')
    for mode in ('baseline','trail10'):
        try: exit_reference(r,mode,audit)
        except ValueError: pass
        else: raise AssertionError('Wrong rule accepted')
    r['research']['structuralExits']['observationCounts']['unknown']=0
    try: exit_reference(r,'structure10',audit)
    except ValueError: pass
    else: raise AssertionError('Mismatched count accepted')
    accounts=[]
    for p in audit.PERIODS:
        for b in audit.BOARDS:
            for value,mode in enumerate(MODES):
                accounts.append({'period':p,'board':b,'policy':mode,'comparisonRow':{'accountReturnPct':value,'unknown':None},
                    'account':{'netPnlAmount':value,'finalEquity':100+value},
                    'curveAttribution':{'investedSessionPct':0,'feesPaid':0,'slippageAlreadyInFillPrices':0,'buyFillCount':0,'sellFillCount':0,
                        'opportunitiesByDecisionDate':{'notEntered':0},'opportunitiesByFormationDate':{'notScheduledFromFormation':0}},
                    'reconciliation':{'closedEpisodePnl':value,'openPositionRealizedPnl':0,'openPositionUnrealizedPnl':0},
                    'concentration':{'top1ProfitSharePct':None,'top5ProfitSharePct':None},'openUnrealizedPnlPctOfFinalEquity':0})
    differences=contrasts_reference(accounts,audit)
    audit.require(len(differences)==18 and [r['candidateMinusControl']['accountReturnPct'] for r in differences[:3]]==[1,1,2]
        and all(r['candidateMinusControl']['unknown'] is None for r in differences),'All signed/null comparisons')
    try: contrasts_reference(accounts[:-1],audit)
    except ValueError: pass
    else: raise AssertionError('Missing account accepted')
    return {'passed':True,'checks':['Independent prior-equity/drawdown and complete dispositions','All four exit states and unknown episode denominator',
        'Close-date interval restriction','Absent adapter distinguished from known nontrigger','Wrong adapter and altered counts rejected',
        'All18 signed comparisons and preserved nulls','Incomplete policy matrix rejected','No input mutation'], 'filesWritten':0}


if __name__ == '__main__':
    parser=ArgumentParser();parser.add_argument('--directory',type=Path,default=DEFAULT);parser.add_argument('--self-test',action='store_true')
    args=parser.parse_args(); helpers=load()
    print(json.dumps(self_test(*helpers) if args.self_test else verify(args.directory.resolve(),*helpers),ensure_ascii=False))
