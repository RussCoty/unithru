import copy
import unittest

from tools.validate import ROOT, load_json
from unithru.learning import analyze_trace, normalize_inventory, validate_trace


class LearningChecks(unittest.TestCase):
    def setUp(self):
        self.trace = load_json(ROOT / "examples/learning/labelled-trace.json")

    def test_repeatable_control_bytes_without_executable_profile(self):
        report = analyze_trace(self.trace)
        self.assertEqual(len(report["candidates"]), 2)
        self.assertEqual({item["byte_offset"] for item in report["candidates"]}, {2})
        self.assertTrue(all(item["evidence"] == "repeatable_correlation" for item in report["candidates"]))
        self.assertFalse(report["write_enabled"])
        self.assertFalse(report["validated"])
        self.assertEqual(report["source"], "synthetic")
        self.assertTrue(all(not item["write_enabled"] and not item["validated"] for item in report["candidates"]))
        self.assertNotIn("commands", report)

    def test_idle_counter_is_filtered(self):
        report = analyze_trace(self.trace)
        self.assertTrue(all(stream["volatile_offsets"] == [3] for stream in report["streams"]))
        self.assertNotIn(3, [item["byte_offset"] for item in report["candidates"]])

    def test_same_engine_handles_thunderbolt_driver_observations(self):
        self.trace["transport"] = "thunderbolt"
        self.trace["capture_layer"] = "driver_api"
        for event in self.trace["events"]:
            event["endpoint"] = "IOUserClient:example-selector"
            event["transfer_type"] = "driver_call"
        report = analyze_trace(self.trace)
        self.assertEqual(report["transport"], "thunderbolt")
        self.assertEqual(len(report["candidates"]), 2)
        self.assertFalse(report["write_enabled"])

    def test_no_false_repeatability_from_packet_duplicates(self):
        for event in self.trace["events"]:
            if event["phase"] == "action":
                event["trial_id"] = "single-trial"
        report = analyze_trace(self.trace)
        self.assertTrue(all(item["evidence"] == "provisional_correlation" for item in report["candidates"]))

    def test_missing_idle_baseline_remains_provisional(self):
        self.trace["events"] = [event for event in self.trace["events"] if event["phase"] != "idle"]
        report = analyze_trace(self.trace)
        self.assertTrue(all(item["evidence"] == "provisional_correlation" for item in report["candidates"]))

    def test_same_value_with_different_bytes_does_not_form_mapping(self):
        event = next(event for event in self.trace["events"] if event.get("control") == "route.enabled")
        event["payload_hex"] = "a00105aa"
        report = analyze_trace(self.trace)
        self.assertNotIn("route.enabled", [item["control"] for item in report["candidates"]])

    def test_cochanging_controls_are_marked_ambiguous(self):
        additional = []
        for event in self.trace["events"]:
            if event.get("control") == "route.enabled":
                added = copy.deepcopy(event)
                added["control"] = "preamp.phantom"
                additional.append(added)
        self.trace["events"].extend(additional)
        report = analyze_trace(self.trace)
        ambiguous = [item for item in report["candidates"] if item["selector"] == "monitor-enable"]
        self.assertTrue(all(item["evidence"] == "ambiguous_correlation" for item in ambiguous))
        self.assertTrue(all(not item["write_enabled"] for item in ambiguous))

    def test_isoc_audio_is_not_interpreted_as_control(self):
        for event in self.trace["events"]:
            event["transfer_type"] = "isochronous"
        report = analyze_trace(self.trace)
        self.assertEqual(report["candidates"], [])
        self.assertEqual(report["skipped_isochronous_events"], len(self.trace["events"]))

    def test_candidate_limit_is_explicit(self):
        self.trace["events"] = [event for event in self.trace["events"] if event["selector"] == "monitor-enable"]
        for event in self.trace["events"]:
            event["payload_hex"] = ("01" if event.get("value") is True else "00") * 260
        report = analyze_trace(self.trace)
        self.assertEqual(len(report["candidates"]), 256)
        self.assertTrue(report["candidates_truncated"])
        self.assertFalse(report["write_enabled"])

    def test_bad_values_and_payloads_are_rejected(self):
        for payload in ("xyz", "a0 1", "aa\n", 123):
            trace = copy.deepcopy(self.trace)
            trace["events"][0]["payload_hex"] = payload
            with self.assertRaises(ValueError):
                validate_trace(trace)
        event = next(event for event in self.trace["events"] if event["phase"] == "action")
        event["value"] = float("inf")
        with self.assertRaises(ValueError):
            validate_trace(self.trace)

    def test_inventory_preserves_topology_without_serials_or_endpoint_claims(self):
        raw = {"SPUSBHostDataType": [{"_name": "USB bus", "_items": [
            {"_name": "Example interface", "vendor_id": "0x0000", "serial_num": "private-device-serial",
             "USBKeyLocationID": "0x1234"}]}],
               "SPThunderboltDataType": [{"_name": "Thunderbolt bus", "receptacle_1_tag": {
                   "_items": [{"_name": "Expansion chassis", "switch_uid_key": "private-uid"}]}}]}
        result = normalize_inventory(raw)
        self.assertEqual(len(result["devices"]), 4)
        self.assertFalse(result["traffic_captured"])
        self.assertNotIn("private-device-serial", str(result))
        self.assertNotIn("private-uid", str(result))
        self.assertEqual(result["devices"][1]["topology_path"], ["USB bus", "Example interface"])
        self.assertTrue(all(item["endpoints"] == [] for item in result["devices"]))


if __name__ == "__main__":
    unittest.main()
