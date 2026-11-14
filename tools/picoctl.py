#!/usr/bin/env python3
"""Host-side provisioning for Weather Station Raspberry Pi Pico 2.

Preserves the already-flashed Pimoroni MicroPython UF2; never erases flash.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
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
RELEASE = "2.3.7"
EXPECTED_SHA256 = "b9351a66af0ff29dd585900e53827a989dedcb02ec63b1041358165089652271"
PYPI_JSON = f"https://pypi.org/pypi/micropython-modbus/{RELEASE}/json"
CORE = ("environmental_reading.py", "dht11_sensor.py", "bme688_sensor.py", "pico_modbus_patch.py")


class ProvisionError(RuntimeError):
    """A recoverable provisioning error."""


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


def device_inventory(port: str) -> list[str]:
    """Enumerate all files on Pico flash for a lossless pre-deploy backup."""
    script = """import os
def walk(path):
    for name in os.listdir(path):
        p = path.rstrip('/') + '/' + name
        try:
            mode = os.stat(p)[0]
        except OSError:
            continue
        if mode & 0x4000:
            walk(p)
        else:
            print('WSPICOFILE:' + p.lstrip('/'))
walk('/')
"""
    output = mpremote(port, "exec", script)
    paths = [
        line.partition("WSPICOFILE:")[2].strip()
        for line in output.splitlines() if "WSPICOFILE:" in line
    ]
    if any(not p or p.startswith("../") or "/../" in p for p in paths):
        raise ProvisionError("Unexpected remote path; refusing backup")
    return sorted(set(paths))


def backup(port: str, destination: Path) -> Path:
    inventory = device_inventory(port)
    destination.mkdir(parents=True, exist_ok=False)
    for name in inventory:
        local = destination / name
        local.parent.mkdir(parents=True, exist_ok=True)
        mpremote(port, "fs", "cp", ":/" + name, str(local), timeout=90)
    (destination / "inventory.json").write_text(
        json.dumps({"device_files": inventory}, indent=2), encoding="utf-8"
    )
    print(f"Backed up {len(inventory)} file(s) to {destination}")
    return destination


def cache_file() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".cache"
    return base / "weather-station" / f"micropython-modbus-{RELEASE}.tar.gz"


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
                if item["filename"] == f"micropython-modbus-{RELEASE}.tar.gz"
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
    if "WS_IMPORT: breakout_bme68x OK" not in result or "WS_IMPORT: pimoroni_i2c OK" not in result:
        raise ProvisionError("Pimoroni drivers missing; verify the preinstalled UF2 (not flashed by this tool)")


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
    if not args.apply:
        print("DRY RUN: add --apply --port COMx to deploy.")
        return

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

    print("Deployment verified by SHA256 read-back for every file.")
    if args.reset:
        print("Resetting Pico; main.py will start if the required wiring is ready.")
        mpremote(args.port, "reset")
    else:
        print("No reset performed; reconnect/reset Pico when sensors and RS-485 wiring are ready.")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pico 2 provisioning for Weather Station")
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("doctor", "backup", "deploy"):
        s = sub.add_parser(command)
        s.add_argument("--port", default="auto", help="USB COM port, e.g. COM5 or /dev/ttyACM0")
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
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            ensure_mpremote()
            doctor(args.port)
        elif args.command == "backup":
            ensure_mpremote()
            path = args.destination or default_backup_path()
            if path.exists():
                raise ProvisionError(f"Backup directory exists: {path}")
            backup(args.port, path)
        else:
            deploy(args)
    except (ProvisionError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
