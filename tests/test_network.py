"""Focused probe tests with mocked processes/sockets; no remote hosts accessed."""
import errno
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(ROOT))
import network


class ProbeTests(unittest.TestCase):
    def probe(self):
        with patch.object(network.threading.Thread, "start"):
            return network.Probe("127.0.0.1", 2222)

    def test_only_safe_destinations(self):
        for address, port in (("-x", 22), ("Demo-Dev;echo", 22), ("Demo-Dev\nLab-Sbx", 22), ("127.0.0.1", 0), ("127.0.0.1", 65536)):
            with self.assertRaises(ValueError):
                network.Probe(address, port)
        self.assertTrue(network.valid_destination("0:0:0:0:0:0:0:1", 22))

    def test_one_numeric_ping_and_latency(self):
        probe = self.probe()
        process = MagicMock()
        process.communicate.return_value = ("64 bytes from 127.0.0.1: time=0.124 ms", "")
        process.returncode = 0
        with patch.object(network.shutil, "which", return_value="/usr/bin/ping"), patch.object(network.subprocess, "Popen", return_value=process) as popen:
            probe._icmp()
        self.assertEqual(popen.call_args.args[0], ["ping", "-n", "-c", "1", "-W", "1", "-w", "2", "--", "127.0.0.1"])
        self.assertEqual(probe.snapshot()["icmp"], {"status": "up", "ms": 0.124})

    def test_ping_filtered_is_not_offline(self):
        probe = self.probe()
        process = MagicMock(returncode=1)
        process.communicate.return_value = ("", "")
        with patch.object(network.shutil, "which", return_value="ping"), patch.object(network.subprocess, "Popen", return_value=process):
            probe._icmp()
        self.assertEqual(probe.snapshot()["icmp"]["status"], "no_reply")
        text = network.network_lines(probe.snapshot(), 22)[0][0]
        self.assertIn("may be filtered", text)
        self.assertNotIn("offline", text)

    def test_missing_ping_is_handled(self):
        probe = self.probe()
        with patch.object(network.shutil, "which", return_value=None):
            probe._icmp()
        self.assertEqual(probe.snapshot()["icmp"]["status"], "unavailable")

    def test_tcp_opens_one_socket_without_authentication(self):
        connection = MagicMock()
        with patch.object(network.socket, "create_connection", return_value=connection) as connect:
            result = network.check_tcp("127.0.0.1", 2222)
        connect.assert_called_once_with(("127.0.0.1", 2222), timeout=1.5)
        connection.__enter__.return_value.send.assert_not_called()
        self.assertEqual(result["status"], "up")

    def test_tcp_refused_differs_from_timeout(self):
        for error, expected in ((ConnectionRefusedError(), "refused"), (TimeoutError(), "timeout"), (socket.gaierror(), "dns_error"), (OSError(errno.ENETUNREACH, "no route"), "no_route")):
            with patch.object(network.socket, "create_connection", side_effect=error):
                result = network.check_tcp("127.0.0.1", 2222)
            self.assertEqual(result["status"], expected)

    def test_dns_worker_is_killed_at_hard_deadline(self):
        probe = self.probe()
        process = MagicMock()
        process.communicate.side_effect = [network.subprocess.TimeoutExpired('tcp-worker', 2.5), ("", "")]
        with patch.object(network.subprocess, "Popen", return_value=process):
            probe._tcp()
        process.kill.assert_called_once()
        self.assertEqual(probe.snapshot()["tcp"]["status"], "timeout")

    def test_dns_stall_has_a_display_deadline(self):
        probe = self.probe()
        with patch.object(network.time, "monotonic", return_value=probe.started + network.DEADLINE + 1):
            self.assertEqual(probe.snapshot()["tcp"]["status"], "timeout")

    def test_cancel_prevents_late_updates(self):
        probe = self.probe()
        probe.close()
        probe._set("tcp", {"status": "up"})
        self.assertEqual(probe.snapshot()["tcp"]["status"], "checking")

    def test_cancel_reaps_running_ping(self):
        probe = self.probe()
        process = MagicMock()
        process.poll.return_value = None
        probe.ping = process
        probe.close()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=0.3)


if __name__ == "__main__":
    unittest.main(verbosity=2)