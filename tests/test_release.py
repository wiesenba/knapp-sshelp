"""Release allowlist, leak detection and reproducibility regression tests."""
import tempfile
import unittest
import zipfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from check_release import FILES, audit, inspect_text
from build_release import build


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / 'source'
        self.root.mkdir()
        for name in FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('public fixture\n')

    def tearDown(self):
        self.temporary.cleanup()

    def test_exact_public_tree_is_accepted(self):
        self.assertEqual(audit(self.root), [])

    def test_unexpected_file_blocks_release(self):
        (self.root / 'personal-config').write_text('must not ship')
        self.assertTrue(any('unexpected file' in issue for issue in audit(self.root)))

    def test_unknown_directory_blocks_release(self):
        (self.root / '.ssh').mkdir()
        self.assertTrue(any('unexpected directory' in issue for issue in audit(self.root)))

    def test_missing_file_blocks_release(self):
        (self.root / 'README.md').unlink()
        self.assertTrue(any('required file missing' in issue for issue in audit(self.root)))

    def test_symlinks_block_release(self):
        path = self.root / 'README.md'
        path.unlink()
        path.symlink_to(self.root / 'LICENSE')
        self.assertTrue(any('symbolic link' in issue for issue in audit(self.root)))

    def test_only_documentation_and_loopback_ips_are_allowed(self):
        self.assertFalse(inspect_text('192.0.2.10 198.51.100.20 127.0.0.1'))
        private = '.'.join(('10', '1', '2', '3'))
        public = '.'.join(('8', '8', '8', '8'))
        for address in (private, public):
            self.assertIn('non-documentation IP address', inspect_text(address))

    def test_secret_detection_does_not_echo_values(self):
        marker = '-----BEGIN ' + 'OPENSSH PRIVATE KEY-----'
        self.assertIn('private key', inspect_text(marker))
        self.assertIn('access token', inspect_text('ghp_' + 'a' * 30))
        term = 'private-fixture-token'
        issues = inspect_text(term, [term])
        self.assertEqual(issues, ['locally blocked term'])
        self.assertNotIn(term, ' '.join(issues))

    def test_personal_absolute_path_detection(self):
        value = '/home/' + 'demo/private'
        self.assertIn('personal absolute path', inspect_text(value))

    def test_binary_content_is_blocked(self):
        (self.root / 'README.md').write_bytes(b'\x00\xff')
        self.assertTrue(any('binary' in issue for issue in audit(self.root)))

    def test_archive_is_allowlisted_and_reproducible(self):
        output = self.root.parent / 'release'
        path, first = build(self.root, output)
        before = path.read_bytes()
        second_path, second = build(self.root, output)
        self.assertEqual(first, second)
        self.assertEqual(before, second_path.read_bytes())
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(sorted(archive.namelist()), sorted('sshelp/' + name for name in FILES))
            self.assertTrue(all(not info.filename.startswith('/') and '..' not in Path(info.filename).parts for info in archive.infolist()))

    def test_archive_cannot_be_built_inside_source_tree(self):
        with self.assertRaises(ValueError):
            build(self.root, self.root / 'dist')

    def test_archive_stops_if_source_contains_private_term(self):
        (self.root / 'README.md').write_text('local-only-example')
        with self.assertRaises(ValueError):
            build(self.root, self.root.parent / 'release', ['local-only-example'])


if __name__ == '__main__':
    unittest.main()