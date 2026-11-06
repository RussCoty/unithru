import copy
import math
import unittest

from tools.validate import validate_message
from unithru.osc import PREFIX
from unithru.simulator import MAX_REVISION, Simulator, coefficients


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class SimulatorChecks(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.simulator = Simulator(clock=self.clock)
        self.endpoint = ("127.0.0.1", 30001)
        self.counter = 0
        self.session = self.hello()

    def hello(self, endpoint=None, request_id=None):
        self.counter += 1
        request = {"version": "0.1", "request_id": request_id or f"hello-{self.counter}", "op": "hello",
                   "body": {"versions": ["0.1"], "client_id": "test-client"}}
        result = self.simulator.handle(PREFIX + "hello", request, endpoint or self.endpoint)
        validate_message(result, self.simulator.validator)
        return result["body"]["session_id"]

    def request(self, operation, body, session=None):
        self.counter += 1
        return {"version": "0.1", "request_id": f"test-{self.counter}", "op": operation, "body": body,
                "session_id": session or self.session, "epoch": self.simulator.epoch}

    def send(self, request, endpoint=None):
        result = self.simulator.handle(PREFIX + request["op"], request, endpoint or self.endpoint)
        if result is not None:
            validate_message(result, self.simulator.validator)
        return result

    def call(self, operation, body, session=None):
        return self.send(self.request(operation, body, session))

    def acquire(self, route_id="route-vocal-cue", policy="restore", session=None):
        route = self.simulator.routes[route_id]
        result = self.call("acquire", {"route_id": route_id, "expected_route_revision": route["route_revision"],
                                       "duration_ms": 3000, "expiry_policy": policy}, session)
        self.assertEqual(result["status"], "ok", result)
        return result["body"]["route"]

    def setting(self, route, **changes):
        body = {"route_id": route["route_id"], "lease_id": route["lease_id"],
                "expected_route_revision": route["route_revision"], "enabled": True,
                "gain_db": -6, "pan": 0, "pan_law": route["pan_law"]}
        body.update(changes)
        return self.request("set_route", body)

    def test_apply_and_restore_only_owned_route(self):
        baseline = copy.deepcopy(self.simulator.backend.routes)
        route = self.acquire()
        applied = self.send(self.setting(route))
        self.assertEqual(applied["status"], "ok")
        released = self.call("release", {"lease_id": route["lease_id"], "cleanup": "restore"})
        self.assertEqual(released["status"], "ok")
        self.assertIsNone(released["body"]["route"]["lease_id"])
        self.assertEqual(self.simulator.backend.routes, baseline)

    def test_duplicate_set_is_one_backend_write(self):
        request = self.setting(self.acquire())
        result = self.send(request)
        writes = self.simulator.backend.write_count
        reordered = dict(reversed(list(request.items())))
        reordered["body"] = dict(reversed(list(request["body"].items())))
        self.assertEqual(self.send(reordered), result)
        self.assertEqual(self.simulator.backend.write_count, writes)
        self.assertEqual(writes, 4)
        request["body"]["gain_db"] = -4
        self.assertEqual(self.send(request)["body"]["code"], "request_id_reuse")

    def test_duplicate_hello_does_not_create_session(self):
        first = self.hello(request_id="hello-retry")
        self.assertEqual(self.hello(request_id="hello-retry"), first)
        self.assertEqual(len(self.simulator.sessions), 2)

    def test_duplicate_renew_does_not_extend_deadline(self):
        route = self.acquire()
        request = self.request("renew", {"lease_id": route["lease_id"], "duration_ms": 3000})
        result = self.send(request)
        self.clock.advance(2)
        self.assertEqual(self.send(request), result)
        self.clock.advance(1)
        self.simulator.tick()
        self.assertIsNone(self.simulator.routes[route["route_id"]]["lease_id"])

    def test_cached_set_after_expiry_does_not_reenable(self):
        route = self.acquire()
        request = self.setting(route)
        response = self.send(request)
        self.clock.advance(3)
        self.assertEqual(self.send(request), response)
        self.assertFalse(self.simulator.backend.read(route["route_id"])["enabled"])

    def test_expiry_restores_without_incoming_packet(self):
        route = self.acquire()
        self.send(self.setting(route))
        self.clock.advance(3)
        self.simulator.tick()
        self.assertFalse(self.simulator.routes[route["route_id"]]["enabled"])
        self.assertFalse(self.simulator.leases)

    def test_hold_expiry_preserves_latest_setting(self):
        route = self.acquire(policy="hold")
        self.send(self.setting(route))
        self.clock.advance(3)
        self.simulator.tick()
        self.assertTrue(self.simulator.routes[route["route_id"]]["enabled"])
        self.assertFalse(self.simulator.leases)

    def test_expiry_precedes_renew_at_same_time(self):
        route = self.acquire()
        self.clock.advance(3)
        result = self.call("renew", {"lease_id": route["lease_id"], "duration_ms": 3000})
        self.assertEqual(result["body"]["code"], "lease_expired")

    def test_stale_compare_and_set_never_writes(self):
        route = self.acquire()
        self.call("renew", {"lease_id": route["lease_id"], "duration_ms": 3000})
        response = self.send(self.setting(route))
        self.assertEqual(response["body"]["code"], "stale_revision")
        self.assertEqual(self.simulator.backend.write_count, 0)

    def test_competing_owner_and_endpoint_binding(self):
        route = self.acquire()
        other_session = self.hello()
        result = self.call("acquire", {"route_id": route["route_id"], "expected_route_revision": 1,
                                      "duration_ms": 3000, "expiry_policy": "hold"}, other_session)
        self.assertEqual(result["body"]["code"], "busy")
        result = self.call("renew", {"lease_id": route["lease_id"], "duration_ms": 3000}, other_session)
        self.assertEqual(result["body"]["code"], "not_owner")
        result = self.send(self.setting(route), endpoint=("127.0.0.1", 30002))
        self.assertEqual(result["body"]["code"], "unknown_session")

    def test_independent_cue_levels(self):
        first, second = self.acquire(), self.acquire("route-vocal-cue-b")
        self.assertEqual(self.send(self.setting(first, gain_db=-6))["status"], "ok")
        self.assertEqual(self.send(self.setting(second, gain_db=-18))["status"], "ok")
        self.assertEqual(self.simulator.backend.read(first["route_id"])["gain_db"], -6)
        self.assertEqual(self.simulator.backend.read(second["route_id"])["gain_db"], -18)

    def test_overlapping_backend_resources_cannot_be_owned_twice(self):
        self.simulator.resources["route-vocal-cue-b"] = {"route-vocal-cue"}
        self.acquire()
        result = self.call("acquire", {"route_id": "route-vocal-cue-b", "expected_route_revision": 0,
                                      "duration_ms": 3000, "expiry_policy": "restore"})
        self.assertEqual(result["body"]["code"], "busy")

    def test_manual_edit_revokes_lease_and_preserves_edit(self):
        route = self.acquire()
        self.send(self.setting(route))
        self.simulator.backend.routes[route["route_id"]]["gain_db"] = -2
        self.simulator.tick()
        result = self.call("release", {"lease_id": route["lease_id"], "cleanup": "restore"})
        self.assertEqual(result["body"]["code"], "conflict")
        self.assertEqual(self.simulator.backend.read(route["route_id"])["gain_db"], -2)
        self.assertFalse(self.simulator.leases)

    def test_partial_failure_is_indeterminate_and_quarantined(self):
        route = self.acquire()
        self.simulator.backend.fail_after = 1
        request = self.setting(route)
        result = self.send(request)
        self.assertEqual(result["status"], "indeterminate")
        self.assertEqual(result["body"]["affected_resources"], [route["route_id"]])
        self.assertTrue(result["body"]["route"]["enabled"])
        self.assertEqual(result["body"]["route"]["gain_db"], -12)
        self.assertFalse(self.simulator.leases)
        self.assertEqual(self.send(request), result)
        result = self.call("acquire", {"route_id": route["route_id"], "expected_route_revision": 2,
                                      "duration_ms": 3000, "expiry_policy": "restore"})
        self.assertEqual(result["body"]["code"], "busy")
        self.simulator.reconcile(route["route_id"])
        self.acquire()

    def test_late_backend_completion_cannot_hit_new_owner(self):
        route = self.acquire()
        self.simulator.backend.defer_next = True
        response = self.send(self.setting(route))
        self.assertEqual(response["status"], "indeterminate")
        with self.assertRaises(RuntimeError):
            self.simulator.reconcile(route["route_id"])
        response = self.call("acquire", {"route_id": route["route_id"], "expected_route_revision": 2,
                                        "duration_ms": 3000, "expiry_policy": "restore"})
        self.assertEqual(response["body"]["code"], "busy")
        self.simulator.backend.complete_pending()
        self.simulator.reconcile(route["route_id"])
        self.assertTrue(self.acquire()["enabled"])

    def test_restart_changes_epoch_without_baseline_replay(self):
        route = self.acquire()
        request = self.setting(route)
        self.send(request)
        old_epoch = self.simulator.epoch
        self.simulator.restart()
        self.assertNotEqual(self.simulator.epoch, old_epoch)
        self.assertTrue(self.simulator.backend.read(route["route_id"])["enabled"])
        self.assertEqual(self.send(request)["body"]["code"], "epoch_mismatch")
        self.assertFalse(self.simulator.leases)

    def test_restart_cancels_deferred_work(self):
        route = self.acquire()
        self.simulator.backend.defer_next = True
        self.send(self.setting(route))
        self.simulator.restart()
        self.simulator.backend.complete_pending()
        self.assertFalse(self.simulator.backend.read(route["route_id"])["enabled"])

    def test_rounding_ties_and_range_validation(self):
        route = self.acquire()
        result = self.send(self.setting(route, gain_db=-11.75))
        self.assertEqual(result["body"]["route"]["gain_db"], -12)
        writes = self.simulator.backend.write_count
        route = result["body"]["route"]
        self.assertEqual(self.send(self.setting(route, gain_db=7))["body"]["code"], "out_of_range")
        self.assertEqual(self.simulator.backend.write_count, writes)

    def test_nonfinite_and_unknown_fields_rejected(self):
        route = self.acquire()
        self.assertEqual(self.send(self.setting(route, gain_db=float("nan")))["body"]["code"], "invalid_request")
        request = self.setting(route)
        request["body"]["phantom"] = True
        self.assertEqual(self.send(request)["body"]["code"], "invalid_request")
        self.assertEqual(self.simulator.backend.write_count, 0)

    def test_mono_pan_and_stereo_law(self):
        route = self.acquire("route-vocal-mono")
        result = self.send(self.setting(route, pan=0.5))
        self.assertEqual(result["body"]["code"], "unsupported")
        route = self.acquire("route-stereo-cue")
        result = self.send(self.setting(route, pan=1, gain_db=0))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(coefficients(result["body"]["route"]), (0, 1))

    def test_equal_power_coefficients_and_disable(self):
        route = self.acquire()
        result = self.send(self.setting(route, gain_db=0))
        for coefficient in coefficients(result["body"]["route"]):
            self.assertAlmostEqual(coefficient, math.sqrt(0.5))
        disabled = {**result["body"]["route"], "enabled": False}
        self.assertEqual(coefficients(disabled), (0, 0))

    def test_session_expiry_invalidates_cache(self):
        request = self.request("snapshot", {"device_id": "device-demo"})
        self.send(request)
        self.clock.advance(60)
        self.assertEqual(self.send(request)["body"]["code"], "unknown_session")

    def test_cache_and_session_limits_are_bounded(self):
        self.simulator.capabilities["max_requests_per_session"] = 1
        request = self.request("snapshot", {"device_id": "device-demo"})
        result = self.send(request)
        self.assertEqual(self.call("snapshot", {"device_id": "device-demo"})["body"]["code"], "resource_exhausted")
        self.assertEqual(self.send(request), result)
        self.simulator.max_sessions = 1
        request = {"version": "0.1", "request_id": "extra-hello", "op": "hello",
                   "body": {"versions": ["0.1"], "client_id": "extra"}}
        self.assertEqual(self.send(request)["body"]["code"], "resource_exhausted")

    def test_discovery_negotiation_and_address_mismatch(self):
        request = {"version": "0.1", "request_id": "future-hello", "op": "hello",
                   "body": {"versions": ["9.0"], "client_id": "test-client"}}
        self.assertEqual(self.send(request)["body"]["code"], "unsupported_version")
        request = self.request("snapshot", {"device_id": "device-demo"})
        response = self.simulator.handle(PREFIX + "acquire", request, self.endpoint)
        self.assertEqual(response["body"]["code"], "invalid_request")

    def test_full_snapshot_failure_does_not_report_stale_success(self):
        self.simulator.backend.unavailable = True
        result = self.call("snapshot", {"device_id": "device-demo"})
        self.assertEqual(result["status"], "indeterminate")
        self.assertNotIn("routes", result["body"])
        self.assertEqual(len(result["body"]["affected_resources"]), 4)

    def test_unhashable_operation_is_dropped(self):
        self.assertIsNone(self.simulator.handle(PREFIX + "hello", {"op": [], "request_id": "bad"}, self.endpoint))

    def test_failed_restore_does_not_claim_success(self):
        route = self.acquire()
        self.send(self.setting(route))
        self.simulator.backend.fail_after = 1
        request = self.request("release", {"lease_id": route["lease_id"], "cleanup": "restore"})
        result = self.send(request)
        self.assertEqual(result["status"], "indeterminate")
        self.assertIn(route["route_id"], self.simulator.quarantined)
        self.assertIsNone(result["body"]["route"]["lease_id"])
        writes = self.simulator.backend.write_count
        self.assertEqual(self.send(request), result)
        self.assertEqual(self.simulator.backend.write_count, writes)

    def test_expiry_readback_failure_never_replays_baseline(self):
        route = self.acquire()
        self.send(self.setting(route))
        self.simulator.backend.unavailable = True
        self.clock.advance(3)
        self.simulator.tick()
        self.assertFalse(self.simulator.leases)
        self.simulator.backend.unavailable = False
        self.assertTrue(self.simulator.backend.read(route["route_id"])["enabled"])
        self.assertIn(route["route_id"], self.simulator.quarantined)

    def test_lease_limit_rejects_without_changing_other_routes(self):
        self.simulator.capabilities["max_leases"] = 1
        self.acquire()
        result = self.call("acquire", {"route_id": "route-vocal-cue-b", "expected_route_revision": 0,
                                      "duration_ms": 3000, "expiry_policy": "restore"})
        self.assertEqual(result["body"]["code"], "resource_exhausted")
        self.assertEqual(self.simulator.routes["route-vocal-cue-b"]["route_revision"], 0)

    def test_returned_result_cannot_mutate_cached_success(self):
        request = self.setting(self.acquire())
        result = self.send(request)
        result["body"]["route"]["enabled"] = False
        self.assertTrue(self.send(request)["body"]["route"]["enabled"])

    def test_revision_rollover_changes_epoch_before_multi_route_updates(self):
        epoch = self.simulator.epoch
        self.simulator.revision = MAX_REVISION - 5
        for state in self.simulator.backend.routes.values():
            state["enabled"] = True
        self.simulator.tick()
        self.assertNotEqual(self.simulator.epoch, epoch)
        self.assertEqual(self.simulator.revision, 0)
        self.assertTrue(all(route["enabled"] for route in self.simulator.routes.values()))

    def test_rollover_readback_failure_blocks_writes_without_crashing(self):
        epoch = self.simulator.epoch
        self.simulator.revision = MAX_REVISION - 5
        self.simulator.backend.unavailable = True
        self.simulator.tick()
        self.assertEqual(self.simulator.epoch, epoch)
        self.assertLessEqual(self.simulator.revision, MAX_REVISION)
        self.assertEqual(len(self.simulator.quarantined), 4)
        self.simulator.tick()
        self.simulator.backend.unavailable = False
        self.simulator.tick()
        self.assertNotEqual(self.simulator.epoch, epoch)


if __name__ == "__main__":
    unittest.main()
