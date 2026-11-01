"""Record executed synthetic checks against the registered, frozen sources."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/reclaim-breakout-volatility-trail-v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    plan_path = OUT / 'plan.json'
    plan = json.loads(plan_path.read_text('utf-8'))
    expected = plan['preAccountTestSpec']
    assert expected['inheritedUnitTests'] == 77 and expected['newUnitTests'] > 0 and expected['commandCount'] == 6
    assert not (OUT / 'run-start.json').exists()
    assert not any(OUT.glob('*/*/*.result.json.gz'))
    assert not (OUT / 'pre-account-tests.json').exists()
    pinned = dict(plan['sourcePins'])
    pinned[Path(__file__).relative_to(ROOT).as_posix()] = sha(Path(__file__))
    for name, digest in pinned.items():
        assert sha(ROOT / name) == digest, name
    prefix = [sys.executable, '-X', 'utf8', '-B']
    commands = [
        ('inheritedProduction', [*prefix, '-m', 'unittest', 'tests.test_research_fixed_signal_account',
            'tests.test_research_structural_exits', 'tests.test_research_account_outcomes', 'tests.test_research_account_fees', 'tests.test_research_volatility_trail']),
        ('newProduction', [*prefix, '-m', 'unittest', 'tests.test_research_breakout_volatility_trail']),
        ('runner', [*prefix, 'scripts/run-reclaim-breakout-volatility-trail.py', '--self-test']),
        ('independentAccount', [*prefix, 'scripts/verify-reclaim-breakout-volatility-trail.py', '--self-test']),
        ('summarizer', [*prefix, 'scripts/summarize-reclaim-breakout-volatility-trail.py', '--self-test']),
        ('independentReadout', [*prefix, 'scripts/verify-reclaim-breakout-volatility-trail-readout.py', '--self-test']),
    ]
    started, results = now(), []
    for name, command in commands:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        record = {'name': name, 'command': command, 'exitCode': result.returncode,
                  'stdout': result.stdout, 'stderr': result.stderr}
        results.append(record)
        print(json.dumps({'check': name, 'exitCode': result.returncode}), flush=True)
    cross_command = [*prefix, 'scripts/crosscheck-reclaim-breakout-volatility-trail.py']
    cross = subprocess.run(cross_command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    supplemental = {'name': 'productionIndependentRiskCrosscheck', 'command': cross_command,
        'exitCode': cross.returncode, 'stdout': cross.stdout, 'stderr': cross.stderr}
    print(json.dumps({'check': supplemental['name'], 'exitCode': cross.returncode}), flush=True)
    assert not (OUT / 'run-start.json').exists()
    for name, digest in pinned.items():
        assert sha(ROOT / name) == digest, name
    for row, count in zip(results[:2], (77, expected['newUnitTests'])):
        assert re.search(rf'Ran {count} tests? in ', row['stderr']), 'Unexpected actual unit test count'
    for row in results[2:]:
        if row['exitCode'] == 0:
            assert json.loads(row['stdout'])['passed'] is True
    passed = all(row['exitCode'] == 0 for row in results) and cross.returncode == 0
    if cross.returncode == 0:
        assert json.loads(cross.stdout)['passed'] is True
    receipt = {'schema': 'reclaim-breakout-volatility-trail-pre-account-tests-v1',
        'startedAt': started, 'completedAt': now(), 'passed': passed,
        'planSha256': sha(plan_path), 'pins': pinned, 'commands': results,
        'supplementalChecks': [supplemental],
        'unitTests': 77 + expected['newUnitTests'], 'productionUnitIncrement': expected['newUnitTests'],
        'allChecksBeforeFirstNewAccount': True, 'newAccountExecutions': 0,
        'meaning': 'Actual inherited77 and registered new boundary unit tests; separate synthetic runner/account/readout checks are not unit-test increments.'}
    with (OUT / 'pre-account-tests.json').open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'passed': passed, 'unitTests': 77 + expected['newUnitTests'], 'productionUnitIncrement': expected['newUnitTests'],
                      'sha256': sha(OUT / 'pre-account-tests.json')}))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
