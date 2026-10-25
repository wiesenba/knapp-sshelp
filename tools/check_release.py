#!/usr/bin/env python3
"""Fail-closed public-tree audit. Never reads home config, state or Git history."""
from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
FILES = (
    '.gitattributes', '.gitignore', '.github/workflows/check.yml',
    'README.md', 'LICENSE', 'SECURITY.md', 'CONTRIBUTING.md',
    'Start.cmd', 'Install-Windows.ps1', 'install.sh', 'uninstall.sh',
    'app/app.py', 'app/catalog.py', 'app/state.py', 'app/themes.py',
    'app/network.py', 'app/onboarding.py', 'app/agent.sh',
    'examples/ssh_config.example', 'docs/PRIVACY.md', 'docs/PUBLISHING.md',
    'tests/test_core.py', 'tests/test_network.py', 'tests/test_onboarding.py',
    'tests/test_install.py', 'tests/test_release.py',
    'tests/ui_smoke.py', 'tests/network_smoke.py',
    'tools/install.py', 'tools/check_release.py', 'tools/build_release.py',
)
DOCUMENTATION = tuple(ipaddress.ip_network(value) for value in ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24'))
SENSITIVE = (
    ('private key', re.compile(r'-----BEGIN (?:OPENSSH|RSA|EC|DSA|ENCRYPTED) PRIVATE KEY-----')),
    ('public key data', re.compile(r'\bssh-(?:rsa|ed25519)\s+[A-Za-z0-9+/]{32,}')),
    ('access token', re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b')),
    ('personal absolute path', re.compile(r'(?:[A-Za-z]:[\\/]+Users[\\/]+[^\s\x22\x27<>]+|(?<![\w/])/home/[A-Za-z0-9_.-]+)', re.I)),
    ('private domain', re.compile(r'\b(?:[A-Za-z0-9-]+\.)+(?:local|lan|internal|corp)\b', re.I)),
    ('email address', re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')),
)


def approved_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_loopback or any(address.version == network.version and address in network for network in DOCUMENTATION)


def inspect_text(text: str, deny_terms=()) -> list[str]:
    categories = []
    for name, pattern in SENSITIVE:
        matches = list(pattern.finditer(text))
        if name == 'private domain':
            # These literal ignore-rule filenames are not network destinations.
            matches = [match for match in matches if match.group().lower() not in ('config.local', 'forbidden-terms.local')]
        if matches:
            categories.append(name)
    for match in re.finditer(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])', text):
        if not approved_address(match.group()):
            categories.append('non-documentation IP address')
            break
    # Examples of complete IPv6 addresses must also be loopback/documentation.
    for match in re.finditer(r'(?<![\w:])(?:[0-9a-f]{0,4}:){2,}[0-9a-f:]{0,39}(?:%[A-Za-z0-9_.-]+)?(?![\w:])', text, re.I):
        value = match.group().split('%', 1)[0]
        if value == '::':
            continue  # Also a programming-language scope operator, not an endpoint.
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if not address.is_loopback and address not in ipaddress.ip_network('2001:db8::/32'):
            categories.append('non-documentation IPv6 address')
            break
    folded = text.casefold()
    if any(term.strip() and term.strip().casefold() in folded for term in deny_terms):
        categories.append('locally blocked term')
    return sorted(set(categories))


def audit(root: Path = ROOT, deny_terms=()) -> list[str]:
    root = root.absolute()
    if root.is_symlink() or not root.is_dir():
        return ['Unsafe or missing package root']
    allowed = set(FILES)
    directories = {parent.as_posix() for name in FILES for parent in Path(name).parents if parent.as_posix() != '.'}
    found = set()
    issues = []
    for directory, children, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in list(children):
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                issues.append(f'{relative}: symbolic link')
                children.remove(name)
            elif relative == '.git':
                children.remove(name)  # Metadata is not an archive input; history needs separate review.
            elif relative not in directories:
                issues.append(f'{relative}: unexpected directory')
                children.remove(name)
        for name in files:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                issues.append(f'{relative}: symbolic link')
                continue
            if relative not in allowed:
                issues.append(f'{relative}: unexpected file')
                continue
            found.add(relative)
            try:
                if path.stat().st_size > 1_000_000:
                    raise ValueError('oversized source')
                text = path.read_text(encoding='utf-8')
                if '\x00' in text:
                    raise ValueError('binary source')
                issues.extend(f'{relative}: {category}' for category in inspect_text(text, deny_terms))
            except (OSError, UnicodeError, ValueError):
                issues.append(f'{relative}: unreadable, binary or oversized source')
    issues.extend(f'{name}: required file missing' for name in sorted(allowed - found))
    return sorted(issues)


def private_terms(extra=()):
    return [*extra, *os.environ.get('SSHELP_AUDIT_FORBIDDEN', '').split(',')]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deny-term', action='append', default=[], help='additional private local blocked text; never commit its value')
    args = parser.parse_args()
    issues = audit(deny_terms=private_terms(args.deny_term))
    if issues:
        print('RELEASE BLOCKED. Finding categories only; matched values are not printed:', file=sys.stderr)
        for issue in issues[:100]:
            print(' - ' + issue, file=sys.stderr)
        return 1
    print(f'PASS: {len(FILES)} allowlisted source files; no unexpected artifacts or configured findings.')
    print('Manual content and Git-history review are still required before publishing.')
    return 0


if __name__ == '__main__':
    sys.exit(main())