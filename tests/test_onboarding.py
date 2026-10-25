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
        self.environment = patch.dict(os.environ, {'HOME': str(self.home)})
        self.environment.start()
        for key in ('SSHELP_CONFIG', 'SSH_AUTH_SOCK'):
            os.environ.pop(key, None)

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