import argparse
import json
import threading

from unithru.osc import OPERATIONS
from unithru.transport import LoopbackServer, run_demo


def port_number(value):
    port = int(value)
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port must be 0 through 65535")
    return port


def main():
    parser = argparse.ArgumentParser(description="UniThru fake-mixer loopback simulator")
    commands = parser.add_subparsers(dest="command", required=True)
    server_parser = commands.add_parser("server", help="Run until Ctrl-C; no hardware or audio access")
    server_parser.add_argument("--port", type=port_number, required=True, help="Use 0 for a fresh OS-assigned port")
    server_parser.add_argument("--drop-first-reply", choices=sorted(OPERATIONS))
    demo_parser = commands.add_parser("demo", help="Run a self-contained demo on a fresh port and exit")
    demo_parser.add_argument("--drop-first-reply", choices=sorted(OPERATIONS), default="set_route")
    preview_parser = commands.add_parser("ardour-preview", help="Preview one mapped Ardour track using a private fake mixer")
    preview_parser.add_argument("--listen-port", type=port_number, default=0)
    preview_parser.add_argument("--session-path", required=True)
    preview_parser.add_argument("--track-id", required=True)
    preview_parser.add_argument("--input-port", required=True)
    commands.add_parser("monitor-server", help="Private service used by the UniThru Monitor desktop app")
    learning_parser = commands.add_parser("learn", help="Read-only cross-device inventory and labelled trace analysis")
    learning_commands = learning_parser.add_subparsers(dest="learn_command", required=True)
    inventory_parser = learning_commands.add_parser("inventory", help="Discover macOS USB/Thunderbolt topology; does not capture traffic")
    inventory_parser.add_argument("--output")
    analyze_parser = learning_commands.add_parser("analyze", help="Generate non-executable candidates from a normalized trace")
    analyze_parser.add_argument("trace")
    analyze_parser.add_argument("--output")
    capability_parser = learning_commands.add_parser("capabilities", help="Report capture providers without attaching to a process")
    capability_parser.add_argument("--output")
    capture_parser = learning_commands.add_parser("capture", help="Capture selected process APIs or a selected Linux USB bus/device")
    capture_parser.add_argument("--provider", choices=("iokit", "libusb", "usbmon"), required=True)
    capture_parser.add_argument("--transport", choices=("usb", "thunderbolt"), required=True)
    capture_parser.add_argument("--pid", type=int)
    capture_parser.add_argument("--connection", type=lambda value: int(value, 0))
    capture_parser.add_argument("--selector", type=lambda value: int(value, 0))
    capture_parser.add_argument("--handle")
    capture_parser.add_argument("--bus", type=int)
    capture_parser.add_argument("--device-address", type=int)
    capture_parser.add_argument("--device-id", required=True)
    capture_parser.add_argument("--model", required=True)
    capture_parser.add_argument("--firmware", default="unknown")
    capture_parser.add_argument("--output", required=True)
    options = parser.parse_args()
    if options.command == "learn":
        from unithru.learning import run_learning

        try:
            run_learning(options)
        except (OSError, ValueError, RecursionError) as error:
            parser.exit(1, f"Learning operation failed: {error}\n")
        return
    if options.command == "monitor-server":
        from unithru.monitor import run_monitor_server

        run_monitor_server()
        return
    if options.command == "ardour-preview":
        from unithru.ardour import run_preview

        try:
            run_preview(options)
        except (OSError, RuntimeError, TimeoutError) as error:
            parser.exit(1, f"Ardour preview stopped: {error}\n")
        return
    port = options.port if options.command == "server" else 0
    try:
        server = LoopbackServer(port, drop_first_reply=options.drop_first_reply)
    except OSError as error:
        parser.exit(1, f"Could not bind a private loopback socket: {error}\n")
    with server:
        if options.command == "server":
            print(json.dumps({"listening": list(server.server_address), "backend": "fake-mixer"}), flush=True)
            try:
                server.serve_forever(poll_interval=0.05)
            except KeyboardInterrupt:
                pass
        else:
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            worker.start()
            try:
                print(json.dumps(run_demo(server), indent=2))
            finally:
                server.shutdown()
                worker.join(timeout=2)


if __name__ == "__main__":
    main()
