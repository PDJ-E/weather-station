# Raspberry Pi Pico 2 deployment

This directory now contains canonical application sources:

- profiles/rs485/main.py: unchanged P4 RS-485 entry point originally in experiments/bme_688/pico/bme688_modbus_rs485_pico.py
- profiles/uart/main.py: unchanged P3 UART entry point originally in experiments/bme_688/pico/bme688_modbus_pico.py
- common/: sensor interfaces and Modbus patches copied without edits
- tests/common_interface_test.py: optional active sensor test, not installed by default

The computer-side installer is tools/picoctl.py. See docs/pico_provisioning.md for installation and verification instructions.

These Python modules are **not the UF2 firmware**. The existing Pimoroni MicroPython 1.29.0 UF2 stays installed. Future deploy changes should be made in firmware/pico, with historical experiment files retained as evidence.
