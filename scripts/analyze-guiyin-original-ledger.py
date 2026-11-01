"""Reconstruct source-ordered flat-to-flat episodes, never infer account equity."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1'
SOURCE = BASE / 'guiyin-original-001/归因.xls'
OUT = BASE / 'guiyin-ledger-audit-001'
RUNTIME = BASE / 'analysis-runtime-xlrd-2.0.2/xlrd-2.0.2-py2.py3-none-any.whl'
SPLIT = '2024-09-24'
TRADE_TYPES = {'证券买入': 1, '融资买入': 1, '证券卖出': -1}


def ref(path):
    return {'path': str(path.relative_to(ROOT)).replace('\\', '/'),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def money(value):
    raw = D(str(value))
    rounded = raw.quantize(D('.01'))
    if abs(raw - rounded) > D('.000001'):
        raise ValueError('Source monetary precision needs review')
    return rounded


def integral(value):
    raw = D(str(value))
    if raw != raw.to_integral_value():
        raise ValueError('Non-integral share quantity')
    return int(raw)


def f(value):
    return float(value) if value is not None else None


def board(code):
    if code.startswith(('4', '8', '92')): return 'BSE'
    if code.startswith(('688', '689')): return 'STAR'
    if code.startswith(('300', '301')): return 'ChiNext'
    return 'SHSZ-main'


def symbol(code):
    return ('bj' if board(code) == 'BSE' else 'sh' if code.startswith('6') else 'sz') + code


def reconstruct(trades):
    states, episodes, events = {}, [], []
    for t in trades:
        code = t['code']
        before, after = t['beforeQuantity'], t['afterQuantity']
        if code not in states:
            states[code] = {'quantity': before, 'basis': D(0), 'active': None}
        state = states[code]
        if state['quantity'] != before:
            raise ValueError('Discontinuous source position balance')
        if state['active'] is None:
            state['active'] = {'code': code, 'name': t['name'], 'board': board(code),
                               'startRow': t['sourceRow'], 'entryDate': t['date'],
                               'openingInventoryUnknownCost': before != 0,
                               'openingQuantity': before, 'rows': [], 'financedBuyRows': 0,
                               'buyOutlay': D(0), 'buyGross': D(0), 'sellNet': D(0),
                               'visibleCharges': D(0), 'cumTradeCash': D(0), 'peakNetOutlay': D(0),
                               'buys': 0, 'sells': 0, 'buyAfterSellRows': 0,
                               'abovePriorAverageCostAdds': 0, 'belowPriorAverageCostAdds': 0,
                               'entryDayBuyGross': D(0), 'lastExitDate': None}
        episode = state['active']
        episode['rows'].append(t['sourceRow'])
        gross, net, cost = D(t['grossYuan']), D(t['tradeCashDeltaYuan']), D(t['visibleChargesYuan'])
        episode['visibleCharges'] += cost
        episode['cumTradeCash'] += net
        episode['peakNetOutlay'] = max(episode['peakNetOutlay'], -episode['cumTradeCash'])
        if t['quantity'] > 0:
            if before > 0 and not episode['openingInventoryUnknownCost']:
                previous_average_cost = state['basis'] / before
                execution_average_price = gross / t['quantity']
                comparison = 1 if execution_average_price > previous_average_cost else -1 if execution_average_price < previous_average_cost else 0
                if comparison > 0: episode['abovePriorAverageCostAdds'] += 1
                if comparison < 0: episode['belowPriorAverageCostAdds'] += 1
                events.append({'sourceRow': t['sourceRow'], 'code': code, 'date': t['date'],
                               'kind': 'add-to-existing-holding', 'positionBefore': before,
                               'averageExecutionPrice': str(execution_average_price),
                               'priorRemainingAverageBuyCost': str(previous_average_cost),
                               'priceAbovePriorAverageCost': comparison > 0,
                               'meaning': 'Mechanical source comparison, not proof of trader intent or a profitable add rule.'})
            if episode['sells']: episode['buyAfterSellRows'] += 1
            episode['buys'] += 1
            episode['buyOutlay'] -= net
            episode['buyGross'] += gross
            if t['date'] == episode['entryDate']: episode['entryDayBuyGross'] += gross
            if t['business'] == '融资买入': episode['financedBuyRows'] += 1
            state['basis'] -= net
        else:
            episode['sells'] += 1
            episode['sellNet'] += net
            if before and not episode['openingInventoryUnknownCost']:
                state['basis'] *= D(after) / before
        state['quantity'] = after
        if after == 0:
            episode['lastExitDate'] = t['date']
            episode['endRow'] = t['sourceRow']
            episode['status'] = 'opening-inventory-cost-missing' if episode['openingInventoryUnknownCost'] else 'closed'
            episodes.append(episode)
            state['active'] = None
            state['basis'] = D(0)
    for code, state in states.items():
        if state['active'] is not None:
            episode = state['active']
            episode['status'] = 'open'
            episode['endingQuantity'] = state['quantity']
            episode['endRow'] = episode['rows'][-1]
            episodes.append(episode)
    normalized = []
    for e in episodes:
        dates = [t['date'] for t in trades if t['sourceRow'] in e['rows']]
        result = {k: str(v) if isinstance(v, D) else v for k, v in e.items()}
        result['episodeId'] = f'{e["code"]}:{e["startRow"]}:{e["endRow"]}'
        result['netPnlAfterVisibleTransactionChargesYuan'] = str(e['sellNet'] - e['buyOutlay']) if e['status'] == 'closed' else None
        result['pnlIsAfterCompleteFinancingAndFundingAudit'] = False
        result['cumulativePurchaseCostReturn'] = f((e['sellNet'] - e['buyOutlay']) / e['buyOutlay']) if e['status'] == 'closed' and e['buyOutlay'] > 0 else None
        result['peakNetTradeOutlayReturn'] = f((e['sellNet'] - e['buyOutlay']) / e['peakNetOutlay']) if e['status'] == 'closed' and e['peakNetOutlay'] > 0 else None
        result['holdingCalendarDays'] = (datetime.fromisoformat(dates[-1]) - datetime.fromisoformat(dates[0])).days
        result['observedTradeDays'] = len(set(dates))
        result['entryDayFractionOfAllGrossPurchases'] = f(e['entryDayBuyGross'] / e['buyGross']) if e['buyGross'] else None
        normalized.append(result)
    return sorted(normalized, key=lambda x: x['startRow']), events


def metrics(episodes):
    selected = [e for e in episodes if e['status'] == 'closed']
    wins = [e for e in selected if D(e['netPnlAfterVisibleTransactionChargesYuan']) > 0]
    losses = [e for e in selected if D(e['netPnlAfterVisibleTransactionChargesYuan']) < 0]
    def mean(rows, key): return sum(D(str(x[key])) for x in rows) / len(rows) if rows else None
    wp = sum(D(e['netPnlAfterVisibleTransactionChargesYuan']) for e in wins)
    lp = -sum(D(e['netPnlAfterVisibleTransactionChargesYuan']) for e in losses)
    wroi, lroi = mean(wins, 'cumulativePurchaseCostReturn'), mean(losses, 'cumulativePurchaseCostReturn')
    wpeak, lpeak = mean(wins, 'peakNetTradeOutlayReturn'), mean(losses, 'peakNetTradeOutlayReturn')
    sorted_wins = sorted(wins, key=lambda e: D(e['netPnlAfterVisibleTransactionChargesYuan']), reverse=True)
    return {'closedEpisodes': len(selected), 'wins': len(wins), 'losses': len(losses), 'breakeven': len(selected)-len(wins)-len(losses),
            'winRate': len(wins)/len(selected) if selected else None,
            'meanWinnerPurchaseCostReturn': f(wroi), 'meanLoserPurchaseCostReturn': f(lroi),
            'realizedReturnPayoffRatioAfterVisibleCharges': f(wroi / -lroi) if wroi is not None and lroi else None,
            'meanWinnerPeakNetOutlayReturn': f(wpeak), 'meanLoserPeakNetOutlayReturn': f(lpeak),
            'peakNetOutlayReturnPayoffRatio': f(wpeak / -lpeak) if wpeak is not None and lpeak else None,
            'meanWinningPnlYuan': f(wp / len(wins)) if wins else None,
            'meanLosingPnlYuan': f(-lp / len(losses)) if losses else None,
            'monetaryPayoffRatio': f((wp / len(wins)) / (lp / len(losses))) if wins and losses else None,
            'grossWinningPnlYuan': str(wp), 'absoluteLosingPnlYuan': str(lp), 'netClosedPnlYuan': str(wp-lp),
            'profitFactor': f(wp/lp) if lp else None,
            'visibleTransactionChargesYuan': str(sum(D(e['visibleCharges']) for e in selected)),
            'largestWinShareOfGrossProfits': f(D(sorted_wins[0]['netPnlAfterVisibleTransactionChargesYuan']) / wp) if sorted_wins else None,
            'topFiveWinShareOfGrossProfits': f(sum(D(e['netPnlAfterVisibleTransactionChargesYuan']) for e in sorted_wins[:5]) / wp) if wp else None,
            'meanCalendarHoldingDays': sum(e['holdingCalendarDays'] for e in selected)/len(selected) if selected else None,
            'medianPurchaseCostReturn': sorted(e['cumulativePurchaseCostReturn'] for e in selected)[len(selected)//2] if selected else None,
            'meanWinnerGrossPurchasesYuan': f(mean(wins, 'buyGross')), 'meanLoserGrossPurchasesYuan': f(mean(losses, 'buyGross')),
            'episodesWithFinancedBuys': sum(e['financedBuyRows'] > 0 for e in selected),
            'episodesWithBuysAfterPartialSales': sum(e['buyAfterSellRows'] > 0 for e in selected),
            'auditedAccountReturn': None, 'auditedAccountDrawdown': None,
            'completeFinancingCostAudit': False}


def main():
    if OUT.exists(): raise ValueError('Preserve completed original-ledger analysis')
    assert ref(RUNTIME)['sha256'] == 'ea762c3d29f4cca48d82df517b6d89fbce4db3107f9d78713e48cd321d5c9aa9'
    assert ref(SOURCE)['sha256'] == 'd51d4bcb03f2a99bf7e85a5bc01a582f980f458cb83955b77237ebd043961b3a'
    OUT.mkdir()
    contract = {'schema': 'original-guiyin-episode-analysis-contract-v1', 'registeredAt': datetime.now(timezone.utc).isoformat(),
                'source': ref(SOURCE), 'builder': ref(Path(__file__)), 'parser': ref(RUNTIME),
                'split': SPLIT, 'closedEpisode': 'Per security, source-ordered beforeQuantity0 to afterQuantity0; all partial trades and costs grouped. Opening inventory without basis and unfinished holdings excluded.',
                'primaryCohort': 'Complete episodes whose entryDate is on/after2024-09-24; carry-in cycles displayed separately.',
                'primaryReturnDenominator': 'Sum of all purchase cash outlays within the cycle, including visible buy charges; this is cumulative purchase cost, not actual account equity or stop-risk R.',
                'sensitivityDenominator': 'Peak cumulative negative transaction cash flow within the cycle, not net account equity under financing.',
                'payoffRatio': 'Mean positive cycle return divided by absolute mean negative cycle return; monetary payoff and PF shown separately.',
                'costScope': 'Trade cash-delta column includes visible reported charges. Financing interest, liabilities and funding remain unaudited; do not label fully net account returns.',
                'cannotInfer': ['Intraday timestamps', 'Unlisted cash flows', 'Financing debt and interest', 'Trader intentions', 'A proven executable strategy'],
                'noNewTradingRulesOrThresholdSearch': True}
    write(OUT/'contract.json', contract)
    sys.path.insert(0, str(RUNTIME))
    import xlrd
    book = xlrd.open_workbook(SOURCE, on_demand=True)
    assert book.nsheets == 1
    sheet = book.sheet_by_index(0)
    expected = ['发生日期','证券代码','证券名称','业务名称','买卖标志','成交数量','成交价格','成交金额','发生金额','剩余金额','备注','后证券额']
    assert sheet.row_values(0) == expected
    trades, nontrades, residuals = [], [], []
    previous_balance = None
    positions, opening = {}, {}
    raw_seen = set()
    for i in range(1, sheet.nrows):
        raw = sheet.row_values(i)
        key = tuple(raw)
        if key in raw_seen: raise ValueError('Unexpected identical source rows: preserve and review')
        raw_seen.add(key)
        date = datetime.strptime(raw[0], '%Y%m%d').date().isoformat()
        code, name, business, side = raw[1:5]
        quantity, after = integral(raw[5]), integral(raw[11])
        price, gross, net, balance = D(str(raw[6])), money(raw[7]), money(raw[8]), money(raw[9])
        if previous_balance is not None:
            residual = balance - previous_balance - net
            if residual:
                residuals.append({'sourceRow': i+1, 'date': date, 'unlistedBalanceMovementYuan': str(residual),
                                  'isVerifiedExternalCashFlow': False, 'kind': 'unresolved-financing-funding-or-other-balance-change'})
        previous_balance = balance
        if business == '申购配号':
            if gross or net or after: raise ValueError('Nonzero subscription-allocation money/holdings need review')
            nontrades.append({'sourceRow': i+1, 'date': date, 'code': code, 'business': business,
                              'reportedQuantity': quantity, 'treatedAsStockTrade': False})
            continue
        if business not in TRADE_TYPES: raise ValueError('New business type requires explicit review')
        sign = TRADE_TYPES[business]
        if not quantity or (quantity > 0) != (sign > 0) or (net < 0) != (sign > 0):
            raise ValueError('Trade direction or cash sign mismatch')
        if len(code) != 6 or not code.isdigit(): raise ValueError('Security code must preserve leading zeros')
        before = after - quantity
        if before < 0 or after < 0: raise ValueError('Unexpected short or invalid inventory')
        if code not in positions: opening[code] = before
        elif positions[code] != before: raise ValueError('Source share-balance discontinuity')
        positions[code] = after
        charges = -net-gross if sign > 0 else gross-net
        if charges < 0: raise ValueError('Negative inferred transaction charges')
        if abs(abs(quantity)*price-gross) > abs(quantity)*D('.00005') + D('.01'):
            raise ValueError('Source price/amount differs beyond displayed-price rounding')
        trades.append({'sourceRow': i+1, 'date': date, 'code': code, 'symbol': symbol(code), 'name': name,
                       'business': business, 'side': side, 'quantity': quantity, 'beforeQuantity': before,
                       'afterQuantity': after, 'displayPrice': str(price), 'grossYuan': str(gross),
                       'tradeCashDeltaYuan': str(net), 'visibleChargesYuan': str(charges),
                       'sourceRemainingAmountYuan': str(balance), 'executionTime': None})
    if trades != sorted(trades, key=lambda t: (t['date'], t['sourceRow'])):
        raise ValueError('Source dates are not ordered')
    episodes, add_events = reconstruct(trades)
    # Independent oracle: split each symbol's rows only at reported zero balances;
    # compare exact source rows and cash sums with the state-machine reconstruction.
    by_code = defaultdict(list)
    for t in trades: by_code[t['code']].append(t)
    oracle = {}
    for code, ts in by_code.items():
        segment = []
        for t in ts:
            segment.append(t)
            if t['afterQuantity'] == 0:
                if segment[0]['beforeQuantity'] == 0:
                    assert sum(t['quantity'] for t in segment) == 0
                    oracle[(code, segment[0]['sourceRow'], segment[-1]['sourceRow'])] = sum(D(t['tradeCashDeltaYuan']) for t in segment)
                segment = []
    actual = {(e['code'],e['startRow'],e['endRow']): D(e['netPnlAfterVisibleTransactionChargesYuan']) for e in episodes if e['status'] == 'closed'}
    if oracle != actual: raise ValueError('Independent zero-balance/cash-flow oracle mismatch')
    closed = [e for e in episodes if e['status'] == 'closed']
    cohort = {'allCompleteCycles': closed,
              'primaryEntryOnOrAfterSplit': [e for e in closed if e['entryDate'] >= SPLIT],
              'preSplitFullyClosedStress': [e for e in closed if e['lastExitDate'] < SPLIT],
              'carryInClosedAfterSplit': [e for e in closed if e['entryDate'] < SPLIT <= e['lastExitDate']]}
    summaries = {name: metrics(value) for name, value in cohort.items()}
    post = cohort['primaryEntryOnOrAfterSplit']
    board_summaries = {b: metrics([e for e in post if e['board'] == b]) for b in sorted({e['board'] for e in post})}
    financing_summaries = {name: metrics([e for e in post if bool(e['financedBuyRows']) == value])
                           for name, value in [('cyclesWithFinancedBuys', True), ('cyclesWithoutFinancedBuys', False)]}
    ranked = sorted(post, key=lambda e: D(e['netPnlAfterVisibleTransactionChargesYuan']), reverse=True)
    write(OUT/'trades.json', trades)
    write(OUT/'episodes.json', episodes)
    write(OUT/'add-events.json', add_events)
    write(OUT/'unlisted-balance-movements.json', residuals)
    write(OUT/'nontrade-rows.json', nontrades)
    readout = {'schema': 'guiyin-original-ledger-first-analysis-v1', 'completedAt': datetime.now(timezone.utc).isoformat(),
               'contract': ref(OUT/'contract.json'), 'source': ref(SOURCE), 'acquisition': ref(BASE/'guiyin-original-001/acquisition.json'),
               'sheet': sheet.name, 'sourceRange': f'A1:L{sheet.nrows}', 'sourceRowsExcludingHeader': sheet.nrows-1,
               'dateStart': trades[0]['date'], 'dateEnd': trades[-1]['date'], 'datesWithRecords': len(set(t['date'] for t in trades)),
               'tradeRows': len(trades), 'nontradeRows': len(nontrades), 'businessCounts': dict(Counter(t['business'] for t in trades)),
               'securityCount': len(positions), 'openingInventoryWithUnknownCost': {k:v for k,v in opening.items() if v},
               'endingInventory': {k:v for k,v in positions.items() if v},
               'positionContinuityMismatches': 0, 'identicalRawRows': 0, 'negativeVisibleChargeRows': 0,
               'unlistedBalanceMovementRows': len(residuals), 'unlistedBalanceMovementSumYuan': str(sum(D(x['unlistedBalanceMovementYuan']) for x in residuals)),
               'closedCyclesIndependentlyReconciled': len(oracle), 'episodeStatusCounts': dict(Counter(e['status'] for e in episodes)),
               'cohorts': summaries, 'primaryBoardCohorts': board_summaries, 'primaryFinancingCohorts': financing_summaries,
               'topPrimaryWins': ranked[:10], 'largestPrimaryLosses': list(reversed(ranked[-10:])),
               'trades': ref(OUT/'trades.json'), 'episodes': ref(OUT/'episodes.json'), 'addEvents': ref(OUT/'add-events.json'),
               'unlistedMovements': ref(OUT/'unlisted-balance-movements.json'), 'nontrades': ref(OUT/'nontrade-rows.json'),
               'accountNetReturnAudited': False, 'accountDrawdownAudited': False, 'completeFinancingCostAudit': False,
               'meaning': 'Source-reconciled completed stock cycles after charges present in trade cash deltas; not a verified fully net account return or a causal/replicable winning system.',
               'newStrategyTrials': 0, 'newStrategyAccounts': 0, 'targetAchieved': False}
    write(OUT/'readout.json', readout)
    book.release_resources()
    print(json.dumps({'readout':ref(OUT/'readout.json'),'sourceRows':sheet.nrows-1,'tradeRows':len(trades),
                      'statusCounts':readout['episodeStatusCounts'],'unlistedBalanceMovements':len(residuals),
                      'cohorts':summaries,'topFive':[{'code':e['code'],'name':e['name'],'entry':e['entryDate'],'exit':e['lastExitDate'],
                      'pnl':e['netPnlAfterVisibleTransactionChargesYuan'],'return':e['cumulativePurchaseCostReturn']} for e in ranked[:5]]},ensure_ascii=True))


if __name__ == '__main__':
    main()
