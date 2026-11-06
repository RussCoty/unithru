import plistlib
import platform
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "build/UniThru Monitor.app"


def main():
    if sys.platform != "darwin":
        raise SystemExit("The native monitor build requires macOS and Xcode command-line tools")
    contents = APP / "Contents"
    executable = contents / "MacOS/UniThruMonitor"
    executable.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "xcrun", "swiftc", "-swift-version", "5", "-parse-as-library",
        "-target", f"{platform.machine()}-apple-macosx14.0",
        str(ROOT / "desktop/UniThruMonitor.swift"), "-o", str(executable),
        "-framework", "SwiftUI", "-framework", "AppKit", "-framework", "Foundation",
    ], check=True)
    metadata = {
        "CFBundleExecutable": "UniThruMonitor", "CFBundleIdentifier": "org.unithru.monitor.dev",
        "CFBundleName": "UniThru Monitor", "CFBundleDisplayName": "UniThru Monitor",
        "CFBundleVersion": "1", "CFBundleShortVersionString": "0.1",
        "CFBundlePackageType": "APPL", "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "14.0", "UniThruProjectRoot": str(ROOT),
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    }
    with (contents / "Info.plist").open("wb") as destination:
        plistlib.dump(metadata, destination)
    subprocess.run(["codesign", "--force", "--sign", "-", str(APP)], check=True)
    print(APP)


if __name__ == "__main__":
    main()
