"""Independent membership/maturity readout audit; no new account execution.

Path execution is inherited only through the exact passing path receipt. All
feedback decisions are rebuilt with the frozen independent Cholesky reference;
neither the production feedback implementation nor the new summarizer is used.
"""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import importlib.util
from itertools import zip_longest
import json
import math
from pathlib import Path
import statistics
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/independent-shadow-labels-v1'
HELPERS = {
    'scripts/verify-independent-shadow-labels.py': '5c2b101a372f7df514691cfbcd242cfc9ab79d196c0dcfb0a333c64f0a9be377',
    'scripts/verify-online-feedback-entry.py': 'ec17b1a7114bd2ed6a9ba5757f26fccdbfeb3baa1cedcadf5993ec18a0a963d5',
}
SAMPLES = ('allExecutable', 'originalEntered')
MODELS = ('intercept', 'ridge')
KEYS = ('period', 'board', 'exit')
SUPPORT = ('enteredByAsOfCount', 'closedByAsOfCount', 'closedBeforeWindowCount', 'closedInWindowCount',
           'labelXUnknownCount', 'eligibleEpisodeCount', 'eligibleFormationDayCount', 'immatureCount')
CUT = '2024-09-24'


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                    allow_nan=False).encode('utf-8')).hexdigest()


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz') else Path(path).open(encoding='utf-8')) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def same(actual, expected, where='value'):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and set(actual) == set(expected), where + ': fields differ')
        for key in expected:
            same(actual[key], expected[key], where + '.' + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), where + ': length differs')
        for index, (left, right) in enumerate(zip(actual, expected)):
            same(left, right, f'{where}[{index}]')
    elif finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10), where + ': number differs')
    else:
        require(type(actual) is type(expected) and actual == expected, where + ': value differs')


_REFERENCES = {}
def reference(name='scripts/verify-online-feedback-entry.py'):
    if name not in _REFERENCES:
        require(sha(ROOT / name) == HELPERS[name], 'Changed independent reference: ' + name)
        spec = importlib.util.spec_from_file_location('_shadow_readout_' + Path(name).stem.replace('-', '_'), ROOT / name)
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        _REFERENCES[name] = value
    return _REFERENCES[name]


def dist(items):
    values = [item for item in items if item is not None]
    require(all(finite(item) for item in values), 'Nonfinite diagnostic statistic')
    return dict(count=len(values), minimum=min(values) if values else None,
                mean=statistics.fmean(values) if values else None,
                median=statistics.median(values) if values else None,
                maximum=max(values) if values else None)


def population(labels, symbols):
    closed = [label for label in labels if label['exitDate'] is not None]
    return dict(episodes=len(labels), formationDays=len({label['formationDate'] for label in labels}),
                symbols=len({symbols[label['formationId']] for label in labels}), closed=len(closed),
                immatureAtCutoff=len(labels) - len(closed),
                closedNetReturnPct=dist(label['netReturn'] * 100 for label in closed))


def compact_reference(row, symbols):
    labels, rejected, pending = (row[name] for name in ('trainingLabels', 'labelXUnknown', 'immature'))
    result = deepcopy(row)
    for name in ('trainingLabels', 'labelXUnknown', 'immature'):
        result.pop(name)
    weight_sum = math.fsum(label['weight'] for label in labels)
    by_symbol = defaultdict(list)
    normalized = []
    for label in labels:
        normalized.append(label['weight'] / weight_sum)
        by_symbol[symbols[label['formationId']]].append(normalized[-1])
    days = len({label['formationDate'] for label in labels})
    if labels:
        same(weight_sum, days, 'Formation-day unit weight')
    result['trainingPopulation'] = dict(episodeCount=len(labels), formationDayCount=days,
        symbolCount=len(by_symbol), netReturnPct=dist(label['netReturn'] * 100 for label in labels),
        normalizedEpisodeWeight=dist(normalized), maximumSymbolWeightShare=max(
            (math.fsum(weights) for weights in by_symbol.values()), default=None),
        maximumFormationDayWeightShare=1 / days if days else None, labelsDigest=digest(labels))
    for name, rows in (('labelXUnknownPopulation', rejected), ('immaturePopulation', pending)):
        value = dict(episodeCount=len(rows), formationDayCount=len({item['formationDate'] for item in rows}),
                     symbolCount=len({symbols[item['formationId']] for item in rows}), digest=digest(rows))
        if name == 'immaturePopulation':
            value['ageSlots'] = dist(item['ageSlots'] for item in rows)
        result[name] = value
    return result


def periods(dates):
    require(dates and dates == sorted(set(dates)), 'Unique increasing decision dates')
    result = [dict(id='overall', kind='overall', startDate=dates[0], endDate=dates[-1])]
    for year in sorted(set(day[:4] for day in dates)):
        inside = [day for day in dates if day.startswith(year)]
        result.append(dict(id=year, kind='calendarYear', startDate=inside[0], endDate=inside[-1]))
    if dates[0] < CUT <= dates[-1]:
        for name, inside in (('before20240924', [day for day in dates if day < CUT]),
                             ('from20240924', [day for day in dates if day >= CUT])):
            result.append(dict(id=name, kind='descriptiveEpoch', startDate=inside[0], endDate=inside[-1]))
    return result


