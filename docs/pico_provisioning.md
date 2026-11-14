# Weather Station — Pico 2 provisioning

The host-side tool is **tools/picoctl.py**. It installs application files on a Pico 2 that already has the tested **Pimoroni MicroPython 1.29.0** UF2. **It does not replace the UF2, erase flash, or reset the device by default.**

Requires Python 3.10+, USB serial connection and mpremote. Close Thonny and other serial terminals before connecting.

## Windows PowerShell quick start

    cd <weather-station-repository>
    python -m pip install mpremote
    python tools/picoctl.py doctor --port COM5
    python tools/picoctl.py deploy --profile rs485 --port COM5

The deploy command above only prints a plan (**dry run**). To write to a **new and empty** Pico:

    python tools/picoctl.py deploy --profile rs485 --port COM5 --apply --skip-backup

To write to a Pico with files already present, omit --skip-backup. The tool takes a filesystem backup first:

    python tools/picoctl.py deploy --profile rs485 --port COM5 --apply

You can also take a standalone backup:

    python tools/picoctl.py backup --port COM5

Backups go into .pico-backups/<UTC timestamp> and include all files visible to MicroPython, **not the UF2**. Backups are kept locally and ignored by Git. The tool does not restart the Pico unless --reset is explicitly requested; connect all required sensors and bus hardware before starting main.py.

## Profiles and hardware

| Profile | Repo entry point | Transport | Pins |
| --- | --- | --- | --- |
| rs485 (default) | firmware/pico/profiles/rs485/main.py | SparkFun RS-485 half-duplex (P4) | UART0 GP0/GP1; RTS GP6 |
| uart | firmware/pico/profiles/uart/main.py | Direct TTL UART (P3) | UART0 GP0/GP1 |

Both profiles use Modbus 115200 8N1, Unit ID 1; BME688 on I2C1 GP2 (SDA) / GP3 (SCL) at 0x76; and DHT11 on GP15. The P4 profile exposes register map v1.1 (0x0101), including registers 200–216. The entry point files were copied **unmodified** from the P3 and P4 experiment sources.

## What is installed?

The tool copies environmental_reading.py, dht11_sensor.py, bme688_sensor.py, pico_modbus_patch.py, bme688_modbus_extension.py, and the selected main.py into the Pico filesystem. It installs /lib/umodbus/ from **micropython-modbus==2.3.7**, with the PyPI source archive verified against SHA-256:

    b9351a66af0ff29dd585900e53827a989dedcb02ec63b1041358165089652271

The pinned archive is downloaded once onto the **computer**. Subsequent Pico boards may use --offline to reuse the local cache, or --archive PATH to supply a verified local source distribution. The Pico needs no internet access.

common_interface_test.py is installed only with --include-tests because it executes active sensor reads when run. The BME688 extension file is retained for parity with the previous Pico inventory; P4 main.py implements the register extension itself.

## Existing main.py may be newer

Your old Pico's main.py was **not found** in the GitHub default branch when this feature was created. The checked-in P4 entry point is our best version-controlled baseline, but compare it with the working copy on your PC before using it as a complete replacement. You can substitute the exact known-good local file:

    python tools/picoctl.py deploy --profile rs485 --main "C:\path\to\main.py" --port COM5 --apply

Do not use RS-485 main.py on a direct UART-only setup. Select --profile uart instead.

## Operational safeguards

- Dry-run by default; --apply is required to copy files.
- Backs up all on-board MicroPython files before modifying them, unless --skip-backup was explicitly selected.
- Validates that the board is RP2/RP2350 and that the Pimoroni sensor driver imports work.
- Checks SHA-256 of every transferred file after reading it back from the Pico.
- Installs dependencies and modules first; main.py is copied **last**.
- No automatic deletion and no automatic reset. It interrupts the currently running program to access the REPL.
- If transfer or validation fails, the installer stops and retains any backup. There is no automatic rollback.
- Hardware validation is still required using the P4 RS-485 client on weather-core; our automated CLI tests do not substitute for testing on a physical Pico.
