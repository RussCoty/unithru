# Optional extension proposals

These extensions are design requirements for later drafts. Core 0.1 defines no extension write messages. Discovery lists supported extension names/versions; an empty list is valid. Clients MUST ignore unavailable optional features and MUST NOT send guessed commands. No plugin format compatibility is implied.

## Hardware effects

Proposed extension name: `effects`, version to be assigned after a working prototype. It should describe hardware DSP blocks, not expose arbitrary host plugins as universally portable effects.

Discovery needs stable effect instance and type IDs, display labels, legal insertion points, mono/stereo compatibility, parameter IDs, types, units, ranges, steps, enum values, defaults, read-only flags, bypass semantics and parameter dependencies. Shared DSP and buses require explicit resource IDs and ownership conflicts. Preset names cannot replace typed parameter descriptions.

Resource discovery must include free/total slots or DSP budget, incompatible combinations, sample-rate restrictions and whether allocation can fail dynamically. Changes require revision checks and verified replies. Latency metadata must distinguish processing latency, bypass latency, and an unknown value; disclose whether switching or parameter changes cause a gap or transient. Roundtrip converter/driver latency is separate from DSP latency. The device must report when sample-rate changes invalidate availability or latency.

The signal path must distinguish `monitor_only` from `recorded`. A monitor-only reverb may feed a cue return while capture stays dry. An insert upstream of capture can print processing even if it also affects monitoring. Such a block must be labeled recorded/shared explicitly and require separate user approval, resource ownership and a DAW indication. A client may never infer dry recording from an effect's name. If hardware cannot separate the paths, it must report that restriction.

Bypass should specify hard/soft behavior, tail handling, gain continuity and whether bypass frees resources. Allocation, parameter update, bypass and release need a transaction model with the same duplicate/recovery principles as core. Failure must leave known observed state and never silently move an effect into the capture path. A fixture schema and wire binding are deferred until this model has been tried on hardware.

## Preamp and phantom

Proposed extension name: `preamp`. Analog gain in dB, pad, impedance and phantom power are separate from monitor-route gain. These controls may affect every route and the recorded signal, so route ownership is insufficient. Define shared-resource leases, safe sequencing, hardware limits, verified readback and explicit user confirmation at the DAW/adapter UI. Phantom power must never change as a side effect of acquire, set_route, expiry or restoration in core. Devices lacking these controls still implement core normally.
