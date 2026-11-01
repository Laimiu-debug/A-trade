"""Exact frozen schedules with a lossless execution-only securities projection.

Formation remains the full4999-stock matrix. Only securities absent from every
frozen opportunity stream are omitted from account parsing/hashing. Two completed
full-population reference accounts are retained for exact financial equivalence.
"""
from argparse import ArgumentParser
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_dual_frozen_execution_driver',ROOT/'scripts/run-guiyin-dual-path-system.py')
P=importlib.util.module_from_spec(spec);sys.modules[spec.name]=P;spec.loader.exec_module(P)
ORIGINAL=P.OUT
OUT=ORIGINAL.parent/'attempt-002'


def register():
    if OUT.exists():raise ValueError('Preserve existing projected execution')
    p=P.verify();inputs=P.read(ORIGINAL/'input-validation.json')
    refs=[P.ref(path) for path in sorted(ORIGINAL.rglob('completion.json'))]
    incomplete=[str(path.parent.relative_to(ROOT)) for path in ORIGINAL.rglob('run-start.json') if not (path.parent/'completion.json').exists()]
    symbols=sorted({s['symbol'] for g in inputs['groups'] for s in P.read(P.bound(g['schedule']))})
    data=P.read(P.bound(p['sources']['dataset']));selection_count=len(data['securities'])
    projected={**data,'securities':[s for s in data['securities'] if s['symbol'] in set(symbols)]}
    check=len(projected['securities'])==len(symbols)
    if not check:raise ValueError('Projection lost a frozen candidate')
    projected['fingerprint']=str(data.get('fingerprint',''))+':frozen-signal-union-projection-v1'
    projected['executionProjection']=dict(fullSource=p['sources']['dataset'],selectionUniverse=selection_count,executionSymbols=len(symbols),allSchedulesFrozenBeforeProjection=True)
    OUT.mkdir(parents=True)
    P.write(OUT/'account-data.json.gz',projected)
    source_refs={**p['sources'],'fullDataset':p['sources']['dataset'],'dataset':P.ref(OUT/'account-data.json.gz')}
    plan={**p,'schema':'guiyin-dual-path-lossless-execution-projection-plan-v1','registeredAt':P.now(),'sources':source_refs,
          'sourcePins':{**p['sourcePins'],'scripts/run-guiyin-dual-path-projected.py':P.sha(Path(__file__))},
          'originalFullExecutionPlan':P.ref(ORIGINAL/'plan.json'),
          'computationalProjection':dict(parameterChanges=False,selectionChanges=False,universeUsedForFormation=selection_count,
              executionSymbols=len(symbols),completedFullPopulationReferences=refs,interruptedAccountFolders=incomplete,
              completionRequiresExactFullReferenceTradeCurvePositionAndClosedCycleMatch=True,
              qualityCoverageDenominatorChanges='Account parse population only; full selection cohort/market breadth remains frozen and unchanged',
              ruleOutcomesKnownBeforeProjection=True,noOutcomeBasedRuleChanges=True)}
    P.write(OUT/'plan.json',plan)
    P.write(OUT/'input-validation.json',{**inputs,'plan':P.ref(OUT/'plan.json'),'computationalProjectionPlan':P.ref(OUT/'plan.json'),'originalInputValidation':P.ref(ORIGINAL/'input-validation.json')})
    P.write(OUT/'previous-registry.json',P.read(P.REGISTRY))
    for path in plan['sourcePins']:
        dest=OUT/'frozen-code'/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/path).read_bytes())
    P.emit(dict(projectionFrozen=True,selectionStocks=selection_count,executionStocks=len(symbols),fullReferences=len(refs),interruptedFolders=incomplete,parametersChanged=False))


def equivalence():
    p=P.verify();checks=[]
    for reference in p['computationalProjection']['completedFullPopulationReferences']:
        full_folder=P.bound(reference).parent
        rel=full_folder.relative_to(ORIGINAL)
        old=P.read(full_folder/'result.json.gz');new=P.read(OUT/rel/'result.json.gz')
        for key in ('trades','curve','closedTrades','positions'):
            if old[key]!=new[key]:raise ValueError('Financial projection mismatch:'+str(rel)+':'+key)
        if old['research']['opportunities']!=new['research']['opportunities']:raise ValueError('Opportunity behavior changed')
        checks.append(dict(account=str(rel).replace('\\','/'),fullResult=P.ref(full_folder/'result.json.gz'),projectedResult=P.ref(OUT/rel/'result.json.gz'),
                           everyFillEveryCurveEveryPositionEveryClosedCycleAndOpportunityExactlyEqual=True))
    P.write(OUT/'computational-equivalence.json',dict(passed=True,checks=checks,parametersChanged=False,selectionUniverseUnchanged=True,
          interpretation='Exact financial behavior in all completed full-source references; account-only source coverage denominators differ by declared computational population'))
    return len(checks)


def finalize():
    references=equivalence()
    P.finalize()
    P.write(OUT/'execution-counts.json',dict(fixedHypotheses=1,distinctConfigurations=8,completedFullPopulationReferenceAccounts=references,
          completedProjectedAccounts=8,totalCompletedAccountExecutions=8+references,
          interruptedFolders=P.read(OUT/'plan.json')['computationalProjection']['interruptedAccountFolders'],newOutcomeBasedTuning=0))
    registry=P.read(P.REGISTRY)
    registry.update(newAccountsThisContinuation=registry['newAccountsThisContinuation']+references,
                    completedAccountExecutionsThisStudy=8+references,distinctConfigurationsThisStudy=8)
    registry['latestStudy'].update(computationalEquivalence=P.ref(OUT/'computational-equivalence.json'),executionCounts=P.ref(OUT/'execution-counts.json'))
    registry['activeStudy']['newAccountsRun']=8+references
    P.REGISTRY.write_text(json.dumps(registry,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    parser=ArgumentParser();parser.add_argument('command',choices=('register','run','finalize'));args=parser.parse_args()
    if args.command=='register':register()
    else:
        P.OUT=OUT
        P.run_accounts() if args.command=='run' else finalize()
