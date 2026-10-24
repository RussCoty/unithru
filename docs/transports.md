# Transport and hardware integration paths

Only the OSC/UDP draft has a complete core message binding. The following paths describe feasibility and remaining work. None has an implementation in this repository.

## Basic MIDI 1.0 control proposal

For a manually configured, single-controller route: select a MIDI port, channel and route at both ends. Proposed CC 20 enables the route (0 off, 127 on; other values rejected); CC 21 maps 0..127 linearly to the configured finite monitor gain range in dB; CC 22 maps 0..127 to pan, with 64 exactly center, 0 hard left and 127 hard right. Values below 64 use `(value-64)/64`; values above use `(value-64)/63`. Gain is quantized to the device step after mapping. CC numbers are local choices that must be checked for conflicts, not reserved UniThru assignments. A companion bridge may provide an explicit exclusive manual-control mode; it must not silently bypass a live core lease.

This proposal is limited control, not core conformance. Ordinary CC does not encode stable route IDs, discovery, leases, epochs, CAS, correlated results or verified state. MIDI feedback may help a controller display values but cannot supply those guarantees without another defined protocol. MIDI learn must not map preamp gain to route gain by accident. Feedback-loop prevention and port/channel setup remain application concerns.

SysEx could carry fuller envelopes, but needs byte packing, framing, size limits, transaction IDs, device identity and a permitted manufacturer/universal allocation. This project has no manufacturer ID and specifies no public SysEx format. A private experiment must follow applicable MIDI allocation rules rather than invent an official identifier. MIDI 2.0 UMP/Property Exchange may improve discovery and precision, but neither alone defines UniThru monitoring semantics. A complete negotiated MIDI binding needs separate design and tests.

## MIDI over Ethernet

[RTP-MIDI (RFC 6295)](https://www.rfc-editor.org/info/rfc6295/) defines an RTP payload for MIDI 1.0 commands. Actual ecosystem session establishment must also be considered; transport availability alone is not application negotiation.

[Network MIDI 2.0](https://midi.org/network-midi-2-0-udp-overview) is a separate UDP/UMP standard, initially ratified in November 2024. It can carry MIDI 1.0 and MIDI 2.0 protocol data. It is not interchangeable with RTP-MIDI. Either could transport a future MIDI mapping; neither supplies UniThru route ownership or DAW policy automatically. A network adds access control and loss/reconnect questions even for basic CC.

## USB

A class-compliant USB MIDI interface can transport a future MIDI mapping if the host OS supports the relevant class/version. [USB MIDI 2.0](https://www.usb.org/document-library/usb-class-definition-midi-devices-v20) defines a device class; that does not mean every host supports every device configuration. USB Audio Class support does not imply access to an interface's internal mixer or support for UniThru.

A vendor bulk/control endpoint could carry an envelope, with a user-space bridge or driver. Such a binding must specify descriptors, transfer framing, request ordering, cancellation, unplug semantics and permissions. Vendor endpoints may require OS-specific drivers or driver setup. No VID/PID or endpoint allocation is supplied here. An existing vendor control API can instead sit behind the loopback OSC service without changing the audio driver.

## Thunderbolt

Thunderbolt provides connectivity and transport infrastructure, including PCIe tunneling; see [Intel's technology brief](https://www.intel.com/content/dam/doc/technology-brief/thunderbolt-technology-brief.pdf). An audio device still needs a defined control channel: a documented driver API, vendor service, MIDI endpoint where exposed, or another explicit transport. A host bridge can translate UniThru OSC into that channel. Thunderbolt alone defines neither UniThru messages nor a driverless mixer API, and link bandwidth says nothing about mixer control timing. Driver/firmware access, host permissions and verified readback must be established for each target.

## AES70 investigation

[AES70](https://ocaalliance.github.io/aes70.html) already defines networked device control and monitoring. Before adding another full network binding, map UniThru route/gain/pan and ownership requirements against AES70 object and connection models. A future profile or gateway may reuse that work. No AES70 interoperability or conformance is claimed by this draft; a mapping remains a roadmap item.
