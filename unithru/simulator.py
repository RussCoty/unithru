import copy
import math
import re
import secrets
import threading
import time
from decimal import Decimal, ROUND_HALF_DOWN

from jsonschema import ValidationError

from tools.validate import ROOT, load_json, make_validator, validate_message
from unithru.osc import PREFIX, OPERATIONS, encode


CONTROLS = ("enabled", "gain_db", "pan", "pan_law")
MAX_REVISION = 2147483647
TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


def controls(route):
    return {field: route[field] for field in CONTROLS}


class BackendFailure(Exception):
    pass


class FakeMixer:
    def __init__(self, routes):
        self.routes = {route["route_id"]: controls(route) for route in routes}
        self.write_count = 0
        self.fail_after = None
        self.defer_next = False
        self.pending = None
        self.unavailable = False

    def read(self, route_id):
        if self.unavailable:
            raise BackendFailure("Fake readback unavailable")
        return copy.deepcopy(self.routes[route_id])

    def apply(self, route_id, values):
        if self.unavailable:
            raise BackendFailure("Fake backend unavailable")
        if self.defer_next:
            self.defer_next = False
            self.pending = (route_id, copy.deepcopy(values))
            raise BackendFailure("Simulated deadline exceeded; backend work pending")
        fail_after, self.fail_after = self.fail_after, None
        for position, field in enumerate(CONTROLS):
            if fail_after == position:
                raise BackendFailure("Injected partial write failure")
            self.routes[route_id][field] = values[field]
            self.write_count += 1
        return self.read(route_id)

    def complete_pending(self):
        if self.pending is not None:
            route_id, values = self.pending
            self.pending = None
            self.apply(route_id, values)


class ProtocolFault(Exception):
    def __init__(self, code, message, route=None):
        super().__init__(message)
        self.code = code
        self.route = route


