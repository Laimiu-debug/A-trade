"""Independent unchanged-signal and every-held-day staged-exit audit.

The production staged adapter and runner are never imported. Source identity and all
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
DEFAULT = ROOT / 'artifacts/strategy-search/reclaim-breakout-volatility-trail-v1'
SOURCE_PARENT = ROOT / 'artifacts/strategy-search/board-coverage-reclaim-v1'
PARENT = PREVIOUS = ROOT / 'artifacts/strategy-search/reclaim-volatility-trail-v1'
EXIT_REFERENCE = 'scripts/verify-reclaim-volatility-trail.py'
EXIT_REFERENCE_SHA = '81bd4a8681f31d9a1dc5c5732ed8cb7d763e3c779b58492098bbc3957fd0bb08'
BOARD_REFERENCE = 'scripts/verify-board-coverage-reclaim.py'
BOARD_REFERENCE_SHA = '8d95ec533c901c7e47b6c23beb6567ae6760900d7be9e6038fff0d58853b6c29'
PARENT_PLAN_SHA = '4edd15d2b074ff81992c61e7bfac652e6e77780bbd4f930fb2e7332014fbe1d5'
PARENT_SOURCE_VALIDATION_SHA = 'fb666f86c8f78cd8c1b569bb8a44030e567b56c22db4d91b4880afb91152c649'
PARENT_INPUT_VALIDATION_SHA = '7999c9bca0793698c1598534c35b10504b6469fab9595f10983131e99ec1df75'
PARENT_VALIDATION_SHA = 'a4161122f3ea8c2b8efb04e2dfe6a67cd2ee5c6d8a6cafcdb5e20f560df5fabf'
PARENT_DELIVERY_SHA = '7568310306dfc2bb88b801311ea20e7840d15072d4b973f21e054ba2bf6c2ad9'
REGISTERED_PLAN_SHA = None
PROTOCOL_PATH = 'docs/reclaim-breakout-volatility-trail-v1.json'
PROTOCOL_SHA = '344cb0eb8ea57125c8466f01608fc8a8eff4d8a6992ba2d64975b3461e129dcc'
LATEST = ROOT / 'artifacts/strategy-search/reclaim-exit-mechanism-v1'
LATEST_DELIVERY_SHA = 'a1ad45563984e79997178328a17d942d13be597a490a498fe224e8c79e8ed92a'
SID = BASELINE_SID = 'strongUndercutReclaimV1'
MODES = ('breakoutVolatilityTrail',)
REFERENCE_MODES = ('trail10','volatilityTrail')
REPORT_MODES = (*REFERENCE_MODES, *MODES)
BOARDS = ('main', 'chinext', 'star')
PERIODS = ('2022-2023', '2024-2025')
LABELS = {'trail10':'固定百分比跟踪','volatilityTrail':'全程波动跟踪','breakoutVolatilityTrail':'突破后波动跟踪'}
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


PHASES = ('fixed','volatility')
ACTIVATION_STATUSES = ('activated','notTriggered','unknown','alreadyActive','skipped')


def reference_change(day, previous_day, previous, references):
    """Describe an inherited reference change without applying a price repair."""
    explicit = day in references
    value = references.get(day)
    issue = ('invalidPreviousCalendarClose' if not positive(previous) else
             'invalidExplicitPreclose' if explicit and not positive(value) else None)
    tolerance = max(.005,previous*1e-7) if positive(previous) else None
    changed = scale = None
    if issue is None:
        changed = bool(explicit and abs(value-previous)>tolerance)
        scale = value/previous if changed else 1.
        if not positive(scale):
            issue,scale,changed = 'nonFiniteReferenceScale',None,None
    return {'date':day,'previousDate':previous_day,'previousClose':previous if positive(previous) else None,
        'referenceSource':'precloseByDate' if explicit else 'noExplicitPreclose',
        'referenceValue':value if explicit and positive(value) else None,'changeThreshold':tolerance,
        'referenceChanged':changed,'scaleFactor':scale,'status':'unknown' if issue else 'known','reason':issue}


def anchor_reference(formation_date, entry_date, calendar, bars, references):
    """Independent raw maximum and conversion path, bounded by actual entry."""
    calendar_check(calendar)
    require(isinstance(bars,dict) and isinstance(references,dict),'Anchor maps required')
    indexes={d:n for n,d in enumerate(calendar)}
    require(formation_date in indexes and entry_date in indexes and entry_date>=formation_date,
            'Anchor dates outside source calendar or reversed')
    first,last=indexes[formation_date],indexes[entry_date]
    dates=calendar[max(0,first-20):first]
    issues=[] if len(dates)==20 else [{'date':formation_date,'reason':'insufficientPriorCalendarSlots'}]
    closes=[]
    for day in dates:
        bar=bars.get(day)
        issue=prior_bar_issue(bar,day)
        closes.append({'date':day,'close':bar.get('close') if isinstance(bar,dict) and positive(bar.get('close')) else None,
            'barIssue':issue})
        if issue:issues.append({'date':day,'reason':issue})
    checks=[]
    for n in range(1,len(dates)):
        previous=bars.get(dates[n-1])
        check=reference_change(dates[n],dates[n-1],previous.get('close') if isinstance(previous,dict) else None,references)
        checks.append(check)
        if check['reason'] or check['referenceChanged']:
            issues.append({'date':dates[n],'reason':check['reason'] or 'internalReferenceChange'})
    raw=max(row['close'] for row in closes) if len(dates)==20 and all(row['barIssue'] is None for row in closes) else None
    current=raw if not issues else None
    transports=[]
    for n in range(first,last+1):
        day=calendar[n]
        prevday=calendar[n-1] if n else None
        prior=bars.get(prevday)
        check=reference_change(day,prevday,prior.get('close') if isinstance(prior,dict) else None,references)
        issue=(prior_bar_issue(bars.get(day),day) or
            (prior_bar_issue(prior,prevday) if prevday else 'missingPreviousCalendarSlot') or check['reason'])
        before=current
        if issue:
            issues.append({'date':day,'reason':'conversion:'+issue})
            current=None
        elif current is not None:
            current=current*check['scaleFactor']
            if not positive(current):
                current=None
                issue='nonFiniteTransportedAnchor'
                issues.append({'date':day,'reason':issue})
        transports.append({**check,'status':'unknown' if issue else 'known','reason':issue,
            'anchorPriceBefore':before,'anchorPriceAfter':current})
    return {'formationDate':formation_date,'entryDate':entry_date,'asOfDate':dates[-1] if dates else None,
        'priorDates':dates,'priorCloses':closes,'rawPrior20MaxClose':raw,'internalReferenceChecks':checks,
        'conversionDates':calendar[first:last+1],'conversionSteps':transports,
        'entryAnchorPrice':current if not issues else None,'status':'unknown' if issues else 'known','issues':issues}


def bind_anchor_reference(binding, calendar, bars, references):
    """Return the immutable close-time entry binding without consuming outcomes."""
    result=deepcopy(binding)
    if result['anchor'] is None:
        anchor=anchor_reference(result['signalDate'],result['entryDate'],calendar,bars,references)
        ratio=anchor['entryAnchorPrice']/result['entryFillPrice'] if anchor['status']=='known' else None
        if ratio is not None and not positive(ratio):
            anchor['issues'].append({'date':result['entryDate'],'reason':'nonFiniteAnchorRatio'})
            anchor.update(status='unknown',entryAnchorPrice=None)
            ratio=None
        result.update(anchor=anchor,anchorRatio=ratio)
    return result


def fixed_exit_reference(day, holding_days, position, close):
    return {'observedDate':day,'observedClose':close,'riskEntryPrice':position['riskEntryPrice'],
        'closePeak':position['closePeak'],'gainPct':close/position['riskEntryPrice']-1,
        'drawdownFromPeakPct':close/position['closePeak']-1,'holdingDays':holding_days,
        'stopLossPct':.05,'takeProfitPct':0,'trailingStopPct':.1,'maxHoldingDays':0,'percentageUnit':'ratio'}


def trail_step_reference(day, holding_days, position, binding, bar, window, order, existing):
    """Independent staged decision: returns evidence and does not mutate inputs."""
    valid=isinstance(bar,dict) and bar.get('date')==day and positive(bar.get('close')) and positive(bar.get('volume'))
    phase='volatility' if binding['activationDate'] is not None else 'fixed'
    prior=binding['ratchetRatio']; ratio=prior
    anchor=binding['anchorRatio']*position['riskEntryPrice'] if binding['anchorRatio'] is not None else None
    require(anchor is None or positive(anchor),'Converted anchor must remain positive finite')
    activation_date=binding['activationDate']
    activation='alreadyActive' if phase=='volatility' else 'skipped'
    fixed=volprice=volratio=None
    activated=evaluated=False
    queued_reason=None
    if order is not None:
        status='skippedExistingExit' if existing else 'skippedStandardExit'
        why='earlierForcedExitRetained' if existing else 'standardRiskHasPriority'
        activation='skipped'
    elif not valid:
        status,why='unknown','missingOrInvalidCurrentCloseOrVolume'
        activation='unknown' if phase=='fixed' else 'alreadyActive'
    elif phase=='fixed':
        fixed=position['closePeak']*(1-.1)/position['riskEntryPrice']
        require(positive(fixed),'Fixed protective ratio must be finite positive')
        ratio=max(prior,fixed) if prior is not None else fixed
        # This expression intentionally retains the inherited floating boundary.
        fixed_trigger=bar['close']/position['closePeak']-1<=-.1
        if fixed_trigger:
            status,why,queued_reason='triggered','fixedTrail10','trailingStop'
        else:
            status='notTriggered'
            if anchor is None:
                activation,why='unknown','anchorUnknownFixedTrailRetained'
            elif bar['close']>anchor:
                activated=True
                activation_date=day
                activation,why='activated','breakoutActivatedFixedFloorCarried'
            else:
                activation,why='notTriggered','fixedTrailBeforeBreakout'
    else:
        require(prior is not None,'Active episode requires the carried fixed seed')
        if window['windowStatus']=='known':
            volprice=position['closePeak']*(1-window['scaledDistancePct'])
            volratio=volprice/position['riskEntryPrice']
            require(positive(volprice) and positive(volratio),'Finite positive volatility candidate')
            ratio=max(prior,volratio)
            evaluated=True
        status='triggered' if bar['close']<=ratio*position['riskEntryPrice'] else 'notTriggered'
        why='knownVolatilityWindow' if evaluated else 'carriedThresholdDespiteUnavailableWindow'
        if status=='triggered':queued_reason='breakoutVolatilityTrail'
    return {'date':day,'symbol':binding['symbol'],'signalId':binding['signalId'],'signalDate':binding['signalDate'],
        'entryDate':position['entryDate'],'holdingDays':holding_days,'quantity':position['quantity'],
        'observedClose':bar['close'] if bar else None,'observedVolume':bar['volume'] if bar else None,
        'currentPriceValid':valid,'riskEntryPrice':position['riskEntryPrice'],'closePeak':position['closePeak'],
        'phaseBefore':phase,'phaseAfter':'volatility' if activation_date is not None else 'fixed',
        'activationStatus':activation,'activatedToday':activated,'activationDate':activation_date,
        'anchorStatus':binding['anchor']['status'],'anchorRatio':binding['anchorRatio'],'anchorPrice':anchor,
        'window':deepcopy(window),'volatilityCandidateEvaluated':evaluated,'previousRatio':prior,
        'fixedCandidateRatio':fixed,'volatilityCandidatePrice':volprice,'volatilityCandidateRatio':volratio,
        'ratchetRatio':ratio,'threshold':ratio*position['riskEntryPrice'] if ratio is not None else None,
        'thresholdKnown':ratio is not None,
        'usedCarriedThreshold':phase=='volatility' and valid and order is None and not evaluated,
        'decisionStatus':status,'decisionReason':why,'exitReason':order['reason'] if order else queued_reason,
        'exitSignalDate':order['signalDate'] if order else day if queued_reason else None}


def detail_reference(entries, observations):
    return {'version':'research-breakout-volatility-trail-v1',
        'parameters':{'formationSlots':20,'windowSlots':21,'trueRangeCount':20,'multiplier':3,
            'fixedTrailingPct':.1,'windowEnd':'heldCloseT-1'},'checkpointRestoreSupported':False,
        'entryBindings':deepcopy(entries),'observations':deepcopy(observations),'entryCount':len(entries),
        'activatedEntryCount':sum(r['activatedToday'] for r in observations),
        'unknownAnchorEntryCount':sum(r['anchor']['status']=='unknown' for r in entries),
        'phaseObservationCounts':{s:sum(r['phaseBefore']==s for r in observations) for s in PHASES},
        'activationObservationCounts':{s:sum(r['activationStatus']==s for r in observations) for s in ACTIVATION_STATUSES},
        'observationCounts':{s:sum(r['decisionStatus']==s for r in observations) for s in DECISION_STATUSES},
        'windowCounts':{s:sum(r['window']['windowStatus']==s for r in observations) for s in WINDOW_STATUSES},
        'priority':'existingForcedExit; inheritedEligibilityDelistSTAndStop5; fixedTrail10ThenActivationOrActiveVolatilityTrail',
        'activationTiming':'firstHeldCloseStrictlyAboveFrozenConvertedAnchorAfterFixedRisk; volatilityCandidatesStartNextHeldClose',
        'windowMeaning':'Prior windows are logged on every held close; used only when phaseBefore=volatility, no existing/standard exit, and valid current close/volume.',
        'referencePolicy':'Anchor and floor ratios remain fixed in inherited riskEntryPrice units; no historical rights reconstruction.',
        'certified':False,'targetAchieved':False}

def synthetic_fixture():
    """Only invented prices and calendar days; no data or production imports."""
    days=[(date(2020,1,1)+timedelta(days=i)).isoformat() for i in range(28)]
    bars={d:{'date':d,'open':100.,'high':101.,'low':99.,'close':100.,'volume':10000.} for d in days}
    return days,bars


def synthetic_risk_fixture():
    """Hand-specified first stop, partial retry, rebound and separate reentry."""
    days,bars=synthetic_fixture(); symbol='sh600001'
    bars[days[21]].update(open=100.,high=100.,low=93.,close=94.)
    bars[days[22]].update(open=95.,high=101.,low=94.,close=100.)
    bars[days[23]].update(open=100.,high=111.,low=99.,close=110.)
    bars[days[24]].update(open=110.,high=111.,low=99.,close=100.)
    config={'startDate':days[21],'endDate':days[25],'rebalanceMode':'risk','universe':{'excludeSt':True},
        'stopLossPct':.05,'trailingStopPct':0.,'takeProfitPct':0.,'maxHoldingDays':0}
    risk={'observedDate':days[21],'observedClose':94.,'riskEntryPrice':100.,'closePeak':100.,
        'gainPct':94./100.-1,'drawdownFromPeakPct':94./100.-1,'holdingDays':1,
        'stopLossPct':.05,'takeProfitPct':0.,'trailingStopPct':0.,'maxHoldingDays':0,'percentageUnit':'ratio'}
    order={'signalDate':days[21],'reason':'stopLoss','riskEvidence':risk}
    buys=[{'date':days[n],'symbol':symbol,'side':'buy','quantity':100,'price':100.,'signalDate':days[n-1],
        'signalId':SID+':'+days[n-1]+':'+symbol,'fillId':'buy'+str(n),'positionAction':'open'} for n in (21,25)]
    sells=[{'date':days[n],'symbol':symbol,'side':'sell','quantity':q,'price':100.,'signalDate':days[21],
        'reason':'stopLoss','riskEvidence':deepcopy(risk)} for n,q in ((22,40),(24,60))]
    result={'config':config,'trades':[buys[0],sells[0],sells[1],buys[1]],
        'daily':[{'date':days[n],'holdings':[{'symbol':symbol,'quantity':q}] if q else []}
            for n,q in ((21,100),(22,60),(23,60),(24,0),(25,100))],
        'decisions':[{'date':days[21],'symbol':symbol,'action':'sell','status':'queued','reason':'stopLoss',
            'signalDate':days[21],'observedClose':94.,'riskEvidence':deepcopy(risk)}],
        'forcedExits':{},'positions':[{'symbol':symbol,'entryDate':days[25],'quantity':100,'riskEntryPrice':100.,'closePeak':100.}],
        'versions':{'researchBreakoutVolatilityTrail':'research-breakout-volatility-trail-v1'},'research':{}}
    bindings=[]
    for fill in buys:
        raw={k:fill[k] for k in ('signalId','signalDate','symbol')}
        raw.update(entryDate=fill['date'],entryFillId=fill['fillId'],entryQuantity=100,entryFillPrice=100.,
            anchor=None,anchorRatio=None,activationDate=None,ratchetRatio=None)
        bindings.append(bind_anchor_reference(raw,days,bars,{}))
    observations=[]
    for n,q,peak,bi in ((21,100,100.,0),(22,60,100.,0),(23,60,110.,0),(25,100,100.,1)):
        binding=bindings[bi]
        position={'entryDate':binding['entryDate'],'quantity':q,'riskEntryPrice':100.,'closePeak':peak}
        prior=days[n-21:n]
        row=trail_step_reference(days[n],n-days.index(binding['entryDate'])+1,position,binding,bars[days[n]],
            volatility_reference(days[n],prior,[bars[d] for d in prior],{}),order if bi==0 else None,n!=21)
        binding.update(ratchetRatio=row['ratchetRatio'],activationDate=row['activationDate'])
        observations.append(row)
    entries=[{k:deepcopy(v) for k,v in b.items() if k not in ('ratchetRatio','activationDate')} for b in bindings]
    detail=detail_reference(entries,observations)
    result['research']['breakoutVolatilityTrail']=detail
    checkpoint={'breakoutVolatilityTrail':deepcopy(detail),'currentPositionTrails':{symbol:bindings[1]}}
    return result,checkpoint,{'calendar':days,'securities':[{'symbol':symbol,'bars':list(bars.values())}]}


def self_test():
    checks=0
    def test(condition,message):
        nonlocal checks
        require(condition,message);checks+=1
    days,bars=synthetic_fixture(); f,e=days[20],days[21]
    before=deepcopy(bars)
    anchor=anchor_reference(f,e,days,bars,{})
    test(anchor['status']=='known' and anchor['rawPrior20MaxClose']==100. and anchor['entryAnchorPrice']==100.
         and anchor['priorDates']==days[:20] and anchor['conversionDates']==days[20:22],'Exact preformation maximum')
    altered=deepcopy(bars);altered[days[22]]['close']=1e200
    test(anchor_reference(f,e,days,altered,{})==anchor,'Future price must not change frozen anchor')
    test(anchor_reference(f,e,days,bars,{days[0]:None})==anchor,'First anchor slot needs no prior reference')
    mixed=anchor_reference(f,e,days,bars,{days[10]:50.})
    test(mixed['status']=='unknown' and mixed['rawPrior20MaxClose']==100. and mixed['entryAnchorPrice'] is None
         and mixed['issues'][0]['reason']=='internalReferenceChange','Mixed units rejected without repair')
    missing=deepcopy(bars);del missing[days[10]]
    test(anchor_reference(f,e,days,missing,{})['status']=='unknown','Prior exact missing slot stays unknown')
    missing=deepcopy(bars);del missing[f]
    test(anchor_reference(f,e,days,missing,{})['status']=='unknown','Conversion may not bridge missing slot')
    invalid=anchor_reference(f,e,days,bars,{f:0})
    test(invalid['status']=='unknown' and invalid['issues'][0]['reason']=='conversion:invalidExplicitPreclose','Explicit invalid reference cannot fallback')
    shifted=deepcopy(bars)
    for d in (f,e):shifted[d].update(open=50.,high=51.,low=49.,close=50.)
    transported=anchor_reference(f,e,days,shifted,{f:50.})
    test(transported['entryAnchorPrice']==50. and transported['conversionSteps'][0]['scaleFactor']==.5,'Formation-day unit transport')
    shifted[e].update(open=25.,high=26.,low=24.,close=25.)
    transported=anchor_reference(f,e,days,shifted,{f:50.,e:25.})
    test(transported['entryAnchorPrice']==25. and transported['conversionSteps'][-1]['scaleFactor']==.5,'Entry-day transport before position exists')
    small=anchor_reference(f,e,days,bars,{f:100.004})
    test(small['entryAnchorPrice']==100. and not small['conversionSteps'][0]['referenceChanged'],'Inherited conversion tolerance retained')
    test(anchor_reference(days[2],days[3],days,bars,{})['status']=='unknown','Insufficient prior history not extended')
    binding={'symbol':'sh600001','signalId':'s','signalDate':f,'entryDate':e,'entryFillId':'buy',
        'entryQuantity':100,'entryFillPrice':100.,'anchor':anchor,'anchorRatio':1.,'activationDate':None,'ratchetRatio':None}
    position={'entryDate':e,'quantity':100,'riskEntryPrice':100.,'closePeak':100.}
    window=volatility_reference(e,days[:21],[bars[d] for d in days[:21]],{})
    equal=trail_step_reference(e,1,position,binding,bars[e],window,None,False)
    test(not equal['activatedToday'] and equal['ratchetRatio']==.9,'Anchor equality must not activate')
    high={**bars[e],'high':111.,'close':110.};peak={**position,'closePeak':110.}
    activation=trail_step_reference(e,1,peak,binding,high,window,None,False)
    test(activation['activatedToday'] and activation['phaseAfter']=='volatility' and activation['ratchetRatio']==.99
         and not activation['volatilityCandidateEvaluated'] and activation['volatilityCandidateRatio'] is None,
         'Entry close activation only seeds fixed floor')
    active={**binding,'activationDate':e,'ratchetRatio':.99}
    laterday=days[22];current={**high,'date':laterday}
    active_row=trail_step_reference(laterday,2,peak,active,current,window,None,False)
    test(active_row['volatilityCandidateEvaluated'] and active_row['ratchetRatio']>.99
         and active_row['fixedCandidateRatio'] is None,'Next held close uses vol without fixed update')
    wide={**window,'scaledDistancePct':.4}
    wide_row=trail_step_reference(laterday,2,{**peak,'closePeak':120.},active,{**current,'close':120.},wide,None,False)
    test(wide_row['ratchetRatio']==.99 and wide_row['threshold']==99.,'Volatility increase cannot lower carried seed')
    unknown={**window,'windowStatus':'unknown','scaledDistancePct':None}
    equal_floor=trail_step_reference(laterday,2,peak,active,{**current,'close':99.},unknown,None,False)
    test(equal_floor['decisionStatus']=='triggered' and equal_floor['usedCarriedThreshold']
         and equal_floor['exitReason']=='breakoutVolatilityTrail','Unknown vol carries threshold including equality')
    invalid_current=trail_step_reference(laterday,2,peak,active,{**current,'close':1.,'volume':0.},window,None,False)
    test(invalid_current['decisionStatus']=='unknown' and invalid_current['ratchetRatio']==.99
         and not invalid_current['volatilityCandidateEvaluated'],'Invalid current bar freezes active state')
    legacy=trail_step_reference(laterday,2,{**position,'riskEntryPrice':80.},{**binding,'anchorRatio':2.},
        {**current,'close':90.},window,None,False)
    test(90.<=.9*100. and 90./100.-1>-.1 and legacy['decisionStatus']=='notTriggered','Legacy fixed floating boundary retained')
    fixed=trail_step_reference(laterday,2,{**position,'riskEntryPrice':80.},binding,{**current,'close':89.},window,None,False)
    test(fixed['decisionStatus']=='triggered' and fixed['exitReason']=='trailingStop' and not fixed['activatedToday'],
         'Fixed exit wins even when close also exceeds converted anchor')
    for existing in (False,True):
        skipped=trail_step_reference(e,1,peak,binding,high,window,{'reason':'stopLoss','signalDate':f},existing)
        test(skipped['decisionStatus']==('skippedExistingExit' if existing else 'skippedStandardExit')
             and not skipped['activatedToday'] and skipped['ratchetRatio'] is None,'First/standard exit blocks activation')
    unknown_binding={**binding,'anchor':mixed,'anchorRatio':None}
    unknown_row=trail_step_reference(e,1,peak,unknown_binding,high,window,None,False)
    test(unknown_row['phaseAfter']=='fixed' and unknown_row['activationStatus']=='unknown'
         and unknown_row['ratchetRatio']==.99,'Unknown anchor retains fixed policy')
    converted=trail_step_reference(laterday,2,{**peak,'riskEntryPrice':50.,'closePeak':55.},active,
        {**current,'close':55.},wide,None,False)
    test(converted['anchorPrice']==50. and converted['threshold']==49.5 and converted['ratchetRatio']==.99,
         'Held reference conversion scales anchor/floor but not ratios')
    test(bars==before and binding['activationDate'] is None and binding['ratchetRatio'] is None,'Reference helpers do not mutate inputs')
    result,checkpoint,data=synthetic_risk_fixture()
    audit=daily_risk_reference(result,checkpoint,data,'breakoutVolatilityTrail')
    test(audit['actualEntries']==2 and audit['heldDays']==4 and audit['queuedExits']==1
         and audit['terminalRatchetStates']==1 and audit['activatedEntries']==0,
         'Full held-day audit: stop, partial retry, rebound, full close, reentry reset')
    damaged=deepcopy(result);damaged['decisions']=[]
    try:daily_risk_reference(damaged,checkpoint,data,'breakoutVolatilityTrail')
    except ValueError:test(True,'Missing should-trigger decision rejected')
    else:raise AssertionError('Deleted stop was not detected')
    damaged=deepcopy(checkpoint);damaged['currentPositionTrails']['sh600001']['activationDate']=days[21]
    try:daily_risk_reference(result,damaged,data,'breakoutVolatilityTrail')
    except ValueError:test(True,'Old episode state leaking into reentry rejected')
    else:raise AssertionError('Stale episode state was not detected')
    print(json.dumps({'passed':True,'selfTests':checks,'realSourceRead':False,'accountsExecuted':0}),flush=True)


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
                trail_positions[symbol]={**binding,'anchor':None,'anchorRatio':None,'activationDate':None,'ratchetRatio':None}
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
            if binding['anchor'] is None:
                binding=bind_anchor_reference(binding,calendar,bars[symbol],securities[symbol].get('precloseByDate',{}))
                trail_positions[symbol]=binding
                entry_bindings.append(deepcopy({k:v for k,v in binding.items() if k not in ('activationDate','ratchetRatio')}))
            prior=calendar[max(0,index[day]-21):index[day]]
            window=volatility_reference(day,prior,[bars[symbol].get(d) for d in prior],securities[symbol].get('precloseByDate',{}))
            evidence=trail_step_reference(day,index[day]-index[position['entryDate']]+1,position,binding,
                bars[symbol].get(day),window,pending.get(symbol),symbol in before)
            binding['ratchetRatio']=evidence['ratchetRatio']
            binding['activationDate']=evidence['activationDate']
            observations.append(evidence)
            if evidence['decisionStatus']=='triggered':
                exit_reason=evidence['exitReason']
                require(exit_reason in ('trailingStop','breakoutVolatilityTrail'),'Unregistered staged exit')
                risk_evidence=(fixed_exit_reference(day,index[day]-index[position['entryDate']]+1,position,
                    bars[symbol][day]['close']) if exit_reason=='trailingStop' else evidence)
                enqueue(symbol,day,exit_reason,risk_evidence)
        for symbol in sorted(positions):
            trace.append({'date':day,'symbol':symbol,**deepcopy(positions[symbol]),
                'ratchetRatio':trail_positions[symbol]['ratchetRatio'],
                'anchorRatio':trail_positions[symbol]['anchorRatio'],'activationDate':trail_positions[symbol]['activationDate'],
                'priorExit':deepcopy(before.get(symbol)),'resultingExit':deepcopy(pending.get(symbol))})
    same([r for r in result['decisions'] if r['action']=='sell' and r['status']=='queued'],queued,'All should/should-not-trigger exits')
    same(result['forcedExits'],pending,'Terminal immutable first exits')
    require(not any(r.get('reason') in ('noProgress5','structure10','volatilityTrail') for r in result['decisions']+result['trades']),
            'An unregistered day5/structure/unstaged-volatility exit survived')
    require('structuralExits' not in result['research'] and 'structuralExits' not in checkpoint
            and 'researchStructuralExits' not in result['versions'],'Unregistered structural adapter')
    require(result['versions']['researchBreakoutVolatilityTrail']=='research-breakout-volatility-trail-v1','Staged adapter version')
    require('researchVolatilityTrail' not in result['versions'] and 'volatilityTrail' not in result['research'],
            'Unstaged volatility adapter must not participate')
    detail=detail_reference(entry_bindings,observations)
    same(result['research']['breakoutVolatilityTrail'],detail,'Every held-day anchor, activation, window and ratchet decision')
    same(checkpoint['breakoutVolatilityTrail'],detail,'Checkpoint full staged evidence')
    same(checkpoint['currentPositionTrails'],trail_positions,'Terminal episode ratchet state')
    require({p['symbol'] for p in result['positions']}==set(positions),'Terminal risk population')
    for observed in result['positions']:
        for key,value in positions[observed['symbol']].items():same(observed[key],value,'Terminal risk '+key)
    return {'heldDays':len(trace),'actualEntries':entry_count,'queuedExits':len(queued),
        'exitReasons':dict(Counter(r['reason'] for r in queued)),'riskReferenceConversions':len(conversions),
        'volatilityObservations':len(observations),'observationCounts':detail['observationCounts'],
        'windowCounts':detail['windowCounts'],'usedCarriedThresholdCount':sum(r['usedCarriedThreshold'] for r in observations),
        'activatedEntries':detail['activatedEntryCount'],'unknownAnchorEntries':detail['unknownAnchorEntryCount'],
        'phaseObservationCounts':detail['phaseObservationCounts'],
        'activationObservationCounts':detail['activationObservationCounts'],
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
    require(mode == 'breakoutVolatilityTrail' and checkpoint['schema']=='research-breakout-volatility-trail-descriptive-snapshot-v1', 'Checkpoint adapter schema')
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
    require(plan['schema']=='reclaim-breakout-volatility-trail-v1' and plan['strategyId']==SID and plan['modes']==list(MODES)
            and plan['reportModes']==list(REPORT_MODES) and plan['boards']==list(BOARDS)
            and list(plan['periods'])==list(PERIODS) and plan['labels']==LABELS,'Registered identities differ')
    for key,value in plan['rules']['evidence']['trialAccounting'].items():
        require(type(plan[key]) is int and plan[key]==value,'Registered trial accounting differs: '+key)
    require(plan['newAccounts']==6 and plan['referenceAccountsReused']==12 and plan['parameterSearch'] is False
            and plan['profitHurdles'] is None and plan['holdoutClaim'] is False
            and plan['certified'] is False and plan['targetAchieved'] is False,'Unregistered scope or target claim')
    require(plan['policySpecs']==plan['rules']['policies'] and plan['noSignalsGenerated'] is True
            and plan['productionChanges'] is True,'Changed isolated-adapter/signal scope')
    require(plan['preAccountTestSpec']['inheritedUnitTests']==77 and plan['preAccountTestSpec']['commandCount']==6
            and type(plan['preAccountTestSpec']['newUnitTests']) is int and plan['preAccountTestSpec']['newUnitTests']>0,'Frozen test scope')
    require(plan['policySpecs']['breakoutVolatilityTrail']['lookback']==20
            and plan['policySpecs']['breakoutVolatilityTrail']['multiplier']==3,'Unregistered window or multiplier')
    for path,expected in {**plan['sourcePins'],**plan['inputPins']}.items():
        require(sha(local(path))==expected,'Registered artifact changed: '+path)
    require(pinned(plan['previousTurn']['evidence'])==(LATEST/'delivery.json').resolve()
            and plan['previousTurn']['evidence']['sha256']==LATEST_DELIVERY_SHA,'Latest completed diagnostic lineage')
    latest=read(LATEST/'delivery.json')
    for name in ('plan.json','readout.json','research-decision.json'):
        path=LATEST/name; rel=path.relative_to(ROOT).as_posix()
        require(plan['inputPins'].get(rel)==sha(path) and latest['files'].get(rel)==sha(path),
                'Registered diagnostic decision/readout not bound to completed delivery')
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
        policies['breakoutVolatilityTrail']['trailingStopPct']=0.
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
    require(plan['sourcePlan']==parent['sourcePlan'] and plan['sourceComparison']==parent['sourceComparison'],
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
            require(list(artifacts)==list(REFERENCE_MODES),'Both original reference policies required')
            for mode in REFERENCE_MODES:
                require(artifacts[mode]==comparison['artifacts'][period][board][mode]
                        and set(artifacts[mode])=={mode+s for s in ('.result.json.gz','.checkpoint.json.gz','.summary.json')},
                        'Exact previously completed reference artifacts required')
                for item in artifacts[mode].values():pinned(item)
    return source


def input_receipts(directory,plan):
    completion=read(directory/'input-completion.json')
    require(completion['planSha256']==REGISTERED_PLAN_SHA and completion['allSchedulesFrozenBeforeAccounts'] is True
            and completion['noSignalsGenerated'] is True and completion['newAccounts']==6 and completion['referenceAccountsReused']==12
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
            require(receipt['schema']=='reclaim-breakout-volatility-trail-input-v1' and receipt['planSha256']==REGISTERED_PLAN_SHA
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
    require(signals_total==8155,'All six original formation schedules must total8155')
    registered_plan(directory)
    write_proof(directory,'input-validation.json',{'schema':'reclaim-breakout-volatility-trail-input-independent-validation-v1','passed':True,
        'verifiedAt':datetime.now(timezone.utc).isoformat(),'verifierSha256':sha(__file__),'planSha256':REGISTERED_PLAN_SHA,
        'inputCompletionSha256':sha(directory/'input-completion.json'),**parent_proof_fields(plan),
        'datasetPins':source['datasetPins'],'inputArtifacts':artifacts,'periods':reports,
        'counts':{'sourceInputs':6,'originalSchedules':6,'newPolicySchedules':6,'sourceImmediateOpportunities':signals_total},
        'checks':['All six inherited raw-source/cache proofs and twelve reference account proofs bound',
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
    require(tests['schema']=='reclaim-breakout-volatility-trail-pre-account-tests-v1'
            and tests['productionUnitIncrement']==spec['newUnitTests']>0
            and tests['unitTests']==spec['inheritedUnitTests']+spec['newUnitTests']
            and len(tests['commands'])==spec['commandCount']
            and all(r['exitCode']==0 for r in tests['commands']),'Pre-account test accounting/commands differ')
    for path,value in tests['pins'].items():require(sha(local(path))==value,'Pre-account tested source changed')
    comparison=read(directory/'comparison.json');previous=read(pinned(plan['baselineComparison']))
    require(comparison['schema']=='reclaim-breakout-volatility-trail-comparison-v1' and comparison['planSha256']==REGISTERED_PLAN_SHA,'Comparison identity')
    require(comparison['noSignalsGenerated'] is True,'Comparison claims new signal computation')
    for key in (*plan['rules']['evidence']['trialAccounting'],'limitations','certified','targetAchieved'):
        require(comparison[key]==plan[key],'Comparison scope differs: '+key)
    require([(r['period'],r['board'],r['id'],r['policy']) for r in comparison['rows']]
            ==[(p,b,m,m) for p in PERIODS for b in BOARDS for m in REPORT_MODES],'Full ordered 18-account comparison')
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
            require(list(files)==list(REPORT_MODES) and {m:files[m] for m in REFERENCE_MODES}==plan['baselineArtifacts'][period][b], 'Account mode/reference closure')
            schedule=read(pinned(receipt['baselineSchedule']))
            require(len(schedule)==inputs['periods'][period][b]['sourceImmediateOpportunities'],'Original source count drift')
            reused[period][b]={}
            for reference_mode in REFERENCE_MODES:
                base=deepcopy(next(r for r in previous['rows'] if (r['period'],r['board'],r['id'])==(period,b,reference_mode)))
                base.update(label=LABELS[reference_mode])
                actual=next(r for r in comparison['rows'] if (r['period'],r['board'],r['id'])==(period,b,reference_mode))
                require(actual==base,'Reference comparison changed beyond display labels')
                summary=read(pinned(files[reference_mode][reference_mode+'.summary.json']))
                require(summary['policyId']==reference_mode,'Original reference summary identity changed')
                expected=board.comparison_reference(summary,period,b,LABELS[reference_mode],len(schedule));expected.update(id=reference_mode,policy=reference_mode)
                same(actual,expected,'Original reference metrics')
                reused[period][b][reference_mode]={'reused':True,'sourcePolicy':reference_mode,'sourceStrategyId':SID,
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
    proof={'schema':'reclaim-breakout-volatility-trail-independent-validation-v1','passed':True,
        'verifiedAt':datetime.now(timezone.utc).isoformat(),'verifierSha256':sha(__file__),'planSha256':REGISTERED_PLAN_SHA,
        'comparisonSha256':sha(directory/'comparison.json'),'inputValidationSha256':sha(directory/'input-validation.json'),
        'inputCompletionSha256':sha(directory/'input-completion.json'),'preAccountTests':run_start['preAccountTests'],
        **parent_proof_fields(plan),'datasetPins':source['datasetPins'],'inputArtifacts':inputs['inputArtifacts'],
        'accountArtifacts':comparison['artifacts'],'independentReferences':dependencies,'periods':reports,'reusedAccounts':reused,
        'counts':{'accounts':18,'newAccounts':6,'reusedAccounts':12,'sourceImmediateOpportunities':inputs['counts']['sourceImmediateOpportunities'],
            'newAccountFills':sum(r['fills'] for r in reports_flat),'independentHeldDayRiskChecks':sum(r['dailyRisk']['heldDays'] for r in reports_flat),
            'volatilityObservations':sum(r['dailyRisk']['volatilityObservations'] for r in reports_flat),
            'activatedEntries':sum(r['dailyRisk']['activatedEntries'] for r in reports_flat),
            'unknownAnchorEntries':sum(r['dailyRisk']['unknownAnchorEntries'] for r in reports_flat)},
        'checks':['Twelve original reference account artifacts and unchanged full UR immediate schedules',
            'Six candidate ledgers, dated fees, cash/shares, reserve budgets, board lots/limits, causal fill prices and T+1',
            'Every actual entry has an independent exact prior20 maximum, internal reference guard and formation-through-entry conversion path; missing anchor stays unknown',
            'Every held close reconstructed from fills/source: fixed legacy comparison, standard priority, strict activation, activation-day fixed seed and next-close-only volatility candidates',
            'Exact21 prior slots, normalized TR ratios, peak/floor, carried thresholds, invalid-current freeze and full-close/reentry state reset',
            'Should-trigger and should-not-trigger days; first pending evidence survives rebound/partial sales; next-opening fills retain that evidence',
            'Closed/open episodes, continuous curves, checkpoints, final positions and all18 comparison rows'],
        'scope':{'accountsExecutedByVerifier':0,'productionRiskFunctionsCalled':False,'newLedgerAudits':6,'inheritedBaselineAudits':12,
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


