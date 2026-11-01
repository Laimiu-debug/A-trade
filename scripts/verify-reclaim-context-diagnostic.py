"""Independent saved-evidence audit of undercut/reclaim context attribution.

No production diagnostic calculations are imported, accounts rerun, or raw
market source reloaded. The prior complete account audit is inherited by hash.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/undercut-reclaim-context-diagnostic-v1'
PARENT = ROOT / 'artifacts/strategy-search/strong-undercut-reclaim-timing-v1'
PERIODS = ('2022-2023', '2024-2025')
POLICIES = ('immediate', 'delay2', 'confirm2')
STATES = ('majorityImproving', 'majorityNotImproving', 'notMajorityImproving', 'notMajorityNotImproving', 'unknown')
REGISTERED_PLAN_SHA = 'c7b19a44d4a69f4b1bebc0ce47178bdf199b0c47a45bae8213083a0bf932985f'
MODULE_SHA = '09077584dae5720af8c965bb8b14c4a7e3d79e9b985454df36338f4f10278ed5'
PARENT_VALIDATION_SHA = '74e1e2eea635291342d615e702ae212fcb427303f2328bec142b33edc6400126'
PARENT_READOUT_SHA = 'b58f557029781de669ed7aa3c250c35b339c760250af7c3ec96109580cf25a90'
SPLIT = '2024-09-24'
MARKETS = {'2022-2023': 'market-state-panel-v1', '2024-2025': 'supplemental-market-state-panel-v1'}
MARKET_SHA = {'2022-2023': 'a006ca570b55954d9b6b3db0a7048ad07c843890a2f2e473afb7ab98574b51e5',
              '2024-2025': 'fc7ac704bd48925ca89d4a61888d81639a76977881494fa6d59720e46594991f'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def equal(actual, expected, label='root'):
    if finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8),
                f'{label}: {actual!r} != {expected!r}')
    elif isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), f'{label}: keys differ')
        for key in expected:
            equal(actual[key], expected[key], label + '.' + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), f'{label}: lengths differ')
        for index, (a, b) in enumerate(zip(actual, expected)):
            equal(a, b, f'{label}[{index}]')
    else:
        require(type(actual) is type(expected) and actual == expected, f'{label}: literal differs')


def read(path):
    path = Path(path)
    with (gzip.open(path, 'rt', encoding='utf-8') if path.name.endswith('.gz') else path.open(encoding='utf-8')) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def local(name):
    path = Path(name)
    require(not path.is_absolute() and '..' not in path.parts, 'Expected repository-relative path')
    path = (ROOT / path).resolve()
    require(path.is_relative_to(ROOT.resolve()), 'Evidence outside repository')
    return path


def pin(reference):
    require(isinstance(reference, dict) and set(reference) == {'path', 'sha256'}, 'Reference schema')
    path = local(reference['path'])
    require(sha(path) == reference['sha256'], 'Pinned evidence changed: ' + reference['path'])
    return path


def ratio(a, b, factor=1.):
    return a / b * factor if b else None


def avg(values):
    return math.fsum(values) / len(values) if values else None


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    place = (len(ordered) - 1) * p
    lo, hi = math.floor(place), math.ceil(place)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (place - lo)


def independent_state(market_day):
    """Use a single matched intersection, never the broader price20 denominator."""
    if market_day is None:
        return None, 'missingPriorMarketDate'
    section = market_day.get('matched5')
    if not isinstance(section, dict) or section.get('available') is not True:
        return None, 'matchedComparisonUnavailable'
    n = section.get('pairedCount')
    if type(n) is not int or n <= 0:
        return None, 'emptyMatchedPopulation'
    today, earlier, change = (section.get(key) for key in
        ('positiveBreadthToday', 'positiveBreadthEarlier', 'matchedBreadthChange5'))
    if not all(finite(value) for value in (today, earlier, change)):
        return None, 'unknownMatchedValues'
    require(0 <= today <= 1 and 0 <= earlier <= 1, 'Matched breadth outside probability bounds')
    equal(change, 100 * (today - earlier), 'Matched breadth paired-denominator arithmetic')
    return (today > .5, change > 0), None


def prior_context(formations, calendar, market_rows):
    """Return all formations, including unavailable first target-session context."""
    require(calendar == sorted(set(calendar)), 'Source calendar order')
    by_day = {r['date']: r for r in market_rows}
    require(len(by_day) == len(market_rows), 'Duplicate market daily row')
    indexes = {d: i for i, d in enumerate(calendar)}
    output = []
    for formation in formations:
        day = formation['formationDate']
        require(day in indexes, 'Formation outside source calendar')
        i = indexes[day]
        prior = calendar[i - 1] if i else None
        require(formation['priorDate'] == prior, 'Formation priorDate is not exact preceding source slot')
        state, reason = independent_state(by_day.get(prior))
        output.append({'formationId': formation['formationId'], 'formationDate': day,
                       'priorDate': prior, 'quadrant': state, 'unknownReason': reason})
    return output


def context_reference(calendar, market_rows, formation_dates):
    require(calendar and calendar == sorted(set(calendar)), 'Source calendar order/coverage')
    require(all(type(d) is str and date.fromisoformat(d).isoformat() == d for d in calendar), 'Source date identity')
    at = {d: i for i, d in enumerate(calendar)}
    require(len(formation_dates) == len(set(formation_dates)) and set(formation_dates) <= set(at), 'Formation dates invalid')
    by_day = {r['date']: r for r in market_rows}
    require(len(by_day) == len(market_rows) and set(by_day) <= set(at), 'Duplicate/offcalendar market rows')
    value_fields = ('positiveBreadthToday', 'positiveBreadthEarlier', 'matchedBreadthChange5',
                    'pairedCoverageToday', 'pairedCoverageEarlier', 'pairedCoverageFixed')

    def count(value):
        if not finite(value):
            return None
        require(type(value) is int and value >= 0, 'Count is not a nonnegative integer')
        return value

    output = []
    for day in sorted(formation_dates):
        i = at[day]
        previous, earlier = (calendar[i - n] if i >= n else None for n in (1, 6))
        item = {'formationDate': day, 'asOfDate': previous, 'earlierDate': earlier,
                'state': 'unknown', 'status': 'unknown', 'reason': None, 'pairedCount': None,
                **dict.fromkeys(value_fields)}
        source = by_day.get(previous)
        matched = source.get('matched5') if source is not None else None
        if previous is None:
            item['reason'] = 'asOfSessionUnavailable'
        elif source is None:
            item['reason'] = 'marketRowUnavailable'
        elif matched is None:
            item['reason'] = 'matchedEvidenceUnavailable'
        else:
            require(isinstance(matched, dict), 'Matched record schema')
            e = matched.get('earlierDate')
            require(e is None or e == earlier, 'Market earlier date is not exact T-6')
            n = item['pairedCount'] = count(matched.get('pairedCount'))
            for denominator in (source.get('fixedPopulation'), matched.get('todayEvaluableCount'), matched.get('earlierEvaluableCount')):
                denominator = count(denominator)
                require(n is None or denominator is None or n <= denominator, 'Intersection exceeds supplied denominator')
            for field in value_fields:
                value = matched.get(field)
                if finite(value):
                    bounds = (-100, 100) if field == 'matchedBreadthChange5' else (0, 1)
                    require(bounds[0] <= value <= bounds[1], 'Matched field domain: ' + field)
                    item[field] = value
            today, past, change = (item[k] for k in value_fields[:3])
            if all(v is not None for v in (today, past, change)):
                require(abs(change - 100 * (today - past)) <= 1e-9, 'Matched same-denominator change')
            failures = [(earlier is None, 'earlierSessionUnavailable'),
                (matched.get('available') is not True, 'matchedComparisonUnavailable'),
                (n is None, 'pairedCountUnavailable'), (n == 0, 'emptyMatchedPopulation'),
                (e is None, 'matchedEarlierDateUnavailable'),
                (any(v is None for v in (today, past, change)), 'matchedValuesUnavailable')]
            reason = next((message for failed, message in failures if failed), None)
            if reason is not None:
                item['reason'] = reason
            else:
                item.update(state=STATES[(0 if today > .5 else 2) + (0 if change > 0 else 1)],
                            status='known', reason='classified')
        output.append(item)
    return output


def episode_indexes(result, summary):
    buys = {r['signalId']: r for r in result['trades'] if r['side'] == 'buy'}
    require(len(buys) == sum(r['side'] == 'buy' for r in result['trades']), 'Duplicate buy signal identity')
    closed = {(r['symbol'], r['entryDate']): r for r in result['closedTrades']}
    opened = {(r['record']['symbol'], r['record']['entryDate']): r for r in summary['openPositions']['positions']}
    require(len(closed) == len(result['closedTrades']) and len(opened) == summary['openPositions']['count'],
            'Duplicate closed/open entry identity')
    require(not (closed.keys() & opened.keys()), 'Position simultaneously closed and open')
    require(set(closed) | set(opened) == {(r['symbol'], r['date']) for r in buys.values()},
            'Buys/closed/open identities differ')
    curve_days = [r['date'] for r in result['curve']]
    raw_open = {(r['symbol'], r['entryDate']): r for r in result['positions']}
    require(set(opened) == set(raw_open), 'Saved summary open positions differ from result')
    for identity, item in opened.items():
        raw = raw_open[identity]
        equal(item['record'], raw, 'Saved open record')
        equal(item['unrealizedPnlAmount'], raw['marketValue'] - raw['costBasis'], 'Open mark less cost')
        equal(item['realizedPnlWhileOpen'], raw['realizedPnl'], 'Realized partial open PnL')
        require(item['observedHoldingSessions'] == sum(d >= raw['entryDate'] for d in curve_days), 'Open holding age')
        require(item['markStaleAtCutoff'] is (raw['priceDate'] != curve_days[-1]), 'Stale terminal mark flag')
    return buys, closed, opened


def independent_record(period, policy, formation, state, result_opportunity, buy, closed_episode, open_episode):
    disposition = formation['accountDispositions'][policy]
    scheduled = formation['signals'][policy] is not None
    require(scheduled == (result_opportunity is not None), 'Schedule/account join changed')
    if scheduled:
        require(disposition['signalId'] == formation['formationId'] == result_opportunity['signalId'], 'Account signal ID mismatch')
        require(disposition['signalDate'] == formation['signals'][policy]['signalDate'] == result_opportunity['signalDate'],
                'Actual policy decision date changed')
        require(disposition['reason'] == result_opportunity['reason'] and disposition['status'] == result_opportunity['status'],
                'Readout disposition reason/status mismatch')
        equal(disposition['filledQuantity'], result_opportunity.get('filledQuantity'), 'Readout filled quantity')
    else:
        require(disposition['positionState'] == 'notScheduled' and disposition['signalDate'] is None and buy is None,
                'Unscheduled formation acquired an account entry')
    entered, is_closed, is_open = buy is not None, closed_episode is not None, open_episode is not None
    require(entered == (is_closed or is_open) and not (is_closed and is_open), 'Episode state partition')
    expected_position = 'closed' if is_closed else 'openAtCutoff' if is_open else 'notEntered' if scheduled else 'notScheduled'
    require(disposition['positionState'] == expected_position, 'Readout position state differs from saved episode')
    require(disposition['entryDate'] == (buy['date'] if buy else None), 'Readout actual entry date')
    require(disposition['exitDate'] == (closed_episode['exitDate'] if is_closed else None), 'Readout actual exit date')
    equal(disposition['closedReturnPct'], closed_episode['returnPct'] if is_closed else None, 'Readout closed return')
    equal(disposition['closedPnlAmount'], closed_episode['pnlAmount'] if is_closed else None, 'Readout closed PnL')
    return {'period': period, 'policy': policy, 'formationId': formation['formationId'],
        'formationDate': formation['formationDate'], 'symbol': formation['symbol'], 'state': state,
        'signalDate': disposition['signalDate'], 'entryDate': disposition['entryDate'], 'exitDate': disposition['exitDate'],
        'entered': entered, 'closed': is_closed, 'open': is_open,
        'closedReturnPct': closed_episode['returnPct'] if is_closed else None,
        'closedPnlAmount': closed_episode['pnlAmount'] if is_closed else 0,
        'openUnrealizedPnlAmount': open_episode['unrealizedPnlAmount'] if is_open else 0,
        'openRealizedPnlAmount': open_episode['realizedPnlWhileOpen'] if is_open else 0,
        'openMarkStale': open_episode['markStaleAtCutoff'] if is_open else False,
        'holdingSessions': closed_episode['holdingDays'] if is_closed else open_episode['observedHoldingSessions'] if is_open else None,
        'reason': disposition['reason']}


def check_records(rows, states):
    identities = [(r['period'], r['policy'], r['formationId']) for r in rows]
    require(len(identities) == len(set(identities)), 'Duplicate formation-policy record')
    for row in rows:
        require(row['state'] in states and all(type(row[k]) is bool for k in ('entered', 'closed', 'open', 'openMarkStale')),
                'Record state or boolean schema')
        require(row['entered'] == (row['closed'] or row['open']) and not (row['closed'] and row['open']), 'Invalid episode partition')
        require(row['signalDate'] is not None or not row['entered'], 'Entry for unscheduled formation')
        if row['closed']:
            require(finite(row['closedReturnPct']) and finite(row['closedPnlAmount']), 'Closed return/PnL unavailable')
        else:
            require(row['closedReturnPct'] is None and row['closedPnlAmount'] == 0, 'Nonclosed fabricated as closed zero return')
        for name in ('openUnrealizedPnlAmount', 'openRealizedPnlAmount'):
            require(finite(row[name]) and (row['open'] or row[name] == 0), 'Open PnL assigned to nonopen record')


def aggregate_group(rows, initial=500000.):
    selected = [r for r in rows if r['signalDate'] is not None]
    closed = [r for r in rows if r['closed']]
    positive = [r for r in closed if r['closedPnlAmount'] > 0]
    negative = [r for r in closed if r['closedPnlAmount'] < 0]
    profit = math.fsum(r['closedPnlAmount'] for r in positive)
    loss = -math.fsum(r['closedPnlAmount'] for r in negative)
    closed_amount = math.fsum(r['closedPnlAmount'] for r in rows)
    open_amount = math.fsum(r['openUnrealizedPnlAmount'] for r in rows)
    open_realized = math.fsum(r['openRealizedPnlAmount'] for r in rows)
    total = math.fsum((closed_amount, open_amount, open_realized))
    entered = sum(r['entered'] for r in rows)
    return {'formationCount': len(rows), 'selectedSignals': len(selected), 'entered': entered,
        'closed': len(closed), 'open': sum(r['open'] for r in rows), 'notScheduled': len(rows) - len(selected),
        'notEntered': len(selected) - entered, 'uniqueFormationDays': len({r['formationDate'] for r in rows}),
        'uniqueSymbols': len({r['symbol'] for r in rows}),
        'closedMeanPct': avg([r['closedReturnPct'] for r in closed]), 'winRatePct': ratio(len(positive), len(closed), 100),
        'meanWinPct': avg([r['closedReturnPct'] for r in positive]), 'meanLossPct': avg([r['closedReturnPct'] for r in negative]),
        'profitFactor': ratio(profit, loss), 'minClosedPct': min((r['closedReturnPct'] for r in closed), default=None),
        'top5ShareOfGrossProfitPct': ratio(math.fsum(sorted((r['closedPnlAmount'] for r in positive), reverse=True)[:5]), profit, 100),
        'closedPnlAmount': closed_amount, 'openUnrealizedPnlAmount': open_amount, 'openRealizedPnlAmount': open_realized,
        'totalPnlAmount': total, 'contributionPpOfInitialCapital': total / initial * 100}


def aggregate_reference(rows, states, initial=500000.):
    check_records(rows, states)
    periods = sorted({r['period'] for r in rows})
    policies = sorted({r['policy'] for r in rows})
    overall, yearly = [], []
    for period in periods:
        years = sorted({int(r['formationDate'][:4]) for r in rows if r['period'] == period})
        for policy in policies:
            for state in states:
                selected = [r for r in rows if (r['period'], r['policy'], r['state']) == (period, policy, state)]
                overall.append({'period': period, 'policy': policy, 'state': state, **aggregate_group(selected, initial)})
        for year in years:
            for policy in policies:
                for state in states:
                    selected = [r for r in rows if (r['period'], r['policy'], r['state'], int(r['formationDate'][:4])) ==
                                (period, policy, state, year)]
                    yearly.append({'period': period, 'policy': policy, 'state': state, 'formationYear': year,
                                   **aggregate_group(selected, initial)})
    return {'overallRows': overall, 'formationYearRows': yearly}


def describe(values):
    values = list(values)
    known = [v for v in values if finite(v)]
    answer = {'count': len(values), 'knownCount': len(known), 'unknownCount': len(values) - len(known),
              'status': 'empty' if not values else 'complete' if len(known) == len(values) else 'numericUnknown'}
    answer.update(dict.fromkeys(('mean', 'min', 'p05', 'p25', 'median', 'p75', 'p95', 'max')))
    if values and len(values) == len(known):
        answer.update(mean=avg(values), min=min(values), p05=percentile(values, .05), p25=percentile(values, .25),
                      median=percentile(values, .5), p75=percentile(values, .75), p95=percentile(values, .95), max=max(values))
    return answer


def nullable_sum(values):
    values = list(values)
    return math.fsum(values) if all(finite(v) for v in values) else None


def labels(values):
    return dict(sorted(Counter('null' if value is None else str(value) for value in values).items()))


def closed_stats(rows):
    wins = [r for r in rows if finite(r.get('pnlAmount')) and r['pnlAmount'] > 0]
    losses = [r for r in rows if finite(r.get('pnlAmount')) and r['pnlAmount'] < 0]
    zeros = sum(finite(r.get('pnlAmount')) and r['pnlAmount'] == 0 for r in rows)
    unknown = len(rows) - len(wins) - len(losses) - zeros
    profit, loss = nullable_sum(r['pnlAmount'] for r in wins), nullable_sum(-r['pnlAmount'] for r in losses)
    win_returns, loss_returns = describe(r.get('returnPct') for r in wins), describe(r.get('returnPct') for r in losses)
    payoff = (win_returns['mean'] / -loss_returns['mean'] if not unknown and finite(win_returns['mean'])
              and finite(loss_returns['mean']) and loss_returns['mean'] < 0 else None)
    return {'count': len(rows), 'wins': len(wins), 'losses': len(losses), 'zeros': zeros, 'unknownPnlCount': unknown,
        'netPnlAmount': nullable_sum(r.get('pnlAmount') for r in rows),
        'grossProfitAmount': profit if not unknown else None, 'grossLossAmount': loss if not unknown else None,
        'winRatePct': len(wins) / len(rows) * 100 if rows and not unknown else None,
        'profitFactor': profit / loss if not unknown and loss > 0 else None, 'payoffRatio': payoff,
        'returnPct': describe(r.get('returnPct') for r in rows), 'winReturnPct': win_returns, 'lossReturnPct': loss_returns,
        'pnlAmount': describe(r.get('pnlAmount') for r in rows), 'holdingSessions': describe(r.get('holdingDays') for r in rows),
        'assignment': 'whole closed episode assigned by exitDate; not mark-to-market interval contribution'}


def flow_counts(result, original, start, end):
    schedule = result['research']['identity']['signalSchedule']
    account_rows = result['research']['opportunities']
    by_id = {r['signalId']: r for r in account_rows}
    require(len(by_id) == len(account_rows) and set(by_id) == {r['signalId'] for r in schedule}, 'Complete account schedule')
    formed = [r for r in original if start <= r['formationDate'] <= end]
    selected = [by_id[r['formationId']] for r in formed if r['formationId'] in by_id]
    entered = sum(finite(r.get('filledQuantity')) and r['filledQuantity'] > 0 for r in selected)
    formations = {'formationTop5': len(formed), 'confirmationStates': labels(r['confirmation']['status'] for r in formed),
        'selectedSignalsFromFormation': len(selected), 'notScheduledFromFormation': len(formed) - len(selected),
        'enteredFromFormation': entered, 'notEnteredFromFormation': len(selected) - entered,
        'terminalStatusCounts': labels(r.get('status', 'missing') for r in selected),
        'terminalReasonCounts': labels(r.get('reason', 'missing') for r in selected),
        'meaning': 'formationDate cohort; later decisions, entries and exits remain assigned to their original formation here; terminal dispositions observed at full account cutoff'}
    selected = [r for r in account_rows if start <= r['signalDate'] <= end]
    entered = sum(finite(r.get('filledQuantity')) and r['filledQuantity'] > 0 for r in selected)
    decisions = {'selectedSignals': len(selected), 'entered': entered, 'notEntered': len(selected) - entered,
        'terminalStatusCounts': labels(r.get('status', 'missing') for r in selected),
        'terminalReasonCounts': labels(r.get('reason', 'missing') for r in selected),
        'meaning': 'signalDate is actual policy decision date; terminal dispositions observed at full account cutoff, not phase end'}
    return formations, decisions


def curve_interval(result, start, end, original=()):
    """Continuous original equity curve, including positions carried across split."""
    curve = result['curve']
    require([r['date'] for r in curve] == sorted({r['date'] for r in curve}), 'Curve is not ordered/unique')
    rows = [r for r in curve if start <= r['date'] <= end]
    before = [r for r in curve if r['date'] < start]
    require(bool(rows), 'Empty registered curve epoch')
    initial = before[-1]['equity'] if before else result['config']['initialCapital']
    require(all(finite(r['equity']) and r['equity'] > 0 for r in rows) and finite(initial) and initial > 0,
            'Invalid epoch equity')
    high, drawdown, drawdown_day = initial, 0., None
    for row in rows:
        high = max(high, row['equity'])
        loss = (high - row['equity']) / high * 100
        if loss > drawdown:
            drawdown, drawdown_day = loss, row['date']
    exposure = [r['marketValue'] / r['equity'] * 100 for r in rows]
    daily = {r['date']: r for r in result['daily']}
    require(len(daily) == len(result['daily']), 'Duplicate account daily row')
    ages, positions, missing = [], [], []
    for row in rows:
        holdings = daily.get(row['date'], {}).get('holdings')
        if isinstance(holdings, list):
            positions.append(len(holdings))
            ages.extend(h.get('holdingDays') for h in holdings)
        else:
            positions.append(None)
            missing.append(row['date'])
    fills = [r for r in result['trades'] if start <= r['date'] <= end]
    closed = [r for r in result['closedTrades'] if start <= r['exitDate'] <= end]
    exits = [r for r in result['decisions'] if start <= r['date'] <= end and r.get('action') == 'exit']
    unreliable = [r['date'] for r in rows if r.get('valuationReliable') is not True]
    invested = sum(r['marketValue'] > 0 for r in rows)
    final_holdings = daily.get(rows[-1]['date'], {}).get('holdings')
    formations, decisions = flow_counts(result, original, start, end)
    return {'requestedStart': start, 'requestedEnd': end, 'observedStart': rows[0]['date'],
        'observedEnd': rows[-1]['date'], 'observedSessions': len(rows),
        'enteringEquity': initial, 'enteringEquityDate': before[-1]['date'] if before else None,
        'enteringEquityBasis': 'previousObservedClose' if before else 'initialCapital',
        'endingEquity': rows[-1]['equity'], 'netPnlAmount': rows[-1]['equity'] - initial,
        'returnPct': (rows[-1]['equity'] / initial - 1) * 100, 'maxDrawdownPct': drawdown,
        'maxDrawdownDate': drawdown_day, 'endingCash': rows[-1]['cash'], 'endingMarketValue': rows[-1]['marketValue'],
        'endingPositionCount': len(final_holdings) if isinstance(final_holdings, list) else None,
        'status': 'observedValues;valuationUnverified' if unreliable else 'observedValues',
        'valuationUnreliableDates': unreliable, 'unknownEquityDates': [], 'investedSessions': invested,
        'investedSessionPct': invested / len(rows) * 100, 'exposurePct': describe(exposure),
        'positionCountPerClose': describe(positions), 'heldSessionAge': describe(ages), 'missingDailyHoldingsDates': missing,
        'holdingAgeMeaning': 'position-close observations, not completed trade duration; missing holding dates reported separately',
        'fillCount': len(fills), 'buyFillCount': sum(r['side'] == 'buy' for r in fills),
        'sellFillCount': sum(r['side'] == 'sell' for r in fills), 'feesPaid': nullable_sum(r.get('fee') for r in fills),
        'slippageAlreadyInFillPrices': nullable_sum(r.get('slippage') for r in fills),
        'sellFillReasons': labels(r.get('reason', 'missing') for r in fills if r['side'] == 'sell'),
        'queuedExitReasons': labels(r.get('reason', 'missing') for r in exits),
        'exitCountMeaning': 'queued decisions and sell fills are separate; partial fills/retries need not equal completed episodes',
        'closedEpisodesByExitDate': closed_stats(closed), 'opportunitiesByFormationDate': formations,
        'opportunitiesByDecisionDate': decisions,
        'calendarMeaning': 'supplied source account curve only; no missing-date compression, interpolation, or calendar reconstruction'}


def market_evidence(period, plan):
    folder = ROOT / 'artifacts/strategy-search' / MARKETS[period]
    binding = plan['marketBindings'][period]
    protocol, completion, audit = (read(folder / n) for n in ('protocol.json', 'completion.json', 'independent-verification.json'))
    require(audit['passed'] is True and audit['failureCount'] == 0, 'Inherited market independent audit failed')
    require(digest({k: v for k, v in protocol.items() if k != 'protocolHash'}) == protocol['protocolHash'], 'Market protocol digest')
    require(protocol['protocolHash'] == audit['protocolHash'] == completion['protocolHash'], 'Market closure protocol mismatch')
    require(sha(folder / 'daily.jsonl.gz') == MARKET_SHA[period], 'Market daily values not original independently audited version')
    for name, reference in completion['files'].items():
        require(reference['path'] == name and reference['sha256'] == audit['resultFileHashes'][name], 'Market receipt audit hashes')
    manifest_path = local(protocol['registration']['input']['directory'] + '/manifest.json')
    require(sha(manifest_path) == protocol['inputManifestSha256'] == audit['inputManifestSha256'], 'Market manifest binding')
    expected_files = {str((folder / n).relative_to(ROOT).as_posix()) for n in
        ('completion.json', 'protocol.json', 'summary.json', 'independent-verification.json', 'attempt.json', 'execution-sources.zip', 'daily.jsonl.gz')}
    expected_files.add(manifest_path.relative_to(ROOT).as_posix())
    require(set(binding['files']) == expected_files, 'Market small-evidence closure inventory')
    for name, expected_sha in binding['files'].items():
        require(plan['frozenInputs'][name] == expected_sha == sha(local(name)), 'Market source file pin differs')
    inputs = read(manifest_path)['inputs']
    source = read(PARENT / period / 'source.json')
    metadata = read(PARENT / period / 'signal-metadata.json')
    require(inputs['symbols'] == protocol['symbols'] == sorted(set(protocol['symbols']))
            and len(protocol['symbols']) == 3141, 'Original market sample identity')
    require(inputs['fullDatasetContentSha256'] == source['sourceDatasetDigest'] == binding['rawSourceDatasetDigest'],
            'Market and saved account raw dataset differ')
    require(inputs['snapshotFingerprint'] == source.get('sourceSnapshotFingerprint', source.get('sourceDatasetFingerprint'))
            == binding['rawSourceSnapshot'], 'Market/source snapshot differs')
    require(inputs['calendar']['sha256'] == protocol['calendarHash'] == source['calendarEvidence']['calendarHash']
            == binding['calendarHash'], 'Market/source calendar hash differs')
    require(protocol['dates'] == metadata['targetDates'], 'Market target calendar differs from original formations')
    require(binding['fixedPopulation'] == 3141 and binding['dailyRows'] == len(metadata['targetDates'])
            and binding['originalMarketAuditPassed'] is True and binding['rawFeatureAndRoleFilesReread'] is False,
            'Market inherited scope differs')
    daily = records(folder / 'daily.jsonl.gz')
    require([r['date'] for r in daily] == metadata['targetDates'], 'Market daily sequence/coverage')
    for index, row in enumerate(daily):
        require(row['fixedPopulation'] == 3141 and row['protocolHash'] == protocol['protocolHash']
                and row['calendarHash'] == protocol['calendarHash'] and row['inputManifestSha256'] == protocol['inputManifestSha256']
                and row['inputFeatureFileSha256'] == protocol['inputFilesSha256']['features.jsonl.gz'], 'Market row lineage mismatch')
        require(row['matched5']['earlierDate'] == (daily[index - 5]['date'] if index >= 5 else None),
                'Frozen matched5 target-history boundary')
    return metadata, daily


def verify(directory):
    started = time.perf_counter()
    require(not (directory / 'validation.json').exists(), 'Refusing to overwrite validation.json')
    require(sha(directory / 'plan.json') == REGISTERED_PLAN_SHA, 'Preregistered plan changed')
    plan = read(directory / 'plan.json')
    require(plan['schema'] == 'undercut-reclaim-context-diagnostic-v1' and plan['parent'] == PARENT.name
            and plan['policies'] == list(POLICIES) and plan['periods'] == list(PERIODS), 'Diagnostic registered identities')
    require(plan['newAccounts'] == plan['newPolicyVariants'] == plan['newIndicators'] == 0
            and plan['parameterSearch'] is False and plan['certified'] is False and plan['targetAchieved'] is False,
            'Diagnostic scope/trial count changed')
    require(plan['sourcePins']['service/research_reclaim_context.py'] == MODULE_SHA, 'Production module changed after freeze')
    pins = {**plan['sourcePins'], **plan['frozenInputs']}
    for name, expected in pins.items():
        require(sha(local(name)) == expected, 'Frozen artifact changed: ' + name)
    require(sha(PARENT / 'validation.json') == PARENT_VALIDATION_SHA and sha(PARENT / 'readout.json') == PARENT_READOUT_SHA,
            'Parent independent validation/readout changed')
    parent_audit, parent = read(PARENT / 'validation.json'), read(PARENT / 'readout.json')
    require(parent_audit['passed'] is True and parent_audit['planSha256'] == sha(PARENT / 'plan.json')
            and parent_audit['comparisonSha256'] == sha(PARENT / 'comparison.json'), 'Parent validation has stale bindings')
    require(parent['verification']['passed'] is True and parent['verification']['sha256'] == PARENT_VALIDATION_SHA,
            'Parent readout does not inherit exact independent audit')
    report = read(directory / 'readout.json')
    require(report['schema'] == plan['schema'] + '-readout' and report['planSha256'] == REGISTERED_PLAN_SHA, 'Readout identity binding')
    require(report['inputPins'] == {**plan['frozenInputs'], **plan['sourcePins']}, 'Readout input/code pins differ')
    require(report['sourceBindings'] == plan['marketBindings'] and report['method'] == plan['rules']
            and report['limitations'] == plan['limitations'], 'Readout source/method scope differs')
    require(report['newAccounts'] == report['newPolicyVariants'] == 0 and report['parameterSearch'] is False
            and report['certified'] is False and report['targetAchieved'] is False, 'Readout changes diagnostic scope')
    require(pin(report['contexts']) == (directory / 'contexts.json').resolve()
            and pin(report['records']) == (directory / 'records.jsonl.gz').resolve(), 'Output context/record receipt paths')
    saved_contexts, saved_records = read(directory / 'contexts.json'), records(directory / 'records.jsonl.gz')
    require(set(saved_contexts) == set(PERIODS), 'Context period coverage')
    require(len(parent['opportunityRows']) == 3803 and len(saved_records) == 11409
            and report['formationCount'] == 3803 and report['recordCount'] == 11409, 'All formation-policy records must remain')
    require(len({(r['period'], r['formationId']) for r in parent['opportunityRows']}) == 3803, 'Duplicate original formation')
    all_expected, bridges, expected_curves, qualities, extra_pins, counts = [], [], [], {}, {}, {}
    for period in PERIODS:
        metadata, market = market_evidence(period, plan)
        context = context_reference(metadata['calendar'], market, metadata['targetDates'])
        equal(saved_contexts[period], context, 'Complete daily independent context: ' + period)
        require(all(row['state'] == 'unknown' for row in context[:6]), 'Unknown initial matched history was filled')
        by_day = {r['formationDate']: r for r in context}
        originals = [r for r in parent['opportunityRows'] if r['period'] == period]
        source_reference = parent_audit['inputArtifacts'][period]['opportunities.jsonl.gz']
        raw_formations = records(pin(source_reference))
        extra_pins[source_reference['path']] = source_reference['sha256']
        require(len(raw_formations) == len(originals), 'Parent readout lost original formations')
        for raw, observed in zip(raw_formations, originals):
            require({k: observed[k] for k in raw} == raw, 'Original formation identity/order was altered in readout')
            i = metadata['calendar'].index(observed['formationDate'])
            require(observed['priorDate'] == metadata['calendar'][i - 1] == by_day[observed['formationDate']]['asOfDate'],
                    'Formation and all three clocks must share the exact prior-date context')
        counts[period] = {'dailyContextRows': len(context), 'unknownContextDays': sum(r['state'] == 'unknown' for r in context),
                          'formations': len(originals), 'recordCount': len(originals) * 3, 'accounts': {}}
        for policy in POLICIES:
            print(json.dumps({'phase': 'independent-saved-account-joins', 'period': period, 'policy': policy}), flush=True)
            result_path, summary_path = (PARENT / period / (policy + suffix) for suffix in ('.result.json.gz', '.summary.json'))
            for path in (result_path, summary_path):
                proof = parent_audit['accountArtifacts'][period][path.name]
                require(pin(proof) == path.resolve() and pins[proof['path']] == proof['sha256'], 'Parent account audit binding differs')
            result, summary = read(result_path), read(summary_path)
            require(summary['inputResultSha256'] == digest(result), 'Summary is not bound to full parsed result')
            require(result['status'] == 'completed' and summary['policyId'] == policy
                    and result['config']['initialCapital'] == 500000., 'Original saved account identity')
            buys, closed, opened = episode_indexes(result, summary)
            account_opps = {r['signalId']: r for r in result['research']['opportunities']}
            require(len(account_opps) == len(result['research']['opportunities']), 'Duplicate saved account opportunity')
            schedule = [r['signals'][policy] for r in originals if r['signals'][policy] is not None]
            require(schedule == result['research']['identity']['signalSchedule']
                    and set(account_opps) == {r['signalId'] for r in schedule}, 'Original schedule/account opportunities differ')
            rows = []
            for original in originals:
                identity = original['formationId']
                buy = buys.get(identity)
                key = (buy['symbol'], buy['date']) if buy else None
                rows.append(independent_record(period, policy, original, by_day[original['formationDate']]['state'],
                    account_opps.get(identity), buy, closed.get(key), opened.get(key)))
            check_records(rows, STATES)
            money = math.fsum(r['closedPnlAmount'] + r['openRealizedPnlAmount'] + r['openUnrealizedPnlAmount'] for r in rows)
            net = summary['account']['netPnlAmount']
            equal(money, net, 'All assigned episode PnL equals full original account PnL')
            require(sum(r['closed'] for r in rows) == len(closed) and sum(r['open'] for r in rows) == len(opened)
                    and sum(r['entered'] for r in rows) == len(buys), 'All closed/open/buy episodes conserved')
            bridge = {'period': period, 'policy': policy, 'assignedPnlAmount': money, 'accountNetPnlAmount': net,
                'unallocatedResidual': net - money, 'formationCount': len(rows), 'entered': len(buys),
                'closed': len(closed), 'open': len(opened), 'passed': True}
            bridges.append(bridge)
            counts[period]['accounts'][policy] = {'formations': len(rows), 'scheduled': len(schedule),
                'entered': len(buys), 'closed': len(closed), 'open': len(opened),
                'assignedPnlAmount': money, 'unallocatedResidual': net - money}
            all_expected.extend(rows)
            qualities[period + ':' + policy] = {k: summary[k] for k in ('sourceQuality', 'quality', 'unresolvedExits', 'concentration')}
            if period == '2024-2025':
                epochs = []
                for phase_id, start, end in (('before20240924', '2024-01-02', '2024-09-23'),
                        ('from20240924', SPLIT, '2025-12-31')):
                    value = curve_interval(result, start, end, originals)
                    value.update(period=period, policy=policy, phaseId=phase_id)
                    expected_curves.append(value)
                    epochs.append(value)
                require(epochs[1]['enteringEquityDate'] == epochs[0]['observedEnd'] == '2024-09-23'
                        and epochs[1]['observedStart'] == SPLIT, 'Event epoch boundary/calendar changed')
                equal(epochs[1]['enteringEquity'], epochs[0]['endingEquity'], 'Epoch capital reset instead of carry')
                equal(math.fsum(e['netPnlAmount'] for e in epochs), net, 'Continuous epoch PnL sum')
                equal((math.prod(1 + e['returnPct'] / 100 for e in epochs) - 1) * 100,
                      summary['account']['netReturnPct'], 'Continuous epoch compounded return')
                require(sum(e['observedSessions'] for e in epochs) == len(result['curve']), 'Epoch curves omit/duplicate sessions')
    equal(saved_records, all_expected, 'Every original formation/policy disposition and PnL field')
    expected = aggregate_reference(all_expected, STATES)
    equal(report['overallRows'], expected['overallRows'], 'All 30 overall state rows')
    equal(report['formationYearRows'], expected['formationYearRows'], 'All 60 formation-year rows')
    epoch_records = [{**r, 'period': r['period'] if r['period'] == '2022-2023' else
        '2024-2025-before20240924' if r['formationDate'] < SPLIT else '2024-2025-from20240924'} for r in all_expected]
    expected_epochs = aggregate_reference(epoch_records, STATES)['overallRows']
    equal(report['epochStateRows'], expected_epochs, 'All 45 event-epoch formation-state rows')
    equal(report['curveEpochRows'], expected_curves, 'All six continuous full-equity curve epochs')
    equal(report['accountReconciliation'], bridges, 'Six original account amount bridges')
    equal(report['sourceQualityByAccount'], qualities, 'Source quality/open exits/concentration disclosures')
    require((len(expected['overallRows']), len(expected['formationYearRows']), len(expected_epochs), len(expected_curves)) ==
            (30, 60, 45, 6), 'Complete published table domains')
    for bridge in bridges:
        group = [r for r in expected['overallRows'] if (r['period'], r['policy']) == (bridge['period'], bridge['policy'])]
        equal(math.fsum(r['totalPnlAmount'] for r in group), bridge['accountNetPnlAmount'], 'State money sum to original account')
    output = {'schema': 'undercut-reclaim-context-diagnostic-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'planSha256': REGISTERED_PLAN_SHA,
        'readoutSha256': sha(directory / 'readout.json'), 'verifierSha256': sha(__file__),
        'inputPins': pins, 'additionalParentFormationPins': extra_pins,
        'outputPins': {r['path']: r['sha256'] for r in (report['contexts'], report['records'])},
        'counts': {'formations': 3803, 'records': 11409, 'contextDates': sum(r['dailyContextRows'] for r in counts.values()),
            'overallStateRows': 30, 'formationYearRows': 60, 'epochStateRows': 45, 'curveEpochRows': 6,
            'savedAccountsChecked': 6, 'newAccounts': 0, 'periods': counts},
        'checks': ['Exact registered code/input/readout pins and inherited independent account and market source audit bindings',
            'Every target day independently classified from exact T-1 matched5 same-population endpoints, with all unknown/coverage/date evidence retained',
            'All original formation identities and all three policies retained; frozen formation context unaffected by subsequent decision/fill/exit dates',
            'Every saved-account signal, buy, closed episode and terminal open position joined to parent disposition by exact identity',
            'Closed and partial-open realized/unrealized PnL reconcile to six complete accounts; no invented closed return for unclosed/unentered records',
            'Every overall/year/event-epoch cell independently recomputed, including empty cells, net cash profit factor and top-five positive-money concentration',
            'Both event curve epochs independently recomputed from original full cash/equity/holdings/fills, preserving previous-close capital and carried positions'],
        'scope': {'newDiagnosticModuleImported': False, 'accountsExecuted': 0, 'originalRawMarketReloaded': False,
            'upstreamMarketFeatureAndRoleMathRecomputed': False, 'retrospectiveEpochUsedAsSignal': False,
            'meaning': 'Independent local arithmetic, classification and identity audit; original source and account execution proofs inherited by exact hash. No statistical independence, filtered account return, market ownership or profitability certification.'},
        'elapsedSeconds': time.perf_counter() - started, 'certified': False, 'targetAchieved': False}
    with (directory / 'validation.json').open('x', encoding='utf-8') as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed': True, 'path': str(directory / 'validation.json'),
        'sha256': sha(directory / 'validation.json'), 'contextDates': output['counts']['contextDates'],
        'formations': 3803, 'records': 11409, 'elapsedSeconds': output['elapsedSeconds']}), flush=True)


def self_test():
    from datetime import timedelta
    checks = []
    days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(20)]

    def market(day, today=.6, earlier=.4):
        i = days.index(day)
        return {'date': day, 'fixedPopulation': 100, 'matched5': {'available': i >= 11,
            'earlierDate': days[i - 5] if i >= 11 else None, 'pairedCount': 80 if i >= 11 else None,
            'todayEvaluableCount': 90, 'earlierEvaluableCount': 95 if i >= 11 else None,
            'positiveBreadthToday': today if i >= 11 else None, 'positiveBreadthEarlier': earlier if i >= 11 else None,
            'matchedBreadthChange5': 100 * (today - earlier) if i >= 11 else None,
            'pairedCoverageToday': 80 / 90 if i >= 11 else None, 'pairedCoverageEarlier': 80 / 95 if i >= 11 else None,
            'pairedCoverageFixed': .8 if i >= 11 else None}}

    source = [market(d) for d in days[6:]]
    answer = context_reference(days, source, days[6:])
    require([r['state'] for r in answer[:6]] == ['unknown'] * 6 and answer[6]['state'] == STATES[0],
            'Missing warmup/first-five matched history must yield six unknown formation dates')
    require(answer[0]['asOfDate'] == days[5] and answer[6]['asOfDate'] == days[11]
            and answer[6]['earlierDate'] == days[6], 'Exact T-1/T-6 clock')
    checks.append('six startup unknown dates and exact T-1/T-6 calendar joins')

    for today, earlier, expected in ((.6, .4, STATES[0]), (.6, .6, STATES[1]), (.5, .4, STATES[2]), (.5, .5, STATES[3])):
        row = market(days[11], today, earlier)
        row['price20'] = {'positiveReturn20Breadth': 1 - today}
        require(context_reference(days, [row], [days[12]])[0]['state'] == expected, 'Quadrant/equality/denominator mismatch')
    checks.append('all four quadrants, strict equality rules and independent broader price20 denominator')

    row = market(days[11])
    for field in ('pairedCoverageToday', 'pairedCoverageEarlier', 'pairedCoverageFixed'):
        row['matched5'][field] = None
    require(context_reference(days, [row], [days[12]])[0]['state'] == STATES[0], 'Missing coverage became an unregistered filter')
    for field, value, reason in (('available', False, 'matchedComparisonUnavailable'),
            ('pairedCount', None, 'pairedCountUnavailable'), ('pairedCount', 0, 'emptyMatchedPopulation'),
            ('earlierDate', None, 'matchedEarlierDateUnavailable'), ('positiveBreadthToday', None, 'matchedValuesUnavailable')):
        row = market(days[11])
        row['matched5'][field] = value
        require(context_reference(days, [row], [days[12]])[0]['reason'] == reason, 'Unknown evidence erased: ' + field)
    checks.append('unknown reason handling and coverage retained without an extra coverage gate')

    for field, value in (('pairedCount', 101), ('positiveBreadthToday', 1.1), ('matchedBreadthChange5', 0), ('earlierDate', days[5])):
        row = market(days[11])
        row['matched5'][field] = value
        try:
            context_reference(days, [row], [days[12]])
        except ValueError:
            pass
        else:
            raise ValueError('Contradictory known market evidence admitted: ' + field)
    future = market(days[19]); future['matched5']['positiveBreadthToday'] = 9
    require(context_reference(days, source[:-1] + [future], days[6:13]) == context_reference(days, source, days[6:13]),
            'Unused future market values changed prior context')
    checks.append('contradictory denominators/dates/change rejected; unused future values cannot affect classification')

    base = {'period': '2022-2023', 'policy': 'immediate', 'formationId': 'a', 'formationDate': '2022-09-23',
        'symbol': 'stockA', 'state': STATES[0], 'signalDate': '2022-09-23', 'entered': True, 'closed': True, 'open': False,
        'closedReturnPct': 20., 'closedPnlAmount': 200., 'openUnrealizedPnlAmount': 0., 'openRealizedPnlAmount': 0.,
        'openMarkStale': False, 'holdingSessions': 10, 'reason': 'filled'}
    loss = {**base, 'formationId': 'b', 'symbol': 'stockB', 'closedReturnPct': -10., 'closedPnlAmount': -100.}
    opened = {**base, 'formationId': 'c', 'closed': False, 'open': True, 'closedReturnPct': None, 'closedPnlAmount': 0.,
              'openUnrealizedPnlAmount': 70., 'openRealizedPnlAmount': -5., 'openMarkStale': True}
    rejected = {**base, 'formationId': 'd', 'entered': False, 'closed': False, 'closedReturnPct': None, 'closedPnlAmount': 0.,
                'holdingSessions': None, 'reason': 'capacity'}
    unscheduled = {**rejected, 'formationId': 'e', 'signalDate': None, 'reason': 'failed'}
    group = [base, loss, opened, rejected, unscheduled]
    check_records(group, STATES)
    aggregate = aggregate_group(group)
    require((aggregate['formationCount'], aggregate['selectedSignals'], aggregate['entered'], aggregate['closed'], aggregate['open'],
             aggregate['notScheduled'], aggregate['notEntered']) == (5, 4, 3, 2, 1, 1, 1), 'Disposition counts conflated')
    equal(aggregate['totalPnlAmount'], 165.)
    equal(aggregate['closedMeanPct'], 5.)
    equal(aggregate['profitFactor'], 2.)
    equal(aggregate['contributionPpOfInitialCapital'], .033)
    require(aggregate['uniqueFormationDays'] == 1, 'Same-day stocks treated as independent market dates')
    checks.append('all disposition states and partial-open realized/unrealized money reconcile without fabricated returns')

    expanded = [{**row, 'period': period, 'policy': policy, 'formationDate': year + '-09-23'}
        for period, years in (('2022-2023', ('2022', '2023')), ('2024-2025', ('2024', '2025')))
        for year in years for policy in POLICIES for row in group]
    for i, row in enumerate(expanded):
        row['formationId'] = str(i)
    output = aggregate_reference(expanded, STATES)
    require(len(output['overallRows']) == 30 and len(output['formationYearRows']) == 60, 'Empty/full domain rows omitted')
    empty = next(r for r in output['overallRows'] if r['state'] == 'unknown')
    require(empty['closedMeanPct'] is None and empty['totalPnlAmount'] == 0 and empty['formationCount'] == 0,
            'Empty group values fabricated')
    require([(r['period'], r['formationYear'], r['policy']) for r in output['formationYearRows'][::5]] ==
        sorted({(r['period'], int(r['formationDate'][:4]), r['policy']) for r in expanded}), 'Year table order')
    checks.append('complete 30 overall and 60 formation-year cells, null empty distributions and fixed order')

    invalid = {**rejected, 'closedReturnPct': 0.}
    for rows in ([invalid], [base, base]):
        try:
            check_records(rows, STATES)
        except ValueError:
            pass
        else:
            raise ValueError('Fabricated unclosed zero return or duplicate record admitted')
    wins = [{**base, 'formationId': str(i), 'closedPnlAmount': float(i + 1)} for i in range(6)]
    equal(aggregate_group(wins)['top5ShareOfGrossProfitPct'], 20 / 21 * 100)
    checks.append('duplicate formation policies rejected; top-five concentration uses net positive money')

    sample = {'config': {'initialCapital': 100.}, 'curve': [
        {'date': '2024-09-23', 'equity': 110., 'cash': 10., 'marketValue': 100., 'valuationReliable': True},
        {'date': '2024-09-24', 'equity': 99., 'cash': 10., 'marketValue': 89., 'valuationReliable': True},
        {'date': '2024-09-25', 'equity': 121., 'cash': 10., 'marketValue': 111., 'valuationReliable': True}],
        'daily': [{'date': d, 'holdings': [{'symbol': 'a', 'holdingDays': i + 9}]} for i, d in enumerate(('2024-09-23', '2024-09-24', '2024-09-25'))],
        'trades': [], 'closedTrades': [], 'decisions': [], 'research': {'identity': {'signalSchedule': []}, 'opportunities': []}}
    before = curve_interval(sample, '2024-01-02', '2024-09-23')
    after = curve_interval(sample, '2024-09-24', '2025-12-31')
    require(after['enteringEquity'] == 110. and after['enteringEquityDate'] == '2024-09-23'
            and after['endingPositionCount'] == 1, 'Epoch reset account capital or liquidated carried holdings')
    equal(after['maxDrawdownPct'], 10.)
    equal(before['netPnlAmount'] + after['netPnlAmount'], 21.)
    equal(((1 + before['returnPct'] / 100) * (1 + after['returnPct'] / 100) - 1) * 100, 21.)
    require(after['heldSessionAge']['min'] == 10, 'Holding clock reset at epoch boundary')
    checks.append('continuous equity epoch compounding, previous-close baseline, drawdown and carried holding age')
    print(json.dumps({'passed': True, 'groups': len(checks), 'checks': checks,
                      'newDiagnosticModuleImported': False, 'accountsExecuted': 0}), flush=True)


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        directory = args.directory.resolve()
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        attempts = directory / 'validation-attempts'
        attempts.mkdir(exist_ok=True)
        try:
            verify(directory)
        except Exception as exc:
            failure = {'passed': False, 'at': stamp, 'verifierSha256': sha(__file__), 'error': str(exc),
                       'traceback': traceback.format_exc(), 'changedProductionArtifacts': False}
            for name in ('plan.json', 'readout.json', 'contexts.json', 'records.jsonl.gz'):
                if (directory / name).is_file():
                    failure[name + 'Sha256'] = sha(directory / name)
            path = attempts / (stamp + '-failed.json')
            with path.open('x', encoding='utf-8') as stream:
                json.dump(failure, stream, ensure_ascii=False, indent=2, allow_nan=False)
            print(json.dumps({'passed': False, 'attempt': str(path), 'error': str(exc)}), flush=True)
            raise