def coverage_reference(key, sample, daily, counts):
    output = []
    for span in periods([row['formationDate'] for row in daily]):
        rows = [row for row in daily if span['startDate'] <= row['formationDate'] <= span['endDate']]
        fitted = [row for row in rows if row['models']['ridge']['status'] == 'fitted']
        opportunities = sum(counts[row['formationDate']] for row in rows)
        fitted_opportunities = sum(counts[row['formationDate']] for row in fitted)
        value = dict(**key, sample=sample, **span, dayCount=len(rows), fittedDays=len(fitted),
            fallbackDays=len(rows) - len(fitted), fittedDayPct=100 * len(fitted) / len(rows),
            firstFittedDate=fitted[0]['formationDate'] if fitted else None, originalOpportunities=opportunities,
            fittedOriginalOpportunities=fitted_opportunities,
            fittedOriginalOpportunityPct=100 * fitted_opportunities / opportunities if opportunities else None,
            fallbackReasons=dict(sorted(Counter(row['models']['ridge']['reason'] for row in rows
                if row['models']['ridge']['status'] == 'fallback').items())),
            models={mode: dict(fittedAllowedDays=sum(row['models'][mode]['allow'] for row in fitted),
                fittedRejectedDays=sum(not row['models'][mode]['allow'] for row in fitted),
                fittedPredictionPct=dist(row['models'][mode]['prediction'] * 100 for row in fitted)) for mode in MODELS},
            support={name: dist(row['support'][name] for row in rows) for name in SUPPORT},
            eligibleSymbols=dist(row['trainingPopulation']['symbolCount'] for row in rows),
            eligibleWeightedMeanReturnPct=dist(None if row['trainingSummary']['labelMean'] is None else
                row['trainingSummary']['labelMean'] * 100 for row in rows),
            maximumSymbolWeightShare=dist(row['trainingPopulation']['maximumSymbolWeightShare'] for row in rows),
            maximumFormationDayWeightShare=dist(row['trainingPopulation']['maximumFormationDayWeightShare'] for row in rows),
            maximumEpisodeWeight=dist(row['trainingPopulation']['normalizedEpisodeWeight']['maximum'] for row in rows),
            immatureFormationDays=dist(row['immaturePopulation']['formationDayCount'] for row in rows),
            immatureSymbols=dist(row['immaturePopulation']['symbolCount'] for row in rows),
            dailyMeanImmatureAgeSlots=dist(row['immaturePopulation']['ageSlots']['mean'] for row in rows),
            maximumImmatureAgeSlots=max([row['immaturePopulation']['ageSlots']['maximum'] or 0 for row in rows], default=0),
            basis='Days and original opportunities are separate denominators; distributions are daily snapshots, not independent observations. Epochs use formation T, features use T-1.')
        output.append(value)
    return output


def slim_paths(path_rows, schedule, old_labels, key):
    old = {label['formationId']: label for label in old_labels}
    require(len(old) == len(old_labels), 'Duplicate old actual entry')
    output = []
    for row, signal in zip_longest(path_rows, schedule):
        require(row is not None and signal is not None, 'Path count/order differs from original schedule')
        for name in KEYS:
            same(row[name], key[name], 'Path group')
        same(row['formationId'], signal['signalId'], 'Formation ID and order')
        same(row['formationDate'], signal['signalDate'], 'Original formation date')
        same(row['symbol'], signal['symbol'], 'Original symbol')
        path = row['path']; outcome = path['outcome']; label = path['label']
        same(path['signal'], signal, 'Original five-field signal')
        same(path['exitPolicy'], key['exit'], 'Original exit policy')
        require(row['originalEntered'] == (row['formationId'] in old) == (row['originalDisposition']['filledQuantity'] > 0),
                'Original membership must match actual-entry labels')
        require((label is not None) == outcome['entered'], 'Missing/non-entry cannot become zero label')
        if label is not None:
            require(label['formationId'] == row['formationId'] and label['formationDate'] == row['formationDate'], 'Label identity')
            require(label['entryDate'] == outcome['entryDate'], 'Actual opening entry')
            require((label['exitDate'] is not None) == (path['status'] == 'closed') == outcome['fullyClosed'], 'Maturity status')
            if label['exitDate'] is None:
                require(label['netReturn'] is None, 'Open/unresolved cannot expose final return')
            else:
                same(label['exitDate'], outcome['labelAvailableDate'], 'Final label clock')
                same(label['exitDate'], outcome['finalExitDate'], 'No delayed rights maturity allowed')
                same(label['netReturn'], outcome['netPnlAmount'] / outcome['investedAmount'], 'Net invested-capital return')
        output.append(dict(formationId=row['formationId'], formationDate=row['formationDate'], symbol=row['symbol'],
            originalEntered=row['originalEntered'], status=path['status'], label=label, outcome=outcome))
    require(set(old) <= {row['formationId'] for row in output}, 'Lost original entry')
    return output


