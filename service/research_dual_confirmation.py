"""One completed-session confirmation for frozen dual-path opportunities.

This module only transforms entry schedules. It neither runs an account nor
changes the original signal price, stop floor, volatility, ranking or exits.
"""
from collections import Counter
from copy import deepcopy
from datetime import date
import math
import re

VERSION = 'dual-one-session-confirmation-v1'
SIGNAL_FIELDS = ('signalId', 'strategyId', 'symbol', 'signalDate', 'originalRank')
STATUSES = ('passed', 'rejected', 'unknown', 'censored', 'outsideInterval')


def _day(value):
    if not isinstance(value, str):
        raise ValueError('Canonical YYYY-MM-DD dates required')
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise ValueError('Canonical YYYY-MM-DD dates required') from None
    return value


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _bar_issue(bar, day, security):
    if not isinstance(bar, dict):
        return 'missingConfirmationBar'
    if bar.get('date') != day:
        return 'confirmationDateMismatch'
    status = security.get('tradeStatusByDate', {}).get(day)
    if status is False or type(status) is int and status == 0 or status == '0' or bar.get('knownSuspended') is True:
        return 'knownSuspension'
    if bar.get('priceBasisId', 'raw-unadjusted') != 'raw-unadjusted':
        return 'conflictingPriceBasis'
    if 'knownSuspended' in bar and type(bar['knownSuspended']) is not bool:
        return 'invalidSuspensionFlag'
    if any(not _positive(bar.get(k)) for k in ('open', 'high', 'low', 'close', 'volume', 'amount')):
        return 'nonPositiveOrInvalidOHLCVAmount'
    if not bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']:
        return 'invalidOHLCEnvelope'
    return None


