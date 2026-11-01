"""Independent prefix boundaries and rehashed temporary-artifact attacks only."""
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

ROOT = Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location('succession_independent_test', ROOT / 'scripts/verify-signal-leader-local-succession-result.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def days(n=18):
    return [(date(2022, 12, 27) + timedelta(days=i)).isoformat() for i in range(n)]


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')


def write_lines(path, rows):
    with gzip.open(path, 'wt', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')


class LocalSuccessionVerifierTests(unittest.TestCase):
    def setUp(self):
        self.v = module()

    def outcome(self, symbol, day, q='true', breakout=False, pair=(.92, .92), rank_known=True, kind=None):
        row = {'version': self.v.PARENT_VERSION, 'candidateId': self.v.PARENT_ID,
            'symbol': symbol, 'date': day, 'kind': kind or ('signaled' if breakout else 'notTriggered'),
            'reason': 'synthetic', 'predicates': {key: {'passed': True} for key in self.v.GATE_FIELDS}}
        for key, value in zip(self.v.RANK_FIELDS, pair):
            row['predicates'][key].update(value=value, reason=None)
        if q == 'false':
            row['predicates']['signedPathEfficiency20']['passed'] = False
        elif q == 'unknown':
            row['kind'] = 'unevaluable'
        if not rank_known:
            row['predicates'][self.v.RANK_FIELDS[0]]['reason'] = 'missingRankEvidence'
        return row

    def parent(self, symbol, day, **kwargs):
        return self.v.project_parent(self.outcome(symbol, day, **kwargs))

    def population(self, day, **changes):
        rows = {s: self.parent(s, day, pair=(.97, .97) if s == 'a' else (.92, .92), breakout=s == 'a')
                for s in ['a', *[f'm{i:02}' for i in range(20)]]}
        rows.update({s: self.parent(s, day, **values) for s, values in changes.items()})
        return rows

    def formation(self, day, rank=1, anchor='a', kind='formed'):
        members = [f'm{i:02}' for i in range(20)]
        group = {'version': 'raw-leader-peer-expansion-v1', 'kind': kind, 'reason': 'synthetic',
            'anchor': anchor, 'formationDate': '2022-12-13', 'members': members,
            'requiredCount': 20, 'selectedCount': 20, 'correlations': {s: .7 for s in members}}
        return {'date': day, 'symbol': anchor, 'rank': rank, 'formationDate': group['formationDate'],
                'groupSha256': self.v.digest(group), 'group': group}

    def test_independent_Q_and_rank_known_require_typed_original_evidence(self):
        source = self.outcome('a', days()[0], q='unknown')
        result = self.v.project_parent(source)
        self.assertEqual(result['eligibleParentQ'], 'unknown')
        self.assertTrue(result['rankKnown'])
        for value in (1, 0, None, 'true'):
            changed = deepcopy(source)
            changed['predicates'][self.v.RANK_FIELDS[0]]['passed'] = value
            row = self.v.project_parent(changed)
            self.assertFalse(row['rankKnown'])
            self.assertEqual(row['rankEvidence'][self.v.RANK_FIELDS[0]]['value'], .92)
            self.assertIsNone(row['rankValues'][self.v.RANK_FIELDS[0]])
        for value in (True, -1, 1.1, '0.95', None, 10**400):
            changed = deepcopy(source)
            changed['predicates'][self.v.RANK_FIELDS[0]]['value'] = value
            self.assertFalse(self.v.project_parent(changed)['rankKnown'])
        changed = deepcopy(source)
        del changed['predicates'][self.v.RANK_FIELDS[0]]['reason']
        self.assertFalse(self.v.project_parent(changed)['rankKnown'])
        changed = deepcopy(source)
        changed['predicates'][self.v.RANK_FIELDS[0]]['passed'] = False
        self.assertTrue(self.v.project_parent(changed)['rankKnown'])  # No new threshold inference.
        self.assertEqual(source, self.outcome('a', days()[0], q='unknown'))

    def test_exact_lex_order_uses_secondary_only_at_exact_primary_tie(self):
        anchor = self.parent('a', days()[0], pair=(.95, .96))
        equal = self.parent('z', days()[0], pair=(.95, .96))
        self.assertEqual(self.v.rank_comparison(equal, anchor), 'equal')
        self.assertEqual(self.v.rank_comparison(self.parent('z', days()[0], pair=(.95, .97)), anchor), 'above')
        self.assertEqual(self.v.rank_comparison(self.parent('z', days()[0], pair=(math.nextafter(.95, 1), .90)), anchor), 'above')
        self.assertEqual(self.v.rank_comparison(self.parent('z', days()[0], rank_known=False), anchor), 'unknown')

    def test_conservative_pair_unknown_priority_and_no_permanent_anchor_cancel(self):
        for first, second, expected in (('false','false','true'), ('false','true','false'),
                ('true','unknown','unknown'), ('unknown','true','unknown'), ('unknown','false','unknown')):
            pair = [{'anchorParent': {'eligibleParentQ': q}} for q in (first, second)]
            self.assertEqual(self.v.pair_gate(pair), expected)
        study = self.v.ReferenceEpisodes(days(5))
        study.step(days()[0], 0, self.population(days()[0]), [self.formation(days()[0])])
        study.step(days()[1], 1, self.population(days()[1], a={'q':'unknown','pair':(.97,.97)}), [])
        for i in (2,3):
            result = study.step(days()[i], i, self.population(days()[i],
                a={'q':'false','pair':(.93,.93)}, m00={'pair':(.96,.96),'breakout':True}), [])
        self.assertEqual(result['rawConfirmations'][0]['anchorPairGate'], 'true')
        self.assertEqual([r['offset'] for r in result['rawConfirmations'][0]['pairEvidence']], [2,3])
        self.assertEqual(result['selection']['policyCounts'][self.v.CANDIDATE_ID], 1)

    def test_member_unknown_false_and_rank_unknown_terminate_in_registered_priority(self):
        study = self.v.ReferenceEpisodes(days(4))
        study.step(days()[0], 0, self.population(days()[0]), [self.formation(days()[0])])
        study.step(days()[1], 1, self.population(days()[1], a={'rank_known':False},
            m00={'q':'unknown','rank_known':False}, m01={'q':'false','rank_known':False}), [])
        state = study.snapshot(study.entries[0])
        self.assertEqual([m['state'] for m in state['members'][:3]], ['memberUnknown','memberLostStrength','rankUnknown'])
        self.assertEqual(state['members'][0]['terminalFlags'], ['memberQUnknown','memberRankUnknown','anchorRankUnknown'])
        for i in (2,3):
            result = study.step(days()[i], i, self.population(days()[i],
                a={'q':'false','pair':(.91,.91)}, **{f'm{k:02}':{'pair':(.96,.96),'breakout':True} for k in range(3)}), [])
            self.assertEqual(result['rawConfirmations'], [])

    def test_equal_resets_streak_first_pair_without_breakout_is_consumed(self):
        study = self.v.ReferenceEpisodes(days(6))
        study.step(days()[0], 0, self.population(days()[0]), [self.formation(days()[0])])
        # Above, equal, above, above: the first pair completes on offset4.
        for i, pair in enumerate(((.98,.98), (.97,.97), (.98,.98), (.98,.98)), 1):
            result = study.step(days()[i], i, self.population(days()[i], m00={'pair':pair}), [])
            self.assertEqual(result['rawConfirmations'], [])
        state = study.snapshot(study.entries[0])['members'][0]
        self.assertEqual(state['state'], 'confirmedWithoutBreakout')
        self.assertEqual([r['offset'] for r in state['firstPair']], [3,4])
        saved = deepcopy(result)
        last = study.step(days()[5], 5, self.population(days()[5], m00={'pair':(.99,.99),'breakout':True}), [])
        self.assertEqual(last['rawConfirmations'], [])
        self.assertEqual(result, saved)
        self.assertEqual(study.snapshot(study.entries[0])['members'][0]['terminalIndex'], 4)

    def test_initial_priority_six_session_lock_and_cutoff_preserve_pending_streak(self):
        study = self.v.ReferenceEpisodes(days(7))
        initial = self.population(days()[0], m00={'q':'unknown','rank_known':False}, m01={'q':'false'},
            m02={'rank_known':False}, m03={'pair':(.97,.97)})
        study.step(days()[0], 0, initial, [self.formation(days()[0])])
        self.assertEqual([m['state'] for m in study.snapshot(study.entries[0])['members'][:5]],
            ['initialMemberUnknown','notInitiallyStrong','initialRankUnknown','notInitiallyBelow','observing'])
        for i in range(1,7):
            result = study.step(days()[i], i, self.population(days()[i]), [self.formation(days()[i])])
            self.assertEqual(result['formations'][0]['kind'], 'activated' if i == 6 else 'formationSuppressedByLock')
        terminal, rows = study.finish()
        self.assertEqual(terminal[0]['members'][4]['state'], 'expired')
        self.assertEqual(terminal[1]['members'][4]['state'], 'censoredAtStudyCutoff')
        self.assertEqual(len(rows), 20)
        partial = self.v.ReferenceEpisodes(days(2))
        partial.step(days()[0], 0, self.population(days()[0]), [self.formation(days()[0])])
        partial.step(days()[1], 1, self.population(days()[1], m00={'pair':(.98,.98)}), [])
        self.assertEqual(partial.finish()[0][0]['members'][0]['leadingStreak'], 1)
        invalid = self.v.ReferenceEpisodes(days(2))
        invalid.step(days()[0], 0, initial, [self.formation(days()[0], kind='unevaluable')])
        self.assertEqual(invalid.snapshot(invalid.entries[0])['members'][0]['state'], 'formationUnevaluable')
        self.assertEqual(invalid.step(days()[1],1,self.population(days()[1]),[self.formation(days()[1])])['formations'][0]['kind'], 'formationSuppressedByLock')

    def test_fifth_day_pair_precedes_expiry_and_pair_unknown_keeps_baseline(self):
        study = self.v.ReferenceEpisodes(days(6))
        study.step(days()[0],0,self.population(days()[0]),[self.formation(days()[0])])
        for i in range(1,6):
            change = {'m00':{'pair':(.98,.98),'breakout':True}} if i >= 4 else {}
            if i == 4:
                change['a'] = {'q':'unknown','pair':(.97,.97)}
            result = study.step(days()[i],i,self.population(days()[i], **change),[])
        raw = result['rawConfirmations'][0]
        self.assertEqual(raw['offset'],5)
        self.assertEqual(raw['anchorPairGate'],'unknown')
        self.assertEqual(raw['anchorPairUnknownDates'],[days()[4]])
        self.assertEqual(raw['anchorPairFailedDates'],[days()[5]])
        self.assertEqual(result['selection']['policyCounts'],{self.v.CANDIDATE_ID:0,self.v.MEMBER_CONTROL_ID:1,self.v.ANCHOR_CONTROL_ID:0})
        self.assertEqual(study.snapshot(study.entries[0])['members'][1]['state'],'expired')

    def test_all_raw_ownership_before_mask_top5_no_backfill_and_anchor_dedup_crossyear(self):
        calendar = ['2022-12-29','2022-12-30','2023-01-03']
        study = self.v.ReferenceEpisodes(calendar)
        initial = self.population(calendar[0])
        initial['b'] = self.parent('b',calendar[0],pair=(.97,.97),breakout=True)
        study.step(calendar[0],0,initial,[self.formation(calendar[0]),self.formation(calendar[0],rank=2,anchor='b')])
        for i in (1,2):
            changed = self.population(calendar[i], **{f'm{k:02}':{'pair':(.98,.98),'breakout':True} for k in range(6)})
            changed['b'] = self.parent('b',calendar[i],q='false',pair=(.96,.96))
            result = study.step(calendar[i],i,changed,[])
        selected = result['selection']
        self.assertEqual(len(result['rawConfirmations']),12)
        self.assertEqual(selected['uniqueMemberCount'],6)
        self.assertEqual(len(selected['slots']),5)
        self.assertTrue(all(row['anchorSymbol']=='a' for row in selected['slots']))
        self.assertEqual(selected['policyCounts'],{self.v.CANDIDATE_ID:0,self.v.MEMBER_CONTROL_ID:5,self.v.ANCHOR_CONTROL_ID:0})
        self.assertTrue(all(len(row['discardedRawConfirmationIds'])==1 for row in selected['ownership']))
        # A separate descriptive anchor policy maps admitted slots once, no replacement.
        _, other = self.v.reference_selection(calendar[2], [r for r in result['rawConfirmations'] if r['anchorSymbol']=='b'])
        self.assertEqual(other['policyCounts'],{self.v.CANDIDATE_ID:5,self.v.MEMBER_CONTROL_ID:5,self.v.ANCHOR_CONTROL_ID:1})
        self.assertEqual(other['slots'][1]['anchorControlDisposition'],'mappedAnchorCollision')
        self.assertEqual(other['slots'][1]['anchorControlRetainedRank'],1)

    @contextmanager
    def completed_fixture(self):
        import test_signal_leader_local_succession_runner as fixtures
        fixture = fixtures.LocalSuccessionRunnerTests()
        fixture.setUp()
        try:
            self.assertEqual(fixture.run_complete(), 0)
            with patch.object(self.v, 'REGISTRATION', fixture.registration), patch.object(self.v, 'REGISTRATION_SHA256', fixture.registration_hash):
                yield fixture
        finally:
            fixture.doCleanups()

    def verify(self, fixture):
        with redirect_stdout(io.StringIO()):
            return self.v.verify(fixture.output, root=fixture.root)

    def resign(self, fixture):
        receipt = self.v.read(fixture.attempt() / 'completion.json')
        for item in receipt['files'].values():
            item['sha256'] = self.v.sha(fixture.output / item['path'])
        write(fixture.attempt() / 'completion.json', receipt)
        path = fixture.output / 'events.jsonl'
        journal = [self.v.parse(line) for line in path.read_text(encoding='utf-8').splitlines()]
        journal[-1]['receipt'] = receipt
        path.write_text(''.join(json.dumps(row) + '\n' for row in journal), encoding='utf-8')

    def test_completed_nonempty_three_policy_full_replay_and_saved_report_verify_only(self):
        with self.completed_fixture() as f:
            report = self.verify(f)
            self.assertTrue(report['passed'], report)
            self.assertEqual(set(report['selectedSignals']), set(self.v.GROUPS))
            self.assertGreater(report['slotAccounting']['globalTop5Slots'], 0)
            self.assertEqual(len(report['eventAuditAdaptation']['effectiveAstSha256']), 64)
            self.assertFalse(report['inheritedParentPredicatesRecomputed'])
            self.v.save_report(f.output, report)
            with patch.object(f.runner, 'load_dataset', side_effect=AssertionError('Success must not execute again')):
                self.assertEqual(f.execute(), 0)

    def test_rehashed_ownership_slot_state_and_terminal_tampering_rejected(self):
        with self.completed_fixture() as f:
            for name, change, expected_check in (
                ('selection.jsonl.gz', lambda r: r['selection']['ownership'][0].update(ownerEpisodeId='forged'), 'independent-ownership-three-policies'),
                ('slot-outcomes.jsonl.gz', lambda r: r['memberBaselineDisposition'].update(status='filteredAnchorUnknown'), 'independent-three-policy-slot-dispositions'),
                ('episode-terminals.jsonl.gz', lambda r: r['members'][0].update(leadingStreak=99), 'independent-all-episode-terminals')):
                with self.subTest(name=name):
                    path = f.attempt() / name
                    original = list(self.v.records(path))
                    rows = deepcopy(original)
                    row = next((r for r in rows if name != 'selection.jsonl.gz' or r['selection']['ownership']))
                    change(row)
                    write_lines(path, rows)
                    self.resign(f)
                    report = self.verify(f)
                    self.assertFalse(report['passed'])
                    self.assertIn(expected_check, {r['check'] for r in report['issues']}, report)
                    write_lines(path, original)
                    self.resign(f)

    def test_rehashed_missing_duplicate_extra_rows_and_group_horizon_tampering_rejected(self):
        with self.completed_fixture() as f:
            path = f.attempt() / 'coverage.jsonl.gz'
            original = list(self.v.records(path))
            for rows in (original[:-1], [original[0], original[0], *original[2:]], [*original, original[-1]]):
                with self.subTest(rows=len(rows)):
                    write_lines(path, rows)
                    self.resign(f)
                    report = self.verify(f)
                    self.assertFalse(report['passed'])
                    self.assertTrue(any(r['check'] in ('independent-complete-daily-coverage', 'output-exact-EOF:coverage') for r in report['issues']), report)
            write_lines(path, original)
            result = f.result()
            result['groups'][2]['horizons'][0]['proxies']['nextOpenNet30']['hits'] += 1
            with gzip.open(f.attempt() / 'result.json.gz', 'wt', encoding='utf-8') as stream:
                json.dump(result, stream)
            summary = self.v.read(f.attempt() / 'summary.json')
            summary['groups'] = deepcopy(result['groups'])
            write(f.attempt() / 'summary.json', summary)
            self.resign(f)
            report = self.verify(f)
            self.assertFalse(report['passed'])
            self.assertIn('target-fraction', {r['check'] for r in report['issues']}, report)


if __name__ == '__main__':
    unittest.main()
