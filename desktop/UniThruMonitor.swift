import AppKit
import Foundation
import SwiftUI

struct RouteState: Decodable, Identifiable {
    let route_id: String
    let source_id: String
    let destination_id: String
    let managed: Bool
    let expected: Bool?
    let applied: Bool?
    let gain_db: Double
    let pan: Double
    let revision: Int
    let owner: String
    var id: String { route_id }
}

struct MonitorEvent: Decodable {
    let time: String
    let message: String
}

struct MonitorState: Decodable {
    let mode: String
    let status: String
    let error: String?
    let listener_port: Int?
    let track_name: String?
    let monitor_state: Int?
    let armed: Bool?
    let source_fresh: Bool?
    let readback_valid: Bool?
    let heartbeat_age_ms: Int?
    let routes: [RouteState]
    let events: [MonitorEvent]
}

struct ServiceAddress: Decodable {
    let url: String
    let token: String
}

struct InventoryDevice: Decodable {
    let transport: String
    let name: String
    let topology_path: [String]
    let status: String
}

struct DeviceInventory: Decodable {
    let devices: [InventoryDevice]
    let note: String
}

struct LearnedEncoding: Decodable {
    let label: String
    let byte_hex: String
    let trials: Int
}

struct LearnedCandidate: Decodable, Identifiable {
    let id: String
    let control: String
    let target: String
    let endpoint: String
    let selector: String
    let byte_offset: Int
    let evidence: String
    let encodings: [LearnedEncoding]
    let warnings: [String]
}

struct LearningReport: Decodable {
    struct Device: Decodable { let identity: String; let model: String; let firmware: String }
    let source: String
    let device: Device
    let transport: String
    let event_count: Int
    let write_enabled: Bool
    let candidates_truncated: Bool
    let candidates: [LearnedCandidate]
}

struct CaptureCapabilities: Decodable {
    let frida_installed: Bool
    let dependency_error: String?
}

@MainActor
final class MonitorModel: ObservableObject {
    @Published var state: MonitorState?
    @Published var connected = false
    @Published var connectionError: String?
    @Published var actionError: String?
    @Published var source = "demo"
    @Published var demoMonitor = 4
    @Published var demoArmed = false
    @Published var sessionPath = ""
    @Published var trackID = ""
    @Published var inputPort = ""
    @Published var learningBusy = false
    @Published var learningError: String?
    @Published var inventory: DeviceInventory?
    @Published var learningReport: LearningReport?
    @Published var captureRunning = false
    @Published var captureStatus = "Not capturing"
    @Published var captureActiveWindow = "baseline-1 / idle"
    @Published var captureProvider = "iokit"
    @Published var captureTransport = "usb"
    @Published var capturePID = ""
    @Published var captureIdentity = "unmapped-device"
    @Published var captureModel = "Unidentified interface"
    @Published var captureFilter = ""
    @Published var capturePhase = "idle"
    @Published var captureTrial = "baseline-1"
    @Published var captureControl = "route.enabled"
    @Published var captureTarget = "input-1/cue-1"
    @Published var captureValue = "false"
    @Published var captureCapabilities: CaptureCapabilities?
    private var captureProcess: Process?
    private var captureInput: Pipe?
    private var captureOutput: Pipe?
    private var captureErrors: Pipe?
    private var captureBuffer = Data()
    private var learningData: Data?
    private var learningProcess: Process?
    private var backend: Process?
    private var endpoint: URL?
    private var token = ""
    private var startupData = Data()
    private var output: Pipe?
    private var errors: Pipe?
    private var pollTask: Task<Void, Never>?
    private var actionTask: Task<Void, Never>?
    private var stopping = false

