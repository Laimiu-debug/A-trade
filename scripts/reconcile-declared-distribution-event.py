"""Reconcile one documented distribution with frozen position marks, not returns.

This does not run a strategy or invent stock/cash availability. It measures the
gross entitlement omitted by a known raw-price account on the ex-date.
"""
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/strategy-search'
OLD = BASE / 'account-exit-comparison-v1'
EVIDENCE = BASE / 'corporate-action-evidence-v1/sz002460-20220704'
OUT = BASE / 'declared-distribution-event-reconciliation-v1.json'
FACT_SHA = 'aed22ec56b37e14d1a9e2c2fbe142f83f6ff6ef67c28fab632757a9a33ec4809'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    if path.suffix == '.gz':
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            return json.load(stream)
    return json.loads(path.read_text(encoding='utf-8'))


def number(value):
    return Decimal(str(value))


def main():
    paths = [Path(__file__).resolve(), EVIDENCE / 'fact.json', OLD / 'protocol.json',
             OLD / 'independent-result-verification.json',
             OLD / 'attempts/attempt-0001/completion.json',
             OLD / 'attempts/attempt-0001/trail10.result.json.gz']
    checks = []

    def require(value, label):
        if not value:
            raise ValueError(label)
        checks.append(label)

    require(sha(EVIDENCE / 'fact.json') == FACT_SHA, 'fixed issuer fact identity')
    fact, audit, complete, result = map(read, (paths[1], paths[3], paths[4], paths[5]))
    require(audit['passed'] is True and audit['failureCount'] == 0, 'prior independent account audit passed')
    require(audit['protocolHash'] == complete['protocolHash'] == read(paths[2])['protocolHash'], 'prior protocol links')
    for item in complete['files'].values():
        require(sha(OLD / item['path']) == item['sha256'], 'prior completed file: ' + item['path'])
    for receipt in fact['sourceReceipts']:
        for name, expected in ((receipt['path'], receipt['sha256']),
                               (receipt['receiptPath'], receipt['receiptSha256'])):
            path = EVIDENCE / name
            require(sha(path) == expected, 'issuer source identity: ' + name)
            paths.append(path)
    require(fact['localVerification']['passed'] is True, 'issuer terms locally checked')
    require(fact['symbol'] == 'sz002460' and fact['shareClass'] == 'A', 'event security and share class')
    require(max(fact['headerDate'], fact['bodyDate']) < fact['recordDate'] < fact['exDate'], 'dated document precedes record and ex')
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}
    rows = {}
    for day in (fact['recordDate'], fact['exDate']):
        daily = [r for r in result['daily'] if r['date'] == day]
        require(len(daily) == 1, 'one frozen daily row: ' + day)
        held = [r for r in daily[0]['holdings'] if r['symbol'] == fact['symbol']]
        require(len(held) == 1 and held[0]['priceDate'] == day, 'one same-date marked position: ' + day)
        rows[day] = held[0]
    record, ex = (rows[fact[k]] for k in ('recordDate', 'exDate'))
    q = number(record['quantity'])
    require(q == 400 and q == number(ex['quantity']), 'old account retained 400 shares')
    require(record['costBasis'] == ex['costBasis'], 'unchanged old position cost basis')
    between = [r for r in result['trades'] if r['symbol'] == fact['symbol'] and fact['recordDate'] < r['date'] <= fact['exDate']]
    require(not between, 'no simulated intervening trades')
    p0, p1 = number(record['markPrice']), number(ex['markPrice'])
    require(p0 > 0 and p1 > 0, 'positive frozen same-date marks')
    gross = q * number(fact['cashGrossPerShare'])
    bonus = q * number(fact['capitalizationSharesPerShare'])
    require(bonus == bonus.to_integral_value(), 'no unresolved fractional allocation in this lot')
    q1 = q + bonus
    prior = q * p0
    unadjusted = q * p1
    entitlement = q1 * p1 + gross
    require(q1 == 560 and gross == 120, '400 original shares imply 560 share interests plus gross 120')
    require(unadjusted == number(ex['marketValue']) and prior == number(record['marketValue']), 'recorded old market values independently recomputed')
    omitted = entitlement - unadjusted
    require(omitted == bonus * p1 + gross, 'omitted value equals extra share interests plus gross cash')
    cash_scenarios = []
    for band in fact['tax']['ordinaryUnrestrictedIndividual']['bands']:
        tax = q * number(band['supplementalTaxPerOriginalShare'])
        cash_scenarios.append({'condition': band['holdingPeriod'], 'possibleLaterCashTax': float(tax),
                               'grossCashLessThatTax': float(gross - tax),
                               'timingSimulated': False, 'investorCategoryConfirmed': False})
    require(before == {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}, 'all source bytes unchanged')
    report = {
        'schema': 'declared-distribution-event-reconciliation-v1',
        'createdAt': datetime.now(timezone.utc).isoformat(), 'symbol': fact['symbol'],
        'sourceFiles': before, 'sourceProtocolHash': audit['protocolHash'],
        'checks': checks, 'failureCount': 0, 'passed': True,
        'grain': 'one frozen simulated record-date lot; record-close to ex-date-close gross entitlement mark comparison',
        'recordDate': fact['recordDate'], 'exDate': fact['exDate'],
        'recordCloseMark': float(p0), 'exCloseMark': float(p1),
        'recordDateShares': int(q), 'capitalizationShareEntitlement': int(bonus),
        'postCapitalizationShareInterests': int(q1), 'cashGrossEntitlement': float(gross),
        'recordCloseStockValue': float(prior),
        'oldRawQuantityExCloseStockValue': float(unadjusted),
        'grossEntitlementMarkedValue': float(entitlement),
        'omittedGrossEntitlementMarkedValue': float(omitted),
        'oldRawQuantityMarkChangePct': float((unadjusted / prior - 1) * 100),
        'grossEntitlementMarkChangePct': float((entitlement / prior - 1) * 100),
        'arithmeticExReferenceUnrounded': float((p0 - number(fact['cashGrossPerShare'])) / (1 + number(fact['capitalizationSharesPerShare']))),
        'referenceMeaning': 'arithmetic identity only; not a verified exchange reference, tick rounding, auction price or executable fill',
        'ordinaryUnrestrictedIndividualCashTaxScenarios': cash_scenarios,
        'limits': [
            'Share interests include issued or receivable capitalization rights; they do not establish that all 560 shares were available for sale at that opening.',
            'The gross cash entitlement is not proven usable cash at the opening. Cash income tax is conditional; separate capitalization tax treatment is not established here.',
            'The mark change is one existing lot over one event, before investor-specific tax and hypothetical sale fees. It is not strategy return, an account correction, or a counterfactual trade path.',
            'The frozen signals, account results, shares, cash and quarantine remain unchanged. Other corporate actions and historical identities remain unresolved.',
        ],
        'strategyTrialsExecuted': 0, 'newSalesSimulated': False, 'accountCurveReplayed': False,
        'historicalUniverseComplete': False, 'certified': False, 'targetAchieved': False,
    }
    with OUT.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({k: report[k] for k in ('passed', 'recordDateShares', 'postCapitalizationShareInterests',
                    'cashGrossEntitlement', 'oldRawQuantityMarkChangePct', 'grossEntitlementMarkChangePct',
                    'omittedGrossEntitlementMarkedValue', 'strategyTrialsExecuted')}))
    print(json.dumps({'report': str(OUT), 'sha256': sha(OUT)}))


if __name__ == '__main__':
    main()
