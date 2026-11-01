"""Independent fixed-opportunity breadth-entry and saved-account verification.

Reuses the pinned independent prior context arithmetic, never the new production
filter. Original cached prices and saved account ledgers are read, not rerun.
"""
from __future__ import annotations

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
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/reclaim-breadth-entry-v1'
PARENT = ROOT / 'artifacts/strategy-search/strong-undercut-reclaim-timing-v1'
CONTEXT = ROOT / 'artifacts/strategy-search/undercut-reclaim-context-diagnostic-v1'
CACHE = ROOT / 'artifacts/strategy-search/peer-amount-persistence-v1'
PERIODS = ('2022-2023', '2024-2025')
MODES = ('knownContext', 'breadthMajority', 'breadthImproving', 'breadthBoth')
STATES = ('majorityImproving', 'majorityNotImproving', 'notMajorityImproving', 'notMajorityNotImproving', 'unknown')
FIELDS = ('signalId', 'strategyId', 'symbol', 'signalDate', 'originalRank')
SID = 'strongUndercutReclaimV1'
REGISTERED_PLAN_SHA = '9b16b0a9a3ab4ab4537ee802301a9b7ca790bcd6afb2f4b03ebfdfcaa7360644'
FILTER_SHA = '5f7adc364c19be86ca81969be4e181b0033dd92955d9887ef5f0cdbb711edbe4'
LABELS = {'knownContext': '环境完整，不加广度条件', 'breadthMajority': '上涨股票过半',
          'breadthImproving': '上涨范围扩大', 'breadthBoth': '上涨过半且范围扩大'}
CONTEXT_AUDITOR = 'scripts/verify-reclaim-context-diagnostic.py'
CONTEXT_AUDITOR_SHA = '9b899e4c84ca90d2d26be4ef3a2ae2910029fa726441e23bed7ccbf6f48c67c9'
ACCOUNT_AUDITOR = 'scripts/verify-peer-amount-persistence.py'
ACCOUNT_AUDITOR_SHA = 'cfaabb0262a33c7c6a5776f4fb54b7d0298df104abf8374ac87313ec6cccd026'
CONTEXT_VALIDATION_SHA = '9ecdabf3d86a40fed0ee03ecd6c2d79df5d7e717ae79115770f4502f607528f5'
PARENT_VALIDATION_SHA = '74e1e2eea635291342d615e702ae212fcb427303f2328bec142b33edc6400126'
CONTEXT_ROWS_SHA = '2f55d1e7951814ba7cfda969c4a4f38d76a41ba6c75fc787fc93da03a08ec5c3'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def same(actual, expected, label='root'):
    if finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8),
                f'{label}: {actual!r} != {expected!r}')
    elif isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), f'{label}: keys differ')
        for key in expected:
            same(actual[key], expected[key], label + '.' + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), f'{label}: lengths differ')
        for i, (a, b) in enumerate(zip(actual, expected)):
            same(a, b, f'{label}[{i}]')
    else:
        require(type(actual) is type(expected) and actual == expected, f'{label}: literal differs')


def read(path):
    path = Path(path)
    with (gzip.open(path, 'rt', encoding='utf-8') if path.name.endswith('.gz') else path.open(encoding='utf-8')) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def local(name):
    value = Path(name)
    require(not value.is_absolute() and '..' not in value.parts, 'Expected repository-relative path')
    value = (ROOT / value).resolve()
    require(value.is_relative_to(ROOT.resolve()), 'Reference outside repository')
    return value


def pinned(reference):
    require(isinstance(reference, dict) and set(reference) == {'path', 'sha256'}, 'Reference schema')
    path = local(reference['path'])
    require(sha(path) == reference['sha256'], 'Pinned file changed: ' + reference['path'])
    return path


