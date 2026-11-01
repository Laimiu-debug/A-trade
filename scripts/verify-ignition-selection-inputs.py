"""Independent raw-matrix A-universe, ranking, slot and execution-clock audit.

This module deliberately imports no strategy, feature, or account implementation.
It reconstructs every eligible A stock in the original slot's entire industry.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import math


def require(condition, message):
    if not condition:
        raise ValueError(message)


def equivalent(left, right, label):
    if isinstance(left, dict):
        require(isinstance(right, dict) and set(left) == set(right), label + ': fields')
        for key in left:
            equivalent(left[key], right[key], label + '.' + str(key))
    elif isinstance(left, (list, tuple)):
        require(isinstance(right, (list, tuple)) and len(left) == len(right), label + ': length')
        for i, (a, b) in enumerate(zip(left, right)):
            equivalent(a, b, label + '[' + str(i) + ']')
    elif type(left) in (int, float) and type(right) in (int, float):
        require(math.isfinite(left) and math.isfinite(right) and
                math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-10), label + ': numeric mismatch')
    else:
        require(left == right, label + ': mismatch')


def positive(value):
    return math.isfinite(float(value)) and float(value) > 0


def _raw_sector_candidates(matrix, row_by_symbol, members, day, index, sector, snapshot):
    """Scalar, trailing-only reconstruction; no vectorized feature helper calls."""
    require(index >= 21, 'A slots require 22 source calendar observations')
    historical_returns = {}
    for symbol in members:
        require(symbol in row_by_symbol, 'Published cohort member absent from full matrix')
        row = row_by_symbol[symbol]
        values = [float(matrix['close'][row, j]) for j in range(index - 21, index)]
        valid = all(bool(matrix['priceValid'][row, j]) and positive(values[j - index + 21])
                    for j in range(index - 21, index))
        if valid:
            historical_returns[symbol] = values[-1] / values[0] - 1
    ordered_returns = list(historical_returns.values())
    candidates = {}
    for symbol in members:
        row = row_by_symbol[symbol]
        if symbol not in historical_returns:
            continue
        slots = range(index - 21, index + 1)
        if any(not bool(matrix['priceValid'][row, j]) or
               bool(matrix['explicitSuspended'][row, j]) or
               not positive(matrix['volume'][row, j]) or not positive(matrix['amount'][row, j])
               for j in slots):
            continue
        close = {j: float(matrix['close'][row, j]) for j in slots}
        high = {j: float(matrix['high'][row, j]) for j in slots}
        low = {j: float(matrix['low'][row, j]) for j in slots}
        if not all(positive(x) for series in (close, high, low) for x in series.values()):
            continue
        prior_amount = math.fsum(float(matrix['amount'][row, j]) for j in range(index - 20, index)) / 20
        ntr = math.fsum(max(high[j] - low[j], abs(high[j] - close[j - 1]),
                            abs(low[j] - close[j - 1])) / close[j - 1]
                        for j in range(index - 19, index + 1)) / 20
        if prior_amount < 5e7 or not 0 < ntr < 1:
            continue
        current, previous = close[index], close[index - 1]
        prior_high = max(high[j] for j in range(index - 20, index))
        yesterday_prior_high = max(high[j] for j in range(index - 21, index - 1))
        amount = float(matrix['amount'][row, index])
        cents = {j: round(value * 100) for j, value in close.items()}
        high_cents, low_cents = round(high[index] * 100), round(low[index] * 100)
        ignition = (cents[index - 1] * 100 <= 115 * cents[index - 21] and
                    current > prior_high and previous <= yesterday_prior_high and
                    cents[index] * 100 >= cents[index - 1] * 105 and
                    amount >= 1.5 * prior_amount and high[index] > low[index] and
                    cents[index] * 4 >= high_cents * 3 + low_cents)
        if not ignition:
            continue
        prior_return = historical_returns[symbol]
        industry_rank = (sum(v < prior_return for v in ordered_returns) +
                         .5 * sum(v == prior_return for v in ordered_returns)) / len(ordered_returns)
        candidates[symbol] = {
            'signalDate': day, 'symbol': symbol, 'pattern': 'A_ignition',
            'signalClose': current,
            'failurePrice': max(.95 * current, min(min(low[j] for j in range(index - 2, index + 1)), .99 * current)),
            'meanNtr20': ntr, 'sectorCode': sector, 'snapshotId': snapshot,
            'priorReturn20': prior_return, 'priorIndustryRank20': industry_rank,
            'priorAmount20': prior_amount,
            'closePosition': (current - low[index]) / (high[index] - low[index]),
            'contextCutoff': day, 'sourceIndex': index,
            '_priorHigh20': prior_high, '_amount': amount,
            '_amountRatio': amount / prior_amount,
            '_breakoutExtensionNtr': (current / prior_high - 1) / ntr,
        }
    return candidates


def _feature(raw):
    return {'symbol': raw['symbol'], 'priorHigh20': raw['_priorHigh20'],
            'signalClose': raw['signalClose'], 'meanNtr20': raw['meanNtr20'],
            'breakoutExtensionNtr': raw['_breakoutExtensionNtr'],
            'priorIndustryRank20': raw['priorIndustryRank20'],
            'priorAmount20': raw['priorAmount20'], 'featureCutoff': raw['signalDate'],
            'signalAmount': raw['_amount'], 'amountParticipation': raw['_amountRatio']}


def audit_ignition_inputs(formation, matrix, parent_schedule, case_spec,
                          candidate_system, candidate_schedule, proof, start, end,
                          execution_dataset=None):
    rules = {'baseline': 'original', 'selection': 'leastBreakoutExtension',
             'participation': 'strongestAmountParticipation'}
    require(case_spec.get('id') in rules and case_spec.get('formationPolicy') == rules[case_spec['id']]
            and case_spec.get('parentPolicy') == 'complete' and case_spec.get('entryPolicy') == 'original'
            and case_spec.get('parameters') == {}, 'Unregistered case contract')
    require(start <= end <= '2026-10-15', 'Unregistered source interval')
    calendar = [str(x) for x in matrix['calendar']]
    symbols = [str(x) for x in matrix['symbols']]
    require(calendar == sorted(set(calendar)) and max(calendar) <= '2026-10-15', 'Invalid source calendar')
    require(len(symbols) == len(set(symbols)), 'Duplicate matrix stock')
    date_index, row_index = {d: i for i, d in enumerate(calendar)}, {s: i for i, s in enumerate(symbols)}
    contexts = {d['date']: d for d in formation['daily']}
    require(len(contexts) == len(formation['daily']), 'Duplicate formation context')
    expected_daily = [d for d in formation['daily'] if d['date'] <= end]
    require(candidate_system['daily'] == expected_daily, 'Context not an exact period prefix')
    require(candidate_system['sectorMaps'] == formation['sectorMaps'], 'Published membership changed')
    require(candidate_system['version'] == formation['version'], 'Formation version changed')
    parents = [s for s in parent_schedule if start <= s['signalDate'] <= end]
    require(len({s['signalId'] for s in parents}) == len(parents), 'Duplicate parent identity')
    require(len({(s['signalDate'], s['originalRank']) for s in parents}) == len(parents), 'Duplicate parent rank')
    observed = {r['originSignalId']: r for r in proof['rows']}
    require(len(observed) == len(proof['rows']) == len(parents), 'Every parent slot needs exactly one proof')
    require(set(observed) == {s['signalId'] for s in parents}, 'Unknown or missing original slot')
    require(len(candidate_schedule) == len(parents), 'Slot dropped or added')
    pool_index = defaultdict(list)
    for row in formation['allCandidates']:
        if row['pattern'] == 'A_ignition' and start <= row['signalDate'] <= end:
            pool_index[row['signalDate'], row['sectorCode']].append(row)
    cache, expected_schedule, expected_metadata = {}, [], {}
    source_rows, changes, patterns, selected_symbols = 0, 0, Counter(), set()
    for origin in parents:
        require(set(origin) == {'signalId', 'strategyId', 'symbol', 'signalDate', 'originalRank'}, 'Parent fields')
        old = formation['metadata'][origin['signalId']]
        require(all(old[k] == v for k, v in origin.items()), 'Parent metadata differs from schedule')
        day, sector = origin['signalDate'], old['sectorCode']
        index, context = date_index[day], contexts[day]
        require(context['market']['admission'] is True and sector in context['selectedSectors'], 'Original admission')
        require(old['snapshotId'] == context['snapshotId'], 'Original publication snapshot')
        mapping = formation['sectorMaps'][old['snapshotId']]
        require(mapping.get(origin['symbol']) == sector, 'Original published membership')
        slot = day + '|' + sector + '|' + str(origin['originalRank'])
        raw, source_pool = {}, []
        if old['pattern'] == 'A_ignition':
            key = day, sector
            if key not in cache:
                members = sorted(s for s, group in mapping.items() if group == sector)
                raw = _raw_sector_candidates(matrix, row_index, members, day, index, sector, old['snapshotId'])
                source_pool = pool_index[key]
                require(len(source_pool) == len({r['symbol'] for r in source_pool}), 'Duplicate full-pool candidate')
                require(set(raw) == {r['symbol'] for r in source_pool}, 'Frozen candidate pool differs from full raw sector universe')
                for row in source_pool:
                    for field, value in raw[row['symbol']].items():
                        if not field.startswith('_'):
                            equivalent(value, row[field], 'Raw candidate ' + row['symbol'] + '.' + field)
                cache[key] = raw, source_pool
                source_rows += len(raw)
            raw, source_pool = cache[key]
            require(origin['symbol'] in raw, 'Original selection absent from full eligible pool')
            require(old['symbol'] == origin['symbol'], 'Original stock metadata')
            for field, value in raw[origin['symbol']].items():
                if not field.startswith('_'):
                    equivalent(value, old[field], 'Original raw anchor ' + field)
        else:
            require(old['pattern'] == 'B_corePullback', 'Unknown original path')
            require(candidate_system['metadata'].get(origin['signalId']) == old, 'B metadata must remain byte-equivalent fields')
        new, meta = deepcopy(origin), deepcopy(old)
        if raw and case_spec['id'] != 'baseline':
            # Independent explicit ordering; no import of candidate sorter or feature helper.
            if case_spec['id'] == 'selection':
                order = sorted(raw, key=lambda s: (raw[s]['_breakoutExtensionNtr'],
                               -raw[s]['priorIndustryRank20'], -raw[s]['priorAmount20'], s))
            else:
                order = sorted(raw, key=lambda s: (-raw[s]['_amountRatio'],
                               -raw[s]['priorIndustryRank20'], -raw[s]['priorAmount20'], s))
            winner_symbol = order[0]
            winner = next(row for row in source_pool if row['symbol'] == winner_symbol)
            new['signalId'] = case_spec['id'] + ':' + origin['signalId'] + ':' + winner_symbol
            new['symbol'] = winner_symbol
            meta = {**deepcopy(winner), **new, 'sectorScore': old['sectorScore'],
                    'originSignalId': origin['signalId'], 'originSlotId': slot,
                    'selectionFeature': _feature(raw[winner_symbol])}
        expected_schedule.append(new)
        expected_metadata[new['signalId']] = meta
        selected_symbols.add(new['symbol'])
        expected_row = {'originSlotId': slot, 'originSignalId': origin['signalId'],
                        'selectedSignalId': new['signalId'], 'signalDate': day, 'sectorCode': sector,
                        'originalRank': origin['originalRank'], 'pattern': old['pattern'],
                        'originSymbol': origin['symbol'], 'selectedSymbol': new['symbol'],
                        'candidateCount': len(raw) if raw else 1,
                        'candidateFeatures': [_feature(raw[s]) for s in sorted(raw)],
                        'changed': new['symbol'] != origin['symbol'],
                        'earliestEntryDate': calendar[index + 1] if index + 1 < len(calendar) else None}
        equivalent(expected_row, observed[origin['signalId']], 'Slot proof ' + slot)
        changes += expected_row['changed']
        patterns[old['pattern']] += 1
    require(expected_schedule == candidate_schedule, 'Selected identity/order differs from independent ranking')
    equivalent(expected_metadata, candidate_system['metadata'], 'Selected metadata')
    require(proof['case'] == case_spec and proof['slots'] == len(parents) and
            proof['aSlots'] == patterns['A_ignition'] and proof['bSlots'] == patterns['B_corePullback'] and
            proof['changedSlots'] == changes and proof['droppedSlots'] == 0 and
            proof['formationUsesNextOpen'] is False, 'Proof aggregate or policy mismatch')
    if execution_dataset is not None:
        require(max(execution_dataset['calendar']) <= end, 'Execution calendar exceeds period')
        actual = {s['symbol'] for s in execution_dataset['securities']}
        require(len(actual) == len(execution_dataset['securities']), 'Duplicate execution stock')
        require(selected_symbols <= actual, 'Execution projection omitted a selected stock')
        require(all(bar['date'] <= end for security in execution_dataset['securities'] for bar in security['bars']),
                'Execution source contains later price records')
    return {'passed': True, 'slotsChecked': len(parents), 'aSlots': patterns['A_ignition'],
            'bSlots': patterns['B_corePullback'], 'changedSlots': changes,
            'eligibleFullSectorCandidatesChecked': source_rows,
            'selectedSymbolsChecked': len(selected_symbols), 'rawMatrixReconstruction': True,
            'strategyImplementationImported': False, 'newPriceSegmentAfter20261015Read': False}


def audit_fill_clock(result, dataset, system):
    calendar = dataset['calendar']
    require(calendar == sorted(set(calendar)), 'Independent calendar order')
    indexes = {day: i for i, day in enumerate(calendar)}
    checked, identities = 0, set()
    for fill in result['trades']:
        if fill['side'] != 'buy':
            continue
        identity = fill['signalId']
        meta = system['metadata'][identity]
        require(identity not in identities, 'More than one buy fill from a frozen opportunity')
        identities.add(identity)
        require(fill['signalDate'] == meta['signalDate'] and
                fill['date'] == calendar[indexes[meta['signalDate']] + 1], 'Buy not at D+1 independent session')
        require(fill['symbol'] == meta['symbol'], 'Opening fill identity changed')
        checked += 1
    for opportunity in result.get('research', {}).get('opportunities', []):
        attempt = opportunity.get('attemptDate')
        if attempt is not None:
            require(attempt == calendar[indexes[opportunity['signalDate']] + 1], 'Buy retried after immediate next session')
    return {'passed': True, 'buyFillClocksChecked': checked, 'oneNextOpenAttemptChecked': True}
