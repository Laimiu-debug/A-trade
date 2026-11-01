"""Source-only SSE main-board disclosure census; no orders or strategy returns."""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_sse_evidence_utils', ROOT / 'scripts/run-liquid-trend-breakout.py')
U = importlib.util.module_from_spec(spec); sys.modules[spec.name] = U; spec.loader.exec_module(U)
BASE = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1/sse-main-2024q4-001'
CALENDAR = ROOT / 'artifacts/strategy-search/market-sector-stock-system-v1/published-snapshot-foundation-v3/calendar.json'
CLIENT = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1/disclosure-source-pilot-001/official-page-client.js'
LIST_URL = 'https://query.sse.com.cn/marketdata/tradedata/queryAllTradeOpenDate.do'
DETAIL_URL = 'https://query.sse.com.cn/marketdata/tradedata/queryTradeOpenInfo.do'


def money_cents(value):
    n = Decimal(str(value)) * 100
    if not n.is_finite() or n < 0 or n != n.to_integral_value():
        raise ValueError('Invalid or sub-cent disclosure amount')
    return int(n)


def date_value(value):
    return datetime.strptime(str(value), '%Y%m%d').date().isoformat()


def normalize(raw, requested_date):
    if raw.get('actionErrors') or raw.get('fieldErrors'):
        raise ValueError('Source reports errors')
    if raw.get('isPagination') not in ('false', False):
        raise ValueError('Native query changed to paginated response')
    rows = raw['pageHelp']['data']
    if not isinstance(rows, list): raise ValueError('Missing source row list')
    events, seats, keys = [], [], set()
    for index, row in enumerate(rows):
        day = date_value(row['tradeDate'])
        start, end = date_value(row['abnormalStart']), date_value(row['abnormalEnd'])
        if day != requested_date or not start <= end <= day:
            raise ValueError('Disclosure date or statistical interval mismatch')
        code, reason = row['secCode'], str(row['refType'])
        if not re.fullmatch(r'\d{6}', code) or not reason.isdigit(): raise ValueError('Malformed source identifier')
        key = '|'.join(('sseMain', day, code, reason, start, end))
        if key in keys: raise ValueError('Duplicate event identity needs explicit source investigation')
        keys.add(key)
        event = {'id': key, 'sourceRow': index, 'disclosureDate': day, 'symbol': 'sh' + code,
                 'name': row['secAbbr'], 'securityType': row['secType'], 'refType': reason,
                 'statisticsStart': start, 'statisticsEnd': end, 'multiDay': start != end,
                 'reportedSecurityAmountCents': money_cents(row['secTxAmount']),
                 'originalPublicationTime': None, 'individualAccountIdentified': False}
        events.append(event)
        for side in ('B', 'S'):
            names = row['branchName' + side].split(',')
            amounts = row['branchTxAmt' + side].split(',')
            if len(names) != len(amounts) or not 1 <= len(names) <= 5:
                raise ValueError('Branch name/amount cardinality mismatch')
            prior = None
            for rank, (name, amount) in enumerate(zip(names, amounts), 1):
                if not name.strip(): raise ValueError('Blank reported branch identity')
                cents = money_cents(amount)
                if prior is not None and cents > prior: raise ValueError('Descending source rank mismatch')
                prior = cents
                anonymous = any(tag in name for tag in ('机构专用', '沪股通', '深股通'))
                seats.append({'id': key + '|' + side + '|' + str(rank), 'eventId': key, 'disclosureDate': day,
                              'symbol': event['symbol'], 'side': side, 'rank': rank, 'branchNameOriginal': name,
                              'amountCents': cents, 'anonymousOrAggregateLabel': anonymous,
                              'statisticsStart': start, 'statisticsEnd': end})
    return events, seats


