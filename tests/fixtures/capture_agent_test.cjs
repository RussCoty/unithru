const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Pointer {
    constructor(value = 0, bytes = null) { this.value = value; this.bytes = bytes; }
    toInt32() { return this.value | 0; }
    toUInt32() { return this.value >>> 0; }
    toString() { return "0x" + this.value.toString(16); }
    isNull() { return this.value === 0 && this.bytes === null; }
    readByteArray(length) { assert(length <= this.bytes.length); return Uint8Array.from(this.bytes.subarray(0, length)).buffer; }
    readU32() { return this.bytes.readUInt32LE(0); }
    readU64() { return this.bytes.readBigUInt64LE(0); }
}

const integer = value => new Pointer(value);
const bytes = (...values) => new Pointer(0x1000, Buffer.from(values));
function count(value, wide = false) {
    const buffer = Buffer.alloc(wide ? 8 : 4);
    if (wide) buffer.writeBigUInt64LE(BigInt(value)); else buffer.writeUInt32LE(value);
    return new Pointer(0x2000, buffer);
}

function agent(provider, filters = {}) {
    const callbacks = new Map();
    const messages = [];
    const context = {
        Module: { findGlobalExportByName: name => ({ name }) },
        Interceptor: { attach: (address, callback) => { callbacks.set(address.name, callback); return { detach() {} }; } },
        Process: { pointerSize: 8, arch: "arm64", id: 77 },
        rpc: { exports: {} },
        send: value => messages.push(JSON.parse(JSON.stringify(value))),
        Date, Uint8Array, Map, Array, Number, String, Object, Math,
    };
    vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), context);
    const info = context.rpc.exports.start({ provider, connection: null, selector: null, handle: null, ...filters });
    assert.equal(info.hooks.length, 3);
    function invoke(name, arguments, result, original = () => {}) {
        const call = { threadId: 1 };
        callbacks.get(name).onEnter.call(call, arguments);
        original();
        callbacks.get(name).onLeave.call(call, integer(result));
    }
    return { messages, invoke, exports: context.rpc.exports };
}

const input = bytes(0xa0, 1, 1, 0);
const methodArgs = () => [integer(17), integer(3), bytes(1, 0, 0, 0, 0, 0, 0, 0), integer(1), input, integer(4),
    bytes(1, 0, 0, 0, 0, 0, 0, 0), count(1), bytes(0xa0, 1, 1, 0), count(4, true)];
const io = agent("iokit");
io.exports.setlabel({ phase: "action", trial_id: "one", control: "route.enabled", target: "route", value: true });
io.invoke("IOConnectCallMethod", methodArgs(), 0, () => {
    input.bytes[2] = 0;
    io.exports.setlabel({ phase: "idle", trial_id: "later" });
});
assert.equal(io.messages.length, 1);
assert.equal(io.messages[0].packets.length, 4);
assert.equal(io.messages[0].packets[1].payload_hex, "a0010100");
assert.equal(io.messages[0].label.trial_id, "one");
assert.equal(io.messages[0].packets[3].direction, "device_to_host");
io.invoke("IOConnectCallScalarMethod", [integer(17), integer(3), bytes(1, 0, 0, 0, 0, 0, 0, 0), integer(1),
    bytes(1, 0, 0, 0, 0, 0, 0, 0), count(1)], 0, () => io.invoke("IOConnectCallMethod", methodArgs(), 0));
assert.equal(io.messages.length, 2);
assert.equal(io.messages[1].function, "IOConnectCallScalarMethod");
io.invoke("IOConnectCallMethod", methodArgs(), 5);
assert.equal(io.messages[2].status, 5);
assert.equal(io.messages[2].packets.length, 2);
const oversized = methodArgs(); oversized[5] = integer(8193);
io.invoke("IOConnectCallMethod", oversized, 0);
assert.equal(io.messages[3].problem, "unreadable_or_oversized_buffer");
const filtered = agent("iokit", { connection: 18 });
filtered.invoke("IOConnectCallMethod", methodArgs(), 0);
assert.equal(filtered.messages.length, 0);

const usb = agent("libusb");
const usbInput = bytes(0xa0, 1, 1, 0);
usb.invoke("libusb_control_transfer", [integer(17), integer(0x40), integer(1), integer(1), integer(0), usbInput, integer(4), integer(100)], 4,
    () => { usbInput.bytes[2] = 0; });
assert.equal(usb.messages[0].packets[0].payload_hex, "4001010000000400");
assert.equal(usb.messages[0].packets[1].payload_hex, "a0010100");
usb.invoke("libusb_bulk_transfer", [integer(17), integer(0x82), bytes(1, 2, 3, 4), integer(4), count(2), integer(100)], 0);
assert.equal(usb.messages[1].packets[0].payload_hex, "0102");
assert.equal(usb.messages[1].packets[0].direction, "device_to_host");
assert.equal(usb.exports.stop().in_flight_threads, 0);
assert.equal(io.exports.stop().in_flight_threads, 0);
console.log("Agent observation, deduplication, label, filter, bounds and USB setup checks passed");
