"""Small synthetic event checks; never reads a market workspace or runs a trial."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import date, timedelta
import ast
from pathlib import Path
import unittest

from service.signal_target_study import SignalTargetStudy, summarize_events
from service.signal_target_study_fee_profile import (
    FeeProfileSignalTargetStudy, LegacyFeePolicy, DatedFeePolicy, FeeExecutionContext,
    verify_frozen_dependencies,
)


def fixture(start='2022-04-06', forward=None, symbol='sh600001'):
    prices = [10.]*6 + (forward if forward is not None else [10.,10.8,11.6,12.5,13.5,13.5])
    day = date.fromisoformat(start); rows=[]
    for price in prices:
        while day.weekday() > 4: day += timedelta(days=1)
        rows.append({'date':day.isoformat(),'open':price,'high':price,'low':price,'close':price,
            'volume':1_000_000,'amount':price*1_000_000})
        day += timedelta(days=1)
    security={'symbol':symbol,'bars':rows,'listingDate':'2010-01-01',
        'stByDate':{r['date']:False for r in rows},'tradeStatusByDate':{r['date']:True for r in rows},
        'precloseByDate':{r['date']:rows[max(0,i-1)]['close'] for i,r in enumerate(rows)}}
    data={'securities':[security],'calendar':[r['date'] for r in rows],'fingerprint':'fee-adapter-synthetic'}
    config={'commissionRate':.0003,'minCommission':5,'transferFeeRate':.00001,'stampDutyRate':None,
        'slippageBps':0,'maxParticipationRate':.1,
        'universe':{'strictHistory':False,'excludeSt':True,'minListingDays':0,'minAmount20':0}}
    signal={'strategyId':'synthetic','symbol':symbol,'signalDate':rows[5]['date'],'rank':1,'score':.8}
    return data,config,signal


def dated(config, includes=False):
    return DatedFeePolicy(commission_rate=config['commissionRate'],min_commission=config['minCommission'],
        commission_includes_transfer=includes,stamp_duty_override=config['stampDutyRate'])


def legacy_fields(event):
    result=deepcopy(event)
    result.pop('feeEvidence')
    return result


def active(study, signals):
    busy={}; attempts=[]; suppressed=[]
    for signal in signals:
        key=(signal['strategyId'],signal['symbol'])
        if key in busy and signal['signalDate'] < busy[key]:
            suppressed.append(deepcopy(signal));continue
        event=study.evaluate(signal);attempts.append(event)
        if event['entry']['status']=='filled':
            busy[key]=event['outcomes']['nextOpenNet30']['hitDate'] or study.end_date+'~'
    return attempts,suppressed


class FeeProfileSignalTargetStudyTests(unittest.TestCase):
    def test_legacy_full_events_summaries_and_independent_active_are_exact(self):
        for kind in ('exit','waiting','missing','unfilled','entryHigh','after2023'):
            with self.subTest(kind=kind):
                data,config,signal=fixture(start='2024-01-02' if kind=='after2023' else '2022-04-06')
                rows=data['securities'][0]['bars']
                if kind=='waiting':
                    for row in rows[6:]: row.update(open=10.,high=10.,low=10.,close=10.)
                    data['securities'][0]['precloseByDate']={r['date']:rows[max(0,i-1)]['close'] for i,r in enumerate(rows)}
                if kind=='missing': del rows[7]
                if kind=='unfilled': rows[6].update(open=11.,high=11.,low=11.,close=11.)
                if kind=='entryHigh': rows[6]['high']=14.
                config.update(commissionRate=0,minCommission=0,transferFeeRate=.00007,stampDutyRate=0)
                original=deepcopy((data,config,signal))
                old=SignalTargetStudy(data,config); new=FeeProfileSignalTargetStudy(data,config,fee_policy=LegacyFeePolicy())
                signals=[signal,{**signal,'signalDate':data['calendar'][8]},
                         {**signal,'strategyId':'other','signalDate':data['calendar'][8]},
                         {**signal,'signalDate':data['calendar'][10]}]
                before,blocked=active(old,signals); after,blocked2=active(new,signals)
                self.assertEqual([legacy_fields(e) for e in after],before)
                self.assertEqual(blocked2,blocked)
                self.assertEqual(summarize_events(after,horizons=[3,5,10]),summarize_events(before,horizons=[3,5,10]))
                self.assertEqual((data,config,signal),original)

    def test_transfer_boundary_both_markets_uses_each_actual_execution_date(self):
        for symbol in ('sh600001','sz000001'):
            data,config,signal=fixture(start='2022-04-20',forward=[10.,10.2,10.2],symbol=symbol)
            config.update(commissionRate=0,minCommission=0,stampDutyRate=0)
            event=FeeProfileSignalTargetStudy(data,config,fee_policy=dated(config),target_pct=1).evaluate(signal)
            ledger=event['feeEvidence']['paidLedger']
            self.assertEqual([r['executionDate'] for r in ledger],['2022-04-28','2022-04-29'])
            self.assertEqual([r['rates']['transfer'] for r in ledger],[.00002,.00001])
            self.assertEqual(ledger[0]['breakdown']['transfer'],event['entry']['amount']*.00002)
            self.assertEqual(ledger[1]['breakdown']['transfer'],ledger[1]['amount']*.00001)

    def test_signal_date_reserve_keeps_lower_lot_at_announcement_boundary(self):
        data,config,signal=fixture(start='2022-04-21',forward=[10.,10.2])
        config.update(commissionRate=0,minCommission=0,stampDutyRate=0)
        event=FeeProfileSignalTargetStudy(data,config,fee_policy=dated(config),budget=99001.5).evaluate(signal)
        self.assertEqual(signal['signalDate'],'2022-04-28')
        self.assertEqual(event['entry']['plannedQuantity'],8900)
        self.assertEqual(event['entry']['quantity'],8900)
        self.assertEqual(event['entry']['fee'],89000*.00001)
        reserve=[q for q in event['feeEvidence']['quotes'] if q['purpose']=='reserve']
        self.assertTrue(reserve)
        self.assertTrue(all(q['quoteEffectiveDate']==q['decisionDate']=='2022-04-28' and
            q['executionDate'] is None and q['rates']['transfer']==.00002 for q in reserve))
        later=deepcopy(data);later['securities'][0]['bars'][6].update(open=9.9,low=9.9,volume=9_000_000)
        changed=FeeProfileSignalTargetStudy(later,config,fee_policy=dated(config),budget=99001.5).evaluate(signal)
        self.assertEqual(changed['entry']['plannedQuantity'],8900)

    def test_stamp_boundary_zero_and_explicit_override(self):
        for override in (None,0,.0007):
            data,config,signal=fixture(start='2023-08-17',forward=[10.,10.2,10.2])
            config.update(commissionRate=0,minCommission=0,stampDutyRate=override)
            event=FeeProfileSignalTargetStudy(data,config,fee_policy=dated(config),target_pct=1).evaluate(signal)
            buy,sell=event['feeEvidence']['paidLedger']
            self.assertEqual([buy['executionDate'],sell['executionDate']],['2023-08-25','2023-08-28'])
            self.assertEqual(buy['breakdown']['stampDuty'],0)
            rate=.0005 if override is None else override
            self.assertEqual(sell['breakdown']['stampDuty'],sell['amount']*rate)
            self.assertEqual(sell['rates']['stampDutyOverrideApplied'],override is not None)

    def test_commission_inclusion_minimum_and_no_duplicate_transfer(self):
        data,config,signal=fixture()
        config.update(commissionRate=.000001,minCommission=5,stampDutyRate=0)
        separate=FeeProfileSignalTargetStudy(data,config,fee_policy=dated(config)).evaluate(signal)
        included=FeeProfileSignalTargetStudy(data,config,fee_policy=dated(config,True)).evaluate(signal)
        self.assertEqual(included['entry']['fee'],5)
        self.assertEqual(separate['entry']['fee'],5+separate['entry']['amount']*.00002)
        entry=included['feeEvidence']['paidLedger'][0]
        self.assertEqual(entry['breakdown']['transfer'],0)
        self.assertGreater(entry['transferDisclosure']['notionalTransferReference'],0)
        self.assertFalse(entry['transferDisclosure']['commissionInternalAllocationKnown'])

    def test_fee_difference_changes_first_target_and_later_active_without_relabeling(self):
        data,config,signal=fixture(forward=[10.,10.8,11.6,12.5,13.0004,13.1,13.1])
        config.update(commissionRate=0,minCommission=0,stampDutyRate=0)
        old=FeeProfileSignalTargetStudy(data,config,fee_policy=LegacyFeePolicy())
        new=FeeProfileSignalTargetStudy(data,config,fee_policy=dated(config))
        first,second=old.evaluate(signal),new.evaluate(signal)
        self.assertEqual(first['outcomes']['nextOpenNet30']['tradingDays'],5)
        self.assertEqual(second['outcomes']['nextOpenNet30']['tradingDays'],6)
        self.assertEqual(first['outcomes']['rawHigh30'],second['outcomes']['rawHigh30'])
        following={**signal,'signalDate':data['calendar'][10]}
        a,b=active(old,[signal,following]);c,d=active(new,[signal,following])
        self.assertEqual((len(a),len(b),len(c),len(d)),(2,0,1,1))

    def test_probes_and_hypothetical_mark_never_book_fees(self):
        data,config,signal=fixture(forward=[10.]*18)
        event=FeeProfileSignalTargetStudy(data,config,fee_policy=dated(config)).evaluate(signal)
        evidence=event['feeEvidence']
        self.assertEqual(len(evidence['paidLedger']),1)
        self.assertGreater(len(evidence['quotes']),20)
        self.assertEqual(evidence['paidFeeTotal'],event['entry']['fee'])
        self.assertEqual(evidence['quotes'][-1]['purpose'],'hypotheticalLiquidation')
        self.assertIsNone(evidence['quotes'][-1]['executionDate'])
        data['securities'][0]['bars'][6].update(open=11.,high=11.,low=11.,close=11.)
        unfilled=FeeProfileSignalTargetStudy(data,config,fee_policy=dated(config)).evaluate(signal)
        self.assertEqual(unfilled['entry']['status'],'unfilled')
        self.assertEqual(unfilled['feeEvidence']['paidLedger'],[])
        self.assertEqual(unfilled['feeEvidence']['paidFeeTotal'],0)

    def test_exit_open_excursions_and_cutoff_quote_date_with_stale_mark(self):
        data,config,signal=fixture(forward=[10.,10.8,11.6,12.5,13.5,13.5])
        data['securities'][0]['bars'][10].update(low=1.,high=100.)
        event=FeeProfileSignalTargetStudy(data,config,fee_policy=dated(config)).evaluate(signal)
        self.assertEqual(event['excursions']['maePct'],0)
        self.assertEqual(event['excursions']['mfePct'],(13.5/10.-1)*100)
        self.assertEqual(len(event['feeEvidence']['paidLedger']),2)
        self.assertEqual(event['observation']['availableTradingDays'],6)
        data,config,signal=fixture(start='2023-08-17',forward=[10.,10.,10.])
        data['securities'][0]['bars']=data['securities'][0]['bars'][:-2]
        event=FeeProfileSignalTargetStudy(data,config,fee_policy=dated(config)).evaluate(signal)
        self.assertEqual(event['terminal']['priceDate'],'2023-08-25')
        self.assertEqual(event['feeEvidence']['quotes'][-1]['quoteEffectiveDate'],'2023-08-29')
        self.assertEqual(event['feeEvidence']['quotes'][-1]['rates']['chargedStampDuty'],.0005)

    def test_scope_conflicts_immutable_policy_and_event_context_isolation(self):
        data,config,signal=fixture()
        policy=dated(config)
        with self.assertRaises(FrozenInstanceError): policy.commission_rate=1
        for name,value in (('transferFeeRate',0),('commissionRate',.0004),('minCommission',6),('stampDutyRate',0)):
            bad={**config,name:value}
            with self.subTest(name=name),self.assertRaises(ValueError):
                FeeProfileSignalTargetStudy(data,bad,fee_policy=policy)
        future,settings,signal2=fixture(start='2023-12-18')
        with self.assertRaises(ValueError):
            FeeProfileSignalTargetStudy(future,settings,fee_policy=dated(settings)).evaluate(signal2)
        unsupported,settings,signal2=fixture(symbol='bj430001')
        with self.assertRaises(ValueError):
            FeeProfileSignalTargetStudy(unsupported,settings,fee_policy=dated(settings)).evaluate(signal2)
        study=FeeProfileSignalTargetStudy(data,config,fee_policy=policy)
        event=study.evaluate(signal);event['feeEvidence']['paidLedger'][0]['breakdown']['total']=-1
        again=study.evaluate(signal)
        self.assertGreater(again['feeEvidence']['paidLedger'][0]['breakdown']['total'],0)
        study.config['commissionRate']=.0004
        with self.assertRaises(ValueError): study.evaluate(signal)
        with self.assertRaises(TypeError): LegacyFeePolicy(commission_includes_transfer=True)
        changed_policy=FeeProfileSignalTargetStudy(data,config,fee_policy=policy)
        changed_policy.fee_policy=dated(config,True)
        with self.assertRaises(ValueError): changed_policy.evaluate(signal)
        for wrong_field,wrong_value in (('signal_day',data['calendar'][6]),('market','SZ')):
            def wrong_factory(owner,symbol,signal_day,policy):
                context=FeeExecutionContext(owner,symbol,signal_day,policy)
                setattr(context,wrong_field,wrong_value)
                return context
            with self.subTest(factoryField=wrong_field),self.assertRaises(ValueError):
                FeeProfileSignalTargetStudy(data,config,fee_policy=policy,
                    execution_factory=wrong_factory).evaluate(signal)
        foreign_data=deepcopy(data)
        foreign_data['securities'][0]['bars'][6].update(open=9.9,low=9.9)
        foreign=FeeProfileSignalTargetStudy(foreign_data,config,fee_policy=policy)
        with self.assertRaises(ValueError):
            FeeProfileSignalTargetStudy(data,config,fee_policy=policy,
                execution_factory=lambda owner,symbol,day,p: FeeExecutionContext(foreign,symbol,day,p)
            ).evaluate(signal)

    def test_only_explicit_fee_seams_differ_from_frozen_evaluate_ast(self):
        root=Path(__file__).resolve().parents[1]
        def method(path,clsname):
            tree=ast.parse(path.read_text(encoding='utf-8'))
            cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name==clsname)
            return next(x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name=='evaluate')
        original=method(root/'service/signal_target_study.py','SignalTargetStudy')
        copied=method(root/'service/signal_target_study_fee_profile.py','FeeProfileSignalTargetStudy')
        class ReverseFeeSeams(ast.NodeTransformer):
            def visit_Assign(self,node):
                if any(isinstance(t,ast.Subscript) and isinstance(t.slice,ast.Constant) and t.slice.value=='feeEvidence' for t in node.targets):
                    return None
                return self.generic_visit(node)
            def visit_Expr(self,node):
                if isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and node.value.func.attr=='book_last':
                    return None
                return self.generic_visit(node)
            def visit_FunctionDef(self,node):
                if node.name=='net_return': node.args.args=node.args.args[:2]
                return self.generic_visit(node)
            def visit_Call(self,node):
                if isinstance(node.func,ast.Attribute):
                    if node.func.attr=='_make_execution':
                        return ast.Call(func=ast.Name(id='_Execution',ctx=ast.Load()),args=[ast.Name(id='self',ctx=ast.Load())],keywords=[])
                    if node.func.attr=='reserve_quantity': node.func.attr='_affordable_quantity'
                    if node.func.attr=='quote': node.func.attr='_fees';node.keywords=[]
                elif isinstance(node.func,ast.Name) and node.func.id=='net_return': node.args=node.args[:2]
                return self.generic_visit(node)
        restored=ReverseFeeSeams().visit(copied)
        self.assertEqual(ast.dump(original,include_attributes=False),ast.dump(restored,include_attributes=False))
        verify_frozen_dependencies()


if __name__ == '__main__': unittest.main()
