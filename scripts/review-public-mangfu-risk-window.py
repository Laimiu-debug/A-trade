"""Manual source-image transcription with arithmetic checks, not a trade ledger."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1/mangfu-risk-window-001'
OUT = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1/mangfu-risk-review-001'

# Values were read directly from all 16 source images. Keep the UI's precision,
# units and field meaning. Available funds are not assumed to be total cash.
# No implied fills, fees, average entry price or account returns are produced.
ACCOUNT_COLUMNS = ['replyId', 'assets', 'displayTotalPnl', 'dailyReferencePnl',
                   'dailyReferencePct', 'marketValue', 'availableFunds',
                   'withdrawableFunds', 'positionPct']
ACCOUNTS = [
    ['80849347', '9541229.47', '921791.02', '-320278.44', '-3.25', '9540793.00', '436.47', '436.47', '99.9'],
    ['80920891', '8585549.47', '-33286.89', '-955681.00', '-10.02', '8585112.00', '437.47', '437.47', '99.9'],
    ['80965517', '7774021.47', '-844303.63', '-811528.00', '-9.45', '7773584.00', '437.47', '437.47', '99.9'],
    ['81018978', '8110378.47', '-508158.53', '336357.00', '4.33', '8109941.00', '437.47', '437.47', '99.9'],
    ['81059483', '7870123.47', '-748262.17', '-240255.00', '-2.96', '7869686.00', '437.47', '437.47', '99.9'],
    ['81122283', '7649796.16', '-966037.89', '-223199.50', '-2.84', '3819530.00', '3830266.16', '437.47', '49.9'],
    ['81195362', '10341077.02', '-1272489.78', '-311486.79', '-2.92', '0.00', '10341077.02', '6830407.14', '0.0'],
    ['81253268', '12290915.97', '1947579.32', '1948058.08', '18.83', '2978315.00', '9312600.97', '981.55', '24.2'],
    ['81284967', '11182291.94', '839436.42', '-1111392.24', '-9.04', '2214617.00', '7850013.66', '7850013.66', '19.8'],
    ['81357848', '11126421.74', '-25951.34', '-59582.80', '-0.53', '7845134.00', '3281287.74', '3281287.74', '70.5'],
    ['81410847', '11381394.07', '230441.01', '247603.04', '2.22', '5029020.00', '6352374.07', '259.93', '44.2'],
    ['81477271', '1163.07万', '26.60万', '24.23万', '2.13', '777.61万', '385.46万', '385.46万', '66.9'],
    ['81539171', '1198.63万', '62.13万', '34.86万', '2.99', '843.66万', '354.98万', '354.98万', '70.4'],
    ['81582531', '1145.50万', '91827.76', '-54.23万', '-4.52', '543.58万', '601.92万', '354.98万', '47.5'],
]
POSITION_COLUMNS = ['name', 'quantity', 'availableQuantity', 'currentPrice', 'marketValue', 'displayPnl']
POSITIONS = {
    '80849347': [['城地香江', 533900, 533500, '17.870', '9540793.00', '921791.02']],
    '80920891': [['城地香江', 533900, 533900, '16.080', '8585112.00', '-33286.89']],
    '80965517': [['城地香江', 533900, 533900, '14.560', '7773584.00', '-844303.63']],
    '81018978': [['城地香江', 533900, 533900, '15.190', '8109941.00', '-508158.53']],
    '81059483': [['城地香江', 533900, 533900, '14.740', '7869686.00', '-748262.17']],
    '81122283': [['城地香江', 267100, 267100, '14.300', '3819530.00', '-966037.89']],
    '81195362': [['城地香江', 0, 0, '13.360', '0.00', '-1272489.78']],
    '81253268': [['N红四方', 18500, 0, '160.990', '2978315.00', '1947579.32']],
    # The final digits of this source price are clipped: retain unknown rather
    # than calculating an alleged observed quote from value / quantity.
    '81284967': [['城地香江', 158300, 0, None, '2214617.00', '33441.46'],
                 ['C红四方', 0, 0, '81.000', '0.00', '805994.96']],
    '81357848': [['城地香江', 239800, 158300, '13.510', '3239698.00', '-58007.39'],
                 ['锦龙股份', 165200, 0, '17.930', '2962036.00', '65680.24'],
                 ['日出东方', 83000, 0, '19.800', '1643400.00', '-33624.19']],
    '81410847': [['城地香江', 361800, 119900, '13.900', '5029020.00', '18472.58'],
                 ['锦龙股份', 0, 0, '19.720', '0.00', '346158.23'],
                 ['日出东方', 0, 0, '21.780', '0.00', '-134189.80']],
    '81477271': [['远达环保', 382700, 0, '17.050', '652.50万', '17.28万'],
                 ['城地香江', 88600, 88600, '14.120', '125.10万', '93196.17']],
    '81539171': [['远达环保', 331200, 191400, '18.000', '596.16万', '53.43万'],
                 ['城地香江', 87100, 0, '14.310', '124.64万', '87929.80'],
                 ['肇民科技', 40400, 0, '30.410', '122.86万', '-910.14']],
    '81582531': [['远达环保', 246200, 0, '17.100', '421.00万', '9471.78'],
                 ['肇民科技', 39568, 0, '30.980', '122.58万', '-5940.60'],
                 ['城地香江', 0, 0, '14.230', '0.00', '88296.58']],
}


def amount(raw):
    return Decimal(raw[:-1]) * 10000 if raw.endswith('万') else Decimal(raw)


def half_step(raw):
    value = Decimal(raw[:-1] if raw.endswith('万') else raw)
    return Decimal(10) ** value.as_tuple().exponent * (10000 if raw.endswith('万') else 1) / 2


def ref(path):
    return {'path': str(path.relative_to(ROOT)).replace('\\', '/'),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    if OUT.exists():
        raise ValueError('Preserve completed source review')
    manifest = json.loads((SOURCE / 'manifest.json').read_text(encoding='utf-8'))
    for image in manifest['images']:
        if hashlib.sha256((ROOT / image['file']).read_bytes()).hexdigest() != image['sha256']:
            raise ValueError('Original source image drift')
    posts = {row['replyId']: row for row in manifest['posts']}
    observations, checks = [], []
    stock_rows = price_checks = 0
    for row in ACCOUNTS:
        account = dict(zip(ACCOUNT_COLUMNS, row, strict=True))
        post = posts[account['replyId']]
        account.update({'sourceImage': post['imagePaths'][0],
                        'publishedAtLocal': post['publishedAtLocal'],
                        'screenshotTradingDateExplicitlyShown': False,
                        'snapshotTradingDateVerified': False,
                        'positions': [dict(zip(POSITION_COLUMNS, entry, strict=True)) for entry in POSITIONS[account['replyId']]]})
        account['displayCostIsNotAnAuditedEntryPrice'] = True
        if account['replyId'] in ('81253268', '81410847'):
            account['excludedNonStockUiRows'] = ['新标准券: quantity0, value0; not a stock holding or trade']
        pnl_sum = sum(amount(p['displayPnl']) for p in account['positions'])
        mv_sum = sum(amount(p['marketValue']) for p in account['positions'])
        for label, summed, key in [('positionPnl', pnl_sum, 'displayTotalPnl'), ('positionValue', mv_sum, 'marketValue')]:
            pkey = 'displayPnl' if label == 'positionPnl' else 'marketValue'
            tolerance = half_step(account[key]) + sum(half_step(p[pkey]) for p in account['positions'])
            residual = summed - amount(account[key])
            if abs(residual) > tolerance:
                raise ValueError(f'{account["replyId"]} {label} transcription does not reconcile')
            checks.append({'replyId': account['replyId'], 'check': label, 'residualYuan': str(residual),
                           'displayRoundingToleranceYuan': str(tolerance), 'passed': True})
        for p in account['positions']:
            stock_rows += 1
            if p['quantity'] < 0 or not 0 <= p['availableQuantity'] <= p['quantity']:
                raise ValueError('Invalid transcribed holding quantity')
            if p['currentPrice'] is not None:
                residual = Decimal(p['quantity']) * Decimal(p['currentPrice']) - amount(p['marketValue'])
                if abs(residual) > half_step(p['marketValue']):
                    raise ValueError(f'{account["replyId"]} {p["name"]} price-times-shares mismatch')
                price_checks += 1
        # This is a diagnostic residual, not a failed cash balance: funds marked
        # "available" exclude frozen/unavailable components that are not shown.
        account['assetsMinusMarketValueMinusAvailableFundsYuan'] = str(
            amount(account['assets']) - amount(account['marketValue']) - amount(account['availableFunds']))
        account['residualIsAnAuditedCashFlow'] = False
        observations.append(account)
    monthly = [
        {'sourceImage': posts['81427490']['imagePaths'][0],
         'explicitPeriodStart': '2023-11-01', 'explicitPeriodEnd': '2023-11-30',
         'displayPnlYuan': '-2626506.81', 'displayReturnPct': '-22.99',
         'belongsToPrimaryPost20240924Period': False, 'note': 'Published in a 2024 comparison; source image itself explicitly labels 2023.'},
        {'sourceImage': posts['81427490']['imagePaths'][1],
         'explicitPeriodStart': None, 'explicitPeriodEnd': None,
         'displayPeriodLabel': '本月', 'displayPnlYuan': '-776872.32', 'displayReturnPct': '-10.73',
         'belongsToPrimaryPost20240924Period': None,
         'note': 'Full year/date is absent. Publication context suggests a 2024 comparison but does not independently verify date or cash-flow treatment.'},
    ]
    explanations = [
        {'replyId': '80965517', 'kind': 'trader-after-market-explanation',
         'paraphrase': '作者质疑单股满仓的效果，并考虑同时做两个标的。'},
        {'replyId': '81195362', 'kind': 'trader-after-market-explanation',
         'paraphrase': '作者提到补充资金，并说明这个实盘户的资产并非从早期一路盈利累积而来。'},
        {'replyId': '81253268', 'kind': 'trader-after-market-explanation',
         'paraphrase': '作者将操作描述为空仓、补充资金后参与新股。'},
        {'replyId': '81427490', 'kind': 'trader-retrospective-month-summary',
         'paraphrase': '作者提到十月机会过多导致失序，后来转向熟悉的重组方向；自评仓位和操作节奏失误。'},
        {'replyId': '81582531', 'kind': 'trader-after-market-explanation',
         'paraphrase': '作者将亏损归因于高位加仓。完整成交尚未核实其具体加仓时点与数量。'},
    ]
    missing = ['All fills and execution timestamps', 'Complete fees and financing records',
               'Opening inventory and actual cash balances', 'Dated bank/broker cash movements',
               'Verified screenshot capture/trading dates', 'Historical market/theme candidate universe',
               'Intraday liquidity and executable order paths, especially IPO first-day trades']
    readout = {
        'schema': 'public-first-person-risk-window-review-v1',
        'completedAt': datetime.now(timezone.utc).isoformat(),
        'reviewerScript': ref(Path(__file__)), 'sourceManifest': ref(SOURCE / 'manifest.json'),
        'sourcePages': [p['url'] for p in manifest['pages']],
        'sourceImagesVisuallyReviewed': 16, 'publicAuthorReplies': 15,
        'accountSnapshotsTranscribed': len(observations), 'displayStockRowsTranscribed': stock_rows,
        'accountSnapshots': observations, 'monthlyComparisonImages': monthly,
        'firstPersonExplanations': explanations,
        'arithmeticChecks': checks, 'stockPriceTimesQuantityChecksPassed': price_checks,
        'checksValidate': 'Transcription arithmetic and original-image bytes only; not authenticity, cash flows, fills, returns or a trading method.',
        'researchObservations': [
            {'kind': 'source-fact', 'ids': ['80849347', '80965517'],
             'observation': 'Both show 城地香江533900shares, displayed99.9percent position; the displayed row profit changes from921791.02 to-844303.63. Snapshots do not prove absence of intraday trades.'},
            {'kind': 'source-fact', 'ids': ['81122283', '81195362'],
             'observation': 'Later displayed quantity becomes267100 and then0. Exact intervening execution times/prices/fees are unavailable.'},
            {'kind': 'source-fact', 'ids': ['81253268', '81284967'],
             'observation': 'An IPO holding shows18500shares then0, while its displayed row profit changes from1947579.32 to805994.96. These are UI profit fields, not an audited realized cycle.'},
            {'kind': 'research-inference', 'observation': 'Entry quality must be investigated jointly with concentration, add-on trades, profit retention and exit feasibility. These examples do not establish a winning allocation or stop rule.'},
        ],
        'missingForAccountAudit': missing,
        'sameAccountContinuityIndependentlyVerified': False,
        'competitionStandingOrLeadingAccountStatusVerified': False,
        'verifiedClosedEpisodes': 0, 'wholeLiveLedgersAcquired': 0,
        'netRealizedPayoffRatio': None, 'profitFactor': None, 'auditedReturn': None, 'auditedDrawdown': None,
        'newStrategyTrials': 0, 'newStrategyAccounts': 0, 'targetAchieved': False,
    }
    OUT.mkdir()
    (OUT / 'readout.json').write_text(json.dumps(readout, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'readout': ref(OUT / 'readout.json'), 'snapshots': len(observations),
                      'stockRows': stock_rows, 'aggregateArithmeticChecksPassed': len(checks),
                      'priceQuantityChecksPassed': price_checks, 'verifiedClosedEpisodes': 0}))


if __name__ == '__main__':
    main()
