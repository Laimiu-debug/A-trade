"""Registered follower confirmations from immutable, previously audited streams.

Parent price predicates and correlations are inherited evidence, not recomputed
here. New episode state, ownership and all three selections advance only at the
current close. Event labels are evaluated only after complete source EOF.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager, ExitStack, closing
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.signal_leader_follower_confirmation import (
    VERSION, CANDIDATE_ID, MEMBER_CONTROL_ID, ANCHOR_CONTROL_ID, GROUPS,
    project_parent, FollowerConfirmationStudy,
)
from service.signal_path_risk import PathRiskStudy, summarize_paths

REGISTRATION = ROOT/'docs/signal-leader-follower-confirmation-v1.json'
REGISTRATION_SHA256 = 'b6f382b3edea4ae91ac197ac6b3286d24f2e6a0fb220e3b15e410c0d4b30ae3a'
PARENT_RUNNER = 'scripts/run-signal-leader-peer-expansion.py'
PARENT_AUDITOR = 'scripts/verify-signal-leader-peer-expansion-result.py'
PARENT_REGISTRATION = 'docs/signal-leader-peer-expansion-v1.json'
PARENT_ID = 'rawLeaderPeerExpansion'
AUDIT_REFERENCES = {
    PARENT_RUNNER: 'a16f046922e142a3ac0d11705c445a6de451709169571d15284f06e612262f2a',
    PARENT_AUDITOR: 'e57fbf05c066b04eb616de3c927c39d929aad3313c5101110ddb4a9e22fee5d2',
    'scripts/verify-signal-market-repair-result.py': '922f9872264fd27a2b3c09e2f8586e5698119b75c2fdc6ba569f953c46f7b1bf',
    'scripts/verify-signal-persistent-leader-result.py': '3e9a02ba8f48a34cd81af0f30108d94a0a1b30b60396905f185e37f491785222',
    'scripts/verify-signal-session-decomposition-result.py': 'fd71659842d12ed716305b35cef7f23d8f6f42d82906d4faae5918636f62e10a',
}
ADDITIONAL_IMPLEMENTATION = ('scripts/verify-signal-leader-follower-confirmation-result.py',
    'tests/test_signal_leader_follower_confirmation_verifier.py')
HORIZONS = (5,10,20,40,60,120,250)
ATTEMPT_FILES = {'formations':'formations.jsonl.gz','transitions':'transitions.jsonl.gz',
    'episodeTerminals':'episode-terminals.jsonl.gz','confirmations':'confirmations.jsonl.gz',
    'selections':'selection.jsonl.gz','coverage':'coverage.jsonl.gz','signals':'signals.jsonl.gz',
    'slotOutcomes':'slot-outcomes.jsonl.gz','result':'result.json.gz','summary':'summary.json','attempt':'attempt.json'}


def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


parent=load_module('_follower_parent_source',ROOT/PARENT_RUNNER)
runtime=load_module('_follower_registered_events',ROOT/'scripts/run-signal-target-study.py')
runtime.STRATEGIES,runtime.GROUPS=(CANDIDATE_ID,),tuple(GROUPS)
source_tools=runtime.source_tools
sha=parent.sha
digest=parent.digest
read_json=parent.read_json
write_json=parent.write_json
require=parent.require
_LOADED_RUNNER_SHA=sha(__file__)
_LOADED_RULE_SHA=sha(ROOT/'service/signal_leader_follower_confirmation.py')


def _now(): return datetime.now(timezone.utc).isoformat()


def _local(name):
    path=(ROOT/name).resolve()
    require(path.is_relative_to(ROOT.resolve()) and path.is_file() and not path.is_symlink(),'Missing or escaping bound file: '+str(name))
    return path


def registration():
    require(sha(REGISTRATION)==REGISTRATION_SHA256,'Frozen follower registration changed')
    doc=read_json(REGISTRATION)
    require(doc['candidateId']==CANDIDATE_ID and doc['controlIds']==[MEMBER_CONTROL_ID,ANCHOR_CONTROL_ID]
        and doc.get('certified') is False and doc.get('targetAchieved') is False,'Registration identity differs')
    return doc


@contextmanager
def _parent_context(parent_registration,registration_hash):
    values=parent.ROOT,parent.REGISTRATION,parent.REGISTRATION_SHA256
    parent.ROOT,parent.REGISTRATION,parent.REGISTRATION_SHA256=ROOT,parent_registration,registration_hash
    try: yield
    finally: parent.ROOT,parent.REGISTRATION,parent.REGISTRATION_SHA256=values


def _archive_check(path,hashes):
    parent._check_archive(path,hashes)
    for name,expected in hashes.items(): require(sha(_local(name))==expected,'Archived implementation changed: '+name)


def load_binding():
    """Verify proof and byte closure, without reading inherited trade outcomes."""
    doc=registration(); source=doc['sourceBinding']
    directory=(ROOT/source['parentDirectory']).resolve()
    require(directory.is_relative_to(ROOT.resolve()),'Parent directory escapes root')
    files={}
    for name,expected in source['files'].items():
        path=_local(name);require(sha(path)==expected,'Parent bound file changed: '+name)
        files[str(path)]=expected
    protocol=read_json(directory/'protocol.json')
    receipt=read_json(directory/'attempts/attempt-0001/completion.json')
    proof=read_json(directory/'independent-result-verification.json')
    require(protocol.get('schema')=='leader-peer-expansion-development-v1' and protocol['protocolHash']==source['parentProtocolHash']
        ==digest({k:v for k,v in protocol.items() if k!='protocolHash'}),'Parent protocol differs')
    old_doc=read_json(_local(PARENT_REGISTRATION))
    require(protocol['registration']==old_doc and protocol['registrationSha256']==sha(_local(PARENT_REGISTRATION))
        and doc['sample']==old_doc['sample'] and doc['config']==old_doc['config'],'Inherited sample/config changed')
    require(proof.get('passed') is True and proof.get('verificationCompleted') is True and type(proof.get('failureCount')) is int
        and proof['failureCount']==0 and type(proof.get('checks')) is int and proof['checks']>0
        and proof.get('schema')=='leader-peer-expansion-independent-verification-v1' and proof['candidateId']==PARENT_ID
        and proof['registrationSha256']==protocol['registrationSha256'] and proof['protocolHash']==protocol['protocolHash']
        and proof['completionSha256']==sha(directory/'attempts/attempt-0001/completion.json')
        and proof['implementation']==protocol['implementation'] and proof['verifierSha256']==protocol['implementation'][PARENT_AUDITOR],
        'Parent independent proof is not accepted')
    required={PARENT_REGISTRATION,(directory/'attempts/attempt-0001/completion.json').relative_to(ROOT).as_posix(),
        (directory/'independent-result-verification.json').relative_to(ROOT).as_posix()}
    for item in receipt['files'].values():
        path=(directory/item['path']).resolve()
        require(path.is_relative_to(directory) and files.get(str(path))==item['sha256'],'Parent receipt file not exactly bound')
        required.add(path.relative_to(ROOT).as_posix())
    require(set(source['files'])==required,'Parent registration must bind the complete receipt and proof')
    require(proof['resultSha256']==receipt['files']['result']['sha256'],'Parent proof result differs')
    require(receipt['protocolHash']==protocol['protocolHash'] and receipt.get('attempt')==1
        and receipt['snapshotFingerprint']==protocol['binding']['snapshotFingerprint'],'Parent completed identity differs')
    parent.verify_completion(directory,receipt,protocol)
    _archive_check(directory/'execution-sources.zip',protocol['implementation'])
    require(sha(directory/'execution-sources.zip')==protocol['executionArchiveSha256'],'Parent whole archive differs')
    for name,expected in AUDIT_REFERENCES.items(): require(sha(_local(name))==expected,'Frozen parent/auditor changed: '+name)
    with _parent_context(_local(PARENT_REGISTRATION),sha(_local(PARENT_REGISTRATION))): raw=parent.load_binding()
    require(raw['identity']==protocol['binding'] and str(raw['workspace'])==protocol['workspace'],'Parent raw binding differs')
    files.update(raw['files'])
    for name in ('freeze-attempt.json','events.jsonl'):
        path=directory/name;files[str(path)]=sha(path)
    require(files[str(directory/'freeze-attempt.json')]==protocol['freezeAttemptSha256'],'Parent freeze changed')
    files[str(REGISTRATION.resolve())]=REGISTRATION_SHA256
    identity={'registrationSha256':REGISTRATION_SHA256,'parentProtocolHash':protocol['protocolHash'],
        'parentCompletionSha256':proof['completionSha256'],'parentAuditSha256':sha(directory/'independent-result-verification.json'),
        'parentPredicatesSha256':receipt['files']['predicates']['sha256'],'parentSelectionsSha256':receipt['files']['selections']['sha256'],
        'parentPeerGroupsSha256':receipt['files']['peerGroups']['sha256'],'snapshotFingerprint':raw['identity']['snapshotFingerprint'],
        'calendarHash':raw['identity']['calendarHash'],'symbolsSha256':raw['identity']['symbolsSha256'],'inputFilesSha256':files}
    return {'document':doc,'parentProtocol':protocol,'parentReceipt':receipt,'parentAudit':proof,'parentDirectory':directory,
        'parentAttempt':directory/'attempts/attempt-0001','raw':raw,'workspace':raw['workspace'],'symbols':raw['symbols'],
        'dates':raw['dates'],'calendar':raw['calendar'],'manifest':raw['manifest'],'files':files,'identity':identity}


def records(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        for line in stream:
            row=json.loads(line,object_pairs_hook=parent._object,parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON '+value)))
            require(isinstance(row,dict),'Stream row must be an object')
            yield row


def iter_source_days(binding):
    """One complete, ordered pass; never inspect old expansion or trade labels."""
    attempt=binding['parentAttempt'];symbols=binding['symbols'];inputs=binding['manifest']['inputs']
    streams={key:iter(records(attempt/name)) for key,name in
        {'predicates':'predicates.jsonl.gz','selections':'selection.jsonl.gz','peerGroups':'peer-groups.jsonl.gz'}.items()}
    calendar_index={d:i for i,d in enumerate(binding['calendar'])}
    line=0; formations=0
    for index,day in enumerate(binding['dates']):
        compact={};start=line+1
        for symbol in symbols:
            row=next(streams['predicates'],None);line+=1
            expected={**inputs['securities'][symbol],'benchmarkRawBarsSha256':inputs['benchmark']['rawBarsSha256']}
            require(isinstance(row,dict) and row.get('symbol')==symbol and row.get('date')==day
                and row.get('version')=='raw-leader-peer-expansion-v1' and row.get('certified') is False
                and row.get('calendarHash')==inputs['calendar']['sha256'] and row.get('sourceHashes')==expected
                and row.get('panelManifestSha256')==binding['raw']['identity']['panelManifestSha256'],
                'Parent predicate order/identity/source differs')
            outcome=row.get('currentParent')
            require(isinstance(outcome,dict) and outcome.get('symbol')==symbol and outcome.get('date')==day,
                'Current parent outcome differs from source row')
            compact[symbol]=project_parent(outcome)
        selection=next(streams['selections'],None)
        require(isinstance(selection,dict) and selection.get('date')==day and selection.get('activeExclusionApplied') is False
            and selection.get('noBackfill') is True,'Parent selection day/order differs')
        slots=selection.get('slots');require(isinstance(slots,list) and len(slots)<=5,'Invalid original parent Top5')
        source_groups=[];seen=set()
        for rank,slot in enumerate(slots,1):
            anchor=slot['symbol'];position=calendar_index[day]
            require(slot.get('date')==day and anchor in compact and anchor not in seen and type(slot.get('rank')) is int
                and slot['rank']==rank and position>=10
                and slot.get('formationDate')==binding['calendar'][position-10], 'Original Top5 source identity/rank/F differs')
            require(compact[anchor]['kind']=='signaled','Source anchor is not a current parent signal')
            seen.add(anchor)
            group=next(streams['peerGroups'],None)
            require(isinstance(group,dict) and group.get('date')==day and group.get('symbol')==anchor
                and group.get('rank')==rank and group.get('formationDate')==slot['formationDate']
                and group.get('groupSha256')==slot.get('groupSha256')==digest(group.get('group')),
                'Parent peer group linkage differs')
            require(group['group'].get('anchor')==anchor and group['group'].get('formationDate')==slot['formationDate'],
                'Parent frozen membership identity differs')
            source_groups.append(group);formations+=1
        # Baseline is the unmasked original Top5. The old expansion selection and
        # all old outcomes are intentionally absent from the new rule inputs.
        baseline=selection.get('selection',{}).get('baselineSignals',[])
        require([(s.get('symbol'),s.get('rank')) for s in baseline]==[(s['symbol'],s['rank']) for s in slots],
                'Original Top5 omitted from parent baseline')
        source={'parentProtocolHash':binding['identity']['parentProtocolHash'],
            'predicatesFileSha256':binding['identity']['parentPredicatesSha256'],
            'selectionsFileSha256':binding['identity']['parentSelectionsSha256'],
            'peerGroupsFileSha256':binding['identity']['parentPeerGroupsSha256'],
            'predicateStartLine':start,'predicateEndLine':line}
        yield day,index,compact,source_groups,source
    for key,stream in streams.items(): require(next(stream,None) is None,'Parent stream has extra rows: '+key)
    require(line==len(symbols)*len(binding['dates']) and formations==binding['parentAudit']['slotAccounting']['parentTop5'],
            'Inherited population/opportunity count differs')


def load_dataset(args,binding): return parent.load_dataset(args,binding['raw'])
def validate_dataset(dataset,binding): return parent.validate_dataset(dataset,binding['raw'])


def collect_signals(dataset,binding,attempt):
    validate_dataset(dataset,binding)
    study=FollowerConfirmationStudy(binding['dates'],binding['symbols'],formation_calendar=binding['calendar'])
    signals=[];counts=Counter({k:0 for k in ('formations','transitions','confirmations','sourcePredicateRows','sourceFormations','scoredSessions')})
    q_totals=Counter();kind_totals=Counter();formation_kinds=Counter();ownership_counts=Counter()
    notice=started=time.perf_counter()
    keys=('formations','transitions','episodeTerminals','confirmations','selections','coverage','signals')
    with ExitStack() as stack:
        writers={k:stack.enter_context(gzip.open(attempt/ATTEMPT_FILES[k],'xt',encoding='utf-8',compresslevel=1)) for k in keys}
        for day,index,parents,formations,source in iter_source_days(binding):
            result=study.step(day,index,parents,formations)
            formation_kinds.update(row['kind'] for row in result['formations'])
            ownership_counts.update(rawConfirmations=result['selection']['rawConfirmationCount'],
                uniqueMembers=result['selection']['uniqueMemberCount'],
                duplicateOwnedConfirmations=sum(len(r['discardedRawConfirmationIds']) for r in result['selection']['ownership']),
                membersWithMultipleSources=sum(len(r['allRawConfirmationIds'])>1 for r in result['selection']['ownership']))
            for key,field in (('formations','formations'),('transitions','transitions'),('confirmations','rawConfirmations')):
                for row in result[field]:
                    runtime.write_record(writers[key],{'date':day,'source':source,'record':row})
                    counts[key]+=1
            runtime.write_record(writers['selections'],{'date':day,'source':source,'selection':result['selection']})
            for row in result['signals']:
                require(row['signalDate']==day and row['strategyId'] in GROUPS,'Pure signal identity differs')
                signals.append(row);runtime.write_record(writers['signals'],row)
            q=Counter(p['eligibleParentQ'] for p in parents.values());kinds=Counter(p['kind'] for p in parents.values())
            coverage={'date':day,'targetIndex':index,'source':source,'sourcePredicateRows':len(parents),'sourceFormations':len(formations),
                'parentQCounts':{s:q[s] for s in ('true','false','unknown')},'parentKindCounts':dict(kinds),
                'formations':len(result['formations']),'transitions':len(result['transitions']),'rawConfirmations':len(result['rawConfirmations']),
                'selectedSignals':{g:sum(s['strategyId']==g for s in result['signals']) for g in GROUPS},
                'episodeCounts':result['episodeCounts'],'certified':False,'targetAchieved':False}
            runtime.write_record(writers['coverage'],coverage)
            counts.update(sourcePredicateRows=len(parents),sourceFormations=len(formations),scoredSessions=1)
            q_totals.update(q);kind_totals.update(kinds)
            if time.perf_counter()-notice>=30:
                print(f'Consumed {index+1}/{len(binding["dates"])} frozen parent days; {counts["sourcePredicateRows"]} rows',flush=True)
                notice=time.perf_counter()
        terminal=study.finish(cutoff=binding['dates'][-1])
        for row in terminal['transitions']:
            runtime.write_record(writers['transitions'],{'date':binding['dates'][-1],'source':None,'phase':'cutoff','record':row})
            counts['transitions']+=1
        for row in terminal['episodes']: runtime.write_record(writers['episodeTerminals'],row)
    episode_accounting={'sourceFormations':counts['sourceFormations'],'formationKinds':dict(formation_kinds),
        'episodes':len(terminal['episodes']),
        'anchorGateCounts':dict(Counter(e['anchorGate'] for e in terminal['episodes'])),
        'memberStateCounts':dict(Counter(m['state'] for e in terminal['episodes'] for m in e['members'])),
        'ownership':{k:ownership_counts[k] for k in ('rawConfirmations','uniqueMembers','duplicateOwnedConfirmations','membersWithMultipleSources')}}
    return signals,{'scoringSeconds':time.perf_counter()-started,**dict(counts),'terminalEpisodes':len(terminal['episodes']),
        'episodeAccounting':episode_accounting,
        'parentQCounts':{s:q_totals[s] for s in ('true','false','unknown')},'parentKindCounts':dict(kind_totals),
        'selectedSignals':{g:sum(s['strategyId']==g for s in signals) for g in GROUPS},
        'inheritedParentPredicatesRecomputed':False,'inheritedPeerCorrelationsRecomputed':False}


def evaluate_signals(dataset,config,signals):
    result=runtime.evaluate_signals(dataset,deepcopy(config),signals)
    risk=PathRiskStudy(dataset,config)
    paths=[risk.evaluate(e) for e in result['events'] if e['entry']['status']=='filled']
    result.update(pathRiskEvents=paths,pathRiskSummary=summarize_paths(paths,horizons=HORIZONS),
        actionEvidence='unverified',comparableRightsVerified=False,historicalEligibilityVerified=False,
        resultMeaning='inherited-parent/raw-price-follower-sequence;registered-legacy-cost-opening-proxy;not-economic-profit-certification')
    for group in result['groups']:
        selected=[s for s in signals if s['strategyId']==group['strategyId']]
        group.update(distinctSignalSymbols=len({s['symbol'] for s in selected}),distinctSignalDates=len({s['signalDate'] for s in selected}),
            role={CANDIDATE_ID:'continuously-strong-anchor-filtered-member',MEMBER_CONTROL_ID:'member-confirmation-shadow',
                  ANCHOR_CONTROL_ID:'owning-anchor-at-candidate-time'}[group['strategyId']])
    return result


def slot_dispositions(selections,signals,result):
    identity=lambda e:(e['strategyId'],e['symbol'],e['signalDate'])
    selected={identity(e):e for e in signals};events={identity(e):e for e in result['events']}
    suppressed={identity(e):e for e in result['overlapSuppressed']}
    require(len(selected)==len(signals) and len(events)==len(result['events']) and len(suppressed)==len(result['overlapSuppressed'])
        and not set(events)&set(suppressed) and set(selected)==set(events)|set(suppressed),'Every selected signal needs exactly one disposition')
    accounted=set();rows=[]
    def side(sid,slot):
        if sid==CANDIDATE_ID and not slot['candidateAdmitted']:
            return {'status':'filteredAnchorUnknown' if slot['anchorGate']=='unknown' else 'filteredAnchorCancelled','eventId':None,'entryStatus':None}
        if sid==ANCHOR_CONTROL_ID and not slot['anchorControlAdmitted']:
            if slot['anchorControlDisposition']=='mappedAnchorCollision':
                return {'status':'mappedAnchorCollision','eventId':None,'entryStatus':None,'retainedRank':slot['anchorControlRetainedRank']}
            return {'status':'filteredAnchorUnknown' if slot['anchorGate']=='unknown' else 'filteredAnchorCancelled','eventId':None,'entryStatus':None}
        symbol=slot['anchorSymbol'] if sid==ANCHOR_CONTROL_ID else slot['memberSymbol']
        key=sid,symbol,slot['date']
        require(key in selected and key not in accounted,'Slot missing, duplicated or maps to another signal')
        accounted.add(key)
        if key in suppressed:
            e=suppressed[key]
            return {'status':'activeSuppressed','eventId':None,'entryStatus':None,'reason':e['reason'],
                'blockedByEventId':e['blockedByEventId'],'activeUntil':e['activeUntil']}
        e=events[key]
        return {'status':'attempted','eventId':e['eventId'],'entryStatus':e['entry']['status'],'entryReason':e['entry'].get('reason'),
            'entryDate':e['entry'].get('date'),'targetDate':e['outcomes']['nextOpenNet30']['hitDate'],'terminal':deepcopy(e['terminal'])}
    for wrapper in selections:
        for slot in wrapper['selection']['slots']:
            require(slot['date']==wrapper['date'],'Slot date differs')
            rows.append({**slot,'candidateDisposition':side(CANDIDATE_ID,slot),
                'memberBaselineDisposition':side(MEMBER_CONTROL_ID,slot),'anchorControlDispositionDetails':side(ANCHOR_CONTROL_ID,slot),
                'interpretation':'original-global-member-Top5;three-independent-active-policies;not-randomized-or-fill-matched'})
    require(accounted==set(selected),'Original slot ledger must cover every selected signal')
    summary={'globalTop5Slots':len(rows),'candidateAdmitted':sum(r['candidateAdmitted'] for r in rows),
        'memberBaselineAdmitted':len(rows),'anchorControlAdmitted':sum(r['anchorControlAdmitted'] for r in rows),
        'mappedAnchorCollisions':sum(r['anchorControlDisposition']=='mappedAnchorCollision' for r in rows)}
    for name,field in (('candidate','candidateDisposition'),('memberBaseline','memberBaselineDisposition'),('anchorControl','anchorControlDispositionDetails')):
        sides=[r[field] for r in rows]
        summary[name+'Dispositions']=dict(Counter(s['status'] for s in sides))
        summary[name+'EntryStatuses']=dict(Counter(s['entryStatus'] for s in sides if s['entryStatus'] is not None))
    return rows,summary


def implementation_hashes(binding):
    require(sha(__file__)==_LOADED_RUNNER_SHA and sha(ROOT/'service/signal_leader_follower_confirmation.py')==_LOADED_RULE_SHA,
        'Loaded follower execution code changed')
    hashes=dict(binding['parentProtocol']['implementation'])
    for name,expected in {**hashes,**AUDIT_REFERENCES}.items(): require(sha(_local(name))==expected,'Frozen inherited execution dependency differs: '+name)
    names=set(hashes)|set(AUDIT_REFERENCES)|set(ADDITIONAL_IMPLEMENTATION)|{
        Path(__file__).resolve().relative_to(ROOT.resolve()).as_posix(),REGISTRATION.resolve().relative_to(ROOT.resolve()).as_posix(),
        'service/signal_leader_follower_confirmation.py','tests/test_signal_leader_follower_confirmation.py',
        'tests/test_signal_leader_follower_confirmation_runner.py'}
    return {name:sha(_local(name)) for name in sorted(names)}


def _archive(path,hashes):
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
        for name,expected in hashes.items():
            raw=_local(name).read_bytes();require(hashlib.sha256(raw).hexdigest()==expected,'Source changed while archiving')
            archive.writestr(name,raw)


def _safe_args(args,binding):
    workspace,output=Path(args.workspace).resolve(),Path(args.output).resolve()
    require(args.diagnostic_unverified_history is True and workspace==binding['workspace'],'Use registered workspace and explicit diagnostic flag')
    with closing(sqlite3.connect((workspace/'market.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        source=Path(json.loads(db.execute("SELECT value FROM meta WHERE key='path'").fetchone()[0])).resolve()
    for protected in (workspace,source,binding['parentDirectory'],binding['raw']['directory']):
        require(not output.is_relative_to(protected) and not protected.is_relative_to(output),'Output overlaps immutable source')
    require(all(not Path(p).is_relative_to(output) for p in binding['files']) and
        all(not _local(p).is_relative_to(output) for p in implementation_hashes(binding)),'Output contains input/code')
    return workspace,output


def _inventory(output,completed=False):
    allowed={'.run.lock','freeze-attempt.json','protocol.json','execution-sources.zip','events.jsonl'}
    if completed:allowed.add('attempts')
    for path in output.iterdir():
        report=completed and path.is_file() and (path.name=='independent-result-verification.json' or
            path.name.startswith('independent-result-verification-failed-') and path.suffix=='.json')
        require(path.name in allowed or report,'Unexpected output/failed attempt; restart forbidden')
        require(not path.is_symlink() and not (hasattr(path,'is_junction') and path.is_junction()),'Linked output forbidden')
    if completed:
        attempt=output/'attempts/attempt-0001'
        require({p.name for p in (output/'attempts').iterdir()}=={'attempt-0001'} and
            {p.name for p in attempt.iterdir()}==set(ATTEMPT_FILES.values())|{'completion.json'},'Completed inventory differs')
        for path in (output/'attempts',attempt,*attempt.iterdir()):
            require(not path.is_symlink() and not (hasattr(path,'is_junction') and path.is_junction()),'Linked attempt forbidden')


def _verify_protocol(output,binding,workspace):
    protocol=read_json(output/'protocol.json')
    require(protocol['schema']=='leader-follower-confirmation-development-v1' and
        protocol['protocolHash']==digest({k:v for k,v in protocol.items() if k!='protocolHash'}),'Protocol hash/schema differs')
    require(protocol['registration']==binding['document'] and protocol['registrationSha256']==REGISTRATION_SHA256 and
        protocol['binding']==binding['identity'] and protocol['config']==binding['document']['config'] and
        protocol['workspace']==str(workspace) and protocol['strategyOrder']==[CANDIDATE_ID] and protocol['groups']==list(GROUPS)
        and protocol.get('certified') is False and protocol.get('targetAchieved') is False,'Protocol identity/config differs')
    require(protocol['implementation']==implementation_hashes(binding) and sha(output/'execution-sources.zip')==protocol['executionArchiveSha256'],
        'Implementation or archive changed')
    _archive_check(output/'execution-sources.zip',protocol['implementation'])
    marker=read_json(output/'freeze-attempt.json')
    require(sha(output/'freeze-attempt.json')==protocol['freezeAttemptSha256'] and marker['candidateId']==CANDIDATE_ID
        and marker['registrationSha256']==REGISTRATION_SHA256,'Freeze marker differs')
    return protocol


def _fail(output,phase,error):
    write_json(output/'failure.json',{'phase':phase,'failedAt':_now(),'errorType':type(error).__name__,'error':str(error),'automaticRetryAllowed':False})


def freeze(args):
    binding=load_binding();workspace,output=_safe_args(args,binding)
    output.mkdir(parents=True,exist_ok=True)
    with source_tools.output_lock(output):
        require({p.name for p in output.iterdir()}=={'.run.lock'},'Exclusive freeze requires a new empty output')
        write_json(output/'freeze-attempt.json',{'candidateId':CANDIDATE_ID,'registrationSha256':REGISTRATION_SHA256,
            'startedAt':_now(),'certified':False,'targetAchieved':False})
        try:
            implementation=implementation_hashes(binding);_archive(output/'execution-sources.zip',implementation)
            protocol={'schema':'leader-follower-confirmation-development-v1','createdAt':_now(),'registration':binding['document'],
                'registrationSha256':REGISTRATION_SHA256,'binding':binding['identity'],'workspace':str(workspace),
                'config':deepcopy(binding['document']['config']),'strategyOrder':[CANDIDATE_ID],'groups':list(GROUPS),
                'implementation':implementation,'pythonVersion':sys.version,'stage':'reused-development-diagnostic',
                'inheritedParentPredicatesRecomputed':False,'inheritedPeerCorrelationsRecomputed':False,
                'certified':False,'targetAchieved':False,'executionArchiveSha256':sha(output/'execution-sources.zip'),
                'freezeAttemptSha256':sha(output/'freeze-attempt.json')}
            protocol['protocolHash']=digest(protocol)
            require(load_binding()['identity']==binding['identity'] and implementation_hashes(binding)==implementation,'Input/code drift during freeze')
            write_json(output/'protocol.json',protocol);return protocol
        except Exception as error:_fail(output,'freeze',error);raise


def verify_completion(output,receipt,protocol):
    _inventory(output,completed=True)
    paths={key:'attempts/attempt-0001/'+name for key,name in ATTEMPT_FILES.items()}
    paths.update(protocol='protocol.json',executionSources='execution-sources.zip')
    require(receipt.get('protocolHash')==protocol['protocolHash'] and receipt.get('attempt')==1 and
        receipt.get('snapshotFingerprint')==protocol['binding']['snapshotFingerprint'] and
        receipt.get('freezeAttemptSha256')==protocol['freezeAttemptSha256'] and set(receipt['files'])==set(paths)
        and receipt.get('certified') is False and receipt.get('targetAchieved') is False,'Completion identity differs')
    for key,name in paths.items():require(receipt['files'][key]=={'path':name,'sha256':sha(output/name)},'Completed artifact differs: '+key)
    marker=read_json(output/'attempts/attempt-0001/attempt.json')
    require(marker['attempt']==1 and marker['candidateId']==CANDIDATE_ID and marker['protocolHash']==protocol['protocolHash'],'Attempt marker differs')
    journal=[json.loads(line,object_pairs_hook=parent._object) for line in (output/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    require([r.get('event') for r in journal]==['study_started','scoring_completed','study_succeeded'] and
        all(r['protocolHash']==protocol['protocolHash'] for r in journal) and journal[-1]['receipt']==receipt,'Completion journal differs')


def execute(args):
    binding=load_binding();workspace,output=_safe_args(args,binding)
    require((output/'protocol.json').is_file(),'Freeze and review before execution')
    with source_tools.output_lock(output):
        protocol=_verify_protocol(output,binding,workspace);protocol_sha=sha(output/'protocol.json')
        attempt=output/'attempts/attempt-0001'
        if (attempt/'completion.json').is_file():
            verify_completion(output,read_json(attempt/'completion.json'),protocol);return 0
        _inventory(output)
        require(not (output/'attempts').exists(),'Failed/interrupted attempt retained; restart forbidden')
        attempt.mkdir(parents=True)
        write_json(attempt/'attempt.json',{'attempt':1,'candidateId':CANDIDATE_ID,'protocolHash':protocol['protocolHash'],
            'startedAt':_now(),'certified':False,'targetAchieved':False})
        source_tools.event(output,protocol['protocolHash'],'study_started',attempt=1)
        try:
            dataset=load_dataset(args,binding)
            signals,performance=collect_signals(dataset,binding,attempt)
            source_tools.event(output,protocol['protocolHash'],'scoring_completed',**performance)
            tick=time.perf_counter();result=evaluate_signals(dataset,protocol['config'],signals)
            performance['eventEvaluationSeconds']=time.perf_counter()-tick
            slot_rows,accounting=slot_dispositions(records(attempt/'selection.jsonl.gz'),signals,result)
            with gzip.open(attempt/'slot-outcomes.jsonl.gz','xt',encoding='utf-8',compresslevel=1) as stream:
                for row in slot_rows:runtime.write_record(stream,row)
            result.update(protocolHash=protocol['protocolHash'],snapshotFingerprint=dataset['fingerprint'],strategyOrder=[CANDIDATE_ID],
                stage='reused-development-diagnostic',pilot=False,diagnosticUnverifiedHistory=True,slotAccounting=accounting,
                episodeAccounting=performance['episodeAccounting'],
                inheritedParentPredicatesRecomputed=False,inheritedPeerCorrelationsRecomputed=False,certified=False,targetAchieved=False)
            with gzip.open(attempt/'result.json.gz','xt',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,allow_nan=False,separators=(',',':'))
            summary={k:v for k,v in result.items() if k not in ('events','overlapSuppressed','pathRiskEvents')}
            summary.update(performance=performance,ranking=None,selectionEligible=False);write_json(attempt/'summary.json',summary)
            final=load_binding()
            require(final['identity']==binding['identity'] and sha(output/'protocol.json')==protocol_sha,'Post-run source/protocol drift')
            _verify_protocol(output,final,workspace);validate_dataset(dataset,final)
            paths={key:'attempts/attempt-0001/'+name for key,name in ATTEMPT_FILES.items()}
            paths.update(protocol='protocol.json',executionSources='execution-sources.zip')
            receipt={'protocolHash':protocol['protocolHash'],'snapshotFingerprint':dataset['fingerprint'],'attempt':1,
                'freezeAttemptSha256':protocol['freezeAttemptSha256'],'certified':False,'targetAchieved':False,
                'files':{key:{'path':name,'sha256':sha(output/name)} for key,name in paths.items()}}
            write_json(attempt/'completion.json',receipt);source_tools.event(output,protocol['protocolHash'],'study_succeeded',receipt=receipt)
            return 0
        except Exception as error:
            _fail(output,'execute',error)
            write_json(attempt/'failure.json',{'protocolHash':protocol['protocolHash'],'errorType':type(error).__name__,'error':str(error),
                'failedAt':_now(),'automaticRetryAllowed':False})
            source_tools.event(output,protocol['protocolHash'],'study_failed',errorType=type(error).__name__,error=str(error));raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,default=ROOT/'artifacts/strategy-search/source-workspace')
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/strategy-search/signal-leader-follower-confirmation-v1')
    parser.add_argument('--diagnostic-unverified-history',action='store_true');parser.add_argument('--freeze-only',action='store_true')
    args=parser.parse_args(argv)
    if args.freeze_only:freeze(args);return 0
    return execute(args)


if __name__=='__main__':raise SystemExit(main())
