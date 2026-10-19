"""
rs485_turnaround_diag_pi.py

P4-08 — RS-485 turnaround diagnostic (Pi side)

BOARD:
    Raspberry Pi 5

PICO:
    Keep rs485_turnaround_diag_pico.py running.

PURPOSE:
    Medir, en vez de suponer:

    1. Cuánto tarda la Pi en volver a recepción (bajar RTS) después de
       transmitir, con dos métodos de control de dirección:

         flush     -> write() + uart.flush() (tcdrain) + guarda
                      (método actual de los scripts de P4-08)
         computed  -> write() + espera activa calculada con el tiempo
                      de trama, sin flush()

    2. Con qué turnaround de la Pico (0..100 ms) deja de perderse
       ninguna respuesta, para cada método.

    3. Opcional: cómo cambia todo bajo carga (ejecutar con --label load
       mientras corre algo pesado en la Pi, por ejemplo:
           sudo apt install stress-ng
           stress-ng --cpu 4 --io 2 --timeout 900s
       o con PostgreSQL ingiriendo datos).

METRICS:
    Lado Pi (software, siempre disponible):
        ok / timeout / junk          resultado de cada petición
        pi_overrun_us                tiempo que la Pi mantuvo RTS en
                                     HIGH después del fin teórico de
                                     la trama (p50 / p99 / max)

    Lado Pico (hardware, requiere el cable RTS SENSE GP7 <- GPIO17):
        release_us mean / max        desde el último byte recibido por
                                     la Pico hasta la bajada real de
                                     RTS de la Pi
        late_releases                la Pi bajó RTS después de que la
                                     Pico empezara a responder

OUTPUT:
    Tabla resumen por consola y JSON con todo el detalle en
    experiments/rs485/results/turnaround_<label>_<timestamp>.json

USAGE:
    python3 rs485_turnaround_diag_pi.py
    python3 rs485_turnaround_diag_pi.py --label load
    python3 rs485_turnaround_diag_pi.py --n 500 --turnarounds 0,1,2,5,10
"""

import argparse
import glob
import json
import os
import statistics
import struct
import time

import serial
import gpiod

from gpiod.line import Direction, Value


PORT = "/dev/ttyAMA0"
BAUDRATE = 115200
UNIT_ID = 1
RTS_GPIO = 17

IREG_MAP_VERSION = 208
HREG_TURNAROUND_MS = 900
HREG_RESET_STATS = 901
IREG_STATS_START = 910
IREG_STATS_COUNT = 9

CHAR_TIME_S = 10 / BAUDRATE

CONTROL_RETRIES = 30

RESULTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "results",
)


# ================================================================
# Helpers
# ================================================================

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


def with_crc(payload):
    return payload + struct.pack("<H", crc16(payload))


def find_gpiochip():
    for path in sorted(glob.glob("/dev/gpiochip*")):
        try:
            with gpiod.Chip(path) as chip:
                if chip.get_line_info(RTS_GPIO).name == "GPIO17":
                    return path
        except (OSError, ValueError):
            continue

    raise RuntimeError("Could not locate GPIO17")


def percentile(values, pct):
    if not values:
        return None

    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, int(round(pct / 100 * (len(ordered) - 1)))),
    )
    return ordered[index]


# ================================================================
# RS-485 transmit methods
# ================================================================

def transmit_flush(uart, gpio, frame):
    """
    Método actual de P4-08: flush() (tcdrain) + guarda de 150 us.

    Returns:
        (t_before_write, t_rts_low) en segundos (perf_counter).
    """

    gpio.set_value(RTS_GPIO, Value.ACTIVE)
    time.sleep(0.00005)

    try:
        t_before_write = time.perf_counter()
        uart.write(frame)
        uart.flush()
        time.sleep(0.00015)

    finally:
        gpio.set_value(RTS_GPIO, Value.INACTIVE)
        t_rts_low = time.perf_counter()

    return t_before_write, t_rts_low


