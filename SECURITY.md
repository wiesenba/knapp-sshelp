# Security policy

## Scope

SSHELP 2.2.0 is a local SSH launcher, not an SSH server, credential vault or network isolation boundary. Security fixes target the latest public release; older versions have no separate maintenance commitment. No response-time guarantee is offered.

Only connect to systems you are authorized to use. Verify server fingerprints through a trusted channel. SSHELP delegates authentication and host-key verification to OpenSSH and does not repair or bypass an untrusted SSH configuration.

## Report privately

Use this repository's **Security → Report a vulnerability** option if available. Do not put a suspected vulnerability or sensitive evidence into a public issue. If private reporting is unavailable, ask in a generic public issue for a private reporting channel **without** disclosing the vulnerability or any private data; wait for that channel before sending details. No contact address or response availability is implied.

Provide the version, a short impact description and a minimal reproduction using only Demo-Dev, Demo-Live, Lab-Sbx, EX00001, EX00002, `example.invalid`, `example.com`, loopback or the documentation addresses `192.0.2.10`, `192.0.2.11` and `198.51.100.20`. Describe synthetic configuration in text; do not attach a real config.

Do not send private or public keys, passwords, passphrases, tokens, actual host inventories, account names, personal paths, SSH config, state databases, backups, screenshots, terminal recordings or unreviewed logs. Even a public key or diagnostic transcript can identify its owner or infrastructure. Audit output and issue reports require manual review before sharing.

## Important boundaries

- Installation is user-level and refuses unmanaged application/wrapper conflicts. Optional dependency installation explicitly invokes sudo apt; do not run the entire installer as root.
- Setup preserves existing config and identities. Optional `ssh-keygen` and `ssh-add` prompts belong directly to OpenSSH; SSHELP does not store passphrases. A running agent can retain loaded keys in memory.
- The add-host wizard writes only after review and confirmation, with a private config backup. It does not invent forwarding rules. Treat backups as sensitive as the original config.
- SSH configuration is trusted executable policy: `Match exec` may execute local commands even during `ssh -G`. User-configured proxies and commands remain under OpenSSH's control.
- Preview networking is opt-in for new state and uses direct ICMP/TCP checks without authentication. Explicit connections, copy-key and diagnostics contact servers. A TCP check can still appear in server or firewall logs.
- Uninstall preserves config, keys, private state/backups and the running agent. It does not revoke remotely installed public keys.

See [docs/PRIVACY.md](docs/PRIVACY.md) for local data handling and [docs/PUBLISHING.md](docs/PUBLISHING.md) for publication review. Ignore rules and automated audits are defense in depth, not proof that every sensitive datum is absent. Ignore rules do not remove already tracked files or clean Git history.