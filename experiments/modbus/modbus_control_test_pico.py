"""
modbus_control_test_pico.py

P2-06 — Contrato de control Modbus real.

Raspberry Pi Pico 2
MicroPython
DHT11 -> GP15

Implementa:

Coil 100:
    run_enable
    0 -> STOP
    1 -> START

Holding 101:
    sample_interval_s

Coil 102:
    read_now_trigger
    asynchronous
    self-clearing

Input Registers 200-208:
    telemetry y diagnóstico reales.

IMPORTANTE:
- Usa el compatibility patch de micropython-modbus 2.3.7.
- La validación Modbus formal de valores inválidos / Exception 03
  se cerrará en P2-07/P2-08.
"""

from machine import Pin  # type: ignore
import dht  # type: ignore
import time

from umodbus.serial import ModbusRTU  # type: ignore

from pico_modbus_patch import apply_umodbus_p2_patch # type: ignore


# ================================================================
# Hardware
# ================================================================

DHT_PIN = 15

UART_ID = 0
UART_TX_PIN = 0
UART_RX_PIN = 1

BAUD_RATE = 115200
UNIT_ID = 1


# ================================================================
# Register Map v1
# ================================================================

COIL_RUN_ENABLE = 100
HREG_SAMPLE_INTERVAL = 101
COIL_READ_NOW = 102

IREG_START = 200

REGISTER_MAP_VERSION = 0x0100


# ================================================================
# Estados
# ================================================================

SENSOR_OK = 0
SENSOR_ERR = 1
SENSOR_NO_DATA = 2

STATE_STOPPED = 0
STATE_RUNNING = 1
STATE_FAULT = 2


# ================================================================
# Estado inicial
# ================================================================

run_enabled = False

sample_interval_s = 5
next_sample_ms = None

read_now_pending = False

temperature_scaled = 0
humidity_scaled = 0

sensor_status = SENSOR_NO_DATA
device_state = STATE_STOPPED

sample_counter = 0


# ================================================================
# Uptime monotónico
# ================================================================

last_tick_ms = time.ticks_ms() # type: ignore
uptime_accumulated_ms = 0


def update_uptime_clock():
    global last_tick_ms
    global uptime_accumulated_ms

    now = time.ticks_ms() # type: ignore

    delta = time.ticks_diff( # type: ignore
        now,
        last_tick_ms,
    )

    if delta > 0:
        uptime_accumulated_ms += delta

    last_tick_ms = now


def get_uptime_s():
    update_uptime_clock()

    return (
        uptime_accumulated_ms // 1000
    ) & 0xFFFFFFFF


# ================================================================
# Encoding
# ================================================================

def encode_int16(value):
    """
    Convierte signed int16 a word Modbus uint16.
    """

    return value & 0xFFFF


def split_uint32(value):
    """
    P2 contract:
    HIGH word first.
    """

    value &= 0xFFFFFFFF

    high = (
        value >> 16
    ) & 0xFFFF

    low = (
        value
    ) & 0xFFFF

    return high, low


# ================================================================
# Sensor
# ================================================================

sensor = dht.DHT11(
    Pin(DHT_PIN)
)


# ================================================================
# Modbus server
# ================================================================

server = ModbusRTU(
    addr=UNIT_ID,
    pins=(
        Pin(UART_TX_PIN),
        Pin(UART_RX_PIN),
    ),
    baudrate=BAUD_RATE,
    data_bits=8,
    stop_bits=1,
    parity=None,
    uart_id=UART_ID,
)


apply_umodbus_p2_patch(server)


# ================================================================
# Helpers para callbacks
# ================================================================

def callback_scalar(val):
    """
    micropython-modbus entrega valores de writes como listas
    incluso para registros individuales.

    Ejemplo:
        [True]
        [10]

    Normalizamos a un único valor.
    """

    if isinstance(val, (list, tuple)):
        return val[0]

    return val


# ================================================================
# Publicación de Input Registers
# ================================================================

def publish_snapshot():
    """
    Publica 200-208 como un bloque coherente.
    """

    uptime_s = get_uptime_s()

    uptime_high, uptime_low = (
        split_uint32(uptime_s)
    )

    counter_high, counter_low = (
        split_uint32(sample_counter)
    )

    values = [
        encode_int16(temperature_scaled),  # 200
        humidity_scaled & 0xFFFF,          # 201
        sensor_status,                     # 202
        device_state,                      # 203
        uptime_high,                       # 204
        uptime_low,                        # 205
        counter_high,                      # 206
        counter_low,                       # 207
        REGISTER_MAP_VERSION,              # 208
    ]

    server.set_ireg(
        address=IREG_START,
        value=values,
    )


