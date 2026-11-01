"""Independent frozen-source, leader-lag signal and saved-account audit.

The new signal builder and runner are never imported. Source identity and all
event arithmetic are independently reconstructed. Previously frozen independent
board-aware cash/share/fee audit helpers are reused under exact hashes.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter, deque
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import gc
import gzip
import hashlib
import importlib.util
import json
import math
from statistics import mean
from pathlib import Path
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/leader-lag-followers-v1'
PARENT = ROOT / 'artifacts/strategy-search/board-coverage-reclaim-v1'
BOARD_REFERENCE = 'scripts/verify-board-coverage-reclaim.py'
BOARD_REFERENCE_SHA = '8d95ec533c901c7e47b6c23beb6567ae6760900d7be9e6038fff0d58853b6c29'
PARENT_PLAN_SHA = 'a911df3c26aa6e662ce8c62d6374e856427c311200b2dc99bcf409c2725ffefa'
PARENT_SOURCE_VALIDATION_SHA = 'fb666f86c8f78cd8c1b569bb8a44030e567b56c22db4d91b4880afb91152c649'
REGISTERED_PLAN_SHA = None
PROTOCOL_SHA = 'c5e969bdc0a6e012c9bc34440ee07a47f91833ae7a445efb34270fd02dbbd90f'
SID = 'leaderLagFollowersV1'
MODES = ('control', 'direction')
BOARDS = ('main', 'chinext', 'star')
PERIODS = ('2022-2023', '2024-2025')
LABELS = {'control': '同期同伴', 'direction': '先动后随'}
GATES = (('meanStrengthPercentile20', '>=', .90), ('minimumStrengthPercentile20', '>=', .80),
         ('relativeReturn60Percentile', '>=', .90), ('closePathMaxDrawdown20', '<=', .10),
         ('signedPathEfficiency20', '>=', .35))


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


def bar_issue(bar, status=None):
    if bar is None:
        return 'missingAlignedRawBar'
    if bar.get('priceBasisId', 'raw-unadjusted') != 'raw-unadjusted':
        return 'rawPriceBasisUnknown'
    if 'knownSuspended' in bar and type(bar['knownSuspended']) is not bool:
        return 'invalidKnownSuspensionFlag'
    if bar.get('knownSuspended') is True or status is False or type(status) in (int, float) and status == 0 or status == '0':
        return 'knownSuspension'
    if not all(positive(bar.get(key)) for key in ('open', 'high', 'low', 'close', 'volume')):
        return 'nonpositiveOrUnknownRawOHLCV'
    if not bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']:
        return 'invalidRawOHLCEnvelope'
    return None


def source_reference(dataset, start, end):
    require(dataset.get('priceBasisId') == 'raw-unadjusted', 'Explicit raw dataset required')
    calendar_check(dataset['calendar'])
    calendar = [day for day in dataset['calendar'] if day <= end]
    calendar_set = set(calendar)
    target = [day for day in calendar if start <= day <= end]
    require(target, 'Empty target period')
    symbols = sorted(row['symbol'] for row in dataset['securities'])
    require(len(set(symbols)) == len(symbols) and symbols, 'Duplicate/empty cohort')
    sources = {row['symbol']: row for row in dataset['securities']}
    benchmark = dataset.get('benchmark')
    require(isinstance(benchmark, dict) and benchmark.get('symbol') == 'sh000300', 'Wrong benchmark identity')
    indexed = {}
    for symbol, row in [*sorted(sources.items()), ('__benchmark__', benchmark)]:
        require(not row.get('corporateActions'), 'Unregistered corporate-action instructions')
        prefix = [bar for bar in row['bars'] if bar['date'] <= end]
        days = [bar['date'] for bar in prefix]
        if days:
            calendar_check(days)
        require(set(days) <= calendar_set, 'Off-calendar historical bar')
        lookup = {bar['date']: bar for bar in prefix}
        statuses = row.get('tradeStatusByDate', {})
        require(isinstance(statuses, dict) and all(day in calendar_set for day in statuses if day <= end), 'Malformed historical status dates')
        bars = [lookup.get(day) for day in calendar]
        issues = [bar_issue(bar, statuses.get(day)) for bar, day in zip(bars, calendar)]
        counts = [0]
        for issue in issues:
            counts.append(counts[-1] + (issue is None))
        indexed[symbol] = {'bars': bars, 'issues': issues, 'validPrefix': counts}
    return {'calendar': calendar, 'targetDates': target, 'symbols': symbols, 'indexed': indexed,
            'sources': sources}


def valid_window(item, stop, slots):
    return stop + 1 >= slots and item['validPrefix'][stop + 1] - item['validPrefix'][stop + 1 - slots] == slots


def relative_value(item, bench, stop, horizon):
    if not valid_window(item, stop, horizon + 1) or not valid_window(bench, stop, horizon + 1):
        return None
    try:
        own = item['bars'][stop]['close'] / item['bars'][stop - horizon]['close']
        market = bench['bars'][stop]['close'] / bench['bars'][stop - horizon]['close']
        if not positive(own) or not positive(market):
            return None
        ratio = own / market
        return ratio - 1 if positive(ratio) and finite(ratio - 1) else None
    except (OverflowError, ZeroDivisionError):
        return None


def rank_reference(values):
    ordered = sorted((value, symbol) for symbol, value in values.items() if finite(value))
    count, ranks, first = len(ordered), {}, 0
    while first < count:
        stop = first + 1
        while stop < count and ordered[stop][0] == ordered[first][0]:
            stop += 1
        for _, symbol in ordered[first:stop]:
            ranks[symbol] = (first + stop) / (2 * count)
        first = stop
    return ranks, count


def path_reference(item, stop):
    if not valid_window(item, stop, 21):
        return None, None
    closes = [bar['close'] for bar in item['bars'][stop - 20:stop + 1]]
    peak, drawdown = closes[0], 0.
    for close in closes:
        peak = max(peak, close)
        drawdown = max(drawdown, (peak - close) / peak)
    try:
        distance = math.fsum(abs(b - a) for a, b in zip(closes, closes[1:]))
        efficiency = (closes[-1] - closes[0]) / distance if positive(distance) else None
    except (OverflowError, ZeroDivisionError):
        efficiency = None
    return drawdown if finite(drawdown) else None, efficiency if finite(efficiency) else None


def strength_reference(item, bench, stop, histories, rank60):
    history = list(histories)
    dd, efficiency = path_reference(item, stop)
    values = {'meanStrengthPercentile20': mean(history) if len(history) == 20 and all(finite(v) for v in history) else None,
              'minimumStrengthPercentile20': min(history) if len(history) == 20 and all(finite(v) for v in history) else None,
              'relativeReturn60Percentile': rank60, 'closePathMaxDrawdown20': dd, 'signedPathEfficiency20': efficiency}
    complete = valid_window(item, stop, 61) and valid_window(bench, stop, 61) and all(finite(v) for v in values.values())
    passed = (values['meanStrengthPercentile20'] >= .90 and values['minimumStrengthPercentile20'] >= .80
              and values['relativeReturn60Percentile'] >= .90 and dd <= .10 and efficiency >= .35) if complete else None
    breakout = item['bars'][stop]['close'] > max(bar['high'] for bar in item['bars'][stop - 5:stop]) if valid_window(item, stop, 6) else None
    return values, passed, breakout


def pearson_reference(left, right):
    """Independent two-pass arithmetic; preserves all sufficient statistics."""
    if len(left) != len(right) or not left or not all(finite(v) for v in [*left, *right]):
        return None
    if all(v == left[0] for v in left) or all(v == right[0] for v in right):
        return None
    try:
        mx, my = math.fsum(left) / len(left), math.fsum(right) / len(right)
        x, y = [v - mx for v in left], [v - my for v in right]
        vx, vy = math.fsum(v * v for v in x), math.fsum(v * v for v in y)
        if not positive(vx) or not positive(vy):
            return None
        numerator = math.fsum(a * b for a, b in zip(x, y))
        denominator = math.sqrt(vx) * math.sqrt(vy)
        if not positive(denominator) or not finite(numerator):
            return None
        rho = numerator / denominator
        if not finite(rho):
            return None
        return {'count': len(left), 'meanX': mx, 'meanY': my, 'varianceX': vx, 'varianceY': vy,
                'numerator': numerator, 'denominator': denominator, 'rawRho': rho, 'rho': min(1., max(-1., rho))}
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def excess_reference(item, benchmark, stop):
    if not valid_window(item, stop, 61) or not valid_window(benchmark, stop, 61):
        return None
    own = [bar['close'] for bar in item['bars'][stop - 60:stop + 1]]
    market = [bar['close'] for bar in benchmark['bars'][stop - 60:stop + 1]]
    try:
        result = [math.log(b / a) - math.log(d / c) for a, b, c, d in zip(own, own[1:], market, market[1:])]
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return result if all(finite(value) for value in result) else None


def relationship_math(leader, member):
    if leader is None or member is None or len(leader) != 60 or len(member) != 60:
        return None
    values = {'synchronous59': pearson_reference(leader[1:], member[1:]),
              'forward59': pearson_reference(leader[:-1], member[1:]),
              'reverse59': pearson_reference(member[:-1], leader[1:])}
    if any(value is None for value in values.values()):
        return None
    return values, values['forward59']['rho'] > max(0, values['synchronous59']['rho'], values['reverse59']['rho'])


def interleave_reference(groups):
    edges = [dict(edge) for group in groups for edge in group]
    edges.sort(key=lambda row: (row['frozenMemberRank'], row['originalLeaderRank'], row['anchorSymbol'], row['memberSymbol']))
    owners, seen, all_edges = [], set(), []
    for edge in edges:
        member = edge['memberSymbol']
        duplicate = member in seen
        row = {**edge, 'duplicate': duplicate, 'ownerRank': None if duplicate else len(owners) + 1}
        if not duplicate:
            seen.add(member)
            owners.append(row)
        all_edges.append(row)
    return owners, all_edges


def chronological_strength(indexed):
    """Yield fresh board cross-sections; retain only causal twenty-rank history."""
    calendar, symbols, items = indexed['calendar'], indexed['symbols'], indexed['indexed']
    benchmark = items['__benchmark__']
    begin = max(0, calendar.index(indexed['targetDates'][0]) - 29)
    histories = {symbol: deque(maxlen=20) for symbol in symbols}
    for position in range(begin, len(calendar)):
        values, ranks, populations = {}, {}, {}
        for horizon in (20, 60):
            values[horizon] = {symbol: relative_value(items[symbol], benchmark, position, horizon) for symbol in symbols}
            ranks[horizon], count = rank_reference(values[horizon])
            population = [{'symbol': symbol, 'value': values[horizon][symbol], 'percentile': ranks[horizon][symbol]}
                          for symbol in symbols if symbol in ranks[horizon]]
            populations[str(horizon)] = {'count': count, 'sha256': digest(population)}
        states = {}
        for symbol in symbols:
            histories[symbol].append(ranks[20].get(symbol))
            features, passed, breakout = strength_reference(items[symbol], benchmark, position,
                                                            histories[symbol], ranks[60].get(symbol))
            states[symbol] = {'features': features, 'passed': passed, 'breakout': breakout,
                              'history': list(histories[symbol]), 'relative20': values[20][symbol],
                              'relative60': values[60][symbol]}
        yield position, populations, states


def synchronous_members(indexed, position, anchor):
    """Compute every board candidate before a deterministic top20 selection."""
    items, benchmark = indexed['indexed'], indexed['indexed']['__benchmark__']
    excess = {symbol: excess_reference(items[symbol], benchmark, position) for symbol in indexed['symbols']}
    leader = excess[anchor]
    evaluations = []
    for symbol in indexed['symbols']:
        stats = pearson_reference(leader, excess[symbol]) if leader is not None and excess[symbol] is not None and symbol != anchor else None
        evaluations.append({'symbol': symbol, 'stats': stats, 'qualified': stats is not None and stats['rho'] >= .30,
                            'selected': False, 'rank': None})
    qualified = sorted((row for row in evaluations if row['qualified']), key=lambda row: (-row['stats']['rho'], row['symbol']))
    for rank, row in enumerate(qualified, 1):
        row['rank'] = rank
        row['selected'] = rank <= 20
    return [row['symbol'] for row in qualified[:20]], evaluations, excess


def window_reason(item, stop, length):
    if stop + 1 < length:
        return 'insufficientCalendarHistory'
    if valid_window(item, stop, length):
        return None
    return next(reason for reason in item['issues'][stop + 1 - length:stop + 1] if reason is not None)


def relative_reason(item, benchmark, stop, horizon):
    for label, series in (('stock', item), ('benchmark', benchmark)):
        issue = window_reason(series, stop, horizon + 1)
        if issue:
            return label + ':' + issue
    own = item['bars'][stop]['close'] / item['bars'][stop - horizon]['close']
    market = benchmark['bars'][stop]['close'] / benchmark['bars'][stop - horizon]['close']
    if not positive(own) or not positive(market):
        return 'nonfiniteOrUnderflowIntermediateRatio'
    value = own / market
    return None if positive(value) and finite(value - 1) else 'nonfiniteOrUnderflowRelativeRatio'


def leadership_record(state, item, benchmark, position, calendar):
    values, history = deepcopy(state['features']), state['history']
    reasons = []
    for label, series in (('stock', item), ('benchmark', benchmark)):
        issue = window_reason(series, position, 61)
        if issue:
            reasons.append(label + ':' + issue)
    if len(history) != 20 or any(value is None for value in history):
        reasons.append('incompleteHistoricalRS20Ranks')
    if values['relativeReturn60Percentile'] is None:
        reasons.append('relativeReturn60RankUnknown')
    if valid_window(item, position, 21):
        closes = [bar['close'] for bar in item['bars'][position - 20:position + 1]]
        try:
            distance = math.fsum(abs(b - a) for a, b in zip(closes, closes[1:]))
        except (ValueError, OverflowError):
            distance = None
        if distance == 0:
            reasons.append('zeroPathDistance')
        elif not positive(distance) or not finite(values['signedPathEfficiency20']):
            reasons.append('nonfinitePathEfficiency')
    else:
        reasons.append('path20WindowUnknown')
    if any(not finite(value) for value in values.values()) and not reasons:
        reasons.append('nonfiniteLeadershipValue')
    predicates = {field: None if values[field] is None else
                  values[field] >= threshold if operator == '>=' else values[field] <= threshold
                  for field, operator, threshold in GATES}
    high = max(bar['high'] for bar in item['bars'][position - 5:position]) if valid_window(item, position, 6) else None
    raw_close = item['bars'][position].get('close') if item['bars'][position] is not None else None
    return {'date': calendar[position], 'status': 'unknown' if reasons else 'passed' if all(predicates.values()) else 'failed',
            'reasons': list(dict.fromkeys(reasons)), 'values': values, 'predicates': predicates,
            'rankDates20': calendar[max(0, position - 19):position + 1], 'rankHistory20': history,
            'knownRankCount20': sum(v is not None for v in history), 'prior5High': high,
            'close': raw_close if finite(raw_close) else None, 'breakout': raw_close > high if high is not None else None}


def prepared_reference(values):
    answer = {'known': False, 'reason': None, 'count': len(values), 'mean': None, 'variance': None, 'centered': None}
    if len(values) < 2:
        answer['reason'] = 'insufficientPairs'
    elif any(not finite(value) for value in values):
        answer['reason'] = 'nonfiniteInput'
    elif len(set(values)) == 1:
        answer['reason'] = 'exactConstantInput'
    else:
        try:
            center = math.fsum(values) / len(values)
            deviations = [value - center for value in values]
            variance = math.fsum(value * value for value in deviations)
        except (OverflowError, ValueError):
            answer['reason'] = 'nonfiniteCentering'
            return answer
        answer.update(mean=center if finite(center) else None, variance=variance if finite(variance) else None)
        if not finite(center) or not finite(variance):
            answer['reason'] = 'nonfiniteCentering'
        elif variance <= 0:
            answer['reason'] = 'zeroVariance'
        else:
            answer.update(known=True, centered=deviations)
    return answer


def correlation_record(x, y):
    result = {'known': False, 'reason': None, 'count': x['count'], 'meanX': x['mean'], 'meanY': y['mean'],
              'varianceX': x['variance'], 'varianceY': y['variance'], 'numerator': None, 'denominator': None,
              'rawCorrelation': None, 'correlation': None}
    if x['count'] != y['count']:
        result['reason'] = 'pairCountMismatch'
    elif not x['known'] or not y['known']:
        result['reason'] = 'x:' + x['reason'] if not x['known'] else 'y:' + y['reason']
    else:
        try:
            numerator = math.fsum(a * b for a, b in zip(x['centered'], y['centered']))
            denominator = math.sqrt(x['variance']) * math.sqrt(y['variance'])
        except (OverflowError, ValueError):
            result['reason'] = 'nonfiniteCovarianceOrDenominator'
            return result
        result.update(numerator=numerator if finite(numerator) else None, denominator=denominator if finite(denominator) else None)
        if not finite(numerator) or not positive(denominator):
            result['reason'] = 'nonfiniteCovarianceOrDenominator'
        else:
            rho = numerator / denominator
            if not finite(rho):
                result['reason'] = 'nonfiniteCorrelation'
            else:
                result.update(known=True, rawCorrelation=rho, correlation=min(1., max(-1., rho)))
    return result


def vector_records(indexed, stop):
    items, benchmark = indexed['indexed'], indexed['indexed']['__benchmark__']
    benchmark_issue = window_reason(benchmark, stop, 61)
    logs = None
    if benchmark_issue is None:
        closes = [bar['close'] for bar in benchmark['bars'][stop - 60:stop + 1]]
        try:
            logs = [math.log(b / a) for a, b in zip(closes, closes[1:])]
        except (ValueError, OverflowError, ZeroDivisionError):
            benchmark_issue = 'nonfiniteLogReturn'
        if logs is not None and any(not finite(v) for v in logs):
            benchmark_issue = 'nonfiniteLogReturn'
    output = {}
    for symbol in indexed['symbols']:
        item = items[symbol]
        issue = window_reason(item, stop, 61)
        issue = 'stock:' + issue if issue else 'benchmark:' + benchmark_issue if benchmark_issue else None
        values = None
        if issue is None:
            closes = [bar['close'] for bar in item['bars'][stop - 60:stop + 1]]
            try:
                values = [math.log(b / a) - market for a, b, market in zip(closes, closes[1:], logs)]
            except (ValueError, OverflowError, ZeroDivisionError):
                issue = 'nonfiniteExcessLogReturn'
            if values is not None and any(not finite(v) for v in values):
                issue = 'nonfiniteExcessLogReturn'
        output[symbol] = {'values': values if issue is None else None, 'reason': issue,
                          'prepared': prepared_reference(values) if issue is None else None}
    return output


def group_record(anchor, formation, days, vectors):
    reference = vectors[anchor]
    anchor_reason = reference['reason'] or (reference['prepared']['reason'] if not reference['prepared']['known'] else None)
    evaluations = []
    for symbol in sorted(set(vectors) - {anchor}):
        value = vectors[symbol]
        issue = value['reason'] or ('anchor:' + anchor_reason if anchor_reason else None)
        correlation = correlation_record(reference['prepared'], value['prepared']) if issue is None else None
        if correlation is not None:
            issue = correlation['reason']
        known = correlation is not None and correlation['known']
        eligible = known and correlation['correlation'] >= .30
        evaluations.append({'symbol': symbol, 'status': 'unknown' if not known else 'qualified' if eligible else 'belowThreshold',
                            'reason': issue if not known else 'correlationAtLeast030' if eligible else 'correlationBelow030',
                            'rawCorrelation': correlation['rawCorrelation'] if known else None,
                            'correlation': correlation['correlation'] if known else None, 'pearson': correlation,
                            'rank': None, 'selected': False})
    ranked = sorted([row for row in evaluations if row['status'] == 'qualified'], key=lambda row: (-row['correlation'], row['symbol']))
    for rank, row in enumerate(ranked, 1):
        row.update(rank=rank, selected=rank <= 20)
    members = [{'symbol': row['symbol'], 'frozenMemberRank': row['rank'], 'correlation60': row['correlation'],
                'rawCorrelation60': row['rawCorrelation']} for row in ranked[:20]]
    kind = 'unknown' if anchor_reason else 'formed' if len(members) == 20 else 'incomplete'
    return {'version': 'leader-lag-followers-v1', 'anchorSymbol': anchor, 'peerFormationDate': formation, 'kind': kind,
            'reason': 'anchor:' + anchor_reason if anchor_reason else 'fixedSynchronousPeerGroupFormed' if kind == 'formed' else 'insufficientCorrelatedPeers',
            'windowDates61': days, 'returnDates60': days[1:], 'requiredCount': 20, 'selectedCount': len(members), 'members': members,
            'counts': {'inputSymbols': len(vectors), 'candidateCount': len(evaluations), 'qualifiedCount': len(ranked),
                       'statusCounts': dict(sorted(Counter(row['status'] for row in evaluations).items())),
                       'reasonCounts': dict(sorted(Counter(row['reason'] for row in evaluations).items()))},
            'candidateEvaluationsSha256': digest(evaluations), 'candidateEvaluations': evaluations}


def relationship_record(leader, member, days):
    dates, coefficients = days[1:], {}
    pairs = [('synchronous59', leader[1:], member[1:], dates[1:], dates[1:]),
             ('forward59', leader[:-1], member[1:], dates[:-1], dates[1:]),
             ('reverse59', member[:-1], leader[1:], dates[:-1], dates[1:])]
    for name, x, y, xd, yd in pairs:
        coefficients[name] = {**correlation_record(prepared_reference(x), prepared_reference(y)), 'xDates': xd, 'yDates': yd}
    reasons = [name + ':' + row['reason'] for name, row in coefficients.items() if not row['known']]
    passed = coefficients['forward59']['correlation'] > max(0., coefficients['synchronous59']['correlation'], coefficients['reverse59']['correlation']) if not reasons else None
    return {'status': 'unknown' if reasons else 'known', 'reasons': reasons, 'windowDates61': days,
            'returnDates60': dates, 'coefficients': coefficients, 'directionPassed': passed}


def blank_counts():
    return {'currentCandidateCount': 0, 'temporalCandidateCount': 0, 'leaderEventCount': 0,
            'leaderGroupKinds': dict.fromkeys(('formed', 'incomplete', 'unknown'), 0),
            **dict.fromkeys(('rawEdges', 'uniqueOwners', 'duplicateSuppressed', 'selectedTop5', 'dailyRankExcluded',
                             'knownRelations', 'unknownRelations', 'directionPass', 'directionFail'), 0),
            'signals': dict.fromkeys(MODES, 0), 'notScheduled': dict.fromkeys(MODES, 0),
            'policyCounts': {mode: dict.fromkeys(('included', 'excluded', 'unknown'), 0) for mode in MODES}}


def total_counts(target, increment):
    for key, value in increment.items():
        if isinstance(value, dict):
            total_counts(target[key], value)
        else:
            target[key] += value


def reference_build(dataset, start, end, on_group=None):
    source = source_reference(dataset, start, end)
    calendar, symbols, items = source['calendar'], source['symbols'], source['indexed']
    target = set(source['targetDates'])
    ranked_days, daily, events, member_edges, opportunities = [], [], [], [], []
    schedule, counts, history = {mode: [] for mode in MODES}, blank_counts(), {}
    for position, populations, states in chronological_strength(source):
        day, bench = calendar[position], items['__benchmark__']
        reasons = {str(horizon): dict(sorted(Counter(reason for symbol in symbols
                    if (reason := relative_reason(items[symbol], bench, position, horizon)) is not None).items())) for horizon in (20, 60)}
        ranked_days.append({'date': day, 'populations': populations, 'reasonCounts': reasons})
        current = {symbol: leadership_record(states[symbol], items[symbol], bench, position, calendar) for symbol in symbols}
        history[position] = current
        if position - 11 in history:
            del history[position - 11]
        if day not in target:
            continue
        f = position - 10
        formation = calendar[f] if f >= 0 else None
        previous = history.get(f)
        candidates, activation, formation_counts = [], Counter(), Counter()
        for symbol in symbols:
            evidence = current[symbol]
            activation_kind = 'unknown' if evidence['status'] == 'unknown' else 'candidate' if evidence['status'] == 'passed' and evidence['breakout'] else 'notTriggered'
            activation[activation_kind] += 1
            if activation_kind != 'candidate':
                continue
            past = previous[symbol] if previous is not None else None
            status = past['status'] if past is not None else 'unknown'
            formation_counts[status] += 1
            candidates.append({'symbol': symbol, 'formationDate': day, 'peerFormationDate': formation,
                'currentEvidence': evidence, 'formationEvidence': past, 'formationStatus': status,
                'formationReason': 'formationStateUnavailable' if past is None else 'formationFiveConditionsPassed' if status == 'passed'
                    else 'formationFiveConditionsFailed' if status == 'failed' else 'formationEvidenceUnknown',
                'originalLeaderRank': None, 'selectedTop5': False})
        temporal = sorted([row for row in candidates if row['formationStatus'] == 'passed'],
                          key=lambda row: (-row['currentEvidence']['values']['meanStrengthPercentile20'],
                                           -row['currentEvidence']['values']['relativeReturn60Percentile'], row['symbol']))
        for rank, row in enumerate(temporal, 1):
            row.update(originalLeaderRank=rank, selectedTop5=rank <= 5)
        day_counts = blank_counts()
        day_counts.update(currentCandidateCount=len(candidates), temporalCandidateCount=len(temporal), leaderEventCount=len(temporal[:5]))
        vectors = vector_records(source, f) if temporal else None
        edges, event_ids = [], []
        days = calendar[f - 60:f + 1]
        for leader in temporal[:5]:
            anchor, event_id = leader['symbol'], SID + ':' + day + ':' + leader['symbol']
            group = group_record(anchor, formation, days, vectors)
            group_sha = digest(group)
            identity = {'version': 'leader-lag-followers-v1', 'eventId': event_id, 'formationDate': day,
                        'peerFormationDate': formation, 'anchorSymbol': anchor, 'originalLeaderRank': leader['originalLeaderRank'],
                        'groupSha256': group_sha}
            if on_group is not None:
                on_group({**identity, 'group': group})
            events.append({**identity, 'currentEvidence': deepcopy(leader['currentEvidence']),
                           'formationEvidence': deepcopy(leader['formationEvidence']),
                           'groupSummary': {key: value for key, value in group.items() if key != 'candidateEvaluations'}})
            event_ids.append(event_id)
            day_counts['leaderGroupKinds'][group['kind']] += 1
            if group['kind'] == 'formed':
                for member in group['members']:
                    edges.append({'formationId': event_id + ':' + member['symbol'], 'eventId': event_id, 'formationDate': day,
                        'peerFormationDate': formation, 'symbol': member['symbol'], 'anchorSymbol': anchor,
                        'originalLeaderRank': leader['originalLeaderRank'], 'frozenMemberRank': member['frozenMemberRank'],
                        'groupSha256': group_sha, 'synchronousCorrelation60': member['correlation60']})
        edges.sort(key=lambda row: (row['frozenMemberRank'], row['originalLeaderRank'], row['anchorSymbol'], row['symbol']))
        owners = {}
        for edge in edges:
            duplicate = edge['symbol'] in owners
            if not duplicate:
                owners[edge['symbol']] = edge
            owner = owners[edge['symbol']]
            rank = None if duplicate else len(owners)
            edge.update(ownerFormationId=owner['formationId'], ownershipStatus='duplicateSuppressed' if duplicate else 'owner',
                originalRank=rank, selectedTop5=not duplicate and rank <= 5, relationshipStatus='notEvaluated',
                ownerOriginalRank=owner['originalRank'] if duplicate else rank,
                disposition='duplicateSuppressed' if duplicate else 'selectedTop5' if rank <= 5 else 'dailyRankExcluded')
        selected = list(owners.values())[:5]
        day_counts.update(rawEdges=len(edges), uniqueOwners=len(owners), duplicateSuppressed=len(edges) - len(owners),
                          selectedTop5=len(selected), dailyRankExcluded=max(0, len(owners) - 5))
        for edge in selected:
            relation = relationship_record(vectors[edge['anchorSymbol']]['values'], vectors[edge['symbol']]['values'], days)
            known = relation['status'] == 'known'
            edge['relationshipStatus'] = relation['status']
            day_counts['knownRelations' if known else 'unknownRelations'] += 1
            if known:
                day_counts['directionPass' if relation['directionPassed'] else 'directionFail'] += 1
            decisions, signals = {}, {}
            for mode in MODES:
                included = known and (mode == 'control' or relation['directionPassed'])
                status = 'unknown' if not known else 'included' if included else 'excluded'
                reason = 'jointRelationshipUnknown' if not known else 'completeSynchronousPeer' if mode == 'control' else 'directionAdvantagePassed' if included else 'directionAdvantageNotMet'
                decisions[mode] = {'status': status, 'reason': reason}
                signals[mode] = {'signalId': edge['formationId'], 'strategyId': SID, 'symbol': edge['symbol'], 'signalDate': day,
                                 'originalRank': edge['originalRank']} if included else None
                day_counts['policyCounts'][mode][status] += 1
                day_counts['signals' if included else 'notScheduled'][mode] += 1
                if included:
                    schedule[mode].append(signals[mode])
            opportunities.append({**deepcopy(edge), 'relationship': relation, 'decisions': decisions, 'signals': signals})
        member_edges.extend(edges)
        daily.append({'formationDate': day, 'peerFormationDate': formation, 'rankPopulations': populations, 'rankReasonCounts': reasons,
            'activationCounts': {key: activation[key] for key in ('candidate', 'notTriggered', 'unknown')},
            'formationCounts': {key: formation_counts[key] for key in ('passed', 'failed', 'unknown')}, 'leaderCandidates': candidates,
            'leaderRanking': [{'symbol': row['symbol'], 'originalLeaderRank': row['originalLeaderRank'], 'selectedTop5': row['selectedTop5']} for row in temporal],
            'leaderEventIds': event_ids, 'memberEdgeIds': [edge['formationId'] for edge in edges],
            'selectedFormationIds': [edge['formationId'] for edge in selected], 'counts': day_counts})
        total_counts(counts, day_counts)
        if len(daily) % 25 == 0:
            print(json.dumps({'phase': 'independent-prefix', 'date': day, 'targetDays': len(daily), 'leaderEvents': len(events)}), flush=True)
    for mode in MODES:
        schedule[mode].sort(key=lambda row: (row['signalDate'], row['originalRank'], row['symbol'], row['signalId']))
    counts.update(sampleCount=len(symbols), targetDayCount=len(daily), rankDayCount=len(ranked_days), opportunityCount=len(opportunities))
    return {'version': 'leader-lag-followers-v1', 'strategyId': SID, 'modes': list(MODES), 'calendar': calendar,
        'targetDates': source['targetDates'], 'rankRows': ranked_days, 'dailyRows': daily, 'leaderEvents': events,
        'memberEdges': member_edges, 'opportunities': opportunities, 'schedules': schedule, 'counts': counts,
        'basis': {'protocol': 'docs/signal-leader-lag-followers-v1.json', 'priceBasisId': 'raw-unadjusted',
            'benchmarkSymbol': 'sh000300', 'rankScope': 'entire fixed board cohort, separate valid21/61-slot populations',
            'rankDigest': 'canonical sorted-symbol list of known {symbol,value,percentile}; no eligibility prefilter',
            'rankClockStart': ranked_days[0]['date'], 'formationLag': 10, 'groupSize': 20, 'peerCorrelationMinimum': .30,
            'leaderThresholds': [{'field': f, 'operator': op, 'threshold': v} for f, op, v in GATES],
            'memberOrder': ['frozenMemberRank', 'originalLeaderRank', 'anchorSymbol', 'symbol'],
            'groupEvidence': 'full detached on_group records; returned summaries retain full group and evaluation hashes',
            'unknownHistoricalStatus': 'unverified; never inferred from current names or missing records',
            'legacyEligibilityApplied': False, 'accountStateRead': False, 'noBackfill': True,
            'interpretation': 'board-scoped lagged association, not causal influence or market-wide leadership'},
        'certified': False, 'targetAchieved': False}


def self_test():
    r, n = rank_reference({'a': 1., 'b': 1., 'c': 3., 'd': None})
    require(n == 3 and r == {'a': 1/3, 'b': 1/3, 'c': 5/6}, 'Midrank exact ties and missing denominator')
    require(pearson_reference([.1] * 59, list(range(59))) is None, 'Exact-constant rejection before arithmetic')
    require(pearson_reference(list(range(59)), list(range(59)))['rho'] == 1., 'Positive Pearson')
    require(pearson_reference(list(range(59)), [-v for v in range(59)])['rho'] == -1., 'Negative Pearson')
    import random
    rng = random.Random(14)
    x = [rng.uniform(-.03, .03) for _ in range(60)]
    y = [.01] + x[:-1]
    relation = relationship_math(x, y)
    require(relation is not None and relation[1] and relation[0]['forward59']['rho'] == 1., 'Directional slicing and orientation')
    require(not relationship_math(x, x)[1], 'Synchronous dominance strict boundary')
    require(relationship_math(x, [1.] + [0.] * 59) is None, '59-slice constant even full60 varies')
    groups = [[{'frozenMemberRank': i + 1, 'originalLeaderRank': leader, 'anchorSymbol': f'l{leader}',
                'memberSymbol': member} for i, member in enumerate(members)]
              for leader, members in [(1, ['a', 'b', 'c']), (2, ['b', 'a', 'd'])]]
    owners, edges = interleave_reference(groups)
    require([r['memberSymbol'] for r in owners] == ['a', 'b', 'c', 'd'] and len(edges) == 6
            and sum(r['duplicate'] for r in edges) == 2, 'Peer-first interleave and first-edge ownership')
    require(bar_issue({'open': 1, 'high': 2, 'low': 1, 'close': 2, 'volume': 1}) is None
            and bar_issue({'open': 1, 'high': 2, 'low': 1, 'close': 2, 'volume': 1}, '0') is not None
            and bar_issue({'open': 1, 'high': 2, 'low': 1, 'close': 2, 'volume': 1}, 0.0) is not None,
            'Raw default basis and explicit known suspension')
    fixture = synthetic_source()
    before = digest(fixture)
    start, end = fixture['calendar'][100], fixture['calendar'][109]
    groups = []
    full = reference_build(fixture, start, end, on_group=groups.append)
    require(digest(fixture) == before, 'Independent arithmetic must not mutate the source')
    require(full['counts']['leaderEventCount'] == 40 and full['counts']['rawEdges'] == 800
            and full['counts']['selectedTop5'] == 50 and full['counts']['signals'] == {'control': 50, 'direction': 0},
            'Warmup formation, repeated events, complete groups and all original member Top5')
    require(len(groups) == 40 and all(len(row['group']['candidateEvaluations']) == 39 for row in groups),
            'Every original group scans all nonself cohort members')
    require(full['opportunities'][-1]['formationDate'] == end and full['schedules']['control'][-1]['signalDate'] == end,
            'Final-day signal retained for account cutoff')
    modified = deepcopy(fixture)
    for security in [*modified['securities'], modified['benchmark']]:
        for bar in security['bars']:
            if bar['date'] > end:
                bar['close'] = None
    require(reference_build(modified, start, end) == full, 'Future numerical append cannot alter earlier evidence')
    small = deepcopy(fixture)
    small['securities'] = small['securities'][:10]
    partial = reference_build(small, start, end)
    require(partial['counts']['leaderEventCount'] > 0 and partial['counts']['leaderGroupKinds']['incomplete'] > 0
            and partial['counts']['rawEdges'] == partial['counts']['selectedTop5'] == 0,
            'Incomplete groups preserve leader evidence without invented trading members')
    print(json.dumps({'passed': True, 'selfTests': 15, 'realSourceRead': False}), flush=True)


def synthetic_source():
    calendar = [(date(2020, 1, 1) + timedelta(days=n)).isoformat() for n in range(130)]
    def rows(drift, amplitude=.001, phase=0.):
        close, bars = 10., []
        for n, day in enumerate(calendar):
            close *= math.exp(drift + amplitude * math.sin(n * .5 + phase))
            bars.append({'date': day, 'open': close, 'high': close * 1.00001, 'low': close * .99999,
                         'close': close, 'volume': 100000., 'amount': close * 100000.})
        return bars
    return {'priceBasisId': 'raw-unadjusted', 'calendar': calendar,
            'securities': [{'symbol': f's{n:03d}', 'bars': rows(.002 + .0001 * n)} for n in range(40)],
            'benchmark': {'symbol': 'sh000300', 'bars': rows(.001, .0003, 1.3)}}




def registered_plan(directory):
    global REGISTERED_PLAN_SHA
    actual_plan_sha = sha(directory / 'plan.json')
    require(REGISTERED_PLAN_SHA is None or actual_plan_sha == REGISTERED_PLAN_SHA, 'Plan changed within audit process')
    REGISTERED_PLAN_SHA = actual_plan_sha
    plan = read(directory / 'plan.json')
    require(plan['sourcePins'].get(Path(__file__).relative_to(ROOT).as_posix()) == sha(__file__), 'Verifier not frozen in registered plan')
    require(plan['protocol']['sha256'] == PROTOCOL_SHA, 'Registered protocol changed')
    require(plan['schema'] == 'leader-lag-followers-v1' and plan['strategyId'] == SID
            and plan['modes'] == list(MODES) and plan['boards'] == list(BOARDS)
            and list(plan['periods']) == list(PERIODS) and plan['labels'] == LABELS, 'Registered identities differ')
    for name, number in {'newAccounts': 12, 'newSetupMechanisms': 1, 'newEntryPolicies': 2,
                         'newUniverseVariants': 0, 'referenceAccountsReused': 0}.items():
        require(type(plan[name]) is int and plan[name] == number, 'Registered trial counts differ: ' + name)
    require(plan['parameterSearch'] is False and plan['profitHurdles'] is None and plan['holdoutClaim'] is False
            and plan['certified'] is False and plan['targetAchieved'] is False, 'Unregistered optimization or certification')
    for path, expected in {**plan['sourcePins'], **plan['inputPins']}.items():
        require(sha(local(path)) == expected, 'Registered artifact changed: ' + path)
    require(read(pinned(plan['protocol'])) == plan['rules'], 'Plan does not contain exact protocol')
    require(sha(PARENT / 'plan.json') == PARENT_PLAN_SHA, 'Inherited source plan changed')
    parent = read(PARENT / 'plan.json')
    for period in PERIODS:
        config = deepcopy(parent['periods'][period]['config'])
        config['strategies'][0]['id'] = SID
        config['allocation']['strategyIds'] = [SID]
        require(plan['periods'][period] == {'startDate': config['startDate'], 'endDate': config['endDate'], 'config': config},
                'Account configuration changed beyond registered signal identity')
    return plan


def inherited_sources(plan):
    require(sha(PARENT / 'source-validation.json') == PARENT_SOURCE_VALIDATION_SHA, 'Inherited source audit changed')
    prior = read(PARENT / 'source-validation.json')
    require(prior['passed'] is True and prior['planSha256'] == PARENT_PLAN_SHA
            and prior['sourceCompletionSha256'] == sha(PARENT / 'source-completion.json')
            and prior['verifierSha256'] == BOARD_REFERENCE_SHA, 'Inherited source proof is stale')
    require(len(prior['datasetPins']) == 6 and list(plan['sourceData']) == list(PERIODS), 'Exactly six original source datasets required')
    for path, expected in {**prior['datasetPins'], **prior['manifestPins']}.items():
        require(sha(local(path)) == expected, 'Inherited source evidence changed: ' + path)
    for period in PERIODS:
        require(list(plan['sourceData'][period]) == list(BOARDS), 'Original source board matrix changed')
        manifest_path = PARENT / period / 'source-manifest.json'
        manifest = read(manifest_path)
        for board in BOARDS:
            binding = plan['sourceData'][period][board]
            require(set(binding) == {'accountData', 'accountDatasetDigest', 'sourceManifest', 'sourceValidation', 'members'},
                    'Source binding fields differ')
            cache = pinned(binding['accountData'])
            require(cache == (PARENT / period / (board + '.account-data.json.gz')).resolve()
                    and prior['datasetPins'][cache.relative_to(ROOT).as_posix()] == binding['accountData']['sha256'],
                    'Dataset is not original independently audited cache')
            require(pinned(binding['sourceManifest']) == manifest_path.resolve()
                    and pinned(binding['sourceValidation']) == (PARENT / 'source-validation.json').resolve(), 'Source proof link differs')
            require(binding['accountDatasetDigest'] == manifest['boards'][board]['accountData']['datasetDigest']
                    and binding['members'] == manifest['boards'][board]['includedCount'], 'Inherited cohort/dataset digest differs')
    return prior


def input_receipts(directory, plan):
    completion = read(directory / 'input-completion.json')
    require(completion['planSha256'] == REGISTERED_PLAN_SHA and completion['allSchedulesFrozenBeforeAccounts'] is True
            and completion['newAccounts'] == 12 and list(completion['outputs']) == list(PERIODS), 'Incomplete twelve-schedule receipt')
    receipts = {}
    for period in PERIODS:
        require(list(completion['outputs'][period]) == list(BOARDS), 'Input board matrix changed')
        receipts[period] = {}
        for board in BOARDS:
            folder = directory / period / board
            path = pinned(completion['outputs'][period][board])
            require(path == (folder / 'input-completion.json').resolve(), 'Input receipt outside original folder')
            receipt = read(path)
            require(receipt['planSha256'] == REGISTERED_PLAN_SHA and receipt['period'] == period and receipt['board'] == board,
                    'Input receipt identity changed')
            require({k: receipt[k] for k in plan['sourceData'][period][board]} == plan['sourceData'][period][board],
                    'Inputs no longer bind all original source facts')
            expected_files = {'opportunities.jsonl.gz', 'daily-evidence.jsonl.gz', 'rank-evidence.jsonl.gz', 'leader-events.jsonl.gz', 'member-edges.jsonl.gz', 'group-evidence.jsonl.gz', 'signal-metadata.json', 'input-counts.json'} | {
                mode + '.signals.json' for mode in MODES}
            require(set(receipt['files']) == expected_files, 'Exact signal input closure missing')
            for name, ref in receipt['files'].items():
                require(pinned(ref) == (folder / name).resolve(), 'Input artifact outside original folder')
            require(datetime.fromisoformat(plan['registeredAt']) <= datetime.fromisoformat(receipt['completedAt'])
                    <= datetime.fromisoformat(completion['completedAt']), 'Inputs prepared before protocol registration')
            receipts[period][board] = receipt
    return completion, receipts


def write_proof(directory, filename, proof):
    with (directory / filename).open('x', encoding='utf-8') as stream:
        json.dump(proof, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed': True, 'artifact': filename, 'sha256': sha(directory / filename), 'counts': proof['counts']}), flush=True)


def verify_inputs(directory):
    started = time.perf_counter()
    require(not (directory / 'input-validation.json').exists(), 'Input proof already exists')
    plan = registered_plan(directory)
    source = inherited_sources(plan)
    completion, receipts = input_receipts(directory, plan)
    artifacts, reports = {}, {}
    opportunities = days = evaluations = rank_evaluations = all_group_candidates = 0
    schedule_counts = Counter({mode: 0 for mode in MODES})
    for period in PERIODS:
        artifacts[period], reports[period] = {}, {}
        for board in BOARDS:
            receipt, folder = receipts[period][board], directory / period / board
            data = read(pinned(receipt['accountData']))
            require(digest(data) == receipt['accountDatasetDigest'] and data['period'] == period and data['board'] == board
                    and len(data['securities']) == receipt['members'], 'Input source contents or fixed cohort changed')
            config = plan['periods'][period]['config']
            print(json.dumps({'phase': 'independent-lag-input-start', 'period': period, 'board': board}), flush=True)
            group_count = group_candidates = 0
            with gzip.open(folder / 'group-evidence.jsonl.gz', 'rt', encoding='utf-8') as group_stream:
                def verify_group(expected):
                    nonlocal group_count, group_candidates
                    line = next(group_stream, None)
                    require(line is not None, 'Missing original group evidence')
                    same(json.loads(line), expected, 'Entire independent group including all board candidates')
                    group_count += 1
                    group_candidates += len(expected['group']['candidateEvaluations'])
                rebuilt = reference_build(data, config['startDate'], config['endDate'], on_group=verify_group)
                require(next(group_stream, None) is None and group_count == len(rebuilt['leaderEvents']), 'Group stream coverage/order differs')
            for filename, key in (('daily-evidence.jsonl.gz', 'dailyRows'), ('rank-evidence.jsonl.gz', 'rankRows'),
                                  ('leader-events.jsonl.gz', 'leaderEvents'), ('member-edges.jsonl.gz', 'memberEdges'),
                                  ('opportunities.jsonl.gz', 'opportunities')):
                same(records(folder / filename), rebuilt[key], 'Every original independent row: ' + filename)
            same(read(folder / 'signal-metadata.json'), {k: v for k, v in rebuilt.items() if k not in ('rankRows', 'dailyRows', 'leaderEvents', 'memberEdges', 'opportunities', 'schedules')},
                 'All signal metadata')
            require(read(folder / 'input-counts.json') == receipt['counts'] == rebuilt['counts'], 'All signal counts differ')
            for mode in MODES:
                require(read(folder / (mode + '.signals.json')) == rebuilt['schedules'][mode], 'Exact five-field schedule/order differs: ' + mode)
                schedule_counts[mode] += len(rebuilt['schedules'][mode])
            n, d = len(rebuilt['opportunities']), len(rebuilt['dailyRows'])
            opportunities += n
            days += d
            evaluations += len(data['securities']) * d
            rank_evaluations += len(data['securities']) * len(rebuilt['rankRows'])
            all_group_candidates += group_candidates
            reports[period][board] = {'members': len(data['securities']), 'counts': rebuilt['counts'],
                'memberOpportunities': n, 'targetSessions': d, 'symbolDayEvaluations': len(data['securities']) * d,
                'rankSymbolDayEvaluations': len(data['securities']) * len(rebuilt['rankRows']),
                'groupCandidateEvaluations': group_candidates,
                'accountDatasetDigest': receipt['accountDatasetDigest']}
            artifacts[period][board] = receipt['files']
            print(json.dumps({'phase': 'independent-lag-input-passed', 'period': period, 'board': board,
                'opportunities': n, 'signals': rebuilt['counts']['signals']}), flush=True)
            del data, rebuilt
            gc.collect()
    registered_plan(directory)
    proof = {'schema': 'leader-lag-followers-input-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__), 'planSha256': REGISTERED_PLAN_SHA,
        'inputCompletionSha256': sha(directory / 'input-completion.json'),
        'parentSourceValidationSha256': PARENT_SOURCE_VALIDATION_SHA, 'parentSourceCompletionSha256': sha(PARENT / 'source-completion.json'),
        'inputArtifacts': artifacts, 'datasetPins': source['datasetPins'], 'periods': reports,
        'counts': {'boardPeriodInputs': 6, 'schedules': 12, 'memberOpportunities': opportunities,
            'dailyRows': days, 'symbolDayEvaluations': evaluations, 'rankSymbolDayEvaluations': rank_evaluations,
            'groupCandidateEvaluations': all_group_candidates, 'signals': dict(schedule_counts)},
        'checks': ['All six unchanged source caches and prior independent raw-source proof bound by exact hashes',
            'All per-board causal ranks with warmup, F/A leader populations, original leader/member Top5 and interleaved ownership',
            'Every frozen60-peer group,59-pair directional numerator/denominator, exact constants, strict boundaries and common unknown',
            'All twelve original ordered five-field schedules independently recomputed before accounts'],
        'scope': {'productionSignalBuilderImported': False, 'rawSourceReloaded': False, 'accountsExecuted': 0,
            'sourceAuthenticity': 'Inherited exact-bound prior independent source audit; this stage reads frozen caches only.'},
        'elapsedSeconds': time.perf_counter() - started}
    write_proof(directory, 'input-validation.json', proof)


def verify_accounts(directory):
    started = time.perf_counter()
    require(not (directory / 'validation.json').exists(), 'Full proof already exists')
    plan = registered_plan(directory)
    source = inherited_sources(plan)
    completion, receipts = input_receipts(directory, plan)
    inputs = read(directory / 'input-validation.json')
    require(inputs['passed'] is True and inputs['planSha256'] == REGISTERED_PLAN_SHA and inputs['verifierSha256'] == sha(__file__)
            and inputs['inputCompletionSha256'] == sha(directory / 'input-completion.json')
            and inputs['parentSourceValidationSha256'] == PARENT_SOURCE_VALIDATION_SHA
            and inputs['datasetPins'] == source['datasetPins'], 'Independent input proof stale')
    start = read(directory / 'run-start.json')
    require(pinned(start['inputCompletion']) == (directory / 'input-completion.json').resolve()
            and pinned(start['inputValidation']) == (directory / 'input-validation.json').resolve()
            and datetime.fromisoformat(completion['completedAt']) <= datetime.fromisoformat(inputs['verifiedAt'])
            <= datetime.fromisoformat(start['startedAt']), 'Account started before complete independent signal validation')
    comparison = read(directory / 'comparison.json')
    require(comparison['schema'] == 'leader-lag-followers-comparison-v1' and comparison['planSha256'] == REGISTERED_PLAN_SHA,
            'Comparison identity differs')
    for key in ('newAccounts', 'newSetupMechanisms', 'newEntryPolicies', 'newUniverseVariants', 'referenceAccountsReused',
                'limitations', 'certified', 'targetAchieved'):
        require(comparison[key] == plan[key], 'Comparison scope differs: ' + key)
    require([(r['period'], r['board'], r['id'], r['policy']) for r in comparison['rows']]
            == [(p, b, m, m) for p in PERIODS for b in BOARDS for m in MODES], 'Complete twelve-account matrix/order')
    board_audit = reference(BOARD_REFERENCE, BOARD_REFERENCE_SHA)
    # Only the configurable identity of this newly imported independent helper
    # changes in memory; its frozen on-disk arithmetic and dependencies do not.
    board_audit.SID = SID
    accounting = reference(board_audit.ACCOUNT_REFERENCE, board_audit.ACCOUNT_REFERENCE_SHA)
    dependencies = {BOARD_REFERENCE: BOARD_REFERENCE_SHA, board_audit.ACCOUNT_REFERENCE: board_audit.ACCOUNT_REFERENCE_SHA,
                    accounting.COST_REFERENCE: accounting.COST_REFERENCE_SHA, **accounting.cost_reference().REFERENCES}
    for name, expected in dependencies.items():
        require(sha(local(name)) == expected, 'Independent accounting dependency changed')
    reports = {}
    require(list(comparison['artifacts']) == list(PERIODS), 'Account artifact period closure')
    for period in PERIODS:
        reports[period] = {}
        require(list(comparison['artifacts'][period]) == list(BOARDS), 'Account artifact board closure')
        for board in BOARDS:
            folder, receipt = directory / period / board, receipts[period][board]
            require(inputs['inputArtifacts'][period][board] == receipt['files'], 'Frozen inputs changed since independent audit')
            data = read(pinned(receipt['accountData']))
            require(digest(data) == receipt['accountDatasetDigest'], 'Account source content digest changed')
            require(list(comparison['artifacts'][period][board]) == list(MODES), 'Account mode closure')
            reports[period][board] = {}
            for mode in MODES:
                files = comparison['artifacts'][period][board][mode]
                require(set(files) == {mode + s for s in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json')},
                        'Full saved account closure missing')
                for name, ref in files.items():
                    require(pinned(ref) == (folder / name).resolve(), 'Account artifact outside original folder')
                result, checkpoint, summary = (read(folder / (mode + suffix))
                    for suffix in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json'))
                schedule = read(folder / (mode + '.signals.json'))
                require(len(schedule) == inputs['periods'][period][board]['counts']['signals'][mode], 'Schedule count drift')
                checked = board_audit.verify_board_account(result, checkpoint, summary, schedule,
                    plan['periods'][period]['config'], data, period, accounting)
                require(summary['policyId'] == mode and summary['policyLabel'] == LABELS[mode], 'Account summary policy differs')
                expected = board_audit.comparison_reference(summary, period, board, LABELS[mode], len(schedule))
                expected.update(id=mode, policy=mode)
                row = next(r for r in comparison['rows'] if (r['period'], r['board'], r['id']) == (period, board, mode))
                same(row, expected, 'All comparison statistics')
                reports[period][board][mode] = checked
                print(json.dumps({'phase': 'independent-lag-account-passed', 'period': period, 'board': board,
                                  'mode': mode, 'fills': checked['fills']}), flush=True)
                del result, checkpoint, summary
                gc.collect()
            del data
            gc.collect()
    registered_plan(directory)
    proof = {'schema': 'leader-lag-followers-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__), 'planSha256': REGISTERED_PLAN_SHA,
        'comparisonSha256': sha(directory / 'comparison.json'), 'inputValidationSha256': sha(directory / 'input-validation.json'),
        'inputCompletionSha256': sha(directory / 'input-completion.json'), 'parentSourceValidationSha256': PARENT_SOURCE_VALIDATION_SHA,
        'datasetPins': source['datasetPins'], 'inputArtifacts': inputs['inputArtifacts'], 'accountArtifacts': comparison['artifacts'],
        'independentReferences': dependencies, 'periods': reports,
        'counts': {'accounts': 12, 'reusedAccounts': 0, 'memberOpportunities': inputs['counts']['memberOpportunities'],
            'signals': inputs['counts']['signals'], 'fills': sum(r['fills'] for p in reports.values() for b in p.values() for r in b.values())},
        'checks': ['All twelve independently validated schedules frozen before account execution',
            'All complete saved ledgers: original dated fees, raw opening/slippage, board lots/limits, cash/shares, T+1 and reserve budgets',
            'Closed/open episodes, noProgress5 and risk-exit evidence, checkpoints, summaries and every reported comparison statistic'],
        'scope': {'productionSignalBuilderImported': False, 'accountsExecutedByVerifier': 0,
            'ledgerAudit': 'Exact-bound independent board-aware accounting helpers; only the new strategy identity is configured in memory.',
            'limitations': ['No separate resimulation of every eligibility/capacity/order decision.',
                'Inherited current-survivor/raw-price/status/rights/opening-capacity limitations remain.']},
        'certified': False, 'targetAchieved': False, 'elapsedSeconds': time.perf_counter() - started}
    write_proof(directory, 'validation.json', proof)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--inputs-only', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    directory = args.directory.resolve()
    stage = 'inputs' if args.inputs_only else 'accounts'
    try:
        (verify_inputs if args.inputs_only else verify_accounts)(directory)
    except Exception as error:
        folder = directory / 'validation-attempts'
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        failure = {'passed': False, 'stage': stage, 'at': stamp, 'verifierSha256': sha(__file__),
            'planSha256': sha(directory / 'plan.json') if (directory / 'plan.json').exists() else None,
            'error': str(error), 'traceback': traceback.format_exc(), 'accountsExecuted': 0}
        path = folder / (stamp + '-' + stage + '-failed.json')
        path.write_text(json.dumps(failure, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        print(json.dumps({'passed': False, 'failure': str(path), 'error': str(error)}), flush=True)
        raise


if __name__ == '__main__':
    main()
