"""Independent audit helpers for the frozen volatility-stop account experiment.

The final audit reads the saved source projection and actual account ledgers.
It never invokes the account adapter or loads the full market source.
"""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/relative-repair-volatility-stop-v1'
PERIODS = ('2022-2023', '2024-2025')
MODES = ('fixed5', 'meanTR20x2')
COUNT = {'2022-2023': 565, '2024-2025': 624}
COST_REFERENCE = 'scripts/verify-relative-repair-cost-stress.py'
COST_REFERENCE_SHA = 'df00a1573933b4624563b4db0f451a731449c55c180b8b6793f5967b147e762a'
ADAPTER_SHA = '429fe1df2a237547f811340a9ff28f6b80d2ed9cd5b6ba147c9bfe426757a7d9'
REFERENCE_CONVERSION = 'Inherited riskEntryPrice conversion scales frozen entry fraction; never re-estimates TR.'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def positive(value):
    return finite(value) and value > 0


def same(actual, expected, label):
    if finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7),
                f'{label}: {actual!r} != {expected!r}')
    elif isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), f'{label}: keys differ')
        for key in expected:
            same(actual[key], expected[key], f'{label}.{key}')
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), f'{label}: length differs')
        for index, (left, right) in enumerate(zip(actual, expected)):
            same(left, right, f'{label}[{index}]')
    else:
        require(type(actual) is type(expected) and actual == expected, f'{label}: literal differs')


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz')
          else Path(path).open(encoding='utf-8')) as stream:
        return json.load(stream)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def iso(day):
    require(type(day) is str and date.fromisoformat(day).isoformat() == day, 'Invalid date')
    return day


def local(name):
    candidate = Path(name)
    require(not candidate.is_absolute() and '..' not in candidate.parts, 'Unsafe repository path')
    candidate = (ROOT / candidate).resolve()
    require(candidate.is_relative_to(ROOT.resolve()), 'Path outside repository')
    return candidate


def pinned(ref):
    path = local(ref['path'])
    require(sha(path) == ref['sha256'], 'Pinned reference changed: ' + ref['path'])
    return path