def transmit_computed(uart, gpio, frame, margin_chars=2):
    """
    Sin flush(): espera activa durante el tiempo de trama calculado.

    El tiempo se cuenta desde que write() RETORNA (no desde antes):
    la transmisión ya ha empezado en ese punto, así que el margen es
    conservador y no se corta el último byte.

    Returns:
        (t_before_write, t_rts_low) en segundos (perf_counter).
    """

    frame_time_s = len(frame) * CHAR_TIME_S
    margin_s = margin_chars * CHAR_TIME_S

    gpio.set_value(RTS_GPIO, Value.ACTIVE)
    time.sleep(0.00005)

    try:
        t_before_write = time.perf_counter()

        written = uart.write(frame)

        if written != len(frame):
            raise IOError("Incomplete UART write")

        deadline = time.perf_counter() + frame_time_s + margin_s

        while time.perf_counter() < deadline:
            pass

    finally:
        gpio.set_value(RTS_GPIO, Value.INACTIVE)
        t_rts_low = time.perf_counter()

    return t_before_write, t_rts_low


METHODS = {
    "flush": transmit_flush,
    "computed": transmit_computed,
}


# ================================================================
# Response reader (scans past junk bytes)
# ================================================================

def expected_length(function, third_byte):
    if function & 0x80:
        return 5
    if function == 4:
        return 3 + third_byte + 2
    if function == 6:
        return 8
    return None


def read_response(uart, function, timeout_s):
    """
    Busca una respuesta válida (Unit ID, FC o FC|0x80, CRC).

    Returns:
        (status, response_bytes, junk_bytes)
        status in {"ok", "exception", "timeout", "junk"}
    """

    buf = bytearray()
    junk = bytearray()
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        chunk = uart.read(uart.in_waiting or 1)

        if chunk:
            buf.extend(chunk)

        while len(buf) >= 3:
            if buf[0] != UNIT_ID or buf[1] not in (function, function | 0x80):
                junk.append(buf.pop(0))
                continue

            length = expected_length(buf[1], buf[2])

            if length is None or length > 64:
                junk.append(buf.pop(0))
                continue

            if len(buf) < length:
                break

            candidate = bytes(buf[:length])

            if crc16(candidate) == 0:
                status = "exception" if candidate[1] & 0x80 else "ok"
                return status, candidate, bytes(junk)

            junk.append(buf.pop(0))

    junk.extend(buf)
    status = "junk" if junk else "timeout"
    return status, None, bytes(junk)


# ================================================================
# Modbus transactions
# ================================================================

class DiagClient:

    def __init__(self, uart, gpio, method):
        self.uart = uart
        self.gpio = gpio
        self.transmit = METHODS[method]
        self.response_timeout_s = 0.2

    def transact(self, request, function):
        self.uart.reset_input_buffer()

        t_before_write, t_rts_low = self.transmit(
            self.uart,
            self.gpio,
            request,
        )

        frame_time_s = len(request) * CHAR_TIME_S
        overrun_us = (t_rts_low - t_before_write - frame_time_s) * 1e6

        status, response, junk = read_response(
            self.uart,
            function,
            self.response_timeout_s,
        )

        return status, response, junk, overrun_us

    def read_input(self, address, count):
        request = with_crc(
            struct.pack(">BBHH", UNIT_ID, 4, address, count)
        )
        return self.transact(request, 4)

    def write_holding(self, address, value):
        request = with_crc(
            struct.pack(">BBHH", UNIT_ID, 6, address, value)
        )
        return self.transact(request, 6)

    # --- control operations, retried until they succeed ---

    def control_write(self, address, value):
        for _ in range(CONTROL_RETRIES):
            status, response, _, _ = self.write_holding(address, value)

            if status == "ok":
                return

            time.sleep(0.05)

        raise RuntimeError(
            f"Control write to {address} failed after "
            f"{CONTROL_RETRIES} attempts"
        )

    def control_read_stats(self):
        for _ in range(CONTROL_RETRIES):
            status, response, _, _ = self.read_input(
                IREG_STATS_START,
                IREG_STATS_COUNT,
            )

            if status == "ok":
                regs = struct.unpack(
                    f">{IREG_STATS_COUNT}H",
                    response[3:-2], # type: ignore
                )
                return {
                    "exchanges": regs[0],
                    "sense_samples": regs[1],
                    "last_release_us": regs[2],
                    "max_release_us": regs[3],
                    "mean_release_us": regs[4],
                    "late_releases": regs[5],
                    "no_fall_seen": regs[6],
                    "turnaround_ms": regs[7],
                    "sense_connected": bool(regs[8]),
                }

            time.sleep(0.05)

        raise RuntimeError("Could not read Pico stats")


