# Loopback simulator

The simulator implements the draft core message lifecycle against an in-memory fake mixer. It exchanges real OSC/UDP datagrams over IPv4 loopback. It carries no audio, opens no audio/MIDI devices and has no connection to REAPER, TotalMix or other applications. It is an experimental reference for reviewing behavior, not a certified or complete conformance implementation.

Run from the repository root with Python 3.12 or later:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unithru demo
```

The demo allocates a fresh port, negotiates a session, reads a snapshot, acquires a restore lease, enables a route, renews the lease, restores the baseline and exits. By default it discards the first `set_route` reply **after applying the request**. The client retries the identical request; the cache returns the original result without a second mixer write. The JSON report shows one dropped reply, one retry, the applied and restored routes, and eight backend control writes: four for the set and four for restoration. Gain -11.75 dB rounds down to -12 dB at the fake device's 0.5 dB step.

To exercise negotiation retries instead:

```sh
.venv/bin/python -m unithru demo --drop-first-reply hello
```

## Persistent server

```sh
.venv/bin/python -m unithru server --port 0
```

The first output line reports the OS-assigned port. Use an explicit port if required; no protocol port is registered. The server binds only `127.0.0.1`, does not enable address/port reuse, and fails if that port is occupied. Ctrl-C stops this foreground simulator only. No command searches for or terminates other processes.

To talk to a persistent server from Python, substitute its printed port:

```python
from unithru.transport import Client

with Client(port=YOUR_PRINTED_PORT) as client:
    hello = client.hello()
    snapshot = client.call("snapshot", {"device_id": hello["body"]["device_id"]})
    print(snapshot)
```

Keep the same Client/socket for the whole session. Each `call` creates a new request ID; retries inside a call reuse it. The demo client is synchronous and serializes requests. It validates reply shape, address, ID and epoch. After a mutation times out or returns indeterminate, it refuses further mutation calls; snapshot inspection remains possible. Recovery and reacquisition decisions require caller intervention. This client has no DAW UI and performs no hardware/software monitor handoff.

## Fake topology and behavior

- `route-vocal-cue`: mono input-vocal to stereo cue-a, equal-power pan.
- `route-vocal-cue-b`: the same input to independent stereo cue-b.
- `route-vocal-mono`: the same input to mono-cue, pan fixed at zero.
- `route-stereo-cue`: stereo input-stereo to cue-a, stereo balance.

All routes start disabled at -12 dB. Stable IDs match the fixture vocabulary. Leases last 1000–10000 ms; the server supports restore and hold. Actual fake control values are read back before success. The CLI server requests a 50 ms timer sweep interval, including when no packets arrive; OS scheduling and load can delay a sweep. The fake hardware has immediate state access; there is no physical polling delay. Device and route revisions advance separately. A new epoch invalidates all sessions and leases without replaying baselines.

The server bounds sessions to 32 and request caches to 4096 entries per session. A full cache rejects new IDs; cleanup then occurs by lease expiry unless a previously cached operation already completed it. It does not evict old request IDs to make room. Hello retries are cached by endpoint/request ID for 60 seconds, bounded by the same session limit. Invalid/unidentifiable packets are dropped. A global token bucket allows 200 datagrams per second with a 200-packet burst, dropping excess traffic without replying. These are simulator limits, not a proposed remote security profile.

## Fault injection for tests

These hooks are local Python APIs, not OSC commands. They do not add unversioned protocol operations. Use an isolated `Simulator` with an injected monotonic clock for deterministic tests, or configure one before passing it to `LoopbackServer`.

```python
from unithru.simulator import Simulator

simulator = Simulator()
simulator.backend.fail_after = 1
```

The next fake apply updates the first control (`enabled`) and fails before gain. The result is indeterminate, reports actual readable state, revokes ownership and quarantines that resource. Identical retries reproduce the cached result. Acquisition stays blocked until local `simulator.reconcile(route_id)` verifies the settled state.

`backend.defer_next = True` makes the next apply model a deadline failure with outstanding backend work. `backend.complete_pending()` finishes that work. Reconciliation refuses to release the quarantine while work is pending; a new owner cannot receive a late old write. `simulator.restart()` cancels pending fake work and changes epoch while retaining already applied settings. This models the cancellation boundary; it does not implement an asynchronous hardware driver or measure a real timeout.

Directly changing `backend.routes[route_id]` models a manual mixer edit. The next tick/readback revokes the affected lease and preserves the edit. `backend.unavailable = True` models lost readback. Tests can also map two route resource sets to the same resource to exercise reservation conflicts. These in-process hooks should be used while the server is stopped or under `simulator.lock`.

## What the tests establish

Run `.venv/bin/python -m unittest discover -s tests -v` and `.venv/bin/python tools/validate.py`. Tests exercise the worked fixtures, real packet framing and lost replies, duplicate suppression, endpoint binding, conflicts/CAS, lease expiry, manual edits, scoped restoration, independent cues, pan/gain laws, partial failures, deferred completion, epoch changes and bounded caches. All network tests use fresh ports and close only their own sockets.

Remaining work includes a production backend abstraction, actual asynchronous deadline/cancellation behavior, broader generated fault sequences, DAW policy/handoff integration, durable crash recovery and hardware measurements. Remote access, IPv6, scheduled changes, effects, preamps and MIDI bindings are not implemented. Do not infer hardware timing, acoustic pan accuracy or production readiness from these tests.