# ================================================================
# Adquisición
# ================================================================

def perform_acquisition(source):
    """
    Ejecuta exactamente un intento de adquisición.

    sample_counter incrementa tanto en éxito como en error.

    En error:
        conserva última temperatura/humedad válida.
    """

    global temperature_scaled
    global humidity_scaled

    global sensor_status
    global device_state

    global sample_counter

    success = False

    try:

        sensor.measure()

        temperature_c = (
            sensor.temperature()
        )

        humidity_pct = (
            sensor.humidity()
        )

        new_temperature_scaled = int(
            round(temperature_c * 100)
        )

        new_humidity_scaled = int(
            round(humidity_pct * 100)
        )

        # Solo hacemos visibles ambos valores
        # cuando la adquisición completa fue válida.

        temperature_scaled = (
            new_temperature_scaled
        )

        humidity_scaled = (
            new_humidity_scaled
        )

        sensor_status = SENSOR_OK

        if run_enabled:
            device_state = STATE_RUNNING
        else:
            device_state = STATE_STOPPED

        success = True

    except Exception as exc:

        sensor_status = SENSOR_ERR
        device_state = STATE_FAULT

        print(
            "[SENSOR] error:",
            repr(exc),
        )

    # Exactamente una vez por intento completo.

    sample_counter = (
        sample_counter + 1
    ) & 0xFFFFFFFF

    publish_snapshot()

    print(
        "[SAMPLE]",
        source,
        "counter=",
        sample_counter,
        "status=",
        sensor_status,
    )

    if success:
        print(
            "         T=",
            temperature_scaled / 100,
            "C  RH=",
            humidity_scaled / 100,
            "%",
        )

    return success


# ================================================================
# Callbacks Modbus
# ================================================================

def on_run_enable_set(
    reg_type,
    address,
    val,
):
    """
    Coil 100.

    0 -> STOP
    1 -> START

    Idempotente:
    escribir el mismo valor no reinicia scheduler.
    """

    global run_enabled
    global next_sample_ms
    global device_state

    requested = bool(
        callback_scalar(val)
    )

    # Idempotencia
    if requested == run_enabled:
        return

    run_enabled = requested

    if run_enabled:

        device_state = STATE_RUNNING

        next_sample_ms = time.ticks_add(time.ticks_ms(), # type: ignore
            sample_interval_s * 1000,
        )

        print(
            "[CONTROL] START"
        )

    else:

        next_sample_ms = None

        device_state = STATE_STOPPED

        print(
            "[CONTROL] STOP"
        )

    publish_snapshot()


def on_sample_interval_set(
    reg_type,
    address,
    val,
):
    """
    Holding 101.

    P2-06 implementa la semántica de valores válidos.

    La excepción Modbus 03 para un write inválido se
    implementará formalmente en P2-07/P2-08 porque
    micropython-modbus ejecuta on_set_cb después de responder.
    """

    global sample_interval_s
    global next_sample_ms

    requested = int(
        callback_scalar(val)
    )

    # Protección local.
    #
    # El cliente P2-05 ya evita enviar valores fuera de rango.
    # Si otro cliente lo intenta, restauramos el último valor válido.
    #
    # NOTA:
    # esto NO sustituye Exception 03.
    # Eso queda pendiente para P2-07.

    if not 2 <= requested <= 3600:

        server.set_hreg(
            HREG_SAMPLE_INTERVAL,
            sample_interval_s,
        )

        print(
            "[CONTROL] intervalo inválido:",
            requested,
            "-> restaurado:",
            sample_interval_s,
        )

        return

    # Idempotencia:
    # mismo valor -> no mover scheduler.

    if requested == sample_interval_s:
        return

    sample_interval_s = requested

    print(
        "[CONTROL] interval =",
        sample_interval_s,
        "s",
    )

    # Si está corriendo:
    # nuevo intervalo comienza desde esta escritura.

    if run_enabled:

        next_sample_ms = time.ticks_add(time.ticks_ms(), # type: ignore
            sample_interval_s * 1000,
        )


def on_read_now_set(
    reg_type,
    address,
    val,
):
    """
    Coil 102.

    Writing 1:
        marca un request asíncrono.

    Writing 1 de nuevo mientras está pending:
        no-op.

    Writing 0:
        no cancela request pendiente.

    Solo firmware limpia 1 -> 0.
    """

    global read_now_pending

    requested = bool(
        callback_scalar(val)
    )

    # ------------------------------------------------------------
    # Cliente escribe 0
    # ------------------------------------------------------------

    if not requested:

        if read_now_pending:
            # El cliente NO puede cancelar un trigger pendiente.
            server.set_coil(
                COIL_READ_NOW,
                True,
            )

        return

    # ------------------------------------------------------------
    # Cliente escribe 1
    # ------------------------------------------------------------

    if read_now_pending:
        # Idempotente.
        return

    read_now_pending = True

    print(
        "[CONTROL] READ_NOW pending"
    )


