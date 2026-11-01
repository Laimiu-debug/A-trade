"""Seal the staged-exit evidence and atomically advance the research registry."""
from argparse import ArgumentParser
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/strategy-search'
OUT = BASE / 'reclaim-breakout-volatility-trail-v1'
PROGRAM = BASE / 'program.json'
BEFORE = 'e60f3e1a244afdf60de0c526514ba738814f028f080e110a6a9a4e4e68c9a218'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
MODES = ('trail10', 'volatilityTrail', 'breakoutVolatilityTrail')
CONTRASTS = {
    'breakoutVolatilityTrailVsTrail10': ('trail10', 'breakoutVolatilityTrail'),
    'breakoutVolatilityTrailVsVolatilityTrail': ('volatilityTrail', 'breakoutVolatilityTrail'),
    'volatilityTrailVsTrail10': ('trail10', 'volatilityTrail'),
}
MATRIX = {'accounts': 18, 'accountRows': 18, 'comparisons': 18, 'yearRows': 36,
          'halfYearRows': 72, 'phaseRows': 144, 'curveEpochRows': 18,
          'coverageRows': 18, 'formationCoverageRows': 6, 'partitionReconciliation': 18, 'mechanismRows': 6}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def write_once(path, value):
    content = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    if path.exists():
        require(path.read_bytes() == content, 'Existing evidence differs: ' + str(path))
    else:
        with path.open('xb') as stream:
            stream.write(content)


def ref(path):
    path = Path(path).resolve()
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path), 'bytes': path.stat().st_size}


def bound(item, base=ROOT):
    require(not Path(item['path']).is_absolute(), 'Absolute evidence reference')
    path = (base / item['path']).resolve()
    require(path.is_relative_to(base.resolve()) and path.is_file(), 'Missing/escaped evidence')
    require(sha(path) == item['sha256'], 'Changed evidence: ' + item['path'])
    if 'bytes' in item:
        require(path.stat().st_size == item['bytes'], 'Evidence size differs')
    return path


def pins(mapping, base=ROOT):
    for path, expected in mapping.items():
        bound({'path': path, 'sha256': expected}, base)


def verify_matrix(result):
    require(all(len(result[k]) == n for k, n in MATRIX.items()), 'Incomplete result matrix')
    identities = {(p, b, m) for p in PERIODS for b in BOARDS for m in MODES}
    for key in ('accounts', 'accountRows', 'coverageRows'):
        require({(r['period'], r['board'], r['policy']) for r in result[key]} == identities, 'Account identity mismatch')
    require({(r['period'], r['board'], r['id']) for r in result['comparisons']}
            == {(p, b, c) for p in PERIODS for b in BOARDS for c in CONTRASTS}, 'Contrast identity mismatch')
    for row in result['comparisons']:
        require((row['control'], row['candidate']) == CONTRASTS[row['id']], 'Contrast direction mismatch')
    require(result['primaryComparisons'] == [r for r in result['comparisons'] if r['candidate'] == MODES[-1]],
            'Primary comparisons differ')
    require(all(r['passed'] for r in result['partitionReconciliation']), 'Partition reconciliation failed')
    require(not result['certified'] and not result['targetAchieved'], 'Unregistered certification')
    require(all(result['decision'][k] is None for k in
        ('selectedBoard', 'selectedPolicy', 'automaticNextStrategy', 'profitHurdle')), 'Automatic selection forbidden')


