"""Post-result audit of an exact projection of previously audited phase equity.

This does not rerun accounts, change the joint-state plan, or create a trading
filter. Original source accounting/valuation truth is inherited through hashes.
"""
from argparse import ArgumentParser
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import traceback

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/joint-cohort-regime-v1'
SOURCE = ROOT / 'artifacts/strategy-search/reclaim-volatility-trail-v1'
CONTEXT_SHA = '2d3d5b30abb2e45aad21a23a92c45b49e5fa50eaf771ce569ca042053d831eff'
SOURCE_SHA = 'bdb89cbf1b68051b937dc04203afcaa89bee55d7afb56afe74d977bdf7a571c7'
SOURCE_PROOF_SHA = '3c56e7d56acdfc55534f8b7918aceedbb8d3572cd1ee9fddbd0f2f3cdec2ae7e'
SCALARS = ('period', 'board', 'policy', 'phaseId', 'observedStart', 'observedEnd', 'observedSessions',
           'enteringEquity', 'endingEquity', 'netPnlAmount', 'returnPct', 'buyFillCount', 'sellFillCount', 'endingPositionCount')
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
POLICIES = ('trail10', 'volatilityTrail')


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def ref(path):
    return {'path': Path(path).resolve().relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def bound(item):
    path = (ROOT / item['path']).resolve()
    require(path.is_relative_to(ROOT) and path.is_file() and sha(path) == item['sha256'], 'Changed/outside evidence ' + item['path'])
    return path


def close(actual, expected, label):
    require(type(actual) in (int, float) and type(expected) in (int, float)
            and math.isfinite(actual) and math.isfinite(expected)
            and math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-8), label)


def stamp(value):
    parsed = datetime.fromisoformat(value)
    require(parsed.tzinfo is not None, 'Timezone absent')
    return parsed


def account_chain(phases, calendar, account):
    ordered = sorted(phases, key=lambda row: row['observedStart'])
    require(len(ordered) > 0 and calendar == sorted(set(calendar)), 'Empty phases or nonunique ordered calendar')
    observed, previous, cash_partition = [], None, []
    for row in ordered:
        dates = [day for day in calendar if row['requestedStart'] <= day <= row['requestedEnd']]
        require(dates and row['observedStart'] == dates[0] and row['observedEnd'] == dates[-1]
                and row['observedSessions'] == len(dates), 'Phase calendar slice mismatch')
        require(len(set(row['valuationUnreliableDates'])) == len(row['valuationUnreliableDates'])
                and set(row['valuationUnreliableDates']).issubset(dates), 'Valuation flags outside source interval')
        expected_entering = previous['endingEquity'] if previous else account['initialCapital']
        close(row['enteringEquity'], expected_entering, 'Phase equity was reset or skipped')
        require(row['enteringEquityDate'] == (previous['observedEnd'] if previous else None), 'Wrong previous observed close')
        require(row['enteringEquityBasis'] == ('previousObservedClose' if previous else 'initialCapital'), 'Entering basis mismatch')
        close(row['endingEquity'], row['endingCash'] + row['endingMarketValue'], 'Cash/mark/equity bridge')
        close(row['netPnlAmount'], row['endingEquity'] - row['enteringEquity'], 'Phase money difference')
        close(row['returnPct'], (row['endingEquity'] / row['enteringEquity'] - 1) * 100, 'Phase actual continuous-capital return')
        require(row['fillCount'] == row['buyFillCount'] + row['sellFillCount'], 'Fill side count mismatch')
        observed.extend(dates)
        cash_partition.append(row['netPnlAmount'])
        previous = row
    require(observed == calendar, 'Full phase partition omits or duplicates source sessions')
    require(ordered[0]['observedStart'] == account['observedStart'] and ordered[-1]['observedEnd'] == account['cutoff'], 'Account boundaries differ')
    close(ordered[-1]['endingEquity'], account['finalEquity'], 'Last phase/account equity')
    close(math.fsum(cash_partition), account['netPnlAmount'], 'Phase cash sum/account cash')
    close((math.prod(1 + row['returnPct'] / 100 for row in ordered) - 1) * 100,
          account['netReturnPct'], 'Phase returns compound, not add')
    for name in ('buyFillCount', 'sellFillCount', 'fillCount'):
        require(sum(row[name] for row in ordered) == account[name], 'Phase/account fill partition ' + name)
    return ordered