def register():
    if BASE.exists(): raise ValueError('Preserve previous census; do not overwrite')
    dates = [d for d in U.read(CALENDAR) if '2024-09-24' <= d <= '2024-12-31']
    if len(dates) != 66 or len(set(dates)) != len(dates): raise ValueError('Unexpected source calendar')
    client = CLIENT.read_text('utf-8')
    for endpoint in ('queryAllTradeOpenDate.do', 'queryTradeOpenInfo.do'):
        if endpoint not in client: raise ValueError('Endpoint not bound to official page client')
    BASE.mkdir()
    U.write(BASE / 'plan.json', {'schema': 'sse-main-disclosure-source-census-v1', 'registeredAt': U.now(),
        'collector': U.ref(Path(__file__)), 'calendar': U.ref(CALENDAR), 'officialPageClient': U.ref(CLIENT),
        'dates': dates, 'listUrl': LIST_URL, 'listParams': {'token': 'QUERY', 'flag': '1'},
        'detailUrl': DETAIL_URL, 'detailCheckDateIndices': [0, 11, 22, 33, 44, 55, 65],
        'sourcePurpose': 'Observe disclosed brokerage participation in the first full post-split quarter; no source selection by future returns.',
        'scope': 'Shanghai main-board native daily-query response only; excludes Shenzhen and STAR. Historical source retrieved now, not original publication bytes.',
        'limits': ['Top-five disclosed sides are incomplete brokerage activity; off-list is unknown.',
                   'Overlapping reasons and statistical windows cannot be summed into turnover or positions.',
                   'Anonymous institution/Connect labels are distinct ranked rows and not trackable individuals.',
                   'No direct transaction time; any later model must use information after publication.',
                   'Native nonpaginated response is the coverage contract; total=0/pageSize=10 metadata do not prove empty or limit actual rows.'],
        'workspaceJsonBefore': U.workspace_snapshot(), 'newStrategyTrials': 0, 'newAccounts': 0})
    U.emit({'registered': U.ref(BASE / 'plan.json'), 'dates': len(dates)})


def fetch_json(url, params, target):
    if target.exists():
        receipt = U.read(target.with_suffix('.receipt.json'))
        if U.sha(target) != receipt['sha256'] or not receipt['ok']: raise ValueError('Existing failed or changed source response')
        return json.loads(target.read_bytes())
    response = requests.get(url, params=params, headers={'Referer': 'https://www.sse.com.cn/'}, timeout=18)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    receipt = {'retrievedAt': U.now(), 'url': response.url, 'status': response.status_code,
               'sha256': U.sha(target), 'ok': response.ok, 'bytes': len(response.content)}
    U.write(target.with_suffix('.receipt.json'), receipt)
    response.raise_for_status()
    return response.json()


def run():
    plan = U.read(BASE / 'plan.json')
    for key in ('collector', 'calendar', 'officialPageClient'): U.bound(plan[key])
    for i, day in enumerate(plan['dates']):
        try:
            raw = fetch_json(LIST_URL, {**plan['listParams'], 'tradeDate': day.replace('-', '')}, BASE / 'raw' / (day + '.json'))
            events, seats = normalize(raw, day)
            U.emit({'date': day, 'index': i + 1, 'events': len(events), 'seats': len(seats)})
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            U.write(BASE / ('failure-' + day + '.json'), {'date': day, 'at': U.now(), 'errorType': type(exc).__name__, 'error': str(exc)[:400], 'retried': False})
            raise
        time.sleep(0.5)


