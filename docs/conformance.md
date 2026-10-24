# Conformance and test plan

The draft has no certified implementation. Passing the included validator proves fixture/schema consistency only. A future claim should name the exact draft version, binding, adapter, hardware/firmware, driver, OS, DAW and test harness revision, with raw observations.

## Required harness cases

| Area | Stimulus | Required observation |
| --- | --- | --- |
| Identity | Rename, reorder, unplug and replace identical unit | Same physical IDs persist; replacement never silently maps |
| Negotiation | No shared version; unknown op/field | Explicit rejection; no mixer write |
| Routing | Enable one input-to-cue path | Other sends, bus master and capture path unchanged |
| Units | Min/max/step, ties, out of range, nonfinite values | Quantized gain returned; invalid input rejected |
| Topology | Mono/stereo vectors at pan extremes and center | Advertised coefficients measured; no hidden downmix |
| Shared inputs | Two clients and overlapping resources | Single writer, busy/conflict; no last-writer behavior |
| CAS | Stale revision after edit or renewal | No backend write; current observed route returned |
| Duplicates | Drop success reply, resend; alter reused ID | One hardware write; cached response; reuse rejected |
| Reordering | Delay older write/reply after newer observation | No stale UI overwrite; route mutations serialized |
| Resource limit | Exhaust routes, leases and request cache | Bounded explicit failure; no cache eviction violation |
| Manual edit | Modify leased control outside service | Lease revoked on readback; no automatic restore |
| Cleanup | Expiry/release with matching and changed controls | Conditional scoped restore; unrelated controls intact |
| Failure | Fail second backend write; unplug during set | Indeterminate with affected resources; quarantined until reconciled |
| Restart | Restart server with active lease | New epoch; stale packets rejected; no stale baseline replay |
| Handoff | Lose ack on enable or disable | No blind alternative-path enable; uncertainty visible |
| Parsing | Bad OSC padding/length/tag, nested bundle, oversized JSON | Bounded rejection; no write or unsafe reflection |
| Access | Loopback default, remote without admission | Listener restricted; remote writes rejected |

Use a fake backend first, with injected delay, lost replies, duplicate packets, resource overlap and readback disagreement. A packet-level harness must test the real OSC encoder/parser, not just Python objects. Exercise timeout/cancellation so late vendor writes cannot hit a newly owned route. Add model/property tests for at-most-once writes and restoration predicates.

Hardware validation needs captured mixer state before and after each case, plus audio loopback measurements for pan/gain, cue independence and capture isolation. Test at supported sample rates, buffer sizes and DSP configurations. Measure control latency distribution and handoff gaps/overlap using an actual signal; do not infer sample timing from message timestamps. A second independent adapter must pass the same cases before stabilizing 1.0.

Optional effects need separate tests for discovery, resource exhaustion, latency changes, bypass/tails, shared ownership and dry/printed capture isolation. Basic MIDI is tested only against its published limited mapping and cannot claim full core conformance.
