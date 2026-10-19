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
    RTS: GPIO17 (método "computed", sin flush())

Reliability:
    Cada transacción Modbus se reintenta hasta MODBUS_RETRIES veces
    ante fallos de transporte (timeout, CRC, basura, respuesta
    incompleta), igual que la política retries=3 del cliente PyModbus
    de P2/P3. Las respuestas de excepción Modbus NO se reintentan:
    son una respuesta válida de la Pico.

    El diagnóstico de turnaround (P4-08) midió ~0.05% de peticiones
    que no llegan válidas a la Pico, independientemente del método:
    los reintentos son obligatorios.

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

# Reintentos por transacción (además del primer intento).
MODBUS_RETRIES = 3

# Tiempo máximo de espera de respuesta por intento.
#
# Debe superar el tiempo que el firmware de la Pico puede pasar sin
# atender Modbus: su bucle es cooperativo y, mientras lee los sensores
# (READ_NOW / muestreo periódico), no procesa peticiones. Medido en
# banco (P4-08): hasta ~820 ms en la primera medida del BME688,
# ~450 ms en las siguientes.
#
# Con 0.3 s la Pi reintentaba peticiones que la Pico sí iba a
# responder; esas respuestas tardías llegaban durante la transacción
# siguiente. Ver docs/next_steps.md (NS-05, NS-06).
RESPONSE_TIMEOUT_S = 1.5

# Pausa antes de reintentar.
RETRY_DELAY_S = 0.05


class ModbusTransportError(Exception):
    """Fallo de transporte: se puede reintentar."""


class ModbusExceptionResponse(Exception):
    """La Pico respondió con una excepción Modbus: no se reintenta."""


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
                if chip.get_line_info(RTS_GPIO).name == "GPIO17":
                    return path
        except (OSError, ValueError):
            continue

    raise RuntimeError("Could not locate GPIO17")


def read_exact(uart, count, timeout=RESPONSE_TIMEOUT_S):
    data = bytearray()
    deadline = time.monotonic() + timeout

    while len(data) < count:
        if time.monotonic() >= deadline:
            raise ModbusTransportError(
                f"Expected {count} bytes, received {len(data)}"
            )

        chunk = uart.read(count - len(data))

        if chunk:
            data.extend(chunk)

    return bytes(data)


def read_header(uart, function, timeout=RESPONSE_TIMEOUT_S):
    """
    Busca el inicio de la respuesta: Unit ID seguido del function
    code esperado (o su variante de excepción, function | 0x80).

    Descarta los bytes basura que el bus RS-485 pueda generar antes
    de la respuesta (bus en reposo sin bias, RO flotando durante la
    transmisión propia). Un read_exact(2) a ciegas los tomaría como
    cabecera.
    """

    valid_functions = (function, function | 0x80)
    discarded = bytearray()
    deadline = time.monotonic() + timeout

    previous = None

    while time.monotonic() < deadline:
        chunk = uart.read(1)

        if not chunk:
            continue

        current = chunk[0]

        if previous == UNIT_ID and current in valid_functions:
            if discarded:
                print(
                    "    [RS485] junk discarded:",
                    discarded.hex(" "),
                )

            return bytes([previous, current])

        if previous is not None:
            discarded.append(previous)

        previous = current

    raise ModbusTransportError(
        "No Modbus response header within "
        f"{timeout} s (junk: {discarded.hex(' ') or 'none'})"
    )


def check_response_matches_request(function, payload, response):
    """
    Comprueba que la respuesta corresponde a ESTA petición.

    Modbus RTU no tiene identificador de transacción: una respuesta
    tardía a una petición anterior que ya se dio por perdida tiene
    cabecera, función y CRC válidos, y podría aceptarse como respuesta
    de la petición actual. Aquí se rechaza lo que se puede detectar:

        FC01   byte count == ceil(cantidad / 8)
        FC03/  byte count == cantidad * 2
        FC04
        FC05   eco exacto de dirección + valor

    LÍMITE: una respuesta tardía de la MISMA función y la MISMA
    cantidad pero de otra dirección es indistinguible en Modbus RTU.
    Por eso el timeout debe cubrir el peor tiempo de respuesta real de
    la Pico, para que las respuestas tardías no ocurran en primer
    lugar. Requisito de diseño para ATP: ver docs/next_steps.md (NS-06).
    """

    if function in (1, 3, 4):
        _, quantity = struct.unpack(">HH", payload)

        expected = (
            (quantity + 7) // 8
            if function == 1
            else quantity * 2
        )

        if response[2] != expected:
            raise ModbusTransportError(
                f"Response does not match request: byte count "
                f"{response[2]}, expected {expected} "
                "(likely a late reply to an earlier request)"
            )

    elif function == 5:

        if response[2:6] != payload:
            raise ModbusTransportError(
                "Response does not match request: FC05 echo "
                f"{response[2:6].hex(' ')}, expected {payload.hex(' ')} "
                "(likely a late reply to an earlier request)"
            )


class RS485Modbus:

    def __init__(self, uart, gpio):
        self.uart = uart
        self.gpio = gpio

        self.transactions = 0
        self.retries_used = 0

        self.gpio.set_value(
            RTS_GPIO,
            Value.INACTIVE
        )

    def exchange(self, function, payload):
        """
        Transacción Modbus con reintentos ante fallos de transporte.

        Seguro de reintentar: lecturas (FC01/FC04) son idempotentes, y
        FC05 READ_NOW=ON también (si el primer intento sí llegó, el
        trigger sigue pendiente y repetirlo no genera otra adquisición).
        """

        self.transactions += 1
        last_error = None

        for attempt in range(MODBUS_RETRIES + 1):

            if attempt > 0:
                self.retries_used += 1

                print(
                    f"    [RS485] retry {attempt}/{MODBUS_RETRIES} "
                    f"(FC{function:02d}): {last_error}"
                )

                time.sleep(RETRY_DELAY_S)

            try:
                return self._exchange_once(function, payload)

            except ModbusTransportError as exc:
                last_error = exc

        raise ModbusTransportError(
            f"FC{function:02d} failed after "
            f"{MODBUS_RETRIES + 1} attempts: {last_error}"
        )

    def _exchange_once(self, function, payload):

        request_data = (
            bytes([UNIT_ID, function])
            + payload
        )

        request = (
            request_data
            + struct.pack("<H", crc16(request_data))
        )

        self.uart.reset_input_buffer()

        transmit_frame(self.uart, self.gpio, request)

        # Read response header, skipping junk bytes from the bus
        header = read_header(self.uart, function)

        if header[1] == (function | 0x80):

            rest = read_exact(self.uart, 3)
            response = header + rest

            if crc16(response) != 0:
                raise ModbusTransportError("CRC error in exception")

            raise ModbusExceptionResponse(
                f"Modbus exception: {rest[0]}"
            )

        if function in (1, 3, 4):

            count = read_exact(self.uart, 1)[0]
            data = read_exact(self.uart, count + 2)

            response = header + bytes([count]) + data

        elif function == 5:

            response = header + read_exact(self.uart, 6)

        else:
            raise RuntimeError("Unsupported function")

        if crc16(response) != 0:
            raise ModbusTransportError("Modbus CRC mismatch")

        check_response_matches_request(function, payload, response)

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
            timeout=0.02
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
    print(
        f" Modbus transactions: {client.transactions}, "
        f"retries used: {client.retries_used}"
    )
    print("=" * 55)

    if successful == SAMPLES:
        print("P4-08 RS485 ENVIRONMENTAL TEST: PASS")


if __name__ == "__main__":
    main()