"""Apply explicit stock/ordinary-disclosure scope to the frozen source collector."""
from argparse import ArgumentParser
from copy import deepcopy
import importlib.util
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_sse_source_collector', ROOT / 'scripts/collect-public-sse-disclosures.py')
C = importlib.util.module_from_spec(spec); sys.modules[spec.name] = C; spec.loader.exec_module(C)
U = C.U
OLD = C.BASE
C.BASE = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1/sse-main-2024q4-002'
original_normalize = C.normalize


def is_target(row):
    return (row.get('secType') == 'A' and str(row.get('secCode', '')).startswith('6')
            and not str(row['secCode']).startswith(('688', '689')) and str(row.get('refType')) not in ('41', '42'))


def stock_normalize(raw, requested_date):
    indices = [i for i, row in enumerate(raw['pageHelp']['data']) if is_target(row)]
    projected = {**raw, 'pageHelp': {**raw['pageHelp'], 'data': [raw['pageHelp']['data'][i] for i in indices]}}
    events, seats = original_normalize(projected, requested_date)
    for event in events: event['sourceRow'] = indices[event['sourceRow']]
    return events, seats


def register():
    if C.BASE.exists(): raise ValueError('Preserve existing corrected census')
    old = U.read(OLD / 'plan.json')
    for key in ('collector', 'calendar', 'officialPageClient'): U.bound(old[key])
    C.BASE.mkdir()
    plan = deepcopy(old)
    plan.update({'schema': 'sse-main-Astock-ordinary-disclosure-source-census-v2', 'registeredAt': U.now(),
        'collector': U.ref(Path(__file__)), 'inheritedCollector': old['collector'], 'parentPlan': U.ref(OLD / 'plan.json'),
        'scope': 'Shanghai main-board A stocks and ordinary trading disclosures only. Full native responses preserved; funds, B shares, STAR and financing/lending member reports excluded explicitly.',
        'scopeRule': "secType=A; code starts6 excluding688/689; refType not41/42",
        'schemaEvidence': U.ref(OLD / 'raw/2024-10-08.json'),
        'schemaFinding': 'Native query includes ETF financing report561160/ref41 with only buying members. It is not an ordinary A-stock brokerage buy/sell ranking. Missing selling data must not be turned into zeros.',
        'workspaceJsonBefore': U.workspace_snapshot()})
    U.write(C.BASE / 'plan.json', plan)
    target = C.BASE / 'raw'; target.mkdir()
    for source in sorted((OLD / 'raw').glob('*.json')):
        shutil.copyfile(source, target / source.name)
    U.emit({'registered': U.ref(C.BASE / 'plan.json'), 'cachedSourceDaysReused': len(list(target.glob('????-??-??.json'))), 'newStrategyTrials': 0})


def finalize():
    C.finalize()
    plan = U.read(C.BASE / 'plan.json')
    excluded = []
    native_rows = 0
    for day in plan['dates']:
        raw = U.read(C.BASE / 'raw' / (day + '.json'))['pageHelp']['data']
        native_rows += len(raw)
        for i, row in enumerate(raw):
            if not is_target(row): excluded.append({'date': day, 'sourceRow': i, 'code': row.get('secCode'),
                'securityType': row.get('secType'), 'refType': row.get('refType'), 'reason': 'outsideFrozenOrdinaryMainAStockScope'})
    validation = U.read(C.BASE / 'validation.json')
    if native_rows != validation['events'] + len(excluded): raise ValueError('Source scope reconciliation failed')
    U.write(C.BASE / 'exclusions.json', excluded)
    U.write(C.BASE / 'scope-validation.json', {'passed': True, 'plan': U.ref(C.BASE / 'plan.json'),
        'inheritedCollector': plan['inheritedCollector'], 'normalizationValidation': U.ref(C.BASE / 'validation.json'),
        'nativeResponseRows': native_rows, 'includedStockEvents': validation['events'], 'excludedRows': len(excluded),
        'exclusions': U.ref(C.BASE / 'exclusions.json'), 'allSourceRowsAccountedFor': True, 'goalAchieved': False})
    U.emit({'scopeValidation': U.ref(C.BASE / 'scope-validation.json'), 'nativeRows': native_rows, 'excluded': len(excluded)})


if __name__ == '__main__':
    parser = ArgumentParser(); parser.add_argument('stage', choices=('register', 'run', 'finalize'))
    stage = parser.parse_args().stage
    if stage == 'register': register()
    else:
        U.bound(U.read(C.BASE / 'plan.json')['inheritedCollector'])
        C.normalize = stock_normalize
        if stage == 'run': C.run()
        else: finalize()
