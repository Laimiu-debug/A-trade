"""Regression checks for two explicit gaps in the frozen feature audit scope."""
from contextlib import redirect_stdout
import io
import json
import unittest
import zipfile

import test_signal_supplemental_leadership_panel as fixture

closure = fixture.source_fixture.module('supplemental_panel_closure_tests', 'scripts/verify-supplemental-panel-closure.py')


class SupplementalPanelClosureTests(unittest.TestCase):
    def setUp(self):
        self.case = fixture.SupplementalLeadershipPanelTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        with redirect_stdout(io.StringIO()):
            fixture.runner.execute(fixture.Namespace(workspace=self.case.workspace, output=self.case.output))

    def verify(self):
        return closure.verify(self.case.output, fixture.ROOT, self.case.registration, self.case.digest(self.case.registration))

    def test_valid_source_and_wrong_completion_session_count(self):
        self.assertTrue(self.verify()['passed'])
        path = self.case.output/'completion.json'
        value = self.case.read(path)
        value['targetSessions'] -= 1
        self.case.put(path, value)
        report = self.verify()
        self.assertFalse(report['passed'])
        self.assertIn('completion-target-session-count', report['issues'])

    def test_removed_required_dependency_fails_even_after_all_receipts_rehashed(self):
        output = self.case.output
        manifest = self.case.read(output/'manifest.json')
        name = 'scripts/build-signal-supplemental-leadership-panel.py'
        manifest['implementation'].pop(name)
        with zipfile.ZipFile(output/'execution-sources.zip') as archive:
            contents = {item:archive.read(item) for item in archive.namelist() if item != name}
        with zipfile.ZipFile(output/'execution-sources.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for item, raw in contents.items():
                archive.writestr(item, raw)
        manifest['executionArchiveSha256'] = self.case.digest(output/'execution-sources.zip')
        self.case.put(output/'manifest.json', manifest)
        summary = self.case.read(output/'summary.json')
        summary['sampleManifestHash'] = self.case.digest(output/'manifest.json')
        self.case.put(output/'summary.json', summary)
        receipt = self.case.read(output/'completion.json')
        receipt['sampleManifestHash'] = summary['sampleManifestHash']
        for item in ('manifest.json','summary.json','execution-sources.zip'):
            receipt['files'][item]['sha256'] = self.case.digest(output/item)
        self.case.put(output/'completion.json', receipt)
        report = self.verify()
        self.assertFalse(report['passed'])
        self.assertIn('exact-required-implementation-member-set', report['issues'])


if __name__ == '__main__':
    unittest.main()