def reference(name, expected):
    path = local(name)
    require(sha(path) == expected, 'Independent reference auditor changed: ' + name)
    spec = importlib.util.spec_from_file_location('_breadth_independent_' + path.stem.replace('-', '_'), path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def independent_masks(context):
    if context is None or context['status'] == 'unknown':
        require(context is None or context['state'] == 'unknown', 'Unknown status contradicts state')
        return dict.fromkeys(MODES)
    require(context['status'] == 'known' and context['state'] in STATES[:4], 'Invalid known context')
    require(type(context['pairedCount']) is int and context['pairedCount'] > 0, 'Known context empty paired population')
    level, earlier, change = (context[k] for k in ('positiveBreadthToday', 'positiveBreadthEarlier', 'matchedBreadthChange5'))
    require(all(finite(v) for v in (level, earlier, change)) and 0 <= level <= 1 and 0 <= earlier <= 1,
            'Incomplete known context numeric axes')
    require(abs(change - 100 * (level - earlier)) <= 1e-9, 'Same paired-denominator breadth arithmetic')
    major, improving = level > .5, change > 0
    require(context['state'] == STATES[(0 if major else 2) + (0 if improving else 1)], 'Known context state contradicts numeric axes')
    return {'knownContext': True, 'breadthMajority': major, 'breadthImproving': improving, 'breadthBoth': major and improving}


def independent_filter(signals, contexts, calendar):
    require(calendar and calendar == sorted(set(calendar)), 'Source calendar order')
    index = {d: i for i, d in enumerate(calendar)}
    by_day = {r['formationDate']: r for r in contexts}
    require(len(by_day) == len(contexts), 'Duplicate context date')
    for day, row in by_day.items():
        require(day in index, 'Context date outside calendar')
        i = index[day]
        require(row['asOfDate'] == (calendar[i - 1] if i else None)
                and row['earlierDate'] == (calendar[i - 6] if i >= 6 else None), 'Context T-1/T-6 identity')
    ids = [r['signalId'] for r in signals]
    require(len(ids) == len(set(ids)), 'Duplicate original signal ID')
    require(len({(r['signalDate'], r['symbol']) for r in signals}) == len(signals)
            and len({(r['signalDate'], r['originalRank']) for r in signals}) == len(signals), 'Duplicate date-symbol/date-rank identity')
    schedule, evidence = {m: [] for m in MODES}, {m: [] for m in MODES}
    counts = {m: {'included': 0, 'excluded': 0, 'unknown': 0} for m in MODES}
    for signal in signals:
        require(set(signal) == set(FIELDS) and signal['signalDate'] in index
                and type(signal['originalRank']) is int and 1 <= signal['originalRank'] <= 5, 'Original schedule schema/date/rank')
        require(signal['strategyId'] == SID and signal['signalId'] == f"{SID}:{signal['signalDate']}:{signal['symbol']}",
                'Original immediate formation signal identity')
        day = signal['signalDate']
        context = by_day.get(day)
        flags = independent_masks(context)
        for mode in MODES:
            flag = flags[mode]
            status = 'unknown' if flag is None else 'included' if flag else 'excluded'
            reason = ('missingContextRow' if context is None else context['reason']) if flag is None else (
                mode if flag else {'breadthMajority': 'notBreadthMajority', 'breadthImproving': 'notBreadthImproving',
                                   'breadthBoth': 'notBreadthBoth'}[mode])
            evidence[mode].append({'signalId': signal['signalId'], 'symbol': signal['symbol'], 'signalDate': day,
                'originalRank': signal['originalRank'], 'formationDate': day,
                'asOfDate': context['asOfDate'] if context else calendar[index[day] - 1] if index[day] else None,
                'state': context['state'] if context else 'unknown',
                'status': status, 'reason': reason})
            counts[mode][status] += 1
            if flag:
                schedule[mode].append(deepcopy(signal))
    selected_ids = {m: {r['signalId'] for r in rows} for m, rows in schedule.items()}
    require(selected_ids['breadthBoth'] == selected_ids['breadthMajority'] & selected_ids['breadthImproving']
            and selected_ids['breadthMajority'] | selected_ids['breadthImproving'] <= selected_ids['knownContext'],
            'Four-policy set relationships')
    return {'version': 'research-reclaim-breadth-v1', 'inputSignalCount': len(signals),
            'schedules': schedule, 'evidence': evidence, 'counts': counts}


def comparison_expected(summary, mode, label, period, signal_count):
    closed, account, whole = summary['closedEpisodes'], summary['account'], summary['periods'][0]
    return {'id': mode, 'label': label, 'signals': signal_count, 'closed': closed['count'],
        'winRatePct': closed['winRatePct'], 'meanWinPct': closed['meanWinReturnPct'],
        'meanLossPct': closed['meanLossReturnPct'], 'closedMeanPct': closed['meanReturnPct'],
        'payoffRatio': closed['payoffRatio'], 'profitFactor': closed['profitFactor'],
        'holdingSessions': closed['holdingSessions'], 'maxConsecutiveLosses': closed['maxConsecutiveLosses'],
        'accountReturnPct': account['netReturnPct'], 'maxDrawdownPct': account['maxDrawdownPct'],
        'meanExposurePct': whole['exposurePct']['mean'], 'openPositions': summary['openPositions']['count'],
        'unreliableValuationDays': len(summary['quality']['valuationUnreliableDates']),
        'top5ShareOfGrossProfitPct': summary['concentration']['top5ProfitSharePct'],
        'years': [{'id': r['id'], 'closed': r['closedEpisodes']['count'], 'closedMeanPct': r['closedEpisodes']['meanReturnPct'],
            'accountReturnPct': r['returnPct'], 'maxDrawdownPct': r['maxDrawdownPct']}
            for r in summary['periods'] if r['kind'] == 'calendarYear'],
        'phases': [{'id': r['id'], 'start': r['observedStart'], 'end': r['observedEnd'],
            'closed': r['closedEpisodes']['count'], 'accountReturnPct': r['returnPct'],
            'maxDrawdownPct': r['maxDrawdownPct'], 'meanExposurePct': r['exposurePct']['mean']}
            for r in summary['periods'] if r['kind'] == 'retrospectiveNarrativePhase'], 'period': period}


def verify_registration(directory):
    require(sha(directory / 'plan.json') == REGISTERED_PLAN_SHA, 'Preregistered plan changed')
    plan = read(directory / 'plan.json')
    require(plan['schema'] == 'reclaim-breadth-entry-v1' and plan['modes'] == list(MODES)
            and list(plan['periods']) == list(PERIODS) and plan['labels'] == LABELS and plan['strategyId'] == SID,
            'Registered mode/period/strategy identities')
    require(plan['newAccounts'] == 8 and plan['newPolicyVariants'] == 4 and plan['referenceAccountsReused'] == 2
            and plan['newSetupMechanisms'] == plan['newIndicators'] == 0 and plan['baselineMode'] == 'immediate',
            'Trial count or reused baseline identity')
    require(plan['parameterSearch'] is False and plan['profitHurdles'] is None and plan['holdoutClaim'] is False
            and plan['certified'] is False and plan['targetAchieved'] is False, 'Registered study scope')
    require(plan['sourcePins']['service/research_reclaim_breadth.py'] == FILTER_SHA, 'Registered production filter hash')
    pins = {**plan['sourcePins'], **plan['frozenInputs']}
    for name, expected in pins.items():
        require(sha(local(name)) == expected, 'Frozen code/input changed: ' + name)
    require(sha(PARENT / 'validation.json') == PARENT_VALIDATION_SHA
            and sha(CONTEXT / 'validation.json') == CONTEXT_VALIDATION_SHA
            and sha(CONTEXT / 'contexts.json') == CONTEXT_ROWS_SHA, 'Prior independent proof/context changed')
    parent_proof, context_proof = read(PARENT / 'validation.json'), read(CONTEXT / 'validation.json')
    require(parent_proof['passed'] is True and parent_proof['planSha256'] == sha(PARENT / 'plan.json')
            and parent_proof['comparisonSha256'] == sha(PARENT / 'comparison.json'), 'Parent account proof stale')
    require(context_proof['passed'] is True and context_proof['planSha256'] == sha(CONTEXT / 'plan.json')
            and context_proof['readoutSha256'] == sha(CONTEXT / 'readout.json')
            and context_proof['verifierSha256'] == CONTEXT_AUDITOR_SHA, 'Independent context proof stale')
    parent_plan, context_plan = read(PARENT / 'plan.json'), read(CONTEXT / 'plan.json')
    parent_completion = read(PARENT / 'input-completion.json')
    for period in PERIODS:
        spec, old = plan['periods'][period], parent_plan['periods'][period]
        require(spec['config'] == old['config'] and spec['sourceIdentity'] == old['sourceIdentity'], 'Source/account rules changed')
        require(spec['contextBinding'] == context_plan['marketBindings'][period], 'Original context lineage changed')
        expected_files = {'source.json', 'input-completion.json', 'signal-metadata.json', 'opportunities.jsonl.gz',
            'immediate.signals.json', 'immediate.result.json.gz', 'immediate.summary.json', 'immediate.checkpoint.json.gz'}
        require(set(spec['baselineFiles']) == expected_files, 'Original immediate baseline closure incomplete')
        for name, ref in spec['baselineFiles'].items():
            require(pinned(ref) == (PARENT / period / name).resolve() and pins[ref['path']] == ref['sha256'],
                    'Original baseline reference path/hash differs')
            if name in parent_proof['inputArtifacts'][period]:
                require(ref == parent_proof['inputArtifacts'][period][name], 'Baseline input differs from original audit')
            elif name in parent_proof['accountArtifacts'][period]:
                require(ref == parent_proof['accountArtifacts'][period][name], 'Baseline account differs from original audit')
            else:
                require(name == 'input-completion.json' and ref == parent_completion['outputs'][period], 'Unexpected baseline file')
        receipt = read(PARENT / period / 'input-completion.json')
        require(spec['accountData'] == receipt['accountData'] and pins[spec['accountData']['path']] == spec['accountData']['sha256'],
                'Original full account cache binding differs')
    return plan, parent_proof, context_plan, pins


def verify_inputs(directory, period, plan, context_plan, context_auditor):
    folder, spec = directory / period, plan['periods'][period]
    receipt = read(folder / 'input-completion.json')
    require(set(receipt) == {'completedAt', 'period', 'planSha256', 'counts', 'accountData', 'files'}
            and receipt['period'] == period and receipt['planSha256'] == REGISTERED_PLAN_SHA, 'Period input receipt schema')
    require(receipt['accountData'] == spec['accountData'], 'Receipt account dataset identity changed')
    expected_files = {'source.json', 'contexts.json', 'input-counts.json',
                      *(m + suffix for m in MODES for suffix in ('.signals.json', '.evidence.json'))}
    require(set(receipt['files']) == expected_files, 'Period input file closure')
    for name, ref in receipt['files'].items():
        require(pinned(ref) == (folder / name).resolve(), 'Period input artifact path differs')
    metadata, daily = context_auditor.market_evidence(period, context_plan)
    contexts = context_auditor.context_reference(metadata['calendar'], daily, metadata['targetDates'])
    require(contexts == read(CONTEXT / 'contexts.json')[period] == read(folder / 'contexts.json'),
            'Independent daily classification differs from frozen/copied context')
    require(all(r['state'] == 'unknown' for r in contexts[:6]), 'Startup missing context was filled')
    originals = records(pinned(spec['baselineFiles']['opportunities.jsonl.gz']))
    signals = read(pinned(spec['baselineFiles']['immediate.signals.json']))
    require(signals == [r['signals']['immediate'] for r in originals], 'Complete original immediate formation schedule')
    require(len(signals) == spec['originalSignalCount'] == {'2022-2023': 2093, '2024-2025': 1710}[period],
            'Original opportunities lost before filtering')
    for formation, signal in zip(originals, signals):
        i = metadata['calendar'].index(formation['formationDate'])
        require(formation['formationId'] == signal['signalId'] and formation['formationDate'] == signal['signalDate']
                and formation['priorDate'] == metadata['calendar'][i - 1], 'Original formation identity/prior calendar clock')
    expected = independent_filter(signals, contexts, metadata['calendar'])
    require(receipt['counts'] == read(folder / 'input-counts.json') == expected['counts'], 'All four full input decision counts')
    for mode in MODES:
        require(read(folder / (mode + '.signals.json')) == expected['schedules'][mode], 'Original-order exact schedule: ' + mode)
        require(read(folder / (mode + '.evidence.json')) == expected['evidence'][mode], 'Every original decision and reason: ' + mode)
        require(sum(expected['counts'][mode].values()) == len(signals), 'Excluded/unknown original opportunities disappeared')
    source = read(folder / 'source.json')
    require(source == spec['sourceIdentity'] and sha(folder / 'source.json') == spec['baselineFiles']['source.json']['sha256'],
            'Original account source changed')
    report = {'originalFormations': len(signals), 'independentContextDates': len(contexts),
        'contextStateDayCounts': dict(sorted(Counter(r['state'] for r in contexts).items())), 'counts': expected['counts'],
        'decisionsChecked': len(signals) * len(MODES), 'inputArtifacts': receipt['files'],
        'accountData': spec['accountData'], 'sourceIdentitySha256': sha(folder / 'source.json'),
        'originalOrderAndFieldsPreserved': True, 'contextComputedFromOriginalDaily': True}
    print(json.dumps({'phase': 'independent-inputs-passed', 'period': period,
        'originalFormations': len(signals), 'counts': expected['counts']}), flush=True)
    return report, metadata, source, expected['schedules']


def verify(directory):
    started = time.perf_counter()
    require(not (directory / 'validation.json').exists(), 'Refusing to overwrite validation.json')
    plan, parent_proof, context_plan, pins = verify_registration(directory)
    comparison = read(directory / 'comparison.json')
    require(comparison['schema'] == 'reclaim-breadth-entry-v1-comparison' and comparison['planSha256'] == REGISTERED_PLAN_SHA,
            'Comparison schema/plan binding')
    require(comparison['newPolicyVariants'] == 4 and comparison['newAccounts'] == 8 and comparison['referenceAccountsReused'] == 2
            and comparison['certified'] is False and comparison['targetAchieved'] is False, 'Comparison experiment counts/scope')
    require(comparison['limitations'] == plan['limitations'], 'Comparison limitations changed')
    require([(r['period'], r['id']) for r in comparison['rows']] == [(p, m) for p in PERIODS for m in MODES],
            'Complete ordered eight-account matrix')
    require(set(comparison['artifacts']) == set(PERIODS), 'Comparison artifact period closure')
    rows = {(r['period'], r['id']): r for r in comparison['rows']}
    completion, start = read(directory / 'input-completion.json'), read(directory / 'run-start.json')
    require(completion['planSha256'] == REGISTERED_PLAN_SHA and completion['allSchedulesFrozenBeforeAccounts'] is True
            and set(completion['outputs']) == set(PERIODS), 'Both-period schedules not frozen before accounts')
    require(pinned(start['inputCompletion']) == (directory / 'input-completion.json').resolve(), 'Run-start input freeze binding')
    require(datetime.fromisoformat(completion['completedAt']) <= datetime.fromisoformat(start['startedAt']), 'Account started before full input freeze')
    context_auditor = reference(CONTEXT_AUDITOR, CONTEXT_AUDITOR_SHA)
    accounting = reference(ACCOUNT_AUDITOR, ACCOUNT_AUDITOR_SHA)
    dependencies = {CONTEXT_AUDITOR: CONTEXT_AUDITOR_SHA, ACCOUNT_AUDITOR: ACCOUNT_AUDITOR_SHA,
        accounting.COST_REFERENCE: accounting.COST_REFERENCE_SHA, **accounting.cost_reference().REFERENCES}
    for name, expected in dependencies.items():
        require(sha(local(name)) == expected, 'Independent helper dependency changed')
    output = {'schema': 'reclaim-breadth-entry-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__),
        'planSha256': REGISTERED_PLAN_SHA, 'comparisonSha256': sha(directory / 'comparison.json'),
        'frozenCodeAndInputs': pins, 'independentReferences': dependencies,
        'parentAccountValidationSha256': PARENT_VALIDATION_SHA, 'parentContextValidationSha256': CONTEXT_VALIDATION_SHA,
        'inputArtifacts': {}, 'accountArtifacts': comparison['artifacts'], 'referenceAccounts': {}, 'periods': {},
        'certified': False, 'targetAchieved': False}
    for period in PERIODS:
        folder, spec = directory / period, plan['periods'][period]
        require(pinned(completion['outputs'][period]) == (folder / 'input-completion.json').resolve(), 'Period freeze receipt binding')
        audit, metadata, source, schedules = verify_inputs(directory, period, plan, context_plan, context_auditor)
        output['inputArtifacts'][period] = audit.pop('inputArtifacts')
        print(json.dumps({'phase': 'independent-bound-cache-load', 'period': period}), flush=True)
        dataset = read(pinned(spec['accountData']))
        require(digest(dataset) == source['accountDatasetDigest'] and dataset['calendar'] == metadata['calendar'],
                'Full bound account dataset digest/calendar differs')
        bars = {s['symbol']: {r['date']: r for r in s['bars']} for s in dataset['securities']}
        require(len(bars) == len(dataset['securities']) == 3141, 'Original complete sample cache')
        expected_files = {m + suffix for m in MODES for suffix in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json')}
        require(set(comparison['artifacts'][period]) == expected_files, 'Twelve candidate account files per period required')
        for name, ref in comparison['artifacts'][period].items():
            require(pinned(ref) == (folder / name).resolve(), 'Candidate account artifact path')
        accounts = {}
        for mode in MODES:
            result, checkpoint, summary = (read(folder / (mode + suffix))
                for suffix in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json'))
            schedule = schedules[mode]
            closes = {(r['symbol'], r['signalDate']): bars[r['symbol']].get(r['signalDate'], {}).get('close') for r in schedule}
            accounts[mode] = accounting.verify_saved_account(result, summary, schedule, spec['config'], source,
                dataset['calendar'], closes, period, SID)
            accounting.verify_checkpoint(checkpoint, result)
            for fill in result['trades']:
                raw = bars[fill['symbol']].get(fill['date'])
                require(raw is not None, 'Actual fill has no cached source bar')
                accounting.same(fill['openingPrice'], raw['open'], 'Saved fill raw opening price')
            require(summary['policyId'] == mode and summary['policyLabel'] == LABELS[mode], 'Candidate summary policy identity')
            accounting.same(rows[period, mode], comparison_expected(summary, mode, LABELS[mode], period, len(schedule)),
                            'Every reported candidate comparison field')
            print(json.dumps({'phase': 'independent-account-passed', 'period': period, 'mode': mode,
                'signals': len(schedule), 'fills': accounts[mode]['fills']}), flush=True)
            del result, checkpoint, summary
        baseline_refs = {n: spec['baselineFiles'][n] for n in ('immediate.result.json.gz', 'immediate.summary.json', 'immediate.checkpoint.json.gz')}
        base_result, base_summary, base_checkpoint = (read(pinned(baseline_refs[n])) for n in baseline_refs)
        require(base_summary['inputResultSha256'] == digest(base_result)
                and base_result['research']['identity']['signalSchedule'] == read(PARENT / period / 'immediate.signals.json')
                and base_result['config'] == spec['config'], 'Reused original baseline changed')
        accounting.verify_checkpoint(base_checkpoint, base_result)
        output['referenceAccounts'][period] = {'artifacts': baseline_refs, 'independentAuditInherited': True,
            'parentValidationSha256': PARENT_VALIDATION_SHA, 'rerun': False}
        audit['accounts'] = accounts
        output['periods'][period] = audit
        del dataset, bars, base_result, base_summary, base_checkpoint
        gc.collect()
    output['counts'] = {'originalFormations': sum(r['originalFormations'] for r in output['periods'].values()),
        'contextDatesRecomputed': sum(r['independentContextDates'] for r in output['periods'].values()),
        'fullPolicyDecisionsChecked': sum(r['decisionsChecked'] for r in output['periods'].values()),
        'newCandidateAccountsChecked': 8, 'reusedReferenceAccountsBound': 2, 'newPolicyVariants': 4,
        'actualCandidateFillsChecked': sum(a['fills'] for p in output['periods'].values() for a in p['accounts'].values())}
    require(output['counts']['originalFormations'] == 3803 and output['counts']['fullPolicyDecisionsChecked'] == 15212
            and output['counts']['contextDatesRecomputed'] == 969, 'All original opportunity/context coverage')
    output['checks'] = [
        'Preregistered new filter/code/source hashes and exact inherited independent account/context evidence',
        'All 969 original market daily classifications independently rebuilt at exact T-1/T-6 from same-population breadth endpoints',
        'All 3803 original immediate formation identities/order/ranks retained across 15212 included/excluded/unknown decisions',
        'Four independent numeric masks, common unknown exclusion, strict thresholds and exact no-backfill original-order schedules',
        'Both complete-period input schedules frozen before any candidate account began',
        'Eight complete saved accounts: fees/slippage, cash/shares, T+1, budgets, fills/exits, closed episodes/open positions, curves/checkpoints and summaries',
        'All reported comparison statistics reconciled; two original immediate references remain exact immutable inherited accounts']
    output['scope'] = {'newAccountsExecutedByRunner': 8, 'accountsExecutedByVerifier': 0,
        'newFilterModuleImported': False, 'originalRawMarketReloaded': False,
        'contextCalculation': 'Pinned earlier independent arithmetic recomputed from original audited market daily records.',
        'accountCalculation': 'Pinned independent ledger auditors; no separate resimulation of every eligibility/capacity/order gate.',
        'limitations': ['Original daily market features, raw-source provenance and setup formation proof inherit exact prior independent audits.',
            'Four policies are complete path comparisons on repeatedly inspected development histories, not randomized causal factors or untouched holdouts.',
            'Later phase/epoch readouts are outside this account validation and require their own exact validation binding.']}
    output['elapsedSeconds'] = time.perf_counter() - started
    with (directory / 'validation.json').open('x', encoding='utf-8') as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed': True, 'path': str(directory / 'validation.json'), 'sha256': sha(directory / 'validation.json'),
                      'counts': output['counts'], 'elapsedSeconds': output['elapsedSeconds']}), flush=True)


