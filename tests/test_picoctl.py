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
            common.mkdir()
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

    def test_rs485_uses_pico_root_as_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            pico = Path(tmp)
            common = pico
            common.mkdir()
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
            common.mkdir()
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
