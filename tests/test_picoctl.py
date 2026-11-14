"""Host-side provisioning regression tests; no connected Pico is required."""
import contextlib
import io
import json
import hashlib
import importlib.util
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "picoctl.py"
SPEC = importlib.util.spec_from_file_location("picoctl", SCRIPT)
picoctl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(picoctl)


class PicoctlTests(unittest.TestCase):
    def test_dependency_lock_is_canonical(self):
        lock = picoctl.load_dependency_lock()
        self.assertEqual(lock["schema_version"], 1)
        self.assertEqual(lock["firmware"]["sha256"], picoctl.FIRMWARE_SHA256)
        self.assertEqual(lock["modbus"]["sha256"], picoctl.EXPECTED_SHA256)
        self.assertEqual(lock["modbus"]["version"], picoctl.RELEASE)
        self.assertEqual(lock["modbus"]["filename"], picoctl.MODBUS_ARCHIVE_NAME)
        self.assertEqual(lock["firmware"]["action"], "download_only")

    def test_dependency_lock_rejects_changed_sha256(self):
        lock = json.loads(picoctl.LOCK_FILE.read_text(encoding="utf-8"))
        lock["firmware"]["sha256"] = "abc"
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td) / "bad.lock.json"
            candidate.write_text(json.dumps(lock), encoding="utf-8")
            with self.assertRaises(picoctl.ProvisionError):
                picoctl.load_dependency_lock(candidate)

    def test_dependency_lock_rejects_untrusted_url(self):
        lock = json.loads(picoctl.LOCK_FILE.read_text(encoding="utf-8"))
        lock["firmware"]["url"] = "https://example.com/firmware.uf2"
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td) / "bad.lock.json"
            candidate.write_text(json.dumps(lock), encoding="utf-8")
            with self.assertRaises(picoctl.ProvisionError):
                picoctl.load_dependency_lock(candidate)

    def test_dependency_lock_rejects_inconsistent_version(self):
        lock = json.loads(picoctl.LOCK_FILE.read_text(encoding="utf-8"))
        lock["modbus"]["version"] = "99.99.99"
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td) / "bad.lock.json"
            candidate.write_text(json.dumps(lock), encoding="utf-8")
            with self.assertRaises(picoctl.ProvisionError):
                picoctl.load_dependency_lock(candidate)

    def test_dependency_lock_rejects_firmware_flash_policy(self):
        lock = json.loads(picoctl.LOCK_FILE.read_text(encoding="utf-8"))
        lock["firmware"]["action"] = "flash"
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td) / "bad.lock.json"
            candidate.write_text(json.dumps(lock), encoding="utf-8")
            with self.assertRaises(picoctl.ProvisionError):
                picoctl.load_dependency_lock(candidate)

    def test_firmware_pin_is_defined_by_lock_not_duplicated(self):
        locked = picoctl.load_dependency_lock()["firmware"]
        self.assertEqual(picoctl.FIRMWARE_NAME, locked["filename"])
        self.assertEqual(picoctl.FIRMWARE_URL, locked["url"])
        self.assertEqual(picoctl.FIRMWARE_SHA256, locked["sha256"])
        self.assertEqual(picoctl.FIRMWARE_SIZE, locked["size"])
        self.assertTrue(picoctl.FIRMWARE_NAME.endswith("-micropython.uf2"))
        self.assertIn("pimoroni-pico-rp2350/releases/download/", picoctl.FIRMWARE_URL)

    def test_firmware_download_to_pc_only_and_cache_reuse(self):
        payload = b"test-firmware-UF2"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            with mock.patch.object(picoctl, "FIRMWARE_DIR", path), mock.patch.object(
                picoctl, "FIRMWARE_SIZE", len(payload)
            ), mock.patch.object(
                picoctl, "FIRMWARE_SHA256", hashlib.sha256(payload).hexdigest()
            ), mock.patch.object(
                picoctl, "urlopen", return_value=io.BytesIO(payload)
            ) as get, mock.patch.object(
                picoctl, "mpremote", side_effect=AssertionError("Never flash firmware")
            ):
                saved = picoctl.download_firmware()
                self.assertEqual(saved.read_bytes(), payload)
                self.assertEqual(saved, path / picoctl.FIRMWARE_NAME)
                self.assertEqual(picoctl.download_firmware(offline=True), saved)
                self.assertEqual(get.call_count, 1)

    def test_firmware_offline_uncached_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(picoctl, "FIRMWARE_DIR", Path(tmp)), mock.patch.object(
                picoctl, "urlopen", side_effect=AssertionError("Offline must not fetch")
            ):
                with self.assertRaises(picoctl.ProvisionError):
                    picoctl.download_firmware(offline=True)

    def test_firmware_corrupt_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / picoctl.FIRMWARE_NAME).write_bytes(b"corrupt")
            with mock.patch.object(picoctl, "FIRMWARE_DIR", path), mock.patch.object(
                picoctl, "urlopen", side_effect=AssertionError("Do not overwrite silently")
            ):
                with self.assertRaises(picoctl.ProvisionError):
                    picoctl.download_firmware()

    def test_package_release_and_digest_are_locked(self):
        locked = picoctl.load_dependency_lock()["modbus"]
        self.assertEqual(picoctl.RELEASE, locked["version"])
        self.assertEqual(picoctl.EXPECTED_SHA256, locked["sha256"])
        self.assertEqual(picoctl.MODBUS_ARCHIVE_NAME, locked["filename"])
        self.assertEqual(len(picoctl.EXPECTED_SHA256), 64)

    def test_dry_run_never_connects_to_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            pico = Path(tmp)
            common = pico
            common.mkdir(exist_ok=True)
            for name in (*picoctl.CORE, *picoctl.EXTRA_MODULES):
                (common / name).write_text("# fixture", encoding="utf-8")
            (pico / "main.py").write_text("# fixture", encoding="utf-8")
            with mock.patch.object(picoctl, "PICO", pico), mock.patch.object(
                picoctl, "COMMON", common
            ), mock.patch.object(picoctl, "mpremote", side_effect=AssertionError), mock.patch.object(
                picoctl, "download_firmware", side_effect=AssertionError
            ):
                args = picoctl.parser().parse_args(["deploy", "--profile", "socket"])
                picoctl.deploy(args)

    def test_extractor_accepts_only_module_python_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive = base / "test.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                members = [
                    ("package/umodbus/__init__.py", b""),
                    ("package/umodbus/serial.py", b"print(123)"),
                    ("package/README.txt", b"ignore"),
                    ("package/umodbus/../../../escape.py", b"bad"),
                ]
                for name, contents in members:
                    item = tarfile.TarInfo(name)
                    item.size = len(contents)
                    tar.addfile(item, io.BytesIO(contents))
            results = picoctl.extract_umodbus(archive, base / "vendor")
            self.assertEqual(sorted(f.name for f in results), ["__init__.py", "serial.py"])
            self.assertFalse((base / "escape.py").exists())

    def test_rejects_unverified_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.tar.gz"
            archive.write_bytes(b"wrong")
            with self.assertRaises(picoctl.ProvisionError):
                picoctl.acquire_modbus(True, archive)

    def test_inventory_reads_real_fs_ls_format_including_lib(self):
        def fake_remote(port, *args, **kwargs):
            self.assertEqual(port, "COM12")
            if args == ("fs", "ls", ":"):
                return "ls :\n  4072 bme688_modbus_extension.py\n  0 lib/\n  31556 main.py\n"
            if args == ("fs", "ls", ":/lib"):
                return "ls :/lib\n  0 umodbus/\n"
            if args == ("fs", "ls", ":/lib/umodbus"):
                return "ls :/lib/umodbus\n  5000 serial.py\n  10 __init__.py\n"
            raise AssertionError(args)

        with mock.patch.object(picoctl, "mpremote", side_effect=fake_remote):
            self.assertEqual(
                picoctl.device_inventory("COM12"),
                [
                    ("bme688_modbus_extension.py", 4072),
                    ("lib/umodbus/__init__.py", 10),
                    ("lib/umodbus/serial.py", 5000),
                    ("main.py", 31556),
                ],
            )

    def test_empty_backup_is_fail_closed(self):
        with mock.patch.object(picoctl, "mpremote", return_value="ls :\n"):
            with self.assertRaises(picoctl.EmptyDeviceError):
                picoctl.device_inventory("COM12")

    def test_backup_checks_file_size_and_creates_inventory(self):
        def fake_remote(port, *args, **kwargs):
            if args == ("fs", "ls", ":"):
                return "ls :\n  4 main.py\n"
            if args[:2] == ("fs", "cp"):
                Path(args[-1]).write_bytes(b"test")
                return ""
            raise AssertionError(args)

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "backup"
            with mock.patch.object(picoctl, "mpremote", side_effect=fake_remote):
                picoctl.backup("COM12", target)
            self.assertEqual((target / "main.py").read_bytes(), b"test")
            manifest = __import__("json").loads((target / "inventory.json").read_text())
            self.assertEqual(manifest["device_files"], [{"path": "main.py", "size": 4}])

    def test_backup_refuses_truncated_copy(self):
        def fake_remote(port, *args, **kwargs):
            if args == ("fs", "ls", ":"):
                return "ls :\n  100 main.py\n"
            if args[:2] == ("fs", "cp"):
                Path(args[-1]).write_bytes(b"short")
                return ""
            raise AssertionError(args)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(picoctl, "mpremote", side_effect=fake_remote):
                with self.assertRaises(picoctl.ProvisionError):
                    picoctl.backup("COM12", Path(tmp) / "backup")

    def test_doctor_accepts_missing_modbus_before_install(self):
        output = ("WS_PLATFORM: rp2\n"
                  "WS_MACHINE: Raspberry Pi Pico2 with RP2350\n"
                  "WS_IMPORT: pimoroni_i2c OK\n"
                  "WS_IMPORT: breakout_bme68x OK\n"
                  "WS_IMPORT: umodbus MISSING ImportError('not installed')\n")
        with mock.patch.object(picoctl, "mpremote", return_value=output):
            picoctl.doctor("COM12")

    def test_doctor_rejects_other_rp2_hardware(self):
        output = ("WS_PLATFORM: rp2\n"
                  "WS_MACHINE: Raspberry Pi Pico with RP2040\n"
                  "WS_IMPORT: pimoroni_i2c OK\n"
                  "WS_IMPORT: breakout_bme68x OK\n")
        with mock.patch.object(picoctl, "mpremote", return_value=output):
            with self.assertRaises(picoctl.ProvisionError):
                picoctl.doctor("COM12")

    def test_one_command_install_asks_nothing_when_port_and_profile_are_detected(self):
        with mock.patch.object(picoctl, "bootstrap_mpremote"), mock.patch.object(
            picoctl, "resolve_port", return_value="COM12"
        ), mock.patch.object(
            picoctl, "detect_profile", return_value=("bench", "record")
        ), mock.patch.object(picoctl, "deploy") as deployed, mock.patch(
            "builtins.input", side_effect=AssertionError("must not ask")
        ), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(picoctl.main([]), 0)
        args = deployed.call_args.args[0]
        self.assertEqual(args.port, "COM12")
        self.assertEqual(args.profile, "bench")
        self.assertTrue(args.apply)
        self.assertFalse(args.skip_backup)
        self.assertFalse(args.reset)

    def test_profile_change_needs_confirmation(self):
        common = [mock.patch.object(picoctl, "bootstrap_mpremote"),
                  mock.patch.object(picoctl, "resolve_port", return_value="COM12"),
                  mock.patch.object(picoctl, "detect_profile", return_value=("bench", "record"))]
        for patch in common:
            patch.start()
        try:
            with mock.patch.object(picoctl, "deploy") as deployed, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(picoctl.main(["--profile", "socket"]), 2)  # not a tty, no --yes
                deployed.assert_not_called()
                self.assertEqual(picoctl.main(["--profile", "socket", "--yes"]), 0)
                self.assertEqual(deployed.call_args.args[0].profile, "socket")
        finally:
            for patch in reversed(common):
                patch.stop()

    def test_undetected_profile_without_terminal_is_an_error(self):
        with mock.patch.object(picoctl, "bootstrap_mpremote"), mock.patch.object(
            picoctl, "resolve_port", return_value="COM12"
        ), mock.patch.object(
            picoctl, "detect_profile", return_value=(None, "no main.py")
        ), mock.patch.object(picoctl, "deploy") as deployed, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(picoctl.main([]), 2)
            deployed.assert_not_called()

    def test_select_listed_serial_port(self):
        serial_port = type("Port", (), {"device": "COM12", "description": "Pico 2"})
        with mock.patch("serial.tools.list_ports.comports", return_value=[serial_port]), mock.patch(
            "builtins.input", return_value="1"
        ):
            self.assertEqual(picoctl.interactive_port(), "COM12")

    def test_socket_uses_pico_root_as_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            pico = Path(tmp)
            common = pico
            common.mkdir(exist_ok=True)
            for name in (*picoctl.CORE, *picoctl.EXTRA_MODULES):
                (common / name).write_text("# fixture", encoding="utf-8")
            (pico / "main.py").write_text("# P4 fixture", encoding="utf-8")
            with mock.patch.object(picoctl, "PICO", pico), mock.patch.object(
                picoctl, "COMMON", common
            ):
                files = picoctl.source_files("socket", False, None)
                self.assertEqual(files[-1], (pico / "main.py", "main.py"))

    def test_main_is_last_source_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pico = Path(tmp)
            common = pico
            common.mkdir(exist_ok=True)
            for name in (*picoctl.CORE, *picoctl.EXTRA_MODULES):
                (common / name).write_text("# fixture", encoding="utf-8")
            app = pico / "profiles" / "uart"
            app.mkdir(parents=True)
            (app / "main.py").write_text("# fixture", encoding="utf-8")
            with mock.patch.object(picoctl, "PICO", pico), mock.patch.object(
                picoctl, "COMMON", common
            ):
                files = picoctl.source_files("uart", False, None)
                self.assertEqual(files[-1][1], "main.py")


