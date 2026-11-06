import copy
import importlib.util
import json
import os
import platform
import select
import shutil
import subprocess
import tempfile
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch
import io
import threading

from tools.validate import ROOT, load_json
from unithru.capture import CaptureRecorder, FridaProvider, USBMonProvider, run_capture, validate_label
from unithru.learning import analyze_file, analyze_trace, validate_trace


def observation(label=None, status=0, problem=None):
    return {"kind": "call", "function": "fixture", "endpoint": "connection:17", "selector": "method:3",
            "timestamp_ns": "1000", "label": label or {"phase": "idle", "trial_id": "baseline-1"},
            "status": status, "problem": problem, "packets": [
                {"direction": "host_to_device", "component": "struct", "transfer_type": "driver_call", "payload_hex": "a00100"}]}


class RecorderCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "capture.json"
        self.recorder = CaptureRecorder(self.path, {"identity": "test-device", "model": "Test fixture", "firmware": "none"},
                                        "usb", "driver_api", "iokit", source="synthetic")

    def tearDown(self):
        self.recorder.finish()
        self.directory.cleanup()


class RecorderChecks(RecorderCase):
    def test_failed_or_partial_calls_are_preserved_but_not_learned(self):
        self.recorder.record(observation(status=5))
        self.recorder.record(observation(problem="truncated"))
        self.recorder.record(observation())
        self.recorder.finish()
        trace = load_json(self.path)
        validate_trace(trace)
        self.assertEqual(len(trace["events"]), 1)
        self.assertEqual(len(self.recorder.raw_path.read_text().splitlines()), 3)
        self.assertEqual(self.recorder.snapshot()["failed_calls"], 1)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_saved_capture_can_be_analysed_directly(self):
        for trial in ("trial-1", "trial-2"):
            self.recorder.record(observation({"phase": "idle", "trial_id": trial}))
            for value in (False, True):
                call = observation({"phase": "action", "trial_id": trial, "control": "route.enabled", "target": "in1/cue1", "value": value})
                call["packets"][0]["payload_hex"] = "a001" + ("01" if value else "00")
                self.recorder.record(call)
        self.recorder.finish()
        profile = analyze_trace(load_json(self.path))
        self.assertEqual(len(profile["candidates"]), 1)
        self.assertFalse(profile["write_enabled"])

    def test_existing_output_is_never_overwritten(self):
        with self.assertRaises(FileExistsError):
            CaptureRecorder(self.path, self.recorder.trace["device"], "usb", "driver_api", "iokit")

    def test_limits_stop_capture_explicitly(self):
        self.recorder.max_events = 1
        self.recorder.record(observation())
        self.recorder.record(observation())
        self.assertTrue(self.recorder.limit_reached.is_set())
        self.assertEqual(self.recorder.snapshot()["events"], 1)

    def test_acquisition_manifest_is_linked_to_the_exact_trace(self):
        self.recorder.record(observation())
        self.recorder.finish({"provider_stats": {"calls_rate_dropped": 7}})
        report = analyze_file(self.path)
        self.assertEqual(report["acquisition"]["provider_stats"]["calls_rate_dropped"], 7)
        trace = load_json(self.path)
        trace["events"][0]["payload_hex"] = "a00101"
        self.path.write_text(json.dumps(trace))
        with self.assertRaisesRegex(ValueError, "does not match"):
            analyze_file(self.path)

    def test_label_rejects_ambiguous_or_nonfinite_values(self):
        for label in ({"phase": "action", "trial_id": "one"},
                      {"phase": "idle", "trial_id": "one", "control": "extra"},
                      {"phase": "action", "trial_id": "one", "control": "route.pan", "target": "route", "value": float("nan")}):
            with self.assertRaises(ValueError):
                validate_label(label)


