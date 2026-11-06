import copy
import hashlib
import json
import math
import os
import platform
import select
import signal
import struct
import sys
import threading
import time
from pathlib import Path

from tools.validate import reject_constant, unique_object


AGENT = Path(__file__).with_name("capture_agent.js")


def validate_label(value):
    if not isinstance(value, dict) or value.get("phase") not in ("idle", "action", "readback"):
        raise ValueError("Choose idle, action or readback")
    required = {"phase", "trial_id"} if value["phase"] == "idle" else {"phase", "trial_id", "control", "target", "value"}
    if set(value) != required:
        raise ValueError("Unexpected or missing label fields")
    for field in required - {"value"}:
        if not isinstance(value[field], str) or not value[field].strip() or len(value[field]) > 256:
            raise ValueError(f"Invalid label {field}")
    if "value" in value:
        scalar = value["value"]
        if scalar is not None and type(scalar) not in (bool, int, float, str):
            raise ValueError("Label value must be a JSON scalar")
        if isinstance(scalar, float) and not math.isfinite(scalar):
            raise ValueError("Nonfinite label value")
        if isinstance(scalar, str) and len(scalar) > 256:
            raise ValueError("Label value is too long")
    return copy.deepcopy(value)


def capture_capabilities():
    system = platform.system()
    frida = False
    dependency_error = None
    try:
        import frida as frida_runtime

        frida = bool(frida_runtime.__version__)
    except (ImportError, OSError) as error:
        dependency_error = str(error)
    return {"format": "unithru-capture-capabilities/0.1", "platform": system,
            "frida_installed": frida, "dependency_error": dependency_error,
            "providers": [
                {"id": "iokit", "available": system == "Darwin" and frida, "layer": "driver_api",
                 "scope": "Selected process; synchronous IOConnect calls for USB or Thunderbolt drivers",
                 "restriction": "Target attach permission and matching API calls still required"},
                {"id": "libusb", "available": frida, "layer": "application_api",
                 "scope": "Selected process; synchronous libusb control/bulk/interrupt calls",
                 "restriction": "Target must actually use libusb and permit attach"},
                {"id": "usbmon", "available": system == "Linux" and Path("/sys/kernel/debug/usb/usbmon").is_dir(),
                 "layer": "bus", "scope": "One explicitly selected Linux USB bus/device",
                 "restriction": "Existing readable usbmon node required; no driver loading or permission changes"},
            ], "raw_thunderbolt_wire_capture": False, "hardware_writes": False}


