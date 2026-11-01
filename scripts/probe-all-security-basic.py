"""One bounded public basic-table probe; no historical eligibility certification.

Reuses the frozen reviewed transport, not the user's workspace or database.
Do not run concurrently with other BaoStock SDK collectors.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'artifacts/strategy-search/all-security-basic-v1'
TRANSPORT = ROOT / 'scripts/supplement-missing-historical-evidence.py'
TRANSPORT_HASH = 'bf7fa19fc233c9ced1560d60634239ab6592bf0cec661faae6186efe22412f4d'
REQUEST = {'id': 'all-security-basic', 'api': 'query_stock_basic', 'parameters': {'code': '', 'code_name': ''}}
INPUTS = [ROOT / 'artifacts/strategy-search/historical-universe-coverage-v1.json',
          ROOT / 'artifacts/strategy-search/signal-baseline-v1/snapshot.json']

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def write(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)

def registration():
    protocol = read(OUTPUT / 'protocol.json')
    request = read(OUTPUT / 'request-manifest.json')
    if sha(TRANSPORT) != TRANSPORT_HASH or protocol['sourceSha256'] != sha(Path(__file__)):
        raise ValueError('Frozen implementation changed')
    if request != {'requests': [REQUEST], 'selected': []}:
        raise ValueError('The exact single basic-table request changed')
    if protocol['requestManifestSha256'] != sha(OUTPUT / 'request-manifest.json'):
        raise ValueError('Frozen request manifest hash changed')
    for path in INPUTS:
        if protocol['inputs'][path.relative_to(ROOT).as_posix()] != sha(path):
            raise ValueError('Frozen comparison input changed')
    return request

def freeze():
    if sha(TRANSPORT) != TRANSPORT_HASH:
        raise ValueError('Reviewed transport source changed')
    OUTPUT.mkdir(parents=True, exist_ok=False)
    write(OUTPUT / 'request-manifest.json', {'requests': [REQUEST], 'selected': []})
    write(OUTPUT / 'protocol.json', {
        'schema': 'bounded-all-security-basic-v1', 'registeredAt': datetime.now(timezone.utc).isoformat(),
        'sourceSha256': sha(Path(__file__)), 'transportSha256': TRANSPORT_HASH,
        'requestManifestSha256': sha(OUTPUT / 'request-manifest.json'), 'requests': 1,
        'inputs': {p.relative_to(ROOT).as_posix(): sha(p) for p in INPUTS},
        'purpose': 'Reconcile retrospective listing metadata and four-date universe coverage; no returns',
        'limits': {'wireSendsIncludingLoginLogout': 20, 'rows': 20000, 'requestSeconds': 10,
                   'wallSeconds': 300, 'singleConnection': True, 'automaticRetries': 0},
        'sourceSemantics': 'Installed official SDK permits empty code/name. A complete provider response is not proof of historical population exhaustiveness.',
        'oversizePagePolicy': 'If a response exceeds SDK page size without explicit terminal evidence, mark incomplete; never infer completeness.',
        'certified': False, 'returnsCalculated': False})
    print('Frozen exactly one all-basic request; no network yet.', flush=True)

def authorize_worker():
    attempt = read(OUTPUT / 'attempt.json')
    if attempt.get('parentPid') != os.getppid() or (OUTPUT / 'manifest.json').exists() or (OUTPUT / 'process.json').exists():
        raise ValueError('Worker requires its active exclusive parent before any network activity')

def response_complete(manifest, record, raw, process):
    return (process.get('returncode') == 0 and process.get('timedOut') is False and
            manifest['status'] == 'completed' and record.get('complete') is True and
            raw.get('complete') is True and record.get('errorCode') == raw.get('errorCode') == '0' and
            raw.get('rowBoundReached') is False and record.get('rowBoundReached') is False and
            manifest.get('auth', {}).get('logout', {}).get('errorCode') == '0')

def worker():
    registration()
    authorize_worker()
    spec = importlib.util.spec_from_file_location('_all_basic_frozen_transport', TRANSPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.OUTPUT, module.MAX_SENDS, module.MAX_ROWS = OUTPUT, 20, 20000
    module.verify_frozen = registration
    original_write = module.write
    def checked_write(path, value, **kwargs):
        if isinstance(value, dict) and value.get('terminalReason') == 'sdkDeclaredUnpagedOversizeReply':
            value['complete'] = False
            value['status'] = 'incomplete'
            value['oversizePageIsNotTerminalProof'] = True
        return original_write(path, value, **kwargs)
    module.write = checked_write
    module.worker()

def validate():
    registration()
    manifest = read(OUTPUT / 'manifest.json')
    if manifest['protocol']['sha256'] != sha(OUTPUT / 'protocol.json'):
        raise ValueError('Run protocol mismatch')
    if manifest.get('requestManifest', {}).get('sha256') != sha(OUTPUT / 'request-manifest.json'):
        raise ValueError('Run request mismatch')
    records = manifest['requests']
    if len(records) != 1 or any(records[0][k] != REQUEST[k] for k in REQUEST):
        raise ValueError('Unexpected or absent query')
    row = records[0]
    if sha(OUTPUT / row['path']) != row['sha256']:
        raise ValueError('Aggregate hash mismatch')
    raw = read(OUTPUT / row['path'])
    combined, numbers = [], []
    for page in row['pages']:
        if sha(OUTPUT / page['path']) != page['sha256']:
            raise ValueError('Page hash mismatch')
        p = read(OUTPUT / page['path'])
        if p['api'] != REQUEST['api'] or p['parameters'] != REQUEST['parameters'] or p['fields'] != raw['fields']:
            raise ValueError('Page request/field mismatch')
        if len(p['rows']) != page['rowCount'] or any(len(x) != len(p['fields']) for x in p['rows']):
            raise ValueError('Malformed page rows')
        combined.extend(p['rows'])
        numbers.append(p['sdkPageNumber'])
    if len(numbers) != len(set(numbers)) or combined != raw['rows'] or len(combined) != row['rowCount']:
        raise ValueError('Pagination reconciliation mismatch')
    records_by_code = {}
    for values in combined:
        item = dict(zip(raw['fields'], values))
        if item['code'] in records_by_code:
            raise ValueError('Duplicate security code; no arbitrary survivor selection')
        records_by_code[item['code']] = item
    main = [v for k,v in records_by_code.items() if re.fullmatch(r'(?:sh\.60[0135]\d{3}|sz\.00[0123]\d{3})', k) and v.get('type') == '1']
    audit = read(ROOT / 'artifacts/strategy-search/historical-universe-coverage-v1.json')
    baseline = read(ROOT / 'artifacts/strategy-search/signal-baseline-v1/snapshot.json')
    # Discover the documented frozen list shape explicitly, rather than guessing
    # the name of a nested response object when metadata changes.
    sample_codes = set(baseline['symbols'])
    known_union = sample_codes | set(audit['missingUnionSymbols'])
    overlap = [v for v in main if v.get('ipoDate') and v['ipoDate'] <= '2023-12-31' and
               (not v.get('outDate') or v['outDate'] >= '2022-01-01')]
    full = response_complete(manifest, row, raw, read(OUTPUT / 'process.json'))
    report = {'status': 'complete-response' if full else 'incomplete-response',
        'manifestSha256': sha(OUTPUT / 'manifest.json'), 'rawSha256': row['sha256'],
        'wireSends': manifest['wireSends'], 'pagesVerified': len(numbers), 'rows': len(combined),
        'mainBoardStockRows': len(main), 'retrospectiveIntervalOverlapping2022To2023': len(overlap),
        'fourDateUnionAbsentFromBasic': sorted(known_union - {k.replace('.', '') for k in records_by_code}),
        'intervalCandidatesAbsentFromFourDateUnion': [v for v in overlap if v['code'].replace('.', '') not in known_union],
        'rawCurrentStatusNeverEligibilityFilter': True, 'historicalExhaustivenessProven': False,
        'returnsCalculated': False, 'certified': False,
        'limitations': ['Retrospective interval overlap is a coverage clue, not daily membership proof.',
                        'Current names/status are not historical labels; outDate boundary semantics remain source-specific.',
                        'No original sample or strategy results were changed.']}
    if manifest['wireSends'] > 20 or len(combined) >= 20000:
        raise ValueError('Bound reached or exceeded; completeness forbidden')
    write(OUTPUT / 'validation.json', report)
    print(json.dumps({k: v for k,v in report.items() if k not in ('intervalCandidatesAbsentFromFourDateUnion', 'limitations')}, ensure_ascii=False))
    return full

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    mode.add_argument('--validate', action='store_true')
    args = parser.parse_args()
    if args.worker:
        worker()
    elif args.validate:
        return int(not validate())
    elif args.execute:
        registration()
        write(OUTPUT / 'attempt.json', {'startedAt': datetime.now(timezone.utc).isoformat(), 'singleAttempt': True, 'parentPid': os.getpid()})
        command = [sys.executable, '-X', 'utf8', '-B', str(Path(__file__).resolve()), '--worker']
        try:
            completed = subprocess.run(command, timeout=300, check=False)
            write(OUTPUT / 'process.json', {'returncode': completed.returncode, 'timedOut': False})
        except subprocess.TimeoutExpired:
            write(OUTPUT / 'process.json', {'returncode': None, 'timedOut': True})
            return 1
        return int(not validate())
    else:
        freeze()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
