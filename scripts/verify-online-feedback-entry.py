"""Independent arithmetic audit for online feedback entry; never imports the model.

The production model/runner schemas are being frozen separately.  The reference
math uses an SPD Cholesky solve, rather than the production elimination routine.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import gc
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/online-feedback-entry-v1'
PARENT = ROOT / 'artifacts/strategy-search/reclaim-volatility-trail-v1'
ENVIRONMENT = ROOT / 'artifacts/strategy-search/board-environment-context-v1'
PERIODS, BOARDS = ('2022-2023', '2024-2025'), ('main', 'chinext', 'star')
EXITS, MODES = ('trail10', 'volatilityTrail'), ('intercept', 'ridge')
FEATURE_NAMES = ('breadth20', 'amountRatio', 'continuousRetentionLower')
DEPENDENCIES = {
    'scripts/verify-reclaim-exit-mechanism.py': '96ef23022f1a8a515ecf94eabdab8696057bcb166a2cd309fe43f101a507cd89',
    'scripts/verify-reclaim-volatility-trail.py': '81bd4a8681f31d9a1dc5c5732ed8cb7d763e3c779b58492098bbc3957fd0bb08',
    'scripts/verify-reclaim-exit-transfer.py': '5437d5e9421f65f935d211c553e95c473cf40fddafaf09b12694b3bf3548913a',
    'scripts/verify-board-coverage-reclaim.py': '8d95ec533c901c7e47b6c23beb6567ae6760900d7be9e6038fff0d58853b6c29',
    'scripts/verify-peer-amount-persistence.py': 'cfaabb0262a33c7c6a5776f4fb54b7d0298df104abf8374ac87313ec6cccd026',
    'scripts/verify-relative-repair-cost-stress.py': 'df00a1573933b4624563b4db0f451a731449c55c180b8b6793f5967b147e762a',
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def near(actual, expected, label, *, tolerance=1e-11):
    require(finite(actual) and finite(expected)
            and math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance), label)


def same(actual, expected, label='value'):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and set(actual) == set(expected), label + ': keys')
        for key in expected:
            same(actual[key], expected[key], label + '.' + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + ': length')
        for i, value in enumerate(expected):
            same(actual[i], value, f'{label}[{i}]')
    elif finite(expected):
        near(actual, expected, label)
    else:
        require(type(actual) is type(expected) and actual == expected, label + ': value')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz')
          else Path(path).open(encoding='utf-8')) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def ref(path):
    return {'path': Path(path).resolve().relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def bound(item):
    path = (ROOT / item['path']).resolve()
    require(path.is_relative_to(ROOT) and sha(path) == item['sha256'], 'Changed bound file: ' + item['path'])
    return path


def independent(name):
    path = ROOT / name
    require(name in DEPENDENCIES and sha(path) == DEPENDENCIES[name], 'Independent helper changed: ' + name)
    spec = importlib.util.spec_from_file_location('_online_independent_' + path.stem.replace('-', '_'), path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def solve_spd3(matrix, rhs):
    """Independent 3x3 Cholesky factorization and triangular substitutions."""
    require(len(matrix) == 3 and all(len(row) == 3 for row in matrix)
            and len(rhs) == 3, 'Three-dimensional normal equation required')
    require(all(finite(value) for row in matrix for value in row)
            and all(finite(value) for value in rhs), 'Nonfinite normal equation')
    for i in range(3):
        for j in range(i):
            near(matrix[i][j], matrix[j][i], 'Normal matrix is not symmetric')
    lower = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(i + 1):
            residual = matrix[i][j] - math.fsum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                require(residual > 0, 'Normal matrix is not positive definite')
                lower[i][j] = math.sqrt(residual)
            else:
                lower[i][j] = residual / lower[j][j]
    intermediate = []
    for i in range(3):
        intermediate.append((rhs[i] - math.fsum(lower[i][j] * intermediate[j] for j in range(i))) / lower[i][i])
    solution = [0.0] * 3
    for i in range(2, -1, -1):
        solution[i] = (intermediate[i] - math.fsum(lower[j][i] * solution[j] for j in range(i + 1, 3))) / lower[i][i]
    for row, value in zip(matrix, rhs):
        near(math.fsum(a * b for a, b in zip(row, solution)), value, 'Independent linear-system residual')
    return solution


def fit_reference(samples, current_features):
    """Inputs: formationDate, features[3], target(decimal); no production types."""
    require(bool(samples), 'Cannot fit an empty label set')
    require(len(current_features) == 3 and all(finite(x) for x in current_features), 'Current features invalid')
    for row in samples:
        require(isinstance(row['formationDate'], str) and len(row['features']) == 3
                and all(finite(x) for x in row['features']) and finite(row['target']), 'Training sample invalid')
    per_day = Counter(row['formationDate'] for row in samples)
    weights = [1.0 / per_day[row['formationDate']] for row in samples]
    weight_sum = math.fsum(weights)
    near(weight_sum, len(per_day), 'Each formation date has total weight one')
    means = [math.fsum(w * row['features'][j] for w, row in zip(weights, samples)) / weight_sum for j in range(3)]
    scales = []
    for j in range(3):
        values = [row['features'][j] for row in samples]
        if all(x == values[0] for x in values):
            means[j] = values[0]
            scales.append(0.0)
        else:
            variance = math.fsum(w * (row['features'][j] - means[j]) ** 2
                                 for w, row in zip(weights, samples)) / weight_sum
            require(finite(variance) and variance > 0, 'Invalid nonconstant weighted variance')
            scales.append(math.sqrt(variance))
    standardized = [[(row['features'][j] - means[j]) / scales[j] if scales[j] else 0.0
                     for j in range(3)] for row in samples]
    current_z = [(current_features[j] - means[j]) / scales[j] if scales[j] else 0.0 for j in range(3)]
    targets = [row['target'] for row in samples]
    y_mean = targets[0] if all(y == targets[0] for y in targets) else math.fsum(w * row['target'] for w, row in zip(weights, samples)) / weight_sum
    matrix = [[math.fsum(w * z[i] * z[j] for w, z in zip(weights, standardized)) / weight_sum
               + (1.0 if i == j else 0.0) for j in range(3)] for i in range(3)]
    rhs = [math.fsum(w * z[j] * (row['target'] - y_mean)
                    for w, z, row in zip(weights, standardized, samples)) / weight_sum for j in range(3)]
    slopes = solve_spd3(matrix, rhs)
    prediction = y_mean + math.fsum(b * z for b, z in zip(slopes, current_z))
    require(finite(prediction), 'Prediction is not finite')
    return {'distinctFormationDays': len(per_day), 'weights': weights, 'weightSum': weight_sum,
            'means': means, 'scales': scales, 'standardized': standardized, 'currentStandardized': current_z,
            'targetMean': y_mean, 'normalMatrix': matrix, 'rhs': rhs, 'slopes': slopes,
            'predictions': {'intercept': y_mean, 'ridge': prediction},
            'allow': {'intercept': y_mean > 0, 'ridge': prediction > 0}}


def label_window_reference(calendar, formation_date, episodes, features_by_formation):
    """A=T-1, mature labels only, 126 source slots inclusive of A.

    Future final exits/returns are never copied into the visible immature rows.
    Episode targets are not read until their final exit is already known at A.
    """
    index = {day: i for i, day in enumerate(calendar)}
    require(len(index) == len(calendar) and calendar == sorted(calendar), 'Calendar identity/order')
    require(formation_date in index, 'Formation outside calendar')
    ai = index[formation_date] - 1
    as_of = calendar[ai] if ai >= 0 else None
    window = calendar[max(0, ai - 125):ai + 1] if ai >= 0 else []
    window_set = set(window)
    samples, immature, missing_x = [], [], []
    seen = set()
    for row in episodes:
        identity = row['formationId']
        require(identity not in seen, 'Duplicate shadow episode')
        seen.add(identity)
        entry = row['entryDate']
        require(entry in index and row['formationDate'] in index and row['formationDate'] < entry,
                'Shadow entry must follow original formation')
        if as_of is None or entry > as_of:
            continue
        exit_day = row['exitDate']
        if exit_day is None or exit_day > as_of:
            immature.append({'formationId': identity, 'formationDate': row['formationDate'],
                             'entryDate': entry, 'ageSessions': ai - index[entry] + 1})
            continue
        require(exit_day in index and exit_day > entry, 'Known final exit must follow entry')
        if exit_day not in window_set:
            continue
        features = features_by_formation.get(row['formationDate'])
        if features is None or len(features) != 3 or not all(finite(x) for x in features):
            missing_x.append(identity)
            continue
        require(finite(row['netReturn']), 'Mature label needs a finite net return')
        samples.append({'formationId': identity, 'formationDate': row['formationDate'],
                        'entryDate': entry, 'exitDate': exit_day, 'features': list(features), 'target': row['netReturn']})
    return {'asOfDate': as_of, 'windowDates': window, 'samples': samples,
            'unmatured': immature, 'missingFeatureIds': missing_x}


def feature_reference(day, environment, calendar, indexes):
    if day not in environment:
        return [None, None, None], ['missingEnvironmentRow']
    row = environment[day]
    prior = calendar[indexes[day] - 1] if indexes[day] else None
    require(row['asOfDate'] == prior, 'Feature snapshot is not exact T-1')
    paths = [('price20', 'positiveReturn20Breadth'), ('amount', 'medianRelativeToOwnPrior20'),
             ('retention', 'continuous', 'retentionLower')]
    values, reasons = [], []
    for name, path in zip(FEATURE_NAMES, paths):
        value = row
        for key in path:
            if value is None:
                break
            require(isinstance(value, dict), 'Feature container invalid')
            value = value.get(key)
        if value is None:
            reasons.append(name + 'Unavailable')
        else:
            require(finite(value) and (value > 0 if name == 'amountRatio' else 0 <= value <= 1), 'Feature unit/value invalid')
        values.append(value)
    require(prior is not None or all(x is None for x in values), 'No prior session exists for known features')
    return values, reasons


def reference_build(calendar, target_dates, environment_daily_rows, shadow_episodes):
    require(calendar == sorted(set(calendar)) and bool(calendar), 'Calendar identity/order')
    require(all(date.fromisoformat(d).isoformat() == d for d in calendar), 'Calendar ISO dates')
    indexes = {d: i for i, d in enumerate(calendar)}
    require(target_dates == sorted(set(target_dates)) and set(target_dates).issubset(indexes), 'Target calendar subset')
    environment = {r['formationDate']: r for r in environment_daily_rows}
    require(len(environment) == len(environment_daily_rows) and set(environment).issubset(indexes), 'Environment identities')
    ordered = sorted(shadow_episodes, key=lambda r: (r['formationDate'], r['formationId']))
    require(len({r['formationId'] for r in ordered}) == len(ordered), 'Shadow identity duplication')
    for row in ordered:
        require(isinstance(row['formationId'], str) and bool(row['formationId'].strip()), 'Shadow identity')
        require(row['formationDate'] in indexes and row['entryDate'] in indexes and row['formationDate'] < row['entryDate'], 'Shadow formation/entry clock')
        require((row['exitDate'] is None and row['netReturn'] is None) or
                (row['exitDate'] in indexes and row['exitDate'] > row['entryDate'] and finite(row['netReturn'])), 'Shadow final-exit clock/label')
    rows = []
    for day in target_dates:
        ai = indexes[day] - 1
        as_of = calendar[ai] if ai >= 0 else None
        window = calendar[max(0, ai - 125):ai + 1] if ai >= 0 else []
        current, current_reasons = feature_reference(day, environment, calendar, indexes)
        known_entries = [r for r in ordered if as_of is not None and r['entryDate'] <= as_of]
        matured = [r for r in known_entries if r['exitDate'] is not None and r['exitDate'] <= as_of]
        within = [r for r in matured if r['exitDate'] in window]
        immature = [{'formationId': r['formationId'], 'formationDate': r['formationDate'], 'entryDate': r['entryDate'],
                     'ageSlots': ai - indexes[r['entryDate']] + 1} for r in known_entries
                    if r['exitDate'] is None or r['exitDate'] > as_of]
        labels, rejected = [], []
        for ep in within:
            features, reasons = feature_reference(ep['formationDate'], environment, calendar, indexes)
            identity = {k: ep[k] for k in ('formationId', 'formationDate', 'entryDate', 'exitDate')}
            require(ep['formationDate'] < day, 'Training formation is not in the past')
            if reasons:
                rejected.append({**identity, 'reasons': reasons})
            else:
                labels.append({**identity, 'netReturn': ep['netReturn'], 'features': features})
        counts = Counter(r['formationDate'] for r in labels)
        for label in labels:
            n = counts[label['formationDate']]
            label.update(formationDayCount=n, weight=1.0/n)
        summary = dict(episodeCount=len(labels), formationDayCount=len(counts), weightSum=math.fsum(r['weight'] for r in labels),
                       featureMeans=None, featureStdDevs=None, zeroVarianceColumns=[], labelMean=None,
                       normalMatrix=None, rhs=None, slopes=None)
        fitted = None
        if labels:
            samples = [{'formationDate': r['formationDate'], 'features': r['features'], 'target': r['netReturn']} for r in labels]
            fitted = fit_reference(samples, current if not current_reasons else [0., 0., 0.])
            summary.update(featureMeans=fitted['means'], featureStdDevs=fitted['scales'], labelMean=fitted['targetMean'],
                           zeroVarianceColumns=[n for n, s in zip(FEATURE_NAMES, fitted['scales']) if s == 0])
        fallback = 'insufficientTrainingFormationDays' if len(counts) < 20 else 'currentFeaturesUnknown' if current_reasons else None
        if fallback:
            models = {m: dict(status='fallback', reason=fallback, prediction=None, allow=True) for m in MODES}
        else:
            summary.update(normalMatrix=fitted['normalMatrix'], rhs=fitted['rhs'], slopes=fitted['slopes'])
            models = {m: dict(status='fitted', reason='positivePrediction' if fitted['allow'][m] else 'nonPositivePrediction',
                             prediction=fitted['predictions'][m], allow=fitted['allow'][m]) for m in MODES}
        ages = [r['ageSlots'] for r in immature]
        rows.append(dict(formationDate=day, asOfDate=as_of,
            window=dict(requestedSlots=126, availableSlots=len(window), startDate=window[0] if window else None,
                        endDate=as_of, historyTruncated=len(window)<126), currentFeatures=dict(zip(FEATURE_NAMES,current)),
            currentFeatureStatus='unknown' if current_reasons else 'complete', currentFeatureReasons=current_reasons,
            trainingLabels=labels, trainingSummary=summary, labelXUnknown=rejected, immature=immature,
            support=dict(enteredByAsOfCount=len(known_entries), closedByAsOfCount=len(matured),
                         closedBeforeWindowCount=len(matured)-len(within), closedInWindowCount=len(within),
                         labelXUnknownCount=len(rejected), eligibleEpisodeCount=len(labels), eligibleFormationDayCount=len(counts),
                         immatureCount=len(immature), immatureAgeSlots=dict(minimum=min(ages) if ages else None,
                         mean=math.fsum(ages)/len(ages) if ages else None, maximum=max(ages) if ages else None)), models=models))
    return {'version':'research-online-feedback-v1', 'parameters':{'windowSlots':126, 'minimumFormationDays':20,
        'featureOrder':list(FEATURE_NAMES), 'ridgeLambda':1.0, 'interceptPenalized':False,
        'objective':'sum(weight*squaredError)/sum(weight) + ridgeLambda*sum(slopesSquared)',
        'weighting':'Each eligible episode has 1/count(eligible episodes sharing formationDate in the current exit window)',
        'standardization':'Training-weighted population mean and standard deviation; exact constant columns have z=0',
        'decision':'prediction>0; otherwise reject; both modes retain all signals on insufficient days or unknown current X',
        'clock':'T uses A=T-1; final exits within the inclusive 126-source-slot window ending A',
        'immatureAge':'Source-calendar slots from actual entry through A, inclusive'}, 'targetDates':list(target_dates), 'dailyRows':rows,
        'counts':{'targetDayCount':len(rows), 'modeCounts':{m:dict(Counter('fallback' if r['models'][m]['status']=='fallback'
                  else 'allowed' if r['models'][m]['allow'] else 'rejected' for r in rows)) for m in MODES}},
        'signalsGenerated':False, 'accountExecutions':0, 'certified':False, 'targetAchieved':False}


def synthetic_fixture():
    calendar = [(date(2020,1,1)+timedelta(days=i)).isoformat() for i in range(180)]
    env = [dict(formationDate=d,asOfDate=calendar[i-1] if i else None,
                price20={'positiveReturn20Breadth':.5 if i else None}, amount={'medianRelativeToOwnPrior20':1. if i else None},
                retention={'continuous':{'retentionLower':.5 if i else None}}) for i,d in enumerate(calendar)]
    shadow = []
    for i in range(1,25):
        j=(i-1)%4
        x=[(.25,.75)[j//2], (1.,3.)[j%2], (.25,.75)[j in (0,3)]]
        env[i]['price20']['positiveReturn20Breadth']=x[0]
        env[i]['amount']['medianRelativeToOwnPrior20']=x[1]
        env[i]['retention']['continuous']['retentionLower']=x[2]
        shadow.append(dict(formationId=f's:{i}',formationDate=calendar[i],entryDate=calendar[i+1],exitDate=calendar[30],
                           netReturn=.1+.2*(x[0]-.5)-.1*(x[1]-2)+.05*(x[2]-.5)))
    shadow.append(dict(formationId='stillOpen',formationDate=calendar[25],entryDate=calendar[26],exitDate=None,netReturn=None))
    return calendar,calendar[28:35],env,shadow


def registered_plan(directory):
    plan = read(directory/'plan.json')
    require(plan['schema']=='online-feedback-entry-v1' and plan['boards']==list(BOARDS)
            and plan['exits']==list(EXITS) and plan['candidates']==list(MODES), 'Registered experiment identity')
    require(plan['newAccounts']==24 and plan['referenceAccountsReused']==12 and plan['newEntryPolicies']==2
            and plan['experimentBatches']==1 and plan['parameterSearch'] is False
            and plan['holdoutClaim'] is False, 'Fixed experiment dimensions')
    for name in ('sourcePins','inputPins'):
        for path, expected in plan[name].items():
            bound({'path':path,'sha256':expected})
    require(plan['sourcePins'].get(Path(__file__).relative_to(ROOT).as_posix())==sha(__file__), 'Registered independent auditor')
    same(read(bound(plan['protocol'])),plan['rules'],'Frozen protocol')
    rules=plan['rules']
    require(rules['window']==dict(exitAvailabilityCalendarSlots=126,includesAsOfDate=True,minimumDistinctFormationDays=20,
        partialInitialWindow='Use available prior calendar slots and disclose count; no extra time gate.'), 'Window/support protocol')
    require(rules['features']==['price20.positiveReturn20Breadth','amount.medianRelativeToOwnPrior20','retention.continuous.retentionLower'], 'Feature protocol')
    for name, expected in DEPENDENCIES.items():
        require(sha(ROOT/name)==expected, 'Frozen independent dependency changed')
    parent, comparison, proof = [read(PARENT/n) for n in ('plan.json','comparison.json','validation.json')]
    require(sha(PARENT/'plan.json')=='4edd15d2b074ff81992c61e7bfac652e6e77780bbd4f930fb2e7332014fbe1d5'
            and sha(PARENT/'comparison.json')=='9959f500c8d73e515c5f589b3f8ecfe8158586e99cdd359142f3760341402a94'
            and sha(PARENT/'validation.json')=='a4161122f3ea8c2b8efb04e2dfe6a67cd2ee5c6d8a6cafcdb5e20f560df5fabf', 'Fixed shadow parent identity')
    require(proof['passed'] is True and proof['planSha256']==sha(PARENT/'plan.json')
            and proof['comparisonSha256']==sha(PARENT/'comparison.json'), 'Parent account proof')
    for name in ('sourceData','baselineSchedules','periods'):
        same(plan[name],parent[name],'Inherited '+name)
    same(plan['accountArtifacts'],comparison['artifacts'],'Fixed shadow account artifact references')
    parent_readout, parent_readout_proof = read(PARENT/'readout.json'),read(PARENT/'readout-validation.json')
    require(parent_readout_proof['passed'] is True and parent_readout_proof['readoutSha256']==sha(PARENT/'readout.json'), 'Parent disposition audit')
    same(plan['opportunities'],parent_readout['opportunityRowsArtifact'],'Original full formation sidecar')
    ep, er, ev = [read(ENVIRONMENT/n) for n in ('plan.json','readout.json','validation.json')]
    require(sha(ENVIRONMENT/'validation.json')=='f862afb27062bfe9ed0c50af09953133f9dc1854ad9d349bb05e2a47e5107188'
            and ev['passed'] is True and ev['planSha256']==sha(ENVIRONMENT/'plan.json')
            and ev['readoutSha256']==sha(ENVIRONMENT/'readout.json'), 'Environmental arithmetic proof')
    same(ev['artifacts'],er['artifacts'],'Environment audited artifact set')
    same(ep['sourceData'],plan['sourceData'],'Shared environment/account source identity')
    for period in PERIODS:
        for board in BOARDS:
            same(plan['environment'][period][board],er['artifacts'][period][board]['daily.jsonl.gz'],'Audited daily environment')
            parent_input = read(bound(plan['sourceData'][period][board]['upstreamInputReceipt']))
            require(plan['metadata'][period][board] in parent_input['files'].values(), 'Original calendar metadata not bound by source input receipt')
    require(plan['previousDelivery']['sha256']=='9d40d2257dc71ac201a111e531825b4166f7180b2cdb8413e5d01a40dd04ccb8', 'Environment delivery identity')
    bound(plan['previousDelivery'])
    return plan


def frozen_groups(directory,plan):
    completion=read(directory/'input-completion.json')
    require(completion['planSha256']==sha(directory/'plan.json') and completion['allSchedulesFrozenBeforeAccounts'] is True
            and completion['newAccountsExecuted']==0, 'Inputs frozen before accounts')
    groups=completion['groups']
    require([(g['period'],g['board'],g['exit']) for g in groups]==[(p,b,e) for p in PERIODS for b in BOARDS for e in EXITS], 'All 12 source/exit input groups in original order')
    for g in groups:
        p,b,e=g['period'],g['board'],g['exit']
        same(g['baseline'],plan['accountArtifacts'][p][b][e],'Group original artifacts')
        same(g['baselineSchedule'],plan['baselineSchedules'][p][b],'Group original schedule')
        same(g['accountData'],plan['sourceData'][p][b]['accountData'],'Group source cache')
        require(set(g['candidates'])==set(MODES), 'Both candidate schedules')
        for name in ('decisions','shadow','modelMetadata'):
            bound(g[name])
        for item in g['candidates'].values(): bound(item)
    return completion,groups


def shadow_reference(original,result,summary,period,board,exit_name,helper):
    mapped, bridge = helper.account_reference(original,summary,result,period,board,exit_name)
    shadow=[]
    for row in original:
        value=mapped[row['formationId']]
        if not value['entered']: continue
        shadow.append(dict(formationId=row['formationId'],formationDate=row['formationDate'],entryDate=value['entryDate'],
                           exitDate=value['exitDate'],netReturn=value['closedPnlAmount']/value['investedAmount']
                           if value['positionState']=='closed' else None))
    return shadow,bridge


def write_proof(directory,name,proof):
    with (directory/name).open('x',encoding='utf-8') as stream:
        json.dump(proof,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed':True,'output':ref(directory/name),'counts':proof['counts']},ensure_ascii=False),flush=True)
    return proof


def verify_inputs(directory):
    plan=registered_plan(directory)
    completion,groups=frozen_groups(directory,plan)
    tests=read(directory/'pre-account-tests.json')
    require(tests['passed'] is True and tests['planSha256']==sha(directory/'plan.json')
            and tests['sourcePins']==plan['sourcePins'] and tests['allChecksBeforeFirstNewAccount'] is True
            and all(c['exitCode']==0 for c in tests['commands']), 'Actual pre-account checks')
    require(plan['registeredAt']<=tests['startedAt']<=tests['completedAt']<=completion['completedAt'], 'Registration/test/preparation chronology')
    original=records(bound(plan['opportunities']))
    require(len(original)==8155 and len({(r['period'],r['board'],r['formationId']) for r in original})==8155, 'Original formation population')
    helper=independent('scripts/verify-reclaim-exit-mechanism.py')
    counts=dict(sourceImmediateOpportunities=8155,sourceExitOpportunities=16310,groups=12,preparedSchedules=24,
                sourceShadowEpisodes=0,inputDecisionDays=0,trainingLabelObservations=0,fittingDays=0,newAccounts=0)
    audited=[]
    for g in groups:
        p,b,e=g['period'],g['board'],g['exit']
        formed=[r for r in original if r['period']==p and r['board']==b]
        schedule=read(bound(g['baselineSchedule']))
        same(schedule,[r['signals']['immediate'] for r in formed],'Original exact ordered schedule')
        result=read(bound(g['baseline'][e+'.result.json.gz']))
        summary=read(bound(g['baseline'][e+'.summary.json']))
        shadow,bridge=shadow_reference(formed,result,summary,p,b,e,helper)
        same(read(bound(g['shadow'])),shadow,'Independently reconstructed shadow net-cost episodes')
        metadata=read(bound(plan['metadata'][p][b]))
        environment=records(bound(plan['environment'][p][b]))
        expected=reference_build(metadata['calendar'],metadata['targetDates'],environment,shadow)
        saved=records(bound(g['decisions']))
        same(saved,expected['dailyRows'],'All point-in-time labels/moments/matrices/predictions/decisions')
        same(read(bound(g['modelMetadata'])),{k:v for k,v in expected.items() if k!='dailyRows'},'Model metadata/counts')
        by_day={r['formationDate']:r for r in expected['dailyRows']}
        selected={}
        for m in MODES:
            expected_schedule=[s for s in schedule if by_day[s['signalDate']]['models'][m]['allow']]
            same(read(bound(g['candidates'][m])),expected_schedule,'Original-order filtered schedule '+m)
            selected[m]=len(expected_schedule)
        counts['sourceShadowEpisodes']+=len(shadow)
        counts['inputDecisionDays']+=len(saved)
        counts['trainingLabelObservations']+=sum(len(r['trainingLabels']) for r in saved)
        counts['fittingDays']+=sum(r['models']['ridge']['status']=='fitted' for r in saved)
        audited.append(dict(period=p,board=b,exit=e,shadowEpisodes=len(shadow),calendarDays=len(saved),
                            originalSignals=len(schedule),selectedSignals=selected,accountBridge=bridge,
                            modeCounts=expected['counts']['modeCounts']))
        print(json.dumps({'checkedInputs':[p,b,e],'days':len(saved),'signals':selected}),flush=True)
        del result,summary,expected,saved
        gc.collect()
    require(counts['inputDecisionDays']==5814,'All 12 complete target calendars')
    registered_plan(directory)
    return write_proof(directory,'input-validation.json',dict(passed=True,verifiedAt=datetime.now(timezone.utc).isoformat(),
        planSha256=sha(directory/'plan.json'),inputCompletionSha256=sha(directory/'input-completion.json'),verifierSha256=sha(__file__),
        parentValidationSha256=sha(PARENT/'validation.json'),environmentValidationSha256=sha(ENVIRONMENT/'validation.json'),
        preAccountTests=ref(directory/'pre-account-tests.json'),sourcePins=DEPENDENCIES,inputPins=plan['inputPins'],
        preparedGroups=groups,counts=counts,groups=audited,
        checks=['Frozen original 8155 formations and 12 shadow accounts exactly bound to prior independent proofs',
            'Shadow buy identity, all partial/final sells, fees and terminal positions independently reconstructed',
            'All 5814 daily decisions independently recomputed using only A-visible label status and original formation features',
            'All eligible labels, same-day weights, train-only moments, Cholesky ridge values, strict-zero decisions and fallbacks agree',
            'All 24 schedules preserve original identity/rank/order and do not backfill',
            'No new source bars or account executions performed by this input audit'],holdoutClaim=False,certified=False,targetAchieved=False))


def interval_reference(period,atlas):
    result=[]
    for year in period.split('-'):
        result.extend([dict(id=year,start=year+'-01-01',end=year+'-12-31',kind='calendarYear'),
                       dict(id=year+'-H1',start=year+'-01-01',end=year+'-06-30',kind='calendarHalfYear'),
                       dict(id=year+'-H2',start=year+'-07-01',end=year+'-12-31',kind='calendarHalfYear')])
    start,end=period.split('-')
    result.extend(dict(id=p['id'],start=p['startDate'],end=p['endDate'],kind='retrospectiveNarrativePhase',title=p['title'])
                  for p in atlas['phases'] if start<=p['startDate'][:4]<=end)
    if start=='2024':
        result.extend([dict(id='before20240924',start='2024-01-02',end='2024-09-23',kind='descriptiveEpoch'),
                       dict(id='from20240924',start='2024-09-24',end='2025-12-31',kind='descriptiveEpoch')])
    return result


def verify_accounts(directory):
    plan=registered_plan(directory)
    completion,groups=frozen_groups(directory,plan)
    inputs=read(directory/'input-validation.json')
    require(inputs['passed'] is True and inputs['planSha256']==sha(directory/'plan.json')
            and inputs['verifierSha256']==sha(__file__) and inputs['inputCompletionSha256']==sha(directory/'input-completion.json'), 'Exact independent input proof')
    same(inputs['preparedGroups'],groups,'All prepared input file references unchanged')
    start=read(directory/'run-start.json'); tests=read(bound(start['tests']))
    same(start['inputValidation'],ref(directory/'input-validation.json'),'Run used independent input proof')
    same(start['inputCompletion'],ref(directory/'input-completion.json'),'Run used all frozen schedules')
    require(tests['sourcePins']==plan['sourcePins'] and tests['passed'] is True and tests['allChecksBeforeFirstNewAccount'] is True
            and all(c['exitCode']==0 for c in tests['commands']), 'Actual test results/source identity')
    require(plan['registeredAt']<=tests['startedAt']<=tests['completedAt']<=completion['completedAt']
            <=inputs['verifiedAt']<=start['startedAt'], 'Actual tests/inputs/run chronology')
    comparison=read(directory/'comparison.json')
    require(comparison['schema']=='online-feedback-entry-v1-comparison' and comparison['planSha256']==sha(directory/'plan.json')
            and comparison['newAccounts']==24 and comparison['referenceAccountsReused']==12
            and comparison['newEntryPolicies']==2 and comparison['holdoutClaim'] is False, 'Account comparison scope')
    rows=comparison['rows']
    require([(r['period'],r['board'],r['exit'],r['candidate']) for r in rows]
            ==[(g['period'],g['board'],g['exit'],m) for g in groups for m in ('baseline',*MODES)], 'Complete 36-account order')
    board=independent('scripts/verify-board-coverage-reclaim.py')
    accounting=independent('scripts/verify-peer-amount-persistence.py')
    cost=accounting.cost_reference()
    trail=independent('scripts/verify-reclaim-exit-transfer.py')
    volatility=independent('scripts/verify-reclaim-volatility-trail.py')
    atlas=read(bound(plan['atlas']))
    data=None; data_key=None; results=[]; counts=dict(accounts=36,newAccounts=24,reusedAccounts=12,
        sourceImmediateOpportunities=8155,sourceExitOpportunities=16310,preparedSchedules=24,newFills=0,heldCloseChecks=0,summaryIntervals=0)
    for r in rows:
        p,b,e,m=(r[k] for k in ('period','board','exit','candidate'))
        g=next(g for g in groups if (g['period'],g['board'],g['exit'])==(p,b,e))
        expected_schedule=g['baselineSchedule'] if m=='baseline' else g['candidates'][m]
        same(r['schedule'],expected_schedule,'Account exact frozen schedule ref')
        require(r['reusedAccount'] is (m=='baseline'), 'Account reuse flag')
        folder=directory/p/b/e
        require(bound(r['summary'])==folder/(m+'.summary.json'),'Summary path identity')
        if m=='baseline':
            same(r['result'],g['baseline'][e+'.result.json.gz'],'Original result byte reuse')
            same(r['checkpoint'],g['baseline'][e+'.checkpoint.json.gz'],'Original checkpoint byte reuse')
        else:
            require(bound(r['result'])==folder/(m+'.result.json.gz') and bound(r['checkpoint'])==folder/(m+'.checkpoint.json.gz'), 'New result/checkpoint identity')
        result,checkpoint,summary,schedule=[read(bound(r[k])) for k in ('result','checkpoint','summary','schedule')]
        require(summary['policyId']==m and summary['policyLabel']==m,'Summary policy identity')
        _,market,account=cost.reference_modules(p)
        specs=interval_reference(p,atlas)
        full_summary=account.verify_summary(result,summary,specs)
        counts['summaryIntervals']+=len(summary['periods'])
        if m=='baseline':
            same(result['research']['identity']['signalSchedule'],schedule,'Reused baseline schedule unchanged')
            audit={'reusedAuditedAccount':True,'summary':full_summary,'parentValidationSha256':sha(PARENT/'validation.json')}
        else:
            if data_key!=(p,b):
                if data is not None: del data; gc.collect()
                data=read(bound(g['accountData'])); data_key=(p,b)
                require(digest(data)==plan['sourceData'][p][b]['accountDatasetDigest'],'Frozen account dataset digest')
            # The pinned ledger auditor expects only its six calendar intervals.
            # Project those unchanged rows in memory; full original intervals were
            # independently checked immediately above using the same saved result.
            compatible=deepcopy(summary)
            compatible['periods']=summary['periods'][:7]
            fn=trail.audit_account if e=='trail10' else volatility.audit_account
            audit=fn(result,checkpoint,compatible,schedule,plan['periods'][p]['policyConfigs'][e],data,p,e,board,accounting)
            audit['fullSummaryIntervals']=full_summary
            by_day={d['formationDate']:d for d in records(bound(g['decisions']))}
            permitted={s['signalId']:s for s in schedule}
            for fill in result['trades']:
                if fill['side']=='buy':
                    require(fill['signalId'] in permitted and fill['signalDate']==permitted[fill['signalId']]['signalDate']
                            and by_day[fill['signalDate']]['models'][m]['allow'] is True, 'Actual buy not admitted by frozen point-in-time policy')
            counts['newFills']+=len(result['trades'])
            counts['heldCloseChecks']+=audit['dailyRisk']['heldDays']
            require(digest(data)==plan['sourceData'][p][b]['accountDatasetDigest'],'Audit mutated dataset')
        results.append({'period':p,'board':b,'exit':e,'candidate':m,'artifactRefs':r,'checks':audit})
        print(json.dumps({'checkedAccount':[p,b,e,m],'reused':m=='baseline'}),flush=True)
        del result,checkpoint,summary,schedule
        gc.collect()
    registered_plan(directory)
    source_pins={**DEPENDENCIES,**cost.REFERENCES}
    return write_proof(directory,'validation.json',dict(passed=True,verifiedAt=datetime.now(timezone.utc).isoformat(),
        planSha256=sha(directory/'plan.json'),comparisonSha256=sha(directory/'comparison.json'),
        inputCompletionSha256=sha(directory/'input-completion.json'),inputValidationSha256=sha(directory/'input-validation.json'),
        verifierSha256=sha(__file__),parentValidationSha256=sha(PARENT/'validation.json'),runStart=ref(directory/'run-start.json'),
        preAccountTests=start['tests'],sourcePins=source_pins,inputPins=plan['inputPins'],counts=counts,accounts=results,
        checks=['All 12 original result/checkpoint references are byte-reused and their new summaries independently recomputed',
            'All 24 candidate ledgers, paid fees/slippage, source fills, budgets, calendar, lot/limit rules, positions and cash curves independently audited',
            'All held-close risk decisions use unchanged fixed10 or volatilityTrail independent reference replay',
            'Every new buy matches a frozen allowed original signal; full original summary intervals use carried equity',
            'Pretests, complete schedules and input proof preceded the first account run'],holdoutClaim=False,certified=False,targetAchieved=False))


def self_test():
    checks = []
    a = [[4., 1., 1.], [1., 3., 0.], [1., 0., 2.]]
    expected = [1., -2., 3.]
    solved = solve_spd3(a, [math.fsum(r[j] * expected[j] for j in range(3)) for r in a])
    for actual, value in zip(solved, expected):
        near(actual, value, 'Hand-constructed SPD solution')
    checks.append('Independent Cholesky solution and normal-equation residual')
    orthogonal = [[-1., -1., 1.], [-1., 1., -1.], [1., -1., -1.], [1., 1., 1.]]
    samples = [{'formationDate': str(i), 'features': x, 'target': .02 + .10*x[0] - .06*x[1] + .04*x[2]}
               for i, x in enumerate(orthogonal)]
    fit = fit_reference(samples, [1., 1., 1.])
    for actual, value in zip(fit['slopes'], [.05, -.03, .02]):
        near(actual, value, 'Mean-loss lambda-one closed-form shrinkage')
    near(fit['predictions']['ridge'], .06, 'Unpenalized intercept plus shrunk slopes')
    checks.append('Analytic orthogonal design distinguishes mean-loss ridge from sum-loss ridge')
    duplicated = samples + [dict(samples[0]) for _ in range(4)]
    other = fit_reference(duplicated, [1., 1., 1.])
    for key in ('means', 'scales', 'slopes'):
        for x, y in zip(fit[key], other[key]):
            near(x, y, 'Formation-day duplicate reweighting')
    checks.append('Identical same-day duplicates do not multiply that formation-day weight')
    constant = [{'formationDate': 'a', 'features': [.1, 1., .3], 'target': -.1},
                {'formationDate': 'a', 'features': [.1, 1., .3], 'target': .3},
                {'formationDate': 'b', 'features': [.1, 1., .3], 'target': .2}]
    fit = fit_reference(constant, [.9, 10., .8])
    near(fit['predictions']['ridge'], .15, 'Constant feature prediction equals formation-weighted outcome')
    require(fit['scales'] == [0., 0., 0.] and fit['slopes'] == [0., 0., 0.], 'Exact constant columns')
    checks.append('Constant features do not generate tiny artificial variance or penalize intercept')
    zero = fit_reference([dict(row, target=0.) for row in samples], [1., 1., 1.])
    require(zero['allow'] == {'intercept': False, 'ridge': False}, 'Strict zero threshold, no epsilon')
    checks.append('Exactly zero net prediction is not admitted')
    calendar = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(160)]
    episodes = [dict(formationId='left', formationDate=calendar[0], entryDate=calendar[1], exitDate=calendar[23], netReturn=.1),
                dict(formationId='inside', formationDate=calendar[0], entryDate=calendar[2], exitDate=calendar[24], netReturn=.2),
                dict(formationId='asof', formationDate=calendar[30], entryDate=calendar[31], exitDate=calendar[149], netReturn=.3),
                dict(formationId='future', formationDate=calendar[30], entryDate=calendar[32], exitDate=calendar[151], netReturn='unreadable'),
                dict(formationId='open', formationDate=calendar[30], entryDate=calendar[33], exitDate=None, netReturn='unreadable'),
                dict(formationId='notYetEntered', formationDate=calendar[149], entryDate=calendar[151], exitDate=None, netReturn='unreadable')]
    features = {calendar[0]: [.4, 1., .5], calendar[30]: [.6, 2., .3]}
    window = label_window_reference(calendar, calendar[150], episodes, features)
    require(len(window['windowDates']) == 126 and [r['formationId'] for r in window['samples']] == ['inside', 'asof'],
            'Exact inclusive 126-source-session exit window')
    require([r['formationId'] for r in window['unmatured']] == ['future', 'open']
            and all(set(row) == {'formationId', 'formationDate', 'entryDate', 'ageSessions'} for row in window['unmatured']),
            'Unmatured outcomes leaked or entry not yet known')
    checks.append('126-slot exit clock includes A, excludes old/future exits, keeps no future immature outcomes')
    changed = [dict(row) for row in episodes]
    changed[3].update(exitDate=calendar[159], netReturn=999.)
    changed[4]['netReturn'] = -999.
    require(label_window_reference(calendar, calendar[150], changed, features) == window, 'Future outcome prefix invariance')
    checks.append('Changing future closure or final return cannot change an earlier training view')
    missing = label_window_reference(calendar, calendar[150], episodes, {calendar[0]: features[calendar[0]]})
    require([r['formationId'] for r in missing['samples']] == ['inside'] and missing['missingFeatureIds'] == ['asof'],
            'Missing formation snapshot must not be backfilled from another date')
    checks.append('Training uses original formation snapshot; missing features remain excluded')
    calendar,targets,env,shadow=synthetic_fixture()
    built=reference_build(calendar,targets,env,shadow)
    require(built['dailyRows'][0]['models']['ridge']['status']=='fallback'
            and built['dailyRows'][-1]['models']['ridge']['status']=='fitted', 'Maturity activates training only on next known cutoff')
    require(built['dailyRows'][-1]['support']['eligibleFormationDayCount']==24
            and built['dailyRows'][-1]['support']['immatureCount']==1, 'Full daily support evidence')
    require(built['dailyRows'][-1]['trainingSummary']['zeroVarianceColumns']==[], 'Three varying features')
    checks.append('Complete reference daily schema preserves immature episode and causal support transition')
    env2=deepcopy(env)
    env2[34]['amount']['medianRelativeToOwnPrior20']=None
    missing_current=reference_build(calendar,targets,env2,shadow)['dailyRows'][-1]
    require(missing_current['models']['ridge']==dict(status='fallback',reason='currentFeaturesUnknown',prediction=None,allow=True)
            and missing_current['models']['intercept']==missing_current['models']['ridge']
            and missing_current['trainingSummary']['normalMatrix'] is None
            and missing_current['trainingSummary']['labelMean'] is not None, 'Common fallback without erasing support')
    checks.append('Missing current X shares fallback across both models while retaining past support statistics')
    return {'passed': True, 'syntheticGroups': len(checks), 'checks': checks, 'realSourceReads': 0, 'accountRuns': 0}


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('mode', choices=('inputs','accounts'), nargs='?', default='accounts')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--inputs','--inputs-only',dest='inputs',action='store_true')
    parser.add_argument('--out',type=Path,default=DEFAULT)
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
    else:
        directory=args.out.resolve()
        try:
            (verify_inputs if args.inputs or args.mode=='inputs' else verify_accounts)(directory)
        except Exception as error:
            if directory.exists():
                attempts=directory/'validation-attempts'; attempts.mkdir(exist_ok=True)
                failure=attempts/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')
                with failure.open('x',encoding='utf-8') as stream:
                    json.dump(dict(passed=False,at=datetime.now(timezone.utc).isoformat(),mode='inputs' if args.inputs else args.mode,
                        verifierSha256=sha(__file__),type=type(error).__name__,message=str(error),traceback=traceback.format_exc()),
                        stream,ensure_ascii=False,indent=2)
            raise
