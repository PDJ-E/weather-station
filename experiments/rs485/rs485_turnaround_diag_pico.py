"""
rs485_turnaround_diag_pico.py

P4-08 — RS-485 turnaround diagnostic (Pico side)

BOARD:
    Raspberry Pi Pico 2 (MicroPython)

PURPOSE:
    Medir cuánto tarda la Pi en volver a recepción (bajar RTS) después
    de enviar una petición, y con qué turnaround debe responder la Pico
    para no perder respuestas.

    Es un firmware de diagnóstico, independiente del firmware Modbus
    real. No usa micropython-modbus ni el Register Map v1.1.

WIRING:
    UART0 TX  = GP0  (pin 1)  -> SparkFun RX-I
    UART0 RX  = GP1  (pin 2)  <- SparkFun TX-O   (pull-up interno)
    RTS       = GP6  (pin 9)  -> SparkFun RTS

    RTS SENSE = GP7  (pin 10) <- Pi GPIO17 (pin 11)   [OPCIONAL]

    El cable de RTS SENSE es una derivación del mismo GPIO17 de la Pi
    que ya controla su SparkFun. Ambos lados son lógica de 3.3 V y
    comparten GND. Con él, la Pico mide en hardware el instante exacto
    en que la Pi baja RTS. Sin él, el diagnóstico funciona igual, pero
    solo con las métricas de éxito/fallo del lado de la Pi.

PROTOCOL (Modbus RTU, Unit ID 1, 115200 8N1, solo para diagnóstico):

    FC04  Input 208        -> 0x0101. Petición de PRUEBA: única que
                              usa el turnaround barrido.

    FC06  Holding 900      turnaround_ms (0..100). Responde con eco.
    FC06  Holding 901      cualquier valor: reinicia estadísticas.

    FC04  Input 910..918   estadísticas desde el último reinicio:
        910  exchanges          respuestas de PRUEBA enviadas
        911  sense_samples      bajadas de RTS medidas
        912  last_release_us    última latencia de liberación
        913  max_release_us     máxima latencia de liberación
        914  mean_release_us    media de latencia de liberación
        915  late_releases      la Pi bajó RTS DESPUÉS de que la Pico
                                empezara a responder (= colisión,
                                respuesta casi seguro perdida)
        916  no_fall_seen       no se vio la bajada de RTS en 50 ms
                                (cable de sense ausente o Pi colgada)
        917  turnaround_ms      valor actual
        918  sense_connected    1 si alguna vez se vio un flanco

    release_us = tiempo desde que la Pico recibe el último byte de la
    petición hasta que la Pi baja RTS. Valores saturados a 65535.

USAGE:
    1. Cargar este archivo en la Pico (como main.py o desde Thonny).
    2. En la Pi, ejecutar rs485_turnaround_diag_pi.py.
"""

from machine import UART, Pin  # type: ignore
import time


# ================================================================
# Hardware
# ================================================================

UART_ID = 0
UART_TX_PIN = 0
UART_RX_PIN = 1
BAUDRATE = 115200

RTS_PIN = 6
RTS_SENSE_PIN = 7

UNIT_ID = 1


# ================================================================
# Diagnostic register map
# ================================================================

IREG_MAP_VERSION = 208
MAP_VERSION = 0x0101

HREG_TURNAROUND_MS = 900
HREG_RESET_STATS = 901

IREG_STATS_START = 910
IREG_STATS_COUNT = 9

TURNAROUND_MIN_MS = 0
TURNAROUND_MAX_MS = 100

DEFAULT_TURNAROUND_MS = 30

# Las tramas de control (FC06 900/901 y lectura de stats) se responden
# SIEMPRE con este turnaround fijo y seguro. Solo las peticiones de
# prueba (FC04 208) usan el turnaround barrido. Así, con 0 ms, la Pi
# puede seguir cambiando el valor aunque las pruebas fallen.
CONTROL_TURNAROUND_MS = 30

# Tiempo máximo esperando la bajada de RTS de la Pi tras responder.
# Debe ser menor que la pausa entre peticiones del script de la Pi
# (100 ms por defecto) para no solaparse con la siguiente petición.
FALL_WAIT_US = 50_000

# Silencio que separa una trama de la siguiente.
FRAME_GAP_US = 2_000


# ================================================================
# UART + RS-485 direction
# ================================================================

