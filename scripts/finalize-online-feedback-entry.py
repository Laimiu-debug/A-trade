"""Seal reviewed online-feedback evidence; never execute an account or model.

The only registry transition is 38 -> 39. A sealed delivery precedes the atomic
registry replacement, so an interrupted replacement can be resumed safely.
"""
from argparse import ArgumentParser
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/strategy-search'
OUT = BASE / 'online-feedback-entry-v1'
PROGRAM = BASE / 'program.json'
BEFORE = 'b2c60b262b6b2275f098ade709da2202b37d261e2a8d2f9031acbcb7cc41a7c2'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
EXITS = ('trail10', 'volatilityTrail')
MODES = ('baseline', 'intercept', 'ridge')
KEYS = ('period', 'board', 'exit', 'candidate')
GROUPS = {(p, b, e) for p in PERIODS for b in BOARDS for e in EXITS}
IDENTITIES = {(*g, m) for g in GROUPS for m in MODES}
CONTRASTS = {'interceptVsBaseline': ('baseline', 'intercept'),
             'ridgeVsIntercept': ('intercept', 'ridge'), 'ridgeVsBaseline': ('baseline', 'ridge')}
TRIALS = {'experimentBatches': 1, 'newEntryPolicies': 2, 'newAccounts': 24,
          'referenceAccountsReused': 12, 'newExitDefinitions': 0, 'newUniverseVariants': 0}
MATRIX = {'accounts': 36, 'accountRows': 36, 'comparisons': 36, 'yearRows': 72,
          'halfYearRows': 144, 'phaseRows': 288, 'curveEpochRows': 36,
          'coverageRows': 36, 'feedbackCoverageRows': 24, 'sampleShiftRows': 24,
          'partitionReconciliation': 36, 'feedbackDailyRows': 5814}
