"""Independent account audit regression tests; synthetic prices only."""
from copy import deepcopy
from contextlib import redirect_stdout
import gzip
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('account_exit_independent_verifier',ROOT/'scripts/verify-account-exit-comparison.py')
v=importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
from tests.test_research_fixed_signal_account import fixture,settings,signal,set_prices
from service.research_fixed_signal_account import FixedSignalAccount,DatedFeePolicy
from service.research_account_outcomes import summarize_account


class AccountExitVerifierTests(unittest.TestCase):
    def run_account(self,*,start='2023-08-14',stop=0.,trail=0.,signals=None,edit=None):
        data=fixture(start=start,length=18)
        if edit: edit(data)
        config=settings(data,initialCapital=100000,commissionRate=.0003,minCommission=5.,
            transferFeeRate=.00001,stampDutyRate=None,slippageBps=10.,stopLossPct=stop,
            trailingStopPct=trail,maxPositions=2,maxStockWeight=.6)
        schedule=signals(data) if signals else [signal(data)]
        engine=FixedSignalAccount(data,schedule,config,entry_weight=.6,
            fee_policy=DatedFeePolicy(commission_rate=.0003,min_commission=5.,commission_includes_transfer=False))
        engine.step(20000)
        return data,schedule,engine.config,engine.result(),engine.checkpoint()

    def audit(self,package):
        data,signals,config,result,state=package
        checks=v.Checks(); v.audit_account(checks,data,signals,result,state,config,.6)
        summary=summarize_account(result,state,policy_id='synthetic',policy_label='Synthetic')
        v.audit_summary(checks,summary,result,state,{'id':'synthetic','title':'Synthetic'})
        return checks

    def test_three_paths_raw_independent_ledger_and_fee_boundary(self):
        def edit(data):
            values=[10.]*7+[10.4,10.8,11.,10.5,9.8,10.,10.,10.,10.,10.,10.]
            set_prices(data,'sh600001',values)
            data['securities'][0]['openingVolumeByDate']={d:10000 for d in data['calendar']}
        for start in ('2022-04-18','2023-08-14'):
            for stop,trail in ((0.,0.),(0.,.1),(.05,.1)):
                with self.subTest(start=start,stop=stop,trail=trail):
                    checks=self.audit(self.run_account(start=start,stop=stop,trail=trail,edit=edit))
                    self.assertEqual(checks.failed,0,checks.issues)

    def test_cash_rank_partial_missing_one_attempt_and_stale(self):
        def edit(data):
            data['securities'][0]['bars']=[b for b in data['securities'][0]['bars'] if b['date']!=data['calendar'][6]]
            data['securities'][1]['openingVolumeByDate']={data['calendar'][6]:1500}
        def signals(data):
            return [signal(data,symbol='sh600001',rank=1),signal(data,symbol='sz000001',rank=2),
                    signal(data,symbol='sh600002',rank=4),signal(data,index=17,symbol='sh600002',rank=1)]
        checks=self.audit(self.run_account(edit=edit,signals=signals))
        self.assertEqual(checks.failed,0,checks.issues)

    def test_rehashed_checkpoint_does_not_hide_fee_and_quantity_corruption(self):
        package=self.run_account(); bad=deepcopy(package)
        bad[3]['trades'][0]['quantity']+=100
        checks=self.audit(bad)
        self.assertTrue(any(x['check']=='independent-actual-trades' for x in checks.issues))
        bad=deepcopy(package); bad[4]['feeState']['quotes'][0]['breakdown']['total']+=1
        bad[4]['feeState']['checkpointDigest']=v.digest({k:x for k,x in bad[4]['feeState'].items() if k!='checkpointDigest'})
        bad[4]['digest']=v.digest({k:x for k,x in bad[4].items() if k!='digest'})
        checks=self.audit(bad)
        self.assertTrue(any(x['check']=='independent-quote-component' for x in checks.issues))

    def test_early_or_omitted_exit_and_opportunity_are_detected(self):
        def edit(data): set_prices(data,'sh600001',[10.]*7+[9.4]*11)
        package=self.run_account(stop=.05,edit=edit)
        self.assertTrue(package[3]['closedTrades'])
        for change in ('deleteSell','wrongSignalDate','missingOpportunity'):
            bad=deepcopy(package)
            if change=='deleteSell': bad[3]['trades']=bad[3]['trades'][:1]
            elif change=='wrongSignalDate': bad[3]['trades'][-1]['signalDate']=bad[0]['calendar'][5]
            else: bad[3]['research']['opportunities']=[]
            checks=self.audit(bad)
            self.assertGreater(checks.failed,0)

    def test_empty_signal_input_and_declared_action_fail_closed(self):
        package=self.run_account(signals=lambda data:[])
        checks=self.audit(package); self.assertEqual(checks.failed,0,checks.issues)
        altered=deepcopy(package); altered[0]['securities'][0]['corporateActions']=[{'date':'2023-08-22'}]
        with self.assertRaisesRegex(ValueError,'Declared actions'): self.audit(altered)

    def full_fixture(self):
        from tests.test_account_exit_comparison_runner import AccountExitComparisonRunnerTests
        f=AccountExitComparisonRunnerTests(); f.setUp(); self.addCleanup(f.doCleanups)
        f.run_complete(); return f

    def full_verify(self,f):
        # Only the old metadata/raw I/O module's synthetic registration is
        # substituted. No new binding, audit, ledger or arithmetic is mocked.
        with f.registered(), patch.object(v,'REGISTRATION',f.registration), \
                patch.object(v,'REGISTRATION_SHA256',f.registration_hash), \
                patch.object(v,'import_source',return_value=f.parent_fixture.runner), \
                patch.object(FixedSignalAccount,'step',side_effect=AssertionError('Production replay forbidden')), \
                redirect_stdout(io.StringIO()):
            return v.verify(f.output,root=f.root,dataset=f.data)

    def reseal_output(self,f,key):
        completion=f.attempt()/'completion.json'; receipt=v.read(completion)
        receipt['files'][key]['sha256']=v.sha(f.output/receipt['files'][key]['path'])
        completion.write_text(json.dumps(receipt,ensure_ascii=False),encoding='utf8')
        journal=[json.loads(x) for x in (f.output/'events.jsonl').read_text(encoding='utf8').splitlines()]
        journal[-1]['receipt']=receipt
        (f.output/'events.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in journal),encoding='utf8')

    def test_complete_binding_nonempty_and_saved_report_accepted(self):
        f=self.full_fixture(); report=self.full_verify(f)
        self.assertTrue(report['passed'],report['issues']); self.assertGreater(report['opportunityCount'],0)
        self.assertGreater(report['checks'],1000)
        path=v.save_report(f.output,report)
        self.assertEqual(path.name,'independent-result-verification.json')
        with self.assertRaises(FileExistsError): v.save_report(f.output,report)
        self.assertEqual(f.execute(verify_only=True),0)

    def test_rehashed_full_output_semantics_and_stream_eof(self):
        f=self.full_fixture()
        path=f.attempt()/'hold.result.json.gz'; account=v.read(path)
        account['trades'][0]['quantity']+=100
        with gzip.open(path,'wt',encoding='utf8') as stream: json.dump(account,stream,ensure_ascii=False)
        self.reseal_output(f,'holdResult')
        report=self.full_verify(f)
        self.assertFalse(report['passed'])
        self.assertTrue(any(i['check']=='independent-actual-trades' for i in report['issues']),report['issues'])
        path=f.attempt()/'opportunities.jsonl.gz'; rows=list(v.records(path))
        with gzip.open(path,'wt',encoding='utf8') as stream:
            for row in rows[:-1]: stream.write(json.dumps(row)+'\n')
        self.reseal_output(f,'opportunities')
        report=self.full_verify(f)
        self.assertTrue(any(i['check']=='independent-full-opportunity-lineage' for i in report['issues']),report['issues'])


if __name__=='__main__': unittest.main()
