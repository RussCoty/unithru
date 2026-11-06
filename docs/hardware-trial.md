# First hardware capture trial

The first milestone is a repeatable observation of one monitor-route control in a real manufacturer's mixer. The capture engine stays vendor-independent; this trial establishes evidence for one named device and control path. It does not enable learned writes.

## Prepare the session

Use an interface and monitoring path that are free for an engineering session. Leave active shows, recording sessions and their interfaces undisturbed. Instrumentation adds overhead even though it does not send device commands. The experiment changes a real control in the manufacturer's app, so choose a spare cue/route, note its starting state, and restore it manually afterwards.

Record the interface model, firmware, driver/mixer versions, OS, transport, sample rate, channel grouping and exact input/cue target. Use explicit unknown values where a version cannot be established. Do not publish serial numbers, raw capture payloads, account details or unrelated device inventory by default.

```sh
.venv/bin/python -m unithru learn inventory
.venv/bin/python -m unithru learn capabilities
```

Inventory shows topology only. Confirm the interface is present and the manufacturer mixer is available. In Activity Monitor, identify the mixer/helper process that actually issues the driver calls. Do not attach to the DAW or a shared audio service merely because the mixer GUI shows no traffic. Capture requires target attach permission; do not change signing or OS protections to obtain it.

## Capture one control

Open UniThru Monitor's **Device learning** tab. Choose IOKit for synchronous macOS driver calls, labelled USB or Thunderbolt as appropriate, or libusb for an app known to use its synchronous USB APIs. Enter the explicit mixer/helper PID and device labels. Start a new capture under `captures/` and wait for **ready**.

Use a mute/route-enable control on the spare monitor path. Record the exact name shown in the manufacturer app: a mute checkbox and a route-enabled label may have opposite boolean meanings. Keep the label convention consistent; it is an experimental label, not proof of routing semantics.

For each row, set the label and wait for **Apply window** acknowledgement before moving the manufacturer control. Change one control at a time.

| Window | Trial ID | Action |
| --- | --- | --- |
| Idle | idle-1 | Leave controls untouched for about five seconds |
| Idle | idle-2 | Record another idle interval |
| Action | trial-1 | Set `route.enabled`, target `input-1/cue-1`, value `false`; move the corresponding control |
| Action | trial-1 | Set the same label to `true`; move the control |
| Idle | idle-3 | Leave controls untouched |
| Action | trial-2 | Repeat the change to `false` |
| Action | trial-2 | Repeat the change to `true` |
| Idle | idle-4 | Leave controls untouched |

Replace the target with the actual input/cue names and use distinct trial IDs for separate repetitions. A duplicate packet or an unchanged value is not an independent experiment. If the hardware cannot start in the state needed for a transition, establish that state in an idle/preparation window first.

Stop and analyse. Restore the manufacturer's control to its starting state. Keep the normalized trace, raw journal and acquisition manifest together. The app's requested/applied route indicators still describe its simulator, not the captured interface.

## Decide what the result establishes

- **No observed calls:** the selected process may use another helper, asynchronous APIs, shared memory or another unsupported path. Record that limitation; do not call it successful learning.
- **Calls but no usable observations:** inspect failed, unreadable and truncated counts before changing the experiment.
- **Candidate bytes:** inspect independent repetitions and idle filtering. A correlation is provisional and all exported candidates remain unvalidated with writes disabled.
- **Evidence loss or drop limits:** shorten/filter a new experiment and preserve the original result. Do not treat an incomplete capture as complete evidence.

After the first repeatable candidate, collect a separate negative-control session: repeat the main control and move an unrelated control in its own labelled windows. Then test another channel and cue in separate sessions. This helps distinguish addressing from shared counters or whole-mixer updates.

Do not replay captured payloads. An adapter needs verified identity, encoding, hardware readback, route isolation, ownership, manual-edit handling and disconnect/recovery behavior. The [capture guide](capture.md) describes provider limits; the [learning guide](learning.md) describes candidate analysis.
