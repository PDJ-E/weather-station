# Raspberry Pi Pico 2 — canonical device filesystem

This directory mirrors the main Python files deployed to the root of a Raspberry Pi Pico 2 MicroPython filesystem.

```text
firmware/pico/
├── main.py                        # P4 RS-485 application (formerly bme688_modbus_rs485_pico.py)
├── bme688_modbus_extension.py
├── bme688_sensor.py
├── dht11_sensor.py
├── environmental_reading.py
├── pico_modbus_patch.py
├── profiles/
│   └── uart/
│       └── main.py                # historical P3 direct UART application
└── tests/
    └── common_interface_test.py   # optional device test source
```

Note: common_interface_test.py is **not copied by default** and remains physically in tests/ in the GitHub repository, to avoid running it during provisioning. The default `main.py` is an **unchanged copy** of `experiments/bme_688/pico/bme688_modbus_rs485_pico.py`, the exact filename that was running as main.py on the previous Pico.

The `umodbus` library is installed on the device at `/lib/umodbus/` by the provisioning tool, from the pinned `micropython-modbus==2.3.7` dependency.

To provision from a Windows computer, use `python tools/picoctl.py deploy --port COM5` (dry run) then `python tools/picoctl.py deploy --port COM5 --apply` (backup and deploy). See `docs/pico_provisioning.md`.

**Do not reflash** the already working Pimoroni MicroPython 1.29.0 UF2. Device deployment only copies Python application files. The P4 default expects RS-485 hardware on UART0 (GP0 TX, GP1 RX) with RTS on GP6, BME688 I²C (GP2/GP3, 0x76) and DHT11 on GP15. For a direct TTL UART bench setup, use `--profile uart` instead. Historical experiments remain in `experiments/` for reproducibility.