def cost_reference():
    path = local(COST_REFERENCE)
    require(sha(path) == COST_REFERENCE_SHA, 'Independent cost verifier changed')
    spec = importlib.util.spec_from_file_location('_volatility_independent_cost_reference', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def distribution(values):
    require(all(finite(value) for value in values), 'Nonfinite risk statistic')
    ordered = sorted(values)
    result = {'count': len(ordered), 'mean': math.fsum(ordered) / len(ordered) if ordered else None}
    for label, percentile in (('min', 0), ('p05', .05), ('p25', .25), ('median', .5), ('p75', .75), ('p95', .95), ('max', 1)):
        if not ordered:
            result[label] = None
        else:
            point = (len(ordered) - 1) * percentile
            lo, hi = math.floor(point), math.ceil(point)
            result[label] = ordered[lo] if lo == hi else ordered[lo] * (hi - point) + ordered[hi] * (point - lo)
    return result


def prior_tr20(window_dates, window_bars, signal_date, calendar):
    """Reconstruct 20 unweighted true ranges from exactly T-21..T-1 slots.

    The normalized output is this verifier's own arithmetic representation,
    not the adapter's or preparation script's output schema.
    """
    require(calendar == sorted(set(calendar)), 'Invalid source calendar')
    require(signal_date in calendar, 'Signal date absent from source calendar')
    position = calendar.index(signal_date)
    expected_dates = calendar[max(0, position - 21):position]
    require(window_dates == expected_dates, 'TR window must retain exact T-21..T-1 slots')
    require(len(window_dates) == len(window_bars), 'TR bars/date slot mismatch')
    issues = []
    if len(window_dates) != 21:
        issues.append({'date': signal_date, 'reason': 'insufficientCalendarSlots'})
    for index, (day, bar) in enumerate(zip(window_dates, window_bars)):
        if bar is None:
            issues.append({'date': day, 'reason': 'missingBar'})
            continue
        require(isinstance(bar, dict) and bar.get('date') == day, 'TR bar has foreign date')
        if not positive(bar.get('close')):
            issues.append({'date': day, 'reason': 'invalidClose'})
        if index > 0 and (not positive(bar.get('high')) or not positive(bar.get('low'))
                          or bar['high'] < bar['low']):
            issues.append({'date': day, 'reason': 'invalidHighLow'})
    if issues:
        return {'valid': False, 'slotCount': len(window_dates), 'ranges': None,
                'sumTR': None, 'meanTR20': None, 'issues': issues}
    ranges = []
    for index in range(1, 21):
        previous, current = window_bars[index - 1], window_bars[index]
        # Independent max-of-three definition rather than max/min envelope.
        span = max(current['high'] - current['low'],
                   abs(current['high'] - previous['close']), abs(current['low'] - previous['close']))
        ranges.append({'date': window_dates[index], 'previousDate': window_dates[index - 1],
                       'previousClose': previous['close'], 'high': current['high'], 'low': current['low'],
                       'trueRange': span})
    total = math.fsum(row['trueRange'] for row in ranges)
    return {'valid': True, 'slotCount': 21, 'ranges': ranges,
            'sumTR': total, 'meanTR20': total / 20, 'issues': []}


def frozen_stop(mean_tr20, entry_price, quantity, mode):
    """Freeze stop percentage at actual slipped entry; only price units scale."""
    require(mode in ('fixed5', 'meanTR20x2'), 'Unregistered stop mode')
    require(positive(entry_price) and type(quantity) is int and quantity > 0, 'Invalid actual entry')
    proposed = mean_tr20 * 2 if finite(mean_tr20) else None
    usable = (positive(proposed) and finite(proposed / entry_price) and 0 < proposed / entry_price < 1)
    effective = .05 if mode == 'fixed5' or not usable else proposed / entry_price
    return {'effectivePct': effective, 'entryDistance': entry_price * effective,
            'entryRiskAmount': quantity * entry_price * effective,
            'fallback': mode == 'meanTR20x2' and not usable,
            'proposedDistance': proposed}


def inspect_risk_projection(projection, signals):
    """Check the source projection's shape without claiming original-source reload."""
    calendar = projection['calendar']
    require(calendar == sorted(set(calendar)) and all(iso(day) for day in calendar), 'Risk projection calendar')
    needed = sorted({signal['symbol'] for signal in signals})
    securities = projection['securities']
    require([security['symbol'] for security in securities] == needed, 'Risk projection must contain exactly original-signal securities')
    calendar_set, bars_by_symbol = set(calendar), {}
    for signal in signals:
        require(signal['signalDate'] in calendar_set, 'Signal absent from risk calendar')
    for security in securities:
        symbol = security['symbol']
        bars = security['bars']
        days = [bar['date'] for bar in bars]
        require(days == sorted(set(days)) and set(days) <= calendar_set, 'Risk bars duplicate, unordered or foreign date')
        require(not security.get('corporateActions'), 'Declared corporate actions outside this experiment')
        for key in ('stByDate', 'precloseByDate', 'memberHistory', 'memberByDate', 'tradeStatusByDate'):
            if key in security:
                require(isinstance(security[key], dict), 'Malformed dated security metadata: ' + key)
                for day in security[key]:
                    iso(day)
        for key in ('listingDate', 'delistDate'):
            if security.get(key) is not None:
                iso(security[key])
        bars_by_symbol[symbol] = {bar['date']: bar for bar in bars}
    memberships = projection.get('membershipByDate', {})
    require(isinstance(memberships, dict), 'Malformed dataset memberships')
    for day, members in memberships.items():
        iso(day)
        require(isinstance(members, list) and len(members) == len(set(members))
                and set(members) <= set(needed), 'Risk membership projection contains duplicates/foreign symbols')
    return {'calendar': calendar, 'securities': {s['symbol']: s for s in securities}, 'membershipByDate': memberships,
            'bars': bars_by_symbol, 'counts': {'calendarSessions': len(calendar), 'securities': len(securities),
                'bars': sum(len(s['bars']) for s in securities), 'membershipDates': len(memberships)}}


def audit_tr_inputs(document, prior, signals, calendar, plan_sha, source_ref):
    require(document['schema'] == 'prior-mean-true-range20-v1' and document['planSha256'] == plan_sha, 'TR input schema/plan')
    same(document['source'], source_ref, 'TR original source reference')
    require(document['sourceCalendarSha256'] == digest(calendar) == prior['sourceCalendarSha256'], 'TR calendar hash')
    require(list(prior['rows']) == [s['signalId'] for s in signals], 'Original pressure identity/order')
    require(len(document['rows']) == len(signals), 'TR signal coverage')
    compact, expected_rows = {}, []
    for signal, actual in zip(signals, document['rows']):
        sid, day = signal['signalId'], signal['signalDate']
        source = prior['rows'][sid]
        position = calendar.index(day)
        require(source['signalDate'] == day and source['symbol'] == signal['symbol'], 'Prior pressure signal identity')
        require(source['windowDates'] == calendar[max(0, position - 40):position], 'Prior40 exact calendar')
        require(len(source['windowDates']) == len(source['windowBars']), 'Prior40 aligned bars')
        require(source['asOfDate'] == calendar[position - 1], 'Prior40 is not T-1')
        dates = calendar[max(0, position - 21):position]
        bars = source['windowBars'][-len(dates):] if dates else []
        computed = prior_tr20(dates, bars, day, calendar)
        small = {key: signal[key] for key in ('signalId', 'symbol', 'signalDate')}
        small.update(asOfDate=calendar[position - 1], meanTrueRange20=computed['meanTR20'])
        expected = {**small, 'windowDates': dates, 'windowBars': bars,
                    'trueRanges': [r['trueRange'] for r in computed['ranges']] if computed['valid'] else None,
                    'inputValid': computed['valid'], 'issues': computed['issues'],
                    'windowSha256': digest({'dates': dates, 'bars': bars})}
        same(actual, expected, 'TR full row ' + sid)
        expected_rows.append(expected)
        compact[sid] = small
    same(document['compactInputs'], compact, 'All compact TR inputs')
    counts = {'opportunities': len(signals), 'validWindows': sum(r['inputValid'] for r in expected_rows),
              'invalidWindows': sum(not r['inputValid'] for r in expected_rows),
              'zeroMean': sum(r['meanTrueRange20'] == 0 for r in expected_rows)}
    same(document['counts'], counts, 'TR counts')
    return compact, counts


def normalized_inputs(compact, calendar):
    result = {}
    for sid, row in compact.items():
        value = row['meanTrueRange20']
        index = calendar.index(row['signalDate'])
        result[sid] = {key: row[key] for key in ('signalId', 'symbol', 'signalDate', 'asOfDate')}
        result[sid].update(meanTrueRange20=value if positive(value) else None, inputMeanTrueRange20=value,
                           inputValidity='insufficientPriorCalendarSessions' if index < 21 else
                           'valid' if positive(value) else 'missingOrInvalidMeanTrueRange20')
    return result


def entry_binding(fill, inputs, mode):
    source = inputs.get(fill['signalId'])
    require(source is not None, 'Every registered signal must retain its input, including unknown TR')
    price, quantity = fill['price'], fill['quantity']
    mean_tr = source['meanTrueRange20']
    terms = frozen_stop(mean_tr, price, quantity, mode)
    reason = None
    if mode == 'meanTR20x2':
        if source['inputValidity'] != 'valid':
            reason = source['inputValidity']
        elif terms['fallback']:
            reason = 'distanceNotStrictlyBetweenZeroAndEntryPrice'
    return {'signalId': fill['signalId'], 'symbol': fill['symbol'], 'signalDate': fill['signalDate'],
            'entryDate': fill['date'], 'entryFillId': fill['fillId'], 'entryQuantity': quantity,
            'mode': mode, 'asOfDate': source['asOfDate'], 'meanTrueRange20': mean_tr,
            'inputMeanTrueRange20': source['inputMeanTrueRange20'], 'entryRiskPrice': price, 'entryFillPrice': price,
            'entryDistance': terms['entryDistance'], 'entryInitialRiskAmount': terms['entryRiskAmount'],
            'effectivePct': terms['effectivePct'], 'fallback': reason is not None, 'fallbackReason': reason,
            'effectiveMode': 'meanTR20x2' if mode == 'meanTR20x2' and reason is None else 'fixed5'}


def current_terms(binding, position):
    return {**binding, 'currentRiskEntryPrice': position['riskEntryPrice'],
            'currentRiskDistance': position['riskEntryPrice'] * binding['effectivePct'],
            'threshold': position['riskEntryPrice'] * (1 - binding['effectivePct']),
            'referenceConversion': REFERENCE_CONVERSION}


def audit_daily_risk(result, checkpoint, projection, compact, mode):
    """Rebuild held-day state from actual fills and separately saved source bars.

    This verifies every should-trigger and should-not-trigger day. Order gates
    remain inherited; filled sells must use the first still-pending evidence.
    """
    calendar, securities, bars = projection['calendar'], projection['securities'], projection['bars']
    ci = {day: i for i, day in enumerate(calendar)}
    bars_order = {s: security['bars'] for s, security in securities.items()}
    bar_indices = {s: {bar['date']: i for i, bar in enumerate(values)} for s, values in bars_order.items()}
    memberships = projection.get('membershipByDate', {})
    inputs = normalized_inputs(compact, calendar)
    detail = result['research']['volatilityStop']
    require(detail['version'] == 'research-volatility-stop-v1' and detail['mode'] == mode
            and detail['checkpointRestoreSupported'] is False, 'Volatility adapter identity')
    same(detail['inputs'], inputs, 'Normalized volatility inputs')
    require(detail['inputSha256'] == digest(inputs), 'Normalized input digest')
    fills = defaultdict(list)
    for fill in result['trades']:
        fills[fill['date']].append(fill)
    positions, pending, bindings, observations, structural, queued = {}, {}, [], [], [], []
    conversions = []
    config = result['config']

    def membership(symbol, day):
        if day in memberships:
            return symbol in memberships[day]
        security = securities[symbol]
        return security.get('memberHistory', security.get('memberByDate', {})).get(day)

    def enqueue(symbol, day, reason, evidence):
        pending[symbol] = {'signalDate': day, 'reason': reason, 'riskEvidence': evidence}
        queued.append({'date': day, 'symbol': symbol, 'action': 'sell', 'status': 'queued', 'reason': reason,
                       'signalDate': day, 'observedClose': evidence['observedClose'], 'riskEvidence': deepcopy(evidence)})

    for daily in result['daily']:
        day = daily['date']
        # Original preclose conversion happens before opening fills, for existing holdings only.
        for symbol, position in positions.items():
            index = bar_indices[symbol].get(day)
            if not index:
                continue
            reference = securities[symbol].get('precloseByDate', {}).get(day)
            previous = bars_order[symbol][index - 1]['close']
            if reference is not None and abs(reference - previous) > max(.005, previous * 1e-7):
                factor = reference / previous
                position['riskEntryPrice'] *= factor
                position['closePeak'] *= factor
                conversions.append({'date': day, 'symbol': symbol, 'factor': factor})
        for fill in fills[day]:
            symbol = fill['symbol']
            if fill['side'] == 'buy':
                require(symbol not in positions, 'Risk replay unexpected position add')
                bind = entry_binding(fill, inputs, mode)
                bindings.append(bind)
                positions[symbol] = {'entryDate': day, 'quantity': fill['quantity'], 'riskEntryPrice': fill['price'],
                                     'closePeak': fill['price'], 'binding': bind}
            else:
                require(symbol in positions and symbol in pending, 'Sell lacks independently reconstructed queued exit')
                order = pending[symbol]
                require(order['signalDate'] < day and fill['signalDate'] == order['signalDate']
                        and fill['reason'] == order['reason'], 'Sell reason/date differs from first pending risk trigger')
                same(fill['riskEvidence'], order['riskEvidence'], 'Executed immutable risk evidence')
                positions[symbol]['quantity'] -= fill['quantity']
                if positions[symbol]['quantity'] == 0:
                    del positions[symbol]
                    del pending[symbol]
        held = {r['symbol']: r for r in daily['holdings']}
        require(set(held) == set(positions), 'Held-day coverage differs from fills')
        for symbol in positions:
            require(held[symbol]['quantity'] == positions[symbol]['quantity'], 'Held-day quantity differs')
        before_pending = deepcopy(pending)
        # Standard close rules, followed by the separate exact-fifth-session rule.
        for symbol in sorted(positions):
            position, security, bar = positions[symbol], securities[symbol], bars[symbol].get(day)
            terms = current_terms(position['binding'], position)
            reason = None
            if membership(symbol, day) is False:
                reason = 'baseEligibilityLost'
            elif security.get('delistDate') and day >= security['delistDate']:
                reason = 'delisted'
            elif config['universe']['excludeSt'] and security.get('stByDate', {}).get(day) is True:
                reason = 'historicalST'
            elif bar is not None and bar['volume'] > 0:
                position['closePeak'] = max(position['closePeak'], bar['close'])
                if bar['close'] / position['riskEntryPrice'] - 1 <= -terms['effectivePct']:
                    reason = 'stopLoss'
                elif bar['close'] / position['closePeak'] - 1 <= -.1:
                    reason = 'trailingStop'
            if reason is not None and symbol not in pending:
                close = bar['close'] if bar is not None else None
                evidence = {'observedDate': day, 'observedClose': close, 'riskEntryPrice': position['riskEntryPrice'],
                            'closePeak': position['closePeak'],
                            'gainPct': close / position['riskEntryPrice'] - 1 if close is not None else None,
                            'drawdownFromPeakPct': close / position['closePeak'] - 1 if close is not None else None,
                            'holdingDays': ci[day] - ci[position['entryDate']] + 1,
                            'stopLossPct': terms['effectivePct'], 'takeProfitPct': config['takeProfitPct'],
                            'trailingStopPct': config['trailingStopPct'], 'maxHoldingDays': config['maxHoldingDays'],
                            'percentageUnit': 'ratio'}
                if mode == 'meanTR20x2':
                    evidence.update(configuredFallbackStopLossPct=.05, volatilityStop=terms)
                enqueue(symbol, day, reason, evidence)
        for symbol in sorted(positions):
            position, bar = positions[symbol], bars[symbol].get(day)
            age = ci[day] - ci[position['entryDate']] + 1
            if age != 5:
                continue
            evidence = {'date': day, 'symbol': symbol, 'rule': 'noProgress5', 'entryDate': position['entryDate'],
                        'holdingDays': age, 'observedClose': bar['close'] if bar is not None else None,
                        'riskEntryPrice': position['riskEntryPrice'],
                        'priceReferenceMeaning': 'entryFillPriceIncludingSlippageExcludingFees'}
            if mode == 'meanTR20x2':
                evidence['volatilityStop'] = current_terms(position['binding'], position)
            if symbol in pending:
                evidence.update(status='skippedExistingExit', existingReason=pending[symbol]['reason'],
                                existingSignalDate=pending[symbol]['signalDate'])
            else:
                known = bar is not None and positive(bar.get('close')) and positive(bar.get('volume')) and positive(position['riskEntryPrice'])
                evidence.update(status='unknown' if not known else 'triggered' if bar['close'] <= position['riskEntryPrice'] else 'notTriggered',
                    comparison='fifthSessionCloseLessThanOrEqualRiskEntryPrice',
                    unknown=[] if known else [{'date': day, 'reason': 'missingOrInvalidFifthSessionPriceOrVolume'}], retryOnLaterSession=False)
            structural.append(evidence)
            if evidence['status'] == 'triggered':
                enqueue(symbol, day, 'noProgress5', evidence)
        for symbol in sorted(positions):
            position, bar = positions[symbol], bars[symbol].get(day)
            known = bar is not None and positive(bar.get('close')) and positive(bar.get('volume'))
            order = pending.get(symbol)
            status = 'skippedExistingExit' if symbol in before_pending else 'triggered' if order else 'notTriggered' if known else 'unknown'
            observations.append({**current_terms(position['binding'], position), 'date': day,
                'holdingDays': ci[day] - ci[position['entryDate']] + 1, 'quantity': position['quantity'],
                'observedClose': bar['close'] if bar is not None else None, 'barAvailableForPriceRisk': known,
                'closePeak': position['closePeak'], 'status': status,
                'exitReason': order['reason'] if order else None, 'exitSignalDate': order['signalDate'] if order else None,
                'existingExitReason': before_pending[symbol]['reason'] if symbol in before_pending else None})
    same(detail['entryBindings'], bindings, 'All actual-entry volatility bindings')
    same(detail['observations'], observations, 'Every held-day volatility observation')
    same(detail['observationCounts'], dict(Counter(row['status'] for row in observations)), 'Held-day status counts')
    same(detail['fallbackEntryCount'], sum(row['fallback'] for row in bindings), 'Fallback entry count')
    same(result['research']['structuralExits']['observations'], structural, 'Exact fifth-session observations including skipped/unknown')
    same(result['research']['structuralExits']['observationCounts'],
         {key: sum(r['status'] == key for r in structural) for key in ('triggered', 'notTriggered', 'unknown', 'skippedExistingExit')}, 'Fifth-session status counts')
    same([r for r in result['decisions'] if r['action'] == 'sell' and r['status'] == 'queued'], queued, 'Every queued exit decision')
    same(result['forcedExits'], pending, 'Terminal pending exit orders')
    same(checkpoint['currentPositionStops'], {s: p['binding'] for s, p in positions.items()}, 'Terminal frozen stop bindings')
    same(checkpoint['volatilityStop'], detail, 'Checkpoint risk detail')
    same(checkpoint['structuralExits'], result['research']['structuralExits'], 'Checkpoint structural detail')
    for position in result['positions']:
        state = positions[position['symbol']]
        for key in ('entryDate', 'quantity', 'riskEntryPrice', 'closePeak'):
            same(position[key], state[key], 'Terminal risk state ' + key)
    return {'heldDays': len(observations), 'actualEntries': len(bindings),
            'queuedExits': len(queued), 'fifthSessionObservations': len(structural),
            'riskReferenceConversions': len(conversions), 'fallbackEntries': detail['fallbackEntryCount'],
            'terminalPendingExits': len(pending), 'statusCounts': detail['observationCounts']}


def audit_risk_statistics(result, summary, actual):
    bindings = result['research']['volatilityStop']['entryBindings']
    returns = [r['netReturnPct'] for r in summary['closedEpisodes']['episodes']]
    expected = {'actualEntries': len(bindings),
        'entryStopPct': distribution([100 * row['effectivePct'] for row in bindings]),
        'entryInitialRiskAmount': distribution([row['entryInitialRiskAmount'] for row in bindings]),
        'tighterThan5': sum(row['effectivePct'] < .05 for row in bindings),
        'equalTo5': sum(row['effectivePct'] == .05 for row in bindings),
        'widerThan5': sum(row['effectivePct'] > .05 for row in bindings),
        'atLeastTrailing10': sum(row['effectivePct'] >= .1 for row in bindings),
        'fallbackCount': sum(row['fallback'] for row in bindings),
        'fallbackReasons': dict(Counter(row['fallbackReason'] for row in bindings if row['fallback'])),
        'queuedExitReasons': dict(Counter(row['reason'] for row in result['decisions'] if row.get('action') == 'sell' and row.get('status') == 'queued')),
        'closedReturnPct': distribution(returns), 'lossReturnPct': distribution([value for value in returns if value < 0]),
        'entryRiskMeaning': 'Quantity*initialDistance, excluding fees and gap risk; varying equity/budgets mean fixed20% capital is not fixed risk.'}
    same(actual, expected, 'Risk statistics')
    require(sum(expected[k] for k in ('tighterThan5', 'equalTo5', 'widerThan5')) == len(bindings), 'Entry width partition')
    return expected


def original_event_membership(evidence, signals):
    events = [e['eventId'] for e in evidence['events']]
    require(len(set(events)) == len(events), 'Duplicate original event')
    membership, reconstructed = {}, []
    for daily in evidence['dailyEvidence']:
        for pair in daily['pairs']:
            eid, day, symbol = daily['eventId'], daily['date'], pair['controlSymbol']
            require(pair['eventId'] == eid and pair['date'] == day and eid in events, 'Original pairing event identity')
            sid = f'matchedControl:{eid}:{day}:{symbol}'
            require(sid not in membership, 'Duplicate original signal')
            membership[sid] = eid
            reconstructed.append({'signalId': sid, 'strategyId': 'relativeRepairEventV1', 'symbol': symbol,
                                  'signalDate': day, 'originalRank': pair['originalRank']})
    same(signals, reconstructed, 'All original pairing signals')
    same(evidence['schedules']['matchedControl'], signals, 'Original event schedule')
    return events, membership


def cohort_metrics(opportunities, closed, opened):
    returns = [r['netReturnPct'] for r in closed]
    pnls = [r['pnlAmount'] for r in closed]
    require(all(finite(v) for v in returns + pnls), 'Unknown closed outcome not supported as zero')
    return {'opportunities': len(opportunities), 'opportunityStatuses': dict(Counter(r['status'] for r in opportunities)),
        'actualEntries': sum(r.get('filledQuantity', 0) > 0 for r in opportunities), 'closedCount': len(closed),
        'wins': sum(v > 0 for v in pnls), 'losses': sum(v < 0 for v in pnls), 'zeros': sum(v == 0 for v in pnls),
        'unknownPnlCount': 0, 'unknownReturnCount': 0,
        'meanClosedNetReturnPct': math.fsum(returns) / len(returns) if returns else None,
        'closedNetPnlAmount': math.fsum(pnls), 'openCountAtCutoff': len(opened),
        'openMarketValue': math.fsum(r['marketValue'] for r in opened),
        'openUnrealizedPnlAmount': math.fsum(r['unrealizedPnlAmount'] for r in opened),
        'realizedPnlWhileStillOpen': math.fsum(r['realizedPnlWhileOpen'] for r in opened),
        'staleMarksAtCutoff': sum(r['markStaleAtCutoff'] for r in opened)}


def audit_attribution(result, summary, signals, event_ids, membership, actual, mode):
    opportunities = result['research']['opportunities']
    buckets = {eid: {'opportunities': [], 'closed': [], 'open': [], 'entryAttributions': []} for eid in event_ids}
    entries = {}
    for op in opportunities:
        eid, sid = membership[op['signalId']], op['signalId']
        buckets[eid]['opportunities'].append(op)
        if op.get('filledQuantity', 0):
            key = op['symbol'], op['attemptDate']
            require(key not in entries, 'Duplicate event entry')
            entries[key] = {'signalId': sid, 'eventId': eid, 'originalRank': op['originalRank']}
            buckets[eid]['entryAttributions'].append({**entries[key], 'symbol': key[0], 'entryDate': key[1]})
    consumed, positions = set(), {}
    for state, records in (('closed', summary['closedEpisodes']['episodes']), ('open', summary['openPositions']['positions'])):
        for record in records:
            key = record['record']['symbol'], record['record']['entryDate']
            require(key in entries and key not in consumed, 'Unmapped event episode')
            consumed.add(key)
            row = {**record, **entries[key]}
            buckets[row['eventId']][state].append(row)
            positions[row['signalId']] = (state, record)
    require(consumed == set(entries), 'Filled opportunity lost from attribution')
    expected_events = {}
    for eid, bucket in buckets.items():
        expected_events[eid] = {**cohort_metrics(bucket['opportunities'], bucket['closed'], bucket['open']),
            'entryAttributions': bucket['entryAttributions'], 'closedEpisodes': bucket['closed'], 'openPositions': bucket['open']}
    same(actual['events'], expected_events, 'Every original event attribution')
    all_closed = [r for b in buckets.values() for r in b['closed']]
    all_open = [r for b in buckets.values() for r in b['open']]
    combined = cohort_metrics(opportunities, all_closed, all_open)
    residual = summary['account']['netPnlAmount'] - math.fsum((combined['closedNetPnlAmount'],
        combined['openUnrealizedPnlAmount'], combined['realizedPnlWhileStillOpen']))
    combined.update(group=mode, reconciliation='passed', accountNetPnlAmount=summary['account']['netPnlAmount'],
        accountReturnPct=summary['account']['netReturnPct'], unallocatedPnlBridgeResidual=residual,
        valuationUnreliableDates=summary['quality']['valuationUnreliableDates'],
        meanReturnBasis='all closed episodes equally weighted; never average event means',
        attributionBasis='entry signal event; open PnL separate from closed net PnL')
    same(actual['combined'], combined, 'Event totals / account bridge')
    same(residual, summary['reconciliation']['episodePnlBridge']['residual'], 'Account/event residual')
    buys = {r['signalId']: r for r in result['trades'] if r['side'] == 'buy'}
    views = {}
    for op in opportunities:
        sid = op['signalId']
        view = {key: op.get(key) for key in ('symbol', 'signalDate', 'originalRank', 'status', 'reason',
            'attemptDate', 'plannedQuantity', 'unfilledQuantity', 'budget', 'reservePrice')}
        view.update(filledQuantity=op.get('filledQuantity', 0), entryFill=None, terminalDisposition='notEntered', episode=None)
        if view['filledQuantity']:
            require(sid in buys and sid in positions, 'Entered path lacks actual fill or episode')
            view['entryFill'] = {key: buys[sid].get(key) for key in ('date', 'price', 'openingPrice', 'quantity', 'amount', 'fee', 'slippage', 'cashAfter')}
            state, item = positions[sid]
            view['terminalDisposition'] = state
            if state == 'closed':
                view['episode'] = {key: item['record'].get(key) for key in ('entryDate', 'exitDate', 'pnlAmount', 'investedAmount', 'holdingDays')}
                view['episode']['netReturnPct'] = item['netReturnPct']
            else:
                view['episode'] = {key: item['record'].get(key) for key in ('entryDate', 'quantity', 'costBasis', 'marketValue', 'priceDate')}
                view['episode'].update(unrealizedPnlAmount=item['unrealizedPnlAmount'], realizedPnlWhileOpen=item['realizedPnlWhileOpen'])
        else:
            require(sid not in buys and sid not in positions, 'Unfilled opportunity fabricated as outcome')
        views[sid] = view
    return views, combined


def audit_path_comparison(baseline, candidate, membership, event_ids, actual):
    require(list(baseline) == list(candidate), 'Path comparison lost signal order')
    labels = ('bothEntered', 'baselineOnlyEntered', 'stressOnlyEntered', 'neitherEntered', 'statusChanged',
              'quantityChanged', 'entryDateChanged', 'entryPriceChanged', 'terminalDispositionChanged',
              'closedExitDateChanged', 'anyRecordedPathChange')
    events = {eid: {'opportunities': 0, 'counts': dict.fromkeys(labels, 0), 'signals': []} for eid in event_ids}
    totals = dict.fromkeys(labels, 0)
    for sid, before in baseline.items():
        after = candidate[sid]
        entered_a, entered_b = bool(before['filledQuantity']), bool(after['filledQuantity'])
        changes = ['bothEntered' if entered_a and entered_b else 'baselineOnlyEntered' if entered_a else
                   'stressOnlyEntered' if entered_b else 'neitherEntered']
        for label, changed in (
            ('statusChanged', before['status'] != after['status']),
            ('quantityChanged', before['filledQuantity'] != after['filledQuantity']),
            ('entryDateChanged', entered_a and entered_b and before['entryFill']['date'] != after['entryFill']['date']),
            ('entryPriceChanged', entered_a and entered_b and before['entryFill']['price'] != after['entryFill']['price']),
            ('terminalDispositionChanged', before['terminalDisposition'] != after['terminalDisposition']),
            ('closedExitDateChanged', before['terminalDisposition'] == after['terminalDisposition'] == 'closed'
                and before['episode']['exitDate'] != after['episode']['exitDate']),
            ('anyRecordedPathChange', before != after)):
            if changed:
                changes.append(label)
        event = events[membership[sid]]
        event['opportunities'] += 1
        for label in changes:
            event['counts'][label] += 1
            totals[label] += 1
        event['signals'].append({'signalId': sid, 'eventId': membership[sid], 'changes': changes,
                                'baseline10': before, 'stressed': after})
    expected = {'baseline': 'fixed5', 'opportunities': len(baseline), 'counts': totals, 'events': events,
        'meaning': 'Fixed signal slots; independent full accounts with only initial stop rule changed.',
        'quantityChangeBasis': 'includes zero-versus-positive entry differences; no forced matched fills'}
    same(actual, expected, 'All fixed-signal execution-path differences')
    return totals


def verify_source_binding(raw_projection, source_binding, source, signals, plan_sha, projection_path):
    require(source_binding['planSha256'] == plan_sha, 'Risk source plan binding')
    same(source_binding['sourceIdentity'], source, 'Risk source original identity')
    require(source_binding['accountDatasetDigest'] == source['accountDatasetDigest'], 'Risk account dataset binding')
    require(pinned(source_binding['output']) == projection_path.resolve(), 'Risk projection path binding')
    require(source_binding['calendarSha256'] == digest(raw_projection['calendar']), 'Risk calendar hash')
    require(source_binding['verification']['passed'] is True and source_binding['declaredActions'] == 0
            and source_binding['membershipProjectionCompared'] is True, 'Risk source capture verification incomplete')
    expected_symbols = sorted({row['symbol'] for row in signals})
    same(source_binding['symbols'], expected_symbols, 'Risk source symbol universe')
    expected_fields = [{'symbol': security['symbol'], 'field': field, 'equal': True, 'sha256': digest(value)}
                       for security in raw_projection['securities'] for field, value in security.items()]
    same(source_binding['fieldComparisons'], expected_fields, 'Every projected field capture hash')
    require(isinstance(source_binding['membershipSourceDigest'], str) and len(source_binding['membershipSourceDigest']) == 64,
            'Missing original membership capture digest')
    return inspect_risk_projection(raw_projection, signals)


def audit_calendar_relationship(account_calendar, pressure_calendar, signals):
    """Warmup coverage may differ; every account slot and prior-21 slot must agree."""
    require(account_calendar and pressure_calendar, 'Empty source calendar')
    require(account_calendar == sorted(set(account_calendar))
            and pressure_calendar == sorted(set(pressure_calendar)), 'Calendar order/duplicates')
    same(account_calendar, [d for d in pressure_calendar if account_calendar[0] <= d <= account_calendar[-1]],
         'Account calendar exact contiguous pressure-calendar range')
    for signal in signals:
        day = signal['signalDate']
        ai, pi = account_calendar.index(day), pressure_calendar.index(day)
        same(account_calendar[max(0, ai - 21):ai], pressure_calendar[max(0, pi - 21):pi],
             'Every signal exact T-21 through T-1 calendar slots')
    return {'accountSessions': len(account_calendar), 'accountStart': account_calendar[0], 'accountEnd': account_calendar[-1],
            'pressureSessions': len(pressure_calendar), 'pressureStart': pressure_calendar[0], 'pressureEnd': pressure_calendar[-1],
            'accountCalendarSha256': digest(account_calendar), 'pressureCalendarSha256': digest(pressure_calendar),
            'exactContiguousRange': True, 'signalWindowComparisons': len(signals),
            'meaning': 'Different retained warmup lengths allowed; all account dates and all used 21-slot windows agree exactly.'}


def audit_account(result, checkpoint, summary, config, source, signals, projection, period, mode, references):
    cost, relative, market, account = references
    same(result['config'], config, 'Unchanged original full config')
    require(result['status'] == 'completed' and result['dataDigest'] == source['accountDatasetDigest']
            and result['fingerprint'] == source['accountSnapshotFingerprint'], 'Original account source/completion')
    identity = result['research']['identity']
    same(identity['signalSchedule'], signals, 'Unchanged complete signal schedule')
    require(identity['signalScheduleSha256'] == digest(signals) and identity['entryWeight'] == .2, 'Signal/weight identity')
    for field in ('actionLedger', 'cashReceivables', 'actionPayables', 'preExRights'):
        require(not result[field], 'Unregistered rights instructions')
    require(result['reservedActionCash'] == 0, 'Unexpected reserved rights cash')
    calendar = projection['calendar']
    target = [day for day in calendar if config['startDate'] <= day <= config['endDate']]
    require(len(target) == (484 if period == '2022-2023' else 485), 'Complete original account calendar')
    closes = {(signal['symbol'], signal['signalDate']): projection['bars'][signal['symbol']][signal['signalDate']]['close'] for signal in signals}
    for fill in result['trades']:
        bar = projection['bars'][fill['symbol']].get(fill['date'])
        require(bar is not None, 'Filled opening absent from saved source')
        same(fill['openingPrice'], bar['open'], 'Source-bound actual opening')
    ledger = cost.verify_fills(result, period, 10)
    execution = account.verify_opportunity_execution(signals, result['research']['opportunities'], result['trades'],
        calendar, start=config['startDate'], end=config['endDate'])
    episodes = account.verify_episodes_and_curve(result, target)
    terminal = account.verify_terminal_account(ledger, result['positions'], final_cash=result['curve'][-1]['cash'],
        final_equity=result['metrics']['finalEquity'], final_curve=result['curve'][-1])
    summary_checks = account.verify_summary(result, summary, market.calendar_intervals(period))
    reserves = cost.verify_reserves(result, closes, period, 10)
    require(summary['policyId'] == mode, 'Summary mode identity')
    require(checkpoint['digest'] == digest({key: value for key, value in checkpoint.items() if key != 'digest'}), 'Checkpoint digest')
    require(checkpoint['schema'] == 'research-volatility-stop-descriptive-snapshot-v1'
            and checkpoint['checkpointRestoreSupported'] is False, 'Checkpoint descriptive scope')
    for field in ('curve', 'trades', 'closedTrades', 'forcedExits', 'pendingTarget', 'actionLedger',
                  'cashReceivables', 'actionPayables', 'preExRights', 'reservedActionCash'):
        same(checkpoint['engineState'][field], result[field], 'Checkpoint/result ' + field)
    same(checkpoint['researchState']['opportunities'], result['research']['opportunities'], 'Checkpoint opportunity lifecycle')
    same(checkpoint['feeState'], result['research']['feeLedger'], 'Checkpoint paid fees')
    same(checkpoint['engineState']['cash'], ledger['cash'], 'Checkpoint cash ledger')
    return {'fills': ledger['fills'], 'fees': ledger['fees'], 'slippage': ledger['slippage'],
            'execution': execution, 'episodes': episodes, 'terminal': terminal, 'summary': summary_checks,
            'reserveChecks': reserves, 'sourceOpeningChecks': len(result['trades'])}


def audit_comparison_row(row, summary, statistics, period, mode, signal_count):
    closed, account, overall = summary['closedEpisodes'], summary['account'], summary['periods'][0]
    labels = {'fixed5': '固定5%初始止损', 'meanTR20x2': '入场冻结2倍20日平均真实波幅'}
    require(finite(row['elapsedSeconds']) and row['elapsedSeconds'] >= 0, 'Invalid elapsed runtime')
    expected = {'id': mode, 'label': labels[mode], 'signals': signal_count, 'closed': closed['count'],
        'winRatePct': closed['winRatePct'], 'meanWinPct': closed['meanWinReturnPct'],
        'meanLossPct': closed['meanLossReturnPct'], 'closedMeanPct': closed['meanReturnPct'],
        'payoffRatio': closed['payoffRatio'], 'profitFactor': closed['profitFactor'],
        'holdingSessions': closed['holdingSessions'], 'maxConsecutiveLosses': closed['maxConsecutiveLosses'],
        'accountReturnPct': account['netReturnPct'], 'maxDrawdownPct': account['maxDrawdownPct'],
        'meanExposurePct': overall['exposurePct']['mean'], 'openPositions': summary['openPositions']['count'],
        'unreliableValuationDays': len(summary['quality']['valuationUnreliableDates']),
        'top5ShareOfGrossProfitPct': summary['concentration']['top5ProfitSharePct'],
        'years': [{'id': p['id'], 'closed': p['closedEpisodes']['count'], 'closedMeanPct': p['closedEpisodes']['meanReturnPct'],
                   'accountReturnPct': p['returnPct'], 'maxDrawdownPct': p['maxDrawdownPct']}
                  for p in summary['periods'] if p['kind'] == 'calendarYear'],
        'period': period, 'riskStatistics': statistics, 'elapsedSeconds': row['elapsedSeconds']}
    same(row, expected, 'Comparison row ' + period + '/' + mode)


def verify(directory):
    started = time.perf_counter()
    require(not (directory / 'validation.json').exists(), 'Refusing to overwrite validation.json')
    require(not (directory / 'failure.json').exists(), 'Runner failure present')
    plan_path, compare_path = directory / 'plan.json', directory / 'comparison.json'
    plan, comparison = read(plan_path), read(compare_path)
    plan_sha = sha(plan_path)
    require(plan['schema'] == 'relative-repair-volatility-stop-v1'
            and comparison['schema'] == 'relative-repair-volatility-comparison-v1', 'Experiment schema')
    frozen = {}
    for category in ('sourcePins', 'frozenInputs'):
        for name, expected in plan[category].items():
            require(sha(local(name)) == expected, 'Registered file changed: ' + name)
            frozen[name] = expected
    require(plan['sourcePins']['service/research_volatility_stop.py'] == ADAPTER_SHA, 'Unreviewed adapter')
    require(plan['modes'] == list(MODES) and set(plan['periods']) == set(PERIODS), 'Modes/periods changed')
    require(plan['newCandidateVariants'] == comparison['candidateVariants'] == 1
            and plan['newCandidateAccounts'] == comparison['candidateAccounts'] == 2
            and plan['fixed5EquivalenceReplays'] == comparison['fixed5EquivalenceReplays'] == 2
            and plan['accountsNewlyExecuted'] == comparison['newAccounts'] == 4, 'Wrong run counts')
    require(plan['parameterSearch'] is False and plan['profitHurdles'] is None and plan['holdoutClaim'] is False
            and plan['entryWeight'] == .2, 'Unregistered selection/scope')
    require(plan['rule']['windowSlots'] == 21 and plan['rule']['trueRanges'] == 20
            and plan['rule']['multiplier'] == 2 and plan['rule']['fallbackPct'] == .05, 'Unregistered stop rule')
    require(comparison['planSha256'] == plan_sha and comparison['certified'] is False
            and comparison['targetAchieved'] is False, 'Comparison plan/certification')
    inputs = read(directory / 'input-completion.json')
    require(inputs['planSha256'] == plan_sha and set(inputs['outputs']) == set(PERIODS), 'Input completion binding')
    require(read(directory / 'run-start.json')['planSha256'] == plan_sha, 'Run start binding')
    event_path = pinned(comparison['eventOutcomes'])
    require(event_path == (directory / 'event-outcomes.json').resolve(), 'Wrong event attribution path')
    event_data = read(event_path)
    require(event_data['schema'] == 'volatility-stop-event-outcomes-v1' and event_data['planSha256'] == plan_sha
            and event_data['certified'] is False and set(event_data['periods']) == set(PERIODS), 'Event schema/binding')
    require([(r['period'], r['id']) for r in comparison['rows']] == [(p, m) for p in PERIODS for m in MODES], 'All four comparison rows/order')
    rows = {(r['period'], r['id']): r for r in comparison['rows']}
    cost = cost_reference()
    report = {'schema': 'relative-repair-volatility-stop-validation-v1', 'passed': True, 'status': 'passed',
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(Path(__file__)),
        'planSha256': plan_sha, 'comparisonSha256': sha(compare_path), 'eventOutcomesSha256': sha(event_path),
        'frozenInputsAndCode': frozen, 'artifacts': comparison['artifacts'], 'reusedVerifier': {COST_REFERENCE: COST_REFERENCE_SHA},
        'periods': {}, 'checks': [], 'certified': False, 'targetAchieved': False}
    expected_files = {'volatility-input.json.gz', 'signals.json', 'risk-source.json.gz', 'risk-source-binding.json',
                      'source.json', 'fixed5-equivalence.json'}
    expected_files |= {mode + suffix for mode in MODES for suffix in
                       ('.result.json.gz', '.checkpoint.json.gz', '.summary.json', '.risk-statistics.json')}
    for period in PERIODS:
        spec, folder = plan['periods'][period], directory / period
        require(set(comparison['artifacts'][period]) == expected_files, 'Period artifact coverage')
        for name, expected in comparison['artifacts'][period].items():
            require(sha(folder / name) == expected, 'Run artifact changed: ' + period + '/' + name)
        baseline_paths = {key: pinned(value) for key, value in spec['baseline'].items()}
        require((folder / 'signals.json').read_bytes() == baseline_paths['signals'].read_bytes(), 'Original signal bytes changed')
        signals, source, prior = read(folder / 'signals.json'), read(folder / 'source.json'), read(pinned(spec['priorPressure']))
        require(len(signals) == COUNT[period], 'Original opportunity count')
        same(source, spec['sourceIdentity'], 'Registered source identity')
        same(source, read(baseline_paths['source']), 'Baseline source identity')
        prior_binding = read(pinned(spec['pressureBinding']))
        require(prior_binding['verification']['passed'] is True and prior_binding['output']['sha256'] == spec['priorPressure']['sha256'], 'Prior source binding')
        input_path = pinned(inputs['outputs'][period])
        require(input_path == (folder / 'volatility-input.json.gz').resolve(), 'Wrong prepared input')
        compact, tr_counts = audit_tr_inputs(read(input_path), prior, signals, prior['sourceCalendar'], plan_sha, spec['priorPressure'])
        risk_path = folder / 'risk-source.json.gz'
        projection = verify_source_binding(read(risk_path), read(folder / 'risk-source-binding.json'), source,
                                            signals, plan_sha, risk_path)
        calendar_relationship = audit_calendar_relationship(projection['calendar'], prior['sourceCalendar'], signals)
        # The retained TR slots must also match this independent account projection.
        for item in read(input_path)['rows']:
            same(item['windowBars'], [projection['bars'][item['symbol']].get(day) for day in item['windowDates']], 'TR/account projection bar identity')
        evidence = read(pinned(spec['signalsEvidence']))
        event_ids, membership = original_event_membership(evidence, signals)
        require(len(event_ids) == (4 if period == '2022-2023' else 5), 'All original events')
        period_events = event_data['periods'][period]
        require([e['eventId'] for e in period_events['events']] == event_ids, 'Event metadata coverage/order')
        old_event_path = baseline_paths['result'].parent.parent / 'event-outcomes.json'
        old_event_rel = old_event_path.relative_to(ROOT).as_posix()
        require(frozen.get(old_event_rel) == sha(old_event_path), 'Original event metadata not pinned')
        old_events = read(old_event_path)['periods'][period]
        same(period_events['events'], [{**event, 'groups': {}} for event in old_events['events']], 'Inherited unchanged event metadata')
        references = (cost, *cost.reference_modules(period))
        report_period = {'signals': len(signals), 'events': event_ids, 'trInputs': tr_counts,
                         'sourceProjection': projection['counts'], 'calendarRelationship': calendar_relationship, 'accounts': {}}
        views = {}
        for mode in MODES:
            result, checkpoint, summary = (read(folder / (mode + suffix)) for suffix in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json'))
            if mode == 'fixed5':
                baseline = read(baseline_paths['result'])
                clean = deepcopy(result)
                require(clean['versions'].pop('researchVolatilityStop') == 'research-volatility-stop-v1', 'Replay extra version')
                require(clean['research'].pop('volatilityStop')['mode'] == 'fixed5', 'Replay extra mode')
                require(clean == baseline, 'Complete fixed5 replay differs beyond two allowed metadata paths')
                expected_equivalence = {'passed': True, 'completeResultEqual': True,
                    'removedPathsOnly': ['versions.researchVolatilityStop', 'research.volatilityStop'],
                    'originalDigest': digest(baseline), 'normalizedReplayDigest': digest(clean)}
                same(read(folder / 'fixed5-equivalence.json'), expected_equivalence, 'Full replay equivalence proof')
                report_period['fixed5Equivalence'] = expected_equivalence
                del baseline, clean
            accounting = audit_account(result, checkpoint, summary, spec['config'], source, signals, projection, period, mode, references)
            risk = audit_daily_risk(result, checkpoint, projection, compact, mode)
            statistics = audit_risk_statistics(result, summary, read(folder / (mode + '.risk-statistics.json')))
            audit_comparison_row(rows[period, mode], summary, statistics, period, mode, len(signals))
            views[mode], combined = audit_attribution(result, summary, signals, event_ids, membership,
                                                     period_events['accounts'][mode], mode)
            if mode == 'fixed5':
                for event in old_events['events']:
                    same(period_events['accounts'][mode]['events'][event['eventId']], event['groups']['matchedControl'], 'Original fixed5 event attribution preserved')
            report_period['accounts'][mode] = {'accounting': accounting, 'dailyRisk': risk, 'eventAccountBridge': combined}
            print(json.dumps({'period': period, 'mode': mode, 'passed': True, 'fills': accounting['fills'],
                              'heldDays': risk['heldDays'], 'entries': risk['actualEntries']}), flush=True)
        report_period['pathComparisonCounts'] = audit_path_comparison(views['fixed5'], views['meanTR20x2'], membership,
                                                                     event_ids, period_events['pathComparison'])
        report['periods'][period] = report_period
    report['checks'] = ['Frozen plan/code/input/output bindings and all 1189 original signals/ranks',
        'Independent 21-calendar-slot/20-TR mean, missing fallback and saved account-source consistency',
        'Both fixed5 replays equal entire original result after only two registered metadata removals',
        'Four complete accounts: dated fees/slippage, actual source openings, cash/shares/T+1, budgets, episodes, equity and summaries',
        'Every actual-entry stop percentage, effective distance, risk amount and fallback reason',
        'All held-day risk observations independently reconstructed from actual fills and saved price/eligibility/reference inputs',
        'Initial stop / trailing / exact fifth-session precedence, unknown checks, pending persistence and executed evidence',
        'Risk statistics, all nine entry-event attributions and full account PnL bridges',
        'Every fixed-signal execution-path difference and event partition']
    report['scope'] = {'accountsNewlyExecutedByRunner': 4, 'candidateAccounts': 2, 'equivalenceReplays': 2,
        'accountsExecutedByVerifier': 0, 'signals': 1189, 'events': 9, 'rawMarketReloaded': False,
        'riskAdapterImported': False, 'riskRuleFunctionsReused': False,
        'limitations': ['Source field capture comparison inherits the pinned runner; auditor inspects the saved subset, not full original raw input.',
            'Fill arithmetic and risk triggers are independently checked; all order-gate/capacity outcomes are not separately resimulated.',
            'Raw-price, survivor, incomplete rights/history and simulated opening-execution limitations remain inherited.',
            'Both periods previously inspected; passing correctness audit does not certify profitable/stable strategy.']}
    report['elapsedSeconds'] = time.perf_counter() - started
    with (directory / 'validation.json').open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed': True, 'output': (directory / 'validation.json').relative_to(ROOT).as_posix(),
                      'sha256': sha(directory / 'validation.json'), 'elapsedSeconds': report['elapsedSeconds']}), flush=True)


