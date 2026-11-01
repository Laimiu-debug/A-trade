"""Record the completed descriptive study without counting a strategy trial."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'artifacts/strategy-search'
OUT = BASE/'joint-cohort-regime-v1'
PROGRAM = BASE/'program.json'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def ref(path):
    return {'path':Path(path).relative_to(ROOT).as_posix(),'sha256':sha(path)}


def write(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.write('\n')


def bound(item,base=ROOT):
    path=(base/item['path']).resolve()
    assert path.is_relative_to(base) and sha(path)==item['sha256'],item['path']
    return path


def finish():
    plan=read(OUT/'plan.json')
    before=plan['previousTurnEvidence']['sha256']
    if (OUT/'delivery.json').exists():
        delivery=read(OUT/'delivery.json')
        for name,digest in delivery['files'].items():
            assert sha(ROOT/name)==digest,name
        after=sha(OUT/'program-after.json')
        assert after==delivery['programAfterSha256'] and sha(PROGRAM) in (before,after)
        if sha(PROGRAM)==before:
            pending=BASE/'program.joint-cohort-regime-pending.json'
            pending.write_bytes((OUT/'program-after.json').read_bytes())
            os.replace(pending,PROGRAM)
        return {'completed':True,'recovered':True,'delivery':ref(OUT/'delivery.json')}
    assert sha(PROGRAM)==before,'Registry changed during study'
    assert not (OUT/'program-after.json').exists(),'Partial finalization needs inspection'
    report,proof,render,review=[read(OUT/n) for n in ('readout.json','validation.json','render-receipt.json','visual-review.json')]
    for item in plan['sourcePins']+plan['inputPins']:
        bound(item)
    assert proof['passed'] and proof['readoutSha256']==sha(OUT/'readout.json')
    assert proof['planSha256']==sha(OUT/'plan.json')
    for name,digest in proof['sourcePins'].items():
        assert sha(ROOT/name)==digest,name
    phase_context=read(OUT/'continuous-phase-context.json')
    phase_proof=read(OUT/'continuous-phase-validation.json')
    assert phase_proof['passed']
    assert phase_proof['contextSha256']==sha(OUT/'continuous-phase-context.json')
    assert phase_proof['posthocFollowup'] is True
    for item in phase_proof['sourcePins']:
        bound(item)
    for name in ('sourceReadout','sourceValidation','extractor'):
        bound(phase_context[name])
    assert all(len(report[k])==v for k,v in plan['expectedRows'].items())
    assert report['recordCount']==16310 and report['formationCount']==8155 and report['dailyCount']==2907
    assert not any(report[k] for k in ('holdoutClaim','certified','targetAchieved'))
    assert report['selectedState'] is None
    assert report['trialAccounting']['newAccounts']==report['trialAccounting']['newStrategyTrials']==0
    for name,digest in render['inputPins'].items():
        assert sha(OUT/name)==digest,name
    for group in ('fragments','payloads'):
        for item in render[group].values():
            bound(item,OUT)
    assert review['passed']
    qa_path=bound({'path':review['qaPath'],'sha256':review['qaSha256']})
    qa=read(qa_path)
    assert qa['passed'] and qa['renderReceiptSha256']==sha(OUT/'render-receipt.json')
    assert qa['scriptSha256']==sha(ROOT/'scripts/verify-joint-cohort-regime-preview.cjs')
    assert render['builderSha256']==sha(ROOT/'scripts/render-joint-cohort-regime.py')
    for item in review['images']:
        bound(item)
    decision={
        'schema':'joint-cohort-regime-v1-decision','createdAt':datetime.now(timezone.utc).isoformat(),
        'readout':ref(OUT/'readout.json'),'validation':ref(OUT/'validation.json'),
        'findings':[
            'After the descriptive 9.24 boundary, the neither-positive-majority day share falls in all three source boards. Top-only positive-majority does not increase consistently. This measures two rank deciles, not all-market prosperity or fund identity.',
            'The older relative-stronger label often combines days where neither group has a positive majority. Relative strength alone cannot identify an improving absolute environment.',
            'Top-only positive-majority formations have negative total closed PnL in all11 nonempty board-period-policy cells; the twelfth has no entry. These overlapping views are not independent replications, counts are small and some annual cells are positive. Open positions and all other states remain in the readout. This is an observed entry-context association, not an executable filter result.',
            'Positive state buckets vary across periods, boards and exits. The study does not establish a stable small-loss/large-win strategy.',
            'During 2024-09-24 through 2024-10-08, all six existing new-period accounts show positive marked equity movement; five have no buy or sell fill. This is a carried-position revaluation observation, not evidence of newly triggered profitable entries. Both ChiNext paths retain valuation flags on all six sessions.'
        ],
        'continuousPhaseEvidence':{'context':ref(OUT/'continuous-phase-context.json'),
            'validation':ref(OUT/'continuous-phase-validation.json'),
            'timing':'Post-hoc descriptive follow-up, separately bound; the original joint-state protocol and result remain unchanged.'},
        'nextResearch':{
            'status':'market-cycle-explanation-before-another-entry-or-exit-filter',
            'question':'Can broader participation, liquidity and persistence explain the large-cycle differences that the five-session extreme-cohort feedback does not?',
            'dataWork':'Reuse the same six source cohorts to extend existing whole-sample daily breadth, matched breadth change and relative amount definitions across all three boards. The old 3141-stock main-board panel is only a shared background proxy; it cannot stand in for technology-board participation.',
            'leadershipWork':'Separate sustained membership of the contemporaneous strong-stock queue from its five-day price feedback. Check previous implementations before adding a definition; use full available samples and fixed clocks, not final winners.',
            'controls':'Retain both completed exit accounts, all 16 descriptive phases and actual continuous equity. Distinguish where a signal formed from when a carried holding gained or lost value.',
            'deferredHypothesis':'Skipping known topOnlyPositiveMajority remains a possible risk filter, not a promoted strategy. It covers341/8155 original formations and does not explain the dominant neither-state losses and gains. Do not open twelve accounts merely to repair this small observed loss pocket.',
            'sampleStatus':'All 2022-2025 data remain exposed development histories. No date-coded bull switch, profit target, investor identity or independent validation claim.'
        },
        'newStrategyTrials':0,'newAccounts':0,'selectedState':None,'targetAchieved':False}
    # The decision above is checked against all cells, not a manually selected subset.
    top=[r for r in report['overallRows'] if r['jointState']=='topOnlyPositiveMajority']
    assert len(top)==12 and all(r['closedPnlAmount']<0 for r in top if r['closed'])
    day={ (r['board'],r['formationEpoch'],r['jointState']):r for r in report['epochDayRows'] if r['period']=='2024-2025'}
    assert all(day[b,'from20240924','neitherPositiveMajority']['daySharePct']<day[b,'before20240924','neitherPositiveMajority']['daySharePct'] for b in ('main','chinext','star'))
    rally=[r for r in phase_context['rows'] if r['phaseId']=='2024-09-policy-rally']
    assert len(rally)==6 and all(r['returnPct']>0 for r in rally)
    assert sum(r['buyFillCount']==r['sellFillCount']==0 for r in rally)==5
    assert all(r['valuationFlagDays']==r['observedSessions']==6 for r in rally if r['board']=='chinext')
    write(OUT/'research-decision.json',decision)
    (OUT/'program-before.json').write_bytes(PROGRAM.read_bytes())
    program=read(PROGRAM)
    assert program['goalStatus']=='active' and program['progress']['completedFixedCandidateTrials']==38
    assert program['progress']['currentScopeTestsPassed']==1026
    program['updatedAt']=datetime.now(timezone.utc).isoformat()
    program['progress']['lastTurnClassification']='progress: completed a preregistered joint-cohort attribution of all 16310 existing records and 2907 context days, independently verified; no new account or candidate trial.'
    program['progress']['nextStage']='Extend the existing daily whole-sample breadth, matched breadth-change and relative-amount definitions to the same six frozen three-board sources, with no new parameters or trading rule. The joint top/bottom five-day feedback did not explain the large-cycle payoff reversal, while the policy-rally equity gains mostly came from carried holdings. Compare full-board participation and contemporaneous strong-queue persistence across all16 descriptive phases before another filter. Do not start a top-only exclusion batch merely to repair341 observed formations. Preserve all prior accounts, unknowns and development-data qualifications; see joint-cohort-regime-v1/research-decision.json.'
    program['progress']['jointCohortRegime']={'status':'completed-descriptive-development-diagnostic',
        'plan':ref(OUT/'plan.json'),'readout':ref(OUT/'readout.json'),'validation':ref(OUT/'validation.json'),
        'decision':ref(OUT/'research-decision.json'),'newStrategyTrials':0,'newAccounts':0,
        'formations':8155,'accountRecords':16310,'contextDays':2907,'targetAchieved':False}
    write(OUT/'program-after.json',program)
    paths=[ROOT/'docs/joint-cohort-regime-v1.json',ROOT/'scripts/diagnose-joint-cohort-regime.py',
        ROOT/'scripts/verify-joint-cohort-regime.py',ROOT/'scripts/render-joint-cohort-regime.py',
        ROOT/'scripts/verify-joint-cohort-regime-preview.cjs',Path(__file__).resolve(),
        ROOT/'scripts/diagnose-reclaim-relative-feedback-context.py',ROOT/'scripts/verify-reclaim-relative-feedback-context.py',
        ROOT/'scripts/extract-joint-cohort-continuous-phases.py',ROOT/'scripts/verify-joint-cohort-continuous-phases.py']
    source_pins={p.relative_to(ROOT).as_posix():sha(p) for p in paths}
    archive=OUT/'execution-sources.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as stream:
        for p in paths:
            stream.write(p,p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(archive) as stream:
        assert all(hashlib.sha256(stream.read(p)).hexdigest()==digest for p,digest in source_pins.items())
    write(OUT/'execution-sources-manifest.json',{'sourcePins':source_pins,'archive':ref(archive),
        'analysisSourceFreeze':plan['sourcePins'],'auditAndDisplaySourcesArchivedAtDelivery':True})
    files={p.relative_to(ROOT).as_posix():sha(p) for p in OUT.rglob('*') if p.is_file()}
    write(OUT/'delivery.json',{'schema':'joint-cohort-regime-v1-delivery','completedAt':datetime.now(timezone.utc).isoformat(),
        'planSha256':sha(OUT/'plan.json'),'programBeforeSha256':before,'programAfterSha256':sha(OUT/'program-after.json'),
        'files':files,'newStrategyTrials':0,'newAccounts':0,'candidateBatches':38,'registeredProductionTests':1026,
        'goalStatus':'active','targetAchieved':False})
    pending=BASE/'program.joint-cohort-regime-pending.json'
    with pending.open('xb') as stream:
        stream.write((OUT/'program-after.json').read_bytes())
    os.replace(pending,PROGRAM)
    return {'completed':True,'delivery':ref(OUT/'delivery.json'),'program':ref(PROGRAM),'candidateBatches':38,'goalStatus':'active'}


if __name__=='__main__':
    print(json.dumps(finish(),ensure_ascii=False))
