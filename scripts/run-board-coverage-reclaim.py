"""Freeze and run one unchanged reclaim strategy across three local board samples."""
from argparse import ArgumentParser
from copy import deepcopy
import gc
import gzip
import importlib.util
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('_board_util', ROOT / 'scripts/run-peer-amount-persistence.py')
util = importlib.util.module_from_spec(spec)
spec.loader.exec_module(util)
now, sha, digest, read, write, write_record, ref, emit, module = (
    util.now, util.sha, util.digest, util.read, util.write, util.write_record, util.ref, util.emit, util.module)
BASE = ROOT / 'artifacts/strategy-search'
PARENT = BASE / 'strong-undercut-reclaim-timing-v1'
PREVIOUS = BASE / 'reclaim-breadth-entry-v1'
OUT = BASE / 'board-coverage-reclaim-v1'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
LABELS = {'main': '主板', 'chinext': '创业板', 'star': '科创板'}
PROGRAM_SHA = '6e2915aee29a281585dd30be8c2edf6db9ad79a8f8ec0889147dd257d48d9522'
CALENDARS = ('artifacts/strategy-search/provider-probe/2021-2023-calendar/calendar-with-warmup.json',
             'artifacts/strategy-search/supplemental-calendar-2023-2025-v1.json')


def verify_plan():
    plan = read(OUT / 'plan.json')
    for key in ('sourcePins', 'inputPins'):
        for name, expected in plan[key].items():
            assert sha(ROOT / name) == expected, name
    return plan


def register():
    assert sha(BASE / 'program.json') == PROGRAM_SHA
    assert sha(PREVIOUS / 'delivery.json') == '66a2cd01918c35aac1f57e803c222a41ddb510f6f73787961c9083ee0f6c75d5'
    assert read(PARENT / 'validation.json')['passed']
    parent = read(PARENT / 'plan.json')
    periods = {}
    for period in PERIODS:
        config = deepcopy(parent['periods'][period]['config'])
        periods[period] = {key: config[key] for key in ('startDate', 'endDate')}
        periods[period]['config'] = config
    files = list(parent['sourcePins']) + ['scripts/run-board-coverage-reclaim.py',
        'scripts/materialize-board-coverage-source.py', 'service/portfolio_universe.py', 'service/tdx.py']
    test_path = ROOT / 'tests/test_board_coverage_source.py'
    if test_path.exists():
        files.append(test_path.relative_to(ROOT).as_posix())
    inputs = list(CALENDARS) + [str((folder / name).relative_to(ROOT).as_posix())
        for folder, names in ((PARENT, ('plan.json', 'validation.json')),
                              (PREVIOUS, ('delivery.json', 'readout.json', 'readout-validation.json')),
                              (BASE / 'market-phase-atlas-v1', ('atlas.json',))) for name in names]
    plan = {'schema': 'board-coverage-reclaim-v1', 'registeredAt': now(),
        'sourcePins': {name: sha(ROOT / name) for name in dict.fromkeys(files)},
        'inputPins': {name: sha(ROOT / name) for name in inputs},
        'sourceSpec': {'tdxRoot': 'E:/TDX', 'benchmarkSymbol': 'sh000300', 'boards': list(BOARDS),
            'minPriorValidObservations': 61, 'calendarPaths': list(CALENDARS)},
        'periods': periods, 'boards': list(BOARDS), 'labels': LABELS, 'strategyId': parent['strategyId'],
        'mode': 'immediate', 'newAccounts': 6, 'newUniverseVariants': 3,
        'newSetupMechanisms': 0, 'newEntryPolicies': 0, 'referenceAccountsReused': 0,
        'parameterSearch': False, 'profitHurdles': None, 'holdoutClaim': False,
        'previousTurn': {'classification': 'progress', 'evidence': ref(PREVIOUS / 'delivery.json'),
            'meaning': 'Eight breadth-filter accounts completed; no stable path. Mainboard-only coverage did not test technology boards.'},
        'novelty': 'A local-stock coverage and board-transfer experiment for the unchanged immediate undercut/reclaim setup. Three new local board cohorts under one common pre-start observation rule; not a new alpha mechanism or a pure expansion of the old3141 universe.',
        'rules': {
            'identity': 'Enumerate every currently present SH/SZ main/chinext/star .day identity before outcomes. Within each period fix membership to stocks with at least61 valid strictly pre-start OHLCV observations. This is observed-history sufficiency, not an IPO-date or historical-index-membership claim.',
            'priorValid': 'Finite positive OHLCV with valid OHLC envelope and finite amount>=0. Prior observations need not be contiguous for cohort identity; the original signal still requires its exact consecutive61 calendar slots at each formation.',
            'cutoff': 'Read only through each registered period end for analytical values; preserve raw earlier observations as warmup. Future file bytes/hashes identify current storage only and never define price features. No in-period new listings enter the fixed cohort.',
            'missing': 'Every discovered identity, missing/insufficient prior-history reason, parse failure and observation count retained in the manifest. Unknown historical names, ST, listing dates, membership and rights remain unknown.',
            'calendar': 'Union the two previously bound independent calendars, require agreement on their overlap, restrict to period end, and reject observed dates outside that calendar. Benchmark uses the same raw sh000300 prefix.',
            'ranking': 'For each board and each T separately, call the unchanged original UR builder on all fixed-cohort members. Its prior61 price-valid population defines relative60 midranks; own60>0 and rank>=.80, prior20 amount>0, strict low<T-1 support and close>support are unchanged. Original liquidity-descending Top5 and symbol tie-break remain unchanged.',
            'entry': 'Execute only immediate T-close decisions, one next-source-open attempt, original five signal fields. Do not rerank after account rejection or backfill. No market-breadth/date/future-winner gate.',
            'account': 'Six independent500000 accounts,5 positions and20-percent entry budgets. Same noProgress5 exits,5-percent close stop,10-percent close trailing,no profit cap/no holding expiry; actual next eligible opening can exceed nominal stop losses.',
            'compatibilityConfig': 'Original config is retained, including minListingDays120/minAmount20. FrozenSignalScorer bypasses DailyScorer; these values must not be described as an extra executed signal-eligibility screen. The inherited real execution gates are audited as implemented.',
            'boardExecution': 'Inherited board-specific nominal price limits and order lots are used. Fixed pre-start61-observation identity keeps target-period IPO first-five-session windows out of this experiment; it does not implement a general IPO regime.',
            'fees': 'Same bound DatedFeePolicy for2022-2023 and LegacyFeePolicy for2024-2025,10bp slippage each side. No broker-specific actual-fee or opening-capacity certification.',
            'comparison': 'Main/chinext/star each rank within their own fixed board cohort and compete only for their own capital. Differences describe board-specific strategy paths, not a causal board effect. No merged-market ranking/account in this batch.',
            'oldReference': 'The earlier3141 mainboard account uses a different population/source binding. It is background evidence only; every current mainboard account is freshly run under the new common cohort construction.',
            'evaluation': 'Keep both periods, all years/halves/16 atlas stages and2024-09-24 split with carried equity. Preserve signals/rejections, fills, closed episodes, open positions, costs, exposure, holding ages, tails and quality. No numerical profit hurdle or policy promotion.'},
        'limitations': [
            'Current local surviving-stock coverage is not a historical point-in-time market universe; delisted and absent files remain missing.',
            'New cohorts exclude target-period new listings and require prior61 valid observations; this changes sample scope from the old3141 study.',
            'Raw unadjusted local prices and incomplete corporate actions, historical eligibility and execution evidence limit economic interpretation.',
            'All historical periods have already been inspected; this is development or qualified historical evidence, not untouched validation.',
            'Board-specific price limits, order sizes, volatility and capital utilization differ; a fixed5-percent trigger does not imply equal realized risk.',
            'Price-volume proxies do not identify institutions, retail, quantitative funds or market manipulation.',
            'The2024-09-24 boundary and retrospective leader/emotion labels describe periods only; they are absent from entry rules.'],
        'certified': False, 'targetAchieved': False}
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    emit({'registered': True, **ref(OUT / 'plan.json')})


