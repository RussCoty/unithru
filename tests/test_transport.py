import json
import socket
import struct
import threading
import unittest

from unithru.osc import MAX_PACKET, PREFIX, decode, encode, padded
from unithru.transport import Client, LoopbackServer, run_demo


HELLO = {"version": "0.1", "request_id": "wire-hello", "op": "hello",
         "body": {"versions": ["0.1"], "client_id": "wire-test"}}


def raw_blob(payload, tags=b",b\0\0"):
    return padded((PREFIX + "hello").encode() + b"\0") + tags + struct.pack(">i", len(payload)) + padded(payload)


class CodecChecks(unittest.TestCase):
    def test_round_trip_and_known_wire_layout(self):
        payload = json.dumps(HELLO, separators=(",", ":")).encode()
        packet = raw_blob(payload)
        self.assertEqual(encode(HELLO), packet)
        self.assertEqual(decode(packet), (PREFIX + "hello", HELLO))

    def test_single_immediate_bundle(self):
        packet = encode(HELLO)
        bundle = b"#bundle\0" + struct.pack(">Qi", 1, len(packet)) + packet
        self.assertEqual(decode(bundle), (PREFIX + "hello", HELLO))
        nested = b"#bundle\0" + struct.pack(">Qi", 1, len(bundle)) + bundle
        with self.assertRaises(ValueError):
            decode(nested)
        with self.assertRaises(ValueError):
            decode(bundle[:8] + struct.pack(">Q", 2) + bundle[16:])
        with self.assertRaises(ValueError):
            decode(bundle + struct.pack(">i", len(packet)) + packet)

    def test_padding_lengths_tags_and_trailing_data(self):
        packet = encode(HELLO)
        cases = [packet[:-1], packet + b"\0\0\0\0", b"/abc\0\0\0\1" + packet[8:],
                 raw_blob(b"{}", tags=b",s\0\0"), raw_blob(b"{}")[:-4] + b"{}\0\1",
                 padded((PREFIX + "hello").encode() + b"\0") + b",b\0\0" + struct.pack(">i", -1),
                 bytes(MAX_PACKET + 4)]
        for invalid in cases:
            with self.subTest(packet=invalid[:30]):
                with self.assertRaises(ValueError):
                    decode(invalid)

    def test_duplicate_keys_nonfinite_overflow_and_bom(self):
        for payload in (b'{"op":"hello","op":"hello"}', b'{"gain_db":NaN}',
                        b'{"gain_db":1e999}', b'\xef\xbb\xbf{}', b'[]', b'\xff'):
            with self.subTest(payload=payload):
                with self.assertRaises((ValueError, UnicodeError)):
                    decode(raw_blob(payload))

    def test_wildcard_and_response_address(self):
        packet = encode(HELLO).replace(b"/hello\0", b"/hell*\0")
        with self.assertRaises(ValueError):
            decode(packet)
        response = {**HELLO, "status": "error"}
        self.assertEqual(decode(encode(response))[0], PREFIX + "result/hello")


