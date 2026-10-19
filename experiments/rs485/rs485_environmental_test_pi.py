"""
P4-08 — RS485 Environmental Modbus Test

BOARD:
    Raspberry Pi 5

PICO:
    Keep bme688_modbus_rs485_pico.py running.

Tests:
    - Register Map 0x0101
    - READ_NOW
    - sample_counter
    - DHT11 + BME688 telemetry
    - Modbus CRC

Transport:
    /dev/ttyAMA0
    115200 8N1
    RTS: GPIO17

Acquisitions:
    3 samples, 5 seconds apart
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

SAMPLES = 3
SAMPLE_PERIOD_S = 5.0


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
                if chip.get_line_info(RTS_GPIO).name == "GPIO17":
                    return path
        except (OSError, ValueError):
            continue

    raise RuntimeError("Could not locate GPIO17")


def read_exact(uart, count, timeout=2.0):
    data = bytearray()
    deadline = time.monotonic() + timeout

    while len(data) < count:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Expected {count} bytes, received {len(data)}"
            )

        chunk = uart.read(count - len(data))

        if chunk:
            data.extend(chunk)

    return bytes(data)


class RS485Modbus:

    def __init__(self, uart, gpio):
        self.uart = uart
        self.gpio = gpio

        self.gpio.set_value(
            RTS_GPIO,
            Value.INACTIVE
        )

    def exchange(self, function, payload):

        request_data = (
            bytes([UNIT_ID, function])
            + payload
        )

        request = (
            request_data
            + struct.pack("<H", crc16(request_data))
        )

        self.uart.reset_input_buffer()

        # Transmit
        self.gpio.set_value(
            RTS_GPIO,
            Value.ACTIVE
        )

        time.sleep(0.00005)

        try:
            written = self.uart.write(request)

            if written != len(request):
                raise IOError("Incomplete UART write")

            self.uart.flush()
            time.sleep(0.00015)

        finally:
            # Receive
            self.gpio.set_value(
                RTS_GPIO,
                Value.INACTIVE
            )

        # Read response header
        header = read_exact(self.uart, 2)

        if header[0] != UNIT_ID:
            raise RuntimeError("Incorrect Modbus Unit ID")

        if header[1] == (function | 0x80):

            rest = read_exact(self.uart, 3)
            response = header + rest

            if crc16(response) != 0:
                raise RuntimeError("CRC error in exception")

            raise RuntimeError(
                f"Modbus exception: {rest[0]}"
            )

        if header[1] != function:
            raise RuntimeError("Unexpected function code")

        if function in (1, 3, 4):

            count = read_exact(self.uart, 1)[0]
            data = read_exact(self.uart, count + 2)

            response = header + bytes([count]) + data

        elif function == 5:

            response = header + read_exact(self.uart, 6)

        else:
            raise RuntimeError("Unsupported function")

        if crc16(response) != 0:
            raise RuntimeError("Modbus CRC mismatch")

        return response

    def read_registers(self, address, count):

        response = self.exchange(
            4,
            struct.pack(">HH", address, count)
        )

        data = response[3:-2]

        if len(data) != count * 2:
            raise RuntimeError("Invalid register count")

        return list(
            struct.unpack(f">{count}H", data)
        )

    def read_coil(self, address):

        response = self.exchange(
            1,
            struct.pack(">HH", address, 1)
        )

        if response[2] != 1:
            raise RuntimeError("Invalid coil response")

        return bool(response[3] & 1)

    def write_coil(self, address, value):

        payload = struct.pack(
            ">HH",
            address,
            0xFF00 if value else 0
        )

        response = self.exchange(5, payload)

        if response[2:6] != payload:
            raise RuntimeError("Coil write echo mismatch")

    def read_counter(self):

        regs = self.read_registers(206, 2)

        return (regs[0] << 16) | regs[1]

    def trigger_read_now(self):

        before = self.read_counter()

        self.write_coil(102, True)

        start = time.monotonic()
        deadline = start + 8.0

        while time.monotonic() < deadline:

            pending = self.read_coil(102)
            after = self.read_counter()

            if not pending and after != before:
                return (
                    after,
                    (time.monotonic() - start) * 1000
                )

            time.sleep(0.1)

        raise TimeoutError("READ_NOW timeout")


def decode_int16(value):
    return value - 65536 if value & 0x8000 else value


def decode_u32(high, low):
    return (high << 16) | low


def main():

    print()
    print("=" * 55)
    print(" P4-08 RS485 ENVIRONMENTAL MODBUS TEST")
    print("=" * 55)

    chip_path = find_gpiochip()

    successful = 0

    with gpiod.request_lines(
        chip_path,
        consumer="weather-rs485-environment",
        config={
            RTS_GPIO: gpiod.LineSettings(
                direction=Direction.OUTPUT,
                output_value=Value.INACTIVE
            )
        }
    ) as gpio:

        with serial.Serial(
            PORT,
            baudrate=BAUDRATE,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.15
        ) as uart:

            client = RS485Modbus(uart, gpio)

            version = client.read_registers(208, 1)[0]

            print(f"Register Map: 0x{version:04X}")

            if version != 0x0101:
                raise RuntimeError("Unexpected Register Map")

            # READ_NOW test assumes periodic sampling is stopped.
            if client.read_coil(100):
                raise RuntimeError(
                    "Pico run_enable=True. "
                    "Stop periodic acquisition before this test."
                )

            print("Connection: OK")
            print()

            for i in range(SAMPLES):

                print("-" * 55)
                print(f" ACQUISITION {i + 1}/{SAMPLES}")
                print("-" * 55)

                counter, latency = client.trigger_read_now()

                regs = client.read_registers(200, 17)

                snapshot_counter = decode_u32(
                    regs[6], regs[7]
                )

                if snapshot_counter != counter:
                    raise RuntimeError(
                        "sample_counter mismatch"
                    )

                dht_temp = decode_int16(regs[0]) / 100
                dht_rh = regs[1] / 100
                dht_status = regs[2]

                bme_temp = decode_int16(regs[9]) / 100
                bme_rh = regs[10] / 100

                pressure = decode_u32(
                    regs[11], regs[12]
                ) / 100

                gas = decode_u32(
                    regs[13], regs[14]
                )

                flags = regs[15]
                bme_status = regs[16]

                gas_valid = bool(flags & 1)
                heater_stable = bool(flags & 2)

                print(f"Counter     : {counter}")
                print(f"READ_NOW    : {latency:.2f} ms")
                print()

                print("DHT11")
                print(f"  Temp      : {dht_temp:.2f} C")
                print(f"  Humidity  : {dht_rh:.2f} %")
                print(f"  Status    : {dht_status}")
                print()

                print("BME688")
                print(f"  Temp      : {bme_temp:.2f} C")
                print(f"  Humidity  : {bme_rh:.2f} %")
                print(f"  Pressure  : {pressure:.2f} hPa")
                print(f"  Gas       : {gas} ohm")
                print(f"  Gas valid : {gas_valid}")
                print(f"  Heater    : {heater_stable}")
                print(f"  Status    : {bme_status}")
                print()

                print("DHT11 vs BME688")
                print(
                    f"  Delta T   : {dht_temp - bme_temp:+.2f} C"
                )
                print(
                    f"  Delta RH  : {dht_rh - bme_rh:+.2f} pp"
                )

                if dht_status != 0 or bme_status != 0:
                    raise RuntimeError(
                        "Environmental sensor reported an error"
                    )

                successful += 1

                print()
                print("ACQUISITION: PASS")

                if i < SAMPLES - 1:
                    time.sleep(SAMPLE_PERIOD_S)

    print()
    print("=" * 55)
    print(f" RESULTS: {successful}/{SAMPLES} successful")
    print("=" * 55)

    if successful == SAMPLES:
        print("P4-08 RS485 ENVIRONMENTAL TEST: PASS")


if __name__ == "__main__":
    main()