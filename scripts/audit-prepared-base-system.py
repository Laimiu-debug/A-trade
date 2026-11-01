"""Independent full-account and two-sided opening-risk checks for system V2."""
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


def audit_openings(result,checkpoint,dataset,system):
    securities={s['symbol']:s for s in dataset['securities']}
    positions={s:{b['date']:i for i,b in enumerate(v['bars'])} for s,v in securities.items() if s in {o['symbol'] for o in result['research']['opportunities']}}
    opportunities={r['signalId']:r for r in result['research']['opportunities']}
    attempts=[(d['date'],s) for d in result['daily'] for s in d['buyAttemptOrder']]
    if len({s for _,s in attempts})!=len(attempts):raise ValueError('Repeated immediate-next-session attempt')
    expected=[];rejected=[]
    for day,identifier in attempts:
        opportunity=opportunities[identifier];m=system['metadata'][identifier]
        if opportunity['attemptDate']!=day or opportunity['nextSession']!=day:raise ValueError('Prepared opening clock')
        if opportunity['reason'] in ('immediateNextSessionMissed','alreadyHeldNoAdd','pendingExitOrSoldToday'):continue
        security=securities[opportunity['symbol']];at=positions[opportunity['symbol']].get(day)
        if at is None:continue
        bar=security['bars'][at];opening=bar['open']
        if not (opening>0 and m['failurePrice']<opening<=m['signalClose']*(1+m['meanNtr20'])):continue
        price=opening*(1+result['config']['slippageBps']/10000)
        symbol=opportunity['symbol']
        band=.2 if symbol.startswith(('sh68','sz30')) else .1
        if band==.1 and day<'2026-07-06' and security.get('stByDate',{}).get(day) is not False:band=.05
        previous=security.get('precloseByDate',{}).get(day,security['bars'][at-1]['close'] if at else None)
        limit=float(Decimal(str(previous*(1+band))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        if price>limit+1e-8:continue
        reason='preparedBaseOpeningRiskTooWide' if m['failurePrice']<price*.95 else 'preparedBaseOpeningRiskInsideOneNtr' if m['failurePrice']>price*(1-m['meanNtr20']) else None
        expected.append({'date':day,'symbol':symbol,'signalId':identifier,'modeledPrice':price,'failurePrice':m['failurePrice'],
            'signalNtr20':m['meanNtr20'],'status':'rejected' if reason else 'passed','reason':reason})
        if reason:
            rejected.append(identifier)
            L.check(opportunity['reason']==reason and opportunity['filledQuantity']==0 and opportunity['status']=='expired','Risk-incoherent opening did not expire')
    observed=result['research']['system']['preparedBaseOpeningGuards']
    L.equal(observed,expected,'Every attempted prepared opening including rejection')
    L.equal(checkpoint['system']['preparedBaseOpeningGuards'],expected,'Checkpoint prepared opening decisions')
    for fill in result['trades']:
        if fill['side']=='buy':
            m=system['metadata'][fill['signalId']]
            L.check(.95*fill['price']<=m['failurePrice']<=fill['price']*(1-m['meanNtr20']),'Filled new risk incoherent with actual modeled buy price')
            L.check(fill['signalId'] not in rejected,'Rejected opening later filled')
    return {'openingAttempts':len(attempts),'preparedRiskChecks':len(expected),'rejectedRiskChecks':len(rejected),'noRetry':True,'independentlyRebuiltFromRawOpen':True}


def audit(study,audit_id):
    folder=study/('audit-'+audit_id)
    if folder.exists():raise ValueError('Preserve prior audit attempt')
    folder.mkdir();planpath,inputpath=study/'plan.json',study/'input-validation.json'
    plan,inputs=read(planpath),read(inputpath)
    for p,h in plan['sourcePins'].items():bound({'path':p,'sha256':h})
    for v in plan['bindings']:bound(v)
    if not inputs['passed'] or inputs['planSha256']!=sha(planpath) or not inputs['allFiveSchedulesFrozenBeforeAccounts']:raise ValueError('Frozen V2 input proof required')
    paths=('scripts/audit-prepared-base-system.py','tests/test_prepared_base_system_oracle.py','scripts/audit-market-sector-stock-accounts.py',
        'scripts/verify-market-sector-stock-risk.py','scripts/verify-market-sector-stock-ledger.py')
    pins={p:sha(ROOT/p) for p in paths}
    write(folder/'start.json',{'startedAt':U.now(),'plan':ref(planpath),'inputs':ref(inputpath),'auditorSourcePins':pins})
    try:
        tests=subprocess.run([sys.executable,'-X','utf8','-B','-m','unittest','tests.test_prepared_base_system_oracle'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
        write(folder/'tests.json',{'passed':tests.returncode==0,'exitCode':tests.returncode,'stdout':tests.stdout,'stderr':tests.stderr})
        if tests.returncode:raise ValueError('Independent opening-oracle sensitivity checks failed')
        R=module('_prepared_base_held_risk','scripts/verify-market-sector-stock-risk.py')
        N=module('_prepared_base_outcomes','scripts/verify-liquid-trend-breakout.py')
        from service.portfolio_engine import _digest
        from service.portfolio_config import normalize_config
        data,system=read(bound(inputs['dataset'])),read(bound(inputs['systemInputs']))
        data_digest,system_digest=_digest(data),_digest(system)
        benchmark={r['date']:r['close'] for r in data['benchmark']['bars']}
        rows,proofs,diagnostics=[],[],[];common=None
        for item in plan['plannedAccounts']:
            period,policy=item['period'],item['policy'];account=study/period/policy
            completion=read(account/'completion.json')
            if completion['plan']!=ref(planpath) or completion['inputs']!=ref(inputpath):raise ValueError('Account receipt source differs')
            result,checkpoint,summary=(read(bound(completion[k])) for k in ('result','checkpoint','summary'))
            config=deepcopy(plan['baseConfig']);dates=plan['rules']['periods'][period]
            config.update(startDate=dates['startDate'],endDate=dates['endDate'],initialCapital=dates['initialCapital'])
            identity=result['research']['identity'];sysidentity=result['research']['system']['identity']
            if result['config']!=normalize_config(config) or identity['signalSchedule']!=read(bound(completion['schedule'])):raise ValueError('Configuration/schedule mismatch')
            if identity['datasetDigest']!=data_digest or identity['datasetFingerprint']!=data['fingerprint'] or sysidentity['inputDigest']!=system_digest or sysidentity['policy']!=policy:raise ValueError('Data or system identity differs')
            for p,h in {**sysidentity['sourcePins'],**result['research']['system']['preparedBaseIdentity']['sourcePins']}.items():
                if plan['sourcePins'][p]!=h:raise ValueError('Prepared execution identity mismatch')
            # Fee identity pins the complete account config, including its
            # explicitly different stress dates. Check that binding first;
            # compare fee settings/sources across periods without equating
            # the complete primary and stress config hashes.
            if identity['feePolicy']['configSha256']!=_digest(result['config']):raise ValueError('Fee identity does not bind actual account config')
            current=deepcopy({k:identity[k] for k in ('datasetDigest','datasetFingerprint','feePolicy','sourcePins')})
            current['feePolicy'].pop('configSha256')
            if common is None:common=current
            elif current!=common:raise ValueError('Common source/fee/engine identity differs')
            emit({'auditStart':[period,policy]})
            risk=R.daily_system_risk_reference(result,checkpoint,data,system);states=risk.pop('dailyCloseRiskState')
            money=L.audit_ledger_and_entries(result,data,system,states)
            opening=audit_openings(result,checkpoint,data,system);numbers=N.account_numbers(result)
            c=summary['closedEpisodes'];expected={'closed':c['count'],'closedNetPnl':c['netPnlAmount'],
                **{k:c[k] for k in ('payoffRatio','profitFactor','meanWinReturnPct','meanLossReturnPct')},
                **{k:summary['account'][k] for k in ('netReturnPct','maxDrawdownPct')}}
            if not all(U.close(numbers[k],v) for k,v in expected.items()) or summary['quality']['integrityIssues']:raise ValueError('Trade/curve arithmetic or fee bridge differs')
            diagnostic=A.grouped_episode_diagnostics(result,system['metadata']);diagnostics.append({'period':period,'policy':policy,**diagnostic})
            path=folder/(period+'.'+policy+'.json')
            write(path,{'passed':True,'completion':ref(account/'completion.json'),'heldDayRisk':risk,'cashFeesEpisodesAndEntryCaps':money,
                'preparedOpenings':opening,'independentOutcomeNumbers':numbers})
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
        if not parent['passed'] or len(old['rows'])!=6 or len(rows)!=5 or U.workspace_snapshot()!=plan['workspaceJsonBefore']:raise ValueError('Account population or user workspace differs')
        for p,h in {**plan['sourcePins'],**pins}.items():
            if sha(ROOT/p)!=h:raise ValueError('Source changed during audit')
        write(folder/'validation.json',{'passed':True,'completedAt':U.now(),'plan':ref(planpath),'inputs':ref(inputpath),'auditorSourcePins':pins,'accounts':proofs,
            'newAccounts':0,'independentHeldDayCashAndChangedOpeningRiskPassed':True,'userJsonFilesUnchanged':True,'targetAchieved':False})
        write(study/'episode-diagnostics.json',{'rows':diagnostics,'newAccounts':0,'newStrategyTrials':0,'scope':'Descriptive actual closed episodes only'})
        write(study/'readout.json',{'schema':'prepared-base-system-v2-readout','completedAt':U.now(),'plan':ref(planpath),'inputValidation':ref(inputpath),'audit':ref(folder/'validation.json'),
            'rows':rows,'reusedParentRows':old['rows'],'reusedParentValidation':plan['parentAccountValidation'],'newAccounts':5,'newStrategyTrials':1,'reusedAccounts':6,
            'continuousEquity':True,'annualReset':False,'actualCutoff':'2026-10-15','missingLatestSessions':['2026-10-16'],'certified':False,'holdoutClaim':False,
            'userJsonFilesUnchanged':True,'completeSystemBehaviorVerifiedUnderModel':True,'targetAchieved':False,'profitGoalAchieved':False})
        write(study/'validation.json',{'passed':True,'plan':ref(planpath),'inputs':ref(inputpath),'readout':ref(study/'readout.json'),
            'fullAccountAudit':ref(folder/'validation.json'),'diagnostics':ref(study/'episode-diagnostics.json'),'newAccounts':5,'reusedAccounts':6,'targetAchieved':False,'userJsonFilesUnchanged':True})
        emit({'fullAuditPassed':True,'newAccounts':5,'reusedAccounts':6,'readout':ref(study/'readout.json'),'targetAchieved':False})
    except Exception as e:
        write(folder/'failure.json',{'type':type(e).__name__,'message':str(e),'traceback':traceback.format_exc(),'auditorSourcePins':pins,'executionOutputsUnmodified':True});raise


if __name__=='__main__':
    p=ArgumentParser();p.add_argument('--attempt',default='001');p.add_argument('--audit',default='001');a=p.parse_args()
    if not re.fullmatch(r'[0-9]{3}',a.attempt) or not re.fullmatch(r'[0-9]{3}',a.audit):raise ValueError('Explicit attempt/audit identities required')
    audit(ROOT/'artifacts/strategy-search/market-sector-stock-base-v2'/('attempt-'+a.attempt),a.audit)
