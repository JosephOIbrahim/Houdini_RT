"""CPU-only checks for archive safety, reproducibility and installed-byte verification."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import build_public_release as release


class PublicReleaseTests(unittest.TestCase):
    def test_relative_paths_reject_escape_and_git_query_injection(self):
        for value in ("", ".", "../file", "/file", "C:/file", "a\\b", "a/../b", "a//b", "a\nb"):
            with self.subTest(path=value), self.assertRaises(ValueError):
                release.relative(value)
        self.assertEqual(str(release.relative("packages/HoudiniRT-0.5.0/renderer/hdRT.dll")),
                         "packages/HoudiniRT-0.5.0/renderer/hdRT.dll")

    def test_runtime_allowlist_excludes_legacy_and_development_content(self):
        for name in ("renderer/hdRT.dll", "extension/rt_runtime/sidecar/transport.py",
                     "extension/houdini/icons/rt_toggle.svg", "houdini/toolbar/rt_shelf.py",
                     "extension/VERSION", "extension/rt_toggle.json", "extension/LICENSE"):
            self.assertTrue(release.is_runtime(name), name)
        for name in ("install_manifest.json", "verification/sealed_receipt.json", "extension/README.md",
                     "extension/tests/test.py", "extension/houdini/packages/rt_toggle.json",
                     "extension/houdini/RT_TOGGLE_NODE.md", "extension/rt_runtime/__pycache__/x.pyc"):
            self.assertFalse(release.is_runtime(name), name)

    def test_zip_is_deterministic_readable_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = {"b.txt": b"second", "a.txt": b"first"}
            first = release.write_zip(root / "one", data)
            second = release.write_zip(root / "two", dict(reversed(list(data.items()))))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(archive.namelist(), ["a.txt", "b.txt"])
                self.assertTrue(all(i.date_time == (2026, 11, 17, 0, 0, 0) for i in archive.infolist()))
            with self.assertRaises(FileExistsError):
                release.write_zip(root / "one", data)
            self.assertEqual((root / "one/SHA256SUMS").read_text().split()[0], release.sha(first.read_bytes()))

    def test_windows_duplicate_names_rejected(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            release.write_zip(Path(directory) / "output", {"A.txt": b"a", "a.txt": b"b"})

    def test_payload_tamper_and_unmanifested_runtime_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "renderer").mkdir()
            (root / "renderer/hdRT.dll").write_bytes(b"original")
            manifest = {"files": {"renderer/hdRT.dll": release.sha(b"original")}}
            (root / "renderer/hdRT.dll").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "Installed payload mismatch"):
                release.verify_payload(root, manifest)
            (root / "renderer/hdRT.dll").write_bytes(b"original")
            (root / "renderer/unmanifested.dll").write_bytes(b"unexpected")
            with self.assertRaisesRegex(ValueError, "Unmanifested runtime"):
                release.verify_payload(root, manifest)

    def test_receipts_must_pass_and_match_pinned_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proof.json"
            path.write_text(json.dumps({"status": "passed"}))
            manifest = {"proofs": {"test": {"path": str(path), "sha256": release.sha(path.read_bytes())}}}
            self.assertEqual(release.evidence(manifest, []), {"test": release.sha(path.read_bytes())})
            path.write_text(json.dumps({"status": "failed"}))
            with self.assertRaisesRegex(ValueError, "Unverified evidence"):
                release.evidence(manifest, [])


if __name__ == "__main__":
    unittest.main()
