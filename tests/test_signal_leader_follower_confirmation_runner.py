"""Temporary archived-stream integration; never reads real research artifacts.

The inherited peer fixture has genuine synthetic raw bars and causal features,
hand-authored strength ranks, and a test-only panel proof. Its peer run and
independent audit complete before the new follower binding is constructed.
"""
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
import gzip
import importlib.util
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import zipfile

import test_signal_leader_peer_expansion_runner as fixtures

ROOT=Path(__file__).resolve().parents[1]
SOURCE='scripts/run-signal-leader-follower-confirmation.py'
REGISTRATION='docs/signal-leader-follower-confirmation-v1.json'
write,write_lines,lines=fixtures.write,fixtures.write_lines,fixtures.lines


def module(path=SOURCE,name='follower_runner_test'):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    loaded=importlib.util.module_from_spec(spec);spec.loader.exec_module(loaded)
    return loaded


class LeaderFollowerConfirmationRunnerTests(unittest.TestCase):
    """Public complete temporary fixture for the independent new verifier."""
    def setUp(self):
        f=fixtures.LeaderPeerExpansionRunnerTests();f.setUp()
        self.addCleanup(f.doCleanups);self.parent_fixture=f
        self.runner=module()
        self.root,self.workspace,self.source,self.panel=f.root,f.workspace,f.source,f.panel
        self.data,self.symbols,self.target_dates=f.data,f.symbols,f.target_dates
        self.parent_output=f.output;self.output=self.root/'follower-output'
        # At A=target[10], these members are weak; their first true observation
        # at target[12] is a real synthetic parent breakout. Seven simultaneous
        # members and five anchors exercise ownership, Top5 and anchor collision.
        for row in f.rows:
            if row['symbol'] in self.symbols[6:13] and row['asOfDate']>=self.target_dates[12]:
                row.update(relativeReturn20Percentile=.95,relativeReturn60Percentile=.95,
                    meanStrengthPercentile20=.95,minimumStrengthPercentile20=.95,
                    trailing20DailyStrengthPercentiles=[.95]*20)
        f.rebind()
        (self.root/'synthetic-contract.txt').write_bytes(b'Eligibility fixture only; no actual market audit.')
        self.assertEqual(f.run_complete(),0)
        verifier=module('scripts/verify-signal-leader-peer-expansion-result.py','follower_source_auditor_test')
        with patch.object(verifier,'REGISTRATION',f.registration),patch.object(verifier,'REGISTRATION_SHA256',f.registration_hash),redirect_stdout(io.StringIO()):
            proof=verifier.verify(f.output,root=f.root)
        self.assertTrue(proof['passed'],proof)
        verifier.save_report(f.output,proof)
        self.doc=self.runner.read_json(ROOT/REGISTRATION)
        self.document=self.doc
        self.doc['registeredAt']='synthetic-only'
        self.doc['sample']=deepcopy(f.doc['sample']);self.doc['config']=deepcopy(f.doc['config'])
        self.registration=self.root/REGISTRATION
        for name in {SOURCE,'service/signal_leader_follower_confirmation.py',
                     'tests/test_signal_leader_follower_confirmation.py',
                     'tests/test_signal_leader_follower_confirmation_runner.py',
                     *self.runner.ADDITIONAL_IMPLEMENTATION}:
            destination=self.root/name;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,destination)
        self.rebind_parent()

    def rebind_parent(self,*,resign=False):
        """Refresh declared hashes; resign=True intentionally forges test evidence.

        This reaches structural stream validation after a corruption. It never
        changes real proofs or makes the synthetic edit an independent audit.
        """
        f=self.parent_fixture;protocol=self.runner.read_json(f.output/'protocol.json')
        receipt=self.runner.read_json(f.attempt()/'completion.json')
        proof=self.runner.read_json(f.output/'independent-result-verification.json')
        if resign:
            for item in receipt['files'].values():item['sha256']=self.runner.sha(f.output/item['path'])
            write(f.attempt()/'completion.json',receipt)
            journal=[json.loads(s) for s in (f.output/'events.jsonl').read_text(encoding='utf-8').splitlines()]
            journal[-1]['receipt']=receipt
            (f.output/'events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in journal),encoding='utf-8')
            proof['completionSha256']=self.runner.sha(f.attempt()/'completion.json')
            proof['resultSha256']=receipt['files']['result']['sha256']
            proof['testOnlyForgedForStructuralRegression']=True
            write(f.output/'independent-result-verification.json',proof)
        paths=[self.parent_fixture.registration,f.attempt()/'completion.json',f.output/'independent-result-verification.json']
        paths.extend(f.output/item['path'] for item in receipt['files'].values())
        self.doc['sourceBinding'].update(parentDirectory=f.output.relative_to(self.root).as_posix(),
            parentProtocolHash=protocol['protocolHash'],
            files={p.relative_to(self.root).as_posix():self.runner.sha(p) for p in paths})
        write(self.registration,self.doc);self.registration_hash=self.runner.sha(self.registration)

    @contextmanager
    def registered(self):
        with patch.object(self.runner,'ROOT',self.root),patch.object(self.runner,'__file__',str(self.root/SOURCE)),\
             patch.object(self.runner,'REGISTRATION',self.registration),patch.object(self.runner,'REGISTRATION_SHA256',self.registration_hash):
            yield

    def args(self,**changes):
        values={'workspace':self.workspace,'output':self.output,'diagnostic_unverified_history':True};values.update(changes)
        return Namespace(**values)

    def freeze(self):
        with self.registered(),redirect_stdout(io.StringIO()):return self.runner.freeze(self.args())

    def execute(self):
        with self.registered(),redirect_stdout(io.StringIO()):return self.runner.execute(self.args())

    def run_complete(self):self.freeze();return self.execute()

    def attempt(self):return self.output/'attempts/attempt-0001'

    def result(self):
        with gzip.open(self.attempt()/'result.json.gz','rt',encoding='utf-8') as stream:return json.load(stream)

    def collect(self,destination=None):
        destination=destination or self.root/'collected';destination.mkdir()
        with self.registered():return self.runner.collect_signals(self.data,self.runner.load_binding(),destination)

    def test_exclusive_metadata_freeze_complete_archive(self):
        with patch.object(self.runner,'iter_source_days',side_effect=AssertionError('no source sequence at freeze')),\
             patch.object(self.runner,'load_dataset',side_effect=AssertionError('no outcome data at freeze')):
            protocol=self.freeze()
        self.assertFalse((self.output/'attempts').exists())
        self.assertEqual(protocol['groups'],list(self.runner.GROUPS))
        self.assertFalse(protocol['inheritedParentPredicatesRecomputed'])
        with zipfile.ZipFile(self.output/'execution-sources.zip') as archive:
            self.assertEqual(set(archive.namelist()),set(protocol['implementation']))
            self.assertIn('tests/test_signal_leader_follower_confirmation_verifier.py',archive.namelist())
        with self.assertRaisesRegex(ValueError,'Exclusive freeze'):self.freeze()

    def test_complete_stream_multi_ownership_and_no_backfill(self):
        before=deepcopy(self.data);signals,performance=self.collect()
        self.assertEqual(self.data,before)
        self.assertEqual(performance['sourcePredicateRows'],26*24)
        self.assertEqual(performance['scoredSessions'],24)
        coverage=lines(self.root/'collected/coverage.jsonl.gz')
        self.assertEqual([r['date'] for r in coverage],self.target_dates)
        self.assertTrue(all(sum(r['parentQCounts'].values())==26 for r in coverage))
        forms=lines(self.root/'collected/formations.jsonl.gz')
        self.assertEqual(len(forms),performance['sourceFormations'])
        self.assertTrue(any(r['record']['kind']=='formationSuppressedByLock' for r in forms))
        selection=lines(self.root/'collected/selection.jsonl.gz')[12]['selection']
        self.assertGreater(selection['rawConfirmationCount'],selection['uniqueMemberCount'])
        self.assertGreater(selection['uniqueMemberCount'],5)
        self.assertEqual(len(selection['slots']),5)
        self.assertEqual(sum(s['anchorControlAdmitted'] for s in selection['slots']),1)
        self.assertEqual(sum(s['anchorControlDisposition']=='mappedAnchorCollision' for s in selection['slots']),4)
        self.assertEqual([s['rank'] for s in signals if s['signalDate']==self.target_dates[12] and s['strategyId']==self.runner.MEMBER_CONTROL_ID],list(range(1,6)))
        terminal=lines(self.root/'collected/episode-terminals.jsonl.gz')
        self.assertEqual(len(terminal),performance['terminalEpisodes'])
        self.assertTrue(any(m['state']=='censoredAtStudyCutoff' for e in terminal for m in e['members']))
        self.assertTrue(any(m['state']=='expired' for e in terminal for m in e['members']))

    def test_nonempty_three_policy_events_dispositions_and_verify_only(self):
        self.assertEqual(self.run_complete(),0)
        result=self.result();self.assertEqual([g['strategyId'] for g in result['groups']],list(self.runner.GROUPS))
        self.assertGreater(len(result['events']),0)
        self.assertGreater(len(result['pathRiskEvents']),0)
        slots=lines(self.attempt()/'slot-outcomes.jsonl.gz')
        self.assertEqual(len(slots),result['slotAccounting']['globalTop5Slots'])
        self.assertGreater(result['slotAccounting']['mappedAnchorCollisions'],0)
        for event in result['events']:
            if event['entry']['status']=='filled':
                self.assertEqual(event['entry']['date'],self.data['calendar'][self.data['calendar'].index(event['signalDate'])+1])
        original={p.relative_to(self.output).as_posix():self.runner.sha(p) for p in self.output.rglob('*') if p.is_file()}
        write(self.output/'independent-result-verification.json',{'testOnly':True})
        with patch.object(self.runner,'load_dataset',side_effect=AssertionError('must not execute again')),\
             patch.object(self.runner,'collect_signals',side_effect=AssertionError('must not collect again')),\
             patch.object(self.runner,'evaluate_signals',side_effect=AssertionError('must not label again')):
            self.assertEqual(self.execute(),0)
        self.assertTrue(all(self.runner.sha(self.output/p)==value for p,value in original.items()))

    def test_source_stream_missing_duplicate_disordered_and_extra_eof(self):
        path=self.parent_fixture.attempt()/'predicates.jsonl.gz';original=lines(path)
        variants=[original[:-1],[original[0],original[0],*original[2:]],
                  [original[1],original[0],*original[2:]],[*original,original[-1]]]
        for rows in variants:
            with self.subTest(length=len(rows)):
                write_lines(path,rows);self.rebind_parent(resign=True)
                with self.registered():
                    with self.assertRaisesRegex(ValueError,'predicate|extra rows'):
                        list(self.runner.iter_source_days(self.runner.load_binding()))

    def test_group_missing_or_reordered_rejected_and_all_opportunities_retained(self):
        path=self.parent_fixture.attempt()/'peer-groups.jsonl.gz';original=lines(path)
        for rows in (original[:-1],[original[1],original[0],*original[2:]], [*original,original[-1]]):
            with self.subTest(length=len(rows)):
                write_lines(path,rows);self.rebind_parent(resign=True)
                with self.registered():
                    with self.assertRaisesRegex(ValueError,'peer group|extra rows'):
                        list(self.runner.iter_source_days(self.runner.load_binding()))

    def test_first_target_formation_uses_warmup_F_without_early_skip(self):
        # Deliberately construct a source-contract opportunity at the first target
        # close. It is not a claim that the old fixture selected this opportunity.
        with self.registered():binding=self.runner.load_binding()
        selection_path=self.parent_fixture.attempt()/'selection.jsonl.gz'
        group_path=self.parent_fixture.attempt()/'peer-groups.jsonl.gz'
        selections=lines(selection_path);groups=lines(group_path)
        day=self.target_dates[0];formation=self.data['calendar'][self.data['calendar'].index(day)-10]
        group=deepcopy(groups[0]);group.update(date=day,formationDate=formation)
        group['group']['formationDate']=formation;group['groupSha256']=self.runner.digest(group['group'])
        slot=deepcopy(selections[10]['slots'][0]);slot.update(date=day,formationDate=formation,groupSha256=group['groupSha256'])
        selections[0]['slots']=[slot]
        selections[0]['selection']['baselineSignals']=[{'symbol':slot['symbol'],'rank':1}]
        write_lines(selection_path,selections);write_lines(group_path,[group,*groups])
        binding['parentAudit']['slotAccounting']['parentTop5']+=1
        path=self.root/'warmup-first';path.mkdir()
        with self.registered():self.runner.collect_signals(self.data,binding,path)
        initial=lines(path/'formations.jsonl.gz')[0]['record']
        self.assertEqual((initial['date'],initial['index'],initial['peerFormationDate']),(day,0,formation))
        self.assertEqual(initial['lockThroughIndex'],5)

    def test_source_proof_inventory_and_archive_drift_fail_closed(self):
        proof_path=self.parent_output/'independent-result-verification.json'
        proof=self.runner.read_json(proof_path);proof['passed']=False;write(proof_path,proof)
        self.rebind_parent()
        with self.registered(),self.assertRaisesRegex(ValueError,'independent proof'):self.runner.load_binding()
        proof['passed']=True;write(proof_path,proof);self.rebind_parent()
        self.freeze()
        with (self.output/'execution-sources.zip').open('ab') as stream:stream.write(b'changed')
        with self.assertRaisesRegex(ValueError,'archive'):self.execute()
        self.assertFalse((self.output/'attempts').exists())

    def test_raw_physical_drift_and_output_overlap_rejected(self):
        with self.registered(),self.assertRaisesRegex(ValueError,'overlaps|contains'):
            self.runner.freeze(self.args(output=self.parent_output/'nested'))
        self.freeze()
        raw=next((self.source/'vipdoc/sh/lday').glob('sh600*.day'))
        raw.write_bytes(raw.read_bytes()+b'future-drift')
        with self.assertRaises(ValueError):self.execute()
        self.assertFalse((self.output/'attempts').exists())

    def test_eof_failure_preserved_before_events_and_no_retry(self):
        self.freeze()
        iterator=self.runner.iter_source_days
        def bad(binding):
            yield from iterator(binding)
            raise ValueError('synthetic final EOF failure')
        with patch.object(self.runner,'iter_source_days',bad),\
             patch.object(self.runner,'evaluate_signals',side_effect=AssertionError('must wait for complete source EOF')):
            with self.assertRaisesRegex(ValueError,'final EOF'):self.execute()
        self.assertTrue((self.output/'failure.json').is_file())
        self.assertTrue((self.attempt()/'failure.json').is_file())
        self.assertFalse((self.attempt()/'completion.json').exists())
        with patch.object(self.runner,'collect_signals',side_effect=AssertionError('no retry')):
            with self.assertRaisesRegex(ValueError,'restart forbidden'):self.execute()

    def test_group_active_is_independent_unfilled_not_held_and_exit_day_releases(self):
        # Event executor is unchanged; inject a tiny deterministic outcome helper
        # here solely to exercise ordering/active dispatch, not return arithmetic.
        day=self.target_dates;sid=self.runner.CANDIDATE_ID;symbol=self.symbols[6]
        signals=[dict(strategyId=g,symbol=symbol,signalDate=day[i],rank=1,score=1,rawScore=1)
                 for i in (0,1,2,3) for g in self.runner.GROUPS]
        calls=[]
        class EventStudy:
            def __init__(self,*args,**kwargs):pass
            def evaluate(self,signal):
                calls.append((signal['strategyId'],signal['signalDate']))
                index=day.index(signal['signalDate'])
                return {'eventId':str(len(calls)),'strategyId':signal['strategyId'],'symbol':symbol,'signalDate':signal['signalDate'],
                    'entry':{'status':'unfilled' if index==0 else 'filled'},
                    'outcomes':{'nextOpenNet30':{'hitDate':day[3] if index==1 else None}}}
        with patch('service.signal_target_study.SignalTargetStudy',EventStudy),\
             patch('service.signal_target_study.summarize_events',return_value={'horizons':[]}):
            result=self.runner.runtime.evaluate_signals(self.data,self.doc['config'],signals)
        self.assertEqual(len(result['events']),9)
        self.assertEqual(len(result['overlapSuppressed']),3)
        self.assertEqual({e['signalDate'] for e in result['overlapSuppressed']},{day[2]})
        self.assertEqual(sum(d==day[3] for _,d in calls),3)


if __name__=='__main__':unittest.main()
