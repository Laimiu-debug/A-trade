"""Archive one completed volatility-trail experiment; never run or select accounts."""
from argparse import ArgumentParser
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/strategy-search'
OUT = BASE / 'reclaim-volatility-trail-v1'
PROGRAM = BASE / 'program.json'
BEFORE = 'd6496d7461b7dc386ef8af423f2242c3506bb263c3faf3d43cd9de0a5a37a249'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
MODES = ('trail10', 'volatilityTrail')
CONTRASTS = {'volatilityTrailVsTrail10': ('trail10', 'volatilityTrail')}
MATRIX = {'accounts': 12, 'accountRows': 12, 'comparisons': 6, 'yearRows': 24,
          'halfYearRows': 48, 'phaseRows': 96, 'curveEpochRows': 12,
          'coverageRows': 12, 'formationCoverageRows': 6, 'partitionReconciliation': 12}
TRIALS = {'experimentBatches': 1, 'newAccounts': 6, 'referenceAccountsReused': 6,
          'newSetupMechanisms': 0, 'newUniverseVariants': 0, 'newEntryPolicies': 0,
          'newExitDefinitions': 1, 'transferredExitPolicies': 0}
READOUT_TRIALS = {k: v for k, v in TRIALS.items() if k != 'newAccounts'}
READOUT_TRIALS.update(newAccountExecutions=6, summarizerAccountExecutions=0)
DELIVERY_ORDER = ['chart', 'sources']
TABLE_ROWS = [[12, 6, 12, 12, 6, 12, 24, 48, 96, 12]]
HELPERS = ['scripts/' + name for name in (
    'run-reclaim-volatility-trail.py', 'verify-reclaim-volatility-trail.py',
    'summarize-reclaim-volatility-trail.py', 'verify-reclaim-volatility-trail-readout.py',
    'render-reclaim-volatility-trail.py', 'verify-reclaim-volatility-trail-preview.cjs',
    'finalize-reclaim-volatility-trail.py')]

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
    path = Path(path).resolve()
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path), 'bytes': path.stat().st_size}

def bound(item, base=ROOT):
    require(isinstance(item, dict) and {'path', 'sha256'} <= set(item), 'Missing artifact reference')
    require(not Path(item['path']).is_absolute(), 'Absolute artifact reference')
    path = (base / item['path']).resolve()
    require(path.is_relative_to(base.resolve()) and path.is_file(), 'Missing or escaped artifact')
    require(sha(path) == item['sha256'], 'Artifact changed: ' + item['path'])
    if 'bytes' in item:
        require(path.stat().st_size == item['bytes'], 'Artifact byte size differs')
    return path

def pins(values, base=ROOT):
    require(isinstance(values, dict) and values, 'Missing source/input pins')
    for name, value in values.items():
        bound({'path': name, 'sha256': value}, base)

def matrix(result):
    require(all(len(result[key]) == count for key, count in MATRIX.items()), 'Incomplete readout matrix')
    expected = {(p, b, m) for p in PERIODS for b in BOARDS for m in MODES}
    for name in ('accounts', 'accountRows', 'coverageRows'):
        require({(r['period'], r['board'], r['policy']) for r in result[name]} == expected, 'Account identity differs')
    require({(r['period'], r['board'], r['id']) for r in result['comparisons']}
            == {(p, b, c) for p in PERIODS for b in BOARDS for c in CONTRASTS}, 'Comparison identity differs')
    for row in result['comparisons']:
        require((row['control'], row['candidate']) == CONTRASTS[row['id']], 'Comparison direction differs')
    require(result['primaryComparisons'] == result['comparisons'], 'Primary comparison subset differs')
    require(all(r['passed'] is True for r in result['partitionReconciliation']), 'Failed partition reconciliation')
    require(result['certified'] is False and result['targetAchieved'] is False, 'Unregistered certification')
    require(all(result['decision'][k] is None for k in
                ('selectedBoard', 'selectedPolicy', 'automaticNextStrategy', 'profitHurdle')), 'Unregistered promotion')