def prepare():
    from service.research_undercut_reclaim import build_undercut_reclaim
    plan = verify_plan()
    source_completion = read(OUT / 'source-completion.json')
    validation = read(OUT / 'source-validation.json')
    assert validation['passed'] and validation['planSha256'] == sha(OUT / 'plan.json')
    assert validation['sourceCompletionSha256'] == sha(OUT / 'source-completion.json')
    outputs = {}
    for period in PERIODS:
        outputs[period] = {}
        for board in BOARDS:
            folder = OUT / period / board
            folder.mkdir(exist_ok=False)
            cache = OUT / period / (board + '.account-data.json.gz')
            assert sha(cache) == validation['datasetPins'][cache.relative_to(ROOT).as_posix()]
            data = read(cache)
            assert data['priceBasisId'] == 'raw-unadjusted'
            config = plan['periods'][period]['config']
            emit({'phase': 'signal-start', 'period': period, 'board': board, 'stocks': len(data['securities'])})
            evidence = build_undercut_reclaim(data, start=config['startDate'], end=config['endDate'],
                progress=lambda v: emit({'phase': 'signal-progress', 'period': period, 'board': board, **v}))
            assert evidence['strategyId'] == plan['strategyId']
            for name, rows in (('opportunities.jsonl.gz', evidence['opportunities']),
                               ('daily-evidence.jsonl.gz', evidence['dailyRows'])):
                with gzip.open(folder / name, 'xt', encoding='utf-8', compresslevel=1) as stream:
                    for row in rows:
                        write_record(stream, row)
            write(folder / 'signal-metadata.json', {k: v for k, v in evidence.items()
                if k not in ('opportunities', 'dailyRows', 'schedules')})
            write(folder / 'immediate.signals.json', evidence['schedules']['immediate'])
            write(folder / 'input-counts.json', evidence['counts'])
            write(folder / 'input-completion.json', {'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
                'period': period, 'board': board, 'counts': evidence['counts'],
                'accountData': ref(cache), 'accountDatasetDigest': digest(data),
                'sourceManifest': ref(OUT / period / 'source-manifest.json'),
                'sourceValidation': ref(OUT / 'source-validation.json'),
                'files': {p.name: ref(p) for p in folder.iterdir() if p.is_file()}})
            outputs[period][board] = ref(folder / 'input-completion.json')
            emit({'phase': 'signal-complete', 'period': period, 'board': board,
                'signals': len(evidence['schedules']['immediate'])})
            del data, evidence
            gc.collect()
    verify_plan()
    write(OUT / 'input-completion.json', {'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
        'sourceCompletion': ref(OUT / 'source-completion.json'), 'outputs': outputs,
        'allSchedulesFrozenBeforeAccounts': True})


