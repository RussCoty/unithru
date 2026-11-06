# UniThru

An open protocol proposal for DAW-controlled input monitoring in audio-interface hardware mixers.

**Status: draft 0.1; experimental implementation published 6 November 2026.** Includes an OSC/UDP fake mixer, a native macOS monitor, an opt-in Ardour monitoring preview, and USB/Thunderbolt learning tools. Native IOKit and libusb capture pass interception tests against a disposable mock process; manufacturer hardware remains unverified. No working hardware adapter or production DAW integration exists yet. Addresses, schemas and semantics may change before 1.0. No manufacturer or DAW endorsement is claimed.

The DAW decides when an input should be heard. A UniThru device or bridge applies that decision to a hardware mixer. Audio continues through the interface; UniThru carries control and state. The intended workflow is automatic hardware input monitoring during recording and punch, with separate performer cue levels.

The required core covers route enable, monitor gain, pan, discovery, ownership and observed state. Discoverable hardware effects are an optional extension. Preamp gain and phantom power are separate optional controls. OSC is the first proposed binding; limited MIDI and possible USB/Thunderbolt integration paths are documented separately.

## Read the draft

- [Requirements and architecture](docs/design.md)
- [Core specification 0.1](spec/core-v0.1.md)
- [OSC/UDP binding](spec/osc-v0.1.md)
- [Other transports and integration paths](docs/transports.md)
- [Optional hardware effects and preamps](spec/extensions.md)
- [Recording and recovery scenarios](docs/scenarios.md)
- [Ardour development preview](docs/ardour.md)
- [UniThru Monitor desktop app](docs/monitor.md)
- [USB/Thunderbolt device-learning workbench](docs/learning.md)
- [Live capture providers and host requirements](docs/capture.md)
- [Conformance plan](docs/conformance.md)
- [Implementation roadmap and open decisions](docs/roadmap.md)
- [Related work and primary sources](docs/related-work.md)

## Repository material

`schema/` contains JSON Schema 2020-12 definitions for the proposed core messages. `examples/` contains a discovery reply, snapshot and request/response exchange. These are protocol fixtures, not captured hardware traffic. `tools/validate.py` checks schemas, examples and cross-message invariants. `unithru/` implements the experimental fake mixer, core lifecycle, OSC codec, loopback server and demo client. Tests cover deterministic state transitions and actual loopback UDP packets.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python tools/validate.py
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unithru demo
```

The demo starts its own server on an OS-assigned loopback port, drops one set-route reply, retries the same request, verifies the result, restores the baseline and closes its own socket. It does not open an audio device, control another application or stop any existing service. See [simulator usage and limits](docs/simulator.md) for a persistent server and fault-injection examples.

An opt-in [Ardour EditorHook and preview bridge](docs/ardour.md) can feed one explicitly mapped audio track's resolved monitoring state into a private fake mixer. This reads DAW state only; real hardware control and software-monitor handoff remain unimplemented.

[UniThru Monitor](docs/monitor.md) provides a separate native macOS window with requested/applied ON/OFF indicators, demo controls and an Ardour source connection. Build it with `.venv/bin/python tools/build_monitor.py`; open `build/UniThru Monitor.app`. It starts only its own simulator and leaves other applications alone.

Its [Device learning tab](docs/learning.md) adds USB/Thunderbolt inventory, optional [live process/API and Linux usbmon capture providers](docs/capture.md), and analysis of labelled control traces. It produces unverified candidates with writes disabled. Native capture still needs its optional runtime, target permissions and live validation; raw Thunderbolt wire capture and learned hardware adapters are not implemented.

For the first real-device experiment, follow the [hardware capture trial](docs/hardware-trial.md). Capture traces stay local in the ignored `captures/` directory; only reviewed source and synthetic examples belong in the public repository.

A REAPER adapter and RME TotalMix bridge are candidates for the first hardware trial, followed by a second independent integration before 1.0. Existing vendor applications or drivers may be necessary for such trials; the UniThru specification and implementation will remain open source.

## Licenses and participation

Documentation, including this README, `docs/`, `spec/` and `CONTRIBUTING.md`, is [CC BY 4.0](LICENSES/CC-BY-4.0.txt). Code, schemas, JSON examples, tests and build/CI configuration are [MIT](LICENSE). See [license scope and attribution](LICENSES/README.md). No external patent rights or vendor trademarks are granted by this project.

Issues and reviewed changes are welcome; start with [CONTRIBUTING.md](CONTRIBUTING.md). Interoperability needs measured behavior on real systems. This draft makes no claim of novelty, standard-body approval or production readiness.
