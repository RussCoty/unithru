# OSC/UDP binding, draft 0.1

This binding is specified for implementation but unimplemented here. It uses OSC 1.0 messages with a JSON UTF-8 blob for the logical payload. This trades direct mapping in generic OSC controllers for exact IDs, extensible discovery and consistent validation. Basic MIDI offers a separate limited mapping.

## Framing

One request per UDP datagram, sent to a manually configured loopback address and port. No port, DNS-SD service type or manufacturer namespace has been registered by this project. Replies go to the packet's source IP/port only; no payload-specified reply target. A session is tied to this endpoint. IPv4 and IPv6 loopback may be supported independently.

All request addresses have type tag `,b` (one OSC blob); all response addresses have `,b`. The blob is a single JSON object matching `schema/protocol.schema.json`, encoded in UTF-8 without BOM. JSON has no duplicate keys, NaN or infinity; strings representing IDs are ASCII. OSC address and string padding and blob length follow OSC 1.0. JSON objects are compared structurally for retry identity: member order is immaterial; numbers compare by value and array order is significant. Reject malformed packets before dispatch and do not reflect errors to unadmitted remote endpoints.

| Request address | Required `op` | Response address |
| --- | --- | --- |
| `/unithru/v0.1/hello` | hello | `/unithru/v0.1/result/hello` |
| `/unithru/v0.1/snapshot` | snapshot | `/unithru/v0.1/result/snapshot` |
| `/unithru/v0.1/acquire` | acquire | `/unithru/v0.1/result/acquire` |
| `/unithru/v0.1/renew` | renew | `/unithru/v0.1/result/renew` |
| `/unithru/v0.1/set_route` | set_route | `/unithru/v0.1/result/set_route` |
| `/unithru/v0.1/release` | release | `/unithru/v0.1/result/release` |

Address/op mismatch returns invalid_request if a valid request envelope is available. Wildcards, alternate type tags, multiple arguments and arbitrary vendor addresses are rejected. The server accepts a bare OSC message or a bundle with immediate timetag value 1 and exactly one message. No nested bundle or scheduled bundle is accepted. This binding does not define OSC over TCP.

Maximum packet size is 8192 bytes including OSC framing for the initial loopback profile. Servers advertise `max_message_bytes` no larger than this. A hello MUST fit that limit. A snapshot that will not fit returns snapshot_too_large; the server cannot claim this binding supports its full topology until it can supply a complete bounded snapshot. Pagination/large-device support is an open design item, not an implied mechanism. Never truncate a snapshot silently. Remote profiles will need smaller negotiated sizes or a reliable transport to avoid IP fragmentation.

## Message examples

The notation below shows address, OSC type tag and the blob decoded as JSON; it is not literal OSC bytes. `examples/hello.json`, `examples/discovery.json`, `examples/snapshot-request.json`, `examples/snapshot.json` and `examples/exchange.json` contain complete fixtures.

```text
/unithru/v0.1/hello ,b
{"version":"0.1","request_id":"hello-1","op":"hello","body":{"versions":["0.1"],"client_id":"daw-demo"}}

/unithru/v0.1/acquire ,b
{"version":"0.1","request_id":"acquire-1","session_id":"session-demo","epoch":"epoch-demo","op":"acquire","body":{"route_id":"route-vocal-cue","expected_route_revision":0,"duration_ms":3000,"expiry_policy":"restore"}}

/unithru/v0.1/set_route ,b
{"version":"0.1","request_id":"set-1","session_id":"session-demo","epoch":"epoch-demo","op":"set_route","body":{"route_id":"route-vocal-cue","lease_id":"lease-demo-0123456789abcdef01234567","expected_route_revision":1,"enabled":true,"gain_db":-12,"pan":0,"pan_law":"mono_equal_power"}}
```

Set success returns a result envelope with the same request ID, status ok and body.route containing enabled=true, gain_db=-12, pan=0 and route_revision=2. A duplicate set-1 returns that cached response and does not write again. A new set-2 with expected_route_revision=1 returns stale_revision and the current route. To avoid ambiguity, implementations must inspect the envelope and observed state rather than treat any incoming OSC packet as success.
