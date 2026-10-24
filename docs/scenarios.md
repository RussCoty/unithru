# Worked scenarios

All names and values are fixtures, not tested vendor behavior. Each scenario assumes a discovered route, verified backend readback and a DAW adapter that can confirm its software-monitor state.

## Vocal overdub

The singer uses input `input-vocal` and stereo cue `cue-a`. The adapter verifies the persisted IDs and groups track intent. Track arm allows recording; the chosen always-input mode requests monitoring even while stopped. The adapter reads route revision 0, acquires a 3000 ms restore lease and receives route revision 1. It disables software input monitoring and confirms the DAW state, then sets enabled=true, gain_db=-12, pan=0 with expected revision 1. Verified success returns revision 2. Playback returns and unrelated cue sends stay as they were.

The adapter renews every 1000 ms, using fresh request IDs, and refreshes state every 1000 ms. A renew advances route revision, so it must use the newly returned route for its next CAS. At the end it disables the route and waits for confirmation before enabling software input if requested. It releases with restore, which restores the captured baseline only if ownership and last-write comparison still hold. A baseline that was enabled may become audible again; the adapter must show this and account for it before enabling a duplicate software path. Using cleanup=hold after an explicit disable is appropriate when the user wants the route left silent.

## Tape-style punch

For a chosen tape-style policy, an armed track hears input while stopped, playback while rolling outside the punch region, and input inside it. Before rolling, disable the hardware route and confirm, then allow playback return. At punch-in, request the input route; at punch-out, disable it. Playback return management is a distinct DAW operation; it must not mute another track's playback or cue mix.

These are control changes with host and hardware latency. UDP arrival does not coincide with the audio sample boundary. If a device has no negotiated scheduler, a short gap or late switch is possible. The UI must describe this as best-effort punch monitoring; it must not promise HDX-equivalent timing. A transport stop may arrive before an outstanding punch-in write; serialize and reconcile that write before applying the stopped-state policy.

## Two tracks share an input

Tracks Vocal and Vocal Safety both use input-vocal/cue-a. If their monitoring intents and levels agree, one route represents both. Disarming Vocal does not disable the route if Safety still requests it. If one asks for -12 dB centered and the other -6 dB hard left, the adapter reports conflict until the user selects the controlling track. It must not sum the gains or let UI event order choose the winner.

The same physical input can also feed cue-b at -18 dB through another route if the backend provides independent crosspoint gain. A server that exposes a shared input fader must refuse a second incompatible route reservation rather than pretending the levels are independent.

## Manual mixer edit and crash

An engineer moves the leased crosspoint in the vendor mixer. On readback the server detects an external edit, revokes the lease and records the changed state. The adapter refreshes, stops writes and shows ownership loss. Automatic restore would undo the engineer's decision and is forbidden.

If the DAW crashes without a manual edit, renewal stops. On expiry, restore compares the route controls to the last verified state and restores the saved baseline only when the comparison permits it. A dropped release reply is handled by retrying the identical request; it must not restore twice. Backend uncertainty quarantines the route until readback and old-work cancellation finish.

## Device unplug and replug

Unplug invalidates the epoch. On reconnect, the server reads current state and issues a new epoch. It does not replay stale writes. The DAW verifies device and channel IDs, obtains a new session and explicitly reacquires. If a replacement unit has a different ID, user mapping is required even when its channel count and name match.

## Cue reverb with dry recording

A future effects extension discovers a reverb whose insertion point is monitor_only, verifies its resource requirements and latency, then reserves it independently of core routes. The DAW shows dry capture and a wet cue return. If discovery says the effect is upstream of capture, the adapter cannot offer the same dry-recording workflow; recorded processing requires explicit user action. This is a future scenario, not functionality provided by this repository.
