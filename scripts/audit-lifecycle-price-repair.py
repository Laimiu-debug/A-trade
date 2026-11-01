"""Matched structural-price numerical repair audit; no strategy selection changes."""
from argparse import ArgumentParser
from copy import deepcopy
from decimal import Decimal,ROUND_HALF_UP
import gc
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m


A=module('_prepared_base_parent_readonly_audit','scripts/audit-market-sector-stock-accounts.py')
U=A.U
read,write,ref,bound,sha,emit=(getattr(U,k) for k in ('read','write','ref','bound','sha','emit'))
L=module('_prepared_base_independent_money','scripts/verify-market-sector-stock-ledger.py')


P=module('_lifecycle_common_opening_oracle','scripts/audit-prepared-base-system.py')
audit_openings=P.audit_openings


def audit_projection(system,lifecycle,original,is_control):
    original_by_date={d['date']:d for d in original['daily']}
    expected=[]
    for d in lifecycle['daily']:
        expected.append({'date':d['date'],'snapshotId':d['snapshotId'],
            'market':{k:d['market'][k] for k in ('admission','riskExit','crisisCount')},
            'selectedSectors':original_by_date[d['date']]['selectedSectors'] if is_control else d['selectedSectors'],
            'sectors':[{k:s[k] for k in ('sectorCode','failCount','riskExit','lifecycle')} for s in d['sectors']]})
    L.equal(system['daily'],expected,'Every executed industry/market context projection')
    return {'decisionDays':len(expected),'industryExitStates':sum(len(d['sectors']) for d in expected),'controlRetainsOriginalAdmission':is_control}