def build_confirmation_inputs(dataset, system_inputs, parent_schedule, policy_id, start, end):
    """Return independent system inputs, shifted five-field signals and proof.

    The caller chooses either the frozen complete or B-only parent stream.
    D+2 prices, later returns and account outcomes never enter admission.
    """
    start, end = _day(start), _day(end)
    if start > end or not isinstance(policy_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', policy_id):
        raise ValueError('Valid interval and nonempty policy identifier required')
    if dataset.get('adjustment', 'raw') not in ('raw', 'unadjusted', 'none'):
        raise ValueError('Confirmation requires original raw prices')
    calendar = dataset.get('calendar')
    if not isinstance(calendar, list) or not calendar:
        raise ValueError('Independent source calendar required')
    if any(_day(d) != d for d in calendar) or calendar != sorted(set(calendar)):
        raise ValueError('Source calendar must be unique and increasing')
    calendar_index = {d: i for i, d in enumerate(calendar)}
    context = {}
    for row in system_inputs['daily']:
        day = _day(row['date'])
        if day in context:
            raise ValueError('Duplicate system context date')
        context[day] = row
    security_by_symbol = {}
    for security in dataset['securities']:
        symbol = security['symbol']
        if symbol in security_by_symbol:
            raise ValueError('Duplicate security')
        security_by_symbol[symbol] = security
    if not isinstance(parent_schedule, list):
        raise ValueError('Frozen parent schedule must be a list')
    seen_ids, seen_entries, prepared, wanted = set(), set(), [], set()
    for signal in parent_schedule:
        if not isinstance(signal, dict) or any(k not in signal for k in SIGNAL_FIELDS):
            raise ValueError('Five-field parent signal required')
        old_id, symbol, day, rank = (signal[k] for k in ('signalId', 'symbol', 'signalDate', 'originalRank'))
        _day(day)
        if not isinstance(old_id, str) or not old_id or old_id in seen_ids or (symbol, day) in seen_entries:
            raise ValueError('Parent identities and symbol/date opportunities must be unique')
        if type(rank) is not int or not 1 <= rank <= 5:
            raise ValueError('Original Top5 rank required; ranks cannot be rebuilt')
        seen_ids.add(old_id)
        seen_entries.add((symbol, day))
        meta = system_inputs['metadata'].get(old_id)
        if not isinstance(meta, dict) or any(meta.get(k) != signal[k] for k in SIGNAL_FIELDS):
            raise ValueError('Parent signal metadata mismatch')
        if not all(_positive(meta.get(k)) for k in ('signalClose', 'failurePrice', 'meanNtr20')) or not meta['failurePrice'] < meta['signalClose'] or not meta['meanNtr20'] < 1:
            raise ValueError('Invalid original price/risk metadata')
        if meta.get('pattern') not in ('A_ignition', 'B_corePullback'):
            raise ValueError('Original dual-path entry family required')
        i = calendar_index.get(day)
        confirmation = calendar[i + 1] if i is not None and i + 1 < len(calendar) else None
        prepared.append((signal, meta, i, confirmation))
        if start <= day <= end and confirmation is not None and start <= confirmation <= end:
            wanted.add((symbol, confirmation))
    # Only index OHLCV values for requested D+1 slots. Later bars contribute no
    # price, validity predicate, digest or decision evidence to this transform.
    bars = {}
    wanted_symbols = {symbol for symbol, _ in wanted}
    for symbol in wanted_symbols:
        security = security_by_symbol.get(symbol)
        if security is None:
            continue
        for bar in security['bars']:
            key = (symbol, bar.get('date'))
            if key in wanted:
                if key in bars:
                    raise ValueError('Duplicate confirmation-date bar')
                bars[key] = bar

    rows, schedule, metadata = [], [], {}
    for signal, old_meta, index, confirmation in prepared:
        old_id, symbol, day = signal['signalId'], signal['symbol'], signal['signalDate']
        earliest = calendar[index + 2] if index is not None and index + 2 < len(calendar) else None
        row = dict(originSignalId=old_id, originSignalDate=day, symbol=symbol,
                   originalRank=signal['originalRank'], pattern=old_meta['pattern'],
                   confirmationDate=confirmation, decisionDate=confirmation,
                   earliestEntryDate=earliest, evidenceCutoff=confirmation,
                   status=None, reason=None, checks={}, newSignalId=None)
        if not start <= day <= end:
            row.update(status='outsideInterval', reason='originOutsideInterval')
        elif index is None:
            row.update(status='unknown', reason='originNotInIndependentCalendar')
        elif confirmation is None or not start <= confirmation <= end:
            row.update(status='censored', reason='confirmationOutsideInterval')
        else:
            current = context.get(confirmation)
            security = security_by_symbol.get(symbol)
            bar = bars.get((symbol, confirmation))
            issue = 'missingSecurity' if security is None else _bar_issue(bar, confirmation, security)
            if issue:
                row.update(status='unknown', reason=issue)
            elif current is None:
                row.update(status='unknown', reason='missingConfirmationContext')
            else:
                snapshot = current.get('snapshotId')
                mapping = system_inputs['sectorMaps'].get(snapshot)
                market = current.get('market', {}).get('admission')
                selected = current.get('selectedSectors')
                checks = dict(open=bar['open'], high=bar['high'], low=bar['low'], close=bar['close'],
                    volume=bar['volume'], amount=bar['amount'],
                    originalSignalClose=old_meta['signalClose'], originalFailurePrice=old_meta['failurePrice'],
                    originalMeanNtr20=old_meta['meanNtr20'], snapshotId=snapshot,
                    originalSector=old_meta['sectorCode'], marketAdmitted=market,
                    positiveSession=bar['close'] > bar['open'],
                    holdsOriginalClose=bar['close'] >= old_meta['signalClose'],
                    aboveOriginalFloor=bar['close'] > old_meta['failurePrice'],
                    originalSectorSelected=old_meta['sectorCode'] in selected if isinstance(selected, list) else None,
                    currentSector=mapping.get(symbol) if isinstance(mapping, dict) else None)
                row['checks'] = checks
                if type(market) is not bool or not isinstance(selected, list) or not isinstance(mapping, dict) or symbol not in mapping:
                    row.update(status='unknown', reason='incompleteConfirmationContext')
                else:
                    failures = [reason for passed, reason in (
                        (checks['positiveSession'], 'confirmationNotPositive'),
                        (checks['holdsOriginalClose'], 'confirmationBelowSignalClose'),
                        (checks['aboveOriginalFloor'], 'confirmationBelowStructuralFloor'),
                        (market, 'marketNotAdmitted'),
                        (checks['originalSectorSelected'], 'originalSectorNotSelected'),
                        (mapping[symbol] == old_meta['sectorCode'], 'sectorMembershipChanged')) if not passed]
                    if failures:
                        row.update(status='rejected', reason=failures[0], failedChecks=failures)
                    else:
                        new_id = policy_id + ':' + old_id
                        new_signal = {k: deepcopy(signal[k]) for k in SIGNAL_FIELDS}
                        new_signal.update(signalId=new_id, signalDate=confirmation)
                        meta = {**deepcopy(old_meta), **new_signal,
                                'contextCutoff': confirmation, 'snapshotId': snapshot,
                                'originSignalId': old_id, 'originSignalDate': day,
                                'originSnapshotId': old_meta.get('snapshotId'),
                                'originContextCutoff': old_meta.get('contextCutoff', day),
                                'confirmationVersion': VERSION}
                        if 'sourceIndex' in old_meta:
                            meta['originSourceIndex'] = old_meta['sourceIndex']
                            meta['sourceIndex'] = current.get('index', calendar_index[confirmation])
                        schedule.append(new_signal)
                        metadata[new_id] = meta
                        row.update(status='passed', reason='oneCompletedSessionConfirmed', newSignalId=new_id)
        rows.append(row)
    counts = dict(Counter({key: 0 for key in STATUSES}))
    for row in rows:
        counts[row['status']] += 1
    new_system = dict(version=VERSION, daily=deepcopy(system_inputs['daily']),
                      sectorMaps=deepcopy(system_inputs['sectorMaps']), metadata=metadata)
    proof = dict(version=VERSION, policyId=policy_id, startDate=start, endDate=end,
                 parentScheduleCount=len(parent_schedule), candidateCount=len(schedule),
                 counts=counts, reasonCounts=dict(Counter(row['reason'] for row in rows)), rows=rows,
                 earliestFillRule='nextIndependentSessionAfterConfirmationClose',
                 futurePriceEvidenceUsed=False, originalRankPreserved=True,
                 originalPriceRiskAnchorsPreserved=True, accountPolicy='complete')
    return new_system, schedule, proof