class FakePico:
    """In-memory Pico filesystem that answers the mpremote calls picoctl makes."""

    DOCTOR = ("WS_PLATFORM: rp2\nWS_MACHINE: Raspberry Pi Pico2 with RP2350\n"
              "WS_IMPORT: pimoroni_i2c OK\nWS_IMPORT: breakout_bme68x OK\n"
              "WS_IMPORT: umodbus MISSING ImportError()\n")

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.resets = 0
        self.writes = []

    def __call__(self, port, *args, **kwargs):
        if args[:2] == ("fs", "ls"):
            base = args[2][1:].strip("/")
            prefix = base + "/" if base else ""
            out, dirs = [f"ls :{'/' + base if base else ''}"], set()
            for name, data in sorted(self.files.items()):
                if not name.startswith(prefix):
                    continue
                rest = name[len(prefix):]
                if "/" in rest:
                    dirs.add(rest.split("/")[0])
                else:
                    out.append(f"{len(data):12} {rest}")
            out += [f"{0:12} {d}/" for d in sorted(dirs)]
            return "\n".join(out) + "\n"
        if args[:3] == ("fs", "cp", "-r"):
            local, dest = Path(args[3]), args[4][1:].strip("/")
            for item in local.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(local.parent).as_posix()
                    self.files[f"{dest}/{rel}"] = item.read_bytes()
            return ""
        if args[:2] == ("fs", "cp"):
            src, dst = args[2], args[3]
            if src.startswith(":"):
                Path(dst).write_bytes(self.files[src[1:].lstrip("/")])
            else:
                name = dst[1:].lstrip("/")
                self.files[name] = Path(src).read_bytes()
                self.writes.append(name)
            return ""
        if args[:2] == ("fs", "sha256sum"):
            lines = []
            for path in args[2:]:
                name = path[1:].lstrip("/")
                lines += [f"sha256sum :/{name}", hashlib.sha256(self.files[name]).hexdigest()]
            return "\n".join(lines) + "\n"
        if args[0] == "exec":
            code = args[1]
            if "WS_PLATFORM" in code:
                return self.DOCTOR
            if "WS_PROVISION_VERIFY_OK" in code:
                return "WS_PROVISION_VERIFY_OK\n"
            return ""
        if args == ("reset",):
            self.resets += 1
            return ""
        raise AssertionError(args)


