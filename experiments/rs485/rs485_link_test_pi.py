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


RESPONSE_TIMEOUT_S = 2.0
RESPONSE_LENGTH = 7  # FC04, 1 registro: id, fc, count, hi, lo, crc, crc


def read_response(uart):
    """
    Lee hasta encontrar una respuesta FC04 válida (Unit ID, FC y CRC),
    descartando bytes basura del bus RS-485 que aparezcan antes.

    Mientras la Pi transmite, /RE está en HIGH y RO queda flotando;
    y con el bus en reposo sin bias pueden aparecer bytes espurios.
    Un read(7) a ciegas los tomaría como parte de la respuesta.

    Returns:
        (response | None, discarded_bytes)
    """

    buf = bytearray()
    discarded = bytearray()
    deadline = time.monotonic() + RESPONSE_TIMEOUT_S

    while time.monotonic() < deadline:
        chunk = uart.read(uart.in_waiting or 1)

        if chunk:
            buf.extend(chunk)

        while len(buf) >= RESPONSE_LENGTH:
            candidate = bytes(buf[:RESPONSE_LENGTH])

            if (
                candidate[0] == UNIT_ID
                and candidate[1] == 4
                and crc16(candidate) == 0
            ):
                return candidate, bytes(discarded)

            discarded.append(buf.pop(0))

    return None, bytes(discarded)


def transmit_frame(uart, gpio, frame, margin_chars=2):
    """
    Transmite una trama RS-485 y vuelve a recepción lo antes posible.

    Método "computed" validado con rs485_turnaround_diag (P4-08):
    no usa uart.flush(). En Linux, tcdrain() regresa en escalones de
    ~4 ms (medido: 4-20 ms) y la respuesta se perdía con el receptor de
    la Pi todavía deshabilitado. Con espera activa calculada, la
    liberación medida en hardware fue ~150-200 us (peor caso 5 ms bajo
    carga).

    El tiempo de trama se cuenta desde que write() RETORNA: la
    transmisión ya empezó, así que el margen es conservador y no se
    corta el último byte.

    8N1 = 10 bits por byte.
    """

    char_time_s = 10 / BAUDRATE
    frame_time_s = len(frame) * char_time_s
    margin_s = margin_chars * char_time_s

    gpio.set_value(RTS_GPIO, Value.ACTIVE)
    time.sleep(0.00005)

    try:
        written = uart.write(frame)

        if written != len(frame):
            raise IOError("Incomplete UART write")

        deadline = time.perf_counter() + frame_time_s + margin_s

        # Espera activa: más precisa que time.sleep() o flush().
        while time.perf_counter() < deadline:
            pass

    finally:
        gpio.set_value(RTS_GPIO, Value.INACTIVE)


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
            timeout=0.05,
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

            transmit_frame(uart, gpio, request)

            print("[3] Request transmitted")
            print("[4] Waiting for response...")

            response, discarded = read_response(uart)

            if discarded:
                print(
                    "    Bytes basura descartados:",
                    discarded.hex(" "),
                )

            if response is None:
                raise RuntimeError(
                    "No valid Modbus response within "
                    f"{RESPONSE_TIMEOUT_S} s"
                )

            print("    Response:", response.hex(" "))

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