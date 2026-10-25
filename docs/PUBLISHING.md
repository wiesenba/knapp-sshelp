# Publishing SSHELP 2.2.0 safely

This is a **manual** checklist for an existing empty GitHub repository. No SSHELP script initializes Git, pushes commits, creates releases or uploads files. Work only in this isolated SSHELP folder, never in its parent or your home directory. Do not point a Git client at a broader folder.

## 1. Review the complete isolated folder

- Confirm that the folder contains only public SSHELP source, generic fixtures, tests, tooling, documentation and workflow configuration. Check hidden files too. Do not copy from a live installation, SSH directory, backup or actual inventory.
- Read every proposed public file, including comments, examples, scripts, test fixtures and this documentation. Permit only the approved synthetic aliases, project codes and addresses described in [../CONTRIBUTING.md](../CONTRIBUTING.md).
- Do not add company names, real people/account names, personal paths, internal identifiers, actual hostnames/inventory, keys, SSH config, secrets, state, databases, backups, screenshots, recordings, images or logs. Keep private configuration outside this checkout.
- Verify version 2.2.0, the MIT license in [../LICENSE](../LICENSE), and exactly `Copyright (c) 2026 SSHELP contributors`. Do not add organizational code attribution.
- Review [../.gitignore](../.gitignore), but do not treat it as a privacy guarantee: ignored files can already be tracked or force-added, and an archive tool can ignore Git rules entirely.

## 2. Require all release tooling

The public package uses flat Python modules under [../app/](../app/), not an import package. The entry points must remain [../install.sh](../install.sh), [../tools/install.py](../tools/install.py), [../Start.cmd](../Start.cmd) and [../Install-Windows.ps1](../Install-Windows.ps1).

**Release blockers:** the required `python3 tests/ui_smoke.py`, `python3 tests/network_smoke.py`, `python3 tools/check_release.py` and `python3 tools/build_release.py` commands must exist and pass. If any is missing, stop; do not bypass the workflow or claim the release was audited. The workflow deliberately includes all required commands.

The auditor must enforce an **exact file allowlist**, reject unexpected files/unsafe release inputs, and check for secrets, disallowed IPs and identifying paths. Broadly archiving the directory or relying on ignore patterns is not acceptable. Its optional `--deny-term TEXT` argument adds a private local check. `SSHELP_AUDIT_FORBIDDEN` supplies additional comma-separated blocked terms from the local environment.

Set real blocked terms only locally, outside source control. Never put actual blocked terms, account names or hostnames into the repository, example commands, tests, a committed denylist or CI configuration. Never commit environment files used to supply these values. Command-line values can be retained in shell history; use a private local environment for sensitive lists, and do not echo it or enable shell tracing. Review auditor output before sharing; it may itself reveal the term or location that caused a finding.

The builder must run the audit first, then generate a **reproducible, allowlist-only ZIP** and SHA256SUMS. It must not initialize Git or publish. Reproducibility means identical approved inputs give identical archive bytes, including controlled entry order and metadata. A checksum detects changed bytes; it does not prove the content is safe or authenticate a publisher by itself.

## 3. Run local checks

In Linux, from this isolated repository root, with OpenSSH client, fzf 0.44+ and iputils-ping installed, set `PYTHONDONTWRITEBYTECODE=1` and run all of:

1. `bash -n install.sh uninstall.sh app/agent.sh`
2. `python3 -m unittest discover -s tests -p 'test_*.py' -v`
3. `python3 tests/ui_smoke.py`
4. `python3 tests/network_smoke.py`
5. `python3 tools/check_release.py`
6. `python3 tools/build_release.py --output-dir ../sshelp-release`

Python 3.12 matches [../.github/workflows/check.yml](../.github/workflows/check.yml), which runs on Ubuntu 24.04. CI installs its OS dependencies and needs no repository secrets. Apart from dependency acquisition and CI action setup, test networking must be mocked or loopback-only, never actual hosts or documentation IPs.

Use your private `--deny-term TEXT` or comma-separated `SSHELP_AUDIT_FORBIDDEN` checks in addition to the public audit. They are extra checks, not substitutions for manual review. **No automated audit proves the absence of every sensitive datum.** Stop on every failure and inspect audit output locally before quoting any of it.

Do not run the real installer merely to validate a release. Installer tests should use temporary homes and mocks. Do not exercise a real host, config, key or agent as a release test.

## 4. Review the generated artifact

The output directory above is a dedicated sibling release directory, not a copy of a parent/home directory. This is the only intended build output outside the checkout; it is not a place to collect private material.

- Inspect the ZIP entry list and extracted contents against the exact release allowlist. Review the generated artifact, not only the source tree.
- Verify its checksum against generated SHA256SUMS. Rebuild unchanged inputs and compare archive bytes/checksums when validating reproducibility.
- Confirm there are no local audit files, environment values, absolute personal paths, keys, inventory, SSH config, state, databases, backups, caches, images, screenshots or logs.
- Recheck any packaging/version change with the complete test, audit and build sequence. Never hand-add a file to the generated ZIP afterward.

## 5. First push to the existing empty repository

Use a Git client rooted **only in the reviewed isolated folder**. Do not initialize or stage the parent/home directory. Repository setup, staging and pushing are manual actions; no release command performs them.

1. Verify the remote points to the intended existing empty GitHub repository. Never embed a credential or token in a remote URL or a committed file.
2. Stage only reviewed public files. Use explicit selections; never add screenshots, logs, config, state, backups, real keys or inventory. Do not force-add ignored files.
3. Before the first commit and push, run **`git diff --cached`** and review every staged line. Also inspect the staged file list, including new, hidden and binary files; a textual diff alone cannot validate binary contents. Re-run tests and the audit after any change.
4. Commit only the reviewed public tree and manually push it. Confirm required CI checks pass; a workflow run is not a substitute for a local private-term check.
5. If the local repository is not new, inspect **all history** before any push. Ignore rules cannot remove already tracked data or clean earlier commits, tags, branches or remote copies. Do not push a tree with suspect history into an empty remote; use a separately reviewed clean public history instead.

If a secret was exposed, stop publication, rotate/revoke it as appropriate and follow [../SECURITY.md](../SECURITY.md). Merely deleting the current file, adding an ignore rule or overwriting an archive does not undo disclosure.

## 6. Optional release upload

After all checks and manual reviews pass, manually create the intended versioned release. **Upload only the generated, audited, allowlisted ZIP as the release asset.** Use generated SHA256SUMS locally to verify the upload; a digest may be quoted in release text without copying private audit output. Do not upload the whole working folder, a manually assembled archive, logs, screenshots, config, databases, state or backups. Review any hosting-generated source archives too: they reflect committed history/tree content, not your local ignore rules.

No automatic publishing, Git initialization or credentials are needed for the supplied check workflow.