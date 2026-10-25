"""Install/upgrade/uninstall in disposable Linux homes, never the real home."""
import contextlib
import io
import os
import sqlite3
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
            'XDG_CONFIG_HOME': str(self.home / 'config'),
            'XDG_STATE_HOME': str(self.home / 'state'), 'PYTHONDONTWRITEBYTECODE': '1',
            'PATH': str(self.home / 'bin') + os.pathsep + os.defpath,
        }, clear=True)
        self.environment.start()
        (self.home / 'bin').mkdir()
        for name in ('ssh', 'ssh-keygen', 'ssh-agent', 'ssh-add', 'ssh-copy-id', 'ping'):
            guard = self.home / 'bin' / name
            guard.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$HOME/forbidden-command"\nexit 99\n')
            guard.chmod(0o700)
        self.version = patch.object(installer, 'check_version')
        self.version.start()
        (self.home / '.ssh').mkdir()
        (self.home / '.ssh/config').write_text('# untouched fixture\n')
        (self.home / '.ssh/id_ed25519').write_text('not a real key\n')

    def tearDown(self):
        try:
            self.assertFalse((self.home / 'forbidden-command').exists(), 'A guarded SSH/network command ran')
        finally:
            self.version.stop()
            self.environment.stop()
            self.temporary.cleanup()

    def install(self, no_shell=False, isolated=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return installer.install(no_setup=True, no_shell=no_shell, isolated=isolated)

    def snapshot(self, *paths):
        """Include directory entries, modes and bytes, not just selected sentinels."""
        result = {}
        for root in paths:
            for path in [root, *sorted(root.rglob('*'))] if root.exists() else []:
                result[path.relative_to(self.home).as_posix()] = (
                    path.stat().st_mode & 0o777,
                    None if path.is_dir() else path.read_bytes(),
                )
        return result

    def original_fixture(self, managed=False):
        if managed:
            self.install()
        _, target, wrapper = installer.paths()
        target.mkdir(parents=True, exist_ok=True)
        (target / 'custom-copy').write_bytes(b'original application\x00\xff')
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        if not managed:
            wrapper.write_bytes(b'#!/bin/sh\n# original custom command\nexit 99\n')
            wrapper.chmod(0o700)
        for name in ('.bashrc', '.profile'):
            (self.home / name).write_text(
                'alias sshelp="printf custom"\nsshelp() { printf custom; }\n'
                '# >>> sshelp PATH >>>\nexport PATH="$HOME/.local/bin:$PATH"\n# <<< sshelp PATH <<<\n'
                '# >>> sshelp agent >>>\n# original agent fixture, never sourced\n# <<< sshelp agent <<<\n'
            )
        (self.home / '.ssh/config').write_text(
            '## EX00001 - Demo - Dev\nHost Demo-Dev\n HostName 192.0.2.10\n User demo\n'
        )
        (self.home / '.ssh/id_ed25519.pub').write_text('not a real public key\n')
        (self.home / '.ssh/known_hosts').write_bytes(b'not a real host key\x00\xff')
        state = self.home / 'state/sshelp'
        state.mkdir(parents=True, exist_ok=True)
        (state / 'state.sqlite3').write_bytes(b'original state sentinel\x00\xff')
        config = self.home / 'config/sshelp'
        config.mkdir(parents=True)
        (config / 'config').write_text('# original custom configuration\n')
        self.original_paths = (
            target, wrapper, self.home / '.bashrc', self.home / '.profile',
            self.home / '.ssh', state, config,
        )
        return self.snapshot(*self.original_paths)

    def run_preview(self, *arguments, env=None):
        _, _, wrapper = installer.paths(isolated=True)
        result = subprocess.run([str(wrapper), *arguments], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_clean_install_command_and_config_preservation(self):
        before = {path.name: path.read_bytes() for path in (self.home / '.ssh').iterdir()}
        self.assertEqual(self.install(), 0)
        _, target, wrapper = installer.paths()
        self.assertTrue(installer.owned(target))
        self.assertEqual(wrapper.stat().st_mode & 0o777, 0o700)
        result = subprocess.run([str(wrapper), '--version'], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'sshelp 2.2.1')
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

    def test_isolated_install_preserves_unmanaged_original_and_empty_catalogue(self):
        before = self.original_fixture()
        with self.assertRaisesRegex(ValueError, 'unmanaged application'):
            self.install(no_shell=True)
        self.assertEqual(self.install(isolated=True), 0)
        _, target, wrapper = installer.paths(isolated=True)
        self.assertEqual(target, self.home / 'data/sshelp-preview')
        self.assertEqual(wrapper, self.home / '.local/bin/sshelp-preview')
        self.assertTrue(installer.owned(target))
        self.assertEqual(wrapper.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.run_preview('--version').stdout.strip(), 'sshelp 2.2.1')
        # Explicit wrapper exports must win over inherited normal settings.
        env = dict(os.environ, SSHELP_CONFIG=str(self.home / '.ssh/config'),
                   SSHELP_STATE_DIR=str(self.home / 'state/sshelp'), SSHELP_ISOLATED='0')
        self.assertEqual(self.run_preview('--list', env=env).stdout, '')
        self.assertEqual(self.run_preview('--list', '--json', env=env).stdout.strip(), '[]')
        config = self.home / 'config/sshelp-preview/config'
        self.assertNotIn('Host ', config.read_text())
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(before, self.snapshot(*self.original_paths))

    def test_isolated_preferences_only_update_preview_state(self):
        before = self.original_fixture()
        self.install(isolated=True)
        self.run_preview('--theme', 'ember', '--network', 'on', '--list')
        database = self.home / 'state/sshelp-preview/state.sqlite3'
        with sqlite3.connect(database) as connection:
            self.assertEqual(dict(connection.execute('SELECT name, value FROM preferences')),
                             {'theme': 'ember', 'network': 'on'})
        self.assertEqual(database.stat().st_mode & 0o777, 0o600)
        self.run_preview('--no-network', '--list')
        self.assertEqual(before, self.snapshot(*self.original_paths))

    def test_repeated_isolated_upgrades_preserve_config_and_preview_only_backups(self):
        before = self.original_fixture()
        self.install(isolated=True)
        config = self.home / 'config/sshelp-preview/config'
        config.write_text('## EX00001 - Demo - Live\nHost Demo-Live\n HostName 192.0.2.11\n User demo\n')
        config_before = config.read_bytes()
        self.run_preview('--theme', 'ember', '--list')
        database = self.home / 'state/sshelp-preview/state.sqlite3'
        state_before = database.read_bytes()
        _, target, _ = installer.paths(isolated=True)
        backups = self.home / 'state/sshelp-preview/install-backups'
        for index in range(2):
            with self.subTest(upgrade=index):
                sentinel = f'preview version {index}'.encode()
                (target / 'preview-only-fixture').write_bytes(sentinel)
                prior = {path.name for path in backups.iterdir()} if backups.exists() else set()
                self.assertEqual(self.install(isolated=True), 0)
                added = [path for path in backups.iterdir() if path.name not in prior]
                self.assertEqual(len(added), 1)
                self.assertTrue(installer.owned(added[0]))
                self.assertEqual((added[0] / 'preview-only-fixture').read_bytes(), sentinel)
                self.assertFalse((added[0] / 'custom-copy').exists())
                self.assertEqual(config.read_bytes(), config_before)
                self.assertEqual(database.read_bytes(), state_before)
                self.assertFalse((target / 'preview-only-fixture').exists())
        self.assertEqual(len(list(backups.iterdir())), 2)
        listing = self.run_preview('--list').stdout
        self.assertIn('Demo-Live', listing)
        self.assertNotIn('Demo-Dev', listing)
        self.assertEqual(before, self.snapshot(*self.original_paths))

    def assert_isolated_uninstall(self, managed):
        before = self.original_fixture(managed=managed)
        self.install(isolated=True)
        self.run_preview('--theme', 'ember', '--list')
        self.install(isolated=True)
        config, state = installer.isolated_paths(self.home)
        private_before = self.snapshot(config.parent, state)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(installer.uninstall(isolated=True), 0)
        _, target, wrapper = installer.paths(isolated=True)
        self.assertFalse(target.exists())
        self.assertFalse(wrapper.exists())
        self.assertEqual(private_before, self.snapshot(config.parent, state))
        self.assertEqual(before, self.snapshot(*self.original_paths))
        self.assertEqual(installer.owned(installer.paths()[1]), managed)

    def test_isolated_uninstall_preserves_unmanaged_original_and_all_shell_blocks(self):
        self.assert_isolated_uninstall(managed=False)

    def test_isolated_uninstall_preserves_managed_original_and_all_shell_blocks(self):
        self.assert_isolated_uninstall(managed=True)

    def test_isolated_config_symlinks_are_refused(self):
        before = self.original_fixture()
        config = self.home / 'config/sshelp-preview/config'
        config.parent.mkdir()
        for destination in (self.home / '.ssh/config', self.home / 'missing-config'):
            with self.subTest(dangling=not destination.exists()):
                config.symlink_to(destination)
                with self.assertRaisesRegex(ValueError, 'symbolic link'):
                    self.install(isolated=True)
                self.assertTrue(config.is_symlink())
                config.unlink()
        self.assertFalse(installer.paths(isolated=True)[1].exists())
        self.assertEqual(before, self.snapshot(*self.original_paths))

    def test_isolated_config_directory_symlink_is_refused(self):
        before = self.original_fixture()
        (self.home / 'config/sshelp-preview').symlink_to(self.home / '.ssh', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            self.install(isolated=True)
        self.assertFalse(installer.paths(isolated=True)[1].exists())
        self.assertEqual(before, self.snapshot(*self.original_paths))

    def test_isolated_state_symlinks_are_refused(self):
        before = self.original_fixture()
        state = self.home / 'state/sshelp-preview'
        for destination in (self.home / 'state/sshelp', self.home / 'missing-state'):
            with self.subTest(dangling=not destination.exists()):
                state.symlink_to(destination, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, 'symbolic link'):
                    self.install(isolated=True)
                self.assertTrue(state.is_symlink())
                state.unlink()
        self.assertFalse(installer.paths(isolated=True)[1].exists())
        self.assertEqual(before, self.snapshot(*self.original_paths))

    def test_preview_database_symlink_cannot_modify_original_state(self):
        before = self.original_fixture()
        self.install(isolated=True)
        state = self.home / 'state/sshelp-preview'
        state.mkdir()
        (state / 'state.sqlite3').symlink_to(self.home / 'state/sshelp/state.sqlite3')
        result = subprocess.run([str(installer.paths(isolated=True)[2]), '--theme', 'ember', '--list'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn('symlink', result.stderr)
        self.assertEqual(before, self.snapshot(*self.original_paths))

    def test_isolated_install_skips_interactive_setup_and_shell_edits(self):
        before = self.original_fixture()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(output, 'isatty', return_value=True), patch.object(installer.subprocess, 'call') as call, patch.object(installer, 'shell_block') as shell:
                self.assertEqual(installer.install(no_setup=False, no_shell=False, isolated=True), 0)
        call.assert_not_called()
        shell.assert_not_called()
        self.assertEqual(before, self.snapshot(*self.original_paths))

    def test_preview_setup_cannot_generate_keys_or_edit_startup_config(self):
        before = self.original_fixture()
        self.install(isolated=True)
        preview_before = self.snapshot(self.home / 'config/sshelp-preview')
        result = self.run_preview('--setup')
        self.assertIn('Isolated preview', result.stdout)
        self.assertFalse((self.home / 'state/sshelp-preview').exists())
        self.assertEqual(preview_before, self.snapshot(self.home / 'config/sshelp-preview'))
        self.assertEqual(before, self.snapshot(*self.original_paths))

    @unittest.skipIf(os.geteuid() == 0, 'shell installer intentionally refuses root')
    def test_real_shell_isolated_install_upgrade_and_uninstall_in_temporary_home(self):
        before = self.original_fixture()
        for _ in range(2):
            result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--isolated'],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_preview('--version').stdout.strip(), 'sshelp 2.2.1')
        self.assertEqual(self.run_preview('--list').stdout, '')
        self.run_preview('--setup')
        private = (self.home / 'config/sshelp-preview', self.home / 'state/sshelp-preview')
        preview_before = self.snapshot(*private)
        result = subprocess.run(['bash', str(ROOT / 'uninstall.sh'), '--isolated'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(installer.paths(isolated=True)[1].exists())
        self.assertFalse(installer.paths(isolated=True)[2].exists())
        self.assertEqual(preview_before, self.snapshot(*private))
        self.assertEqual(before, self.snapshot(*self.original_paths))


if __name__ == '__main__':
    unittest.main()