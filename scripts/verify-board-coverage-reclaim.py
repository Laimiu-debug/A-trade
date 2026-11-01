"""Independent source, frozen UR signal and account audits for board coverage.

The materializer is never imported. Source maturity and prefix semantics are
reconstructed directly; the original independently audited UR arithmetic and
saved-account ledger audit are reused only under immutable hashes.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import gzip
import gc
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import struct
import time
import traceback
from decimal import Decimal, ROUND_HALF_UP

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/board-coverage-reclaim-v1'
BOARDS = ('main', 'chinext', 'star')
PERIODS = ('2022-2023', '2024-2025')
REGISTERED_PLAN_SHA = 'a911df3c26aa6e662ce8c62d6374e856427c311200b2dc99bcf409c2725ffefa'
SID = 'strongUndercutReclaimV1'
UR_REFERENCE = 'scripts/verify-undercut-reclaim-timing.py'
UR_REFERENCE_SHA = '3763141311fe456a88e3bc64124e48ee3a0d4e3855c88090cb332f20adc3abb8'
ACCOUNT_REFERENCE = 'scripts/verify-peer-amount-persistence.py'
ACCOUNT_REFERENCE_SHA = 'cfaabb0262a33c7c6a5776f4fb54b7d0298df104abf8374ac87313ec6cccd026'
DAY_RECORD = struct.Struct('<IIIIIfII')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


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
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


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
        for n, (a, b) in enumerate(zip(actual, expected)):
            same(a, b, f'{label}[{n}]')
    else:
        require(type(actual) is type(expected) and actual == expected, label + ': literal differs')


def pinned(ref, base=ROOT):
    require(isinstance(ref, dict) and {'path', 'sha256'} <= set(ref), 'Missing file reference')
    path = (base / ref['path']).resolve()
    require(not Path(ref['path']).is_absolute() and path.is_relative_to(base.resolve()), 'Reference escapes registered directory')
    require(sha(path) == ref['sha256'], 'File hash changed: ' + ref['path'])
    if 'bytes' in ref:
        require(path.stat().st_size == ref['bytes'], 'File byte size changed')
    return path


def write_proof(directory, name, document):
    with (directory / name).open('x', encoding='utf-8') as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed': True, 'artifact': name, 'sha256': sha(directory / name),
                      'counts': document.get('counts')}), flush=True)


def registered_plan(directory):
    require(REGISTERED_PLAN_SHA is not None and sha(directory / 'plan.json') == REGISTERED_PLAN_SHA, 'Frozen plan not registered or changed')
    plan = read(directory / 'plan.json')
    require(plan['schema'] == 'board-coverage-reclaim-v1' and plan['boards'] == list(BOARDS)
            and set(plan['periods']) == set(PERIODS) and plan['strategyId'] == SID and plan['mode'] == 'immediate', 'Registered scope changed')
    for name, value in (('newAccounts', 6), ('newUniverseVariants', 3), ('newSetupMechanisms', 0),
                        ('newEntryPolicies', 0), ('referenceAccountsReused', 0)):
        require(plan[name] == value, 'Registered trial count changed: ' + name)
    require(plan['parameterSearch'] is False and plan['holdoutClaim'] is False and plan['profitHurdles'] is None
            and plan['certified'] is False and plan['targetAchieved'] is False, 'Unregistered scope/profit or certification claim')
    spec = plan['sourceSpec']
    require(spec['boards'] == list(BOARDS) and spec['benchmarkSymbol'] == 'sh000300'
            and spec['minPriorValidObservations'] == 61, 'Registered source selection changed')
    pins = {**plan['inputPins'], **plan['sourcePins']}
    for name, expected in pins.items():
        require(sha(local(name)) == expected, 'Frozen input/code changed: ' + name)
    parent = read(ROOT / 'artifacts/strategy-search/strong-undercut-reclaim-timing-v1/plan.json')
    for period in PERIODS:
        require(plan['periods'][period]['config'] == parent['periods'][period]['config'], 'Original account/setup config changed')
    return plan


def prefix_evidence(rows, prefix):
    return {'prefixBytes': len(prefix), 'prefixRecords': len(rows), 'prefixSha256': hashlib.sha256(prefix).hexdigest(),
            'firstDate': rows[0]['date'] if rows else None, 'lastDate': rows[-1]['date'] if rows else None}


def physical_evidence(path, data):
    stat = path.stat()
    return {'bytes': len(data), 'mtimeNs': stat.st_mtime_ns, 'ctimeNs': stat.st_ctime_ns,
            'inode': stat.st_ino, 'sha256': hashlib.sha256(data).hexdigest()}


def verify_sources(directory):
    started = time.perf_counter()
    plan = registered_plan(directory)
    require(not (directory / 'source-validation.json').exists(), 'Source validation already exists')
    completion = read(directory / 'source-completion.json')
    builder = 'scripts/materialize-board-coverage-source.py'
    require(completion['schema'] == 'board-coverage-source-v1-completion' and completion['passed'] is True
            and completion['planSha256'] == REGISTERED_PLAN_SHA and completion['builderSha256'] == plan['sourcePins'][builder]
            and completion['sourceFilesUnchanged'] is True and completion['signalsCalculated'] is False
            and completion['actualAccountRuns'] == 0 and set(completion['periods']) == set(PERIODS), 'Complete frozen source receipt')
    spec = plan['sourceSpec']
    calendar = calendar_reference(spec['calendarPaths'])
    tdx_root = Path(spec['tdxRoot']).resolve()
    inventory, ignored = catalogue_reference(tdx_root)
    identities = [{'symbol': s, 'board': v['board'], 'path': v['path'].relative_to(tdx_root).as_posix()} for s, v in inventory.items()]
    require(completion['inventoryCount'] == len(identities) and completion['inventorySha256'] == digest(identities)
            and completion['physicalSourceCount'] == len(identities) + 1, 'Current local universe identity closure')
    dataset_pins, results, manifest_pins = {}, {}, {}
    source_reads = 0
    for period in PERIODS:
        fields = plan['periods'][period]
        start, end = fields['startDate'], fields['endDate']
        manifest_path = pinned(completion['periods'][period], directory)
        require(manifest_path == (directory / period / 'source-manifest.json').resolve(), 'Period manifest path')
        manifest = read(manifest_path)
        dates = [d for d in calendar if d <= end]
        require(manifest['passed'] is True and not manifest['readFailures'] and manifest['signalsCalculated'] is False
                and manifest['actualAccountRuns'] == 0 and manifest['planSha256'] == REGISTERED_PLAN_SHA
                and manifest['sourceSpec'] == spec and manifest['period'] == period
                and manifest['startDate'] == start and manifest['endDate'] == end
                and manifest['builderSha256'] == plan['sourcePins'][builder], 'Period source scope/quality/identity')
        require(manifest['inputPins'] == {**plan['inputPins'], **plan['sourcePins']}
                and manifest['calendarSources'] == {p: plan['inputPins'][p] for p in spec['calendarPaths']}, 'Manifest frozen evidence closure')
        require(manifest['calendar'] == dates and manifest['calendarSha256'] == digest(dates)
                and manifest['targetSessions'] == sum(start <= d <= end for d in dates), 'Source exact merged calendar')
        require(manifest['inventoryCount'] == len(identities) and manifest['inventorySha256'] == digest(identities)
                and manifest['ignoredNonStudyFiles'] == ignored, 'Source enumeration or nonstudy identity changed')
        require([{k: r[k] for k in ('symbol', 'board', 'path')} for r in manifest['records']] == identities, 'All source identities retained in order')
        manifest_pins[manifest_path.relative_to(ROOT).as_posix()] = sha(manifest_path)
        evidence_rows = {r['symbol']: r for r in manifest['records']}
        benchmark_path = tdx_root / 'vipdoc/sh/lday/sh000300.day'
        raw = stable_bytes(benchmark_path)
        benchmark_bars, benchmark_prefix = decode_prefix(raw, end)
        source_reads += 1
        require(set(r['date'] for r in benchmark_bars) <= set(dates), 'Benchmark prefix off registered calendar')
        expected_benchmark = {'symbol': 'sh000300', 'path': benchmark_path.relative_to(tdx_root).as_posix(),
            'physicalSource': physical_evidence(benchmark_path, raw), 'prefix': prefix_evidence(benchmark_bars, benchmark_prefix),
            'barsDigest': digest(benchmark_bars)}
        same(manifest['benchmark'], expected_benchmark, 'Source benchmark prefix proof')
        board_results = {}
        for board in BOARDS:
            declared = manifest['boards'][board]
            cache = pinned(declared['accountData'], directory)
            require(cache == (directory / period / (board + '.account-data.json.gz')).resolve(), 'Source board cache path')
            dataset = read(cache)
            require(digest(dataset) == declared['accountData']['datasetDigest']
                    and dataset['fingerprint'] == declared['accountData']['fingerprint']
                    and digest({k: v for k, v in dataset.items() if k != 'fingerprint'}) == dataset['fingerprint'], 'Source dataset canonical digest/fingerprint')
            actual = {s['symbol']: s for s in dataset['securities']}
            require(len(actual) == len(dataset['securities']), 'Source duplicate member security')
            selected, retained = [], 0
            states = Counter()
            for symbol, item in inventory.items():
                if item['board'] != board:
                    continue
                raw = stable_bytes(item['path'])
                bars, prefix = decode_prefix(raw, end)
                source_reads += 1
                require(set(r['date'] for r in bars) <= set(dates), 'Stock prefix off registered calendar: ' + symbol)
                prior = [r for r in bars if r['date'] < start]
                valid_days = [r['date'] for r in prior if valid_observation(r)]
                included = len(valid_days) >= 61
                status = 'included' if included else 'excluded'
                states[status] += 1
                expected = {'symbol': symbol, 'board': board, 'path': item['path'].relative_to(tdx_root).as_posix(),
                    'physicalSource': physical_evidence(item['path'], raw), 'prefix': prefix_evidence(bars, prefix),
                    'priorObservationCount': len(prior), 'priorValidObservationCount': len(valid_days),
                    'priorInvalidObservationCount': len(prior) - len(valid_days), 'priorValidDatesSha256': digest(valid_days),
                    'priorFirstDate': prior[0]['date'] if prior else None, 'priorLastDate': prior[-1]['date'] if prior else None,
                    'selectionStatus': status, 'reason': 'atLeast61ValidPriorObservations' if included else 'fewerThan61ValidPriorObservations'}
                same(evidence_rows[symbol], expected, 'Independent source identity and prior count ' + symbol)
                if included:
                    selected.append(symbol)
                    retained += len(bars)
                    security = {'symbol': symbol, 'name': symbol, 'board': board, 'bars': bars, 'adjustment': 'none',
                        'localCoverageOnly': True, 'volumeUnit': 'shares', 'amountUnit': 'CNY', 'historicalNameKnown': False,
                        'corporateActionsComplete': False, 'sourceSha256': digest(bars), 'rawPrefixSha256': hashlib.sha256(prefix).hexdigest()}
                    same(actual.get(symbol), security, 'Exact complete retained raw security ' + symbol)
            require(list(actual) == selected and declared['includedCount'] == len(selected)
                    and declared['excludedCount'] == states['excluded'] and declared['unknownCount'] == declared['readFailureCount'] == 0
                    and declared['symbolsSha256'] == digest(selected) and declared['retainedBarCount'] == retained, 'Source board member counts/coverage')
            expected_metadata = {'source': 'board-coverage-source-v1', 'priceBasisId': 'raw-unadjusted', 'adjustment': 'none',
                'startDate': start, 'endDate': end, 'period': period, 'board': board,
                'benchmark': {'symbol': 'sh000300', 'bars': benchmark_bars, 'source': 'TDX raw local index',
                              'priceBasisId': 'raw-unadjusted', 'volumeUnit': 'lots', 'amountUnit': 'CNY'},
                'calendar': dates, 'calendarSource': 'pinned-independent-exchange-calendars-merged',
                'universeSelection': {'mode': 'periodStartObservedMaturity', 'markets': ['sh', 'sz'], 'boards': [board],
                    'minPriorValidObservations': 61, 'membershipAsOf': start},
                'universeCoverage': {'scope': 'fixed-period-start-local-board-observations', 'loadedCount': len(selected),
                    'barCount': retained, 'symbolsSha256': digest(selected), 'pointInTimeComplete': False, 'boardCounts': {board: len(selected)}},
                'dataQuality': {'certified': False, 'pointInTimeComplete': False, 'listingDatesKnown': 0, 'historicalStKnownBars': 0,
                    'historicalMemberKnownBars': 0, 'reasons': ['local-file-survivor-coverage', 'historical-status-and-rights-unknown']},
                'warnings': manifest['limitations']}
            same({k: v for k, v in dataset.items() if k not in ('securities', 'fingerprint')}, expected_metadata, 'Source full dataset metadata')
            dataset_pins[cache.relative_to(ROOT).as_posix()] = sha(cache)
            board_results[board] = {'included': len(selected), 'excluded': states['excluded'], 'retainedBarsCompared': retained,
                                   'datasetDigest': digest(dataset), 'datasetSha256': sha(cache)}
            print(json.dumps({'phase': 'independent-source-board', 'period': period, 'board': board, **board_results[board]}), flush=True)
            del dataset, actual
            gc.collect()
        results[period] = board_results
    current, current_ignored = catalogue_reference(tdx_root)
    require(current == inventory and current_ignored == ignored, 'Source inventory changed during audit')
    registered_plan(directory)
    document = {'schema': 'board-coverage-reclaim-source-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__), 'planSha256': REGISTERED_PLAN_SHA,
        'sourceCompletionSha256': sha(directory / 'source-completion.json'), 'datasetPins': dataset_pins, 'manifestPins': manifest_pins,
        'periods': results, 'counts': {'enumeratedIdentities': len(inventory), 'datasets': len(dataset_pins),
            'periodIdentityDecisions': len(inventory) * len(PERIODS), 'physicalPrefixReads': source_reads},
        'checks': ['Current three-board identities independently enumerated with all nonstudy files accounted for',
            'Raw TDX historical prefixes independently decoded; physical hashes, every retained OHLCV/amount and cutoff checked',
            'Exact strictly prestart61 valid-observation selection and all excluded identities independently recomputed',
            'Original merged calendar and overlap, six full cache hashes/digests and no fabricated eligibility fields'],
        'scope': {'sourceBuilderImported': False, 'signalsCalculated': False, 'accountsExecuted': 0,
            'membershipMeaning': 'Current surviving local identities with valid observed history; not IPO dates or historical market membership.'},
        'elapsedSeconds': time.perf_counter() - started}
    write_proof(directory, 'source-validation.json', document)


def local(path):
    path = Path(path)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def reference(name, expected):
    path = local(name)
    require(sha(path) == expected, 'Frozen independent reference changed: ' + name)
    spec = importlib.util.spec_from_file_location('board_audit_' + path.stem.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def board_identity(symbol):
    if not isinstance(symbol, str) or not re.fullmatch(r'(sh|sz)[0-9]{6}', symbol):
        return None
    if symbol.startswith(('sh60', 'sz00')):
        return 'main'
    if symbol.startswith('sz30'):
        return 'chinext'
    if symbol.startswith('sh68'):
        return 'star'
    return None


def canonical_day(value):
    require(type(value) is int, 'TDX day is not an integer')
    parsed = date(value // 10000, value // 100 % 100, value % 100)
    require(1990 <= parsed.year <= 2200, 'TDX day outside supported numeric domain')
    return parsed.isoformat()


def decode_prefix(data, end):
    """Decode the cutoff prefix independently of any production loader.

    Date ordering is structural; post-cutoff prices and amounts cannot select
    membership, maturity or valid observations. Zero volume is kept and is not
    a valid positive-OHLCV observation for the maturity count.
    """
    require(data and len(data) % DAY_RECORD.size == 0, 'TDX file is empty or truncated')
    output, previous, prefix_end = [], None, 0
    for offset in range(0, len(data), DAY_RECORD.size):
        packed = data[offset:offset + DAY_RECORD.size]
        stamp, opening, high, low, close, amount, volume, _ = DAY_RECORD.unpack(packed)
        day = canonical_day(stamp)
        require(previous is None or day > previous, 'TDX duplicate or nonascending date')
        previous = day
        if day > end:
            continue
        prefix_end = offset + DAY_RECORD.size
        require(finite(amount), 'Nonfinite raw TDX prefix amount')
        output.append({'date': day, 'open': opening / 100, 'high': high / 100, 'low': low / 100,
                       'close': close / 100, 'volume': volume, 'amount': amount})
    return output, data[:prefix_end]


def valid_observation(bar):
    return (all(finite(bar.get(k)) and bar[k] > 0 for k in ('open', 'high', 'low', 'close', 'volume'))
            and finite(bar.get('amount')) and bar['amount'] >= 0
            and bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high'])


def fixed_membership(rows, start, calendar=None, minimum=61):
    allowed = set(calendar) if calendar is not None else None
    previous = [r for r in rows if r['date'] < start and (allowed is None or r['date'] in allowed)]
    valid = [r for r in previous if valid_observation(r)]
    return {'priorObservedBars': len(previous), 'priorValidObservations': len(valid), 'included': len(valid) >= minimum}


def calendar_reference(paths):
    sources = []
    for name in paths:
        value = read(local(name))
        if 'fields' in value:
            require(value['fields'] == ['calendar_date', 'is_trading_day'], 'Provider calendar columns')
            require(value.get('errorCode') in ('0', 0) and not value.get('rowBoundReached'), 'Incomplete provider calendar response')
            pairs = value['rows']
            require(pairs and all(isinstance(r, list) and len(r) == 2 and r[1] in ('0', '1') for r in pairs), 'Calendar row domain')
            all_days = [r[0] for r in pairs]
            require(all_days == sorted(set(all_days)), 'Provider calendar date order')
            trading = [r[0] for r in pairs if r[1] == '1']
            lower, upper = all_days[0], all_days[-1]
        else:
            trading = value['dates']
            lower, upper = value['start'], value['end']
        require(trading and trading == sorted(set(trading)) and lower <= trading[0] <= trading[-1] <= upper,
                'Calendar order/coverage')
        require(all(date.fromisoformat(d).isoformat() == d for d in trading), 'Canonical calendar date')
        sources.append((lower, upper, trading))
    for n, (lower, upper, trading) in enumerate(sources):
        for other_lower, other_upper, other_trading in sources[n + 1:]:
            begin, finish = max(lower, other_lower), min(upper, other_upper)
            if begin <= finish:
                require([d for d in trading if begin <= d <= finish] == [d for d in other_trading if begin <= d <= finish],
                        'Independent calendars disagree on overlap')
    return sorted({d for _, _, trading in sources for d in trading})


def catalogue_reference(tdx_root):
    root = Path(tdx_root).resolve()
    vipdoc = (root / 'vipdoc').resolve()
    require(vipdoc.is_relative_to(root) and vipdoc.is_dir(), 'TDX vipdoc root missing or escaped')
    answer, ignored = {}, []
    for market in ('sh', 'sz'):
        folder = (vipdoc / market / 'lday').resolve()
        require(folder.is_relative_to(vipdoc) and folder.is_dir(), 'TDX daily folder missing or escaped')
        for path in sorted(folder.glob('*.day')):
            symbol = path.stem.lower()
            if re.fullmatch(r'[0-9]{6}', symbol):
                symbol = market + symbol
            board = board_identity(symbol)
            if board is None or symbol[:2] != market:
                ignored.append(path.relative_to(root).as_posix())
                continue
            resolved = path.resolve()
            require(resolved.is_relative_to(folder) and symbol[:2] == market and symbol not in answer,
                    'TDX identity duplicate or path/market mismatch')
            answer[symbol] = {'board': board, 'path': resolved}
    require(answer and set(row['board'] for row in answer.values()) == set(BOARDS), 'Missing research board identity inventory')
    return dict(sorted(answer.items())), ignored


def stable_bytes(path):
    path = Path(path)
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    identity = lambda s: (s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino)
    require(identity(before) == identity(after) and len(data) == before.st_size, 'Raw source changed during independent read')
    return data


def board_quantity(symbol, value):
    board = board_identity(symbol)
    require(board in BOARDS, 'Unexpected instrument in board account')
    minimum, step = (200, 1) if board == 'star' else (100, 100)
    count = max(0, math.floor(value / step + 1e-10) * step)
    return count if count >= minimum else 0


def reserve_reference(symbol, price, budget, day, period, cost):
    minimum, step = (200, 1) if board_identity(symbol) == 'star' else (100, 100)
    count = board_quantity(symbol, budget / price)
    while count and count * price + cost.fee(count * price, 'buy', day, period)['breakdown']['total'] > budget + 1e-8:
        count -= step
    return count if count >= minimum else 0


def verify_board_reserves(result, close_by_key, period, cost):
    equities = {r['date']: r['equity'] for r in result['curve']}
    quotes = {r['quoteId']: r for r in result['research']['feeLedger']['quotes']}
    buys = {r['signalId']: r for r in result['trades'] if r['side'] == 'buy'}
    count = 0
    for opportunity in result['research']['opportunities']:
        if opportunity['plannedQuantity'] is None:
            require(opportunity['budget'] is None and opportunity['reservePrice'] is None, 'Unplanned budget fabricated')
            continue
        count += 1
        day, symbol = opportunity['signalDate'], opportunity['symbol']
        close = close_by_key.get((symbol, day))
        require(finite(close) and close > 0, 'Planned opportunity missing valid signal close')
        price, budget = close * 1.001, equities[day] * .2
        cost.near(opportunity['reservePrice'], price, 'Board reserve slipped signal close')
        cost.near(opportunity['budget'], budget, 'Board reserve frozen signal equity')
        quantity = reserve_reference(symbol, price, budget, day, period, cost)
        require(opportunity['plannedQuantity'] == quantity, 'Board-aware reserve affordability differs')
        quote = quotes[opportunity['reserveQuoteId']]
        context = quote['context']
        require(context['purpose'] == 'signalReserve' and context['effective_fee_date'] == context['signal_date'] == day
                and context['decision_date'] == day and context['symbol'] == symbol and context['execution_date'] is None,
                'Reserve anticipates later fee/execution evidence')
        require(quote['symbol'] == symbol and quote['side'] == 'buy', 'Board reserve quote identity')
        cost.near(quote['amount'], quantity * price, 'Board reserve quote amount')
        for component, expected in cost.fee(quantity * price, 'buy', day, period)['breakdown'].items():
            cost.near(quote['breakdown'][component], expected, 'Board reserve component ' + component)
        fill = buys.get(opportunity['signalId'])
        if fill:
            require(fill['quantity'] == board_quantity(symbol, fill['quantity']) and fill['quantity'] <= quantity
                    and fill['amount'] + fill['fee'] <= budget + 1e-7, 'Board buy exceeds frozen lot/budget')
    return count


def verify_board_account(result, checkpoint, summary, schedule, config, dataset, period, accounting):
    """Reuse ledger maths, replacing only its old mainboard-specific lot audit."""
    cost = accounting.cost_reference()
    relative, market, account = cost.reference_modules(period)
    account.SID = SID
    cost.verify_recorded_config(result['config'], config, relative.same)
    require(result['status'] == 'completed' and result['dataDigest'] == digest(dataset)
            and result['fingerprint'] == str(dataset.get('fingerprint') or digest(dataset)), 'Board account dataset identity')
    identity = result['research']['identity']
    require(identity['signalSchedule'] == schedule and identity['signalScheduleSha256'] == digest(schedule)
            and identity['entryWeight'] == .2, 'Board complete original schedule/entry weight')
    require(all(not result[k] for k in ('actionLedger', 'cashReceivables', 'actionPayables', 'preExRights'))
            and result['reservedActionCash'] == 0, 'Undeclared rights introduced')
    start, end, sessions, _ = cost.PERIODS[period]
    calendar = dataset['calendar']
    target = [d for d in calendar if start <= d <= end]
    require(len(target) == sessions, 'Board target independent calendar count')
    ledger = cost.verify_fills(result, period, 10)
    execution = account.verify_opportunity_execution(schedule, result['research']['opportunities'], result['trades'], calendar, start=start, end=end)
    episodes = account.verify_episodes_and_curve(result, target)
    exits = account.verify_exit_evidence(result['trades'], allow_no_progress=True)
    structural = account.verify_structural_observations(result, target)
    terminal = account.verify_terminal_account(ledger, result['positions'], final_cash=result['curve'][-1]['cash'],
        final_equity=result['metrics']['finalEquity'], final_curve=result['curve'][-1])
    summary_checks = account.verify_summary(result, summary, market.calendar_intervals(period))
    accounting.verify_checkpoint(checkpoint, result)
    bars = {s['symbol']: {b['date']: b for b in s['bars']} for s in dataset['securities']}
    closes = {(r['symbol'], r['signalDate']): bars[r['symbol']].get(r['signalDate'], {}).get('close') for r in schedule}
    reserves = verify_board_reserves(result, closes, period, cost)
    indices = {d: n for n, d in enumerate(calendar)}
    source_rows = {s['symbol']: s for s in dataset['securities']}
    for fill in result['trades']:
        symbol, day, side = fill['symbol'], fill['date'], fill['side']
        bar = bars[symbol].get(day)
        require(bar is not None and indices[day] > 0, 'Board fill has no source bar/preceding source session')
        previous = bars[symbol].get(calendar[indices[day] - 1])
        require(previous is not None and previous['volume'] > 0 and bar['volume'] > 0, 'Board fill missing positive-volume consecutive bar')
        cost.near(fill['openingPrice'], bar['open'], 'Board fill actual raw opening')
        cap = math.floor(previous['volume'] * config['maxParticipationRate'])
        require(fill['quantity'] <= cap, 'Board fill exceeds causal prior-volume capacity')
        board = board_identity(symbol)
        nominal = .1 if board == 'main' else .2
        band = nominal if board != 'main' or source_rows[symbol].get('stByDate', {}).get(day) is False else .05
        reference_close = source_rows[symbol].get('precloseByDate', {}).get(day, previous['close'])
        upper = float(Decimal(str(reference_close * (1 + band))).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
        lower = float(Decimal(str(reference_close * (1 - band))).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
        require(abs(bar['open'] / reference_close - 1) <= nominal + .005, 'Filled discontinuous board opening')
        require((bar['open'] < upper - 1e-8 and fill['price'] <= upper + 1e-8) if side == 'buy'
                else (bar['open'] > lower + 1e-8 and fill['price'] >= lower - 1e-8), 'Board limit/open/slippage rule')
        require(fill['quantity'] == fill['quantityBefore'] or fill['quantity'] == board_quantity(symbol, fill['quantity']),
                'Invalid board partial-fill lot size')
    return {'fills': ledger['fills'], 'fees': ledger['fees'], 'slippage': ledger['slippage'], 'execution': execution,
            'reserveChecks': reserves, 'episodes': episodes, 'exits': exits, 'noProgress5': structural,
            'terminal': terminal, 'statistics': summary_checks, 'boardLotAndLimitFillChecks': len(result['trades'])}


def source_proof(directory):
    proof = read(directory / 'source-validation.json')
    require(proof['passed'] is True and proof['planSha256'] == REGISTERED_PLAN_SHA
            and proof['sourceCompletionSha256'] == sha(directory / 'source-completion.json'), 'Source proof stale')
    require(len(proof['datasetPins']) == 6, 'Six source cache pins required')
    for path, expected in {**proof['datasetPins'], **proof['manifestPins']}.items():
        require(sha(local(path)) == expected, 'Independently verified source artifact changed: ' + path)
    return proof


def input_receipts(directory, plan, source):
    completion = read(directory / 'input-completion.json')
    require(completion['planSha256'] == REGISTERED_PLAN_SHA and completion['allSchedulesFrozenBeforeAccounts'] is True
            and set(completion['outputs']) == set(PERIODS), 'Full six-input completion missing')
    require(pinned(completion['sourceCompletion']) == (directory / 'source-completion.json').resolve(), 'Input/source completion binding')
    output = {}
    for period in PERIODS:
        require(set(completion['outputs'][period]) == set(BOARDS), 'Three input boards required')
        output[period] = {}
        for board in BOARDS:
            folder = directory / period / board
            receipt_path = pinned(completion['outputs'][period][board])
            require(receipt_path == (folder / 'input-completion.json').resolve(), 'Board input receipt path')
            receipt = read(receipt_path)
            require(receipt['period'] == period and receipt['board'] == board and receipt['planSha256'] == REGISTERED_PLAN_SHA,
                    'Board input receipt identity')
            require(pinned(receipt['sourceValidation']) == (directory / 'source-validation.json').resolve()
                    and pinned(receipt['sourceManifest']) == (directory / period / 'source-manifest.json').resolve(), 'Input independently audited source binding')
            cache = pinned(receipt['accountData'])
            require(cache == (directory / period / (board + '.account-data.json.gz')).resolve()
                    and source['datasetPins'][cache.relative_to(ROOT).as_posix()] == receipt['accountData']['sha256'], 'Input cache not original source-validated dataset')
            expected = {'opportunities.jsonl.gz', 'daily-evidence.jsonl.gz', 'signal-metadata.json', 'immediate.signals.json', 'input-counts.json'}
            require(set(receipt['files']) == expected, 'Exact original UR input output closure')
            for name, ref in receipt['files'].items():
                require(pinned(ref) == (folder / name).resolve(), 'Board signal artifact outside input folder')
            require(datetime.fromisoformat(source['verifiedAt']) <= datetime.fromisoformat(receipt['completedAt'])
                    <= datetime.fromisoformat(completion['completedAt']), 'Signals not prepared after complete source validation')
            output[period][board] = receipt
    return completion, output


def verify_inputs(directory):
    started = time.perf_counter()
    plan = registered_plan(directory)
    require(not (directory / 'input-validation.json').exists(), 'Input validation already exists')
    source = source_proof(directory)
    completion, receipts = input_receipts(directory, plan, source)
    ur = reference(UR_REFERENCE, UR_REFERENCE_SHA)
    counts, artifacts = {}, {}
    total_signals, total_days, total_ranked = 0, 0, 0
    for period in PERIODS:
        counts[period], artifacts[period] = {}, {}
        for board in BOARDS:
            folder, receipt = directory / period / board, receipts[period][board]
            dataset = read(pinned(receipt['accountData']))
            require(digest(dataset) == receipt['accountDatasetDigest'], 'Input dataset digest changed')
            require(dataset['board'] == board and dataset['period'] == period
                    and all(board_identity(r['symbol']) == board for r in dataset['securities']), 'Independent per-board rank population scope')
            print(json.dumps({'phase': 'independent-board-signal-start', 'period': period, 'board': board}), flush=True)
            indexes = ur.source_index(dataset, plan['periods'][period]['endDate'])
            calculated = ur.reference_build(indexes, plan['periods'][period]['startDate'], plan['periods'][period]['endDate'])
            same(records(folder / 'daily-evidence.jsonl.gz'), calculated['dailyRows'], 'All original UR board daily ranks/setup/full Top5')
            same(records(folder / 'opportunities.jsonl.gz'), calculated['opportunities'], 'All UR formation evidence and unchanged causal clocks')
            require(read(folder / 'immediate.signals.json') == calculated['schedules']['immediate'], 'Exact original immediate schedule/ranks/order')
            same(read(folder / 'signal-metadata.json'), {k: v for k, v in calculated.items() if k not in ('dailyRows', 'opportunities', 'schedules')},
                 'All UR signal metadata')
            require(read(folder / 'input-counts.json') == receipt['counts'] == calculated['counts'], 'Input total counts differ')
            signals, days = len(calculated['schedules']['immediate']), len(calculated['dailyRows'])
            total_signals += signals
            total_days += days
            total_ranked += len(dataset['securities']) * days
            counts[period][board] = {'members': len(dataset['securities']), 'counts': calculated['counts'],
                'rankedSymbolDays': len(dataset['securities']) * days, 'signalCount': signals, 'targetSessions': days,
                'accountDatasetDigest': receipt['accountDatasetDigest']}
            artifacts[period][board] = receipt['files']
            print(json.dumps({'phase': 'independent-board-input-passed', 'period': period, 'board': board, 'signals': signals}), flush=True)
            del dataset, indexes, calculated
            gc.collect()
    document = {'schema': 'board-coverage-reclaim-input-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__), 'planSha256': REGISTERED_PLAN_SHA,
        'sourceValidationSha256': sha(directory / 'source-validation.json'), 'sourceCompletionSha256': sha(directory / 'source-completion.json'),
        'inputCompletionSha256': sha(directory / 'input-completion.json'), 'inputArtifacts': artifacts,
        'datasetPins': source['datasetPins'], 'periods': counts, 'independentReferences': {UR_REFERENCE: UR_REFERENCE_SHA},
        'counts': {'boardPeriodInputs': 6, 'signals': total_signals, 'dailyRanks': total_days, 'rankedSymbolDays': total_ranked},
        'checks': ['All six source datasets exact-bound to prior independent source validation',
            'Every per-board source session population/rank digest, complete candidates and original Top5 independently recomputed',
            'Original strict support reclaim, strength/liquidity gates and five-field immediate signals unchanged; no outcome/execution selection'],
        'scope': {'productionSourceBuilderImported': False, 'productionSignalBuilderImported': False, 'accountsExecuted': 0,
            'otherClockEvidence': 'Original builder still records delay/confirmation diagnostics, but only immediate schedules are executed.'},
        'elapsedSeconds': time.perf_counter() - started}
    write_proof(directory, 'input-validation.json', document)


def comparison_reference(summary, period, board, label, signals):
    closed, account, whole = summary['closedEpisodes'], summary['account'], summary['periods'][0]
    return {'id': 'immediate', 'label': label, 'signals': signals, 'closed': closed['count'],
        'winRatePct': closed['winRatePct'], 'meanWinPct': closed['meanWinReturnPct'], 'meanLossPct': closed['meanLossReturnPct'],
        'closedMeanPct': closed['meanReturnPct'], 'payoffRatio': closed['payoffRatio'], 'profitFactor': closed['profitFactor'],
        'holdingSessions': closed['holdingSessions'], 'maxConsecutiveLosses': closed['maxConsecutiveLosses'],
        'accountReturnPct': account['netReturnPct'], 'maxDrawdownPct': account['maxDrawdownPct'],
        'meanExposurePct': whole['exposurePct']['mean'], 'openPositions': summary['openPositions']['count'],
        'unreliableValuationDays': len(summary['quality']['valuationUnreliableDates']),
        'top5ShareOfGrossProfitPct': summary['concentration']['top5ProfitSharePct'],
        'years': [{'id': r['id'], 'closed': r['closedEpisodes']['count'], 'closedMeanPct': r['closedEpisodes']['meanReturnPct'],
                   'accountReturnPct': r['returnPct'], 'maxDrawdownPct': r['maxDrawdownPct']} for r in summary['periods'] if r['kind'] == 'calendarYear'],
        'phases': [{'id': r['id'], 'start': r['observedStart'], 'end': r['observedEnd'], 'closed': r['closedEpisodes']['count'],
                    'accountReturnPct': r['returnPct'], 'maxDrawdownPct': r['maxDrawdownPct'], 'meanExposurePct': r['exposurePct']['mean']}
                   for r in summary['periods'] if r['kind'] == 'retrospectiveNarrativePhase'], 'period': period, 'board': board}


def verify_accounts(directory):
    started = time.perf_counter()
    plan = registered_plan(directory)
    require(not (directory / 'validation.json').exists(), 'Account validation already exists')
    source = source_proof(directory)
    completion, receipts = input_receipts(directory, plan, source)
    inputs = read(directory / 'input-validation.json')
    require(inputs['passed'] is True and inputs['planSha256'] == REGISTERED_PLAN_SHA
            and inputs['inputCompletionSha256'] == sha(directory / 'input-completion.json')
            and inputs['sourceValidationSha256'] == sha(directory / 'source-validation.json')
            and inputs['datasetPins'] == source['datasetPins'], 'Independent input proof stale')
    start = read(directory / 'run-start.json')
    require(pinned(start['inputCompletion']) == (directory / 'input-completion.json').resolve()
            and pinned(start['inputValidation']) == (directory / 'input-validation.json').resolve()
            and datetime.fromisoformat(inputs['verifiedAt']) <= datetime.fromisoformat(start['startedAt']), 'Accounts began without complete validated schedules')
    comparison = read(directory / 'comparison.json')
    require(comparison['schema'] == 'board-coverage-reclaim-comparison-v1' and comparison['planSha256'] == REGISTERED_PLAN_SHA
            and comparison['newAccounts'] == 6 and comparison['newUniverseVariants'] == 3 and comparison['newSetupMechanisms'] == 0
            and comparison['referenceAccountsReused'] == 0 and comparison['certified'] is False
            and comparison['targetAchieved'] is False and comparison['limitations'] == plan['limitations'], 'Complete comparison identity/trial scope')
    require([(r['period'], r['board'], r['id']) for r in comparison['rows']] == [(p, b, 'immediate') for p in PERIODS for b in BOARDS],
            'Complete six-account matrix/order')
    require(set(comparison['artifacts']) == set(PERIODS), 'Account artifact period closure')
    accounting = reference(ACCOUNT_REFERENCE, ACCOUNT_REFERENCE_SHA)
    dependencies = {UR_REFERENCE: UR_REFERENCE_SHA, ACCOUNT_REFERENCE: ACCOUNT_REFERENCE_SHA,
        accounting.COST_REFERENCE: accounting.COST_REFERENCE_SHA, **accounting.cost_reference().REFERENCES}
    for name, expected in dependencies.items():
        require(sha(local(name)) == expected, 'Pinned independent accounting dependency changed')
    reports = {}
    for period in PERIODS:
        require(set(comparison['artifacts'][period]) == set(BOARDS), 'Three account artifact boards')
        reports[period] = {}
        for board in BOARDS:
            folder, receipt = directory / period / board, receipts[period][board]
            files = comparison['artifacts'][period][board]
            require(set(files) == {'immediate.result.json.gz', 'immediate.checkpoint.json.gz', 'immediate.summary.json'}, 'Three saved account artifacts required')
            for name, ref in files.items():
                require(pinned(ref) == (folder / name).resolve(), 'Account artifact path outside board')
            require(inputs['inputArtifacts'][period][board] == receipt['files'], 'Full input artifacts differ from independent signal proof')
            dataset = read(pinned(receipt['accountData']))
            result, checkpoint, summary = (read(folder / n) for n in ('immediate.result.json.gz', 'immediate.checkpoint.json.gz', 'immediate.summary.json'))
            schedule = read(folder / 'immediate.signals.json')
            require(len(schedule) == inputs['periods'][period][board]['signalCount'], 'Independent schedule count changed')
            audit = verify_board_account(result, checkpoint, summary, schedule, plan['periods'][period]['config'], dataset, period, accounting)
            require(summary['policyId'] == 'immediate' and summary['policyLabel'] == plan['labels'][board], 'Board account summary policy/label')
            row = next(r for r in comparison['rows'] if r['period'] == period and r['board'] == board)
            same(row, comparison_reference(summary, period, board, plan['labels'][board], len(schedule)), 'Every reported board comparison statistic')
            reports[period][board] = audit
            print(json.dumps({'phase': 'independent-board-account-passed', 'period': period, 'board': board, 'fills': audit['fills']}), flush=True)
            del dataset, result, checkpoint, summary
            gc.collect()
    registered_plan(directory)
    document = {'schema': 'board-coverage-reclaim-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__), 'planSha256': REGISTERED_PLAN_SHA,
        'comparisonSha256': sha(directory / 'comparison.json'), 'sourceValidationSha256': sha(directory / 'source-validation.json'),
        'inputValidationSha256': sha(directory / 'input-validation.json'), 'sourceCompletionSha256': sha(directory / 'source-completion.json'),
        'inputCompletionSha256': sha(directory / 'input-completion.json'), 'datasetPins': source['datasetPins'],
        'inputArtifacts': inputs['inputArtifacts'], 'accountArtifacts': comparison['artifacts'], 'independentReferences': dependencies,
        'periods': reports, 'counts': {'accounts': 6, 'reusedAccounts': 0, 'signals': inputs['counts']['signals'],
            'fills': sum(v['fills'] for p in reports.values() for v in p.values())},
        'checks': ['Frozen independent source validation precedes original UR input preparation; complete input validation precedes all six accounts',
            'All six complete saved ledgers: dated fees, raw opening/slippage/board bands, cash/shares/T+1, frozen budgets and board-aware lots',
            'Closed episodes/open holdings, noProgress5/risk exit evidence, curves/checkpoints/summaries and every comparison field'],
        'scope': {'productionSignalOrSourceImported': False, 'accountsExecutedByVerifier': 0,
            'ledgerAudit': 'Pinned independent accounting functions; original mainboard-only reserve helper replaced by independently implemented board-aware arithmetic.',
            'limitations': ['No separate resimulation of every eligibility/capacity/order decision.',
                'Current local survivor sample and raw unadjusted rights/status/opening-capacity limitations remain; no strategy certification.']},
        'certified': False, 'targetAchieved': False, 'elapsedSeconds': time.perf_counter() - started}
    write_proof(directory, 'validation.json', document)


def self_test():
    checks = []
    require([board_identity(s) for s in ('sh600000', 'sz000001', 'sz300001', 'sz301001', 'sh688001', 'sh689001')]
            == ['main', 'main', 'chinext', 'chinext', 'star', 'star'], 'Independent board prefixes')
    require(all(board_identity(s) is None for s in ('sh000300', 'sz399001', 'bj920001', 'sh510300', 'sz160000', 'SZ300001')),
            'Nonresearch instrument entered board universe')
    checks.append('exact three board identity prefixes without indices, funds or BSE')
    days = [(date(2021, 1, 1) + timedelta(days=n)).isoformat() for n in range(65)]
    packed = b''.join(DAY_RECORD.pack(int(d.replace('-', '')), 1000, 1100, 900, 1000, 100000., 1000, 0) for d in days)
    rows, prefix = decode_prefix(packed, days[-2])
    require(len(rows) == 64 and len(prefix) == 64 * 32 and rows[0]['volume'] == 1000 and rows[0]['open'] == 10., 'Independent binary units/cutoff')
    require(fixed_membership(rows, days[61])['included'] and not fixed_membership(rows, days[60])['included'], 'Exact prestart 61 observations')
    checks.append('independent 32-byte prices/amount/shares and strict prestart membership threshold')
    rows[1]['volume'] = 0
    require(not fixed_membership(rows, days[61])['included'] and fixed_membership(rows, days[62])['included'], 'Zero volume or current-day bar counted as prior valid')
    require(fixed_membership(rows, days[62], calendar=days[2:])['priorValidObservations'] == 60, 'Registered calendar projection')
    checks.append('maturity counts valid observed history without treating zero volume/current date as mature history')
    future = DAY_RECORD.pack(int(days[-1].replace('-', '')), 0, 0, 0, 0, float('nan'), 0, 0)
    require(decode_prefix(packed[:-32] + future, days[-2]) == decode_prefix(packed, days[-2]), 'Future numerical data leaked into prefix')
    checks.append('post-cutoff numerical corruption cannot alter historical prefix arithmetic')
    for bad in (packed[:-1], packed[:32] + packed[:32]):
        try:
            decode_prefix(bad, days[-1])
        except ValueError:
            pass
        else:
            raise ValueError('Malformed/duplicate TDX records were admitted')
    checks.append('truncated records and duplicate dates rejected')
    reference(UR_REFERENCE, UR_REFERENCE_SHA)
    accounting = reference(ACCOUNT_REFERENCE, ACCOUNT_REFERENCE_SHA)
    checks.append('unchanged independent UR rank/setup and account ledger helper hashes')
    cost = accounting.cost_reference()
    require(board_quantity('sh688001', 199.99) == 0 and board_quantity('sh688001', 201.9) == 201
            and board_quantity('sz300001', 201.9) == 200 and board_quantity('sh600001', 201.9) == 200,
            'Independent STAR/chinext/main quantity boundaries')
    require(reserve_reference('sh688001', 10., 2015.1, '2024-01-02', '2024-2025', cost) == 201
            and reserve_reference('sz300001', 10., 2015.1, '2024-01-02', '2024-2025', cost) == 200
            and reserve_reference('sh688001', 10., 2005.01, '2024-01-02', '2024-2025', cost) == 0,
            'Board minimums or fee-aware affordability')
    checks.append('STAR 200-share minimum/one-share increment and existing 100-share board lots with dated fees')
    print(json.dumps({'passed': True, 'groups': len(checks), 'checks': checks, 'accountsExecuted': 0}), flush=True)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--self-test', action='store_true')
    stages = parser.add_mutually_exclusive_group()
    stages.add_argument('--source-only', action='store_true')
    stages.add_argument('--inputs-only', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    directory = args.directory.resolve()
    stage = 'source' if args.source_only else 'inputs' if args.inputs_only else 'accounts'
    try:
        {'source': verify_sources, 'inputs': verify_inputs, 'accounts': verify_accounts}[stage](directory)
    except Exception as error:
        folder = directory / 'validation-attempts'
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        failure = {'passed': False, 'at': stamp, 'stage': stage, 'verifierSha256': sha(__file__),
                   'planSha256': sha(directory / 'plan.json') if (directory / 'plan.json').exists() else None,
                   'error': str(error), 'traceback': traceback.format_exc(), 'accountsExecuted': 0}
        path = folder / (stamp + '-' + stage + '-failed.json')
        path.write_text(json.dumps(failure, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        print(json.dumps({'passed': False, 'failure': str(path), 'error': str(error)}), flush=True)
        raise


if __name__ == '__main__':
    main()