def check_configs(plan):
    for period in PERIODS:
        configs = plan['periods'][period]['policyConfigs']
        original = deepcopy(plan['periods'][period]['config'])
        require(original == configs['trail10'], 'Reference config changed')
        expected = deepcopy(original)
        expected['trailingStopPct'] = 0
        require(configs['volatilityTrail'] == expected, 'Volatility exit changed unrelated config')
        require(original['stopLossPct'] == .05 and original['trailingStopPct'] == .1
                and original['takeProfitPct'] == original['maxHoldingDays'] == 0,
                'Original risk rules changed')

def finalize(plan_sha, tests_sha):
    require(sha(OUT / 'plan.json') == plan_sha and sha(OUT / 'pre-account-tests.json') == tests_sha,
            'Explicit preregistration hashes differ')
    require(sha(PROGRAM) == BEFORE, 'Research registry changed')
    pending = BASE / 'program.reclaim-volatility-trail-pending.json'
    require(not pending.exists() and not any((OUT / name).exists() for name in
        ('execution-sources.zip', 'execution-sources-manifest.json', 'program-before.json', 'program-after.json', 'delivery.json')),
        'Finalization outputs already exist')
    plan, tests, comparison, result = [read(OUT / name) for name in
        ('plan.json', 'pre-account-tests.json', 'comparison.json', 'readout.json')]
    for values in (plan['sourcePins'], plan['inputPins'], tests['pins'], result['inputPins']):
        pins(values)
    check_configs(plan)
    require(all(type(plan[k]) is int and plan[k] == v for k, v in TRIALS.items())
            and plan['rules']['evidence']['trialAccounting'] == TRIALS
            and result['trialAccounting'] == READOUT_TRIALS, 'Trial accounting changed')
    test_spec = plan['preAccountTestSpec']
    new_tests = test_spec['newUnitTests']
    require(type(new_tests) is int and new_tests > 0 and test_spec['inheritedUnitTests'] == 57
            and test_spec['commandCount'] == 6, 'Unregistered test count')
    require(tests['passed'] is True and tests['planSha256'] == plan_sha and tests['unitTests'] == 57 + new_tests
            and tests['productionUnitIncrement'] == new_tests and tests['allChecksBeforeFirstNewAccount'] is True,
            'Pre-account tests changed')
    require(len(tests['commands']) == 6 and all(r['exitCode'] == 0 for r in tests['commands']), 'A pre-account check failed')
    require(len(tests['supplementalChecks']) == 1
            and tests['supplementalChecks'][0]['name'] == 'productionIndependentRiskCrosscheck'
            and tests['supplementalChecks'][0]['exitCode'] == 0
            and json.loads(tests['supplementalChecks'][0]['stdout'])['passed'] is True,
            'Production/independent synthetic risk crosscheck missing or failed')
    started = read(OUT / 'run-start.json')
    require(bound(started['preAccountTests']) == OUT / 'pre-account-tests.json'
            and bound(started['inputValidation']) == OUT / 'input-validation.json'
            and bound(started['inputCompletion']) == OUT / 'input-completion.json', 'Run-start prerequisites differ')
    require(comparison['planSha256'] == result['planSha256'] == plan_sha, 'Wrong account/readout registration')
    matrix(result)
    require(result['accountRows'] == comparison['rows'], 'Readout account matrix differs')
    audits = {name: read(OUT / name) for name in ('input-validation.json', 'validation.json', 'readout-validation.json')}
    for name, proof in audits.items():
        require(proof['passed'] is True and proof['planSha256'] == plan_sha, 'Failed/stale audit: ' + name)
        script = HELPERS[3] if name == 'readout-validation.json' else HELPERS[1]
        require(proof['verifierSha256'] == plan['sourcePins'][script] == sha(ROOT / script), 'Audit code differs')
        if proof.get('independentReferences'):
            pins(proof['independentReferences'])
    inputs, accounts, readout = [audits[name] for name in ('input-validation.json', 'validation.json', 'readout-validation.json')]
    require(bound(accounts['preAccountTests']) == OUT / 'pre-account-tests.json'
            and accounts['preAccountTests'] == started['preAccountTests'], 'Audited pre-account tests differ')
    for proof in (inputs, accounts):
        require(proof['inputCompletionSha256'] == sha(OUT / 'input-completion.json'), 'Input completion binding differs')
        for key in ('baselineArtifacts', 'baselineSchedules', 'baselineInputs', 'baselineComparison', 'baselinePlan'):
            require(proof[key] == plan[key], 'Reused baseline binding differs')
    require(inputs['counts']['sourceInputs'] == inputs['counts']['originalSchedules'] == 6
            and inputs['counts']['newPolicySchedules'] == 6, 'Input matrix incomplete')
    require(tuple(accounts['counts'][k] for k in ('accounts', 'newAccounts', 'reusedAccounts')) == (12, 6, 6),
            'Account matrix incomplete')
    require(accounts['inputValidationSha256'] == sha(OUT / 'input-validation.json'), 'Account input audit differs')
    for proof in (accounts, readout):
        require(proof['comparisonSha256'] == sha(OUT / 'comparison.json'), 'Audited comparison differs')
    require(readout['readoutSha256'] == sha(OUT / 'readout.json')
            and readout['accountValidationSha256'] == sha(OUT / 'validation.json'), 'Audited readout differs')
    require(bound(result['verification']) == OUT / 'validation.json', 'Readout account audit differs')
    sidecar = bound(result['opportunityRowsArtifact'])
    require(sidecar == OUT / 'opportunity-dispositions.jsonl.gz'
            and result['opportunityRowsArtifact']['policiesPerRow'] == list(MODES), 'Opportunity sidecar differs')
    require(readout['opportunityRowsArtifactSha256'] == sha(sidecar), 'Audited opportunity sidecar differs')
    opportunity_count = result['opportunityRowsArtifact']['count']
    require(opportunity_count == sum(r['formationTop5'] for r in result['formationCoverageRows'])
            == inputs['counts']['sourceImmediateOpportunities'] == accounts['counts']['sourceImmediateOpportunities'],
            'Original opportunity population differs')
    require(readout['counts']['sourceImmediateOpportunities'] == opportunity_count
            and readout['counts']['policyDispositions'] == 2 * opportunity_count
            and readout['counts']['fullCurveIntervals'] == 12 and readout['counts']['subIntervals'] == 180
            and all(readout['counts'][k] == n for k, n in MATRIX.items()), 'Readout audit coverage incomplete')
    for period in PERIODS:
        for board in BOARDS:
            require(comparison['artifacts'][period][board]['trail10'] == plan['baselineArtifacts'][period][board],
                    'Baseline account was not reused byte-for-byte')
            for item in plan['baselineArtifacts'][period][board].values():
                bound(item)
    render = read(OUT / 'render-receipt.json')
    pins(render['inputPins'], OUT)
    require(all(render['inputPins'].get(name) == sha(OUT / name)
                for name in ('readout.json', 'readout-validation.json')), 'Stale presentation input')
    require(render['builderSha256'] == sha(ROOT / HELPERS[-3]), 'Presentation builder changed')
    require(render['deliveryOrder'] == DELIVERY_ORDER and set(render['fragments']) == set(DELIVERY_ORDER)
            and render['chartRows'] == 6 and render['tableRows'] == TABLE_ROWS,
            'Incomplete presentation matrix')
    pins(render['presentationHelpers'])
    for item in render['fragments'].values():
        bound(item, OUT)
    for item in render['payloads'].values():
        bound(item, OUT)
    visual = read(OUT / 'visual-review.json')
    require(visual['passed'] is True and visual['images'], 'Root actual image review missing')
    qa_path = bound({'path': visual['qaPath'], 'sha256': visual['qaSha256']})
    require(qa_path.is_relative_to(OUT), 'QA belongs to another experiment')
    qa = read(qa_path)
    require(qa['passed'] is True and qa['renderReceiptSha256'] == sha(OUT / 'render-receipt.json')
            and qa['scriptSha256'] == sha(ROOT / HELPERS[-2]), 'Stale or failed browser QA')
    require(set(qa['fragments']) == set(render['fragments']), 'Incomplete fragment QA')
    for key, item in qa['fragments'].items():
        require(bound(item, OUT) == bound(render['fragments'][key], OUT), 'QA fragment differs')
    qa_images = {bound(item, OUT): item['sha256'] for item in qa['images']}
    reviewed = {bound(item): item['sha256'] for item in visual['images']}
    require(reviewed == qa_images, 'Not all final QA images actually reviewed')
    program = read(PROGRAM)
    progress = program['progress']
    require(progress['completedFixedCandidateTrials'] == 36 and progress['currentScopeTestsPassed'] == 958
            and program['goalStatus'] == 'active' and 'reclaimVolatilityTrail' not in progress, 'Registry state differs')
    sources = set(plan['sourcePins']) | set(tests['pins']) | set(HELPERS)
    sources.update(render['presentationHelpers'])
    sources.add('docs/reclaim-volatility-trail-v1.json')
    sources.update(p.relative_to(ROOT).as_posix() for p in OUT.glob('*.py'))
    for proof in audits.values():
        sources.update(proof.get('independentReferences', {}))
    source_pins = {name: sha(ROOT / name) for name in sorted(sources)}
    pins(source_pins)
    with zipfile.ZipFile(OUT / 'execution-sources.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in source_pins:
            archive.write(ROOT / name, name)
    with zipfile.ZipFile(OUT / 'execution-sources.zip') as archive:
        require(archive.testzip() is None and set(archive.namelist()) == set(source_pins), 'Source archive inventory differs')
        require(all(hashlib.sha256(archive.read(n)).hexdigest() == h for n, h in source_pins.items()), 'Source archive hashes differ')
    now = datetime.now(timezone.utc).isoformat()
    write(OUT / 'execution-sources-manifest.json', {'createdAt': now, 'planSha256': plan_sha,
          'sourcePins': source_pins, 'archive': ref(OUT / 'execution-sources.zip')})
    progress['reclaimVolatilityTrail'] = {
        'status': 'completed-development-volatility-trail-independently-verified-not-promoted',
        'directory': OUT.name, 'planSha256': plan_sha, 'comparisonSha256': sha(OUT / 'comparison.json'),
        'readoutSha256': sha(OUT / 'readout.json'), 'trialAccounting': result['trialAccounting'],
        'accountRows': result['accountRows'], 'comparisons': result['comparisons'],
        'coverageRows': result['coverageRows'], 'curveEpochRows': result['curveEpochRows'],
        'auditRefs': {name: ref(OUT / name) for name in audits}, 'preAccountTests': ref(OUT / 'pre-account-tests.json'),
        'opportunityRowsArtifact': result['opportunityRowsArtifact'], 'limitations': result['limitations'],
        'selectedWinner': None, 'holdoutClaim': False, 'certified': False, 'targetAchieved': False,
        'newProductionUnitTests': new_tests}
    progress['completedFixedCandidateTrials'] = 37
    progress['currentScopeTestsPassed'] = 958 + new_tests
    progress['lastTurnClassification'] = ('progress: completed six volatility-trail accounts and reused six exact fixed-percentage trail accounts; '
        'independent source/account/readout audits and browser/root image review passed. No strategy promotion or fresh-holdout claim.')
    progress['nextStage'] = ('结合全部六个来源检验波动跟踪退出的实际闭仓、期末持仓、费用、回撤、暴露与阶段差异；'
        '不按结果挑选板块或事后阶段，不设固定赢家收益/胜率/持有门槛。下一机制先查重冻结，目标继续active。')
    progress['currentScopeTestEvidence'] += (f' Volatility-trail reused 57 existing production tests and added {new_tests} new adapter tests. '
        'Independent synthetic runner/audit/readout checks are not included in the unit test increment.')
    program.update(updatedAt=now, updatedDate=now[:10], goalStatus='active')
    with (OUT / 'program-before.json').open('xb') as stream:
        stream.write(PROGRAM.read_bytes())
    require(sha(OUT / 'program-before.json') == BEFORE, 'Registry changed before backup')
    write(OUT / 'program-after.json', program)
    with pending.open('xb') as stream:
        stream.write((OUT / 'program-after.json').read_bytes())
    pins(source_pins)
    require(sha(PROGRAM) == BEFORE and sha(OUT / 'plan.json') == plan_sha, 'State changed before registry write')
    os.replace(pending, PROGRAM)
    files = {p.relative_to(ROOT).as_posix(): sha(p) for p in OUT.rglob('*') if p.is_file()}
    write(OUT / 'delivery.json', {'completedAt': now, 'files': files, 'programBeforeSha256': BEFORE,
        'programAfterSha256': sha(PROGRAM), 'planSha256': plan_sha, 'candidateBatchesBefore': 36,
        'candidateBatchesAfter': 37, 'newAccounts': 6, 'referenceAccountsReused': 6,
        'newSetupMechanisms': 0, 'newUniverseVariants': 0, 'newEntryPolicies': 0,
        'newExitDefinitions': 1, 'transferredExitPolicies': 0, 'distinctNewUnitTests': new_tests,
        'currentScopeTestsPassed': 958 + new_tests, 'preAccountTests': ref(OUT / 'pre-account-tests.json'),
        'audits': {name: ref(OUT / name) for name in audits}, 'readout': ref(OUT / 'readout.json'),
        'opportunities': ref(sidecar), 'renderReceipt': ref(OUT / 'render-receipt.json'),
        'browserQa': ref(qa_path), 'visualReview': ref(OUT / 'visual-review.json'),
        'executionSources': ref(OUT / 'execution-sources.zip'),
        'executionSourceManifest': ref(OUT / 'execution-sources-manifest.json'),
        'actualDataPeriods': list(PERIODS), 'data2026Included': False,
        'onlyRepositoryResearchFilesWritten': True, 'userWorkspaceWrites': 0,
        'holdoutClaim': False, 'certified': False, 'targetAchieved': False, 'goalStatus': 'active'})
    return {'completed': True, 'batches': 37, 'productionTests': 958 + new_tests, 'goalStatus': 'active',
            'programSha256': sha(PROGRAM), 'deliverySha256': sha(OUT / 'delivery.json')}

def self_test():
    fixture = {k: [{} for _ in range(n)] for k, n in MATRIX.items()}
    for name in ('accounts', 'accountRows', 'coverageRows'):
        fixture[name] = [{'period': p, 'board': b, 'policy': m} for p in PERIODS for b in BOARDS for m in MODES]
    fixture['comparisons'] = [{'period': p, 'board': b, 'id': c, 'control': pair[0], 'candidate': pair[1]}
        for p in PERIODS for b in BOARDS for c, pair in CONTRASTS.items()]
    fixture['primaryComparisons'] = list(fixture['comparisons'])
    fixture.update(partitionReconciliation=[{'passed': True} for _ in range(12)], certified=False, targetAchieved=False,
                   decision={k: None for k in ('selectedBoard', 'selectedPolicy', 'automaticNextStrategy', 'profitHurdle')})
    matrix(fixture)
    for name in MATRIX:
        broken = deepcopy(fixture)
        broken[name].pop()
        try:
            matrix(broken)
        except ValueError:
            continue
        raise AssertionError('Incomplete matrix accepted: ' + name)
    config = {'stopLossPct': .05, 'trailingStopPct': .1, 'takeProfitPct': 0, 'maxHoldingDays': 0}
    sample = {'periods': {p: {'config': deepcopy(config), 'policyConfigs': {
        'trail10': deepcopy(config), 'volatilityTrail': {**config, 'trailingStopPct': 0}}} for p in PERIODS}}
    check_configs(sample)
    sample['periods'][PERIODS[0]]['policyConfigs']['trail10']['stopLossPct'] = 0
    try:
        check_configs(sample)
    except ValueError:
        pass
    else:
        raise AssertionError('Missing original stop accepted')
    return {'passed': True, 'checks': len(MATRIX) + 3, 'realFilesRead': 0, 'filesWritten': 0}

if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--plan-sha256')
    parser.add_argument('--pretests-sha256')
    args = parser.parse_args()
    if not args.self_test and (not args.plan_sha256 or not args.pretests_sha256):
        parser.error('Explicit plan and pre-account test receipt hashes required')
    print(json.dumps(self_test() if args.self_test else finalize(args.plan_sha256, args.pretests_sha256), ensure_ascii=False))
