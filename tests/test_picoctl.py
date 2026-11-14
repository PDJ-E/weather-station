"""Host-side provisioning regression tests; no connected Pico is required."""
import io
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
    def test_firmware_is_pinned_to_original_pimoroni_build(self):
        self.assertEqual(picoctl.FIRMWARE_NAME, "rpi_pico2-v1.29.0-micropython.uf2")
        self.assertIn("pimoroni-pico-rp2350", picoctl.FIRMWARE_URL)
        self.assertEqual(picoctl.FIRMWARE_SIZE, 1_833_472)
        self.assertEqual(
            picoctl.FIRMWARE_SHA256,
            "2246a7daaf436d39f8c6810342def221c87aa4d50386942b32b9fadd10471dd3",
        )

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
        self.assertEqual(picoctl.RELEASE, "2.3.7")
        self.assertEqual(len(picoctl.EXPECTED_SHA256), 64)

    def test_dry_run_never_connects_to_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            pico = Path(tmp)
            common = pico
            common.mkdir(exist_ok=True)
            for name in (*picoctl.CORE, "bme688_modbus_extension.py"):
                (common / name).write_text("# fixture", encoding="utf-8")
            (pico / "main.py").write_text("# fixture", encoding="utf-8")
            with mock.patch.object(picoctl, "PICO", pico), mock.patch.object(
                picoctl, "COMMON", common
            ), mock.patch.object(picoctl, "mpremote", side_effect=AssertionError), mock.patch.object(
                picoctl, "download_firmware", side_effect=AssertionError
            ):
                args = picoctl.parser().parse_args(["deploy", "--port", "COM5"])
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
            with self.assertRaises(picoctl.ProvisionError):
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

    def test_one_command_wizard_selects_port_and_applies(self):
        with mock.patch.object(picoctl, "bootstrap_mpremote"), mock.patch.object(
            picoctl, "interactive_port", return_value="COM12"
        ), mock.patch.object(picoctl, "deploy") as deployed:
            self.assertEqual(picoctl.interactive_install(), 0)
        args = deployed.call_args.args[0]
        self.assertEqual(args.port, "COM12")
        self.assertEqual(args.profile, "rs485")
        self.assertTrue(args.apply)
        self.assertFalse(args.skip_backup)
        self.assertFalse(args.reset)

    def test_select_listed_serial_port(self):
        serial_port = type("Port", (), {"device": "COM12", "description": "Pico 2"})
        with mock.patch("serial.tools.list_ports.comports", return_value=[serial_port]), mock.patch(
            "builtins.input", return_value="1"
        ):
            self.assertEqual(picoctl.interactive_port(), "COM12")

    def test_rs485_uses_pico_root_as_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            pico = Path(tmp)
            common = pico
            common.mkdir(exist_ok=True)
            for name in (*picoctl.CORE, "bme688_modbus_extension.py"):
                (common / name).write_text("# fixture", encoding="utf-8")
            (pico / "main.py").write_text("# P4 fixture", encoding="utf-8")
            with mock.patch.object(picoctl, "PICO", pico), mock.patch.object(
                picoctl, "COMMON", common
            ):
                files = picoctl.source_files("rs485", False, None)
                self.assertEqual(files[-1], (pico / "main.py", "main.py"))

    def test_main_is_last_source_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pico = Path(tmp)
            common = pico
            common.mkdir(exist_ok=True)
            for name in (*picoctl.CORE, "bme688_modbus_extension.py"):
                (common / name).write_text("# fixture", encoding="utf-8")
            app = pico / "profiles" / "uart"
            app.mkdir(parents=True)
            (app / "main.py").write_text("# fixture", encoding="utf-8")
            with mock.patch.object(picoctl, "PICO", pico), mock.patch.object(
                picoctl, "COMMON", common
            ):
                files = picoctl.source_files("uart", False, None)
                self.assertEqual(files[-1][1], "main.py")


if __name__ == "__main__":
    unittest.main()
