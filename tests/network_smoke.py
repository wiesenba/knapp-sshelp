#!/usr/bin/env python3
"""Real fzf streaming-preview regression; fake ICMP and loopback-only TCP.

Run with Python 3.10+ on Linux with fzf >= 0.44. No remote SSH or real ping
is executed. All config, logs, preferences and helper files are disposable.
Cancellation on hiding a pending preview is required even with older fzf.
"""
from __future__ import annotations

import argparse
import re
import select
import socket
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

sys.dont_write_bytecode = True
from ui_smoke import Driver, Fixture, check_prerequisites

ROOT = Path(__file__).resolve().parents[1] / "app"

SAMPLE = """## EX00001 - Demo - Dev
Host A-Dev
    HostName 127.0.0.1
    User demo
## EX00002 - Lab - Dev
Host B-Dev
    HostName 127.0.0.2
    User demo
"""

PING_MOCK = r'''#!/usr/bin/env python3
import json
import os
import threading
import time
import sys
from pathlib import Path

args = sys.argv[1:]
if args[:-1] != ["-n", "-c", "1", "-W", "1", "-w", "2", "--"] or args[-1] not in ("127.0.0.1", "127.0.0.2"):
    raise SystemExit("loopback fixture arguments required")
home = Path(os.environ["HOME"])
blocked = (home / "block-ping").exists()
record = {"argv": args, "pid": os.getpid(), "started": time.monotonic(), "latency_ms": 0.125, "blocked": blocked}
with open(home / "pings.jsonl", "a", encoding="utf-8") as stream:
    stream.write(json.dumps(record) + "\n")
if blocked:
    threading.Event().wait(30)
print("64 bytes from loopback: time=0.125 ms", flush=True)
'''


class Listener:
    """An ephemeral listener on one loopback address, not INADDR_ANY."""

    def __init__(self):
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen()
        self.port = self.socket.getsockname()[1]
        self.stop_read, self.stop_write = socket.socketpair()
        self.accepted = []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while True:
            ready, _, _ = select.select([self.socket, self.stop_read], [], [], 8)
            if self.stop_read in ready:
                return
            if self.socket in ready:
                connection, address = self.socket.accept()
                with connection:
                    self.accepted.append(address[0])

    def close(self):
        self.stop_write.send(b"x")
        self.thread.join(timeout=2)
        self.socket.close()
        self.stop_read.close()
        self.stop_write.close()
        assert not self.thread.is_alive(), "Loopback listener did not stop"


def running(pid):
    try:
        # A cancelled worker may briefly await reaping, but must not run.
        state = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[0]
        return state not in ("Z", "X")
    except FileNotFoundError:
        return False


def run_case(pending_hide=False):
    listener = Listener()
    fixture = None
    driver = None
    try:
        fixture = Fixture(SAMPLE, {"A-Dev": "127.0.0.1", "B-Dev": "127.0.0.2"}, network=True, port=listener.port)
        fixture.executable("ping", PING_MOCK)
        driver = Driver(fixture, ["--theme", "minimal", "--network", "on"])
        pings = lambda: fixture.records("pings.jsonl")
        right = lambda: driver.screen.text("right")
        ready = lambda alias: driver.query_is("") and driver.focused(alias) and driver.briefing(alias)
        driver.until(lambda: ready("A-Dev") and "waiting for selection" in right(), "initial debounce")
        driver.send(b"\x1b[B")
        driver.until(lambda: ready("B-Dev") and "connection refused" in right() and "0.125 ms" in right(), "second host probes after rapid selection")
        logs = pings()
        assert len(logs) == 1 and logs[0]["argv"][-1] == "127.0.0.2", "Debounce probed a stale selection"
        assert logs[0]["latency_ms"] == 0.125 and not logs[0]["blocked"], "Fake ping latency not recorded"
        assert not listener.accepted, "First host TCP ran during cancelled debounce"
        driver.send(b"\x1b[A")
        driver.until(lambda: ready("A-Dev") and "port open" in right() and "0.125 ms" in right()
                     and bool(listener.accepted), "first host loopback TCP and fake ping")
        assert len(pings()) == 2 and pings()[1]["argv"][-1] == "127.0.0.1", "Unexpected focused ping sequence"
        assert listener.accepted == ["127.0.0.1"], "Unexpected TCP connection"

        def pulse():
            match = re.search(r"NETWORK\s+([◌○◉⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏])", right())
            return match[1] if match else None

        initial_pulse = pulse()
        assert initial_pulse, "Streaming preview has no animated pulse"
        driver.until(lambda: pulse() is not None and pulse() != initial_pulse and "port open" in right(), "animation advances without another probe", timeout=2)
        assert len(pings()) == 2, "Animation triggered additional probes"
        driver.send(b"\x1bn")
        driver.until(lambda: fixture.preference("network") == "off" and ready("A-Dev") and "Network off" in right(), "Alt-N off and persistence")
        driver.observe(0.7, lambda: len(pings()) == 2 and len(listener.accepted) == 1, "Disabled checks still send probes")

        if pending_hide:
            (fixture.home / "block-ping").touch()
            driver.send(b"\x1bn")
            driver.until(lambda: fixture.preference("network") == "on" and len(pings()) == 3 and pings()[-1]["blocked"], "deliberately pending fake ping")
            pending = pings()[-1]["pid"]
            assert running(pending), "Pending fake ping already exited"
            driver.send(b"\x1bp")
            # Less than the probe's 2.5s timeout: this must be cancellation,
            # not the fake ping's normal deadline or its 30s fixture wait.
            driver.until(lambda: "HOST BRIEFING" not in right() and not running(pending), "Alt-P hide cancels pending ping", timeout=1.5)
            driver.observe(0.7, lambda: len(pings()) == 3 and not running(pending), "Hidden preview restarted checks")
        assert all(row["argv"][-1] in ("127.0.0.1", "127.0.0.2") for row in pings()), "Non-loopback ping destination"
        assert not fixture.records("connections.jsonl"), "Network case attempted an SSH launch"
        driver.finish()
    finally:
        try:
            if driver is not None:
                driver.close()
        finally:
            if fixture is not None:
                fixture.close()
            listener.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pending-hide", action="store_true", help="compatibility flag; hide cancellation is always checked")
    args = parser.parse_args()
    try:
        check_prerequisites()
        run_case(pending_hide=True)
    except (AssertionError, OSError, subprocess.SubprocessError, sqlite3.Error) as error:
        detail = str(error) if isinstance(error, AssertionError) else type(error).__name__
        print("FAIL: loopback streaming preview — " + detail, file=sys.stderr)
        return 1
    coverage = "debounce, focused fake ping/latency, TCP, animation, Alt-N off, Alt-P cancellation"
    print("PASS: loopback streaming preview — " + coverage)
    return 0


if __name__ == "__main__":
    sys.exit(main())