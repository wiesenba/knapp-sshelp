#!/usr/bin/env python3
"""sshelp 2: a local SSH cockpit. SSH remains the authority for connections."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import threading
from dataclasses import asdict
from pathlib import Path

from catalog import ALIAS, Catalog, ENVIRONMENTS, Host, safe_text
from state import Store
from themes import NAMES as THEME_NAMES, PALETTES, compact, fzf_colors, pointer
from network import Probe, network_lines, DEBOUNCE, REFRESH, FRAME_INTERVAL, valid_destination

VERSION = "2.2.0"
APP = Path(__file__).resolve()


class Context:
    def __init__(self):
        self.home = Path.home()
        self.config = Path(os.environ.get("SSHELP_CONFIG", str(self.home / ".ssh/config"))).expanduser().absolute()
        self.directory = Path(os.environ.get("XDG_STATE_HOME", str(self.home / ".local/state"))) / "sshelp"
        self.color = "NO_COLOR" not in os.environ and os.environ.get("SSHELP_COLOR") != "0"
        self.store = Store(self.directory)
        self.theme = self.store.preference("theme", "minimal")
        if self.theme not in THEME_NAMES:
            self.theme = "minimal"
        self.network = self.store.preference("network", "off") == "on"
        self.catalog = Catalog(self.config, self.home)

    def tint(self, text: str, color: str) -> str:
        return paint(text, color, self.color, self.theme)


def paint(text: str, color: str, enabled: bool = True, theme: str = "midnight") -> str:
    text = "".join(c if c.isprintable() else " " for c in text)
    code = PALETTES[theme][color]
    return f"\033[{code}m{text}\033[0m" if enabled and code else text


def age(timestamp: float | None) -> str:
    if timestamp is None:
        return "never opened"
    elapsed = max(0, time.time() - timestamp)
    if elapsed < 60:
        return "just now"
    if elapsed < 3600:
        return f"{int(elapsed // 60)}m ago"
    if elapsed < 86400:
        return f"{int(elapsed // 3600)}h ago"
    return f"{int(elapsed // 86400)}d ago"


def selected_hosts(ctx: Context, settings: dict) -> list[Host]:
    hosts = ctx.catalog.filtered(settings.get("env", "all"), settings.get("project", ""))
    data = ctx.store.metadata()
    view = settings.get("view", "all")
    if view == "favourites":
        hosts = [host for host in hosts if data.get(host.alias.casefold(), {}).get("favourite")]
    elif view == "recent":
        hosts = [host for host in hosts if data.get(host.alias.casefold(), {}).get("opened")]
        hosts.sort(key=lambda host: -(data[host.alias.casefold()]["opened"]))
    return hosts


def rows(ctx: Context, settings: dict) -> str:
    hosts = selected_hosts(ctx, settings)
    metadata = ctx.store.metadata()
    total = len(ctx.catalog.hosts)
    counts = collections.Counter(host.environment for host in ctx.catalog.hosts.values())
    p = ctx.tint
    project = settings.get("project", "") or "ALL PROJECTS"
    headings = [
        p(f"  sshelp / {ctx.theme}" if compact(ctx.theme) else "  SSHELP / MIDNIGHT", "cyan"),
        p(f"  {total} hosts" if compact(ctx.theme) else f"  {total} hosts   ·   {counts['live']} live   {counts['dev']} dev   {counts['sbx']} sandbox", "muted"),
        p(f"  {settings.get('view', 'all').upper()}   /   {settings.get('env', 'all').upper()}   /   {project}", "white"),
        p(f"  {'STAR':4} {'HOST':32} {'ENV':7} PROJECT", "muted"),
    ]
    result = ["_\t" + heading for heading in headings]
    for host in hosts:
        record = metadata.get(host.alias.casefold(), {})
        star = "★" if record.get("favourite") else "·"
        env = {"sbx": "SBX", "other": "OTHER"}.get(host.environment, host.environment.upper())
        alias = host.alias
        display = (p(f"  {star:4} ", "cyan" if record.get("favourite") else "muted")
               # Let fzf apply the focus colour/attributes to the entire alias.
               # An embedded ANSI alias colour would mask its current-row style.
               + f"{alias:32} "
               + p(f"{env:7} ", host.environment)
               + p(host.project or "—", "muted"))
        project_name = safe_text(host.project_name)
        result.append(f"{host.alias}\t{display}\t{project_name}")
    return "\n".join(result) + "\n"


def effective(ctx: Context, alias: str) -> dict[str, list[str]]:
    """-G does not connect; SSH still evaluates any user-authored Match exec rules."""
    try:
        result = subprocess.run(["ssh", "-G", "-F", str(ctx.config), alias], capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    options: dict[str, list[str]] = {}
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            key, _, value = line.partition(" ")
            options.setdefault(key, []).append(safe_text(value))
    return options


class PreviewCancelled(Exception):
    pass


def briefing(ctx: Context, host: Host, opts: dict, compact: bool) -> list[str]:
    data = ctx.store.metadata().get(host.alias.casefold(), {})
    def option(key: str, default: str = "—") -> str:
        return (opts.get(key) or [host.declared.get(key, default)])[0]
    lines = [ctx.tint("HOST BRIEFING", "cyan"), ctx.tint(f"{pointer(ctx.color)} {host.alias}", "white"), ""]
    for label, value in (
        ("Environment", host.environment.upper()),
        ("Project", host.project or "unassigned"),
        ("Address", option("hostname", host.alias)),
        ("Username", option("user")), ("Port", option("port", "22")),
        ("Favourite", "yes ★" if data.get("favourite") else "no"),
        ("Last opened", age(data.get("opened"))),
    ):
        lines.append(ctx.tint(f"{label:12}", "muted") + safe_text(value))
    if not compact:
        lines += ["", ctx.tint("CONNECTION POLICY", "cyan"),
                  f"Agent forwarding  {option('forwardagent', 'no')}",
                  f"X11 forwarding    {option('forwardx11', 'no')}",
                  f"Connect timeout   {option('connecttimeout', 'default')}",
                  f"Keepalive         {option('serveraliveinterval', '0')}s"]
    proxied = bool((opts.get("proxycommand") or opts.get("proxyjump")) and
                   (opts.get("proxycommand") or opts.get("proxyjump"))[0] != "none")
    lines.append(ctx.tint("Transport   ", "muted") + ("configured proxy / jump" if proxied else "direct"))
    if host.environment == "live":
        lines += ["", ctx.tint("LIVE — check the host before Enter.", "live")]
    return lines


def preview(ctx: Context, alias: str, live: bool = False, session: Path | None = None) -> None:
    host = ctx.catalog.lookup(alias)
    if host is None:
        print("No host selected.\n\nType a name, project number or environment.\nCTRL-A shows all hosts.")
        return
    if live and session is not None and not session_read(session).get('preview_visible', True):
        return
    opts = effective(ctx, host.alias)
    lines = briefing(ctx, host, opts, compact=live and compact(ctx.theme))
    if not live or not ctx.network:
        print("\n".join(lines))
        print(ctx.tint("\nNetwork off · Alt-N toggles checks" if live else "\nDetails only · no network probes", "muted"))
        return
    address = (opts.get("hostname") or [""])[0]
    port_text = (opts.get("port") or ["22"])[0]
    port = int(port_text) if port_text.isascii() and port_text.isdecimal() else 0
    if not valid_destination(address, port):
        print("\n".join(lines + ["", "NETWORK · no valid effective address/port"]))
        return
    previous = signal.getsignal(signal.SIGTERM)
    def cancel(_signal, _frame):
        raise PreviewCancelled()
    signal.signal(signal.SIGTERM, cancel)
    pause = threading.Event()
    probe = None
    def draw(extra: list[str]) -> None:
        # fzf understands clear-screen as a streaming preview replacement.
        sys.stdout.write("\033[2J\033[H" + "\n".join(lines + [""] + extra) + "\n")
        sys.stdout.flush()
    try:
        draw([ctx.tint("NETWORK · waiting for selection…", "muted")])
        pause.wait(DEBOUNCE)  # Scrolling past a host should not send probes.
        frame = 0
        while True:
            if session is not None and not session_read(session).get('preview_visible', True):
                return
            if probe is None or time.monotonic() - probe.started >= REFRESH:
                if probe is not None:
                    probe.close()
                probe = Probe(address, port)
            results = probe.snapshot()
            checking = any(result["status"] == "checking" for result in results.values())
            glyph = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[frame % 10] if checking else "◌○◉○"[(frame // 2) % 4]
            remaining = max(0, int(REFRESH - (time.monotonic() - probe.started)))
            label = "checking" if checking else f"refresh {remaining}s"
            extra = [ctx.tint(f"NETWORK  {glyph}  {label}", "cyan")]
            extra += [ctx.tint(text, color) for text, color in network_lines(results, port)]
            extra += [ctx.tint("Focused host only · Alt-N off", "muted")]
            if any(key in opts and opts[key][0] != "none" for key in ("proxyjump", "proxycommand")):
                extra += [ctx.tint("Direct path only · proxy is not tested", "muted")]
            if results["icmp"]["status"] != "up" and results["tcp"]["status"] == "up":
                extra += [ctx.tint("SSH port reachable despite ping result", "dev")]
            draw(extra)
            frame += 1
            pause.wait(FRAME_INTERVAL)
    finally:
        if probe is not None:
            probe.close()
        signal.signal(signal.SIGTERM, previous)


def session_read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def session_write(path: Path, settings: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix='.view-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(settings, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def session_action(ctx: Context, path: Path, action: str, alias: str = "") -> None:
    settings = session_read(path)
    if action == "star":
        host = ctx.catalog.lookup(alias)
        if host:
            ctx.store.toggle(host.alias)
        return
    if action == "env":
        settings["env"] = ENVIRONMENTS[(ENVIRONMENTS.index(settings.get("env", "all")) + 1) % len(ENVIRONMENTS)]
    elif action == "favourites":
        settings["view"] = "all" if settings.get("view") == "favourites" else "favourites"
    elif action == "recent":
        settings["view"] = "all" if settings.get("view") == "recent" else "recent"
    elif action == "all":
        settings.update(env="all", view="all", project="")
    elif action == 'preview':
        settings['preview_visible'] = not settings.get('preview_visible', True)
    else:
        raise ValueError("Unknown session action")
    session_write(path, settings)


def choose(ctx: Context, path: Path, query: str) -> tuple[str, str, str]:
    if not shutil.which("fzf"):
        raise ValueError("fzf is not installed; use sshelp --plain or install fzf through your approved package manager.")
    settings = session_read(path)
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(APP))}"
    session = shlex.quote(str(path))
    reload_cmd = f"{command} __rows {session}"
    bindings = []
    for key, action in (("ctrl-s", "star"), ("ctrl-f", "favourites"), ("ctrl-r", "recent"), ("ctrl-e", "env"), ("ctrl-a", "all")):
        target = " {1}" if action == "star" else ""
        refresh = "+refresh-preview" if action == "star" else ""
        bindings.append(f"{key}:execute-silent({command} __action {session} {action}{target})+reload({reload_cmd}){refresh}")
    bindings.extend((f"alt-p:execute-silent({command} __action {session} preview)+toggle-preview+refresh-preview", "ctrl-u:clear-query"))
    width, height = shutil.get_terminal_size((120, 32))
    preview_window = "right,42%,border-left,wrap" if width >= 116 else "down,40%,border-top,wrap"
    if 'preview_visible' not in settings:
        settings['preview_visible'] = width >= 88 and height >= 27
        session_write(path, settings)
    if not settings['preview_visible']:
        preview_window += ",hidden"
    minimal = compact(ctx.theme)
    header = ("enter connect  ^S star  ^F fav  ^R recent  ^P project\n^E env  ^T theme  alt-N network  alt-P details  esc exit" if minimal else
              "ENTER connect   CTRL-S star   CTRL-F favourites   CTRL-R recent\nCTRL-P project   CTRL-E env   CTRL-D diagnose\nCTRL-T theme   ALT-N network   CTRL-A reset   CTRL-N add   ESC exit")
    argv = [
        "fzf", "--ansi", "--delimiter=\t", "--with-nth=2,3", "--header-lines=4",
        "--layout=reverse", "--height=90%", "--min-height=16", "--border=" + ("horizontal" if minimal else "rounded"), "--padding=" + ("0,1" if minimal else "1,2"),
        "--border-label= " + ("sshelp" if ctx.theme == "minimal" else f"SSHELP / {ctx.theme.upper()}") + " ", "--border-label-pos=3", "--info=inline",
        "--prompt=  FIND  › ", "--pointer=" + pointer(ctx.color), "--marker=★", "--query=" + query,
        "--print-query", "--expect=enter,ctrl-d,ctrl-n,ctrl-p,ctrl-t,alt-n", "--bind=" + ",".join(bindings),
        "--header=" + header,
        "--preview=" + command + f" __preview {{1}} --live {session}", "--preview-window=" + preview_window,
        "--preview-label= BRIEFING ", "--preview-label-pos=2",
        "--color=" + fzf_colors(ctx.theme, ctx.color),
    ]
    env = dict(os.environ, FZF_DEFAULT_OPTS="", FZF_DEFAULT_COMMAND="")
    # Newer fzf can read an options file; do not inherit hidden UI/key overrides.
    env.pop("FZF_DEFAULT_OPTS_FILE", None)
    process = subprocess.run(argv, input=rows(ctx, settings), stdout=subprocess.PIPE, text=True, env=env)
    if process.returncode in (1, 130):
        return query, "", ""
    if process.returncode != 0:
        raise ValueError(f"fzf exited with status {process.returncode}; use sshelp --plain as a fallback.")
    lines = process.stdout.splitlines()
    result_query = lines[0] if lines else query
    key = lines[1] if len(lines) > 1 else "enter"
    alias = lines[2].split("\t", 1)[0] if len(lines) > 2 else ""
    return result_query, key, alias


def choose_project(ctx: Context) -> str | None:
    projects: dict[str, list[Host]] = {}
    for host in ctx.catalog.hosts.values():
        if host.project:
            projects.setdefault(host.project, []).append(host)
    choices = ["*\tALL PROJECTS"]
    for project, hosts in sorted(projects.items()):
        names = ", ".join(dict.fromkeys(host.project_name for host in hosts if host.project_name))
        choices.append(f"{project}\t{project}   {len(hosts):2} hosts   {safe_text(names)}")
    argv = ["fzf", "--delimiter=\t", "--with-nth=2", "--layout=reverse", "--height=60%",
            "--border=" + ("horizontal" if compact(ctx.theme) else "rounded"), "--border-label= PROJECT SWITCHER ", "--padding=1,2",
            "--pointer=" + pointer(ctx.color),
            "--prompt=  PROJECT  › ", "--header=ENTER choose project   ESC back   /   metadata from SSH comments",
            "--color=" + fzf_colors(ctx.theme, ctx.color)]
    env = dict(os.environ, FZF_DEFAULT_OPTS="", FZF_DEFAULT_COMMAND="")
    env.pop("FZF_DEFAULT_OPTS_FILE", None)
    result = subprocess.run(argv, input="\n".join(choices) + "\n", stdout=subprocess.PIPE, text=True, env=env)
    if result.returncode in (1, 130):
        return None
    if result.returncode != 0:
        raise ValueError("Project switcher failed; use sshelp --project PROJECT as a fallback.")
    project = result.stdout.strip().split("\t", 1)[0]
    return "" if project == "*" else project


def run_connection(ctx: Context, alias: str, arguments: list[str], diagnostic: bool = False) -> int:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@:+-]*", alias):
        raise ValueError("Invalid SSH destination.")
    ssh_args = ["ssh", "-F", str(ctx.config)]
    if diagnostic:
        ssh_args = ["timeout", "--foreground", "--kill-after=2", "25"] + ssh_args
        ssh_args += ["-vv", "-n", "-T", "-x", "-a", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", alias, "true"]
        print("DIAGNOSTIC / no forwarding / configured transport / 25s limit", flush=True)
    else:
        ssh_args += [alias] + arguments
        ctx.store.opened(alias)
        host = ctx.catalog.lookup(alias)
        env_label = host.environment.upper() if host else "SSH"
        print(paint(f"  CONNECT  /  {alias}  /  {env_label}", "cyan", ctx.color and sys.stdout.isatty(), ctx.theme), flush=True)
    status = subprocess.call(ssh_args)
    if status == 255:
        print(f"SSH could not complete. Diagnostic: sshelp -d {alias}", file=sys.stderr)
        print("VPN, network settings and server sessions were not changed. Commands are never replayed.", file=sys.stderr)
    return status


def add_host(ctx: Context) -> int:
    if not sys.stdin.isatty():
        raise ValueError("The add-host wizard needs an interactive terminal.")
    print("\n  ADD HOST / a minimal entry, no network or forwarding changes\n")
    alias = input("Alias (for example Demo-Dev): ").strip()
    if not ALIAS.fullmatch(alias) or ctx.catalog.lookup(alias):
        raise ValueError("Use a unique alias containing letters, digits, dots, underscores or hyphens.")
    address = input("Hostname or IP: ").strip()
    username = input("SSH username: ").strip()
    port = input("SSH port [22]: ").strip() or "22"
    project = input("Project number (optional, e.g. EX00001): ").strip().upper()
    name = input("Project name (optional): ").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]*", address):
        raise ValueError("Invalid hostname or IP address.")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", username):
        raise ValueError("Invalid SSH username.")
    if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    if project and not PROJECT_INPUT.fullmatch(project):
        raise ValueError("Project number must have two letters and five digits.")
    if any(not c.isprintable() for c in name) or len(name) > 120:
        raise ValueError("Use a short, single-line project name.")
    description = " - ".join(filter(None, (project, name, environment_label(alias)))) or alias
    block = f"\n\n## {description}\nHost {alias}\n    HostName {address}\n    User {username}\n    Port {int(port)}\n"
    print(block)
    if input("Append this entry and create a backup? [y/N]: ").strip().casefold() not in ("y", "yes"):
        print("Cancelled. Nothing changed.")
        return 0
    config = ctx.config.resolve()
    if not config.is_file():
        raise ValueError("SSH configuration must already exist; run sshelp --setup first.")
    lock_path = ctx.directory / "config-write.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if Catalog(ctx.config, ctx.home).lookup(alias):
            raise ValueError("The alias now exists; configuration was not changed.")
        original = config.read_bytes()
        if original and not original.endswith(b"\n"):
            block = "\n" + block
        backup_dir = ctx.directory / "config-backups"
        backup_dir.mkdir(mode=0o700, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = backup_dir / f"ssh-config-{stamp}"
        backup.write_bytes(original)
        os.chmod(backup, 0o600)
        fd, temp = tempfile.mkstemp(prefix=".sshelp-", dir=config.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(original + block.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            if config.read_bytes() != original:
                raise ValueError("The SSH configuration changed while adding the host; no update was applied.")
            os.replace(temp, config)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    print(f"Added {alias}. Backup: {backup}")
    print("Other SSH configurations and existing host settings were not changed.")
    return 0


PROJECT_INPUT = re.compile(r"[A-Z]{2}\d{5}")


def environment_label(alias: str) -> str:
    from catalog import environment
    return {"live": "Live", "dev": "Dev", "sbx": "Sbx", "other": ""}[environment(alias)]


def copy_key(ctx: Context, alias: str) -> int:
    if not ALIAS.fullmatch(alias):
        raise ValueError("Invalid host alias.")
    key = next((path for path in (ctx.home / ".ssh/id_ed25519.pub", ctx.home / ".ssh/id_rsa.pub") if path.is_file()), None)
    if key is None:
        raise ValueError("No default public key was found. Create your own SSH key before using -s.")
    print(f"Copying your PUBLIC key to {alias}; authentication/approval is handled by ssh-copy-id.", flush=True)
    return subprocess.call(["ssh-copy-id", "-i", str(key), "-F", str(ctx.config), alias])


def plain_menu(ctx: Context, settings: dict, query: str) -> int:
    hosts = selected_hosts(ctx, settings)
    if query:
        hosts = [host for host in hosts if all(term in host.search_text() for term in query.casefold().split())]
    print("\nSSHELP / HOST CONTROL\n")
    metadata = ctx.store.metadata()
    for i, host in enumerate(hosts, 1):
        star = "*" if metadata.get(host.alias.casefold(), {}).get("favourite") else " "
        print(f"[{i:02}] {star} {host.alias:34} {host.environment.upper():5} {host.project}")
    if not hosts:
        print("No hosts match. Use sshelp --list or clear the filters.")
        return 0
    try:
        choice = input("\nHost number (or q to exit): ").strip()
    except EOFError:
        return 0
    if choice.casefold() in ("", "q", "quit"):
        return 0
    if not re.fullmatch(r"[0-9]{1,6}", choice) or not 1 <= int(choice) <= len(hosts):
        raise ValueError("Invalid host number.")
    return run_connection(ctx, hosts[int(choice) - 1].alias, [])


def dashboard(ctx: Context, settings: dict, query: str) -> int:
    if not ctx.catalog.hosts:
        print("No literal host aliases found. Add one with sshelp -a.")
        return 0
    with tempfile.TemporaryDirectory(prefix="session-", dir=ctx.directory) as temporary:
        path = Path(temporary) / "view.json"
        path.write_text(json.dumps(settings), encoding="utf-8")
        while True:
            ctx.catalog = Catalog(ctx.config, ctx.home)
            query, key, alias = choose(ctx, path, query)
            if not key:
                return 0
            if key == "ctrl-t":
                ctx.theme = THEME_NAMES[(THEME_NAMES.index(ctx.theme) + 1) % len(THEME_NAMES)]
                ctx.store.set_preference("theme", ctx.theme)
                continue
            if key == "alt-n":
                ctx.network = not ctx.network
                ctx.store.set_preference("network", "on" if ctx.network else "off")
                continue
            if key == "ctrl-p":
                project = choose_project(ctx)
                if project is not None:
                    settings = session_read(path)
                    settings["project"] = project
                    path.write_text(json.dumps(settings), encoding="utf-8")
                    query = ""
                continue
            if key == "ctrl-n":
                try:
                    add_host(ctx)
                except ValueError as error:
                    print(str(error), file=sys.stderr)
                input("Press Enter to return to the cockpit…")
                continue
            host = ctx.catalog.lookup(alias)
            if host is None:
                continue
            status = run_connection(ctx, host.alias, [], diagnostic=key == "ctrl-d")
            if key == "ctrl-d" or status != 0:
                input("Press Enter to return to the cockpit…")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="SSHELP — a minimal, transparent SSH host launcher.",
        epilog="Examples: sshelp | sshelp --env dev --project EX00001 | sshelp -q demo | sshelp Demo-Live | sshelp --star Demo-Dev",
    )
    result.add_argument("--version", action="version", version=f"sshelp {VERSION}")
    result.add_argument("--theme", choices=THEME_NAMES, help="choose and remember a theme (default: minimal)")
    result.add_argument("--network", choices=("on", "off"), help="remember focused preview checks on/off (default: off)")
    result.add_argument("--no-network", dest="network", action="store_const", const="off", help="disable preview network checks")
    result.add_argument("-q", "--search", default="", help="initial fuzzy search (plain mode uses words)")
    result.add_argument("--env", choices=ENVIRONMENTS, default="all", help="filter by environment")
    result.add_argument("--project", default="", help="filter project number/name or host name")
    view = result.add_mutually_exclusive_group()
    view.add_argument("-f", "--favourites", "--favorites", action="store_true", help="show starred hosts")
    view.add_argument("-r", "--recent", action="store_true", help="show recently opened hosts")
    action = result.add_mutually_exclusive_group()
    action.add_argument("--setup", action="store_true", help="set up the launcher interactively")
    action.add_argument("--doctor", action="store_true", help="check launcher prerequisites and configuration")
    action.add_argument("--star", metavar="HOST", help="toggle a favourite locally")
    action.add_argument("--list", action="store_true", help="print filtered hosts without a dashboard")
    action.add_argument("--details", metavar="HOST", help="show effective settings, without connecting")
    action.add_argument("-d", "--diagnose", metavar="HOST", help="bounded SSH diagnostic without forwarding")
    action.add_argument("-a", "--add", action="store_true", help="add a minimal host entry with a backup")
    action.add_argument("-s", "--copy-key", metavar="HOST", help="install your public key using ssh-copy-id")
    result.add_argument("--plain", action="store_true", help="simple numbered fallback")
    result.add_argument("--json", action="store_true", help="machine-readable output with --list")
    result.add_argument("host", nargs="?", help="exact SSH destination; never fuzzy-auto-connects")
    result.add_argument("ssh_arguments", nargs=argparse.REMAINDER, help="optional SSH arguments or remote command")
    return result


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    argv = list(sys.argv[1:] if argv is None else argv)
    ctx = None
    try:
        if argv and argv[0].startswith("__"):
            ctx = Context()
            if argv[0] == "__rows" and len(argv) == 2:
                print(rows(ctx, session_read(Path(argv[1]))), end="")
            elif argv[0] == "__preview" and len(argv) in (2, 3, 4):
                preview(ctx, argv[1], live=len(argv) >= 3 and argv[2] == "--live", session=Path(argv[3]) if len(argv) == 4 else None)
            elif argv[0] == "__action" and len(argv) in (3, 4):
                session_action(ctx, Path(argv[1]), argv[2], argv[3] if len(argv) > 3 else "")
            else:
                raise ValueError("Invalid internal action.")
            return 0
        argument_parser = parser()
        args = argument_parser.parse_args(argv)
        if args.host and any((args.setup, args.doctor, args.star, args.list, args.details, args.diagnose, args.add, args.copy_key)):
            argument_parser.error("Choose a command or an exact destination, not both.")
        if args.setup or args.doctor:
            from onboarding import setup, doctor
            return setup() if args.setup else doctor()
        if args.json and not args.list:
            raise ValueError("Use --json together with --list.")
        ctx = Context()
        if args.theme is not None:
            ctx.theme = args.theme
            ctx.store.set_preference("theme", ctx.theme)
        if args.network is not None:
            ctx.network = args.network == "on"
            ctx.store.set_preference("network", args.network)
        if not ctx.config.is_file():
            raise ValueError(f"SSH configuration not found: {ctx.config}. Run sshelp --setup to get started.")
        settings = {"env": args.env, "project": args.project,
                    "view": "favourites" if args.favourites else "recent" if args.recent else "all"}
        if args.star:
            host = ctx.catalog.lookup(args.star)
            if host is None:
                raise ValueError("That alias is not in the SSH catalogue.")
            starred = ctx.store.toggle(host.alias)
            print(f"{'Starred' if starred else 'Unstarred'} {host.alias}")
            return 0
        if args.list:
            hosts = selected_hosts(ctx, settings)
            if args.search:
                hosts = [host for host in hosts if all(term in host.search_text() for term in args.search.casefold().split())]
            if args.json:
                print(json.dumps([asdict(host) | ctx.store.metadata().get(host.alias.casefold(), {}) for host in hosts], indent=2, ensure_ascii=False))
            else:
                for host in hosts:
                    print(f"{host.alias:34} {host.environment.upper():5} {host.project:8} {host.project_name}")
            return 0
        if args.details:
            if not ctx.catalog.lookup(args.details):
                raise ValueError("That alias is not in the SSH catalogue.")
            ctx.color = ctx.color and sys.stdout.isatty()
            preview(ctx, args.details)
            return 0
        if args.diagnose:
            return run_connection(ctx, args.diagnose, [], diagnostic=True)
        if args.add:
            return add_host(ctx)
        if args.copy_key:
            return copy_key(ctx, args.copy_key)
        if args.host:
            return run_connection(ctx, args.host, args.ssh_arguments)
        if args.plain or not sys.stdin.isatty() or not sys.stdout.isatty() or not shutil.which("fzf") or os.environ.get("TERM") == "dumb":
            return plain_menu(ctx, settings, args.search)
        return dashboard(ctx, settings, args.search)
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"sshelp: {safe_text(str(error))}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        return 130
    except PreviewCancelled:
        return 0
    finally:
        if ctx is not None:
            ctx.store.close()

if __name__ == "__main__":
    sys.exit(main())