class Simulator:
    def __init__(self, clock=time.monotonic, max_sessions=32, cache_limit=4096):
        self.clock = clock
        self.max_sessions = max_sessions
        self.lock = threading.RLock()
        self.validator = make_validator()
        self.capabilities = load_json(ROOT / "examples/discovery.json")["body"]["capabilities"]
        self.capabilities["max_requests_per_session"] = cache_limit
        self.capabilities["destinations"].extend([
            {"id": "cue-b", "label": "Cue B", "channels": ["analog-out-5", "analog-out-6"]},
            {"id": "mono-cue", "label": "Mono cue", "channels": ["analog-out-7"]},
        ])
        self.capabilities["sources"].append({
            "id": "input-stereo", "label": "Stereo", "channels": ["analog-in-2", "analog-in-3"],
        })
        self.capabilities["topologies"].extend([
            {"source_channels": 1, "destination_channels": 1, "pan_law": "none"},
            {"source_channels": 2, "destination_channels": 2, "pan_law": "stereo_balance"},
        ])
        initial = load_json(ROOT / "examples/snapshot.json")["body"]["routes"][0]
        routes = [initial, {**initial, "route_id": "route-vocal-cue-b", "destination_id": "cue-b"},
                  {**initial, "route_id": "route-vocal-mono", "destination_id": "mono-cue", "pan_law": "none"},
                  {**initial, "route_id": "route-stereo-cue", "source_id": "input-stereo", "pan_law": "stereo_balance"}]
        self.routes = {route["route_id"]: copy.deepcopy(route) for route in routes}
        self.capabilities["max_routes"] = len(routes)
        self.capabilities["max_leases"] = len(routes)
        self.backend = FakeMixer(routes)
        self.resources = {route_id: {route_id} for route_id in self.routes}
        self.quarantined = set()
        self.sessions = {}
        self.hello_cache = {}
        self.leases = {}
        self.lost_leases = {}
        self.epoch = secrets.token_hex(16)
        self.revision = 0
        self.device_id = "device-demo"

    def _bump(self, route):
        self.revision += 1
        route["route_revision"] += 1

    def _forget_lease(self, route, reason="lease_expired"):
        lease_id = route["lease_id"]
        if lease_id:
            lease = self.leases.pop(lease_id)
            self.lost_leases[lease_id] = (lease["session_id"], reason, route["route_id"])
            if len(self.lost_leases) > 128:
                self.lost_leases.pop(next(iter(self.lost_leases)))
        route["lease_id"] = None
        route["owner_session"] = None

    def _observe(self, route):
        observed = self.backend.read(route["route_id"])
        if observed != controls(route):
            self._forget_lease(route, "conflict")
            route.update(observed)
            self._bump(route)
        return observed

    def _uncertain(self, route):
        self.quarantined.add(route["route_id"])
        self._forget_lease(route)
        try:
            route.update(self.backend.read(route["route_id"]))
        except BackendFailure:
            pass
        self._bump(route)

    def _cleanup(self, route, policy):
        lease = self.leases[route["lease_id"]]
        observed = self.backend.read(route["route_id"])
        if observed != lease["last"]:
            self._forget_lease(route, "conflict")
            route.update(observed)
            self._bump(route)
            raise ProtocolFault("conflict", "Mixer changed; baseline was not restored", route)
        if policy == "restore" and observed != lease["baseline"]:
            route.update(self.backend.apply(route["route_id"], lease["baseline"]))
        self._forget_lease(route)
        self._bump(route)

    def tick(self):
        with self.lock:
            headroom = 2 * len(self.routes) + 2
            if self.revision >= MAX_REVISION - headroom or any(
                route["route_revision"] >= MAX_REVISION - headroom for route in self.routes.values()
            ):
                try:
                    self.restart()
                except BackendFailure:
                    for route in self.routes.values():
                        if route["route_id"] not in self.quarantined:
                            self._uncertain(route)
                    return
            now = self.clock()
            for route in self.routes.values():
                if route["route_id"] in self.quarantined:
                    continue
                try:
                    self._observe(route)
                except BackendFailure:
                    self._uncertain(route)
                if route["lease_id"]:
                    lease = self.leases[route["lease_id"]]
                    session = self.sessions.get(lease["session_id"])
                    if now >= lease["deadline"] or session is None or now - session["last_seen"] >= 60:
                        try:
                            self._cleanup(route, lease["policy"])
                        except BackendFailure:
                            self._uncertain(route)
                        except ProtocolFault:
                            pass
            expired = [session_id for session_id, session in self.sessions.items() if now - session["last_seen"] >= 60]
            for session_id in expired:
                del self.sessions[session_id]
            self.hello_cache = {key: value for key, value in self.hello_cache.items() if now - value[0] < 60}

    def restart(self):
        with self.lock:
            observations = {route_id: self.backend.read(route_id) for route_id in self.routes}
            self.backend.pending = None
            self.sessions.clear()
            self.hello_cache.clear()
            self.leases.clear()
            self.lost_leases.clear()
            self.quarantined.clear()
            self.epoch = secrets.token_hex(16)
            self.revision = 0
            for route in self.routes.values():
                route.update(observations[route["route_id"]])
                route.update(owner_session=None, lease_id=None, route_revision=0)

    def reconcile(self, route_id):
        with self.lock:
            if self.backend.pending is not None:
                raise RuntimeError("Pending fake backend work must complete or be cancelled first")
            route = self.routes[route_id]
            route.update(self.backend.read(route_id))
            self.quarantined.discard(route_id)
            self._bump(route)

    def _result(self, request, body, status="ok"):
        return {"version": "0.1", "request_id": request["request_id"], "op": request["op"],
                "epoch": self.epoch, "revision": self.revision, "status": status, "body": copy.deepcopy(body)}

    def _error(self, request, code, message, route=None, uncertain=False):
        body = {"code": code, "message": message}
        if route is not None:
            if not self.backend.unavailable:
                body["route"] = copy.deepcopy(route)
            if uncertain:
                body["affected_resources"] = sorted(self.resources[route["route_id"]])
        return self._result(request, body, "indeterminate" if uncertain else "error")

    def handle(self, address, request, endpoint):
        with self.lock:
            self.tick()
            operation = request.get("op")
            request_id = request.get("request_id")
            if address.startswith(PREFIX + "result/"):
                return None
            if not isinstance(operation, str) or operation not in OPERATIONS or not isinstance(request_id, str) or not TOKEN.fullmatch(request_id):
                return None
            if address != PREFIX + operation:
                return self._error(request, "invalid_request", "OSC address does not match operation")
            if request.get("version") != "0.1":
                return self._error(request, "unsupported_version", "Only draft 0.1 is available")
            try:
                validate_message(request, self.validator)
                if "status" in request:
                    raise ValueError("Expected a request")
            except (ValidationError, ValueError, RecursionError):
                return self._error(request, "invalid_request", "Request does not match the core schema")
            if operation == "hello":
                return self._hello(request, endpoint)
            if request["epoch"] != self.epoch:
                return self._error(request, "epoch_mismatch", "Refresh discovery after epoch change")
            session = self.sessions.get(request["session_id"])
            if session is None or session["endpoint"] != endpoint:
                return self._error(request, "unknown_session", "Session is expired or belongs to another endpoint")
            session["last_seen"] = self.clock()
            cached = session["cache"].get(request_id)
            if cached:
                if cached[0] != request:
                    return self._error(request, "request_id_reuse", "Request ID already has different contents")
                return copy.deepcopy(cached[1])
            if len(session["cache"]) >= self.capabilities["max_requests_per_session"]:
                return self._error(request, "resource_exhausted", "Session request cache is full; allow leases to expire")
            try:
                body = self._dispatch(request)
                response = self._result(request, body)
            except ProtocolFault as error:
                route = error.route
                if route is None:
                    route_id = request["body"].get("route_id")
                    lease = self.leases.get(request["body"].get("lease_id"))
                    if lease:
                        route_id = lease["route_id"]
                    route = self.routes.get(route_id)
                response = self._error(request, error.code, str(error), route)
            except BackendFailure as error:
                route_id = request["body"].get("route_id")
                lease_id = request["body"].get("lease_id")
                if route_id is None and lease_id in self.leases:
                    route_id = self.leases[lease_id]["route_id"]
                if route_id in self.routes:
                    route = self.routes[route_id]
                    self._uncertain(route)
                    response = self._error(request, "backend_failure", str(error), route, uncertain=True)
                else:
                    response = self._error(request, "backend_failure", str(error))
                    response["status"] = "indeterminate"
                    response["body"]["affected_resources"] = sorted(self.routes)
            try:
                encode(response)
            except ValueError:
                response = self._error(request, "snapshot_too_large", "Complete response exceeds packet cap")
            session["cache"][request_id] = (copy.deepcopy(request), copy.deepcopy(response))
            return response

    def _hello(self, request, endpoint):
        key = (endpoint, request["request_id"])
        cached = self.hello_cache.get(key)
        if cached:
            if cached[1] != request:
                return self._error(request, "request_id_reuse", "Hello ID already has different contents")
            return copy.deepcopy(cached[2])
        if "0.1" not in request["body"]["versions"]:
            return self._error(request, "unsupported_version", "No common version")
        if len(self.sessions) >= self.max_sessions or len(self.hello_cache) >= self.max_sessions:
            return self._error(request, "resource_exhausted", "Session limit reached")
        session_id = secrets.token_hex(16)
        response = self._result(request, {"device_id": self.device_id, "session_id": session_id,
                                         "capabilities": self.capabilities})
        encode(response)
        self.sessions[session_id] = {"endpoint": endpoint, "last_seen": self.clock(), "cache": {}}
        self.hello_cache[key] = (self.clock(), copy.deepcopy(request), copy.deepcopy(response))
        return response

    def _route(self, route_id):
        route = self.routes.get(route_id)
        if route is None:
            raise ProtocolFault("not_found", "Unknown route")
        if any(self.resources[route_id] & self.resources[other] for other in self.quarantined):
            raise ProtocolFault("busy", "Resource is quarantined pending local reconciliation", route)
        return route

    def _owned(self, request):
        lease_id = request["body"]["lease_id"]
        lease = self.leases.get(lease_id)
        if lease is None:
            lost = self.lost_leases.get(lease_id)
            if lost and lost[0] == request["session_id"]:
                raise ProtocolFault(lost[1], "Lease ended; refresh actual state", self.routes[lost[2]])
            raise ProtocolFault("lease_expired", "No active lease with that ID")
        route = self._route(lease["route_id"])
        if lease["session_id"] != request["session_id"]:
            raise ProtocolFault("not_owner", "Lease belongs to another session", route)
        return route, lease

    def _duration(self, body):
        duration = body["duration_ms"]
        limits = self.capabilities["lease_ms"]
        if not limits["min"] <= duration <= limits["max"]:
            raise ProtocolFault("out_of_range", "Lease duration outside advertised limits")
        return duration

    def _cas(self, route, body):
        if route["route_revision"] != body["expected_route_revision"]:
            raise ProtocolFault("stale_revision", "Route revision has changed", route)

    def _dispatch(self, request):
        operation, body = request["op"], request["body"]
        if operation == "snapshot":
            if body["device_id"] != self.device_id:
                raise ProtocolFault("not_found", "Unknown device")
            for route in self.routes.values():
                self._observe(route)
            return {"device_id": self.device_id, "observed_ms": int(self.clock() * 1000),
                    "routes": list(self.routes.values())}
        if operation == "acquire":
            route = self._route(body["route_id"])
            duration = self._duration(body)
            if route["lease_id"] or any(
                self.resources[route["route_id"]] & self.resources[lease["route_id"]] for lease in self.leases.values()
            ):
                raise ProtocolFault("busy", "Route or shared resource is owned", route)
            self._cas(route, body)
            if len(self.leases) >= self.capabilities["max_leases"]:
                raise ProtocolFault("resource_exhausted", "Lease limit reached", route)
            baseline = self.backend.read(route["route_id"])
            lease_id = secrets.token_hex(16)
            route.update(owner_session=request["session_id"], lease_id=lease_id)
            self.leases[lease_id] = {"route_id": route["route_id"], "session_id": request["session_id"],
                                     "baseline": baseline, "last": baseline, "policy": body["expiry_policy"],
                                     "deadline": self.clock() + duration / 1000}
            self._bump(route)
            return {"route": route, "duration_ms": duration}
        route, lease = self._owned(request)
        if operation == "renew":
            duration = self._duration(body)
            lease["deadline"] = self.clock() + duration / 1000
            self._bump(route)
            return {"route": route, "duration_ms": duration}
        if operation == "release":
            self._cleanup(route, body["cleanup"])
            return {"route": route}
        if route["route_id"] != body["route_id"]:
            raise ProtocolFault("not_owner", "Lease does not cover requested route", route)
        self._cas(route, body)
        if body["pan_law"] != route["pan_law"] or (route["pan_law"] == "none" and body["pan"] != 0):
            raise ProtocolFault("unsupported", "Unsupported pan law for this route", route)
        limits = self.capabilities["gain_db"]
        if not limits["min"] <= body["gain_db"] <= limits["max"]:
            raise ProtocolFault("out_of_range", "Monitor gain outside advertised range", route)
        gain = Decimal(str(body["gain_db"]))
        minimum, step = Decimal(str(limits["min"])), Decimal(str(limits["step"]))
        steps = ((gain - minimum) / step).quantize(Decimal("1"), rounding=ROUND_HALF_DOWN)
        values = {**controls(body), "gain_db": float(minimum + steps * step)}
        observed = self.backend.apply(route["route_id"], values)
        if observed != values:
            raise BackendFailure("Readback differs from requested controls")
        route.update(observed)
        lease["last"] = copy.deepcopy(observed)
        self._bump(route)
        return {"route": route}


def coefficients(route):
    gain = 10 ** (route["gain_db"] / 20) if route["enabled"] else 0
    pan = route["pan"]
    if route["pan_law"] == "mono_equal_power":
        angle = (pan + 1) * math.pi / 4
        return gain * math.cos(angle), gain * math.sin(angle)
    if route["pan_law"] == "stereo_balance":
        return gain * min(1, 1 - pan), gain * min(1, 1 + pan)
    return (gain,)
