"""Supplement the frozen panel replay with exact dependency/count checks only."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT/'docs/signal-supplemental-leadership-panel-v1.json'
REGISTRATION_SHA = '8b0f07ecd4e9b49e95f9abdecbcbd9a33af14d51dccd06395ddebf5e7796a558'
OUTPUT = ROOT/'artifacts/strategy-search/supplemental-leadership-panel-v1'
REPORT = ROOT/'artifacts/strategy-search/supplemental-panel-closure-verification-v1.json'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def verify(output=OUTPUT, root=ROOT, registration=REGISTRATION, registration_sha=REGISTRATION_SHA):
    """Read only metadata and exact source bytes; no price/feature calculation."""
    output, root, registration = map(Path, (output, root, registration))
    checks, failures = 0, []

    def check(condition, name):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(name)

    report = {'schema':'supplemental-panel-closure-verification-v1',
        'verifiedAt':datetime.now(timezone.utc).isoformat(),
        'verifierPath':'scripts/verify-supplemental-panel-closure.py', 'verifierSha256':sha(__file__),
        'scope':'Supplemental structural checks only: exact required execution member set, archived/current source hashes and registration/completion/summary target and row counts. The separate frozen independent replay supplies numerical verification.',
        'featuresRecomputed':False, 'returnsCalculated':False, 'certified':False, 'targetAchieved':False}
    try:
        check(sha(registration) == registration_sha, 'registered-document-sha')
        doc, manifest, receipt, summary = [read(p) for p in (
            registration, output/'manifest.json', output/'completion.json', output/'summary.json')]
        source_dir = root/doc['sourceMaterialization']['directory']
        source_protocol = read(source_dir/'protocol.json')
        check(sha(source_dir/'protocol.json') == doc['sourceMaterialization']['files']['protocol.json'], 'source-protocol-sha')
        check(source_protocol['protocolHash'] == doc['sourceMaterialization']['protocolHash'], 'source-protocol-identity')
        required = (set(doc['frozenFeatureImplementation']) | set(source_protocol['implementation']) |
            set(source_protocol['metadata']) | {registration.relative_to(root).as_posix(),
                'scripts/build-signal-supplemental-leadership-panel.py',
                'scripts/verify-signal-supplemental-leadership-panel.py',
                'tests/test_signal_supplemental_leadership_panel.py',
                doc['frozenIndependentVerifier']['path']})
        check(set(manifest['implementation']) == required, 'exact-required-implementation-member-set')
        check(sha(output/'execution-sources.zip') == manifest['executionArchiveSha256'], 'whole-execution-archive-sha')
        with zipfile.ZipFile(output/'execution-sources.zip') as archive:
            check(sorted(archive.namelist()) == sorted(required), 'exact-archive-member-set-no-duplicates')
            for name in sorted(required):
                check(sha(root/name) == manifest['implementation'].get(name) == hashlib.sha256(archive.read(name)).hexdigest(), 'member-current-and-archived-sha:'+name)
        mh = sha(output/'manifest.json')
        check(manifest['registration'] == doc and manifest['registrationSha256'] == receipt['registrationSha256'] == registration_sha, 'registration-binding')
        check(mh == receipt['sampleManifestHash'] == summary['sampleManifestHash'], 'manifest-binding')
        sample = doc['sample']
        check(type(receipt['targetSessions']) is int and receipt['targetSessions'] == sample['expectedScoredSessions'] == summary['targetSessions'], 'completion-target-session-count')
        check(type(receipt['featureRows']) is int and receipt['featureRows'] == sample['expectedFeatureRows'] == summary['featureRows'] == sample['expectedFullSecurities']*sample['expectedScoredSessions'], 'completion-feature-row-count')
        check(summary['warmupSessions'] == sample['expectedWarmupSessions'], 'warmup-count')
        for name in ('manifest.json','summary.json','execution-sources.zip'):
            check(receipt['files'][name] == {'path':name, 'sha256':sha(output/name)}, 'completion-metadata-file-sha:'+name)
        report.update(sampleManifestHash=mh, completionSha256=sha(output/'completion.json'),
            registrationSha256=registration_sha, targetSessions=receipt['targetSessions'], featureRows=receipt['featureRows'],
            requiredImplementation=list(sorted(required)))
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        check(False, type(exc).__name__+': '+str(exc))
    check(sha(__file__) == report['verifierSha256'], 'verifier-unchanged')
    report.update(passed=not failures, checks=checks, failureCount=len(failures), issues=failures)
    return report


if __name__ == '__main__':
    report = verify()
    target = REPORT if report['passed'] else REPORT.with_name(REPORT.stem+'-failed-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    with target.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')
    print(json.dumps({'passed':report['passed'], 'checks':report['checks'], 'failureCount':report['failureCount'], 'report':str(target)}, ensure_ascii=False))
    raise SystemExit(0 if report['passed'] else 1)
