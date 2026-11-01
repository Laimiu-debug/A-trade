"""Independent formula and completed temporary-fixture audits only."""
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import date, timedelta
import gzip
import importlib.util
import io
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

import test_signal_leader_peer_expansion_runner as fixtures

ROOT=Path(__file__).resolve().parents[1]


def module():
    spec=importlib.util.spec_from_file_location('independent_peer_test',ROOT/'scripts/verify-signal-leader-peer-expansion-result.py')
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


class LeaderPeerExpansionVerifierTests(unittest.TestCase):
    def setUp(self): self.verifier=module()

    @contextmanager
    def completed_fixture(self):
        f=fixtures.LeaderPeerExpansionRunnerTests();f.setUp()
        try:
            (f.root/'synthetic-contract.txt').write_bytes(b'Eligibility fixture only; no actual market audit.')
            self.assertEqual(f.run_complete(),0)
            with patch.object(self.verifier,'REGISTRATION',f.registration),patch.object(self.verifier,'REGISTRATION_SHA256',f.registration_hash):
                yield f
        finally: f.doCleanups()

    def verify(self,f):
        with redirect_stdout(io.StringIO()): return self.verifier.verify(f.output,root=f.root)

    def resign(self,f):
        """Intentionally forge the test-only receipt to reach semantic checks."""
        receipt=f.runner.read_json(f.attempt()/'completion.json')
        for item in receipt['files'].values(): item['sha256']=f.runner.sha(f.output/item['path'])
        fixtures.write(f.attempt()/'completion.json',receipt)
        path=f.output/'events.jsonl';rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        rows[-1]['receipt']=receipt
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')

    def test_independent_correlation_fixed_member_window_and_unknown(self):
        v=self.verifier
        values=[math.sin(i*.4)*.01 for i in range(60)]
        raw,projected=v.pearson(values,values)
        self.assertAlmostEqual(raw,1);self.assertEqual(projected,min(1.,raw))
        self.assertIsNone(v.pearson([0.]*60,values))
        days=[(date(2022,1,1)+timedelta(days=i)).isoformat() for i in range(71)]
        def bars(prices,dates):
            return [dict(date=d,open=p,high=p*1.001,low=p*.999,close=p,volume=1000,priceBasisId='raw-unadjusted') for p,d in zip(prices,dates)]
        prices=[10.]
        for value in values: prices.append(prices[-1]*math.exp(value))
        symbols=['anchor']+[f'p{i:02}' for i in range(21)]
        histories={s:bars(prices,days[:61]) for s in symbols}
        eligibility={s:dict(eligible=True,reason='eligible',unknown=['historicalSt']) for s in symbols}
        group=v.reference_group('anchor',days[60],days[:61],histories,bars([10.]*61,days[:61]),eligibility)
        self.assertEqual(group['members'],symbols[1:21])
        self.assertEqual(group['counts']['qualifiedPeers'],21)
        responses={s:bars([10-i*.01 for i in range(6)]+[10.1+i*.1 for i in range(5)],days[60:]) for s in group['members']}
        benchmark=bars([10.]*11,days[60:])
        expanded=v.reference_expansion(group,days[60:],responses,benchmark)
        self.assertEqual((expanded['beforeCount'],expanded['nowCount'],expanded['kind']),(0,20,'expanded'))
        responses[group['members'][0]][3]=None
        unknown=v.reference_expansion(group,days[60:],responses,benchmark)
        self.assertEqual(unknown['members'],group['members'])
        self.assertEqual(unknown['kind'],'unevaluable');self.assertIsNone(unknown['nowCount'])

    def test_nonempty_nested_baseline_and_saved_audit_verify_only(self):
        with self.completed_fixture() as f:
            report=self.verify(f)
            self.assertTrue(report['passed'],report)
            self.assertEqual(report['evaluatedSecuritySessions'],26*24)
            self.assertEqual(report['temporalCounts']['formationUnavailable'],26*10)
            self.assertEqual(report['slotAccounting']['parentTop5'],70)
            self.assertGreater(report['slotAccounting']['candidateAdmitted'],0)
            self.assertIn('filteredNotExpanded',report['slotAccounting']['candidateDispositions'])
            self.verifier.save_report(f.output,report)
            with patch.object(f.runner,'load_dataset',side_effect=AssertionError('no second execution')):
                self.assertEqual(f.execute(),0)

    def test_rehashed_membership_and_nested_slot_disposition_rejected(self):
        with self.completed_fixture() as f:
            groups=fixtures.lines(f.attempt()/'peer-groups.jsonl.gz');original=deepcopy(groups)
            groups[0]['group']['members'].reverse()
            groups[0]['groupSha256']=f.runner.digest(groups[0]['group'])
            fixtures.write_lines(f.attempt()/'peer-groups.jsonl.gz',groups);self.resign(f)
            report=self.verify(f)
            self.assertFalse(report['passed'])
            self.assertIn('independent-frozen-peer-group',{i['check'] for i in report['issues']})
            fixtures.write_lines(f.attempt()/'peer-groups.jsonl.gz',original)
            slots=fixtures.lines(f.attempt()/'slot-outcomes.jsonl.gz')
            slots[0]['baselineDisposition']['status']='filteredUnknown'
            fixtures.write_lines(f.attempt()/'slot-outcomes.jsonl.gz',slots);self.resign(f)
            report=self.verify(f)
            self.assertFalse(report['passed'])
            self.assertIn('independent-original-slot-dispositions',{i['check'] for i in report['issues']})

    def test_missing_duplicate_and_trailing_full_population_rows_rejected(self):
        with self.completed_fixture() as f:
            original=fixtures.lines(f.attempt()/'predicates.jsonl.gz')
            for rows in (original[:-1],[original[0],original[0],*original[2:]],[*original,original[-1]]):
                with self.subTest(length=len(rows)):
                    fixtures.write_lines(f.attempt()/'predicates.jsonl.gz',rows);self.resign(f)
                    report=self.verify(f)
                    self.assertFalse(report['passed'],report)
                    self.assertTrue(any(i['check'] in ('independent-all-population-predicate-row','independent-parent-identity-kind','output-exact-EOF:predicates') for i in report['issues']),report)

    def test_rehashed_horizon_numerator_rejected(self):
        with self.completed_fixture() as f:
            result=f.result()
            result['groups'][0]['horizons'][0]['proxies']['nextOpenNet30']['hits']+=1
            with gzip.open(f.attempt()/'result.json.gz','wt',encoding='utf-8') as stream:json.dump(result,stream)
            summary=f.runner.read_json(f.attempt()/'summary.json');summary['groups']=deepcopy(result['groups'])
            fixtures.write(f.attempt()/'summary.json',summary);self.resign(f)
            report=self.verify(f)
            self.assertFalse(report['passed'])
            self.assertIn('target-fraction',{i['check'] for i in report['issues']})


if __name__=='__main__':unittest.main()
