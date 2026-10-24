# Requirements and architecture

## Scope

UniThru proposes a shared vocabulary between a DAW and an interface mixer. Manufacturers may implement a device service directly; an adapter may translate into an existing mixer API. A DAW integration may be native or use a supported extension API. A plugin alone may not have access to track input assignments, punch state or software-monitor control, so it cannot be assumed sufficient.

```text
DAW track/transport state -> DAW adapter -> UniThru service -> hardware mixer
physical input ------------------------------------------> cue output
physical input -> interface capture stream -> DAW recording
```

The last two paths are independent. Monitoring controls MUST NOT change the capture path. Optional recorded processing requires an explicit extension and user consent.

## Required behavior

1. Identify devices, inputs and output buses across reconnects without silently remapping channels.
2. Discover supported topology, gain range, pan law, readback quality and resource limits before creating routes.
3. Enable/disable input-to-cue routes, set route gain and pan, and read actual applied values.
4. Support one input feeding multiple cue buses at independent levels.
5. Give each physical route one writer; resolve multiple track intents in the DAW adapter.
6. Return correlated results, reject stale writes, tolerate duplicate requests and expose partial hardware failures.
7. Preserve unrelated mixer state and stop automatic writes after ownership loss or a local manual edit.
8. Recover using fresh observed state after packet loss, application failure or hardware reconnect.

No mandatory effects, preamp, phantom, streaming audio, clock distribution, plugin hosting or vendor SDK is part of core. Metering is deferred.

## DAW policy

Track arm is an eligibility signal, not an instruction to make a route audible. The DAW adapter translates its actual monitor mode, arm, assigned input, playback, recording and punch state into route requests. A user-visible policy selects always-input, record-only, or tape-style behavior. The [scenarios](scenarios.md) give concrete examples; DAWs differ in terminology.

The adapter maintains a map from track IDs to device IDs, source IDs and cue IDs. Missing IDs require user resolution. It groups intents by physical `(device, source, destination)` route. If several tracks request that route with identical settings, one shared route suffices. For conflicting settings the adapter MUST use a persisted user-selected controlling track or report conflict and leave the route unchanged. No last-arriving-track rule or automatic gain summation is permitted. Track removal recomputes the group; it must not disable a route still needed by another track.

## Hardware translation

A bridge must know which actual mixer controls its route touches. Some vendor mixers expose input faders, others input-to-output crosspoints or a selected submix. A bridge must reserve the whole affected resource set, reject overlapping ownership and explicitly advertise restrictions such as shared pan or unavailable independent cue gain. A global output selection must be serialized and verified inside the bridge. Never assume a vendor OSC address matches a UniThru route.

`verified` readback means the backend reports the applied setting. `estimated` means the bridge only knows what it sent. A conforming core bridge must provide verified readback for its advertised routes; a send-only experimental bridge must label itself nonconforming and must not claim reliable restoration or seamless handoff.

## Limits and tradeoffs

Control-plane confirmation is not proof that a performer heard audio. Hardware gain steps, ramps, converter latency and scheduling vary. Disable-first handoff can leave a gap; enable-first can produce duplicate monitoring. Core chooses disable-first when the DAW can confirm its own path state. If confirmation is unavailable, automatic handoff must be refused and the limitation shown. No sample-accurate punch or zero-latency claim is made.

Use local loopback for the first implementation. Remote operation requires deliberate configuration and a protected channel. Ownership tokens address concurrency; they are not authentication.
