import json
import struct

from tools.validate import finite_numbers, reject_constant, unique_object


MAX_PACKET = 8192
PREFIX = "/unithru/v0.1/"
OPERATIONS = {"hello", "snapshot", "acquire", "renew", "set_route", "release"}


def padded(value):
    return value + bytes(-len(value) % 4)


def encode(message):
    operation = message.get("op")
    if operation not in OPERATIONS:
        raise ValueError("Unknown operation")
    address = PREFIX + ("result/" if "status" in message else "") + operation
    payload = json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")
    packet = padded(address.encode("ascii") + b"\0") + b",b\0\0"
    packet += struct.pack(">i", len(payload)) + padded(payload)
    if len(packet) > MAX_PACKET:
        raise ValueError("OSC packet exceeds 8192 bytes")
    return packet


def read_string(packet, offset):
    end = packet.find(b"\0", offset)
    if end < 0:
        raise ValueError("Unterminated OSC string")
    following = (end + 4) // 4 * 4
    if following > len(packet) or any(packet[end:following]):
        raise ValueError("Invalid OSC string padding")
    return packet[offset:end].decode("ascii"), following


def decode(packet):
    if not packet or len(packet) > MAX_PACKET or len(packet) % 4:
        raise ValueError("Invalid OSC packet size")
    if packet.startswith(b"#bundle\0"):
        if len(packet) < 20 or struct.unpack(">Q", packet[8:16])[0] != 1:
            raise ValueError("Only immediate bundles are supported")
        length = struct.unpack(">i", packet[16:20])[0]
        if length <= 0 or length != len(packet) - 20:
            raise ValueError("Bundle must contain exactly one message")
        packet = packet[20:]
        if packet.startswith(b"#bundle\0"):
            raise ValueError("Nested bundles are unsupported")
    address, offset = read_string(packet, 0)
    allowed = {PREFIX + operation for operation in OPERATIONS}
    allowed.update(PREFIX + "result/" + operation for operation in OPERATIONS)
    if address not in allowed:
        raise ValueError("Unknown or wildcard OSC address")
    tags, offset = read_string(packet, offset)
    if tags != ",b" or offset + 4 > len(packet):
        raise ValueError("Expected a single OSC blob")
    length = struct.unpack(">i", packet[offset:offset + 4])[0]
    offset += 4
    end = offset + length
    if length < 0 or offset + (length + 3) // 4 * 4 != len(packet):
        raise ValueError("Invalid blob length or trailing arguments")
    if any(packet[end:]):
        raise ValueError("Invalid blob padding")
    payload = packet[offset:end].decode("utf-8")
    message = json.loads(payload, parse_constant=reject_constant, object_pairs_hook=unique_object)
    if not isinstance(message, dict):
        raise ValueError("Expected a JSON object")
    finite_numbers(message)
    return address, message