# ================================================================
# Sweep
# ================================================================

def run_step(client, turnaround_ms, n, pause_s):
    # Margen de espera de respuesta: turnaround + 100 ms.
    client.response_timeout_s = turnaround_ms / 1000 + 0.1

    client.control_write(HREG_TURNAROUND_MS, turnaround_ms)
    client.control_write(HREG_RESET_STATS, 1)

    counts = {"ok": 0, "exception": 0, "timeout": 0, "junk": 0}
    overruns = []
    junk_bytes_total = 0

    for _ in range(n):
        status, response, junk, overrun_us = client.read_input(
            IREG_MAP_VERSION,
            1,
        )

        if status == "ok" and response[3:5] != b"\x01\x01":
            status = "junk"

        counts[status] += 1
        overruns.append(overrun_us)
        junk_bytes_total += len(junk)

        time.sleep(pause_s)

    pico = client.control_read_stats()

    return {
        "turnaround_ms": turnaround_ms,
        "n": n,
        "counts": counts,
        "ok_pct": 100 * counts["ok"] / n,
        "junk_bytes_total": junk_bytes_total,
        "pi_overrun_us": {
            "p50": percentile(overruns, 50),
            "p99": percentile(overruns, 99),
            "max": max(overruns),
            "mean": statistics.fmean(overruns),
        },
        "pico": pico,
    }


def print_header():
    print()
    print(
        f"{'method':<9} {'turn':>5} {'ok%':>7} {'tout':>5} {'junk':>5} "
        f"{'pi p50':>8} {'pi p99':>8} {'pi max':>8} "
        f"{'hw mean':>8} {'hw max':>8} {'late':>5}"
    )
    print("-" * 92)


def fmt_us(value):
    return "-" if value is None else f"{value:8.0f}"


def print_row(method, step):
    c = step["counts"]
    o = step["pi_overrun_us"]
    p = step["pico"]

    if p["sense_connected"]:
        hw_mean = fmt_us(p["mean_release_us"])
        hw_max = fmt_us(p["max_release_us"])
        late = f"{p['late_releases']:>5}"
    else:
        hw_mean = hw_max = f"{'n/a':>8}"
        late = f"{'n/a':>5}"

    print(
        f"{method:<9} {step['turnaround_ms']:>5} {step['ok_pct']:>6.1f}% "
        f"{c['timeout']:>5} {c['junk']:>5} "
        f"{fmt_us(o['p50'])} {fmt_us(o['p99'])} {fmt_us(o['max'])} "
        f"{hw_mean} {hw_max} {late}"
    )


