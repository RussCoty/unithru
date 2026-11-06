# Live capture providers

The learner now has provider code for observing selected processes and Linux USB transfers. Capture feeds the existing normalized trace analyser; it never sends learned commands or enables hardware writes.

**Validation status, 2026-11-06:** all 112 local tests pass, including native IOKit and libusb interception against our disposable mock process with Frida 17.2.17. The [hosted capture checks](https://github.com/RussCoty/unithru/actions/runs/42208567067) also pass their native-fixture tests on macOS 15 and Linux, and compile the macOS monitor. The Linux job skips the macOS-only IOKit case. No manufacturer mixer has been attached or hardware protocol verified. The [first hardware trial](hardware-trial.md) remains pending an available interface and mixer app.

## Observation paths

| Provider | What it observes | Important limits |
| --- | --- | --- |
| `iokit` | A selected macOS PID's synchronous `IOConnectCallMethod`, `IOConnectCallScalarMethod` and `IOConnectCallStructMethod` calls | Driver/API boundary, not physical USB or Thunderbolt wire traffic; requires attach permission and actual use of these APIs |
| `libusb` | A selected PID's synchronous control, bulk and interrupt transfer calls | Application/API boundary; does not cover other USB libraries or libusb's asynchronous submission API |
| `usbmon` | Linux host-controller USB submission/completion pairs for one bus/device address | Existing readable text node required; not available on this Mac; payload truncation can prevent analysis |

The IOKit provider accepts a USB or Thunderbolt provenance label because either kind of driver can expose this user-client boundary. The label is supplied by the operator, not inferred from the PID. Not all Thunderbolt drivers or mixer applications use these methods. Async IOKit calls, traps, shared-memory command rings, XPC-only frontends and other private APIs are outside this first provider.

The process agent observes arguments at entry and output buffers on successful return. It does not replace return values, edit arguments, write buffers, call device methods, enumerate/attach other processes, spawn the mixer or stop it. Nested wrapper calls are suppressed to avoid double counting; this can omit genuinely distinct nested calls. Instrumentation itself adds overhead and temporarily inserts hooks, so these are engineering/test-session tools, not an uninterrupted-audio guarantee.

## Install optional support and probe

```sh
.venv/bin/python -m pip install -r requirements-capture.txt
.venv/bin/python -m unithru learn capabilities
```

The optional process-capture runtime is Frida 17.x. Core simulation, inventory and offline analysis do not require it. The capability probe imports the native runtime rather than checking only that a package directory exists. Provider availability still does not establish permission to attach to a particular PID or prove that the process issues relevant calls.

The initial installation hit disk-space errors; installation and native-fixture verification subsequently succeeded after more space became available. Storage remains limited. This change did not delete unrelated files, install system drivers or alter process/OS protections. A protected or hardened target may still refuse attachment; the provider reports the error and does not weaken its protections. Use an approved debug build, an accessible vendor service/API exporter, or a compatible bus-capture environment when needed.

## Desktop workflow

Build/open [UniThru Monitor](monitor.md), select Device learning and press **Check capture support**. The app also probes on opening the tab. Start stays disabled when the runtime is absent.

1. Choose IOKit or libusb. For IOKit, label the underlying transport as USB or Thunderbolt.
2. Enter the explicit PID of the mixer or the control helper actually issuing device calls. Obtain it from Activity Monitor or your development tools. Do not guess another process or assume the GUI process owns the driver connection.
3. Enter capture identity/model labels. If a helper handles several devices, start with a discovery capture and use observed connection/handle identities to select the relevant stream on a new capture. These software identities are not stable physical channel IDs.
4. An optional connection filter (IOKit, decimal or `0x` integer) or handle filter (libusb pointer) restricts observation. Advanced selector filtering is available in the CLI.
5. Press **Start live capture**, choosing a new output filename. Capture begins in `baseline-1 / idle`. No relevant exported APIs means an explicit startup failure, not a claim that a device was learned. Exported APIs with zero observed calls can mean the wrong process/provider or a different control path.
6. Select a window, trial ID and label. Press **Apply window** and wait for its acknowledgement before changing one control in the manufacturer's mixer. The displayed applied window is distinct from unsent edits to the fields.
7. Capture repeated idle windows and at least two trials of every labelled value. Use new trial IDs for independent experiments. JSON values include `true`, `false`, `-12`, `0.5`, quoted strings and `null`.
8. Press **Stop & analyse**. The app stops observation, saves the trace and runs offline analysis when usable events exist. It does not terminate the selected target. Exported candidates remain unverified with writes disabled.

For process providers, a label is snapshotted at call entry inside the agent. A completion arriving after a window change retains the original label. usbmon snapshots labels on URB submission and retains them through completion. Readback labels identify experimental windows; they do not certify that an API output is hardware-applied mixer state.

## Command-line capture

The following PID/device values are placeholders, not discovered hardware:

```sh
.venv/bin/python -m unithru learn capture \
  --provider iokit --transport thunderbolt --pid 12345 \
  --device-id 'capture-device-1' --model 'Your interface model' \
  --firmware 'known-version-or-unknown' --output captures/session-1.json
```

For a USB application using libusb, choose `--provider libusb --transport usb`. `--handle 0x...` limits a handle. IOKit supports `--connection 0x...` and `--selector 3`. Filters are provider-specific; passing an ignored filter type is rejected.

The command emits newline-delimited JSON events on stdout and accepts commands on stdin. Keep stdin open throughout the experiment. Examples:

```json
{"action":"label","label":{"phase":"idle","trial_id":"baseline-2"}}
{"action":"label","label":{"phase":"action","trial_id":"trial-1","control":"route.enabled","target":"input-1/cue-1","value":true}}
{"action":"stop"}
```

Wait for a `label` acknowledgement before the associated mixer action. `ready` means hooks/node access succeeded, not that useful payloads have arrived. `progress` reports calls, usable observations and failures. `saved` identifies the output and counts. EOF, SIGINT, SIGTERM, target detachment or capture limits stop the session and save completed observations where possible. Capture helpers are stopped; the selected target is never killed by the provider.

## Linux USB capture

```sh
.venv/bin/python -m unithru learn capture \
  --provider usbmon --transport usb --bus 1 --device-address 5 \
  --device-id 'capture-device-1' --model 'Your interface model' \
  --output captures/linux-usb-session.json
```

The provider opens `/sys/kernel/debug/usb/usbmon/1u` read-only and filters to device address 5 before retaining payloads. It does not load usbmon, mount debugfs, run sudo or change permissions. If the node is absent or unreadable, capture fails explicitly. Device addresses are temporary capture selectors, not stable device identities.

Submission and completion are paired by URB tag and address. Failed transfers remain in the journal but are excluded from learning. Outgoing payloads come from submission; incoming data comes from completion. USB control setup packets are retained separately, including zero-data requests whose values live in `wValue`/`wIndex`. Setup selectors group by request type/request; data selectors retain index context. Captured setup/data bytes are observations, not commands authorized for replay.

Isochronous/audio transfers and other devices are ignored. Missing/truncated payloads are reported and excluded rather than treated as complete messages. The text API has capture-size limits; a later binary usbmon/USBPcap provider can improve completeness without changing the analysis format.

## Files, limits and provenance

An output such as `session.json` creates three owner-readable files using exclusive creation:

- `session.json`: completed, usable normalized observations for the learner. Zero usable events is a real capture result; it is not converted into synthetic data or a learned profile.
- `session.json.raw.jsonl`: bounded call records, including failures and unreadable/truncated observations.
- `session.json.capture.json`: provider, target selection, platform, timing/limit metadata, drop counts and trace checksum. A checksum is a consistency link, not proof of trustworthy labels.

The normalised trace and manifest are flushed on orderly stop. Keep them together. `learn analyze` verifies a matching acquisition checksum and carries metadata into the report; incomplete captures downgrade candidate evidence. Editing the trace invalidates that link. Derived/relabelled datasets must retain their original evidence separately rather than silently reusing a stale acquisition manifest.

The agent bounds each copied buffer at 8192 bytes and emits at most 200 call records/second with a burst allowance. It stops emitting after 20000 calls and reports rate drops. The recorder limits normalized events to 20000/8 MiB compact data and raw journal data to 16 MiB, stopping explicitly at limits. usbmon retains at most 4096 pending URBs and expires unmatched submissions after 30 seconds when processing traffic. No silent truncation is accepted as a complete control payload.

Frida timestamps describe wall-clock milliseconds at call entry. usbmon uses its reported microsecond timestamps. Neither is a hardware-applied timestamp or a sample clock. API success, echoed output, a repeatable byte change and physical audible monitoring are four different observations. Actual readback, route identity/isolation and recovery still need adapter validation before any learned hardware writes.

## Tests and primary references

Recorder and parser tests use synthetic observations. A Node harness executes the actual agent against pointer/interceptor fixtures, including nesting, label boundaries, filters, return counts and bounds. Optional native tests compile an explicitly disposable C fixture that exports mock API names; they never address real hardware. On macOS, only that test binary is ad-hoc signed with a debug entitlement. Target applications are never re-signed. Both native interception tests pass locally with Frida 17.2.17. An initial run hit a fixture shutdown timeout; cleanup now allows ten seconds and always reaps its own fixture on timeout.

```sh
.venv/bin/python -m unittest discover -s tests -p test_capture.py -v
```

`.github/workflows/capture-fixtures.yml` is a manually runnable macOS/Linux native-fixture job after publication; ordinary tests may skip the optional runtime cases. Linux usbmon behavior has parser-fixture coverage here, not a live Linux bus validation result.

- [Frida JavaScript API](https://frida.re/docs/javascript-api/): exported-symbol lookup, argument observation and interceptors.
- [Apple IOConnect scalar method API](https://developer.apple.com/documentation/iokit/1514793-ioconnectcallscalarmethod): synchronous user-client call boundary and buffer/count types.
- [libusb synchronous I/O](https://libusb.sourceforge.io/api-1.0/group__libusb__syncio.html): control, bulk and interrupt request/return semantics.
- [Linux usbmon](https://docs.kernel.org/usb/usbmon.html): host-controller request traces, submission/completion records and text capture limitations.
