"""Archive a completed diagnostic without incrementing strategy/account trials."""
from argparse import ArgumentParser
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/strategy-search'
OUT = BASE / 'reclaim-relative-feedback-context-v1'
PROGRAM = BASE / 'program.json'
BEFORE = '79abc50e3b1ac8c5c3363812f7ea3e5941d9285eb44c89387dc82f70a7997496'
ORDER = ['mainChart', 'mainSources', 'chinextChart', 'chinextSources', 'starChart', 'starSources']
ROWS = {'overallRows': 48, 'formationYearRows': 96, 'epochStateRows': 72, 'comparisonRows': 24,
        'accountReconciliation': 12, 'curveEpochRows': 12, 'contextDayCounts': 6, 'qualityRows': 12}
HELPERS = ['scripts/' + p for p in ('render-reclaim-relative-feedback-context.py',
    'verify-reclaim-relative-feedback-context-preview.cjs', 'finalize-reclaim-relative-feedback-context.py')]


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def ref(path):
    path = Path(path)
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def bound(value, base=ROOT):
    path = (base / value['path']).resolve()
    require(not Path(value['path']).is_absolute() and path.is_relative_to(base.resolve()) and path.is_file(), 'Missing/escaped reference')
    require(sha(path) == value['sha256'], 'Changed reference: ' + value['path'])
    return path


def pins(mapping, base=ROOT):
    require(isinstance(mapping, dict) and mapping, 'Missing pins')
    for name, expected in mapping.items():
        bound({'path': name, 'sha256': expected}, base)


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def matrix(report):
    require(all(len(report[k]) == v for k, v in ROWS.items()), 'Incomplete diagnostic matrix')
    expected = {(p, b, m, s) for p in ('2022-2023', '2024-2025') for b in ('main', 'chinext', 'star')
                for m in ('trail10', 'volatilityTrail') for s in ('stronger', 'weaker', 'equal', 'unknown')}
    require({(r['period'], r['board'], r['policy'], r['state']) for r in report['overallRows']} == expected, 'Diagnostic identities differ')
    require(all(r['passed'] for r in report['accountReconciliation']), 'Unreconciled account')
    require(report['formationCount'] == 8155 and report['recordCount'] == 16310, 'Original opportunity denominator differs')
    require(not report['certified'] and not report['targetAchieved'] and not report['holdoutClaim'], 'Unregistered success/holdout claim')
    require(report['selectedState'] is report['automaticNextStrategy'] is report['profitHurdles'] is None, 'Unregistered promotion')


def recover_committed_delivery(plan_sha):
    """Finish only an already fully written, exact verified delivery commit."""
    delivery = read(OUT / 'delivery.json')
    require(delivery['planSha256'] == plan_sha and delivery['programBeforeSha256'] == BEFORE, 'Recovery belongs to another registration')
    pins(delivery['files'])
    manifest = read(bound(delivery['executionSourceManifest']))
    pins(manifest['sourcePins'])
    bound(manifest['archive'])
    plan = read(OUT / 'plan.json')
    pins(plan['sourcePins']); pins(plan['inputPins'])
    after = sha(OUT / 'program-after.json')
    require(after == delivery['programAfterSha256'] and sha(PROGRAM) in (BEFORE, after), 'Registry changed outside this commit')
    if sha(PROGRAM) == BEFORE:
        pending = BASE / 'program.reclaim-relative-feedback-context-pending.json'
        if pending.exists():
            require(sha(pending) == after, 'Pending registry differs')
        else:
            with pending.open('xb') as stream:
                stream.write((OUT / 'program-after.json').read_bytes())
        os.replace(pending, PROGRAM)
    return {'completed': True, 'candidateBatches': 37, 'productionTests': 994, 'newAccounts': 0,
        'goalStatus': 'active', 'programSha256': sha(PROGRAM), 'deliverySha256': sha(OUT / 'delivery.json')}


