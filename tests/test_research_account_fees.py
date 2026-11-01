"""Small, synthetic account-fee tests; no market workspace or strategy replay."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from service.portfolio_config import normalize_config
from service.portfolio_engine import PortfolioEngine, _digest as data_digest
from service.execution_evidence import audit_opening_fills
from service.research_account_fees import (
    AccountFeeLedger, OrderFeeContext, LegacyFeePolicy, DatedFeePolicy,
)


def owner(**overrides):
    config = normalize_config({'commissionRate': .0003, 'minCommission': 5,
        'transferFeeRate': .00001, 'stampDutyRate': None, **overrides})
    securities = {symbol: {'symbol': symbol, 'bars': []} for symbol in ('sh600001', 'sz000001')}
    dataset = {'securities': list(securities.values()), 'fingerprint': 'synthetic-fee-owner'}
    return SimpleNamespace(config=config, securities=securities, dataset=dataset,
        data_digest=data_digest(dataset), fingerprint=dataset['fingerprint'], state={'trades': []})


def dated(account, includes=False):
    return DatedFeePolicy(commission_rate=account.config['commissionRate'],
        min_commission=account.config['minCommission'], commission_includes_transfer=includes,
        stamp_duty_override=account.config['stampDutyRate'])


def context(day='2022-04-29', signal='2022-04-28', purpose='fillQuote', symbol='sh600001'):
    if purpose == 'signalReserve':
        return OrderFeeContext(symbol, signal, signal, signal, None, purpose)
    return OrderFeeContext(symbol, signal, day, day, day if purpose == 'fillQuote' else None, purpose)


def fill_for(quote, quantity=100, price=10.):
    return {'date': quote['context']['execution_date'], 'signalDate': quote['context']['signal_date'],
        'symbol': quote['symbol'], 'side': quote['side'], 'quantity': quantity, 'price': price,
        'amount': quantity*price, 'fee': quote['breakdown']['total'], 'cashAfter': 100000.,
        'feeQuoteId': quote['quoteId']}


def append_fill(ledger, account, *, day='2022-04-29', signal='2022-04-28', side='buy',
                quantity=100, price=10., symbol='sh600001'):
    quote = ledger.quote(quantity*price, side, context(day, signal, symbol=symbol))
    fill = fill_for(quote, quantity, price)
    account.state['trades'].append(fill)
    paid = ledger.commit_fill(quote, fill, 'fill-'+str(len(account.state['trades'])))
    return quote, fill, paid


def reseal(state):
    state.pop('checkpointDigest', None)
    state['checkpointDigest'] = hashlib.sha256(json.dumps(state, sort_keys=True,
        separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return state


class ResearchAccountFeeTests(unittest.TestCase):
    def test_context_is_immutable_and_dates_purposes_sides_are_explicit(self):
        account = owner(); ledger = AccountFeeLedger(account, LegacyFeePolicy())
        ctx = context()
        with self.assertRaises(FrozenInstanceError):
            ctx.effective_fee_date = '2022-04-28'
        for kwargs in ({'signal_date':'2022-4-28'}, {'decision_date':'2022-04-28'},
                       {'effective_fee_date':'2022-04-28'}, {'execution_date':None}, {'purpose':'probe'}):
            values = vars(ctx).copy(); values.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                OrderFeeContext(**values)
        for purpose in ('signalReserve', 'executionSizing'):
            with self.assertRaises(ValueError):
                ledger.quote(1000, 'sell', context(purpose=purpose))
        for bad in (True, -1, float('nan'), float('inf'), '1000'):
            with self.subTest(amount=bad), self.assertRaises(ValueError):
                ledger.quote(bad, 'buy', ctx)
        self.assertEqual(ledger.checkpoint()['paidLedger'], [])

    def test_legacy_fee_totals_are_exact_for_overrides_dates_and_markets(self):
        for stamp in (None, 0., .0007):
            account = owner(stampDutyRate=stamp, transferFeeRate=.00007)
            ledger = AccountFeeLedger(account, LegacyFeePolicy())
            for symbol in account.securities:
                for day, signal in (('2022-04-29','2022-04-28'), ('2023-08-28','2023-08-25'),
                                    ('2024-01-03','2024-01-02')):
                    for side in ('buy','sell'):
                        for amount in (0., 1000., 123456.789):
                            quote = ledger.quote(amount, side, context(day,signal,symbol=symbol))
                            self.assertEqual(quote['breakdown']['total'], PortfolioEngine._fees(account,amount,side,day))
            self.assertEqual(ledger.checkpoint()['paidFeeTotal'], 0)
            self.assertEqual(ledger.settings['minimumCommissionUnit'], 'each-simulated-fill')

    def test_reserve_uses_signal_date_actual_sizing_and_fill_use_attempt_date(self):
        account = owner(commissionRate=0, minCommission=0, stampDutyRate=0)
        ledger = AccountFeeLedger(account, dated(account))
        planned = ledger.affordable_quantity(9000,100,100,11.,99001.5,context(purpose='signalReserve'))
        later = ledger.affordable_quantity(9000,100,100,11.,99001.5,context(purpose='executionSizing'))
        self.assertEqual((planned,later),(8900,9000))
        _,fill,_ = append_fill(ledger,account,quantity=planned)
        self.assertEqual(fill['fee'],89000*.00001)
        state = ledger.checkpoint()
        self.assertEqual(len(state['paidLedger']),1)
        self.assertEqual(state['paidFeeTotal'],89000*.00001)
        reserve = [q for q in state['quotes'] if q['context']['purpose']=='signalReserve']
        self.assertTrue(all(q['rates']['transfer']==.00002 and q['context']['execution_date'] is None for q in reserve))

    def test_stamp_boundaries_and_zero_override_remain_per_sell_fill(self):
        for stamp in (None,0.,.0007):
            account=owner(commissionRate=0,minCommission=0,stampDutyRate=stamp)
            ledger=AccountFeeLedger(account,dated(account))
            _,buy,_=append_fill(ledger,account,day='2023-08-25',signal='2023-08-24')
            _,old,_=append_fill(ledger,account,day='2023-08-25',signal='2023-08-24',side='sell')
            _,new,_=append_fill(ledger,account,day='2023-08-28',signal='2023-08-24',side='sell')
            self.assertEqual(buy['fee'],1000*.00001)
            self.assertEqual(old['fee'],1000*.00001+1000*(.001 if stamp is None else stamp))
            self.assertEqual(new['fee'],1000*.00001+1000*(.0005 if stamp is None else stamp))

    def test_many_partial_fills_each_pay_minimum_and_inclusion_is_not_double_charged(self):
        for included in (False,True):
            account=owner(commissionRate=.000001,minCommission=5,stampDutyRate=0)
            ledger=AccountFeeLedger(account,dated(account,included))
            for day,side,quantity in (('2022-04-28','buy',100),('2022-04-29','buy',200),
                                      ('2022-05-02','sell',100),('2022-05-03','sell',200)):
                append_fill(ledger,account,day=day,signal='2022-04-27',side=side,quantity=quantity)
            state=ledger.checkpoint()
            expected=[5 if included else 5+amount*rate for amount,rate in ((1000,.00002),(2000,.00001),(1000,.00001),(2000,.00001))]
            self.assertEqual([r['quote']['breakdown']['total'] for r in state['paidLedger']],expected)
            self.assertEqual(state['paidFeeTotal'],sum(expected))
            self.assertEqual(len(state['paidLedger']),4)
            if included:
                self.assertGreater(state['paidLedger'][0]['quote']['transferDisclosure']['notionalTransferReference'],0)

    def test_only_new_original_fill_and_original_untampered_quote_can_commit(self):
        for kind in ('copiedQuote','foreignQuote','changedQuote','noAppend','copiedFill','probe'):
            account=owner(); ledger=AccountFeeLedger(account,dated(account))
            q=ledger.quote(1000.,'buy',context(purpose='executionSizing' if kind=='probe' else 'fillQuote'))
            fill=fill_for(q)
            if kind=='copiedQuote': q=deepcopy(q)
            if kind=='foreignQuote':
                other=owner(); foreign=AccountFeeLedger(other,dated(other));q=foreign.quote(1000.,'buy',context())
            if kind=='changedQuote': q['breakdown']['total']+=1
            if kind!='noAppend': account.state['trades'].append(fill)
            if kind=='copiedFill': fill=deepcopy(fill)
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                ledger.commit_fill(q,fill,'id-1')
        account=owner();ledger=AccountFeeLedger(account,dated(account))
        quote,fill,_=append_fill(ledger,account)
        with self.assertRaises(ValueError): ledger.commit_fill(quote,fill,'id-again')

    def test_every_economic_fill_field_is_bound_and_failed_commit_does_not_pay(self):
        for key,value in (('date','2022-05-02'),('signalDate','2022-04-27'),('symbol','sz000001'),
                          ('side','sell'),('quantity',200),('price',11.),('amount',999.),('fee',0.),('quantity',True)):
            account=owner();ledger=AccountFeeLedger(account,dated(account))
            quote=ledger.quote(1000.,'buy',context());fill=fill_for(quote);fill[key]=value
            account.state['trades'].append(fill)
            with self.subTest(key=key,value=value),self.assertRaises(ValueError): ledger.commit_fill(quote,fill,'bad')
            account.state['trades'].clear()
            self.assertEqual(ledger.checkpoint()['paidFeeTotal'],0)

    def test_eod_opening_quality_annotations_are_allowed_but_cash_and_fees_cannot_change(self):
        account=owner();ledger=AccountFeeLedger(account,dated(account))
        _,fill,_=append_fill(ledger,account)
        audit_opening_fills([fill],account.securities,{fill['symbol']:{'volume':100000}})
        checkpoint=ledger.checkpoint()
        self.assertNotIn('capacityBasis',checkpoint['paidLedger'][0]['fill'])
        self.assertFalse(fill['openingCapacityVerified'])
        ledger.restore(checkpoint)
        fill['cashAfter']+=1
        with self.assertRaises(ValueError): ledger.checkpoint()

    def test_checkpoint_json_roundtrip_continues_exactly_and_old_handles_expire(self):
        account=owner();ledger=AccountFeeLedger(account,dated(account))
        append_fill(ledger,account)
        stale=ledger.quote(1000.,'sell',context(day='2022-05-02'))
        saved=json.loads(json.dumps(ledger.checkpoint(),sort_keys=True))
        copy_account=deepcopy(account); resumed=AccountFeeLedger(copy_account,dated(copy_account));resumed.restore(saved)
        for current,current_owner in ((ledger,account),(resumed,copy_account)):
            append_fill(current,current_owner,day='2022-05-02',side='sell')
        self.assertEqual(resumed.checkpoint(),ledger.checkpoint())
        ledger.restore(ledger.checkpoint())
        account.state['trades'].append(fill_for(stale))
        with self.assertRaises(ValueError): ledger.commit_fill(stale,account.state['trades'][-1],'stale')

    def test_transaction_rollback_restores_every_counter_and_invalidates_handles(self):
        account=owner();ledger=AccountFeeLedger(account,dated(account))
        append_fill(ledger,account)
        before=ledger.checkpoint(); owner_before=deepcopy(account.state)
        snapshot=ledger.transaction_snapshot()
        stale=ledger.quote(1000.,'sell',context(day='2022-05-02'))
        append_fill(ledger,account,day='2022-05-02',side='sell')
        with self.assertRaises(ValueError): ledger.transaction_restore(snapshot)
        account.state=owner_before
        ledger.transaction_restore(snapshot)
        self.assertEqual(ledger.checkpoint(),before)
        account.state['trades'].append(fill_for(stale))
        with self.assertRaises(ValueError): ledger.commit_fill(stale,account.state['trades'][-1],'stale')
        account.state['trades'].pop()
        fresh=ledger.quote(1000.,'sell',context(day='2022-05-02'))
        self.assertEqual(fresh['quoteId'],len(before['quotes']))
        other_owner=owner();other=AccountFeeLedger(other_owner,dated(other_owner))
        with self.assertRaises(ValueError): other.transaction_restore(snapshot)

    def test_rehashed_checkpoint_forgery_is_rejected_without_mutating_current_ledger(self):
        account=owner();ledger=AccountFeeLedger(account,dated(account));append_fill(ledger,account)
        original=ledger.checkpoint()
        for kind in ('total','quote','paidQuote','duplicateFill','identity'):
            bad=deepcopy(original)
            if kind=='total': bad['paidFeeTotal']+=1
            if kind=='quote': bad['quotes'][0]['breakdown']['total']+=1
            if kind=='paidQuote': bad['paidLedger'][0]['quote']['amount']+=1
            if kind=='duplicateFill': bad['paidLedger'].append(deepcopy(bad['paidLedger'][0]))
            if kind=='identity': bad['identity']['datasetFingerprint']='foreign'
            with self.subTest(kind=kind),self.assertRaises(ValueError): ledger.restore(reseal(bad))
            self.assertEqual(ledger.checkpoint(),original)

    def test_config_policy_owner_data_identity_and_source_drift_fail_closed(self):
        for kind in ('config','policy','owner','securities','dataIdentity'):
            account=owner();ledger=AccountFeeLedger(account,dated(account))
            if kind=='config': account.config['commissionRate']=.0004
            if kind=='policy': ledger.fee_policy=dated(account,True)
            if kind=='owner': ledger.owner=deepcopy(account)
            if kind=='securities': account.securities=deepcopy(account.securities)
            if kind=='dataIdentity': account.data_digest='changed'
            with self.subTest(kind=kind),self.assertRaises(ValueError): ledger.quote(1000.,'buy',context())
        account=owner();ledger=AccountFeeLedger(account,dated(account))
        read_bytes=Path.read_bytes
        def changed(path):
            return b'changed source' if path.name=='research_fee_profile.py' else read_bytes(path)
        with patch.object(Path,'read_bytes',changed),self.assertRaises(ValueError): ledger.checkpoint()
        self.assertEqual(ledger.checkpoint()['paidLedger'],[])

    def test_dated_scope_conflicts_and_invalid_sizing_are_rejected(self):
        account=owner();policy=dated(account)
        for key,value in (('commissionRate',0),('minCommission',0),('transferFeeRate',0),('stampDutyRate',0)):
            bad=owner(**{key:value})
            with self.subTest(key=key),self.assertRaises(ValueError): AccountFeeLedger(bad,policy)
        ledger=AccountFeeLedger(account,policy)
        with self.assertRaises(ValueError): ledger.quote(1000.,'buy',context(day='2024-01-03',signal='2024-01-02'))
        for index,value in ((0,True),(1,0),(2,0),(3,float('inf')),(4,-1)):
            args=[1000,100,100,10.,10000.];args[index]=value
            with self.subTest(index=index),self.assertRaises(ValueError):
                ledger.affordable_quantity(*args,context(purpose='signalReserve'))
        self.assertEqual(ledger.affordable_quantity(100,100,100,10.,0.,context(purpose='signalReserve')),0)
        self.assertEqual(ledger.checkpoint()['paidFeeTotal'],0)


if __name__=='__main__': unittest.main()
