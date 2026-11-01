"""Independent raw-field audit of delayed entry eligibility; no strategy calls."""
from collections import Counter
import math


def require(condition, label):
    if not condition:
        raise ValueError(label)


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def audit_confirmation(data, parent_system, parent_schedule, candidate_system,
                       candidate_schedule, proof, start, end):
    calendar = data['calendar']
    require(calendar == sorted(set(calendar)), 'Calendar must be increasing and unique')
    require(max(calendar) <= '2026-10-15', 'Unregistered later source calendar')
    days = {day: i for i, day in enumerate(calendar)}
    securities = {row['symbol']: row for row in data['securities']}
    require(len(securities) == len(data['securities']), 'Duplicate source security')
    contexts = {row['date']: row for row in parent_system['daily']}
    require(len(contexts) == len(parent_system['daily']), 'Duplicate daily context')
    require(all(day <= end for day in contexts), 'Period context includes later dates')
    observed = {row['originSignalId']: row for row in proof['rows']}
    require(len(observed) == len(proof['rows']) == len(parent_schedule), 'Every parent must have one disposition')
    signals = {row['signalId']: row for row in candidate_schedule}
    require(len(signals) == len(candidate_schedule), 'Duplicate candidate identity')
    require(candidate_system['daily'] == parent_system['daily'], 'Market context changed')
    require(candidate_system['sectorMaps'] == parent_system['sectorMaps'], 'Sector map changed')
    expected, statuses = [], Counter()
    checked_bars, projected_symbols = 0, set()
    for origin in parent_schedule:
        ident, symbol, day = origin['signalId'], origin['symbol'], origin['signalDate']
        meta = parent_system['metadata'][ident]
        index = days.get(day)
        confirm = calendar[index + 1] if index is not None and index + 1 < len(calendar) else None
        earliest = calendar[index + 2] if index is not None and index + 2 < len(calendar) else None
        row = observed[ident]
        require(row['originSignalDate'] == day and row['symbol'] == symbol and
                row['originalRank'] == origin['originalRank'], 'Parent identity or rank changed')
        require(row['confirmationDate'] == confirm and row['earliestEntryDate'] == earliest,
                'Confirmation or execution clock differs from independent sessions')
        if not start <= day <= end:
            status = 'outsideInterval'
        elif index is None:
            status = 'unknown'
        elif confirm is None or not start <= confirm <= end:
            status = 'censored'
        else:
            require(symbol in securities, 'Projected dataset omitted a parent candidate')
            projected_symbols.add(symbol)
            security = securities[symbol]
            matches = [bar for bar in security['bars'] if bar['date'] == confirm]
            require(len(matches) <= 1, 'Duplicate confirmation bar')
            bar = matches[0] if matches else None
            context = contexts.get(confirm)
            valid = bar is not None
            if valid:
                suspended = security.get('tradeStatusByDate', {}).get(confirm)
                valid = suspended not in (False, 0, '0') and bar.get('knownSuspended') is not True
                valid = valid and ('knownSuspended' not in bar or type(bar['knownSuspended']) is bool)
                valid = valid and bar.get('priceBasisId', 'raw-unadjusted') == 'raw-unadjusted'
                valid = valid and all(positive(bar.get(field)) for field in ('open', 'high', 'low', 'close', 'volume', 'amount'))
                if valid:
                    valid = (bar['low'] <= bar['open'] <= bar['high'] and
                             bar['low'] <= bar['close'] <= bar['high'])
            if not valid or context is None:
                status = 'unknown'
            else:
                selected = context.get('selectedSectors')
                mapping = parent_system['sectorMaps'].get(context.get('snapshotId'))
                admitted = context.get('market', {}).get('admission')
                complete_context = (type(admitted) is bool and isinstance(selected, list) and
                                    isinstance(mapping, dict) and symbol in mapping)
                if not complete_context:
                    status = 'unknown'
                else:
                    passes = (bar['close'] > bar['open'] and bar['close'] >= meta['signalClose'] and
                              bar['close'] > meta['failurePrice'] and admitted and
                              meta['sectorCode'] in selected and mapping[symbol] == meta['sectorCode'])
                    status = 'passed' if passes else 'rejected'
                    checked_bars += 1
                for field in ('open', 'high', 'low', 'close', 'volume', 'amount'):
                    require(row['checks'][field] == bar[field], 'Proof price is not D+1 source: ' + field)
        require(row['status'] == status, 'Independent disposition differs: ' + ident)
        statuses[status] += 1
        if status != 'passed':
            require(row['newSignalId'] is None, 'Rejected/unknown/censored opportunity became candidate')
            continue
        new_id = proof['policyId'] + ':' + ident
        expected_signal = {**origin, 'signalId': new_id, 'signalDate': confirm}
        expected.append(expected_signal)
        require(row['newSignalId'] == new_id and signals.get(new_id) == expected_signal,
                'Candidate schedule differs from independently admitted parent')
        new_meta = candidate_system['metadata'][new_id]
        for field in ('signalClose', 'failurePrice', 'meanNtr20', 'originalRank', 'symbol',
                      'strategyId', 'sectorCode', 'pattern'):
            require(new_meta[field] == meta[field], 'Original risk/identity anchor changed: ' + field)
        require(new_meta['signalDate'] == new_meta['contextCutoff'] == confirm and
                new_meta['snapshotId'] == contexts[confirm]['snapshotId'] and
                new_meta['originSignalId'] == ident and new_meta['originSignalDate'] == day,
                'Candidate uses wrong decision context')
    require(candidate_schedule == expected, 'Candidate ordering/refill differs from original stream')
    require(set(candidate_system['metadata']) == set(signals), 'Unexpected candidate metadata')
    require(proof['parentScheduleCount'] == len(parent_schedule) and
            proof['candidateCount'] == len(expected), 'Proof count mismatch')
    require(all(proof['counts'].get(key, 0) == statuses[key] for key in
                ('passed', 'rejected', 'unknown', 'censored', 'outsideInterval')), 'Disposition counts mismatch')
    return {'passed': True, 'parentOpportunitiesChecked': len(parent_schedule),
            'confirmationBarsChecked': checked_bars, 'projectedCandidateSymbols': len(projected_symbols),
            'candidateSignalsChecked': len(expected), 'dispositions': dict(statuses),
            'independentRawFieldReconstruction': True, 'strategyImplementationImported': False,
            'newSegmentAfter20261015Read': False}


def audit_fill_clock(result, data, candidate_system):
    calendar = data['calendar']
    indexes = {day: i for i, day in enumerate(calendar)}
    checked = 0
    for fill in result['trades']:
        if fill['side'] != 'buy':
            continue
        meta = candidate_system['metadata'][fill['signalId']]
        signal_day = meta['signalDate']
        require(fill['date'] == calendar[indexes[signal_day] + 1], 'Buy not at next independent session')
        if 'originSignalDate' in meta:
            require(indexes[signal_day] == indexes[meta['originSignalDate']] + 1 and
                    indexes[fill['date']] == indexes[meta['originSignalDate']] + 2,
                    'Delayed entry is not origin D+2')
        checked += 1
    return {'passed': True, 'buyFillClocksChecked': checked}
