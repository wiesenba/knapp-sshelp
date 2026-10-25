#!/usr/bin/env python3
"""Reproducible allowlist-only source ZIP. Never publishes or gathers user data."""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
from check_release import ROOT, FILES, audit, inspect_text, private_terms

VERSION = '2.2.1'


def build(root: Path, output: Path, deny_terms=()) -> tuple[Path, str]:
    root, output = root.resolve(), output.resolve()
    if output == root or root in output.parents:
        raise ValueError('Build output must be outside the reviewed source tree.')
    issues = audit(root, deny_terms)
    if issues:
        raise ValueError('Release audit failed: ' + '; '.join(issues[:8]))
    # Snapshot exactly these bytes; audit them again to avoid a read/build race.
    content = {}
    for name in sorted(FILES):
        path = root / name
        if path.is_symlink():
            raise ValueError('Archive input changed to a symbolic link.')
        data = path.read_bytes()
        if inspect_text(data.decode('utf-8'), deny_terms):
            raise ValueError('An archive input failed its final content check.')
        data = data.replace(b'\r\n', b'\n')
        content[name] = data.replace(b'\n', b'\r\n') if name.endswith('.cmd') else data
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = output / f'sshelp-{VERSION}.zip'
    fd, temporary = tempfile.mkstemp(prefix='.sshelp-release-', dir=output)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in content.items():
                info = zipfile.ZipInfo('sshelp/' + name, date_time=(2020, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        digest = hashlib.sha256(Path(temporary).read_bytes()).hexdigest()
        os.replace(temporary, destination)
        (output / 'SHA256SUMS').write_text(f'{digest}  {destination.name}\n', encoding='ascii')
        return destination, digest
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT.parent / 'sshelp-release')
    parser.add_argument('--deny-term', action='append', default=[])
    args = parser.parse_args()
    try:
        destination, digest = build(ROOT, args.output_dir, private_terms(args.deny_term))
        print(f'Built {destination.name}: {len(FILES)} audited files.')
        print('SHA256 ' + digest)
        print('No Git repository was initialized and nothing was published.')
        return 0
    except (OSError, ValueError, UnicodeError) as error:
        print(f'Release blocked: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())