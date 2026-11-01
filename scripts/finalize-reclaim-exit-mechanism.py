"""Commit an audited saved-account diagnostic without new strategy trials."""
from argparse import ArgumentParser
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/strategy-search'
OUT = BASE / 'reclaim-exit-mechanism-v1'
PROGRAM = BASE / 'program.json'
BEFORE = 'd5c730ee782e319afcade4539944179c9d3157bf703a10c26582290c5e305c0e'
HELPERS = ('scripts/render-reclaim-exit-mechanism.py', 'scripts/verify-reclaim-exit-mechanism-preview.cjs',
           'scripts/finalize-reclaim-exit-mechanism.py')


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def ref(path):
    return {'path': Path(path).relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def bound(item, base=ROOT):
    path = (base / item['path']).resolve()
    require(not Path(item['path']).is_absolute() and path.is_relative_to(base) and path.is_file(), 'Invalid ref')
    require(sha(path) == item['sha256'], 'Changed ref: ' + item['path'])
    return path


def pins(mapping, base=ROOT):
    require(isinstance(mapping, dict) and mapping, 'Missing pins')
    for name, digest in mapping.items():
        bound({'path': name, 'sha256': digest}, base)


def recover(plan_sha):
    d = read(OUT / 'delivery.json')
    require(d['planSha256'] == plan_sha and d['programBeforeSha256'] == BEFORE, 'Recovery identity')
    pins(d['files'])
    m = read(bound(d['executionSourceManifest']))
    pins(m['sourcePins']); bound(m['archive'])
    plan = read(OUT / 'plan.json'); pins(plan['sourcePins']); pins(plan['inputPins'])
    after = sha(OUT / 'program-after.json')
    require(after == d['programAfterSha256'] and sha(PROGRAM) in (BEFORE, after), 'Registry changed')
    if sha(PROGRAM) == BEFORE:
        pending = BASE / 'program.reclaim-exit-mechanism-pending.json'
        if not pending.exists():
            with pending.open('xb') as stream:
                stream.write((OUT / 'program-after.json').read_bytes())
        require(sha(pending) == after, 'Pending registry differs')
        os.replace(pending, PROGRAM)
    return {'completed': True, 'candidateBatches': 37, 'productionTests': 1006, 'newAccounts': 0,
            'goalStatus': 'active', 'deliverySha256': sha(OUT / 'delivery.json'), 'programSha256': sha(PROGRAM)}


def finish(plan_sha):
    if (OUT / 'delivery.json').exists():
        return recover(plan_sha)
    require(sha(PROGRAM) == BEFORE and sha(OUT / 'plan.json') == plan_sha, 'Freeze differs')
    require(not any((OUT / p).exists() for p in ('execution-sources.zip', 'execution-sources-manifest.json', 'program-before.json', 'program-after.json')), 'Finalization already started')
    plan, tests, report, proof, render, review = [read(OUT / n) for n in
        ('plan.json', 'pre-extraction-tests.json', 'readout.json', 'validation.json', 'render-receipt.json', 'visual-review.json')]
    pins(plan['sourcePins']); pins(plan['inputPins']); pins(report['inputPins'])
    require(tests['passed'] and len(tests['commands']) == 2 and all(r['exitCode'] == 0 for r in tests['commands']), 'Pretests failed')
    require('Ran 12 tests' in bound(tests['commands'][0]['log']).read_text('utf-8'), 'Production test count differs')
    for command in tests['commands']:
        bound(command['log'])
    require(tests['planSha256'] == report['planSha256'] == proof['planSha256'] == plan_sha, 'Plan audit binding')
    require(plan['registeredAt'] <= tests['startedAt'] <= tests['completedAt'] <= read(OUT / 'extraction-start.json')['startedAt'] <= report['generatedAt'], 'Chronology differs')
    require(proof['passed'] and proof['readoutSha256'] == sha(OUT / 'readout.json')
            and proof['pairsSha256'] == sha(bound(report['pairs']))
            and proof['verifierSha256'] == sha(ROOT / 'scripts/verify-reclaim-exit-mechanism.py'), 'Independent audit mismatch')
    require(report['pairCount'] == 8155 and len(report['accountBridges']) == 12 and len(report['accountDifferenceRows']) == 6, 'Incomplete account matrix')
    for key, count in plan['expectedRows'].items():
        if key != 'pairRows':
            require(len(report[key]) == count, 'Incomplete ' + key)
    require(all(r['passed'] for r in report['accountBridges'] + report['accountDifferenceRows']), 'Unreconciled account')
    require(report['trialAccounting'] == plan['trialAccounting'] and report['trialAccounting']['newAccounts'] == report['trialAccounting']['newStrategyTrials'] == 0, 'Trial count differs')
    require(report['selectedRule'] is report['profitHurdles'] is None and not any(report[k] for k in ('certified', 'targetAchieved', 'holdoutClaim')), 'Unjustified promotion')
    pins(render['inputPins'], OUT)
    require(all(render['inputPins'].get(n) == sha(OUT / n) for n in ('plan.json', 'readout.json', 'validation.json')), 'Presentation inputs')
    require(render['builderSha256'] == sha(ROOT / HELPERS[0]) and render['deliveryOrder'] == ['chart', 'sources'], 'Presentation code/order')
    require(render['chartRows'] == 6 and render['tableRows'] == [[6, 30, 54, 60, 45, 18, 18, 12, 12]], 'Presentation dimensions')
    for item in [*render['fragments'].values(), *render['payloads'].values()]:
        bound(item, OUT)
    require(review['passed'], 'Root actual visual review missing')
    qa_path = bound({'path': review['qaPath'], 'sha256': review['qaSha256']})
    qa = read(qa_path)
    require(qa['passed'] and qa['renderReceiptSha256'] == sha(OUT / 'render-receipt.json')
            and qa['scriptSha256'] == sha(ROOT / HELPERS[1]), 'QA stale/failed')
    require(qa['deliveryOrder'] == render['deliveryOrder'] and qa['tableRows'] == render['tableRows'], 'QA incomplete')
    require(set(qa['fragments']) == set(render['fragments']) == {'chart', 'sources'}, 'Missing fragment')
    for key in qa['fragments']:
        require(bound(qa['fragments'][key], OUT) == bound(render['fragments'][key], OUT), 'QA fragment differs')
    require(5 <= len(qa['images']) <= 6 and {bound(r, OUT): r['sha256'] for r in qa['images']} ==
            {bound(r): r['sha256'] for r in review['images']}, 'Images not all reviewed')
    program = read(PROGRAM); progress = program['progress']
    require(program['goalStatus'] == 'active' and progress['completedFixedCandidateTrials'] == 37
            and progress['currentScopeTestsPassed'] == 994 and 'reclaimExitMechanism' not in progress, 'Wrong registry')
    source_names = set(plan['sourcePins']) | set(HELPERS) | {'docs/reclaim-exit-mechanism-v1.json'}
    source_names.update(p.relative_to(ROOT).as_posix() for p in OUT.glob('*.py'))
    source_pins = {n: sha(ROOT / n) for n in sorted(source_names)}
    with zipfile.ZipFile(OUT / 'execution-sources.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in source_pins:
            archive.write(ROOT / name, name)
    with zipfile.ZipFile(OUT / 'execution-sources.zip') as archive:
        require(archive.testzip() is None and set(archive.namelist()) == set(source_pins), 'Archive inventory')
        require(all(hashlib.sha256(archive.read(n)).hexdigest() == h for n, h in source_pins.items()), 'Archive bytes')
    stamp = datetime.now(timezone.utc).isoformat()
    write(OUT / 'execution-sources-manifest.json', {'createdAt': stamp, 'sourcePins': source_pins, 'archive': ref(OUT / 'execution-sources.zip')})
    progress['reclaimExitMechanism'] = {'status': 'completed-development-account-decomposition-independently-verified-not-promoted',
        'directory': OUT.name, 'planSha256': plan_sha, 'readout': ref(OUT / 'readout.json'), 'validation': ref(OUT / 'validation.json'),
        'pairs': report['pairs'], 'trialAccounting': plan['trialAccounting'], 'accountDifferenceRows': report['accountDifferenceRows'],
        'pairCount': 8155, 'newProductionUnitTests': 12, 'researchDecision': ref(OUT / 'research-decision.json'),
        'selectedRule': None, 'holdoutClaim': False, 'certified': False, 'targetAchieved': False}
    progress['currentScopeTestsPassed'] = 1006
    progress['currentScopeTestEvidence'] += ' Exit-mechanism decomposition added12 production algebra/execution/maturity boundary tests; independent audit/presentation checks are not strategy trials.'
    progress['lastTurnClassification'] = 'progress: independently decomposed all8155 paired formations across12 unchanged accounts into return-profile, committed-capital, one-sided-admission and residual contributions; no new strategy/account or holdout claim.'
    progress['nextStage'] = '先核对既有实验，再细化并冻结确认突破后切换波动追踪的单个候选；继承已有保护下限且不得放松。保留全部六来源、两原退出对照、未入场与未闭仓。候选尚未注册或测试，代数贡献不是因果；不按盈利组或9.24日期自动筛选。'
    program.update(updatedAt=stamp, updatedDate=stamp[:10], goalStatus='active')
    with (OUT / 'program-before.json').open('xb') as stream:
        stream.write(PROGRAM.read_bytes())
    write(OUT / 'program-after.json', program)
    pins(source_pins)
    require(sha(PROGRAM) == BEFORE, 'Registry changed before commit')
    files = {p.relative_to(ROOT).as_posix(): sha(p) for p in OUT.rglob('*') if p.is_file()}
    write(OUT / 'delivery.json', {'completedAt': stamp, 'planSha256': plan_sha, 'files': files,
        'programBeforeSha256': BEFORE, 'programAfterSha256': sha(OUT / 'program-after.json'),
        'candidateBatchesBefore': 37, 'candidateBatchesAfter': 37, 'currentScopeTestsPassed': 1006,
        'newAccounts': 0, 'newStrategyTrials': 0, 'existingAccountsRead': 12,
        'readout': ref(OUT / 'readout.json'), 'validation': ref(OUT / 'validation.json'), 'browserQa': ref(qa_path),
        'visualReview': ref(OUT / 'visual-review.json'), 'executionSourceManifest': ref(OUT / 'execution-sources-manifest.json'),
        'data2026Included': False, 'userWorkspaceWrites': 0, 'holdoutClaim': False, 'certified': False,
        'targetAchieved': False, 'goalStatus': 'active'})
    return recover(plan_sha)


if __name__ == '__main__':
    parser = ArgumentParser(); parser.add_argument('--plan-sha256', required=True)
    print(json.dumps(finish(parser.parse_args().plan_sha256), ensure_ascii=False))
