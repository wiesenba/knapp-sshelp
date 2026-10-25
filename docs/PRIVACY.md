# Privacy and local data

SSHELP 2.2.0 uses local files and the Python standard library. There is no telemetry, analytics, account service, automatic inventory upload or automatic publishing. The package contains no personal keys, personal SSH config, secret examples, actual inventory, screenshots or background images. The example configuration is documentation only and is never activated by setup.

## What lives where

These are generic Linux locations, not paths to copy into this repository.

| Location | Purpose and sensitivity |
| --- | --- |
| `${XDG_DATA_HOME:-~/.local/share}/sshelp` | Managed application modules and installation marker. |
| `~/.local/bin/sshelp` | Managed wrapper; contains the local installed application path. |
| `~/.ssh/config` | Your private host inventory and SSH policy. Includes may reference additional private files. `SSHELP_CONFIG` can select a different config you manage yourself. |
| `~/.ssh/` | Your keys, known-host data and other SSH files. Private and public keys can both be identifying; publish neither. |
| `${XDG_STATE_HOME:-~/.local/state}/sshelp/state.sqlite3` | Host aliases, favourite flags, opened timestamps/counts and UI preferences, including theme and network-preview choice. Database sidecars are private too. |
| State directory's config backups | Copies of the full prior SSH config made by confirmed add-host edits. These contain private inventory. |
| State directory's install backups | Previous managed application files, retained during upgrades. Treat all local backups as private. |
| State directory's session files | Temporary view/filter data while the dashboard runs; a lock file coordinates host edits. |
| State directory's optional agent files | Shared socket and lock; keys are not written there by SSHELP. The separate agent can retain loaded keys in memory. |
| `~/.bashrc` and `~/.profile` | Bounded PATH blocks unless `--no-shell`; optional agent setup adds a bounded Bash block. Such blocks can contain your local installation path. |

Keep all of these personal files **outside the checkout**, including when choosing XDG directories or a custom SSH config. State and backup directories must never become release inputs. Local output such as `--list --json` can include aliases, addresses, account names, config source paths and other identifying metadata. Treat it as private inventory, not a harmless support attachment.

## Installation, setup and editing

Installation manages the application and wrapper and refuses unmanaged conflicts. It does not change WSL networking, Windows settings, VPN configuration, global SSH defaults, existing host entries or keys. Dependency installation uses sudo apt only after a prompt or explicit `--install-deps`; package installation contacts package mirrors and is separate from runtime behavior. `--no-setup` skips setup; `--no-shell` skips installer PATH edits. Enabling an agent later is a separate, explicit shell change.

Setup creates an empty default SSH config only when absent; it does not import examples or replace existing config. Optional key creation invokes `ssh-keygen` directly and does not overwrite the existing default identity. Passwords and passphrases are typed directly into the terminal/OpenSSH prompts and are not echoed or stored by SSHELP. A sudo prompt expects your Linux user's password, not a Windows password.

The optional shared Bash agent preserves working inherited sockets and never automatically loads a key. Loading a key requires an explicit `ssh-add` action. Agent behavior and memory retention belong to OpenSSH; uninstall does not stop an already running agent.

The `sshelp -a` wizard shows a minimal entry and requires confirmation before appending it to your private SSH config. It first makes a private backup. It does not invent X11, agent or port forwarding settings or rewrite existing host entries.

## What can contact a server

| Action | Behavior |
| --- | --- |
| List, local doctor, static details | No SSHELP network probes or login. Doctor checks local executables/config presence and local agent availability. Details use `ssh -G`. |
| Dashboard, network preview off | Local inventory and effective-config display; no SSHELP probes. |
| Alt-N or `--network on` | Remembers opt-in. The selected visible preview performs ICMP and TCP checks against the effective address/port. No authentication or remote command. |
| Enter or an explicit destination | OpenSSH connection using your configuration; authentication or a remote command may follow. |
| Copy-key | `ssh-copy-id` authenticates to the selected destination and can modify its authorized public keys. |
| Diagnostics | Bounded SSH authentication/remote `true` attempt with X11, agent and port forwarding disabled. Verbose output may reveal private data. |

**Trusted SSH configuration remains important:** `ssh -G` may run user-authored `Match exec` commands. Those commands can have their own side effects, including network activity. Configured transports and proxy commands are also under the user's/OpenSSH's control; SSHELP is not a sandbox.

New state defaults network previews to off; the chosen setting persists. Enabled checks wait briefly after selection, refresh periodically, and cancel/reap helpers on selection change, hiding the preview or exiting. TCP checks may resolve DNS and establish a direct connection; ICMP and TCP can be visible in resolver, server or firewall logs. They do not test proxy/jump routing. ICMP filtering is not proof of an outage, and an open TCP port does not prove successful SSH login.

## Sharing and removal

- Never add SSH config, either type of key, aliases/inventory, databases, state, backups, environment secrets, audit denylists, screenshots, recordings or logs to the repository or a release.
- Use only synthetic placeholders from [../examples/ssh_config.example](../examples/ssh_config.example) when describing a problem. Write a minimal sanitized description rather than attaching real artifacts.
- Manually review audit output and every issue/report before sharing. Automated checks cannot prove that every sensitive datum is absent.
- [../.gitignore](../.gitignore) is only defense in depth. It does not protect already tracked files, unignore a past leak or remove Git history. Keep private configs outside the checkout even if a pattern would ignore them.

`bash uninstall.sh` removes only the managed application, wrapper and managed shell blocks. It preserves config, keys, preferences/history, state, backups and the running agent. It does not revoke remote authorized keys or uninstall packages. Review retained local data yourself if you no longer need it; do not delete keys or config merely to remove this launcher.

Use [../SECURITY.md](../SECURITY.md) for sensitive reports and [PUBLISHING.md](PUBLISHING.md) before publishing anything.