READOUT_POLICIES = ('immediate', *MODES)
CONTRASTS = (('knownVsOriginal', 'immediate', 'knownContext'),
             ('majorityVsKnown', 'knownContext', 'breadthMajority'),
             ('improvingVsKnown', 'knownContext', 'breadthImproving'),
             ('bothVsMajority', 'breadthMajority', 'breadthBoth'),
             ('bothVsImproving', 'breadthImproving', 'breadthBoth'))


def readout_formation_counts(original, result, mode, start, end, ctx):
    opportunities = {r['signalId']: r for r in result['research']['opportunities']}
    selected = [r for r in original if start <= r['formationDate'] <= end]
    admitted = [opportunities[r['formationId']] for r in selected if r['formationId'] in opportunities]
    entered = sum(finite(r.get('filledQuantity')) and r['filledQuantity'] > 0 for r in admitted)
    statuses = Counter(r['gateDispositions'][mode]['status'] for r in selected)
    require(statuses['admitted'] == len(admitted), 'Complete formation gate/account admission bridge')
    return {'formationTop5': len(selected), 'contextStateCounts': ctx.labels(r['contextState'] for r in selected),
        'gateStatusCounts': {k: statuses[k] for k in ('admitted', 'rejected', 'unknown')},
        'gateReasonCounts': ctx.labels(r['gateDispositions'][mode]['reason'] for r in selected),
        'selectedSignalsFromFormation': len(admitted), 'notScheduledFromFormation': len(selected) - len(admitted),
        'enteredFromFormation': entered, 'notEnteredFromFormation': len(admitted) - entered,
        'terminalStatusCounts': ctx.labels(r.get('status', 'missing') for r in admitted),
        'terminalReasonCounts': ctx.labels(r.get('reason', 'missing') for r in admitted),
        'meaning': 'formationDate cohort; gate, later account execution and cutoff status are separate'}


