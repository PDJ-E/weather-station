# Raspberry Pi Pico 2 — canonical device filesystem

This directory holds the Python files deployed to the root of a Raspberry Pi Pico 2 running Pimoroni MicroPython 1.29.0. Deploy them with [`tools/picoctl.py`](../../tools/picoctl.py), not by hand: see [`docs/pico_provisioning.md`](../../docs/pico_provisioning.md).

```text
firmware/pico/
├── main.py                        # profile "socket": Pico 2 socket in the outdoor box (NOT yet validated on hardware)
├── bme688_modbus_extension.py     # legacy P3 module, deployed for file parity; main.py does not import it
├── bme688_sensor.py
├── common_interface_test.py       # optional active sensor test (--include-tests)
├── dht11_sensor.py
├── environmental_reading.py
├── pico_modbus_patch.py
├── dependencies.lock.json         # pinned UF2 and micropython-modbus
└── profiles/
    ├── bench/
    │   └── main.py                # profile "bench": validated P4-08 RS-485 bench firmware
    └── uart/
        └── main.py                # profile "uart": historical P3 direct UART application
```

## Profiles and pin maps

The profile decides which `main.py` is installed, and each `main.py` assumes a specific wiring. Installing firmware for the wrong wiring stops the RS-485 link from answering, so `picoctl` always asks for the profile.

| Signal | `socket` (`main.py`) | `bench` (`profiles/bench/main.py`) |
|---|---|---|
| RS-485 TX / RX | GP0 / GP1 (pins 1, 2) | GP0 / GP1 |
| RS-485 RTS (DE + /RE) | **GP2** (pin 4) | GP6 (pin 9) |
| BME688 I²C1 SDA / SCL, 0x76 | **GP6 / GP7** (pins 9, 10) | GP2 / GP3 |
| DHT11 | **not installed** | GP15 |
| Register map version | **`0x0102`** | `0x0101` |
| Hardware status | Not validated | Validated in P4-08 |

- `profiles/bench/main.py` is an unchanged copy of `experiments/bme_688/pico/bme688_modbus_rs485_pico.py`, the firmware used in the P4-08 bench validation.
- `main.py` is derived from it with three changes: the socket pin map, `DHT11_INSTALLED = False`, and register map `0x0102`. The [v1.2 delta](../../docs/phase_4/p4_register_map_v1_2_delta.md) describes the new DHT11 status `3 = NOT_INSTALLED`. The Grove box-temperature sensor on GP28 is wired in the socket but is **not read** by this version.
- The shared modules (`bme688_sensor.py`, `dht11_sensor.py`, `environmental_reading.py`, `pico_modbus_patch.py`) are identical to the validated copies in `experiments/`.

## On the device

| Pico path | Origin |
|---|---|
| `/main.py` | The selected profile's `main.py` |
| `/environmental_reading.py`, `/dht11_sensor.py`, `/bme688_sensor.py`, `/pico_modbus_patch.py`, `/bme688_modbus_extension.py` | This directory |
| `/lib/umodbus/` | `micropython-modbus==2.3.7`, SHA-256 pinned in `dependencies.lock.json` |
| `/ws_deploy.json` | Deployment record written by `picoctl`: profile, Git commit, SHA-256 of every file, later pushes |

**Do not reflash** the working Pimoroni MicroPython 1.29.0 UF2. Deployment only copies Python files.
