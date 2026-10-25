"""First-run tests: temporary homes, fake interactive prompts, no real keys."""
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
sys.path.insert(0, str(ROOT / 'app'))
import onboarding


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home / 'config'),
            'XDG_DATA_HOME': str(self.home / 'data'), 'XDG_STATE_HOME': str(self.home / 'state'),
            'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1',
        }, clear=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def run_setup(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(output, 'isatty', return_value=True), patch.object(onboarding, 'yes', return_value=False), patch.object(onboarding.subprocess, 'call') as call:
                result = onboarding.setup()
        call.assert_not_called()
        return result

    def test_creates_only_empty_private_config(self):
        self.assertEqual(self.run_setup(), 0)
        config = self.home / '.ssh/config'
        self.assertNotIn('HostName', config.read_text())
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(config.parent.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.home / '.ssh/id_ed25519').exists())
        self.assertFalse((self.home / '.bashrc').exists())

    def test_preserves_existing_config_and_identity(self):
        directory = self.home / '.ssh'
        directory.mkdir()
        config, key = directory / 'config', directory / 'id_ed25519'
        config.write_text('# existing personal fixture\n')
        key.write_bytes(b'not a real key\n')
        before = (config.read_bytes(), key.read_bytes())
        self.run_setup()
        self.assertEqual((config.read_bytes(), key.read_bytes()), before)

    def test_setup_is_idempotent(self):
        self.run_setup()
        before = (self.home / '.ssh/config').read_bytes()
        self.run_setup()
        self.assertEqual((self.home / '.ssh/config').read_bytes(), before)

    def test_existing_public_key_alone_is_not_overwritten(self):
        directory = self.home / '.ssh'
        directory.mkdir()
        public = directory / 'id_ed25519.pub'
        public.write_text('not real public key material\n')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(output, 'isatty', return_value=True), patch.object(onboarding, 'yes', return_value=False) as ask, patch.object(onboarding.subprocess, 'call') as call:
                self.assertEqual(onboarding.setup(), 0)
        call.assert_not_called()
        self.assertFalse(any('Create your own' in item.args[0] for item in ask.call_args_list))
        self.assertEqual(public.read_text(), 'not real public key material\n')

    def test_noninteractive_setup_is_refused(self):
        with patch.object(sys.stdin, 'isatty', return_value=False), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(onboarding.setup(), 1)
        self.assertFalse((self.home / '.ssh').exists())

    def assert_isolated_setup_skips_every_side_effect(self):
        before = {path.relative_to(self.home): path.read_bytes()
                  for path in self.home.rglob('*') if path.is_file()}
        with patch.dict(os.environ, {'SSHELP_ISOLATED': '1'}), contextlib.redirect_stdout(io.StringIO()) as output:
            with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(output, 'isatty', return_value=True), patch.object(onboarding, 'yes') as ask, patch.object(onboarding.subprocess, 'call') as call, patch.object(onboarding, 'shell_block') as shell, patch.object(onboarding, 'private_directory') as directory:
                self.assertEqual(onboarding.setup(), 0)
        ask.assert_not_called()
        call.assert_not_called()
        shell.assert_not_called()
        directory.assert_not_called()
        self.assertIn('Isolated preview', output.getvalue())
        self.assertEqual(before, {path.relative_to(self.home): path.read_bytes()
                                  for path in self.home.rglob('*') if path.is_file()})

    def test_isolated_setup_does_not_create_config_keys_or_startup_files(self):
        self.assert_isolated_setup_skips_every_side_effect()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_isolated_setup_preserves_existing_config_keys_and_shell_files(self):
        (self.home / '.ssh').mkdir()
        for name in ('.ssh/config', '.ssh/id_ed25519', '.ssh/id_ed25519.pub', '.bashrc', '.profile'):
            (self.home / name).write_bytes(b'untouched synthetic fixture\x00\xff')
        self.assert_isolated_setup_skips_every_side_effect()

    def test_shell_blocks_do_not_duplicate(self):
        path = self.home / '.bashrc'
        path.write_text('export LOCAL_SETTING=1\n')
        for _ in range(2):
            onboarding.shell_block(path, 'PATH', 'export PATH="$HOME/.local/bin:$PATH"')
        self.assertEqual(path.read_text().count('# >>> sshelp PATH >>>'), 1)
        onboarding.shell_block(path, 'PATH', None)
        self.assertIn('export LOCAL_SETTING=1\n', path.read_text())
        self.assertNotIn('sshelp', path.read_text())

    def test_malformed_block_is_not_edited(self):
        path = self.home / '.bashrc'
        path.write_text('# >>> sshelp PATH >>>\nmanual fixture\n')
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            onboarding.shell_block(path, 'PATH', 'new')
        self.assertEqual(path.read_bytes(), before)

    def test_shell_symlink_is_refused(self):
        target = self.home / 'other'
        target.write_text('leave unchanged')
        path = self.home / '.bashrc'
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            onboarding.shell_block(path, 'agent', 'new')
        self.assertEqual(target.read_text(), 'leave unchanged')

    def test_doctor_does_not_read_inventory_or_connect(self):
        (self.home / '.ssh').mkdir()
        (self.home / '.ssh/config').write_text('# fixture\n')
        with patch.object(onboarding.shutil, 'which', return_value='/usr/bin/available'), patch.object(onboarding.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(onboarding.doctor(), 0)
        run.assert_not_called()
        self.assertIn('contents not displayed', output.getvalue())

    def test_working_inherited_agent_is_preserved(self):
        binaries = self.home / 'bin'
        binaries.mkdir()
        for name, body in (('ssh-add', 'exit 1'), ('ssh-agent', 'exit 99')):
            path = binaries / name
            path.write_text('#!/bin/sh\n' + body + '\n')
            path.chmod(0o700)
        env = dict(os.environ, PATH=str(binaries) + ':' + os.environ['PATH'], SSH_AUTH_SOCK=str(self.home / 'inherited-socket'))
        result = subprocess.run(['bash', '-c', 'source "$1"; printf "%s" "$SSH_AUTH_SOCK"', 'test-agent', str(ROOT / 'app/agent.sh')], env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, env['SSH_AUTH_SOCK'])
        self.assertFalse((self.home / '.local/state/sshelp').exists())


if __name__ == '__main__':
    unittest.main()