#!/usr/bin/env python3
"""Host-side provisioning for Weather Station Raspberry Pi Pico 2.

Preserves the already-flashed Pimoroni MicroPython UF2; never erases flash.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[1]
PICO = REPO / "firmware" / "pico"
COMMON = PICO  # Deployed modules live beside the canonical main.py
LOCK_FILE = PICO / "dependencies.lock.json"
FIRMWARE_DIR = REPO / ".pico-firmware"
CORE = ("environmental_reading.py", "dht11_sensor.py", "bme688_sensor.py", "pico_modbus_patch.py")


class ProvisionError(RuntimeError):
    """A recoverable provisioning error."""


def load_dependency_lock(path: Path = LOCK_FILE) -> dict:
    """Read a committed, strictly validated dependency lock, never remote metadata.

    SHA-256 values are deliberate pins, not dynamically inferred from a
    download. Updating firmware or Modbus requires reviewing a Git diff.
    """
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProvisionError(f"Cannot load dependency lock: {path}: {exc}") from exc

    if not isinstance(lock, dict) or set(lock) != {
        "schema_version", "firmware", "modbus"
    } or type(lock["schema_version"]) is not int or lock["schema_version"] != 1:
        raise ProvisionError("Unsupported dependency lock schema; expected version 1")

    firmware, modbus = lock["firmware"], lock["modbus"]
    if not isinstance(firmware, dict) or set(firmware) != {
        "board", "distribution", "version", "filename", "url",
        "sha256", "size", "action"
    }:
        raise ProvisionError("Malformed firmware lock entry")
    if not isinstance(modbus, dict) or set(modbus) != {
        "package", "version", "filename", "metadata_url", "sha256",
        "device_path"
    }:
        raise ProvisionError("Malformed Modbus lock entry")

    import urllib.parse

    for entry in (firmware, modbus):
        digest = entry["sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ProvisionError("Invalid SHA-256 in dependency lock")
        version, filename = entry["version"], entry["filename"]
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\\.[0-9]+\\.[0-9]+", version):
            raise ProvisionError("Invalid version in dependency lock")
        if not isinstance(filename, str) or filename in ("", ".", "..") or "/" in filename or "\\" in filename:
            raise ProvisionError("Invalid filename in dependency lock")

    if (
        firmware["board"] != "Raspberry Pi Pico 2 (RP2350)"
        or firmware["distribution"] != "Pimoroni MicroPython"
        or firmware["action"] != "download_only"
        or type(firmware["size"]) is not int
        or not 0 < firmware["size"] <= 8_000_000
    ):
        raise ProvisionError("Unexpected firmware board, size or installation policy")
    if firmware["filename"] != f"rpi_pico2-v{firmware['version']}-micropython.uf2":
        raise ProvisionError("Firmware filename/version mismatch")
    parsed = urllib.parse.urlsplit(firmware["url"])
    expected_path = (
        "/pimoroni/pimoroni-pico-rp2350/releases/download/"
        f"v{firmware['version']}/{firmware['filename']}"
    )
    if (parsed.scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment) != (
        "https", "github.com", expected_path, "", ""
    ):
        raise ProvisionError("Untrusted firmware download URL")

    if modbus["package"] != "micropython-modbus" or modbus["device_path"] != "/lib/umodbus":
        raise ProvisionError("Unsupported Modbus dependency or device path")
    if modbus["filename"] != f"micropython-modbus-{modbus['version']}.tar.gz":
        raise ProvisionError("Modbus filename/version mismatch")
    if modbus["metadata_url"] != f"https://pypi.org/pypi/micropython-modbus/{modbus['version']}/json":
        raise ProvisionError("Untrusted Modbus metadata URL")

    return lock


# Version and checksums are read exclusively from the reviewed lock manifest.
DEPENDENCIES = load_dependency_lock()
RELEASE = DEPENDENCIES["modbus"]["version"]
EXPECTED_SHA256 = DEPENDENCIES["modbus"]["sha256"]
MODBUS_ARCHIVE_NAME = DEPENDENCIES["modbus"]["filename"]
PYPI_JSON = DEPENDENCIES["modbus"]["metadata_url"]
FIRMWARE_NAME = DEPENDENCIES["firmware"]["filename"]
FIRMWARE_URL = DEPENDENCIES["firmware"]["url"]
FIRMWARE_SHA256 = DEPENDENCIES["firmware"]["sha256"]
FIRMWARE_SIZE = DEPENDENCIES["firmware"]["size"]


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def mpremote(port: str, *args: str, timeout: int = 35) -> str:
    command = [sys.executable, "-m", "mpremote", "connect", port, "resume", *args]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise ProvisionError("Python or mpremote unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProvisionError(f"Timed out running mpremote: {args[:2]}") from exc
    if result.returncode:
        reason = result.stderr.strip() or result.stdout.strip()
        raise ProvisionError(f"mpremote failed ({' '.join(args[:3])}): {reason}")
    return result.stdout


def ensure_mpremote() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "show", "mpremote"],
        capture_output=True, text=True,
    )
    if result.returncode:
        raise ProvisionError("Install mpremote: python -m pip install mpremote")


def list_device_entries(port: str, directory: str = "") -> list[tuple[str, bool, int]]:
    """Read Pico filesystem using the same mpremote fs ls API as users.

    Unlike executing a custom os.walk script in raw REPL, mpremote's
    filesystem listing has already proven reliable for this device.
    Returns (relative_path, is_directory, reported_size).
    """
    location = ":/" + directory if directory else ":"
    output = mpremote(port, "fs", "ls", location)
    entries = []
    saw_heading = False
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("ls :"):
            saw_heading = True
            continue
        match = re.fullmatch(r"(\d+)\s+(.+)", line)
        if not match:
            if line and not line.startswith("ls :"):
                raise ProvisionError(f"Unrecognized mpremote fs ls output: {line!r}")
            continue
        size = int(match.group(1))
        name = match.group(2).strip()
        is_dir = name.endswith("/")
        name = name.rstrip("/")
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise ProvisionError(f"Unsafe Pico filesystem name: {name!r}")
        rel = f"{directory}/{name}" if directory else name
        entries.append((rel, is_dir, size))

    # An empty listing is legitimate; an unrecognized or empty response is not.
    if not entries and not saw_heading:
        raise ProvisionError(
            "Pico file listing returned no recognizable output; refusing backup"
        )
    return entries


def device_inventory(port: str) -> list[tuple[str, int]]:
    """Enumerate and size-check Pico files before overwriting anything."""
    stack = [""]
    files = []
    visited = set()
    while stack:
        directory = stack.pop()
        if directory in visited:
            raise ProvisionError("Recursive/duplicate directory in Pico inventory")
        visited.add(directory)
        for name, is_dir, size in list_device_entries(port, directory):
            if is_dir:
                stack.append(name)
            else:
                files.append((name, size))
        if len(files) > 1000 or len(visited) > 100:
            raise ProvisionError("Unexpectedly large Pico inventory; refusing backup")

    # Fail closed: Pico already holding files MUST NOT report 0 backups.
    # On a genuinely blank board, --skip-backup is the explicit opt-in.
    if not files:
        raise ProvisionError(
            "Found no files to back up. Refusing unsafe empty backup; "
            "use --skip-backup only if the Pico is definitely empty."
        )
    return sorted(files)



def backup(port: str, destination: Path) -> Path:
    inventory = device_inventory(port)
    destination.mkdir(parents=True, exist_ok=False)
    for name, expected_size in inventory:
        local = destination / name
        local.parent.mkdir(parents=True, exist_ok=True)
        mpremote(port, "fs", "cp", ":/" + name, str(local), timeout=90)
        if not local.is_file() or local.stat().st_size != expected_size:
            raise ProvisionError(
                f"Backup incomplete: {name}, expected {expected_size} bytes. "
                "No deployment has started; inspect the Pico before retrying."
            )
    (destination / "inventory.json").write_text(
        json.dumps(
            {"device_files": [
                {"path": name, "size": size} for name, size in inventory
            ]}, indent=2
        ), encoding="utf-8"
    )
    print(f"Backup verified: {len(inventory)} file(s) saved to {destination}")
    return destination



def download_firmware(offline: bool = False) -> Path:
    """Cache the exact known-good UF2 on the PC. NEVER flash the board."""
    target = FIRMWARE_DIR / FIRMWARE_NAME
    if target.is_file():
        if target.stat().st_size != FIRMWARE_SIZE or file_sha(target) != FIRMWARE_SHA256:
            raise ProvisionError(
                f"Existing UF2 failed integrity verification: {target}. "
                "Move the corrupt file aside and retry."
            )
        print(f"Pimoroni UF2 verified (already cached, NOT flashed): {target}")
        return target
    if offline:
        raise ProvisionError(f"Offline mode: UF2 is not cached at {target}")

    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading pinned Pimoroni UF2 to host PC: {FIRMWARE_NAME}")
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".pico-uf2-", suffix=".download",
            dir=FIRMWARE_DIR, delete=False,
        ) as temp_file:
            temp_name = Path(temp_file.name)
            with urlopen(FIRMWARE_URL, timeout=45) as response:
                total = 0
                digest = hashlib.sha256()
                while True:
                    data = response.read(65536)
                    if not data:
                        break
                    total += len(data)
                    if total > FIRMWARE_SIZE:
                        raise ProvisionError("UF2 download larger than expected")
                    digest.update(data)
                    temp_file.write(data)
        if total != FIRMWARE_SIZE or digest.hexdigest() != FIRMWARE_SHA256:
            raise ProvisionError("UF2 size/SHA256 mismatch; download rejected")
        os.replace(temp_name, target)
    finally:
        if temp_name and temp_name.exists():
            temp_name.unlink()
    print(f"Pimoroni UF2 saved (NOT flashed): {target}")
    return target


def cache_file() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".cache"
    return base / "weather-station" / MODBUS_ARCHIVE_NAME


def acquire_modbus(offline: bool, supplied_archive: Path | None) -> Path:
    if supplied_archive:
        archive = supplied_archive.expanduser().resolve()
        if not archive.is_file():
            raise ProvisionError(f"Archive does not exist: {archive}")
    else:
        archive = cache_file()
        if not archive.exists():
            if offline:
                raise ProvisionError(f"Offline cache missing: {archive}")
            with urlopen(PYPI_JSON, timeout=20) as response:
                metadata = json.load(response)
            matches = [
                item for item in metadata["urls"]
                if item["filename"] == MODBUS_ARCHIVE_NAME
            ]
            if len(matches) != 1 or matches[0]["digests"]["sha256"] != EXPECTED_SHA256:
                raise ProvisionError("Unexpected PyPI metadata; refusing download")
            with urlopen(matches[0]["url"], timeout=30) as response:
                payload = response.read(2_000_000)
            if hashlib.sha256(payload).hexdigest() != EXPECTED_SHA256:
                raise ProvisionError("Downloaded archive SHA256 mismatch")
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(payload)
    if file_sha(archive) != EXPECTED_SHA256:
        raise ProvisionError("micropython-modbus 2.3.7 SHA256 mismatch")
    return archive


def extract_umodbus(archive: Path, destination: Path) -> list[Path]:
    """Extract only .py files inside the umodbus package from verified sdist."""
    output = destination / "umodbus"
    output.mkdir(parents=True)
    installed = []
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            segments = Path(member.name).parts
            if "umodbus" not in segments:
                continue
            idx = segments.index("umodbus")
            relative = Path(*segments[idx + 1:])
            if not relative.parts or relative.suffix != ".py" or ".." in relative.parts:
                continue
            stream = tar.extractfile(member)
            if stream is None:
                continue
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(stream.read())
            installed.append(target)
    if not (output / "serial.py").is_file() or not (output / "__init__.py").is_file():
        raise ProvisionError("Pinned sdist does not contain expected umodbus module")
    return installed


def source_files(profile: str, include_tests: bool, main_override: Path | None) -> list[tuple[Path, str]]:
    files = [(COMMON / name, name) for name in CORE]
    files.append((COMMON / "bme688_modbus_extension.py", "bme688_modbus_extension.py"))
    if include_tests:
        files.append((PICO / "common_interface_test.py", "common_interface_test.py"))
    entry = main_override.expanduser().resolve() if main_override else (PICO / "main.py" if profile == "rs485" else PICO / "profiles" / "uart" / "main.py")
    files.append((entry, "main.py"))
    missing = [str(source) for source, _ in files if not source.is_file()]
    if missing:
        raise ProvisionError("Missing source files:\n" + "\n".join(missing))
    return files


def verify_copy(port: str, source: Path, remote: str, scratch: Path) -> None:
    local = scratch / (str(len(list(scratch.iterdir()))) + "-" + source.name)
    mpremote(port, "fs", "cp", ":/" + remote, str(local), timeout=90)
    if file_sha(local) != file_sha(source):
        raise ProvisionError(f"SHA256 read-back mismatch: {remote}")


def doctor(port: str) -> None:
    code = """import sys, os