def exclusive_file(path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8")


class CaptureRecorder:
    def __init__(self, output, device, transport, layer, provider, source="external", max_events=20000):
        if transport not in ("usb", "thunderbolt"):
            raise ValueError("Live captures require an explicit USB or Thunderbolt transport label")
        if source not in ("external", "synthetic"):
            raise ValueError("Invalid source")
        if set(device) != {"identity", "model", "firmware"} or any(
            not isinstance(value, str) or not value.strip() or len(value) > 256 for value in device.values()
        ):
            raise ValueError("Device identity, model and firmware labels are required")
        self.output = Path(output).resolve()
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.raw_path = self.output.with_suffix(self.output.suffix + ".raw.jsonl")
        self.manifest_path = self.output.with_suffix(self.output.suffix + ".capture.json")
        self.trace = {"format": "unithru-learning/0.1", "source": source, "device": dict(device),
                      "transport": transport, "capture_layer": layer, "events": []}
        self.metadata = {"provider": provider, "capture_layer": layer, "transport": transport,
                         "started_wall_ns": time.time_ns(), "writes_enabled": False,
                         "host_platform": platform.platform(),
                         "note": "API success is not proof of hardware-applied state"}
        self.stats = {"calls": 0, "failed_calls": 0, "unusable_calls": 0, "events": 0, "dropped": 0}
        self.lock = threading.RLock()
        self.limit_reached = threading.Event()
        self.max_events = min(max_events, 20000)
        self.trace_bytes = 0
        self.raw_bytes = 0
        self.closed = False
        self.trace_file = None
        self.raw_file = None
        self.manifest_file = None
        opened = []
        try:
            for path, attribute in ((self.output, "trace_file"), (self.raw_path, "raw_file"), (self.manifest_path, "manifest_file")):
                setattr(self, attribute, exclusive_file(path))
                opened.append(path)
        except OSError:
            for file in (self.trace_file, self.raw_file, self.manifest_file):
                if file is not None:
                    file.close()
            for path in opened:
                path.unlink()
            raise

    def record(self, call):
        with self.lock:
            if self.closed or self.limit_reached.is_set():
                self.stats["dropped"] += 1
                return
            serialized = json.dumps(call, allow_nan=False, separators=(",", ":")) + "\n"
            if self.raw_bytes + len(serialized.encode()) > 16 * 1024 * 1024:
                self.limit_reached.set()
                self.stats["dropped"] += 1
                return
            self.raw_file.write(serialized)
            self.raw_bytes += len(serialized.encode())
            self.stats["calls"] += 1
            if call.get("status") != 0:
                self.stats["failed_calls"] += 1
                return
            if call.get("problem"):
                self.stats["unusable_calls"] += 1
                return
            label = validate_label(call["label"])
            events = []
            for packet in call.get("packets", []):
                payload = packet["payload_hex"]
                if not payload:
                    continue
                if len(payload) > 16384 or len(bytes.fromhex(payload)) * 2 != len(payload):
                    self.stats["unusable_calls"] += 1
                    return
                event = {**label, "timestamp_ns": int(call["timestamp_ns"]), "endpoint": call["endpoint"],
                         "selector": call["selector"] + ":" + packet["component"],
                         "direction": packet["direction"], "transfer_type": packet["transfer_type"], "payload_hex": payload}
                events.append(event)
            encoded_size = len(json.dumps(events, separators=(",", ":")).encode())
            if len(self.trace["events"]) + len(events) > self.max_events or self.trace_bytes + encoded_size > 8 * 1024 * 1024:
                self.limit_reached.set()
                self.stats["dropped"] += 1
                return
            self.trace["events"].extend(events)
            self.trace_bytes += encoded_size
            self.stats["events"] = len(self.trace["events"])

    def snapshot(self):
        with self.lock:
            return dict(self.stats)

    def finish(self, extra=None):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.metadata.update(extra or {})
            self.metadata["trace_sha256"] = hashlib.sha256(json.dumps(self.trace, sort_keys=True, allow_nan=False).encode()).hexdigest()
            self.metadata.update(ended_wall_ns=time.time_ns(), stats=self.snapshot(),
                                 limit_reached=self.limit_reached.is_set(), trace_path=str(self.output))
            for value, file in ((self.trace, self.trace_file), (self.metadata, self.manifest_file)):
                json.dump(value, file, allow_nan=False, indent=2)
                file.write("\n")
            for file in (self.trace_file, self.raw_file, self.manifest_file):
                file.flush()
                os.fsync(file.fileno())
                file.close()


class FridaProvider:
    def __init__(self, recorder, pid, provider, connection=None, selector=None, handle=None):
        if provider not in ("iokit", "libusb"):
            raise ValueError("Unknown process capture provider")
        if any(value is not None and not 0 <= value <= 0xffffffff for value in (connection, selector)):
            raise ValueError("IOKit connection and selector filters must be uint32 values")
        if handle is not None:
            numeric_handle = int(handle, 0)
            if not 0 <= numeric_handle <= 0xffffffffffffffff:
                raise ValueError("libusb handle must be a nonnegative pointer value")
            handle = hex(numeric_handle)
        self.recorder = recorder
        self.pid = pid
        self.provider = provider
        self.settings = {"provider": provider, "connection": connection, "selector": selector, "handle": handle}
        self.session = None
        self.script = None
        self.ended = threading.Event()
        self.error = None
        self.frida = None

    def _message(self, message, data):
        try:
            if message["type"] == "send" and message["payload"].get("kind") == "call":
                self.recorder.record(message["payload"])
            elif message["type"] == "send" and message["payload"].get("kind") == "limit":
                self.recorder.limit_reached.set()
                self.ended.set()
            elif message["type"] == "error":
                self.error = message.get("description", "Agent failed")
                self.ended.set()
        except (ValueError, KeyError, TypeError) as error:
            self.error = f"Invalid observation: {error}"
            self.ended.set()

    def start(self):
        if self.provider == "iokit" and platform.system() != "Darwin":
            raise ValueError("IOKit capture requires macOS")
        if self.pid <= 0 or self.pid == os.getpid():
            raise ValueError("Choose the explicit PID of the mixer or driver helper, not the capture process")
        try:
            import frida
        except ImportError as error:
            raise ValueError("Install optional capture support: .venv/bin/python -m pip install -r requirements-capture.txt") from error
        self.frida = frida
        cancellable = frida.Cancellable()
        timer = threading.Timer(10, cancellable.cancel)
        timer.daemon = True
        try:
            timer.start()
            with cancellable:
                device = frida.get_local_device()
                self.session = device.attach(self.pid)
                self.session.on("detached", lambda *_: self.ended.set())
                self.script = self.session.create_script(AGENT.read_text(encoding="utf-8"))
                self.script.on("message", self._message)
                self.script.load()
                result = self.script.exports_sync.start(self.settings)
            if not result["hooks"]:
                raise ValueError("Selected process exports none of the provider's APIs; choose the actual control helper or another provider")
            return result
        except Exception as error:
            self.stop()
            raise ValueError(f"Could not start process capture: {error}. Target protections are not bypassed or changed.") from error
        finally:
            timer.cancel()

    def set_label(self, label):
        if self.script is None or self.frida is None:
            raise ValueError("Capture is not attached")
        validated = validate_label(label)
        cancellable = self.frida.Cancellable()
        timer = threading.Timer(3, cancellable.cancel)
        timer.daemon = True
        try:
            timer.start()
            with cancellable:
                return self.script.exports_sync.setlabel(validated)
        finally:
            timer.cancel()

    def stop(self):
        result = {}
        if self.frida is not None and (self.script is not None or self.session is not None):
            cancellable = self.frida.Cancellable()
            timer = threading.Timer(3, cancellable.cancel)
            timer.daemon = True
            try:
                timer.start()
                with cancellable:
                    if self.script is not None:
                        try:
                            result = self.script.exports_sync.stop()
                        except Exception:
                            pass
                    if self.session is not None:
                        self.session.detach()
                        result["detached"] = True
            except Exception as error:
                result["detached"] = False
                result["detach_error"] = str(error)
            finally:
                timer.cancel()
        self.script = None
        self.session = None
        return result


class USBMonProvider:
    def __init__(self, recorder, bus, device_address):
        if not 1 <= bus <= 65535 or not 1 <= device_address <= 127:
            raise ValueError("Select a valid USB bus and device address")
        self.recorder = recorder
        self.bus = bus
        self.device_address = device_address
        self.label = {"phase": "idle", "trial_id": "baseline-1"}
        self.lock = threading.Lock()
        self.pending = {}
        self.ended = threading.Event()
        self.worker = None
        self.descriptor = None
        self.error = None
        self.dropped = 0

    def start(self):
        if platform.system() != "Linux":
            raise ValueError("usbmon live bus capture requires Linux; use IOKit/libusb on this platform")
        path = f"/sys/kernel/debug/usb/usbmon/{self.bus}u"
        try:
            self.descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as error:
            raise ValueError(f"Cannot read existing usbmon node {path}: {error}; no permissions or drivers were changed") from error
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()
        return {"node": path, "bus": self.bus, "device_address": self.device_address, "layer": "bus"}

    def set_label(self, label):
        with self.lock:
            self.label = validate_label(label)
            return dict(self.label)

    def feed_line(self, line):
        words = line.split()
        if len(words) < 7:
            return
        try:
            transfer, bus, device, endpoint = words[3].split(":")
            if int(bus) != self.bus or int(device) != self.device_address or transfer[0] == "Z":
                return
            transfer_type = {"C": "control", "B": "bulk", "I": "interrupt"}[transfer[0]]
            endpoint_number = int(endpoint)
            if not 0 <= endpoint_number <= 15:
                raise ValueError("Invalid USB endpoint number")
            inbound = transfer[1] == "i"
            timestamp = int(words[1]) * 1000
            setup = None
            if words[4] == "s":
                setup = struct.pack("<BBHHH", *(int(value, 16) for value in words[5:10]))
                length_index = 10
            else:
                length_index = 5
            length = int(words[length_index])
            data_tag = words[length_index + 1]
            payload = bytes.fromhex("".join(words[length_index + 2:])) if data_tag == "=" else b""
        except (ValueError, KeyError, IndexError, struct.error):
            self.dropped += 1
            return
        tag, event_type = words[0], words[2]
        if event_type == "S":
            if len(self.pending) >= 4096:
                self.dropped += 1
                return
            with self.lock:
                label = dict(self.label)
            self.pending[tag] = {"label": label, "setup": setup, "payload": payload,
                                 "timestamp": timestamp, "created": time.monotonic(), "address": words[3]}
            return
        if event_type not in ("C", "E"):
            return
        pending = self.pending.pop(tag, None)
        if pending is None:
            return
        if pending["address"] != words[3]:
            self.dropped += 1
            return
        try:
            status = int(words[4].split(":")[0])
        except ValueError:
            self.dropped += 1
            return
        setup = pending["setup"]
        packets = []
        selector = f"endpoint:{endpoint_number}"
        if setup is not None:
            selector = f"control:{setup[0]:x}:{setup[1]:x}"
            packets.append({"direction": "host_to_device", "component": "setup", "payload_hex": setup.hex(), "transfer_type": "control"})
        observed = payload if inbound else pending["payload"]
        problem = None
        if length > len(observed):
            problem = "usbmon_payload_not_fully_captured"
        elif length > 8192:
            problem = "oversized_payload"
        elif length:
            component = "data" if setup is None else f"data:index:{int.from_bytes(setup[4:6], 'little'):x}"
            packets.append({"direction": "device_to_host" if inbound else "host_to_device", "component": component,
                            "payload_hex": observed[:length].hex(), "transfer_type": transfer_type})
        self.recorder.record({"kind": "call", "function": "usbmon", "endpoint": f"usb:{self.bus}:{self.device_address}:ep{endpoint_number}",
                              "selector": selector, "timestamp_ns": pending["timestamp"], "label": pending["label"],
                              "status": status, "problem": problem, "packets": packets})

    def _run(self):
        buffer = b""
        try:
            while not self.ended.is_set():
                ready, _, _ = select.select([self.descriptor], [], [], 0.1)
                if not ready:
                    continue
                chunk = os.read(self.descriptor, 65536)
                if not chunk:
                    raise OSError("usbmon stream ended")
                buffer += chunk
                if len(buffer) > 1024 * 1024:
                    raise ValueError("usbmon line buffer limit reached")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    self.feed_line(line.decode("ascii", errors="replace"))
                expired = [tag for tag, item in self.pending.items() if time.monotonic() - item["created"] > 30]
                for tag in expired:
                    self.pending.pop(tag)
                    self.dropped += 1
        except (OSError, ValueError) as error:
            self.error = str(error)
            self.ended.set()

    def stop(self):
        self.ended.set()
        if self.worker is not None:
            self.worker.join(timeout=2)
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None
        return {"usbmon_dropped_or_unmatched": self.dropped, "incomplete_urbs": len(self.pending)}


def run_capture(options):
    if options.provider != "iokit" and options.transport != "usb":
        raise ValueError("libusb/usbmon providers observe USB; Thunderbolt requires the driver/API provider")
    if options.provider in ("iokit", "libusb") and options.pid is None:
        raise ValueError("An explicit --pid is required; no process is selected automatically")
    if options.provider in ("iokit", "libusb") and (options.pid <= 0 or options.pid == os.getpid()):
        raise ValueError("Choose a positive mixer/helper PID, not the capture process")
    if options.provider == "usbmon" and (options.bus is None or options.device_address is None):
        raise ValueError("usbmon requires --bus and --device-address")
    if options.provider in ("iokit", "libusb") and not capture_capabilities()["frida_installed"]:
        raise ValueError("Capture runtime unavailable. Install requirements-capture.txt after ensuring sufficient free disk space")
    layer = {"iokit": "driver_api", "libusb": "application_api", "usbmon": "bus"}[options.provider]
    if options.provider == "iokit" and options.handle is not None:
        raise ValueError("IOKit uses --connection, not a libusb --handle")
    if options.provider == "libusb" and (options.connection is not None or options.selector is not None):
        raise ValueError("libusb uses --handle; IOKit connection/selector filters would not apply")
    provider = USBMonProvider(None, options.bus, options.device_address) if options.provider == "usbmon" else FridaProvider(
        None, options.pid, options.provider, options.connection, options.selector, options.handle)
    recorder = CaptureRecorder(options.output, {"identity": options.device_id, "model": options.model,
                                               "firmware": options.firmware}, options.transport, layer, options.provider)
    provider.recorder = recorder
    stopped = threading.Event()
    output_lock = threading.Lock()

    def emit(value):
        with output_lock:
            print(json.dumps(value, allow_nan=False), flush=True)

    def commands():
        for line in sys.stdin:
            try:
                value = json.loads(line, parse_constant=reject_constant, object_pairs_hook=unique_object)
                if value == {"action": "stop"}:
                    stopped.set()
                    return
                if not isinstance(value, dict) or set(value) != {"action", "label"} or value["action"] != "label":
                    raise ValueError("Use a label or stop command")
                label = provider.set_label(value["label"])
                emit({"event": "label", "label": label})
            except Exception as error:
                emit({"event": "command_error", "message": str(error)})
        stopped.set()

    for termination_signal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(termination_signal, lambda *_: stopped.set())
    provider_info = {}
    try:
        provider_info = provider.start()
        emit({"event": "ready", "provider": options.provider, "info": provider_info, "output": str(recorder.output)})
        threading.Thread(target=commands, daemon=True).start()
        while not stopped.wait(0.25):
            if provider.ended.is_set() or recorder.limit_reached.is_set():
                break
            emit({"event": "progress", "stats": recorder.snapshot()})
        if provider.error:
            emit({"event": "capture_error", "message": provider.error})
    finally:
        extra = provider.stop()
        filters = {"pid": options.pid, "connection": options.connection, "selector": options.selector,
                   "handle": options.handle, "bus": options.bus, "device_address": options.device_address}
        recorder.finish({"provider_info": provider_info, "provider_stats": extra, "provider_error": provider.error, "selection": filters})
    emit({"event": "saved", "output": str(recorder.output), "stats": recorder.snapshot(), "error": provider.error})
