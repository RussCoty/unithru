import copy
import json
import socket
import struct
import threading
import time
from dataclasses import dataclass

from unithru.osc import MAX_PACKET, read_string
from unithru.transport import Client, LoopbackServer


ADDRESS = "/unithru/ardour-preview/v1/state"
TAGS = ",ssssiiiiiii"


@dataclass(frozen=True)
class TrackState:
    session_path: str
    track_id: str
    input_port: str
    name: str
    sequence: int
    present: int
    input_matches: int
    monitor_state: int
    armed: int
    rolling: int
    recording: int

    @property
    def wants_input(self):
        return bool(self.present and self.input_matches and self.monitor_state & 2)


def decode_state(packet):
    if not packet or len(packet) > MAX_PACKET or len(packet) % 4:
        raise ValueError("Invalid telemetry size")
    address, offset = read_string(packet, 0)
    tags, offset = read_string(packet, offset)
    if address != ADDRESS or tags != TAGS:
        raise ValueError("Not an Ardour preview state packet")
    values = []
    for field in range(4):
        end = packet.find(b"\0", offset)
        if end < 0:
            raise ValueError("Unterminated telemetry string")
        following = (end + 4) // 4 * 4
        if following > len(packet) or any(packet[end:following]):
            raise ValueError("Invalid telemetry string padding")
        value = packet[offset:end].decode("utf-8")
        limit = 4096 if field == 0 else 512
        if len(value) > limit:
            raise ValueError("Telemetry string exceeds limit")
        values.append(value)
        offset = following
    if len(packet) - offset != 28:
        raise ValueError("Expected seven int32 telemetry arguments")
    numbers = struct.unpack(">7i", packet[offset:])
    state = TrackState(*values, *numbers)
    if state.sequence < 1 or state.monitor_state not in (0, 2, 4, 6):
        raise ValueError("Invalid sequence or Ardour MonitorState")
    if any(value not in (0, 1) for value in (
        state.present, state.input_matches, state.armed, state.rolling, state.recording,
    )):
        raise ValueError("Telemetry flags must be zero or one")
    return state


class PreviewController:
    def __init__(self, port, session_path, track_id, input_port, clock=time.monotonic):
        self.clock = clock
        self.mapping = (session_path, track_id, input_port)
        self.client = Client(port, client_id="ardour-preview")
        self.client.hello()
        self.route = None
        self.lease_id = None
        self.sequence = 0
        self.endpoint = None
        self.last_received = None
        self.last_refresh = self.clock()
        self.last_renew = self.clock()
        self.desired = False
        self.halted = False

    def _ok(self, response):
        if response["status"] != "ok":
            self.halted = True
            raise RuntimeError(f"Preview halted: {response['body']}")
        return response["body"]

    def _refresh(self):
        snapshot = self._ok(self.client.call("snapshot", {"device_id": "device-demo"}))
        route = next(route for route in snapshot["routes"] if route["route_id"] == "route-vocal-cue")
        if self.lease_id is not None and (
            route["lease_id"] != self.lease_id or route["owner_session"] != self.client.session_id
        ):
            self.halted = True
            raise RuntimeError("Preview ownership lost; restart to reacquire explicitly")
        self.route = copy.deepcopy(route)
        self.last_refresh = self.clock()

    def _set(self, enabled):
        body = {"route_id": self.route["route_id"], "lease_id": self.lease_id,
                "expected_route_revision": self.route["route_revision"], "enabled": enabled,
                "gain_db": -12, "pan": 0, "pan_law": "mono_equal_power"}
        self.route = self._ok(self.client.call("set_route", body))["route"]

    def receive(self, state, endpoint):
        if self.halted or self.mapping != (state.session_path, state.track_id, state.input_port):
            return False
        if self.endpoint is not None and endpoint != self.endpoint:
            return False
        if state.sequence <= self.sequence:
            return False
        self.endpoint = endpoint
        self.sequence = state.sequence
        self.last_received = self.clock()
        self.desired = state.wants_input
        if self.lease_id is None:
            self._refresh()
            acquired = self._ok(self.client.call("acquire", {
                "route_id": self.route["route_id"], "expected_route_revision": self.route["route_revision"],
                "duration_ms": 3000, "expiry_policy": "restore",
            }))
            self.route = acquired["route"]
            self.lease_id = self.route["lease_id"]
            self.last_renew = self.clock()
        if self.route["enabled"] != self.desired:
            self._set(self.desired)
        return True

    def tick(self):
        if self.halted or self.lease_id is None:
            return
        now = self.clock()
        if self.last_received is not None and now - self.last_received >= 1.5:
            self.stop()
            self.halted = True
            raise RuntimeError("Ardour heartbeat expired; preview disabled and released")
        if now - self.last_refresh >= 0.5:
            self._refresh()
        if now - self.last_renew >= 1:
            self.route = self._ok(self.client.call("renew", {"lease_id": self.lease_id, "duration_ms": 3000}))["route"]
            self.last_renew = now

    def stop(self):
        if self.lease_id is not None and not self.halted:
            self._refresh()
            if self.route["enabled"]:
                self._set(False)
            self._ok(self.client.call("release", {"lease_id": self.lease_id, "cleanup": "hold"}))
            self.lease_id = None

    def close(self):
        try:
            self.stop()
        finally:
            self.client.close()


def run_preview(options):
    with LoopbackServer(port=0) as server, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", options.listen_port))
        listener.settimeout(0.1)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        worker.start()
        controller = None
        try:
            controller = PreviewController(server.server_address[1], options.session_path, options.track_id, options.input_port)
            print(json.dumps({"mode": "ardour-simulator-preview", "listener": list(listener.getsockname()),
                              "session_path": options.session_path, "track_id": options.track_id,
                              "input_port": options.input_port, "audio_path_changed": False}), flush=True)
            while True:
                try:
                    packet, endpoint = listener.recvfrom(MAX_PACKET + 1)
                except socket.timeout:
                    packet = None
                if packet is not None:
                    try:
                        state = decode_state(packet)
                    except (ValueError, UnicodeError):
                        state = None
                    if state is not None and endpoint[0] == "127.0.0.1":
                        previous = controller.desired
                        if controller.receive(state, endpoint) and previous != controller.desired:
                            print(json.dumps({"track": state.name, "armed": bool(state.armed),
                                              "monitor_state": state.monitor_state,
                                              "simulator_route": controller.route}), flush=True)
                controller.tick()
        except KeyboardInterrupt:
            pass
        finally:
            try:
                if controller is not None:
                    controller.close()
            finally:
                server.shutdown()
                worker.join(timeout=2)
