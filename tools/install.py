"""User-level install/uninstall. Never copies the invoking user's SSH data."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from onboarding import shell_block

FILES = ('app.py', 'catalog.py', 'state.py', 'themes.py', 'network.py', 'onboarding.py', 'agent.sh')
MARKER = {'application': 'sshelp', 'format': 1}
WRAPPER_MARKER = '# sshelp-managed-launcher'


def paths(isolated: bool = False):
    home = Path.home()
    data = Path(os.environ.get('XDG_DATA_HOME', str(home / '.local/share')))
    if not data.is_absolute():
        raise ValueError('XDG_DATA_HOME must be an absolute path.')
    name = 'sshelp-preview' if isolated else 'sshelp'
    return home, data / name, home / '.local/bin' / name


def isolated_paths(home: Path) -> tuple[Path, Path]:
    config = Path(os.environ.get('XDG_CONFIG_HOME', str(home / '.config'))) / 'sshelp-preview/config'
    state = Path(os.environ.get('XDG_STATE_HOME', str(home / '.local/state'))) / 'sshelp-preview'
    if not config.is_absolute() or not state.is_absolute():
        raise ValueError('XDG_CONFIG_HOME and XDG_STATE_HOME must be absolute paths.')
    for path in (config, config.parent, state):
        if path.is_symlink():
            raise ValueError('Refusing a symbolic link for isolated configuration or state.')
    return config, state


def owned(target: Path) -> bool:
    if target.is_symlink() or not target.is_dir():
        return False
    marker = target / 'installation.json'
    if marker.is_symlink():
        return False
    try:
        return json.loads(marker.read_text()) == MARKER
    except (OSError, ValueError):
        return False


def check_version() -> None:
    if sys.version_info < (3, 10):
        raise ValueError('Python 3.10+ is required.')
    for command in ('ssh', 'fzf'):
        if not shutil.which(command):
            raise ValueError(f'Missing {command}; run bash install.sh --install-deps on Ubuntu 24.04+.')
    result = subprocess.run(['fzf', '--version'], capture_output=True, text=True, timeout=5, check=True)
    match = re.match(r'(\d+)\.(\d+)', result.stdout)
    if not match or tuple(map(int, match.groups())) < (0, 44):
        raise ValueError('fzf 0.44+ is required. Ubuntu 24.04+ provides a compatible version.')


def uninstall(no_shell: bool = False, isolated: bool = False) -> int:
    home, target, wrapper = paths(isolated)
    no_shell = no_shell or isolated
    if not owned(target):
        raise ValueError('No managed installation found. Nothing was removed.')
    if wrapper.is_symlink() or (wrapper.exists() and WRAPPER_MARKER not in wrapper.read_text()):
        raise ValueError('Launcher is not managed by this installer. Nothing was removed.')
    if not no_shell:
        for file in (home / '.bashrc', home / '.profile'):
            shell_block(file, 'PATH', None)
        shell_block(home / '.bashrc', 'agent', None)
    if wrapper.exists():
        wrapper.unlink()
    shutil.rmtree(target)
    print('Removed the isolated preview application.' if isolated else 'Removed the managed application and shell blocks.')
    print('Your SSH config, keys, agent process, preferences, history and backups were preserved.')
    print('Preview config/state were preserved.' if isolated else 'Private state remains under ${XDG_STATE_HOME:-~/.local/state}/sshelp. Review it manually if no longer needed.')
    return 0


def install(no_setup: bool, no_shell: bool, isolated: bool = False) -> int:
    check_version()
    home, target, wrapper = paths(isolated)
    no_shell = no_shell or isolated
    config, state = isolated_paths(home) if isolated else (None, None)
    if target.is_symlink() or (target.exists() and not owned(target)):
        raise ValueError('An unmanaged application already occupies the install directory. It will not be overwritten. Use bash install.sh --isolated to test a separate sshelp-preview installation.')
    if wrapper.is_symlink() or (wrapper.exists() and WRAPPER_MARKER not in wrapper.read_text()):
        raise ValueError('An existing unmanaged sshelp command will not be overwritten.')
    for name in FILES:
        source = ROOT / 'app' / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('The application source is incomplete or contains a symbolic link.')
    if not no_shell:
        for profile in (home / '.bashrc', home / '.profile'):
            if profile.is_symlink():
                raise ValueError('Shell startup file is a symbolic link. Use --no-shell and configure PATH yourself.')
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    wrapper.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if wrapper.parent.is_symlink():
        raise ValueError('Refusing a symbolic-link command directory.')
    temporary = Path(tempfile.mkdtemp(prefix='.sshelp-install-', dir=target.parent))
    backup = None
    try:
        for name in FILES:
            shutil.copyfile(ROOT / 'app' / name, temporary / name)
            os.chmod(temporary / name, 0o600)
        (temporary / 'installation.json').write_text(json.dumps(MARKER) + '\n')
        if target.exists():
            backup_directory = (state if isolated else Path(os.environ.get('XDG_STATE_HOME', str(home / '.local/state'))) / 'sshelp') / 'install-backups'
            backup_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            backup = backup_directory / ('app-' + str(time.time_ns()))
            target.rename(backup)
        try:
            temporary.rename(target)
        except OSError:
            if backup is not None:
                backup.rename(target)
            raise
        fd, wrapper_temp = tempfile.mkstemp(prefix='.sshelp-', dir=wrapper.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write('#!/usr/bin/env bash\n' + WRAPPER_MARKER + '\n')
                if isolated:
                    config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    if not config.exists():
                        with config.open('x', encoding='utf-8') as config_file:
                            config_file.write('# Isolated SSHELP preview aliases. No personal inventory is imported.\n')
                        os.chmod(config, 0o600)
                    stream.write('export SSHELP_ISOLATED=1\nexport SSHELP_CONFIG=' + shlex.quote(str(config)) + '\nexport SSHELP_STATE_DIR=' + shlex.quote(str(state)) + '\n')
                stream.write('exec python3 ' + shlex.quote(str(target / 'app.py')) + ' "$@"\n')
            os.chmod(wrapper_temp, 0o700)
            os.replace(wrapper_temp, wrapper)
        finally:
            if os.path.exists(wrapper_temp):
                os.unlink(wrapper_temp)
        if not no_shell:
            for profile in (home / '.bashrc', home / '.profile'):
                shell_block(profile, 'PATH', 'case ":$PATH:" in\n    *":$HOME/.local/bin:"*) ;;\n    *) export PATH="$HOME/.local/bin:$PATH" ;;\nesac')
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    if isolated:
        print('Installed isolated sshelp-preview. Existing application, hosts, state, keys and shell files were not changed.')
        print('Run: ~/.local/bin/sshelp-preview')
        print('Add separate preview hosts: ~/.local/bin/sshelp-preview -a')
        print('Remove preview application: bash uninstall.sh --isolated')
        return 0
    print('SSHELP installed for your Linux user. No SSH or network settings were changed.')
    print('Close and reopen Ubuntu, then run: sshelp --setup')
    print('You can also start immediately with: ~/.local/bin/sshelp --setup')
    if not no_setup and sys.stdin.isatty() and sys.stdout.isatty():
        return subprocess.call([sys.executable, str(target / 'app.py'), '--setup'])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-setup', action='store_true')
    parser.add_argument('--no-shell', action='store_true')
    parser.add_argument('--uninstall', action='store_true')
    parser.add_argument('--isolated', action='store_true', help='install/remove sshelp-preview with separate config/state and no shell/key setup')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if os.geteuid() == 0:
            raise ValueError('Install/uninstall as your normal Linux user, not root.')
        return uninstall(args.no_shell, args.isolated) if args.uninstall else install(args.no_setup, args.no_shell, args.isolated)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'sshelp installer: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())