class NetworkChecks(unittest.TestCase):
    def setUp(self):
        self.server = LoopbackServer(port=0, drop_first_reply="set_route")
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)
        self.assertFalse(self.worker.is_alive())

    def test_real_udp_demo_recovers_lost_reply_once(self):
        result = run_demo(self.server)
        self.assertEqual(result["retry_count"], 1)
        self.assertEqual(result["dropped_replies"], 1)
        self.assertEqual(result["backend_control_writes"], 8)
        self.assertFalse(result["restored_route"]["enabled"])
        self.assertIsNone(result["restored_route"]["lease_id"])

    def test_all_operations_recover_lost_reply(self):
        for operation in ("hello", "snapshot", "acquire", "renew", "release"):
            with self.subTest(operation=operation):
                self.server.drop_first_reply = operation
                writes = self.server.simulator.backend.write_count
                result = run_demo(self.server)
                self.assertEqual(result["retry_count"], 1)
                self.assertEqual(self.server.simulator.backend.write_count - writes, 8)
                self.assertFalse(self.server.simulator.leases)

    def test_occupied_port_fails_without_displacing_listener(self):
        port = self.server.server_address[1]
        with self.assertRaises(OSError):
            LoopbackServer(port)
        with Client(port) as client:
            self.assertEqual(client.hello()["status"], "ok")

    def test_invalid_datagrams_do_not_stop_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(bytes(MAX_PACKET + 100), self.server.server_address)
            sender.sendto(raw_blob(b'{"op":[],"request_id":"bad"}'), self.server.server_address)
            sender.sendto(b"bad", self.server.server_address)
        with Client(self.server.server_address[1]) as client:
            self.assertEqual(client.hello()["status"], "ok")
        self.assertGreaterEqual(self.server.rejected_packets, 2)

    def test_dropped_hello_reply_reuses_session(self):
        self.server.drop_first_reply = "hello"
        with Client(self.server.server_address[1]) as client:
            client.hello()
            self.assertEqual(client.retries, 1)
        self.assertEqual(len(self.server.simulator.sessions), 1)

    def test_incoming_result_is_never_a_write_request(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.settimeout(0.1)
            sender.sendto(encode({**HELLO, "status": "ok"}), self.server.server_address)
            with self.assertRaises(socket.timeout):
                sender.recvfrom(MAX_PACKET)
        self.assertEqual(self.server.simulator.backend.write_count, 0)


class ReplySocket:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.sent = []

    def sendto(self, packet, endpoint):
        self.sent.append((packet, endpoint))

    def settimeout(self, timeout):
        pass

    def recvfrom(self, size):
        if self.replies:
            return self.replies.pop(0)
        raise socket.timeout()

    def close(self):
        pass


class ClientChecks(unittest.TestCase):
    def make_client(self, replies=()):
        client = Client(12345)
        client.socket.close()
        client.socket = ReplySocket(replies)
        self.addCleanup(client.close)
        return client

    def test_timeout_retries_identical_packet_and_blocks_mutation(self):
        client = self.make_client()
        request = {"version": "0.1", "request_id": "set-timeout", "op": "set_route", "body": {}}
        with self.assertRaises(TimeoutError):
            client.exchange(request)
        self.assertEqual(len(client.socket.sent), 5)
        self.assertTrue(all(packet == client.socket.sent[0] for packet in client.socket.sent))
        self.assertTrue(client.uncertain)
        with self.assertRaises(RuntimeError):
            client.call("acquire", {})

    def test_client_ignores_wrong_id_endpoint_epoch_and_invalid_shape(self):
        endpoint = ("127.0.0.1", 12345)
        request = {"version": "0.1", "request_id": "read-1", "op": "snapshot", "epoch": "current",
                   "session_id": "session-1", "body": {"device_id": "device-demo"}}
        result = {"version": "0.1", "request_id": "read-1", "op": "snapshot", "epoch": "current",
                  "revision": 0, "status": "ok", "body": {"device_id": "device-demo", "observed_ms": 0, "routes": []}}
        replies = [
            (encode({**result, "request_id": "old-request"}), endpoint),
            (encode(result), ("127.0.0.1", 12346)),
            (encode({**result, "epoch": "previous"}), endpoint),
            (encode({**result, "body": {}}), endpoint),
            (encode(result), endpoint),
        ]
        client = self.make_client(replies)
        self.assertEqual(client.exchange(request), result)
        self.assertEqual(len(client.socket.sent), 1)

    def test_indeterminate_reply_blocks_new_mutations(self):
        endpoint = ("127.0.0.1", 12345)
        request = {"version": "0.1", "request_id": "set-failed", "op": "set_route", "epoch": "current", "body": {}}
        response = {"version": "0.1", "request_id": "set-failed", "op": "set_route", "epoch": "current",
                    "revision": 2, "status": "indeterminate", "body": {"code": "backend_failure",
                    "message": "Unknown applied state", "affected_resources": ["route-vocal-cue"]}}
        client = self.make_client([(encode(response), endpoint)])
        self.assertEqual(client.exchange(request), response)
        self.assertTrue(client.uncertain)
        with self.assertRaises(RuntimeError):
            client.call("set_route", {})


if __name__ == "__main__":
    unittest.main()
