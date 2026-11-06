# Ardour development preview

This integration lets a real Ardour audio track drive the UniThru **simulator**. The Lua EditorHook reads Ardour's resolved monitoring state every GUI timer tick and sends it to a local bridge. The bridge uses the core OSC binding to acquire, set, refresh, renew and release a route on its own private fake mixer.

The preview changes no Ardour controls, audio connections, transport state, faders or recording settings. It does not switch software monitoring off. It therefore cannot be used for live hardware/software handoff. The command has no option to target a real interface or an existing server. It always creates a new fake-mixer server on an OS-assigned loopback port.

For a separate graphical window, use [UniThru Monitor](monitor.md) and its Ardour source instead of starting the command-line bridge. It displays the listener port and separates requested ON/OFF from simulator-applied ON/OFF.

## Installed development script

`integrations/ardour/unithru_preview.lua` is the canonical MIT-licensed script. An identical copy has been added to `share/scripts/unithru_preview.lua` in the local Ardour elastic-time development checkout. That checkout's development launcher includes `share/` in its data path, so a script scan can find it without recompiling Ardour. No existing elastic-time files were edited. Other installations can copy the canonical script into their Ardour scripts directory.

The script is opt-in. It has not been enabled in a live session by this change. If the running development app does not list it, rescan scripts or restart Ardour when convenient; do not interrupt an ongoing session to do so.

## Configure one track

In Ardour's Lua scripting console, print the session path and persistent track IDs:

```lua
print(Session:path())
for route in Session:get_routes():iter() do
    if not route:to_track():isnil() then
        print(route:name(), route:to_stateful():id():to_s())
    end
end
```

Use a mono audio track with exactly one connected input source. To list backend output-port names, including capture sources:

```lua
local _, ports = Session:engine():get_backend_ports(
    "", ARDOUR.DataType("audio"), ARDOUR.PortFlags.IsOutput, C.StringVector())
for name in ports[4]:iter() do print(name) end
```

Start the bridge from the UniThru repository root, substituting the exact session path, track ID and connected source-port name. The ID below is an example, not a track discovered on this machine:

```sh
.venv/bin/python -m unithru ardour-preview \
  --session-path '/path/to/ArdourSession' \
  --track-id '1234' \
  --input-port 'system:capture_1'
```

The first JSON line prints a fresh `listener` port. In Ardour's **Script Manager > Action Hooks > New Hook**, select **UniThru Simulator Preview**. Enter that listener port, the same track ID and the same input-port name. A zero or invalid port is rejected. The script always targets `127.0.0.1`; it cannot be configured to send remotely.

Change the track's Input/Disk/Auto monitor mode, arm state and transport in the development session. When Ardour resolves the track to an input-monitoring state, the bridge prints an enabled fake route. When Ardour resolves it to disk or silence, the route is disabled. Arm alone does not override Ardour's monitoring decision. The fake monitor gain is fixed at -12 dB and pan is centered; the track fader is not interpreted as a cue level.

Remove the hook when finished and stop the bridge with Ctrl-C. Normal bridge shutdown verifies ownership, disables its fake route and releases with hold. If the hook disappears, a 1.5 second heartbeat timeout performs the same cleanup and halts the preview. Restart the bridge and recreate the hook to resume; this makes a sender restart or sequence reset explicit.

## Mapping and failure behavior

The bridge pins session path, persistent track ID, expected input connection, UDP sender endpoint and increasing sequence number. Track names are labels only. Missing tracks, MIDI/stereo tracks, a changed input, or several connected sources produce a disabled preview rather than an inferred mapping. Other sessions/tracks, reordered packets and duplicate sequence numbers are ignored. A source reassignment that still uses the same backend name cannot establish hardware identity; this preview does not claim otherwise.

Ardour's `MonitorChoice` and resolved `MonitorState` use different bit values in this checkout. The hook sends resolved `Route::monitoring_state()`; the bridge tests `MonitoringInput` (0x2), including `MonitoringCue` (0x6). Disk is 0x4 and silence is zero. This delegates Auto/tape/punch policy to Ardour rather than reconstructing it from arm alone. Reading the GUI-side state at roughly 100 ms intervals cannot reproduce sample-accurate punch or route process-latency compensation.

On lease loss, a manual fake-mixer edit, a failed protocol operation or unknown applied state, the bridge stops rather than reacquiring automatically. The core request-cache limit also applies; a long-running preview can exhaust its session cache and must be restarted. The preview is intended for short engineering trials, not unattended use. Multiple tracks, cue-send levels, native control-surface UI and real device identity mapping remain future work.

## Internal telemetry

`/unithru/ardour-preview/v1/state` is a private preview message, **not** a core UniThru address or a proposed manufacturer binding. It has OSC tags `,ssssiiiiiii`: session path, track ID, expected input-port name, track label; then sequence, track-present flag, input-matches flag, resolved monitor state, arm flag, rolling flag and actively-recording flag. Text arguments use UTF-8. Flags are 0/1 and sequence is positive. Packets are bounded at 8192 bytes. Core communication between bridge and simulator uses the existing single-JSON-blob OSC binding.

## Validation limits

Automated tests execute the actual script under Lua 5.3 with mocked Ardour objects, validate mapping and monitor-state behavior, and send its messages through a running preview command and real loopback UDP core connection. The APIs and port-name handling were checked against the local development source. These checks do not establish that a live Ardour GUI session has loaded or run the hook. That final check requires enabling it for an explicit session/track mapping as above. No hardware monitoring, audio output or elastic-time behavior has been tested or changed by this integration.
