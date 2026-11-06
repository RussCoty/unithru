import hashlib
import json
import math
import platform
import subprocess
from collections import defaultdict
from pathlib import Path

from tools.validate import load_json


TRANSPORTS = {"usb", "thunderbolt", "driver", "osc", "midi"}
CONTROL_NAMES = {"route.enabled", "route.gain_db", "route.pan"}
MAX_TRACE_BYTES = 16 * 1024 * 1024


def normalize_inventory(data):
    records = []

    def walk(node, transport, parent=()):
        if isinstance(node, list):
            for item in node:
                walk(item, transport, parent)
            return
        if not isinstance(node, dict):
            return
        path = parent
        name = node.get("device_name_key") or node.get("_name")
        if isinstance(name, str) and name:
            path = parent + (name,)
            identifiers = {}
            for key, label in (("vendor_id", "vendor_id"), ("product_id", "product_id"),
                               ("location_id", "location"), ("USBKeyLocationID", "location")):
                if key in node and isinstance(node[key], (str, int)):
                    identifiers[label] = str(node[key])
            records.append({"transport": transport, "name": name, "topology_path": list(path),
                            "identifiers": identifiers, "endpoints": [],
                            "status": "Inventory only; control endpoints not discovered"})
        for child in node.values():
            if isinstance(child, (dict, list)):
                walk(child, transport, path)

    for category, items in data.items():
        if category in ("SPUSBDataType", "SPUSBHostDataType"):
            walk(items, "usb")
        elif category == "SPThunderboltDataType":
            walk(items, "thunderbolt")
    return {"format": "unithru-inventory/0.1", "devices": records,
            "traffic_captured": False, "serial_numbers_included": False,
            "note": "Topology and location are not stable device/channel identities or mixer capabilities"}


def discover_inventory():
    if platform.system() != "Darwin":
        raise ValueError("This inventory provider currently supports macOS; trace analysis is cross-platform")
    available = subprocess.run(["/usr/sbin/system_profiler", "-listDataTypes"], capture_output=True,
                               text=True, check=True, timeout=15).stdout.split()
    usb_category = "SPUSBHostDataType" if "SPUSBHostDataType" in available else "SPUSBDataType"
    categories = [name for name in (usb_category, "SPThunderboltDataType") if name in available]
    if not categories:
        raise ValueError("No USB or Thunderbolt inventory providers are available")
    result = subprocess.run(["/usr/sbin/system_profiler", *categories, "-json", "-detailLevel", "full"],
                            capture_output=True, text=True, check=True, timeout=30)
    return normalize_inventory(json.loads(result.stdout))