def finish(plan_sha, tests_sha):
    require(sha(OUT / 'plan.json') == plan_sha and sha(OUT / 'pre-account-tests.json') == tests_sha,
            'Explicit registration/test hashes differ')
    delivery_path = OUT / 'delivery.json'
    if delivery_path.exists():
        delivery = read(delivery_path)
        pins(delivery['files'])
        require(delivery['programBeforeSha256'] == BEFORE and delivery['planSha256'] == plan_sha,
                'Existing completion differs')
        if sha(PROGRAM) == BEFORE:
            pending = BASE / 'program.breakout-volatility-pending.json'
            pending.write_bytes((OUT / 'program-after.json').read_bytes())
            os.replace(pending, PROGRAM)
        require(sha(PROGRAM) == delivery['programAfterSha256'], 'Registry differs from sealed delivery')
        return {'completed': True, 'reusedDelivery': True, 'deliverySha256': sha(delivery_path)}
    require(sha(PROGRAM) == BEFORE, 'Registry changed during experiment')
    plan, tests, comparison, result = (read(OUT / n) for n in
        ('plan.json', 'pre-account-tests.json', 'comparison.json', 'readout.json'))
    for mapping in (plan['sourcePins'], plan['inputPins'], tests['pins'], result['inputPins']):
        pins(mapping)
    require(tuple(plan[k] for k in ('experimentBatches', 'newAccounts', 'referenceAccountsReused', 'newExitDefinitions'))
            == (1, 6, 12, 1), 'Trial scope mismatch')
    require(all(plan[k] == 0 for k in ('newSetupMechanisms', 'newUniverseVariants', 'newEntryPolicies')),
            'Unexpected scope expansion')
    new_tests = plan['preAccountTestSpec']['newUnitTests']
    require(new_tests > 0 and tests['passed'] and tests['planSha256'] == plan_sha
            and tests['unitTests'] == 77 + new_tests and tests['productionUnitIncrement'] == new_tests
            and tests['allChecksBeforeFirstNewAccount'], 'Invalid executed pre-account tests')
    require(len(tests['commands']) == 6 and all(r['exitCode'] == 0 for r in tests['commands']), 'Test command failed')
    require(len(tests['supplementalChecks']) == 1
            and tests['supplementalChecks'][0]['name'] == 'productionIndependentRiskCrosscheck'
            and tests['supplementalChecks'][0]['exitCode'] == 0
            and json.loads(tests['supplementalChecks'][0]['stdout'])['passed'], 'Synthetic risk crosscheck failed')
    started = read(OUT / 'run-start.json')
    for key, name in (('preAccountTests', 'pre-account-tests.json'), ('inputValidation', 'input-validation.json'),
                      ('inputCompletion', 'input-completion.json')):
        require(bound(started[key]) == OUT / name, 'Run prerequisites mismatch')
    audits = {name: read(OUT / name) for name in ('input-validation.json', 'validation.json', 'readout-validation.json')}
    for name, proof in audits.items():
        require(proof['passed'] and proof['planSha256'] == plan_sha, 'Failed/stale audit: ' + name)
        script = 'scripts/verify-reclaim-breakout-volatility-trail' + ('-readout.py' if name.startswith('readout') else '.py')
        require(proof['verifierSha256'] == plan['sourcePins'][script] == sha(ROOT / script), 'Audit source differs')
        pins(proof.get('independentReferences', {}))
    account_proof, readout_proof = audits['validation.json'], audits['readout-validation.json']
    require(tuple(account_proof['counts'][k] for k in ('accounts', 'newAccounts', 'reusedAccounts')) == (18, 6, 12),
            'Account audit incomplete')
    require(account_proof['comparisonSha256'] == readout_proof['comparisonSha256'] == sha(OUT / 'comparison.json'),
            'Comparison audit stale')
    require(readout_proof['readoutSha256'] == sha(OUT / 'readout.json')
            and readout_proof['accountValidationSha256'] == sha(OUT / 'validation.json'), 'Readout audit stale')
    require(comparison['planSha256'] == result['planSha256'] == plan_sha, 'Results bound to wrong plan')
    require(result['accountRows'] == comparison['rows'], 'Account readout differs')
    verify_matrix(result)
    for period in PERIODS:
        configs = plan['periods'][period]['policyConfigs']
        baseline = configs['trail10']
        require(baseline['stopLossPct'] == .05 and baseline['trailingStopPct'] == .1
                and baseline['maxHoldingDays'] == baseline['takeProfitPct'] == 0, 'Original rules changed')
        for mode in MODES[1:]:
            require(configs[mode] == {**baseline, 'trailingStopPct': 0}, 'Unrelated policy changes')
        for board in BOARDS:
            for mode in MODES[:2]:
                require(comparison['artifacts'][period][board][mode] == plan['baselineArtifacts'][period][board][mode],
                        'Reference bytes not reused')
                for item in plan['baselineArtifacts'][period][board][mode].values():
                    bound(item)
    sidecar = bound(result['opportunityRowsArtifact'])
    require(result['opportunityRowsArtifact']['count'] == 8155
            and result['opportunityRowsArtifact']['policiesPerRow'] == list(MODES)
            and readout_proof['opportunityRowsArtifactSha256'] == sha(sidecar), 'Original opportunity matrix mismatch')
    require(readout_proof['counts']['policyDispositions'] == 8155 * 3, 'Disposition denominator mismatch')
    render, visual = (read(OUT / n) for n in ('render-receipt.json', 'visual-review.json'))
    pins(render['inputPins'], OUT)
    pins(render['presentationHelpers'])
    presentation_files = ['scripts/render-reclaim-breakout-volatility-trail.py',
                          'scripts/verify-reclaim-breakout-volatility-trail-preview.cjs']
    require(render['builderSha256'] == sha(ROOT / presentation_files[0]), 'Presentation builder changed')
    require(all(render['inputPins'].get(n) == sha(OUT / n) for n in ('readout.json', 'readout-validation.json')),
            'Presentation readout binding missing')
    require(render['deliveryOrder'] == ['chart', 'sources'] and set(render['fragments']) == {'chart', 'sources'},
            'Missing final presentation')
    for mapping in (render['fragments'], render['payloads']):
        for item in mapping.values():
            bound(item, OUT)
    require(visual['passed'] and visual['images'], 'Actual image review missing')
    qa_path = bound({'path': visual['qaPath'], 'sha256': visual['qaSha256']})
    qa = read(qa_path)
    require(qa['passed'] and qa['renderReceiptSha256'] == sha(OUT / 'render-receipt.json')
            and qa['scriptSha256'] == sha(ROOT / presentation_files[1]), 'Browser QA stale')
    require({bound(i, OUT): i['sha256'] for i in qa['images']}
            == {bound(i): i['sha256'] for i in visual['images']}, 'Final images not reviewed')
    for name, item in qa['fragments'].items():
        require(bound(item, OUT) == bound(render['fragments'][name], OUT), 'Reviewed fragment differs')
    sources = set(plan['sourcePins']) | set(tests['pins']) | set(render['presentationHelpers'])
    sources.update(presentation_files)
    sources.add(Path(__file__).relative_to(ROOT).as_posix())
    sources.add('docs/reclaim-breakout-volatility-trail-v1.json')
    for proof in audits.values():
        sources.update(proof.get('independentReferences', {}))
    source_pins = {name: sha(ROOT / name) for name in sorted(sources)}
    archive_path = OUT / 'execution-sources.zip'
    if not archive_path.exists():
        with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in source_pins:
                archive.write(ROOT / name, name)
    with zipfile.ZipFile(archive_path) as archive:
        require(archive.testzip() is None and set(archive.namelist()) == set(source_pins), 'Archive inventory differs')
        require(all(hashlib.sha256(archive.read(n)).hexdigest() == h for n, h in source_pins.items()), 'Archive bytes differ')
    write_once(OUT / 'execution-sources-manifest.json', {'planSha256': plan_sha, 'sourcePins': source_pins, 'archive': ref(archive_path)})
    program = read(PROGRAM)
    progress = program['progress']
    decision = read(OUT / 'research-decision.json')
    require(bound(decision['basis']) == OUT / 'readout.json' and not decision['automaticPromotion']
            and not decision['targetAchieved'] and decision['goalStatus'] == 'active', 'Research decision unsupported')
    require(progress['completedFixedCandidateTrials'] == 37 and progress['currentScopeTestsPassed'] == 1006,
            'Registry starting counts differ')
    progress['reclaimBreakoutVolatilityTrail'] = {
        'status': 'completed-development-staged-exit-independently-verified-not-promoted',
        'directory': OUT.name, 'planSha256': plan_sha, 'comparisonSha256': sha(OUT / 'comparison.json'),
        'readoutSha256': sha(OUT / 'readout.json'), 'trialAccounting': result['trialAccounting'],
        'accountRows': result['accountRows'], 'comparisons': result['comparisons'],
        'auditRefs': {name: ref(OUT / name) for name in audits}, 'preAccountTests': ref(OUT / 'pre-account-tests.json'),
        'newProductionUnitTests': new_tests, 'selectedWinner': None, 'holdoutClaim': False, 'targetAchieved': False,
        'researchDecision': ref(OUT / 'research-decision.json'),
    }
    progress.update(completedFixedCandidateTrials=38, currentScopeTestsPassed=1006 + new_tests,
        lastTurnClassification='progress: one registered staged exit completed across all six cohorts; twelve exact reference accounts reused; independent account/readout checks passed. No automatic promotion.',
        nextStage=decision['nextResearchScope'])
    progress['currentScopeTestEvidence'] += f' Breakout-volatility staging reused77 existing tests and added{new_tests} distinct production boundary tests; separate audit/readout/browser checks are not trial or unit increments.'
    timestamp = (read(OUT / 'program-after.json')['updatedAt'] if (OUT / 'program-after.json').exists()
                 else datetime.now(timezone.utc).isoformat())
    program.update(updatedAt=timestamp, updatedDate=timestamp[:10], goalStatus='active')
    before_path = OUT / 'program-before.json'
    if not before_path.exists():
        before_path.write_bytes(PROGRAM.read_bytes())
    require(sha(before_path) == BEFORE, 'Registry backup differs')
    # The sealed delivery is written before registry replacement, allowing safe recovery.
    write_once(OUT / 'program-after.json', program)
    files = {p.relative_to(ROOT).as_posix(): sha(p) for p in OUT.rglob('*') if p.is_file()}
    write_once(delivery_path, {'completedAt': timestamp, 'files': files, 'programBeforeSha256': BEFORE,
        'programAfterSha256': sha(OUT / 'program-after.json'), 'planSha256': plan_sha,
        'candidateBatchesBefore': 37, 'candidateBatchesAfter': 38, 'newAccounts': 6, 'referenceAccountsReused': 12,
        'newExitDefinitions': 1, 'distinctNewUnitTests': new_tests, 'currentScopeTestsPassed': 1006 + new_tests,
        'audits': {name: ref(OUT / name) for name in audits}, 'readout': ref(OUT / 'readout.json'),
        'executionSources': ref(archive_path), 'renderReceipt': ref(OUT / 'render-receipt.json'),
        'onlyRepositoryResearchFilesWritten': True, 'userWorkspaceWrites': 0,
        'actualDataPeriods': list(PERIODS), 'data2026Included': False,
        'holdoutClaim': False, 'certified': False, 'targetAchieved': False, 'goalStatus': 'active'})
    require(sha(PROGRAM) == BEFORE, 'Registry changed before replacement')
    pending = BASE / 'program.breakout-volatility-pending.json'
    pending.write_bytes((OUT / 'program-after.json').read_bytes())
    os.replace(pending, PROGRAM)
    return {'completed': True, 'batches': 38, 'productionTests': 1006 + new_tests,
            'deliverySha256': sha(delivery_path), 'programSha256': sha(PROGRAM), 'goalStatus': 'active'}


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--pretests-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(finish(args.plan_sha256, args.pretests_sha256)))