    func start() {
        guard backend == nil else { return }
        guard let root = Bundle.main.object(forInfoDictionaryKey: "UniThruProjectRoot") as? String else {
            connectionError = "The development app is missing its project location. Rebuild it from this checkout."
            return
        }
        let process = Process()
        let standardOutput = Pipe()
        let standardError = Pipe()
        process.executableURL = URL(fileURLWithPath: root).appendingPathComponent(".venv/bin/python")
        process.arguments = ["-m", "unithru", "monitor-server"]
        process.currentDirectoryURL = URL(fileURLWithPath: root)
        process.standardOutput = standardOutput
        process.standardError = standardError
        output = standardOutput
        errors = standardError
        standardOutput.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            Task { @MainActor in self?.readStartup(data) }
        }
        standardError.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            let message = String(decoding: data, as: UTF8.self)
            Task { @MainActor in
                guard let self, !self.stopping else { return }
                self.connectionError = String(message.suffix(1500))
            }
        }
        process.terminationHandler = { [weak self] process in
            let status = process.terminationStatus
            Task { @MainActor in
                guard let self, !self.stopping else { return }
                self.connected = false
                self.connectionError = "The local monitor service stopped (\(status)). Quit and reopen this window."
            }
        }
        do {
            try process.run()
            backend = process
        } catch {
            connectionError = "Could not start the project’s Python environment: \(error.localizedDescription)"
        }
    }

    private func readStartup(_ data: Data) {
        guard endpoint == nil else { return }
        startupData.append(data)
        guard let newline = startupData.firstIndex(of: 10) else { return }
        do {
            let address = try JSONDecoder().decode(ServiceAddress.self, from: startupData.prefix(upTo: newline))
            guard let url = URL(string: address.url), url.scheme == "http", url.host == "127.0.0.1", url.port != nil else {
                throw URLError(.badURL)
            }
            endpoint = url
            token = address.token
            pollTask = Task { [weak self] in
                while !Task.isCancelled {
                    await self?.refresh()
                    try? await Task.sleep(nanoseconds: 250_000_000)
                }
            }
        } catch {
            connectionError = "Could not read the local service address: \(error.localizedDescription)"
        }
    }

    private func request(_ path: String) throws -> URLRequest {
        guard let endpoint else { throw URLError(.notConnectedToInternet) }
        var request = URLRequest(url: endpoint.appendingPathComponent(path))
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.timeoutInterval = 2
        request.cachePolicy = .reloadIgnoringLocalCacheData
        return request
    }

    private func refresh() async {
        do {
            let (data, response) = try await URLSession.shared.data(for: request("api/state"))
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
            state = try JSONDecoder().decode(MonitorState.self, from: data)
            connected = true
            connectionError = nil
        } catch {
            guard !stopping else { return }
            connected = false
            connectionError = "Readback unavailable. Applied indicators are unknown. \(error.localizedDescription)"
        }
    }

    func send(_ action: [String: Any]) {
        let previous = actionTask
        actionTask = Task {
            await previous?.value
            guard !Task.isCancelled else { return }
            do {
                var request = try request("api/action")
                request.httpMethod = "POST"
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                request.httpBody = try JSONSerialization.data(withJSONObject: action)
                let (data, response) = try await URLSession.shared.data(for: request)
                guard (response as? HTTPURLResponse)?.statusCode == 202 else {
                    let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
                    throw NSError(domain: "UniThru", code: 1, userInfo: [NSLocalizedDescriptionKey: body?["error"] as? String ?? "Action was rejected"])
                }
                actionError = nil
                await refresh()
            } catch {
                actionError = error.localizedDescription
            }
        }
    }

    func configureDemo() {
        send(["action": "configure", "mode": "demo"])
    }

    func updateDemo() {
        send(["action": "demo", "monitor_state": demoMonitor, "armed": demoArmed])
    }

    func connectArdour() {
        send(["action": "configure", "mode": "ardour", "session_path": sessionPath,
              "track_id": trackID, "input_port": inputPort])
    }

    func stop() {
        stopping = true
        pollTask?.cancel()
        actionTask?.cancel()
        output?.fileHandleForReading.readabilityHandler = nil
        errors?.fileHandleForReading.readabilityHandler = nil
        if let backend, backend.isRunning { backend.terminate() }
        if let learningProcess, learningProcess.isRunning { learningProcess.terminate() }
        if let captureProcess, captureProcess.isRunning {
            sendCaptureCommand(["action": "stop"])
            captureProcess.terminate()
        }
    }

    func startCapture() {
        guard !captureRunning, !learningBusy,
              let root = Bundle.main.object(forInfoDictionaryKey: "UniThruProjectRoot") as? String else { return }
        guard captureCapabilities?.frida_installed == true else {
            learningError = "Capture runtime is not ready. Install requirements-capture.txt, then check capture support."
            return
        }
        guard let pid = Int(capturePID), pid > 0 else {
            learningError = "Enter the explicit PID of the manufacturer’s mixer or control helper."
            return
        }
        let panel = NSSavePanel()
        panel.title = "Save a new live learning capture"
        panel.nameFieldStringValue = "unithru-live-capture.json"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        var arguments = ["-m", "unithru", "learn", "capture", "--provider", captureProvider,
                         "--transport", captureProvider == "libusb" ? "usb" : captureTransport,
                         "--pid", String(pid), "--device-id", captureIdentity,
                         "--model", captureModel, "--output", url.path]
        if !captureFilter.isEmpty {
            arguments += [captureProvider == "iokit" ? "--connection" : "--handle", captureFilter]
        }
        let process = Process()
        let input = Pipe(), output = Pipe(), errors = Pipe()
        process.executableURL = URL(fileURLWithPath: root).appendingPathComponent(".venv/bin/python")
        process.arguments = arguments
        process.currentDirectoryURL = URL(fileURLWithPath: root)
        process.standardInput = input
        process.standardOutput = output
        process.standardError = errors
        captureInput = input; captureOutput = output; captureErrors = errors
        captureBuffer = Data()
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            Task { @MainActor in self?.receiveCaptureEvents(data) }
        }
        errors.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            let text = String(decoding: data, as: UTF8.self)
            Task { @MainActor in self?.learningError = String(text.suffix(1800)) }
        }
        process.terminationHandler = { [weak self] process in
            let status = process.terminationStatus
            Task { @MainActor in
                guard let self else { return }
                self.captureRunning = false
                self.captureInput = nil
                if status != 0 { self.captureStatus = "Capture stopped with an error (\(status))" }
            }
        }
        do {
            try process.run()
            captureProcess = process
            captureRunning = true
            captureStatus = "Attaching to selected PID \(pid)…"
            learningError = nil
        } catch {
            learningError = error.localizedDescription
        }
    }

    private func receiveCaptureEvents(_ data: Data) {
        captureBuffer.append(data)
        while let newline = captureBuffer.firstIndex(of: 10) {
            let line = captureBuffer.prefix(upTo: newline)
            guard let event = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else {
                captureBuffer.removeSubrange(...newline)
                continue
            }
            switch event["event"] as? String {
            case "ready":
                captureActiveWindow = "baseline-1 / idle"
                captureStatus = "Capturing selected process"
            case "label":
                if let label = event["label"] as? [String: Any] {
                    captureActiveWindow = "\(label["trial_id"] ?? "") / \(label["phase"] ?? "")"
                    learningError = nil
                }
            case "progress":
                if let stats = event["stats"] as? [String: Any] {
                    captureStatus = "\(stats["calls"] ?? 0) calls · \(stats["events"] ?? 0) usable observations · \(stats["failed_calls"] ?? 0) failed calls"
                }
            case "saved":
                captureStatus = "Capture saved; target process was not stopped"
                if let path = event["output"] as? String,
                   let stats = event["stats"] as? [String: Any], let count = stats["events"] as? Int, count > 0 {
                    runLearning(["analyze", path])
                } else {
                    learningError = "No usable control payloads were captured. Select the actual control helper or another provider."
                }
            case "capture_error", "command_error":
                learningError = event["message"] as? String
            default: break
            }
            captureBuffer.removeSubrange(...newline)
        }
    }

    private func sendCaptureCommand(_ command: [String: Any]) {
        guard let captureInput else { return }
        do {
            var data = try JSONSerialization.data(withJSONObject: command)
            data.append(10)
            try captureInput.fileHandleForWriting.write(contentsOf: data)
        } catch { learningError = error.localizedDescription }
    }

    func markCaptureWindow() {
        var label: [String: Any] = ["phase": capturePhase, "trial_id": captureTrial]
        if capturePhase != "idle" {
            do {
                let value = try JSONSerialization.jsonObject(with: Data(captureValue.utf8), options: .fragmentsAllowed)
                guard !(value is [Any]) && !(value is [String: Any]) else {
                    throw NSError(domain: "UniThru", code: 1, userInfo: [NSLocalizedDescriptionKey: "Enter a JSON scalar value"])
                }
                label["control"] = captureControl; label["target"] = captureTarget; label["value"] = value
            } catch {
                learningError = "Label value must be JSON: true, false, a number, quoted text, or null."
                return
            }
        }
        sendCaptureCommand(["action": "label", "label": label])
    }

    func stopCapture() {
        sendCaptureCommand(["action": "stop"])
        captureStatus = "Stopping observation and saving capture…"
    }

    func runLearning(_ arguments: [String], inventoryOnly: Bool = false, capabilitiesOnly: Bool = false) {
        guard !learningBusy,
              let root = Bundle.main.object(forInfoDictionaryKey: "UniThruProjectRoot") as? String else { return }
        learningBusy = true
        learningError = nil
        let process = Process()
        process.executableURL = URL(fileURLWithPath: root).appendingPathComponent(".venv/bin/python")
        process.arguments = ["-m", "unithru", "learn"] + arguments
        process.currentDirectoryURL = URL(fileURLWithPath: root)
        let output = Pipe()
        let errors = Pipe()
        process.standardOutput = output
        process.standardError = errors
        learningProcess = process
        Task.detached { [weak self] in
            guard let self else { return }
            do {
                try process.run()
                let data = output.fileHandleForReading.readDataToEndOfFile()
                let errorData = errors.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                guard process.terminationStatus == 0 else {
                    throw NSError(domain: "UniThru", code: Int(process.terminationStatus), userInfo: [
                        NSLocalizedDescriptionKey: String(decoding: errorData, as: UTF8.self)])
                }
                if capabilitiesOnly {
                    let result = try JSONDecoder().decode(CaptureCapabilities.self, from: data)
                    await MainActor.run { self.captureCapabilities = result }
                } else if inventoryOnly {
                    let result = try JSONDecoder().decode(DeviceInventory.self, from: data)
                    await MainActor.run { self.inventory = result }
                } else {
                    let result = try JSONDecoder().decode(LearningReport.self, from: data)
                    await MainActor.run { self.learningReport = result; self.learningData = data }
                }
            } catch {
                await MainActor.run { self.learningError = error.localizedDescription }
            }
            await MainActor.run { self.learningBusy = false; self.learningProcess = nil }
        }
    }

    func loadLearningExample() {
        guard let root = Bundle.main.object(forInfoDictionaryKey: "UniThruProjectRoot") as? String else { return }
        runLearning(["analyze", root + "/examples/learning/labelled-trace.json"])
    }

    func importLearningTrace() {
        let panel = NSOpenPanel()
        panel.title = "Import a normalized, labelled learning trace"
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { runLearning(["analyze", url.path]) }
    }

    func exportLearningProfile() {
        guard let learningData else { return }
        let panel = NSSavePanel()
        panel.title = "Export unverified candidate profile"
        panel.nameFieldStringValue = "unithru-candidate-profile.json"
        if panel.runModal() == .OK, let url = panel.url {
            do { try learningData.write(to: url, options: .atomic) }
            catch { learningError = error.localizedDescription }
        }
    }
}