def validate_trace(trace):
    if not isinstance(trace, dict) or trace.get("format") != "unithru-learning/0.1":
        raise ValueError("Expected a normalized unithru-learning/0.1 trace, not a raw packet capture")
    if set(trace) != {"format", "source", "device", "transport", "capture_layer", "events"}:
        raise ValueError("Unexpected or missing trace fields")
    if not isinstance(trace["source"], str) or not isinstance(trace["transport"], str) or trace["source"] not in ("synthetic", "external") or trace["transport"] not in TRANSPORTS:
        raise ValueError("Invalid source or transport")
    if trace["capture_layer"] not in ("bus", "driver_api", "application_api", "message"):
        raise ValueError("Invalid capture layer")
    device = trace["device"]
    if not isinstance(device, dict) or set(device) != {"identity", "model", "firmware"}:
        raise ValueError("Device identity, model and firmware fields are required")
    if any(not isinstance(value, str) or not value or len(value) > 256 for value in device.values()):
        raise ValueError("Invalid device metadata")
    if not isinstance(trace["events"], list) or not 1 <= len(trace["events"]) <= 20000:
        raise ValueError("Trace must contain 1–20000 events")
    required = {"trial_id", "timestamp_ns", "phase", "endpoint", "selector", "direction", "transfer_type", "payload_hex"}
    for event in trace["events"]:
        if not isinstance(event, dict) or set(event) - (required | {"control", "target", "value"}) or not required <= set(event):
            raise ValueError("Invalid event fields")
        for field in ("trial_id", "endpoint", "selector", "transfer_type"):
            if not isinstance(event[field], str) or not event[field] or len(event[field]) > 256:
                raise ValueError(f"Invalid {field}")
        if type(event["timestamp_ns"]) is not int or event["timestamp_ns"] < 0:
            raise ValueError("Timestamp must be a nonnegative integer")
        if event["direction"] not in ("host_to_device", "device_to_host"):
            raise ValueError("Invalid direction")
        if event["phase"] not in ("idle", "action", "readback"):
            raise ValueError("Invalid phase")
        payload = event["payload_hex"]
        if not isinstance(payload, str) or len(payload) > 16384 or len(payload) % 2:
            raise ValueError("Invalid payload size")
        try:
            decoded = bytes.fromhex(payload)
        except ValueError as error:
            raise ValueError("Payload is not hex") from error
        if len(decoded) * 2 != len(payload):
            raise ValueError("Hex payload must not contain whitespace")
        if event["phase"] != "idle":
            if not {"control", "target", "value"} <= set(event):
                raise ValueError("Action/readback events require a control, target and labelled value")
            if any(not isinstance(event[field], str) or not event[field] or len(event[field]) > 256 for field in ("control", "target")):
                raise ValueError("Invalid control or target label")
            value = event["value"]
            if value is not None and type(value) not in (bool, int, float, str):
                raise ValueError("Label value must be a JSON scalar")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Nonfinite label value")
            if isinstance(value, str) and len(value) > 256:
                raise ValueError("Label value exceeds 256 characters")
    return trace


