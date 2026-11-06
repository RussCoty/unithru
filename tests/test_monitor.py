import json
import socket
import struct
import threading
import time
import unittest
import urllib.error
import urllib.request

from unithru.ardour import ADDRESS, TAGS
from unithru.monitor import MonitorEngine, MonitorHandler, MonitorHTTPServer, validate_action
from unithru.osc import padded


class MonitorChecks(unittest.TestCase):
    def setUp(self):
        self.engine = MonitorEngine()
        self.engine.start()
        self.wait_for(lambda state: len(state["routes"]) == 4 and state["routes"][0]["expected"] is False)

    def tearDown(self):
        self.engine.close()
        self.assertFalse(self.engine.worker.is_alive())
        self.assertFalse(self.engine.server_worker.is_alive())

    def wait_for(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.engine.snapshot()
            if predicate(state):
                return state
            self.engine.stopped.wait(0.02)
        self.fail(f"Monitor did not reach expected state: {self.engine.snapshot()}")

    def test_separate_requested_and_applied_state_tracks_demo(self):
        self.engine.submit({"action": "demo", "monitor_state": 2, "armed": False})
        state = self.wait_for(lambda state: state["routes"][0]["expected"] is True and state["routes"][0]["applied"] is True)
        self.assertFalse(state["armed"])
        self.assertEqual(state["routes"][0]["owner"], "Preview")
        for route in state["routes"][1:]:
            self.assertIsNone(route["expected"])
            self.assertFalse(route["applied"])

    def test_arm_alone_leaves_monitoring_off(self):
        self.engine.submit({"action": "demo", "monitor_state": 4, "armed": True})
        state = self.wait_for(lambda state: state["armed"])
        self.assertFalse(state["routes"][0]["expected"])
        self.assertFalse(state["routes"][0]["applied"])

    def test_ardour_waiting_is_unknown_then_follows_telemetry(self):
        self.engine.submit({"action": "configure", "mode": "ardour", "session_path": "/session",
                            "track_id": "track-1", "input_port": "system:capture_1"})
        state = self.wait_for(lambda state: state["mode"] == "ardour")
        self.assertIsNone(state["routes"][0]["expected"])
        packet = padded(ADDRESS.encode() + b"\0") + padded(TAGS.encode() + b"\0")
        for value in ("/session", "track-1", "system:capture_1", "Test vocal"):
            packet += padded(value.encode() + b"\0")
        packet += struct.pack(">7i", 1, 1, 1, 2, 1, 0, 0)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(packet, self.engine.listener.getsockname())
        state = self.wait_for(lambda state: state["routes"][0]["expected"] is True and state["routes"][0]["applied"] is True)
        self.assertEqual(state["track_name"], "Test vocal")
        state = self.wait_for(lambda state: state["error"] is not None and state["routes"][0]["applied"] is False)
        self.assertIsNone(state["routes"][0]["expected"])
        self.assertIn("heartbeat", state["error"].lower())

    def test_readback_loss_does_not_present_old_state_as_current(self):
        with self.engine.server.simulator.lock:
            self.engine.server.simulator.backend.unavailable = True
        state = self.wait_for(lambda state: not state["readback_valid"])
        self.assertTrue(all(route["applied"] is None for route in state["routes"]))
        self.assertIsNotNone(state["error"])

    def test_invalid_actions_are_rejected_before_queueing(self):
        for action in (
            {"action": "demo", "monitor_state": True, "armed": False},
            {"action": "demo", "monitor_state": 2, "armed": 1},
            {"action": "configure", "mode": "hardware"},
            {"action": "configure", "mode": "demo", "port": 12345},
        ):
            with self.assertRaises(ValueError):
                validate_action(action)


class MonitorHTTPChecks(unittest.TestCase):
    def setUp(self):
        self.engine = MonitorEngine()
        self.engine.start()
        self.http = MonitorHTTPServer(("127.0.0.1", 0), MonitorHandler)
        self.http.engine = self.engine
        self.http.token = "test-secret"
        self.worker = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.01})
        self.worker.start()
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.worker.join(timeout=2)
        self.engine.close()

    def test_private_state_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as unauthorized:
            urllib.request.urlopen(self.url + "/api/state", timeout=2)
        self.assertEqual(unauthorized.exception.code, 401)
        unauthorized.exception.close()
        request = urllib.request.Request(self.url + "/api/state", headers={"Authorization": "Bearer test-secret"})
        with urllib.request.urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("mode", json.load(response))

    def test_action_endpoint_validates_before_accepting(self):
        for body, status in (({"action": "reset_everything"}, 400),
                             ({"action": "demo", "monitor_state": 2, "armed": False}, 202)):
            request = urllib.request.Request(self.url + "/api/action", data=json.dumps(body).encode(),
                                             headers={"Authorization": "Bearer test-secret", "Content-Type": "application/json"})
            if status == 400:
                with self.assertRaises(urllib.error.HTTPError) as invalid:
                    urllib.request.urlopen(request, timeout=2)
                self.assertEqual(invalid.exception.code, status)
                invalid.exception.close()
            else:
                with urllib.request.urlopen(request, timeout=2) as response:
                    self.assertEqual(response.status, status)


if __name__ == "__main__":
    unittest.main()