def bridge_reference(paths, old_labels, calendar):
    old = {row['formationId']: row for row in old_labels}
    index = {day: i for i, day in enumerate(calendar)}
    output = []
    for path in paths:
        fid = path['formationId']
        if fid not in old:
            continue
        before, after = old[fid], path['label']
        both_closed = after is not None and before['exitDate'] is not None and after['exitDate'] is not None
        if after is None:
            category = 'oldEnteredNewNotEntered'
        else:
            category = ('oldClosed' if before['exitDate'] is not None else 'oldOpen')
            category += 'NewClosed' if after['exitDate'] is not None else 'NewUnmatured'
        output.append(dict(formationId=fid, formationDate=path['formationDate'], symbol=path['symbol'],
            category=category, pathStatus=path['status'], oldActualLabel=before, independentLabel=after,
            sameEntryDate=None if after is None else before['entryDate'] == after['entryDate'],
            entryDeltaSlots=None if after is None else index[after['entryDate']] - index[before['entryDate']],
            sameFinalExitDate=before['exitDate'] == after['exitDate'] if both_closed else None,
            finalExitDeltaSlots=index[after['exitDate']] - index[before['exitDate']] if both_closed else None,
            netReturnDeltaPct=(after['netReturn'] - before['netReturn']) * 100 if both_closed else None,
            independentInvestedAmount=path['outcome']['investedAmount']))
    require(len(output) == len(old), 'Old actual-entry denominator not preserved')
    return output


def contrast(candidate, control):
    states = (candidate['models']['ridge']['status'] == 'fitted', control['models']['ridge']['status'] == 'fitted')
    names = {(True, True): 'bothFitted', (True, False): 'candidateOnlyFitted',
             (False, True): 'controlOnlyFitted', (False, False): 'neitherFitted'}
    return dict(fitState=names[states],
        eligibleEpisodeDelta=candidate['support']['eligibleEpisodeCount'] - control['support']['eligibleEpisodeCount'],
        eligibleFormationDayDelta=candidate['support']['eligibleFormationDayCount'] - control['support']['eligibleFormationDayCount'],
        immatureDelta=candidate['support']['immatureCount'] - control['support']['immatureCount'],
        models={mode: dict(allowDiffers=candidate['models'][mode]['allow'] != control['models'][mode]['allow'],
            bothFittedPredictionDeltaPct=(candidate['models'][mode]['prediction'] - control['models'][mode]['prediction']) * 100
            if all(states) else None) for mode in MODELS})


def group_reference(key, calendar, target_dates, environment_rows, path_rows, schedule, old_labels, old_decisions):
    paths = slim_paths(path_rows, schedule, old_labels, key)
    symbols = {path['formationId']: path['symbol'] for path in paths}
    counts = Counter(path['formationDate'] for path in paths)
    samples = {sample: [path['label'] for path in paths if path['label'] is not None and
        (sample == 'allExecutable' or path['originalEntered'])] for sample in SAMPLES}
    daily = {}; parameters = None
    for sample in SAMPLES:
        calculated = reference().reference_build(calendar, target_dates, environment_rows, samples[sample])
        daily[sample] = [compact_reference(row, symbols) for row in calculated['dailyRows']]
        parameters = calculated['parameters']
    # Old actual labels are independently rebuilt as well. Their saved daily
    # decisions are reference evidence, not an oracle for the two new masks.
    old_calculated = reference().reference_build(calendar, target_dates, environment_rows, old_labels)
    same(old_decisions, old_calculated['dailyRows'], 'Inherited old-actual daily feedback')
    old_daily = [compact_reference(row, symbols) for row in old_calculated['dailyRows']]
    comparisons = []
    for i, day in enumerate(target_dates):
        a, b, old = daily[SAMPLES[0]][i], daily[SAMPLES[1]][i], old_daily[i]
        require(a['formationDate'] == b['formationDate'] == old['formationDate'] == day, 'Decision date alignment')
        same(a['currentFeatures'], b['currentFeatures'], 'Same current features')
        same(a['currentFeatures'], old['currentFeatures'], 'Unchanged current features')
        within, bridge = contrast(a, b), contrast(b, old)
        require(within['eligibleEpisodeDelta'] >= 0 and within['eligibleFormationDayDelta'] >= 0
            and within['fitState'] != 'controlOnlyFitted', 'Subset cannot exceed same-library supported population')
        comparisons.append(dict(**key, formationDate=day, asOfDate=a['asOfDate'], originalOpportunities=counts[day],
                                sameLibrarySelection=within, independentSubsetVsOldActual=bridge))
    bridges = bridge_reference(paths, old_labels, calendar)
    summary_bridge = dict(oldActualEntryCount=len(old_labels), matchedIndependentEntries=len(samples['originalEntered']),
        missingIndependentEntries=len(old_labels) - len(samples['originalEntered']),
        categories=dict(sorted(Counter(row['category'] for row in bridges).items())))
    for name in ('EntryDate', 'FinalExitDate'):
        summary_bridge['same' + name] = sum(row['same' + name] is True for row in bridges)
        summary_bridge['different' + name] = sum(row['same' + name] is False for row in bridges)
    summary_bridge['bothClosed'] = sum(row['sameFinalExitDate'] is not None for row in bridges)
    for name in ('entryDeltaSlots', 'finalExitDeltaSlots', 'netReturnDeltaPct'):
        summary_bridge[name] = dist(row[name] for row in bridges)
    summary = dict(**key, originalOpportunities=len(paths),
        pathStatusCounts=dict(sorted(Counter(path['status'] for path in paths).items())),
        populations={sample: population(labels, symbols) for sample, labels in samples.items()},
        oldActualPopulation=population(old_labels, symbols), bridge=summary_bridge, dailyComparisons={})
    for name in ('sameLibrarySelection', 'independentSubsetVsOldActual'):
        summary['dailyComparisons'][name] = dict(fitStates=dict(sorted(Counter(row[name]['fitState'] for row in comparisons).items())),
            models={mode: dict(allowDiffersDays=sum(row[name]['models'][mode]['allowDiffers'] for row in comparisons),
                bothFittedPredictionDeltaPct=dist(row[name]['models'][mode]['bothFittedPredictionDeltaPct'] for row in comparisons)) for mode in MODELS})
    return dict(groupReadout=summary,
        coverageRows=[row for sample in SAMPLES for row in coverage_reference(key, sample, daily[sample], counts)],
        oldActualCoverageRows=coverage_reference(key, 'oldActual', old_daily, counts),
        decisionLibraries=daily, dailyComparisonRows=comparisons, bridgeRows=[dict(**key, **row) for row in bridges],
        parameters=parameters)