uart = UART(
    UART_ID,
    baudrate=BAUDRATE,
    bits=8,
    parity=None,
    stop=1,
    tx=Pin(UART_TX_PIN),
    rx=Pin(UART_RX_PIN),
)

# Pull-up interno en RX: RO queda en alta impedancia mientras la Pico
# transmite (/RE = HIGH). Debe aplicarse DESPUÉS de crear la UART.
try:
    Pin(
        UART_RX_PIN,
        Pin.ALT,
        pull=Pin.PULL_UP,
        alt=Pin.ALT_UART,
    )
    pullup_state = "ENABLED"
except (AttributeError, ValueError) as exc:
    pullup_state = "FAILED ({})".format(repr(exc))

rts = Pin(RTS_PIN, Pin.OUT, value=0)

CHAR_TIME_US = (10 * 1_000_000) // BAUDRATE


# ================================================================
# RTS sense (hard IRQ: timestamp preciso, sin asignar memoria)
# ================================================================

rts_fall_us = 0
rts_fall_count = 0


def _on_rts_fall(pin):
    global rts_fall_us
    global rts_fall_count

    rts_fall_us = time.ticks_us()  # type: ignore
    rts_fall_count += 1


rts_sense = Pin(RTS_SENSE_PIN, Pin.IN, Pin.PULL_DOWN)
rts_sense.irq(
    trigger=Pin.IRQ_FALLING,
    handler=_on_rts_fall,
    hard=True,
)


# ================================================================
# Modbus helpers
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
    crc = crc16(payload)
    return bytes(payload) + bytes([crc & 0xFF, crc >> 8])


def is_valid_request(frame):
    if len(frame) != 8:
        return False

    if frame[0] != UNIT_ID:
        return False

    if frame[1] not in (4, 6):
        return False

    return crc16(frame) == 0


def exception_response(function, code):
    return with_crc(bytes([UNIT_ID, function | 0x80, code]))


# ================================================================
# Stats
# ================================================================

turnaround_ms = DEFAULT_TURNAROUND_MS

stats = {}


def reset_stats():
    stats["exchanges"] = 0
    stats["sense_samples"] = 0
    stats["last_release_us"] = 0
    stats["max_release_us"] = 0
    stats["sum_release_us"] = 0
    stats["late_releases"] = 0
    stats["no_fall_seen"] = 0


reset_stats()


def sat16(value):
    if value < 0:
        return 0
    if value > 0xFFFF:
        return 0xFFFF
    return value


def stats_registers():
    samples = stats["sense_samples"]

    mean = (
        stats["sum_release_us"] // samples
        if samples
        else 0
    )

    return [
        sat16(stats["exchanges"]),
        sat16(samples),
        sat16(stats["last_release_us"]),
        sat16(stats["max_release_us"]),
        sat16(mean),
        sat16(stats["late_releases"]),
        sat16(stats["no_fall_seen"]),
        turnaround_ms,
        1 if rts_fall_count > 0 else 0,
    ]


def read_input_registers(address, count):
    regs = {}

    regs[IREG_MAP_VERSION] = MAP_VERSION

    for i, value in enumerate(stats_registers()):
        regs[IREG_STATS_START + i] = value

    values = []

    for addr in range(address, address + count):
        if addr not in regs:
            return None
        values.append(regs[addr])

    return values


# ================================================================
# Request handling
# ================================================================

def build_response(frame):
    """Devuelve (response_bytes, action) o (None, None)."""

    global turnaround_ms

    function = frame[1]
    address = (frame[2] << 8) | frame[3]
    value = (frame[4] << 8) | frame[5]

    if function == 4:
        count = value

        if count < 1 or count > 20:
            return exception_response(4, 3), None

        values = read_input_registers(address, count)

        if values is None:
            return exception_response(4, 2), None

        payload = bytearray([UNIT_ID, 4, count * 2])

        for v in values:
            payload.append((v >> 8) & 0xFF)
            payload.append(v & 0xFF)

        return with_crc(payload), None

    if function == 6:

        if address == HREG_TURNAROUND_MS:
            if not (TURNAROUND_MIN_MS <= value <= TURNAROUND_MAX_MS):
                return exception_response(6, 3), None

            # Se aplica DESPUÉS de responder, con el turnaround actual.
            return bytes(frame), ("set_turnaround", value)

        if address == HREG_RESET_STATS:
            return bytes(frame), ("reset_stats", None)

        return exception_response(6, 2), None

    return None, None


