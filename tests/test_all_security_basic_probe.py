import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('basic_probe_test', Path(__file__).resolve().parents[1] / 'scripts/probe-all-security-basic.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

class BasicProbeBoundaries(unittest.TestCase):
    def test_worker_requires_real_parent_and_unused_output(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(probe, 'OUTPUT', Path(directory)), patch.object(probe.os, 'getppid', return_value=42):
            marker = Path(directory) / 'attempt.json'
            marker.write_text(json.dumps({'parentPid': 41}))
            with self.assertRaises(ValueError):
                probe.authorize_worker()
            marker.write_text(json.dumps({'parentPid': 42}))
            probe.authorize_worker()
            (Path(directory) / 'manifest.json').write_text('{}')
            with self.assertRaises(ValueError):
                probe.authorize_worker()

    def test_process_failure_cannot_certify_completed_manifest(self):
        manifest = {'status': 'completed', 'auth': {'logout': {'errorCode': '0'}}}
        raw = {'complete': True, 'errorCode': '0', 'rowBoundReached': False}
        good = {'returncode': 0, 'timedOut': False}
        self.assertTrue(probe.response_complete(manifest, raw, raw, good))
        for process in ({'returncode': 1, 'timedOut': False}, {'returncode': 0, 'timedOut': True}, {}):
            self.assertFalse(probe.response_complete(manifest, raw, raw, process))
        for key, value in (('errorCode', '1'), ('complete', False), ('rowBoundReached', True)):
            changed = copy.deepcopy(raw)
            changed[key] = value
            self.assertFalse(probe.response_complete(manifest, changed, raw, good))
            self.assertFalse(probe.response_complete(manifest, raw, changed, good))

if __name__ == '__main__':
    unittest.main()