def verify(directory):
    directory = Path(directory).resolve()
    require(not (directory / 'readout-validation.json').exists(), 'Never replace an existing readout audit')
    inputs = {}
    def bound(item):
        path = (ROOT / item['path']).resolve()
        require(path.is_relative_to(ROOT) and sha(path) == item['sha256'], 'Changed bound input ' + item['path'])
        inputs[item['path']] = item['sha256']
        return path
    def local(name):
        path = directory / name
        return bound(dict(path=path.relative_to(ROOT).as_posix(), sha256=sha(path)))
    plan_file = local('plan.json'); plan = read(plan_file); plan_sha = sha(plan_file)
    completion_file = local('library-completion.json'); completion = read(completion_file)
    validation_file = local('validation.json'); validation = read(validation_file)
    readout_file = local('readout.json'); saved = read(readout_file)
    require(validation['passed'] is True and validation['planSha256'] == plan_sha
        and validation['libraryCompletionSha256'] == sha(completion_file)
        and validation['verifierSha256'] == HELPERS['scripts/verify-independent-shadow-labels.py'], 'Exact independent path proof required')
    for name, value in {**plan['sourcePins'], **validation['sourcePins'], **HELPERS}.items():
        bound(dict(path=name, sha256=value))
    require(completion['planSha256'] == saved['planSha256'] == plan_sha, 'Plan identity')
    require(saved['pathValidationSha256'] == sha(validation_file)
        and saved['libraryCompletionSha256'] == sha(completion_file), 'Readout path-proof identity')
    require(saved['signalsGenerated'] is False and saved['candidateAccountExecutions'] == 0
        and saved['certified'] is False and saved['targetAchieved'] is False, 'Diagnostic-only scope')
    for item in saved['inputRefs']:
        bound(item)
    summary_name = 'scripts/summarize-independent-shadow-labels.py'
    bound(dict(path=summary_name, sha256=saved['builderSha256']))
    post_file = directory / 'readout-source-pins.json'
    if post_file.exists():
        post = read(local('readout-source-pins.json'))
        require(post['planSha256'] == plan_sha and post['sourcePins'][summary_name] == saved['builderSha256'], 'Frozen postprocessing source')
        for name, value in post['sourcePins'].items():
            bound(dict(path=name, sha256=value))
    else:
        require(plan['sourcePins'].get(summary_name) == saved['builderSha256'], 'Missing pre-readout source freeze')
    parent_file = bound(plan['parentPlan']); parent = read(parent_file)
    bound(plan['parentDelivery'])
    old_file = bound(plan['parentInputCompletion']); old_input = read(old_file)
    old_proof = read(bound(plan['parentInputValidation']))
    require(old_proof['passed'] and old_proof['planSha256'] == sha(parent_file)
        and old_proof['inputCompletionSha256'] == sha(old_file), 'Audited original input lineage')
    for name in ('sourceData', 'metadata', 'environment', 'baselineSchedules', 'opportunities'):
        same(plan[name], parent[name], 'Unchanged original inputs ' + name)
    old_groups = {tuple(row[name] for name in KEYS): row for row in old_input['groups']}
    declared = [tuple(row[name] for name in KEYS) for row in completion['groups']]
    require(len(declared) == len(set(declared)) == 12 and set(declared) == set(old_groups), 'Twelve source-exit groups')
    require([tuple(row[name] for name in KEYS) for row in saved['decisionArtifacts']] == declared, 'Decision artifact order')
    totals, coverage, old_coverage, daily_rows, bridge_rows, nfeedback = [], [], [], [], [], 0
    for g, artifact in zip(completion['groups'], saved['decisionArtifacts']):
        key = {name: g[name] for name in KEYS}; p, b, e = (key[name] for name in KEYS)
        old = old_groups[(p, b, e)]
        metadata = read(bound(plan['metadata'][p][b]))
        environment = list(records(bound(plan['environment'][p][b])))
        schedule = read(bound(plan['baselineSchedules'][p][b]))
        require(old['baselineSchedule'] == plan['baselineSchedules'][p][b], 'Original schedule source')
        path_ref = g['paths']
        require(validation['inputPins'].get(path_ref['path']) == path_ref['sha256'], 'Path bytes not audited')
        result = group_reference(key, metadata['calendar'], metadata['targetDates'], environment,
            records(bound(path_ref)), schedule, read(bound(old['shadow'])), list(records(bound(old['decisions']))))
        same(result['parameters'], saved['parameters'], 'Unchanged feedback definition')
        require(result['groupReadout']['originalOpportunities'] == g['pathCount'], 'Full original group denominator')
        same(result['groupReadout']['pathStatusCounts'], g['counts'], 'All terminal states preserved')
        totals.append(result['groupReadout']); coverage.extend(result['coverageRows']); old_coverage.extend(result['oldActualCoverageRows'])
        daily_rows.extend(result['dailyComparisonRows']); bridge_rows.extend(result['bridgeRows'])
        require(set(artifact['samples']) == set(SAMPLES), 'Both same-library populations')
        for sample in SAMPLES:
            expected = [dict(**key, sample=sample, **row) for row in result['decisionLibraries'][sample]]
            ref = artifact['samples'][sample]
            require(ref['count'] == len(expected) and ref['format'] == 'jsonl+gzip', 'Decision descriptor')
            for i, (actual, calculated) in enumerate(zip_longest(records(bound(ref)), expected)):
                require(actual is not None and calculated is not None, 'Missing/extra compact decision')
                same(actual, calculated, f'{p}/{b}/{e}/{sample}/{i}')
            nfeedback += len(expected)
        print(json.dumps(dict(auditedReadoutGroup=key, originalOpportunities=len(schedule), decisions=2 * len(metadata['targetDates']))), flush=True)
    same(saved['groupReadout'], totals, 'All group summaries')
    same(saved['coverageRows'], coverage, 'All sample/time coverage cells')
    same(saved['oldActualCoverageRows'], old_coverage, 'Old-actual coverage cells')
    for name, expected in (('dailyComparisonRowsArtifact', daily_rows), ('oldActualBridgeRowsArtifact', bridge_rows)):
        descriptor = saved[name]
        require(descriptor['count'] == len(expected) and descriptor['format'] == 'jsonl+gzip', name + ' descriptor')
        for i, (actual, calculated) in enumerate(zip_longest(records(bound(descriptor)), expected)):
            require(actual is not None and calculated is not None, name + ' missing/extra record')
            same(actual, calculated, name + '/' + str(i))
    counts = dict(groups=12, decisionLibraries=24, originalFormations=8155,
        independentPaths=sum(row['originalOpportunities'] for row in totals), feedbackDays=nfeedback,
        coverageRows=len(coverage), oldActualCoverageRows=len(old_coverage), oldActualEntries=len(bridge_rows), newCandidateAccounts=0, newSignals=0)
    same(saved['counts'], counts, 'Complete readout counts')
    require(counts['independentPaths'] == 16310 == validation['counts']['paths'] == completion['independentPathExecutions']
        and len(coverage) == 96 and len(old_coverage) == 48 and nfeedback == 11628, 'Frozen full denominators')
    require(all(sha(ROOT / name) == value for name, value in inputs.items()), 'Input changed during verification')
    proof = dict(passed=True, verifiedAt=datetime.now(timezone.utc).isoformat(), planSha256=plan_sha,
        readoutSha256=sha(readout_file), pathValidationSha256=sha(validation_file),
        libraryCompletionSha256=sha(completion_file), verifierSha256=sha(__file__),
        dailyComparisonRowsArtifactSha256=saved['dailyComparisonRowsArtifact']['sha256'],
        oldActualBridgeRowsArtifactSha256=saved['oldActualBridgeRowsArtifact']['sha256'],
        sourcePins={**HELPERS, Path(__file__).relative_to(ROOT).as_posix(): sha(__file__)},
        inputPins=inputs, counts={**counts, 'dailyComparisonRows': len(daily_rows)},
        checks=['Exact passing all-path audit and immutable library/source lineage',
            'Two masks use the same independently audited labels; old actual execution bridge remains separate',
            'All 24 feedback libraries independently rebuilt including 126-session exit windows, per-day weights, maturity and ridge math',
            'Every compact decision, digest, support/weight/symbol distribution and original-entry attrition matched',
            'All group, annual and descriptive-epoch matrices and both sidecars independently reconciled'],
        scope='No new account, signal or market-price calculation. All data remain development; distributions do not imply independent observations or portfolio returns.',
        certified=False, targetAchieved=False)
    with (directory / 'readout-validation.json').open('x', encoding='utf-8') as stream:
        json.dump(proof, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write('\n')
    print(json.dumps(dict(passed=True, readoutValidationSha256=sha(directory / 'readout-validation.json'), counts=proof['counts'])), flush=True)
    return proof


def regime_day_reference(row):
    labels = row['trainingLabels']
    by_day = Counter(label['formationDate'] for label in labels)
    post_days = {day for day in by_day if day >= CUT}
    post_labels = [label for label in labels if label['formationDate'] >= CUT]
    for label in labels:
        require(row['window']['startDate'] <= label['exitDate'] <= row['asOfDate'], 'Matured exit window')
        same(label['weight'], 1 / by_day[label['formationDate']], 'Same-day label weighting')
    total_weight = math.fsum(label['weight'] for label in labels)
    post_weight = math.fsum(label['weight'] for label in post_labels)
    same(total_weight, len(by_day), 'Total weight equals formation days')
    same(post_weight, len(post_days), 'Post-era weight equals formation days')
    return dict(formationDate=row['formationDate'], asOfDate=row['asOfDate'], window=row['window'],
        trainingLabelsDigest=digest(labels), eligibleEpisodeCount=len(labels), eligibleFormationDayCount=len(by_day),
        weightSum=total_weight, preRegimeEpisodeCount=len(labels) - len(post_labels), postRegimeEpisodeCount=len(post_labels),
        preRegimeFormationDayCount=len(by_day) - len(post_days), postRegimeFormationDayCount=len(post_days),
        preRegimeWeight=total_weight - post_weight, postRegimeWeight=post_weight,
        postRegimeWeightShare=len(post_days) / len(by_day) if by_day else None,
        hasPostRegimeMaturedLabels=bool(post_days), labelXUnknownCount=row['support']['labelXUnknownCount'],
        immatureCount=row['support']['immatureCount'], currentFeatureStatus=row['currentFeatureStatus'],
        modelStatus=row['models']['ridge']['status'], modelReason=row['models']['ridge']['reason'],
        modelAllow={mode: row['models'][mode]['allow'] for mode in MODELS})


def regime_spans_reference(key, daily):
    result = []
    for identity, kind, rows in (('overall', 'overall', daily),
        ('before20240924', 'descriptiveEpoch', [row for row in daily if row['formationDate'] < CUT]),
        ('from20240924', 'descriptiveEpoch', [row for row in daily if row['formationDate'] >= CUT])):
        if not rows:
            continue
        post = [row for row in rows if row['hasPostRegimeMaturedLabels']]
        fitted = [row for row in rows if row['modelStatus'] == 'fitted']
        result.append(dict(**key, id=identity, kind=kind, startDate=rows[0]['formationDate'], endDate=rows[-1]['formationDate'],
            dayCount=len(rows), fittedDays=len(fitted), fallbackDays=len(rows) - len(fitted),
            daysWithAnyEligibleLabels=sum(row['eligibleEpisodeCount'] > 0 for row in rows),
            daysWithPostRegimeMaturedLabels=len(post), firstPostRegimeMaturedLabelDate=post[0]['formationDate'] if post else None,
            firstPostRegimeMaturedLabelAsOfDate=post[0]['asOfDate'] if post else None,
            fittedDaysWithPostRegimeMaturedLabels=sum(row['hasPostRegimeMaturedLabels'] for row in fitted),
            fittedDaysWithoutPostRegimeMaturedLabels=sum(not row['hasPostRegimeMaturedLabels'] for row in fitted),
            fallbackDaysWithPostRegimeMaturedLabels=sum(row['modelStatus'] == 'fallback' for row in post),
            postRegimeWeightShare=dist(row['postRegimeWeightShare'] for row in rows),
            postRegimeWeightShareOnFittedDays=dist(row['postRegimeWeightShare'] for row in fitted),
            eligibleFormationDays=dist(row['eligibleFormationDayCount'] for row in rows),
            postRegimeFormationDays=dist(row['postRegimeFormationDayCount'] for row in rows),
            eligibleEpisodes=dist(row['eligibleEpisodeCount'] for row in rows),
            postRegimeEpisodes=dist(row['postRegimeEpisodeCount'] for row in rows),
            basis='Decision-day snapshots; share is eligible post-9.24 formation days divided by all eligible formation days. Null means no usable matured labels. Not an adaptation-success threshold.'))
    return result


def verify_regime(directory):
    directory = Path(directory).resolve()
    require(not (directory / 'regime-maturity-validation.json').exists(), 'Never replace regime-maturity evidence')
    pins = {}
    def bound(item):
        path = (ROOT / item['path']).resolve()
        require(path.is_relative_to(ROOT) and sha(path) == item['sha256'], 'Changed regime input: ' + item['path'])
        pins[item['path']] = item['sha256']
        return path
    def local(name):
        path = directory / name
        return bound(dict(path=path.relative_to(ROOT).as_posix(), sha256=sha(path)))
    plan_file = local('plan.json'); plan = read(plan_file); plan_sha = sha(plan_file)
    main_file = local('readout.json'); main = read(main_file)
    proof_file = local('readout-validation.json'); proof = read(proof_file)
    paths_file = local('validation.json'); path_proof = read(paths_file)
    completion_file = local('library-completion.json'); completion = read(completion_file)
    side_file = local('regime-maturity-context.json'); side = read(side_file)
    require(proof['passed'] and path_proof['passed'] and proof['planSha256'] == path_proof['planSha256'] == plan_sha,
            'Passing exact independent library and readout proofs')
    require(proof['readoutSha256'] == sha(main_file) and proof['pathValidationSha256'] == sha(paths_file)
        and proof['libraryCompletionSha256'] == sha(completion_file) and proof['verifierSha256'] == sha(__file__), 'Readout proof lineage')
    for name, value in proof['sourcePins'].items():
        bound(dict(path=name, sha256=value))
    for name, expected in dict(planSha256=plan_sha, readoutSha256=sha(main_file), readoutValidationSha256=sha(proof_file),
        pathValidationSha256=sha(paths_file), libraryCompletionSha256=sha(completion_file)).items():
        same(side[name], expected, 'Descriptive sidecar lineage ' + name)
    require(side['posthoc'] is True and side['cutoff'] == CUT and side['newSignals'] == side['newCandidateAccounts'] == side['parameterChanges'] == 0,
            'Posthoc composition is not a strategy')
    for item in side['inputRefs']:
        bound(item)
    freeze = read(local('regime-maturity-source-pins.json'))
    require(freeze['planSha256'] == plan_sha, 'Regime source freeze plan')
    for name, value in freeze['sourcePins'].items():
        bound(dict(path=name, sha256=value))
    require(freeze['sourcePins']['scripts/summarize-independent-shadow-regime-maturity.py'] == side['extractorSha256'], 'Extractor frozen before use')
    old_groups = {tuple(row[name] for name in KEYS): row for row in read(bound(plan['parentInputCompletion']))['groups']}
    artifacts = {tuple(row[name] for name in KEYS): row for row in main['decisionArtifacts']}
    groups = [row for row in completion['groups'] if row['period'] == '2024-2025']
    require(len(groups) == 6, 'Six new-period source-exit groups')
    actual = iter(records(bound(side['dailyRowsArtifact'])))
    overall, epochs, day_count = [], [], 0
    for group in groups:
        key = {name: group[name] for name in KEYS}; p, b, e = (key[name] for name in KEYS); old = old_groups[(p, b, e)]
        metadata = read(bound(plan['metadata'][p][b])); environment = list(records(bound(plan['environment'][p][b])))
        old_labels = read(bound(old['shadow'])); schedule = read(bound(plan['baselineSchedules'][p][b]))
        require(path_proof['inputPins'].get(group['paths']['path']) == group['paths']['sha256'], 'Unaudited regime path library')
        paths = slim_paths(records(bound(group['paths'])), schedule, old_labels, key)
        symbols = {row['formationId']: row['symbol'] for row in paths}
        samples = dict(oldActual=old_labels, originalEntered=[row['label'] for row in paths if row['originalEntered'] and row['label'] is not None],
                       allExecutable=[row['label'] for row in paths if row['label'] is not None])
        for sample, labels in samples.items():
            full = reference().reference_build(metadata['calendar'], metadata['targetDates'], environment, labels)['dailyRows']
            saved_ref = old['decisions'] if sample == 'oldActual' else artifacts[(p, b, e)]['samples'][sample]
            saved_rows = iter(records(bound(saved_ref))); daily = []
            for row in full:
                frozen = next(saved_rows, None); require(frozen is not None, 'Missing frozen model date')
                if sample == 'oldActual':
                    same(frozen, row, 'Old actual fixed decisions')
                else:
                    same(frozen, dict(**key, sample=sample, **compact_reference(row, symbols)), 'Audited same-library fixed decisions')
                expected = dict(**key, sample=sample, **regime_day_reference(row))
                saved_day = next(actual, None); require(saved_day is not None, 'Missing regime day')
                same(saved_day, expected, 'Daily eligible-label era composition')
                daily.append(expected); day_count += 1
            require(next(saved_rows, None) is None, 'Extra fixed model dates')
            for interval in regime_spans_reference(dict(**key, sample=sample), daily):
                (overall if interval['id'] == 'overall' else epochs).append(interval)
    require(next(actual, None) is None, 'Extra regime composition dates')
    same(side['summaryRows'], overall, 'All era composition groups')
    same(side['epochRows'], epochs, 'All descriptive epoch compositions')
    expected_counts = dict(sourceExitGroups=6, samples=3, summaryRows=18, epochRows=36, dailyRows=day_count)
    same(side['counts'], expected_counts, 'Regime composition complete counts')
    require(day_count == side['dailyRowsArtifact']['count'] == 8730, 'All six x three x 485 days')
    require(all(sha(ROOT / name) == value for name, value in pins.items()), 'Regime input changed while auditing')
    result = dict(passed=True, posthoc=True, verifiedAt=datetime.now(timezone.utc).isoformat(), planSha256=plan_sha,
        contextSha256=sha(side_file), readoutSha256=sha(main_file), readoutValidationSha256=sha(proof_file),
        pathValidationSha256=sha(paths_file), libraryCompletionSha256=sha(completion_file), verifierSha256=sha(__file__),
        dailyRowsArtifactSha256=side['dailyRowsArtifact']['sha256'], inputPins=pins, sourcePins={**HELPERS,
            Path(__file__).relative_to(ROOT).as_posix(): sha(__file__)}, counts=expected_counts,
        checks=['All eligible labels independently rebuilt with the unchanged causal feedback reference',
            'Formation-day weights, post-boundary shares and null empty denominators independently matched',
            'First observed post-boundary maturity date and all descriptive group/epoch rows reconciled'],
        newCandidateAccounts=0, certified=False, targetAchieved=False)
    with (directory / 'regime-maturity-validation.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write('\n')
    print(json.dumps(dict(passed=True, regimeValidationSha256=sha(directory / 'regime-maturity-validation.json'), counts=expected_counts)), flush=True)
    return result


def synthetic_fixture():
    calendar = []; day = date(2024, 9, 2)
    while len(calendar) < 55:
        if day.weekday() < 5:
            calendar.append(day.isoformat())
        day += timedelta(days=1)
    environment = [dict(formationDate=day, asOfDate=calendar[i-1] if i else None,
        price20={'positiveReturn20Breadth': .5 if i else None}, amount={'medianRelativeToOwnPrior20': 1. if i else None},
        retention={'continuous': {'retentionLower': .6 if i else None}}) for i, day in enumerate(calendar)]
    key = dict(period='2024-2025', board='main', exit='trail10')
    schedule, paths, old = [], [], []
    for i in range(1, 22):
        fid = 'test' + str(i); symbol = 'symbol' + str(i)
        signal = dict(signalId=fid, strategyId='strongUndercutReclaimV1', symbol=symbol, signalDate=calendar[i], originalRank=1)
        label = dict(formationId=fid, formationDate=calendar[i], entryDate=calendar[i+1],
                     exitDate=calendar[24] if i <= 20 else None, netReturn=.1 if i <= 20 else None)
        entered_before = i <= 19
        if entered_before:
            old.append(deepcopy(label))
        outcome = dict(entered=True, entryDate=calendar[i+1], fullyClosed=i <= 20,
            labelAvailableDate=label['exitDate'], finalExitDate=label['exitDate'], investedAmount=100000.,
            netPnlAmount=10000. if i <= 20 else None)
        schedule.append(signal)
        paths.append(dict(**key, formationId=fid, formationDate=calendar[i], symbol=symbol,
            originalEntered=entered_before, originalDisposition=dict(filledQuantity=100 if entered_before else 0),
            path=dict(signal=signal, exitPolicy='trail10', status='closed' if i <= 20 else 'open', label=label, outcome=outcome)))
    old_daily = reference().reference_build(calendar, calendar, environment, old)['dailyRows']
    return dict(key=key, calendar=calendar, target_dates=calendar, environment_rows=environment,
                path_rows=paths, schedule=schedule, old_labels=old, old_decisions=old_daily)


def self_test():
    args = synthetic_fixture(); before = digest(args); got = group_reference(**args)
    require(digest(args) == before, 'Reference mutates evidence')
    a = got['decisionLibraries']['allExecutable'][25]; b = got['decisionLibraries']['originalEntered'][25]
    require(a['support']['eligibleFormationDayCount'] == 20 and b['support']['eligibleFormationDayCount'] == 19,
            'Same-label population support boundary')
    require(got['dailyComparisonRows'][25]['sameLibrarySelection']['fitState'] == 'candidateOnlyFitted', 'Common support state')
    require(a['immaturePopulation']['episodeCount'] == 1 and a['trainingPopulation']['episodeCount'] == 20, 'Open not zero label')
    lost = deepcopy(args); path = lost['path_rows'][0]['path']; path.update(status='unfilled', label=None)
    path['outcome'].update(entered=False, entryDate=None, fullyClosed=False, labelAvailableDate=None, finalExitDate=None,
                          investedAmount=None, netPnlAmount=None)
    lost_out = group_reference(**lost)
    require(len(lost_out['bridgeRows']) == 19 and lost_out['groupReadout']['bridge']['missingIndependentEntries'] == 1
        and lost_out['bridgeRows'][0]['netReturnDeltaPct'] is None, 'Missing independent entry cannot disappear or gain zero return')
    all_full = reference().reference_build(args['calendar'], args['target_dates'], args['environment_rows'],
        [row['path']['label'] for row in args['path_rows']])['dailyRows'][25]
    extra = deepcopy(all_full['trainingLabels'][0]); extra['formationId'] = 'extra'; extra['netReturn'] = .9
    expanded = [row['path']['label'] for row in args['path_rows']] + [{k: extra[k] for k in ('formationId', 'formationDate', 'entryDate', 'exitDate', 'netReturn')}]
    fit = reference().reference_build(args['calendar'], args['target_dates'], args['environment_rows'], expanded)['dailyRows'][25]
    same(fit['trainingSummary']['labelMean'], .12, 'Recomputed equal-day weighting')
    same(fit['trainingSummary']['weightSum'], 20., 'Day weight not episode weight')
    future = deepcopy(args)
    future['path_rows'][-1]['path']['label'].update(exitDate=args['calendar'][45], netReturn=9.)
    future['path_rows'][-1]['path']['status'] = 'closed'
    future['path_rows'][-1]['path']['outcome'].update(fullyClosed=True, labelAvailableDate=args['calendar'][45],
        finalExitDate=args['calendar'][45], netPnlAmount=900000.)
    future_out = group_reference(**future)
    for sample in SAMPLES:
        same(future_out['decisionLibraries'][sample][:46], got['decisionLibraries'][sample][:46], 'Future return cannot leak into prefix')
    for sample in (*SAMPLES, 'oldActual'):
        cells = [row for row in got['coverageRows'] + got['oldActualCoverageRows'] if row['sample'] == sample]
        full = next(row for row in cells if row['id'] == 'overall')
        split = [row for row in cells if row['kind'] == 'descriptiveEpoch']
        require(sum(row['dayCount'] for row in split) == full['dayCount']
            and sum(row['originalOpportunities'] for row in split) == full['originalOpportunities'], 'Formation-date partition')
    same(dist([None, 1., 3.]), dict(count=2, minimum=1., mean=2., median=2., maximum=3.), 'Null-safe distribution')
    era = regime_day_reference(all_full)
    expected_share = len({label['formationDate'] for label in all_full['trainingLabels'] if label['formationDate'] >= CUT}) / 20
    same(era['postRegimeWeightShare'], expected_share, 'Era uses eligible formation days, not exits or episode counts')
    empty = reference().reference_build(args['calendar'], args['target_dates'], args['environment_rows'], [])['dailyRows'][0]
    require(regime_day_reference(empty)['postRegimeWeightShare'] is None, 'Empty era denominator is unknown')
    return dict(passed=True, syntheticGroups=7, realSourceReads=0, accountExecutions=0, productionTestIncrement=0)


if __name__ == '__main__':
    parser = ArgumentParser(); parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--regime', action='store_true')
    parser.add_argument('--out', type=Path, default=DEFAULT); args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test()), flush=True)
    else:
        try:
            (verify_regime if args.regime else verify)(args.out)
        except Exception as error:
            folder = args.out / 'readout-validation-attempts'; folder.mkdir(parents=True, exist_ok=True)
            with (folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json')).open('x', encoding='utf-8') as stream:
                json.dump(dict(passed=False, at=datetime.now(timezone.utc).isoformat(), error=str(error), traceback=traceback.format_exc()),
                          stream, ensure_ascii=False, indent=2)
            raise