def finalize():
    plan = U.read(BASE / 'plan.json')
    for key in ('collector', 'calendar', 'officialPageClient'): U.bound(plan[key])
    all_events, all_seats, source_refs, daily = [], [], [], []
    checks = []
    for index, day in enumerate(plan['dates']):
        path = BASE / 'raw' / (day + '.json')
        receipt = U.read(path.with_suffix('.receipt.json'))
        if not receipt['ok'] or U.sha(path) != receipt['sha256']: raise ValueError('Missing or changed daily source')
        raw = U.read(path); events, seats = normalize(raw, day)
        all_events.extend(events); all_seats.extend(seats); source_refs.append(U.ref(path))
        daily.append({'date': day, 'events': len(events), 'seats': len(seats), 'multiDayEvents': sum(e['multiDay'] for e in events)})
        if index in plan['detailCheckDateIndices'] and events:
            event = sorted(events, key=lambda e: e['id'])[0]
            params = {'orderB': 'desc', 'orderS': 'desc', 'Token': 'QUERY', 'tradeDate': day.replace('-', ''),
                      'refType': event['refType'], 'secCode': event['symbol'][2:]}
            check_path = BASE / 'detail-checks' / (day + '-' + event['symbol'] + '-' + event['refType'] + '.json')
            detail = fetch_json(DETAIL_URL, params, check_path)
            original = detail['pageHelp']['data']
            # Separate detail endpoint, independent field projection; no normalized parser reused here.
            expected = sorted((str(r['bsType']), int(r['branchRank']), r['branchName'], money_cents(r['branchTxAmt']),
                               date_value(r['abnormalStart']), date_value(r['abnormalEnd'])) for r in original)
            observed = sorted((s['side'], s['rank'], s['branchNameOriginal'], s['amountCents'], s['statisticsStart'], s['statisticsEnd'])
                              for s in seats if s['eventId'] == event['id'])
            if expected != observed or detail['pageHelp']['total'] != len(original): raise ValueError('Detail/list cross-endpoint disagreement')
            checks.append({'eventId': event['id'], 'seatsMatched': len(observed), 'source': U.ref(check_path)})
            time.sleep(0.5)
    if len({e['id'] for e in all_events}) != len(all_events) or len({s['id'] for s in all_seats}) != len(all_seats): raise ValueError('Global identity collision')
    activity = defaultdict(lambda: {'dates': set(), 'stockDays': set(), 'stocks': set(), 'B': 0, 'S': 0})
    for seat in all_seats:
        if seat['anonymousOrAggregateLabel']: continue
        a = activity[seat['branchNameOriginal']]
        a['dates'].add(seat['disclosureDate']); a['stockDays'].add((seat['disclosureDate'], seat['symbol'])); a['stocks'].add(seat['symbol'])
        a[seat['side']] += 1
    frequencies = sorted(({'branchNameOriginal': name, 'disclosureDates': len(v['dates']), 'distinctStockDays': len(v['stockDays']),
                           'distinctStocks': len(v['stocks']), 'buySeatMentions': v['B'], 'sellSeatMentions': v['S']}
                          for name, v in activity.items()), key=lambda v: (-v['disclosureDates'], -v['distinctStockDays'], v['branchNameOriginal']))
    U.write(BASE / 'events.json.gz', all_events); U.write(BASE / 'seats.json.gz', all_seats)
    U.write(BASE / 'activity.json', {'definition': 'Disclosure participation frequency, not trade counts, positions, performance or endorsements.',
                                  'rows': frequencies, 'excludeAnonymousFromLongitudinalIdentity': True, 'summedBrokerageMoney': None})
    U.write(BASE / 'validation.json', {'passed': True, 'completedAt': U.now(), 'plan': U.ref(BASE / 'plan.json'),
        'dailySourceFiles': source_refs, 'daysChecked': len(daily), 'events': len(all_events), 'seats': len(all_seats),
        'dailyCounts': daily, 'eventsOutput': U.ref(BASE / 'events.json.gz'), 'seatsOutput': U.ref(BASE / 'seats.json.gz'),
        'activityOutput': U.ref(BASE / 'activity.json'), 'crossEndpointChecks': checks,
        'rawIntervalsPreserved': True, 'anonymousRankedRowsPreserved': True,
        'coveredMarket': 'Shanghai main board native query only', 'allChinaMarketCoverage': False,
        'originalPublicationArchive': False, 'newStrategyAccounts': 0, 'realizedPayoffRatio': None,
        'userJsonFilesUnchanged': U.workspace_snapshot() == plan['workspaceJsonBefore'], 'goalAchieved': False})
    if not U.read(BASE / 'validation.json')['userJsonFilesUnchanged']: raise ValueError('Protected user workspace drift')
    U.emit({'validation': U.ref(BASE / 'validation.json'), 'days': len(daily), 'events': len(all_events), 'seats': len(all_seats), 'detailChecks': len(checks)})


if __name__ == '__main__':
    parser = ArgumentParser(); parser.add_argument('stage', choices=('register', 'run', 'finalize'))
    globals()[parser.parse_args().stage]()
