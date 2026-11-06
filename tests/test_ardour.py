import json
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import unittest
from pathlib import Path

from lupa.lua53 import LuaRuntime

from unithru.ardour import ADDRESS, TAGS, PreviewController, TrackState, decode_state
from unithru.osc import padded
from unithru.transport import LoopbackServer


ROOT = Path(__file__).resolve().parents[1]


def packet_from_values(values):
    address, tags, *arguments = values
    packet = padded(address.encode() + b"\0") + padded(("," + tags).encode() + b"\0")
    for kind, value in zip(tags, arguments):
        if kind == "s":
            packet += padded(value.encode("utf-8") + b"\0")
        else:
            packet += struct.pack(">i", value)
    return packet


class HookChecks(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute((ROOT / "tests/ardour_hook_fixture.lua").read_text())
        self.lua.execute((ROOT / "integrations/ardour/unithru_preview.lua").read_text())
        self.callback = self.lua.globals().factory(self.lua.table_from({
            "port": "12345", "track_id": "track-7", "input_port": "system:capture_1",
        }))

    def state(self):
        self.callback()
        emitted = self.lua.globals().emitted
        values = [emitted[index] for index in range(1, len(emitted) + 1)]
        return decode_state(packet_from_values(values))

    def test_actual_lua53_hook_emits_mapped_input_intent(self):
        state = self.state()
        self.assertEqual(state.track_id, "track-7")
        self.assertEqual(state.session_path, "/sessions/preview")
        self.assertTrue(state.wants_input)
        self.assertEqual(state.armed, 0)
        self.assertEqual(self.lua.globals().emitted_uri, "osc.udp://127.0.0.1:12345")
        self.assertEqual(self.state().sequence, 2)

    def test_arm_alone_does_not_enable_disk_monitoring(self):
        self.lua.globals().armed = True
        self.lua.globals().monitor_state = 4
        state = self.state()
        self.assertEqual(state.armed, 1)
        self.assertFalse(state.wants_input)

    def test_all_monitor_states(self):
        for mode, expected in ((0, False), (2, True), (4, False), (6, True)):
            self.lua.globals().monitor_state = mode
            self.assertEqual(self.state().wants_input, expected)

    def test_changed_or_multiple_connections_disable_preview(self):
        for connections in ([], ["system:capture_2"], ["system:capture_1", "other:out"]):
            self.lua.globals().connections = self.lua.table_from(connections)
            self.assertFalse(self.state().wants_input)

    def test_stereo_missing_or_midi_track_is_not_remapped(self):
        self.lua.globals().audio_channels = 2
        self.assertFalse(self.state().wants_input)
        self.lua.globals().audio_channels = 1
        self.lua.globals().track_present = False
        self.assertFalse(self.state().present)
        self.lua.globals().track_present = True
        self.lua.globals().is_audio = False
        self.assertFalse(self.state().present)

    def test_invalid_configuration_refused(self):
        for port in ("0", "-1", "65536", "1.5", "remote.example:12345"):
            with self.assertRaises(Exception):
                self.lua.globals().factory(self.lua.table_from({
                    "port": port, "track_id": "track-7", "input_port": "system:capture_1",
                }))


class PreviewChecks(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.server = LoopbackServer(port=0)
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.worker.start()
        self.controller = PreviewController(self.server.server_address[1], "/sessions/preview", "track-7",
                                            "system:capture_1", clock=lambda: self.now)
        self.endpoint = ("127.0.0.1", 40001)

    def tearDown(self):
        try:
            self.controller.close()
        finally:
            self.server.shutdown()
            self.server.server_close()
            self.worker.join(timeout=2)

    def state(self, sequence=1, monitor=2, matches=1, present=1):
        return TrackState("/sessions/preview", "track-7", "system:capture_1", "Vocal", sequence,
                          present, matches, monitor, 0, 0, 0)

    def test_daw_input_and_disk_modes_control_real_udp_fake_mixer(self):
        self.assertTrue(self.controller.receive(self.state(), self.endpoint))
        self.assertTrue(self.controller.route["enabled"])
        self.controller.receive(self.state(2, monitor=4), self.endpoint)
        self.assertFalse(self.controller.route["enabled"])
        self.controller.stop()
        self.assertFalse(self.server.simulator.leases)

    def test_lua_hook_to_real_unithru_wire_exchange(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute((ROOT / "tests/ardour_hook_fixture.lua").read_text())
        lua.execute((ROOT / "integrations/ardour/unithru_preview.lua").read_text())
        callback = lua.globals().factory(lua.table_from({
            "port": "12345", "track_id": "track-7", "input_port": "system:capture_1",
        }))
        for monitor, enabled in ((2, True), (4, False), (6, True), (0, False)):
            lua.globals().monitor_state = monitor
            callback()
            emitted = lua.globals().emitted
            values = [emitted[index] for index in range(1, len(emitted) + 1)]
            self.controller.receive(decode_state(packet_from_values(values)), self.endpoint)
            self.assertEqual(self.controller.route["enabled"], enabled)

    def test_wrong_session_or_track_cannot_acquire(self):
        state = self.state()
        for field in ("session_path", "track_id", "input_port"):
            values = vars(state).copy()
            values[field] = "wrong"
            self.assertFalse(self.controller.receive(TrackState(**values), self.endpoint))
        self.assertFalse(self.server.simulator.leases)

    def test_reordered_duplicate_and_new_endpoint_rejected(self):
        self.controller.receive(self.state(2), self.endpoint)
        self.assertFalse(self.controller.receive(self.state(1, monitor=4), self.endpoint))
        self.assertFalse(self.controller.receive(self.state(2, monitor=4), self.endpoint))
        self.assertFalse(self.controller.receive(self.state(3, monitor=4), ("127.0.0.1", 40002)))
        self.assertTrue(self.controller.route["enabled"])

    def test_input_change_or_track_removal_disables(self):
        self.controller.receive(self.state(), self.endpoint)
        self.controller.receive(self.state(2, matches=0), self.endpoint)
        self.assertFalse(self.controller.route["enabled"])
        self.controller.receive(self.state(3), self.endpoint)
        self.controller.receive(self.state(4, present=0), self.endpoint)
        self.assertFalse(self.controller.route["enabled"])

    def test_heartbeat_timeout_disables_releases_and_requires_restart(self):
        self.controller.receive(self.state(), self.endpoint)
        self.now += 1.5
        with self.assertRaises(RuntimeError):
            self.controller.tick()
        self.assertFalse(self.server.simulator.leases)
        self.assertFalse(self.server.simulator.routes["route-vocal-cue"]["enabled"])
        self.assertFalse(self.controller.receive(self.state(2), self.endpoint))

    def test_manual_edit_halts_without_reacquiring(self):
        self.controller.receive(self.state(), self.endpoint)
        with self.server.simulator.lock:
            self.server.simulator.backend.routes["route-vocal-cue"]["gain_db"] = -2
        self.now += 0.5
        with self.assertRaises(RuntimeError):
            self.controller.tick()
        self.assertTrue(self.controller.halted)
        self.assertEqual(self.server.simulator.backend.routes["route-vocal-cue"]["gain_db"], -2)
        self.assertFalse(self.controller.receive(self.state(2), self.endpoint))

    def test_tick_renews_and_refreshes_revision(self):
        self.controller.receive(self.state(), self.endpoint)
        self.now += 1
        self.controller.tick()
        self.assertEqual(self.controller.route["route_revision"], 3)
        self.controller.receive(self.state(2, monitor=4), self.endpoint)
        self.assertFalse(self.controller.route["enabled"])


class TelemetryChecks(unittest.TestCase):
    def test_rejects_other_bindings_truncation_and_invalid_flags(self):
        values = [ADDRESS, TAGS[1:], "/sessions/preview", "track-7", "system:capture_1", "Vocal", 1, 1, 1, 2, 0, 0, 0]
        packet = packet_from_values(values)
        self.assertTrue(decode_state(packet).wants_input)
        for invalid in (packet[:-4], packet + bytes(4), packet.replace(b",ssss", b",bsss")):
            with self.assertRaises(ValueError):
                decode_state(invalid)
        values[-1] = 3
        with self.assertRaises(ValueError):
            decode_state(packet_from_values(values))


class PreviewCommandChecks(unittest.TestCase):
    def test_command_accepts_lua_telemetry_over_udp_and_exits_cleanly(self):
        process = subprocess.Popen([
            sys.executable, "-m", "unithru", "ardour-preview", "--session-path", "/sessions/preview",
            "--track-id", "track-7", "--input-port", "system:capture_1",
        ], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def read_event():
            ready, _, _ = select.select([process.stdout], [], [], 5)
            self.assertTrue(ready, "Preview did not emit an event")
            line = process.stdout.readline()
            self.assertTrue(line, "Preview exited unexpectedly")
            return json.loads(line)

        try:
            started = read_event()
            self.assertFalse(started["audio_path_changed"])
            self.assertEqual(started["listener"][0], "127.0.0.1")
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                lua = LuaRuntime(unpack_returned_tuples=True)
                lua.execute((ROOT / "tests/ardour_hook_fixture.lua").read_text())
                lua.execute((ROOT / "integrations/ardour/unithru_preview.lua").read_text())
                lua.globals().on_send = lambda *values: sender.sendto(packet_from_values(values), tuple(started["listener"]))
                callback = lua.globals().factory(lua.table_from({
                    "port": str(started["listener"][1]), "track_id": "track-7", "input_port": "system:capture_1",
                }))
                callback()
                self.assertTrue(read_event()["simulator_route"]["enabled"])
                lua.globals().monitor_state = 4
                callback()
                self.assertFalse(read_event()["simulator_route"]["enabled"])
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
            try:
                _, errors = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise
        self.assertEqual(process.returncode, 0, errors)


if __name__ == "__main__":
    unittest.main()
