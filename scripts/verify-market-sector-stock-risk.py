"""Independent actual-fill risk replay, extending the preserved prior oracle.

No production account class is imported. Fills establish held quantities;
source windows, frozen close contexts and entry data reconstruct all exits.
This is not a second execution engine.
"""
from collections import defaultdict,Counter
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HELPER='scripts/verify-reclaim-volatility-trail.py'
HELPER_SHA='81bd4a8681f31d9a1dc5c5732ed8cb7d763e3c779b58492098bbc3957fd0bb08'
if hashlib.sha256((ROOT/HELPER).read_bytes()).hexdigest()!=HELPER_SHA:
    raise ValueError('Preserved independent volatility oracle changed')
spec=importlib.util.spec_from_file_location('_whole_system_preserved_risk_oracle',ROOT/HELPER)
helper=importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
require,same,positive,calendar_check,digest,prior_bar_issue,volatility_reference,trail_step_reference,detail_reference=(getattr(helper,n) for n in
    ('require','same','positive','calendar_check','digest','prior_bar_issue','volatility_reference','trail_step_reference','detail_reference'))

def daily_system_risk_reference(result, checkpoint, dataset, system_inputs):
    """Replay every held close from actual fills, never from observed sell signals.

    This is a risk-decision audit, not a second account execution. Existing
    opening execution/cash/capacity checks are handled by independent helpers.
    """
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
    closing_states = {}
    system_days = {r['date']: r for r in system_inputs['daily']}
    system_meta = system_inputs['metadata']
    system_maps = system_inputs['sectorMaps']
    system_entries, system_observations, system_live = [], [], {}

    def member(symbol, day):
        if day in memberships:
            return symbol in memberships[day]
        security = securities[symbol]
        return security.get('memberHistory', security.get('memberByDate', {})).get(day)

    def enqueue(symbol, day, reason, evidence):
        require(symbol not in pending, 'First risk intent was overwritten')
        pending[symbol] = {'signalDate': day, 'reason': reason, 'riskEvidence': deepcopy(evidence)}
        queued.append({'date': day, 'symbol': symbol, 'action': 'sell', 'status': 'queued', 'reason': reason,
            'signalDate': day, **({} if reason.startswith('system') else {'observedClose': evidence['observedClose']}), 'riskEvidence': deepcopy(evidence)})

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
                meta = system_meta[fill['signalId']]
                sys_binding = {'symbol': symbol, 'signalId': fill['signalId'], 'signalDate': fill['signalDate'], 'entryDate': day,
                    'entryFillId': fill['fillId'], 'entryFillPrice': fill['price'], 'failurePriceAtSignal': meta['failurePrice'],
                    'failureRatio': meta['failurePrice']/fill['price'], 'entrySector': meta['sectorCode'], 'lastKnownSector': meta['sectorCode']}
                system_live[symbol] = sys_binding
                system_entries.append(deepcopy(sys_binding))
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
                    del system_live[symbol]
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
        context = system_days[day]
        mapping = system_maps.get(context['snapshotId'], {})
        sector_states = {r['sectorCode']:r for r in context['sectors']}
        for symbol in sorted(positions):
            position, binding, bar = positions[symbol], system_live[symbol], bars[symbol].get(day)
            if symbol in mapping:
                binding['lastKnownSector'] = mapping[symbol]
            state = sector_states.get(mapping.get(symbol))
            floor = binding['failurePriceAtSignal'] / binding['entryFillPrice'] * position['riskEntryPrice']
            valid = prior_bar_issue(bar,day) is None
            trigger_stock = bool(valid and bar['close'] <= floor)
            trigger_market = context['market']['riskExit'] is True
            trigger_sector = state is not None and state['riskExit'] is True
            old_order = pending.get(symbol)
            requested_reason = ('systemStructuralFailure' if trigger_stock else 'systemMarketRisk' if trigger_market
                else 'systemSectorRisk' if trigger_sector else None)
            reason = old_order['reason'] if old_order else requested_reason
            sys_evidence = {'date':day,'symbol':symbol,'signalId':binding['signalId'],'entryDate':position['entryDate'],
                'riskEntryPrice':position['riskEntryPrice'],'failureRatio':binding['failureRatio'],'failureThreshold':floor,
                'observedClose':bar['close'] if bar else None,'currentPriceValid':valid,'structuralTriggered':trigger_stock,
                'marketRiskTriggered':trigger_market,'marketCrisisCount':context['market']['crisisCount'],'knownSector':mapping.get(symbol),
                'sectorFailCount':state['failCount'] if state else None,'sectorRiskTriggered':trigger_sector,
                'earlierRiskRetained':old_order is not None,'reason':reason}
            system_observations.append(sys_evidence)
            if old_order is None and requested_reason:
                enqueue(symbol,day,requested_reason,sys_evidence)
        closing_states[day] = {'pending': deepcopy(pending), 'systemLive': deepcopy(system_live)}
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
    same(result['research']['system']['entryBindings'],system_entries,'Every structural entry binding')
    same(checkpoint['currentSystemEntries'],system_live,'Terminal system entry reservations')
    same(result['research']['system']['riskObservations'],system_observations,'Every system held-day decision')
    same(checkpoint['system']['riskObservations'],system_observations,'Checkpoint system held-day decision')
    detail=detail_reference(entry_bindings,observations)
    same(result['research']['volatilityTrail'],detail,'Every held-day volatility window and ratchet decision')
    same(checkpoint['volatilityTrail'],detail,'Checkpoint full volatility evidence')
    same(checkpoint['currentPositionTrails'],trail_positions,'Terminal episode ratchet state')
    require({p['symbol'] for p in result['positions']}==set(positions),'Terminal risk population')
    for observed in result['positions']:
        for key,value in positions[observed['symbol']].items():same(observed[key],value,'Terminal risk '+key)
    return {'dailyCloseRiskState':closing_states,'systemHeldDayObservations':len(system_observations),'systemEntryBindings':len(system_entries),'heldDays':len(trace),'actualEntries':entry_count,'queuedExits':len(queued),
        'exitReasons':dict(Counter(r['reason'] for r in queued)),'riskReferenceConversions':len(conversions),
        'volatilityObservations':len(observations),'observationCounts':detail['observationCounts'],
        'windowCounts':detail['windowCounts'],'usedCarriedThresholdCount':sum(r['usedCarriedThreshold'] for r in observations),
        'terminalPendingExits':len(pending),'terminalRatchetStates':len(trail_positions),
        'heldDayRiskTraceSha256':digest(trace),'unregisteredExitsAbsent':True}
