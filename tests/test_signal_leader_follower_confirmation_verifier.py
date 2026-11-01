"""Independent oracle boundaries and temporary completed-artifact audits only."""
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import date, timedelta
import gzip
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location('follower_independent_test', ROOT / 'scripts/verify-signal-leader-follower-confirmation-result.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def days(n=18):
    return [(date(2022, 12, 20) + timedelta(days=i)).isoformat() for i in range(n)]


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')


def write_lines(path, rows):
    with gzip.open(path, 'wt', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')


class LeaderFollowerConfirmationVerifierTests(unittest.TestCase):
    def setUp(self):
        self.v = module()

    def parent(self, symbol, day, q='false', breakout=False, kind=None):
        observed = {'version': self.v.PARENT_VERSION, 'candidateId': self.v.PARENT_ID,
            'symbol': symbol, 'date': day, 'kind': kind or ('signaled' if breakout else 'notTriggered'),
            'reason': 'synthetic', 'predicates': {key: {'passed': True} for key in self.v.GATE_FIELDS}}
        if q == 'false':
            observed['predicates'][self.v.GATE_FIELDS[0]]['passed'] = False
        elif q == 'unknown':
            observed['kind'] = 'unevaluable'
        return self.v.project_parent(observed)

    def population(self, day, **changes):
        rows = {s: self.parent(s, day, q='true' if s == 'a' else 'false', breakout=s == 'a')
                for s in ['a', *[f'm{i:02}' for i in range(20)]]}
        rows.update({s: self.parent(s, day, **values) for s, values in changes.items()})
        return rows

    def formation(self, day, rank=1, anchor='a', kind='formed'):
        members = [f'm{i:02}' for i in range(20)]
        group = {'version': 'raw-leader-peer-expansion-v1', 'kind': kind, 'reason': 'synthetic',
            'anchor': anchor, 'formationDate': '2021-12-06', 'members': members,
            'requiredCount': 20, 'selectedCount': 20, 'correlations': {s: .7 for s in members}}
        return {'date': day, 'symbol': anchor, 'rank': rank, 'formationDate': group['formationDate'],
                'groupSha256': self.v.digest(group), 'group': group}

    def test_projection_three_values_requires_known_exact_bools_and_eligible_kind(self):
        for kind in ('ineligible', 'unevaluable', None):
            row = self.parent('m00', days()[0], q='true', kind=kind or 'unevaluable')
            self.assertEqual(row['eligibleParentQ'], 'unknown')
        self.assertEqual(self.parent('m00', days()[0], q='false')['eligibleParentQ'], 'false')
        self.assertEqual(self.parent('m00', days()[0], q='true')['eligibleParentQ'], 'true')
        source = {'version': self.v.PARENT_VERSION, 'candidateId': self.v.PARENT_ID,
            'symbol': 'm00', 'date': days()[0], 'kind': 'notTriggered', 'reason': 'synthetic',
            'predicates': {key: {'passed': True} for key in self.v.GATE_FIELDS}}
        for value in (1, 0, None, 'true'):
            source['predicates'][self.v.GATE_FIELDS[0]]['passed'] = value
            self.assertEqual(self.v.project_parent(source)['eligibleParentQ'], 'unknown')

    def test_first_terminal_anchor_and_member_shadows_are_causal_and_irreversible(self):
        calendar = days(7)
        study = self.v.ReferenceEpisodes(calendar)
        study.step(calendar[0], 0, self.population(calendar[0]), [self.formation(calendar[0])])
        first = study.step(calendar[1], 1, self.population(calendar[1],
            a={'q': 'unknown'}, m00={'q': 'unknown'}, m01={'q': 'true'}), [])
        unchanged = deepcopy(first)
        second = study.step(calendar[2], 2, self.population(calendar[2],
            a={'q': 'false'}, m00={'q': 'true', 'breakout': True},
            m01={'q': 'true', 'breakout': True}, m02={'q': 'true', 'breakout': True}), [])
        self.assertEqual([r['memberSymbol'] for r in second['rawConfirmations']], ['m02'])
        self.assertEqual(second['rawConfirmations'][0]['anchorGate'], 'unknown')
        self.assertEqual(second['selection']['policyCounts'][self.v.CANDIDATE_ID], 0)
        self.assertEqual(second['selection']['policyCounts'][self.v.MEMBER_CONTROL_ID], 1)
        self.assertEqual(first, unchanged)
        snapshot = study.snapshot(study.entries[0])
        self.assertEqual(snapshot['anchorGateTerminal']['date'], calendar[1])
        self.assertEqual([m['state'] for m in snapshot['members'][:3]], ['memberUnknown', 'confirmedWithoutBreakout', 'confirmed'])

    def test_full_lock_through_fifth_day_assessment_then_new_episode_and_cutoff(self):
        calendar = days(7)
        study = self.v.ReferenceEpisodes(calendar)
        for index, day in enumerate(calendar):
            overrides = {'m00': {'q': 'true', 'breakout': True}} if index == 5 else {}
            result = study.step(day, index, self.population(day, **overrides), [self.formation(day)])
            self.assertEqual(result['formations'][0]['kind'], 'activated' if index in (0, 6) else 'formationSuppressedByLock')
            if index == 5:
                self.assertEqual(len(result['rawConfirmations']), 1)
                self.assertEqual(result['rawConfirmations'][0]['offset'], 5)
        episodes, transitions = study.finish()
        self.assertEqual(episodes[0]['members'][0]['state'], 'confirmed')
        self.assertEqual(episodes[0]['members'][1]['state'], 'expired')
        self.assertEqual(episodes[1]['members'][0]['state'], 'censoredAtStudyCutoff')
        self.assertEqual(len(transitions), 20)
        self.assertTrue(all(r['offset'] == 0 for r in transitions))

    def test_unevaluable_formation_still_owns_six_session_lock(self):
        study = self.v.ReferenceEpisodes(days(3))
        first = study.step(days()[0], 0, self.population(days()[0]), [self.formation(days()[0], kind='unevaluable')])
        self.assertEqual(first['formations'][0]['kind'], 'unevaluableFormation')
        later = study.step(days()[1], 1, self.population(days()[1]), [self.formation(days()[1])])
        self.assertEqual(later['formations'][0]['kind'], 'formationSuppressedByLock')
        self.assertEqual(later['rawConfirmations'], [])
        self.assertEqual(study.finish()[0][0]['members'][0]['state'], 'formationUnevaluable')

    def test_global_ownership_precedes_anchor_mask_and_top5_without_backfill(self):
        study = self.v.ReferenceEpisodes(days(2))
        population = self.population(days()[0])
        population['b'] = self.parent('b', days()[0], q='true', breakout=True)
        study.step(days()[0], 0, population, [self.formation(days()[0]), self.formation(days()[0], rank=2, anchor='b')])
        changed = self.population(days()[1], **{f'm{i:02}': {'q': 'true', 'breakout': True} for i in range(6)})
        changed['a'] = self.parent('a', days()[1], q='false')
        changed['b'] = self.parent('b', days()[1], q='true', breakout=True)
        result = study.step(days()[1], 1, changed, [])
        selection = result['selection']
        self.assertEqual(len(result['rawConfirmations']), 12)
        self.assertEqual(selection['uniqueMemberCount'], 6)
        self.assertEqual(len(selection['slots']), 5)
        self.assertTrue(all(r['anchorSymbol'] == 'a' for r in selection['slots']))
        self.assertTrue(all(len(r['discardedRawConfirmationIds']) == 1 for r in selection['ownership']))
        self.assertEqual(selection['policyCounts'], {self.v.CANDIDATE_ID: 0, self.v.MEMBER_CONTROL_ID: 5, self.v.ANCHOR_CONTROL_ID: 0})

    def test_anchor_object_control_deduplicates_by_original_slot_and_crossyear_no_reset(self):
        calendar = ['2022-12-30', '2023-01-03']
        study = self.v.ReferenceEpisodes(calendar)
        study.step(calendar[0], 0, self.population(calendar[0]), [self.formation(calendar[0])])
        result = study.step(calendar[1], 1, self.population(calendar[1],
            m00={'q': 'true', 'breakout': True}, m01={'q': 'true', 'breakout': True}), [])
        self.assertEqual(result['selection']['policyCounts'], {self.v.CANDIDATE_ID: 2, self.v.MEMBER_CONTROL_ID: 2, self.v.ANCHOR_CONTROL_ID: 1})
        self.assertEqual(result['selection']['slots'][1]['anchorControlDisposition'], 'mappedAnchorCollision')
        self.assertEqual(result['selection']['slots'][1]['anchorControlRetainedRank'], 1)

    @contextmanager
    def completed_fixture(self):
        import test_signal_leader_follower_confirmation_runner as fixtures
        fixture = fixtures.LeaderFollowerConfirmationRunnerTests()
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
                ('episode-terminals.jsonl.gz', lambda r: r.update(anchorGate='forged'), 'independent-all-episode-terminals')):
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