struct LearningView: View {
    @ObservedObject var model: MonitorModel

    var body: some View {
        ScrollView {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Device learning").font(.title2).fontWeight(.semibold)
                    Text("USB and Thunderbolt discovery · labelled control-traffic analysis").foregroundStyle(.secondary)
                }
                Spacer()
                Label("Read only", systemImage: "lock").foregroundStyle(.secondary)
            }
            HStack {
                Button("Scan USB & Thunderbolt") { model.runLearning(["inventory"], inventoryOnly: true) }
                Button("Import labelled trace…") { model.importLearningTrace() }
                Button("Load synthetic example") { model.loadLearningExample() }
                Button("Check capture support") { model.runLearning(["capabilities"], capabilitiesOnly: true) }
                Spacer()
                if model.learningBusy { ProgressView().controlSize(.small) }
                Button("Export candidates…") { model.exportLearningProfile() }.disabled(model.learningReport == nil)
            }.disabled(model.learningBusy || model.captureRunning)
            if let error = model.learningError {
                Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            GroupBox("Live capture — observes selected process; never sends device commands") {
                VStack(alignment: .leading, spacing: 9) {
                    HStack {
                        Picker("Provider", selection: $model.captureProvider) {
                            Text("IOKit driver/API").tag("iokit")
                            Text("libusb API").tag("libusb")
                        }.frame(width: 220)
                        Picker("Transport", selection: $model.captureTransport) {
                            Text("USB").tag("usb")
                            Text("Thunderbolt").tag("thunderbolt")
                        }.frame(width: 210).disabled(model.captureProvider == "libusb")
                        TextField("Mixer/helper PID", text: $model.capturePID).frame(width: 125)
                        TextField("Optional connection / handle", text: $model.captureFilter)
                    }.disabled(model.captureRunning)
                    HStack {
                        TextField("Capture device identity", text: $model.captureIdentity)
                        TextField("Device model label", text: $model.captureModel)
                        Button("Start live capture…") { model.startCapture() }
                            .disabled(model.captureRunning || model.learningBusy || model.capturePID.isEmpty || model.captureCapabilities?.frida_installed != true)
                        Button("Stop & analyse") { model.stopCapture() }.disabled(!model.captureRunning)
                    }
                    HStack {
                        Picker("Window", selection: $model.capturePhase) {
                            Text("Idle").tag("idle")
                            Text("Action").tag("action")
                            Text("Readback").tag("readback")
                        }.frame(width: 160)
                        TextField("Trial ID", text: $model.captureTrial).frame(width: 120)
                        TextField("Control", text: $model.captureControl)
                        TextField("Target", text: $model.captureTarget)
                        TextField("JSON value", text: $model.captureValue).frame(width: 95)
                        Button("Apply window") { model.markCaptureWindow() }.disabled(!model.captureRunning)
                    }
                    Text("\(model.captureStatus) · Applied window: \(model.captureActiveWindow)")
                        .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                    Text("Mark the window, then move one control in the manufacturer’s mixer. Only APIs used by the selected process are visible; attach protections are respected.")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    if let support = model.captureCapabilities {
                        Text(support.frida_installed ? "Runtime loaded; target attachment and API use remain to be checked." : "Capture unavailable: optional Frida runtime is missing or cannot load. Install requirements-capture.txt after freeing enough storage.")
                            .font(.caption).foregroundStyle(support.frida_installed ? Color.secondary : Color.orange)
                    }
                }.padding(6)
            }
            GroupBox("Connected topology — inventory is not a traffic capture") {
                if let inventory = model.inventory {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 10) {
                            ForEach(Array(inventory.devices.enumerated()), id: \.offset) { _, device in
                                HStack(alignment: .top) {
                                    Text(device.transport.uppercased()).font(.caption).frame(width: 110, alignment: .leading)
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(device.name).fontWeight(.medium)
                                        Text(device.topology_path.joined(separator: " → ")).font(.caption).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Text("Endpoints not captured").font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            if inventory.devices.isEmpty { Text("No topology entries reported by macOS") }
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(6)
                    }.frame(height: 95)
                } else {
                    Text("Scan connected buses and devices without opening interfaces or changing drivers.")
                        .foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading).padding(10)
                }
            }
            GroupBox("Candidate mappings — hardware writes disabled") {
                if let report = model.learningReport {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            Text(report.device.model).fontWeight(.medium)
                            Text("\(report.transport.uppercased()) · \(report.event_count) events").foregroundStyle(.secondary)
                            Spacer()
                            Text(report.source == "synthetic" ? "SYNTHETIC EXAMPLE" : "UNVERIFIED CAPTURE")
                                .font(.caption).foregroundStyle(.orange)
                        }
                        if report.candidates_truncated {
                            Text("Candidate display limited to 256 byte positions; the trace needs a narrower experiment.")
                                .font(.caption).foregroundStyle(.orange)
                        }
                        ScrollView {
                            VStack(alignment: .leading, spacing: 12) {
                                ForEach(report.candidates) { candidate in
                                    VStack(alignment: .leading, spacing: 4) {
                                        HStack {
                                            Text(candidate.control).fontWeight(.medium)
                                            Text(candidate.target).foregroundStyle(.secondary)
                                            Spacer()
                                            Text(candidate.evidence.replacingOccurrences(of: "_", with: " ")).font(.caption)
                                        }
                                        Text("\(candidate.endpoint) · \(candidate.selector) · byte offset \(candidate.byte_offset)")
                                            .font(.caption).monospaced().textSelection(.enabled)
                                        Text(candidate.encodings.map { "\($0.label) → 0x\($0.byte_hex) (\($0.trials) trials)" }.joined(separator: "    "))
                                            .font(.caption).textSelection(.enabled)
                                        Text(candidate.warnings.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                                    }
                                    Divider()
                                }
                                if report.candidates.isEmpty { Text("No repeatable byte/value correlations found") }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }.frame(minHeight: 100, maxHeight: 190)
                    }.padding(6)
                } else {
                    Text("Move one control at a time in the manufacturer’s mixer, label the observations, then import the trace.")
                        .foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading).padding(10)
                }
            }
            Spacer(minLength: 0)
            Text("Live process/API capture requires the optional Frida runtime and target attach permission. Linux USB bus capture is available through the CLI usbmon provider. No raw Thunderbolt wire sniffer or command replay is provided.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }.padding(24)
        }.frame(minWidth: 900, minHeight: 690)
            .onAppear {
                if model.captureCapabilities == nil {
                    model.runLearning(["capabilities"], capabilitiesOnly: true)
                }
            }
    }
}

