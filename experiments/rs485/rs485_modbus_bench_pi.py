#!/usr/bin/env python3
"""Pi 5 P4-08 RS485 bench test. Requires pyserial and python-gpiod v2.

Physical Pi: GPIO14 TX (pin 8), GPIO15 RX (pin 10), GPIO17 RTS (pin 11),
3.3 V (pin 1), GND (pin 6). Both SparkFun RTS pins HIGH=TX, LOW=RX.
Pico: GP0 TX, GP1 RX, GP6 RTS, VCC 3V3OUT. Between modules: A-A B-B GND-GND.
"""
import argparse
import glob
import struct
import time

import gpiod
from gpiod.line import Direction, Value
import serial

UNIT = 1
MAP_VERSION = 0x0101


def crc16(data):
    result = 0xFFFF
    for b in data:
        result ^= b
        for _ in range(8):
            result = (result >> 1) ^ (0xA001 if (result & 1) else 0)
    return result


def pack(data):
    return data + struct.pack("<H", crc16(data))


def chip_for_gpio17():
    for path in sorted(glob.glob("/dev/gpiochip*")):
        try:
            with gpiod.Chip(path) as chip:
                if chip.get_line_info(17).name == "GPIO17":
                    return path
        except (OSError, ValueError):
            pass
    raise RuntimeError(
        "Cannot detect RP1 GPIO17 line. Inspect gpioinfo and pass "
        "--chip /dev/gpiochipN."
    )


def u32(hi, lo):
    return (hi << 16) | lo


def s16(raw):
    return raw - 65536 if raw & 0x8000 else raw


class RTU:
    def __init__(self, uart, direction):
        self.uart = uart
        self.direction = direction
        self.direction.set_value(17, Value.INACTIVE)

    def exact(self, n, seconds):
        result = bytearray()
        until = time.monotonic() + seconds
        while len(result) < n:
            if time.monotonic() >= until:
                raise TimeoutError("Short response: {} of {} bytes".format(len(result), n))
            chunk = self.uart.read(n - len(result))
            if chunk:
                result.extend(chunk)
        return bytes(result)

    def exchange(self, function, args):
        request = pack(bytes((UNIT, function)) + args)
        self.uart.reset_input_buffer()
        self.direction.set_value(17, Value.ACTIVE)
        time.sleep(0.00006)
        try:
            written = self.uart.write(request)
            if written != len(request):
                raise IOError("Short serial write")
            self.uart.flush()
            time.sleep(0.0002)
        finally:
            self.direction.set_value(17, Value.INACTIVE)

        header = self.exact(3, 1.5)
        if header[0] != UNIT:
            raise IOError("Wrong unit {}".format(header[0]))
        if header[1] == (function | 0x80):
            message = header + self.exact(2, 1.5)
            if crc16(message) != 0:
                raise IOError("CRC error in Modbus exception")
            raise IOError("FC{} exception {}".format(function, header[2]))
        if header[1] != function:
            raise IOError("Unexpected function {}".format(header[1]))
        if function in (1, 3, 4):
            message = header + self.exact(header[2] + 2, 1.5)
        else:
            message = header + self.exact(5, 1.5)
        if crc16(message) != 0:
            raise IOError("CRC mismatch")
        return message

    def regs(self, address, count):
        msg = self.exchange(4, struct.pack(">HH", address, count))
        data = msg[3:-2]
        if len(data) != count * 2:
            raise IOError("Incorrect register count")
        return struct.unpack(">{}H".format(count), data)

    def coil(self, address):
        msg = self.exchange(1, struct.pack(">HH", address, 1))
        if msg[2] != 1:
            raise IOError("Invalid coil response")
        return bool(msg[3] & 1)

    def write_coil(self, address, enabled):
        payload = struct.pack(">HH", address, 0xFF00 if enabled else 0)
        msg = self.exchange(5, payload)
        if msg[2:6] != payload:
            raise IOError("Write coil echo mismatch")

    def read_now(self):
        before = u32(*self.regs(206, 2))
        start = time.monotonic()
        self.write_coil(102, True)
        while time.monotonic() - start < 8.0:
            pending = self.coil(102)
            after = u32(*self.regs(206, 2))
            if not pending and after != before:
                return before, after, (time.monotonic() - start) * 1000
            time.sleep(0.075)
        raise TimeoutError("READ_NOW timed out")

    def snapshot(self):
        r = self.regs(200, 17)
        if r[8] != MAP_VERSION:
            raise IOError("Register Map version changed")
        return {
            "counter": u32(r[6], r[7]),
            "uptime": u32(r[4], r[5]),
            "dht_status": r[2],
            "dht_temp": s16(r[0])/100.0,
            "dht_rh": r[1]/100.0,
            "bme_status": r[16],
            "bme_temp": s16(r[9])/100.0,
            "bme_rh": r[10]/100.0,
            "pressure": u32(r[11], r[12])/100.0,
            "gas_ohm": u32(r[13], r[14]),
            "gas_valid": bool(r[15]&1),
            "heater_stable": bool(r[15]&2),
        }


