import ctypes
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from service.runtime_cache import checked_root, collect, prepare


@unittest.skipUnless(os.name == "nt", "Windows runtime cache")
class RuntimeCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="atrade-cache-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def stage(self, version):
        key = version + "-" + "a" * 20
        folder = self.root / (key + ".staging")
        (folder / "resources/app").mkdir(parents=True)
        (folder / "resources/app/portable-build.json").write_text("{}")
        files = []
        for name, content in [("engine.dll", b"same engine"), ("font-a.ttf", b"font"), ("font-b.ttf", b"font")]:
            (folder / name).write_bytes(content)
            files.append({"path": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
        (folder / "resources/cache-manifest.json").write_text(json.dumps({"schema": "atrade-cache-files-v1", "files": files}))
        return key, folder

    def commit(self, key, stage):
        destination = self.root / key
        (stage / ".atrade-runtime-ready").write_text(key)
        stage.rename(destination)
        return destination

    def test_content_is_shared_within_and_across_versions(self):
        key1, stage1 = self.stage("0.4.4")
        result = prepare(self.root, stage1)
        self.assertEqual(result["verifiedFiles"], 3)
        self.assertTrue(os.path.samefile(stage1 / "font-a.ttf", stage1 / "font-b.ttf"))
        first = self.commit(key1, stage1)
        _, stage2 = self.stage("0.4.5")
        prepare(self.root, stage2)
        self.assertTrue(os.path.samefile(first / "engine.dll", stage2 / "engine.dll"))
        self.assertEqual(len(list((self.root / ".objects").iterdir())), 2)

    def test_outside_path_and_tampered_payload_fail_before_sharing(self):
        _, stage = self.stage("0.4.4")
        with self.assertRaises(ValueError):
            checked_root(self.root / "other", stage, staging=True)
        (stage / "engine.dll").write_bytes(b"bad")
        with self.assertRaises(ValueError):
            prepare(self.root, stage)
        self.assertFalse(any((self.root / ".objects").iterdir()))

    def test_unavailable_hard_links_keep_verified_private_files(self):
        _, stage = self.stage("0.4.4")
        with patch("service.runtime_cache.os.link", side_effect=OSError("unsupported filesystem")):
            result = prepare(self.root, stage)
        self.assertEqual(result["verifiedFiles"], 3)
        self.assertEqual(result["sharedBytes"], 0)
        self.assertGreater(result["privateBytes"], 0)
        self.assertEqual((stage / "engine.dll").read_bytes(), b"same engine")

    def test_active_version_is_preserved_and_unused_version_is_collected(self):
        key1, stage1 = self.stage("0.4.4")
        prepare(self.root, stage1)
        first = self.commit(key1, stage1)
        key2, stage2 = self.stage("0.4.5")
        prepare(self.root, stage2)
        second = self.commit(key2, stage2)
        data = self.root / "workspace"
        data.mkdir()
        (data / "account.txt").write_text("preserve")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                      ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateFileW(str(first), 0x80000000, 3, None, 3, 0x02000000, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            result = collect(self.root, second)
            self.assertIn(key1, result["retainedVersions"])
            self.assertTrue(first.exists())
        finally:
            kernel.CloseHandle(handle)
        result = collect(self.root, second)
        self.assertIn(key1, result["removedVersions"])
        self.assertFalse(first.exists())
        self.assertEqual((second / "engine.dll").read_bytes(), b"same engine")
        self.assertEqual((data / "account.txt").read_text(), "preserve")
        self.assertEqual(len(list((self.root / ".objects").iterdir())), 2)

    def test_interrupted_cleanup_is_retried(self):
        key, stage = self.stage("0.4.4")
        prepare(self.root, stage)
        current = self.commit(key, stage)
        abandoned = self.root / (".gc-" + "b" * 32)
        abandoned.mkdir()
        (abandoned / "leftover.dll").write_bytes(b"old")
        collect(self.root, current)
        self.assertFalse(abandoned.exists())
        self.assertTrue(current.exists())


if __name__ == "__main__":
    unittest.main()