class USBMonChecks(RecorderCase):
    def provider(self):
        return USBMonProvider(self.recorder, 1, 5)

    def test_output_submission_and_completion_are_one_observation(self):
        provider = self.provider()
        provider.feed_line("tag1 100 S Bo:1:005:2 -115 4 = a0010102")
        self.assertEqual(self.recorder.snapshot()["events"], 0)
        provider.feed_line("tag1 110 C Bo:1:005:2 0 4 >")
        self.assertEqual(self.recorder.snapshot()["events"], 1)
        self.assertEqual(self.recorder.trace["events"][0]["payload_hex"], "a0010102")

    def test_control_setup_is_captured_even_when_no_data(self):
        provider = self.provider()
        provider.feed_line("tag1 100 S Co:1:005:0 s 40 01 0001 0002 0000 0 >")
        provider.feed_line("tag1 110 C Co:1:005:0 0 0 >")
        self.assertEqual(self.recorder.trace["events"][0]["payload_hex"], "4001010002000000")
        self.assertTrue(self.recorder.trace["events"][0]["selector"].endswith(":setup"))

    def test_input_completion_retains_submission_label(self):
        provider = self.provider()
        first = {"phase": "action", "trial_id": "first", "control": "route.enabled", "target": "route", "value": True}
        provider.set_label(first)
        provider.feed_line("tag1 100 S Ci:1:005:0 s c0 01 0000 0002 0004 4 <")
        provider.set_label({"phase": "idle", "trial_id": "later"})
        provider.feed_line("tag1 110 C Ci:1:005:0 0 4 = 01020304")
        self.assertTrue(all(event["trial_id"] == "first" for event in self.recorder.trace["events"]))
        self.assertEqual(self.recorder.trace["events"][-1]["direction"], "device_to_host")

    def test_truncated_payload_is_not_analysed(self):
        provider = self.provider()
        provider.feed_line("tag1 100 S Bo:1:005:2 -115 8 = a001")
        provider.feed_line("tag1 110 C Bo:1:005:2 0 8 >")
        self.assertFalse(self.recorder.trace["events"])
        self.assertEqual(self.recorder.snapshot()["unusable_calls"], 1)

    def test_other_devices_and_audio_are_not_collected(self):
        provider = self.provider()
        for address in ("Bo:1:006:2", "Bo:2:005:2", "Zo:1:005:2"):
            provider.feed_line(f"tag1 100 S {address} -115 4 = a0010102")
            provider.feed_line(f"tag1 110 C {address} 0 4 >")
        self.assertEqual(self.recorder.snapshot()["calls"], 0)

    def test_mismatched_completion_is_not_associated_with_submission(self):
        provider = self.provider()
        provider.feed_line("tag1 100 S Bo:1:005:2 -115 4 = a0010102")
        provider.feed_line("tag1 110 C Bo:1:005:3 0 4 >")
        self.assertEqual(self.recorder.snapshot()["events"], 0)
        self.assertEqual(provider.dropped, 1)


class CaptureCommandChecks(unittest.TestCase):
    def options(self, path):
        return Namespace(provider="iokit", transport="thunderbolt", pid=123, bus=None, device_address=None,
                         connection=None, selector=None, handle=None, output=str(path), device_id="fixture",
                         model="Command fixture", firmware="none")

    def test_dependency_preflight_creates_no_capture_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            with patch("unithru.capture.capture_capabilities", return_value={"frida_installed": False}):
                with self.assertRaisesRegex(ValueError, "runtime unavailable"):
                    run_capture(self.options(path))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_live_command_labels_journals_saves_and_stops_without_target_control(self):
        class Provider:
            def __init__(self, recorder, *arguments):
                self.recorder = recorder
                self.ended = threading.Event()
                self.error = None

            def start(self):
                self.recorder.record(observation())
                return {"hooks": ["fixture"], "layer": "driver_api"}

            def set_label(self, label):
                validated = validate_label(label)
                self.recorder.record(observation(validated))
                return validated

            def stop(self):
                return {"detached": True}

        label = {"phase": "action", "trial_id": "trial-1", "control": "route.enabled", "target": "route", "value": True}
        commands = json.dumps({"action": "label", "label": label}) + '\n{"action":"stop"}\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            output = io.StringIO()
            with patch("unithru.capture.FridaProvider", Provider), patch("unithru.capture.signal.signal"), \
                 patch("unithru.capture.capture_capabilities", return_value={"frida_installed": True}), \
                 patch("sys.stdin", io.StringIO(commands)), patch("sys.stdout", output):
                run_capture(self.options(path))
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(events[0]["event"], "ready")
            self.assertEqual(events[-1]["event"], "saved")
            self.assertEqual(events[-1]["stats"]["events"], 2)
            self.assertTrue(load_json(path)["events"][-1]["value"])
            self.assertFalse(load_json(Path(str(path) + ".capture.json"))["writes_enabled"])


