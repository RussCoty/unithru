# Cross-device learning workbench

The aim is to build UniThru adapters for existing hardware without requiring each manufacturer to implement UniThru. The learner separates three jobs: finding a device, observing its control traffic, and establishing what that traffic does. USB and Thunderbolt are both in scope. The shared analysis format also accepts driver/API, OSC and MIDI observations.

**Implemented:** read-only macOS USB/Thunderbolt topology inventory, normalized labelled trace imports, byte/value correlation analysis, a Device learning tab, and optional [live capture providers](capture.md). IOKit observes selected macOS driver/API calls; libusb observes synchronous USB calls; Linux usbmon pairs selected-device USB transfers. Native IOKit and libusb attachment/interception tests pass against our disposable mock process; manufacturer hardware remains unverified. Raw Thunderbolt wire capture, automatic protocol reconstruction, packet replay and working learned hardware adapters are not provided. A device appearing in inventory is not evidence that its mixer can be controlled.

## Use the desktop tool

Build and open [UniThru Monitor](monitor.md), then select **Device learning**.

1. **Scan USB & Thunderbolt** queries macOS inventory without claiming interfaces, opening audio streams or changing drivers. Endpoint addresses are not reported by this inventory provider; they must come from a capture/descriptor provider.
2. **Load synthetic example** demonstrates learning from a fictional USB trace. It finds a route-enable byte and a cue-gain byte while filtering out an idle-varying counter. These are fabricated test bytes, not commands for a real device.
3. Use the [Live capture panel](capture.md) when its optional runtime and target permissions are available, or **Import labelled trace** from another provider. Raw PCAP/PCAPNG files are not accepted by this version; an exporter must preserve the relevant payload and metadata in the format below.
4. **Export candidates** writes a provenance-linked report. Every candidate and the top-level report retain `validated: false` and `write_enabled: false`. There is no replay button or executable adapter template.

The same operations are available from the repository root:

```sh
.venv/bin/python -m unithru learn inventory
.venv/bin/python -m unithru learn analyze examples/learning/labelled-trace.json
.venv/bin/python -m unithru learn analyze captures/labelled-trace.json --output captures/candidate-profile.json
```

Output files use exclusive creation: an existing file is not overwritten. The local `captures/` directory is ignored by Git. Inventory output omits serial numbers and Thunderbolt switch/domain UIDs; imported trace metadata is supplied by its author and should be reviewed before sharing. Nothing is uploaded automatically.

## Capture providers and transport boundaries

| Path | Acquisition approach | Current support |
| --- | --- | --- |
| macOS USB | Inventory plus selected-process IOKit/libusb instrumentation where the application uses those APIs | Native mock-process interception tested; manufacturer target/hardware validation pending |
| Linux USB | usbmon observes driver requests to USB host controllers | Text provider implemented; live bus validation pending |
| Windows USB | USBPcap/Wireshark capture path | Provider/exporter planned |
| Thunderbolt/PCIe | Inventory plus selected-process synchronous IOKit call observation | Provider code implemented; no raw wire sniffer or universal driver coverage |
| OSC / MIDI | Export identifiable control messages into the same observation model | Normalized imports only |
| Pro Tools HDX/DigiLink | Investigate host-driver and hardware boundaries on a documented test setup | Future research; no capture or compatibility claim |