def finalize(plan_sha):
    if (OUT / 'delivery.json').exists():
        return recover_committed_delivery(plan_sha)
    require(sha(PROGRAM) == BEFORE and sha(OUT / 'plan.json') == plan_sha, 'Registration/registry changed')
    pending = BASE / 'program.reclaim-relative-feedback-context-pending.json'
    require(not pending.exists() and not any((OUT / p).exists() for p in
        ('execution-sources.zip', 'execution-sources-manifest.json', 'program-before.json', 'program-after.json', 'delivery.json')), 'Finalization already started')
    plan, tests, completion, proof, report = [read(OUT / p) for p in
        ('plan.json', 'pre-extraction-tests.json', 'input-completion.json', 'validation.json', 'readout.json')]
    for value in (plan['sourcePins'], plan['inputPins'], tests['sourcePins'], report['inputPins']):
        pins(value)
    require(tests['passed'] and tests['planSha256'] == completion['planSha256'] == proof['planSha256'] == report['planSha256'] == plan_sha, 'Audit/receipt binding differs')
    require(plan['expectedRows'] == ROWS, 'Registered matrix differs')
    require(tests['unitTests'] == plan['preExtractionTestSpec']['newUnitTests'] == 16
            and len(tests['commands']) == 3 and all(r['exitCode'] == 0 for r in tests['commands']), 'Production tests failed/missing')
    require(proof['passed'] and proof['readoutSha256'] == sha(OUT / 'readout.json')
            and proof['inputCompletionSha256'] == sha(OUT / 'input-completion.json')
            and proof['recordsSha256'] == sha(bound(report['records']))
            and proof['verifierSha256'] == sha(ROOT / 'scripts/verify-reclaim-relative-feedback-context.py'), 'Failed/stale independent audit')
    require(proof['counts']['expectedRows'] == ROWS
            and proof['counts']['formationCount'] == 8155 and proof['counts']['recordCount'] == 16310, 'Incomplete independent coverage')
    require(report['trialAccounting'] == plan['trialAccounting']
            and plan['trialAccounting']['newAccounts'] == plan['trialAccounting']['newStrategyTrials'] == 0, 'Diagnostic counted as a new strategy')
    require(bound(completion['preExtractionTests']) == OUT / 'pre-extraction-tests.json', 'Test receipt differs')
    extraction = read(OUT / 'extraction-start.json')
    require(bound(extraction['preExtractionTests']) == OUT / 'pre-extraction-tests.json'
            and plan['registeredAt'] <= tests['startedAt'] <= tests['completedAt'] <= extraction['startedAt'] <= completion['completedAt'], 'Extraction chronology differs')
    for boards in completion['outputs'].values():
        for item in boards.values():
            receipt = read(bound(item))
            require(receipt['planSha256'] == plan_sha, 'Source receipt differs')
            for output in receipt['files'].values():
                bound(output)
    matrix(report)
    render = read(OUT / 'render-receipt.json')
    pins(render['inputPins'], OUT)
    if render.get('presentationHelpers'):
        pins(render['presentationHelpers'])
    require(all(render['inputPins'].get(p) == sha(OUT / p) for p in ('plan.json', 'readout.json', 'validation.json')), 'Presentation lacks exact audited inputs')
    require(render['deliveryOrder'] == ORDER and set(render['fragments']) == set(ORDER), 'Presentation order incomplete')
    require(render['chartRows'] == {b: 16 for b in ('main', 'chinext', 'star')}
            and render['tableRows'] == {b: [[16, 8, 4, 4, 2, 32, 24, 4, 16]] for b in ('main', 'chinext', 'star')}, 'Presentation matrix incomplete')
    require(render['builderSha256'] == sha(ROOT / HELPERS[0]), 'Presentation code changed')
    for item in render['fragments'].values():
        bound(item, OUT)
    for item in render['payloads'].values():
        bound(item, OUT)
    review = read(OUT / 'visual-review.json')
    require(review['passed'], 'Root actual visual review missing')
    qa_path = bound({'path': review['qaPath'], 'sha256': review['qaSha256']})
    qa = read(qa_path)
    require(qa['passed'] and qa['renderReceiptSha256'] == sha(OUT / 'render-receipt.json')
            and qa['scriptSha256'] == sha(ROOT / HELPERS[1]), 'Stale/failed browser QA')
    require(qa['deliveryOrder'] == ORDER and qa['tableRows'] == render['tableRows']
            and set(qa['fragments']) == set(ORDER) and len(qa['images']) == 15, 'Incomplete browser QA coverage')
    for key, item in qa['fragments'].items():
        require(bound(item, OUT) == bound(render['fragments'][key], OUT), 'QA fragment differs')
    require({bound(r, OUT): r['sha256'] for r in qa['images']} == {bound(r): r['sha256'] for r in review['images']}, 'Not all QA images reviewed')
    program = read(PROGRAM)
    progress = program['progress']
    require(program['goalStatus'] == 'active' and progress['completedFixedCandidateTrials'] == 37
            and progress['currentScopeTestsPassed'] == 978 and 'reclaimRelativeFeedbackContext' not in progress, 'Registry state differs')
    sources = set(plan['sourcePins']) | set(HELPERS) | set(render.get('presentationHelpers', {}))
    sources.add('docs/reclaim-relative-feedback-context-v1.json')
    sources.update(p.relative_to(ROOT).as_posix() for p in OUT.glob('*.py'))
    source_pins = {name: sha(ROOT / name) for name in sorted(sources)}
    pins(source_pins)
    with zipfile.ZipFile(OUT / 'execution-sources.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in source_pins:
            archive.write(ROOT / name, name)
    with zipfile.ZipFile(OUT / 'execution-sources.zip') as archive:
        require(archive.testzip() is None and set(archive.namelist()) == set(source_pins), 'Source archive inventory differs')
        require(all(hashlib.sha256(archive.read(n)).hexdigest() == h for n, h in source_pins.items()), 'Source archive bytes differ')
    stamp = datetime.now(timezone.utc).isoformat()
    write(OUT / 'execution-sources-manifest.json', {'createdAt': stamp, 'planSha256': plan_sha,
        'sourcePins': source_pins, 'archive': ref(OUT / 'execution-sources.zip')})
    progress['reclaimRelativeFeedbackContext'] = {'status': 'completed-development-context-diagnostic-independently-verified-not-promoted',
        'directory': OUT.name, 'planSha256': plan_sha, 'readout': ref(OUT / 'readout.json'),
        'validation': ref(OUT / 'validation.json'), 'records': report['records'],
        'trialAccounting': report['trialAccounting'], 'formationCount': 8155, 'recordCount': 16310,
        'accountReconciliation': report['accountReconciliation'], 'contextDayCounts': report['contextDayCounts'],
        'comparisons': report['comparisonRows'], 'limitations': report['limitations'],
        'selectedState': None, 'holdoutClaim': False, 'certified': False, 'targetAchieved': False,
        'newProductionUnitTests': 16}
    progress['currentScopeTestsPassed'] = 994
    progress['lastTurnClassification'] = ('progress: independently reconstructed six causal top-versus-bottom feedback contexts and '
        'attributed all8155 original opportunities across12 unchanged accounts; no new account, strategy trial or holdout claim.')
    progress['nextStage'] = ('结合完整强弱反馈归因决定下一个可事前执行的研究假设；保留所有时期、板块和未知分组。'
        '分组贡献不能当筛选账户收益；不按单个盈利组自动选择规则，不设收益/胜率/持有硬门槛。目标继续active。')
    progress['currentScopeTestEvidence'] += ' Relative-feedback context added16 production boundary tests; diagnostic/audit/presentation synthetic checks are not extra strategy trials or unit-test increments.'
    program.update(updatedAt=stamp, updatedDate=stamp[:10], goalStatus='active')
    with (OUT / 'program-before.json').open('xb') as stream:
        stream.write(PROGRAM.read_bytes())
    write(OUT / 'program-after.json', program)
    with pending.open('xb') as stream:
        stream.write((OUT / 'program-after.json').read_bytes())
    pins(source_pins)
    require(sha(PROGRAM) == BEFORE, 'Registry changed before replacement')
    files = {p.relative_to(ROOT).as_posix(): sha(p) for p in OUT.rglob('*') if p.is_file()}
    write(OUT / 'delivery.json', {'completedAt': stamp, 'files': files, 'programBeforeSha256': BEFORE,
        'programAfterSha256': sha(OUT / 'program-after.json'), 'planSha256': plan_sha, 'candidateBatchesBefore': 37, 'candidateBatchesAfter': 37,
        'newAccounts': 0, 'newStrategyTrials': 0, 'existingAccountsRead': 12, 'newContextDefinitions': 1,
        'distinctNewUnitTests': 16, 'currentScopeTestsPassed': 994, 'readout': ref(OUT / 'readout.json'),
        'validation': ref(OUT / 'validation.json'), 'preExtractionTests': ref(OUT / 'pre-extraction-tests.json'),
        'renderReceipt': ref(OUT / 'render-receipt.json'), 'browserQa': ref(qa_path), 'visualReview': ref(OUT / 'visual-review.json'),
        'executionSources': ref(OUT / 'execution-sources.zip'), 'executionSourceManifest': ref(OUT / 'execution-sources-manifest.json'),
        'actualDataPeriods': ['2022-2023', '2024-2025'], 'data2026Included': False,
        'userWorkspaceWrites': 0, 'onlyRepositoryResearchFilesWritten': True,
        'holdoutClaim': False, 'certified': False, 'targetAchieved': False, 'goalStatus': 'active'})
    return recover_committed_delivery(plan_sha)


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--plan-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(finalize(args.plan_sha256), ensure_ascii=False))