def readout_interval_reference(result, original, mode, start, end, ctx):
    expected = ctx.curve_interval(result, start, end)
    queued = [r for r in result['decisions'] if start <= r['date'] <= end
              and r.get('action') == 'sell' and r.get('status') == 'queued']
    expected['queuedExitReasons'] = ctx.labels(r.get('reason', 'missing') for r in queued)
    expected['queuedExitDecisionCount'] = len(queued)
    expected['queuedExitDefinition'] = 'decision.action == sell AND decision.status == queued; queued decisions are not sell fills'
    expected['opportunitiesByFormationDate'] = readout_formation_counts(original, result, mode, start, end, ctx)
    return expected


def readout_dispositions(result):
    buys = {r['signalId']: r for r in result['trades'] if r['side'] == 'buy'}
    require(len(buys) == sum(r['side'] == 'buy' for r in result['trades']), 'Readout multiple buys per signal')
    episodes = {(r['symbol'], r['entryDate']): r for r in result['closedTrades']}
    require(len(episodes) == len(result['closedTrades']), 'Readout duplicate episode identity')
    output = {}
    for row in result['research']['opportunities']:
        sid = row['signalId']
        require(sid not in output, 'Readout duplicate opportunity identity')
        buy = buys.get(sid)
        episode = episodes.get((buy['symbol'], buy['date'])) if buy else None
        require((buy is not None) == (finite(row.get('filledQuantity')) and row['filledQuantity'] > 0), 'Opportunity entry quantity')
        output[sid] = {'signalId': sid, 'signalDate': row['signalDate'], 'entryDate': buy['date'] if buy else None,
            'exitDate': episode['exitDate'] if episode else None,
            'positionState': 'closed' if episode else 'openAtCutoff' if buy else 'notEntered',
            'status': row['status'], 'reason': row['reason'], 'filledQuantity': row.get('filledQuantity'),
            'attemptDate': row.get('attemptDate'), 'closedReturnPct': episode.get('returnPct') if episode else None,
            'closedPnlAmount': episode.get('pnlAmount') if episode else None}
    return output