def main():
    parser = argparse.ArgumentParser(description="SparkFun RS485 bench test")
    parser.add_argument("--port", default="/dev/ttyAMA0")
    parser.add_argument("--chip", default=None)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--period", type=float, default=5.0)
    args = parser.parse_args()
    if args.samples < 1 or args.period < 2:
        parser.error("samples >= 1 and period >= 2 required")
    chip_path = args.chip or chip_for_gpio17()
    print("P4-08: UART={}, RTS=GPIO17 on {}, samples={}".format(
        args.port, chip_path, args.samples
    ))
    req = gpiod.request_lines(
        chip_path,
        consumer="weather-station-rs485",
        config={17: gpiod.LineSettings(
            direction=Direction.OUTPUT, output_value=Value.INACTIVE)},
    )
    passed = failed = 0
    latencies = []
    try:
        with req, serial.Serial(
            args.port, baudrate=115200, bytesize=8,
            parity="N", stopbits=1, timeout=0.15, write_timeout=1.0
        ) as uart:
            client = RTU(uart, req)
            version = client.regs(208, 1)[0]
            if version != MAP_VERSION:
                raise RuntimeError("Map mismatch 0x{:04X}".format(version))
            if client.coil(100):
                raise RuntimeError(
                    "Pico periodic mode active (coil 100). Stop it before test."
                )
            print("Register Map 0x0101 OK; run_enable=False")
            next_at = time.monotonic()
            previous_counter = None
            for i in range(1, args.samples + 1):
                wait = next_at - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                try:
                    before, after, latency = client.read_now()
                    snap = client.snapshot()
                    if after != snap["counter"]:
                        raise IOError("Counter/snapshot mismatch")
                    if previous_counter is not None and after != previous_counter + 1:
                        raise IOError("Sequence discontinuity {}->{}".format(
                            previous_counter, after))
                    previous_counter = after
                    if snap["dht_status"] != 0 or snap["bme_status"] != 0:
                        raise IOError("Sensor status error DHT={} BME={}".format(
                            snap["dht_status"], snap["bme_status"]))
                    passed += 1
                    latencies.append(latency)
                    print(
                        "[{:03d}] ctr={:5d} DHT={:.2f}C/{:.2f}% "
                        "BME={:.2f}C/{:.2f}% P={:.2f}hPa Gas={}ohm "
                        "valid={} heater={} latency={:.1f}ms".format(
                            i, after, snap["dht_temp"], snap["dht_rh"],
                            snap["bme_temp"], snap["bme_rh"],
                            snap["pressure"], snap["gas_ohm"],
                            snap["gas_valid"], snap["heater_stable"], latency))
                except (OSError, TimeoutError) as exc:
                    failed += 1
                    print("[{:03d}] ERROR: {}".format(i, exc))
                next_at += args.period
                if next_at < time.monotonic():
                    next_at = time.monotonic() + args.period
    finally:
        avg = sum(latencies) / len(latencies) if latencies else None
        print("RESULT passed={} failed={} avg_latency_ms={}".format(
            passed, failed, "{:.2f}".format(avg) if avg is not None else "N/A"))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
