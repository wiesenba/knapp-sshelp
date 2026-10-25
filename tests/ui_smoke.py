#!/usr/bin/env python3
"""Real fzf PTY regression tests; synthetic homes and mock connections only.

Run with Python 3.10+ on Linux with fzf >= 0.44 and openssh-client installed.
No arguments/capture files are needed. Importing this module runs no tests.
"""
from __future__ import annotations

import codecs
import errno
import fcntl
import json
import os
import pty
import re
import select
import signal
import sqlite3
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unicodedata
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(ROOT))
from themes import NAMES


SAMPLE = """Host *
    User demo
    IdentityFile none
    CertificateFile none
    IdentityAgent none
    IdentitiesOnly yes
    UserKnownHostsFile /dev/null
    GlobalKnownHostsFile /dev/null
    CanonicalizeHostname no
    ProxyCommand none
    ProxyJump none
## EX00001 - Demo - Dev
Host Demo-Dev
    HostName 192.0.2.10
## EX00001 - Demo - Live
Host Demo-Live
    HostName 192.0.2.11
## EX00002 - Lab - Sbx
Host Lab-Sbx
    HostName 198.51.100.20
"""

# All executable fixtures are generated beneath the disposable home. In
# particular, -G cannot evaluate an inherited config, Include or Match exec.
SSH_MOCK = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
config = Path(os.environ["SSHELP_CONFIG"])
home = Path(os.environ["HOME"])
if config != home / ".ssh/config" or config.is_symlink():
    raise SystemExit("fixture config required")
aliases = json.loads(os.environ["TEST_ALIASES"])
if "-G" in args:
    if len(args) != 4 or args[:3] != ["-G", "-F", str(config)] or args[3] not in aliases:
        raise SystemExit("fixture evaluation arguments required")
    with open(home / "evaluations.jsonl", "a", encoding="utf-8") as stream:
        stream.write(json.dumps(args[3]) + "\n")
    if os.environ["TEST_SSH_MODE"] == "evaluate":
        os.execv("/usr/bin/ssh", ["ssh", "-G", "-F", str(config), args[3]])
    # Network fixtures never execute any real SSH, including -G.
    print("hostname " + aliases[args[3]])
    print("port " + os.environ["TEST_PORT"])
    print("user demo")
else:
    with open(home / "connections.jsonl", "a", encoding="utf-8") as stream:
        stream.write(json.dumps(args) + "\n")
    print("MOCK_CONNECTION_ONLY", flush=True)
'''

# This guard is loaded only by fixture children, including the TCP helper.
# Numeric loopback is the only allowed network destination, and UI cases
# forbid even that. No inherited PYTHONPATH, shell startup or agent is used.
NETWORK_GUARD = r'''import os
import sys

