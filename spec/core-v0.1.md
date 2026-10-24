# UniThru core, draft 0.1

This is a proposed implementable contract, dated 2026-10-24. MUST, MUST NOT, SHOULD and MAY express requirements of this draft. No conformance implementation has been certified. JSON examples and schemas describe the logical messages; the OSC binding supplies packet encoding.

## 1. Roles, identity and version

A client is normally a DAW adapter. A server owns a hardware-mixer adapter. The server is the authority for applied state and leases. All IDs are case-sensitive ASCII tokens matching `[A-Za-z0-9._:-]{1,128}`; UUIDs are recommended. Example IDs describe a fictional device.

`device_id` MUST persist across power cycles and distinguish two identical units. `source_id` identifies an ordered mono or stereo input group; `destination_id` identifies an ordered mono or stereo cue bus. IDs MUST NOT be reassigned to different physical channels. If the bridge cannot establish identity, it MUST require a new user mapping. Display names and channel numbers are metadata only. Stereo channel order is left then right. A new channel grouping gets a new ID.

`epoch` is a fresh unpredictable server-instance ID on restart, hardware reconnect, or topology change. Every request after discovery MUST carry the advertised epoch. Old epochs return `epoch_mismatch` with no write. Reconnect never automatically reacquires leases. A server MUST discard leases when changing epoch. The backend must first reconcile any outstanding writes; it may not accept a new owner while an older asynchronous write can still arrive.

Every message has `version: "0.1"`. A `hello` request offers a nonempty list of versions. The server selects one exact common version, or returns `unsupported_version` without a session. No implicit fallback. All subsequent requests carry the selected version and a server-issued `session_id`. A session is bound to the requesting endpoint and expires after 60 seconds without a valid request; lease expiry is independent. An endpoint change requires a new hello. Unknown operations or fields are rejected in this version. Future revisions negotiate a new exact version.

## 2. Discovery and refresh

The logical hello request is `{version, request_id, op:"hello", body:{versions:["0.1"], client_id}}`. Its result includes `session_id`, `epoch`, `device_id`, `capabilities` and `revision`. The client_id identifies a client installation, not a lease. Read-only `snapshot` uses `{device_id}` as its body and returns the entire core route list, epoch and revision. The server MUST serialize snapshot capture against its own mutations. Changes arriving during backend readback require repeating the read or explicitly returning `indeterminate`.

Capabilities include protocol versions; source and destination objects with ordered physical channel IDs; supported topology pairs and pan laws; gain min/max/step in dB; max routes and max leases; lease min/max duration; maximum message bytes; readback quality; atomic batch support; timing support; and extension names/versions. Limits reflect currently usable mixer controls. Capability changes force a new epoch. Resource availability can still change between discovery and acquire.

`revision` is a monotonically increasing integer from 0 through 2147483647 within an epoch, incremented for every observed core state change, ownership change and lease renewal. Before exhaustion the server changes epoch. Periodic backend readback MUST detect manual edits; advertise `poll_interval_ms`, bounded at 1000 ms for core. Only state at a completed readback is called verified; include server-monotonic `observed_ms`. No claim is made about changes between polls.

`observed_ms` in a snapshot is the nonnegative integer millisecond reading of the server's monotonic clock at completion of that snapshot's readback. Its origin is server-defined within an epoch; clients must not compare it with their wall clock. Per-operation route results are verified at completion; core does not include a per-operation hardware timestamp. The schemas validate message shape; topology support, lease ownership, revisions, limits and state transitions also require runtime validation.

Clients MUST refresh at least once per second while owning a route, and after timeout, conflict, reconnect or an indeterminate response. Core has no push subscription. Polling avoids an undefined reliable-event mechanism; later extensions may add one.

## 3. Route model and signal semantics

A route has `{route_id, source_id, destination_id, enabled, gain_db, pan, pan_law, owner_session, lease_id, route_revision}`. Unowned routes have null ownership fields. A route represents a single existing input-to-cue path or a bridge-created virtual grouping of physical controls. It is identified in discovery/snapshot before acquisition; acquire does not create audio routing implicitly. A server MUST expose which routes are available, including disabled ones, within advertised limits.

