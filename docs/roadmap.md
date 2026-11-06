# Implementation plan

Work is ordered by evidence needed, with no delivery promises.

## 1. Review the contract

Review core lifecycle, quantization, mixer resource overlap, manual-edit races and session/cache limits. Resolve large-snapshot pagination before supporting devices beyond the initial loopback size cap. Review whether JSON-in-OSC is the best first binding for embedded manufacturers. Record changes in draft versions with fixture migrations.

Exit: reviewers can implement both ends without guessing operation fields, error behavior or cleanup rules. Existing fixtures are useful starting points, not proof that this gate has been met.

## 2. Simulator and conformance harness

An initial implementation is available as of 2026-10-30: [usage, test coverage and remaining limits](simulator.md). It covers synchronous fake state, the core lifecycle, real OSC/UDP framing and retries, and deterministic injected failures. The broader conformance gate below remains open.

Build an open-source OSC client/server over loopback with a fake mixer backend, monotonic lease timer, request cache, scoped resource reservation and controllable failures. Use a backend interface with read, apply, reserve and cancel/reconcile operations. Add packet capture fixtures and a small CLI that shows requested/applied state and ownership. Keep the audio path out of the simulator.

Exit: executable lifecycle, network fault and late-backend-completion cases from the conformance plan pass reproducibly. Publish limitations and observed results.

## 3. First DAW and hardware trial

An initial Ardour development [preview](ardour.md) reads one mapped track's monitoring state through a Lua EditorHook and drives a private simulator. It changes no DAW audio controls and does not establish hardware interoperability. Live-session validation, explicit hardware identity mapping and safe monitoring handoff are still required.

Prototype a REAPER integration using supported APIs for monitor mode, track input/arm and transport/punch state. Confirm that software monitoring can be controlled and observed for the chosen workflow. Investigate an RME TotalMix OSC bridge, including feedback, crosspoint addressing, submix selection, manual edits and multi-output independence. Document legal/access and driver prerequisites; do not bundle a proprietary SDK or assume a vendor UI setting.

Study TotalReaper as a practical reference and possible future collaboration, respecting its license. A [public review invitation](https://github.com/acklin83/totalreaper/issues/4) has been posted; no collaboration has been agreed. Publish a channel-mapping procedure and repeatable audio/readback tests. If reliable readback or cancellation cannot be achieved, label the adapter experimental and document which core requirements it cannot meet.

Exit: a performer cue overdub and best-effort punch workflow work on a named test setup, with measured latency and no unexplained mixer changes.

## 4. Hardware effects prototype

Implement discovery and control for a small real set of DSP blocks. Establish resource limits, path placement, bypass behavior and latency reporting before defining a public effects binding. Demonstrate dry capture with monitor effects where hardware supports it. Keep preamp/phantom separate and optional.

## 5. Independent interoperability and 1.0

Bring up a second independent DAW or device integration, preferably both, against the same harness. Investigate an AES70 profile/gateway before expanding custom network machinery. Trial remote protected access only after loopback behavior is reliable. Stabilize core after resolving manual-edit guarantees, large devices, crash journals, MIDI negotiation needs and timing-extension requirements.

Version 1.0 requires public interoperability results, a clear compatibility policy and a reviewed specification. Manufacturer adoption, driver changes and sample-accurate punch remain dependent on capabilities outside this repository.

## Cross-device learning and later Pro Tools research

The [learning workbench](learning.md) provides macOS USB/Thunderbolt inventory, trace analysis and optional [capture providers](capture.md) for IOKit, libusb and Linux usbmon. Native IOKit and libusb interception tests pass against a disposable mock process. The next gate is a [labelled real-device capture](hardware-trial.md). Finish live hardware validation, expand API coverage, and add binary bus-capture exporters before claiming broad device compatibility. The common analysis layer remains vendor-independent. Move candidates into adapters only after verified identity, encoding, readback, route isolation and recovery tests pass.

Investigate Pro Tools hardware later as a distinct PCIe/driver/DigiLink research track. Establish an observable monitoring-control boundary on real equipment first. This is not a commitment to implement DigiLink or replace HDX DSP, drivers or firmware.
