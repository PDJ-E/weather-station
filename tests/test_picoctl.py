"""Host-side provisioning regression tests; no connected Pico is required."""
import io
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
    def test_package_release_and_digest_are_locked(self):
        self.assertEqual(picoctl.RELEASE, "2.3.7")
        self.assertEqual(len(picoctl.EXPECTED_SHA256), 64)

    def test_dry_run_never_connects_to_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            pico = Path(tmp)
            common = pico / "common"
            common.mkdir()
            for name in (*picoctl.CORE, "bme688_modbus_extension.py"):
                (common / name).write_text("# fixture", encoding="utf-8")
            app = pico / "profiles" / "rs485"
            app.mkdir(parents=True)
            (app / "main.py").write_text("# fixture", encoding="utf-8")
            with mock.patch.object(picoctl, "PICO", pico), mock.patch.object(
                picoctl, "COMMON", common
            ), mock.patch.object(picoctl, "mpremote", side_effect=AssertionError):
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

    def test_main_is_last_source_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pico = Path(tmp)
            common = pico / "common"
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
