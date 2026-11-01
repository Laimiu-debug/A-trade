"""Verify two sealed public dividend responses and their evidence normalization.

No network, price loading, account simulation, cache writes or execution-action
generation. Issuer assertions remain distinct from provider-reported fields.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
SELF_SHA = hashlib.sha256(SELF.read_bytes()).hexdigest()
BASE = 'artifacts/strategy-search/corporate-action-provider-capability-v1/'
PROBE = BASE + 'sz002460-2022-2023-probe/'
FACT_BASE = 'artifacts/strategy-search/corporate-action-evidence-v1/sz002460-20220704/'
MODULE = 'service/research_distribution_evidence.py'
OUTPUT = ROOT / 'artifacts/strategy-search/corporate-action-normalization-v1.json'
PINS = {
    MODULE: 'f2ef5732169df3d6fcedcea5e35d1382ea2ba71af93fdeaebd0816e8ea461885',
    PROBE + 'dividend-operate-2022.json': '077e99157fa999ebe10d9811c62af4b3a1c2c0102db5e88c2617452a411ef219',
    PROBE + 'dividend-operate-2023.json': '117636ee2ff43195bbf66d0d3cda0ae5fd3801165c25f40a2ffbdf56f06bbc10',
    PROBE + 'protocol.json': 'b07c7ba3ebfb3bb1d41996a8a3b4a954c5f01bee40c0faec83d3dbd9a1ce31c6',
    PROBE + 'manifest.json': '5bfc40556912ec451c7cf063fb959b11b7b2cc68be0904afe90db84d4985a3d4',
    PROBE + 'process.json': '8cdb86e4f50fba247f99ddf97a4f8a731cf79a96a41e47192185bb9d2e22835f',
    FACT_BASE + 'fact.json': 'aed22ec56b37e14d1a9e2c2fbe142f83f6ff6ef67c28fab632757a9a33ec4809',
    FACT_BASE + '2022062400934_c.pdf': '8c3fb4c1a102a1ac7d670a86e48f0b1187892ff3f3d769619914cf657f8d72dc',
    FACT_BASE + '1214472922.PDF': 'f1f9517a3702eda12706eb8f5b681458152fa614c59ab3cf2d3329021e2973b1',
    FACT_BASE + 'source-receipt.json': 'cfa625c5be796ac8876729d097a8f4c0a1ecf9a450dfe1bd75bae0bbed43e7b6',
    FACT_BASE + 'implementation-source-receipt.json': '4eb0b5aeea43efb3c3e2b8bd8245178d32e2ba91f3ff44482a6568c583e373df',
    BASE + 'sz002460-2023-implementation.pdf': '0f3176eff97bf5e8e96c1bb721367116a35ec9be69613ebd869c941e6905237c',
    BASE + 'sz002460-2023-implementation.receipt.json': 'd15408930ffd5ba15ff60642b69ee781d5e62084cfad863da7562519c5a3ac3a',
    BASE + 'official-dividInfo.md': 'b3b60b2044899f35aefc40ddca023a69883204ba55d5754049e7fc8593a6f2a1',
}
EXPECTED_FIELDS = {
    'code', 'dividPreNoticeDate', 'dividAgmPumDate', 'dividPlanAnnounceDate',
    'dividPlanDate', 'dividRegistDate', 'dividOperateDate', 'dividPayDate',
    'dividStockMarketDate', 'dividCashPsBeforeTax', 'dividCashPsAfterTax',
    'dividStocksPs', 'dividCashStock', 'dividReserveToStockPs',
}
DATE_MAP = {
    'preNoticeDate': 'dividPreNoticeDate', 'agmAnnouncementDate': 'dividAgmPumDate',
    'planAnnouncementDate': 'dividPlanAnnounceDate', 'planDate': 'dividPlanDate',
    'recordDate': 'dividRegistDate', 'exDate': 'dividOperateDate',
    'payDate': 'dividPayDate', 'shareListingDate': 'dividStockMarketDate',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def read(path):
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def verify():
    started = time.monotonic()
    checks, failures, normalized, reconciliations = [], [], [], []

    def check(condition, name):
        checks.append(name)
        if not condition:
            failures.append({'check': name})

    def eq(actual, expected, name):
        check(type(actual) is type(expected) and actual == expected, name)

    try:
        for name, expected in PINS.items():
            eq(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected, 'source-sha:' + name)
        eq(hashlib.sha256(SELF.read_bytes()).hexdigest(), SELF_SHA, 'verifier-start-sha')
        if failures:
            raise ValueError('Frozen input identity mismatch')
        spec = importlib.util.spec_from_file_location('_verified_distribution_evidence', ROOT / MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        manifest, protocol, process = [read(ROOT / (PROBE + name + '.json')) for name in ('manifest', 'protocol', 'process')]
        eq(manifest['status'], 'completed', 'probe-completed')
        eq(manifest['login']['errorCode'], '0', 'login-success')
        eq(manifest['logout']['errorCode'], '0', 'logout-success')
        eq(manifest['wireSends'], 4, 'bounded-four-wire-sends')
        eq(process['exitCode'], 0, 'process-success')
        eq(process['timedOut'], False, 'no-wall-timeout')
        eq(process['manifestSha256'], PINS[PROBE + 'manifest.json'], 'process-manifest-binding')
        check(datetime.fromisoformat(protocol['frozenAt']) < datetime.fromisoformat(manifest['startedAt']), 'protocol-before-network')
        expected_requests = [{'code': 'sz.002460', 'year': str(y), 'yearType': 'operate'} for y in (2022, 2023)]
        eq(protocol['requests'], expected_requests, 'protocol-only-two-operate-years')
        eq(manifest['requests'], expected_requests, 'manifest-only-two-operate-years')
        eq(len(manifest['results']), 2, 'two-response-receipts')
        fact = read(ROOT / (FACT_BASE + 'fact.json'))
        eq(fact['symbol'], 'sz002460', 'issuer-fact-symbol')
        eq(fact['shareClass'], 'A', 'issuer-fact-share-class')
        eq(fact['localVerification']['passed'], True, 'issuer-fact-local-review')
        eq(fact['shareListingDate'], None, 'issuer-2022-listing-remains-unknown')
        eq(fact['cashIntradayAvailableTime'], None, 'issuer-intraday-cash-remains-unknown')
        for receipt in fact['sourceReceipts']:
            eq(receipt['sha256'], PINS[FACT_BASE + receipt['path']], 'issuer-pdf-binding:' + receipt['path'])
            eq(receipt['receiptSha256'], PINS[FACT_BASE + receipt['receiptPath']], 'issuer-receipt-binding:' + receipt['path'])
        r2023 = read(ROOT / (BASE + 'sz002460-2023-implementation.receipt.json'))
        eq(r2023['sha256'], PINS[BASE + 'sz002460-2023-implementation.pdf'], '2023-primary-pdf-receipt')
        eq(r2023['url'], 'https://static.cninfo.com.cn/finalpage/2023-07-06/1217219337.PDF', '2023-primary-url')
        for year, expected_request in zip((2022, 2023), expected_requests):
            relative = PROBE + f'dividend-operate-{year}.json'
            response = read(ROOT / relative)
            prefix = str(year) + ':'
            eq(response['provider'], 'baostock', prefix + 'provider')
            eq(response['api'], 'query_dividend_data', prefix + 'api')
            eq(response['parameters'], expected_request, prefix + 'parameters')
            eq(response['errorCode'], '0', prefix + 'error-zero')
            for key, expected in (('rowBoundReached', False), ('sdkIteratorExhausted', True), ('completeResponse', True)):
                eq(response[key], expected, prefix + key)
            fields = response['fields']
            check(isinstance(fields, list) and all(isinstance(f, str) for f in fields), prefix + 'field-list')
            eq(len(fields), 14, prefix + 'field-count')
            eq(len(set(fields)), 14, prefix + 'unique-fields')
            check(set(fields) == EXPECTED_FIELDS, prefix + 'exact-field-set')
            eq(len(response['rows']), 1, prefix + 'one-row')
            values = response['rows'][0]
            check(isinstance(values, list) and len(values) == len(fields) and all(isinstance(v, str) for v in values), prefix + 'exact-string-row-shape')
            if failures:
                raise ValueError('Response contract failed; do not normalize')
            row = dict(zip(fields, values))
            eq(row['code'], 'sz.002460', prefix + 'row-symbol')
            before = deepcopy(row)
            source = {'provider': 'baostock', 'responseHash': PINS[relative]}
            source_before = deepcopy(source)
            value = module.normalize_dividend_row(row, symbol='sz002460', source=source)
            eq(row, before, prefix + 'input-row-unchanged')
            eq(source, source_before, prefix + 'input-source-unchanged')
            eq(value['rawRow'], before, prefix + 'raw-row-preserved')
            eq(value['source'], source_before, prefix + 'source-preserved')
            eq(value['rawRowSha256'], digest(before), prefix + 'raw-row-canonical-sha')
            eq(value['sourceSha256'], digest(source_before), prefix + 'source-canonical-sha')
            eq(value['contentSha256'], digest({k: v for k, v in value.items() if k != 'contentSha256'}), prefix + 'normalized-content-sha')
            eq(value['symbol'], 'sz002460', prefix + 'canonical-symbol')
            eq(value['providerCode'], 'sz.002460', prefix + 'provider-symbol')
            for key, original in DATE_MAP.items():
                eq(value[key], row[original] or None, prefix + 'reported-date:' + key)
                eq(value['fieldStatus'][key], 'known' if row[original] else 'unknown', prefix + 'date-status:' + key)
            expected_numbers = {'cashGrossPerShare': .3 if year == 2022 else 1., 'stockDividendPerShare': 0., 'capitalizationPerShare': .4 if year == 2022 else None}
            for key, expected in expected_numbers.items():
                eq(value[key], expected, prefix + 'independent-numeric:' + key)
                eq(value['fieldStatus'][key], 'known' if expected is not None else 'unknown', prefix + 'number-status:' + key)
            eq(value['cashAfterTaxText'], '0.27或0.3' if year == 2022 else '0.9或1', prefix + 'ambiguous-tax-preserved')
            eq(value['distributionText'], row['dividCashStock'], prefix + 'description-preserved')
            for key in ('cashNetPerShare', 'cashAvailableAt', 'knownByDate'):
                eq(value[key], None, prefix + 'unresolved:' + key)
            for key in ('responseHashVerified', 'issuerTermsVerified', 'pointInTimeTermsVerified', 'historicalCompletenessCertified', 'openingCashAvailabilityVerified', 'economicLedgerReady', 'executionInstructionGenerated', 'certified', 'targetAchieved'):
                eq(value[key], False, prefix + 'parser-does-not-certify:' + key)
            eq(value['status'], 'incomplete', prefix + 'parsing-status')
            eq(value['invalidFields'], {}, prefix + 'no-invalid-fields')
            eq(value['chronologyIssues'], [], prefix + 'no-chronology-conflict')
            eq(value['missingSourceFields'], [], prefix + 'all-source-fields-present')
            eq(value['unexpectedSourceFields'], [], prefix + 'no-extra-source-fields')
            eq(value['unknownFields'], ['preNoticeDate'] if year == 2022 else ['preNoticeDate', 'shareListingDate', 'capitalizationPerShare'], prefix + 'unknown-fields-exact')
            eq(value['shareListingDateMeaning'], 'providerReportedRedShareListingDate;notIndependentCapitalizationUnlockProof', prefix + 'listing-evidence-level')
            if year == 2022:
                for normalized_key, fact_key in (('cashGrossPerShare', 'cashGrossPerShare'), ('capitalizationPerShare', 'capitalizationSharesPerShare'), ('recordDate', 'recordDate'), ('exDate', 'exDate'), ('payDate', 'cashPayDate')):
                    eq(value[normalized_key], fact[fact_key], prefix + 'issuer-match:' + normalized_key)
                reconciliations.append({'year': year, 'issuerFact': FACT_BASE + 'fact.json', 'issuerFactSha256': PINS[FACT_BASE + 'fact.json'], 'matchedFields': ['cashGrossPerShare', 'capitalizationPerShare', 'recordDate', 'exDate', 'payDate'], 'providerStockDividendPerShare': value['stockDividendPerShare'], 'providerShareListingDate': value['shareListingDate'], 'issuerShareListingDate': fact['shareListingDate'], 'independentListingConfirmed': False, 'capitalizationImplementationDateIsNotListingProof': True})
            else:
                for key, expected in {'cashGrossPerShare': 1., 'recordDate': '2023-07-11', 'exDate': '2023-07-12', 'payDate': '2023-07-12'}.items():
                    eq(value[key], expected, prefix + 'official-pdf-reviewed-fact:' + key)
                eq(row['dividReserveToStockPs'], '', prefix + 'raw-capitalization-still-blank')
                eq(row['dividStockMarketDate'], '', prefix + 'raw-listing-still-blank')
                reconciliations.append({'year': year, 'issuerPdf': BASE + 'sz002460-2023-implementation.pdf', 'issuerPdfSha256': PINS[BASE + 'sz002460-2023-implementation.pdf'], 'primaryPages': [2, 3], 'factSource': 'Previously read primary PDF and root visual review; this script verifies sealed bytes and registered factual assertions, not OCR.', 'issuerFacts': {'cashGrossPerShare': 1., 'recordDate': '2023-07-11', 'exDate': '2023-07-12', 'scheduledCashPayDate': '2023-07-12', 'noCapitalChangeDuringThisDistribution': True}, 'providerCapitalizationPerShare': None, 'providerShareListingDate': None, 'providerBlanksNotBackfilled': True})
            receipt = next(item for item in manifest['results'] if item['file'] == Path(relative).name)
            eq(receipt['sha256'], PINS[relative], prefix + 'probe-receipt-sha')
            eq(receipt['rowCount'], 1, prefix + 'probe-receipt-row-count')
            normalized.append({'operateYear': year, 'response': relative, 'responseSha256': PINS[relative], 'responseIdentityVerifiedByThisAudit': True, 'normalized': value})
    except Exception as error:
        failures.append({'check': 'verification-exception', 'exceptionType': type(error).__name__, 'message': str(error)})
    finally:
        for name, expected in PINS.items():
            try:
                eq(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected, 'source-unchanged:' + name)
            except OSError as error:
                failures.append({'check': 'source-unreadable-at-end:' + name, 'exceptionType': type(error).__name__})
        eq(hashlib.sha256(SELF.read_bytes()).hexdigest(), SELF_SHA, 'verifier-end-sha')
    return {'schema': 'corporate-action-normalization-v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
            'passed': not failures, 'checks': len(checks), 'failureCount': len(failures), 'failures': failures,
            'checkNames': checks, 'sourcePins': PINS, 'verifierPath': str(SELF.relative_to(ROOT)).replace('\\', '/'),
            'verifierSha256': SELF_SHA, 'normalizerSha256': PINS[MODULE], 'normalizedRows': normalized,
            'officialReconciliation': reconciliations, 'elapsedSeconds': time.monotonic() - started,
            'scope': 'Exact frozen two-response contract, lossless parser outputs and hashes, fixed economic/date facts and unchanged source bytes; issuer facts stay a separate evidence layer.',
            'limitations': ['Not a complete corporate-action history or no-events certificate.',
                            '2022 share listing remains provider-reported; issuer listing is unknown and is not backfilled.',
                            '2023 provider blank capitalization/listing stay null despite a separate cash-only issuer fact.',
                            'Tax alternatives are retained verbatim; no net-cash instruction or deferred-tax ledger is inferred.',
                            'No intraday broker credit, entitlement custody, liquidity, execution, or point-in-time publication certification.',
                            'PDF text/visual review is inherited as pinned evidence; this checker does not rerun OCR or calculate strategy outcomes.'],
            'networkRequests': 0, 'actualMarketPriceDataRead': False, 'actualProviderMetadataRead': True,
            'actualStudyRun': False, 'newStrategyTrials': 0, 'certified': False, 'targetAchieved': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Preserve existing evidence: ' + str(args.output))
    report = verify()
    path = args.output if report['passed'] else args.output.with_name(args.output.stem + '-failed-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'path': str(path), 'passed': report['passed'], 'checks': report['checks'],
                      'failureCount': report['failureCount'], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