def fixture_audit(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"):
        address = args[1] if event == "socket.connect" else args[0]
        address = address[0] if isinstance(address, tuple) else address
        if os.environ["TEST_NETWORK"] != "loopback" or address not in ("127.0.0.1", "127.0.0.2"):
            raise RuntimeError("non-fixture network access denied")
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = os.fsdecode(args[0])
        if "/.ssh/" in path and not path.startswith(os.environ["HOME"] + "/.ssh/"):
            raise RuntimeError("non-fixture SSH file access denied")

sys.addaudithook(fixture_audit)
'''


def sgr_commands(params):
    """Yield SGR opcodes, consuming extended colour payloads, not their RGBs."""
    fields = params.split(";") if params else ["0"]
    i = 0
    while i < len(fields):
        parts = fields[i].split(":")
        code = int(parts[0] or 0)
        payload = ()
        if code in (38, 48, 58):
            if len(parts) > 1:
                mode = int(parts[1] or 0)
                count = 3 if mode == 2 else 1 if mode == 5 else 0
                assert count and len(parts) >= count + 2, "Malformed colour SGR"
                payload = (mode, *(int(v or 0) for v in parts[-count:]))
            else:
                assert i + 1 < len(fields), "Missing colour mode"
                mode = int(fields[i + 1])
                count = 3 if mode == 2 else 1 if mode == 5 else 0
                assert count and i + count + 1 < len(fields), "Malformed colour SGR"
                payload = (mode, *(int(v) for v in fields[i + 2:i + 2 + count]))
                i += count + 1
        yield code, payload
        i += 1


class Screen:
    """Small incremental ANSI screen, with cell-level focus style assertions."""

    def __init__(self, width=144, height=38):
        self.width, self.height = width, height
        self.x = self.y = 0
        self.fg = self.bg = "default"
        self.bold = self.underline = False
        self.reverse = False
        self.reverse_selection = False
        self.grid = [[self.blank() for _ in range(width)] for _ in range(height)]
        self.buffer = ""
        self.cursor_requests = 0

    def blank(self):
        return [" ", self.fg, self.bg, self.bold, self.underline]

    def text(self, side=None):
        start, end = (0, self.width)
        if side == "left":
            end = self.width // 2
        elif side == "right":
            start = self.width // 2
        return "\n".join("".join(c[0] for c in row[start:end]) for row in self.grid)

    def assert_transparent(self):
        assert not self.reverse_selection, "Reverse-video selection"

    def feed(self, text):
        self.buffer += text
        while self.buffer:
            if self.buffer.startswith("\x1b["):
                match = re.match(r"\x1b\[([0-9;:?]*)([ -/]*)([@-~])", self.buffer)
                if not match:
                    return
                self.csi(match[1], match[3])
                self.buffer = self.buffer[len(match[0]):]
                continue
            if self.buffer.startswith("\x1b]"):
                match = re.match(r"\x1b\].*?(?:\x07|\x1b\\)", self.buffer, re.S)
                if not match:
                    return
                self.buffer = self.buffer[len(match[0]):]
                continue
            if self.buffer[0] == "\x1b":
                if len(self.buffer) < 2 or (self.buffer[1] in "()" and len(self.buffer) < 3):
                    return
                self.buffer = self.buffer[3 if self.buffer[1] in "()" else 2:]
                continue
            char, self.buffer = self.buffer[0], self.buffer[1:]
            if char == "\r":
                self.x = 0
            elif char == "\n":
                self.y = min(self.height - 1, self.y + 1)
            elif char == "\b":
                self.x = max(0, self.x - 1)
            elif char == "\t":
                self.x = min(self.width - 1, (self.x // 8 + 1) * 8)
            elif char.isprintable() and not unicodedata.combining(char):
                if self.x >= self.width:
                    self.x = 0
                    self.y = min(self.height - 1, self.y + 1)
                if self.reverse and self.x < self.width // 2:
                    self.reverse_selection = True
                self.grid[self.y][self.x] = [char, self.fg, self.bg, self.bold, self.underline]
                self.x += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1

    def csi(self, params, command):
        if params.startswith("?"):
            return
        if command == "m":
            for code, payload in sgr_commands(params):
                assert code != 48 and not 40 <= code <= 47 and not 100 <= code <= 107, "Painted terminal background"
                if code == 0:
                    self.fg = self.bg = "default"
                    self.bold = self.underline = False
                    self.reverse = False
                elif code == 7:
                    # fzf 0.44 uses inverse video for its preview scrollbar;
                    # it must never leak into the selection/list region.
                    self.reverse = True
                elif code == 27:
                    self.reverse = False
                elif code == 1:
                    self.bold = True
                elif code == 22:
                    self.bold = False
                elif code == 4:
                    self.underline = True
                elif code == 24:
                    self.underline = False
                elif code == 39:
                    self.fg = "default"
                elif code == 38:
                    self.fg = ("#" + "".join(f"{v:02x}" for v in payload[1:])
                               if payload[0] == 2 else f"indexed:{payload[1]}")
            return
        values = [int(v or 0) for v in params.split(";")] if params else [0]
        n = values[0] or 1
        if command == "n" and values == [6]:
            self.cursor_requests += 1
        elif command in "Hf":
            self.y = min(self.height - 1, max(0, n - 1))
            self.x = min(self.width - 1, max(0, (values[1] if len(values) > 1 else 1) - 1))
        elif command == "A":
            self.y = max(0, self.y - n)
        elif command == "B":
            self.y = min(self.height - 1, self.y + n)
        elif command == "C":
            self.x = min(self.width - 1, self.x + n)
        elif command == "D":
            self.x = max(0, self.x - n)
        elif command == "G":
            self.x = min(self.width - 1, n - 1)
        elif command == "J":
            if values[0] in (2, 3):
                self.grid = [[self.blank() for _ in range(self.width)] for _ in range(self.height)]
            elif values[0] == 0:
                self.grid[self.y][self.x:] = [self.blank() for _ in range(self.width - self.x)]
                for y in range(self.y + 1, self.height):
                    self.grid[y] = [self.blank() for _ in range(self.width)]
        elif command == "K":
            start, end = ((0, self.width) if values[0] == 2 else
                          (0, self.x + 1) if values[0] == 1 else (self.x, self.width))
            self.grid[self.y][start:end] = [self.blank() for _ in range(end - start)]


class Fixture:
    """No inherited HOME, XDG, SSH, Python, fzf or shell configuration."""

    def __init__(self, config=SAMPLE, aliases=None, network=False, port=22, monochrome=False):
        self.temporary = tempfile.TemporaryDirectory(prefix="sshelp-smoke-", dir="/tmp")
        self.home = Path(self.temporary.name)
        self.config = self.home / ".ssh/config"
        for name in ("bin", ".ssh", "state", "config", "cache", "data", "tmp", "guard"):
            (self.home / name).mkdir(mode=0o700)
        self.config.write_text(config, encoding="utf-8")
        self.config.chmod(0o600)
        self.executable("ssh", SSH_MOCK)
        self.executable("ping", '#!/bin/sh\nprintf forbidden > "$HOME/forbidden-probe"\nexit 1\n')
        (self.home / "guard/sitecustomize.py").write_text(NETWORK_GUARD, encoding="utf-8")
        self.env = {
            "HOME": str(self.home), "USER": "demo", "LOGNAME": "demo",
            "XDG_STATE_HOME": str(self.home / "state"),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "XDG_CACHE_HOME": str(self.home / "cache"),
            "XDG_DATA_HOME": str(self.home / "data"),
            "TMPDIR": str(self.home / "tmp"), "SHELL": "/bin/sh",
            "PATH": f"{self.home / 'bin'}:{Path(sys.executable).parent}:/usr/bin:/bin",
            "TERM": "xterm-256color", "COLORTERM": "truecolor", "LC_ALL": "C.UTF-8",
            "SSHELP_COLOR": "1", "SSHELP_CONFIG": str(self.config),
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(self.home / "guard"),
            "TEST_ALIASES": json.dumps(aliases or {"Demo-Dev": "192.0.2.10", "Demo-Live": "192.0.2.11", "Lab-Sbx": "198.51.100.20"}),
            "TEST_SSH_MODE": "fake" if network else "evaluate",
            "TEST_NETWORK": "loopback" if network else "none", "TEST_PORT": str(port),
        }
        if monochrome:
            self.env["NO_COLOR"] = "1"

    def executable(self, name, source):
        path = self.home / "bin" / name
        path.write_text(source, encoding="utf-8")
        path.chmod(0o700)

    def records(self, name):
        path = self.home / name
        if not path.exists():
            return []
        # Readers can observe a writer just before its newline/close.
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        return [json.loads(line) for line in lines if line.endswith("\n")]

    def query(self, sql, params=()):
        path = self.home / "state/sshelp/state.sqlite3"
        if not path.exists():
            return []
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            return db.execute(sql, params).fetchall()

    def preference(self, name):
        rows = self.query("SELECT value FROM preferences WHERE name=?", (name,))
        return rows[0][0] if rows else None

    def close(self):
        self.temporary.cleanup()


def own_groups(session):
    """Inspect only process metadata; never signal unrelated user processes."""
    groups = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[3]) == session:
                group = int(fields[2])
                assert group != os.getpgrp(), "Refusing to signal the caller group"
                groups.add(group)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return groups


class Driver:
    """Bounded select waits; incremental CSI parsing also answers split CSI 6n."""

    def __init__(self, fixture, arguments):
        self.screen = Screen()
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.eof = False
        self.status = None
        self.answered = 0
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            try:
                os.chdir(fixture.home)
                fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 38, 144, 0, 0))
                os.execve(sys.executable, [sys.executable, str(ROOT / "app.py"), *arguments], fixture.env)
            except BaseException:
                os._exit(127)

    def send(self, data):
        os.write(self.fd, data)

    def pump(self, timeout):
        ready, _, _ = select.select([] if self.eof else [self.fd], [], [], timeout)
        if not ready:
            return
        try:
            data = os.read(self.fd, 65536)
        except OSError as error:
            if error.errno != errno.EIO:
                raise
            data = b""
        if not data:
            self.eof = True
            return
        self.screen.feed(self.decoder.decode(data))
        while self.answered < self.screen.cursor_requests:
            self.send(b"\x1b[1;1R")
            self.answered += 1

    def until(self, predicate, label, timeout=8):
        deadline = time.monotonic() + timeout
        while True:
            if predicate():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Do not dump process lists, argv, paths or inherited values.
                raise AssertionError("Timed out: " + label)
            self.pump(min(remaining, 0.05))

    def observe(self, duration, predicate, label):
        deadline = time.monotonic() + duration
        while True:
            assert predicate(), label
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.pump(min(remaining, 0.05))

    def focus(self, monochrome=False):
        rail = ">>" if monochrome else "▌▸"
        for y, row in enumerate(self.screen.grid):
            text = "".join(cell[0] for cell in row[:self.screen.width // 2])
            if rail in text:
                return y, text
        return -1, ""

    def focused(self, value, monochrome=False):
        return re.search(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])", self.focus(monochrome)[1]) is not None

    def query_is(self, query, prompt="FIND"):
        return re.search(r"\b" + prompt + r"\s+› " + re.escape(query) + r"(?=\s+[<‹]|\s*$)",
                         self.screen.text("left"), re.M) is not None

    def briefing(self, alias, address=None):
        text = self.screen.text("right")
        return "HOST BRIEFING" in text and alias in text and (address is None or address in text)

    def assert_focus(self, value, theme, monochrome=False):
        y, text = self.focus(monochrome)
        assert y >= 0 and value in text, "Missing strong focus rail/selected value"
        start = text.index(value)
        cells = self.screen.grid[y][start:start + len(value)]
        color = "default" if monochrome else {"minimal": "#ffffff", "midnight": "#f5fbff", "ember": "#fff0dc"}[theme]
        assert all(c[1] == color and c[3] and c[4] for c in cells), "Focused value is not bright, bold and underlined"
        return y, start, len(value)

    def exited(self):
        if self.status is None:
            child, status = os.waitpid(self.pid, os.WNOHANG)
            if child:
                self.status = status
        return self.status is not None

    def finish(self):
        self.send(b"\x1b")
        self.until(self.exited, "clean application exit")
        assert os.waitstatus_to_exitcode(self.status) == 0, "Application exit was not successful"

    def close(self):
        try:
            # fzf gives preview workers their own groups within our PTY
            # session. Kill those too, not every fzf/ping owned by the user.
            for sig in (signal.SIGTERM, signal.SIGKILL):
                for group in own_groups(self.pid):
                    try:
                        os.killpg(group, sig)
                    except ProcessLookupError:
                        pass
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline and not self.exited():
                    select.select([], [], [], min(0.02, max(0, deadline - time.monotonic())))
            if self.status is None:
                _, self.status = os.waitpid(self.pid, 0)
        finally:
            os.close(self.fd)


def check_prerequisites():
    assert sys.platform.startswith("linux"), "These PTY tests require Linux"
    result = subprocess.run(["/usr/bin/fzf", "--version"], capture_output=True, text=True, timeout=5,
                            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"})
    match = re.match(r"(\d+)\.(\d+)", result.stdout)
    assert result.returncode == 0 and match and tuple(map(int, match.groups())) >= (0, 44), "fzf >= 0.44 required"
    assert Path("/usr/bin/ssh").is_file(), "openssh-client required"


def check_parser():
    screen = Screen()
    screen.feed("\x1b[")
    screen.feed("6")
    assert screen.cursor_requests == 0, "Partial cursor request was consumed"
    screen.feed("n\x1b[38;2;40;48;100mA\x1b[38;5;48mB\x1b[38:2::100:48:40mC")
    assert screen.cursor_requests == 1, "Split cursor request was lost"
    assert screen.grid[0][0][1] == "#283064", "RGB channels were parsed as SGR opcodes"
    assert screen.grid[0][1][1] == "indexed:48", "Indexed colour was parsed as background"
    for code in ("48;2;1;2;3", "48;5;1", "40", "47", "100", "107", "7"):
        try:
            sample = Screen()
            sample.feed("\x1b[" + code + "mX")
            sample.assert_transparent()
        except AssertionError:
            continue
        raise AssertionError("Background/reverse-video guard missed an opcode")


def run_case(theme, monochrome=False):
    fixture = Fixture(monochrome=monochrome)
    driver = None
    try:
        driver = Driver(fixture, ["--theme", theme, "--no-network"])
        screens = [driver.screen]
        left = lambda: driver.screen.text("left")
        ready = lambda query, alias, address: (driver.query_is(query) and driver.focused(alias, monochrome)
                                               and driver.briefing(alias, address))
        driver.until(lambda: ready("", "Demo-Dev", "192.0.2.10") and "Network off" in driver.screen.text(), "initial briefing")
        assert fixture.preference("theme") == theme, "Initial theme not persisted"
        old_y, old_x, old_length = driver.assert_focus("Demo-Dev", theme, monochrome)
        driver.send(b"\x1b[B")
        driver.until(lambda: ready("", "Demo-Live", "192.0.2.11"), "focus moves down")
        driver.assert_focus("Demo-Live", theme, monochrome)
        assert not any(c[4] for c in driver.screen.grid[old_y][old_x:old_x + old_length]), "Previous alias remains underlined"
        driver.send(b"\x1b[A")
        driver.until(lambda: ready("", "Demo-Dev", "192.0.2.10"), "focus moves up")
        driver.send(b"Demo")
        driver.until(lambda: ready("Demo", "Demo-Dev", "192.0.2.10") and "Lab-Sbx" not in left(), "actual fuzzy Demo result")
        driver.send(b"\x05")
        driver.until(lambda: "ALL   /   LIVE" in left() and ready("Demo", "Demo-Live", "192.0.2.11") and "Demo-Dev" not in left(), "environment-filtered result")
        driver.send(b"\x13")
        driver.until(lambda: fixture.query("SELECT favourite FROM hosts WHERE alias='demo-live'") == [(1,)]
                     and "★" in driver.focus(monochrome)[1] and "yes ★" in driver.screen.text("right"), "star and favourite persistence")
        driver.send(b"\x06")
        driver.until(lambda: "FAVOURITES   /   LIVE" in left() and ready("Demo", "Demo-Live", "192.0.2.11"), "favourites result")
        driver.send(b"\x01")
        # Reload headers arrive before the asynchronous matcher replaces rows.
        driver.until(lambda: "ALL   /   ALL   /   ALL PROJECTS" in left() and ready("Demo", "Demo-Dev", "192.0.2.10")
                     and "Demo-Live" in left(), "reset with actual focused result")
        driver.send(b"\x10")
        driver.until(lambda: "PROJECT SWITCHER" in driver.screen.text() and driver.query_is("", "PROJECT")
                     and driver.focused("ALL PROJECTS", monochrome), "project picker ready")
        driver.assert_focus("ALL PROJECTS", theme, monochrome)
        driver.send(b"EX00001")
        driver.until(lambda: driver.query_is("EX00001", "PROJECT") and driver.focused("EX00001", monochrome), "actual EX00001 project result")
        driver.assert_focus("EX00001", theme, monochrome)
        driver.send(b"\r")
        driver.until(lambda: "ALL   /   ALL   /   EX00001" in left() and ready("", "Demo-Dev", "192.0.2.10")
                     and "Lab-Sbx" not in left(), "return from project picker")
        driver.send(b"Demo-Dev")
        driver.until(lambda: ready("Demo-Dev", "Demo-Dev", "192.0.2.10") and "Demo-Live" not in left(), "exact host result before Enter")
        evaluations = len(fixture.records("evaluations.jsonl"))
        driver.send(b"\r")
        driver.until(lambda: bool(fixture.records("connections.jsonl")), "mock SSH launch")
        assert fixture.records("connections.jsonl") == [["-F", str(fixture.config), "Demo-Dev"]], "Mock launch argv differs from exact selected host"
        driver.until(lambda: len(fixture.records("evaluations.jsonl")) > evaluations
                     and ready("Demo-Dev", "Demo-Dev", "192.0.2.10")
                     and fixture.query("SELECT opens FROM hosts WHERE alias='demo-dev'") == [(1,)], "new dashboard after mock SSH")
        driver.send(b"\x12")
        driver.until(lambda: "RECENT   /   ALL   /   EX00001" in left() and ready("Demo-Dev", "Demo-Dev", "192.0.2.10"), "recent view")
        next_theme = NAMES[(NAMES.index(theme) + 1) % len(NAMES)]
        evaluations = len(fixture.records("evaluations.jsonl"))
        driver.send(b"\x14")
        driver.until(lambda: fixture.preference("theme") == next_theme
                     and len(fixture.records("evaluations.jsonl")) > evaluations
                     and next_theme.upper() in driver.screen.text().upper()
                     and "RECENT   /   ALL" in left() and ready("Demo-Dev", "Demo-Dev", "192.0.2.10"), "theme switch and persistence")
        driver.assert_focus("Demo-Dev", next_theme, monochrome)
        driver.finish()
        driver.close()
        driver = None
        # Restart without --theme: both preference and recency must survive.
        driver = Driver(fixture, ["--no-network", "--recent"])
        screens.append(driver.screen)
        driver.until(lambda: next_theme.upper() in driver.screen.text().upper() and "RECENT   /   ALL" in left()
                     and ready("", "Demo-Dev", "192.0.2.10") and "Demo-Live" not in left(), "restart remembers theme and recents")
        driver.assert_focus("Demo-Dev", next_theme, monochrome)
        driver.finish()
        assert fixture.preference("network") == "off", "UI case enabled network checks"
        assert not (fixture.home / "forbidden-probe").exists(), "UI case attempted a ping"
        assert len(fixture.records("connections.jsonl")) == 1, "Unexpected additional connection"
        # Defer the reverse-video assertion so a monochrome style regression
        # cannot prevent the remaining interaction/persistence coverage.
        for screen in screens:
            screen.assert_transparent()
    finally:
        if driver is not None:
            driver.close()
        fixture.close()


def main():
    try:
        check_prerequisites()
        check_parser()
    except (AssertionError, OSError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, AssertionError) else type(error).__name__
        print("FAIL: prerequisites/parser — " + detail, file=sys.stderr)
        return 1
    failures = 0
    for theme, monochrome in [(theme, False) for theme in NAMES] + [("minimal", True)]:
        label = theme + (" monochrome" if monochrome else "")
        try:
            run_case(theme, monochrome)
            print("PASS: " + label + " — transparent focus, filters, favourites, project, mock SSH, recents/theme restart", flush=True)
        except (AssertionError, OSError, subprocess.SubprocessError, sqlite3.Error) as error:
            detail = str(error) if isinstance(error, AssertionError) else type(error).__name__
            print("FAIL: " + label + " — " + detail, file=sys.stderr)
            failures += 1
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())