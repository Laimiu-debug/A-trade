"""Independent label/clock/ledger audit; no new path runner or model oracle.

The separately named direct-reference sample uses the unchanged original account
classes only as a bounded implementation-equivalence check. All paths are also
checked using independent order arithmetic, raw-fill accounting and old risk
references. No account-return claim is made by aggregating these paths.
"""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import importlib.util
from itertools import zip_longest
import json
import math
from pathlib import Path
import sys
import traceback

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'artifacts/strategy-search/independent-shadow-labels-v1'
FEE_BINDING={'path':'docs/account-exit-comparison-v1.json',
             'sha256':'1406190246a20ccb823b129f471bcd5c16ede59b78c56fe62947ff4c4f4cb21e'}
DEPENDENCIES={
    'scripts/verify-online-feedback-entry.py':'ec17b1a7114bd2ed6a9ba5757f26fccdbfeb3baa1cedcadf5993ec18a0a963d5',
    'scripts/verify-reclaim-exit-mechanism.py':'96ef23022f1a8a515ecf94eabdab8696057bcb166a2cd309fe43f101a507cd89',
    'scripts/verify-reclaim-exit-transfer.py':'5437d5e9421f65f935d211c553e95c473cf40fddafaf09b12694b3bf3548913a',
    'scripts/verify-reclaim-volatility-trail.py':'81bd4a8681f31d9a1dc5c5732ed8cb7d763e3c779b58492098bbc3957fd0bb08',
    'scripts/verify-relative-repair-cost-stress.py':'df00a1573933b4624563b4db0f451a731449c55c180b8b6793f5967b147e762a',
}


def require(condition,message):
    if not condition:raise ValueError(message)


def finite(x):return type(x) in (int,float) and math.isfinite(x)
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def same(actual,expected,name='value'):
    if finite(actual) and finite(expected):
        require(math.isclose(actual,expected,rel_tol=1e-11,abs_tol=1e-8),f'{name}: {actual!r} != {expected!r}')
    elif isinstance(expected,dict):
        require(isinstance(actual,dict) and set(actual)==set(expected),name+': dictionary fields differ')
        for key in expected:same(actual[key],expected[key],name+'.'+str(key))
    elif isinstance(expected,list):
        require(isinstance(actual,list) and len(actual)==len(expected),name+': list count differs')
        for i,(a,b) in enumerate(zip(actual,expected)):same(a,b,name+'['+str(i)+']')
    else:require(type(actual)==type(expected) and actual==expected,name+': different identity/value')


_MODULES={}
def independent(name):
    if name not in _MODULES:
        require(sha(ROOT/name)==DEPENDENCIES[name],'Independent helper changed: '+name)
        spec=importlib.util.spec_from_file_location('_shadow_'+Path(name).stem.replace('-','_'),ROOT/name)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);_MODULES[name]=module
    return _MODULES[name]


def read(path):
    path=Path(path)
    with (gzip.open(path,'rt',encoding='utf-8') if path.suffix=='.gz' else path.open(encoding='utf-8')) as f:return json.load(f)


def records(path):
    with gzip.open(path,'rt',encoding='utf-8') as f:
        for line in f:
            if line.strip():yield json.loads(line)


def bound(ref):
    path=(ROOT/ref['path']).resolve()
    require(path.is_relative_to(ROOT) and sha(path)==ref['sha256'],'Changed/outside input '+ref['path'])
    return path


def projection_reference(dataset,symbol):
    """Preserve metadata/calendar/status/rights and ALL historical bars verbatim."""
    selected=[s for s in dataset['securities'] if s['symbol']==symbol]
    require(len(selected)==1,'Unique registered source security')
    return {**dataset,'securities':selected}


def quantity(value,symbol):
    minimum,increment=(200,1) if symbol.startswith('sh68') else (100,100)
    n=max(0,math.floor(value/increment+1e-10)*increment)
    return n if n>=minimum else 0


def fees(amount,side,day,period):
    return independent('scripts/verify-relative-repair-cost-stress.py').fee(amount,side,day,period)['breakdown']['total']


def affordable(ceiling,symbol,price,budget,day,period):
    minimum,increment=(200,1) if symbol.startswith('sh68') else (100,100)
    high=math.floor(ceiling/increment);lo=math.ceil(minimum/increment);best=0
    while lo<=high:
        mid=(lo+high)//2;q=mid*increment
        if q*price+fees(q*price,'buy',day,period)<=budget+1e-8:best=q;lo=mid+1
        else:high=mid-1
    return best


