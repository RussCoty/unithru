import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


def reject_constant(value):
    raise ValueError(f"Nonfinite JSON constant: {value}")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path):
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=unique_object,
    )


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite_numbers(value):
    if isinstance(value, float):
        require(math.isfinite(value), "Nonfinite number")
    elif isinstance(value, dict):
        for child in value.values():
            finite_numbers(child)
    elif isinstance(value, list):
        for child in value:
            finite_numbers(child)


def validate_message(message, validator):
    finite_numbers(message)
    validator.validate(message)
    if message.get("status") == "indeterminate":
        require(message["body"].get("affected_resources"), "Missing affected resources")
    routes = message["body"].get("routes", [])
    if "route" in message["body"]:
        routes = [message["body"]["route"]]
    for route in routes:
        require(
            (route["lease_id"] is None) == (route["owner_session"] is None),
            "Lease and owner must both be present or both null",
        )


def indexed_unique(items, label):
    indexed = {item["id"]: item for item in items}
    require(len(indexed) == len(items), f"Duplicate {label} IDs")
    return indexed


def validate_fixtures(fixtures, validator):
    messages = []
    for value in fixtures.values():
        messages.extend(value if isinstance(value, list) else [value])
    for message in messages:
        validate_message(message, validator)

    discovery = fixtures["discovery.json"]
    snapshot = fixtures["snapshot.json"]
    capabilities = discovery["body"]["capabilities"]
    sources = indexed_unique(capabilities["sources"], "source")
    destinations = indexed_unique(capabilities["destinations"], "destination")
    gain_range = capabilities["gain_db"]
    require(gain_range["min"] <= gain_range["max"], "Inverted gain range")
    require(capabilities["lease_ms"]["min"] <= capabilities["lease_ms"]["max"], "Inverted lease range")
    require(capabilities["max_leases"] <= capabilities["max_routes"], "Too many leases")
    require(snapshot["body"]["device_id"] == discovery["body"]["device_id"], "Wrong snapshot device")
    routes = snapshot["body"]["routes"]
    require(len(routes) <= capabilities["max_routes"], "Route limit exceeded")
    require(len({route["route_id"] for route in routes}) == len(routes), "Duplicate route IDs")

    for message in messages:
        if "epoch" in message:
            require(message["epoch"] == discovery["epoch"], "Fixture epoch mismatch")
        if "session_id" in message:
            require(message["session_id"] == discovery["body"]["session_id"], "Fixture session mismatch")
        address = "/unithru/v0.1/" + ("result/" if "status" in message else "") + message["op"]
        payload = json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")
        padded_address = (len(address.encode("ascii")) + 4) // 4 * 4
        packet_bytes = padded_address + 4 + 4 + (len(payload) + 3) // 4 * 4
        require(packet_bytes <= capabilities["max_message_bytes"], "Fixture exceeds OSC packet cap")
        body = message["body"]
        candidate_routes = body.get("routes", [])
        if "route" in body:
            candidate_routes = [body["route"]]
        if message["op"] == "set_route" and "status" not in message:
            candidate_routes = [{**body, "source_id": routes[0]["source_id"], "destination_id": routes[0]["destination_id"]}]
        for route in candidate_routes:
            require(route["source_id"] in sources, "Unknown source ID")
            require(route["destination_id"] in destinations, "Unknown destination ID")
            topology = {
                "source_channels": len(sources[route["source_id"]]["channels"]),
                "destination_channels": len(destinations[route["destination_id"]]["channels"]),
                "pan_law": route["pan_law"],
            }
            require(topology in capabilities["topologies"], "Unsupported topology/pan law")
            require(gain_range["min"] <= route["gain_db"] <= gain_range["max"], "Gain outside capabilities")
            if "status" in message:
                steps = (route["gain_db"] - gain_range["min"]) / gain_range["step"]
                require(math.isclose(steps, round(steps), abs_tol=1e-6), "Unquantized readback")
            if route["pan_law"] == "none":
                require(route["pan"] == 0, "Mono destination requires pan zero")

    exchange = fixtures["exchange.json"]
    require(len(exchange) % 2 == 0, "Unpaired fixture exchange")
    previous_revision = snapshot["revision"]
    previous_route = routes[0]
    for offset in range(0, len(exchange), 2):
        request, response = exchange[offset:offset + 2]
        require(request["request_id"] == response["request_id"], "Uncorrelated response")
        require(request["op"] == response["op"], "Operation mismatch")
        require(response["status"] == "ok", "Worked exchange expects success")
        require(response["revision"] == previous_revision + 1, "Invalid device revision progression")
        route = response["body"]["route"]
        require(route["route_id"] == previous_route["route_id"], "Unexpected route change")
        require(route["route_revision"] == previous_route["route_revision"] + 1, "Invalid route revision progression")
        body = request["body"]
        if "expected_route_revision" in body:
            require(body["expected_route_revision"] == previous_route["route_revision"], "Stale fixture CAS")
        if request["op"] in ("acquire", "renew"):
            duration = body["duration_ms"]
            require(capabilities["lease_ms"]["min"] <= duration <= capabilities["lease_ms"]["max"], "Lease outside capabilities")
            require(response["body"]["duration_ms"] == duration, "Unexpected granted duration")
        if "lease_id" in body:
            require(body["lease_id"] == previous_route["lease_id"], "Incorrect lease")
        if request["op"] == "acquire":
            require(previous_route["lease_id"] is None, "Acquiring owned route")
            require(route["owner_session"] == request["session_id"] and route["lease_id"], "Acquire missing ownership")
        elif request["op"] == "release":
            require(route["lease_id"] is None, "Released route still owned")
            if body["cleanup"] == "restore":
                for field in ("enabled", "gain_db", "pan", "pan_law"):
                    require(route[field] == routes[0][field], "Fixture restore differs from baseline")
        else:
            require(route["lease_id"] == previous_route["lease_id"], "Lease changed unexpectedly")
        if request["op"] == "set_route":
            for field in ("enabled", "pan", "pan_law"):
                require(route[field] == body[field], "Applied route differs from requested route")
            requested_steps = (body["gain_db"] - gain_range["min"]) / gain_range["step"]
            rounded_steps = math.ceil(requested_steps - 0.5)
            applied_gain = gain_range["min"] + rounded_steps * gain_range["step"]
            require(math.isclose(route["gain_db"], applied_gain, abs_tol=1e-6), "Wrong applied gain")
        if request["op"] in ("acquire", "renew") or (
            request["op"] == "release" and body["cleanup"] == "hold"
        ):
            for field in ("enabled", "gain_db", "pan", "pan_law"):
                require(route[field] == previous_route[field], "Ownership operation changed audio controls")
        previous_revision, previous_route = response["revision"], route
    return len(messages)


def read_fixtures():
    return {path.name: load_json(path) for path in sorted((ROOT / "examples").glob("*.json"))}


def make_validator():
    schema = load_json(ROOT / "schema/protocol.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


if __name__ == "__main__":
    count = validate_fixtures(read_fixtures(), make_validator())
    print(f"Validated schema and {count} fixture messages, including ownership/revision exchange")