ORDER = ['trail10Chart', 'trail10Sources', 'volatilityTrailChart', 'volatilityTrailSources']
TABLE_ROWS = [[18, 18, 18, 18, 12, 12, 36, 72, 144, 18]] * 2
MAIN = 'scripts/verify-online-feedback-entry.py'
READOUT_AUDIT = 'scripts/verify-online-feedback-readout.py'
RENDER = 'scripts/render-online-feedback-entry.py'
QA = 'scripts/verify-online-feedback-preview.cjs'
_HASHES = {}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    path = Path(path).resolve()
    stat = path.stat()
    key = (path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    if key not in _HASHES:
        with path.open('rb') as stream:
            _HASHES[key] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return _HASHES[key]


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def write_once(path, value):
    raw = value if isinstance(value, bytes) else encoded(value)
    if path.exists():
        require(path.read_bytes() == raw, 'Existing evidence differs: ' + str(path))
    else:
        with path.open('xb') as stream:
            stream.write(raw)


def ref(path):
    path = Path(path).resolve()
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def bound(item, base=ROOT):
    relative = Path(item['path'])
    require(not relative.is_absolute(), 'Absolute repository/artifact reference')
    path = (base / relative).resolve()
    require(path.is_relative_to(base.resolve()) and path.is_file(), 'Missing/escaped evidence: ' + str(relative))
    require(sha(path) == item['sha256'], 'Changed evidence: ' + str(relative))
    return path


def pins(mapping, base=ROOT):
    for name, digest in mapping.items():
        bound({'path': name, 'sha256': digest}, base)


def exact_ref(item, path):
    require(bound(item) == path.resolve(), 'Reference points to wrong artifact: ' + str(path))


def identities(rows, keys, expected, label):
    actual = [tuple(row[k] for k in keys) for row in rows]
    require(len(actual) == len(expected) and set(actual) == expected, 'Incomplete/duplicate ' + label)


def no_promotion(value):
    require(all(value[k] is False for k in ('holdoutClaim', 'certified', 'targetAchieved')), 'Unsupported promotion/holdout')


def verify_tests(tests, plan, plan_sha):
    require(tests['passed'] is True and tests['planSha256'] == plan_sha
            and tests['sourcePins'] == plan['sourcePins'] and tests['allChecksBeforeFirstNewAccount'] is True,
            'Invalid actual pre-account test receipt')
    require(tests['newUnitTests'] == 28 and tests['inheritedUnitTests'] == 69, 'Production test counts differ')
    commands = tests['commands']
    require(len(commands) == 4 and all(row['exitCode'] == 0 for row in commands), 'Missing/failed pre-account command')
    for row, count in zip(commands[:2], (28, 69)):
        require(re.search(r'Ran ' + str(count) + r' tests\b', row['stderr']) is not None
                and re.search(r'^OK\s*$', row['stderr'], re.MULTILINE) is not None, 'Actual unittest output differs')
    require('tests.test_research_online_feedback' in commands[0]['argv']
            and 'tests.test_online_feedback_entry_runner' in commands[0]['argv'], 'New production tests omitted')
    require(all(name in commands[1]['argv'] for name in ('tests.test_research_fixed_signal_account',
                'tests.test_research_account_fees', 'tests.test_research_account_outcomes',
                'tests.test_research_volatility_trail')), 'Inherited production tests omitted')
    synthetic = []
    for row, name in zip(commands[2:], (MAIN, 'scripts/summarize-online-feedback-entry.py')):
        require(name in row['argv'] and '--self-test' in row['argv'], 'Wrong synthetic command')
        receipt = json.loads(row['stdout'])
        require(receipt['passed'] is True and receipt['syntheticGroups'] == len(receipt['checks']) > 0,
                'Failed/empty independent or summary synthetic checks')
        synthetic.append({'script': name, 'groups': receipt['syntheticGroups']})
    return {'historicalCurrentScopeTestsPassed': 1026, 'historicalSuiteRerunThisBatch': False,
            'newDistinctProductionUnitTests': 28, 'inheritedProductionUnitTestsRerun': 69,
            'actualProductionUnitTestsRun': 97, 'separateSyntheticChecks': synthetic,
            'meaning': 'Historical 1026 is preserved, not a claim that all 1026 were rerun. New28 and rerun69 are separately recorded.'}


def verify_matrix(report, comparison, proof):
    for name, count in MATRIX.items():
        require(len(report[name]) == count, 'Incomplete readout: ' + name)
    for name in ('accounts', 'accountRows', 'coverageRows', 'partitionReconciliation'):
        identities(report[name], KEYS, IDENTITIES, name)
    candidates = {identity for identity in IDENTITIES if identity[-1] != 'baseline'}
    for name in ('feedbackCoverageRows', 'sampleShiftRows'):
        identities(report[name], KEYS, candidates, name)
    identities(comparison['rows'], KEYS, IDENTITIES, 'saved accounts')
    require([r['artifacts'] for r in report['accounts']] == comparison['rows'], 'Readout saved-account references differ')
    require([r['artifactRefs'] for r in proof['accounts']] == comparison['rows'], 'Account audit references differ')
    identities(report['comparisons'], ('period', 'board', 'exit', 'id'),
               {(*g, c) for g in GROUPS for c in CONTRASTS}, 'signed comparisons')
    for row in report['comparisons']:
        require((row['control'], row['candidate']) == CONTRASTS[row['id']], 'Comparison direction differs')
    require(all(r['passed'] is True and r['checks'] and all(c['passed'] is True for c in r['checks'])
                for r in report['partitionReconciliation']), 'Continuous interval reconciliation failed')
    require(report['counts'] == proof_readout_counts(report), 'Readout declared counts differ')
    no_promotion(report)


def proof_readout_counts(report):
    counts = {name: MATRIX[name] for name in ('accounts', 'comparisons', 'yearRows', 'halfYearRows', 'phaseRows',
                                            'curveEpochRows', 'coverageRows', 'feedbackCoverageRows', 'sampleShiftRows')}
    return {**counts, 'originalOpportunities': 8155, 'sourceExitOpportunities': 16310,
            'policyDispositions': 48930, 'actualCandidateEntryEpisodes': report['candidateEntryRowsArtifact']['count'],
            'feedbackDecisionDays': 5814}


def stamp(text):
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    require(value.tzinfo is not None, 'Evidence timestamp lacks timezone')
    return value


def validate(plan_sha, tests_sha):
    names = ('plan.json', 'pre-account-tests.json', 'input-completion.json', 'input-validation.json', 'run-start.json',
             'comparison.json', 'validation.json', 'readout.json', 'readout-validation.json',
             'render-receipt.json', 'visual-review.json', 'research-decision.json')
    docs = {name: read(OUT / name) for name in names}
    plan, tests, completion, inputs, start, comparison, audit, report, check, render, visual, decision = (docs[n] for n in names)
    require(sha(OUT / 'plan.json') == plan_sha and sha(OUT / 'pre-account-tests.json') == tests_sha, 'Explicit hashes differ')
    require(plan['schema'] == OUT.name and plan['previousProgram'] == {'path': PROGRAM.relative_to(ROOT).as_posix(), 'sha256': BEFORE},
            'Wrong registered predecessor')
    require(all(plan[k] == n for k, n in TRIALS.items()) and plan['rules']['trialAccounting'] == TRIALS, 'Trial scope differs')
    no_promotion(plan)
    require(plan['parameterSearch'] is False, 'Parameter search outside registered scope')
    exact_ref(plan['protocol'], ROOT / 'docs/online-feedback-entry-v1.json')
    require(read(bound(plan['protocol'])) == plan['rules'], 'Registered protocol bytes/content differ')
    bound(plan['previousDelivery'])
    for value in (plan, tests, inputs, audit, report, check):
        for key in ('sourcePins', 'inputPins'):
            if key in value:
                pins(value[key])
    test_evidence = verify_tests(tests, plan, plan_sha)
    for key, name in (('tests', 'pre-account-tests.json'), ('inputValidation', 'input-validation.json'),
                      ('inputCompletion', 'input-completion.json')):
        exact_ref(start[key], OUT / name)
    for proof, script in ((inputs, MAIN), (audit, MAIN), (check, READOUT_AUDIT)):
        require(proof['passed'] is True and proof['planSha256'] == plan_sha
                and proof['verifierSha256'] == sha(ROOT / script), 'Failed/stale audit source or plan')
        no_promotion(proof)
    require(plan['sourcePins'][MAIN] == audit['verifierSha256'] == check['mainVerifierSha256'], 'Main auditor not frozen')
    for proof in (inputs, audit, check):
        require(proof['inputCompletionSha256'] == sha(OUT / 'input-completion.json'), 'Stale input completion')
    for proof in (audit, check):
        require(proof['inputValidationSha256'] == sha(OUT / 'input-validation.json')
                and proof['comparisonSha256'] == sha(OUT / 'comparison.json'), 'Stale independent input/account evidence')
    for proof in (inputs, audit):
        exact_ref(proof['preAccountTests'], OUT / 'pre-account-tests.json')
    exact_ref(audit['runStart'], OUT / 'run-start.json')
    require(check['accountValidationSha256'] == sha(OUT / 'validation.json')
            and check['readoutSha256'] == sha(OUT / 'readout.json'), 'Readout audit is stale')
    require(report['verification']['passed'] is True, 'Report verification false')
    exact_ref(report['verification'], OUT / 'validation.json')
    require(report['planSha256'] == comparison['planSha256'] == completion['planSha256'] == plan_sha
            and report['comparisonSha256'] == sha(OUT / 'comparison.json'), 'Cross-stage plan/comparison differs')
    require(comparison['schema'] == OUT.name + '-comparison', 'Unexpected comparison schema')
    for value in (comparison, report):
        require(all(value[k] == TRIALS[k] for k in ('newAccounts', 'referenceAccountsReused', 'newEntryPolicies')), 'Account/entry scope changed')
        no_promotion(value)
    require(comparison['experimentBatches'] == 1 and completion['allSchedulesFrozenBeforeAccounts'] is True
            and completion['newAccountsExecuted'] == 0, 'Unfrozen input execution')
    groups = completion['groups']
    identities(groups, KEYS[:3], GROUPS, 'input groups')
    require(inputs['preparedGroups'] == groups, 'Input proof group bindings differ')
    expected_counts = {'accounts': 36, 'newAccounts': 24, 'reusedAccounts': 12,
                       'sourceImmediateOpportunities': 8155, 'sourceExitOpportunities': 16310, 'preparedSchedules': 24}
    require(all(audit['counts'][k] == n for k, n in expected_counts.items()), 'Account proof matrix differs')
    require(inputs['counts']['groups'] == 12 and inputs['counts']['inputDecisionDays'] == 5814
            and inputs['counts']['preparedSchedules'] == 24 and inputs['counts']['newAccounts'] == 0,
            'Input proof matrix differs')
    by_group = {tuple(g[k] for k in KEYS[:3]): g for g in groups}
    for g in groups:
        p, b, e = (g[k] for k in KEYS[:3])
        require(g['accountData'] == plan['sourceData'][p][b]['accountData']
                and g['baseline'] == plan['accountArtifacts'][p][b][e]
                and g['baselineSchedule'] == plan['baselineSchedules'][p][b], 'Source/shadow identity differs')
        for name in ('accountData', 'baselineSchedule', 'decisions', 'shadow', 'modelMetadata'):
            bound(g[name])
        require(set(g['candidates']) == set(MODES[1:]), 'Candidate schedule set differs')
        for item in (*g['baseline'].values(), *g['candidates'].values()):
            bound(item)
    for row in comparison['rows']:
        p, b, e, m = (row[k] for k in KEYS)
        g = by_group[p, b, e]
        require(row['reusedAccount'] is (m == 'baseline'), 'Reference reuse marker differs')
        require(row['schedule'] == (g['baselineSchedule'] if m == 'baseline' else g['candidates'][m]), 'Unfrozen account schedule')
        folder = OUT / p / b / e
        exact_ref(row['summary'], folder / (m + '.summary.json'))
        for key in ('result', 'checkpoint', 'schedule'):
            bound(row[key])
        if m == 'baseline':
            require(all(row[k] == g['baseline'][e + '.' + k + '.json.gz'] for k in ('result', 'checkpoint')), 'Baseline result/checkpoint not byte-reused')
        else:
            for key in ('result', 'checkpoint'):
                exact_ref(row[key], folder / (m + '.' + key + '.json.gz'))
    verify_matrix(report, comparison, audit)
    require(check['counts'] == {**report['counts'], 'newAccounts': 24, 'reusedAccounts': 12,
                               'sourceImmediateOpportunities': 8155}, 'Readout independent count matrix differs')
    for name, count in (('opportunityRowsArtifact', 48930), ('candidateEntryRowsArtifact', report['counts']['actualCandidateEntryEpisodes'])):
        item = report[name]
        bound(item)
        require(check[name] == item and check[name + 'Sha256'] == item['sha256']
                and item['count'] == count and item['format'] == 'jsonl+gzip'
                and item['identity'] == ['period', 'board', 'exit', 'candidate', 'formationId'], 'Sidecar identity/count/binding differs')
    timeline = [plan['registeredAt'], tests['startedAt'], tests['completedAt'], completion['completedAt'],
                inputs['verifiedAt'], start['startedAt'], comparison['completedAt'], audit['verifiedAt'], check['verifiedAt']]
    times = list(map(stamp, timeline))
    require(times == sorted(times), 'Tests/input audit did not precede accounts or final audit')
    pins(render['inputPins'], OUT)
    require(all(render['inputPins'].get(name) == sha(OUT / name) for name in
                ('plan.json', 'comparison.json', 'validation.json', 'readout.json', 'readout-validation.json')), 'Presentation analytical binding missing')
    require(render['builderSha256'] == sha(ROOT / RENDER) and render['deliveryOrder'] == ORDER
            and set(render['fragments']) == set(render['payloads']) == set(ORDER)
            and render['tableRows'] == TABLE_ROWS and render['chartRows'] == 36
            and render['chartRowsByExit'] == {'trail10': 18, 'volatilityTrail': 18}, 'Presentation matrix/builder differs')
    require(render['actualMarketRuns'] == 0 and render['analysisArtifactsModified'] is False
            and render['holdoutClaim'] is False and render['includes2026'] is False, 'Presentation changed analytical scope')
    for mapping in (render['fragments'], render['payloads']):
        for item in mapping.values():
            bound(item, OUT)
    require(visual['passed'] is True and visual['reviewedBy'] == 'root' and visual['actualImagesViewed'] is True
            and visual['renderReceiptSha256'] == sha(OUT / 'render-receipt.json') and visual['observations'], 'Actual root image review missing/stale')
    qa_path = bound({'path': visual['qaPath'], 'sha256': visual['qaSha256']})
    require(qa_path.is_relative_to(OUT / 'browser-qa') and qa_path.name == 'preview-verification.json', 'Wrong QA report')
    qa = read(qa_path)
    require(qa['passed'] is True and qa['renderReceiptSha256'] == sha(OUT / 'render-receipt.json')
            and qa['scriptSha256'] == sha(ROOT / QA) and qa['errors'] == []
            and qa['actualMarketRuns'] == 0 and qa['analysisArtifactsModified'] is False, 'Failed/stale browser QA')
    require(all(qa[k] == render[k] for k in ('fragments', 'payloads', 'deliveryOrder', 'tableRows', 'presentationHelpers')),
            'QA presentation/helpers differ')
    require(len(qa['images']) == len(visual['images']) == 8
            and len({i['path'] for i in qa['images']}) == 8, 'Actual eight-image review incomplete')
    require({bound(i, OUT): i['sha256'] for i in qa['images']} == {bound(i): i['sha256'] for i in visual['images']}, 'Final QA images not all reviewed')
    require(len(qa['chartViews']) == 6 and len(qa['tableChecks']) == 20 and len(qa['interactions']) == 2,
            'QA views/tables/interactions incomplete')
    for key, name in (('readout', 'readout.json'), ('validation', 'readout-validation.json'), ('accountValidation', 'validation.json')):
        exact_ref(decision[key], OUT / name)
    require(decision['automaticPromotion'] is False and decision['targetAchieved'] is False and decision['goalStatus'] == 'active'
            and decision['findings'] and isinstance(decision['nextResearch']['action'], str)
            and decision['nextResearch']['action'].strip(), 'Research decision missing or automatic promotion')
    if 'policyContext' in decision:
        exact_ref(decision['policyContext'], OUT / 'policy-context.json')
    return docs, test_evidence, qa_path


def archive_sources(docs):
    sources = set(docs['plan.json']['sourcePins'])
    for name in ('input-validation.json', 'validation.json', 'readout-validation.json'):
        sources.update(docs[name]['sourcePins'])
    sources.update((MAIN, READOUT_AUDIT, RENDER, QA, 'scripts/summarize-online-feedback-entry.py',
                    'docs/online-feedback-entry-v1.json', Path(__file__).relative_to(ROOT).as_posix()))
    entries = {name: ROOT / name for name in sorted(sources)}
    helpers = docs['render-receipt.json'].get('presentationHelpers', [])
    require(isinstance(helpers, list) and helpers, 'Presentation helper source references required')
    for at, item in enumerate(helpers):
        path = Path(item['path']).resolve()
        require(path.is_file() and sha(path) == item['sha256'], 'Presentation helper changed')
        entries[f'external-presentation/{at:02d}-{path.name}'] = path
    require(len({item['path'] for item in helpers}) == len(helpers), 'Duplicate presentation helpers')
    names = {path.name for path in entries.values()}
    require({'render-inline-chart.mjs', 'render-inline-sources.mjs', 'render.py'} <= names, 'Actual presentation render/wrapper helpers omitted')
    helper_by_name = {Path(item['path']).name: item['sha256'] for item in helpers}
    for name, item in docs['render-receipt.json']['fragments'].items():
        renderer = 'render-inline-chart.mjs' if name.endswith('Chart') else 'render-inline-sources.mjs'
        require(item['rendererSha256'] == helper_by_name[renderer], 'Fragment renderer helper differs')
    digests = {name: sha(path) for name, path in entries.items()}
    archive = OUT / 'execution-sources.zip'
    if not archive.exists():
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as stream:
            for name, path in entries.items():
                stream.write(path, name)
    with zipfile.ZipFile(archive) as stream:
        require(stream.testzip() is None and len(stream.namelist()) == len(entries)
                and set(stream.namelist()) == set(entries), 'Source archive inventory differs')
        require(all(hashlib.sha256(stream.read(name)).hexdigest() == digest for name, digest in digests.items()), 'Source archive bytes differ')
    write_once(OUT / 'execution-sources-manifest.json', {'planSha256': sha(OUT / 'plan.json'),
        'sourcePins': {name: sha(ROOT / name) for name in sorted(sources)}, 'presentationHelpers': helpers,
        'archiveEntries': digests, 'archive': ref(archive),
        'meaning': 'Registered research sources plus actual audit/presentation/finalizer sources; installed runtime is not repackaged.'})
    return archive


def registry_after(program, timestamp, test_evidence, decision, evidence):
    program = deepcopy(program)
    progress = program['progress']
    require(progress['completedFixedCandidateTrials'] == 38 and progress['currentScopeTestsPassed'] == 1026
            and program['goalStatus'] == 'active', 'Registry starting counts/state differ')
    require('onlineFeedbackEntry' not in progress, 'This batch already registered without its sealed delivery')
    progress['onlineFeedbackEntry'] = {'status': 'completed-development-online-feedback-independently-verified-not-promoted',
        'directory': OUT.name, 'trialAccounting': TRIALS, 'testEvidence': test_evidence, 'evidence': evidence,
        'selectedWinner': None, 'holdoutClaim': False, 'targetAchieved': False,
        'meaning': 'Fixed shadow labels are capacity-selected closed episodes, not all opportunities. Candidate-only entries remain posthoc diagnostics.'}
    progress['completedFixedCandidateTrials'] = 39
    # Deliberately preserve the historical aggregate: this run did not rerun1026.
    progress['lastTurnClassification'] = 'progress: two fixed online entry policies;24 new complete accounts and12 byte-reused references; no automatic promotion.'
    progress['nextStage'] = decision['nextResearch']['action']
    progress['currentScopeTestEvidence'] = str(progress.get('currentScopeTestEvidence', '')) + (
        ' Online feedback:28 new distinct production tests and69 inherited tests actually rerun; historical1026 retained unchanged. Independent/summary/browser checks are separate.')
    program.update(updatedAt=timestamp, updatedDate=timestamp[:10], goalStatus='active', targetAchieved=False)
    return program


def replace_program(after):
    require(sha(PROGRAM) == BEFORE, 'Registry changed before atomic replacement')
    pending = BASE / 'program.online-feedback-pending.json'
    write_once(pending, after.read_bytes())
    os.replace(pending, PROGRAM)


def finish(plan_sha, tests_sha):
    require(sha(OUT / 'plan.json') == plan_sha and sha(OUT / 'pre-account-tests.json') == tests_sha, 'Explicit hashes differ')
    delivery_path = OUT / 'delivery.json'
    if delivery_path.exists():
        delivery = read(delivery_path)
        require(delivery['schema'] == OUT.name + '-delivery' and delivery['planSha256'] == plan_sha
                and delivery['preAccountTestsSha256'] == tests_sha and delivery['programBeforeSha256'] == BEFORE
                and delivery['candidateBatchesBefore'] == 38 and delivery['candidateBatchesAfter'] == 39
                and delivery['goalStatus'] == 'active' and delivery['targetAchieved'] is False, 'Existing delivery differs')
        pins(delivery['files'])
        manifest = read(bound(delivery['executionSourcesManifest']))
        pins(manifest['sourcePins'])
        after = OUT / 'program-after.json'
        require(sha(after) == delivery['programAfterSha256'] and sha(OUT / 'program-before.json') == BEFORE,
                'Sealed registry snapshots differ')
        require(read(after)['progress']['completedFixedCandidateTrials'] == 39
                and read(after)['progress']['currentScopeTestsPassed'] == 1026, 'Sealed counters differ')
        if sha(PROGRAM) == BEFORE:
            replace_program(after)
        require(sha(PROGRAM) == delivery['programAfterSha256'], 'Registry differs from sealed delivery')
        return {'completed': True, 'reusedDelivery': True, 'delivery': ref(delivery_path), 'goalStatus': 'active'}
    require(sha(PROGRAM) == BEFORE, 'Registry changed during experiment')
    docs, test_evidence, qa_path = validate(plan_sha, tests_sha)
    archive = archive_sources(docs)
    timestamp = (read(OUT / 'program-after.json')['updatedAt'] if (OUT / 'program-after.json').exists()
                 else datetime.now(timezone.utc).isoformat())
    names = ('plan.json', 'pre-account-tests.json', 'input-completion.json', 'input-validation.json', 'run-start.json',
             'comparison.json', 'validation.json', 'readout.json', 'readout-validation.json', 'render-receipt.json',
             'visual-review.json', 'research-decision.json')
    evidence = {name: ref(OUT / name) for name in names}
    evidence.update(browserQA=ref(qa_path), executionSourcesManifest=ref(OUT / 'execution-sources-manifest.json'))
    if (OUT / 'policy-context.json').exists():
        evidence['policyContext'] = ref(OUT / 'policy-context.json')
    program = registry_after(read(PROGRAM), timestamp, test_evidence, docs['research-decision.json'], evidence)
    write_once(OUT / 'program-before.json', PROGRAM.read_bytes())
    require(sha(OUT / 'program-before.json') == BEFORE, 'Registry backup differs')
    write_once(OUT / 'program-after.json', program)
    files = {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(OUT.rglob('*')) if p.is_file()}
    delivery = {'schema': OUT.name + '-delivery', 'completedAt': timestamp, 'files': files,
        'programBeforeSha256': BEFORE, 'programAfterSha256': sha(OUT / 'program-after.json'),
        'planSha256': plan_sha, 'preAccountTestsSha256': tests_sha,
        'candidateBatchesBefore': 38, 'candidateBatchesAfter': 39, **TRIALS, 'testEvidence': test_evidence,
        'currentScopeTestsPassed': 1026, 'evidence': evidence, 'executionSources': ref(archive),
        'executionSourcesManifest': ref(OUT / 'execution-sources-manifest.json'),
        'actualDataPeriods': list(PERIODS), 'data2026Included': False, 'holdoutClaim': False,
        'certified': False, 'targetAchieved': False, 'goalStatus': 'active', 'automaticPromotion': False}
    # The evidence inventory and snapshots exist before the only registry write.
    require(sha(PROGRAM) == BEFORE, 'Registry changed while sealing')
    write_once(delivery_path, delivery)
    replace_program(OUT / 'program-after.json')
    return {'completed': True, 'candidateBatches': 39, 'historicalCurrentScopeTestsPassed': 1026,
            'newDistinctProductionTests': 28, 'inheritedProductionTestsRerun': 69,
            'delivery': ref(delivery_path), 'programSha256': sha(PROGRAM), 'goalStatus': 'active'}


def self_test():
    checks = []
    def rejects(fn):
        try:
            fn()
        except ValueError:
            return
        raise AssertionError('Unsafe synthetic input accepted')
    rows = [dict(zip(KEYS, identity)) for identity in sorted(IDENTITIES)]
    identities(rows, KEYS, IDENTITIES, 'synthetic')
    rejects(lambda: identities(rows[:-1] + [rows[0]], KEYS, IDENTITIES, 'duplicate'))
    checks.append('All36 account identities required; same-length duplicate rejected')
    rejects(lambda: no_promotion(dict(holdoutClaim=False, certified=False, targetAchieved=True)))
    checks.append('A passing outcome cannot imply target achievement')
    plan = {'sourcePins': {}}
    commands = [{'argv': ['tests.test_research_online_feedback', 'tests.test_online_feedback_entry_runner'],
                 'exitCode': 0, 'stderr': 'Ran28 tests\nOK\n'.replace('Ran28', 'Ran 28'), 'stdout': ''},
                {'argv': ['tests.test_research_fixed_signal_account', 'tests.test_research_account_fees',
                          'tests.test_research_account_outcomes', 'tests.test_research_volatility_trail'],
                 'exitCode': 0, 'stderr': 'Ran 69 tests\nOK\n', 'stdout': ''}]
    commands += [{'argv': [name, '--self-test'], 'exitCode': 0, 'stderr': '',
                  'stdout': json.dumps({'passed': True, 'syntheticGroups': 1, 'checks': ['synthetic']})}
                 for name in (MAIN, 'scripts/summarize-online-feedback-entry.py')]
    tests = dict(passed=True, planSha256='synthetic', sourcePins={}, allChecksBeforeFirstNewAccount=True,
                 newUnitTests=28, inheritedUnitTests=69, commands=commands)
    evidence = verify_tests(tests, plan, 'synthetic')
    require(evidence['actualProductionUnitTestsRun'] == 97 and not evidence['historicalSuiteRerunThisBatch'], 'Test scope conflation')
    bad = deepcopy(tests); bad['commands'][1]['stderr'] = 'Ran 68 tests\nOK\n'
    rejects(lambda: verify_tests(bad, plan, 'synthetic'))
    checks.append('Actual28 and69 test stdout checked separately from historical1026')
    bad = deepcopy(tests); bad['commands'][3]['exitCode'] = 1
    rejects(lambda: verify_tests(bad, plan, 'synthetic'))
    checks.append('Failed synthetic command blocks completion')
    program = {'progress': {'completedFixedCandidateTrials': 38, 'currentScopeTestsPassed': 1026}, 'goalStatus': 'active'}
    frozen = deepcopy(program); decision = {'nextResearch': {'action': 'Continue research without automatic promotion.'}}
    after = registry_after(program, '2026-01-01T00:00:00+00:00', evidence, decision, {})
    require(program == frozen and after['progress']['completedFixedCandidateTrials'] == 39
            and after['progress']['currentScopeTestsPassed'] == 1026 and after['goalStatus'] == 'active'
            and after['targetAchieved'] is False, 'Registry scope/state changed')
    rejects(lambda: registry_after(after, '2026-01-01T00:00:00+00:00', evidence, decision, {}))
    checks.append('Nonmutating exactly-once38to39 transition leaves1026 historical and goal active')
    require(stamp('2026-01-01T08:00:00+08:00') == stamp('2026-01-01T00:00:00Z'), 'Timezone comparison')
    rejects(lambda: stamp('2026-01-01T00:00:00'))
    checks.append('Chronology requires explicit timezone and compares actual instants')
    return {'passed': True, 'checks': checks, 'syntheticGroups': len(checks), 'filesWritten': 0,
            'realDataReads': 0, 'accountRuns': 0, 'productionUnitIncrement': 0}


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--plan-sha256')
    parser.add_argument('--pretests-sha256')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        output = self_test()
    else:
        parser.error('Both explicit hashes required') if not (args.plan_sha256 and args.pretests_sha256) else None
        output = finish(args.plan_sha256, args.pretests_sha256)
    print(json.dumps(output, ensure_ascii=False, allow_nan=False))
