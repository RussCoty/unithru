import copy
import hmac
import json
import queue
import secrets
import signal
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools.validate import reject_constant, unique_object
from unithru.ardour import PreviewController, TrackState, decode_state
from unithru.osc import MAX_PACKET
from unithru.transport import Client, LoopbackServer


def validate_action(action):
    if not isinstance(action, dict):
        raise ValueError("Expected an action object")
    if action.get("action") == "demo":
        if set(action) != {"action", "monitor_state", "armed"}:
            raise ValueError("Demo requires monitor_state and armed")
        if type(action["monitor_state"]) is not int or action["monitor_state"] not in (0, 2, 4, 6):
            raise ValueError("Unknown monitoring state")
        if type(action["armed"]) is not bool:
            raise ValueError("Armed must be a boolean")
    elif action.get("action") == "configure":
        if action.get("mode") == "demo":
            if set(action) != {"action", "mode"}:
                raise ValueError("Unexpected demo configuration fields")
        elif action.get("mode") == "ardour":
            if set(action) != {"action", "mode", "session_path", "track_id", "input_port"}:
                raise ValueError("Ardour requires session_path, track_id and input_port")
            for field in ("session_path", "track_id", "input_port"):
                if not isinstance(action[field], str) or not action[field].strip() or len(action[field]) > 1024:
                    raise ValueError(f"Invalid {field}")
        else:
            raise ValueError("Choose demo or ardour")
    else:
        raise ValueError("Unknown action")
    return copy.deepcopy(action)


class MonitorEngine:
    def __init__(self):
        self.server = LoopbackServer(port=0)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.setblocking(False)
        self.lock = threading.Lock()
        self.commands = queue.Queue(maxsize=16)
        self.stopped = threading.Event()
        self.worker = None
        self.server_worker = None
        self.controller = None
        self.observer = None
        self.mode = "demo"
        self.mapping = ("/unithru/demo", "demo-track", "demo:input-1")
        self.monitor_state = 4
        self.armed = False
        self.sequence = 0
        self.track_state = None
        self.last_demo = 0
        self.last_observation = 0
        self.observed_at = None
        self.routes = []
        self.error = None
        self.history = []
        self.published = {"mode": "demo", "status": "Starting", "routes": [], "events": []}

    def start(self):
        self.server_worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.server_worker.start()
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def submit(self, action):
        self.commands.put_nowait(validate_action(action))

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.published)

    def _event(self, message):
        if not self.history or self.history[0]["message"] != message:
            self.history.insert(0, {"time": time.strftime("%H:%M:%S"), "message": message})
            del self.history[12:]

    def _configure(self, action):
        if self.controller is not None:
            try:
                self.controller.close()
            finally:
                self.controller = None
        self.mode = action["mode"]
        self.mapping = ("/unithru/demo", "demo-track", "demo:input-1") if self.mode == "demo" else (
            action["session_path"], action["track_id"], action["input_port"],
        )
        self.controller = PreviewController(self.server.server_address[1], *self.mapping)
        self.track_state = None
        self.error = None
        self.sequence = 0
        self.last_demo = 0
        self._event("Demo connected to private simulator" if self.mode == "demo" else "Waiting for the mapped Ardour track")

    def _readback(self):
        if self.observer.sequence >= 500:
            self.observer.close()
            self.observer = Client(self.server.server_address[1], client_id="monitor-observer")
            self.observer.hello()
        response = self.observer.call("snapshot", {"device_id": "device-demo"})
        if response["status"] != "ok":
            raise RuntimeError(f"Readback unavailable: {response['body']['message']}")
        self.routes = response["body"]["routes"]
        self.observed_at = time.monotonic()
        self.last_observation = self.observed_at

    def _receive(self, state, endpoint):
        previous = self.controller.desired
        if self.controller.receive(state, endpoint):
            self.track_state = state
            if previous != state.wants_input:
                self._event("Input monitoring requested ON" if state.wants_input else "Input monitoring requested OFF")

    def _publish(self):
        now = time.monotonic()
        controller = self.controller
        fresh = controller is not None and controller.last_received is not None and now - controller.last_received < 1.5
        healthy = self.error is None and controller is not None and not controller.halted
        readback_valid = self.observed_at is not None and now - self.observed_at < 1.5
        state = self.track_state
        status = self.error or ("Following Ardour" if self.mode == "ardour" and fresh else
                                "Waiting for Ardour" if self.mode == "ardour" else "Demo connected")
        if fresh and state is not None and not state.present:
            status = "Mapped audio track missing — requested OFF"
        elif fresh and state is not None and not state.input_matches:
            status = "Input mapping changed — requested OFF"
        published_routes = []
        for route in self.routes:
            managed = route["route_id"] == "route-vocal-cue"
            published_routes.append({
                "route_id": route["route_id"], "source_id": route["source_id"],
                "destination_id": route["destination_id"], "managed": managed,
                "expected": controller.desired if managed and fresh and healthy else None,
                "applied": route["enabled"] if readback_valid else None,
                "gain_db": route["gain_db"], "pan": route["pan"], "revision": route["route_revision"],
                "owner": "Preview" if controller is not None and route["owner_session"] == controller.client.session_id else
                         "Other" if route["owner_session"] is not None else "None",
            })
        value = {"mode": self.mode, "status": status, "error": self.error,
                 "listener_port": self.listener.getsockname()[1], "session_path": self.mapping[0],
                 "track_id": self.mapping[1], "input_port": self.mapping[2],
                 "track_name": state.name if state is not None else "No track received",
                 "monitor_state": state.monitor_state if state is not None else self.monitor_state,
                 "armed": bool(state.armed) if state is not None else self.armed,
                 "source_fresh": fresh, "readback_valid": readback_valid,
                 "heartbeat_age_ms": round((now - controller.last_received) * 1000) if fresh else None,
                 "routes": published_routes, "events": copy.deepcopy(self.history)}
        with self.lock:
            self.published = value

    def _run(self):
        try:
            self.observer = Client(self.server.server_address[1], client_id="monitor-observer")
            self.observer.hello()
            self._configure({"mode": "demo"})
            while not self.stopped.is_set():
                try:
                    action = self.commands.get_nowait()
                except queue.Empty:
                    action = None
                try:
                    if action is not None:
                        if action["action"] == "configure":
                            self._configure(action)
                        elif self.mode != "demo":
                            raise ValueError("Demo controls are disabled while following Ardour")
                        else:
                            self.monitor_state, self.armed = action["monitor_state"], action["armed"]
                            self.last_demo = 0
                    now = time.monotonic()
                    if self.controller is not None and not self.controller.halted and self.error is None:
                        if self.mode == "demo" and now - self.last_demo >= 0.25:
                            self.sequence += 1
                            state = TrackState(*self.mapping, "Demo vocal", self.sequence, 1, 1,
                                               self.monitor_state, int(self.armed), 0, 0)
                            self._receive(state, ("127.0.0.1", 0))
                            self.last_demo = now
                        for packet_index in range(32):
                            try:
                                packet, endpoint = self.listener.recvfrom(MAX_PACKET + 1)
                            except BlockingIOError:
                                break
                            if self.mode == "ardour" and endpoint[0] == "127.0.0.1":
                                try:
                                    state = decode_state(packet)
                                except (ValueError, UnicodeError):
                                    continue
                                self._receive(state, endpoint)
                        self.controller.tick()
                    if now - self.last_observation >= 0.25:
                        self._readback()
                except (OSError, RuntimeError, TimeoutError, ValueError) as error:
                    self.error = str(error)
                    self._event(self.error)
                    self.last_observation = time.monotonic()
                self._publish()
                self.stopped.wait(0.05)
        except Exception as error:
            self.error = f"Monitor stopped: {error}"
            self._event(self.error)
            self._publish()
        finally:
            if self.controller is not None:
                try:
                    self.controller.close()
                except (OSError, RuntimeError, TimeoutError):
                    pass
            if self.observer is not None:
                self.observer.close()

    def close(self):
        self.stopped.set()
        if self.worker is not None:
            self.worker.join(timeout=6)
        if self.server_worker is not None:
            self.server.shutdown()
            self.server_worker.join(timeout=2)
        self.server.server_close()
        self.listener.close()


class MonitorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8


class MonitorHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(2)

    def log_message(self, *arguments):
        pass

    def _authorized(self):
        value = self.headers.get("Authorization", "")
        return hmac.compare_digest(value, "Bearer " + self.server.token)

    def _reply(self, status, value):
        payload = json.dumps(value, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if not self._authorized():
            self._reply(401, {"error": "Unauthorized"})
        elif self.path == "/api/state":
            self._reply(200, self.server.engine.snapshot())
        else:
            self._reply(404, {"error": "Not found"})

    def do_POST(self):
        if not self._authorized():
            self._reply(401, {"error": "Unauthorized"})
            return
        if self.path != "/api/action":
            self._reply(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("Action body must be 1–4096 bytes")
            action = json.loads(self.rfile.read(length).decode("utf-8"),
                                parse_constant=reject_constant, object_pairs_hook=unique_object)
            self.server.engine.submit(action)
        except (ValueError, UnicodeError) as error:
            self._reply(400, {"error": str(error)})
            return
        except queue.Full:
            self._reply(429, {"error": "Action queue is full"})
            return
        self._reply(202, {"queued": True})


def run_monitor_server():
    engine = MonitorEngine()
    stopped = threading.Event()
    for termination_signal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(termination_signal, lambda *_: stopped.set())
    try:
        with MonitorHTTPServer(("127.0.0.1", 0), MonitorHandler) as http:
            http.token = secrets.token_hex(32)
            http.engine = engine
            http.timeout = 0.1
            engine.start()
            print(json.dumps({"url": f"http://127.0.0.1:{http.server_address[1]}", "token": http.token}), flush=True)
            while not stopped.is_set():
                http.handle_request()
    finally:
        engine.close()
