"""Compare real synthetic executions with the separately authored risk replay."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.research_breakout_volatility_trail import BreakoutVolatilityTrailAccount


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run():
    fixture = load('_breakout_cross_fixture', 'tests/test_research_volatility_trail.py')
    audit = load('_breakout_cross_audit', 'scripts/verify-reclaim-breakout-volatility-trail.py')
    cases = ('ordinaryFixed', 'entryDayActivation', 'lateActivation', 'unknownAnchor',
             'fixedExitBeforeBreakout', 'activeExit', 'entryReferenceChange',
             'heldReferenceChange', 'partialRetry', 'reentry', 'invalidCurrent', 'initialStopPriority')
    checked = []
    for case in cases:
        data = fixture.fixture()
        signals = fixture.schedule(data)
        days = data['calendar']
        security = data['securities'][0]
        if case in ('entryDayActivation', 'activeExit', 'heldReferenceChange', 'partialRetry', 'reentry', 'invalidCurrent', 'initialStopPriority'):
            fixture.set_bar(data, 26, close=10.4)
        if case == 'lateActivation':
            fixture.set_bar(data, 30, close=10.4)
        elif case == 'unknownAnchor':
            fixture.set_bar(data, 20, volume=0)
        elif case == 'fixedExitBeforeBreakout':
            for index in range(5, 25):
                fixture.set_bar(data, index, close=11.)
            fixture.set_bar(data, 26, close=10.6)
            fixture.set_bar(data, 27, close=10.)
            fixture.set_bar(data, 28, close=9.53)
        elif case == 'entryReferenceChange':
            security['precloseByDate'] = {days[26]: 5.}
            for index in range(26, len(days)):
                fixture.set_bar(data, index, opening=5., close=5.2, high=5.25, low=4.95)
        elif case == 'heldReferenceChange':
            fixture.set_bar(data, 27, close=10.5)
            security['precloseByDate'] = {days[28]: 5.25}
            for index in range(28, len(days)):
                fixture.set_bar(data, index, opening=5.3, close=5.3, high=5.35, low=5.25)
        elif case in ('activeExit', 'partialRetry', 'reentry'):
            fixture.set_bar(data, 27, close=9.99)
            if case == 'partialRetry':
                for index in range(27, 34):
                    fixture.set_bar(data, index, volume=3000)
                fixture.set_bar(data, 28, volume=0)
            if case == 'reentry':
                signals = fixture.schedule(data, (25, 31))
        elif case == 'invalidCurrent':
            fixture.set_bar(data, 27, volume=0)
        elif case == 'initialStopPriority':
            fixture.set_bar(data, 27, close=9.4)
        before = deepcopy((data, signals))
        # Reuse only the original synthetic inputs/config/fees, never its risk oracle.
        account = BreakoutVolatilityTrailAccount(data, signals, fixture.configuration(data),
            entry_weight=.2, fee_policy=fixture.LegacyFeePolicy())
        account.step(20000)
        result, checkpoint = account.result(), account.checkpoint()
        assert before == (data, signals), 'Synthetic input mutated'
        proof = audit.daily_risk_reference(result, checkpoint, data, 'breakoutVolatilityTrail')
        detail = result['research']['breakoutVolatilityTrail']
        if case == 'ordinaryFixed':
            assert detail['activatedEntryCount'] == 0
        elif case == 'unknownAnchor':
            assert detail['unknownAnchorEntryCount'] == 1 and detail['activatedEntryCount'] == 0
        elif case in ('entryDayActivation', 'entryReferenceChange'):
            assert detail['observations'][0]['activatedToday'] and not detail['observations'][0]['volatilityCandidateEvaluated']
        elif case == 'lateActivation':
            assert any(r['activatedToday'] and r['date'] == days[30] for r in detail['observations'])
        elif case == 'fixedExitBeforeBreakout':
            assert any(r['reason'] == 'trailingStop' for r in result['trades'] if r['side'] == 'sell')
        elif case in ('activeExit', 'partialRetry', 'reentry'):
            assert any(r['reason'] == 'breakoutVolatilityTrail' for r in result['trades'] if r['side'] == 'sell')
        elif case == 'initialStopPriority':
            assert any(r['reason'] == 'stopLoss' for r in result['trades'] if r['side'] == 'sell')
        checked.append({'case': case, 'passed': True, 'entries': detail['entryCount'],
            'activations': detail['activatedEntryCount'], 'observations': len(detail['observations'])})
    return {'passed': True, 'checks': checked, 'syntheticAccounts': len(cases),
            'actualDataRead': False, 'realAccountExecutions': 0, 'filesWritten': 0,
            'productionUnitIncrement': 0}


if __name__ == '__main__':
    print(json.dumps(run()))
