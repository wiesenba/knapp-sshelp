# Contributing

Keep SSHELP standalone, small and safe to publish. Contributions use the MIT license in [LICENSE](LICENSE); no organizational attribution or private operational material is required.

## Design boundaries

- Keep runtime Python modules flat under [app/](app/), using only the Python standard library. Target Linux, Ubuntu 24.04+ and WSL2; require Python 3.10+ and fzf 0.44+.
- Keep [install.sh](install.sh) as the shell entry point to [tools/install.py](tools/install.py). Install as a normal Linux user and refuse unmanaged conflicts. Preserve managed-marker checks and bounded shell blocks.
- Keep `--isolated` preview application/config/state separate, with no migration, wizard, key/agent setup or shell changes. Preview upgrades/backups and uninstall must leave normal managed or custom copies untouched; preserve preview config/state on uninstall. `--no-shell` is not isolation. See [README.md](README.md#existing-custom-installation--isolated-preview).
- Do not change WSL networking, Windows settings, VPN configuration, global SSH defaults, keys or existing host entries. Keep setup and host edits explicit and reviewed. Do not invent X11, agent or port forwards.
- Preserve working inherited agent sockets. Never auto-load a key, capture a passphrase or add telemetry.
- Network preview must start off for new state, remain focused/visible-only, use direct ICMP/TCP without login, and cancel helpers reliably. Static commands must not introduce probes. Document the `ssh -G` / user-authored `Match exec` caveat.
- Use subprocess argument lists and safe display text. Keep tests isolated from real config, keys, agents and inventories.

## Public fixtures only

Use only Demo-Dev, Demo-Live and Lab-Sbx for host aliases; EX00001 and EX00002 for project codes; `192.0.2.10`, `192.0.2.11`, `198.51.100.20`, loopback, `example.com` and `example.invalid` for addresses/domains. Any SSH username in a fixture must be synthetic, never an actual account name. Project comments accept two letters plus five digits and an optional label.

Do not include company names, real people or account names, personal paths, internal identifiers, actual inventory, keys, config, state, backups, screenshots, recordings or unreviewed logs. Keep private local configuration outside this checkout. Do not add real blocked terms to source, test fixtures or workflow configuration.

## Validate changes

Run checks from this isolated repository root in Linux with Python 3.12 for CI parity. Have OpenSSH client, fzf 0.44+ and iputils-ping available first. Set `PYTHONDONTWRITEBYTECODE=1` in the test environment, then perform:

1. `bash -n install.sh uninstall.sh app/agent.sh`
2. `python3 -m unittest discover -s tests -p 'test_*.py' -v`
3. `python3 tests/ui_smoke.py`
4. `python3 tests/network_smoke.py`
5. `python3 tools/check_release.py`
6. `python3 tools/build_release.py --output-dir ../sshelp-release`

All application test networking must be mocked or loopback-only. Do not probe documentation addresses or public hosts, and do not use your real agent/config for tests. UI tests should cover all themes, strong focus, monochrome, project selection and terminal resizing; network tests should cover opt-in, hide/change/exit cancellation, DNS deadlines and direct-path limitations. Installer tests must use temporary homes and mocked privileged operations, not a real installation.

The release auditor must use an exact file allowlist and secret/IP/path checks; the builder must audit before making a reproducible allowlisted ZIP and SHA256SUMS. These commands and both smoke scripts are mandatory release prerequisites: **if a script is missing, stop rather than skip it or publish an unverified release.**

For an additional private check, use `python3 tools/check_release.py --deny-term TEXT`, replacing TEXT only in your local terminal, or use the comma-separated `SSHELP_AUDIT_FORBIDDEN` environment variable. Real values must never be committed, pasted into reports or placed in CI configuration. Commands may enter local shell history; keep it private. Review audit output before sharing it. See [docs/PUBLISHING.md](docs/PUBLISHING.md).

## Propose a change

Make a focused change with regression tests and update relevant documentation. Review the entire diff and the staged diff, not just ignored-file status. Report test results as a short sanitized summary, without logs or screenshots. Use [SECURITY.md](SECURITY.md) for vulnerabilities. Do not publish until the privacy and release checklist passes; automated checks cannot prove the absence of every sensitive datum.