[Linux usbmon](https://www.kernel.org/doc/html/v6.7/usb/usbmon.html) reports requests made by peripheral drivers to host-controller drivers; it is not necessarily a perfect recording of physical bus transactions. Preserve that distinction in capture provenance.

[Wireshark's USB capture documentation](https://wiki.wireshark.org/CaptureSetup/USB) describes different capture mechanisms and restrictions across operating systems. A working capture method on one OS does not imply a corresponding one on another. This app installs no capture driver or kernel extension and makes no system-protection changes.

[Apple IOUSBHost](https://developer.apple.com/documentation/iousbhost/) provides user-space device/driver access. Its [deviceCapture option](https://developer.apple.com/documentation/iousbhost/iousbhostobjectinitoptions/devicecapture) has authorization/privilege requirements; its name should not be mistaken for a passive sniffer of another driver's traffic. The workbench does not use it.

Thunderbolt can tunnel PCIe, as described in [Intel's architecture brief](https://www.intel.com/content/dam/doc/technology-brief/thunderbolt-technology-brief.pdf). A useful software capture may therefore be at a vendor service or driver-call boundary rather than a USB endpoint. Discovering a Thunderbolt chassis reveals neither mixer selectors nor the semantics of its driver calls. Protected, opaque or inaccessible traffic may prevent learning until a suitable observation point is available.

## Controlled learning procedure

Record idle traffic, then change one control in the manufacturer mixer while labelling its visible value and intended input/cue target. Repeat every value in independent trials, return to a baseline, and collect another idle interval. Keep device firmware, driver version, sample rate, mixer configuration and channel grouping recorded with the experiment. Do not change preamp/phantom controls as a substitute for monitor-gain experiments.

First learn route on/off, monitor gain and pan. Test another channel and another cue to distinguish a control byte from channel addressing. Capture inbound feedback separately from outbound commands. Move an unrelated control as a negative control. A field that changes alongside two labels might be a shared counter, a checksum or a compound state update, not two independent controls.

The current analyser groups observations by endpoint, selector, direction, transfer type and payload length. It filters byte positions that vary during idle; then finds positions that consistently distinguish at least two labelled values. A repeatability label requires at least two trial IDs for every value and two idle samples. Shared correlations across controls/targets are marked ambiguous. Isochronous events are excluded from control analysis.

This is byte-level evidence, not semantic proof. The analyser does not infer multi-byte encoding, checksums, handshakes, sequencing, gain formulas, routing matrices, timing, DSP allocation or recorded-versus-monitor paths. A duplicate packet is not an independent trial. The user/provider supplies trial IDs and labels; false labels can produce false correlations. Candidate status never changes into hardware validation automatically.

## Normalized trace format

`examples/learning/labelled-trace.json` is the complete synthetic reference. The top-level fields are:

- `format`: `unithru-learning/0.1`.
- `source`: `synthetic` or `external`.
- `device`: `identity`, `model` and `firmware` strings. This is capture provenance, not proof of stable hardware identity; use an explicit unknown value when necessary.
- `transport`: `usb`, `thunderbolt`, `driver`, `osc` or `midi`.
- `capture_layer`: `bus`, `driver_api`, `application_api` or `message`.
- `events`: a nonempty array of observations from that single device/capture configuration.

Each event requires `trial_id`, integer `timestamp_ns`, `phase` (`idle`, `action` or `readback`), `endpoint`, `selector`, `direction` (`host_to_device` or `device_to_host`), `transfer_type` and `payload_hex`. Action/readback events additionally require `control`, `target` and scalar `value` labels. The core labels are `route.enabled`, `route.gain_db` and `route.pan`; other labels remain outside the core.

Endpoint and selector strings are provider-defined observation identities, not registered addresses. For a USB control transfer, preserve the setup-request selector and relevant addressing context in these fields so unrelated requests are not combined merely because they share endpoint zero. For a driver call, preserve the service/user-client identity and method selector. A provider must distinguish completed payloads, submissions, responses and duplicates in its normalization; transport headers must not be silently reinterpreted as command bytes.

Limits: 16 MiB input, 20000 events and 8192 payload bytes per event. Hex is contiguous and even-length. The analyser reports at most 256 candidate byte positions; `candidates_truncated` explicitly reports truncation. Captures that require fragmentation/reassembly need a provider to supply complete logical payloads first.

## From observations to an adapter

A later adapter needs a verified model/firmware/driver match, absolute-value encoding, safe readback, channel identity, scoped ownership, resource conflicts and partial-failure/reconnect handling. Monitoring writes must leave unrelated cues, master controls and recording paths unchanged. Unknown bytes must never be replayed simply because they correlated with an on-screen action. Optional effects and preamp controls need separate semantic and signal-path validation.

The open profile catalogue can eventually share validated mappings across devices. The learning engine is vendor-independent; compatibility will still be demonstrated per device/firmware/control path. There is no technically sound promise that every interface can be learned automatically or that all control surfaces expose enough information.

## Pro Tools hardware later

Add Pro Tools hardware as a separate research track after the capture providers and validation process exist. [Avid's HDX installation guide](https://resources.avid.com/SupportFiles/PT/Pro_Tools_HDX_Guide.pdf) describes PCIe HDX cards, host drivers and DigiLink Mini connections to interfaces. A Thunderbolt expansion chassis can sit on the host side without turning the interface link into USB control traffic.

The first question will be which host-driver/API or hardware observation point exposes monitoring changes on a named setup. Control of an interface mixer, DigiLink interoperability, HDX DSP behavior and Pro Tools workflow integration are different questions. No Avid support, DigiLink implementation, firmware modification or HDX replacement is supplied or promised by this workbench.
