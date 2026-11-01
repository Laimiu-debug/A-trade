"""Compare source screenshots with pinned daily context; create no signals."""
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path

import bs4
import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1'
OUT = BASE / 'mangfu-risk-context-001'


def ref(path):
    return {'path': str(path.relative_to(ROOT)).replace('\\', '/'),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    if OUT.exists():
        raise ValueError('Preserve prior source-context comparison')
    review = BASE / 'mangfu-risk-review-001/readout.json'
    r = json.loads(review.read_text(encoding='utf-8'))
    assert r['verifiedClosedEpisodes'] == 0 and r['auditedReturn'] is None
    matrix = ROOT / 'artifacts/strategy-search/market-sector-stock-system-v1/price-matrix-v1/prices.npz'
    matrix_ref = ref(matrix)
    assert matrix_ref['sha256'] == '07411701006794937428d1c1b2232d1c99456b34db6c032fc7ca12f076eaee6e'
    admission = ROOT / 'artifacts/strategy-search/market-sector-stock-accounts-v1/attempt-001/formation.candidate.json.gz'
    admission_ref = ref(admission)
    assert admission_ref['sha256'] == 'b5628746426dd360fe056139c866d9abfecbf97023c41ef817536466b92075c9'
    with gzip.open(admission, 'rt', encoding='utf-8') as f:
        days = {row['date']: row for row in json.load(f)['daily']}
    comparisons = []
    with np.load(matrix, allow_pickle=False) as prices:
        calendar = prices['calendar'].tolist()
        row = prices['symbols'].tolist().index('sh603887')
        for account in r['accountSnapshots']:
            date = account['publishedAtLocal'][:10]
            holding = next((p for p in account['positions'] if p['name'] == '城地香江'), None)
            if holding is None or holding['currentPrice'] is None or date not in calendar:
                continue
            i = calendar.index(date)
            observed = Decimal(holding['currentPrice'])
            close = Decimal(str(prices['close'][row, i]))
            candidates = [calendar[j] for j in range(max(0, i - 4), i + 1)
                          if prices['priceValid'][row, j]
                          and Decimal(str(prices['close'][row, j])) == observed]
            comparisons.append({'replyId': account['replyId'], 'publishedAtLocal': account['publishedAtLocal'],
                                'image': account['sourceImage'], 'displayCurrentPrice': str(observed),
                                'publicationDayLocalDailyClose': str(close),
                                'matchesPublicationDayClose': close == observed,
                                'matchingCloseDatesWithinFiveTradingDays': candidates,
                                'matchingQuoteProvesTradingOrExecutionDate': False,
                                'sourceSnapshotIsNecessarilyClosingData': False})
    market_rows = []
    for date in ['2024-11-15', '2024-11-18', '2024-11-19', '2024-11-20', '2024-11-21', '2024-11-22']:
        market = days[date]['market']
        market_rows.append({'date': date, 'tdxShSzBroadTurnoverProxyYuan': market['turnover'],
                            'proxy5DayMeanToPrior20DayMean': market['turnover5ToPrior20'],
                            'existingResearchMarketAdmission': market['admission'],
                            'existingResearchMarketRiskExit': market['riskExit'],
                            'isTradersOwnRecordedMarketRule': False})
    # Independently identify the new listing on the exchange's original notice.
    listing_url = 'https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20241125_10764891.shtml'
    response = requests.get(listing_url, timeout=25)
    response.raise_for_status()
    response.encoding = 'utf-8'
    text = ''.join(bs4.BeautifulSoup(response.text, 'html.parser').stripped_strings)
    assert '603395' in text and '于2024年11月26日起上市交易' in text
    listing = {'url': listing_url, 'httpStatus': response.status_code,
               'responseSha256': hashlib.sha256(response.content).hexdigest(), 'responseBytesRetained': False,
               'publisher': '上海证券交易所', 'announcementDate': '2024-11-25',
               'symbol': 'sh603395', 'securityName': '红四方', 'listingDate': '2024-11-26',
               'fact': 'The screenshot published on2024-11-26 shows a security on its official first listing date.',
               'inference': 'Its first-day execution and risk cannot be reproduced from an ordinary close-to-next-open breakout rule or a displayed cost field.'}
    first = next(x for x in r['accountSnapshots'] if x['replyId'] == '80849347')
    later = next(x for x in r['accountSnapshots'] if x['replyId'] == '80965517')
    assert first['positions'][0]['quantity'] == later['positions'][0]['quantity'] == 533900
    price_change = (Decimal(later['positions'][0]['currentPrice']) / Decimal(first['positions'][0]['currentPrice']) - 1) * 100
    payload = {
        'schema': 'public-risk-window-source-context-v1', 'completedAt': datetime.now(timezone.utc).isoformat(),
        'builder': ref(Path(__file__)), 'review': ref(review), 'matrix': matrix_ref,
        'priorResearchMarketContext': admission_ref, 'quoteComparisons': comparisons,
        'samePublicationDayCloseMatches': sum(x['matchesPublicationDayClose'] for x in comparisons),
        'quoteDateMismatchesAreNotAutomaticallySourceErrors': True,
        'specificDateCautions': [
            {'replyId': '81357848', 'observation': 'Publication is2024-11-29 00:23, while screenshot13.510 matches local2024-11-28 close. Keep publication time separate from unverified capture date.'},
            {'replyId': '81195362', 'observation': 'Screenshot price13.360 differs from local2024-11-25 close13.40; source phone clock is2:59. An intraday image need not be a closing snapshot. It is not an execution price.'}],
        'marketContext': market_rows,
        'marketScope': 'Pinned TDX SH/SZ broad turnover proxy and an existing research filter; not an official total-market census or proof of what the trader used. Existing survivor/cohort and raw-price limitations remain.',
        'officialNewListingContext': listing,
        'displayQuoteChangeNov15To19Pct': str(price_change),
        'quoteChangeIsNotRealizedAccountProfit': True,
        'hypothesesToInvestigateWithFullLedgers': [
            'Concentration and exit failure can coexist with an admitted liquid market. A market condition alone does not establish a complete system.',
            'Determine whether profits survive add-on orders and T+1 restrictions, using actual fills rather than displayed floating profit.',
            'Separate IPO first-day and ordinary-stock contributions before attributing big wins to a general entry pattern.',
            'Distinguish theme-level trial losses from independent trades and reconcile external funding before account return calculations.'],
        'newStrategyTrials': 0, 'newStrategyAccounts': 0, 'verifiedClosedEpisodes': 0,
        'netRealizedPayoffRatio': None, 'auditedAccountReturn': None, 'targetAchieved': False,
    }
    OUT.mkdir()
    (OUT / 'readout.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'readout': ref(OUT / 'readout.json'), 'quoteComparisons': len(comparisons),
                      'sameDayCloseMatches': payload['samePublicationDayCloseMatches'],
                      'marketContextDays': len(market_rows), 'targetAchieved': False}))


if __name__ == '__main__':
    main()
