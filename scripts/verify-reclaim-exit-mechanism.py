#!/usr/bin/env python3
"""Independent saved-fill/episode and algebra audit; never runs an account.

No new production module or diagnostic runner is imported. The chronological
fill replay, normalized episodes, symmetric decomposition and all aggregates
are recomputed here. Source execution truth remains the pinned prior audit.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/reclaim-exit-mechanism-v1'
PARENT = ROOT / 'artifacts/strategy-search/reclaim-volatility-trail-v1'
PREVIOUS = ROOT / 'artifacts/strategy-search/reclaim-relative-feedback-context-v1'
VERSION = 'reclaim-exit-mechanism-v1'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
POLICIES = ('trail10', 'volatilityTrail')
GROUPS = ('bothSameEntry', 'bothDifferentEntry', 'controlOnly', 'candidateOnly', 'neither')
STATES = ('closed', 'openAtCutoff', 'notEntered')
COMPONENTS = ('returnProfileComponent', 'committedCapitalComponent', 'candidateOnlyComponent', 'controlOnlyComponent')
MONEY = ('closedPnlAmount', 'openRealizedPnlAmount', 'openUnrealizedPnlAmount')
TRIALS = {'diagnosticBatches': 1, 'existingAccountsRead': 12, 'newAccounts': 0,
          'newStrategyTrials': 0, 'newEntryPolicies': 0, 'newExitDefinitions': 0}
EXPECTED = {'accountBridges': 12, 'pairRows': 8155, 'accountDifferenceRows': 6, 'groupRows': 30,
            'maturityRows': 54, 'formationYearRows': 60, 'formationEpochRows': 45,
            'closedPairTimingRows': 18, 'closedPairBaselineOutcomeRows': 18}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def same(actual, expected, label='value'):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and set(actual) == set(expected), label + ': keys differ')
        for key in expected:
            same(actual[key], expected[key], label + '.' + str(key))
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + ': length differs')
        for i, (got, want) in enumerate(zip(actual, expected)):
            same(got, want, label + '[' + str(i) + ']')
    elif type(expected) is float:
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7), label + ': number differs')
    else:
        require(type(actual) is type(expected) and actual == expected, label + ': value differs')


def near(actual, expected, label):
    require(finite(actual) and finite(expected) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7), label)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def read(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def pinned(ref):
    require(isinstance(ref, dict) and 'path' in ref and 'sha256' in ref, 'Malformed artifact reference')
    path = (ROOT / ref['path']).resolve()
    require(not Path(ref['path']).is_absolute() and path.is_relative_to(ROOT) and path.is_file(), 'Invalid repository evidence path')
    require(sha(path) == ref['sha256'], 'Changed artifact: ' + ref['path'])
    return path


def reference(path):
    path = Path(path).resolve()
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def timestamp(text):
    value = datetime.fromisoformat(text)
    require(value.tzinfo is not None, 'Timestamp lacks timezone')
    return value


def reconstruct_episodes(result):
    """Replay original fill order; sell signal IDs/dates never identify an entry."""
    require(not any(result.get(k) for k in ('actionLedger', 'cashReceivables', 'actionPayables', 'preExRights'))
            and result.get('reservedActionCash', 0) == 0, 'Unexpected rights/top-up investment contract')
    calendar = [r['date'] for r in result['curve']]
    require(calendar and calendar == sorted(set(calendar)), 'Original curve calendar not ordered/unique')
    day_index = {day: i for i, day in enumerate(calendar)}
    cash = result['config']['initialCapital']
    require(finite(cash) and cash > 0, 'Unknown initial cash')
    active, episodes, fill_ids, previous_date = {}, {}, set(), None
    fees, buy_count, sell_count = [], 0, 0
    for fill in result['trades']:
        day, symbol, side = fill['date'], fill['symbol'], fill['side']
        require(day in day_index and (previous_date is None or day >= previous_date), 'Fill chronology not original ordered calendar')
        previous_date = day
        fid = fill['fillId']
        require(isinstance(fid, str) and fid and fid not in fill_ids, 'Duplicate/empty fill ID')
        fill_ids.add(fid)
        qty, price, amount, fee = (fill[k] for k in ('quantity', 'price', 'amount', 'fee'))
        require(type(qty) is int and qty > 0 and finite(price) and price > 0
                and finite(amount) and amount > 0 and finite(fee) and fee >= 0, 'Invalid actual fill economics')
        near(amount, qty * price, 'Fill notional is not quantity times slipped price')
        fees.append(fee)
        if side == 'buy':
            sid = fill.get('signalId')
            require(isinstance(sid, str) and sid and sid not in episodes and symbol not in active,
                    'Missing original buy identity, duplicate buy or unexpected top-up')
            require(fill['positionAction'] == 'open' and fill['quantityBefore'] == 0 and fill['quantityAfter'] == qty,
                    'Buy must open exactly one original episode')
            require(fill['signalDate'] < day and fill['signalDate'] in day_index, 'Buy decision chronology')
            invested = amount + fee
            episode = {'signalId': sid, 'symbol': symbol, 'signalDate': fill['signalDate'], 'entryDate': day,
                'entryPrice': price, 'entryFillId': fid, 'entryQuantity': qty, 'investedAmount': invested,
                'quantity': qty, 'costBasis': invested, 'netSellProceeds': 0., 'realizedPnl': 0.,
                'sellFillIds': [], 'exitDate': None, 'fees': fee}
            active[symbol] = episode
            episodes[sid] = episode
            cash -= invested
            buy_count += 1
            require(fill['realizedPnl'] is None, 'Buy cannot carry realized sell PnL')
        else:
            require(side == 'sell' and symbol in active, 'Sell has no currently active episode')
            episode = active[symbol]
            require(day > episode['entryDate'] and qty <= episode['quantity'], 'T+1 or held quantity violated')
            before = episode['quantity']
            require(fill['quantityBefore'] == before and fill['quantityAfter'] == before - qty,
                    'Sell quantities differ from original chronological episode')
            require(fill['positionAction'] == ('exit' if qty == before else 'reduce'), 'Sell action differs from quantity transition')
            allocated = episode['costBasis'] * qty / before
            proceeds = amount - fee
            realized = proceeds - allocated
            near(fill['realizedPnl'], realized, 'Sell realized PnL from independently allocated entry cost')
            episode['quantity'] -= qty
            episode['costBasis'] -= allocated
            episode['netSellProceeds'] += proceeds
            episode['realizedPnl'] += realized
            episode['sellFillIds'].append(fid)
            episode['fees'] += fee
            cash += proceeds
            sell_count += 1
            if episode['quantity'] == 0:
                episode['exitDate'] = day
                episode['holdingSessions'] = day_index[day] - day_index[episode['entryDate']] + 1
                episode['positionState'] = 'closed'
                episode['closedPnlAmount'] = episode['netSellProceeds'] - episode['investedAmount']
                episode['openRealizedPnlAmount'] = episode['openUnrealizedPnlAmount'] = 0.
                episode['openMarkStale'] = False
                near(episode['realizedPnl'], episode['closedPnlAmount'], 'Closed cash-flow and realized PnL identity')
                near(episode['costBasis'], 0., 'Closed position retains cost basis')
                del active[symbol]
        near(fill['cashAfter'], cash, 'Original sequential cash-after transition')
    saved_closed = {(r['symbol'], r['entryDate']): r for r in result['closedTrades']}
    saved_open = {r['symbol']: r for r in result['positions']}
    require(len(saved_closed) == len(result['closedTrades']) and len(saved_open) == len(result['positions'])
            and set(saved_open) == set(active), 'Closed/open saved episode identities differ')
    reconstructed_closed = set()
    for episode in episodes.values():
        key = episode['symbol'], episode['entryDate']
        if episode['exitDate'] is not None:
            require(key in saved_closed and key not in reconstructed_closed, 'Missing/duplicate saved closed episode')
            reconstructed_closed.add(key)
            raw = saved_closed[key]
            require(raw['exitDate'] == episode['exitDate'] and raw['holdingDays'] == episode['holdingSessions'], 'Closed date/age differs')
            near(raw['investedAmount'], episode['investedAmount'], 'Closed investment differs from original buy outlay')
            near(raw['pnlAmount'], episode['closedPnlAmount'], 'Closed record differs from fill cash flows')
            near(raw['returnPct'], episode['closedPnlAmount'] / episode['investedAmount'] * 100, 'Closed net return denominator')
        else:
            raw = saved_open[episode['symbol']]
            require(raw['entryDate'] == episode['entryDate'] and raw['quantity'] == episode['quantity'], 'Open entry/quantity differs')
            for key2 in ('costBasis', 'investedAmount', 'realizedPnl'):
                near(raw[key2], episode[key2], 'Open original ledger.' + key2)
            require(finite(raw['markPrice']) and raw['markPrice'] > 0 and raw['priceDate'] <= calendar[-1], 'Invalid original terminal mark')
            near(raw['marketValue'], raw['quantity'] * raw['markPrice'], 'Original terminal position valuation')
            require(sum(lot['quantity'] for lot in raw['lots']) == raw['quantity'], 'Terminal lot share count')
            near(math.fsum(lot['cost'] for lot in raw['lots']), raw['costBasis'], 'Terminal lot retained cost')
            episode['positionState'] = 'openAtCutoff'
            episode['closedPnlAmount'] = 0.
            episode['openRealizedPnlAmount'] = episode['realizedPnl']
            episode['openUnrealizedPnlAmount'] = raw['marketValue'] - episode['costBasis']
            episode['holdingSessions'] = len(calendar) - day_index[episode['entryDate']]
            episode['openMarkStale'] = raw['priceDate'] != calendar[-1]
            near(episode['openRealizedPnlAmount'] + episode['openUnrealizedPnlAmount'],
                 episode['netSellProceeds'] + raw['marketValue'] - episode['investedAmount'], 'Open three-leg cash identity')
    require(reconstructed_closed == set(saved_closed), 'Unallocated raw closed episode')
    final = result['curve'][-1]
    near(cash, final['cash'], 'Final cash reconciles all original fills')
    near(math.fsum(r['marketValue'] for r in result['positions']), final['marketValue'], 'Final market value reconciles all positions')
    near(cash + final['marketValue'], final['equity'], 'Final equity cash/market bridge')
    return episodes, {'fillCount': len(fill_ids), 'buyFillCount': buy_count, 'sellFillCount': sell_count,
                      'fees': math.fsum(fees), 'finalCash': cash, 'finalEquity': final['equity']}


def normalized_reference(episode, opportunity):
    entered = episode is not None
    row = {'entered': entered, 'entryDate': None, 'entryPrice': None, 'exitDate': None, 'positionState': 'notEntered',
           'investedAmount': 0., 'closedPnlAmount': 0., 'openRealizedPnlAmount': 0., 'openUnrealizedPnlAmount': 0.,
           'holdingSessions': None, 'reason': opportunity['reason'], 'originalPositionState': opportunity['positionState'], 'openMarkStale': False}
    if entered:
        for name in ('entryDate', 'entryPrice', 'exitDate', 'positionState', 'investedAmount', *MONEY, 'holdingSessions', 'openMarkStale'):
            row[name] = episode[name]
    return row


def episode_values(row):
    require(type(row['entered']) is bool and row['positionState'] in STATES, 'Invalid normalized episode state')
    require(all(finite(row[name]) for name in ('investedAmount', *MONEY)), 'Unknown normalized episode money')
    investment = row['investedAmount']
    if not row['entered']:
        require(row['positionState'] == 'notEntered' and investment == 0 and all(row[name] == 0 for name in MONEY)
                and all(row[name] is None for name in ('entryDate', 'entryPrice', 'exitDate', 'holdingSessions')), 'Unentered fabricated as trade')
        return 0., 0., None
    require(investment > 0 and finite(row['entryPrice']) and row['entryPrice'] > 0
            and isinstance(row['entryDate'], str), 'Missing actual entered capital/price/date')
    require(row['holdingSessions'] is None or type(row['holdingSessions']) is int and row['holdingSessions'] > 0, 'Invalid observed holding age')
    if row['positionState'] == 'closed':
        require(isinstance(row['exitDate'], str) and row['exitDate'] >= row['entryDate']
                and row['openRealizedPnlAmount'] == row['openUnrealizedPnlAmount'] == 0, 'Closed episode contains open values')
        pnl = row['closedPnlAmount']
    else:
        require(row['positionState'] == 'openAtCutoff' and row['exitDate'] is None and row['closedPnlAmount'] == 0,
                'Open episode became a closed observation')
        pnl = math.fsum((row['openRealizedPnlAmount'], row['openUnrealizedPnlAmount']))
    rate = pnl / investment
    require(finite(rate), 'Nonfinite invested-capital return')
    return investment, pnl, rate


def pair_reference(control, candidate):
    ic, pc, rc = episode_values(control)
    iv, pv, rv = episode_values(candidate)
    both = control['entered'] and candidate['entered']
    same_entry = both and control['entryDate'] == candidate['entryDate'] and control['entryPrice'] == candidate['entryPrice']
    group = ('bothSameEntry' if same_entry else 'bothDifferentEntry' if both else 'controlOnly'
             if control['entered'] else 'candidateOnly' if candidate['entered'] else 'neither')
    components = {name: 0. for name in COMPONENTS}
    if both:
        components['returnProfileComponent'] = math.fsum((ic / 2, iv / 2)) * math.fsum((rv, -rc))
        components['committedCapitalComponent'] = math.fsum((rc / 2, rv / 2)) * math.fsum((iv, -ic))
    elif control['entered']:
        components['controlOnlyComponent'] = -pc
    elif candidate['entered']:
        components['candidateOnlyComponent'] = pv
    delta = math.fsum((pv, -pc))
    require(all(finite(value) for value in components.values()) and finite(delta), 'Nonfinite pair decomposition')
    near(math.fsum(components.values()), delta, 'Four components do not sum to exact pair money difference')
    closed_both = same_entry and control['positionState'] == candidate['positionState'] == 'closed'
    timing = ('later' if candidate['exitDate'] > control['exitDate'] else 'earlier'
              if candidate['exitDate'] < control['exitDate'] else 'same') if closed_both else None
    known_age = both and control['holdingSessions'] is not None and candidate['holdingSessions'] is not None
    return {'control': deepcopy(control), 'candidate': deepcopy(candidate), 'group': group,
        'maturity': control['positionState'] + '__' + candidate['positionState'],
        'controlPnl': pc, 'candidatePnl': pv, 'deltaPnl': delta, **components,
        'controlReturnPct': rc * 100 if rc is not None else None, 'candidateReturnPct': rv * 100 if rv is not None else None,
        'holdingDeltaSessions': candidate['holdingSessions'] - control['holdingSessions'] if known_age else None,
        'closedPairTiming': timing, 'baselineOutcome': ('win' if pc > 0 else 'loss' if pc < 0 else 'zero') if closed_both else None}


def aggregate_reference(rows):
    deltas = sorted(math.fsum((r['candidateReturnPct'], -r['controlReturnPct'])) for r in rows
                    if r['control']['entered'] and r['candidate']['entered'])
    count = len(deltas)
    middle = (deltas[count // 2] if count % 2 else math.fsum((deltas[count // 2 - 1] / 2, deltas[count // 2] / 2))) if count else None
    out = {'count': len(rows), 'groupCounts': {g: sum(r['group'] == g for r in rows) for g in GROUPS},
           'maturityCounts': {a + '__' + b: sum(r['maturity'] == a + '__' + b for r in rows) for a in STATES for b in STATES},
           'commonEnteredCount': count,
           **{key: math.fsum(r[key] for r in rows) for key in (*COMPONENTS, 'controlPnl', 'candidatePnl', 'deltaPnl')},
           'meanReturnDeltaPct': math.fsum(v / count for v in deltas) if count else None,
           'medianReturnDeltaPct': middle}
    for side in ('control', 'candidate'):
        out[side + 'EnteredCount'] = sum(r[side]['entered'] for r in rows)
        out[side + 'ClosedCount'] = sum(r[side]['positionState'] == 'closed' for r in rows)
        out[side + 'OpenCount'] = sum(r[side]['positionState'] == 'openAtCutoff' for r in rows)
        for name in MONEY:
            out[side + name[0].upper() + name[1:]] = math.fsum(r[side][name] for r in rows)
    near(math.fsum(out[name] for name in COMPONENTS), out['deltaPnl'], 'Aggregate component/cash difference')
    return out


def account_reference(original, summary, result, period, board, policy):
    require(summary['policyId'] == policy and summary['inputResultSha256'] == digest(result), 'Summary semantic result binding')
    episodes, ledger = reconstruct_episodes(result)
    schedule = [row['signals']['immediate'] for row in original]
    same(result['research']['identity']['signalSchedule'], schedule, 'Original ordered immediate schedule')
    require(result['research']['identity']['signalScheduleSha256'] == digest(schedule), 'Original schedule digest')
    opportunities = {row['signalId']: row for row in result['research']['opportunities']}
    require(len(opportunities) == len(result['research']['opportunities']) == len(original)
            and set(opportunities) == {row['formationId'] for row in original}, 'Original opportunity population differs')
    closed_summary = {(e['record']['symbol'], e['record']['entryDate']): e for e in summary['closedEpisodes']['episodes']}
    open_summary = {(e['record']['symbol'], e['record']['entryDate']): e for e in summary['openPositions']['positions']}
    require(len(closed_summary) == summary['closedEpisodes']['count'] == len(result['closedTrades'])
            and len(open_summary) == summary['openPositions']['count'] == len(result['positions']), 'Summary episode counts differ')
    for row in result['closedTrades']:
        saved = closed_summary[(row['symbol'], row['entryDate'])]
        same(saved['record'], row, 'Full original closed episode summary')
        near(saved['pnlAmount'], row['pnlAmount'], 'Summary closed cash PnL')
        near(saved['netReturnPct'], row['returnPct'], 'Summary closed net return')
        require(saved['holdingSessions'] == row['holdingDays'], 'Summary closed age')
    for row in result['positions']:
        saved = open_summary[(row['symbol'], row['entryDate'])]
        same(saved['record'], row, 'Full original open episode summary')
        near(saved['unrealizedPnlAmount'], row['marketValue'] - row['costBasis'], 'Summary terminal unrealized mark')
        near(saved['realizedPnlWhileOpen'], row['realizedPnl'], 'Summary partial realized PnL')
    paid = result['research']['feeLedger']['paidLedger']
    require(len(paid) == len(result['trades']), 'Paid-fee/fill population differs')
    for index, (fee, fill) in enumerate(zip(paid, result['trades'])):
        require(fee['tradeIndex'] == index and fee['fillId'] == fill['fillId'] and fee['quoteId'] == fill['feeQuoteId'], 'Paid-fee linkage differs')
        for key, value in fee['fill'].items():
            same(fill[key], value, 'Saved fee-ledger fill.' + key)
        near(fee['quote']['breakdown']['total'], fill['fee'], 'Fee payment differs from actual fill fee')
    near(result['research']['feeLedger']['paidFeeTotal'], ledger['fees'], 'Paid fees include every original fill once')
    near(summary['account']['feesPaid'], ledger['fees'], 'Original summary fees')
    seen, output = set(), {}
    for formed in original:
        sid = formed['formationId']
        signal, d, opportunity = formed['signals']['immediate'], formed['accountDispositions'][policy], opportunities[sid]
        require(sid not in output and sid == signal['signalId'] == d['signalId']
                and formed['formationDate'] == signal['signalDate'] == d['signalDate'] == opportunity['signalDate']
                and formed['symbol'] == opportunity['symbol'], 'Original formation identity/clock changed')
        same(d['accountOpportunity'], opportunity, 'Full parent sidecar account opportunity')
        require(d['status'] == opportunity['status'] and d['reason'] == opportunity['reason'], 'Opportunity reason/status differs')
        same(d['filledQuantity'], opportunity['filledQuantity'], 'Original filled quantity')
        same(d['attemptDate'], opportunity.get('attemptDate'), 'Original attempted entry date')
        episode = episodes.get(sid)
        require((episode is not None) == (opportunity['filledQuantity'] > 0), 'Opportunity entry differs from actual buy')
        normalized = normalized_reference(episode, d)
        require((d['positionState'] == normalized['positionState'] if episode is not None else d['positionState'] not in ('closed', 'openAtCutoff'))
                and d['entryDate'] == normalized['entryDate']
                and d['exitDate'] == normalized['exitDate'], 'Original sidecar maturity/episode dates differ')
        if episode:
            seen.add(sid)
            require(episode['symbol'] == formed['symbol'] and episode['signalDate'] == formed['formationDate']
                    and episode['entryQuantity'] == opportunity['filledQuantity'], 'Buy matched wrong original signal')
            if episode['positionState'] == 'closed':
                near(d['closedPnlAmount'], episode['closedPnlAmount'], 'Parent closed PnL versus original cash flows')
                near(d['closedReturnPct'], episode['closedPnlAmount'] / episode['investedAmount'] * 100, 'Parent closed return')
                require(d['openPositionAtCutoff'] is None, 'Closed episode has a terminal open object')
            else:
                require(d['closedPnlAmount'] is None and d['closedReturnPct'] is None, 'Open position treated as a closed return')
                saved = open_summary[(episode['symbol'], episode['entryDate'])]
                same(d['openPositionAtCutoff'], saved, 'Parent complete open-position evidence')
                require(saved['observedHoldingSessions'] == episode['holdingSessions']
                        and saved['markStaleAtCutoff'] is episode['openMarkStale'], 'Open age/mark maturity differs')
        else:
            require(d['closedPnlAmount'] is None and d['closedReturnPct'] is None and d['openPositionAtCutoff'] is None,
                    'Unentered opportunity fabricated as a position')
        output[sid] = normalized
    require(seen == set(episodes), 'Original buy/episode left unassigned')
    assigned = math.fsum(row['closedPnlAmount'] + row['openRealizedPnlAmount'] + row['openUnrealizedPnlAmount'] for row in output.values())
    account = summary['account']
    near(account['initialCapital'], result['config']['initialCapital'], 'Original capital')
    near(account['finalEquity'], ledger['finalEquity'], 'Original final equity')
    near(account['netPnlAmount'], ledger['finalEquity'] - result['config']['initialCapital'], 'Net account cash bridge')
    near(account['netReturnPct'], (ledger['finalEquity'] / result['config']['initialCapital'] - 1) * 100, 'Account return denominator')
    residual = account['netPnlAmount'] - assigned
    near(residual, summary['reconciliation']['rightsAttributionGapAmount'], 'Original unallocated residual preserved')
    bridge = {'period': period, 'board': board, 'policy': policy, 'formationCount': len(output),
        'entered': len(episodes), 'closed': len(closed_summary), 'open': len(open_summary),
        'assignedPnl': assigned, 'unallocatedResidual': residual, 'netPnlAmount': account['netPnlAmount'],
        'initialCapital': account['initialCapital'], 'finalEquity': account['finalEquity'],
        'accountReturnPct': account['netReturnPct'], 'maxDrawdownPct': account['maxDrawdownPct'],
        'valuationUnreliableDays': len(summary['quality']['valuationUnreliableDates']),
        'numericUnknownCount': len(summary['quality']['numericUnknownPaths']), 'integrityIssueCount': len(summary['quality']['integrityIssues']),
        'openMarkStaleCount': sum(row['openMarkStale'] for row in output.values()), 'passed': True}
    return output, bridge


def matrix_reference(pairs):
    names = ('groupRows', 'maturityRows', 'formationYearRows', 'formationEpochRows', 'closedPairTimingRows', 'closedPairBaselineOutcomeRows')
    matrices = {name: [] for name in names}
    for period in PERIODS:
        for board in BOARDS:
            selected = [row for row in pairs if (row['period'], row['board']) == (period, board)]
            identity = {'period': period, 'board': board}
            for name, key, values in (
                ('groupRows', 'group', GROUPS),
                ('maturityRows', 'maturity', tuple(a + '__' + b for a in STATES for b in STATES)),
                ('closedPairTimingRows', 'closedPairTiming', ('later', 'same', 'earlier')),
                ('closedPairBaselineOutcomeRows', 'baselineOutcome', ('win', 'loss', 'zero'))):
                for value in values:
                    matrices[name].append({**identity, key: value, **aggregate_reference([r for r in selected if r[key] == value])})
            for name, key, values in (
                ('formationYearRows', 'formationYear', period.split('-')),
                ('formationEpochRows', 'formationEpoch', ('oldPeriod',) if period == PERIODS[0] else ('before20240924', 'from20240924'))):
                for value in values:
                    for group in GROUPS:
                        matrices[name].append({**identity, key: value, 'group': group,
                            **aggregate_reference([r for r in selected if r[key] == value and r['group'] == group])})
            total = aggregate_reference(selected)
            for name in ('groupRows', 'maturityRows', 'formationYearRows', 'formationEpochRows'):
                cells = [row for row in matrices[name] if (row['period'], row['board']) == (period, board)]
                require(sum(row['count'] for row in cells) == total['count'], 'Partition opportunity count: ' + name)
                for key in (*COMPONENTS, 'deltaPnl', 'controlPnl', 'candidatePnl'):
                    near(math.fsum(row[key] for row in cells), total[key], 'Complete partition money: ' + name + '.' + key)
            mature = aggregate_reference([r for r in selected if r['group'] == 'bothSameEntry'
                and r['control']['positionState'] == r['candidate']['positionState'] == 'closed'])
            for name in ('closedPairTimingRows', 'closedPairBaselineOutcomeRows'):
                cells = [row for row in matrices[name] if (row['period'], row['board']) == (period, board)]
                require(sum(row['count'] for row in cells) == mature['count'], 'Closed same-entry descriptive subset count')
                for key in (*COMPONENTS, 'deltaPnl', 'controlPnl', 'candidatePnl'):
                    near(math.fsum(row[key] for row in cells), mature[key], 'Closed subset descriptive partition money')
    return matrices


def plan_reference(directory):
    plan = read(directory / 'plan.json')
    require(plan['schema'] == VERSION, 'Wrong registered diagnostic')
    same(plan['policies'], list(POLICIES), 'Both original policies')
    same(plan['groups'], list(GROUPS), 'All five entry groups')
    same(plan['trialAccounting'], TRIALS, 'No strategy/account scope')
    same(plan['expectedRows'], EXPECTED, 'All original complete matrices')
    require(plan['profitHurdles'] is None and plan['holdoutClaim'] is False
            and plan['certified'] is False and plan['targetAchieved'] is False, 'Unsupported strategy/holdout claim')
    for mapping in (plan['sourcePins'], plan['inputPins']):
        for name, expected in mapping.items():
            pinned({'path': name, 'sha256': expected})
    require(plan['sourcePins'][Path(__file__).resolve().relative_to(ROOT).as_posix()] == sha(__file__), 'Verifier source not frozen')
    protocol_path = ROOT / 'docs/reclaim-exit-mechanism-v1.json'
    require(plan['inputPins'][protocol_path.relative_to(ROOT).as_posix()] == sha(protocol_path), 'Protocol not frozen')
    protocol = read(protocol_path)
    for key, value in protocol.items():
        same(plan[key], value, 'Plan/protocol.' + key)
    return plan


def parent_reference(plan):
    report, comparison, proof, readout_proof, delivery = [read(PARENT / name) for name in
        ('readout.json', 'comparison.json', 'validation.json', 'readout-validation.json', 'delivery.json')]
    for name in ('readout.json', 'comparison.json', 'validation.json', 'readout-validation.json', 'delivery.json'):
        path = PARENT / name
        rel = path.relative_to(ROOT).as_posix()
        require(plan['inputPins'][rel] == sha(path), 'Parent receipt not frozen')
        if name != 'delivery.json':
            require(delivery['files'][rel] == sha(path), 'Parent delivery no longer binds evidence')
    require(proof['passed'] is True and readout_proof['passed'] is True
            and proof['planSha256'] == readout_proof['planSha256'] == delivery['planSha256'] == sha(PARENT / 'plan.json')
            and proof['comparisonSha256'] == readout_proof['comparisonSha256'] == sha(PARENT / 'comparison.json')
            and readout_proof['accountValidationSha256'] == sha(PARENT / 'validation.json')
            and readout_proof['readoutSha256'] == sha(PARENT / 'readout.json'), 'Parent independent proof chain broken')
    same(plan['accountArtifacts'], comparison['artifacts'], 'Exact original 12 account references')
    same(proof['accountArtifacts'], plan['accountArtifacts'], 'Account byte references in original audit')
    same(plan['accountReadout'], reference(PARENT / 'readout.json'), 'Original audited account readout')
    same(plan['opportunities'], report['opportunityRowsArtifact'], 'Complete original opportunity sidecar')
    require(plan['opportunities']['sha256'] == readout_proof['opportunityRowsArtifactSha256'], 'Opportunity sidecar proof binding')
    previous_delivery = read(pinned(plan['previousDelivery']))
    require(pinned(plan['previousDelivery']) == (PREVIOUS / 'delivery.json').resolve(), 'Wrong diagnostic predecessor')
    previous_proof = read(PREVIOUS / 'validation.json')
    for name in ('validation.json', 'readout.json'):
        path = PREVIOUS / name
        rel = path.relative_to(ROOT).as_posix()
        require(plan['inputPins'][rel] == previous_delivery['files'][rel] == sha(path), 'Previous diagnostic chain changed')
    require(previous_proof['passed'] is True and previous_proof['readoutSha256'] == sha(PREVIOUS / 'readout.json')
            and previous_proof['planSha256'] == sha(PREVIOUS / 'plan.json'), 'Previous diagnostic not audited')
    for period in PERIODS:
        for board in BOARDS:
            for policy in POLICIES:
                for ref in plan['accountArtifacts'][period][board][policy].values():
                    pinned(ref)
    return report


def verify(directory):
    directory = directory.resolve()
    require(not (directory / 'validation.json').exists(), 'Refuse to overwrite independent validation')
    plan = plan_reference(directory)
    parent = parent_reference(plan)
    tests, start = read(directory / 'pre-extraction-tests.json'), read(directory / 'extraction-start.json')
    require(tests['passed'] is True and tests['planSha256'] == sha(directory / 'plan.json'), 'Pretests not bound to plan')
    same(start['preExtractionTests'], reference(directory / 'pre-extraction-tests.json'), 'Extraction start binds actual pretests')
    require(len(tests['commands']) == 2 and all(row['exitCode'] == 0 for row in tests['commands']), 'Missing successful prerequisite commands')
    same([row['command'][1:] for row in tests['commands']],
         [['-X', 'utf8', '-B', '-m', 'unittest', 'tests.test_research_exit_mechanism', '-v'],
          ['-X', 'utf8', '-B', 'scripts/verify-reclaim-exit-mechanism.py', '--self-test']], 'Actual preregistered test command identities')
    for row in tests['commands']:
        pinned(row['log'])
    require(timestamp(plan['registeredAt']) <= timestamp(tests['startedAt']) <= timestamp(tests['completedAt'])
            <= timestamp(start['startedAt']), 'Extraction preceded registered successful tests')
    original = records(pinned(plan['opportunities']))
    require(len(original) == 8155 and len({(r['period'], r['board'], r['formationId']) for r in original}) == 8155,
            'Original opportunity identity/count changed')
    pairs, bridges, differences = [], [], []
    for period in PERIODS:
        for board in BOARDS:
            source = [row for row in original if (row['period'], row['board']) == (period, board)]
            accounts, account_bridges = {}, {}
            for policy in POLICIES:
                refs = plan['accountArtifacts'][period][board][policy]
                result, summary = read(pinned(refs[policy + '.result.json.gz'])), read(pinned(refs[policy + '.summary.json']))
                accounts[policy], account_bridges[policy] = account_reference(source, summary, result, period, board, policy)
                bridges.append(account_bridges[policy])
                print(json.dumps({'phase': 'independent-episode-ledger', 'period': period, 'board': board,
                    'policy': policy, 'formations': len(source), 'entered': account_bridges[policy]['entered']}), flush=True)
            selected = []
            for row in source:
                day, sid = row['formationDate'], row['formationId']
                epoch = 'oldPeriod' if period == PERIODS[0] else 'before20240924' if day < '2024-09-24' else 'from20240924'
                selected.append({'period': period, 'board': board, 'formationId': sid, 'symbol': row['symbol'],
                    'formationDate': day, 'formationYear': day[:4], 'formationEpoch': epoch,
                    **pair_reference(accounts['trail10'][sid], accounts['volatilityTrail'][sid])})
            pairs.extend(selected)
            total = aggregate_reference(selected)
            control, candidate = (account_bridges[policy] for policy in POLICIES)
            near(control['initialCapital'], candidate['initialCapital'], 'Independent accounts must have equal original capital')
            residual_delta = candidate['unallocatedResidual'] - control['unallocatedResidual']
            net_delta = candidate['netPnlAmount'] - control['netPnlAmount']
            near(total['deltaPnl'] + residual_delta, net_delta, 'All pair cash plus original residual equals account difference')
            near(total['controlPnl'], control['assignedPnl'], 'All control episodes reconcile')
            near(total['candidatePnl'], candidate['assignedPnl'], 'All candidate episodes reconcile')
            differences.append({'period': period, 'board': board, **total, 'residualDelta': residual_delta,
                'accountNetPnlDelta': net_delta, 'controlNetPnl': control['netPnlAmount'], 'candidateNetPnl': candidate['netPnlAmount'],
                'initialCapitalEach': control['initialCapital'], 'passed': True})
    same(records(directory / 'pairs.jsonl.gz'), pairs, 'Every independent original pair and algebraic component')
    matrices = matrix_reference(pairs)
    report = read(directory / 'readout.json')
    require(timestamp(start['startedAt']) <= timestamp(report['generatedAt']), 'Readout predates extraction')
    expected = {'schema': VERSION, 'generatedAt': report['generatedAt'], 'planSha256': sha(directory / 'plan.json'),
        'inputPins': plan['inputPins'], 'pairs': reference(directory / 'pairs.jsonl.gz'), 'pairCount': len(pairs),
        'accountBridges': bridges, 'accountDifferenceRows': differences, **matrices,
        'curveEpochRows': parent['curveEpochRows'], 'sourceLimitations': parent['limitations'],
        'rules': plan['rules'], 'trialAccounting': TRIALS, 'profitHurdles': None, 'selectedRule': None,
        'holdoutClaim': False, 'certified': False, 'targetAchieved': False}
    same(report, expected, 'Full independent saved-account diagnostic')
    actual_counts = {key: len(pairs) if key == 'pairRows' else len(report[key]) for key in EXPECTED}
    same(actual_counts, EXPECTED, 'Complete matrix dimensions')
    plan_reference(directory)
    proof = {'schema': VERSION + '-independent-validation', 'passed': True, 'verifiedAt': datetime.now(timezone.utc).isoformat(),
        'planSha256': sha(directory / 'plan.json'), 'readoutSha256': sha(directory / 'readout.json'),
        'pairsSha256': sha(directory / 'pairs.jsonl.gz'), 'verifierSha256': sha(__file__),
        'parentValidationSha256': sha(PARENT / 'validation.json'), 'parentReadoutValidationSha256': sha(PARENT / 'readout-validation.json'),
        'preExtractionTests': reference(directory / 'pre-extraction-tests.json'),
        'inputPins': plan['inputPins'], 'sourcePins': plan['sourcePins'], 'accountArtifacts': plan['accountArtifacts'],
        'counts': {**actual_counts, 'existingAccountsRead': 12, 'newAccounts': 0, 'newStrategyTrials': 0,
                   'filledEpisodes': sum(row['entered'] for row in bridges), 'curveEpochRowsInherited': len(parent['curveEpochRows'])},
        'checks': ['Exact frozen input/source hashes, actual successful pretests before extraction, and prior audit chain',
            'All original buy identities and chronological symbol-active sell assignment, including partial/reentry handling',
            'Every actual outlay, fee, share transition, cash-after state and terminal cost/mark reconciled',
            'Every original closed/open summary episode and every one of8155 original opportunities retained',
            'All four symmetric/single-side components independently reconcile each pair and all six account differences',
            'All empty entry/maturity/time cells retained; closed same-entry descriptive subsets reconcile separately',
            'Twelve original cash bridges keep unallocated residual separate; continuous epochs inherited byte-semantically'],
        'scope': {'marketData': 'No market/source reload; original execution and risk decisions rely on pinned prior audits',
                  'accounts': 'No account execution; original saved fills and positions independently replayed for cash/episode attribution',
                  'interpretation': 'Algebra of actual saved paths, not a causal exit effect, future selector, or counterfactual account'},
        'certified': False, 'targetAchieved': False}
    with (directory / 'validation.json').open('x', encoding='utf-8') as stream:
        json.dump(proof, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write('\n')
    print(json.dumps({'passed': True, 'validation': reference(directory / 'validation.json'), 'counts': proof['counts']}), flush=True)
    return proof


def synthetic_fixture():
    """Partial sale, full closure, same-symbol reentry and stale open mark."""
    days = ['2024-01-02', '2024-01-03', '2024-01-04', '2024-01-05', '2024-01-06', '2024-01-07']
    cash = 10000.
    fills = []
    specs = [(1, 'buy', 100, 10., 2., 0, 100, 'open', 'first', None),
             (2, 'sell', 40, 12., 1., 100, 60, 'reduce', None, 479. - 1002. * .4),
             (3, 'sell', 60, 9., 1., 60, 0, 'exit', None, 539. - (1002. - 1002. * .4)),
             (4, 'buy', 50, 20., 2., 0, 50, 'open', 'second', None)]
    for i, (day, side, qty, price, fee, before, after, action, sid, pnl) in enumerate(specs):
        amount = qty * price
        cash += amount - fee if side == 'sell' else -amount - fee
        fills.append({'date': days[day], 'signalDate': days[day - 1], 'symbol': 's', 'side': side,
            'quantity': qty, 'price': price, 'amount': amount, 'fee': fee, 'cashAfter': cash,
            'quantityBefore': before, 'quantityAfter': after, 'positionAction': action,
            'signalId': sid, 'realizedPnl': pnl, 'fillId': 'f' + str(i)})
    curve = [{'date': day, 'cash': 10000., 'marketValue': 0., 'equity': 10000.} for day in days]
    curve[-1].update(cash=cash, marketValue=1050., equity=cash + 1050.)
    return {'config': {'initialCapital': 10000.}, 'trades': fills, 'curve': curve,
        'closedTrades': [{'symbol': 's', 'entryDate': days[1], 'exitDate': days[3], 'pnlAmount': 16.,
            'investedAmount': 1002., 'returnPct': 16. / 1002. * 100, 'holdingDays': 3}],
        'positions': [{'symbol': 's', 'entryDate': days[4], 'quantity': 50, 'costBasis': 1002., 'investedAmount': 1002.,
            'realizedPnl': 0., 'markPrice': 21., 'priceDate': days[4], 'marketValue': 1050.,
            'lots': [{'date': days[4], 'quantity': 50, 'cost': 1002.}]}]}


def self_test():
    tests = 0
    data = synthetic_fixture()
    before = digest(data)
    episodes, ledger = reconstruct_episodes(data)
    require(digest(data) == before and set(episodes) == {'first', 'second'}, 'Independent replay must preserve source and reentry identity')
    near(episodes['first']['closedPnlAmount'], 16., 'Partial sales include buy and each sell fee once')
    near(episodes['second']['openUnrealizedPnlAmount'], 48., 'Terminal open mark is not closed')
    require(episodes['first']['sellFillIds'] == ['f1', 'f2'] and episodes['second']['sellFillIds'] == []
            and episodes['second']['openMarkStale'] is True and ledger['fees'] == 6., 'Sell IDs ignored; partial and reentry retained')
    tests += 1
    for field in ('costBasis', 'quantity', 'investedAmount'):
        bad = deepcopy(data); bad['positions'][0][field] += 1
        try:
            reconstruct_episodes(bad)
        except ValueError:
            pass
        else:
            raise ValueError('Corrupt terminal economics accepted: ' + field)
    tests += 1
    bad_cases = []
    bad = deepcopy(data); bad['trades'][1]['cashAfter'] += 2; bad_cases.append(bad)
    bad = deepcopy(data); bad['trades'][0]['signalId'] = None; bad_cases.append(bad)
    bad = deepcopy(data); bad['trades'][3]['signalId'] = 'first'; bad_cases.append(bad)
    bad = deepcopy(data); bad['trades'][0]['positionAction'] = 'add'; bad_cases.append(bad)
    bad = deepcopy(data); bad['closedTrades'] = []; bad_cases.append(bad)
    bad = deepcopy(data); bad['actionLedger'] = [{'cash': 1}]; bad_cases.append(bad)
    bad = deepcopy(data); bad['trades'][1], bad['trades'][2] = bad['trades'][2], bad['trades'][1]; bad_cases.append(bad)
    for bad in bad_cases:
        try:
            reconstruct_episodes(bad)
        except ValueError:
            pass
        else:
            raise ValueError('Invalid identity/cash/order/rights contract accepted')
    tests += 1
    first = normalized_reference(episodes['first'], {'reason': 'filled', 'positionState': 'closed'})
    second = normalized_reference(episodes['second'], {'reason': 'filled', 'positionState': 'openAtCutoff'})
    absent = normalized_reference(None, {'reason': 'cutoff', 'positionState': 'censored'})
    require(absent['originalPositionState'] == 'censored' and absent['positionState'] == 'notEntered'
            and episode_values(absent) == (0., 0., None), 'Original unentered maturity retained without fake return')
    tests += 1
    other = deepcopy(first)
    other.update(investedAmount=2004., closedPnlAmount=80., exitDate='2024-01-07', holdingSessions=5)
    paired, reversed_pair = pair_reference(first, other), pair_reference(other, first)
    require(paired['group'] == 'bothSameEntry' and paired['closedPairTiming'] == 'later'
            and paired['baselineOutcome'] == 'win', 'Capital change is not different entry; closed timing descriptive only')
    for key in (*COMPONENTS, 'deltaPnl'):
        near(paired[key], -reversed_pair[key], 'Symmetric decomposition reverses sign')
    near(paired['deltaPnl'], 64., 'Actual episode difference')
    tests += 1
    different_price = deepcopy(other); different_price['entryPrice'] += 1e-12
    changed = pair_reference(first, different_price)
    require(changed['group'] == 'bothDifferentEntry' and changed['closedPairTiming'] is None and changed['baselineOutcome'] is None,
            'Entry-price sameness is exact, not a rounded tolerance')
    different_date = deepcopy(other); different_date['entryDate'] = '2024-01-04'
    require(pair_reference(first, different_date)['group'] == 'bothDifferentEntry', 'Entry date differs')
    tests += 1
    mixed = pair_reference(first, second)
    require(mixed['maturity'] == 'closed__openAtCutoff' and mixed['closedPairTiming'] is None,
            'Open marked return must not become a completed-trade timing sample')
    tests += 1
    single_c, single_v, neither = pair_reference(first, absent), pair_reference(absent, other), pair_reference(absent, absent)
    require(single_c['group'] == 'controlOnly' and single_v['group'] == 'candidateOnly' and neither['group'] == 'neither'
            and single_c['candidateReturnPct'] is None and single_v['controlReturnPct'] is None
            and neither['controlReturnPct'] is None and neither['candidateReturnPct'] is None, 'Missing side remains null return')
    near(single_c['controlOnlyComponent'], -16., 'Control-only signed contribution')
    near(single_v['candidateOnlyComponent'], 80., 'Candidate-only signed contribution')
    tests += 1
    aggregate = aggregate_reference([paired, mixed, single_c, single_v, neither])
    require(aggregate['count'] == 5 and aggregate['commonEnteredCount'] == 2
            and len(aggregate['groupCounts']) == 5 and len(aggregate['maturityCounts']) == 9,
            'Complete opportunity and maturity domains; no missing-side zero-return means')
    empty = aggregate_reference([])
    require(empty['meanReturnDeltaPct'] is None and empty['medianReturnDeltaPct'] is None
            and empty['deltaPnl'] == 0 and not any(empty['maturityCounts'].values()), 'Empty matrix cells retained')
    tests += 1
    fixture_rows = [{'period': '2024-2025', 'board': 'main', 'formationId': str(i), 'symbol': 's',
        'formationDate': '2024-09-24', 'formationYear': '2024', 'formationEpoch': 'from20240924', **r}
        for i, r in enumerate((paired, mixed, single_c, single_v, neither))]
    matrices = matrix_reference(fixture_rows)
    require(all(len(matrices[k]) == EXPECTED[k] for k in matrices), 'All fixed matrices include empty sources/time cells')
    require(sum(r['count'] for r in matrices['closedPairTimingRows']) == 1
            and sum(r['count'] for r in matrices['formationEpochRows'] if r['formationEpoch'] == 'before20240924') == 0,
            'Maturity and descriptive date membership do not gate whole population')
    tests += 1
    damaged = deepcopy(first); damaged['investedAmount'] = 0.
    for row in (damaged, {**absent, 'closedPnlAmount': 1.}):
        try:
            pair_reference(row, other)
        except ValueError:
            pass
        else:
            raise ValueError('Unknown investment or absent-side money accepted')
    tests += 1
    require(math.fsum((paired['deltaPnl'], single_c['deltaPnl'], single_v['deltaPnl'])) + 3. - 1.
            == (other['closedPnlAmount'] * 2 + 3.) - (first['closedPnlAmount'] * 2 + 1.),
            'Original residual difference added once after full pair sum')
    tests += 1
    print(json.dumps({'passed': True, 'selfTests': tests, 'realSourceRead': False, 'actualAccountRuns': 0}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    try:
        verify(args.out)
    except Exception as error:
        print(json.dumps({'passed': False, 'type': type(error).__name__, 'message': str(error),
                          'verifierSha256': sha(__file__), 'scope': 'Frozen account inputs unchanged'}), flush=True)
        raise


if __name__ == '__main__':
    main()