`enabled=false` removes this route's contribution. It MUST NOT mute the source, destination bus, other sends or recording. `gain_db` is monitor-route gain before pan, referenced to unity at 0 dB; it is distinct from analog preamp gain. All numeric fields must be finite. The advertised dB range and step apply. A request inside the range is rounded to the nearest step measured from the minimum, ties toward lower gain; out-of-range requests are rejected. Mute uses enabled=false, never a sentinel dB or negative infinity. Readback returns the quantized applied value.

`pan` lies in [-1,1]. Core requires mono source to stereo destination with `mono_equal_power`: angle = `(pan+1)*pi/4`; left coefficient = `cos(angle)` and right coefficient = `sin(angle)`, multiplied by `10^(gain_db/20)`. Center therefore gives approximately -3.0103 dB per leg. If hardware uses a different law it MUST advertise a named law with its precise coefficients in its adapter documentation; it must not claim mono_equal_power.

Mono-to-mono uses `none` and pan=0. Stereo-to-stereo uses `stereo_balance`: negative pan leaves left unchanged and scales right by `1+pan`; positive pan leaves right unchanged and scales left by `1-pan`, before common route gain. Center preserves both channels at unity. Stereo width and stereo-to-mono downmix are out of core; reject unsupported topology. Hardware that cannot match these laws must advertise its alternative and require explicit client acceptance. No inferred channel duplication or downmix.

Each source may feed several destinations through distinct routes with independent leases and settings, if supported. The bridge MUST expose resource overlap: two virtual routes sharing a physical gain/pan control cannot be independently owned or advertised as independent controls.

## 4. Ownership lifecycle

`acquire` body: `{route_id, expected_route_revision, duration_ms, expiry_policy}`. `expiry_policy` is `restore` or `hold`. Duration must be within advertised bounds. Acquire requires an unowned route and an exact route revision, reserves every touched backend resource, stores a verified baseline, and returns a random `lease_id`, granted duration and complete route. Lease time starts when the server grants it, using its monotonic clock. The timer is not a synchronized client clock.

The default UI choice SHOULD be restore, but clients MUST send it explicitly. `hold` leaves the latest setting on expiry and releases ownership. A server must support both policies or reject the unsupported policy before acquisition. A lease ID is at least 128 random bits represented as an ASCII token; possession alone does not authorize another session.

`renew` body: `{lease_id, duration_ms}`. Only its owning session can renew an unexpired lease. Renewal starts a new interval on acceptance and does not change route controls. On expiry, explicit release or session expiry, apply the chosen cleanup policy and relinquish ownership. Expiration is processed before incoming requests at the same server time.

`set_route` body: `{route_id, lease_id, expected_route_revision, enabled, gain_db, pan, pan_law}`. These are absolute settings, never toggles or deltas. All fields are mandatory. Only the current lease/session may write. Every write uses compare-and-set on route_revision; a mismatch rejects the request without writing. A successful control write increments route_revision once and the device revision once, even if values quantize to the current settings. Ownership changes and renewal also advance route_revision. Unrelated routes do not invalidate a route CAS.

Manual edits take precedence. When the backend reports an external edit to any leased resource, the server revokes that lease, cancels its queued writes and updates state. It MUST NOT undo the edit or reacquire on the client's behalf. A bridge that cannot distinguish an external edit from its own write must serialize writes/readback and conservatively revoke on ambiguity. This does not eliminate the race between external hardware edits and bridge writes: advertise that limitation and avoid claiming atomic protection against another mixer application.

`release` body: `{lease_id, cleanup:"restore"|"hold"}`. Explicit cleanup overrides expiry_policy. Restore is allowed only while ownership is intact and every touched resource still matches the server's last verified write, with no known external edit. It restores only the recorded baseline controls, never a mixer-wide preset. Restore first re-reads those controls. If changed, retain the observed state and return `conflict` with ownership released. If the adapter cannot safely compare and restore, report `indeterminate`, release ownership, and require inspection. This check is best effort without a backend compare-and-set primitive; physical changes between check and write remain a documented limitation.

After client disconnect there is no immediate reliable UDP notification: lease expiry is the cleanup trigger. Client restart uses a new session. A timed-out acquire may still own a lease: retrieve snapshot and correlate the cached request response before attempting recovery. A server restart cannot promise restoration without a durable ownership journal; core requires fresh readback and leaves current mixer state untouched rather than replaying a baseline from a previous epoch.

