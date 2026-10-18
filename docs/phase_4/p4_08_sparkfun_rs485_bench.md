# P4-08 — SparkFun RS-485 half-duplex bench

**Status:** Software prepared. Physical acceptance pending. Pico map v1.1 (0x0101) unchanged.

The Pico 2 firmware is a copy of the validated P3 environmental Modbus server with only a direction-controlled UART wrapper. No sensor, scheduler or register changes.

## Five-wire logic connector (SparkFun JP9)

Verified from SparkFun RS485_Breakout_v10 schematic; observed wire colors in photos must be checked with a continuity meter before powering.

| Wire | Board label | Raspberry Pi 5 | Raspberry Pi Pico 2 |
|---|---|---|---|
| Red | 3-5 / VCC | 3V3, physical pin 1 | 3V3(OUT), physical pin 36 |
| Blue | RX | GPIO15 / UART RX, pin 10 | GP1 / UART RX, pin 2 |
| Yellow | TX | GPIO14 / UART TX, pin 8 | GP0 / UART TX, pin 1 |
| Green | RTS | GPIO17, pin 11 | GP6, pin 9 |
| Black | GND | GND, pin 6 | GND, pin 3 |

Supply the SparkFun modules at **3.3V only**. The SP3485 silicon datasheet specifies 3.3V ± 5% operating supply, notwithstanding the silkscreen "3-5".

RTS HIGH enables transmit and disables receive. RTS LOW enables receive and disables transmit. **Idle LOW on both ends**.

## Three-wire differential bus connector (SparkFun JP3)

| Wire | Board terminal | To other breakout |
|---|---|---|
| Green | B | B / green |
| Yellow | A | A / yellow |
| White | GND signal reference | GND / white |

Connect A-A, B-B, GND-GND; NO A/B crossing. Also connect each breakout logic GND to its own board GND; the reference cable links both grounds. Do not supply 24V in the bench setup.

## Termination, bias, and RJ45 mismatch

SparkFun breakout v10 schematic shows fixed **R4 = 220 ohm between A and B**; with two installed modules the parallel DC bus load is 110 ohm. Each end, however, is terminated at 220 ohm, not the nominal 100 ohm characteristic impedance of CAT6. This must be validated and possibly modified for P4-10 and the long cable; **do not simply add another 120 ohm across each end**.

No separate bias network is shown on this schematic. Fail-safe idle for the terminated bus needs confirmation at P4-10.

**CRITICAL:** The SparkFun optional onboard RJ45 is NOT pin-compatible with the custom station T568B wiring: SparkFun RJ45 pins 4/5 are GND while the custom CAT6 pins 4/5 carry RS485 A/B. Never plug the custom cable into this RJ45 footprint or an Ethernet switch.

Custom T568B later: pin 1 W/O +24V; pin 2 O power GND; pin 3 W/G RS485 reference GND; pin 4 blue A; pin 5 W/blue B; 6 green and 7/8 brown spare. Use an isolated dedicated patch/terminal board, not direct breakout RJ45.

## Bench software

- Pico: copy experiments/bme_688/pico/bme688_modbus_rs485_pico.py to the Pico together with the same module dependencies already used for P3; execute it.
- Pi 5: stop any P3 program using /dev/ttyAMA0, then install python-gpiod v2 if needed:
    .venv/bin/python -m pip install "gpiod>=2,<3"
- Run: .venv/bin/python experiments/rs485/rs485_modbus_bench_pi.py --samples 30
- GPIO17 line chip is auto-detected on Pi 5. Override with --chip /dev/gpiochipN if needed (see gpioinfo).

Acceptance: 30/30 RTU exchanges, valid CRC, v1.1 map, READ_NOW counter advanced, zero sequence gaps, and DHT11/BME688 telemetry valid.

**Tracker:** P4-08 remains pending until physical bench PASS; P4-09 cable plan documented but not validated; P4-10 termination/bias open; P4-11 representative cable and P4-12 dynamic load open.
