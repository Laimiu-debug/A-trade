"""Research-only A-slot reranking using pre-entry price/amount evidence.

The candidate universe, day/sector slots, B selections, sizing and exits are
inherited. Formation reads at most D. Execution stays at the next open.
"""
from collections import defaultdict
from copy import deepcopy
import math

from .research_guiyin_dual_path import DualPathAccount

VERSION = 'ignition-slot-selection-v1'
SIGNAL_FIELDS = ('signalId', 'strategyId', 'symbol', 'signalDate', 'originalRank')
CASE_RULES = {
    'baseline': ('original', 'original'),
    'selection': ('leastBreakoutExtension', 'original'),
    'participation': ('strongestAmountParticipation', 'original'),
}


def validate_case(case):
    expected = CASE_RULES.get(case.get('id'))
    if (expected is None or case.get('parentPolicy') != 'complete'
            or (case.get('formationPolicy'), case.get('entryPolicy')) != expected
            or case.get('parameters') != {}):
        raise ValueError('Only the three fixed research cases are allowed')


def _positive(value):
    return isinstance(value, (float, int)) and math.isfinite(value) and value > 0


def build_ignition_inputs(formation, matrix_or_features, parent_schedule,
                          case_spec, start, end):
    validate_case(case_spec)
    if not start <= end <= '2026-10-15':
        raise ValueError('Invalid exposed-history bounds')
    matrix = matrix_or_features
    calendar = [str(x) for x in matrix['calendar']]
    symbols = [str(x) for x in matrix['symbols']]
    date_index, stock_index = {d: i for i, d in enumerate(calendar)}, {s: i for i, s in enumerate(symbols)}
    if len(date_index) != len(calendar) or calendar != sorted(calendar) or len(stock_index) != len(symbols):
        raise ValueError('Unique sorted calendar and unique securities required')
    contexts = {d['date']: d for d in formation['daily']}
    parent = [deepcopy(s) for s in parent_schedule if start <= s['signalDate'] <= end]
    pools = defaultdict(list)
    for candidate in formation['allCandidates']:
        if start <= candidate['signalDate'] <= end and candidate['pattern'] == 'A_ignition':
            pools[(candidate['signalDate'], candidate['sectorCode'])].append(candidate)
    chosen, metadata, proof, used_slots = [], {}, [], set()
    features = {}

    def feature(candidate):
        key = (candidate['signalDate'], candidate['symbol'])
        if key not in features:
            day, symbol = key
            i, r = date_index[day], stock_index[symbol]
            if i < 20 or candidate['sourceIndex'] != i or candidate['contextCutoff'] != day:
                raise ValueError('Candidate decision cutoff does not match matrix')
            prior = [float(x) for x in matrix['high'][r, i-20:i]]
            close, ntr = float(matrix['close'][r, i]), candidate['meanNtr20']
            amount, prior_amount = float(matrix['amount'][r, i]), candidate['priorAmount20']
            if not all(_positive(x) for x in prior + [close, ntr, amount, prior_amount]) or close != candidate['signalClose']:
                raise ValueError('Incomplete or inconsistent pre-entry feature evidence')
            high = max(prior)
            if close <= high:
                raise ValueError('Frozen A candidate is not a prior-high breakout')
            features[key] = {'symbol': symbol, 'priorHigh20': high,
                             'signalClose': close, 'meanNtr20': ntr,
                             'breakoutExtensionNtr': (close / high - 1) / ntr,
                             'priorIndustryRank20': candidate['priorIndustryRank20'],
                             'priorAmount20': prior_amount, 'signalAmount': amount,
                             'amountParticipation': amount / prior_amount, 'featureCutoff': day}
        return features[key]

    for original in parent:
        if set(original) != set(SIGNAL_FIELDS):
            raise ValueError('Unexpected parent signal fields')
        old = formation['metadata'][original['signalId']]
        if any(old[k] != v for k, v in original.items()):
            raise ValueError('Parent identity mismatch')
        day, sector = original['signalDate'], old['sectorCode']
        slot = f'{day}|{sector}|{original["originalRank"]}'
        if slot in used_slots:
            raise ValueError('Duplicate date-sector-rank slot')
        used_slots.add(slot)
        candidates = pools[(day, sector)] if old['pattern'] == 'A_ignition' else []
        if old['pattern'] == 'A_ignition':
            if len({v['symbol'] for v in candidates}) != len(candidates):
                raise ValueError('Duplicate stock in candidate pool')
            if original['symbol'] not in {v['symbol'] for v in candidates}:
                raise ValueError('Original A selection absent from the full pool')
            for candidate in candidates:
                if candidate['snapshotId'] != old['snapshotId']:
                    raise ValueError('Candidate membership cutoff mismatch')
                if formation['sectorMaps'][old['snapshotId']].get(candidate['symbol']) != sector:
                    raise ValueError('Candidate sector membership mismatch')
                feature(candidate)
        new, meta = deepcopy(original), deepcopy(old)
        if old['pattern'] == 'A_ignition' and case_spec['formationPolicy'] != 'original':
            feature_name = 'breakoutExtensionNtr' if case_spec['formationPolicy'] == 'leastBreakoutExtension' else 'amountParticipation'
            direction = 1 if feature_name == 'breakoutExtensionNtr' else -1
            winner = min(candidates, key=lambda v: (direction * feature(v)[feature_name],
                         -v['priorIndustryRank20'], -v['priorAmount20'], v['symbol']))
            new['signalId'] = f'{case_spec["id"]}:{original["signalId"]}:{winner["symbol"]}'
            new['symbol'] = winner['symbol']
            meta = {**deepcopy(winner), **new, 'sectorScore': old['sectorScore'],
                    'originSignalId': original['signalId'], 'originSlotId': slot,
                    'selectionFeature': deepcopy(feature(winner))}
        if old['pattern'] == 'B_corePullback' and (new != original or meta != old):
            raise ValueError('B selection must remain unchanged')
        if sector not in contexts[day]['selectedSectors']:
            raise ValueError('Original sector admission mismatch')
        chosen.append(new)
        metadata[new['signalId']] = meta
        proof.append({'originSlotId': slot, 'originSignalId': original['signalId'],
                      'selectedSignalId': new['signalId'], 'signalDate': day,
                      'sectorCode': sector, 'originalRank': original['originalRank'],
                      'pattern': old['pattern'], 'originSymbol': original['symbol'],
                      'selectedSymbol': new['symbol'], 'candidateCount': len(candidates) if candidates else 1,
                      'candidateFeatures': [deepcopy(feature(v)) for v in sorted(candidates, key=lambda v: v['symbol'])],
                      'changed': new['symbol'] != original['symbol'],
                      'earliestEntryDate': calendar[date_index[day]+1] if date_index[day]+1 < len(calendar) else None})
    system = {'version': formation['version'], 'daily': deepcopy([d for d in formation['daily'] if d['date'] <= end]),
              'sectorMaps': deepcopy(formation['sectorMaps']), 'metadata': metadata}
    return system, chosen, {'version': VERSION, 'case': deepcopy(case_spec), 'rows': proof,
                           'slots': len(parent), 'aSlots': sum(r['pattern'] == 'A_ignition' for r in proof),
                           'bSlots': sum(r['pattern'] == 'B_corePullback' for r in proof),
                           'changedSlots': sum(r['changed'] for r in proof),
                           'formationUsesNextOpen': False, 'droppedSlots': 0}


class IgnitionSelectionAccount(DualPathAccount):
    def __init__(self, dataset, signals, config, *, system_inputs, case_spec, fee_policy):
        validate_case(case_spec)
        self.selection_case = deepcopy(case_spec)
        self.selection_identity = deepcopy(case_spec)
        super().__init__(dataset, signals, config, system_inputs=system_inputs,
                         policy='complete', fee_policy=fee_policy)

    def _identity_stable(self):
        super()._identity_stable()
        if self.selection_case != self.selection_identity:
            raise ValueError('Frozen selection rule drift')

    def result(self):
        result = super().result()
        result['research']['ignitionSelection'] = {'version': VERSION, 'case': deepcopy(self.selection_case),
             'orderMeaning': 'Precommitted D-close selection; one D+1 open attempt; no intraday low-touch fill or replacement'}
        return result


def make_account(dataset, system, schedule, config, case_spec, *, fee_policy):
    validate_case(case_spec)
    if case_spec['id'] == 'baseline':
        return DualPathAccount(dataset, schedule, config, system_inputs=system,
                               policy='complete', fee_policy=fee_policy)
    return IgnitionSelectionAccount(dataset, schedule, config, system_inputs=system,
                                    case_spec=case_spec, fee_policy=fee_policy)
