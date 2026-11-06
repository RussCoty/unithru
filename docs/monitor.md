# UniThru Monitor desktop app

UniThru Monitor is a separate macOS window showing which input-monitoring routes are requested and which settings the simulator has actually applied. It runs independently of the DAW UI. This development version connects only to its own fake mixer; it never opens an audio device or changes Ardour controls.

## Build and open

Requires macOS 14 or later, Xcode command-line tools, and this checkout's Python environment. The resulting development app depends on the checkout and `.venv`; it is not a self-contained distributable package.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python tools/build_monitor.py
open 'build/UniThru Monitor.app'
```

The build creates an app bundle in the ignored `build/` directory and signs it locally with an ad-hoc signature. It does not install system software, change global Python packages or modify other apps. The source is `desktop/UniThruMonitor.swift`; the Python service is `unithru/monitor.py`. Both are MIT-licensed under the repository license.

## What the window shows

- **Requested** is the current monitoring intent for the mapped track. **Unknown** means no valid recent intent is available. **Unmanaged** means this preview has no intent for that route; it is not an instruction to turn it off.
- **Applied** comes from protocol snapshots of the fake mixer. Missing/stale readback becomes Unknown rather than displaying an old ON/OFF as current.
- **Gain / pan** describes the observed monitor route, separate from preamp gain. Revision and owner expose state and lease changes. These numeric fields are last observed values; readback status indicates whether the snapshot remains current.
- **Recent changes** records monitoring requests and connection/ownership failures. Routine lease renewals advance revisions without filling this list.

The simulator label is always visible. ON in this app means a fake route is enabled, not that physical audio has been measured or heard.

The **Device learning** tab is a separate workbench for USB/Thunderbolt topology discovery, optional [live capture](capture.md), labelled trace imports and candidate export. The macOS capture panel attaches only to an explicitly selected PID when its optional runtime is ready. It does not execute learned device commands or provide raw Thunderbolt wire capture. See [learning workflow and limits](learning.md).

## Demo source

The app opens with Demo connected and Disk monitoring selected, so the mapped vocal route starts OFF. Choose Input to see Requested ON and Applied ON. Choose Disk or Silence to turn it off. Input + disk also requests input. Track armed is an independent signal; arming alone does not enable monitoring. The remaining routes are displayed as unmanaged and remain untouched.

Demo buttons change only the app's private fake mixer. Reconnect demo starts a fresh preview controller when a short engineering trial needs restarting after a cache limit or error.

## Ardour source

Select Ardour, enter the exact session path, persistent track ID and connected input-port name, then press Connect. The current source status changes to Waiting for Ardour. Copy the displayed listener port into the **UniThru Simulator Preview** EditorHook in Ardour. The remaining hook setup is described in [the Ardour integration guide](ardour.md).

Once matching telemetry arrives, Requested follows Ardour's resolved track-monitoring state; Applied reports the private simulator response. Until Connect is pressed, selecting the Ardour setup form alone does not replace the currently connected source. Track removal, a changed/multiple input connection, stale sequence numbers and a missing heartbeat use the same safety rules as the command-line preview. A heartbeat failure disables/releases the fake route and stops the controller. Reconnection is explicit.

Only one explicitly mapped mono audio track controls Vocal → Cue A in this first version. Stereo and other cue routes are visible for context but are not mapped automatically. Monitor gain stays at -12 dB with centered pan. Live-session setup and real hardware integration remain separate work.

## Process and network scope

Each window launches its own Python service, private OSC server and telemetry receiver on OS-assigned IPv4 loopback ports. The native UI reads state through a private loopback HTTP endpoint protected by a per-run random bearer token. Actions are queued and processed serially; the UI does not block mixer request handling. There is no browser UI, remote listener, registered network port, filesystem editing API or hardware-server selector.

Closing the app requests termination of only the child service it started. The service stops its preview, releases its owned fake route and closes its own sockets. It does not search for, stop or reconfigure Ardour, Exit Zero or other processes. A second instance uses separate ports and state.

This is still an engineering prototype. The session-cache limit of the preview controller applies to long runs; reconnect explicitly if it stops. There is no audio transport, real hardware readback, sample-accurate punch or automatic software/hardware monitor handoff.

## Validation

Run `.venv/bin/python -m unittest discover -s tests -v` for the protocol, Ardour hook and monitor-service tests. Monitor tests cover separate requested/applied indicators, arm independence, unmanaged routes, real telemetry arrival, heartbeat expiry, stale readback, action validation and HTTP token enforcement. The macOS app also requires a build and direct UI check; Python tests alone do not verify SwiftUI layout. Ordinary CI runs the Python checks on Linux. The manually triggered capture-fixture workflow also builds the native app on macOS; it does not automate the UI check.