print('WS_PLATFORM:', sys.platform)
print('WS_VERSION:', sys.version)
print('WS_MACHINE:', os.uname().machine)
for name in ('pimoroni_i2c', 'breakout_bme68x', 'umodbus'):
    try:
        __import__(name)
        print('WS_IMPORT:', name, 'OK')
    except Exception as exc:
        print('WS_IMPORT:', name, 'MISSING', repr(exc))
"""
    result = mpremote(port, "exec", code)
    print(result.strip())
    if "WS_PLATFORM: rp2" not in result:
        raise ProvisionError("Device is not an RP2/RP2350 MicroPython board")
    if "RP2350" not in result:
        raise ProvisionError("This installer requires a Raspberry Pi Pico 2 (RP2350); detected another RP2 board")
    if "WS_IMPORT: breakout_bme68x OK" not in result or "WS_IMPORT: pimoroni_i2c OK" not in result:
        raise ProvisionError("Pimoroni drivers missing; verify the preinstalled UF2 (not flashed by this tool)")
    if "WS_IMPORT: umodbus MISSING" in result:
        print("INFO: umodbus is not installed yet; the deploy step installs it automatically.")


def default_backup_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return REPO / ".pico-backups" / stamp


def deploy(args: argparse.Namespace) -> None:
    files = source_files(args.profile, args.include_tests, args.main)
    print(f"Profile: {args.profile} | MicroPython UF2: unchanged | umodbus: {RELEASE}")
    for source, target in files:
        shown = source.relative_to(REPO) if source.is_relative_to(REPO) else source
        print(f"  {shown} -> /{target}")
    print("  micropython-modbus (SHA256-pinned) -> /lib/umodbus/")
    print(f"  Pimoroni UF2 -> host cache .pico-firmware/{FIRMWARE_NAME} (never flashed)")
    if not args.apply:
        print("DRY RUN: add --apply --port COMx to deploy.")
        return

    download_firmware(args.offline)
    ensure_mpremote()
    doctor(args.port)
    if args.skip_backup:
        print("WARNING: backup explicitly skipped; use only on a known empty Pico.")
    else:
        dest = args.backup_dir or default_backup_path()
        if dest.exists():
            raise ProvisionError(f"Backup directory already exists: {dest}")
        backup(args.port, dest)

    archive = acquire_modbus(args.offline, args.archive)
    with tempfile.TemporaryDirectory(prefix="weather-station-pico-") as td:
        staging = Path(td)
        vendor = staging / "vendor"
        vendor.mkdir()
        vendor_files = extract_umodbus(archive, vendor)

        mpremote(args.port, "exec", "import os\ntry:\n os.mkdir('/lib')\nexcept OSError:\n pass")
        mpremote(args.port, "fs", "cp", "-r", str(vendor / "umodbus"), ":/lib/", timeout=120)

        scratch = staging / "verification"
        scratch.mkdir()
        for source in vendor_files:
            rel = str(source.relative_to(vendor / "umodbus")).replace(os.sep, "/")
            verify_copy(args.port, source, "lib/umodbus/" + rel, scratch)

        # Modules first, boot entry point LAST: avoids a partially installed app.
        for source, target in files:
            if target == "main.py":
                continue
            mpremote(args.port, "fs", "cp", str(source), ":/" + target, timeout=90)
            verify_copy(args.port, source, target, scratch)
        main_source = next(source for source, target in files if target == "main.py")
        mpremote(args.port, "fs", "cp", str(main_source), ":/main.py", timeout=90)
        verify_copy(args.port, main_source, "main.py", scratch)

    postcheck = mpremote(
        args.port, "exec",
        "from umodbus.serial import ModbusRTU\n"
        "import breakout_bme68x, pimoroni_i2c\n"
        "import pico_modbus_patch, bme688_sensor, environmental_reading\n"
        "print('WS_PROVISION_VERIFY_OK')",
    )
    if "WS_PROVISION_VERIFY_OK" not in postcheck:
        raise ProvisionError("Post-install import verification did not pass")
    print("Deployment verified: file SHA256 read-back AND on-device imports OK.")
    if args.reset:
        print("Resetting Pico; main.py will start if the required wiring is ready.")
        mpremote(args.port, "reset")
    else:
        print("No reset performed; reconnect/reset Pico when sensors and RS-485 wiring are ready.")


def bootstrap_mpremote() -> None:
    """The interactive one-command workflow installs its host dependency if needed."""
    try:
        ensure_mpremote()
        return
    except ProvisionError:
        print("mpremote not found. Installing the USB provisioning dependency...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "mpremote"],
            check=False,
        )
        if result.returncode:
            raise ProvisionError(
                "Failed to install mpremote. Install it once with: "
                "python -m pip install mpremote"
            )
        ensure_mpremote()


def interactive_port() -> str:
    """Show actual serial ports and let the operator select a target."""
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise ProvisionError(
            "pyserial unavailable; mpremote installation may be incomplete"
        ) from exc

    ports = sorted(list_ports.comports(), key=lambda p: p.device.upper())
    print("\nAvailable serial ports:")
    if not ports:
        print("  No serial ports detected. Plug the Pico in via USB and retry.")
        raise ProvisionError("No serial port detected")
    for index, port in enumerate(ports, 1):
        details = port.description or "Unknown device"
        print(f"  [{index}] {port.device} — {details}")
    print("\nSelect the Pico by number, or type its COM port.")
    while True:
        answer = input("Pico port [1]: ").strip()
        if not answer:
            return ports[0].device
        if answer.isdigit():
            selected = int(answer)
            if 1 <= selected <= len(ports):
                return ports[selected - 1].device
        for port in ports:
            if answer.upper() == port.device.upper():
                return port.device
        print("Invalid selection. Choose a listed number or COM port.")


def interactive_install() -> int:
    """Full setup in one command: python tools/picoctl.py."""
    print("=" * 55)
    print("  Weather Station | Pico 2 — One-command installer")
    print("=" * 55)
    bootstrap_mpremote()
    port = interactive_port()
    print(f"\nInstalling to {port} using RS-485 (P4) profile.")
    print("The installed MicroPython UF2 will NOT be flashed or erased.")
    args = parser().parse_args(["deploy", "--port", port, "--apply"])
    deploy(args)
    print("\nSUCCESS — Pico application and umodbus are installed and verified.")
    print("UF2 archive retained on your PC. No weather-core setup is necessary.")
    print("Reset/power-cycle only when the sensors and RS-485 wiring are ready.")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pico 2 provisioning for Weather Station")
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("doctor", "backup", "deploy", "firmware"):
        s = sub.add_parser(command)
        if command != "firmware":
            s.add_argument("--port", default="auto", help="USB COM port, e.g. COM5 or /dev/ttyACM0")
        else:
            s.add_argument("--offline", action="store_true", help="Verify local UF2 cache without network")
        if command == "backup":
            s.add_argument("--destination", type=Path)
        if command == "deploy":
            s.add_argument("--profile", choices=("rs485", "uart"), default="rs485")
            s.add_argument("--main", type=Path, help="Use exact local main.py instead of stored profile")
            s.add_argument("--include-tests", action="store_true")
            s.add_argument("--apply", action="store_true", help="Actually write to the Pico")
            s.add_argument("--skip-backup", action="store_true", help="Only for a known empty new board")
            s.add_argument("--backup-dir", type=Path)
            s.add_argument("--offline", action="store_true", help="Only use cached sdist")
            s.add_argument("--archive", type=Path, help="Local SHA256-verified 2.3.7 tar.gz")
            s.add_argument("--reset", action="store_true", help="Run main.py after deployment")
    return p


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    try:
        if not argv:
            return interactive_install()
        args = parser().parse_args(argv)
        if args.command == "doctor":
            ensure_mpremote()
            doctor(args.port)
        elif args.command == "firmware":
            download_firmware(args.offline)
        elif args.command == "backup":
            ensure_mpremote()
            path = args.destination or default_backup_path()
            if path.exists():
                raise ProvisionError(f"Backup directory exists: {path}")
            backup(args.port, path)
        else:
            deploy(args)
    except (ProvisionError, OSError, ValueError, KeyError, EOFError, KeyboardInterrupt) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