def analyze_trace(trace):
    validate_trace(trace)
    payloads = {id(event): bytes.fromhex(event["payload_hex"]) for event in trace["events"]}
    streams = defaultdict(list)
    skipped_audio = 0
    for event in trace["events"]:
        if event["transfer_type"] == "isochronous":
            skipped_audio += 1
            continue
        key = (event["endpoint"], event["selector"], event["direction"], event["transfer_type"], len(event["payload_hex"]) // 2)
        streams[key].append(event)
    candidates = []
    truncated = False
    stream_reports = []
    for stream, events in streams.items():
        endpoint, selector, direction, transfer_type, length = stream
        idle = [payloads[id(event)] for event in events if event["phase"] == "idle"]
        volatile = {offset for offset in range(length) if len({payload[offset] for payload in idle}) > 1}
        stream_reports.append({"endpoint": endpoint, "selector": selector, "direction": direction,
                               "transfer_type": transfer_type, "payload_bytes": length,
                               "events": len(events), "idle_samples": len(idle), "volatile_offsets": sorted(volatile)})
        labels = defaultdict(list)
        for event in events:
            if event["phase"] != "idle":
                labels[(event["control"], event["target"], event["phase"])].append(event)
        for (control, target, phase), labelled in labels.items():
            values = defaultdict(list)
            for event in labelled:
                values[json.dumps(event["value"], sort_keys=True, allow_nan=False)].append(event)
            if len(values) < 2:
                continue
            for offset in range(length):
                if offset in volatile:
                    continue
                encodings = []
                observed_bytes = []
                for value, group in sorted(values.items()):
                    byte_values = {payloads[id(event)][offset] for event in group}
                    if len(byte_values) != 1:
                        break
                    byte_value = next(iter(byte_values))
                    observed_bytes.append(byte_value)
                    encodings.append({"label_value": json.loads(value), "label": value, "byte_hex": f"{byte_value:02x}",
                                      "samples": len(group), "trials": len({event["trial_id"] for event in group})})
                if len(encodings) != len(values) or len(set(observed_bytes)) != len(values):
                    continue
                if len(candidates) >= 256:
                    truncated = True
                    continue
                warnings = ["Correlation only; no verified readback or audio-path isolation", "No executable command template generated"]
                if len(idle) < 2:
                    warnings.append("Fewer than two idle samples; background fields may be mistaken for controls")
                repeated = all(value["trials"] >= 2 for value in encodings)
                if not repeated:
                    warnings.append("Each labelled value needs at least two independent trial IDs")
                if control not in CONTROL_NAMES:
                    warnings.append("Label is outside the UniThru route core; never map it implicitly")
                if trace["source"] == "synthetic":
                    warnings.append("Synthetic fixture; not evidence about physical hardware")
                candidate_key = json.dumps([stream, control, target, phase, offset])
                candidates.append({"id": hashlib.sha256(candidate_key.encode()).hexdigest()[:16],
                                   "endpoint": endpoint, "selector": selector, "direction": direction,
                                   "transfer_type": transfer_type, "payload_bytes": length,
                                   "control": control, "target": target, "phase": phase,
                                   "byte_offset": offset, "encodings": encodings,
                                   "evidence": "repeatable_correlation" if repeated and len(idle) >= 2 else "provisional_correlation",
                                   "warnings": warnings, "validated": False, "write_enabled": False})
    shared = defaultdict(list)
    for candidate in candidates:
        shared[(candidate["endpoint"], candidate["selector"], candidate["direction"],
                candidate["transfer_type"], candidate["payload_bytes"], candidate["byte_offset"])].append(candidate)
    for group in shared.values():
        if len({(item["control"], item["target"]) for item in group}) > 1:
            for item in group:
                item["warnings"].append("This byte also correlates with another control/target; semantics are ambiguous")
                item["evidence"] = "ambiguous_correlation"
    digest = hashlib.sha256(json.dumps(trace, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {"format": "unithru-learning-profile/0.1", "source": trace["source"],
            "device": dict(trace["device"]), "transport": trace["transport"],
            "capture_layer": trace["capture_layer"], "capture_sha256": digest,
            "validated": False, "write_enabled": False, "event_count": len(trace["events"]),
            "skipped_isochronous_events": skipped_audio, "candidates_truncated": truncated,
            "streams": stream_reports, "candidates": candidates}

def analyze_file(path):
    source = Path(path)
    if source.stat().st_size > MAX_TRACE_BYTES:
        raise ValueError("Trace exceeds the 16 MiB analysis limit")
    report = analyze_trace(load_json(source))
    manifest_path = source.with_suffix(source.suffix + ".capture.json")
    if manifest_path.exists():
        if manifest_path.stat().st_size > 1024 * 1024:
            raise ValueError("Acquisition manifest exceeds its size limit")
        manifest = load_json(manifest_path)
        if manifest.get("trace_sha256") != report["capture_sha256"]:
            raise ValueError("Acquisition manifest does not match this trace; preserve the original capture or remove the stale sidecar")
        report["acquisition"] = manifest
        stats = manifest.get("stats", {})
        provider_stats = manifest.get("provider_stats", {})
        incomplete = manifest.get("limit_reached") or manifest.get("provider_error") or stats.get("dropped", 0) or \
            stats.get("unusable_calls", 0) or provider_stats.get("calls_rate_dropped", 0) or \
            provider_stats.get("usbmon_dropped_or_unmatched", 0) or provider_stats.get("incomplete_urbs", 0) or \
            provider_stats.get("in_flight_threads", 0)
        if incomplete:
            for candidate in report["candidates"]:
                candidate["warnings"].append("Capture is incomplete or contains unreadable/dropped observations; inspect acquisition metadata")
                candidate["evidence"] = "incomplete_capture_correlation"
    return report


def run_learning(options):
    if options.learn_command == "capture":
        from unithru.capture import run_capture

        run_capture(options)
        return
    try:
        if options.learn_command == "capabilities":
            from unithru.capture import capture_capabilities

            result = capture_capabilities()
        else:
            result = discover_inventory() if options.learn_command == "inventory" else analyze_file(options.trace)
    except subprocess.SubprocessError as error:
        raise ValueError(f"Inventory query could not complete: {error}") from error
    text = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if options.output is None:
        print(text, end="")
    else:
        with Path(options.output).open("x", encoding="utf-8") as destination:
            destination.write(text)
