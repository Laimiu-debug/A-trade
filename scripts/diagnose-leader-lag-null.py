"""Describe an already frozen relation mask; never tune it or run accounts."""
from collections import Counter
import gzip
import importlib.util
import json
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_lag_diagnostic_util', ROOT / 'scripts/run-leader-lag-followers.py')
util = importlib.util.module_from_spec(spec)
spec.loader.exec_module(util)


def main():
    plan = util.verify_plan()
    proof = util.read(util.OUT / 'input-validation.json')
    assert proof['passed'] and proof['planSha256'] == util.sha(util.OUT / 'plan.json')
    rows, pins = [], {}
    for period in util.PERIODS:
        for board in util.BOARDS:
            folder = util.OUT / period / board
            path = folder / 'opportunities.jsonl.gz'
            receipt = util.read(folder / 'input-completion.json')
            assert util.sha(path) == receipt['files'][path.name]['sha256']
            pins[path.relative_to(ROOT).as_posix()] = util.sha(path)
            known, states = [], Counter()
            with gzip.open(path, 'rt', encoding='utf-8') as stream:
                for line in stream:
                    relation = json.loads(line)['relationship']
                    states[relation['status']] += 1
                    if relation['status'] == 'known':
                        values = tuple(relation['coefficients'][key]['correlation']
                                       for key in ('forward59', 'synchronous59', 'reverse59'))
                        assert relation['directionPassed'] == (values[0] > max(0., values[1], values[2]))
                        known.append(values)
            rows.append({'period': period, 'board': board, 'relationStates': dict(states),
                'knownRelations': len(known),
                'forwardPositive': sum(a > 0 for a, b, c in known),
                'forwardAboveSynchronous': sum(a > b for a, b, c in known),
                'forwardAboveReverse': sum(a > c for a, b, c in known),
                'directionPass': sum(a > max(0., b, c) for a, b, c in known),
                'medianSynchronous59': median(b for a, b, c in known) if known else None,
                'medianForward59': median(a for a, b, c in known) if known else None,
                'maximumForwardMinusSynchronous': max((a - b for a, b, c in known), default=None)})
    output = {'schema': 'leader-lag-frozen-mask-diagnostic-v1', 'recordedAt': util.now(),
        'planSha256': util.sha(util.OUT / 'plan.json'), 'inputValidation': util.ref(util.OUT / 'input-validation.json'),
        'script': util.ref(Path(__file__)), 'inputPins': pins, 'rows': rows,
        'knownRelations': sum(row['knownRelations'] for row in rows),
        'forwardAboveSynchronous': sum(row['forwardAboveSynchronous'] for row in rows),
        'directionPass': sum(row['directionPass'] for row in rows),
        'interpretation': 'Description of the frozen Top5 population selected for high synchronous correlation. It does not test all possible lead-lag definitions or establish causal influence.',
        'parameterChanges': 0, 'newSignals': 0, 'newAccounts': 0,
        'certified': False, 'targetAchieved': False}
    destination = util.OUT / 'relation-diagnostic.json'
    util.write(destination, output)
    util.emit(util.ref(destination))


if __name__ == '__main__':
    main()
