"""Install/upgrade/uninstall in disposable Linux homes, never the real home."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import install as installer


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='sshelp-install-test-')
        self.home = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            'HOME': str(self.home), 'XDG_DATA_HOME': str(self.home / 'data'),
            'XDG_STATE_HOME': str(self.home / 'state'), 'PYTHONDONTWRITEBYTECODE': '1',
        })
        self.environment.start()
        self.version = patch.object(installer, 'check_version')
        self.version.start()
        (self.home / '.ssh').mkdir()
        (self.home / '.ssh/config').write_text('# untouched fixture\n')
        (self.home / '.ssh/id_ed25519').write_text('not a real key\n')

    def tearDown(self):
        self.version.stop()
        self.environment.stop()
        self.temporary.cleanup()

    def install(self, no_shell=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return installer.install(no_setup=True, no_shell=no_shell)

    def test_clean_install_command_and_config_preservation(self):
        before = {path.name: path.read_bytes() for path in (self.home / '.ssh').iterdir()}
        self.assertEqual(self.install(), 0)
        _, target, wrapper = installer.paths()
        self.assertTrue(installer.owned(target))
        self.assertEqual(wrapper.stat().st_mode & 0o777, 0o700)
        result = subprocess.run([str(wrapper), '--version'], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'sshelp 2.2.0')
        self.assertEqual({path.name: path.read_bytes() for path in (self.home / '.ssh').iterdir()}, before)

    def test_upgrade_is_idempotent_and_backed_up(self):
        self.install()
        self.install()
        for name in ('.bashrc', '.profile'):
            self.assertEqual((self.home / name).read_text().count('# >>> sshelp PATH >>>'), 1)
        backups = list((self.home / 'state/sshelp/install-backups').iterdir())
        self.assertEqual(len(backups), 1)
        self.assertTrue(installer.owned(backups[0]))

    def test_unmanaged_app_is_not_overwritten(self):
        _, target, _ = installer.paths()
        target.mkdir(parents=True)
        (target / 'keep').write_text('unchanged')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual((target / 'keep').read_text(), 'unchanged')

    def test_unmanaged_command_is_not_overwritten(self):
        _, _, wrapper = installer.paths()
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text('# user command\n')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(wrapper.read_text(), '# user command\n')

    def test_binary_unmanaged_command_is_safely_refused(self):
        _, _, wrapper = installer.paths()
        wrapper.parent.mkdir(parents=True)
        wrapper.write_bytes(b'\xff\x00user binary')
        with patch.object(installer.os, 'geteuid', return_value=1000), patch.object(sys, 'argv', ['install.py', '--no-setup']), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(installer.main(), 1)
        self.assertEqual(wrapper.read_bytes(), b'\xff\x00user binary')

    def test_binary_install_marker_is_not_owned(self):
        _, target, _ = installer.paths()
        target.mkdir(parents=True)
        (target / 'installation.json').write_bytes(b'\xff\x00')
        self.assertFalse(installer.owned(target))
        with self.assertRaises(ValueError):
            self.install()

    @unittest.skipIf(os.geteuid() == 0, 'shell installer intentionally refuses root')
    def test_real_shell_install_and_uninstall_in_temporary_home(self):
        env = dict(os.environ)
        for key in ('SSHELP_CONFIG', 'SSH_AUTH_SOCK', 'BASH_ENV', 'ENV'):
            env.pop(key, None)
        result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--no-setup', '--no-shell'], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        _, _, wrapper = installer.paths()
        doctor = subprocess.run([str(wrapper), '--doctor'], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        listing = subprocess.run([str(wrapper), '--list'], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertEqual(listing.stdout, '')
        removal = subprocess.run(['bash', str(ROOT / 'uninstall.sh'), '--no-shell'], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(removal.returncode, 0, removal.stderr)
        self.assertFalse(wrapper.exists())
        self.assertEqual((self.home / '.ssh/config').read_text(), '# untouched fixture\n')

    def test_app_symlink_is_refused(self):
        _, target, _ = installer.paths()
        target.parent.mkdir(parents=True)
        other = self.home / 'other'
        other.mkdir()
        target.symlink_to(other, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.install()

    def test_no_shell_option_preserves_startup_files(self):
        path = self.home / '.bashrc'
        path.write_text('export LOCAL_SETTING=1\n')
        self.install(no_shell=True)
        self.assertEqual(path.read_text(), 'export LOCAL_SETTING=1\n')
        self.assertFalse((self.home / '.profile').exists())

    def test_uninstall_preserves_user_files_and_state(self):
        self.install()
        state = self.home / 'state/sshelp'
        state.mkdir(parents=True, exist_ok=True)
        (state / 'keep').write_text('private local fixture')
        before = (self.home / '.ssh/config').read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(installer.uninstall(), 0)
        _, target, wrapper = installer.paths()
        self.assertFalse(target.exists())
        self.assertFalse(wrapper.exists())
        self.assertEqual((self.home / '.ssh/config').read_bytes(), before)
        self.assertTrue((self.home / '.ssh/id_ed25519').exists())
        self.assertTrue((state / 'keep').exists())
        self.assertNotIn('# >>> sshelp', (self.home / '.bashrc').read_text())

    def test_uninstall_refuses_unknown_app(self):
        with self.assertRaises(ValueError):
            installer.uninstall()

    def test_root_install_is_refused(self):
        with patch.object(installer.os, 'geteuid', return_value=0), patch.object(sys, 'argv', ['install.py', '--no-setup']), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(installer.main(), 1)
        self.assertFalse((self.home / 'data').exists())


if __name__ == '__main__':
    unittest.main()