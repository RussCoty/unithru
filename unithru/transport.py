import copy
import secrets
import socket
import socketserver
import time

from unithru.osc import MAX_PACKET, PREFIX, decode, encode
from unithru.simulator import Simulator
from jsonschema import ValidationError

from tools.validate import make_validator, validate_message


class DatagramHandler(socketserver.BaseRequestHandler):
    def handle(self):
        packet, connection = self.request
        if self.client_address[0] != "127.0.0.1":
            return
        if not self.server.admit_packet():
            return
        try:
            address, request = decode(packet)
            response = self.server.simulator.handle(address, request, self.client_address)
            if response is None:
                return
            encoded = encode(response)
        except (ValueError, UnicodeError, RecursionError):
            self.server.rejected_packets += 1
            return
        if self.server.drop_first_reply == request["op"]:
            self.server.drop_first_reply = None
            self.server.dropped_replies += 1
            return
        connection.sendto(encoded, self.client_address)


class LoopbackServer(socketserver.UDPServer):
    max_packet_size = MAX_PACKET + 1

    def __init__(self, port=0, simulator=None, drop_first_reply=None):
        self.simulator = simulator if simulator is not None else Simulator()
        self.drop_first_reply = drop_first_reply
        self.dropped_replies = 0
        self.rejected_packets = 0
        self.rate_dropped_packets = 0
        self.tokens = 200.0
        self.token_time = time.monotonic()
        super().__init__(("127.0.0.1", port), DatagramHandler)

    def admit_packet(self):
        now = time.monotonic()
        self.tokens = min(200.0, self.tokens + (now - self.token_time) * 200)
        self.token_time = now
        if self.tokens < 1:
            self.rate_dropped_packets += 1
            return False
        self.tokens -= 1
        return True

    def service_actions(self):
        self.simulator.tick()


class Client:
    def __init__(self, port, client_id="simulator-demo"):
        self.server = ("127.0.0.1", port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.sequence = 0
        self.request_prefix = secrets.token_hex(8)
        self.session_id = None
        self.epoch = None
        self.uncertain = False
        self.retries = 0
        self.client_id = client_id
        self.validator = make_validator()

    def close(self):
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()

    def call(self, operation, body):
        if self.uncertain and operation not in ("snapshot", "hello"):
            raise RuntimeError("Prior mutation is uncertain; inspect state before starting another session")
        self.sequence += 1
        request = {"version": "0.1", "request_id": f"request-{self.request_prefix}-{self.sequence}", "op": operation, "body": body}
        if operation != "hello":
            if self.session_id is None:
                raise RuntimeError("Call hello first")
            request.update(session_id=self.session_id, epoch=self.epoch)
        return self.exchange(request)

    def exchange(self, request):
        packet = encode(request)
        mutation = request["op"] not in ("hello", "snapshot")
        for attempt, timeout in enumerate((0.1, 0.2, 0.4, 0.8, 0.8)):
            self.socket.sendto(packet, self.server)
            if attempt:
                self.retries += 1
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                self.socket.settimeout(max(0.001, deadline - time.monotonic()))
                try:
                    incoming, endpoint = self.socket.recvfrom(MAX_PACKET + 1)
                except socket.timeout:
                    break
                if endpoint != self.server:
                    continue
                try:
                    address, response = decode(incoming)
                except (ValueError, UnicodeError, RecursionError):
                    continue
                if address != PREFIX + "result/" + request["op"] or response.get("request_id") != request["request_id"]:
                    continue
                if response.get("status") not in ("ok", "error", "indeterminate"):
                    continue
                try:
                    validate_message(response, self.validator)
                except (ValidationError, ValueError, RecursionError):
                    continue
                if response["op"] != request["op"]:
                    continue
                if request["op"] != "hello" and response["epoch"] != request["epoch"]:
                    if response.get("body", {}).get("code") != "epoch_mismatch":
                        continue
                if response["status"] == "indeterminate":
                    self.uncertain = True
                return response
        if mutation:
            self.uncertain = True
        raise TimeoutError(f"No correlated reply to {request['op']}; applied state is unknown")

    def hello(self):
        response = self.call("hello", {"versions": ["0.1"], "client_id": self.client_id})
        if response["status"] != "ok":
            raise RuntimeError(response["body"])
        self.session_id = response["body"]["session_id"]
        self.epoch = response["epoch"]
        return response


def run_demo(server):
    simulator = server.simulator
    with Client(server.server_address[1]) as client:
        discovery = client.hello()
        validate_message(discovery, simulator.validator)
        snapshot = client.call("snapshot", {"device_id": discovery["body"]["device_id"]})
        route = snapshot["body"]["routes"][0]
        baseline = copy.deepcopy(route)
        acquired = client.call("acquire", {
            "route_id": route["route_id"], "expected_route_revision": route["route_revision"],
            "duration_ms": 3000, "expiry_policy": "restore",
        })
        if acquired["status"] != "ok":
            raise RuntimeError(acquired["body"])
        route = acquired["body"]["route"]
        applied = client.call("set_route", {
            "route_id": route["route_id"], "lease_id": route["lease_id"],
            "expected_route_revision": route["route_revision"], "enabled": True,
            "gain_db": -11.75, "pan": 0, "pan_law": route["pan_law"],
        })
        if applied["status"] != "ok":
            raise RuntimeError(applied["body"])
        renewed = client.call("renew", {"lease_id": route["lease_id"], "duration_ms": 3000})
        released = client.call("release", {"lease_id": route["lease_id"], "cleanup": "restore"})
        for response in (snapshot, acquired, applied, renewed, released):
            validate_message(response, simulator.validator)
            if response["status"] != "ok":
                raise RuntimeError(response["body"])
        restored = released["body"]["route"]
        if any(restored[field] != baseline[field] for field in ("enabled", "gain_db", "pan", "pan_law")):
            raise RuntimeError("Baseline restoration failed")
        return {"endpoint": list(server.server_address), "applied_route": applied["body"]["route"],
                "restored_route": restored, "retry_count": client.retries,
                "dropped_replies": server.dropped_replies, "backend_control_writes": simulator.backend.write_count}