## 5. Completion, errors and partial failure

Requests have unique `request_id` per session, plus version, session_id, epoch, op and body. IDs must not be reused. Server responses contain `version`, echoed `request_id`, `op`, `epoch`, `revision`, `status` and `body`. Successful hello supplies session_id inside body. `status` is `ok`, `error` or `indeterminate`. Errors include `body.code` and a human-readable `body.message`; state-changing operations also return any verified affected route in `body.route`. An error is not an acknowledgment of the desired setting.

Required codes: `invalid_request`, `unsupported_version`, `unknown_session`, `epoch_mismatch`, `not_found`, `unsupported`, `out_of_range`, `busy`, `stale_revision`, `lease_expired`, `not_owner`, `conflict`, `resource_exhausted`, `backend_failure`, `request_id_reuse`, `message_too_large`, `snapshot_too_large`. Rejection before a backend write returns error. A failure after any possible write returns indeterminate, a list of affected resource IDs and observed route if obtainable. Clients MUST stop automatic writes to affected routes until refresh and explicit reconciliation. The server quarantines uncertain resources, revokes affected leases and completes or cancels old backend work before making those resources acquirable again.

Core has one route per request, with no cross-route atomicity. A bridge validates all fields before writing but hardware may require several control writes. `ok` means all fields have been applied and verified through backend readback. Partial completion never returns ok, even if an attempted rollback appears to succeed. Return observed state, including `enabled` as actually found. Never reset unrelated controls. No intermediate accepted acknowledgment is defined; receipt alone is insufficient.

For retries, the server caches the complete request and final response by `(session_id, request_id)` until session expiry, for at least 60 seconds after that request's completion. Session cache capacity is advertised as `max_requests_per_session`; once full, reject new IDs with resource_exhausted and require cleanup/new session. Never evict cached IDs within the retention window. Identical duplicates return the same result without another hardware write or lease extension; differing contents return request_id_reuse. In-progress duplicates join the pending operation. Cache lookup happens before lease/CAS validation so a lost success acknowledgment is recoverable. Epoch/session validation precedes cache lookup; an old-epoch cached success is never replayed into a new epoch.

Clients retry identical packets after 100, 200, 400 and 800 ms, then treat the result as unknown and request a snapshot. A late result must not replace a newer observed route revision in the UI. Use a new request_id for each intentional subsequent change and serialize route mutations. After a timeout, do not send a newer mutation until the previous operation has completed or been reconciled. Server backend operations have a configured deadline (advertised `operation_timeout_ms`); crossing it returns indeterminate and quarantines the resource as above. Hardware completion timing is not bounded by UDP delivery.

## 6. Timing and handoff

Core requests execute as soon as practical. `timing: "immediate"` is mandatory. OSC bundle timetags do not create sample-accurate hardware scheduling. The core binding accepts only immediate timetags. A future scheduled extension must negotiate clock identity, clock uncertainty, sample rate, device timeline, lead time, cancellation, late policy and applied timestamp/error reporting. No such binding is specified here.

To switch software -> hardware monitoring, disable the DAW's software input path, confirm that state through the DAW API, then enable the hardware route and wait for verified completion. Reverse: disable hardware and confirm before enabling software input. Playback return audio must remain independently routed. A gap is possible. A timeout leaves the path unknown; never automatically enable an alternative path based only on a missing acknowledgment. If DAW control or verified backend state is unavailable, require manual handoff. Cross-device or DAW/hardware atomicity is not provided.

## 7. Safety and access

Default listeners MUST bind only to loopback and use explicitly configured ports. Remote exposure is opt-in and requires authenticated access and integrity protection through a protected gateway/tunnel, scoped route permissions and endpoint admission. The raw OSC/UDP profile provides none of these protections and MUST NOT be exposed directly to untrusted networks. Core IDs, session IDs and leases do not provide remote authentication.

Servers bound message sizes, parsing time, sessions and outstanding operations, rate-limit malformed traffic, reject nonfinite values and never accept wildcard write addresses. A failed route request must not alter phantom power, preamp gain, output master level or capture routing.