def run():
    from service.research_structural_exits import ResearchExitAccount
    from service.research_account_outcomes import summarize_account
    from service.research_account_fees import LegacyFeePolicy
    plan = verify_plan()
    completion = read(OUT / 'input-completion.json')
    validation = read(OUT / 'input-validation.json')
    assert validation['passed'] and validation['planSha256'] == sha(OUT / 'plan.json')
    assert validation['inputCompletionSha256'] == sha(OUT / 'input-completion.json')
    assert completion['allSchedulesFrozenBeforeAccounts']
    write(OUT / 'run-start.json', {'startedAt': now(), 'inputCompletion': ref(OUT / 'input-completion.json'),
        'inputValidation': ref(OUT / 'input-validation.json')})
    compact = module('_board_compact', 'run-supplemental-force-context.py').compact
    loader = module('_board_fee_loader', 'load-market-context-screen.py')
    rows, artifacts = [], {}
    for period in PERIODS:
        artifacts[period] = {}
        if period == '2022-2023':
            helper = loader._module('run-direction-account-screen.py').load_helper()
            fee = helper.fee_policy(helper.load_binding()['document'])
        else:
            fee = LegacyFeePolicy()
        for board in BOARDS:
            folder = OUT / period / board
            receipt = read(folder / 'input-completion.json')
            assert sha(folder / 'input-completion.json') == completion['outputs'][period][board]['sha256']
            for item in receipt['files'].values():
                assert sha(ROOT / item['path']) == item['sha256']
            cache = receipt['accountData']
            assert sha(ROOT / cache['path']) == cache['sha256']
            data = read(ROOT / cache['path'])
            assert digest(data) == receipt['accountDatasetDigest']
            signals = read(folder / 'immediate.signals.json')
            emit({'phase': 'account-start', 'period': period, 'board': board, 'signals': len(signals)})
            engine = ResearchExitAccount(data, signals, deepcopy(plan['periods'][period]['config']),
                exit_rule='noProgress5', entry_weight=.2, fee_policy=fee)
            engine.step(20000)
            result, checkpoint = engine.result(), engine.checkpoint()
            assert result['status'] == 'completed'
            summary = summarize_account(result, checkpoint, policy_id='immediate', policy_label=LABELS[board])
            assert not summary['quality']['integrityIssues']
            for suffix, value in (('.result.json.gz', result), ('.checkpoint.json.gz', checkpoint), ('.summary.json', summary)):
                write(folder / ('immediate' + suffix), value)
            row = compact('immediate', LABELS[board], signals, summary)
            row.update(period=period, board=board)
            rows.append(row)
            artifacts[period][board] = {p.name: ref(p) for p in folder.iterdir() if p.is_file()
                and any(tag in p.name for tag in ('.result.', '.summary.', '.checkpoint.'))}
            emit({'phase': 'account-complete', **{k: row[k] for k in ('period', 'board', 'closed', 'accountReturnPct', 'maxDrawdownPct', 'openPositions')}})
            del engine, result, checkpoint, summary, data
            gc.collect()
    write(OUT / 'comparison.json', {'schema': 'board-coverage-reclaim-comparison-v1', 'completedAt': now(),
        'planSha256': sha(OUT / 'plan.json'), 'rows': rows, 'artifacts': artifacts,
        'newSetupMechanisms': 0, 'newUniverseVariants': 3, 'newAccounts': 6,
        'referenceAccountsReused': 0, 'limitations': plan['limitations'], 'certified': False, 'targetAchieved': False})
    emit({'phase': 'all-accounts-complete', **ref(OUT / 'comparison.json')})


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('mode', choices=('register', 'prepare', 'run'))
    args = parser.parse_args()
    try:
        globals()[args.mode]()
    except Exception as error:
        if OUT.exists():
            failures = OUT / 'failures'
            failures.mkdir(exist_ok=True)
            write(failures / (str(time.time_ns()) + '.json'), {'at': now(), 'mode': args.mode,
                'type': type(error).__name__, 'message': str(error)})
        raise
