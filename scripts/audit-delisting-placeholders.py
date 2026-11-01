"""Distinguish retained nontrading rows from actual trading near delisting."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/strategy-search'
SOURCE = BASE / 'evidence-missing-history-v1'
OUTPUT = BASE / 'delisting-placeholder-audit-v1.json'

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    validation = read(SOURCE / 'validation.json')
    manifest = read(SOURCE / 'manifest.json')
    if sha(SOURCE / 'manifest.json') != validation['manifestSha256']:
        raise ValueError('Validated manifest changed')
    rows, issues = [], []
    for info in validation['details']:
        if not info['basicRows'] or not info['basicRows'][0]['outDate'] or info['basicRows'][0]['outDate'] > '2023-12-31':
            continue
        symbol = info['symbol']
        record = next(r for r in manifest['requests'] if r['id'] == symbol+'-daily')
        if sha(SOURCE / record['path']) != record['sha256']:
            raise ValueError('Daily response changed')
        payload = read(SOURCE / record['path'])
        if payload['complete'] is not True:
            raise ValueError('Incomplete source')
        daily = [dict(zip(payload['fields'], r)) for r in payload['rows']]
        traded = [r for r in daily if r['tradestatus'] == '1' and r['volume'] and float(r['volume']) > 0]
        latest = traded[-1] if traded else None
        trailing = [r for r in daily if latest is None or r['date'] > latest['date']]
        placeholders = [r for r in trailing if r['tradestatus'] == '0' and r['volume'] in ('', '0', '0.0000') and r['amount'] in ('', '0', '0.0000')]
        boundary = [r for r in daily if r['date'] == info['basicRows'][0]['outDate']]
        if len(placeholders) != len(trailing):
            issues.append({'symbol': symbol, 'issue': 'non-placeholder row after last observed trading date'})
        rows.append({'symbol': symbol, 'dailySourceSha256': record['sha256'],
            'reportedOutDate': info['basicRows'][0]['outDate'], 'dailyResponseEnd': daily[-1]['date'],
            'lastObservedPositiveVolumeDate': latest['date'] if latest else None,
            'trailingNontradingRows': len(placeholders), 'atOutDateRows': boundary,
            'atOutDateRowsAreTradingEvidence': False,
            'interpretation': 'Retained nontrading values are not executable quotes; do not manufacture fills from copied OHLC.'})
    report = {'schemaVersion': 1, 'checkedAt': datetime.now(timezone.utc).isoformat(),
        'sourceValidationSha256': sha(SOURCE / 'validation.json'), 'scriptSha256': sha(Path(__file__)),
        'scope': 'Three fixed v1 samples with reported outDate within the requested daily window; no strategy returns calculated',
        'rows': rows, 'issues': issues,
        'primaryNoticeCrossCheck': [{
            'symbol': 'sz000606', 'issuer': '顺利办信息服务股份有限公司', 'noticeNumber': '2023-046',
            'title': '关于公司股票终止上市暨摘牌的公告',
            'url': 'https://static.cninfo.com.cn/finalpage/2023-07-06/1217217338.PDF',
            'publicationDate': '2023-07-06', 'noticeLastTradingDate': '2023-07-05', 'noticeDelistingDate': '2023-07-06',
            'readMethod': 'Web PDF text pages 1-2; paraphrased factual fields only; no local full-document hash',
            'conclusion': 'For this issuer the reported outDate is the nontrading delisting day, while the preceding day is the last trading day.',
            'notGeneralizedToAllSecurities': True}],
        'limitations': ['A trailing row is not proof of active trading. Missing or zero volume must be interpreted together with explicit trade status.',
                        'The one issuer notice does not establish the effective dates or publication-time availability of every security.',
                        'No original engine, normalizer, strategy rules or results changed.'],
        'certified': False, 'targetAchieved': False}
    with OUTPUT.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({'securities': len(rows), 'issues': len(issues), 'rows': [{'symbol': r['symbol'], 'lastTrade': r['lastObservedPositiveVolumeDate'], 'reportedOutDate': r['reportedOutDate'], 'trailingNontradingRows': r['trailingNontradingRows']} for r in rows]}))

if __name__ == '__main__':
    main()
