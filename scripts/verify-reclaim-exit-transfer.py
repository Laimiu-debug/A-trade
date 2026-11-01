"""Independent unchanged-signal and full held-day exit-transfer audit.

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
DEFAULT = ROOT / 'artifacts/strategy-search/reclaim-exit-transfer-v1'
PARENT = PREVIOUS = ROOT / 'artifacts/strategy-search/board-coverage-reclaim-v1'
BOARD_REFERENCE = 'scripts/verify-board-coverage-reclaim.py'
BOARD_REFERENCE_SHA = '8d95ec533c901c7e47b6c23beb6567ae6760900d7be9e6038fff0d58853b6c29'
PARENT_PLAN_SHA = 'a911df3c26aa6e662ce8c62d6374e856427c311200b2dc99bcf409c2725ffefa'
PARENT_SOURCE_VALIDATION_SHA = 'fb666f86c8f78cd8c1b569bb8a44030e567b56c22db4d91b4880afb91152c649'
PARENT_INPUT_VALIDATION_SHA = '97a32dfdf95ce942047dfb15ba4d6f9322e7c933d023e2a2732d7c1c7d07374b'
PARENT_VALIDATION_SHA = 'df665fa487867cb77a152c6964da4783e21c867e563cfa65e43b03ecc9c5677a'
PARENT_DELIVERY_SHA = 'fd6a973f5fe06be38316a371632efa3b7be2d7d2acec00e4b02d54be7975afed'
REGISTERED_PLAN_SHA = None
PROTOCOL_SHA = '3feff80ea026ef02bfd66622150b9e42d7c8e1392c9eca101135345ba151f7bb'
SID = BASELINE_SID = 'strongUndercutReclaimV1'
MODES = ('trail10', 'structure10')
REPORT_MODES = ('baseline', *MODES)
BOARDS = ('main', 'chinext', 'star')
PERIODS = ('2022-2023', '2024-2025')
LABELS = {'baseline':'原第五日退出','trail10':'保留百分比跟踪','structure10':'前十日低点退出'}



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


def structure_reference(day, dates, current, bars):
    """Exact inherited observation semantics, independently written arithmetic."""
    unknown, lows = [], []
    if current is None or current.get('date') != day or not positive(current.get('close')) or not positive(current.get('volume')):
        unknown.append({'date': day, 'reason': 'missingOrInvalidCurrentCloseOrVolume'})
    if len(dates) != 10:
        unknown.append({'date': day, 'reason': 'insufficientPriorCalendarSessions'})
    for required_day, bar in zip(dates, bars):
        reason = ('missingDailyBar' if bar is None or bar.get('date') != required_day else
                  'nonPositiveOrInvalidVolume' if not positive(bar.get('volume')) else
                  'invalidLow' if not positive(bar.get('low')) else None)
        if reason:
            unknown.append({'date': required_day, 'reason': reason})
        else:
            lows.append({'date': required_day, 'low': bar['low']})
    if len(bars) != len(dates):
        unknown.append({'date': day, 'reason': 'missingWindowSlots'})
    threshold = None if unknown else min(r['low'] for r in lows)
    return {'status': 'unknown' if unknown else 'triggered' if current['close'] < threshold else 'notTriggered',
        'priorDates': list(dates), 'priorLows': lows, 'prior10Low': threshold,
        'comparison': 'closeStrictlyLessThanPrior10Low', 'unknown': unknown}


def daily_risk_reference(result, checkpoint, dataset, mode):
    """Replay every held close from actual fills, never from observed sell signals.

    This is a risk-decision audit, not a second account execution. Existing
    opening execution/cash/capacity checks are handled by independent helpers.
    """
    require(mode in MODES, 'Unexpected exit policy')
    config, calendar = result['config'], dataset['calendar']
    calendar_check(calendar)
    index = {d: n for n, d in enumerate(calendar)}
    target = [d for d in calendar if config['startDate'] <= d <= config['endDate']]
    require([d['date'] for d in result['daily']] == target, 'Every target day must be retained')
    securities = {s['symbol']: s for s in dataset['securities']}
    require(len(securities) == len(dataset['securities']), 'Duplicate securities')
    require(not any(s.get('corporateActions') for s in securities.values()), 'Declared rights outside frozen source scope')
    rows = {s: security['bars'] for s, security in securities.items()}
    bars = {s: {b['date']: b for b in values} for s, values in rows.items()}
    bar_index = {s: {b['date']: n for n, b in enumerate(values)} for s, values in rows.items()}
    require(all(len(bars[s]) == len(rows[s]) for s in rows), 'Duplicate source bar')
    memberships = dataset.get('membershipByDate', {})
    fills = defaultdict(list)
    for fill in result['trades']:
        require(fill['date'] in target, 'Trade outside target calendar')
        fills[fill['date']].append(fill)
    positions, pending, queued, structural, trace, conversions = {}, {}, [], [], [], []
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
        if mode == 'structure10':
            for symbol in sorted(positions):
                p, bar = positions[symbol], bars[symbol].get(day)
                evidence = {'date':day,'symbol':symbol,'rule':'structure10','entryDate':p['entryDate'],
                    'holdingDays':index[day]-index[p['entryDate']]+1,'observedClose':bar['close'] if bar else None,
                    'riskEntryPrice':p['riskEntryPrice'],'priceReferenceMeaning':'entryFillPriceIncludingSlippageExcludingFees'}
                if symbol in pending:
                    evidence.update(status='skippedExistingExit', existingReason=pending[symbol]['reason'],
                        existingSignalDate=pending[symbol]['signalDate'])
                else:
                    prior = calendar[max(0,index[day]-10):index[day]]
                    evidence.update(structure_reference(day,prior,bar,[bars[symbol].get(d) for d in prior]))
                structural.append(evidence)
                if evidence['status'] == 'triggered':
                    enqueue(symbol, day, 'structure10', evidence)
        for symbol in sorted(positions):
            trace.append({'date':day,'symbol':symbol,**deepcopy(positions[symbol]),
                'priorExit':deepcopy(before.get(symbol)), 'resultingExit':deepcopy(pending.get(symbol))})
    same([r for r in result['decisions'] if r['action']=='sell' and r['status']=='queued'], queued, 'All should/should-not-trigger exits')
    same(result['forcedExits'],pending,'Terminal pending first exits')
    require(not any(r.get('reason') == 'noProgress5' for r in result['decisions']+result['trades']), 'Day-five exit survived removal')
    if mode == 'structure10':
        detail = {'version':'research-structural-exits-v1','rule':'structure10','checkpointRestoreSupported':False,
            'standardRiskHasPriority':True,'signalTiming':'close;earliestSubsequentExecutableOpen',
            'holdingClock':'marketCalendarSessionsIncludingEntry;missingSessionsConsumeTime',
            'window':'prior10CalendarSessionsExcludingToday',
            'observationCounts':{s:sum(r['status']==s for r in structural) for s in ('triggered','notTriggered','unknown','skippedExistingExit')},
            'observations':structural,'certified':False,'targetAchieved':False}
        same(result['research']['structuralExits'],detail,'Every held-day structure evaluation')
        same(checkpoint['structuralExits'],detail,'Checkpoint full structure evidence')
    else:
        require('structuralExits' not in result['research'] and 'structuralExits' not in checkpoint
                and 'researchStructuralExits' not in result['versions'], 'Plain trailing account includes structural/day-five adapter')
    require({p['symbol'] for p in result['positions']} == set(positions), 'Terminal risk population')
    for observed in result['positions']:
        for key, value in positions[observed['symbol']].items():
            same(observed[key],value,'Terminal risk '+key)
    return {'heldDays':len(trace),'actualEntries':entry_count,'queuedExits':len(queued),
        'exitReasons':dict(Counter(r['reason'] for r in queued)), 'riskReferenceConversions':len(conversions),
        'structureObservations':len(structural),'structureStatuses':dict(Counter(r['status'] for r in structural)),
        'terminalPendingExits':len(pending),'heldDayRiskTraceSha256':digest(trace),'noProgress5Absent':True}


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
    require(checkpoint['schema']==('fixed-signal-account-checkpoint-v1' if mode=='trail10'
            else 'research-structural-exits-descriptive-snapshot-v1'), 'Checkpoint adapter schema')
    if mode=='structure10':require(checkpoint['checkpointRestoreSupported'] is False,'Structural snapshot restore scope')
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


def self_test():
    days = [(date(2020,1,1)+timedelta(days=i)).isoformat() for i in range(17)]
    bars = [{'date':d,'open':101.,'high':102.,'low':100.,'close':101.,'volume':1000.} for d in days]
    def measure(close=100., omitted=None, volume=None, low=None):
        prior = deepcopy(bars[:10]); current = {**bars[10],'close':close}
        if omitted is not None: prior[omitted]=None
        if volume is not None: prior[5]['volume']=volume
        if low is not None: prior[5]['low']=low
        return structure_reference(days[10],days[:10],current,prior)
    require(measure()['status']=='notTriggered','Equality does not break prior support')
    require(measure(99.)['status']=='triggered' and measure(99.)['prior10Low']==100.,'Strict close break')
    require(measure(200.)['prior10Low']==100.,'Today does not enter prior support')
    require(measure(99.,omitted=4)['status']=='unknown','Missing interior slot cannot be compressed')
    require(measure(99.,volume=0)['status']=='unknown','Zero-volume prior slot is unknown')
    require(measure(99.,low=0)['status']=='unknown','Invalid prior low is unknown')
    require(structure_reference(days[10],days[1:10],bars[10],bars[1:10])['status']=='unknown','Insufficient exact history')
    require(structure_reference(days[10],days[:10],None,bars[:10])['status']=='unknown','Missing current close')

    def fixture(mode, closes):
        source = deepcopy(bars)
        for j,c in enumerate(closes,10): source[j].update(close=c,open=c,high=c+1,low=c-1)
        final=10+len(closes)-1
        config={'startDate':days[10],'endDate':days[final],'stopLossPct':.05,'trailingStopPct':.1 if mode=='trail10' else 0.,
            'takeProfitPct':0.,'maxHoldingDays':0,'rebalanceMode':'risk','universe':{'excludeSt':True}}
        data={'calendar':days,'securities':[{'symbol':'s','bars':source}]}
        result={'config':config,'daily':[{'date':d,'holdings':[{'symbol':'s','quantity':100}]} for d in days[10:final+1]],
            'trades':[{'date':days[10],'side':'buy','symbol':'s','price':100.,'quantity':100}],
            'decisions':[],'forcedExits':{},'research':{},'versions':{},
            'positions':[{'symbol':'s','entryDate':days[10],'quantity':100,'riskEntryPrice':100.,'closePeak':max(100.,*closes)}]}
        return data,result,{}
    data,result,checkpoint=fixture('trail10',[101.,101.,101.,101.,100.])
    require(daily_risk_reference(result,checkpoint,data,'trail10')['heldDays']==5,'No day-five risk exit under trailing-only policy')
    data,result,checkpoint=fixture('trail10',[120.,107.])
    evidence={'observedDate':days[11],'observedClose':107.,'riskEntryPrice':100.,'closePeak':120.,'gainPct':107./100-1,
        'drawdownFromPeakPct':107./120-1,'holdingDays':2,'stopLossPct':.05,'takeProfitPct':0.,'trailingStopPct':.1,
        'maxHoldingDays':0,'percentageUnit':'ratio'}
    result['forcedExits']={'s':{'signalDate':days[11],'reason':'trailingStop','riskEvidence':evidence}}
    result['decisions']=[{'date':days[11],'symbol':'s','action':'sell','status':'queued','reason':'trailingStop',
        'signalDate':days[11],'observedClose':107.,'riskEvidence':evidence}]
    require(daily_risk_reference(result,checkpoint,data,'trail10')['queuedExits']==1,'Actual running close peak trailing')
    broken=deepcopy(result);broken['decisions']=[]
    try: daily_risk_reference(broken,checkpoint,data,'trail10')
    except ValueError: pass
    else: raise ValueError('Missing should-trigger exit escaped audit')
    # Explicit structural fixture: one trigger, a partial sale after rebound,
    # and a final fill must retain the original close's evidence throughout.
    data,result,checkpoint=fixture('structure10',[101.,99.,105.,106.])
    observations=[]
    for i,status in ((10,'notTriggered'),(11,'triggered'),(12,'skippedExistingExit')):
        e={'date':days[i],'symbol':'s','rule':'structure10','entryDate':days[10],'holdingDays':i-9,
            'observedClose':data['securities'][0]['bars'][i]['close'],'riskEntryPrice':100.,
            'priceReferenceMeaning':'entryFillPriceIncludingSlippageExcludingFees'}
        if i<12: e.update(structure_reference(days[i],days[i-10:i],data['securities'][0]['bars'][i],data['securities'][0]['bars'][i-10:i]))
        else: e.update(status=status,existingReason='structure10',existingSignalDate=days[11])
        require(e['status']==status,'Synthetic structural fixture status')
        observations.append(e)
    e=observations[1]
    result['decisions']=[{'date':days[11],'symbol':'s','action':'sell','status':'queued','reason':'structure10',
        'signalDate':days[11],'observedClose':99.,'riskEvidence':e}]
    for i,q in ((12,40),(13,60)):
        result['trades'].append({'date':days[i],'side':'sell','symbol':'s','quantity':q,'reason':'structure10',
            'signalDate':days[11],'riskEvidence':e})
    result['daily'][2]['holdings'][0]['quantity']=60
    result['daily'][3]['holdings']=[]
    result['positions']=[]
    detail={'version':'research-structural-exits-v1','rule':'structure10','checkpointRestoreSupported':False,
        'standardRiskHasPriority':True,'signalTiming':'close;earliestSubsequentExecutableOpen',
        'holdingClock':'marketCalendarSessionsIncludingEntry;missingSessionsConsumeTime',
        'window':'prior10CalendarSessionsExcludingToday',
        'observationCounts':{'triggered':1,'notTriggered':1,'unknown':0,'skippedExistingExit':1},
        'observations':observations,'certified':False,'targetAchieved':False}
    result['research']['structuralExits']=detail;checkpoint['structuralExits']=deepcopy(detail)
    require(daily_risk_reference(result,checkpoint,data,'structure10')['heldDays']==3,'Pending first intent survives rebound and partial sells')
    broken=deepcopy(result);broken['research']['structuralExits']['observations'].pop(0)
    try: daily_risk_reference(broken,checkpoint,data,'structure10')
    except ValueError: pass
    else: raise ValueError('Missing nontrigger day escaped audit')
    data,result,checkpoint=fixture('trail10',[101.,50.])
    data['securities'][0]['precloseByDate']={days[11]:50.5}
    result['positions'][0].update(riskEntryPrice=50.,closePeak=50.5)
    require(daily_risk_reference(result,checkpoint,data,'trail10')['riskReferenceConversions']==1,
            'Preclose risk references convert before the next risk decision')
    data,result,checkpoint=fixture('trail10',[101.,94.])
    data['securities'][0]['bars'][11]['volume']=0
    require(daily_risk_reference(result,checkpoint,data,'trail10')['queuedExits']==0,
            'Missing price-risk activity cannot invent a percent exit')
    data,result,checkpoint=fixture('structure10',[101.,94.])
    risk={'observedDate':days[11],'observedClose':94.,'riskEntryPrice':100.,'closePeak':101.,
        'gainPct':94./100-1,'drawdownFromPeakPct':94./101-1,'holdingDays':2,'stopLossPct':.05,
        'takeProfitPct':0.,'trailingStopPct':0.,'maxHoldingDays':0,'percentageUnit':'ratio'}
    result['forcedExits']={'s':{'signalDate':days[11],'reason':'stopLoss','riskEvidence':risk}}
    result['decisions']=[{'date':days[11],'symbol':'s','action':'sell','status':'queued','reason':'stopLoss',
        'signalDate':days[11],'observedClose':94.,'riskEvidence':risk}]
    first=deepcopy(observations[0])
    second={'date':days[11],'symbol':'s','rule':'structure10','entryDate':days[10],'holdingDays':2,'observedClose':94.,
        'riskEntryPrice':100.,'priceReferenceMeaning':'entryFillPriceIncludingSlippageExcludingFees',
        'status':'skippedExistingExit','existingReason':'stopLoss','existingSignalDate':days[11]}
    detail=deepcopy(detail);detail['observations']=[first,second]
    detail['observationCounts']={'triggered':0,'notTriggered':1,'unknown':0,'skippedExistingExit':1}
    result['research']['structuralExits']=detail;checkpoint['structuralExits']=deepcopy(detail)
    require(daily_risk_reference(result,checkpoint,data,'structure10')['exitReasons']=={'stopLoss':1},
            'Initial stop wins simultaneous structural break and its observation remains skipped')
    data['membershipByDate']={days[11]:[]}
    result['forcedExits']['s']['reason']='baseEligibilityLost'
    result['decisions'][0]['reason']='baseEligibilityLost'
    result['research']['structuralExits']['observations'][1]['existingReason']='baseEligibilityLost'
    checkpoint['structuralExits']=deepcopy(result['research']['structuralExits'])
    require(daily_risk_reference(result,checkpoint,data,'structure10')['exitReasons']=={'baseEligibilityLost':1},
            'Dataset membership takes precedence over price rules')
    print(json.dumps({'passed':True,'selfTests':17,'realSourceRead':False,'accountsExecuted':0}),flush=True)


def registered_plan(directory):
    global REGISTERED_PLAN_SHA
    observed=sha(directory/'plan.json')
    require(REGISTERED_PLAN_SHA is None or REGISTERED_PLAN_SHA==observed,'Plan changed during audit')
    REGISTERED_PLAN_SHA=observed
    plan=read(directory/'plan.json')
    require(plan['sourcePins'].get(Path(__file__).relative_to(ROOT).as_posix())==sha(__file__),'Auditor not frozen in plan')
    require(plan['protocol']['sha256']==PROTOCOL_SHA and read(pinned(plan['protocol']))==plan['rules'],'Frozen protocol changed')
    require(plan['schema']=='reclaim-exit-transfer-v1' and plan['strategyId']==SID and plan['modes']==list(MODES)
            and plan['reportModes']==list(REPORT_MODES) and plan['boards']==list(BOARDS)
            and list(plan['periods'])==list(PERIODS) and plan['labels']==LABELS,'Registered identities differ')
    for key,value in plan['rules']['evidence']['trialAccounting'].items():
        require(type(plan[key]) is int and plan[key]==value,'Registered trial accounting differs: '+key)
    require(plan['newAccounts']==12 and plan['referenceAccountsReused']==6 and plan['parameterSearch'] is False
            and plan['profitHurdles'] is None and plan['holdoutClaim'] is False
            and plan['certified'] is False and plan['targetAchieved'] is False,'Unregistered scope or target claim')
    require(plan['policySpecs']==plan['rules']['policies'] and plan['noSignalsGenerated'] is True
            and plan['productionChanges'] is False,'Changed production or signal scope')
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
        policies['structure10']['trailingStopPct']=0.
        expected={**deepcopy(parent['periods'][period]),'policyConfigs':policies}
        require(plan['periods'][period]==expected,'Configuration changed beyond structural trailing switch')
        require(original['stopLossPct']==.05 and original['trailingStopPct']==.1 and original['takeProfitPct']==0
                and original['maxHoldingDays']==0 and original['allocation']['strategyIds']==[SID], 'Original exit/identity scope')
    return plan


def inherited_sources(plan):
    expected={'plan.json':PARENT_PLAN_SHA,'source-validation.json':PARENT_SOURCE_VALIDATION_SHA,
        'input-validation.json':PARENT_INPUT_VALIDATION_SHA,'validation.json':PARENT_VALIDATION_SHA,'delivery.json':PARENT_DELIVERY_SHA}
    for name,value in expected.items():require(sha(PARENT/name)==value,'Original source/account proof changed: '+name)
    board=reference(BOARD_REFERENCE,BOARD_REFERENCE_SHA)
    source=board.source_proof(PARENT)
    completion,parent_receipts=board.input_receipts(PARENT,read(PARENT/'plan.json'),source)
    inputs,validation=read(PARENT/'input-validation.json'),read(PARENT/'validation.json')
    require(pinned(plan['baselineComparison'])==(PARENT/'comparison.json').resolve(),'Baseline comparison path')
    comparison=read(PARENT/'comparison.json')
    require(inputs['passed'] is True and validation['passed'] is True
            and inputs['planSha256']==validation['planSha256']==PARENT_PLAN_SHA
            and inputs['verifierSha256']==validation['verifierSha256']==BOARD_REFERENCE_SHA
            and validation['inputValidationSha256']==PARENT_INPUT_VALIDATION_SHA
            and inputs['inputCompletionSha256']==validation['inputCompletionSha256']==sha(PARENT/'input-completion.json')
            and validation['comparisonSha256']==sha(PARENT/'comparison.json')
            and inputs['datasetPins']==validation['datasetPins']==source['datasetPins']
            and comparison['artifacts']==validation['accountArtifacts'],'Original completed-account audit chain stale')
    delivery=read(PARENT/'delivery.json')
    for name in ('plan.json','input-completion.json','input-validation.json','validation.json','comparison.json'):
        path=PARENT/name
        require(delivery['files'][path.relative_to(ROOT).as_posix()]==sha(path),'Original delivery link changed')
    for period in PERIODS:
        manifest=read(PARENT/period/'source-manifest.json')
        for b in BOARDS:
            parent=parent_receipts[period][b]
            binding=plan['sourceData'][period][b]
            expected={k:parent[k] for k in ('accountData','accountDatasetDigest','sourceManifest','sourceValidation')}
            expected.update(members=manifest['boards'][b]['includedCount'],sourceCompletion=completion['sourceCompletion'],
                            upstreamInputReceipt=completion['outputs'][period][b])
            require(binding==expected,'Original six-source full binding changed')
            require(plan['baselineInputs'][period][b]==completion['outputs'][period][b]
                    and plan['baselineSchedules'][period][b]==parent['files']['immediate.signals.json'], 'Baseline input/schedule differs')
            require(parent['files']==inputs['inputArtifacts'][period][b]==validation['inputArtifacts'][period][b], 'Original signal lineage changed')
            for ref in parent['files'].values():pinned(ref)
            account_files=plan['baselineArtifacts'][period][b]
            require(account_files==comparison['artifacts'][period][b]
                    and set(account_files)=={'immediate'+s for s in ('.result.json.gz','.checkpoint.json.gz','.summary.json')},
                    'Original immediate artifacts changed')
            for name,ref in account_files.items():require(pinned(ref)==(PARENT/period/b/name).resolve(),'Baseline original file path')
    return source


def input_receipts(directory,plan):
    completion=read(directory/'input-completion.json')
    require(completion['planSha256']==REGISTERED_PLAN_SHA and completion['allSchedulesFrozenBeforeAccounts'] is True
            and completion['noSignalsGenerated'] is True and completion['newAccounts']==12 and completion['referenceAccountsReused']==6
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
            require(receipt['schema']=='reclaim-exit-transfer-input-v1' and receipt['planSha256']==REGISTERED_PLAN_SHA
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
        **{k:plan[k] for k in ('baselinePlan','baselineComparison','baselineArtifacts','baselineSchedules','baselineInputs')}}


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
            print(json.dumps({'phase':'independent-exit-transfer-input','period':period,'board':board,'signals':len(signals),'passed':True}),flush=True)
            del data,opportunities;gc.collect()
    registered_plan(directory)
    write_proof(directory,'input-validation.json',{'schema':'reclaim-exit-transfer-input-independent-validation-v1','passed':True,
        'verifiedAt':datetime.now(timezone.utc).isoformat(),'verifierSha256':sha(__file__),'planSha256':REGISTERED_PLAN_SHA,
        'inputCompletionSha256':sha(directory/'input-completion.json'),**parent_proof_fields(plan),
        'datasetPins':source['datasetPins'],'inputArtifacts':artifacts,'periods':reports,
        'counts':{'sourceInputs':6,'originalSchedules':6,'newPolicySchedules':12,'sourceImmediateOpportunities':signals_total},
        'checks':['All six inherited raw-source/cache proofs and baseline account proofs bound',
            'Every original UR immediate opportunity and exact five-field original schedule retained without reranking, filtering or regeneration',
            'Both candidate policies use the same unchanged strategy identity, source and complete signal queue'],
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
    for path,value in tests['pins'].items():require(sha(local(path))==value,'Pre-account tested source changed')
    comparison=read(directory/'comparison.json');previous=read(pinned(plan['baselineComparison']))
    require(comparison['schema']=='reclaim-exit-transfer-comparison-v1' and comparison['planSha256']==REGISTERED_PLAN_SHA,'Comparison identity')
    require(comparison['noSignalsGenerated'] is True,'Comparison claims new signal computation')
    for key in (*plan['rules']['evidence']['trialAccounting'],'limitations','certified','targetAchieved'):
        require(comparison[key]==plan[key],'Comparison scope differs: '+key)
    require([(r['period'],r['board'],r['id'],r['policy']) for r in comparison['rows']]
            ==[(p,b,m,m) for p in PERIODS for b in BOARDS for m in REPORT_MODES],'Full ordered 18-account comparison')
    board=reference(BOARD_REFERENCE,BOARD_REFERENCE_SHA)
    accounting=reference(board.ACCOUNT_REFERENCE,board.ACCOUNT_REFERENCE_SHA)
    dependencies={BOARD_REFERENCE:BOARD_REFERENCE_SHA,board.ACCOUNT_REFERENCE:board.ACCOUNT_REFERENCE_SHA,
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
            require(list(files)==list(REPORT_MODES) and files['baseline']==plan['baselineArtifacts'][period][b], 'Account mode/baseline closure')
            schedule=read(pinned(receipt['baselineSchedule']))
            require(len(schedule)==inputs['periods'][period][b]['sourceImmediateOpportunities'],'Original source count drift')
            base=deepcopy(next(r for r in previous['rows'] if (r['period'],r['board'],r['id'])==(period,b,'immediate')))
            base.update(id='baseline',policy='baseline',label=LABELS['baseline'])
            actual=next(r for r in comparison['rows'] if (r['period'],r['board'],r['id'])==(period,b,'baseline'))
            require(actual==base,'Baseline comparison changed beyond display labels')
            summary=read(pinned(files['baseline']['immediate.summary.json']))
            require(summary['policyId']=='immediate','Original baseline summary identity changed')
            expected=board.comparison_reference(summary,period,b,LABELS['baseline'],len(schedule));expected.update(id='baseline',policy='baseline')
            same(actual,expected,'Original baseline metrics')
            reused[period][b]={'reused':True,'sourcePolicy':'immediate','sourceStrategyId':SID,
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
                print(json.dumps({'phase':'independent-exit-transfer-account','period':period,'board':b,'mode':mode,
                    'fills':checked['fills'],'heldDays':checked['dailyRisk']['heldDays'],'passed':True}),flush=True)
                del result,checkpoint,summary;gc.collect()
            del data;gc.collect()
    registered_plan(directory)
    reports_flat=[r for p in reports.values() for b in p.values() for r in b.values()]
    proof={'schema':'reclaim-exit-transfer-independent-validation-v1','passed':True,
        'verifiedAt':datetime.now(timezone.utc).isoformat(),'verifierSha256':sha(__file__),'planSha256':REGISTERED_PLAN_SHA,
        'comparisonSha256':sha(directory/'comparison.json'),'inputValidationSha256':sha(directory/'input-validation.json'),
        'inputCompletionSha256':sha(directory/'input-completion.json'),'preAccountTests':run_start['preAccountTests'],
        **parent_proof_fields(plan),'datasetPins':source['datasetPins'],'inputArtifacts':inputs['inputArtifacts'],
        'accountArtifacts':comparison['artifacts'],'independentReferences':dependencies,'periods':reports,'reusedAccounts':reused,
        'counts':{'accounts':18,'newAccounts':12,'reusedAccounts':6,'sourceImmediateOpportunities':inputs['counts']['sourceImmediateOpportunities'],
            'newAccountFills':sum(r['fills'] for r in reports_flat),'independentHeldDayRiskChecks':sum(r['dailyRisk']['heldDays'] for r in reports_flat),
            'structureObservations':sum(r['dailyRisk']['structureObservations'] for r in reports_flat)},
        'checks':['Six original baseline account artifacts and unchanged full UR immediate schedules',
            'Twelve candidate ledgers, dated fees, cash/shares, reserve budgets, board lots/limits, causal fill prices and T+1',
            'Every held close independently reconstructed from fills/source: reference conversion, exact prior10 slots, unknown, priority and absence of day5',
            'Should-trigger and should-not-trigger days; first pending evidence survives rebound/partial sales; next-opening fills retain that evidence',
            'Closed/open episodes, continuous curves, checkpoints, final positions and all18 comparison rows'],
        'scope':{'accountsExecutedByVerifier':0,'productionRiskFunctionsCalled':False,'newLedgerAudits':12,'inheritedBaselineAudits':6,
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