def frida_available():
    try:
        import frida

        return bool(frida.__version__)
    except (ImportError, OSError):
        return False


@unittest.skipUnless(frida_available() and shutil.which("clang"), "Optional Frida runtime and clang required")
class NativeCaptureChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.executable = Path(cls.directory.name) / "capture-fixture"
        export_flag = "-Wl,-export_dynamic" if platform.system() == "Darwin" else "-Wl,--export-dynamic"
        subprocess.run(["clang", "-O0", "-g", export_flag, str(ROOT / "tests/fixtures/capture_fixture.c"),
                        "-o", str(cls.executable)], check=True)
        if platform.system() == "Darwin":
            subprocess.run(["codesign", "--force", "--sign", "-", "--entitlements",
                            str(ROOT / "tests/fixtures/capture-entitlements.plist"), str(cls.executable)],
                           check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.output_dir = tempfile.TemporaryDirectory()
        self.process = subprocess.Popen([str(self.executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.assertTrue(select.select([self.process.stdout], [], [], 3)[0])
        self.assertTrue(self.process.stdout.readline().startswith("ready"))
        self.recorder = CaptureRecorder(Path(self.output_dir.name) / "capture.json",
                                        {"identity": "native-fixture", "model": "Native API test fixture; no hardware", "firmware": "none"},
                                        "usb", "application_api", "libusb", source="synthetic")
        self.provider = None

    def tearDown(self):
        if self.provider is not None:
            self.provider.stop()
        self.recorder.finish()
        if self.process.poll() is None:
            self.process.stdin.write("quit\n")
            self.process.stdin.flush()
        try:
            self.process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            # Only the disposable process created by this test is terminated.
            self.process.kill()
            self.process.communicate(timeout=3)
            raise
        finally:
            self.output_dir.cleanup()

    def invoke(self, command, calls):
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()
        self.assertTrue(select.select([self.process.stdout], [], [], 3)[0])
        self.assertEqual(self.process.stdout.readline().strip(), "done")
        deadline = time.monotonic() + 3
        while self.recorder.snapshot()["calls"] < calls and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.recorder.snapshot()["calls"], calls)

    def test_live_libusb_interception_reads_original_arguments(self):
        self.provider = FridaProvider(self.recorder, self.process.pid, "libusb")
        info = self.provider.start()
        self.assertIn("libusb_control_transfer", info["hooks"])
        self.provider.set_label({"phase": "action", "trial_id": "one", "control": "route.enabled", "target": "route", "value": True})
        self.invoke("usb 1", 1)
        events = self.recorder.trace["events"]
        self.assertEqual(events[0]["payload_hex"], "4001010000000400")
        self.assertEqual(events[1]["payload_hex"], "a0010100")
        self.assertTrue(all(event["value"] is True for event in events))
        self.provider.stop()
        self.assertIsNone(self.process.poll())

    @unittest.skipUnless(platform.system() == "Darwin", "macOS IOKit provider")
    def test_live_iokit_wrappers_are_deduplicated_and_failed_calls_excluded(self):
        self.provider = FridaProvider(self.recorder, self.process.pid, "iokit", connection=17)
        info = self.provider.start()
        self.assertIn("IOConnectCallMethod", info["hooks"])
        self.invoke("scalar 1", 1)
        self.assertEqual(self.recorder.snapshot()["events"], 2)
        self.invoke("fail 1", 2)
        self.assertEqual(self.recorder.snapshot()["failed_calls"], 1)
        self.assertEqual(self.recorder.snapshot()["events"], 2)
        self.invoke("struct 1", 3)
        self.assertEqual(self.recorder.snapshot()["events"], 4)


@unittest.skipUnless(shutil.which("node"), "Node required to execute the agent logic harness")
class AgentLogicChecks(unittest.TestCase):
    def test_actual_agent_with_bounded_pointer_and_interceptor_fixtures(self):
        subprocess.run(["node", str(ROOT / "tests/fixtures/capture_agent_test.cjs"),
                        str(ROOT / "unithru/capture_agent.js")], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
