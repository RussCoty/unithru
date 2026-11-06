"use strict";

let label = { phase: "idle", trial_id: "baseline-1" };
let settings = null;
let listeners = [];
let active = new Map();
let hooks = [];
let sent = 0;
let dropped = 0;
let limitNotified = false;
let tokens = 200;
let tokenTime = Date.now();
const limit = 8192;

function hex(pointer, length) {
    if (length < 0 || length > limit) return null;
    if (length === 0) return "";
    if (pointer.isNull()) return null;
    const bytes = new Uint8Array(pointer.readByteArray(length));
    return Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
}

function sizeAt(pointer) {
    if (pointer.isNull()) return 0;
    return Process.pointerSize === 8 ? Number(pointer.readU64().toString()) : pointer.readU32();
}

function countAt(pointer) {
    return pointer.isNull() ? 0 : pointer.readU32();
}

function publish(call, packets, status, problem = null) {
    const now = Date.now();
    tokens = Math.min(200, tokens + (now - tokenTime) * 0.2);
    tokenTime = now;
    if (sent >= 20000) {
        dropped++;
        if (!limitNotified) { limitNotified = true; send({ kind: "limit", reason: "agent_call_limit" }); }
        return;
    }
    if (tokens < 1) { dropped++; return; }
    tokens--;
    sent++;
    send({ kind: "call", function: call.function, endpoint: call.endpoint,
           selector: call.selector, label: call.label, timestamp_ns: call.timestamp,
           status, problem, packets });
}

function enter(call, args, name) {
    const depth = active.get(call.threadId) || 0;
    active.set(call.threadId, depth + 1);
    call.outer = depth === 0;
    call.function = name;
    call.label = Object.assign({}, label);
    call.timestamp = String(Date.now()) + "000000";
    call.packets = [];
    call.problem = null;
}

function leave(call) {
    const depth = (active.get(call.threadId) || 1) - 1;
    if (depth === 0) active.delete(call.threadId); else active.set(call.threadId, depth);
}

function packet(call, direction, component, data, transferType) {
    if (data === null) { call.problem = "unreadable_or_oversized_buffer"; return; }
    if (data.length > 0) call.packets.push({ direction, component, payload_hex: data, transfer_type: transferType });
}

function attachIOKit(name) {
    const address = Module.findGlobalExportByName(name);
    if (address === null) return;
    listeners.push(Interceptor.attach(address, {
        onEnter(args) {
            enter(this, args, name);
            if (!this.outer) return;
            try {
                this.endpoint = "iokit:connection:" + args[0].toUInt32();
                this.selector = "method:" + args[1].toUInt32();
                this.selected = (settings.connection === null || args[0].toUInt32() === settings.connection) &&
                    (settings.selector === null || args[1].toUInt32() === settings.selector);
                if (!this.selected) return;
                if (name === "IOConnectCallMethod") {
                    packet(this, "host_to_device", "scalar-native-u64", hex(args[2], args[3].toUInt32() * 8), "driver_call");
                    packet(this, "host_to_device", "struct", hex(args[4], Number(args[5].toString())), "driver_call");
                    this.scalarOut = args[6]; this.scalarCount = args[7];
                    this.structOut = args[8]; this.structSize = args[9];
                } else if (name === "IOConnectCallScalarMethod") {
                    packet(this, "host_to_device", "scalar-native-u64", hex(args[2], args[3].toUInt32() * 8), "driver_call");
                    this.scalarOut = args[4]; this.scalarCount = args[5];
                } else {
                    packet(this, "host_to_device", "struct", hex(args[2], Number(args[3].toString())), "driver_call");
                    this.structOut = args[4]; this.structSize = args[5];
                }
                this.scalarCapacity = this.scalarCount === undefined ? 0 : countAt(this.scalarCount);
                this.structCapacity = this.structSize === undefined ? 0 : sizeAt(this.structSize);
            } catch (error) { this.problem = String(error); }
        },
        onLeave(result) {
            try {
                if (!this.outer || !this.selected) return;
                const status = result.toInt32();
                if (status === 0) {
                    if (this.scalarOut !== undefined) {
                        const count = countAt(this.scalarCount);
                        if (count <= this.scalarCapacity) packet(this, "device_to_host", "scalar-native-u64", hex(this.scalarOut, count * 8), "driver_call");
                        else this.problem = "returned_scalar_count_exceeds_capacity";
                    }
                    if (this.structOut !== undefined) {
                        const length = sizeAt(this.structSize);
                        if (length <= this.structCapacity) packet(this, "device_to_host", "struct", hex(this.structOut, length), "driver_call");
                        else this.problem = "returned_struct_size_exceeds_capacity";
                    }
                }
                publish(this, this.packets, status, this.problem);
            } catch (error) { publish(this, [], result.toInt32(), String(error)); }
            finally { leave(this); }
        }
    }));
    hooks.push(name);
}