def recommend(results):
    print()
    print("=" * 92)
    print(" RECOMMENDATION (per method)")
    print("=" * 92)

    for method, steps in results.items():
        clean = [
            s for s in steps
            if s["counts"]["ok"] == s["n"]
            and (
                not s["pico"]["sense_connected"]
                or s["pico"]["late_releases"] == 0
            )
        ]

        worst_pi_us = max(s["pi_overrun_us"]["max"] for s in steps)

        hw_steps = [s for s in steps if s["pico"]["sense_connected"]]
        worst_hw_us = (
            max(s["pico"]["max_release_us"] for s in hw_steps)
            if hw_steps
            else None
        )

        print()
        print(f"[{method}]")

        if clean:
            print(
                "  Smallest turnaround with 0 losses: "
                f"{min(s['turnaround_ms'] for s in clean)} ms"
            )
        else:
            print("  No turnaround in the sweep was loss-free.")

        print(f"  Worst Pi-side RTS overrun (software): {worst_pi_us:.0f} us")

        if worst_hw_us is not None:
            print(
                f"  Worst Pi release seen by Pico (hardware): "
                f"{worst_hw_us} us"
            )

        print(
            "  Note: el turnaround a fijar debe cubrir el PEOR caso "
            "medido con margen, no el que pasó en una corrida."
        )


# ================================================================
# Main
# ================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="P4-08 RS-485 turnaround diagnostic (Pi side)",
    )
    parser.add_argument(
        "--methods",
        default="flush,computed",
        help="comma-separated: flush,computed",
    )
    parser.add_argument(
        "--turnarounds",
        default="0,1,2,3,5,10,20,30",
        help="comma-separated Pico turnaround values in ms (0..100)",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=200,
        help="requests per (method, turnaround) step",
    )
    parser.add_argument(
        "--pause-ms",
        type=int,
        default=100,
        help="pause between requests (must exceed the Pico's 50 ms "
             "sense wait)",
    )
    parser.add_argument(
        "--label",
        default="idle",
        help="tag for the run, e.g. idle, load, postgres",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    turnarounds = [
        int(t) for t in args.turnarounds.split(",") if t.strip()
    ]

    for m in methods:
        if m not in METHODS:
            raise SystemExit(f"Unknown method: {m}")

    print()
    print("=" * 92)
    print(" P4-08 RS485 TURNAROUND DIAGNOSTIC")
    print("=" * 92)
    print(f"Label       : {args.label}")
    print(f"Methods     : {', '.join(methods)}")
    print(f"Turnarounds : {turnarounds} ms")
    print(f"Requests    : {args.n} per step")

    chip_path = find_gpiochip()
    results = {}

    with gpiod.request_lines(
        chip_path,
        consumer="weather-rs485-turnaround-diag",
        config={
            RTS_GPIO: gpiod.LineSettings(
                direction=Direction.OUTPUT,
                output_value=Value.INACTIVE,
            )
        },
    ) as gpio:

        with serial.Serial(
            PORT,
            baudrate=BAUDRATE,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.01,
        ) as uart:

            # Link check with a generous turnaround first.
            client = DiagClient(uart, gpio, "flush")
            client.control_write(HREG_TURNAROUND_MS, 30)
            stats = client.control_read_stats()

            print(
                "RTS sense   : "
                + (
                    "connected (hardware timing available)"
                    if stats["sense_connected"]
                    else "NOT detected (software metrics only)"
                )
            )

            print_header()

            for method in methods:
                client = DiagClient(uart, gpio, method)
                results[method] = []

                for turnaround in turnarounds:
                    step = run_step(
                        client,
                        turnaround,
                        args.n,
                        args.pause_ms / 1000,
                    )
                    results[method].append(step)
                    print_row(method, step)

            # Dejar la Pico en un turnaround seguro al terminar.
            client = DiagClient(uart, gpio, "flush")
            client.control_write(HREG_TURNAROUND_MS, 30)

    recommend(results)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(
        RESULTS_DIR,
        f"turnaround_{args.label}_{stamp}.json",
    )

    with open(path, "w") as f:
        json.dump(
            {
                "label": args.label,
                "timestamp": stamp,
                "baudrate": BAUDRATE,
                "n_per_step": args.n,
                "pause_ms": args.pause_ms,
                "results": results,
            },
            f,
            indent=2,
        )

    print()
    print(f"Results saved: {path}")


if __name__ == "__main__":
    main()