# ================================================================
# Refresh antes de FC04
# ================================================================

def on_telemetry_get(
    reg_type,
    address,
    val,
):
    """
    Actualiza uptime inmediatamente antes de responder FC04.

    micropython-modbus ejecuta on_get_cb antes de construir
    definitivamente la respuesta.
    """

    publish_snapshot()


# ================================================================
# Register definitions
# ================================================================

register_definitions = {

    "COILS": {

        "run_enable": {
            "register": COIL_RUN_ENABLE,
            "len": 1,
            "val": 0,
            "on_set_cb": on_run_enable_set,
        },

        "read_now_trigger": {
            "register": COIL_READ_NOW,
            "len": 1,
            "val": 0,
            "on_set_cb": on_read_now_set,
        },
    },

    "HREGS": {

        "sample_interval_s": {
            "register": HREG_SAMPLE_INTERVAL,
            "len": 1,
            "val": sample_interval_s,
            "on_set_cb": on_sample_interval_set,
        },
    },

    "IREGS": {

        "telemetry": {
            "register": IREG_START,
            "len": 9,
            "val": [
                0,                      # 200 temperature
                0,                      # 201 humidity
                SENSOR_NO_DATA,         # 202
                STATE_STOPPED,          # 203
                0,                      # 204 uptime H
                0,                      # 205 uptime L
                0,                      # 206 counter H
                0,                      # 207 counter L
                REGISTER_MAP_VERSION,   # 208
            ],
            "on_get_cb": on_telemetry_get,
        },
    },
}


server.setup_registers(
    registers=register_definitions
)


publish_snapshot()


# ================================================================
# Scheduler
# ================================================================

def process_read_now():
    """
    Ejecuta adquisición manual pendiente FUERA del handler Modbus.
    """

    global read_now_pending
    global next_sample_ms

    if not read_now_pending:
        return

    perform_acquisition(
        "READ_NOW"
    )

    # Si RUNNING:
    # READ_NOW reinicia el calendario periódico desde
    # el FINAL de esta adquisición.

    if run_enabled:

        next_sample_ms = time.ticks_add(time.ticks_ms(), # type: ignore
            sample_interval_s * 1000,
        )

    # Commit ya ocurrió.
    #
    # Ahora hacemos visible la finalización.

    read_now_pending = False

    server.set_coil(
        COIL_READ_NOW,
        False,
    )

    print(
        "[CONTROL] READ_NOW complete"
    )


def update_periodic_sampling():
    """
    Ejecuta sampling periódico no bloqueante a nivel scheduler.
    """

    global next_sample_ms

    if not run_enabled:
        return

    if next_sample_ms is None:
        return

    now = time.ticks_ms() # type: ignore

    if time.ticks_diff( # type: ignore
        now,
        next_sample_ms,
    ) < 0:
        return

    perform_acquisition(
        "PERIODIC"
    )

    # Programar desde el final del sample.
    next_sample_ms = time.ticks_add( # type: ignore
        time.ticks_ms(), # type: ignore
        sample_interval_s * 1000,
    )


# ================================================================
# Boot
# ================================================================

print()
print("========================================")
print(" P2-06 Pico Control Contract")
print("========================================")
print("Unit ID        :", UNIT_ID)
print("UART           : UART0 GP0/GP1")
print("Serial         : 115200 8N1")
print("DHT11          : GP15")
print("run_enable     : False")
print("interval       :", sample_interval_s)
print("device_state   : STOPPED")
print("sensor_status  : NO_DATA")
print("Map version    : 0x0100")
print("P2 patch       : ENABLED")
print()
print("Esperando control Modbus...")
print()


# ================================================================
# Main cooperative loop
# ================================================================

while True:

    try:

        update_uptime_clock()

        # Atiende una transacción Modbus.
        server.process()

        # READ_NOW tiene prioridad.
        #
        # Si coincide con un periodic que estaba a punto de vencer,
        # READ_NOW lo resetea antes de update_periodic_sampling().
        process_read_now()

        update_periodic_sampling()

        time.sleep_ms(2) # type: ignore

    except KeyboardInterrupt:

        print()
        print("Firmware detenido.")
        break

    except Exception as exc:

        print(
            "[MAIN] error:",
            repr(exc),
        )