def make_sdist(path):
    with tarfile.open(path, "w:gz") as tar:
        for name, contents in (("pkg/umodbus/__init__.py", b""), ("pkg/umodbus/serial.py", b"# rtu")):
            item = tarfile.TarInfo(name)
            item.size = len(contents)
            tar.addfile(item, io.BytesIO(contents))
    return path


class TraceabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        pico = self.root / "firmware" / "pico"
        (pico / "profiles" / "bench").mkdir(parents=True)
        (pico / "profiles" / "uart").mkdir(parents=True)
        for name in (*picoctl.CORE, *picoctl.EXTRA_MODULES):
            (pico / name).write_text(f"# {name}\n", encoding="utf-8")
        (pico / "main.py").write_text("# socket main\n", encoding="utf-8")
        (pico / "profiles" / "bench" / "main.py").write_text("# bench main\n", encoding="utf-8")
        (pico / "profiles" / "uart" / "main.py").write_text("# uart main\n", encoding="utf-8")
        archive = make_sdist(self.root / "mm.tar.gz")
        self.patches = [
            mock.patch.object(picoctl, "REPO", self.root),
            mock.patch.object(picoctl, "PICO", pico),
            mock.patch.object(picoctl, "COMMON", pico),
            mock.patch.object(picoctl, "BACKUP_ROOT", self.root / ".pico-backups"),
            mock.patch.object(picoctl, "LOG_FILE", self.root / ".pico-deployments" / "log.jsonl"),
            mock.patch.object(picoctl, "EXPECTED_SHA256", picoctl.file_sha(archive)),
            mock.patch.object(picoctl, "ensure_mpremote"),
            mock.patch.object(picoctl, "cache_uf2_best_effort"),
            mock.patch.object(picoctl, "_git", return_value=None),
        ]
        for patch in self.patches:
            patch.start()
        self.archive = archive
        self.pico_dir = pico

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def deploy(self, fake, *extra, profile="socket"):
        args = picoctl.parser().parse_args(
            ["deploy", "--port", "COM12", "--profile", profile, "--apply",
             "--archive", str(self.archive), *extra]
        )
        with mock.patch.object(picoctl, "mpremote", side_effect=fake), \
                contextlib.redirect_stdout(io.StringIO()):
            picoctl.deploy(args)

    def run_status(self, fake):
        args = picoctl.parser().parse_args(["status", "--port", "COM12"])
        out = io.StringIO()
        with mock.patch.object(picoctl, "mpremote", side_effect=fake), contextlib.redirect_stdout(out):
            code = picoctl.status(args)
        return code, out.getvalue()

    def test_port_is_detected_once_from_raspberry_pi_usb_id(self):
        pico = type("Port", (), {"device": "COM12", "description": "Board", "vid": 0x2E8A})
        pico2 = type("Port", (), {"device": "COM14", "description": "Board", "vid": 0x2E8A})
        other = type("Port", (), {"device": "COM3", "description": "USB serial", "vid": 0x0403})
        out = contextlib.redirect_stdout(io.StringIO())
        with out:
            self.assertEqual(picoctl.resolve_port("COM7"), "COM7")
            with mock.patch("serial.tools.list_ports.comports", return_value=[other, pico]):
                self.assertEqual(picoctl.resolve_port(None), "COM12")
                self.assertEqual(picoctl.resolve_port("auto"), "COM12")
            with mock.patch("serial.tools.list_ports.comports", return_value=[other]):
                with self.assertRaises(picoctl.ProvisionError):
                    picoctl.resolve_port(None)
            with mock.patch("serial.tools.list_ports.comports", return_value=[pico, pico2]):
                with self.assertRaises(picoctl.ProvisionError):
                    picoctl.resolve_port(None)

    def test_profile_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            picoctl.parser().parse_args(["deploy", "--port", "COM12"])

    def test_profiles_resolve_to_distinct_entry_points(self):
        mains = {name: picoctl.source_files(name, False, None)[-1][0] for name in picoctl.PROFILES}
        self.assertEqual(mains["socket"], self.pico_dir / "main.py")
        self.assertEqual(mains["bench"], self.pico_dir / "profiles" / "bench" / "main.py")
        self.assertEqual(len(set(mains.values())), 3)

    def test_empty_pico_installs_without_backup(self):
        fake = FakePico()
        self.deploy(fake)
        self.assertIn("main.py", fake.files)
        self.assertFalse((self.root / ".pico-backups").exists())

    def test_profile_detected_from_record_then_from_main_hash(self):
        fake = FakePico({"boot.py": b"# old"})
        self.deploy(fake, profile="bench")
        with mock.patch.object(picoctl, "mpremote", side_effect=fake):
            self.assertEqual(picoctl.detect_profile("COM12")[0], "bench")
            del fake.files[picoctl.MANIFEST_NAME]
            self.assertEqual(picoctl.detect_profile("COM12")[0], "bench")
            fake.files["main.py"] = b"# something else"
            self.assertIsNone(picoctl.detect_profile("COM12")[0])
            del fake.files["main.py"]
            self.assertIsNone(picoctl.detect_profile("COM12")[0])

    def test_deploy_writes_manifest_last_and_status_is_in_sync(self):
        fake = FakePico({"boot.py": b"# old"})
        self.deploy(fake)
        self.assertEqual(fake.writes[-1], picoctl.MANIFEST_NAME)
        self.assertEqual(fake.writes[-2], "main.py")
        manifest = json.loads(fake.files[picoctl.MANIFEST_NAME])
        self.assertEqual(manifest["profile"], "socket")
        self.assertEqual(manifest["files"]["main.py"]["source"], "firmware/pico/main.py")
        self.assertEqual(manifest["files"]["main.py"]["sha256"],
                         hashlib.sha256(b"# socket main\n").hexdigest())
        self.assertIn("lib/umodbus/serial.py", manifest["files"])
        self.assertEqual(fake.resets, 0)
        log = (self.root / ".pico-deployments" / "log.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(log[-1])["event"], "deploy")
        backups = list((self.root / ".pico-backups").iterdir())
        self.assertEqual((backups[0] / "boot.py").read_bytes(), b"# old")

        code, out = self.run_status(fake)
        self.assertEqual(code, 0, out)
        self.assertIn("/boot.py", out)  # reported as not in the record
        self.assertIn("Pico matches its deployment record", out)

    def test_status_detects_hand_edit_and_repo_change(self):
        fake = FakePico({"boot.py": b"# old"})
        self.deploy(fake)
        fake.files["main.py"] = b"# edited in Thonny"
        (self.pico_dir / "bme688_sensor.py").write_text("# changed in repo\n", encoding="utf-8")
        code, out = self.run_status(fake)
        self.assertEqual(code, 1)
        self.assertIn("CHANGED since recorded", out)
        self.assertIn("repository copy differs: firmware/pico/bme688_sensor.py", out)

    def test_status_without_manifest(self):
        code, out = self.run_status(FakePico({"main.py": b"x"}))
        self.assertEqual(code, 1)
        self.assertIn("No /" + picoctl.MANIFEST_NAME, out)

    def test_push_backs_up_overwritten_file_and_records_it(self):
        fake = FakePico({"boot.py": b"# old"})
        self.deploy(fake)
        script = self.root / "experiments" / "probe.py"
        script.parent.mkdir()
        script.write_text("# probe\n", encoding="utf-8")
        args = picoctl.parser().parse_args(
            ["push", str(script), "--as", "main.py", "--port", "COM12", "--note", "probe"]
        )
        with mock.patch.object(picoctl, "mpremote", side_effect=fake), \
                contextlib.redirect_stdout(io.StringIO()):
            picoctl.push(args)
        self.assertEqual(fake.files["main.py"], b"# probe\n")
        manifest = json.loads(fake.files[picoctl.MANIFEST_NAME])
        self.assertEqual(manifest["files"]["main.py"]["source"], "experiments/probe.py")
        self.assertEqual(manifest["pushes"][-1]["note"], "probe")
        push_backup = [p for p in (self.root / ".pico-backups").iterdir() if p.name.endswith("-push")]
        self.assertEqual((push_backup[0] / "main.py").read_bytes(), b"# socket main\n")
        code, out = self.run_status(fake)
        self.assertEqual(code, 0, out)

    def test_push_dry_run_does_not_touch_device(self):
        script = self.root / "x.py"
        script.write_text("# x\n", encoding="utf-8")
        args = picoctl.parser().parse_args(["push", str(script), "--dry-run"])
        with mock.patch.object(picoctl, "mpremote", side_effect=AssertionError), \
                contextlib.redirect_stdout(io.StringIO()):
            picoctl.push(args)

    def test_push_rejects_manifest_name_and_multi_file_rename(self):
        a, b = self.root / "a.py", self.root / "b.py"
        a.write_text("a"), b.write_text("b")
        for argv in (["push", str(a), "--as", picoctl.MANIFEST_NAME],
                     ["push", str(a), str(b), "--as", "main.py"]):
            with self.assertRaises(picoctl.ProvisionError):
                picoctl.push(picoctl.parser().parse_args(argv))

    def test_sha256sum_parser_rejects_unknown_output(self):
        with mock.patch.object(picoctl, "mpremote", return_value="garbage\n"):
            with self.assertRaises(picoctl.ProvisionError):
                picoctl.device_sha256("COM12", ["main.py"])

    def test_git_porcelain_keeps_first_character_of_path(self):
        outputs = {
            ("rev-parse", "HEAD"): "a" * 40 + "\n",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main\n",
        }

        def fake_git(*args):
            if args[0] == "status":
                return " M firmware/pico/main.py\n?? experiments/new.py\n"
            return outputs[args]

        with mock.patch.object(picoctl, "_git", side_effect=fake_git):
            info = picoctl.git_snapshot([self.pico_dir / "main.py", self.root / "experiments" / "new.py"])
        self.assertEqual(info["modified_files"], ["firmware/pico/main.py"])
        self.assertEqual(info["untracked_files"], ["experiments/new.py"])
        self.assertFalse(info["clean"])


class MpremotePinTests(unittest.TestCase):
    def test_wrong_mpremote_version_is_rejected(self):
        with mock.patch.object(picoctl, "installed_mpremote_version", return_value="1.30.0"):
            with self.assertRaises(picoctl.ProvisionError):
                picoctl.ensure_mpremote()
        with mock.patch.object(picoctl, "installed_mpremote_version", return_value=None):
            with self.assertRaises(picoctl.ProvisionError):
                picoctl.ensure_mpremote()
        with mock.patch.object(picoctl, "installed_mpremote_version",
                               return_value=picoctl.MPREMOTE_VERSION):
            picoctl.ensure_mpremote()

    def test_requirements_pin_matches_tool(self):
        requirements = (Path(picoctl.REPO) / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn(f"mpremote=={picoctl.MPREMOTE_VERSION}", requirements)

    def test_interactive_port_defaults_only_to_single_raspberry_pi(self):
        pico = type("Port", (), {"device": "COM12", "description": "Board", "vid": 0x2E8A})
        other = type("Port", (), {"device": "COM3", "description": "USB serial", "vid": 0x0403})
        with mock.patch("serial.tools.list_ports.comports", return_value=[other, pico]), \
                mock.patch("builtins.input", return_value=""), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(picoctl.interactive_port(), "COM12")

    def test_interactive_profile_has_no_default(self):
        with mock.patch("builtins.input", side_effect=["", "9", "bench"]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(picoctl.interactive_profile(), "bench")


if __name__ == "__main__":
    unittest.main()
