#!/usr/bin/env python3
"""Freeze and audit the registered supplemental interval; never compute returns."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.signal_interval_quality import scan_daily

REGISTRATION = ROOT / 'docs/signal-supplemental-interval-quality-v1.json'
REGISTRATION_SHA = '7a60c771afb2338596564f43b9f28e2c119ce0f20206b33632af2459d7d9b2e8'
STATES = ('validPositiveActivity','validZeroVolumeOrAmount','invalidRecord','duplicateDate','missingRecord')
GROUPS = ('fixed','otherPinned','benchmark')
OUTPUTS = ('symbols.jsonl.gz','symbol-summary.json','daily-coverage.json','summary.json')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    with Path(path).open('xb') as stream:
        stream.write(encoded(value) + b'\n')


def now():
    return datetime.now(timezone.utc).isoformat()


def verify_code(protocol):
    for relative, expected in protocol['implementation'].items():
        if file_sha(ROOT/relative) != expected:
            raise ValueError('Frozen implementation changed: ' + relative)


def symbol_of(path):
    path = Path(path)
    market = path.parent.parent.name.lower()
    stem = path.stem.lower()
    symbol = market + stem if len(stem) == 6 and stem.isdigit() else stem
    if market not in ('sh','sz') or len(symbol) != 8 or symbol[:2] != market or not symbol[2:].isdigit():
        raise ValueError('Unrecognized source identity')
    return symbol


def load_registration():
    if file_sha(REGISTRATION) != REGISTRATION_SHA:
        raise ValueError('Registration changed')
    doc = read(REGISTRATION)
    if (doc.get('schema') != 'supplemental-interval-quality-registration-v1' or doc.get('states') != list(STATES) or
            any(doc.get(k) is not False for k in ('returnsCalculated','certified','targetAchieved'))):
        raise ValueError('Unsupported quality registration')
    for key in ('calendar','sourceManifest'):
        if file_sha(ROOT/doc[key]['path']) != doc[key]['sha256']:
            raise ValueError('Registration input changed: '+key)
    if file_sha(ROOT/doc['frozenCandidate']['path']) != doc['frozenCandidate']['sha256']:
        raise ValueError('Candidate changed')
    if file_sha(ROOT/'docs/strategy-backtesting.md') != doc['exposure']['documentSha256']:
        raise ValueError('Exposure documentation changed')
    calendar = read(ROOT/doc['calendar']['path'])
    manifest = read(ROOT/doc['sourceManifest']['path'])
    fixed = doc['fixedPopulation']['symbols']
    if (fixed != sorted(set(fixed)) or len(fixed) != doc['fixedPopulation']['count'] or fixed != manifest['inputs']['symbols'] or
            sha(encoded(fixed)) != doc['fixedPopulation']['sha256']):
        raise ValueError('Fixed population changed')
    if calendar['start'] != doc['auditStart'] or calendar['end'] != doc['auditEnd']:
        raise ValueError('Calendar interval changed')
    scan_daily(b'', calendar['dates'], start=doc['auditStart'], end=doc['auditEnd'])
    return doc, calendar, manifest


def freeze(output):
    doc, calendar, manifest = load_registration()
    output.mkdir(parents=True, exist_ok=False)
    save(output/'freeze-attempt.json', {'startedAt':now(),'registrationSha256':REGISTRATION_SHA})
    try:
        code_paths = [Path(__file__), ROOT/'service/signal_interval_quality.py',
            ROOT/'tests/test_signal_interval_quality.py', ROOT/'scripts/verify-signal-interval-quality.py',
            ROOT/'tests/test_signal_interval_quality_runner.py']
        implementation = {p.relative_to(ROOT).as_posix():file_sha(p) for p in code_paths}
        metadata_paths = [REGISTRATION, ROOT/doc['calendar']['path'], ROOT/doc['sourceManifest']['path'],
            ROOT/doc['frozenCandidate']['path'],ROOT/'docs/strategy-backtesting.md']
        metadata = {p.relative_to(ROOT).as_posix():file_sha(p) for p in metadata_paths}
        with zipfile.ZipFile(output/'execution-sources.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
            for relative, expected in {**implementation,**metadata}.items():
                raw = (ROOT/relative).read_bytes()
                if sha(raw) != expected:
                    raise ValueError('Execution/metadata drift')
                archive.writestr(relative,raw)
        fixed, seen, sources = set(doc['fixedPopulation']['symbols']), set(), []
        benchmark = manifest['inputs']['benchmark']['symbol']
        with zipfile.ZipFile(output/'input-sources.zip','x',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
            for source_path, expected in sorted(manifest['physicalSourceHashes'].items()):
                path = Path(source_path)
                if path.suffix.lower() != '.day':
                    continue
                symbol = symbol_of(path)
                if symbol in seen:
                    raise ValueError('Ambiguous physical files for symbol: '+symbol)
                seen.add(symbol)
                row = {'symbol':symbol,'sourcePath':str(path),'expectedSha256':expected,
                    'group':'benchmark' if symbol == benchmark else 'fixed' if symbol in fixed else 'otherPinned'}
                try:
                    raw = path.read_bytes()
                except FileNotFoundError as error:
                    row.update(sourceReadStatus='unavailable',errorType=type(error).__name__,member=None)
                else:
                    if sha(raw) != expected:
                        raise ValueError('Physical source changed: '+symbol)
                    member = 'raw/'+symbol[:2]+'/'+path.name
                    archive.writestr(member,raw)
                    row.update(sourceReadStatus='available',member=member,byteLength=len(raw),sha256=expected)
                sources.append(row)
                if len(sources) % 500 == 0:
                    print(json.dumps({'stage':'freezing','sourceFiles':len(sources)}),flush=True)
        if not fixed <= seen or benchmark not in seen:
            raise ValueError('Original physical binding omits fixed symbols or benchmark')
        for source in sources:
            path = Path(source['sourcePath'])
            if source['sourceReadStatus'] == 'available' and file_sha(path) != source['sha256']:
                raise ValueError('Source changed during freezing: '+source['symbol'])
            if source['sourceReadStatus'] == 'unavailable' and path.is_file():
                raise ValueError('Unavailable source appeared during freezing')
        load_registration()
        protocol = {'schema':'supplemental-interval-quality-protocol-v1','frozenAt':now(),
            'registrationSha256':REGISTRATION_SHA,'registration':doc,'calendar':calendar,'sources':sources,
            'implementation':implementation,'metadata':metadata,
            'inputArchiveSha256':file_sha(output/'input-sources.zip'),
            'executionArchiveSha256':file_sha(output/'execution-sources.zip'),
            'sourceGroupCounts':dict(Counter(x['group'] for x in sources)),
            'archiveContainsPostCutoffBytes':True,'numericAuditBoundedByRegistration':True,
            'returnsCalculated':False,'certified':False,'targetAchieved':False}
        verify_code(protocol)
        protocol['protocolHash'] = sha(encoded(protocol))
        save(output/'protocol.json',protocol)
        return {'frozen':True,'protocolHash':protocol['protocolHash'],'sourceGroupCounts':protocol['sourceGroupCounts']}
    except Exception as error:
        save(output/'freeze-failure.json',{'failedAt':now(),'errorType':type(error).__name__,'message':str(error)})
        raise


def verified_protocol(output):
    p = read(output/'protocol.json')
    if sha(encoded({k:v for k,v in p.items() if k != 'protocolHash'})) != p['protocolHash']:
        raise ValueError('Protocol changed')
    if p['registrationSha256'] != REGISTRATION_SHA:
        raise ValueError('Different registration')
    doc, calendar, manifest = load_registration()
    if p['registration'] != doc or p['calendar'] != calendar:
        raise ValueError('Embedded registration/calendar differs from frozen input')
    verify_code(p)
    expected_code = {Path(__file__).relative_to(ROOT).as_posix(),'service/signal_interval_quality.py',
        'tests/test_signal_interval_quality.py','scripts/verify-signal-interval-quality.py',
        'tests/test_signal_interval_quality_runner.py'}
    if set(p['implementation']) != expected_code:
        raise ValueError('Implementation closure changed')
    for file,key in [('input-sources.zip','inputArchiveSha256'),('execution-sources.zip','executionArchiveSha256')]:
        if file_sha(output/file) != p[key]:
            raise ValueError('Archive changed: '+file)
    expected_metadata = {
        REGISTRATION.relative_to(ROOT).as_posix():REGISTRATION_SHA,
        doc['calendar']['path']:doc['calendar']['sha256'],
        doc['sourceManifest']['path']:doc['sourceManifest']['sha256'],
        doc['frozenCandidate']['path']:doc['frozenCandidate']['sha256'],
        'docs/strategy-backtesting.md':doc['exposure']['documentSha256'],
    }
    if p['metadata'] != expected_metadata:
        raise ValueError('Metadata closure changed')
    with zipfile.ZipFile(output/'execution-sources.zip') as archive:
        expected = {**p['implementation'],**expected_metadata}
        if len(archive.namelist()) != len(set(archive.namelist())) or set(archive.namelist()) != set(expected):
            raise ValueError('Execution archive member set changed')
        for name, digest in expected.items():
            if sha(archive.read(name)) != digest:
                raise ValueError('Execution archive member changed')
    expected_sources = {path:digest for path,digest in manifest['physicalSourceHashes'].items() if Path(path).suffix.lower() == '.day'}
    if len(p['sources']) != len(expected_sources) or {s['sourcePath']:s['expectedSha256'] for s in p['sources']} != expected_sources:
        raise ValueError('Source inventory changed')
    fixed = set(doc['fixedPopulation']['symbols'])
    benchmark = manifest['inputs']['benchmark']['symbol']
    seen = set()
    for source in p['sources']:
        symbol = symbol_of(source['sourcePath'])
        group = 'benchmark' if symbol == benchmark else 'fixed' if symbol in fixed else 'otherPinned'
        if symbol in seen or source['symbol'] != symbol or source['group'] != group:
            raise ValueError('Source identity changed')
        seen.add(symbol)
        if source['sourceReadStatus'] == 'available':
            if source['member'] != 'raw/'+symbol[:2]+'/'+Path(source['sourcePath']).name or source['sha256'] != source['expectedSha256']:
                raise ValueError('Available source identity changed')
        elif source['sourceReadStatus'] != 'unavailable' or source['member'] is not None:
            raise ValueError('Unavailable source state changed')
    if p['sourceGroupCounts'] != dict(Counter(s['group'] for s in p['sources'])) or not fixed <= seen or benchmark not in seen:
        raise ValueError('Source group accounting changed')
    return p


def run(output):
    p = verified_protocol(output)
    if (output/'completion.json').exists():
        receipt = read(output/'completion.json')
        if (receipt.get('schema') != 'supplemental-interval-quality-completion-v1' or
                receipt['protocolHash'] != p['protocolHash'] or set(receipt['files']) != set(OUTPUTS) or
                any(receipt.get(k) is not False for k in ('returnsCalculated','certified','targetAchieved'))):
            raise ValueError('Different completed protocol')
        for name, expected in receipt['files'].items():
            if file_sha(output/name) != expected:
                raise ValueError('Completed output changed')
        return {'alreadyCompleted':True,'protocolHash':p['protocolHash']}
    save(output/'audit-attempt.json',{'startedAt':now(),'protocolHash':p['protocolHash']})
    try:
        doc, dates = p['registration'], p['calendar']['dates']
        daily = {g:{d:Counter({s:0 for s in STATES}) for d in dates} for g in GROUPS}
        by_year = {g:{y:Counter({s:0 for s in STATES}) for y in sorted({d[:4] for d in dates})} for g in GROUPS}
        symbols, unavailable = [], []
        with zipfile.ZipFile(output/'input-sources.zip') as archive, gzip.open(output/'symbols.jsonl.gz','xt',encoding='utf-8') as stream:
            members = [s['member'] for s in p['sources'] if s['member'] is not None]
            if len(archive.namelist()) != len(set(archive.namelist())) or set(archive.namelist()) != set(members):
                raise ValueError('Source archive member set mismatch')
            for index, source in enumerate(p['sources']):
                raw = archive.read(source['member']) if source['member'] is not None else b''
                if source['sourceReadStatus'] == 'available' and sha(raw) != source['sha256']:
                    raise ValueError('Archived input changed')
                audit = scan_daily(raw,dates,start=doc['auditStart'],end=doc['auditEnd'])
                if [r['date'] for r in audit['records']] != dates:
                    raise ValueError('Incomplete daily quality journal')
                for row in audit['records']:
                    if row['state'] not in STATES:
                        raise ValueError('Unknown quality state')
                    daily[source['group']][row['date']][row['state']] += 1
                    by_year[source['group']][row['date'][:4]][row['state']] += 1
                item = {'symbol':source['symbol'],'group':source['group'],'sourceReadStatus':source['sourceReadStatus'],
                    'sourceSha256':source.get('sha256'),'audit':audit}
                stream.write(encoded(item).decode('utf-8')+'\n')
                compact = {k:v for k,v in audit.items() if k != 'records'}
                symbols.append({'symbol':source['symbol'],'group':source['group'],
                    'sourceReadStatus':source['sourceReadStatus'],**compact})
                if source['sourceReadStatus'] != 'available':
                    unavailable.append(source['symbol'])
                if (index+1) % 500 == 0:
                    print(json.dumps({'stage':'auditing','symbols':index+1}),flush=True)
        for group in GROUPS:
            for day, counts in daily[group].items():
                if sum(counts.values()) != p['sourceGroupCounts'].get(group,0):
                    raise ValueError('Daily population mismatch')
        save(output/'daily-coverage.json',daily)
        save(output/'symbol-summary.json',symbols)
        benchmark_audit = next(s for s in symbols if s['group'] == 'benchmark')
        benchmark_complete = p['sourceGroupCounts'].get('benchmark') == 1 and all(
            daily['benchmark'][d]['validPositiveActivity']+daily['benchmark'][d]['validZeroVolumeOrAmount'] == 1 for d in dates)
        summary = {'schema':'supplemental-interval-quality-summary-v1','protocolHash':p['protocolHash'],
            'auditStart':doc['auditStart'],'auditEnd':doc['auditEnd'],
            'sessionsByYear':dict(Counter(d[:4] for d in dates)),'sourceGroupCounts':p['sourceGroupCounts'],
            'byGroupYear':by_year,'unavailableSources':unavailable,
            'benchmarkCalendarCompleteValidContent':benchmark_complete,
            'benchmarkCalendarExactlyObserved':benchmark_complete and not benchmark_audit['offCalendarDates'] and
                benchmark_audit['invalidDateRecords'] == 0 and benchmark_audit['trailingBytes'] == 0,
            'fixedPopulationUnchanged':True,'missingnessChangesEligibility':False,
            'exposure':doc['exposure'],'certificationGaps':doc['certificationGaps'],
            'returnsCalculated':False,'strategyRanksCalculated':False,'candidateGatesCalculated':False,
            'certified':False,'targetAchieved':False,
            'scope':'Frozen physical files only. Content validity and presence are not PIT membership, tradability or economic-return evidence.'}
        save(output/'summary.json',summary)
        verified_protocol(output)
        receipt = {'schema':'supplemental-interval-quality-completion-v1','completedAt':now(),
            'protocolHash':p['protocolHash'],'files':{n:file_sha(output/n) for n in OUTPUTS},
            'returnsCalculated':False,'certified':False,'targetAchieved':False}
        save(output/'completion.json',receipt)
        return summary
    except Exception as error:
        save(output/'audit-failure.json',{'failedAt':now(),'errorType':type(error).__name__,'message':str(error)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('freeze','run'))
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    result = (freeze if args.command == 'freeze' else run)(args.output.resolve())
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))


if __name__ == '__main__':
    main()