def self_test():
    calendar = ['2024-09-23', '2024-09-24', '2024-09-25']
    account = {'initialCapital': 100.0, 'observedStart': calendar[0], 'cutoff': calendar[-1],
               'finalEquity': 121.0, 'netPnlAmount': 21.0, 'netReturnPct': 21.0,
               'buyFillCount': 1, 'sellFillCount': 0, 'fillCount': 1}
    a = {'requestedStart': calendar[0], 'requestedEnd': calendar[0], 'observedStart': calendar[0],
         'observedEnd': calendar[0], 'observedSessions': 1, 'enteringEquity': 100.0,
         'enteringEquityDate': None, 'enteringEquityBasis': 'initialCapital', 'endingEquity': 110.0,
         'endingCash': 10.0, 'endingMarketValue': 100.0, 'netPnlAmount': 10.0, 'returnPct': 10.0,
         'buyFillCount': 1, 'sellFillCount': 0, 'fillCount': 1, 'valuationUnreliableDates': []}
    b = {**a, 'requestedStart': calendar[1], 'requestedEnd': calendar[-1], 'observedStart': calendar[1],
         'observedEnd': calendar[-1], 'observedSessions': 2, 'enteringEquity': 110.0,
         'enteringEquityDate': calendar[0], 'enteringEquityBasis': 'previousObservedClose',
         'endingEquity': 121.0, 'endingMarketValue': 111.0, 'netPnlAmount': 11.0,
         'buyFillCount': 0, 'fillCount': 0, 'valuationUnreliableDates': calendar[1:]}
    account_chain([a, b], calendar, account)
    for changed in ({'enteringEquity': 100.0}, {'observedSessions': 1},
                    {'requestedStart': calendar[0]}, {'valuationUnreliableDates': ['2024-10-01']}):
        try:
            account_chain([a, {**b, **changed}], calendar, account)
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid continuity/calendar accepted')
    require(b['buyFillCount'] == 0 and b['netPnlAmount'] > 0 and b['endingCash'] == a['endingCash'],
            'No-fill marked holding gain distinction')
    return {'passed': True, 'syntheticGroups': 6, 'newAccounts': 0}


