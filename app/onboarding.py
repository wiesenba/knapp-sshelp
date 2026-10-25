"""Explicit first-run setup. No host discovery, networking or stored secrets."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError('Refusing a symbolic link for a private directory.')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def shell_block(path: Path, name: str, content: str | None) -> None:
    """Change only our bounded block; refuse links and malformed markers."""
    if path.is_symlink():
        raise ValueError('Refusing to edit a symbolic-link shell startup file.')
    text = path.read_text(encoding='utf-8') if path.exists() else ''
    start, end = f'# >>> sshelp {name} >>>', f'# <<< sshelp {name} <<<'
    if text.count(start) != text.count(end) or text.count(start) > 1:
        raise ValueError('Malformed managed shell block; review it manually.')
    if start in text:
        pattern = re.escape(start) + r'\n.*?' + re.escape(end) + r'\n?'
        text, count = re.subn(pattern, '', text, flags=re.S)
        if count != 1:
            raise ValueError('Cannot safely replace a managed shell block.')
    if content is not None:
        text += ('' if not text or text.endswith('\n') else '\n') + f'\n{start}\n{content}\n{end}\n'
    if content is not None or path.exists():
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        path.write_text(text, encoding='utf-8')
        os.chmod(path, mode)


def yes(prompt: str) -> bool:
    return input(prompt + ' [y/N] ').strip().lower() in ('y', 'yes')


def setup() -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print('Open an interactive Ubuntu/WSL terminal and run sshelp --setup.', file=sys.stderr)
        return 1
    os.umask(0o077)
    home = Path.home()
    print('\nSSHELP / first-run setup\n')
    print('Existing hosts, SSH defaults, VPN, Windows and WSL network settings are preserved.')
    print('No host is contacted. Key passphrases are entered directly into OpenSSH, never stored here.\n')
    private_directory(home / '.ssh')
    config = Path(os.environ.get('SSHELP_CONFIG', str(home / '.ssh/config'))).expanduser().absolute()
    if config.is_symlink():
        print('Existing SSH config is a symbolic link; left unchanged.')
    elif config.exists():
        print('Existing SSH config preserved.')
    else:
        if config.parent != home / '.ssh':
            raise ValueError('Create a custom SSHELP_CONFIG yourself; setup only creates the default config.')
        with config.open('x', encoding='utf-8') as stream:
            stream.write('# Your personal SSH aliases. Never commit this file.\n# Add a host with: sshelp -a\n')
        os.chmod(config, 0o600)
        print('Created an empty private SSH config. No sample hosts were activated.')
    key = home / '.ssh/id_ed25519'
    if any(path.exists() or path.is_symlink() for path in (key, key.with_name(key.name + '.pub'))):
        print('An existing default Ed25519 key file was found and will not be overwritten.')
    elif yes('Create your own Ed25519 key? Use a strong passphrase when prompted'):
        status = subprocess.call(['ssh-keygen', '-t', 'ed25519', '-a', '64', '-f', str(key), '-C', 'sshelp'])
        if status:
            print('Key generation did not complete. You can retry with ssh-keygen later.')
    if yes('Enable the optional shared SSH agent in new Bash terminals'):
        if not shutil.which('flock'):
            print('Agent setup skipped: flock is missing (normally provided by util-linux).')
        else:
            helper = Path(__file__).resolve().with_name('agent.sh')
            shell_block(home / '.bashrc', 'agent', f'if [[ $- == *i* && -r {shlex.quote(str(helper))} ]]; then\n    source {shlex.quote(str(helper))}\nfi')
            print('Agent enabled for new Bash terminals. Existing working agents are preserved.')
            print('After reopening Ubuntu, load your own key with: ssh-add ~/.ssh/id_ed25519')
    print('\nNext: sshelp -a to add YOUR first host, then sshelp to open the dashboard.')
    print('Network previews start OFF. Alt-N enables them; Ctrl-T changes the theme.')
    print('When first connecting, verify the server fingerprint with its administrator before accepting.')
    return 0


def doctor() -> int:
    """Local checks only: never enumerate keys or read/print the host inventory."""
    print('SSHELP / local checks only (no host connections)')
    failed = False
    for command, required in (('ssh', True), ('fzf', False), ('ping', False), ('timeout', False), ('ssh-copy-id', False), ('flock', False)):
        found = bool(shutil.which(command))
        print(f'{command:12} ' + ('available' if found else ('MISSING' if required else 'optional / missing')))
        failed |= required and not found
    print(f'Python       {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')
    config = Path(os.environ.get('SSHELP_CONFIG', str(Path.home() / '.ssh/config'))).expanduser()
    print('SSH config   ' + ('present (contents not displayed)' if config.is_file() else 'missing; run sshelp --setup'))
    failed |= not config.is_file()
    if os.environ.get('SSH_AUTH_SOCK') and shutil.which('ssh-add'):
        try:
            result = subprocess.run(['ssh-add', '-l'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            print('SSH agent    ' + ('reachable' if result.returncode in (0, 1) else 'unreachable; reopen your terminal or inspect your agent'))
        except (OSError, subprocess.TimeoutExpired):
            print('SSH agent    check unavailable')
    else:
        print('SSH agent    not configured (optional; SSH can prompt directly)')
    return int(failed)