def transmit(response):
    rts.value(1)
    time.sleep_us(50)  # type: ignore

    try:
        uart.write(response)

        # Esperar a que salga el último bit + 1 carácter de guarda.
        tx_us = len(response) * CHAR_TIME_US + CHAR_TIME_US
        time.sleep_us(tx_us)  # type: ignore

    finally:
        rts.value(0)


def wait_until(start_us, delay_us):
    while time.ticks_diff(time.ticks_us(), start_us) < delay_us:  # type: ignore
        pass


def measure_release(frame_start_us, last_byte_us, tx_start_us):
    """
    Busca la bajada de RTS de la Pi que corresponde a esta petición
    (ocurrida después del primer byte de la trama).
    """

    # Sin cable de sense nunca hay flancos: no esperar, para no
    # retrasar la siguiente petición.
    if rts_fall_count == 0:
        stats["no_fall_seen"] += 1
        return

    deadline_start = time.ticks_us()  # type: ignore

    while True:
        fall = rts_fall_us

        if (
            rts_fall_count > 0
            and time.ticks_diff(fall, frame_start_us) > 0  # type: ignore
        ):
            release = time.ticks_diff(fall, last_byte_us)  # type: ignore

            stats["sense_samples"] += 1
            stats["last_release_us"] = release if release > 0 else 0
            stats["sum_release_us"] += stats["last_release_us"]

            if stats["last_release_us"] > stats["max_release_us"]:
                stats["max_release_us"] = stats["last_release_us"]

            if time.ticks_diff(fall, tx_start_us) > 0:  # type: ignore
                stats["late_releases"] += 1

            return

        if time.ticks_diff(  # type: ignore
            time.ticks_us(),  # type: ignore
            deadline_start,
        ) > FALL_WAIT_US:
            stats["no_fall_seen"] += 1
            return

        time.sleep_us(50)  # type: ignore


# ================================================================
# Main loop
# ================================================================

print()
print("========================================")
print(" PICO 2 — RS485 TURNAROUND DIAGNOSTIC")
print("========================================")
print("UART0      : GP0 / GP1")
print("RTS        : GP6")
print("RTS sense  : GP7 (opcional, Pi GPIO17)")
print("RX pull-up :", pullup_state)
print("Turnaround :", turnaround_ms, "ms")
print()

window = bytearray()
frame_start_us = time.ticks_us()  # type: ignore
last_byte_us = frame_start_us

while True:

    available = uart.any()

    if available:
        now = time.ticks_us()  # type: ignore

        # Inicio de una trama nueva tras un silencio.
        if time.ticks_diff(now, last_byte_us) > FRAME_GAP_US:  # type: ignore
            frame_start_us = now
            window = bytearray()

        last_byte_us = now

        for byte in uart.read(available):
            window.append(byte)

            if len(window) > 8:
                window = window[-8:]

            if not is_valid_request(window):
                continue

            frame = bytes(window)
            window = bytearray()

            response, action = build_response(frame)

            if response is None:
                continue

            is_test = (
                frame[1] == 4
                and ((frame[2] << 8) | frame[3]) == IREG_MAP_VERSION
            )

            delay_ms = turnaround_ms if is_test else CONTROL_TURNAROUND_MS

            # Turnaround medido desde el último byte de la petición.
            wait_until(last_byte_us, delay_ms * 1000)

            tx_start_us = time.ticks_us()  # type: ignore
            transmit(response)

            if not is_test:
                # Tramas de control: no cuentan en las estadísticas.
                junk = uart.any()
                if junk:
                    uart.read(junk)

                if action is not None:
                    kind, value = action

                    if kind == "set_turnaround":
                        turnaround_ms = value
                        print("[DIAG] turnaround_ms =", turnaround_ms)

                    elif kind == "reset_stats":
                        reset_stats()
                        print("[DIAG] stats reset")

                break

            stats["exchanges"] += 1

            # Descartar lo que haya entrado en RX durante la propia
            # transmisión. La Pi no envía otra petición hasta recibir
            # esta respuesta, así que aquí solo puede haber basura.
            junk = uart.any()
            if junk:
                uart.read(junk)

            measure_release(frame_start_us, last_byte_us, tx_start_us)

            break

    else:
        time.sleep_us(20)  # type: ignore
