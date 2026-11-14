# Pico 2 — repeatable provisioning (Weather Station)

**Canonical source:** `firmware/pico/`. Primary entry point `firmware/pico/main.py` is an unchanged copy of the previously used `bme688_modbus_rs485_pico.py`. Only the host installer and repository layout changed.

**Important:** The board already runs the known-good Pimoroni MicroPython 1.29.0. The tool **downloads an archived copy of the UF2 to your Windows PC, never flashes it**, and installs Python application files via mpremote. No automatic erasure or reset.

## 1. Get the feature branch locally BEFORE merging PR #8

From your existing Weather Station repository directory in PowerShell:

```powershell
git status
git fetch origin
git switch --track origin/feat/pico2-repeatable-provisioning
```

If the local feature branch already exists, instead use `git switch feat/pico2-repeatable-provisioning` followed by `git pull`. Resolve or stash any uncommitted changes before switching. Do **not** merge the PR first; this branch exists for testing.

## 2. Host-only tests (no Pico required)

```powershell
python -m unittest discover -s tests -p "test_picoctl.py" -v
```

These test the dry run, profile resolution and UF2 cache integrity behavior using mocks. GitHub Actions also runs these tests when PR #8 updates. These are **not** hardware integration tests.

## 3. Download and preserve the tested Pimoroni UF2

```powershell
python tools/picoctl.py firmware
Get-FileHash .pico-firmware\rpi_pico2-v1.29.0-micropython.uf2 -Algorithm SHA256
```

Expected SHA256:

```text
2246a7daaf436d39f8c6810342def221c87aa4d50386942b32b9fadd10471dd3
```

Original official release, now in archived repository:
https://github.com/pimoroni/pimoroni-pico-rp2350/releases/download/v1.29.0/rpi_pico2-v1.29.0-micropython.uf2

Local path: `.pico-firmware/rpi_pico2-v1.29.0-micropython.uf2` (gitignored). Correct board: RPI_PICO2 (RP2350), not Pico/Pico 2 W. The installer verifies the official asset's byte size and SHA256, downloads only if missing, and refuses an existing corrupt cache. No BOOTSEL or flash step is performed.

**Note:** Running `deploy --apply` also calls the UF2 downloader automatically if not yet cached. `firmware` lets you download it independently before connecting the Pico. The Pico itself needs no network access.

## 4. Diagnose a physical replacement Pico on USB

Install host tool and close Thonny/serial monitors:

```powershell
python -m pip install mpremote
python -m mpremote connect list
python tools/picoctl.py doctor --port COM5
```

Replace `COM5` with the serial port reported on **your computer**. This checks RP2 MicroPython and `pimoroni_i2c` / `breakout_bme68x` imports. It does not compare UF2 flash bytes. Connect the Pico by USB only; avoid simultaneous external power to the board during provisioning.

## 5. Dry-run deployment, then actual installation

```powershell
# Display deployment plan. Does not contact or modify the board.
python tools/picoctl.py deploy --profile rs485 --port COM5

# Backup existing MicroPython files; then install and verify.
python tools/picoctl.py deploy --profile rs485 --port COM5 --apply
```

The real command automatically caches the UF2 on your PC, then confirms drivers, backs up files under `.pico-backups/<UTC timestamp>/`, downloads/checks the pinned `micropython-modbus==2.3.7` archive, installs `/lib/umodbus/`, deploys application modules from `firmware/pico/`, and writes `main.py` **last**. Every transfer is read back and checked with SHA256. It does not restart the board unless explicitly passed `--reset`.

For a definitely empty new Pico, `--skip-backup` is optional, but not recommended for the first hardware test.

The optional `common_interface_test.py` is included only with `--include-tests`. The extra BME688 extension file is retained for file parity although P4 main integrates its registers. If you need the older direct TTL UART version, choose `--profile uart`; this changes the entry point, not the common modules.

## 6. Verify imports after transfer

```powershell
python -m mpremote connect COM5 exec "from umodbus.serial import ModbusRTU; import breakout_bme68x; print('IMPORTS OK')"
python -m mpremote connect COM5 fs ls
python -m mpremote connect COM5 fs ls :/lib/umodbus
```

After confirming that the correct sensors/transceiver are connected, reset or power-cycle the Pico to start `main.py`. A successful import alone is **not** an RS-485 integration test.

## 7. End-to-end link test on weather-core (after reconnecting hardware)

The default P4 firmware expects: BME688 I²C1 GP2/GP3, address `0x76`; DHT11 GP15; UART0 GP0/GP1 at 115200 8N1; RS-485 direction/RTS GP6. On the Raspberry Pi 5, the existing test uses `/dev/ttyAMA0`, direction GPIO17 and Modbus Unit ID 1.

From an updated copy of the repository on `weather-core`, with its Python environment and serial/GPIO dependencies installed:

```bash
python experiments/rs485/rs485_environmental_test_pi.py
```

This existing P4-08 test expects register-map version `0x0101`, `run_enable=0` (stopped), and three READ_NOW acquisitions with status OK from **both** the DHT11 and BME688. It prints `P4-08 RS485 ENVIRONMENTAL TEST: PASS` only if those checks succeed. Do not run it before connecting both sensors.

If this passes, report the test output, mark PR #8 ready for review, merge into `main` using GitHub and return to your development PC:

```powershell
git switch main
git pull origin main
```

## Artifact and dependency inventory

| Destination | Origin |
| --- | --- |
| Host `.pico-firmware/rpi_pico2-v1.29.0-micropython.uf2` | Pinned official Pimoroni v1.29.0 release — **downloaded only** |
| Pico `/lib/umodbus/` | PyPI `micropython-modbus==2.3.7`, SHA256 `b9351a66af0ff29dd585900e53827a989dedcb02ec63b1041358165089652271` |
| Pico `/main.py` | `firmware/pico/main.py` — unchanged original P4 application |
| Pico `/environmental_reading.py`, `/dht11_sensor.py`, `/bme688_sensor.py`, `/pico_modbus_patch.py`, `/bme688_modbus_extension.py` | `firmware/pico/` |
| Pico `/common_interface_test.py` | Optional source in `firmware/pico/`, copy with `--include-tests` |

No automatic flash, flash erasure, file deletion, rollback, or reset. Failure aborts the process and preserves backups; restore manually if needed. A change of Pimoroni UF2 or micropython-modbus library version requires a separate compatibility evaluation.