def validate():
    destination = OUT / 'continuous-phase-validation.json'
    require(not destination.exists(), 'Refuse to overwrite prior continuous-phase validation')
    context_path = OUT / 'continuous-phase-context.json'
    require(sha(context_path) == CONTEXT_SHA, 'Follow-up sidecar changed')
    context = read(context_path)
    require(context['schema'] == 'joint-cohort-continuous-phase-context-v1', 'Wrong sidecar schema')
    require(context['sourceReadout'] == {'path': (SOURCE / 'readout.json').relative_to(ROOT).as_posix(), 'sha256': SOURCE_SHA}
            and context['sourceValidation'] == {'path': (SOURCE / 'readout-validation.json').relative_to(ROOT).as_posix(), 'sha256': SOURCE_PROOF_SHA}, 'Wrong frozen source')
    source, source_proof = read(bound(context['sourceReadout'])), read(bound(context['sourceValidation']))
    bound(context['extractor'])
    main_proof = read(SOURCE / 'validation.json')
    require(source_proof['passed'] is True and source_proof['readoutSha256'] == SOURCE_SHA
            and source_proof['planSha256'] == sha(SOURCE / 'plan.json') == source['planSha256']
            and source_proof['comparisonSha256'] == sha(SOURCE / 'comparison.json')
            and source_proof['accountValidationSha256'] == sha(SOURCE / 'validation.json')
            and main_proof['passed'] is True and main_proof['planSha256'] == source_proof['planSha256']
            and main_proof['comparisonSha256'] == source_proof['comparisonSha256'], 'Original source audit chain stale')
    joint, joint_proof = read(OUT / 'readout.json'), read(OUT / 'validation.json')
    require(joint_proof['passed'] is True and joint_proof['planSha256'] == sha(OUT / 'plan.json')
            and joint_proof['readoutSha256'] == sha(OUT / 'readout.json')
            and joint_proof['dailySha256'] == joint['daily']['sha256'], 'Joint provenance/calendar proof stale')
    relative = read(bound(joint['sourceReadout']))
    require(relative['sourceAccountReadout'] == context['sourceReadout'], 'Joint and continuous rows have different original accounts')
    require(stamp(joint['generatedAt']) <= stamp(context['createdAt']) <= datetime.now(timezone.utc), 'Follow-up timing inconsistent')
    require(context['newAccounts'] == context['newSignals'] == 0 and context['certified'] is False
            and context['targetAchieved'] is False and 'after joint-state outcomes' in context['timing']
            and 'not part of the preregistered' in context['timing'], 'Post-hoc/non-strategy scope absent')
    expected = [{**{k: r[k] for k in SCALARS}, 'valuationFlagDays': len(r['valuationUnreliableDates'])} for r in source['phaseRows']]
    require(context['rows'] == expected, 'Scalar extraction/order is not exact')
    identities = {(r['period'], r['board'], r['policy'], r['phaseId']) for r in expected}
    require(len(expected) == len(identities) == 96, 'Phase identity/count changed')
    dates = defaultdict(list)
    with gzip.open(bound(joint['daily']), 'rt', encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            dates[(row['period'], row['board'])].append(row['formationDate'])
    require(set(dates) == {(p, b) for p in PERIODS for b in BOARDS}, 'Source-calendar domain mismatch')
    accounts = {(r['period'], r['board'], r['policy']): r['account'] for r in source['accounts']}
    require(set(accounts) == {(p, b, m) for p in PERIODS for b in BOARDS for m in POLICIES}, 'Account domain mismatch')
    phase_groups = defaultdict(list)
    for row in source['phaseRows']:
        phase_groups[(row['period'], row['board'], row['policy'])].append(row)
    reconciliation, boundary_rows = [], []
    for key, account in accounts.items():
        calendar = dates[key[:2]]
        require(len(calendar) == (484 if key[0] == PERIODS[0] else 485), 'All original source sessions required')
        ordered = account_chain(phase_groups[key], calendar, account)
        reconciliation.append({'period': key[0], 'board': key[1], 'policy': key[2], 'phaseCount': len(ordered),
            'observedSessions': len(calendar), 'enteringEquity': ordered[0]['enteringEquity'],
            'endingEquity': ordered[-1]['endingEquity'], 'netPnlAmount': account['netPnlAmount'],
            'buyFillCount': account['buyFillCount'], 'sellFillCount': account['sellFillCount'], 'passed': True})
        for index, row in enumerate(ordered):
            if row['phaseId'] != '2024-09-policy-rally':
                continue
            require(index > 0 and row['observedStart'] == '2024-09-24' and row['observedEnd'] == '2024-10-08'
                    and row['observedSessions'] == 6 and row['netPnlAmount'] > 0, 'Policy phase identity/gain differs')
            previous = ordered[index - 1]
            no_fills = row['buyFillCount'] == row['sellFillCount'] == 0
            cash_change = row['endingCash'] - previous['endingCash']
            mark_change = row['endingMarketValue'] - previous['endingMarketValue']
            close(row['netPnlAmount'], cash_change + mark_change, 'Policy-phase cash/market-value decomposition')
            if no_fills:
                close(cash_change, 0.0, 'No-fill policy-phase cash changed')
                close(mark_change, row['netPnlAmount'], 'No-fill gain not equal marked holdings change')
                require(previous['endingPositionCount'] == row['endingPositionCount'] == 5
                        and row['closedEpisodesByExitDate']['count'] == 0, 'No-fill holdings/closure evidence')
            formations = row['opportunitiesByFormationDate']
            require(formations['formationTop5'] > 0, 'Formation opportunities were erased by no fills')
            flags = len(row['valuationUnreliableDates'])
            if key[1] == 'chinext':
                require(flags == row['observedSessions'] == 6, 'All six ChiNext valuation-flagged sessions must remain disclosed')
            boundary_rows.append({'period': key[0], 'board': key[1], 'policy': key[2], 'phaseId': row['phaseId'],
                'observedSessions': row['observedSessions'], 'returnPct': row['returnPct'], 'netPnlAmount': row['netPnlAmount'],
                'buyFillCount': row['buyFillCount'], 'sellFillCount': row['sellFillCount'],
                'formationTop5': formations['formationTop5'], 'enteredFromFormation': formations['enteredFromFormation'],
                'formationDispositionObservedAt': 'original account cutoff, not phase end',
                'cashChangeAmount': cash_change, 'markedHoldingsChangeAmount': mark_change,
                'noFillsAndCashUnchanged': no_fills, 'valuationFlagDays': flags})
    require(len(boundary_rows) == 6 and sum(r['noFillsAndCashUnchanged'] for r in boundary_rows) == 5, 'Policy-phase no-fill conclusion differs')
    star = next(r for r in boundary_rows if r['board'] == 'star' and r['policy'] == 'volatilityTrail')
    require(star['buyFillCount'] == 2 and star['sellFillCount'] == 0 and star['enteredFromFormation'] == 1,
            'Two fills must not be represented as two newly successful signals')
    protected = {name: sha(OUT / name) for name in ('plan.json', 'readout.json', 'validation.json')}
    proof = {'schema': 'joint-cohort-continuous-phase-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__),
        'contextSha256': CONTEXT_SHA, 'sourceReadoutSha256': SOURCE_SHA, 'sourceValidationSha256': SOURCE_PROOF_SHA,
        'sourceAccountValidationSha256': sha(SOURCE / 'validation.json'), 'jointPlanSha256': protected['plan.json'],
        'jointReadoutSha256': protected['readout.json'], 'jointValidationSha256': protected['validation.json'],
        'sourcePins': [ref(Path(__file__).resolve()), context['extractor']],
        'inputPins': [context['sourceReadout'], context['sourceValidation'], ref(SOURCE / 'validation.json'),
                      ref(SOURCE / 'plan.json'), ref(SOURCE / 'comparison.json'), ref(OUT / 'validation.json'),
                      ref(OUT / 'readout.json'), joint['daily'], joint['sourceReadout']],
        'posthocFollowup': True, 'newAccounts': 0, 'newSignals': 0,
        'counts': {'rows': 96, 'accounts': 12, 'accountSessions': sum(r['observedSessions'] for r in reconciliation),
                   'policyPhaseAccounts': 6, 'policyPhasePositiveAccounts': 6, 'policyPhaseNoFillAccounts': 5},
        'accountContinuity': reconciliation, 'policyPhaseEvidence': boundary_rows,
        'checks': ['Exact hash chain to original independently audited 12-account readout',
                   'Every 96-row scalar and valuation-flag count matches source exactly, including order',
                   'All 484/485 source sessions covered once per account; no calendar gaps/overlaps or equity reset',
                   'Adjacent phase equity, final equity, cash-PnL sum, compounded return and fill totals reconcile',
                   '9.24..10.08: six positive marked-equity movements; five accounts have zero fills and unchanged cash',
                   'Formation opportunities, entered episodes and partial buy fills are explicitly distinct',
                   'ChiNext valuation flags retained on all six policy-phase source sessions'],
        'limitations': ['This post-result descriptive follow-up does not change the registered joint partition.',
                        'Marked open-holding gains are not new-signal win rates, realised profits, or evidence of executable liquidation.',
                        'Positive market-value movement is an accounting observation, not causal attribution to policy or a market participant.',
                        'Original frozen-survivor universe, missing historical status/rights and valuation-quality limitations remain inherited.'],
        'certified': False, 'targetAchieved': False}
    require(protected == {name: sha(OUT / name) for name in protected}, 'Protected original artifacts changed during audit')
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(proof, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    return {'passed': True, 'validation': ref(destination), 'counts': proof['counts']}


def main():
    parser = ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False))
        return
    try:
        result = validate()
    except Exception as error:
        folder = OUT / 'continuous-phase-validation-attempts'
        folder.mkdir(exist_ok=True)
        path = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        with path.open('x', encoding='utf-8') as stream:
            json.dump({'passed': False, 'error': str(error), 'traceback': traceback.format_exc(),
                       'verifierSha256': sha(__file__), 'contextSha256': sha(OUT / 'continuous-phase-context.json')}, stream, indent=2)
        raise
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