function attachUSB(name) {
    const address = Module.findGlobalExportByName(name);
    if (address === null) return;
    listeners.push(Interceptor.attach(address, {
        onEnter(args) {
            enter(this, args, name);
            if (!this.outer) return;
            try {
                this.endpoint = "libusb:handle:" + args[0].toString();
                this.selected = settings.handle === null || args[0].toString() === settings.handle;
                if (!this.selected) return;
                if (name === "libusb_control_transfer") {
                    const requestType = args[1].toUInt32() & 255;
                    const request = args[2].toUInt32() & 255;
                    const value = args[3].toUInt32() & 65535;
                    const index = args[4].toUInt32() & 65535;
                    this.length = args[6].toUInt32() & 65535;
                    this.buffer = args[5];
                    this.input = (requestType & 128) !== 0;
                    this.selector = "control:" + requestType.toString(16) + ":" + request.toString(16);
                    this.transferType = "control";
                    const setup = [requestType, request, value & 255, value >> 8, index & 255, index >> 8,
                                   this.length & 255, this.length >> 8];
                    packet(this, "host_to_device", "setup", setup.map(value => value.toString(16).padStart(2, "0")).join(""), "control");
                    this.component = "data:index:" + index.toString(16);
                } else {
                    const endpoint = args[1].toUInt32() & 255;
                    this.input = (endpoint & 128) !== 0;
                    this.buffer = args[2]; this.length = args[3].toInt32(); this.actual = args[4];
                    this.selector = "endpoint:" + endpoint.toString(16);
                    this.transferType = name === "libusb_bulk_transfer" ? "bulk" : "interrupt";
                    this.component = "data";
                }
                this.requested = this.input ? "" : hex(this.buffer, this.length);
            } catch (error) { this.problem = String(error); }
        },
        onLeave(result) {
            try {
                if (!this.outer || !this.selected) return;
                const returned = result.toInt32();
                const status = name === "libusb_control_transfer" ? Math.min(returned, 0) : returned;
                if (status === 0) {
                    const length = name === "libusb_control_transfer" ? returned : countAt(this.actual);
                    if (length > this.length || length < 0) this.problem = "returned_size_exceeds_capacity";
                    else packet(this, this.input ? "device_to_host" : "host_to_device", this.component,
                                this.input ? hex(this.buffer, length) : this.requested === null ? null : this.requested.slice(0, length * 2), this.transferType);
                }
                publish(this, this.packets, status, this.problem);
            } catch (error) { publish(this, [], result.toInt32(), String(error)); }
            finally { leave(this); }
        }
    }));
    hooks.push(name);
}

rpc.exports = {
    start(options) {
        if (settings !== null) throw new Error("Capture already started");
        settings = options;
        const names = options.provider === "iokit" ? ["IOConnectCallMethod", "IOConnectCallScalarMethod", "IOConnectCallStructMethod"] :
            ["libusb_control_transfer", "libusb_bulk_transfer", "libusb_interrupt_transfer"];
        names.forEach(name => options.provider === "iokit" ? attachIOKit(name) : attachUSB(name));
        return { hooks, architecture: Process.arch, pid: Process.id, layer: options.provider === "iokit" ? "driver_api" : "application_api",
                 timestamp_clock: "wall milliseconds at call entry", payload_limit: limit, synchronous_calls_only: true };
    },
    setlabel(next) { label = Object.assign({}, next); return label; },
    stop() {
        listeners.forEach(listener => listener.detach());
        listeners = [];
        return { calls_sent: sent, calls_rate_dropped: dropped, in_flight_threads: active.size };
    }
};
