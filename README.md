# UniThru

An open protocol proposal for DAW-controlled input monitoring in audio-interface hardware mixers.

**Status: draft 0.1, 24 October 2026.** No hardware integration or wire implementation exists yet. Addresses, schemas and semantics may change before 1.0. No manufacturer or DAW support is claimed.

The DAW decides when an input should be heard. A UniThru device or bridge applies that decision to a hardware mixer. Audio continues through the interface; UniThru carries control and state. The intended workflow is automatic hardware input monitoring during recording and punch, with separate performer cue levels.

The required core covers route enable, monitor gain, pan, discovery, ownership and observed state. Discoverable hardware effects are an optional extension. Preamp gain and phantom power are separate optional controls. OSC is the first proposed binding; limited MIDI and possible USB/Thunderbolt integration paths are documented separately.

## Read the draft

- [Requirements and architecture](docs/design.md)
- [Core specification 0.1](spec/core-v0.1.md)
- [OSC/UDP binding](spec/osc-v0.1.md)
- [Other transports and integration paths](docs/transports.md)
- [Optional hardware effects and preamps](spec/extensions.md)
- [Recording and recovery scenarios](docs/scenarios.md)
- [Conformance plan](docs/conformance.md)
- [Implementation roadmap and open decisions](docs/roadmap.md)
- [Related work and primary sources](docs/related-work.md)

## Repository material

`schema/` contains JSON Schema 2020-12 definitions for the proposed core messages. `examples/` contains a discovery reply, snapshot and request/response exchange. These are protocol fixtures, not captured hardware traffic. `tools/validate.py` checks schemas, examples and cross-message invariants. It does not exercise OSC, a DAW or hardware.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python tools/validate.py
.venv/bin/python -m unittest discover -s tests -v
```

The first implementation milestone is a loopback simulator and conformance harness. A REAPER adapter and RME TotalMix bridge are candidates for the first hardware trial, followed by a second independent integration before 1.0. Existing vendor applications or drivers may be necessary for such trials; the UniThru specification and implementation will remain open source.

## Licenses and participation

Documentation, including this README, `docs/`, `spec/` and `CONTRIBUTING.md`, is [CC BY 4.0](LICENSES/CC-BY-4.0.txt). Code, schemas, JSON examples, tests and build/CI configuration are [MIT](LICENSE). See [license scope and attribution](LICENSES/README.md). No external patent rights or vendor trademarks are granted by this project.

Issues and reviewed changes are welcome; start with [CONTRIBUTING.md](CONTRIBUTING.md). Interoperability needs measured behavior on real systems. This draft makes no claim of novelty, standard-body approval or production readiness.
