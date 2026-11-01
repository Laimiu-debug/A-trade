"""Raw-first collection; extract only the official client's brokerage table types."""
from argparse import ArgumentParser
from copy import deepcopy
import importlib.util
from pathlib import Path
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_sse_brokerage_base', ROOT / 'scripts/collect-public-sse-disclosures.py')
C = importlib.util.module_from_spec(spec); sys.modules[spec.name] = C; spec.loader.exec_module(C)
U = C.U
PARENT = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1/sse-main-2024q4-002'
C.BASE = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1/sse-main-2024q4-003'
original_normalize = C.normalize


def table_types():
    text = C.CLIENT.read_text('utf-8')
    first = text.index('function dataPackage('); last = text.index('palteType == "star"', first)
    types = {}
    for match in re.finditer(r'disclosureJson\["([^"]+)"\]\s*=\s*\{(.*?)\n    \};', text[first:last], re.S):
        kind = re.search(r'infors:\s*"([^"]+)"', match.group(2))
        if kind: types[match.group(1)] = kind.group(1)
    if types.get('41') != 'mainmember' or types.get('Z5') != 'investor' or types.get('11') != 'mainsales':
        raise ValueError('Official client category contract not recognized')
    return types


def is_target(row):
    return (row.get('secType') == 'A' and str(row.get('secCode', '')).startswith('6')
            and not str(row['secCode']).startswith(('688', '689')) and table_types().get(str(row.get('refType'))) == 'mainsales')


def brokerage_normalize(raw, day):
    indices = [i for i, row in enumerate(raw['pageHelp']['data']) if is_target(row)]
    projected = {**raw, 'pageHelp': {**raw['pageHelp'], 'data': [raw['pageHelp']['data'][i] for i in indices]}}
    events, seats = original_normalize(projected, day)
    for event in events: event['sourceRow'] = indices[event['sourceRow']]
    return events, seats


def register():
    if C.BASE.exists(): raise ValueError('Preserve previous source attempts')
    parent = U.read(PARENT / 'plan.json')
    for key in ('collector', 'inheritedCollector', 'calendar', 'officialPageClient'): U.bound(parent[key])
    C.BASE.mkdir(); (C.BASE / 'raw').mkdir()
    plan = deepcopy(parent)
    plan.update({'schema': 'sse-main-Astock-brokerage-source-census-v3', 'registeredAt': U.now(),
        'collector': U.ref(Path(__file__)), 'inheritedCollector': parent['inheritedCollector'],
        'parentPlan': U.ref(PARENT / 'plan.json'), 'officialTableTypes': table_types(),
        'scopeRule': 'Main A stock and official dataPackage(main,refType).infors=mainsales; raw source retains every native query row.',
        'sourceSchemaLearning': 'Native daily query mixes brokerage rankings, financing/lending member reports and investor-category serious-abnormality tables. Their fields and currency scales differ; do not coerce them into one brokerage table.',
        'schemaEvidence': U.ref(PARENT / 'raw/2024-10-09.json'), 'workspaceJsonBefore': U.workspace_snapshot()})
    U.write(C.BASE / 'plan.json', plan)
    for source in sorted((PARENT / 'raw').glob('*.json')): shutil.copyfile(source, C.BASE / 'raw' / source.name)
    U.emit({'plan': U.ref(C.BASE / 'plan.json'), 'dates': len(plan['dates']), 'officialTypes': len(table_types())})


def run():
    plan = U.read(C.BASE / 'plan.json')
    for key in ('collector', 'inheritedCollector', 'calendar', 'officialPageClient'): U.bound(plan[key])
    for index, day in enumerate(plan['dates'], 1):
        raw = C.fetch_json(C.LIST_URL, {**plan['listParams'], 'tradeDate': day.replace('-', '')}, C.BASE / 'raw' / (day + '.json'))
        rows = raw['pageHelp']['data']
        if raw.get('actionErrors') or raw.get('fieldErrors') or not isinstance(rows, list): raise ValueError('Source error')
        if any(C.date_value(row['tradeDate']) != day for row in rows): raise ValueError('Source date mismatch')
        U.emit({'date': day, 'index': index, 'nativeRows': len(rows)})
        time.sleep(0.5)


def finalize():
    C.normalize = brokerage_normalize
    C.finalize()
    plan = U.read(C.BASE / 'plan.json'); excluded = []; native_count = 0
    for day in plan['dates']:
        rows = U.read(C.BASE / 'raw' / (day + '.json'))['pageHelp']['data']; native_count += len(rows)
        for index, row in enumerate(rows):
            if not is_target(row): excluded.append({'date': day, 'sourceRow': index, 'code': row.get('secCode'),
                'securityType': row.get('secType'), 'refType': row.get('refType'),
                'officialTableKind': table_types().get(str(row.get('refType')), 'unknown'), 'reason': 'outsideFrozenMainAStockBrokerageScope'})
    validation = U.read(C.BASE / 'validation.json')
    if native_count != len(excluded) + validation['events']: raise ValueError('Raw row accounting mismatch')
    U.write(C.BASE / 'exclusions.json', excluded)
    U.write(C.BASE / 'scope-validation.json', {'passed': True, 'plan': U.ref(C.BASE / 'plan.json'),
        'normalizationValidation': U.ref(C.BASE / 'validation.json'), 'nativeResponseRows': native_count,
        'includedStockBrokerageEvents': validation['events'], 'excludedRows': len(excluded),
        'exclusions': U.ref(C.BASE / 'exclusions.json'), 'allSourceRowsAccountedFor': True, 'goalAchieved': False})
    U.emit({'scopeValidation': U.ref(C.BASE / 'scope-validation.json'), 'nativeRows': native_count, 'excludedRows': len(excluded)})


if __name__ == '__main__':
    parser = ArgumentParser(); parser.add_argument('stage', choices=('register', 'run', 'finalize'))
    stage = parser.parse_args().stage
    if stage != 'register': U.bound(U.read(C.BASE / 'plan.json')['inheritedCollector'])
    globals()[stage]()