def execution_reference(signal,dataset,config,period,trades,queued,processed_dates):
    """All opening should-fill/should-not-fill decisions, including empty prefixes.

    Queue evidence is separately recomputed by the pinned independent risk audit;
    it is not accepted here as a production risk oracle.
    """
    symbol=signal['symbol'];calendar=dataset['calendar'];ci={d:i for i,d in enumerate(calendar)}
    security=next(s for s in dataset['securities'] if s['symbol']==symbol)
    rows=security['bars'];bi={b['date']:i for i,b in enumerate(rows)}
    nominal=.2 if symbol.startswith(('sz30','sh68')) else .1
    observed=defaultdict(list)
    for f in trades:observed[f['date']].append(f)
    orders=defaultdict(list)
    for q in queued:orders[q['date']].append(q)
    target=[d for d in calendar if config['startDate']<=d<=config['endDate']]
    require(processed_dates==target[:len(processed_dates)] and processed_dates,'Original complete period prefix')
    require(signal['signalDate'] in processed_dates,'Formation never processed')
    nextday=calendar[ci[signal['signalDate']]+1] if ci[signal['signalDate']]+1<len(calendar) else None
    plan=dict(status='notObserved',reason=None,nextSession=nextday,budget=None,plannedQuantity=None,reservePrice=None,
              attemptDate=None,filledQuantity=0,unfilledQuantity=None)
    cash=config['initialCapital'];held=0;entry=None;pending=None;quarantined=None
    expected_fills=[];holdings=[]
    def bar(day):return rows[bi[day]] if day in bi else None
    def member(day):
        if day in dataset.get('membershipByDate',{}):return symbol in dataset['membershipByDate'][day]
        return security.get('memberHistory',security.get('memberByDate',{})).get(day)
    def reference(day):
        i=bi[day]
        return security.get('precloseByDate',{}).get(day,rows[i-1]['close'] if i else None)
    def gate(day,side):
        nonlocal quarantined
        b=bar(day);i=bi.get(day)
        if quarantined:return 'rawPriceDiscontinuity'
        if security.get('delistDate') and day>=security['delistDate']:return 'delistedUnsettledPosition'
        if b is None:return 'missingBar'
        status=security.get('tradeStatusByDate',{}).get(day)
        if status in (False,0,'0') or b['volume']<=0:return 'suspended'
        if i<5:return 'insufficientListingObservations'
        prev=rows[i-1]
        if ci[day]-ci[prev['date']]!=1 or prev['volume']<=0:return 'missingPreviousTradingBar'
        if side=='buy':
            if member(day) is False:return 'notHistoricalMember'
            if security.get('listingDate') and day<security['listingDate']:return 'notListed'
            if config['universe']['excludeSt'] and security.get('stByDate',{}).get(day) is True:return 'historicalST'
            if config['universe']['strictHistory'] and (status is None or day not in security.get('stByDate',{}) or member(day) is None or not security.get('listingDate')):return 'executionHistoryUnknown'
        ref=reference(day)
        if abs(b['open']/ref-1)>nominal+.005:quarantined=day;return 'rawPriceDiscontinuity'
        band=nominal if nominal!=.1 or security.get('stByDate',{}).get(day) is False else .05
        upper=float(Decimal(str(ref*(1+band))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        lower=float(Decimal(str(ref*(1-band))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        if side=='buy' and b['open']>=upper-1e-8:return 'openAtUpperLimitOrUncertainST'
        if side=='sell' and b['open']<=lower+1e-8:return 'openAtLowerLimitOrUncertainST'
        price=b['open']*(1+config['slippageBps']/10000*(1 if side=='buy' else -1))
        if (side=='buy' and price>upper+1e-8) or (side=='sell' and price<lower-1e-8):return 'slippageBeyondLimitOrUncertainST'
        return None
    for day in processed_dates:
        fill=None
        side='sell' if held and pending else 'buy' if plan['status']=='planned' and day==nextday else None
        if side:
            if side=='buy':plan['attemptDate']=day
            reason=gate(day,side)
            if reason is None:
                b=bar(day);i=bi[day];price=b['open']*(1+config['slippageBps']/10000*(1 if side=='buy' else -1))
                cap=math.floor(rows[i-1]['volume']*config['maxParticipationRate'])
                if day in security.get('openingVolumeByDate',{}):cap=min(cap,math.floor(security['openingVolumeByDate'][day]*config['maxParticipationRate']))
                if side=='buy':
                    qty=affordable(quantity(min(plan['plannedQuantity'],cap),symbol),symbol,price,min(cash,plan['budget']),day,period)
                    if not qty:reason='cashOrPreviousVolumeBelowMinimum'
                else:
                    desired=min(held,cap);qty=desired if desired==held else quantity(desired,symbol)
                    if day<=entry:reason='tPlusOne'
                    elif not qty:reason='previousVolumeOrLotMinimum'
                    elif cash+qty*price<fees(qty*price,'sell',day,period):reason='insufficientSellFeeCash'
                if reason is None:
                    fee=fees(qty*price,side,day,period)
                    cash+=(qty*price-fee) if side=='sell' else -(qty*price+fee)
                    held+=qty if side=='buy' else -qty
                    fill=dict(date=day,symbol=symbol,side=side,quantity=qty,openingPrice=b['open'],price=price,fee=fee,cashAfter=cash)
                    expected_fills.append(fill)
                    if side=='buy':entry=day;plan.update(status='filled' if qty==plan['plannedQuantity'] else 'partial',reason='filled' if qty==plan['plannedQuantity'] else 'partialRemainderExpired',filledQuantity=qty,unfilledQuantity=plan['plannedQuantity']-qty)
                    elif not held:pending=None
            if side=='buy' and reason is not None:plan.update(status='expired',reason=reason)
        actual=observed[day]
        require(len(actual)==int(fill is not None),'Missing/unexpected actual opening fill '+day)
        if fill:
            for key,value in fill.items():same(actual[0][key],value,'Independent fill '+key)
        b=bar(day);i=bi.get(day)
        if b is not None and i and b['volume']>0 and abs(b['close']/reference(day)-1)>nominal+.005:quarantined=quarantined or day
        holdings.append(dict(date=day,holdings=[dict(symbol=symbol,quantity=held)] if held else []))
        for q in orders[day]:
            require(held and pending is None,'Duplicate risk queue or queue without held position')
            pending=q
        if day==signal['signalDate']:
            reason=None;status=security.get('tradeStatusByDate',{}).get(day)
            if quarantined:reason='soldTodayOrQuarantined'
            elif member(day) is False:reason='notHistoricalMember'
            elif security.get('delistDate') and day>=security['delistDate']:reason='delisted'
            elif security.get('listingDate') and day<security['listingDate']:reason='notListed'
            elif config['universe']['excludeSt'] and security.get('stByDate',{}).get(day) is True:reason='historicalST'
            elif b is None or b['volume']<=0 or status in (False,0,'0'):reason='missingOrSuspendedSignalClose'
            elif config['universe']['strictHistory'] and (status is None or day not in security.get('stByDate',{}) or member(day) is None or not security.get('listingDate')):reason='signalHistoryUnknown'
            if reason:plan.update(status='notPlanned',reason=reason)
            else:
                price=b['close']*(1+config['slippageBps']/10000);budget=config['initialCapital']*.2
                planned=affordable(quantity(budget/price,symbol),symbol,price,budget,day,period)
                plan.update(budget=budget,reservePrice=price,plannedQuantity=planned,unfilledQuantity=planned)
                if nextday is None or nextday>config['endDate']:plan.update(status='censored',reason='nextSessionOutsideStudy')
                elif not planned:plan.update(status='notPlanned',reason='budgetBelowLotMinimum')
                else:plan.update(status='planned',reason='nextSessionOnly')
    require(set(observed)<=set(processed_dates),'Fill outside processed prefix')
    return dict(opportunity=plan,daily=holdings,fillCount=len(expected_fills),terminalCash=cash,terminalQuantity=held,
                quarantined={symbol:quarantined} if quarantined else {})


def deterministic_samples(paths,maximum=6):
    """First/last plus first closed/unfilled/open/partial; never sort by profit."""
    require(maximum==6,'Registered equivalence sample cap changed')
    chosen=[]
    def add(i):
        if i is not None and i not in chosen:chosen.append(i)
    if paths:add(0);add(len(paths)-1)
    predicates=(lambda r:r['positionState']=='closed',lambda r:r['positionState']=='notEntered',
                lambda r:r['positionState']=='openAtCutoff',lambda r:any(f['positionAction']=='reduce' for f in r['trades']) or r['opportunity']['status']=='partial')
    for predicate in predicates:add(next((i for i,r in enumerate(paths) if predicate(r)),None))
    return chosen


def path_sample_view(path):
    return dict(positionState={'closed':'closed','open':'openAtCutoff','unfilled':'notEntered','unresolved':'unresolved'}[path['status']],
                trades=path['evidence']['trades'],opportunity=path['evidence']['opportunity'])


def audit_path(path,signal,view,config,period,exit_policy,projection_digest=None):
    """Every compact path, including nonentries; no production runner import."""
    evidence=path['evidence'];terminal=evidence['terminal'];run=path['run'];outcome=path['outcome']
    same(path['signal'],signal,'Entire original five-field signal')
    require(path['version']=='research-independent-paths-v1' and path['exitPolicy']==exit_policy,'Path version/exit')
    require(len(view['securities'])==1 and view['securities'][0]['symbol']==signal['symbol'],'Exact single security')
    require(not view.get('corporateActions') and not view['securities'][0].get('corporateActions'),'Declared rights outside frozen label scope')
    identity=evidence['identity'];cost=independent('scripts/verify-relative-repair-cost-stress.py')
    cost.verify_recorded_config(identity['config'],config,same)
    same(identity['signalSchedule'],[signal],'Single unmodified original opportunity')
    require(identity['signalScheduleSha256']==digest([signal]) and identity['entryWeight']==.2,'Frozen input/risk budget')
    source_digest=projection_digest or digest(view)
    require(identity['datasetDigest']==source_digest and identity['datasetFingerprint']==str(view.get('fingerprint') or source_digest),'Single security source identity')
    projection=path['sourceProjection']
    require(projection['sourceSecurityCount']==projection['securityCount']==1 and projection['symbol']==signal['symbol'],
            'Runner one-security view provenance; parent universe is bound separately')
    for field,value in dict(datasetDigest=source_digest,sourceFingerprint=str(view.get('fingerprint') or source_digest),
        calendarDigest=digest(view['calendar']),securityDigest=digest(view['securities'][0]),calendarSessions=len(view['calendar']),
        retainedMetadataFields=sorted(k for k in view if k!='securities')).items():same(projection[field],value,'Source projection '+field)
    target=[d for d in view['calendar'] if config['startDate']<=d<=config['endDate']]
    require(type(run['processedSessions']) is int and 1<=run['processedSessions']<=len(target),'Processed date count')
    processed=target[:run['processedSessions']]
    same(run,dict(requestedStartDate=config['startDate'],requestedEndDate=config['endDate'],firstProcessedDate=target[0],
        processedThrough=processed[-1],processedSessions=len(processed),totalPeriodSessions=len(target),
        stoppedEarly=len(processed)<len(target),stopReason='signalTerminalAndLedgerSettled' if len(processed)<len(target) else 'periodEnd',
        initialCapital=500000.,entryWeight=.2,nominalEntryBudget=100000.,notWholePeriodInvestmentAccount=True),'Entire execution clock')
    require(all(not terminal[k] for k in ('actionLedger','cashReceivables','actionPayables','preExRights','unprocessedEntitlements'))
            and terminal['reservedActionCash']==0,'Unexpected rights cash requires a separately registered label definition')
    queued=[d for d in evidence['decisions'] if d['action']=='sell' and d['status']=='queued']
    execution=execution_reference(signal,view,identity['config'],period,evidence['trades'],queued,processed)
    for key,value in execution['opportunity'].items():same(evidence['opportunity'][key],value,'Original one-attempt disposition '+key)
    for key in signal:same(evidence['opportunity'][key],signal[key],'Opportunity input '+key)
    same(terminal['quarantined'],execution['quarantined'],'All-prefix quarantine state')
    same(terminal['cash'],execution['terminalCash'],'Independent final cash')
    # These date-only rows are a calculation facade, never a saved equity path.
    # Only the measured last row has prices/cash. All holding quantities are
    # rebuilt independently from actual fills; no absent future close is added.
    curve=[{'date':d} for d in processed];curve[-1]=terminal['lastCurve']
    same(curve[-1]['date'],processed[-1],'Actual terminal valuation date')
    for key in ('cash','marketValue','equity'):same(curve[-1][key],terminal[key],'Terminal curve '+key)
    result=dict(config=deepcopy(identity['config']),trades=evidence['trades'],closedTrades=evidence['closedTrades'],
        positions=terminal['positions'],curve=curve,daily=execution['daily'],decisions=evidence['decisions'],
        forcedExits=terminal['forcedExits'],research={'opportunities':[evidence['opportunity']], 'feeLedger':evidence['feeLedger']},
        versions={},metrics={'totalFees':terminal['totalFees'],'totalSlippage':terminal['totalSlippage']},
        **{k:terminal[k] for k in ('actionLedger','cashReceivables','actionPayables','preExRights','reservedActionCash')})
    fee_audit=cost.verify_fills(result,period,10)
    episodes,ledger=independent('scripts/verify-reclaim-exit-mechanism.py').reconstruct_episodes(result)
    require(set(episodes)<={signal['signalId']},'Foreign episode in one-signal path')
    episode=episodes.get(signal['signalId'])
    empty_target=terminal['pendingTarget'] is None or all(terminal['pendingTarget'].get(k)=={} for k in
        ('weights','quantities','budgets','priorityBySymbol','signalDates'))
    same(terminal['emptyPendingTargetEnvelope'],terminal['pendingTarget'] is not None and empty_target,'Empty target is not a live order')
    unresolved=[]
    if evidence['opportunity']['status'] in ('notObserved','planned'):unresolved.append('signalNotTerminal')
    for key in ('positions','forcedExits','cashReceivables','actionPayables','preExRights'):
        if terminal[key]:unresolved.append(key)
    if terminal['reservedActionCash']:unresolved.append('reservedActionCash')
    if terminal['unprocessedEntitlements']:unresolved.append('unprocessedEntitlements')
    if terminal['unaccountedActionExposure']:unresolved.append('unaccountedActionExposure')
    if not empty_target:unresolved.append('pendingTargetOrders')
    same(terminal['unresolvedReasons'],unresolved,'Unsettled money/position/order reasons')
    entered=episode is not None;closed=entered and episode['exitDate'] is not None;settled=closed and not unresolved
    status='closed' if settled else 'open' if terminal['positions'] else 'unresolved' if entered or unresolved else 'unfilled'
    same(path['status'],status,'Independent maturity state')
    expected_outcome=dict(entered=entered,positionClosed=bool(closed),fullyClosed=bool(settled),labelEligible=bool(settled),
        entryDate=episode['entryDate'] if entered else None,finalExitDate=episode['exitDate'] if closed else None,
        labelAvailableDate=episode['exitDate'] if settled else None,investedAmount=episode['investedAmount'] if entered else None,
        netPnlAmount=episode['closedPnlAmount'] if settled else None,
        netReturn=episode['closedPnlAmount']/episode['investedAmount'] if settled else None)
    same(outcome,expected_outcome,'All final cash label fields')
    expected_label=dict(formationId=signal['signalId'],formationDate=signal['signalDate'],entryDate=episode['entryDate'],
        exitDate=episode['exitDate'] if settled else None,netReturn=expected_outcome['netReturn']) if entered else None
    same(path['label'],expected_label,'Closed labels only; no open/unfilled zero')
    same(terminal['tradeCashNet'],math.fsum(f['amount']-f['fee'] if f['side']=='sell' else -f['amount']-f['fee'] for f in evidence['trades']),'All fill cash legs')
    same(terminal['actionCashNet'],0.,'No invented rights cash')
    same(terminal['cashChange'],terminal['cash']-500000.,'Final cash change')
    if run['stoppedEarly']:
        require(not unresolved and not terminal['positions'],'Early stop with unresolved position/rights/order')
        expected_stop=episode['exitDate'] if entered else evidence['opportunity']['attemptDate'] or signal['signalDate']
        require(processed[-1]==expected_stop,'Path stopped after/before its first resolved session')
    if status in ('open','unresolved'):require(processed==target,'Unmatured path stopped before original cutoff')
    for position in terminal['positions']:
        sec=view['securities'][0];observed={b['date']:b for b in sec['bars']}
        marks=[b for b in sec['bars'] if position['entryDate']<=b['date']<=processed[-1] and
               (b['volume']>0 or (view.get('dataQuality',{}).get('certified') is True and sec.get('tradeStatusByDate',{}).get(b['date']) in (False,0,'0')))]
        last=marks[-1] if marks else None
        same(position['markPrice'],last['close'] if last else episode['entryPrice'],'Terminal actual available mark')
        same(position['priceDate'],last['date'] if last else episode['entryDate'],'Terminal mark timestamp')
    # Restrict only the calculation window to actual processed dates. The
    # original immutable config/endDate was checked above and is not edited.
    result['config']['endDate']=processed[-1]
    checkpoint={}
    if exit_policy=='volatilityTrail':
        result['versions']['researchVolatilityTrail']='research-volatility-trail-v1'
        result['research']['volatilityTrail']=evidence['volatilityTrail']
        checkpoint={'volatilityTrail':evidence['volatilityTrail'],'currentPositionTrails':evidence['currentPositionTrails']}
        risk=independent('scripts/verify-reclaim-volatility-trail.py').daily_risk_reference(result,checkpoint,view,exit_policy)
    else:
        require(evidence['volatilityTrail'] is None and evidence['currentPositionTrails'] is None,'Unexpected volatility adapter')
        risk=independent('scripts/verify-reclaim-exit-transfer.py').daily_risk_reference(result,checkpoint,view,'trail10')
    require(path['certified'] is False and path['targetAchieved'] is False,'Unsupported label certification')
    return dict(status=status,entered=entered,fills=fee_audit['fills'],heldCloses=risk['heldDays'],queuedExits=risk['queuedExits'],
                processedSessions=len(processed),earlyStopped=run['stoppedEarly'],label=expected_label)


def dated_policy_reference(settings):
    """Retain registered JSON number types: fee identity is a byte digest."""
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from service.research_account_fees import DatedFeePolicy
    require(settings['profileId']=='dated-transfer-assumption-v1'
            and settings['commissionRate']==.0003 and settings['minCommission']==5
            and settings['commissionIncludesTransfer'] is False and settings['stampDutyOverride'] is None
            and settings['securityType']=='ordinary-a-share' and settings['tradingMode']=='exchange-auction',
            'Changed registered dated fee economics')
    return DatedFeePolicy(commission_rate=settings['commissionRate'],min_commission=settings['minCommission'],
        commission_includes_transfer=settings['commissionIncludesTransfer'],stamp_duty_override=settings['stampDutyOverride'],
        security_type=settings['securityType'],trading_mode=settings['tradingMode'])


def direct_reference(path,view,config,period,exit_policy):
    """Bounded original-class FULL-period check; never the new path wrapper."""
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from service.research_fixed_signal_account import FixedSignalAccount
    from service.research_volatility_trail import VolatilityTrailAccount
    from service.research_account_fees import LegacyFeePolicy
    policy=dated_policy_reference(read(bound(FEE_BINDING))['feePolicy']) if period=='2022-2023' else LegacyFeePolicy()
    cls=FixedSignalAccount if exit_policy=='trail10' else VolatilityTrailAccount
    engine=cls(deepcopy(view),[deepcopy(path['signal'])],deepcopy(config),entry_weight=.2,fee_policy=policy)
    engine.step(len(engine.dates));full=engine.result();evidence=path['evidence']
    require(engine.state['cursor']==len(engine.dates),'Direct reference did not cover complete original period')
    for name in ('trades','closedTrades','positions'):
        same(evidence['terminal'][name] if name=='positions' else evidence[name],full[name],'Direct original full-period '+name)
    same(evidence['opportunity'],full['research']['opportunities'][0],'Direct original one-attempt outcome')
    same(evidence['feeLedger'],full['research']['feeLedger'],'Full reference quote and paid-fee linkage')
    same(evidence['terminal']['cash'],full['curve'][-1]['cash'],'Full reference final cash')
    same(evidence['terminal']['marketValue'],full['curve'][-1]['marketValue'],'Full reference marked assets')
    if exit_policy=='volatilityTrail':same(evidence['volatilityTrail'],full['research']['volatilityTrail'],'Full reference held-close risk path')
    else:
        select=lambda ds:[d for d in ds if d['action']=='sell' and d['status']=='queued']
        same(select(evidence['decisions']),select(full['decisions']),'Full reference first exit triggers')
    return dict(formationId=path['signal']['signalId'],passed=True,status=path['status'],
        fullPeriodSessions=len(engine.dates),compactProcessedSessions=path['run']['processedSessions'],
        directTradesSha256=digest(full['trades']),directClosedTradesSha256=digest(full['closedTrades']),
        newWrapperImported=False,meaning='Full-period unchanged original account class; bounded equivalence, not a second strategy trial.')


def registered_plan(directory):
    plan=read(directory/'plan.json')
    require(plan['schema']=='independent-shadow-labels-v1-plan' and plan['expectedFormations']==8155 and plan['expectedPaths']==16310,
            'Registered original complete population')
    require(plan['newCandidateAccounts']==0 and plan['holdoutClaim'] is False and plan['certified'] is False,'Diagnostic scope changed')
    for name,h in plan['sourcePins'].items():bound({'path':name,'sha256':h})
    for name,h in DEPENDENCIES.items():require(sha(ROOT/name)==h,'Independent dependency changed '+name)
    bound(FEE_BINDING)
    parent=read(bound(plan['parentPlan']));delivery=read(bound(plan['parentDelivery']))
    for name in ('sourceData','metadata','environment','baselineSchedules','periods','opportunities'):
        same(plan[name],parent[name],'Exact parent input '+name)
    completion=read(bound(plan['parentInputCompletion']));proof=read(bound(plan['parentInputValidation']))
    require(proof['passed'] is True and proof['planSha256']==plan['parentPlan']['sha256']
            and proof['inputCompletionSha256']==plan['parentInputCompletion']['sha256'],'Parent frozen schedule/input proof')
    require(completion['planSha256']==plan['parentPlan']['sha256'],'Parent completion plan')
    require(len(completion['groups'])==12,'Original baseline groups')
    return plan


def write_proof(directory,name,value):
    with (directory/name).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({'passed':True,'path':(directory/name).relative_to(ROOT).as_posix(),'sha256':sha(directory/name),'counts':value.get('counts')},ensure_ascii=False),flush=True)
    return value


def verify(directory):
    plan=registered_plan(directory);plan_sha=sha(directory/'plan.json')
    library=read(directory/'library-completion.json');tests=read(directory/'pre-path-tests.json')
    require(library['planSha256']==plan_sha and library['independentPathExecutions']==16310 and library['newCandidateAccounts']==0,'Library completion scope')
    require(tests['passed'] is True and tests['exitCode']==0 and tests['planSha256']==plan_sha,'Required pre-path tests failed/stale')
    bound(tests['log'])
    time=lambda x:datetime.fromisoformat(x.replace('Z','+00:00'))
    require(time(plan['registeredAt'])<=time(tests['startedAt'])<=time(tests['completedAt']),'Pretests registration chronology')
    original=list(records(bound(plan['opportunities'])))
    require(len(original)==8155,'All source formations retained')
    indexed={(r['period'],r['board'],r['formationId']):r for r in original}
    require(len(indexed)==8155,'Unique original source formations')
    expected_groups=[(p,b,e) for p in ('2022-2023','2024-2025') for b in ('main','chinext','star') for e in ('trail10','volatilityTrail')]
    same([(r['period'],r['board'],r['exit']) for r in library['groups']],expected_groups,'Original ordered twelve-group matrix')
    counts=Counter();group_checks=[];direct=[];input_pins={}
    dataset=None;source_key=None;views={};digests={}
    for group in library['groups']:
        p,b,e=group['period'],group['board'],group['exit'];folder=directory/p/b/e
        saved=read(folder/'completion.json');same(group,saved,'Library per-group completed receipt')
        require(group['planSha256']==plan_sha and group['newCandidateAccounts']==0,'Group plan/scope')
        start=read(folder/'started.json')
        require(start['planSha256']==plan_sha and time(tests['completedAt'])<=time(start['startedAt'])<=time(group['completedAt']),
                'All pretests before actual path execution')
        if source_key!=(p,b):
            source_key=(p,b);source_ref=plan['sourceData'][p][b]['accountData'];dataset=read(bound(source_ref));views={};digests={}
            require(digest(dataset)==plan['sourceData'][p][b]['accountDatasetDigest'],'Exact full source cache digest')
            input_pins[source_ref['path']]=source_ref['sha256']
        original_rows=[r for r in original if r['period']==p and r['board']==b]
        schedule=read(bound(plan['baselineSchedules'][p][b]));same(schedule,[r['signals']['immediate'] for r in original_rows],'Original fixed schedule')
        path_file=bound(group['paths']);input_pins[group['paths']['path']]=group['paths']['sha256']
        # Keep only compact classification keys and at most six selected paths;
        # all detailed evidence streams through the full independent auditor.
        selected={};first_by_kind={};status_counts=Counter();local=Counter();last_path=None
        for i,(row,origin) in enumerate(zip_longest(records(path_file),original_rows)):
            require(row is not None and origin is not None,'Missing or extra independent path')
            require(row['period']==p and row['board']==b and row['exit']==e,'Path group identity')
            for key in ('formationId','formationDate','symbol'):same(row[key],origin[key],'Original formation identity '+key)
            same(row['originalDisposition'],origin['accountDispositions'][e],'Original baseline disposition')
            same(row['originalEntered'],origin['accountDispositions'][e]['filledQuantity']>0,'Baseline actual-entered subset mask')
            symbol=row['symbol']
            if symbol not in views:views[symbol]=projection_reference(dataset,symbol);digests[symbol]=digest(views[symbol])
            audit=audit_path(row['path'],schedule[i],views[symbol],plan['periods'][p]['policyConfigs'][e],p,e,digests[symbol])
            local['paths']+=1;status_counts[audit['status']]+=1
            for field in ('entered','fills','heldCloses','queuedExits','processedSessions','earlyStopped'):local[field]+=audit[field]
            local['originalEntered']+=row['originalEntered']
            if row['originalEntered'] and audit['entered']:local['originalEnteredAndExecutable']+=1
            path=row['path'];last_path=(i,path)
            if i==0:selected[i]=path
            kinds=[path['status']]
            if path['evidence']['opportunity']['status']=='partial' or any(f['positionAction']=='reduce' for f in path['evidence']['trades']):kinds.append('partial')
            for kind in ('closed','unfilled','open','partial'):
                if kind in kinds and kind not in first_by_kind:first_by_kind[kind]=i;selected[i]=path
            if (i+1)%250==0:print(json.dumps({'auditedGroup':[p,b,e],'paths':i+1,'target':len(schedule)}),flush=True)
        require(local['paths']==len(schedule)==group['pathCount'],'Path count, order and no trailing duplicates')
        if last_path:selected[last_path[0]]=last_path[1]
        wanted=[]
        for idx in [0,len(schedule)-1,*[first_by_kind.get(k) for k in ('closed','unfilled','open','partial')]]:
            if idx is not None and idx not in wanted:wanted.append(idx)
        require(len(wanted)<=6,'Unregistered equivalence sample expansion')
        for idx in wanted:
            path=selected[idx];result=direct_reference(path,views[path['signal']['symbol']],plan['periods'][p]['policyConfigs'][e],p,e)
            direct.append(dict(period=p,board=b,exit=e,originalIndex=idx,**result))
        same(dict(status_counts),group['counts'],'All outcome status counts')
        counts.update(local);counts.update(status_counts)
        group_checks.append(dict(period=p,board=b,exit=e,passed=True,paths=group['paths'],counts=dict(local),statuses=dict(status_counts),directSampleIndices=wanted))
        require(sha(path_file)==group['paths']['sha256'],'Path stream changed during independent verification')
    require(counts['paths']==16310 and len(group_checks)==12,'Complete independent library proof')
    registered_plan(directory)
    proof=dict(passed=True,verifiedAt=datetime.now(timezone.utc).isoformat(),planSha256=plan_sha,
        libraryCompletionSha256=sha(directory/'library-completion.json'),verifierSha256=sha(__file__),
        prePathTests={'path':(directory/'pre-path-tests.json').relative_to(ROOT).as_posix(),'sha256':sha(directory/'pre-path-tests.json')},
        sourcePins={**DEPENDENCIES,FEE_BINDING['path']:FEE_BINDING['sha256'],
                    Path(__file__).relative_to(ROOT).as_posix():sha(__file__)},inputPins=input_pins,
        counts={**dict(counts),'formations':8155,'groups':12,'directReferencePaths':len(direct),'newCandidateAccounts':0},
        groups=group_checks,directReferenceSamples=direct,
        checks=['All original identity/subset/source/calendars exact; no signal regeneration',
            'Every path opening should-fill/should-reject, dated fees and cash/quantity independently checked',
            'Every held close risk including unknown windows, first-exit priority and preclose conversion independently replayed',
            'Only fully closed/settled cash labels mature; open/unfilled are never zero returns',
            'Bounded deterministic original-class full-period references establish wrapper/early-stop equivalence'],
        scope='Independent label paths, not an investable combined portfolio; all dates remain development. No production path wrapper imported.',
        holdoutClaim=False,certified=False,targetAchieved=False)
    return write_proof(directory,'validation.json',proof)


def self_test():
    import unittest
    spec=importlib.util.spec_from_file_location('_independent_label_tests',ROOT/'tests/test_independent_shadow_labels_verifier.py')
    tests=importlib.util.module_from_spec(spec);spec.loader.exec_module(tests)
    result=unittest.TextTestRunner(stream=sys.stderr,verbosity=1).run(unittest.defaultTestLoader.loadTestsFromModule(tests))
    require(result.wasSuccessful(),'Independent synthetic audit failed')
    print(json.dumps(dict(passed=True,syntheticTests=result.testsRun,realSourceReads=0,independentHistoricalPaths=0,newCandidateAccounts=0)),flush=True)


if __name__=='__main__':
    parser=ArgumentParser();parser.add_argument('--self-test',action='store_true');parser.add_argument('--out',type=Path,default=DEFAULT)
    args=parser.parse_args()
    if args.self_test:self_test()
    else:
        try:verify(args.out.resolve())
        except Exception as error:
            folder=args.out/'validation-attempts';folder.mkdir(parents=True,exist_ok=True)
            record=dict(passed=False,at=datetime.now(timezone.utc).isoformat(),type=type(error).__name__,message=str(error),traceback=traceback.format_exc())
            with (folder/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')).open('x',encoding='utf-8') as f:json.dump(record,f,ensure_ascii=False,indent=2)
            raise