struct StateIndicator: View {
    let value: Bool?
    var unmanaged = false
    var body: some View {
        HStack(spacing: 7) {
            Image(systemName: unmanaged ? "minus.circle" : value == true ? "checkmark.circle.fill" : value == false ? "circle" : "questionmark.circle")
            Text(unmanaged ? "Unmanaged" : value == true ? "ON" : value == false ? "OFF" : "Unknown")
                .fontWeight(.medium)
        }
        .foregroundStyle(unmanaged ? Color.secondary : value == true ? Color.green : value == false ? Color.secondary : Color.orange)
        .accessibilityElement(children: .combine)
    }
}

struct MonitorView: View {
    @ObservedObject var model: MonitorModel

    private func routeName(_ route: RouteState) -> String {
        switch route.route_id {
        case "route-vocal-cue": return "Vocal → Cue A"
        case "route-vocal-cue-b": return "Vocal → Cue B"
        case "route-vocal-mono": return "Vocal → Mono cue"
        case "route-stereo-cue": return "Stereo input → Cue A"
        default: return route.route_id
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Image(systemName: "waveform.path").font(.largeTitle).foregroundStyle(.tint)
                VStack(alignment: .leading, spacing: 3) {
                    Text("UniThru Monitor").font(.title2).fontWeight(.semibold)
                    Text("Requested monitoring and verified mixer state").foregroundStyle(.secondary)
                }
                Spacer()
                Text("SIMULATOR").font(.caption).fontWeight(.semibold)
                    .padding(.horizontal, 10).padding(.vertical, 6)
                    .background(Color.orange.opacity(0.15), in: Capsule())
            }

            GroupBox {
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Picker("Source", selection: $model.source) {
                            Text("Demo").tag("demo")
                            Text("Ardour").tag("ardour")
                        }.pickerStyle(.segmented).frame(width: 230)
                            .onChange(of: model.source) { _, value in
                                if value == "demo" { model.configureDemo() }
                            }
                        Spacer()
                        Label(model.connected ? model.state?.status ?? "Starting" : "Connecting to local service",
                              systemImage: model.connected ? "link" : "antenna.radiowaves.left.and.right.slash")
                            .foregroundStyle(.secondary)
                    }
                    if model.source == "demo" {
                        HStack(spacing: 20) {
                            Picker("Monitoring", selection: $model.demoMonitor) {
                                Text("Input").tag(2)
                                Text("Disk").tag(4)
                                Text("Silence").tag(0)
                                Text("Input + disk").tag(6)
                            }.frame(width: 300)
                                .onChange(of: model.demoMonitor) { _, _ in model.updateDemo() }
                            Toggle("Track armed", isOn: $model.demoArmed)
                                .onChange(of: model.demoArmed) { _, _ in model.updateDemo() }
                            Spacer()
                            Button("Reconnect demo") { model.configureDemo() }
                        }
                        Text("Arming alone does not request input monitoring. Demo controls change only the fake mixer.")
                            .font(.caption).foregroundStyle(.secondary)
                    } else {
                        HStack {
                            TextField("Exact Ardour session path", text: $model.sessionPath)
                            TextField("Track ID", text: $model.trackID).frame(width: 120)
                            TextField("Connected input port", text: $model.inputPort).frame(width: 210)
                            Button("Connect") { model.connectArdour() }
                                .disabled(model.sessionPath.isEmpty || model.trackID.isEmpty || model.inputPort.isEmpty)
                        }
                        HStack {
                            Text("Ardour hook listener: 127.0.0.1:\(model.state?.listener_port ?? 0)")
                                .monospaced().textSelection(.enabled)
                            Spacer()
                            Text("\(model.state?.track_name ?? "No track received") · \(model.state?.armed == true ? "Armed" : "Not armed")")
                        }.font(.caption).foregroundStyle(.secondary)
                    }
                }.padding(6)
            }.disabled(!model.connected)

            if let error = model.connectionError ?? model.actionError ?? model.state?.error {
                Label(error, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }

            GroupBox("Monitoring routes") {
                Grid(alignment: .leading, horizontalSpacing: 26, verticalSpacing: 0) {
                    GridRow {
                        Text("Route").frame(maxWidth: .infinity, alignment: .leading)
                        Text("Requested")
                        Text("Applied")
                        Text("Gain / pan")
                        Text("Revision")
                        Text("Owner")
                    }.font(.caption).foregroundStyle(.secondary).padding(.vertical, 10)
                    Divider().gridCellColumns(6)
                    ForEach(model.state?.routes ?? []) { route in
                        GridRow {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(routeName(route)).fontWeight(.medium)
                                Text(route.managed ? "Mapped preview route" : "Outside this track’s mapping")
                                    .font(.caption).foregroundStyle(.secondary)
                            }.frame(maxWidth: .infinity, alignment: .leading)
                            StateIndicator(value: model.connected ? route.expected : nil, unmanaged: !route.managed)
                            StateIndicator(value: model.connected ? route.applied : nil)
                            Text(String(format: "%.1f dB / %.1f", route.gain_db, route.pan)).monospacedDigit()
                            Text("\(route.revision)").monospacedDigit()
                            Text(route.owner).foregroundStyle(.secondary)
                        }.padding(.vertical, 12)
                        Divider().gridCellColumns(6)
                    }
                }.padding(.horizontal, 6)
            }

            GroupBox("Recent changes") {
                ScrollView {
                    VStack(alignment: .leading, spacing: 7) {
                        ForEach(Array((model.state?.events ?? []).enumerated()), id: \.offset) { _, event in
                            HStack(alignment: .top, spacing: 12) {
                                Text(event.time).monospacedDigit().foregroundStyle(.secondary)
                                Text(event.message).textSelection(.enabled)
                                Spacer()
                            }
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading).padding(6)
                }.frame(minHeight: 70, maxHeight: 130)
            }
            Spacer(minLength: 0)
            HStack {
                Label("Fake mixer only · No audio or DAW controls changed", systemImage: "info.circle")
                Spacer()
                Text(model.connected && model.state?.readback_valid == true ? "Verified simulator readback" : "Readback unknown")
            }.font(.caption).foregroundStyle(.secondary)
        }.padding(24).frame(minWidth: 900, minHeight: 690)
            .onAppear { model.start() }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let model = MonitorModel()
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.applicationIconImage = NSImage(systemSymbolName: "waveform.path", accessibilityDescription: "UniThru")
        NSApplication.shared.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { model.stop() }
}

@main
struct UniThruMonitorApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    var body: some Scene {
        Window("UniThru Monitor", id: "monitor") {
            TabView {
                MonitorView(model: delegate.model).tabItem { Label("Monitor", systemImage: "waveform.path") }
                LearningView(model: delegate.model).tabItem { Label("Device learning", systemImage: "magnifyingglass") }
            }
        }.defaultSize(width: 1020, height: 760)
    }
}