def self_test():
    calendar = [f'2020-01-{d:02d}' for d in range(1, 24)]
    days = calendar[:21]
    bars = [{'date': day, 'close': 100., 'high': 102., 'low': 98., 'volume': 0., 'open': -1.} for day in days]
    answer = prior_tr20(days, bars, calendar[21], calendar)
    same(answer['meanTR20'], 4., 'Manual constant TR')
    same(answer['sumTR'], 80., 'Twenty TR sum')
    require(answer['valid'] and len(answer['ranges']) == 20, '21 bars produce exactly 20 ranges; volume/open irrelevant')
    changed = deepcopy(bars)
    changed[1].update(high=110., low=108.)
    gap = prior_tr20(days, changed, calendar[21], calendar)
    same(gap['ranges'][0]['trueRange'], 10., 'Gap TR includes previous close')
    same(gap['meanTR20'], 4.3, 'Unweighted mean with one gap')
    missing = deepcopy(bars)
    missing[5] = None
    result = prior_tr20(days, missing, calendar[21], calendar)
    require(result['valid'] is False and result['meanTR20'] is None and result['ranges'] is None, 'Missing exact slot stays unknown')
    short = prior_tr20(calendar[:10], bars[:10], calendar[10], calendar)
    require(short['valid'] is False and short['issues'][0]['reason'] == 'insufficientCalendarSlots', 'Short warmup unknown')
    invalid = deepcopy(bars)
    invalid[10]['high'] = 90.
    require(prior_tr20(days, invalid, calendar[21], calendar)['valid'] is False, 'Inverted range invalid')
    invalid = deepcopy(bars)
    invalid[0]['close'] = False
    require(prior_tr20(days, invalid, calendar[21], calendar)['valid'] is False, 'Bool close invalid')
    prior_only = deepcopy(bars)
    prior_only[0].update(high=None, low=None)
    require(prior_tr20(days, prior_only, calendar[21], calendar)['valid'], 'First slot only supplies previous close')
    for dates in (calendar[1:22], calendar[:20]):
        try:
            prior_tr20(dates, bars[:len(dates)], calendar[21], calendar)
        except ValueError:
            pass
        else:
            raise ValueError('Signal-day inclusion or compressed window accepted')
    frozen = frozen_stop(4., 100., 1000, 'meanTR20x2')
    same(frozen, {'effectivePct': .08, 'entryDistance': 8., 'entryRiskAmount': 8000.,
                  'fallback': False, 'proposedDistance': 8.}, 'Actual-entry frozen stop')
    same(50. * frozen['effectivePct'], 4., 'Preclose scaling changes distance proportionately')
    for value in (None, 0., -1., 50., 60.):
        fallback = frozen_stop(value, 100., 1000, 'meanTR20x2')
        require(fallback['fallback'] is True and fallback['effectivePct'] == .05, 'Invalid distance must fallback')
    require(frozen_stop(None, 100., 1000, 'fixed5')['fallback'] is False, 'Fixed5 is a control, not a fallback')
    projection = {'calendar': calendar, 'securities': [{'symbol': 's', 'bars': bars,
                  'stByDate': {calendar[0]: False}}], 'membershipByDate': {calendar[0]: ['s']}}
    signals = [{'symbol': 's', 'signalDate': calendar[21]}]
    same(inspect_risk_projection(projection, signals)['counts']['bars'], 21, 'Projection bar count')
    bad = deepcopy(projection)
    bad['securities'][0]['bars'].append(deepcopy(bars[0]))
    try:
        inspect_risk_projection(bad, signals)
    except ValueError:
        pass
    else:
        raise ValueError('Duplicate projection bar accepted')
    same(inspect_risk_projection(projection, signals)['membershipByDate'], projection['membershipByDate'], 'Global membership retained')
    audit_calendar_relationship(calendar[1:], calendar, [{'signalDate': calendar[22]}])
    try:
        audit_calendar_relationship(calendar[2:], calendar, [{'signalDate': calendar[22]}])
    except ValueError:
        pass
    else:
        raise ValueError('Different effective prior-21 window accepted')
    daily_checks = self_test_daily_risk()
    print(json.dumps({'passed': True, 'checks': ['21 slots / 20 simple TRs', 'manual sum/mean',
        'previous-close gap TR', 'missing/short/invalid unknown', 'open/volume not extra gates',
        'first slot supplies close only', 'T inclusion/compression rejected', 'entry percentage frozen',
        'reference scaling', 'missing/zero/negative/excess distance fallback', 'fixed5 no fallback label',
        'projection identity and duplicate-bar check', 'global membership retained',
        'different warmup accepted only with identical used windows', *daily_checks]}))


