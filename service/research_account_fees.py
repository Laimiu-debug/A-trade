"""Explicit, account-local research fees; probes are never paid fills.

The caller owns cash/share execution and rolls that state back before restoring
this side ledger. signal_date is the current order's source date (a sell's exit
trigger), not necessarily the initial purchase signal. No market run is here.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

from .signal_target_study_fee_profile import (
    LegacyFeePolicy, DatedFeePolicy, FROZEN_DEPENDENCIES,
)

VERSION = 'research-account-fees-v1'
SOURCE_PINS = {**FROZEN_DEPENDENCIES,
    'signal_target_study_fee_profile.py': '50f6331a50d66e73f3bae7a1de817252310d52a627eff55c541774061067ed36'}
PURPOSES = ('signalReserve', 'executionSizing', 'fillQuote')
POST_FILL_AUDIT_FIELDS = ('capacityBasis', 'openingCapacityVerified', 'executionValid')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def _day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('Fee dates must be canonical YYYY-MM-DD') from None
    return value


def _number(value, name, positive=False):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(name+' must be finite and '+('positive' if positive else 'nonnegative'))
    return value


def _fill_identity(fill):
    # The frozen EOD opening audit adds these three quality annotations after
    # cash/fill commitment. They never change the fee/notional linkage.
    return {key: deepcopy(value) for key, value in fill.items() if key not in POST_FILL_AUDIT_FIELDS}


@dataclass(frozen=True)
class OrderFeeContext:
    symbol: str
    signal_date: str
    decision_date: str
    effective_fee_date: str
    execution_date: str | None
    purpose: str

    def __post_init__(self):
        if not isinstance(self.symbol, str) or not self.symbol or self.purpose not in PURPOSES:
            raise ValueError('Explicit symbol and fee purpose required')
        for value in (self.signal_date, self.decision_date, self.effective_fee_date):
            _day(value)
        if self.execution_date is not None:
            _day(self.execution_date)
        if self.purpose == 'signalReserve':
            valid = self.signal_date == self.decision_date == self.effective_fee_date and self.execution_date is None
        else:
            valid = self.signal_date < self.decision_date == self.effective_fee_date
            valid = valid and (self.execution_date == self.decision_date if self.purpose == 'fillQuote'
                               else self.execution_date is None)
        if not valid:
            raise ValueError('Fee context chronology/purpose mismatch')


@dataclass(frozen=True)
class _TransactionSnapshot:
    issuer: object
    quote_count: int
    paid_count: int
    paid_total: float
    last_quote_hash: str | None
    last_paid_hash: str | None


class AccountFeeLedger:
    """All fills of one owner, including repeated partial buys and sells.

    quote() returns a dict isolated from the audit copy. commit_fill() requires
    that exact issued handle and the owner's newly appended trade object; a
    copied or serialized handle cannot authorize a payment. Restore/rollback
    invalidate every outstanding handle. Checkpoints are portable evidence,
    not pending payment authorizations.

    Owner dataset/config/securities references and validated data identities
    are bound. The owning engine remains responsible for full raw-data content
    integrity; this helper does not rescan market bars on every fee probe.
    """
    def __init__(self, owner, fee_policy):
        if type(fee_policy) not in (LegacyFeePolicy, DatedFeePolicy):
            raise ValueError('Explicit supported immutable fee policy required')
        for key in ('config', 'dataset', 'securities'):
            if not isinstance(getattr(owner, key, None), dict):
                raise ValueError('Owner is missing '+key)
        if not isinstance(getattr(owner, 'state', None), dict) or not isinstance(owner.state.get('trades'), list):
            raise ValueError('Owner must have a trades ledger')
        for key in ('data_digest', 'fingerprint'):
            if not isinstance(getattr(owner, key, None), str) or not getattr(owner, key):
                raise ValueError('Owner requires validated '+key)
        self.owner, self.fee_policy = owner, fee_policy
        self._owner = owner
        self._policy = fee_policy
        self._refs = {key: getattr(owner, key) for key in ('config', 'dataset', 'securities')}
        self._policy_settings = deepcopy(fee_policy.settings(owner.config))
        self._settings = {**deepcopy(self._policy_settings),
            'inheritedEventReservePolicyUnused': self._policy_settings['reservePolicy'],
            'reservePolicy': 'explicit-signal-date-effective-rate;current-order-source-date',
            'feeContextVersion': VERSION, 'minimumCommissionUnit': 'each-simulated-fill',
            'actualBrokerChargesVerified': False}
        self._settings_sha = _digest(self._settings)
        self._pins = {**SOURCE_PINS, Path(__file__).name: hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        self._source_stats = {}
        self._check_sources(force=True)
        self._identity = {'version': VERSION, 'configSha256': _digest(owner.config),
            'dataDigest': owner.data_digest, 'datasetFingerprint': owner.fingerprint,
            'datasetDeclaredFingerprint': deepcopy(owner.dataset.get('fingerprint')),
            'securitySymbolsSha256': _digest(sorted(owner.securities)),
            'settings': deepcopy(self._settings), 'settingsSha256': self._settings_sha,
            'sourcePins': deepcopy(self._pins)}
        self._identity_sha = _digest(self._identity)
        self._quotes, self._paid = [], []
        self._paid_total = 0.
        self._issued, self._fill_ids, self._paid_quote_ids = {}, set(), set()
        self._transaction_issuer = object()

    @property
    def settings(self):
        return deepcopy(self._settings)

    @property
    def settings_sha256(self):
        return self._settings_sha

    @property
    def identity(self):
        return deepcopy(self._identity)

    def _check_sources(self, force=False):
        for name, expected in self._pins.items():
            path = Path(__file__).with_name(name)
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if force or self._source_stats.get(name) != signature:
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError('Frozen fee source changed: '+name)
                self._source_stats[name] = signature

    def _guard(self, force_sources=False):
        owner = self.owner
        if owner is not self._owner or any(getattr(owner, key, None) is not ref for key, ref in self._refs.items()):
            raise ValueError('Foreign or replaced owner references')
        if (self.fee_policy is not self._policy or _digest(owner.config) != self._identity['configSha256']
                or self.fee_policy.settings(owner.config) != self._policy_settings):
            raise ValueError('Fee policy/config changed')
        if (owner.data_digest != self._identity['dataDigest'] or owner.fingerprint != self._identity['datasetFingerprint']
                or owner.dataset.get('fingerprint') != self._identity['datasetDeclaredFingerprint']):
            raise ValueError('Owner data identity changed')
        if not isinstance(owner.state, dict) or not isinstance(owner.state.get('trades'), list):
            raise ValueError('Owner trades ledger missing')
        self._check_sources(force=force_sources)

    def _context(self, context, side):
        if type(context) is not OrderFeeContext or side not in ('buy', 'sell'):
            raise ValueError('Explicit immutable order context and side required')
        # Revalidate even an object altered through low-level object APIs.
        OrderFeeContext(**asdict(context))
        if context.symbol not in self.owner.securities:
            raise ValueError('Fee symbol absent from owner securities')
        if context.purpose != 'fillQuote' and side != 'buy':
            raise ValueError('Sizing/reserve quotes are buy-only')
        if isinstance(self.fee_policy, DatedFeePolicy):
            self.fee_policy.validate_event(self.owner.config, context.symbol,
                                          context.signal_date, context.decision_date)

    def _render_quote(self, amount, side, context, quote_id):
        self._context(context, side)
        _number(amount, 'Fee notional')
        calculation = self.fee_policy.calculation(
            SimpleNamespace(config=self.owner.config, market=context.symbol[:2].upper()),
            amount, side, context.effective_fee_date)
        for value in calculation['breakdown'].values():
            _number(value, 'Fee component')
        result = {'version': VERSION, 'ledgerIdentitySha256': self._identity_sha,
            'quoteId': quote_id, 'symbol': context.symbol, 'market': context.symbol[:2].upper(),
            'side': side, 'amount': amount, 'context': asdict(context),
            'settingsSha256': self._settings_sha,
            'profileSettingsSha256': calculation['profileSettingsSha256'],
            'breakdown': deepcopy(calculation['breakdown']), 'rates': deepcopy(calculation['rates']),
            'evidence': deepcopy(calculation['evidence']),
            'meaning': 'research-fee-quote;not-a-payment'}
        if 'transferDisclosure' in calculation:
            result['transferDisclosure'] = deepcopy(calculation['transferDisclosure'])
        return result

    def quote(self, amount, side, context):
        self._guard()
        self._reconciled(full=False)
        result = self._render_quote(amount, side, context, len(self._quotes))
        self._quotes.append(deepcopy(result))
        if context.purpose == 'fillQuote':
            self._issued[result['quoteId']] = (result, len(self.owner.state['trades']))
        return result

    def affordable_quantity(self, ceiling, minimum, increment, price, budget, context):
        self._guard()
        self._context(context, 'buy')
        if context.purpose not in ('signalReserve', 'executionSizing'):
            raise ValueError('Affordability requires a nonpayment sizing context')
        for name, value, lower in (('ceiling', ceiling, 0), ('minimum', minimum, 1), ('increment', increment, 1)):
            if type(value) is not int or value < lower:
                raise ValueError(name+' must be an integer in range')
        _number(price, 'Sizing price', positive=True)
        _number(budget, 'Sizing budget')
        # Preserve frozen PortfolioEngine binary search and 1e-8 tolerance.
        low, high, quantity = 0, ceiling // increment, 0
        while low <= high:
            mid = (low + high) // 2
            count = mid * increment
            fee = self.quote(count * price, 'buy', context)['breakdown']['total']
            if count * price + fee <= budget + 1e-8:
                quantity, low = count, mid + 1
            else:
                high = mid - 1
        return quantity if quantity >= minimum else 0

    def _match_fill(self, quote, fill):
        if not isinstance(fill, dict) or quote['context']['purpose'] != 'fillQuote':
            raise ValueError('Only an explicit fill quote can be paid')
        quantity = fill.get('quantity')
        if type(quantity) is not int or quantity <= 0:
            raise ValueError('Paid fill quantity must be a positive integer')
        price = _number(fill.get('price'), 'Fill price', positive=True)
        comparisons = {'symbol': quote['symbol'], 'side': quote['side'],
            'date': quote['context']['execution_date'], 'signalDate': quote['context']['signal_date'],
            'amount': quote['amount'], 'fee': quote['breakdown']['total']}
        if any(type(fill.get(key)) is bool or fill.get(key) != value for key, value in comparisons.items()):
            raise ValueError('Fill context/notional/fee mismatch')
        if quantity * price != quote['amount']:
            raise ValueError('Fill quantity/price notional mismatch')
        _digest(fill)

    def commit_fill(self, quote, fill, fill_id):
        self._guard()
        if not isinstance(fill_id, str) or not fill_id or fill_id != fill_id.strip() or fill_id in self._fill_ids:
            raise ValueError('Unique nonempty fill ID required')
        quote_id = quote.get('quoteId') if isinstance(quote, dict) else None
        if type(quote_id) is not int or quote_id not in self._issued or quote_id in self._paid_quote_ids:
            raise ValueError('Foreign, stale or previously paid quote')
        issued, trade_count = self._issued[quote_id]
        if quote is not issued or quote != self._quotes[quote_id]:
            raise ValueError('Foreign, copied or changed quote handle')
        trades = self.owner.state['trades']
        if len(trades) != len(self._paid) + 1 or len(trades) != trade_count + 1 or trades[-1] is not fill:
            raise ValueError('Commit requires this owner newly appended fill object')
        self._match_fill(quote, fill)
        paid_total = _number(self._paid_total + quote['breakdown']['total'], 'Total paid fees')
        row = {'fillId': fill_id, 'tradeIndex': len(self._paid), 'quoteId': quote_id,
            'fill': _fill_identity(fill), 'quote': deepcopy(quote),
            'meaning': 'simulated-paid-fee;not-actual-broker-payment'}
        self._paid.append(row)
        self._paid_total = paid_total
        self._fill_ids.add(fill_id)
        self._paid_quote_ids.add(quote_id)
        self._issued.clear()  # No other old handle may authorize a later trade.
        return deepcopy(row)

    def _reconciled(self, full):
        trades = self.owner.state['trades']
        if len(trades) != len(self._paid):
            raise ValueError('Owner fills and paid ledger are not reconciled')
        pairs = zip(self._paid, trades) if full else zip(self._paid[-1:], trades[-1:])
        if any(row['fill'] != _fill_identity(trade) for row, trade in pairs):
            raise ValueError('Committed owner fill changed')

    def checkpoint(self):
        self._guard(force_sources=True)
        self._reconciled(full=True)
        result = {'version': VERSION, 'identity': deepcopy(self._identity),
            'quotes': deepcopy(self._quotes), 'paidLedger': deepcopy(self._paid),
            'paidFeeTotal': self._paid_total}
        result['checkpointDigest'] = _digest(result)
        return result

    def restore(self, state):
        self._guard(force_sources=True)
        if not isinstance(state, dict) or set(state) != {'version','identity','quotes','paidLedger','paidFeeTotal','checkpointDigest'}:
            raise ValueError('Incomplete fee checkpoint')
        restored = deepcopy(state)
        checksum = restored.pop('checkpointDigest')
        if checksum != _digest(restored) or restored['version'] != VERSION or restored['identity'] != self._identity:
            raise ValueError('Fee checkpoint identity/digest mismatch')
        quotes, paid = restored['quotes'], restored['paidLedger']
        if not isinstance(quotes, list) or not isinstance(paid, list) or len(paid) != len(self.owner.state['trades']):
            raise ValueError('Fee checkpoint fill count mismatch')
        for index, quote in enumerate(quotes):
            if not isinstance(quote, dict) or _digest(quote) != _digest(self._render_quote(quote.get('amount'), quote.get('side'),
                    OrderFeeContext(**quote.get('context', {})), index)):
                raise ValueError('Fee checkpoint quote differs from explicit fee policy')
        fill_ids, quote_ids, total = set(), set(), 0.
        for index, row in enumerate(paid):
            if not isinstance(row, dict) or set(row) != {'fillId','tradeIndex','quoteId','fill','quote','meaning'}:
                raise ValueError('Malformed paid record')
            qid, fid = row['quoteId'], row['fillId']
            if (type(qid) is not int or not 0 <= qid < len(quotes) or qid in quote_ids
                    or not isinstance(fid, str) or not fid or fid != fid.strip() or fid in fill_ids
                    or type(row['tradeIndex']) is not int or row['tradeIndex'] != index
                    or row['quote'] != quotes[qid] or row['fill'] != _fill_identity(self.owner.state['trades'][index])
                    or row['meaning'] != 'simulated-paid-fee;not-actual-broker-payment'):
                raise ValueError('Fee checkpoint paid linkage mismatch')
            self._match_fill(quotes[qid], row['fill'])
            fill_ids.add(fid); quote_ids.add(qid)
            total += quotes[qid]['breakdown']['total']
        if type(restored['paidFeeTotal']) is bool or restored['paidFeeTotal'] != total:
            raise ValueError('Fee checkpoint paid total mismatch')
        self._quotes, self._paid, self._paid_total = quotes, paid, total
        self._fill_ids, self._paid_quote_ids = fill_ids, quote_ids
        self._issued.clear()

    def transaction_snapshot(self):
        self._guard(force_sources=True)
        self._reconciled(full=False)
        return _TransactionSnapshot(self._transaction_issuer, len(self._quotes), len(self._paid), self._paid_total,
            _digest(self._quotes[-1]) if self._quotes else None, _digest(self._paid[-1]) if self._paid else None)

    def transaction_restore(self, snapshot):
        self._guard(force_sources=True)
        if (type(snapshot) is not _TransactionSnapshot or snapshot.issuer is not self._transaction_issuer
                or type(snapshot.quote_count) is not int or type(snapshot.paid_count) is not int
                or not 0 <= snapshot.quote_count <= len(self._quotes)
                or not 0 <= snapshot.paid_count <= len(self._paid)
                or snapshot.paid_count != len(self.owner.state['trades'])):
            raise ValueError('Foreign snapshot or owner was not rolled back first')
        if ((snapshot.quote_count and _digest(self._quotes[snapshot.quote_count-1]) != snapshot.last_quote_hash)
                or (snapshot.paid_count and _digest(self._paid[snapshot.paid_count-1]) != snapshot.last_paid_hash)):
            raise ValueError('Transaction fee prefix changed')
        prefix = self._paid[:snapshot.paid_count]
        if (snapshot.paid_total != sum(row['quote']['breakdown']['total'] for row in prefix)
                or any(row['fill'] != _fill_identity(fill) for row, fill in zip(prefix, self.owner.state['trades']))):
            raise ValueError('Transaction paid total or restored owner prefix differs')
        del self._quotes[snapshot.quote_count:]
        del self._paid[snapshot.paid_count:]
        self._paid_total = snapshot.paid_total
        self._fill_ids = {row['fillId'] for row in self._paid}
        self._paid_quote_ids = {row['quoteId'] for row in self._paid}
        self._issued.clear()
        self._reconciled(full=False)