def readout_metrics(account):
    values = {k: v for k, v in account['comparisonRow'].items() if v is None or finite(v)}
    curve, summary, bridge = account['curveAttribution'], account['account'], account['reconciliation']
    values.update(netPnlAmount=summary['netPnlAmount'], finalEquity=summary['finalEquity'],
        closedPnlAmount=bridge['closedEpisodePnl'], openRealizedPnlAmount=bridge['openPositionRealizedPnl'],
        openUnrealizedPnlAmount=bridge['openPositionUnrealizedPnl'], investedSessionPct=curve['investedSessionPct'],
        feesPaid=curve['feesPaid'], slippageAlreadyInFillPrices=curve['slippageAlreadyInFillPrices'],
        buyFillCount=curve['buyFillCount'], sellFillCount=curve['sellFillCount'],
        notEntered=curve['opportunitiesByDecisionDate']['notEntered'],
        notScheduled=curve['opportunitiesByFormationDate']['notScheduledFromFormation'],
        top1ShareOfGrossProfitPct=account['concentration']['top1ProfitSharePct'],
        top5ShareOfGrossProfitPct=account['concentration']['top5ProfitSharePct'])
    return values


def verify_readout(directory):
    started = time.perf_counter()
    require(not (directory / 'readout-validation.json').exists(), 'Readout validation already exists')
    plan, _, _, _ = verify_registration(directory)
    proof, readout = read(directory / 'validation.json'), read(directory / 'readout.json')
    comparison, parent_comparison = read(directory / 'comparison.json'), read(PARENT / 'comparison.json')
    require(proof['passed'] is True and proof['planSha256'] == REGISTERED_PLAN_SHA
            and proof['comparisonSha256'] == sha(directory / 'comparison.json')
            and proof['verifierSha256'] == sha(__file__), 'Main account validation stale')
    require(readout['schema'] == 'reclaim-breadth-entry-readout-v1' and readout['planSha256'] == REGISTERED_PLAN_SHA
            and readout['verification']['passed'] is True
            and pinned(readout['verification']) == (directory / 'validation.json').resolve(), 'Readout validation binding')
    for name, expected in readout['inputPins'].items():
        require(sha(local(name)) == expected, 'Readout source binding changed: ' + name)
    ctx = reference(CONTEXT_AUDITOR, CONTEXT_AUDITOR_SHA)
    expected_matrix = [(p, m) for p in PERIODS for m in READOUT_POLICIES]
    require([(r['period'], r['policy']) for r in readout['accounts']] == expected_matrix, 'Complete ten-account readout order')
    accounts = {(r['period'], r['policy']): r for r in readout['accounts']}
    comparison_rows = {(r['period'], r['id']): r for r in comparison['rows']}
    comparison_rows.update({(r['period'], r['id']): r for r in parent_comparison['rows'] if r['id'] == 'immediate'})
    same(readout['accountRows'], [comparison_rows[key] for key in expected_matrix], 'Readout reported ten-account comparison rows')
    require(len(readout['opportunityRows']) == 3803, 'Original formation readout count')
    formation_index = {(r['period'], r['formationId']): r for r in readout['opportunityRows']}
    require(len(formation_index) == 3803, 'Readout repeated formation identity')
    atlas_path = ROOT / 'artifacts/strategy-search/market-phase-atlas-v1/atlas.json'
    require(sha(atlas_path) == 'ddf55f2741dca3793923e5da782690aad500510848077f43492fff41af1a44d8', 'Frozen phase atlas changed')
    atlas = read(atlas_path)['phases']
    expected_lengths = {'yearRows': 20, 'halfYearRows': 40, 'phaseRows': 80, 'curveEpochRows': 10, 'coverageRows': 10}
    require(all(len(readout[k]) == v for k, v in expected_lengths.items()), 'Complete period matrices')
    interval_count, disposition_count = 0, 0
    for period in PERIODS:
        folder, spec = directory / period, plan['periods'][period]
        originals = records(PARENT / period / 'opportunities.jsonl.gz')
        contexts = {r['formationDate']: r for r in read(folder / 'contexts.json')}
        evidence = {m: read(folder / (m + '.evidence.json')) for m in MODES}
        expected_ids = [r['formationId'] for r in originals]
        require([r['formationId'] for r in readout['opportunityRows'] if r['period'] == period] == expected_ids,
                'Readout original formation order changed')
        normalized = []
        for n, original in enumerate(originals):
            signal, day = original['signals']['immediate'], original['formationDate']
            gates = {'immediate': {'status': 'admitted', 'reason': 'originalImmediate'}}
            signals = {'immediate': signal}
            for mode in MODES:
                e = evidence[mode][n]
                gates[mode] = {'status': {'included': 'admitted', 'excluded': 'rejected', 'unknown': 'unknown'}[e['status']], 'reason': e['reason']}
                signals[mode] = signal if e['status'] == 'included' else None
            expected = {'formationId': original['formationId'], 'formationDate': day, 'symbol': signal['symbol'],
                'originalRank': signal['originalRank'], 'originalSignal': signal, 'contextState': contexts[day]['state'],
                'context': contexts[day], 'gateEvidence': {m: evidence[m][n] for m in MODES}, 'gateDispositions': gates,
                'signals': signals, 'formationEvidence': original, 'period': period}
            saved = formation_index[period, original['formationId']]
            same({k: v for k, v in saved.items() if k != 'accountDispositions'}, expected, 'All original formation evidence')
            require(set(saved['accountDispositions']) == set(READOUT_POLICIES), 'Five policy dispositions per formation')
            normalized.append(saved)
        for mode in READOUT_POLICIES:
            is_base = mode == 'immediate'
            source_folder = PARENT / period if is_base else folder
            result, summary, schedule = (read(source_folder / (mode + s))
                for s in ('.result.json.gz', '.summary.json', '.signals.json'))
            account = accounts[period, mode]
            same(account['comparisonRow'], comparison_rows[period, mode], 'Readout account comparison copy')
            for target, source in (('account', 'account'), ('closedEpisodes', 'closedEpisodes'), ('unresolvedExits', 'unresolvedExits'),
                    ('concentration', 'concentration'), ('reconciliation', 'reconciliation'), ('sourceQuality', 'sourceQuality'),
                    ('quality', 'quality'), ('rights', 'rights'), ('opportunityDispositions', 'opportunityDispositions')):
                same(account[target], summary[source], 'Readout immutable summary ' + target)
            same(account['warnings'], summary.get('warnings', []), 'Readout complete warnings')
            same(account['sourceQualityScalarFlags'], {k: v for k, v in summary['sourceQuality'].items()
                 if not isinstance(v, (dict, list))}, 'Readout scalar source flags')
            require(account['execution'] == ('reused-original-immediate' if is_base else 'new-independent-account')
                    and account['label'] == summary['policyLabel'], 'Readout execution identity')
            for suffix, ref in account['artifactRefs'].items():
                require(pinned(ref) == (source_folder / (mode + suffix)).resolve(), 'Readout account source paths')
            dispositions = readout_dispositions(result)
            require(set(dispositions) == {r['signalId'] for r in schedule}, 'Readout schedule disposition closure')
            for original in normalized:
                sid, day = original['formationId'], original['formationDate']
                gate = original['gateDispositions'][mode]
                detail = dispositions.get(sid)
                require((detail is not None) == (gate['status'] == 'admitted'), 'Readout rejected formation became account opportunity')
                expected = {'formationId': sid, 'formationDate': day, 'decisionDate': day if detail else None,
                    'gateStatus': gate['status'], 'gateReason': gate['reason'], **(detail if detail else {
                        'signalId': sid, 'signalDate': None, 'entryDate': None, 'exitDate': None, 'positionState': 'notScheduled',
                        'status': 'notScheduled', 'reason': gate['reason'], 'filledQuantity': 0, 'attemptDate': None,
                        'closedReturnPct': None, 'closedPnlAmount': None})}
                same(original['accountDispositions'][mode], expected, 'Readout every original account disposition')
                disposition_count += 1
            overall = readout_interval_reference(result, normalized, mode, spec['config']['startDate'], spec['config']['endDate'], ctx)
            same(account['curveAttribution'], overall, 'Readout whole curve independent arithmetic')
            same(account['closedDistribution'], overall['closedEpisodesByExitDate'], 'Readout whole closed distribution')
            positions = deepcopy(summary['openPositions'])
            positions['unrealizedPnlAmount'] = math.fsum(r['unrealizedPnlAmount'] for r in positions['positions'])
            positions['realizedPnlWhileOpen'] = math.fsum(r['realizedPnlWhileOpen'] for r in positions['positions'])
            same(account['openPositionsAtCutoff'], positions, 'Readout all terminal holdings')
            bridge = summary['reconciliation']
            same(math.fsum([overall['closedEpisodesByExitDate']['netPnlAmount'], positions['unrealizedPnlAmount'],
                           positions['realizedPnlWhileOpen'], bridge['rightsAttributionGapAmount']]),
                 overall['netPnlAmount'], 'Readout closed/open/rights monetary bridge')
            grouped = {key: [r for r in readout[key] if r['period'] == period and r.get('policy', r.get('accountMode')) == mode]
                       for key in ('yearRows', 'halfYearRows', 'phaseRows', 'curveEpochRows')}
            for key, rows in grouped.items():
                if key == 'curveEpochRows' and period == PERIODS[0]:
                    require(not rows, 'Old period has extraneous user epoch')
                    continue
                expected_windows = ([(r['requestedStart'], r['requestedEnd']) for r in summary['periods']
                    if r['kind'] == ('calendarYear' if key == 'yearRows' else 'calendarHalfYear')]
                    if key in ('yearRows', 'halfYearRows') else
                    [(r['startDate'], r['endDate']) for r in atlas if r['startDate'] <= spec['config']['endDate']
                      and r['endDate'] >= spec['config']['startDate']] if key == 'phaseRows' else
                    [('2024-01-02', '2024-09-23'), ('2024-09-24', '2025-12-31')])
                require([(r['requestedStart'], r['requestedEnd']) for r in rows] == expected_windows, 'Readout exact interval set: ' + key)
                for row in rows:
                    expected = readout_interval_reference(result, normalized, mode, row['requestedStart'], row['requestedEnd'], ctx)
                    same({k: row[k] for k in expected}, expected, 'Independent interval arithmetic ' + key)
                    interval_count += 1
                same(math.fsum(r['netPnlAmount'] for r in rows), overall['netPnlAmount'], 'Interval PnL sum')
                same((math.prod(1 + r['returnPct'] / 100 for r in rows) - 1) * 100, overall['returnPct'], 'Interval return compounding')
                require(sum(r['observedSessions'] for r in rows) == overall['observedSessions']
                        and sum(r['opportunitiesByFormationDate']['formationTop5'] for r in rows) == len(originals), 'Interval complete source/formation coverage')
            coverage = [r for r in readout['coverageRows'] if r['period'] == period and r['policy'] == mode]
            require(len(coverage) == 1, 'One complete coverage row per account')
            expected_coverage = {'period': period, 'id': mode, 'policy': mode, **overall['opportunitiesByFormationDate'],
                **overall['opportunitiesByDecisionDate'], 'unknownContextFormations': sum(r['contextState'] == 'unknown' for r in normalized),
                'knownContextFormations': sum(r['contextState'] != 'unknown' for r in normalized),
                'buyFillCount': overall['buyFillCount'], 'sellFillCount': overall['sellFillCount'],
                'closedCount': len(result['closedTrades']), 'openCountAtCutoff': positions['count'],
                'unresolvedExitCountAtCutoff': summary['unresolvedExits']['count'], 'curveSessions': len(result['curve']), 'baselineReused': is_base}
            same(coverage[0], expected_coverage, 'Full original formation coverage matrix')
    require([(r['period'], r['id'], r['control'], r['candidate']) for r in readout['comparisons']]
            == [(p, *c) for p in PERIODS for c in CONTRASTS], 'All ten preregistered contrasts')
    for row in readout['comparisons']:
        control, candidate = (readout_metrics(accounts[row['period'], row[k]]) for k in ('control', 'candidate'))
        same(row['controlMetrics'], control, 'Full contrast control metrics')
        same(row['candidateMetrics'], candidate, 'Full contrast candidate metrics')
        same(row['candidateMinusControl'], {k: candidate[k] - control[k] if finite(candidate[k]) and finite(control[k]) else None
                                          for k in control}, 'Every policy metric difference')
    same(readout['knownVsOriginalRows'], [r for r in readout['comparisons'] if r['id'] == 'knownVsOriginal'], 'Availability contrast retained')
    require([r['period'] for r in readout['descriptiveInteractionRows']] == list(PERIODS), 'Both descriptive interactions')
    for row in readout['descriptiveInteractionRows']:
        metrics = {m: readout_metrics(accounts[row['period'], m]) for m in MODES}
        same(row['values'], {k: metrics[MODES[3]][k] - metrics[MODES[1]][k] - metrics[MODES[2]][k] + metrics[MODES[0]][k]
             if all(finite(metrics[m][k]) for m in MODES) else None for k in metrics[MODES[0]]}, 'Descriptive B-L-M+K arithmetic')
    require(interval_count == 150 and disposition_count == 19015, 'Complete readout audit coverage')
    output = {'schema': 'reclaim-breadth-entry-readout-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'planSha256': REGISTERED_PLAN_SHA,
        'comparisonSha256': sha(directory / 'comparison.json'), 'accountValidationSha256': sha(directory / 'validation.json'),
        'readoutSha256': sha(directory / 'readout.json'), 'verifierSha256': sha(__file__),
        'independentReferences': {CONTEXT_AUDITOR: CONTEXT_AUDITOR_SHA}, 'inputPins': readout['inputPins'],
        'counts': {'accounts': 10, 'originalFormations': 3803, 'policyDispositions': disposition_count,
                   'fullCurveIntervals': 10, 'subIntervals': interval_count, **expected_lengths, 'comparisons': 10, 'descriptiveInteractions': 2},
        'checks': ['Every original formation, all five admission and account dispositions, exact identities/order and null unclosed returns',
            'Ten complete curves and 150 calendar intervals independently recomputed including carried equity, drawdown, exposure and holding observations',
            'Queued sell decisions distinguished from sell fills; original formation cohorts distinguished from admitted opportunities',
            'Full summary/terminal holdings, closed/open/rights monetary bridge, interval amount addition and return compounding',
            'All preregistered comparison metrics and descriptive interaction arithmetic'],
        'scope': {'accountsRerun': 0, 'productionSummarizerImported': False, 'certified': False,
            'inheritedAccountValidation': 'Exact prior independent account validation; this pass verifies readout calculations only.'},
        'elapsedSeconds': time.perf_counter() - started}
    with (directory / 'readout-validation.json').open('x', encoding='utf-8') as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed': True, 'scope': 'readout', 'sha256': sha(directory / 'readout-validation.json'), 'counts': output['counts']}), flush=True)