def audit(study,audit_id):
    folder=study/('audit-'+audit_id)
    if folder.exists():raise ValueError('Preserve prior audit attempt')
    folder.mkdir();planpath,inputpath=study/'plan.json',study/'input-validation.json'
    plan,inputs=read(planpath),read(inputpath)
    for p,h in plan['sourcePins'].items():bound({'path':p,'sha256':h})
    for v in plan['bindings']:bound(v)
    if not inputs['passed'] or inputs['planSha256']!=sha(planpath) or not inputs['allSixSchedulesFrozenBeforeAccounts']:raise ValueError('Frozen V3 input proof required')
    paths=('scripts/audit-lifecycle-price-repair.py','tests/test_sector_lifecycle_system_oracle.py','scripts/audit-prepared-base-system.py','tests/test_prepared_base_system_oracle.py','scripts/audit-market-sector-stock-accounts.py',
        'scripts/verify-market-sector-stock-risk.py','scripts/verify-market-sector-stock-ledger.py','scripts/verify-sector-lifecycle-risk.py',
        'tests/test_sector_lifecycle_numeric_reference.py','service/research_sector_lifecycle_precision.py','docs/sector-lifecycle-price-boundary-repair-v1.json')
    pins={p:sha(ROOT/p) for p in paths}
    write(folder/'start.json',{'startedAt':U.now(),'plan':ref(planpath),'inputs':ref(inputpath),'auditorSourcePins':pins})
    for path,digest in pins.items():
        target=folder/'frozen-code'/path;target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as f:f.write((ROOT/path).read_bytes())
    try:
        tests=subprocess.run([sys.executable,'-X','utf8','-B','-m','unittest','tests.test_prepared_base_system_oracle','tests.test_sector_lifecycle_system_oracle','tests.test_sector_lifecycle_numeric_reference'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
        write(folder/'tests.json',{'passed':tests.returncode==0,'exitCode':tests.returncode,'stdout':tests.stdout,'stderr':tests.stderr})
        if tests.returncode:raise ValueError('Independent opening-oracle sensitivity checks failed')
        R=module('_lifecycle_exact_inherited_risk','scripts/verify-sector-lifecycle-risk.py')
        N=module('_prepared_base_outcomes','scripts/verify-liquid-trend-breakout.py')
        from service.portfolio_engine import _digest
        from service.portfolio_config import normalize_config
        data=read(bound(inputs['dataset']));data_digest=_digest(data)
        lifecycle=read(bound(inputs['lifecycleContext']));original=read(bound(plan['originalIndustryContext']))
        if not read(bound(inputs['lifecycleValidation']))['passed']:raise ValueError('Independent lifecycle proof required')
        composed=read(bound(inputs['formationValidation']))
        if not composed['passed'] or composed['context']!=inputs['lifecycleContext'] or composed['formation']!=inputs['formation'] or composed['lifecycle']!=inputs['lifecycleValidation']:
            raise ValueError('Lifecycle/entry proof does not bind executed sources')
        if not read(bound(composed['entries']))['passed']:raise ValueError('Separate entry preparation proof required')
        formation=read(bound(inputs['formation']));parent_system=read(bound(plan['parentSystemInputs']))
        control_schedule=read(bound(plan['parentFrozenFullSchedule']))
        expected_metadata={**formation['metadata'],**{s['signalId']:parent_system['metadata'][s['signalId']] for s in control_schedule}}
        originals=read(bound(plan['originalSixInputs']))
        for group in inputs['groups']:
            anchor=next(g for g in originals['groups'] if (g['period'],g['policy'])==(group['period'],group['policy']))
            if group['enginePolicy']!=anchor['enginePolicy']:raise ValueError('Repair changed engine admission policy')
            for key in ('schedule','systemInputs'):
                if group[key]['sha256']!=anchor[key]['sha256'] or bound(group[key]).read_bytes()!=bound(anchor[key]).read_bytes():raise ValueError('Matched repair changed frozen signal or context bytes')
        system_cache={} 

        benchmark={r['date']:r['close'] for r in data['benchmark']['bars']}
        rows,proofs,diagnostics=[],[],[];common=None;numeric_boundaries=[]
        for item in plan['plannedAccounts']:
            period,policy=item['period'],item['policy'];account=study/period/policy
            group=next(g for g in inputs['groups'] if (g['period'],g['policy'])==(period,policy))
            if group['systemInputs']['sha256'] not in system_cache:
                value=read(bound(group['systemInputs']))
                system_cache[group['systemInputs']['sha256']]=(value,_digest(value))
            system,system_digest=system_cache[group['systemInputs']['sha256']]
            projection=audit_projection(system,lifecycle,original,policy=='v2AdmissionWithLifecycleExit')
            L.equal(system['metadata'],expected_metadata,'All exact independently checked entry metadata')
            L.equal(system['sectorMaps'],parent_system['sectorMaps'],'Inherited publication-time industry maps')
            if policy=='v2AdmissionWithLifecycleExit' and group['schedule']['sha256']!=plan['parentFrozenFullSchedule']['sha256']:raise ValueError('Control mutated frozen V2 entry schedule')
            completion=read(account/'completion.json')
            if completion['systemInputs']!=group['systemInputs'] or completion['enginePolicy']!=group['enginePolicy']:raise ValueError('Wrong account context or engine policy')
            if completion['schedule']!=group['schedule'] or completion['period']!=period or completion['policy']!=policy:raise ValueError('Account does not bind its registered frozen group')
            if completion['plan']!=ref(planpath) or completion['inputs']!=ref(inputpath):raise ValueError('Account receipt source differs')
            result,checkpoint,summary=(read(bound(completion[k])) for k in ('result','checkpoint','summary'))
            config=deepcopy(plan['baseConfig']);dates=plan['rules']['periods'][period]
            config.update(startDate=dates['startDate'],endDate=dates['endDate'],initialCapital=dates['initialCapital'])
            identity=result['research']['identity'];sysidentity=result['research']['system']['identity']
            if result['config']!=normalize_config(config) or identity['signalSchedule']!=read(bound(completion['schedule'])):raise ValueError('Configuration/schedule mismatch')
            if identity['datasetDigest']!=data_digest or identity['datasetFingerprint']!=data['fingerprint'] or sysidentity['inputDigest']!=system_digest or sysidentity['policy']!=group['enginePolicy']:raise ValueError('Data or system identity differs')
            for p,h in {**sysidentity['sourcePins'],**result['research']['system']['preparedBaseIdentity']['sourcePins'],**result['research']['system']['lifecycleIdentity']['sourcePins'],**result['research']['system']['precisionIdentity']['sourcePins']}.items():
                if plan['sourcePins'][p]!=h:raise ValueError('Prepared execution identity mismatch')
            # Fee identity pins the complete account config, including its
            # explicitly different stress dates. Check that binding first;
            # compare fee settings/sources across periods without equating
            # the complete primary and stress config hashes.
            if identity['feePolicy']['configSha256']!=_digest(result['config']):raise ValueError('Fee identity does not bind actual account config')
            if result['research']['system']['lifecycleIdentity']['policy']!=policy:raise ValueError('Wrong lifecycle policy identity')
            current=deepcopy({k:identity[k] for k in ('datasetDigest','datasetFingerprint','feePolicy','sourcePins')})
            current['feePolicy'].pop('configSha256')
            if common is None:common=current
            elif current!=common:raise ValueError('Common source/fee/engine identity differs')
            precision=result['research']['system']['precisionIdentity']
            if precision['comparison']!=plan['numericalComparisonPolicy'] or precision['version']!='sector-lifecycle-structural-price-precision-v1':raise ValueError('Wrong numerical correction identity')
            emit({'auditStart':[period,policy]})
            risk=R.daily_system_risk_reference(result,checkpoint,data,system,price_boundary_policy=plan['numericalComparisonPolicy']);states=risk.pop('dailyCloseRiskState')
            numeric_boundaries.extend({'period':period,'policy':policy,**v} for v in risk['structuralPriceBoundaryAmbiguities'])
            money=L.audit_ledger_and_entries(result,data,system,states)
            opening=audit_openings(result,checkpoint,data,system);numbers=N.account_numbers(result)
            c=summary['closedEpisodes'];expected={'closed':c['count'],'closedNetPnl':c['netPnlAmount'],
                **{k:c[k] for k in ('payoffRatio','profitFactor','meanWinReturnPct','meanLossReturnPct')},
                **{k:summary['account'][k] for k in ('netReturnPct','maxDrawdownPct')}}
            if not all(U.close(numbers[k],v) for k,v in expected.items()) or summary['quality']['integrityIssues']:raise ValueError('Trade/curve arithmetic or fee bridge differs')
            diagnostic=A.grouped_episode_diagnostics(result,system['metadata']);diagnostics.append({'period':period,'policy':policy,**diagnostic})
            path=folder/(period+'.'+policy+'.json')
            write(path,{'passed':True,'completion':ref(account/'completion.json'),'heldDayRisk':risk,'cashFeesEpisodesAndEntryCaps':money,
                'preparedOpenings':opening,'executedContextProjection':projection,'independentOutcomeNumbers':numbers})
            proofs.append({'period':period,'policy':policy,'proof':ref(path)})
            br=(benchmark[dates['endDate']]/benchmark[dates['startDate']]-1)*100
            passed=numbers['closed']>=30 and numbers['payoffRatio'] is not None and numbers['payoffRatio']>=3 and numbers['profitFactor'] is not None and numbers['profitFactor']>=1.5 and numbers['meanLossReturnPct'] is not None and numbers['meanLossReturnPct']>=-5 and numbers['maxDrawdownPct']<=20
            rows.append({'period':period,'policy':policy,**numbers,'signals':len(result['research']['opportunities']),'entries':len(result['research']['system']['entryBindings']),
                'openCount':summary['openPositions']['count'],'openUnrealizedPnl':summary['reconciliation']['openPositionUnrealizedPnl'],
                'winRatePct':c['winRatePct'],'returnQuantilesPct':c['returnQuantilesPct'],'maxConsecutiveLosses':c['maxConsecutiveLosses'],
                'averageExposurePct':summary['periods'][0]['exposurePct']['mean'],'top5ProfitSharePct':summary['concentration']['top5ProfitSharePct'],
                'valuationUnreliableDays':len(summary['quality']['valuationUnreliableDates']),'benchmarkSymbol':'sh000300','benchmarkReturnPct':br,
                'excessPercentagePoints':numbers['netReturnPct']-br,'proposedNumericScreenPassed':bool(passed) if period=='primary' else None,'screenHumanConfirmed':False,
                'weakProfitTargetRequired':False,'periods':[{'id':p['id'],'returnPct':p['returnPct'],'DD':p['maxDrawdownPct'],'closed':p['closedEpisodes']['count'],
                    'R':p['closedEpisodes']['payoffRatio'],'PF':p['closedEpisodes']['profitFactor']} for p in summary['periods']],
                'evidence':completion,'independentProof':ref(path)})
            emit({'auditPassed':[period,policy],'closed':numbers['closed'],'preparedRiskChecks':opening['preparedRiskChecks']})
            del result,checkpoint,summary,risk,states;gc.collect()
        parent=read(bound(plan['parentAccountValidation']));old=read(bound(parent['readout']))
        if not parent['passed'] or len(old['rows'])!=6 or len(rows)!=6 or U.workspace_snapshot()!=plan['workspaceJsonBefore']:raise ValueError('Account population or user workspace differs')
        for p,h in {**plan['sourcePins'],**pins}.items():
            if sha(ROOT/p)!=h:raise ValueError('Source changed during audit')
        if numeric_boundaries:raise ValueError('Corrected structural numerical boundaries remain unresolved')
        write(folder/'validation.json',{'passed':True,'completedAt':U.now(),'plan':ref(planpath),'inputs':ref(inputpath),'auditorSourcePins':pins,'accounts':proofs,
            'newAccounts':0,'independentFrozenArithmeticReplayPassed':True,'numericalContractConformancePassed':not numeric_boundaries,
            'numericBoundaryExceptionCount':len(numeric_boundaries),'userJsonFilesUnchanged':True,'targetAchieved':False})
        write(study/'numeric-boundary-diagnostic.json',{'rows':numeric_boundaries,'count':len(numeric_boundaries),
            'riskOracle':ref(ROOT/'scripts/verify-sector-lifecycle-risk.py'),'scope':'Actual inherited binary-float structural comparisons replayed exactly; these sub-cent equality ambiguities prevent strict mathematical fixed-floor contract acceptance. No corrected-account return is inferred.',
            'accountResultsChanged':False,'newAccounts':0,'targetAchieved':False})
        write(study/'episode-diagnostics.json',{'rows':diagnostics,'newAccounts':0,'newStrategyTrials':0,'scope':'Descriptive actual closed episodes only'})
        write(study/'readout.json',{'schema':'lifecycle-structural-price-repair-readout','completedAt':U.now(),'plan':ref(planpath),'inputValidation':ref(inputpath),'audit':ref(folder/'validation.json'),
            'rows':rows,'reusedParentRows':old['rows'],'reusedParentValidation':plan['parentAccountValidation'],'newAccounts':6,'newStrategyTrials':0,'repairOfCandidateTrial':58,'reusedAccounts':6,
            'continuousEquity':True,'annualReset':False,'actualCutoff':'2026-10-15','missingLatestSessions':['2026-10-16'],'certified':False,'holdoutClaim':False,
            'userJsonFilesUnchanged':True,'completeSystemBehaviorVerifiedUnderFrozenArithmetic':True,'numericalContractConformancePassed':not numeric_boundaries,
            'numericBoundaryExceptionCount':len(numeric_boundaries),'numericBoundaryDiagnostic':ref(study/'numeric-boundary-diagnostic.json'),'targetAchieved':False,'profitGoalAchieved':False})
        write(study/'validation.json',{'passed':True,'plan':ref(planpath),'inputs':ref(inputpath),'readout':ref(study/'readout.json'),
            'fullAccountAudit':ref(folder/'validation.json'),'diagnostics':ref(study/'episode-diagnostics.json'),'newAccounts':6,'reusedAccounts':6,
            'numericBoundaryDiagnostic':ref(study/'numeric-boundary-diagnostic.json'),'numericBoundaryExceptionCount':len(numeric_boundaries),
            'numericalContractConformancePassed':not numeric_boundaries,'targetAchieved':False,'userJsonFilesUnchanged':True})
        emit({'frozenArithmeticReplayCompleted':True,'numericalContractConformancePassed':not numeric_boundaries,'numericBoundaryExceptionCount':len(numeric_boundaries),
            'newAccounts':6,'reusedAccounts':6,'readout':ref(study/'readout.json'),'targetAchieved':False})
    except Exception as e:
        write(folder/'failure.json',{'type':type(e).__name__,'message':str(e),'traceback':traceback.format_exc(),'auditorSourcePins':pins,'executionOutputsUnmodified':True});raise


if __name__=='__main__':
    p=ArgumentParser();p.add_argument('--attempt',default='001');p.add_argument('--audit',default='001');a=p.parse_args()
    if not re.fullmatch(r'[0-9]{3}',a.attempt) or not re.fullmatch(r'[0-9]{3}',a.audit):raise ValueError('Explicit attempt/audit identities required')
    audit(ROOT/'artifacts/strategy-search/sector-lifecycle-system-v3/price-boundary-repair-v1'/('attempt-'+a.attempt),a.audit)
