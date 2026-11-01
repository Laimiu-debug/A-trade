"""Save reviewed diagnostic evidence without promoting a strategy trial."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'artifacts/strategy-search'
OUT = BASE/'board-environment-context-v1'
PROGRAM = BASE/'program.json'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def ref(path):
    return {'path': Path(path).relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def check(item):
    path = (ROOT/item['path']).resolve()
    assert path.is_relative_to(ROOT) and sha(path) == item['sha256'], item['path']
    return path


def finish():
    plan = read(OUT/'plan.json')
    before = plan['previousProgram']['sha256']
    if (OUT/'delivery.json').exists():
        delivery = read(OUT/'delivery.json')
        for name, digest in delivery['files'].items():
            assert sha(ROOT/name) == digest, name
        after = sha(OUT/'program-after.json')
        assert after == delivery['programAfterSha256'] and sha(PROGRAM) in (before, after)
        if sha(PROGRAM) == before:
            pending = BASE/'program.board-environment-pending.json'
            pending.write_bytes((OUT/'program-after.json').read_bytes())
            os.replace(pending, PROGRAM)
        return {'completed': True, 'recovered': True, 'delivery': ref(OUT/'delivery.json')}
    assert sha(PROGRAM) == before and not (OUT/'program-after.json').exists()
    proof, report, decision, visual = [read(OUT/name) for name in
        ('validation.json', 'readout.json', 'research-decision.json', 'visual-review.json')]
    assert proof['passed'] and proof['readoutSha256'] == sha(OUT/'readout.json')
    assert proof['planSha256'] == sha(OUT/'plan.json')
    for mapping in (plan['sourcePins'], plan['inputPins'], proof['inputPins']):
        for name, digest in mapping.items():
            assert sha(ROOT/name) == digest, name
    for item in proof['sourcePins']:
        check(item)
    for boards in report['artifacts'].values():
        for artifacts in boards.values():
            for item in artifacts.values():
                check(item)
    assert report['dailyCount'] == 2907 and len(report['phaseRows']) == 48
    assert len(report['epochRows']) == 9 and len(report['continuousAccountRows']) == 96
    assert report['newAccounts'] == report['newStrategyTrials'] == 0
    assert not any(report[k] for k in ('certified', 'targetAchieved', 'holdoutClaim'))
    assert decision['readout'] == ref(OUT/'readout.json') and decision['validation'] == ref(OUT/'validation.json')
    assert decision['targetAchieved'] is False and decision['nextResearch']['action']
    amount = read(OUT/'amount-level-context.json')
    amount_check = read(OUT/'amount-level-independent-check.json')
    assert amount['verification']['passed'] and amount_check['passed']
    assert amount_check['amountLevel'] == ref(OUT/'amount-level-context.json')
    assert amount_check['sourceReadoutSha256'] == sha(OUT/'readout.json')
    assert amount_check['sourceValidationSha256'] == sha(OUT/'validation.json')
    for item in amount_check['sourcePins'] + amount_check['inputPins']:
        check(item)
    assert decision['amountLevel'] == ref(OUT/'amount-level-context.json')
    assert amount['inputs']['readout'] == ref(OUT/'readout.json')
    assert amount['inputs']['audit'] == ref(OUT/'validation.json')
    assert [len(amount[k]) for k in ('dailyRows', 'epochRows', 'comparisonRows')] == [1452, 6, 3]
    assert amount['posthoc'] and amount['newStrategyTrials'] == amount['newAccounts'] == 0
    check(amount['extractor'])
    for item in amount['unit']['sourceCodeRefs']:
        check(item)
    assert visual['passed']
    qa_path = check({'path': visual['qaPath'], 'sha256': visual['qaSha256']})
    qa = read(qa_path)
    assert qa['passed'] and qa['renderReceiptSha256'] == sha(OUT/'render-receipt.json')
    assert qa['scriptSha256'] == sha(ROOT/'scripts/verify-board-environment-context-preview.cjs')
    render = read(OUT/'render-receipt.json')
    assert render['builderSha256'] == sha(ROOT/'scripts/render-board-environment-context.py')
    for name, digest in render['inputPins'].items():
        assert sha(OUT/name) == digest, name
    for group in ('fragments', 'payloads'):
        for item in render[group].values():
            assert sha(OUT/item['path']) == item['sha256']
    for item in visual['images']:
        check(item)
    check(visual['axisSupplement'])
    amount_visual = read(OUT/'amount-visual-review.json')
    assert amount_visual['passed']
    amount_qa = read(check(amount_visual['qa']))
    assert amount_qa['passed']
    for item in amount_visual['bindings'] + amount_visual['images']:
        check(item)
    (OUT/'program-before.json').write_bytes(PROGRAM.read_bytes())
    program = read(PROGRAM)
    assert program['goalStatus'] == 'active'
    assert program['progress']['completedFixedCandidateTrials'] == 38
    assert program['progress']['currentScopeTestsPassed'] == 1026
    program['updatedAt'] = datetime.now(timezone.utc).isoformat()
    program['progress']['lastTurnClassification'] = 'progress: six frozen board sources now have independently replayed causal breadth, amount and endpoint/continuous rank-retention observations, all 2907 source-days and 48 phases, linked to 96 unchanged account phase rows; a separately checked 1452-day-row posthoc amount-level supplement distinguishes persistent activity from rolling ratios; no new trading trial.'
    program['progress']['nextStage'] = decision['nextResearch']['action']
    program['progress']['boardEnvironmentContext'] = {
        'status': 'completed-descriptive-development-diagnostic', 'plan': ref(OUT/'plan.json'),
        'readout': ref(OUT/'readout.json'), 'validation': ref(OUT/'validation.json'),
        'decision': ref(OUT/'research-decision.json'), 'sourceDays': 2907, 'phaseRows': 48,
        'amountLevel': ref(OUT/'amount-level-context.json'),
        'amountIndependentCheck': ref(OUT/'amount-level-independent-check.json'),
        'continuousAccountRows': 96, 'newDiagnosticUnitTestsPassed': 16,
        'newStrategyTrials': 0, 'newAccounts': 0, 'targetAchieved': False}
    write(OUT/'program-after.json', program)
    names = list(plan['sourcePins']) + [
        'docs/board-environment-context-v1.json', 'scripts/verify-board-environment-context.py',
        'scripts/render-board-environment-context.py', 'scripts/verify-board-environment-context-preview.cjs',
        'scripts/summarize-board-environment-amount-level.py',
        'scripts/render-board-amount-level-sources.py', 'scripts/verify-board-amount-level-preview.cjs',
        Path(__file__).resolve().relative_to(ROOT).as_posix()]
    names += [item['path'] for item in amount['unit']['sourceCodeRefs']]
    names += [item['path'] for item in amount_check['sourcePins']]
    for item in proof['sourcePins']:
        names.append(item['path'])
    names = sorted(set(names))
    pins = {name: sha(ROOT/name) for name in names}
    archive = OUT/'execution-sources.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as stream:
        for name in names:
            stream.write(ROOT/name, name)
    with zipfile.ZipFile(archive) as stream:
        assert all(hashlib.sha256(stream.read(name)).hexdigest() == digest for name, digest in pins.items())
    write(OUT/'execution-sources-manifest.json', {'sourcePins': pins, 'archive': ref(archive),
        'analysisSourcesFrozenBeforeExtraction': plan['sourcePins'],
        'auditAndPresentationArchivedAtDelivery': True})
    files = {p.relative_to(ROOT).as_posix(): sha(p) for p in OUT.rglob('*') if p.is_file()}
    write(OUT/'delivery.json', {'schema': OUT.name+'-delivery', 'completedAt': datetime.now(timezone.utc).isoformat(),
        'files': files, 'programBeforeSha256': before, 'programAfterSha256': sha(OUT/'program-after.json'),
        'candidateBatches': 38, 'newStrategyTrials': 0, 'newAccounts': 0,
        'goalStatus': 'active', 'targetAchieved': False})
    pending = BASE/'program.board-environment-pending.json'
    with pending.open('xb') as stream:
        stream.write((OUT/'program-after.json').read_bytes())
    os.replace(pending, PROGRAM)
    return {'completed': True, 'delivery': ref(OUT/'delivery.json'), 'goalStatus': 'active'}


if __name__ == '__main__':
    print(json.dumps(finish(), ensure_ascii=False))
