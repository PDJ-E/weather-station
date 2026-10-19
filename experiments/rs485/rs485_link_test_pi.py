"""
P4-08 — RS485 Modbus Link Test

BOARD:
    Raspberry Pi 5

PURPOSE:
    Read Register Map version (Input Register 208)
    from Pico 2 through two SparkFun RS485 breakouts.

UART:
    /dev/ttyAMA0
    115200 8N1

RS485 direction:
    GPIO17
    HIGH = Transmit
    LOW  = Receive

Expected response:
    0x0101
"""

import glob
import struct
import time

import serial
import gpiod

from gpiod.line import Direction, Value


PORT = "/dev/ttyAMA0"
BAUDRATE = 115200
UNIT_ID = 1
RTS_GPIO = 17


def crc16(data):
    crc = 0xFFFF

    for byte in data:
        crc ^= byte

        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1

    return crc


def find_gpiochip():
    for path in sorted(glob.glob("/dev/gpiochip*")):
        try:
            with gpiod.Chip(path) as chip:
                info = chip.get_line_info(RTS_GPIO)

                if info.name == "GPIO17":
                    return path
        except (OSError, ValueError):
            continue

    raise RuntimeError(
        "Could not locate GPIO17. "
        "Check gpioinfo output."
    )


def main():
    print()
    print("========================================")
    print(" P4-08 RS485 MODBUS LINK TEST")
    print(" Raspberry Pi 5")
    print("========================================")
    print()

    chip_path = find_gpiochip()

    print("UART:", PORT)
    print("GPIO chip:", chip_path)
    print("RTS GPIO:", RTS_GPIO)

    # Configure RS485 direction pin.
    # Start in receive mode.

    with gpiod.request_lines(
        chip_path,
        consumer="weather-rs485-test",
        config={
            RTS_GPIO: gpiod.LineSettings(
                direction=Direction.OUTPUT,
                output_value=Value.INACTIVE,
            )
        },
    ) as gpio:

        with serial.Serial(
            port=PORT,
            baudrate=BAUDRATE,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=2.0,
        ) as uart:

            print()
            print("[1] Serial port opened")

            # Modbus request:
            # Slave 1, FC04, address 208, count 1

            request_data = struct.pack(
                ">BBHH",
                UNIT_ID,
                4,
                208,
                1,
            )

            request = request_data + struct.pack(
                "<H",
                crc16(request_data),
            )

            print("[2] Modbus request:", request.hex(" "))

            uart.reset_input_buffer()

            # Enable RS485 transmission.

            gpio.set_value(RTS_GPIO, Value.ACTIVE)

            time.sleep(0.00005)

            try:
                uart.write(request)
                uart.flush()

                # Small guard interval after UART drain.
                time.sleep(0.00015)

            finally:
                # Return to receive mode.
                gpio.set_value(
                    RTS_GPIO,
                    Value.INACTIVE,
                )

            print("[3] Request transmitted")
            print("[4] Waiting for response...")

            response = uart.read(7)

            print("    Response:", response.hex(" "))

            if len(response) != 7:
                raise RuntimeError(
                    "Incomplete Modbus response: "
                    f"received {len(response)} bytes, "
                    "expected 7"
                )

            if crc16(response) != 0:
                raise RuntimeError(
                    "Modbus CRC validation failed"
                )

            if response[0] != UNIT_ID:
                raise RuntimeError(
                    "Unexpected Modbus Unit ID"
                )

            if response[1] != 4:
                raise RuntimeError(
                    "Unexpected Modbus function code"
                )

            if response[2] != 2:
                raise RuntimeError(
                    "Unexpected register byte count"
                )

            version = struct.unpack(
                ">H",
                response[3:5],
            )[0]

            print()
            print("Register Map Version: 0x{:04X}".format(version))

            if version != 0x0101:
                raise RuntimeError(
                    "Unexpected Register Map version"
                )

            print()
            print("========================================")
            print(" RS485 MODBUS LINK: PASS")
            print("========================================")


if __name__ == "__main__":
    main()