"""Independent unchanged-signal and every-held-day volatility-ratchet audit.

The new signal builder and runner are never imported. Source identity and all
risk arithmetic are independently reconstructed. Previously frozen independent
board-aware cash/share/fee audit helpers are reused under exact hashes.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import gc
import gzip
import hashlib
import importlib.util
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/reclaim-volatility-trail-v1'
SOURCE_PARENT = ROOT / 'artifacts/strategy-search/board-coverage-reclaim-v1'
PARENT = PREVIOUS = ROOT / 'artifacts/strategy-search/reclaim-exit-transfer-v1'
EXIT_REFERENCE = 'scripts/verify-reclaim-exit-transfer.py'
EXIT_REFERENCE_SHA = '5437d5e9421f65f935d211c553e95c473cf40fddafaf09b12694b3bf3548913a'
BOARD_REFERENCE = 'scripts/verify-board-coverage-reclaim.py'
BOARD_REFERENCE_SHA = '8d95ec533c901c7e47b6c23beb6567ae6760900d7be9e6038fff0d58853b6c29'
PARENT_PLAN_SHA = '0894322f614ee4c482cab1a6e00c235dddf94e0bdfcaf462865e34d938c546c1'
PARENT_SOURCE_VALIDATION_SHA = 'fb666f86c8f78cd8c1b569bb8a44030e567b56c22db4d91b4880afb91152c649'
PARENT_INPUT_VALIDATION_SHA = 'aedb98f3f86446a9ee30615868506c50a7bae085aed6d37ebd91f8b0dd9b8dc8'
PARENT_VALIDATION_SHA = '4916272c91b8f5678740670e1edb0bc002d1b00d391d669638e28b4d09c54016'
PARENT_DELIVERY_SHA = '0b25d2dc102f36fd5e282202942be3aa1b9d587705420883aa48fe77b1a4b7f8'
REGISTERED_PLAN_SHA = None
PROTOCOL_PATH = 'docs/reclaim-volatility-trail-v1.json'
PROTOCOL_SHA = '43d44d54803b0edf6e24fa71b62aa8adf7556d3b3fcc6b1cb7637326a39d5dc6'
SID = BASELINE_SID = 'strongUndercutReclaimV1'
MODES = ('volatilityTrail',)
REPORT_MODES = ('trail10', *MODES)
BOARDS = ('main', 'chinext', 'star')
PERIODS = ('2022-2023', '2024-2025')
LABELS = {'trail10':'固定百分比跟踪','volatilityTrail':'波动比例跟踪'}
DECISION_STATUSES = ('triggered','notTriggered','unknown','skippedExistingExit','skippedStandardExit')
WINDOW_STATUSES = ('known','unknown','invalidDistance')



def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def positive(value):
    return finite(value) and value > 0


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def read(path):
    path = Path(path)
    with (gzip.open(path, 'rt', encoding='utf-8') if path.name.endswith('.gz') else path.open(encoding='utf-8')) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def reference(path, expected):
    require(sha(ROOT / path) == expected, 'Frozen independent helper changed: ' + path)
    spec = importlib.util.spec_from_file_location('_volume_anchor_independent_' + Path(path).stem.replace('-', '_'), ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def same(actual, expected, label='value'):
    if type(expected) is int:
        require(type(actual) is int and actual == expected, label + ': integer differs')
    elif finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8), label + ': number differs')
    elif isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), label + ': fields differ')
        for key in expected:
            same(actual[key], expected[key], label + '.' + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + ': length differs')
        for n, (left, right) in enumerate(zip(actual, expected)):
            same(left, right, f'{label}[{n}]')
    else:
        require(type(actual) is type(expected) and actual == expected, label + ': literal differs')


def local(path):
    value = (ROOT / path).resolve()
    require(not Path(path).is_absolute() and value.is_relative_to(ROOT), 'Nonlocal evidence path')
    return value


def pinned(ref):
    require(isinstance(ref, dict) and {'path', 'sha256'} <= set(ref), 'Missing evidence reference')
    path = local(ref['path'])
    require(sha(path) == ref['sha256'], 'Frozen artifact changed: ' + ref['path'])
    if 'bytes' in ref:
        require(path.stat().st_size == ref['bytes'], 'Frozen artifact byte count changed')
    return path


def calendar_check(days):
    require(isinstance(days, list) and bool(days) and all(type(day) is str and date.fromisoformat(day).isoformat() == day for day in days)
            and days == sorted(set(days)), 'Calendar must contain unique increasing ISO dates')


def prior_bar_issue(bar, day):
    if not isinstance(bar, dict):
        return 'missingBar'
    if bar.get('date') != day:
        return 'barDateMismatch'
    if not all(positive(bar.get(k)) for k in ('open','high','low','close','volume')):
        return 'nonPositiveOrInvalidOHLCV'
    if bar['low'] > min(bar['open'],bar['close']) or bar['high'] < max(bar['open'],bar['close']):
        return 'invalidOHLCEnvelope'
    return None


def volatility_reference(day, dates, bars, references):
    """Independently evaluate normalized TR from an exact prior-calendar slice."""
    require(isinstance(dates,list) and isinstance(bars,list) and isinstance(references,dict)
            and len(dates)==len(bars) and len(dates)<=21,'Aligned prior window shape')
    for d in [day,*dates]:
        require(type(d) is str and date.fromisoformat(d).isoformat()==d,'Canonical window date')
    require(dates==sorted(set(dates)) and all(d<day for d in dates),'Strict prior ordered calendar')
    bar_issues=[]
    for d,bar in zip(dates,bars):
        issue=prior_bar_issue(bar,d)
        if issue:bar_issues.append({'date':d,'reason':issue})
    issues=deepcopy(bar_issues)
    if len(dates)!=21:issues.insert(0,{'date':day,'reason':'insufficientPriorCalendarSlots'})
    terms=[]
    for i in range(1,len(dates)):
        d,previous_day=dates[i],dates[i-1]
        bar,prior=bars[i],bars[i-1]
        explicit=d in references
        ref=references[d] if explicit else prior.get('close') if isinstance(prior,dict) else None
        reason='invalidCurrentBar' if prior_bar_issue(bar,d) else None
        if not positive(ref):
            ref_issue='invalidExplicitPreclose' if explicit else 'invalidPreviousCalendarClose'
            issues.append({'date':d,'reason':ref_issue})
            reason=reason or ref_issue
        tr=pct=None
        if reason is None:
            tr=max(bar['high']-bar['low'],abs(bar['high']-ref),abs(bar['low']-ref))
            pct=tr/ref
            if not finite(tr) or not finite(pct):
                tr=pct=None
                reason='nonFiniteTrueRange'
                issues.append({'date':d,'reason':reason})
        terms.append({'date':d,'previousDate':previous_day,
            'referenceSource':'precloseByDate' if explicit else 'previousCalendarClose',
            'referenceValue':ref if positive(ref) else None,
            'high':bar['high'] if isinstance(bar,dict) and positive(bar.get('high')) else None,
            'low':bar['low'] if isinstance(bar,dict) and positive(bar.get('low')) else None,
            'trueRange':tr,'trPct':pct,'status':'unknown' if reason else 'known','reason':reason})
    mean=width=None
    status='unknown'
    if not issues:
        try:
            mean=math.fsum(t['trPct'] for t in terms)/20
            width=3*mean
        except OverflowError:
            mean=width=None
        if not finite(mean) or not finite(width):
            mean=width=None
            issues.append({'date':day,'reason':'nonFiniteMeanOrDistance'})
        elif width<=0 or width>=1:
            status='invalidDistance'
            issues.append({'date':day,'reason':'distanceNotStrictlyBetweenZeroAndOne'})
        else:status='known'
    return {'asOfDate':dates[-1] if dates else None,'windowDates':list(dates),'barIssues':bar_issues,
        'trTerms':terms,'meanTRPct20':mean,'scaledDistancePct':width,'windowStatus':status,'issues':issues}


def trail_step_reference(day, holding_days, position, binding, bar, window, order, existing):
    """One held-close observation; input state is not mutated."""
    valid=bar is not None and bar.get('date')==day and positive(bar.get('close')) and positive(bar.get('volume'))
    old=binding['ratchetRatio']
    candidate_price=candidate=None
    ratio=old
    if order is not None:
        status='skippedExistingExit' if existing else 'skippedStandardExit'
        reason='earlierForcedExitRetained' if existing else 'standardRiskHasPriority'
    elif not valid:
        status,reason='unknown','missingOrInvalidCurrentCloseOrVolume'
    else:
        if window['windowStatus']=='known':
            candidate_price=position['closePeak']*(1-window['scaledDistancePct'])
            candidate=candidate_price/position['riskEntryPrice']
            require(positive(candidate_price) and positive(candidate),'Finite positive candidate required')
            ratio=candidate if old is None else max(old,candidate)
        status='unknown' if ratio is None else 'triggered' if bar['close']<=ratio*position['riskEntryPrice'] else 'notTriggered'
        reason=('noKnownThreshold' if ratio is None else 'knownWindow' if window['windowStatus']=='known'
                else 'carriedThresholdDespiteUnavailableWindow')
    return {'date':day,'symbol':binding['symbol'],'signalId':binding['signalId'],'signalDate':binding['signalDate'],
        'entryDate':position['entryDate'],'holdingDays':holding_days,'quantity':position['quantity'],
        'observedClose':bar['close'] if bar else None,'observedVolume':bar['volume'] if bar else None,
        'currentPriceValid':valid,'riskEntryPrice':position['riskEntryPrice'],'closePeak':position['closePeak'],
        'window':deepcopy(window),'previousRatio':old,'candidatePrice':candidate_price,'candidateRatio':candidate,
        'ratchetRatio':ratio,'threshold':ratio*position['riskEntryPrice'] if ratio is not None else None,
        'thresholdKnown':ratio is not None,
        'usedCarriedThreshold':old is not None and valid and order is None and window['windowStatus']!='known',
        'decisionStatus':status,'decisionReason':reason,
        'exitReason':order['reason'] if order else 'volatilityTrail' if status=='triggered' else None,
        'exitSignalDate':order['signalDate'] if order else day if status=='triggered' else None}


def detail_reference(entries, observations):
    return {'version':'research-volatility-trail-v1',
        'parameters':{'windowSlots':21,'trueRangeCount':20,'multiplier':3,'windowEnd':'heldCloseT-1'},
        'checkpointRestoreSupported':False,'entryBindings':entries,'observations':observations,
        'observationCounts':{s:sum(r['decisionStatus']==s for r in observations) for s in DECISION_STATUSES},
        'windowCounts':{s:sum(r['window']['windowStatus']==s for r in observations) for s in WINDOW_STATUSES},
        'formula':'mean(TR/reference) over20 exact prior sessions; candidate=closePeak*(1-3*mean)/riskEntryPrice; ratchetRatio=max(previous,candidate)',
        'priority':'existingForcedExit; inheritedEligibilityDelistSTAndStop5; volatilityTrail; noDay5/noPercentageTrail',
        'unknownPolicy':'Unavailable window creates no new candidate; existing ratio remains usable at a valid current close. Invalid current close/volume neither updates nor triggers.',
        'referencePolicy':'Ratio is unchanged by inherited simultaneous riskEntryPrice/closePeak conversion; raw historical rights uncertainty remains.',
        'certified':False,'targetAchieved':False}


def synthetic_risk_fixture():
    """Tiny saved-risk evidence, built without source files or any account engine."""
    days=[(date(2020,1,1)+timedelta(days=i)).isoformat() for i in range(28)]
    symbol='sh600001'
    bars=[{'date':d,'open':100.,'high':101.,'low':99.,'close':100.,'volume':10000.} for d in days]
    bars[21].update(high=111.,close=110.)
    bars[22].update(high=111.,close=105.,volume=0.)
    bars[23].update(high=105.,close=102.)
    bars[24].update(open=106.,high=121.,low=105.,close=120.)
    bars[25].update(open=108.,high=110.,low=107.,close=109.)
    bars[27].update(open=50.,high=51.,low=49.,close=50.)
    security={'symbol':symbol,'bars':bars,'precloseByDate':{days[27]:50.}}
    data={'calendar':days,'securities':[security]}
    config={'startDate':days[21],'endDate':days[27],'rebalanceMode':'risk','universe':{'excludeSt':True},
        'stopLossPct':.05,'trailingStopPct':0.,'takeProfitPct':0.,'maxHoldingDays':0}
    result={'config':config,'daily':[],'trades':[],'decisions':[],'forcedExits':{},'positions':[],
        'versions':{'researchVolatilityTrail':'research-volatility-trail-v1'},'research':{}}
    observations,entries=[],[]
    binding=position=order=None
    for i in range(21,28):
        day=days[i]
        if i in (21,26):
            fill={'side':'buy','symbol':symbol,'date':day,'quantity':100,'price':100.,
                'signalId':SID+':'+days[i-1]+':'+symbol,'signalDate':days[i-1],
                'fillId':'entry'+str(i),'positionAction':'open'}
            result['trades'].append(fill)
            position={'entryDate':day,'quantity':100,'riskEntryPrice':100.,'closePeak':100.}
            entry={k:fill[k] for k in ('signalId','signalDate','symbol')}
            entry.update(entryDate=day,entryFillId=fill['fillId'],entryQuantity=100,entryFillPrice=100.)
            entries.append(deepcopy(entry));binding={**entry,'ratchetRatio':None}
        if i in (24,25):
            quantity=40 if i==24 else 60
            result['trades'].append({'side':'sell','symbol':symbol,'date':day,'quantity':quantity,
                'price':bars[i]['open'],'signalDate':order['signalDate'],'reason':order['reason'],
                'riskEvidence':deepcopy(order['riskEvidence'])})
            position['quantity']-=quantity
            if position['quantity']==0:position=binding=order=None
        if i==27:
            position['riskEntryPrice']*=.5;position['closePeak']*=.5
        result['daily'].append({'date':day,'holdings':[] if position is None else [{'symbol':symbol,'quantity':position['quantity']}]})
        if position is None:continue
        if bars[i]['volume']>0:position['closePeak']=max(position['closePeak'],bars[i]['close'])
        window=volatility_reference(day,days[i-21:i],bars[i-21:i],security['precloseByDate'])
        observation=trail_step_reference(day,i-days.index(position['entryDate'])+1,position,binding,bars[i],window,order,order is not None)
        observations.append(observation);binding['ratchetRatio']=observation['ratchetRatio']
        if observation['decisionStatus']=='triggered':
            order={'signalDate':day,'reason':'volatilityTrail','riskEvidence':deepcopy(observation)}
            result['decisions'].append({'date':day,'symbol':symbol,'action':'sell','status':'queued',
                'reason':'volatilityTrail','signalDate':day,'observedClose':bars[i]['close'],'riskEvidence':deepcopy(observation)})
    result['positions']=[{'symbol':symbol,**position}]
    result['forcedExits']={} if order is None else {symbol:order}
    detail=detail_reference(entries,observations)
    result['research']['volatilityTrail']=deepcopy(detail)
    checkpoint={'volatilityTrail':deepcopy(detail),'currentPositionTrails':{symbol:deepcopy(binding)}}
    return result,checkpoint,data


def self_test():
    tests=0
    def checked(value,message):
        nonlocal tests
        require(value,message);tests+=1
    def rejects(fn,message):
        nonlocal tests
        try:fn()
        except (ValueError,AssertionError):tests+=1
        else:raise ValueError(message)
    days=[(date(2020,1,1)+timedelta(days=i)).isoformat() for i in range(23)]
    bars=[{'date':d,'open':100.,'high':101.,'low':99.,'close':100.,'volume':10000.} for d in days[:21]]
    w=volatility_reference(days[21],days[:21],bars,{})
    checked(w['windowStatus']=='known' and w['meanTRPct20']==.02 and w['scaledDistancePct']==.06 and len(w['trTerms'])==20,'Twenty exact normalized terms')
    split=deepcopy(bars);split[-1].update(open=50.,high=50.5,low=49.5,close=50.)
    split_w=volatility_reference(days[21],days[:21],split,{days[20]:50.})
    checked(split_w['meanTRPct20']==.02 and split_w['trTerms'][-1]['referenceSource']=='precloseByDate','Explicit reference uses same-day units')
    invalid=volatility_reference(days[21],days[:21],split,{days[20]:None})
    checked(invalid['windowStatus']=='unknown' and invalid['trTerms'][-1]['referenceValue'] is None and invalid['trTerms'][-1]['reason']=='invalidExplicitPreclose','Invalid supplied preclose never falls back')
    missing=deepcopy(bars);missing[10]=None
    missing_w=volatility_reference(days[21],days[:21],missing,{})
    checked(missing_w['windowStatus']=='unknown' and len(missing_w['windowDates'])==21 and len(missing_w['trTerms'])==20,'Missing interior slot is not compressed')
    bad=deepcopy(bars);bad[3]['low']=101.
    checked(volatility_reference(days[21],days[:21],bad,{})['barIssues']==[{'date':days[3],'reason':'invalidOHLCEnvelope'}],'OHLC envelope independently checked')
    checked(volatility_reference(days[21],days[:20],bars[:20],{})['issues'][0]['reason']=='insufficientPriorCalendarSlots','Short exact window unknown')
    rejects(lambda:volatility_reference(days[20],days[:21],bars,{}),'Current day accepted in volatility window')
    zero=deepcopy(bars)
    for row in zero:row['high']=row['low']=100.
    checked(volatility_reference(days[21],days[:21],zero,{})['windowStatus']=='invalidDistance','Zero width cannot create floor')
    wide=deepcopy(bars)
    for row in wide:row['high']=200.
    checked(volatility_reference(days[21],days[:21],wide,{})['windowStatus']=='invalidDistance','Width >=one cannot create floor')
    p={'entryDate':days[20],'quantity':100,'riskEntryPrice':100.,'closePeak':120.}
    binding={'symbol':'sh600001','signalId':'fixture','signalDate':days[19],'entryDate':days[20],'ratchetRatio':1.15}
    current={'date':days[21],'close':116.,'volume':100.}
    step=lambda p=p,b=binding,c=current,x=w,o=None,e=False:trail_step_reference(days[21],2,p,b,c,x,o,e)
    before=deepcopy((p,binding,current,w))
    out=step()
    checked(out['candidateRatio']<1.15 and out['ratchetRatio']==1.15,'Widening/new candidate cannot loosen an old floor')
    checked(step(c={**current,'close':out['threshold']})['decisionStatus']=='triggered','Equality triggers')
    carried=step(c={**current,'close':114.},x=missing_w)
    checked(carried['decisionStatus']=='triggered' and carried['usedCarriedThreshold'],'Unknown window still enforces prior known floor')
    frozen=step(c={**current,'volume':0.})
    checked(frozen['candidateRatio'] is None and frozen['ratchetRatio']==1.15 and frozen['decisionStatus']=='unknown','Invalid current price freezes, never silently tightens')
    checked(step(b={**binding,'ratchetRatio':None},x=missing_w)['decisionStatus']=='unknown','Absent initial floor remains unknown')
    order={'reason':'stopLoss','signalDate':days[20]}
    checked(step(o=order,e=True)['decisionStatus']=='skippedExistingExit' and step(o=order,e=True)['candidateRatio'] is None,'Existing immutable exit has priority')
    checked(step(o=order)['decisionStatus']=='skippedStandardExit','New standard stop has priority')
    converted=step(p={**p,'riskEntryPrice':50.,'closePeak':60.},c={**current,'close':58.})
    checked(converted['ratchetRatio']==out['ratchetRatio'] and converted['threshold']==out['threshold']/2,'Reference conversion preserves dimensionless protection')
    checked((p,binding,current,w)==before,'Reference helper never mutates inputs')
    result,checkpoint,data=synthetic_risk_fixture()
    fixture_before=digest([result,checkpoint,data])
    audit=daily_risk_reference(result,checkpoint,data,'volatilityTrail')
    checked(audit['heldDays']==6 and audit['actualEntries']==2 and audit['queuedExits']==1
            and audit['usedCarriedThresholdCount']==1 and audit['riskReferenceConversions']==1
            and result['decisions'][0]['date']==data['calendar'][23]
            and checkpoint['currentPositionTrails']['sh600001']['ratchetRatio'] is None,
            'Every held day, missing-window trigger, partial exit, immutable rebound, reentry reset and reference conversion')
    deleted=deepcopy(result);deleted['decisions']=[]
    rejects(lambda:daily_risk_reference(deleted,checkpoint,data,'volatilityTrail'),'Missing should-trigger exit accepted')
    removed=deepcopy(result);removed['research']['volatilityTrail']['observations'].pop(0)
    rejects(lambda:daily_risk_reference(removed,checkpoint,data,'volatilityTrail'),'Missing nontrigger held-close observation accepted')
    forged=deepcopy(checkpoint);forged['currentPositionTrails']['sh600001']['ratchetRatio']=1.1
    rejects(lambda:daily_risk_reference(result,forged,data,'volatilityTrail'),'Stale prior-episode floor accepted')
    checked(digest([result,checkpoint,data])==fixture_before,'Full independent risk audit is immutable')
    print(json.dumps({'passed':True,'selfTests':tests,'realSourceRead':False,'accountsExecuted':0}),flush=True)


def daily_risk_reference(result, checkpoint, dataset, mode):
    """Replay every held close from actual fills, never from observed sell signals.

    This is a risk-decision audit, not a second account execution. Existing
    opening execution/cash/capacity checks are handled by independent helpers.
    """
    require(mode in MODES, 'Unexpected exit policy')
    config, calendar = result['config'], dataset['calendar']
    require(config['stopLossPct']==.05 and config['trailingStopPct']==0 and config['takeProfitPct']==0 and config['maxHoldingDays']==0,'Volatility policy risk configuration')
    calendar_check(calendar)
    index = {d: n for n, d in enumerate(calendar)}
    target = [d for d in calendar if config['startDate'] <= d <= config['endDate']]
    require([d['date'] for d in result['daily']] == target, 'Every target day must be retained')
    securities = {s['symbol']: s for s in dataset['securities']}
    require(len(securities) == len(dataset['securities']), 'Duplicate securities')
    require(not dataset.get('corporateActions') and not any(s.get('corporateActions') for s in securities.values()), 'Declared rights outside frozen source scope')
    rows = {s: security['bars'] for s, security in securities.items()}
    bars = {s: {b['date']: b for b in values} for s, values in rows.items()}
    bar_index = {s: {b['date']: n for n, b in enumerate(values)} for s, values in rows.items()}
    require(all(len(bars[s]) == len(rows[s]) for s in rows), 'Duplicate source bar')
    memberships = dataset.get('membershipByDate', {})
    fills = defaultdict(list)
    for fill in result['trades']:
        require(fill['date'] in target, 'Trade outside target calendar')
        fills[fill['date']].append(fill)
    positions, pending, queued, observations, trace, conversions = {}, {}, [], [], [], []
    trail_positions, entry_bindings = {}, []
    entry_count = 0

    def member(symbol, day):
        if day in memberships:
            return symbol in memberships[day]
        security = securities[symbol]
        return security.get('memberHistory', security.get('memberByDate', {})).get(day)

    def enqueue(symbol, day, reason, evidence):
        require(symbol not in pending, 'First risk intent was overwritten')
        pending[symbol] = {'signalDate': day, 'reason': reason, 'riskEvidence': deepcopy(evidence)}
        queued.append({'date': day, 'symbol': symbol, 'action': 'sell', 'status': 'queued', 'reason': reason,
            'signalDate': day, 'observedClose': evidence['observedClose'], 'riskEvidence': deepcopy(evidence)})

    for daily in result['daily']:
        day = daily['date']
        for symbol, position in positions.items():
            i = bar_index[symbol].get(day)
            if not i:
                continue
            previous = rows[symbol][i-1]['close']
            preclose = securities[symbol].get('precloseByDate', {}).get(day)
            if preclose is not None and abs(preclose-previous) > max(.005, previous*1e-7):
                factor = preclose / previous
                position['riskEntryPrice'] *= factor
                position['closePeak'] *= factor
                conversions.append({'date': day, 'symbol': symbol, 'factor': factor})
        for fill in fills[day]:
            symbol = fill['symbol']
            if fill['side'] == 'buy':
                require(symbol not in positions, 'Unexpected position add')
                positions[symbol] = {'entryDate': day, 'quantity': fill['quantity'],
                    'riskEntryPrice': fill['price'], 'closePeak': fill['price']}
                binding={'signalId':fill['signalId'],'signalDate':fill['signalDate'],'symbol':symbol,'entryDate':day,
                    'entryFillId':fill['fillId'],'entryQuantity':fill['quantity'],'entryFillPrice':fill['price']}
                require(fill['positionAction']=='open' and symbol not in trail_positions,'Unexpected entry binding/addition')
                entry_bindings.append(deepcopy(binding))
                trail_positions[symbol]={**binding,'ratchetRatio':None}
                entry_count += 1
            else:
                require(fill['side'] == 'sell' and symbol in positions and symbol in pending, 'Sell lacks reconstructed first exit')
                order = pending[symbol]
                require(order['signalDate'] < day and fill['signalDate'] == order['signalDate']
                        and fill['reason'] == order['reason'], 'Wrong risk reason/date or same-close sell')
                require(index[day] > index[positions[symbol]['entryDate']], 'T+1 violated')
                same(fill['riskEvidence'], order['riskEvidence'], 'Executed immutable first exit evidence')
                positions[symbol]['quantity'] -= fill['quantity']
                require(positions[symbol]['quantity'] >= 0, 'Sell exceeds held shares')
                if not positions[symbol]['quantity']:
                    del positions[symbol]
                    del pending[symbol]
                    del trail_positions[symbol]
        held = {r['symbol']: r for r in daily['holdings']}
        require(len(held) == len(daily['holdings']) and set(held) == set(positions), 'Held-close population differs from fills')
        require(all(held[s]['quantity'] == p['quantity'] for s, p in positions.items()), 'Held-close quantity differs')
        before = deepcopy(pending)
        for symbol in sorted(positions):
            position, security, bar = positions[symbol], securities[symbol], bars[symbol].get(day)
            reason = None
            if member(symbol, day) is False:
                reason = 'baseEligibilityLost'
            elif security.get('delistDate') and day >= security['delistDate']:
                reason = 'delisted'
            elif config['universe']['excludeSt'] and security.get('stByDate', {}).get(day) is True:
                reason = 'historicalST'
            elif config['rebalanceMode'] in ('risk', 'hybrid') and bar is not None and bar['volume'] > 0:
                position['closePeak'] = max(position['closePeak'], bar['close'])
                gain = bar['close'] / position['riskEntryPrice'] - 1
                if config['stopLossPct'] and gain <= -config['stopLossPct']:
                    reason = 'stopLoss'
                elif config['trailingStopPct'] and bar['close'] / position['closePeak'] - 1 <= -config['trailingStopPct']:
                    reason = 'trailingStop'
                elif config['takeProfitPct'] and gain >= config['takeProfitPct']:
                    reason = 'takeProfit'
                elif config['maxHoldingDays'] and index[day]-index[position['entryDate']]+1 >= config['maxHoldingDays']:
                    reason = 'maxHoldingDays'
            if reason and symbol not in pending:
                close = bar['close'] if bar else None
                evidence = {'observedDate': day, 'observedClose': close, 'riskEntryPrice': position['riskEntryPrice'],
                    'closePeak': position['closePeak'], 'gainPct': close / position['riskEntryPrice']-1 if close is not None else None,
                    'drawdownFromPeakPct': close / position['closePeak']-1 if close is not None else None,
                    'holdingDays': index[day]-index[position['entryDate']]+1,
                    **{k: config[k] for k in ('stopLossPct','takeProfitPct','trailingStopPct','maxHoldingDays')}, 'percentageUnit':'ratio'}
                enqueue(symbol, day, reason, evidence)
        for symbol in sorted(positions):
            position, binding = positions[symbol], trail_positions[symbol]
            require(binding['entryDate']==position['entryDate'],'Current episode trail identity')
            prior=calendar[max(0,index[day]-21):index[day]]
            window=volatility_reference(day,prior,[bars[symbol].get(d) for d in prior],securities[symbol].get('precloseByDate',{}))
            evidence=trail_step_reference(day,index[day]-index[position['entryDate']]+1,position,binding,
                bars[symbol].get(day),window,pending.get(symbol),symbol in before)
            binding['ratchetRatio']=evidence['ratchetRatio']
            observations.append(evidence)
            if evidence['decisionStatus']=='triggered':enqueue(symbol,day,'volatilityTrail',evidence)
        for symbol in sorted(positions):
            trace.append({'date':day,'symbol':symbol,**deepcopy(positions[symbol]),
                'ratchetRatio':trail_positions[symbol]['ratchetRatio'],
                'priorExit':deepcopy(before.get(symbol)),'resultingExit':deepcopy(pending.get(symbol))})
    same([r for r in result['decisions'] if r['action']=='sell' and r['status']=='queued'],queued,'All should/should-not-trigger exits')
    same(result['forcedExits'],pending,'Terminal immutable first exits')
    require(not any(r.get('reason') in ('noProgress5','structure10','trailingStop') for r in result['decisions']+result['trades']),
            'An unregistered day5/structure/fixed-percentage exit survived')
    require('structuralExits' not in result['research'] and 'structuralExits' not in checkpoint
            and 'researchStructuralExits' not in result['versions'],'Unregistered structural adapter')
    require(result['versions']['researchVolatilityTrail']=='research-volatility-trail-v1','Volatility adapter version')
    detail=detail_reference(entry_bindings,observations)
    same(result['research']['volatilityTrail'],detail,'Every held-day volatility window and ratchet decision')
    same(checkpoint['volatilityTrail'],detail,'Checkpoint full volatility evidence')
    same(checkpoint['currentPositionTrails'],trail_positions,'Terminal episode ratchet state')
    require({p['symbol'] for p in result['positions']}==set(positions),'Terminal risk population')
    for observed in result['positions']:
        for key,value in positions[observed['symbol']].items():same(observed[key],value,'Terminal risk '+key)
    return {'heldDays':len(trace),'actualEntries':entry_count,'queuedExits':len(queued),
        'exitReasons':dict(Counter(r['reason'] for r in queued)),'riskReferenceConversions':len(conversions),
        'volatilityObservations':len(observations),'observationCounts':detail['observationCounts'],
        'windowCounts':detail['windowCounts'],'usedCarriedThresholdCount':sum(r['usedCarriedThreshold'] for r in observations),
        'terminalPendingExits':len(pending),'terminalRatchetStates':len(trail_positions),
        'heldDayRiskTraceSha256':digest(trace),'unregisteredExitsAbsent':True}


def audit_account(result, checkpoint, summary, schedule, config, dataset, period, mode, board, accounting):
    cost = accounting.cost_reference()
    relative, market, account = cost.reference_modules(period)
    account.SID = SID
    cost.verify_recorded_config(result['config'], config, relative.same)
    require(result['status']=='completed' and result['dataDigest']==digest(dataset)
            and result['fingerprint']==str(dataset.get('fingerprint') or digest(dataset)), 'Account source identity')
    identity = result['research']['identity']
    require(identity['signalSchedule']==schedule and identity['signalScheduleSha256']==digest(schedule)
            and identity['entryWeight']==.2, 'Unchanged entire schedule and capital sizing')
    require(all(not result[k] for k in ('actionLedger','cashReceivables','actionPayables','preExRights'))
            and result['reservedActionCash']==0, 'Undeclared rights introduced')
    start,end,sessions,_ = cost.PERIODS[period]
    calendar = dataset['calendar']
    target = [d for d in calendar if start<=d<=end]
    require(len(target)==sessions, 'Complete target calendar')
    ledger = cost.verify_fills(result,period,10)
    execution = account.verify_opportunity_execution(schedule,result['research']['opportunities'],result['trades'],calendar,start=start,end=end)
    episodes = account.verify_episodes_and_curve(result,target)
    terminal = account.verify_terminal_account(ledger,result['positions'],final_cash=result['curve'][-1]['cash'],
        final_equity=result['metrics']['finalEquity'],final_curve=result['curve'][-1])
    statistics = account.verify_summary(result,summary,market.calendar_intervals(period))
    require(checkpoint['digest']==digest({k:v for k,v in checkpoint.items() if k!='digest'}),'Checkpoint digest')
    for key in ('trades','closedTrades','curve','daily','decisions','forcedExits','pendingTarget',
                'actionLedger','cashReceivables','actionPayables','preExRights','reservedActionCash'):
        same(checkpoint['engineState'][key],result[key],'Checkpoint/result '+key)
    same(checkpoint['researchState']['opportunities'],result['research']['opportunities'],'Checkpoint opportunities')
    same(checkpoint['identity'],identity,'Checkpoint immutable account identity')
    same(checkpoint['feeState'],result['research']['feeLedger'],'Checkpoint fee ledger')
    same(checkpoint['engineState']['cash'],result['curve'][-1]['cash'],'Checkpoint final cash')
    require(mode == 'volatilityTrail' and checkpoint['schema']=='research-volatility-trail-descriptive-snapshot-v1', 'Checkpoint adapter schema')
    require(checkpoint['checkpointRestoreSupported'] is False,'Volatility snapshot restore scope')
    saved_positions=checkpoint['engineState']['positions']
    require(set(saved_positions)=={p['symbol'] for p in result['positions']},'Checkpoint position coverage')
    for position in result['positions']:
        for key in ('quantity','entryDate','riskEntryPrice','closePeak'):
            same(saved_positions[position['symbol']][key],position[key],'Checkpoint terminal position '+key)
    bars = {s['symbol']:{b['date']:b for b in s['bars']} for s in dataset['securities']}
    closes = {(r['symbol'],r['signalDate']):bars[r['symbol']].get(r['signalDate'],{}).get('close') for r in schedule}
    reserves = board.verify_board_reserves(result,closes,period,cost)
    indexes = {d:n for n,d in enumerate(calendar)}
    source_rows = {s['symbol']:s for s in dataset['securities']}
    for fill in result['trades']:
        symbol,day,side = fill['symbol'],fill['date'],fill['side']
        bar = bars[symbol].get(day)
        require(bar is not None and indexes[day]>0,'Fill has no source bar or previous calendar slot')
        prior = bars[symbol].get(calendar[indexes[day]-1])
        require(prior is not None and prior['volume']>0 and bar['volume']>0,'Fill lacks consecutive positive volume bars')
        same(fill['openingPrice'],bar['open'],'Source opening')
        require(fill['quantity']<=math.floor(prior['volume']*config['maxParticipationRate']),'Prior-volume participation cap')
        kind = board.board_identity(symbol)
        nominal = .1 if kind=='main' else .2
        band = nominal if kind!='main' or source_rows[symbol].get('stByDate',{}).get(day) is False else .05
        preclose = source_rows[symbol].get('precloseByDate',{}).get(day,prior['close'])
        upper = float(Decimal(str(preclose*(1+band))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        lower = float(Decimal(str(preclose*(1-band))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        require(abs(bar['open']/preclose-1)<=nominal+.005,'Filled discontinuous opening')
        require((bar['open']<upper-1e-8 and fill['price']<=upper+1e-8) if side=='buy'
                else (bar['open']>lower+1e-8 and fill['price']>=lower-1e-8),'Opening/filled board limit')
        require(fill['quantity']==fill['quantityBefore'] or fill['quantity']==board.board_quantity(symbol,fill['quantity']),
                'Board partial-fill lot size')
    risk = daily_risk_reference(result,checkpoint,dataset,mode)
    return {'fills':ledger['fills'],'fees':ledger['fees'],'slippage':ledger['slippage'],'execution':execution,
        'episodes':episodes,'terminal':terminal,'statistics':statistics,'reserveChecks':reserves,
        'boardLotAndLimitFillChecks':len(result['trades']),'dailyRisk':risk}


def registered_plan(directory):
    global REGISTERED_PLAN_SHA
    observed=sha(directory/'plan.json')
    require(REGISTERED_PLAN_SHA is None or REGISTERED_PLAN_SHA==observed,'Plan changed during audit')
    REGISTERED_PLAN_SHA=observed
    plan=read(directory/'plan.json')
    require(plan['sourcePins'].get(Path(__file__).relative_to(ROOT).as_posix())==sha(__file__),'Auditor not frozen in plan')
    require(pinned(plan['protocol'])==local(PROTOCOL_PATH) and plan['protocol']['sha256']==PROTOCOL_SHA
            and read(pinned(plan['protocol']))==plan['rules'],'Frozen protocol changed')
    require(plan['schema']=='reclaim-volatility-trail-v1' and plan['strategyId']==SID and plan['modes']==list(MODES)
            and plan['reportModes']==list(REPORT_MODES) and plan['boards']==list(BOARDS)
            and list(plan['periods'])==list(PERIODS) and plan['labels']==LABELS,'Registered identities differ')
    for key,value in plan['rules']['evidence']['trialAccounting'].items():
        require(type(plan[key]) is int and plan[key]==value,'Registered trial accounting differs: '+key)
    require(plan['newAccounts']==6 and plan['referenceAccountsReused']==6 and plan['parameterSearch'] is False
            and plan['profitHurdles'] is None and plan['holdoutClaim'] is False
            and plan['certified'] is False and plan['targetAchieved'] is False,'Unregistered scope or target claim')
    require(plan['policySpecs']==plan['rules']['policies'] and plan['noSignalsGenerated'] is True
            and plan['productionChanges'] is True,'Changed isolated-adapter/signal scope')
    require(plan['preAccountTestSpec']=={'inheritedUnitTests':57,'newUnitTests':20,'commandCount':6},'Frozen test scope')
    require(plan['policySpecs']['volatilityTrail']['lookback']==20
            and plan['policySpecs']['volatilityTrail']['multiplier']==3,'Unregistered window or multiplier')
    for path,expected in {**plan['sourcePins'],**plan['inputPins']}.items():
        require(sha(local(path))==expected,'Registered artifact changed: '+path)
    require(pinned(plan['baselinePlan'])==(PARENT/'plan.json').resolve()
            and plan['baselinePlan']['sha256']==PARENT_PLAN_SHA,'Baseline plan identity')
    parent=read(PARENT/'plan.json')
    for group in ('sourcePins','inputPins'):
        require(all(plan[group].get(p)==h for p,h in parent[group].items()),'Inherited pins changed: '+group)
    for group in ('sourceData','baselineArtifacts','baselineSchedules','baselineInputs'):
        require(list(plan[group])==list(PERIODS) and all(list(plan[group][p])==list(BOARDS) for p in PERIODS),'Six-source matrix: '+group)
    for period in PERIODS:
        original=parent['periods'][period]['config']
        policies={mode:deepcopy(original) for mode in REPORT_MODES}
        policies['volatilityTrail']['trailingStopPct']=0.
        expected={**{k:deepcopy(v) for k,v in parent['periods'][period].items() if k!='policyConfigs'},'policyConfigs':policies}
        require(plan['periods'][period]==expected,'Configuration changed beyond volatility trailing switch')
        require(original['stopLossPct']==.05 and original['trailingStopPct']==.1 and original['takeProfitPct']==0
                and original['maxHoldingDays']==0 and original['allocation']['strategyIds']==[SID], 'Original exit/identity scope')
    return plan


def inherited_sources(plan):
    expected={'plan.json':PARENT_PLAN_SHA,'input-validation.json':PARENT_INPUT_VALIDATION_SHA,
        'validation.json':PARENT_VALIDATION_SHA,'delivery.json':PARENT_DELIVERY_SHA}
    for name,value in expected.items():require(sha(PARENT/name)==value,'Previous independently audited experiment changed: '+name)
    previous=reference(EXIT_REFERENCE,EXIT_REFERENCE_SHA)
    parent=previous.registered_plan(PARENT)
    source=previous.inherited_sources(parent)
    completion,parent_receipts=previous.input_receipts(PARENT,parent)
    inputs,validation=read(PARENT/'input-validation.json'),read(PARENT/'validation.json')
    require(pinned(plan['baselineComparison'])==(PARENT/'comparison.json').resolve(),'Baseline comparison path')
    comparison=read(PARENT/'comparison.json')
    require(inputs['passed'] is True and validation['passed'] is True
            and inputs['planSha256']==validation['planSha256']==PARENT_PLAN_SHA
            and inputs['verifierSha256']==validation['verifierSha256']==EXIT_REFERENCE_SHA
            and validation['inputValidationSha256']==PARENT_INPUT_VALIDATION_SHA
            and inputs['inputCompletionSha256']==validation['inputCompletionSha256']==sha(PARENT/'input-completion.json')
            and validation['comparisonSha256']==sha(PARENT/'comparison.json')
            and inputs['datasetPins']==validation['datasetPins']==source['datasetPins']
            and comparison['artifacts']==validation['accountArtifacts'],'Previous completed-account audit chain stale')
    require(plan['sourcePlan']==parent['baselinePlan'] and plan['sourceComparison']==parent['baselineComparison'],
            'Original source plan/comparison lineage differs')
    require(plan['sourceData']==parent['sourceData'] and plan['baselineSchedules']==parent['baselineSchedules']
            and plan['baselineInputs']==parent['baselineInputs'],'Original source and signal lineage changed')
    delivery=read(PARENT/'delivery.json')
    for name in ('plan.json','input-completion.json','input-validation.json','validation.json','comparison.json'):
        path=PARENT/name
        require(delivery['files'][path.relative_to(ROOT).as_posix()]==sha(path),'Previous delivery link changed')
    for period in PERIODS:
        for board in BOARDS:
            receipt=parent_receipts[period][board]
            require(receipt['files']==inputs['inputArtifacts'][period][board]==validation['inputArtifacts'][period][board],
                    'Original signal artifact closure changed')
            for item in receipt['files'].values():pinned(item)
            artifacts=plan['baselineArtifacts'][period][board]
            require(artifacts==comparison['artifacts'][period][board]['trail10']
                    and set(artifacts)=={'trail10'+s for s in ('.result.json.gz','.checkpoint.json.gz','.summary.json')},
                    'Exact previously completed trail10 artifacts required')
            for name,item in artifacts.items():require(pinned(item)==(PARENT/period/board/name).resolve(),'Reused trail10 artifact path')
    return source


def input_receipts(directory,plan):
    completion=read(directory/'input-completion.json')
    require(completion['planSha256']==REGISTERED_PLAN_SHA and completion['allSchedulesFrozenBeforeAccounts'] is True
            and completion['noSignalsGenerated'] is True and completion['newAccounts']==6 and completion['referenceAccountsReused']==6
            and list(completion['outputs'])==list(PERIODS),'Pure binding input completion differs')
    require(pinned(completion['baselineInputCompletion'])==(PARENT/'input-completion.json').resolve(),'Original full-input receipt binding')
    receipts={}
    for period in PERIODS:
        require(list(completion['outputs'][period])==list(BOARDS),'Complete board input matrix')
        receipts[period]={}
        for board in BOARDS:
            path=pinned(completion['outputs'][period][board])
            require(path==(directory/period/board/'input-completion.json').resolve(),'New input receipt location')
            receipt=read(path); parent=read(pinned(plan['baselineInputs'][period][board]))
            require(receipt['schema']=='reclaim-volatility-trail-input-v1' and receipt['planSha256']==REGISTERED_PLAN_SHA
                    and receipt['period']==period and receipt['board']==board and receipt['noSignalsGenerated'] is True,'Input binding identity')
            require({k:receipt[k] for k in plan['sourceData'][period][board]}==plan['sourceData'][period][board]
                    and receipt['baselineSchedule']==plan['baselineSchedules'][period][board]
                    and receipt['baselineInput']==plan['baselineInputs'][period][board]
                    and receipt['baselineArtifacts']==plan['baselineArtifacts'][period][board]
                    and receipt['files']==parent['files'],'Input exact inherited closure changed')
            for ref in receipt['files'].values():pinned(ref)
            require(datetime.fromisoformat(plan['registeredAt'])<=datetime.fromisoformat(receipt['completedAt'])
                    <=datetime.fromisoformat(completion['completedAt']),'Input binding chronology')
            receipts[period][board]=receipt
    return completion,receipts


def parent_proof_fields(plan):
    return {'parentValidationSha256':PARENT_VALIDATION_SHA,'parentInputValidationSha256':PARENT_INPUT_VALIDATION_SHA,
        'parentSourceValidationSha256':PARENT_SOURCE_VALIDATION_SHA,'parentDeliverySha256':PARENT_DELIVERY_SHA,
        **{k:plan[k] for k in ('baselinePlan','baselineComparison','sourcePlan','sourceComparison','baselineArtifacts','baselineSchedules','baselineInputs')}}


def write_proof(directory,name,proof):
    with (directory/name).open('x',encoding='utf-8') as stream:
        json.dump(proof,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps({'passed':True,'artifact':name,'sha256':sha(directory/name),'counts':proof['counts']}),flush=True)


def verify_inputs(directory):
    started=time.perf_counter()
    require(not (directory/'input-validation.json').exists(),'Input proof already exists')
    plan=registered_plan(directory);source=inherited_sources(plan)
    _,receipts=input_receipts(directory,plan)
    reports,artifacts={},{}
    signals_total=0
    for period in PERIODS:
        reports[period],artifacts[period]={},{}
        for board in BOARDS:
            receipt=receipts[period][board]
            data=read(pinned(receipt['accountData']))
            require(digest(data)==receipt['accountDatasetDigest'] and len(data['securities'])==receipt['members']
                    and data['period']==period and data['board']==board,'Original frozen dataset contents changed')
            signals=read(pinned(receipt['baselineSchedule']))
            opportunities=records(pinned(receipt['files']['opportunities.jsonl.gz']))
            require(signals==[r['signals']['immediate'] for r in opportunities]
                    and all(r['strategyId']==SID for r in signals)
                    and len({r['signalId'] for r in signals})==len(signals),'Complete original immediate opportunity identity/order changed')
            require(receipt['signalCount']==len(signals) and receipt['signalScheduleSha256']==digest(signals),'Schedule count/hash')
            require(signals==sorted(signals,key=lambda r:(r['signalDate'],r['originalRank'],r['symbol'],r['signalId'])), 'Original order changed')
            require(all(set(r)=={'signalId','strategyId','symbol','signalDate','originalRank'} for r in signals),'Original signal field closure')
            signals_total+=len(signals)
            reports[period][board]={'members':receipt['members'],'sourceImmediateOpportunities':len(signals),
                'signalScheduleSha256':digest(signals),'accountDatasetDigest':receipt['accountDatasetDigest'],
                'newPolicySchedulesIdentical':True}
            artifacts[period][board]=receipt['files']
            print(json.dumps({'phase':'independent-volatility-trail-input','period':period,'board':board,'signals':len(signals),'passed':True}),flush=True)
            del data,opportunities;gc.collect()
    registered_plan(directory)
    write_proof(directory,'input-validation.json',{'schema':'reclaim-volatility-trail-input-independent-validation-v1','passed':True,
        'verifiedAt':datetime.now(timezone.utc).isoformat(),'verifierSha256':sha(__file__),'planSha256':REGISTERED_PLAN_SHA,
        'inputCompletionSha256':sha(directory/'input-completion.json'),**parent_proof_fields(plan),
        'datasetPins':source['datasetPins'],'inputArtifacts':artifacts,'periods':reports,
        'counts':{'sourceInputs':6,'originalSchedules':6,'newPolicySchedules':6,'sourceImmediateOpportunities':signals_total},
        'checks':['All six inherited raw-source/cache proofs and baseline account proofs bound',
            'Every original UR immediate opportunity and exact five-field original schedule retained without reranking, filtering or regeneration',
            'The candidate and reference use the same unchanged strategy identity, source and complete signal queue'],
        'scope':{'accountsExecuted':0,'signalsRecomputed':False,'rawSourceReloaded':False,
            'signalArithmetic':'Inherited exact-hash completed independent original UR input audit; this stage verifies all original rows and bindings.'},
        'elapsedSeconds':time.perf_counter()-started})


def verify_accounts(directory):
    started=time.perf_counter()
    require(not (directory/'validation.json').exists(),'Account proof already exists')
    plan=registered_plan(directory);source=inherited_sources(plan)
    completion,receipts=input_receipts(directory,plan)
    inputs=read(directory/'input-validation.json')
    require(inputs['passed'] is True and inputs['planSha256']==REGISTERED_PLAN_SHA and inputs['verifierSha256']==sha(__file__)
            and inputs['inputCompletionSha256']==sha(directory/'input-completion.json') and inputs['datasetPins']==source['datasetPins']
            and {k:inputs[k] for k in parent_proof_fields(plan)}==parent_proof_fields(plan),'Independent input audit stale')
    run_start=read(directory/'run-start.json')
    require(pinned(run_start['inputCompletion'])==(directory/'input-completion.json').resolve()
            and pinned(run_start['inputValidation'])==(directory/'input-validation.json').resolve(), 'Run input/audit links')
    require(datetime.fromisoformat(completion['completedAt'])<=datetime.fromisoformat(inputs['verifiedAt'])
            <=datetime.fromisoformat(run_start['startedAt']),'Input audit not complete before new accounts')
    tests=read(pinned(run_start['preAccountTests']))
    require(tests['passed'] is True and tests['planSha256']==REGISTERED_PLAN_SHA
            and tests['allChecksBeforeFirstNewAccount'] is True,'Pre-account test receipt missing/stale')
    require(datetime.fromisoformat(plan['registeredAt'])<=datetime.fromisoformat(tests['startedAt'])
            <=datetime.fromisoformat(tests['completedAt'])<=datetime.fromisoformat(run_start['startedAt']),
            'Actual preregistered test timestamps do not precede the first new account')
    spec=plan['preAccountTestSpec']
    require(tests['schema']=='reclaim-volatility-trail-pre-account-tests-v1'
            and tests['productionUnitIncrement']==spec['newUnitTests']>0
            and tests['unitTests']==spec['inheritedUnitTests']+spec['newUnitTests']
            and len(tests['commands'])==spec['commandCount']
            and all(r['exitCode']==0 for r in tests['commands']),'Pre-account test accounting/commands differ')
    for path,value in tests['pins'].items():require(sha(local(path))==value,'Pre-account tested source changed')
    comparison=read(directory/'comparison.json');previous=read(pinned(plan['baselineComparison']))
    require(comparison['schema']=='reclaim-volatility-trail-comparison-v1' and comparison['planSha256']==REGISTERED_PLAN_SHA,'Comparison identity')
    require(comparison['noSignalsGenerated'] is True,'Comparison claims new signal computation')
    for key in (*plan['rules']['evidence']['trialAccounting'],'limitations','certified','targetAchieved'):
        require(comparison[key]==plan[key],'Comparison scope differs: '+key)
    require([(r['period'],r['board'],r['id'],r['policy']) for r in comparison['rows']]
            ==[(p,b,m,m) for p in PERIODS for b in BOARDS for m in REPORT_MODES],'Full ordered 12-account comparison')
    board=reference(BOARD_REFERENCE,BOARD_REFERENCE_SHA)
    accounting=reference(board.ACCOUNT_REFERENCE,board.ACCOUNT_REFERENCE_SHA)
    dependencies={EXIT_REFERENCE:EXIT_REFERENCE_SHA,BOARD_REFERENCE:BOARD_REFERENCE_SHA,board.ACCOUNT_REFERENCE:board.ACCOUNT_REFERENCE_SHA,
        accounting.COST_REFERENCE:accounting.COST_REFERENCE_SHA,**accounting.cost_reference().REFERENCES}
    for path,value in dependencies.items():require(sha(local(path))==value,'Independent accounting dependency changed')
    reports,reused={},{}
    require(list(comparison['artifacts'])==list(PERIODS),'Artifact period matrix')
    for period in PERIODS:
        reports[period],reused[period]={},{}
        require(list(comparison['artifacts'][period])==list(BOARDS),'Artifact board matrix')
        for b in BOARDS:
            folder,receipt=directory/period/b,receipts[period][b]
            require(inputs['inputArtifacts'][period][b]==receipt['files'],'Input files changed after audit')
            files=comparison['artifacts'][period][b]
            require(list(files)==list(REPORT_MODES) and files['trail10']==plan['baselineArtifacts'][period][b], 'Account mode/baseline closure')
            schedule=read(pinned(receipt['baselineSchedule']))
            require(len(schedule)==inputs['periods'][period][b]['sourceImmediateOpportunities'],'Original source count drift')
            base=deepcopy(next(r for r in previous['rows'] if (r['period'],r['board'],r['id'])==(period,b,'trail10')))
            base.update(label=LABELS['trail10'])
            actual=next(r for r in comparison['rows'] if (r['period'],r['board'],r['id'])==(period,b,'trail10'))
            require(actual==base,'Baseline comparison changed beyond display labels')
            summary=read(pinned(files['trail10']['trail10.summary.json']))
            require(summary['policyId']=='trail10','Original baseline summary identity changed')
            expected=board.comparison_reference(summary,period,b,LABELS['trail10'],len(schedule));expected.update(id='trail10',policy='trail10')
            same(actual,expected,'Original baseline metrics')
            reused[period][b]={'reused':True,'sourcePolicy':'trail10','sourceStrategyId':SID,
                'sourceImmediateOpportunities':len(schedule),'parentValidationSha256':PARENT_VALIDATION_SHA}
            data=read(pinned(receipt['accountData']))
            require(digest(data)==receipt['accountDatasetDigest'],'Account source content changed')
            reports[period][b]={}
            for mode in MODES:
                require(set(files[mode])=={mode+s for s in ('.result.json.gz','.checkpoint.json.gz','.summary.json')},'Saved account file closure')
                for name,ref in files[mode].items():require(pinned(ref)==(folder/name).resolve(),'Saved account path')
                result,checkpoint,summary=(read(folder/(mode+suffix)) for suffix in ('.result.json.gz','.checkpoint.json.gz','.summary.json'))
                checked=audit_account(result,checkpoint,summary,schedule,plan['periods'][period]['policyConfigs'][mode],data,period,mode,board,accounting)
                require(summary['policyId']==mode and summary['policyLabel']==LABELS[mode],'Candidate summary identity')
                expected=board.comparison_reference(summary,period,b,LABELS[mode],len(schedule));expected.update(id=mode,policy=mode)
                actual=next(r for r in comparison['rows'] if (r['period'],r['board'],r['id'])==(period,b,mode))
                same(actual,expected,'Full candidate comparison metrics')
                reports[period][b][mode]=checked
                print(json.dumps({'phase':'independent-volatility-trail-account','period':period,'board':b,'mode':mode,
                    'fills':checked['fills'],'heldDays':checked['dailyRisk']['heldDays'],'passed':True}),flush=True)
                del result,checkpoint,summary;gc.collect()
            del data;gc.collect()
    registered_plan(directory)
    reports_flat=[r for p in reports.values() for b in p.values() for r in b.values()]
    proof={'schema':'reclaim-volatility-trail-independent-validation-v1','passed':True,
        'verifiedAt':datetime.now(timezone.utc).isoformat(),'verifierSha256':sha(__file__),'planSha256':REGISTERED_PLAN_SHA,
        'comparisonSha256':sha(directory/'comparison.json'),'inputValidationSha256':sha(directory/'input-validation.json'),
        'inputCompletionSha256':sha(directory/'input-completion.json'),'preAccountTests':run_start['preAccountTests'],
        **parent_proof_fields(plan),'datasetPins':source['datasetPins'],'inputArtifacts':inputs['inputArtifacts'],
        'accountArtifacts':comparison['artifacts'],'independentReferences':dependencies,'periods':reports,'reusedAccounts':reused,
        'counts':{'accounts':12,'newAccounts':6,'reusedAccounts':6,'sourceImmediateOpportunities':inputs['counts']['sourceImmediateOpportunities'],
            'newAccountFills':sum(r['fills'] for r in reports_flat),'independentHeldDayRiskChecks':sum(r['dailyRisk']['heldDays'] for r in reports_flat),
            'volatilityObservations':sum(r['dailyRisk']['volatilityObservations'] for r in reports_flat)},
        'checks':['Six original baseline account artifacts and unchanged full UR immediate schedules',
            'Six candidate ledgers, dated fees, cash/shares, reserve budgets, board lots/limits, causal fill prices and T+1',
            'Every held close reconstructed from fills/source: exact21 prior slots, references, TR ratios, peak/floor, unknown windows and carried threshold, standard priority',
            'Should-trigger and should-not-trigger days; first pending evidence survives rebound/partial sales; next-opening fills retain that evidence',
            'Closed/open episodes, continuous curves, checkpoints, final positions and all12 comparison rows'],
        'scope':{'accountsExecutedByVerifier':0,'productionRiskFunctionsCalled':False,'newLedgerAudits':6,'inheritedBaselineAudits':6,
            'limitations':['No separate account resimulation or certification of unobserved opening liquidity.',
                'Original raw-price, historical-status, current-survivor and rights limitations remain.']},
        'certified':False,'targetAchieved':False,'elapsedSeconds':time.perf_counter()-started}
    write_proof(directory,'validation.json',proof)


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=DEFAULT)
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--inputs','--inputs-only',dest='inputs',action='store_true')
    args=parser.parse_args()
    if args.self_test:self_test();return
    directory=args.directory.resolve();stage='inputs' if args.inputs else 'accounts'
    try:(verify_inputs if args.inputs else verify_accounts)(directory)
    except Exception as error:
        folder=directory/'validation-attempts';folder.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path=folder/(stamp+'-'+stage+'-failed.json')
        failure={'passed':False,'stage':stage,'at':stamp,'verifierSha256':sha(__file__),
            'planSha256':sha(directory/'plan.json') if (directory/'plan.json').exists() else None,
            'error':str(error),'traceback':traceback.format_exc(),'accountsExecuted':0}
        path.write_text(json.dumps(failure,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        print(json.dumps({'passed':False,'failure':str(path),'error':str(error)}),flush=True)
        raise


if __name__=='__main__':main()