def self_test():
    checks = []
    calendar = [(date(2020, 1, 1) + timedelta(days=i)) .isoformat() for i in range(24)]
    contexts, signals = [], []
    for n, (today, earlier) in enumerate(((.6, .4), (.6, .6), (.5, .4), (.5, .5)), 10):
        state = STATES[(0 if today > .5 else 2) + (0 if today - earlier > 0 else 1)]
        contexts.append({'formationDate': calendar[n], 'asOfDate': calendar[n - 1], 'earlierDate': calendar[n - 6],
            'state': state, 'status': 'known', 'reason': 'classified', 'pairedCount': 100,
            'positiveBreadthToday': today, 'positiveBreadthEarlier': earlier, 'matchedBreadthChange5': 100 * (today - earlier)})
        signals.append({'signalId': f'{SID}:{calendar[n]}:s{n}', 'strategyId': SID, 'symbol': f's{n}',
                        'signalDate': calendar[n], 'originalRank': n - 9})
    before = deepcopy((signals, contexts))
    value = independent_filter(signals, contexts, calendar)
    require([len(value['schedules'][m]) for m in MODES] == [4, 2, 2, 1], 'Four policy definitions do not isolate level/change gates')
    require(value['schedules']['breadthMajority'] == signals[:2]
            and value['schedules']['breadthImproving'] == signals[::2], 'Strict >.5/>0 equality or order differs')
    require((signals, contexts) == before, 'Reference mutated its inputs')
    checks.append('all four state combinations, strict equality, complete original fields/order and input immutability')

    extra = {**signals[-1], 'signalId': f'{SID}:{calendar[14]}:s13', 'signalDate': calendar[14]}
    unknown = {'formationDate': calendar[14], 'asOfDate': calendar[13], 'earlierDate': calendar[8],
               'state': 'unknown', 'status': 'unknown', 'reason': 'matchedComparisonUnavailable'}
    value = independent_filter(signals + [extra], contexts + [unknown], calendar)
    require(all(value['counts'][m]['unknown'] == 1 and value['evidence'][m][-1]['reason'] == unknown['reason']
                and extra not in value['schedules'][m] for m in MODES), 'Unknown not common to all four policies')
    absent = independent_filter([extra], contexts, calendar)
    require(all(absent['evidence'][m][0]['reason'] == 'missingContextRow'
                and absent['evidence'][m][0]['asOfDate'] == calendar[13] for m in MODES), 'Missing context exact prior clock')
    checks.append('shared unknown exclusion and missing context preserve exact preceding source day')

    same_day = [{**signals[0], 'signalId': f'{SID}:{calendar[10]}:z', 'symbol': 'z', 'originalRank': 3},
                {**signals[0], 'signalId': f'{SID}:{calendar[10]}:a', 'symbol': 'a', 'originalRank': 1}]
    value = independent_filter(same_day, contexts, calendar)
    require(all(value['schedules'][m] == same_day for m in MODES), 'Sorting/backfilling changed supplied original order/ranks')
    require(all(len(value['evidence'][m]) == 2 for m in MODES), 'Same-day repeated signals were deduplicated')
    checks.append('rank gaps and supplied order remain intact without same-date deduplication or backfill')

    last = {**signals[0], 'signalId': f'{SID}:{calendar[-1]}:s10', 'signalDate': calendar[-1]}
    final_context = {**contexts[0], 'formationDate': calendar[-1], 'asOfDate': calendar[-2], 'earlierDate': calendar[-7]}
    value = independent_filter([last], [final_context], calendar)
    require(all(value['schedules'][m] == [last] for m in MODES), 'Last-calendar-day opportunity was prefiltered by future execution')
    checks.append('last-session accepted signal retained for account cutoff handling')

    for field, changed in (('positiveBreadthToday', None), ('state', STATES[1]), ('matchedBreadthChange5', 0.)):
        broken = deepcopy(contexts)
        broken[0][field] = changed
        try:
            independent_filter(signals, broken, calendar)
        except ValueError:
            pass
        else:
            raise ValueError('Corrupted known context admitted: ' + field)
    for field in ('asOfDate', 'earlierDate'):
        broken = deepcopy(contexts)
        broken[0][field] = calendar[0]
        try:
            independent_filter(signals, broken, calendar)
        except ValueError:
            pass
        else:
            raise ValueError('Noncausal context clock admitted: ' + field)
    checks.append('known numeric/state corruption and nonexact T-1/T-6 dates rejected')

    future = {**final_context, 'positiveBreadthToday': 99.}
    require(independent_filter(signals, contexts + [future], calendar) == independent_filter(signals, contexts, calendar),
            'Unused future numerical evidence changed earlier schedules')
    for new_signals, new_contexts in ((signals + signals[:1], contexts), (signals, contexts + contexts[:1])):
        try:
            independent_filter(new_signals, new_contexts, calendar)
        except ValueError:
            pass
        else:
            raise ValueError('Duplicate signal/context identity admitted')
    checks.append('future unused values ignored; duplicate original signal or context identity rejected')

    ctx = reference(CONTEXT_AUDITOR, CONTEXT_AUDITOR_SHA)
    market = [{'date': calendar[9], 'fixedPopulation': 100, 'matched5': {'available': True,
        'earlierDate': calendar[4], 'pairedCount': 80, 'todayEvaluableCount': 90, 'earlierEvaluableCount': 95,
        'positiveBreadthToday': .5, 'positiveBreadthEarlier': .4, 'matchedBreadthChange5': 100 * (.5 - .4),
        'pairedCoverageToday': 80 / 90, 'pairedCoverageEarlier': 80 / 95, 'pairedCoverageFixed': .8}}]
    independent_context = ctx.context_reference(calendar, market, [calendar[10]])
    answer = independent_filter(signals[:1], independent_context, calendar)
    require([len(answer['schedules'][m]) for m in MODES] == [1, 0, 1, 0], 'Independent raw daily -> context -> policy pipeline')
    checks.append('pinned independent daily arithmetic feeds new masks without importing production filter')

    days = calendar[10:13]
    fake = {'config': {'initialCapital': 100.},
        'curve': [{'date': d, 'equity': eq, 'cash': eq / 2, 'marketValue': eq / 2, 'valuationReliable': True}
                  for d, eq in zip(days, (100., 80., 90.))],
        'daily': [{'date': d, 'holdings': []} for d in days], 'trades': [], 'closedTrades': [],
        'research': {'identity': {'signalSchedule': []}, 'opportunities': []},
        'decisions': [{'date': days[1], 'action': a, 'status': s, 'reason': r} for a, s, r in
                      (('sell', 'queued', 'count'), ('sell', 'rejected', 'skip'), ('exit', 'queued', 'skip'))]}
    formed = [{'formationId': 'a', 'formationDate': days[1], 'contextState': 'unknown',
               'gateDispositions': {'breadthBoth': {'status': 'unknown', 'reason': 'missing'}}}]
    measured = readout_interval_reference(fake, formed, 'breadthBoth', days[1], days[2], ctx)
    require(measured['maxDrawdownPct'] == 20. and math.isclose(measured['returnPct'], -10.)
            and measured['enteringEquity'] == 100. and measured['queuedExitReasons'] == {'count': 1}
            and measured['queuedExitDecisionCount'] == 1, 'Readout carried equity/drawdown/queued exit semantics')
    require(measured['opportunitiesByFormationDate']['formationTop5'] == 1
            and measured['opportunitiesByFormationDate']['notScheduledFromFormation'] == 1
            and measured['opportunitiesByDecisionDate']['selectedSignals'] == 0,
            'Readout original formation denominator was replaced by admitted population')
    checks.append('readout carried equity, drawdown, queued sells and rejected/unknown formation denominators')
    print(json.dumps({'passed': True, 'groups': len(checks), 'checks': checks,
                      'newFilterModuleImported': False, 'accountsExecuted': 0}), flush=True)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--inputs-only', choices=PERIODS)
    parser.add_argument('--readout-only', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    directory = args.directory.resolve()
    try:
        if args.readout_only:
            verify_readout(directory)
        elif args.inputs_only:
            plan, _, context_plan, _ = verify_registration(directory)
            ctx = reference(CONTEXT_AUDITOR, CONTEXT_AUDITOR_SHA)
            report, _, _, _ = verify_inputs(directory, args.inputs_only, plan, context_plan, ctx)
            print(json.dumps({'passed': True, 'scope': 'inputs-only', 'period': args.inputs_only,
                              'counts': report}, ensure_ascii=False), flush=True)
        else:
            verify(directory)
    except Exception as error:
        attempts = directory / 'validation-attempts'
        attempts.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        failure = {'passed': False, 'at': stamp, 'scope': 'readout' if args.readout_only else args.inputs_only or 'full-account-audit',
                   'verifierSha256': sha(__file__), 'planSha256': sha(directory / 'plan.json')
                   if (directory / 'plan.json').exists() else None,
                   'error': str(error), 'traceback': traceback.format_exc()}
        path = attempts / (stamp + '-failed.json')
        path.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'passed': False, 'failure': str(path), 'error': str(error)}, ensure_ascii=False), flush=True)
        raise


if __name__ == '__main__':
    main()
