#!/usr/bin/env python3
"""Host-side provisioning for Weather Station Raspberry Pi Pico 2.

Preserves the already-flashed Pimoroni MicroPython UF2; never erases flash.

Every write to the Pico (``deploy`` or ``push``) is recorded twice:

* on the Pico, in ``/ws_deploy.json`` (what is installed, from which Git
  commit, with the SHA-256 of every file), and
* on the PC, appended to ``.pico-deployments/log.jsonl``.

``status`` reads the Pico back and compares it with that record and with
the current repository.

Everyday use (the Pico's port and current profile are detected):

    python tools/picoctl.py                      install / reinstall the firmware
    python tools/picoctl.py --profile socket     switch the Pico to another wiring profile
    python tools/picoctl.py push FILE --as main.py
    python tools/picoctl.py status
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
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
BACKUP_ROOT = REPO / ".pico-backups"
LOG_FILE = REPO / ".pico-deployments" / "log.jsonl"
CORE = ("environmental_reading.py", "dht11_sensor.py", "bme688_sensor.py", "pico_modbus_patch.py")
# Kept for file parity with the previously used Pico; main.py does not import it.
EXTRA_MODULES = ("bme688_modbus_extension.py",)

MANIFEST_NAME = "ws_deploy.json"
MANIFEST_SCHEMA = 1

# Host tool pin. The backup parses `mpremote fs ls` text output, and
# status parses `mpremote fs sha256sum`, so the version must not float.
MPREMOTE_VERSION = "1.29.0"

# Raspberry Pi USB vendor ID (Pico / Pico 2 running MicroPython).
RASPBERRY_PI_USB_VID = 0x2E8A

# Application entry points. The pin map is part of the profile because
# deploying firmware with the wrong wiring silently kills the RS-485 link.
PROFILES = {
    "socket": {
        "main": Path("main.py"),
        "summary": "Pico 2 socket (outdoor box): RS-485 RTS GP2, BME688 I2C1 GP6/GP7, "
                   "DHT11 not installed, register map 0x0102. NOT yet validated on hardware.",
    },
    "bench": {
        "main": Path("profiles") / "bench" / "main.py",
        "summary": "P4-08 RS-485 bench (validated): RTS GP6, BME688 I2C1 GP2/GP3, "
                   "DHT11 GP15, register map 0x0101.",
    },
    "uart": {
        "main": Path("profiles") / "uart" / "main.py",
        "summary": "P3 direct TTL UART bench (historical): no RS-485 transceiver.",
    },
}


class ProvisionError(RuntimeError):
    """A recoverable provisioning error."""


class EmptyDeviceError(ProvisionError):
    """The Pico filesystem was listed successfully and holds no files."""


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
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
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


# ----------------------------------------------------------------------
# Low-level helpers
# ----------------------------------------------------------------------

def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_pico_ports() -> list[str]:
    """Serial ports of attached Raspberry Pi USB devices (Pico / Pico 2)."""
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise ProvisionError("pyserial unavailable; install requirements.txt") from exc
    return sorted(
        (p.device for p in list_ports.comports() if getattr(p, "vid", None) == RASPBERRY_PI_USB_VID),
        key=str.upper,
    )


def resolve_port(port: str | None, interactive: bool = False) -> str:
    """Find the Pico's port once and use that same port for every call.

    mpremote's own `auto` is never passed on: it is re-resolved on each
    call, so with two boards a backup and a deploy could hit different
    devices. Here the port is detected once, from the Raspberry Pi USB ID.
    """
    if port and port.strip().lower() != "auto":
        return port
    picos = find_pico_ports()
    if len(picos) == 1:
        print(f"Pico detected on {picos[0]}")
        return picos[0]
    if interactive and sys.stdin.isatty():
        return interactive_port()
    if not picos:
        raise ProvisionError(
            "No Pico found on USB. Plug it in and close Thonny or any serial monitor, "
            "or give the port with --port COMx."
        )
    raise ProvisionError(
        f"Several Picos are connected ({', '.join(picos)}); choose one with --port COMx."
    )


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


def installed_mpremote_version() -> str | None:
    try:
        return metadata.version("mpremote")
    except metadata.PackageNotFoundError:
        return None


def ensure_mpremote() -> None:
    found = installed_mpremote_version()
    if found is None:
        raise ProvisionError(
            f"Install mpremote: python -m pip install mpremote=={MPREMOTE_VERSION}"
        )
    if found != MPREMOTE_VERSION:
        raise ProvisionError(
            f"mpremote {found} found, {MPREMOTE_VERSION} required (its output format is "
            f"parsed). Run: python -m pip install mpremote=={MPREMOTE_VERSION}"
        )


def bootstrap_mpremote() -> None:
    """The interactive one-command workflow installs its pinned host dependency."""
    try:
        ensure_mpremote()
        return
    except ProvisionError as exc:
        print(f"{exc}\nInstalling mpremote=={MPREMOTE_VERSION} ...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", f"mpremote=={MPREMOTE_VERSION}"],
        check=False,
    )
    if result.returncode:
        raise ProvisionError(
            "Failed to install mpremote. Install it once with: "
            f"python -m pip install mpremote=={MPREMOTE_VERSION}"
        )
    ensure_mpremote()


# ----------------------------------------------------------------------
# Git traceability
# ----------------------------------------------------------------------

def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO), *args],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    # Not stripped: `git status --porcelain` lines start with a space.
    return result.stdout if result.returncode == 0 else None


def repo_relative(path: Path) -> str | None:
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return None


def git_snapshot(sources: list[Path]) -> dict:
    """Commit, branch and the state of the files being sent to the Pico."""
    commit = (_git("rev-parse", "HEAD") or "").strip() or None
    info = {
        "commit": commit,
        "branch": (_git("rev-parse", "--abbrev-ref", "HEAD") or "").strip() or None,
        "modified_files": [],
        "untracked_files": [],
        "outside_repo_files": [],
    }
    rel_paths = []
    for source in sources:
        rel = repo_relative(source)
        if rel is None:
            info["outside_repo_files"].append(str(source))
        else:
            rel_paths.append(rel)
    if commit and rel_paths:
        status = _git("status", "--porcelain", "--untracked-files=all", "--", *rel_paths) or ""
        for line in status.splitlines():
            if len(line) < 4:
                continue
            code, name = line[:2], line[3:].strip().strip('"')
            (info["untracked_files"] if code == "??" else info["modified_files"]).append(name)
    info["clean"] = bool(commit) and not (
        info["modified_files"] or info["untracked_files"] or info["outside_repo_files"]
    )
    return info


def describe_git(info: dict) -> str:
    if not info.get("commit"):
        return "Git: unavailable (deployment recorded without a commit)"
    text = f"Git: {info['commit'][:12]} on {info.get('branch')}"
    if info["clean"]:
        return text + " (all sent files committed)"
    extra = info["modified_files"] + info["untracked_files"] + info["outside_repo_files"]
    return text + " — NOT committed: " + ", ".join(extra)


def append_log(entry: dict) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"WARNING: could not append to host log {LOG_FILE}: {exc}")


# ----------------------------------------------------------------------
# Device filesystem
# ----------------------------------------------------------------------

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


def device_files(port: str) -> list[tuple[str, int]]:
    """Every file on the Pico as (path, size); may be empty."""
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
    return sorted(files)


def device_inventory(port: str) -> list[tuple[str, int]]:
    """Enumerate and size-check Pico files before overwriting anything."""
    files = device_files(port)
    # Fail closed: an empty listing is only accepted by an explicit decision.
    if not files:
        raise EmptyDeviceError("The Pico filesystem is empty: there is nothing to back up.")
    return files


def backup(port: str, destination: Path, only: list[str] | None = None) -> Path:
    """Copy Pico files to the PC and check their sizes.

    ``only`` limits the backup to those device paths (used by ``push``).
    """
    inventory = device_inventory(port) if only is None else [
        item for item in device_files(port) if item[0] in only
    ]
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


def device_sha256(port: str, paths: list[str]) -> dict[str, str]:
    """SHA-256 of Pico files, computed on the Pico by `mpremote fs sha256sum`."""
    if not paths:
        return {}
    output = mpremote(port, "fs", "sha256sum", *[":/" + p for p in paths], timeout=120)
    digests: dict[str, str] = {}
    current = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("sha256sum :"):
            current = line[len("sha256sum :"):].lstrip("/")
        elif re.fullmatch(r"[0-9a-f]{64}", line) and current is not None:
            digests[current] = line
            current = None
        elif line:
            raise ProvisionError(f"Unrecognized mpremote sha256sum output: {line!r}")
    missing = [p for p in paths if p not in digests]
    if missing:
        raise ProvisionError("No SHA-256 reported for: " + ", ".join(missing))
    return digests


def verify_copy(port: str, source: Path, remote: str, scratch: Path) -> None:
    local = scratch / (str(len(list(scratch.iterdir()))) + "-" + source.name)
    mpremote(port, "fs", "cp", ":/" + remote, str(local), timeout=90)
    if file_sha(local) != file_sha(source):
        raise ProvisionError(f"SHA256 read-back mismatch: {remote}")


def read_manifest(port: str) -> dict | None:
    """The deployment record on the Pico, or None when there is none."""
    names = [name for name, _ in device_files(port)]
    if MANIFEST_NAME not in names:
        return None
    with tempfile.TemporaryDirectory(prefix="weather-station-manifest-") as td:
        local = Path(td) / MANIFEST_NAME
        mpremote(port, "fs", "cp", ":/" + MANIFEST_NAME, str(local), timeout=30)
        try:
            manifest = json.loads(local.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProvisionError(f"/{MANIFEST_NAME} on the Pico is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ProvisionError(f"/{MANIFEST_NAME} has an unknown schema")
    return manifest


def write_manifest(port: str, manifest: dict, scratch: Path) -> None:
    local = scratch / MANIFEST_NAME
    local.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    mpremote(port, "fs", "cp", str(local), ":/" + MANIFEST_NAME, timeout=30)
    verify_copy(port, local, MANIFEST_NAME, scratch)


# ----------------------------------------------------------------------
# Host-side artifacts
# ----------------------------------------------------------------------

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


def cache_uf2_best_effort(offline: bool) -> None:
    """The UF2 is a recovery copy for the PC; deploying never needs it."""
    try:
        download_firmware(offline)
    except (ProvisionError, OSError) as exc:
        print(f"WARNING: UF2 recovery copy not available ({exc}). "
              "Deployment continues: the UF2 is never flashed. "
              "Cache it later with: python tools/picoctl.py firmware")


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
                metadata_json = json.load(response)
            matches = [
                item for item in metadata_json["urls"]
                if item["filename"] == MODBUS_ARCHIVE_NAME
            ]
            if len(matches) != 1 or matches[0]["digests"]["sha256"] != EXPECTED_SHA256:
                raise ProvisionError("Unexpected PyPI metadata; refusing download")
            with urlopen(matches[0]["url"], timeout=30) as response:
                payload = response.read(2_000_000)
            if hashlib.sha256(payload).hexdigest() != EXPECTED_SHA256:
                raise ProvisionError("Downloaded archive SHA256 mismatch")
            archive.parent.mkdir(parents=True, exist_ok=True)
            partial = archive.with_suffix(archive.suffix + ".partial")
            partial.write_bytes(payload)
            os.replace(partial, archive)
    if file_sha(archive) != EXPECTED_SHA256:
        raise ProvisionError(
            f"micropython-modbus {RELEASE} SHA256 mismatch: {archive}. "
            "Delete that file and retry."
        )
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


# ----------------------------------------------------------------------
# Deployment
# ----------------------------------------------------------------------

def profile_main(profile: str) -> Path:
    if profile not in PROFILES:
        raise ProvisionError(f"Unknown profile {profile!r}; choose one of {', '.join(PROFILES)}")
    return PICO / PROFILES[profile]["main"]


def source_files(profile: str, include_tests: bool, main_override: Path | None) -> list[tuple[Path, str]]:
    files = [(COMMON / name, name) for name in (*CORE, *EXTRA_MODULES)]
    if include_tests:
        files.append((PICO / "common_interface_test.py", "common_interface_test.py"))
    entry = main_override.expanduser().resolve() if main_override else profile_main(profile)
    files.append((entry, "main.py"))
    missing = [str(source) for source, _ in files if not source.is_file()]
    if missing:
        raise ProvisionError("Missing source files:\n" + "\n".join(missing))
    return files


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


def default_backup_path(suffix: str = "") -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return BACKUP_ROOT / (stamp + suffix)


def file_record(source: Path) -> dict:
    return {
        "sha256": file_sha(source),
        "size": source.stat().st_size,
        "source": repo_relative(source) or str(source),
    }


def deploy(args: argparse.Namespace) -> None:
    files = source_files(args.profile, args.include_tests, args.main)
    git_info = git_snapshot([source for source, _ in files])
    print(f"Profile: {args.profile} — {PROFILES[args.profile]['summary']}")
    if args.main:
        print("  main.py override: the profile's entry point is replaced by --main.")
    print(f"MicroPython UF2: unchanged | umodbus: {RELEASE} | {describe_git(git_info)}")
    for source, target in files:
        print(f"  {repo_relative(source) or source} -> /{target}")
    print("  micropython-modbus (SHA256-pinned) -> /lib/umodbus/")
    print(f"  deployment record -> /{MANIFEST_NAME} and {LOG_FILE.relative_to(REPO).as_posix()}")
    if not args.apply:
        print("DRY RUN: add --apply to deploy.")
        return

    bootstrap_mpremote()
    port = resolve_port(args.port)
    cache_uf2_best_effort(args.offline)
    doctor(port)

    backup_path = None
    previous_files: list[tuple[str, int]] = []
    if args.skip_backup:
        print("WARNING: backup explicitly skipped; use only on a known empty Pico.")
        previous_files = device_files(port)
    else:
        dest = args.backup_dir or default_backup_path()
        if dest.exists():
            raise ProvisionError(f"Backup directory already exists: {dest}")
        try:
            backup_path = backup(port, dest)
            inventory = json.loads((dest / "inventory.json").read_text(encoding="utf-8"))
            previous_files = [(item["path"], item["size"]) for item in inventory["device_files"]]
        except EmptyDeviceError:
            # The listing itself succeeded ("ls :" heading seen), so this is
            # a genuinely empty board, not a failed read: nothing to lose.
            print("The Pico has no files (new board): nothing to back up.")

    archive = acquire_modbus(args.offline, args.archive)
    records: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="weather-station-pico-") as td:
        staging = Path(td)
        vendor = staging / "vendor"
        vendor.mkdir()
        vendor_files = extract_umodbus(archive, vendor)

        mpremote(port, "exec", "import os\ntry:\n os.mkdir('/lib')\nexcept OSError:\n pass")
        mpremote(port, "fs", "cp", "-r", str(vendor / "umodbus"), ":/lib/", timeout=120)

        scratch = staging / "verification"
        scratch.mkdir()
        for source in vendor_files:
            rel = "lib/umodbus/" + str(source.relative_to(vendor / "umodbus")).replace(os.sep, "/")
            verify_copy(port, source, rel, scratch)
            records[rel] = {
                "sha256": file_sha(source), "size": source.stat().st_size,
                "source": f"{MODBUS_ARCHIVE_NAME} (SHA256-pinned)",
            }

        # Modules first, boot entry point LAST: avoids a partially installed app.
        for source, target in files:
            if target == "main.py":
                continue
            mpremote(port, "fs", "cp", str(source), ":/" + target, timeout=90)
            verify_copy(port, source, target, scratch)
            records[target] = file_record(source)
        main_source = next(source for source, target in files if target == "main.py")
        mpremote(port, "fs", "cp", str(main_source), ":/main.py", timeout=90)
        verify_copy(port, main_source, "main.py", scratch)
        records["main.py"] = file_record(main_source)

        postcheck = mpremote(
            port, "exec",
            "from umodbus.serial import ModbusRTU\n"
            "import breakout_bme68x, pimoroni_i2c\n"
            "import pico_modbus_patch, bme688_sensor, dht11_sensor, environmental_reading\n"
            "import bme688_modbus_extension\n"
            "print('WS_PROVISION_VERIFY_OK')",
        )
        if "WS_PROVISION_VERIFY_OK" not in postcheck:
            raise ProvisionError("Post-install import verification did not pass")

        manifest = {
            "schema": MANIFEST_SCHEMA,
            "kind": "deploy",
            "deployed_at_utc": utc_now(),
            "host": platform.node(),
            "profile": args.profile,
            "main_override": bool(args.main),
            "git": git_info,
            "umodbus": RELEASE,
            "expected_uf2": FIRMWARE_NAME,
            "files": records,
            "pushes": [],
        }
        write_manifest(port, manifest, scratch)

    append_log({
        "event": "deploy", "at_utc": manifest["deployed_at_utc"], "port": port,
        "profile": args.profile, "git": git_info,
        "backup": str(backup_path) if backup_path else None,
        "files": {name: rec["sha256"] for name, rec in records.items()},
    })
    print("Deployment verified: file SHA256 read-back AND on-device imports OK.")
    print(f"Deployment recorded in /{MANIFEST_NAME} on the Pico and in {LOG_FILE}.")
    if not git_info["clean"]:
        print("WARNING: some deployed files are not committed; the record holds their SHA-256, "
              "but the Git commit alone does not reproduce this deployment.")

    stale = sorted(
        name for name, _ in previous_files
        if name not in records and name != MANIFEST_NAME
    )
    if stale:
        print("INFO: files on the Pico that this deployment does not manage (left untouched):")
        for name in stale:
            print(f"  /{name}")

    if args.reset:
        print("Resetting Pico; main.py will start if the required wiring is ready.")
        mpremote(port, "reset")
    else:
        print("No reset performed; reconnect/reset Pico when sensors and RS-485 wiring are ready.")


def push(args: argparse.Namespace) -> None:
    """Copy individual files to the Pico root, with backup, read-back and a record."""
    sources = [Path(item).expanduser().resolve() for item in args.files]
    if args.as_name and len(sources) != 1:
        raise ProvisionError("--as can only be used with a single file")
    targets = []
    for source in sources:
        if not source.is_file():
            raise ProvisionError(f"File not found: {source}")
        name = args.as_name or source.name
        if "/" in name or "\\" in name or name in ("", ".", "..", MANIFEST_NAME):
            raise ProvisionError(f"Invalid Pico file name: {name!r}")
        targets.append((source, name))
    if len({name for _, name in targets}) != len(targets):
        raise ProvisionError("Two files would be written to the same Pico path")

    git_info = git_snapshot(sources)
    print(describe_git(git_info))
    for source, name in targets:
        print(f"  {repo_relative(source) or source} -> /{name}")
    if args.dry_run:
        print("DRY RUN: nothing was copied.")
        return

    bootstrap_mpremote()
    port = resolve_port(args.port, interactive=True)
    doctor(port)
    manifest = read_manifest(port)

    names = [name for _, name in targets]
    existing = [n for n, _ in device_files(port) if n in names]
    backup_path = None
    if existing:
        backup_path = backup(port, default_backup_path("-push"), only=existing)

    pushed_at = utc_now()
    with tempfile.TemporaryDirectory(prefix="weather-station-push-") as td:
        scratch = Path(td)
        # A new main.py goes last, so its imports are already in place.
        for source, name in sorted(targets, key=lambda item: item[1] == "main.py"):
            mpremote(port, "fs", "cp", str(source), ":/" + name, timeout=90)
            verify_copy(port, source, name, scratch)

        if manifest is None:
            print(f"INFO: no /{MANIFEST_NAME} on the Pico yet; starting a new record.")
            manifest = {
                "schema": MANIFEST_SCHEMA, "kind": "push", "deployed_at_utc": None,
                "host": platform.node(), "profile": None, "main_override": False,
                "git": None, "umodbus": None, "expected_uf2": FIRMWARE_NAME,
                "files": {}, "pushes": [],
            }
        for source, name in targets:
            record = file_record(source)
            record["pushed_at_utc"] = pushed_at
            manifest["files"][name] = record
        manifest["pushes"].append({
            "at_utc": pushed_at, "host": platform.node(), "git": git_info,
            "files": {name: manifest["files"][name]["sha256"] for _, name in targets},
            "note": args.note,
        })
        write_manifest(port, manifest, scratch)

    append_log({
        "event": "push", "at_utc": pushed_at, "port": port, "git": git_info,
        "backup": str(backup_path) if backup_path else None, "note": args.note,
        "files": {name: file_sha(source) for source, name in targets},
    })
    print(f"Push verified (SHA256 read-back) and recorded in /{MANIFEST_NAME}.")
    if args.reset:
        mpremote(port, "reset")
        print("Pico reset.")
    else:
        print("No reset performed.")


def status(args: argparse.Namespace) -> int:
    """Compare the Pico with its deployment record and with the repository."""
    bootstrap_mpremote()
    port = resolve_port(args.port, interactive=True)
    manifest = read_manifest(port)
    on_device = [name for name, _ in device_files(port)]

    if manifest is None:
        print(f"No /{MANIFEST_NAME} on the Pico: its files were not deployed by this tool "
              "(or were deployed before deployment records existed).")
        print("Files on the Pico:")
        for name in on_device:
            print(f"  /{name}")
        return 1

    git = manifest.get("git") or {}
    print(f"Deployed:  {manifest.get('deployed_at_utc')} from {manifest.get('host')} "
          f"| profile {manifest.get('profile')}"
          + (" (main.py override)" if manifest.get("main_override") else ""))
    if git:
        print(f"Commit:    {git.get('commit')} on {git.get('branch')} "
              + ("(clean)" if git.get("clean") else "(had uncommitted files)"))
    for item in manifest.get("pushes", []):
        print(f"Pushed:    {item['at_utc']} " + ", ".join(item["files"])
              + (f" — {item['note']}" if item.get("note") else ""))

    recorded = manifest.get("files", {})
    present = [name for name in recorded if name in on_device]
    digests = device_sha256(port, present)
    in_sync = True
    print("\nPico vs deployment record:")
    for name, record in sorted(recorded.items()):
        if name not in on_device:
            state, in_sync = "MISSING on the Pico", False
        elif digests[name] != record["sha256"]:
            state, in_sync = "CHANGED since recorded (edited by hand?)", False
        else:
            state = "ok"
        print(f"  /{name:40s} {state}")
    unmanaged = sorted(set(on_device) - set(recorded) - {MANIFEST_NAME})
    for name in unmanaged:
        print(f"  /{name:40s} not in the record")

    print("\nRecord vs current repository:")
    behind = False
    for name, record in sorted(recorded.items()):
        if name.startswith("lib/umodbus/"):
            continue  # pinned by the dependency lock, not by a repository file
        source = Path(record["source"])
        if not source.is_absolute():
            source = REPO / source
        if not source.is_file():
            print(f"  /{name:40s} source not found: {record['source']}")
            behind = True
        elif file_sha(source) != record["sha256"]:
            print(f"  /{name:40s} repository copy differs: {record['source']}")
            behind = True
    if not behind:
        print("  every recorded source matches the repository working tree")

    print("\nRESULT: " + ("Pico matches its deployment record." if in_sync
                          else "DRIFT — the Pico does not match its deployment record."))
    return 0 if in_sync else 1


# ----------------------------------------------------------------------
# Interactive one-command installer
# ----------------------------------------------------------------------

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
    picos = [port for port in ports if getattr(port, "vid", None) == RASPBERRY_PI_USB_VID]
    for index, port in enumerate(ports, 1):
        details = port.description or "Unknown device"
        marker = "  <- Raspberry Pi device" if port in picos else ""
        print(f"  [{index}] {port.device} — {details}{marker}")
    default = picos[0].device if len(picos) == 1 else None
    if default:
        print(f"\nOne Raspberry Pi device found: {default}. Press Enter to use it.")
    else:
        print("\nSelect the Pico by number, or type its COM port.")
    while True:
        answer = input(f"Pico port [{default}]: " if default else "Pico port: ").strip()
        if not answer and default:
            return default
        if answer.isdigit():
            selected = int(answer)
            if 1 <= selected <= len(ports):
                return ports[selected - 1].device
        for port in ports:
            if answer and answer.upper() == port.device.upper():
                return port.device
        print("Invalid selection. Choose a listed number or COM port.")


def interactive_profile() -> str:
    """No default: the profile must match how the Pico is wired."""
    names = list(PROFILES)
    print("\nWhich wiring is this Pico connected to?")
    for index, name in enumerate(names, 1):
        print(f"  [{index}] {name}: {PROFILES[name]['summary']}")
    while True:
        answer = input("Profile (number or name): ").strip().lower()
        if answer.isdigit() and 1 <= int(answer) <= len(names):
            return names[int(answer) - 1]
        if answer in PROFILES:
            return answer
        print("Choose one of the listed profiles. There is no default.")


def ask_yes(question: str) -> bool:
    return input(f"{question} [y/N]: ").strip().lower() in ("y", "yes", "s", "si", "sí")


def detect_profile(port: str) -> tuple[str | None, str]:
    """Which profile is on this Pico now, so a reinstall needs no answer.

    1. The deployment record (/ws_deploy.json), unless it was a --main override.
    2. Otherwise, the SHA-256 of /main.py against each profile's main.py.
    """
    manifest = read_manifest(port)
    if manifest and manifest.get("profile") in PROFILES and not manifest.get("main_override"):
        return manifest["profile"], "from the deployment record on the Pico"
    if "main.py" in [name for name, _ in device_files(port)]:
        on_pico = device_sha256(port, ["main.py"])["main.py"]
        for name in PROFILES:
            if file_sha(profile_main(name)) == on_pico:
                return name, f"/main.py on the Pico is the '{name}' firmware"
        return None, "/main.py on the Pico does not match any profile"
    return None, "the Pico has no main.py"


def interactive_install(
    port: str | None = None,
    profile: str | None = None,
    reset: bool = False,
    dry_run: bool = False,
    assume_yes: bool = False,
) -> int:
    """Full setup in one command: python tools/picoctl.py

    With one Pico attached that was installed before, it asks nothing:
    port and profile are detected. It only asks when it cannot know
    (several Picos, or a Pico whose firmware it does not recognize), and
    before switching a Pico to a different wiring profile.
    """
    print("=" * 55)
    print("  Weather Station | Pico 2 — One-command installer")
    print("=" * 55)
    bootstrap_mpremote()
    port = resolve_port(port, interactive=True)
    detected, reason = detect_profile(port)

    if profile is None:
        if detected:
            profile = detected
            print(f"Profile: {profile} ({reason})")
        elif sys.stdin.isatty():
            print(f"Profile not detected: {reason}.")
            profile = interactive_profile()
        else:
            raise ProvisionError(f"Profile not detected ({reason}); give it with --profile.")
    elif detected and detected != profile:
        print(f"WARNING: the Pico has the '{detected}' firmware and you asked for '{profile}'.")
        print(f"  {profile}: {PROFILES[profile]['summary']}")
        print("  Install it only if the Pico is already wired for it; otherwise RS-485 stops answering.")
        if not assume_yes and not (sys.stdin.isatty() and ask_yes("Is the Pico wired for it?")):
            raise ProvisionError("Profile change not confirmed; nothing was written.")

    argv = ["deploy", "--port", port, "--profile", profile]
    if not dry_run:
        argv.append("--apply")
    if reset:
        argv.append("--reset")
    deploy(parser().parse_args(argv))
    if dry_run:
        return 0
    print("\nSUCCESS — Pico application and umodbus are installed and verified.")
    print("UF2 archive retained on your PC. No weather-core setup is necessary.")
    if not reset:
        print("Reset/power-cycle only when the sensors and RS-485 wiring are ready.")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Pico 2 provisioning for Weather Station. Without a command it installs "
                    "the firmware, detecting the Pico's port and its current profile.",
    )
    # Distinct dests: argparse lets subcommand defaults overwrite top-level values.
    p.add_argument("--port", dest="install_port", metavar="COMx", help="Pico port, only if it cannot be detected")
    p.add_argument("--profile", dest="install_profile", choices=tuple(PROFILES), metavar="PROFILE",
                   help="Install this profile instead of the one detected on the Pico")
    p.add_argument("--reset", dest="install_reset", action="store_true",
                   help="Start main.py after installing")
    p.add_argument("--dry-run", dest="install_dry_run", action="store_true",
                   help="Show the plan without writing")
    p.add_argument("--yes", dest="install_yes", action="store_true",
                   help="Do not ask before changing the profile (non-interactive use)")
    sub = p.add_subparsers(dest="command")
    port_help = "USB serial port of the Pico, e.g. COM12; detected automatically when omitted"

    s = sub.add_parser("doctor", help="Check the Pico model and drivers")
    s.add_argument("--port", help=port_help)

    s = sub.add_parser("firmware", help="Cache the pinned UF2 on the PC (never flashed)")
    s.add_argument("--offline", action="store_true", help="Verify local UF2 cache without network")

    s = sub.add_parser("backup", help="Copy every Pico file to the PC")
    s.add_argument("--port", help=port_help)
    s.add_argument("--destination", type=Path)

    s = sub.add_parser("deploy", help="Install a firmware profile (dry run without --apply)")
    s.add_argument("--port", help=port_help)
    s.add_argument("--profile", choices=tuple(PROFILES), required=True,
                   help="; ".join(f"{name}: {info['summary']}" for name, info in PROFILES.items()))
    s.add_argument("--main", type=Path, help="Use exact local main.py instead of the profile's")
    s.add_argument("--include-tests", action="store_true")
    s.add_argument("--apply", action="store_true", help="Actually write to the Pico")
    s.add_argument("--skip-backup", action="store_true", help="Only for a known empty new board")
    s.add_argument("--backup-dir", type=Path)
    s.add_argument("--offline", action="store_true", help="Only use cached UF2/sdist")
    s.add_argument("--archive", type=Path, help="Local SHA256-verified micropython-modbus tar.gz")
    s.add_argument("--reset", action="store_true", help="Run main.py after deployment")

    s = sub.add_parser("push", help="Copy individual files to the Pico root, recorded and verified")
    s.add_argument("files", nargs="+", help="Local files, e.g. experiments/.../script.py")
    s.add_argument("--port", help=port_help)
    s.add_argument("--as", dest="as_name", help="Name on the Pico for a single file, e.g. main.py")
    s.add_argument("--note", help="Short reason stored in the record, e.g. 'P4 long run test'")
    s.add_argument("--dry-run", action="store_true", help="Show what would be copied, write nothing")
    s.add_argument("--reset", action="store_true", help="Reset the Pico afterwards")

    s = sub.add_parser("status", help="Compare the Pico with its deployment record and the repo")
    s.add_argument("--port", help=port_help)
    return p


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    try:
        args = parser().parse_args(argv)
        if args.command is None:
            return interactive_install(
                port=args.install_port, profile=args.install_profile,
                reset=args.install_reset, dry_run=args.install_dry_run,
                assume_yes=args.install_yes,
            )
        if args.command == "doctor":
            bootstrap_mpremote()
            doctor(resolve_port(args.port, interactive=True))
        elif args.command == "firmware":
            download_firmware(args.offline)
        elif args.command == "backup":
            bootstrap_mpremote()
            port = resolve_port(args.port, interactive=True)
            path = args.destination or default_backup_path()
            if path.exists():
                raise ProvisionError(f"Backup directory exists: {path}")
            backup(port, path)
        elif args.command == "push":
            push(args)
        elif args.command == "status":
            return status(args)
        else:
            deploy(args)
    except (ProvisionError, OSError, ValueError, KeyError, EOFError, KeyboardInterrupt) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