def self_test_daily_risk():
    """Hand-specified expected daily paths; no engine/adapter execution."""
    from datetime import timedelta
    calendar = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(40)]
    signal_day, entry_day = calendar[21:23]
    compact = {'s1': {'signalId': 's1', 'symbol': 's', 'signalDate': signal_day,
                       'asOfDate': calendar[20], 'meanTrueRange20': 4.}}

    def fixture(stages, mode='meanTR20x2', extra_security=None, memberships=None):
        # Each stage declares the expected peak/reference/status/trigger independently.
        inputs = normalized_inputs(compact, calendar)
        pct = .08 if mode == 'meanTR20x2' else .05
        binding = {'signalId': 's1', 'symbol': 's', 'signalDate': signal_day, 'entryDate': entry_day,
            'entryFillId': 'b1', 'entryQuantity': 100, 'mode': mode, 'asOfDate': calendar[20],
            'meanTrueRange20': 4., 'inputMeanTrueRange20': 4., 'entryRiskPrice': 100., 'entryFillPrice': 100.,
            'entryDistance': 8. if mode == 'meanTR20x2' else 5.,
            'entryInitialRiskAmount': 800. if mode == 'meanTR20x2' else 500.,
            'effectivePct': pct, 'fallback': False, 'fallbackReason': None, 'effectiveMode': mode}
        bars = [{'date': d, 'open': 100., 'close': 100., 'high': 104., 'low': 96., 'volume': 100.} for d in calendar[:22]]
        for i, stage in enumerate(stages):
            if stage.get('missing'):
                continue
            close = stage['close']
            bars.append({'date': calendar[i + 22], 'open': close, 'close': close,
                         'high': close, 'low': close, 'volume': stage.get('volume', 100.)})
        sec = {'symbol': 's', 'bars': bars, **(extra_security or {})}
        raw = {'calendar': calendar, 'securities': [sec], 'membershipByDate': memberships or {}}
        projection = inspect_risk_projection(raw, list(compact.values()))
        observed, structural, decisions, pending, daily = [], [], [], {}, []
        for i, stage in enumerate(stages):
            day, age = calendar[i + 22], i + 1
            close, peak, risk = stage.get('close'), stage['peak'], stage.get('risk', 100.)
            terms = {**binding, 'currentRiskEntryPrice': risk, 'currentRiskDistance': risk * pct,
                     'threshold': risk * (1 - pct), 'referenceConversion': REFERENCE_CONVERSION}
            previous = pending.get('s')
            fifth = None
            if age == 5:
                fifth = {'date': day, 'symbol': 's', 'rule': 'noProgress5', 'entryDate': entry_day,
                    'holdingDays': 5, 'observedClose': close, 'riskEntryPrice': risk,
                    'priceReferenceMeaning': 'entryFillPriceIncludingSlippageExcludingFees'}
                if mode == 'meanTR20x2':
                    fifth['volatilityStop'] = terms
                if previous or stage.get('reason') not in (None, 'noProgress5'):
                    fifth.update(status='skippedExistingExit',
                        existingReason=previous['reason'] if previous else stage['reason'],
                        existingSignalDate=previous['signalDate'] if previous else day)
                else:
                    fifth.update(status=stage['fifth'], comparison='fifthSessionCloseLessThanOrEqualRiskEntryPrice',
                        unknown=[{'date': day, 'reason': 'missingOrInvalidFifthSessionPriceOrVolume'}] if stage['fifth'] == 'unknown' else [],
                        retryOnLaterSession=False)
                structural.append(fifth)
            if stage.get('reason'):
                reason = stage['reason']
                if reason == 'noProgress5':
                    evidence = fifth
                else:
                    evidence = {'observedDate': day, 'observedClose': close, 'riskEntryPrice': risk, 'closePeak': peak,
                        'gainPct': close / risk - 1 if close is not None else None,
                        'drawdownFromPeakPct': close / peak - 1 if close is not None else None,
                        'holdingDays': age, 'stopLossPct': pct, 'takeProfitPct': 0.,
                        'trailingStopPct': .1, 'maxHoldingDays': 0, 'percentageUnit': 'ratio'}
                    if mode == 'meanTR20x2':
                        evidence.update(configuredFallbackStopLossPct=.05, volatilityStop=terms)
                pending['s'] = {'signalDate': day, 'reason': reason, 'riskEvidence': evidence}
                decisions.append({'date': day, 'symbol': 's', 'action': 'sell', 'status': 'queued', 'reason': reason,
                                  'signalDate': day, 'observedClose': close, 'riskEvidence': evidence})
            order = pending.get('s')
            observed.append({**terms, 'date': day, 'holdingDays': age, 'quantity': 100,
                'observedClose': close, 'barAvailableForPriceRisk': stage.get('known', True),
                'closePeak': peak, 'status': stage['status'], 'exitReason': order['reason'] if order else None,
                'exitSignalDate': order['signalDate'] if order else None,
                'existingExitReason': previous['reason'] if previous else None})
            daily.append({'date': day, 'holdings': [{'symbol': 's', 'quantity': 100}]})
        detail = {'version': 'research-volatility-stop-v1', 'mode': mode, 'checkpointRestoreSupported': False,
            'inputs': inputs, 'inputSha256': digest(inputs), 'entryBindings': [binding], 'observations': observed,
            'observationCounts': dict(Counter(r['status'] for r in observed)), 'fallbackEntryCount': 0}
        structural_detail = {'observations': structural,
            'observationCounts': {k: sum(r['status'] == k for r in structural) for k in ('triggered', 'notTriggered', 'unknown', 'skippedExistingExit')}}
        result = {'research': {'volatilityStop': detail, 'structuralExits': structural_detail},
            'trades': [{'side': 'buy', 'signalId': 's1', 'symbol': 's', 'signalDate': signal_day,
                        'date': entry_day, 'fillId': 'b1', 'quantity': 100, 'price': 100.}],
            'config': {'universe': {'excludeSt': True}, 'takeProfitPct': 0., 'trailingStopPct': .1, 'maxHoldingDays': 0},
            'daily': daily, 'decisions': decisions, 'forcedExits': pending,
            'positions': [{'symbol': 's', 'entryDate': entry_day, 'quantity': 100,
                           'riskEntryPrice': stages[-1].get('risk', 100.), 'closePeak': stages[-1]['peak']}]}
        checkpoint = {'currentPositionStops': {'s': binding}, 'volatilityStop': detail, 'structuralExits': structural_detail}
        audit_daily_risk(result, checkpoint, projection, compact, mode)
        return result, checkpoint, projection

    fixture([{'close': 94., 'peak': 100., 'status': 'notTriggered'}])
    fixture([{'close': 94., 'peak': 100., 'status': 'triggered', 'reason': 'stopLoss'}], mode='fixed5')
    fixture([{'close': 91., 'peak': 100., 'status': 'triggered', 'reason': 'stopLoss'},
             {'close': 110., 'volume': 0., 'peak': 100., 'known': False, 'status': 'skippedExistingExit'}])
    fixture([{'close': 120., 'peak': 120., 'status': 'notTriggered'},
             {'close': 107., 'peak': 120., 'status': 'triggered', 'reason': 'trailingStop'}])
    fixture([{'close': 91., 'peak': 100., 'status': 'triggered', 'reason': 'baseEligibilityLost'}],
        extra_security={'memberHistory': {entry_day: True}, 'delistDate': entry_day, 'stByDate': {entry_day: True}},
        memberships={entry_day: []})
    fixture([{'close': 100., 'peak': 100., 'status': 'notTriggered'},
             {'close': 47., 'risk': 50., 'peak': 50., 'status': 'notTriggered'},
             {'close': 45., 'risk': 50., 'peak': 50., 'status': 'triggered', 'reason': 'stopLoss'}],
        extra_security={'precloseByDate': {calendar[23]: 50.}})
    first = [{'close': 104., 'peak': 104., 'status': 'notTriggered'} for _ in range(4)]
    fixture(first + [{'close': 99., 'peak': 104., 'status': 'triggered', 'fifth': 'triggered', 'reason': 'noProgress5'}])
    fixture(first + [{'close': 99., 'volume': 0., 'peak': 104., 'status': 'unknown', 'known': False, 'fifth': 'unknown'},
                     {'close': 99., 'peak': 104., 'status': 'notTriggered'}])
    fixture(first + [{'close': 91., 'peak': 104., 'status': 'triggered', 'reason': 'stopLoss'}])
    result, checkpoint, projection = fixture([{'close': 94., 'peak': 100., 'status': 'notTriggered'}])
    result['research']['volatilityStop']['observations'][0]['threshold'] = 95.
    try:
        audit_daily_risk(result, checkpoint, projection, compact, 'meanTR20x2')
    except ValueError:
        pass
    else:
        raise ValueError('Risk observation corruption accepted')
    return ['dynamic versus fixed stop boundary', 'pending evidence retained across unknown bar',
            'trailing trigger', 'global membership precedence over local/delist/ST/price',
            'preclose scales frozen percentage without TR reset', 'exact fifth-session trigger',
            'unknown fifth session never retried', 'standard stop precedes fifth-session exit',
            'deliberate risk evidence corruption rejected']


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        try:
            verify(args.directory.resolve())
        except Exception as exc:
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            failure = {'schema': 'independent-volatility-stop-audit-attempt-v1', 'passed': False,
                'timestamp': stamp, 'verifierSha256': sha(Path(__file__)), 'error': str(exc),
                'traceback': traceback.format_exc(), 'changedProductionArtifacts': False}
            for filename in ('plan.json', 'comparison.json', 'event-outcomes.json'):
                path = args.directory / filename
                if path.exists():
                    failure[filename + 'Sha256'] = sha(path)
            path = args.directory / ('validation-attempt-' + stamp + '.json')
            with path.open('x', encoding='utf-8') as stream:
                json.dump(failure, stream, ensure_ascii=False, indent=2, allow_nan=False)
            print(json.dumps({'passed': False, 'attempt': str(path), 'error': str(exc)}), flush=True)
            raise
