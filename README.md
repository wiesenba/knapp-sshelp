# SSHELP 2.2.0

A standalone terminal dashboard for your own SSH aliases. Search hosts and projects, keep favourites and recents, and connect using OpenSSH. The application uses the Python standard library; no Python packages or account registration are required.

## Requirements

- Linux; Ubuntu 24.04 or newer is the recommended baseline. Windows users run it inside Ubuntu on WSL2, not native Windows Python.
- Python 3.10+, OpenSSH client and **fzf 0.44+**. Optional ping uses `iputils-ping`.
- A normal Linux user, **not root**. Install and uninstall without prefixing the scripts with `sudo`.

## Windows: first installation

1. Follow the [official WSL installation guide](https://learn.microsoft.com/en-us/windows/wsl/install). Install WSL2 and Ubuntu 24.04 or newer; finish any restart requested by that guide.
2. **Open Ubuntu once.** Complete its first-run prompts and create a **normal Linux user**, not root. Its password is a Linux password, not your Windows password.
3. On this GitHub repository, choose **Code → Download ZIP**. In Windows, right-click the downloaded ZIP and choose **Extract All**. Do not start installation from inside the compressed ZIP.
4. Open the extracted folder and double-click [Start.cmd](Start.cmd). It calls [Install-Windows.ps1](Install-Windows.ps1), which launches [install.sh](install.sh) in your installed distribution. If several distributions exist, select your Ubuntu distribution, not a container-managed one.
5. If required commands are missing, the installer asks before installing dependencies with `sudo apt-get`. Review the prompt. Type any sudo password **directly into that terminal**: nothing is echoed while you type. Use the Linux user's password, not a Windows password. Do not paste passwords into issues or chats.
6. Follow the setup prompts. Close and reopen the Ubuntu terminal, then run `sshelp --setup` if setup was skipped and `sshelp --doctor` for local checks. Run `sshelp` to open the dashboard.

If Windows prevents the launcher from opening, use the Ubuntu installation route below. Do not disable system security controls to install SSHELP.

## Ubuntu / Linux installation

Open a terminal **in the extracted SSHELP folder**, then run `bash install.sh`. This shell entry point calls [tools/install.py](tools/install.py).

Installer options:

| Option | Meaning |
| --- | --- |
| `--install-deps` | Explicitly authorize `sudo apt-get update` and dependency installation on an apt-based distribution. |
| `--no-setup` | Skip the interactive first-run wizard. |
| `--no-shell` | Do not add PATH blocks to shell startup files; configure your PATH yourself. |

Options can be combined. Without shell changes, the installed command can be invoked as `~/.local/bin/sshelp`. An outdated fzf must be upgraded to 0.44+; merely having an older executable is insufficient.

The installer manages `${XDG_DATA_HOME:-~/.local/share}/sshelp` and the wrapper `~/.local/bin/sshelp`. It refuses unmanaged conflicts rather than replacing them. Unless `--no-shell` is used, it adds bounded PATH blocks to `~/.bashrc` and `~/.profile`. Reopen the terminal after installation. Reinstalling updates a managed application and keeps a private backup of its previous files.

It does **not** change WSL networking, Windows settings, VPN configuration, global SSH defaults, keys or existing host entries. The optional setup and add-host actions below make only their stated, user-approved changes.

## Set up your own hosts

Run `sshelp --setup`. Existing SSH config is preserved; when absent, setup creates an empty `~/.ssh/config`. **Examples are never activated automatically.**

- Optional key creation runs `ssh-keygen` directly. Enter a strong passphrase into OpenSSH's prompt; SSHELP does not store it or overwrite an existing default identity.
- An optional shared Bash agent preserves a working inherited agent socket. Enabling it adds a bounded Bash startup block; it does not load keys automatically. After reopening Ubuntu, explicitly load your key with `ssh-add ~/.ssh/id_ed25519`. Enter the passphrase directly into OpenSSH.
- Run `sshelp -a` to review and confirm a minimal host entry in your private SSH config, **outside this checkout**. A private backup is created before the confirmed append. Existing host settings remain unchanged; the wizard does not invent X11, agent or port forwards.
- Verify a server's fingerprint through a trusted channel before accepting it on your first connection.

[examples/ssh_config.example](examples/ssh_config.example) is documentation only. It uses Demo-Dev, Demo-Live and Lab-Sbx with reserved documentation addresses, not live servers. Project comments use two letters plus five digits, for example `EX00001`, with an optional label; see the example's comment layout. Never replace the checked-in examples with your real inventory.

## Everyday use

Run `sshelp` and type to search. Connections always use the selected or explicitly named destination; fuzzy search does not automatically connect.

| Key | Action |
| --- | --- |
| Enter | Connect to the selected host. |
| Ctrl-S / Ctrl-F / Ctrl-R | Toggle favourite / show favourites / show recents. |
| Ctrl-P / Ctrl-E / Ctrl-A | Choose project / cycle environment / reset filters. |
| Ctrl-T | Cycle Minimal, Midnight and Ember. |
| Alt-P | Show or hide details. |
| Alt-N | Toggle focused network checks. |
| Ctrl-N / Ctrl-D | Add a host / run an SSH diagnostic. |
| Esc | Exit. |

All three themes preserve the terminal's background; transparency is controlled by your terminal. The focus marker is a strong two-cell rail and arrow, with bold, underlined selection; monochrome uses `>>`.

Useful commands:

- `sshelp --env dev --project EX00001`: filter the dashboard.
- `sshelp --list` or `sshelp --list --json`: print private inventory locally.
- `sshelp --details Demo-Dev`: show effective settings without SSHELP probes.
- `sshelp --plain`: numbered-menu fallback.
- `sshelp --no-network`: remember network previews as off.
- `sshelp --doctor`: check local prerequisites and agent availability.

**Network previews start OFF for new state.** Alt-N or `--network on` enables checks only for the selected, visible preview: one ICMP ping and one TCP connection to its effective SSH port, without login. The setting is remembered. Checks are debounced, periodically refreshed and cancelled on selection change, preview hiding or exit. They test the **direct path only**, not an SSH proxy or jump route. Filtered ICMP does not prove a host is down, and an open TCP port does not prove SSH authentication will work.

Enter, an exact destination, `--copy-key` and diagnostics contact servers. Copy-key installs your public key remotely through `ssh-copy-id`; diagnostics perform a bounded SSH attempt with forwarding disabled. Static details, lists and doctor do not run SSHELP probes. Effective settings use `ssh -G`, which may execute your own SSH `Match exec` rules. Use only SSH configuration you trust.

## Privacy and removal

No keys, personal SSH config, actual inventory, telemetry, screenshots or background images are bundled. Your config, keys, state and backups belong **outside the checkout**. Local state records private aliases, favourites, recency and preferences; backups can contain a full SSH config. Never publish them. See [docs/PRIVACY.md](docs/PRIVACY.md).

From the extracted folder, run `bash uninstall.sh` as your normal Linux user. It removes only the managed application, wrapper and managed shell blocks. SSH config, keys, state, backups and an already running agent are preserved. It does not undo remote public-key installation or remove dependency packages.

## Contributing and publishing

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and the isolated-folder checklist in [docs/PUBLISHING.md](docs/PUBLISHING.md). Release tooling and smoke tests must be present and pass before publishing; the publishing guide lists the required checks. Nothing initializes Git or publishes automatically.

### Validation baseline

Validated on Ubuntu 24.04 / WSL2 with Python 3.12 and fzf 0.44.1: **95 unit tests**, real terminal checks for all themes and monochrome, loopback-only preview checks, and actual shell install/uninstall in temporary homes. Tests use only synthetic configuration and never test a user's real servers. The Windows launcher was syntax-checked; a fresh Windows/WSL installation and privileged dependency installation were not executed as part of this validation.

MIT licensed; see [LICENSE](LICENSE). Copyright (c) 2026 SSHELP contributors.