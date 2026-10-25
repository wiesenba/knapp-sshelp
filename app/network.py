"""One focused, cancellable ping + TCP check. Never logs in or changes routes."""
from __future__ import annotations

import errno
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

DEBOUNCE = 0.4
REFRESH = 10.0
DEADLINE = 3.0
FRAME_INTERVAL = 0.2


def valid_destination(address: str, port: int) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:%-]{0,252}", address)) and 1 <= port <= 65535


def check_tcp(address: str, port: int) -> dict:
    start = time.monotonic()
    try:
        with socket.create_connection((address, port), timeout=1.5):
            return {"status": "up", "ms": round((time.monotonic() - start) * 1000, 1)}
    except (socket.timeout, TimeoutError):
        return {"status": "timeout"}
    except ConnectionRefusedError:
        return {"status": "refused"}
    except socket.gaierror:
        return {"status": "dns_error"}
    except OSError as error:
        return {"status": "no_route" if error.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH) else "error"}


class Probe:
    def __init__(self, address: str, port: int):
        if not valid_destination(address, port):
            raise ValueError("The configured address/port cannot be probed safely.")
        self.address, self.port = address, port
        self.started = time.monotonic()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.ping: subprocess.Popen | None = None
        self.tcp: subprocess.Popen | None = None
        self.results = {"icmp": {"status": "checking"}, "tcp": {"status": "checking"}}
        for method in (self._icmp, self._tcp):
            threading.Thread(target=method, daemon=True).start()

    def _set(self, kind: str, result: dict) -> None:
        with self.lock:
            if not self.stop.is_set():
                self.results[kind] = result

    def _icmp(self) -> None:
        if not shutil.which("ping"):
            self._set("icmp", {"status": "unavailable"})
            return
        process = None
        try:
            with self.lock:
                if self.stop.is_set():
                    return
                # Linux iputils: one packet, numeric output, one-second reply
                # timeout and two-second deadline. No shell or alias expansion.
                process = subprocess.Popen(
                    ["ping", "-n", "-c", "1", "-W", "1", "-w", "2", "--", self.address],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    env=dict(os.environ, LC_ALL="C"),
                )
                self.ping = process
            out, error = process.communicate(timeout=2.5)
            latency = re.search(r"time\s*[=<]\s*([0-9.]+)\s*ms", out)
            if process.returncode == 0:
                self._set("icmp", {"status": "up", "ms": float(latency[1]) if latency else None})
            elif process.returncode == 1:
                self._set("icmp", {"status": "no_reply"})
            else:
                self._set("icmp", {"status": "unavailable" if "permitted" in error.lower() else "error"})
        except subprocess.TimeoutExpired:
            if process is not None:
                process.kill()
                process.communicate()
            self._set("icmp", {"status": "no_reply"})
        except OSError:
            self._set("icmp", {"status": "unavailable"})

    def _tcp(self) -> None:
        process = None
        try:
            # DNS resolution does not honor socket timeouts on every system.
            # A killable helper bounds both DNS and the single TCP connection.
            with self.lock:
                if self.stop.is_set():
                    return
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "__tcp", self.address, str(self.port)],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                )
                self.tcp = process
            output, _ = process.communicate(timeout=2.5)
            result = json.loads(output) if process.returncode == 0 else {"status": "error"}
            self._set("tcp", result)
        except subprocess.TimeoutExpired:
            if process is not None:
                process.kill()
                process.communicate()
            self._set("tcp", {"status": "timeout"})
        except (OSError, ValueError):
            self._set("tcp", {"status": "error"})

    def snapshot(self) -> dict:
        with self.lock:
            data = {key: dict(value) for key, value in self.results.items()}
        if time.monotonic() - self.started >= DEADLINE:
            for result in data.values():
                if result["status"] == "checking":
                    result["status"] = "timeout"
        return data

    def close(self) -> None:
        self.stop.set()
        with self.lock:
            processes = (self.ping, self.tcp)
        for process in processes:
            if process is None or process.poll() is not None:
                continue
            try:
                process.terminate()
                process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            except ProcessLookupError:
                pass


def network_lines(results: dict, port: int) -> list[tuple[str, str]]:
    lines = []
    for key, label in (("icmp", "Ping"), ("tcp", f"SSH :{port}")):
        result = results.get(key, {"status": "checking"})
        status = result["status"]
        duration = result.get("ms")
        latency = f" · {duration:g} ms" if isinstance(duration, (int, float)) and math.isfinite(duration) else ""
        text, color = {
            "checking": ("checking…", "muted"), "up": ("reply" if key == "icmp" else "port open", "dev"),
            "no_reply": ("no reply · may be filtered", "sbx"), "timeout": ("timed out", "sbx"),
            "refused": ("connection refused", "sbx"), "dns_error": ("DNS lookup failed", "sbx"),
            "no_route": ("no route", "sbx"), "unavailable": ("unavailable", "muted"), "error": ("check failed", "sbx"),
        }.get(status, ("unknown", "muted"))
        lines.append((f"{label:12}{text}{latency}", color))
    return lines


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "__tcp" or not sys.argv[3].isdecimal():
        sys.exit(1)
    address, port = sys.argv[2], int(sys.argv[3])
    if not valid_destination(address, port):
        sys.exit(1)
    print(json.dumps(check_tcp(address, port)))